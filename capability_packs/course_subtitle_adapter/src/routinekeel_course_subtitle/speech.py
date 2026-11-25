"""Bind the course blueprint's WAV, clip ZIP and transcript ports to local models."""

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from zipfile import ZIP_STORED, BadZipFile, ZipFile, ZipInfo

from flow_base.runtime import FlowError

RATE = 16_000
VAD_SETTINGS = {"threshold": 0.3, "min_speech_duration_ms": 100,
                "min_silence_duration_ms": 350, "speech_pad_ms": 200,
                "max_speech_duration_s": 28.0}
MODEL_HASH = "ed3a0b6b1c0edf879ad9b11b1af5a0e6ab5db9205f891f668f8b0e6c6326e34e"


def _model_python(distribution):
    try:
        version(distribution)
        return sys.executable
    except PackageNotFoundError:
        executable = shutil.which("whisper")
        if executable:
            scripts = Path(executable).resolve().parent
            candidates = [scripts / "python", scripts / "python3", scripts / "python.exe",
                          scripts.parent / "python.exe"]
            for candidate in candidates:
                if candidate.is_file():
                    return str(candidate)
    raise FlowError("CAPABILITY_UNAVAILABLE", f"找不到既有 {distribution} 執行環境。")


def _run_model(operation, data):
    distribution = "faster-whisper" if operation == "vad" else "openai-whisper"
    python = _model_python(distribution)
    helper = Path(__file__).with_name("speech_worker.py")
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        proc = subprocess.run(
            [python, "-I", "-X", "utf8", str(helper), operation],
            input=json.dumps(data).encode(), capture_output=True,
            timeout=50 if operation == "vad" else 1800, check=False, **kwargs,
        )
        if proc.returncode:
            raise ValueError("model process failed")
        return json.loads(proc.stdout)
    except subprocess.TimeoutExpired as exc:
        raise FlowError("ADAPTER_TIMEOUT", f"{distribution} 執行逾時。") from exc
    except (OSError, ValueError) as exc:
        raise FlowError("CAPABILITY_UNAVAILABLE", f"無法執行鎖定版本的 {distribution}。") from exc


def _wav(stream):
    try:
        audio = wave.open(stream, "rb")
    except (wave.Error, EOFError) as exc:
        raise ValueError("素材不是有效的 PCM WAV") from exc
    if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (RATE, 1, 2):
        audio.close()
        raise ValueError("需要 16 kHz 單聲道 PCM16 WAV")
    return audio


def _original_ms(start_ms, sample):
    return start_ms + round(sample * 1000 / RATE)


def _write_clip(archive, source, member, start, stop):
    source.setpos(start)
    with io.BytesIO() as payload:
        with wave.open(payload, "wb") as out:
            out.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
            out.writeframes(source.readframes(stop - start))
        item = ZipInfo(member)
        item.create_system = 0
        archive.writestr(item, payload.getvalue())


def detect_speech(inputs, parameters, *, assets):
    source = assets.resolve(inputs["audio"])
    start_ms = inputs["media"].get("audio_start_ms", 0)
    if type(start_ms) is not int:
        raise ValueError("audio_start_ms 須為整數毫秒")
    clips = []
    with _wav(str(source)) as audio, tempfile.TemporaryDirectory(prefix="rk-speech-") as temp:
        detected = _run_model("vad", {"audio": str(source), "settings": VAD_SETTINGS})
        total = audio.getnframes()
        path = Path(temp) / "speech.zip"
        previous = 0
        with ZipFile(path, "w", compression=ZIP_STORED) as archive:
            for region in detected:
                start, stop = region["start"], min(region["end"], total)
                if not previous <= start < stop:
                    raise ValueError("VAD 區間重疊或超出音軌")
                identifier = f"clip-{len(clips) + 1:05d}"
                member = identifier + ".wav"
                _write_clip(archive, audio, member, start, stop)
                clips.append({"id": identifier, "member": member, "start_sample": start,
                              "end_sample": stop, "start_ms": _original_ms(start_ms, start),
                              "end_ms": _original_ms(start_ms, stop)})
                previous = stop
        speech_clips = assets.store(path)

    selected = sum(row["end_sample"] - row["start_sample"] for row in clips)
    return {"clip_map": clips, "speech_clips": speech_clips, "vad_report": {
        "model": "faster-whisper-1.2.0/silero-vad", "settings": VAD_SETTINGS,
        "sample_rate": RATE, "total_samples": total, "selected_samples": selected,
        "excluded_samples": total - selected, "has_speech": bool(clips),
    }}


def _clip_files(archive, clip_map, directory):
    entries = archive.infolist()
    names = [row["member"] for row in clip_map]
    if len(set(names)) != len(names) or sorted(row.filename for row in entries) != sorted(names):
        raise ValueError("語音 ZIP 成員與片段映射不符")
    files = []
    previous = 0
    for i, row in enumerate(clip_map):
        start, end = row["start_sample"], row["end_sample"]
        start_ms, end_ms = row["start_ms"], row["end_ms"]
        if not (previous <= start < end and end - start <= 29 * RATE and start_ms < end_ms):
            raise ValueError("語音片段時間映射無效")
        member = archive.getinfo(row["member"])
        if not 44 <= member.file_size <= 64 + (end - start) * 2:
            raise ValueError("語音片段大小不符")
        data = archive.read(member)
        with _wav(io.BytesIO(data)) as audio:
            if audio.getnframes() != end - start:
                raise ValueError("語音片段長度與樣本數不符")
        target = directory / f"clip-{i}.wav"
        target.write_bytes(data)
        files.append(str(target))
        previous = end
    return files


def _model_path():
    path = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "whisper" / "base.pt"
    try:
        with path.open("rb") as model:
            actual = hashlib.file_digest(model, "sha256").hexdigest()
    except OSError as exc:
        raise FlowError("CAPABILITY_UNAVAILABLE", "找不到已驗證的本機 Whisper base.pt。") from exc
    if actual != MODEL_HASH:
        raise FlowError("CAPABILITY_UNAVAILABLE", "本機 Whisper base.pt 的 SHA-256 不符。")
    return path


def _term_hints(glossary):
    values = glossary.get("terms", [])
    terms = [row.get("term", "") if isinstance(row, dict) else row for row in values]
    return [term.strip() for term in terms if isinstance(term, str) and term.strip()][:30]


def _absolute(item, clip):
    start = max(clip["start_ms"], clip["start_ms"] + round(item["start"] * 1000))
    end = min(clip["end_ms"], clip["start_ms"] + round(item["end"] * 1000))
    return {"start_ms": start, "end_ms": end}


def transcribe(inputs, parameters, *, assets):
    clips = inputs["clip_map"]
    if not clips:
        raise FlowError("ADAPTER_INPUT_REJECTED", "VAD 未選到人聲，語音辨識已停止。")
    terms = _term_hints(inputs["glossary"])
    path = assets.resolve(inputs["speech_clips"])
    try:
        with ZipFile(path) as archive, tempfile.TemporaryDirectory(prefix="rk-whisper-") as temp:
            files = _clip_files(archive, clips, Path(temp))
            results = _run_model("transcribe", {"clips": files, "model": str(_model_path()),
                                               "prompt": ", ".join(terms)[:300]})
    except BadZipFile as exc:
        raise ValueError("人聲片段 ZIP 損毀") from exc
    editing, segments = [], []
    for clip, result in zip(clips, results, strict=True):
        for item in result["segments"]:
            text = item["text"].strip()
            timing = _absolute(item, clip)
            if not text or timing["start_ms"] >= timing["end_ms"]:
                continue
            identifier = f"segment-{len(segments) + 1:05d}"
            words = [{"word": word["word"], **_absolute(word, clip)} for word in item["words"]]
            language = result["language"]
            segments.append({"id": identifier, "clip_id": clip["id"], "text": text,
                             "language": language, **timing, "words": [
                                 word for word in words if word["start_ms"] < word["end_ms"]
                             ]})
            editing.append({"id": identifier, "text": text, "language": language,
                            "terms": [term for term in terms if term.casefold() in text.casefold()]})
    if not segments:
        raise FlowError("ADAPTER_INPUT_REJECTED", "人聲片段沒有可辨識的文字。")
    return {"segments": segments, "editing_input": editing}
