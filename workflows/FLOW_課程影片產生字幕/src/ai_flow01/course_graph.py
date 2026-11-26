"""Bridge the course Graph's speech clips to the existing faster-whisper Worker."""

import tempfile
import zipfile
from pathlib import Path

from flow_base.outputs import reject_links
from flow_base.resources import ensure_resource
from flow_base.runtime import FlowError, Runtime, atomic_json, inspect_environment, read_json

from .adapter import resolve_environment
from .contracts import Options, subtitle_workflow
from .resources import catalogue

MODEL = "whisper-large-v3"
MAX_CLIP_BYTES = 2 * 1024**2


def configure_asr(runtime, *, device="auto"):
    workflow = subtitle_workflow()
    workflow.options = Options(device=device)
    workflow = resolve_environment(workflow, inspect_environment(runtime.home))
    if device == "auto" and workflow.options.device == "cuda":
        raise FlowError(
            "COMPUTE_DEVICE_CONFIRMATION_REQUIRED",
            "已偵測到 CUDA GPU。建議用 CUDA 執行字幕辨識，請確認裝置後續做。",
            recommended_device="cuda", choices=["cuda", "cpu"],
        )
    package = Path(__file__).parent
    runtime.register_profile(workflow.profile, package / "profiles" / workflow.profile)
    runtime.workers[workflow.profile] = package / "worker.py"
    return workflow.execution_selection


def _vad_manifest(clips, archive):
    regions = []
    seen = set()
    for index, clip in enumerate(clips):
        key, start, end = clip["id"], clip["start_ms"], clip["end_ms"]
        if (not isinstance(key, str) or not key or key in seen
                or type(start) is not int or type(end) is not int or not 0 <= start < end):
            raise ValueError("VAD 片段的識別碼或時間範圍無效。")
        seen.add(key)
        member = archive.getinfo(clip["member"])
        if member.is_dir() or not member.filename.endswith(".wav") or not 0 < member.file_size <= MAX_CLIP_BYTES:
            raise ValueError("VAD 片段超出支援的 WAV 範圍。")
        regions.append({
            "id": index, "start": start / 1000, "end": end / 1000,
            "archive_member": member.filename, "size": member.file_size,
        })
    if not regions:
        raise ValueError("沒有可辨識的 VAD 片段。")
    return {"duration": max(region["end"] for region in regions), "regions": regions}


def _outputs(result, clips, terms, checkpoints):
    segments = []
    languages = {}
    for row in result["segments"]:
        key = str(row["id"])
        region = result["recognition_metrics"][key]["region_id"]
        clip = clips[region]
        if region not in languages:
            saved = read_json(checkpoints / f"region_{region:05}.json")
            languages[region] = saved["result"]["language"]
        start = max(clip["start_ms"], round(row["start"] * 1000))
        end = min(clip["end_ms"], round(row["end"] * 1000))
        text = result["recognizer_text"][key].strip()
        if not text:
            continue
        if segments and start < segments[-1]["end_ms"]:
            if segments[-1]["text"].casefold() == text.casefold():
                continue
            start = segments[-1]["end_ms"]
        if end <= start:
            continue
        offset = clip["start_ms"] / 1000
        segments.append({
            "id": f"{clip['id']}-{key}", "text": text, "language": languages[region],
            "actual_device": result["device"],
            "start_ms": start, "end_ms": end, "clip_start_ms": clip["start_ms"], "clip_end_ms": clip["end_ms"],
            "words": [
                {**word, "start": round(word["start"] - offset, 6), "end": round(word["end"] - offset, 6)}
                for word in row["words"]
            ],
        })
    if not segments:
        raise FlowError("ADAPTER_INPUT_REJECTED", "VAD 片段沒有可辨識語音。")
    return {"segments": segments, "editing_input": [
        {"id": segment["id"], "original": segment["text"], "language": segment["language"],
         "terms": [term for term in terms if term.casefold() in segment["text"].casefold()]}
        for segment in segments
    ]}


def transcribe(inputs, parameters, *, assets, checkpoint_dir=None):
    """Run the pinned offline profile, returning source-timeline segments and minimal editing input."""
    runtime = Runtime(assets.root.parent)
    selection = configure_asr(runtime, device=parameters.get("device", "auto"))["selected"]
    profile, device = selection["profile"], selection["device"]
    resource = catalogue()[MODEL]
    model = ensure_resource(runtime, resource, offline=True)
    if not runtime.profile_ready(profile):
        raise FlowError("ENV_MISSING", "字幕轉錄的 Python profile 尚未準備完成。", profile=profile)
    clips = inputs["clip_map"]
    terms = [row["term"] for row in inputs["glossary"]["terms"]][:20]
    archive_path = assets.resolve(inputs["speech_clips"])
    with tempfile.TemporaryDirectory(prefix="course-graph-asr-", dir=runtime.home) as directory:
        folder = Path(directory)
        manifest, output = folder / "vad.json", folder / "asr.json"
        checkpoints = (checkpoint_dir or folder) / "region_checkpoints"
        reject_links(checkpoints)
        try:
            with zipfile.ZipFile(archive_path) as archive:
                atomic_json(manifest, _vad_manifest(clips, archive))
        except (ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
            raise FlowError("ADAPTER_INPUT_REJECTED", "VAD 封存檔或片段映射不符。") from exc
        runtime.worker(profile, {
            "action": "transcribe", "vad_path": str(manifest), "output": str(output),
            "checkpoints": str(checkpoints), "speech_archive": str(archive_path),
            "model": MODEL, "model_path": str(model), "model_revision": resource.revision,
            "device": device, "allow_cpu_fallback": False, "traditional_chinese": False,
            "language": None, "beam_size": 5, "prompt": ", ".join(terms),
        }, timeout=Options().worker_timeout_seconds, log=(checkpoint_dir or folder) / "worker.log")
        result = read_json(output)
        if result["device"] != device:
            raise FlowError("ASR_DEVICE_MISMATCH", "實際辨識裝置與選定裝置不同。",
                            expected=device, actual=result["device"])
        return _outputs(result, clips, terms, checkpoints)
