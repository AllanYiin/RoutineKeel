import subprocess
import sys
from contextlib import closing

import pytest
from flow_base.runtime import FlowError

from flow_base import studio


@pytest.mark.parametrize("failure", ["exit", "timeout"])
def test_failed_studio_child_is_reaped_without_replaying_request(tmp_path, monkeypatch, failure):
    trace = tmp_path / "requests.txt"
    script = """
import json, sys, time
from pathlib import Path

trace, failure, command = sys.argv[1:]
assert command == "bridge"
print('{"protocol": 1}', flush=True)
for line in sys.stdin:
    operation, payload = json.loads(line)
    with Path(trace).open("a", encoding="utf-8") as output:
        print(operation, file=output)
    if operation == "write":
        if failure == "timeout":
            time.sleep(60)
        raise SystemExit(3)
    print(json.dumps({"ok": True, "result": json.loads(payload)}), flush=True)
"""
    monkeypatch.setattr(
        studio, "_studio_command",
        lambda _: [sys.executable, "-u", "-c", script, str(trace), failure],
    )
    with closing(studio.StudioBridge(tmp_path)) as bridge, bridge.activate():
        assert studio.studio_request(tmp_path, "before", {"text": "甲"}) == {"text": "甲"}
        first = bridge._process
        expected = subprocess.TimeoutExpired if failure == "timeout" else FlowError
        with pytest.raises(expected):
            bridge.response("write", "{}", timeout=1)
        assert first.poll() is not None
        assert studio.studio_request(tmp_path, "after", {"text": "乙"}) == {"text": "乙"}
        assert bridge._process is not first
        assert trace.read_text(encoding="utf-8").splitlines() == ["before", "write", "after"]


def test_older_studio_cli_is_used_after_rejecting_bridge_start(tmp_path, monkeypatch):
    trace = tmp_path / "commands.txt"
    script = """
import json, sys
from pathlib import Path

trace, command, *args = sys.argv[1:]
with Path(trace).open("a", encoding="utf-8") as output:
    print(command, file=output)
if command == "bridge":
    raise SystemExit(2)
assert args[1:] == ["--payload", "-"]
print(json.dumps({"ok": True, "result": json.load(sys.stdin)}), flush=True)
"""
    monkeypatch.setattr(
        studio, "_studio_command",
        lambda _: [sys.executable, "-u", "-c", script, str(trace)],
    )
    with closing(studio.StudioBridge(tmp_path)) as bridge, bridge.activate():
        for text in ("初稿", "定稿"):
            assert studio.studio_request(tmp_path, "get", {"text": text}) == {"text": text}
        assert trace.read_text(encoding="utf-8").splitlines() == ["bridge", "interview", "interview"]
