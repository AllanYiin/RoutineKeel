"""Measure actual media, independently of the synthetic host replies in the Reel trial."""

import array
import shutil
import subprocess
import wave
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not all(shutil.which(x) for x in ("ffmpeg", "ffprobe")),
    reason="media binaries unavailable",
)


def test_timeline_subtitles_cover_and_mix(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "media_test_component",
        Path(__file__).resolve().parents[1]
        / "flow_base/components/blueprint_media/src/routinekeel_media/__init__.py",
    )
    ops = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ops)

    def ff(*args):
        return subprocess.run(
            ["ffmpeg", "-v", "error", *map(str, args)],
            capture_output=True,
            check=True,
            timeout=30,
        ).stdout

    def resolve(name):
        return tmp_path / name

    def store(path):
        return path.name

    for name in ("lead-in.wav", "guest-in.wav"):
        with wave.open(str(resolve(name)), "wb") as audio:
            audio.setparams((1, 2, 24000, 0, "NONE", ""))
            audio.writeframes(array.array("h", [9000, -9000] * 6000).tobytes())
    segments = [
        {"role": "lead", "text": "hello", "audio": "lead-in.wav"},
        {"role": "other", "text": "world", "audio": "guest-in.wav"},
    ]
    made = ops.audio_timeline({"segments": segments}, tmp_path, resolve, store)
    assert [row["start"] for row in made["timeline"]] == [0, 0.5]
    for key in ("lead", "other"):
        with wave.open(str(resolve(made[key])), "rb") as audio:
            a, b = audio.readframes(12000), audio.readframes(12000)
            assert (any(a), any(b)) == (
                (True, False) if key == "lead" else (False, True)
            )
    subs = ops.subtitles(
        {"timeline": made["timeline"], "line_limit": 3, "split": "proportional"},
        tmp_path,
        resolve,
        store,
    )
    assert [c["start"] for c in subs["cues"]] == [0, 0.3, 0.5, 0.8]
    # Keep the managed source separate from the temporary compositor directory.
    folder = tmp_path / "render"
    folder.mkdir()
    ff(
        "-f",
        "lavfi",
        "-i",
        "color=red:s=160x240",
        "-frames:v",
        "1",
        resolve("cover.png"),
    )
    ff("-f", "lavfi", "-i", "color=blue:s=160x240:r=25", "-t", "1", resolve("body.mp4"))
    result = ops.compose(
        {
            "width": 160,
            "height": 240,
            "max_seconds": 2,
            "cover_seconds": 0.4,
            "video": "body.mp4",
            "cover": "cover.png",
            "captions": subs["captions"],
            "lead": made["lead"],
            "other": made["other"],
        },
        folder,
        resolve,
        store,
    )
    video = folder / result["file"]
    assert abs(ops.probe(video)["seconds"] - 1.4) <= 0.08

    def pixel(t):
        return ff(
            "-ss",
            str(t),
            "-i",
            video,
            "-frames:v",
            "1",
            "-vf",
            "crop=2:2:0:0,scale=1:1",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
        )

    red, blue = pixel(0.2), pixel(0.6)
    assert red[0] > 180 and red[2] < 50 and blue[0] < 50 and blue[2] > 180

    # Burn-in appears only in the subtitle region after the cover.
    def highlights(t):
        frame = ff(
            "-ss",
            str(t),
            "-i",
            video,
            "-frames:v",
            "1",
            "-vf",
            "crop=160:80:0:160",
            "-pix_fmt",
            "gray",
            "-f",
            "rawvideo",
            "pipe:1",
        )
        return sum(v > 150 for v in frame)

    assert highlights(0.2) == 0 and highlights(0.6) > 0
