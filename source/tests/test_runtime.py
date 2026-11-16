import sys
from pathlib import Path
import pytest
from pydantic import ValidationError
from flow_base.contracts import AssetFile, Resource
from flow_base.runtime import FlowError, Runtime, run_process

def test_process_timeout_and_no_shell():
    assert run_process([sys.executable, "-c", "print('literal; $HOME')"]).strip() == "literal; $HOME"
    with pytest.raises(FlowError) as e:
        run_process([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.1)
    assert e.value.code == "PROCESS_TIMEOUT"


def test_exclusive_lock(tmp_path):
    r = Runtime(tmp_path)
    r.initialize()
    with r.lock("same"):
        with pytest.raises(FlowError) as e:
            with r.lock("same", timeout=0.05):
                pass
    assert e.value.code == "BUSY"


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/file", "a\\b"])
def test_resource_path_safety(name):
    with pytest.raises(ValidationError):
        AssetFile(path=name, url="https://example.org/file", sha256="0" * 64, size=1)


def test_offline_missing_model(tmp_path):
    from flow_base.resources import ensure_resource
    r = Resource(id="test", revision="1", license="MIT", files=[AssetFile(path="model", url="https://example.org/file", sha256="0"*64, size=1)])
    with pytest.raises(FlowError) as e:
        ensure_resource(Runtime(tmp_path), r, offline=True)
    assert e.value.code == "OFFLINE_RESOURCE_MISSING"


