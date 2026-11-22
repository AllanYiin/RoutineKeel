import shutil

import pytest
from workflow_studio.contracts import StudioError
from workflow_studio.ingestion.video import VideoIngestion
from workflow_studio.service import StudioService

pytest.importorskip("PIL", reason="Video preview requires Studio image dependencies")


def test_actual_video_preview_timestamps_and_aspect_ratio(video_studio):
    studio, project, source = video_studio
    assert source.kind == "video"
    metadata = source.manifest["video"]
    assert metadata["duration_seconds"] == 5
    assert [f["time_seconds"] for f in metadata["frames"]] == [0, 1, 2, 3, 4]
    records = {r["path"]: r for r in source.manifest["files"]}
    for frame in metadata["frames"]:
        preview = records[frame["path"]]["preview"]
        assert preview["width"] / preview["height"] == 320 / 180
        assert studio.sources.content(project.project_id, source.source_id, frame["path"])


def test_video_disabled_by_default_and_corruption_rejected(tmp_path):
    with StudioService.open_workspace(tmp_path / "studio") as studio:
        p = studio.projects.create("影片")
        with pytest.raises(StudioError) as caught:
            VideoIngestion(studio).import_path(p.project_id, 1, "unused.mp4")
        assert caught.value.code == "FEATURE_DISABLED"
        if shutil.which("ffprobe") and shutil.which("ffmpeg"):
            path = tmp_path / "broken.mp4"
            path.write_bytes(b"not a video")
            with pytest.raises(StudioError) as caught:
                VideoIngestion(studio, enabled=True).import_path(p.project_id, 1, path)
            assert caught.value.code == "SOURCE_PREVIEW_UNAVAILABLE"
        assert studio.sources.list(p.project_id) == []
