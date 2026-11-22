"""Fixed local media operations. Generated recipes supply data, never ffmpeg arguments."""

import json
import shutil
import subprocess
import wave


def require(condition, message):
    if not condition:
        raise ValueError(message)


FORMATS = {
    ".mp4": "mov",
    ".png": "png_pipe",
    ".jpg": "jpeg_pipe",
    ".wav": "wav",
    ".mp3": "mp3",
    ".m4a": "mov",
    ".srt": "srt",
}


def command(tool, args, cwd):
    executable = shutil.which(tool)
    require(executable is not None, f"找不到 {tool}")
    proc = subprocess.run(
        [executable, "-v", "error", *map(str, args)],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=120,
    )
    require(proc.returncode == 0, f"{tool} 無法處理媒體 (exit {proc.returncode})")
    return proc.stdout


def source(path):
    require(path.suffix in FORMATS, "不支援的素材格式")
    return ["-protocol_whitelist", "file", "-f", FORMATS[path.suffix], "-i", path]


def probe(path):
    data = json.loads(
        command(
            "ffprobe",
            [
                *source(path),
                "-show_entries",
                "format=duration:stream=codec_type,width,height",
                "-of",
                "json",
            ],
            path.parent,
        )
    )
    return {"seconds": float(data["format"]["duration"]), "streams": data["streams"]}


def audio_timeline(data, folder, resolve, store):
    parts = data["segments"]
    require(0 < len(parts) <= 300, "音軌段落數不合法")
    timeline, frames = [], 0
    with (
        wave.open(str(folder / "lead.wav"), "wb") as lead,
        wave.open(str(folder / "other.wav"), "wb") as other,
    ):
        for writer in (lead, other):
            writer.setparams((1, 2, 24000, 0, "NONE", ""))
        for segment in parts:
            voice = segment["role"]
            require(
                voice in ("lead", "other") and isinstance(segment["text"], str),
                "配音段落格式不合法",
            )
            audio = resolve(segment["audio"])
            seconds = probe(audio)["seconds"]
            require(
                0 < seconds <= 300 and frames / 24000 + seconds <= 600,
                "音軌超過時間上限",
            )
            pcm = command(
                "ffmpeg",
                [
                    *source(audio),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "24000",
                    "-t",
                    "301",
                    "-f",
                    "s16le",
                    "pipe:1",
                ],
                folder,
            )
            require(len(pcm) > 0 and len(pcm) % 2 == 0, "無法解碼配音")
            start = frames / 24000
            frames += len(pcm) // 2
            for writer, audible in ((lead, voice == "lead"), (other, voice == "other")):
                writer.writeframes(pcm if audible else bytes(len(pcm)))
            timeline.append(
                {
                    "role": voice,
                    "start": start,
                    "end": frames / 24000,
                    "text": segment["text"],
                }
            )
    return {
        "lead": store(folder / "lead.wav"),
        "other": store(folder / "other.wav"),
        "timeline": timeline,
        "seconds": frames / 24000,
    }


def stamp(seconds):
    millis = round(seconds * 1000)
    return f"{millis // 3600000:02}:{millis // 60000 % 60:02}:{millis // 1000 % 60:02},{millis % 1000:03}"


def subtitles(data, folder, resolve, store):
    width = data["line_limit"]
    require(type(width) is int and 1 <= width <= 120, "字幕字數上限無效")
    prev = 0
    cues, lines = [], []
    for seg in data["timeline"]:
        start, end, content = seg["start"], seg["end"], seg["text"]
        require(
            prev <= start < end <= 600 and isinstance(content, str) and content.strip(),
            "字幕時段不合法",
        )
        pieces = [content[i : i + width] for i in range(0, len(content), width)]
        require(
            len(pieces) == 1 or data.get("split") == "proportional",
            "請確認長字幕的時間分配方式",
        )
        for i, text in enumerate(pieces):
            a, b = [
                start + (end - start) * min(j * width, len(content)) / len(content)
                for j in (i, i + 1)
            ]
            require(round(b * 1000) > round(a * 1000), "字幕時間太短")
            cues.append({"start": a, "end": b, "text": text})
            lines.append(f"{len(cues)}\n{stamp(a)} --> {stamp(b)}\n{text}\n")
        prev = end
    require(cues, "沒有字幕")
    path = folder / "captions.srt"
    path.write_text("\n".join(lines), encoding="utf-8")
    return {"captions": store(path), "cues": cues}


def compose(data, folder, resolve, store):
    width, height = data["width"], data["height"]
    cover_s = data["cover_seconds"]
    require(
        all(type(n) is int and 16 <= n <= 1920 and n % 2 == 0 for n in (width, height))
        and width < height
        and 0 < cover_s <= 10,
        "請提供直式尺寸與封面時長",
    )
    video, cover = resolve(data["video"]), resolve(data["cover"])
    require(
        video.suffix == ".mp4" and cover.suffix in {".png", ".jpg"},
        "影片或封面格式不符",
    )
    secs = probe(video)["seconds"]
    require(
        0 < secs and secs + cover_s <= min(data["max_seconds"], 600), "成片超過長度上限"
    )
    audios = [resolve(data[k]) for k in ("lead", "other")]
    require(
        all(abs(probe(a)["seconds"] - secs) <= 0.12 for a in audios),
        "音軌與影片長度不符",
    )
    subs = resolve(data["captions"])
    require(subs.suffix == ".srt", "需要 SRT 字幕")
    shutil.copyfile(subs, folder / "captions.srt")
    args = [
        "-loop",
        "1",
        "-framerate",
        "25",
        "-t",
        str(cover_s),
        *source(cover),
        *source(video),
    ]
    for audio in audios:
        args.extend(source(audio))
    shape = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=25,format=yuv420p"
    filters = [
        f"[0:v]{shape}[cover]",
        f"[1:v]{shape},subtitles=captions.srt[body]",
        "[cover][body]concat=n=2:v=1:a=0[v]",
    ]
    tracks = "[2:a][3:a]"
    count = 2
    if data.get("music"):
        args.extend(source(resolve(data["music"])))
        filters.append("[4:a]volume=0.15[bg]")
        tracks += "[bg]"
        count += 1
    filters.append(
        f"{tracks}amix=inputs={count}:duration=first:normalize=0,adelay={round(cover_s * 1000)}:all=1[a]"
    )
    dest = folder / "final.mp4"
    command(
        "ffmpeg",
        [
            *args,
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-threads",
            "2",
            dest,
        ],
        folder,
    )
    result = probe(dest)
    require(
        any(
            s.get("width") == width and s.get("height") == height
            for s in result["streams"]
        )
        and any(s["codec_type"] == "audio" for s in result["streams"])
        and abs(result["seconds"] - (cover_s + secs)) <= 0.15,
        "成片驗證失敗",
    )
    return {
        "file": store(dest),
        "seconds": result["seconds"],
        "width": width,
        "height": height,
    }
