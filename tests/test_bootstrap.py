"""Regression tests exercise launchers as executables, without network downloads."""
import hashlib
import os
import platform
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name == 'nt' or shutil.which('sh') is None,
                                reason='POSIX sh is required')

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def launch(tmp_path):
    folder = tmp_path / 'flow_base'
    folder.mkdir()
    wheel = folder / 'fixture.whl'
    wheel.write_bytes(b'not installed: guard must reject interpreter first')
    script = (ROOT/'tools/start.sh.in').read_text().replace('@WHEEL@', wheel.name).replace('@WHEEL_HASH@', hashlib.sha256(wheel.read_bytes()).hexdigest()).replace('@VERSION@', '0.2.1').replace('@UV_SH_CASES@', '')
    (folder/'start.sh').write_text(script)
    commands = tmp_path/'commands'
    commands.mkdir()
    fake = commands/'uv'
    fake.write_text('#!/bin/sh\nif [ "$1" = venv ]; then\n  for target do :; done\n  mkdir -p "$target/bin"\n  echo "version = 3.10.10" > "$target/pyvenv.cfg"\n  printf "#!/bin/sh\\nexit 1\\n" > "$target/bin/python"\n  chmod +x "$target/bin/python"\nelse\n  echo INSTALL_CALLED >> "$FLOW_BASE_HOME/unexpected-install"\nfi\n')
    fake.chmod(0o755)
    env = {**os.environ, 'PATH': str(commands) + os.pathsep + os.environ['PATH']}
    return tmp_path, lambda: subprocess.run(['sh', str(folder/'start.sh'), 'list'], env=env, capture_output=True, text=True, timeout=20, check=False)


def test_incompatible_python_blocks_before_install(launch):
    root, run = launch
    result = run()
    assert result.returncode != 0
    assert 'Expected local .venv with Python 3.12' in result.stderr
    assert not (root/'share_states/unexpected-install').exists()
    assert not (root/'share_states/base-environment').exists()


def test_unmanaged_virtualenv_preserved_when_repair_fails(launch):
    root, run = launch
    old = root/'.venv'
    old.mkdir()
    (old/'pyvenv.cfg').write_text('version = 3.10.10\n')
    (old/'keep.txt').write_text('preserve previous files')
    result = run()
    assert result.returncode != 0
    backups = list((root/'share_states/retired_envs').glob('*/.venv/keep.txt'))
    assert len(backups) == 1 and backups[0].read_text() == 'preserve previous files'
    assert not (root/'share_states/unexpected-install').exists()


def test_upgrade_reuses_the_previous_base_interpreter(launch):
    root, run = launch
    old = root / '.venv'
    (old / 'bin').mkdir(parents=True)
    (old / 'pyvenv.cfg').write_text('version = 3.12.11\n')
    existing = root / 'existing-python'
    existing.touch()
    python = old / 'bin/python'
    python.write_text('#!/bin/sh\ncase "$*" in *sys._base_executable*) printf "%s\\n" "' + str(existing) + '" ;; esac\n')
    python.chmod(0o755)
    uv = root / 'commands/uv'
    original = uv.read_text()
    uv.write_text(original.replace('if [ "$1" = venv ]; then', 'if [ "$1" = venv ]; then\n  printf "%s\\n" "$5" > "$FLOW_BASE_HOME/interpreter"'))
    assert run().returncode != 0
    assert (root / 'share_states/interpreter').read_text().strip() == str(existing)


def test_matching_marker_cannot_bypass_interpreter_check(launch):
    root, run = launch
    (root/'.venv/bin').mkdir(parents=True)
    (root/'.venv/pyvenv.cfg').write_text('version = 3.10.10')
    python = root/'.venv/bin/python'
    python.write_text('#!/bin/sh\nexit 1\n')
    python.chmod(0o755)
    (root/'share_states').mkdir()
    digest = hashlib.sha256((root/'flow_base/fixture.whl').read_bytes()).hexdigest()
    marker = f'{digest}|@CONTRACTS_SHA256@|{root}|{platform.system()}|{platform.machine()}'
    (root/'share_states/base-environment').write_text(marker+'\n')
    result = run()
    assert result.returncode != 0 and 'Previous environment files preserved' in result.stderr
    assert 'Expected local .venv with Python 3.12' in result.stderr


def test_non_virtualenv_directory_is_untouched(launch):
    root, run = launch
    (root/'.venv').mkdir()
    (root/'.venv/important.txt').write_text('keep')
    result = run()
    assert result.returncode != 0 and 'not a recognized virtual environment' in result.stderr
    assert (root/'.venv/important.txt').read_text() == 'keep'
    assert not (root/'share_states/retired_envs').exists()
