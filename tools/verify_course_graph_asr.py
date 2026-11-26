"""Offline acceptance for the installed course Graph faster-whisper adapter."""

import argparse
import json
import re
import sys
import time
import wave
import zipfile
from io import BytesIO
from pathlib import Path

from ai_flow01.course_graph import MODEL, catalogue, configure_asr
from flow_base.blueprints.sessions import Assets
from flow_base.graph.portable import installed_local_tools, local_entry
from flow_base.graph.qualification import execute_local
from flow_base.resources import check_resource
from flow_base.runtime import FlowError, sha256
from flow_base.workspace import Workspace
from routinekeel_contracts.bindings import ImplementationAdapter
from routinekeel_contracts.domain_assets import content_hash

TOOL = "course.local.transcribe_faster_whisper"
FIXTURE_SHA256 = "2869b5a810bcfe3004276861680b0d764b5573fdf3804b3072b517af72d7f2dc"
OFFSETS = (1_000, 4_000)
SPEECH_PATTERN = r"\b[Dd]o not exceed (?:three|3) milliseconds\b"


def _clip_duration(archive):
    if sha256(archive) != FIXTURE_SHA256:
        raise ValueError("語音測具的 SHA-256 不符。")
    with zipfile.ZipFile(archive) as bundle, wave.open(BytesIO(bundle.read("clip-00001.wav")), "rb") as audio:
        return round(audio.getnframes() * 1000 / audio.getframerate())


def _checks(outputs, duration, device):
    clips = digits = 0
    for index, start in enumerate(OFFSETS):
        rows = [row for row in outputs["segments"] if row["id"].startswith(f"fixture-{index}-")]
        transcript = " ".join(row["text"] for row in rows)
        if re.search(SPEECH_PATTERN, transcript):
            digits += 1
        if rows and all(
            row["language"] == "en"
            and row["clip_start_ms"] == start
            and row["clip_end_ms"] == start + duration
            and start <= row["start_ms"] < row["end_ms"] <= start + duration
            and row["words"]
            for row in rows
        ):
            clips += 1
    segments = outputs["segments"]
    edits = outputs["editing_input"]
    return {
        "independent_speech_expectation": digits == len(OFFSETS),
        "actual_device_matches": bool(segments) and all(row["actual_device"] == device for row in segments),
        "absolute_time_mapping_and_words": clips == len(OFFSETS),
        "ordered_segments": all(a["end_ms"] <= b["start_ms"] for a, b in zip(segments, segments[1:])),
        "named_editing_output": len(edits) == len(segments) and all(
            edit["id"] == row["id"] and edit["original"] == row["text"]
            and edit["language"] == row["language"]
            for edit, row in zip(edits, segments)
        ),
    }


def qualification_cases(asset, duration, device):
    cases = []
    text = {"type": "string", "pattern": SPEECH_PATTERN}
    for index, start in enumerate(OFFSETS):
        clip_id = f"fixture-{index}"
        end = start + duration
        bounds = {"type": "integer", "minimum": start, "maximum": end}
        identity = {"type": "string", "pattern": f"^{clip_id}-"}
        language = {"const": "en"}
        word_offset = {"type": "number", "minimum": 0, "maximum": duration / 1000}
        segment_fields = {
            "id": identity, "text": {"type": "string", "minLength": 1}, "language": language,
            "actual_device": {"const": device},
            "clip_start_ms": {"const": start}, "clip_end_ms": {"const": end},
            "start_ms": {**bounds, "maximum": end - 1}, "end_ms": {**bounds, "minimum": start + 1},
            "words": {"type": "array", "minItems": 1, "items": {
                "type": "object", "required": ["word", "start", "end"],
                "properties": {"word": {"type": "string", "minLength": 1},
                               "start": word_offset, "end": word_offset},
            }},
        }
        editing_fields = {
            "id": identity, "original": {"type": "string", "minLength": 1},
            "language": language, "terms": {"type": "array", "items": {"type": "string"}},
        }
        cases.append({
            "id": f"speech_{index}",
            "inputs": {
                "speech_clips": asset,
                "clip_map": [{"id": clip_id, "member": "clip-00001.wav", "start_ms": start, "end_ms": end}],
                "glossary": {"terms": [{"term": "milliseconds"}]},
            },
            "expected_schema": {
                "segments": {"type": "array", "minItems": 1, "items": {
                    "type": "object", "required": list(segment_fields), "properties": segment_fields,
                }, "contains": {"type": "object", "required": ["text"], "properties": {"text": text}}},
                "editing_input": {"type": "array", "minItems": 1, "items": {
                    "type": "object", "required": list(editing_fields), "properties": editing_fields,
                    "additionalProperties": False,
                }, "contains": {"type": "object", "required": ["original"], "properties": {"original": text}}},
            },
        })
    return cases


def verify(workspace, archive, cases_path=None, device="auto"):
    duration = _clip_duration(archive)
    runtime = Workspace(workspace).runtime
    try:
        selection = configure_asr(runtime, device=device)
    except FlowError as exc:
        if exc.code != "COMPUTE_DEVICE_CONFIRMATION_REQUIRED":
            raise
        return {"status": "NOT_RUN", "code": exc.code, "reason": str(exc), **exc.details}
    profile, selected_device = selection["selected"]["profile"], selection["selected"]["device"]
    model = catalogue()[MODEL]
    model_status = check_resource(runtime, model)
    _, python = runtime.environment(profile)
    profile_observation = {"status": "NOT_RUN", "code": "ENV_MISSING"}
    if runtime.profile_ready(profile):
        try:
            result = runtime.worker(profile, {"action": "dependencies"}, timeout=45,
                                    log=runtime.logs / "acceptance/asr-dependencies.log")
            profile_observation = {
                "status": "PASS" if result.get("ok") is True else "FAIL",
                "versions": result.get("versions", {}),
            }
        except FlowError as error:
            profile_observation = {"status": "FAIL", "code": error.code}
    tool = installed_local_tools().get(TOOL)
    adapter = ImplementationAdapter.model_validate(tool["portable"]) if tool else None
    entry = local_entry(adapter) if adapter else None
    report = {
        "status": "NOT_RUN", "tool": TOOL, "fixture_sha256": FIXTURE_SHA256,
        "expected_model_revision": model.revision, "workspace": str(runtime.workspace_root),
        "model": model_status, "profile_python": str(python), "profile_status": profile_observation,
        "execution_selection": {key: selection[key] for key in ("requested", "selected", "reason")},
        "profile_ready": profile_observation["status"] == "PASS",
        "adapter_ready": entry is not None and adapter.model_version == model.revision,
    }
    if cases_path is not None:
        cases = qualification_cases(Assets(runtime).store(archive), duration, selected_device)
        with cases_path.open("x", encoding="utf-8") as output:
            json.dump(cases, output, ensure_ascii=False, indent=2)
        report["qualification_cases"] = str(cases_path)
    if not (model_status["ready"] and report["profile_ready"] and report["adapter_ready"]):
        return report
    binding = {
        "provider": "portable_local", "tool": TOOL, "portable_adapter": tool["portable"],
        "implementation_hash": entry[1],
    }
    node = {
        "inputs": {name: {"port": port} for name, port in tool["inputs"].items()},
        "outputs": tool["outputs"],
    }
    inputs = {
        "speech_clips": Assets(runtime).store(archive),
        "clip_map": [
            {"id": f"fixture-{index}", "member": "clip-00001.wav", "start_ms": start, "end_ms": start + duration}
            for index, start in enumerate(OFFSETS)
        ],
        "glossary": {"terms": [{"term": "milliseconds"}]},
    }
    started = time.monotonic()
    outputs = execute_local(binding, node, inputs, {"device": device}, workspace=runtime.workspace_root)
    checks = _checks(outputs, duration, selected_device)
    return {
        **report, "status": "PASS" if all(checks.values()) else "FAIL", "checks": checks,
        "adapter_fingerprint": binding["implementation_hash"], "output_hash": content_hash(outputs),
        "elapsed_ms": round((time.monotonic() - started) * 1000), "outputs": outputs,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--speech-zip", type=Path, required=True)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto",
                        help="已由使用者確認的裝置。偵測到 GPU 時，auto 會先要求確認。")
    parser.add_argument("--write-qualification-cases", type=Path)
    args = parser.parse_args()
    try:
        report = verify(args.workspace, args.speech_zip, args.write_qualification_cases, args.device)
    except (FlowError, ValueError, OSError, KeyError, zipfile.BadZipFile) as exc:
        report = {"status": "FAIL", "code": getattr(exc, "code", type(exc).__name__),
                  "reason": str(exc)}
    sys.stdout.buffer.write((json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode())
    return {"PASS": 0, "NOT_RUN": 3, "FAIL": 1}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
