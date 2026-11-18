"""Opt-in, bounded video evidence; input and frame output use pipes only."""

import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path

from ..contracts import StudioError
from .models import ImportLimits, Preview
from .preview import preview_file
from .security import read_file
from .service import SourceService


class VideoIngestion:
    def __init__(self, studio, *, enabled=False, ffmpeg=None, ffprobe=None):
        self.studio, self.enabled = studio, enabled
        self.ffmpeg, self.ffprobe = (
            ffmpeg or shutil.which("ffmpeg"),
            ffprobe or shutil.which("ffprobe"),
        )

    def import_path(self, project_id, expected_version, path, *, frames=5):
        if not self.enabled:
            raise StudioError("FEATURE_DISABLED", "影片分析仍為實驗功能，預設停用。")
        if not self.ffmpeg or not self.ffprobe:
            raise StudioError("PLATFORM_UNSUPPORTED", "影片預覽需要可用的 FFmpeg／ffprobe。")
        if type(frames) is not int or not 1 <= frames <= 20:
            raise StudioError("INPUT_INVALID", "代表影格數須介於 1 與 20。")
        path = Path(path).absolute()
        if path.suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm"}:
            raise StudioError("SOURCE_PREVIEW_UNAVAILABLE", "僅接受 MP4／MOV／MKV／WebM。")
        raw = read_file(path, path.parent, 100 * 1024 * 1024)
        try:
            probe = subprocess.run(
                [
                    str(self.ffprobe),
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "pipe",
                    "-show_streams",
                    "-show_format",
                    "-of",
                    "json",
                    "-i",
                    "pipe:0",
                ],
                input=raw,
                capture_output=True,
                timeout=15,
                check=True,
            )
            metadata = json.loads(probe.stdout)
            video = next(s for s in metadata["streams"] if s["codec_type"] == "video")
            duration = float(video.get("duration") or metadata["format"]["duration"])
            width, height = int(video["width"]), int(video["height"])
            if (
                not math.isfinite(duration)
                or not 0 < duration <= 600
                or not 0 < width * height <= 20_000_000
            ):
                raise StudioError("INPUT_INVALID", "影片限 10 分鐘內、2000 萬像素以下。")
            files, timeline = {path.name: raw}, []
            for index in range(frames):
                timestamp = duration * index / frames
                command = [
                    str(self.ffmpeg),
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "pipe",
                    "-i",
                    "pipe:0",
                    "-ss",
                    str(timestamp),
                    "-frames:v",
                    "1",
                    "-an",
                    "-vf",
                    "scale=640:-2:force_original_aspect_ratio=decrease",
                    "-f",
                    "image2pipe",
                    "-vcodec",
                    "mjpeg",
                    "pipe:1",
                ]
                frame = subprocess.run(
                    command, input=raw, capture_output=True, timeout=15, check=True
                ).stdout
                name = f"previews/frame-{index:03d}.jpg"
                preview_file(name, frame, ImportLimits())
                files[name] = frame
                timeline.append(
                    {
                        "index": index,
                        "time_seconds": timestamp,
                        "path": name,
                        "sha256": hashlib.sha256(frame).hexdigest(),
                    }
                )
        except (subprocess.SubprocessError, OSError, ValueError, KeyError, StopIteration) as exc:
            raise StudioError(
                "SOURCE_PREVIEW_UNAVAILABLE", "影片無法安全讀取或抽取代表影格。"
            ) from exc
        limits = ImportLimits(max_file_bytes=100 * 1024 * 1024, max_total_bytes=120 * 1024 * 1024)
        return SourceService(self.studio.repository, limits)._save(
            project_id,
            expected_version,
            files,
            path.name,
            None,
            previews={
                path.name: Preview(
                    "video",
                    text=f"{duration:.2f} 秒，{width} × {height}；{frames} 個代表影格",
                    media_type="video/" + path.suffix[1:],
                    width=width,
                    height=height,
                )
            },
            extra_metadata={
                "experimental": True,
                "video": {
                    "duration_seconds": duration,
                    "width": width,
                    "height": height,
                    "frames": timeline,
                },
            },
            source_kind="video",
        )
