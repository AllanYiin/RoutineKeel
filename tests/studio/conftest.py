import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "workflow_studio/src"))


@pytest.fixture
def video_studio(tmp_path):
    import shutil
    import subprocess

    from workflow_studio.ingestion.video import VideoIngestion
    from workflow_studio.service import StudioService

    pytest.importorskip("PIL", reason="Video preview requires Studio image dependencies")

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not shutil.which("ffprobe"):
        pytest.skip("P2 video requires ffmpeg/ffprobe; feature remains disabled")
    path = tmp_path / "操作 測試.mp4"
    run = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=1:duration=5",
            "-c:v",
            "mpeg4",
            "-movflags",
            "+faststart",
            str(path),
        ],
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert run.returncode == 0, run.stderr.decode(errors="replace")
    with StudioService.open_workspace(tmp_path / "studio") as studio:
        project = studio.projects.create(
            "操作影片", {"description": "觀察操作", "success_criteria": ["來源可追溯"]}
        )
        source = VideoIngestion(studio, enabled=True).import_path(project.project_id, 1, path)
        yield studio, project, source
