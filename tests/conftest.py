import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for project in [ROOT / 'flow_base', *(ROOT / 'workflows').iterdir()]:
    if (project / 'src').is_dir():
        sys.path.insert(0, str(project / 'src'))


@pytest.fixture
def desktop_test_catalog(tmp_path_factory, monkeypatch):
    """Emulate a host-published observation outside the workflow's data workspace."""
    directory=tmp_path_factory.mktemp("host-inventory")
    path=directory/"catalog.json"
    record={"tool":"test-sink","version":"1","receipt_id":"test-1","observed":"sent",
            "platforms":[sys.platform],"available":True,"allowed":True,"network":False,
            "permissions":["send"],"side_effect":"external","resources":{"cost_micro_usd":0},
            "input_schema":{"type":"object","required":["body"]},
            "output_schema":{"type":"object","required":["receipt"]}}
    data={"version":1,"expires_at":(datetime.now(UTC)+timedelta(hours=1)).isoformat(),
          "tools":{"test-sink":record}}
    path.write_text(json.dumps(data),encoding="utf-8")
    monkeypatch.setenv("ROUTINEKEEL_HOST_CATALOG",str(path))
    return path


@pytest.fixture
def workspace(tmp_path):
    import shutil

    from package_paths import WORKSPACE

    for task, folder in {'ai_flow01': '課程影片產生字幕', 'ai_flow02': '檔案複製範例'}.items():
        shutil.copytree(WORKSPACE / 'workflows' / ('FLOW_' + folder), tmp_path / task)
    return tmp_path
