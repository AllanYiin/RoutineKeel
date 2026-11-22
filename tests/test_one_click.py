import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name == 'nt' or shutil.which('sh') is None,
                                reason='POSIX sh is required')

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('flow_base_profile', ROOT/'flow_base/scripts/flow_base_profile.py')
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


def install_fixture(tmp_path, bootstrap_status=0):
    app = tmp_path/'workspace with spaces'/'flow_base'
    app.mkdir(parents=True)
    profile.generate(app)
    script = '''#!/bin/sh
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$1" = '--bootstrap-only' ]; then
    echo 'bootstrap output'
    exit STATUS
fi
printf '%s\\n' "$@" > "$HERE/received.txt"
echo 'PRIVATE_APPLICATION_OUTPUT'
exit 0
'''.replace('STATUS', str(bootstrap_status))
    (app/'start.sh').write_text(script)
    return app


def test_success_keeps_application_io_outside_bootstrap_log(tmp_path):
    app = install_fixture(tmp_path)
    result = subprocess.run(['sh', str(app/'run_app.sh'), 'ask', '有 空格的需求'], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert (app/'received.txt').read_text().splitlines() == ['ask', '有 空格的需求']
    log = next((app.parent/'share_logs/launcher').glob('*.log')).read_text()
    assert 'bootstrap output' in log and 'PRIVATE_APPLICATION_OUTPUT' not in log
    state = json.loads(next((app.parent/'share_states/launcher').glob('*.json')).read_text())
    assert state['status'] == 'ready'


def test_failure_is_logged_and_prevents_launch_without_hanging(tmp_path):
    app = install_fixture(tmp_path, 17)
    result = subprocess.run(['sh', str(app/'run_app.sh')], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
    assert result.returncode == 17
    assert not (app/'received.txt').exists()
    assert '環境準備失敗' in result.stderr
    state = json.loads(next((app.parent/'share_states/launcher').glob('*.json')).read_text())
    assert state['status'] == 'failed' and state['exit_code'] == 17
    assert 'bootstrap output' in next((app.parent/'share_logs/launcher').glob('*.log')).read_text()


def test_default_enters_chat_and_preserves_retry(tmp_path):
    app = install_fixture(tmp_path)
    for _ in range(2):
        result = subprocess.run(['sh', str(app/'run_app.command')], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
    assert (app/'received.txt').read_text().strip() == 'chat'
    assert len(list((app.parent/'share_states/launcher').glob('*.json'))) == 2


def test_generated_wrappers_are_portable_and_syntax_valid(tmp_path):
    profile.generate(tmp_path)
    bat = (tmp_path/'run_app.bat').read_bytes()
    assert bat.isascii() and b'\r\n' in bat
    assert b'-File "%~dp0start.ps1"' in bat and b'pause' in bat
    for name in ('run_app.sh', 'run_app.command'):
        p = tmp_path/name
        assert p.stat().st_mode & 0o111
        assert b'\r' not in p.read_bytes()
        subprocess.run(['sh', '-n', str(p)], check=True)
