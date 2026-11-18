"""Capture only non-secret platform metadata for the acceptance artifact."""

import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[3]
    output = Path(os.environ.get("STUDIO_GATE_REPORT_DIR", root / "dist/studio-ci"))
    output.mkdir(parents=True, exist_ok=True)
    runtime = (
        root
        / "routinekeel/flow_base/.venv"
        / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    if not runtime.is_file():
        raise RuntimeError("The separate Runtime environment must exist before acceptance")
    isolated = subprocess.run(
        [
            str(runtime),
            "-I",
            "-c",
            "import importlib.util; assert importlib.util.find_spec('workflow_studio') is None",
        ],
        capture_output=True,
        timeout=30,
    )
    if isolated.returncode:
        raise RuntimeError("Runtime environment is not independent of Studio")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    payload = {
        "schema_version": 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "commit": commit,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version,
        "runtime_python": str(runtime),
        "runtime_independent": True,
        "packages": {
            name: version(name)
            for name in ("workflow-studio", "flow-base", "pydantic", "pillow", "filelock")
        },
        "ffmpeg_available": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
        "native_ui_excluded": [
            "test_real_tk_buttons",
            "test_tk_analysis",
            "test_ui_state_multiline",
        ],
        "scope": "Non-desktop engineering acceptance; not real-model or human conversion quality",
    }
    (output / "environment.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
