import zipfile

import pytest
from ai_flow01 import course_graph
from flow_base.blueprints.sessions import Assets
from flow_base.runtime import FlowError, Runtime, atomic_json, read_json
from routinekeel_contracts.bindings import ImplementationAdapter


@pytest.mark.parametrize("device,profile", [("cpu", "asr-cpu"), ("cuda", "asr-cuda")])
def test_course_graph_maps_clips_to_the_pinned_worker_and_preserves_per_clip_language(
    tmp_path, monkeypatch, device, profile,
):
    runtime = Runtime(tmp_path / "share_states")
    assets = Assets(runtime)
    monkeypatch.setattr(course_graph, "inspect_environment", lambda _: {"gpu": {"status": "detected"}})
    course_graph.configure_asr(runtime, device=device)
    profile_root, python = runtime.environment(profile)
    python.parent.mkdir(parents=True)
    python.touch()
    atomic_json(profile_root / "location.json", {"path": str(profile_root)})
    archive = tmp_path / "speech.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("first.wav", b"fixture1")
        bundle.writestr("second.wav", b"fixture2")
    model = tmp_path / "pinned-model"
    checkpoint_dir = tmp_path / "graph-session-checkpoint"
    resource = course_graph.catalogue()[course_graph.MODEL]

    def cached(runtime, selected, *, offline):
        assert selected.id == "whisper-large-v3"
        assert offline
        return model

    def worker(runtime, selected_profile, request, **kwargs):
        assert selected_profile == profile
        assert request["model_path"] == str(model) and request["model_revision"] == resource.revision
        assert request["language"] is None and request["allow_cpu_fallback"] is False
        assert request["device"] == device
        manifest = read_json(course_graph.Path(request["vad_path"]))
        assert [(r["start"], r["end"]) for r in manifest["regions"]] == [(1.0, 2.0), (4.0, 5.0)]
        assert [r["archive_member"] for r in manifest["regions"]] == ["first.wav", "second.wav"]
        assert course_graph.Path(request["speech_archive"]) == assets.resolve(inputs["speech_clips"])
        output = course_graph.Path(request["output"])
        assert not list(output.parent.glob("*.wav"))
        checkpoints = course_graph.Path(request["checkpoints"])
        assert checkpoints == checkpoint_dir / "region_checkpoints"
        checkpoints.mkdir(parents=True)
        for i, language in enumerate(("zh", "en")):
            atomic_json(checkpoints / f"region_{i:05}.json", {"result": {"language": language}})
        atomic_json(output, {
            "device": device,
            "segments": [
                {"id": 0, "start": 1.1, "end": 1.8, "words": [{"start": 1.1, "end": 1.3, "word": "中文"}]},
                {"id": 1, "start": 4.2, "end": 4.7, "words": []},
            ],
            "recognition_metrics": {"0": {"region_id": 0}, "1": {"region_id": 1}},
            "recognizer_text": {"0": "中文", "1": "Keep Kubernetes"},
        })

    monkeypatch.setattr(course_graph, "ensure_resource", cached)
    monkeypatch.setattr(course_graph.Runtime, "worker", worker)
    inputs = {
        "speech_clips": assets.store(archive),
        "clip_map": [
            {"id": "first", "member": "first.wav", "start_ms": 1000, "end_ms": 2000},
            {"id": "second", "member": "second.wav", "start_ms": 4000, "end_ms": 5000},
        ],
        "glossary": {"terms": [{"term": "Kubernetes"}]},
    }
    actual = course_graph.transcribe(inputs, {"device": device}, assets=assets, checkpoint_dir=checkpoint_dir)
    assert [(row["id"], row["language"], row["start_ms"]) for row in actual["segments"]] == [
        ("first-0", "zh", 1100), ("second-1", "en", 4200),
    ]
    assert actual["segments"][0]["words"] == [{"start": 0.1, "end": 0.3, "word": "中文"}]
    assert {row["actual_device"] for row in actual["segments"]} == {device}
    assert (checkpoint_dir / "region_checkpoints" / "region_00000.json").is_file()
    assert actual["editing_input"][1] == {
        "id": "second-1", "original": "Keep Kubernetes", "language": "en", "terms": ["Kubernetes"],
    }
    descriptor, = read_json(course_graph.Path(course_graph.__file__).with_suffix(".adapters.json")).values()
    assert ImplementationAdapter.model_validate(descriptor).model_version == resource.revision


def test_course_graph_without_the_pinned_model_does_not_switch_or_dispatch(tmp_path, monkeypatch):
    assets = Assets(Runtime(tmp_path / "share_states"))
    monkeypatch.setattr(course_graph, "inspect_environment", lambda _: {})
    monkeypatch.setattr(course_graph.Runtime, "worker", lambda *_args, **_kwargs: pytest.fail("worker dispatched"))
    with pytest.raises(FlowError) as error:
        course_graph.transcribe({}, {}, assets=assets)
    assert error.value.code == "OFFLINE_RESOURCE_MISSING"
    assert error.value.details["resource"] == "whisper-large-v3"


@pytest.mark.parametrize("record_path", [None, "before-the-profile-was-moved"])
def test_course_graph_does_not_dispatch_with_an_incomplete_profile(tmp_path, monkeypatch, record_path):
    runtime = Runtime(tmp_path / "share_states")
    assets = Assets(runtime)
    monkeypatch.setattr(course_graph, "inspect_environment", lambda _: {})
    course_graph.configure_asr(runtime)
    profile_root, python = runtime.environment("asr-cpu")
    python.parent.mkdir(parents=True)
    python.touch()
    if record_path is not None:
        atomic_json(profile_root / "location.json", {"path": record_path})
    monkeypatch.setattr(course_graph, "ensure_resource", lambda *_args, **_kwargs: tmp_path / "model")
    monkeypatch.setattr(course_graph.Runtime, "worker", lambda *_args, **_kwargs: pytest.fail("worker dispatched"))

    with pytest.raises(FlowError) as error:
        course_graph.transcribe({}, {}, assets=assets)

    assert error.value.code == "ENV_MISSING"
    assert error.value.details["profile"] == "asr-cpu"


def test_course_graph_suggests_detected_gpu_without_dispatch_until_confirmed(tmp_path, monkeypatch):
    runtime = Runtime(tmp_path / "share_states")
    gpu = {"status": "detected", "cuda": {"status": "available"}}
    monkeypatch.setattr(course_graph, "inspect_environment", lambda _: {"gpu": gpu})
    monkeypatch.setattr(course_graph, "ensure_resource", lambda *args, **kwargs: pytest.fail("model accessed"))
    monkeypatch.setattr(course_graph.Runtime, "worker", lambda *args, **kwargs: pytest.fail("worker dispatched"))

    with pytest.raises(FlowError) as error:
        course_graph.transcribe({}, {}, assets=Assets(runtime))

    assert error.value.code == "COMPUTE_DEVICE_CONFIRMATION_REQUIRED"
    assert error.value.details == {"recommended_device": "cuda", "choices": ["cuda", "cpu"]}
    gpu["cuda"]["status"] = "disabled"
    assert course_graph.configure_asr(runtime)["selected"] == {"profile": "asr-cpu", "device": "cpu"}
