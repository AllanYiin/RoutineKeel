import json
import os
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from flow_base import runtime


@pytest.mark.parametrize('os_name,use_local', [('nt', False), ('nt', True), ('posix', True)])
def test_profile_uses_launcher_python_location_and_records_environment(tmp_path, monkeypatch, os_name, use_local):
    home = tmp_path / 'share_states'
    local_app_data = tmp_path / 'Local AppData'
    monkeypatch.setenv('LOCALAPPDATA', str(local_app_data))
    runtime_os = SimpleNamespace(**vars(os))
    runtime_os.name = os_name
    monkeypatch.setattr(runtime, 'os', runtime_os)
    if use_local:
        launcher = home / 'launcher'
        launcher.mkdir(parents=True)
        (launcher / 'use-local-python').touch()

    profile = tmp_path / 'profile'
    profile.mkdir()
    (profile / '.python-version').write_text('3.12\n')
    (profile / 'uv.lock').write_text('')
    (profile / 'pyproject.toml').write_text('[tool.uv]\npackage = false\n')
    instance = runtime.Runtime(home)
    instance.register_profile('fixture', profile)
    root, python = instance.environment('fixture')
    monkeypatch.setattr(instance, 'lock', lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(runtime.shutil, 'which', lambda command: command)

    install_dirs = []

    def sync_project(argv, *, env, **kwargs):
        assert argv[:2] == ['uv', 'sync']
        install_dirs.append(Path(env['UV_PYTHON_INSTALL_DIR']))
        python.parent.mkdir(parents=True)
        python.touch()

    monkeypatch.setattr(runtime, 'run_process', sync_project)
    assert instance.prepare_profile('fixture') == python
    expected = (local_app_data / 'RoutineKeel' / 'python'
                if os_name == 'nt' and use_local else home / 'python')
    assert install_dirs == [expected]
    assert json.loads((root / 'location.json').read_text()) == {'path': str(root)}
