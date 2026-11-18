import json
import os
import subprocess
from pathlib import Path

from ..compiler.package import digest
from ..contracts import StudioError
from ..ingestion.security import read_file, reject_links, safe_relative

ENV_NAMES = {
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATH",
    "PATHEXT",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
    "LANG",
    "LC_ALL",
}


def replay(package, root, source, runtime_python, *, timeout=60, runtime_env=None):
    root.mkdir(parents=True, exist_ok=False)
    (root / "generated").mkdir()
    for name, content in package.items():
        (root / "generated" / name).write_bytes(content)
    (root / "input.bin").write_bytes(source)
    env = {k: v for k, v in os.environ.items() if k.upper() in ENV_NAMES}
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(runtime_env or {})
    runner = Path(__file__).with_name("runtime_runner.py")
    try:
        result = subprocess.run(
            [str(runtime_python), "-I", str(runner), str(root)],
            env=env,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise StudioError("TEST_FAILED", "測試工作流執行逾時。") from exc
    (root / "console.log").write_bytes(result.stdout + result.stderr)
    if result.returncode != 0 or not (root / "runtime-result.json").is_file():
        raise StudioError("TEST_FAILED", "測試工作流失敗，請查看本機測試紀錄。")
    report = json.loads((root / "runtime-result.json").read_text(encoding="utf-8"))
    if report["status"] != "complete" or (
        not runtime_env and report["token_usage"]["registered_requests"] != 0
    ):
        raise StudioError("TEST_FAILED", "工作流未完成或確定性流程產生了模型呼叫。")
    if not {"run.started", "step.started", "delivery.published", "run.finished"} <= set(
        report["lifecycle"]
    ):
        raise StudioError("TEST_FAILED", "缺少實際 Runtime 生命週期證據。")
    return report


def evidence_file(root, relative, *, limit=10 * 1024 * 1024):
    safe_relative(relative)
    path = root / relative
    for part in (path, *path.parents):
        reject_links(part)
        if part == root:
            break
    return read_file(path, root, limit)


def output_bytes(root, report, output_name, ir):
    node_id = ir["outputs"][output_name].split("//", 1)[1].split("/", 1)[0]
    node = next(n for n in ir["nodes"] if n["id"] == node_id)
    relative = node["outputs"]["result"]["path"]
    primary_relative = report["named_outputs"][output_name]["primary"]
    primary = root / "output" / primary_relative
    record = json.loads(evidence_file(root / "output", primary_relative, limit=1_000_000))["result"]
    path = primary.parent / relative
    if (
        record["path"] != relative
        or path.is_symlink()
        or not path.resolve().is_relative_to((root / "output").resolve())
    ):
        raise StudioError("TEST_FAILED", "交付路徑與契約不符。")
    content = evidence_file(root / "output", path.relative_to(root / "output").as_posix())
    if digest(content) != record["sha256"]:
        raise StudioError("TEST_FAILED", "交付產物指紋不符。")
    return content
