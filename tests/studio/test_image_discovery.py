import base64
import io
import json
from dataclasses import replace

import pytest
from compiler_helpers import runtime_python
from ir_cases import valid_ir
from PIL import Image, ImageDraw, ImageOps, PngImagePlugin
from workflow_studio.contracts import StudioError
from workflow_studio.discovery.images import context_from_messages, input_reservation
from workflow_studio.llm.provider import Chunk
from workflow_studio.llm.usage import Budget
from workflow_studio.registry.policy import ToolPolicy
from workflow_studio.service import StudioService
from workflow_studio.verifier.golden_cases import GoldenCases
from workflow_studio.verifier.report import Verifier


class VisionFixture:
    model, destination, external = "vision-fixture", "https://fixture.invalid/vision", True

    def __init__(self, transform=None, interrupted=False):
        self.transform, self.interrupted = transform, interrupted
        self.calls, self.requests = 0, []

    def stream(self, messages, max_output_tokens):
        self.calls += 1
        self.requests.append(messages)
        context = context_from_messages(messages)
        image = next(s for s in context["sources"] if s.get("kind") == "image")
        ir = valid_ir()
        ir.update(
            workflow_id=context["workflow_id"],
            revision=context["next_revision"],
            goal=context["goal"],
        )
        ir["evidence"][0].update(
            source_id=image["source_id"],
            source_version=image["source_version"],
            evidence_locator=image["path"] + ":region=0.1,0.1,0.8,0.8",
            confidence_label="inferred",
        )
        result = {
            "analysis": "依圖片的標籤與箭頭提出複製候選，仍須獨立試做。",
            "ir": ir,
            "questions": [],
        }
        if self.transform:
            self.transform(result, context, self.calls)
        text = json.dumps(result, ensure_ascii=False)
        yield Chunk(text=text[:60])
        if self.interrupted:
            raise ConnectionError("private transport error")
        yield Chunk(text=text[60:])
        yield Chunk(usage={"prompt_tokens": 1000, "completion_tokens": 250}, done=True)


def diagram(path, suffix="png"):
    image = Image.new("RGB", (640, 320), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((50, 100, 200, 210), outline="black", width=3)
    draw.rectangle((420, 100, 590, 210), outline="black", width=3)
    draw.line((200, 155, 420, 155), fill="black", width=3)
    draw.polygon(((420, 155), (400, 147), (400, 163)), fill="black")
    draw.text((70, 145), "INPUT", fill="black")
    draw.text((270, 130), "COPY", fill="black")
    draw.text((450, 145), "OUTPUT", fill="black")
    if suffix == "png":
        info = PngImagePlugin.PngInfo()
        info.add_text("Comment", "private-metadata-sentinel")
        image.save(path, pnginfo=info)
    elif suffix == "jpg":
        exif = Image.Exif()
        exif[274] = 6
        exif[270] = "private-metadata-sentinel"
        image.save(path, exif=exif)
    else:
        image.save(path)


def setup_image(tmp_path, provider=None, suffix="png"):
    studio = StudioService.open_workspace(tmp_path / "studio")
    p = studio.projects.create("圖片流程", valid_ir()["goal"])
    path = tmp_path / ("diagram." + suffix)
    diagram(path, suffix)
    source = studio.sources.import_path(p.project_id, p.version, path)
    studio.tools.seed()
    provider = provider or VisionFixture()
    policy = ToolPolicy("windows")
    planner = studio.discovery(provider, Budget(5, 1_000_000, 8000, 1_000_000, 1_000_000), policy)
    prepared = planner.prepare(p.project_id, [source.source_id])
    return studio, p, source, path, planner, prepared, policy


@pytest.mark.parametrize("suffix", ["png", "jpg", "webp"])
def test_full_resolution_oriented_pixels_and_metadata_free_disclosure(tmp_path, suffix):
    studio, p, source, path, planner, prepared, _ = setup_image(tmp_path, suffix=suffix)
    messages = json.loads(prepared.messages_json)
    context = context_from_messages(messages)
    data = base64.b64decode(messages[1]["content"][1]["image_url"]["url"].split(",", 1)[1])
    with Image.open(io.BytesIO(data)) as actual, Image.open(path) as original:
        expected = ImageOps.exif_transpose(original).convert("RGB")
        assert actual.size == expected.size
        assert actual.convert("RGB").tobytes() == expected.tobytes()
        assert not actual.getexif() and "Comment" not in actual.info
    assert b"private-metadata-sentinel" not in data
    assert context["sources"][0]["transmission"]["width"] == expected.width
    assert studio.sources.content(p.project_id, source.source_id, path.name) == path.read_bytes()
    events = list(planner.run(prepared, authorized_hash=prepared.request_hash))
    assert events[-1]["event"] == "analysis.completed"
    assert any(e["event"] == "analysis.delta" for e in events)
    assert "base64" not in json.dumps(planner.events.read(p.project_id))
    assert "private-metadata-sentinel" not in json.dumps(planner.events.read(p.project_id))
    studio.close()


def test_image_candidate_is_compiled_and_replayed_through_runtime(tmp_path):
    studio, p, _, _, planner, prepared, policy = setup_image(tmp_path)
    completed = list(planner.run(prepared, authorized_hash=prepared.request_hash))[-1]
    revision = studio.projects.get_revision(p.project_id, completed["revision_id"])
    assert revision.ir["evidence"][0]["evidence_locator"].endswith("region=0.1,0.1,0.8,0.8")
    build = studio.compile(p.project_id, revision.revision_id, policy)
    source = studio.sources.import_text(
        p.project_id,
        studio.projects.get(p.project_id).version,
        "independent input",
        name="case.txt",
    )
    record = source.manifest["files"][0]
    ref = {
        "source_id": source.source_id,
        "version": source.version,
        "path": record["path"],
        "sha256": record["sha256"],
    }
    case = GoldenCases(studio).save(
        p.project_id,
        studio.projects.get(p.project_id).version,
        {"name": "separate replay", "input": ref, "expected": ref, "comparison": "exact_bytes"},
    )
    report = Verifier(studio, runtime_python()).verify(
        p.project_id,
        build["build_id"],
        [{"case_id": case["case_id"], "version": 1, "output": "result"}],
        policy,
    )
    assert report["status"] == "passed"
    studio.close()


@pytest.mark.parametrize("ambiguity", ["questions", "conflicted"])
def test_ambiguous_diagram_keeps_candidate_only_in_clarification_draft(tmp_path, ambiguity):
    def unclear(result, context, call):
        if ambiguity == "questions":
            result["questions"] = ["箭頭是往左還是往右？"]
        else:
            result["ir"]["evidence"][0]["confidence_label"] = "conflicted"

    studio, p, _, _, planner, prepared, policy = setup_image(tmp_path, VisionFixture(unclear))
    event = list(planner.run(prepared, authorized_hash=prepared.request_hash))[-1]
    revision = studio.projects.get_revision(p.project_id, event["revision_id"])
    assert revision.status == "needs_clarification" and revision.ir["questions"]
    assert revision.ir["ir"] is not None
    with pytest.raises(StudioError):
        studio.compile(p.project_id, revision.revision_id, policy)
    studio.close()


@pytest.mark.parametrize("attack", ["tool", "verified", "fake_file", "fake_line", "outside", "nan"])
def test_image_claims_cannot_invent_tools_or_evidence(tmp_path, attack):
    def mutate(result, context, call):
        evidence = result["ir"]["evidence"][0]
        if attack == "tool":
            result["ir"]["nodes"][0]["tool"] = "shell.execute"
        elif attack == "verified":
            evidence.update(confidence_label="verified", verified_by="image-says-approved")
        else:
            endings = {
                "fake_file": "missing.png",
                "fake_line": "diagram.png:1",
                "outside": "diagram.png:region=0,0,2,2",
                "nan": "diagram.png:region=NaN,0,1,1",
            }
            evidence["evidence_locator"] = endings[attack]

    studio, p, _, _, planner, prepared, _ = setup_image(tmp_path, VisionFixture(mutate))
    with pytest.raises(StudioError):
        list(planner.run(prepared, authorized_hash=prepared.request_hash))
    assert studio.projects.revisions(p.project_id) == []
    studio.close()


def test_authorization_source_changes_and_request_tampering_prevent_transmission(tmp_path):
    studio, p, source, _, planner, prepared, _ = setup_image(tmp_path)
    with pytest.raises(StudioError):
        list(planner.run(prepared))
    tampered = replace(
        prepared, messages_json=prepared.messages_json.replace("data:image/png", "data:image/jpeg")
    )
    with pytest.raises(StudioError):
        list(planner.run(tampered, authorized_hash=tampered.request_hash))
    studio.sources.describe(
        p.project_id, source.source_id, studio.projects.get(p.project_id).version, "updated scope"
    )
    with pytest.raises(StudioError):
        list(planner.run(prepared, authorized_hash=prepared.request_hash))
    assert planner.provider.calls == 0
    studio.close()


def test_image_cost_is_reserved_separately_from_compressed_bytes(tmp_path):
    studio, p, source, _, planner, prepared, _ = setup_image(tmp_path)
    messages = json.loads(prepared.messages_json)
    estimate = input_reservation(messages, 65536)
    assert estimate > 65536
    events = list(planner.run(prepared, authorized_hash=prepared.request_hash))
    assert events[-1]["event"] == "analysis.completed"
    receipt = planner.ledger.summary(p.project_id)["receipts"][0]
    assert receipt["reserved"] == planner.budget.cost(estimate, planner.budget.max_output_tokens)
    planner.budget = Budget(5, 1, 8000, 1_000_000, 1_000_000)
    fresh = planner.prepare(p.project_id, [source.source_id])
    with pytest.raises(StudioError) as exc:
        list(planner.run(fresh, authorized_hash=fresh.request_hash))
    assert exc.value.code == "BUDGET_EXCEEDED" and planner.provider.calls == 1
    studio.close()


def test_interrupted_image_analysis_preserves_last_valid_revision(tmp_path):
    studio, p, source, _, planner, prepared, _ = setup_image(tmp_path)
    previous = list(planner.run(prepared, authorized_hash=prepared.request_hash))[-1]["revision_id"]
    planner.provider.interrupted = True
    fresh = planner.prepare(p.project_id, [source.source_id])
    with pytest.raises(StudioError):
        list(planner.run(fresh, authorized_hash=fresh.request_hash))
    assert studio.projects.get(p.project_id).current_revision_id == previous
    assert planner.ledger.summary(p.project_id)["receipts"][-1]["status"] == "interrupted"
    studio.close()


def test_repair_keeps_same_image_scope_and_streams_again(tmp_path):
    def first_invalid(result, context, call):
        if call == 1:
            result["ir"]["nodes"][0]["depends_on"] = ["missing"]

    studio, _p, _, _, planner, prepared, _ = setup_image(tmp_path, VisionFixture(first_invalid))
    events = list(planner.run(prepared, authorized_hash=prepared.request_hash))
    assert planner.provider.calls == 2
    assert planner.provider.requests[0][1] == planner.provider.requests[1][1]
    assert any(e["event"] == "analysis.repairing" for e in events)
    assert events[-1]["event"] == "analysis.completed"
    studio.close()


def test_mixed_sources_keep_image_mapping_and_exclude_unselected_files(tmp_path):
    studio, p, source, _, planner, _, _ = setup_image(tmp_path)
    text = studio.sources.import_text(
        p.project_id,
        studio.projects.get(p.project_id).version,
        "copy the input",
        name="instructions.txt",
    )
    studio.sources.import_text(
        p.project_id,
        studio.projects.get(p.project_id).version,
        "unselected-private-sentinel",
        name="private.txt",
    )
    prepared = planner.prepare(p.project_id, [text.source_id, source.source_id, source.source_id])
    messages = json.loads(prepared.messages_json)
    context = context_from_messages(messages)
    assert len(context["sources"]) == 2 and len(prepared.source_refs) == 2
    assert context["sources"][0]["content"] == "copy the input"
    assert context["sources"][1]["image_index"] == 1
    assert len(messages[1]["content"]) == 2
    assert "unselected-private-sentinel" not in prepared.messages_json
    assert (
        list(planner.run(prepared, authorized_hash=prepared.request_hash))[-1]["event"]
        == "analysis.completed"
    )
    studio.close()


def test_animation_and_experimental_video_are_not_silently_treated_as_one_image(tmp_path):
    studio, p, _, path, planner, _, _ = setup_image(tmp_path)
    animated = tmp_path / "animated.png"
    Image.new("RGB", (64, 64), "white").save(
        animated,
        save_all=True,
        append_images=[Image.new("RGB", (64, 64), "black")],
        duration=100,
        loop=0,
    )
    source = studio.sources.import_path(
        p.project_id, studio.projects.get(p.project_id).version, animated
    )
    with pytest.raises(StudioError, match="單張靜態圖"):
        planner.prepare(p.project_id, [source.source_id])
    # Exercise the real video source shape, without invoking a decoder or enabling P2.
    video = studio.sources._save(
        p.project_id,
        studio.projects.get(p.project_id).version,
        {"frame.png": path.read_bytes()},
        "video fixture",
        None,
        extra_metadata={"video": {"frames": []}},
        source_kind="video",
    )
    with pytest.raises(StudioError, match="獨立的實驗"):
        planner.prepare(p.project_id, [video.source_id])
    assert planner.provider.calls == 0
    studio.close()


def test_oversized_request_is_rejected_without_call_or_partial_revision(tmp_path, monkeypatch):
    from workflow_studio.discovery import images

    studio, p, source, _, planner, _, _ = setup_image(tmp_path)
    monkeypatch.setattr(images, "REQUEST_LIMIT", 100)
    with pytest.raises(StudioError):
        planner.prepare(p.project_id, [source.source_id])
    assert planner.provider.calls == 0 and studio.projects.revisions(p.project_id) == []
    studio.close()


def test_ui_disclosure_uses_frozen_sent_pixels_and_persists_image_budget(tmp_path):
    from workflow_studio.conversation.session import StudioSession

    studio, p, source, _, _, _, _ = setup_image(tmp_path)
    studio.close()
    model = StudioSession(tmp_path / "studio")
    model.select_project(p.project_id)
    model.configure_provider(
        "https://example.invalid/chat", "vision", 1, 1, 1, image_input_token_bound=100_000
    )
    prepared = model.prepare_analysis([source.source_id])
    disclosure = model.analysis_disclosure(prepared)
    assert disclosure["image_tokens"] == 100_000
    asset = model.analysis_asset(prepared, 0)
    assert (
        asset["preview"]["kind"] == "image" and b"private-metadata-sentinel" not in asset["content"]
    )
    reopened = StudioSession(tmp_path / "studio")
    reopened.select_project(p.project_id)
    assert reopened.session_config["image_input_token_bound"] == 100_000
    assert reopened.prepare_analysis([source.source_id]) == prepared
    with pytest.raises(StudioError):
        reopened.configure_provider(
            "https://example.invalid/chat", "vision", 1, 1, 1, image_input_token_bound=True
        )


def test_image_parts_reach_existing_sse_transport_without_body_credentials(tmp_path, monkeypatch):
    import urllib.request

    from test_llm_streaming import sse
    from workflow_studio.llm.provider import ChatCompletionsProvider

    studio, _, _, _, _, prepared, _ = setup_image(tmp_path)
    captured = {}

    class Opener:
        def open(self, request, timeout):
            captured["body"] = json.loads(request.data)
            return sse(
                {"choices": [{"delta": {"content": "{}"}, "finish_reason": "stop"}]}, b"[DONE]"
            )

    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: Opener())
    monkeypatch.setenv("VISION_TEST_KEY", "credential-not-in-body")
    provider = ChatCompletionsProvider(
        "https://example.invalid/chat", "vision", api_key_env="VISION_TEST_KEY"
    )
    messages = json.loads(prepared.messages_json)
    assert list(provider.stream(messages, 500))[-1].done
    assert captured["body"]["messages"] == messages
    assert captured["body"]["messages"][1]["content"][1]["type"] == "image_url"
    assert "credential-not-in-body" not in json.dumps(captured["body"])
    studio.close()


def test_high_depth_grayscale_is_not_clipped_during_metadata_removal():
    from workflow_studio.discovery.images import image_part

    original = Image.new("I;16", (32, 16), 0)
    original.putpixel((1, 1), 1000)
    original.putpixel((2, 1), 50000)
    buffer = io.BytesIO()
    original.save(buffer, format="PNG")
    part, metadata = image_part(buffer.getvalue())
    with Image.open(
        io.BytesIO(base64.b64decode(part["image_url"]["url"].split(",", 1)[1]))
    ) as actual:
        assert actual.getpixel((1, 1)) == 1000
        assert actual.getpixel((2, 1)) == 50000
        assert actual.size == (metadata["width"], metadata["height"]) == original.size
