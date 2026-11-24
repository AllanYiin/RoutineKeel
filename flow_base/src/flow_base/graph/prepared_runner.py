"""Child interpreter for an Adapter in a verified, operation-scoped wheel site."""

import json
import sys

from ..runtime import canonical, digest
from ..workspace import Workspace
from .preparation import (
    WheelPreparation,
    _entry,
    _fail,
    _target,
    preparation_id,
    read_preparation,
)


def run(workspace, data):
    binding = data["binding"]
    runtime = Workspace(workspace).runtime
    recipe = WheelPreparation.model_validate(binding["preparation"])
    receipt = read_preparation(runtime, recipe)
    if receipt is None or digest(receipt) != binding["implementation_hash"]:
        _fail("已準備的 Adapter 收據不符。")
    site = _target(runtime, preparation_id(recipe)) / "site"
    entry = _entry(site, recipe)
    sys.path.insert(0, str(site))
    return entry.load()(data["inputs"], data["parameters"])


if __name__ == "__main__":
    request = sys.stdin.buffer.read(2 * 1024**2 + 1)
    if len(request) > 2 * 1024**2:
        raise SystemExit(2)
    try:
        record = {"outputs": run(sys.argv[1], json.loads(request))}
    except (KeyError, TypeError, ValueError):
        record = {"code": "ADAPTER_INPUT_REJECTED"}
    sys.stdout.buffer.write(canonical(record).encode("utf-8"))
