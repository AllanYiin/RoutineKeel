import json

import pytest
from workflow_studio.contracts import StudioError
from workflow_studio.discovery.evidence_fusion import fuse_claims
from workflow_studio.discovery.video import VideoDiscovery
from workflow_studio.llm.provider import Chunk
from workflow_studio.llm.usage import Budget


class FrameFixture:
    model = "vision-fixture"
    destination = "fixture://local-vision"
    external = False

    def __init__(self, mode="valid"):
        self.mode = mode

    def stream(self, messages, max_output_tokens):
        assert len([p for p in messages[1]["content"] if p["type"] == "image_url"]) == 5
        result = {
            "steps": [
                {"frame_index": i, "observation": f"測試影格 {i}", "confidence_label": "inferred"}
                for i in range(5)
            ],
            "unknowns": ["影格之間的動作沒有足夠證據。"],
            "conflicts": [],
        }
        if self.mode == "invented":
            result["steps"][0]["frame_index"] = 99
        if self.mode == "verified":
            result["steps"][0]["confidence_label"] = "verified"
        raw = json.dumps(result, ensure_ascii=False)
        yield Chunk(text=raw[:50])
        if self.mode == "interrupted":
            return
        yield Chunk(text=raw[50:])
        yield Chunk(done=True)


def test_video_candidates_preserve_timestamp_evidence_and_unknowns(video_studio):
    studio, p, source = video_studio
    discovery = VideoDiscovery(
        studio, FrameFixture(), Budget(3, 10_000_000, 1000, 1, 1), enabled=True
    )
    prepared = discovery.prepare(p.project_id, source.source_id)
    assert "image/jpeg;base64," in json.dumps(prepared)
    result = list(discovery.run(prepared))
    revision = studio.projects.get_revision(p.project_id, result[-1]["revision_id"])
    timeline = revision.ir["video_analysis"]["timeline"]
    assert [step["time_seconds"] for step in timeline] == [0, 1, 2, 3, 4]
    assert all(step["confidence_label"] == "inferred" for step in timeline)
    assert revision.status == "draft"
    assert revision.ir["video_analysis"]["unknowns"]


@pytest.mark.parametrize("mode", ["invented", "verified", "interrupted"])
def test_video_cannot_confirm_unknown_frames_or_partial_results(video_studio, mode):
    studio, p, source = video_studio
    discovery = VideoDiscovery(
        studio, FrameFixture(mode), Budget(3, 10_000_000, 1000, 1, 1), enabled=True
    )
    with pytest.raises(StudioError):
        list(discovery.run(discovery.prepare(p.project_id, source.source_id)))
    assert studio.projects.revisions(p.project_id) == []


def test_conflicting_sources_remain_conflicted():
    result = fuse_claims(
        [
            {"subject": "轉換工具", "claim": "工具甲", "source_id": "video"},
            {"subject": "轉換工具", "claim": "工具乙", "source_id": "log"},
        ]
    )
    assert result[0]["confidence_label"] == "conflicted"
    assert len(result[0]["evidence"]) == 2


def test_cross_source_scope_requires_explicit_selection(video_studio):
    studio, p, source = video_studio
    extra = studio.sources.import_text(
        p.project_id, 2, "日誌補充：第二步未出現在影片中。", name="日誌.txt"
    )
    discovery = VideoDiscovery(
        studio, FrameFixture(), Budget(3, 10_000_000, 1000, 1, 1), enabled=True
    )
    ordinary = discovery.prepare(p.project_id, source.source_id)
    assert "日誌補充" not in json.dumps(ordinary, ensure_ascii=False)
    selected = discovery.prepare(p.project_id, source.source_id, [extra.source_id])
    assert "日誌補充" in json.dumps(selected, ensure_ascii=False)
    assert ordinary["request_hash"] != selected["request_hash"]
