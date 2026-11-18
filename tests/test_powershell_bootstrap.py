"""Executable PowerShell regressions; set FLOW_BASE_TEST_PWSH or install pwsh."""
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PWSH = os.environ.get('FLOW_BASE_TEST_PWSH') or shutil.which('pwsh')
pytestmark = pytest.mark.skipif(not PWSH or os.name == 'nt', reason='Requires PowerShell on POSIX for executable fixture scripts; Windows remains a separate acceptance target')


def invoke(script, env=None):
    result = subprocess.run([PWSH, '-NoLogo', '-NoProfile', '-NonInteractive', '-File', str(script)],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize('statuses,selected', [([0, 0, 0], 0), ([1, 0, 0], 1), ([1, 1, 0], 2)])
def test_multiple_uv_paths_use_one_working_executable(tmp_path, statuses, selected):
    commands = []
    for index, status in enumerate(statuses):
        folder = tmp_path / f'uv installation {index}'
        folder.mkdir()
        binary = folder / 'uv'
        binary.write_text(f'#!/bin/sh\necho "uv 0.13.6"\nexit {status}\n')
        binary.chmod(0o755)
        commands.append(binary)
    app = tmp_path/'flow_base'
    app.mkdir()
    old = tmp_path/'.venv'
    old.mkdir()
    (old/'keep.txt').write_text('untouched during tool discovery')
    # Run the production discovery block, including Get-Command and invocation.
    prefix = (ROOT/'dist/workspace/flow_base/start.ps1').read_text().split('$Wheel =', 1)[0]
    script = app/'check.ps1'
    script.write_text(prefix + '\n[pscustomobject]@{path=$UvExe;type=$UvExe.GetType().FullName;count=$KnownUV.Count} | ConvertTo-Json -Compress\n')
    env = dict(os.environ, PATH=os.pathsep.join(str(x.parent) for x in commands) + os.pathsep + os.environ['PATH'], PROCESSOR_ARCHITECTURE='AMD64')
    result = invoke(script, env)
    assert result['path'] == str(commands[selected])
    assert result['type'] == 'System.String'
    assert result['count'] >= 3
    assert (old/'keep.txt').read_text() == 'untouched during tool discovery'


def test_no_working_candidate_returns_null(tmp_path):
    source = (ROOT/'tools/start.ps1.in').read_text()
    function = source[source.index('function Select-FlowUv'):source.index('$KnownUV =')]
    script = tmp_path/'empty.ps1'
    script.write_text("$ErrorActionPreference = 'Stop'\n" + function + "\n$result = Select-FlowUv -Candidates @()\n[pscustomobject]@{empty=($null -eq $result)} | ConvertTo-Json -Compress\n")
    assert invoke(script)['empty'] is True


def test_full_launcher_has_valid_powershell_syntax(tmp_path):
    source = (ROOT/'dist/workspace/flow_base/start.ps1').read_text()
    launcher = tmp_path/'launcher.ps1'
    launcher.write_text(source)
    script = tmp_path/'parse.ps1'
    script.write_text("$tokens=$null; $errors=$null\n[System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'launcher.ps1'),[ref]$tokens,[ref]$errors) | Out-Null\n[pscustomobject]@{errors=@($errors | ForEach-Object {$_.Message})} | ConvertTo-Json -Compress\n")
    assert invoke(script)['errors'] == []


def test_bootstrap_failure_records_diagnostic_before_touching_venv(tmp_path):
    app = tmp_path/'flow_base'
    app.mkdir()
    (app/'fixture.whl').write_bytes(b'wrong wheel')
    old = tmp_path/'.venv'
    old.mkdir()
    (old/'keep.txt').write_text('keep existing files')
    source = (ROOT/'tools/start.ps1.in').read_text().replace('@UV_PS_CASES@','').replace('@WHEEL@','fixture.whl').replace('@WHEEL_HASH@','0'*64).replace('@VERSION@','0.2.3')
    script = app/'start.ps1'
    script.write_text(source, encoding='utf-8-sig')
    result = subprocess.run([PWSH,'-NoLogo','-NoProfile','-NonInteractive','-File',str(script)],
                            env={**os.environ,'PROCESSOR_ARCHITECTURE':'AMD64'}, capture_output=True, text=True, timeout=30)
    assert result.returncode == 1
    assert '啟動失敗' in result.stdout
    state = json.loads(next((tmp_path/'share_states/launcher').glob('*.json')).read_text(encoding='utf-8'))
    assert state['status'] == 'failed'
    assert (old/'keep.txt').read_text() == 'keep existing files'
    log = next((tmp_path/'share_logs/launcher').glob('*.log')).read_text(encoding='utf-8-sig')
    assert 'Runtime package integrity check failed' in log


@pytest.mark.parametrize('failure,local_available,retry_fails,exit_code', [
    ('', True, False, 0),
    ('Failed to create Python minor version link directory', True, False, 0),
    ('Missing expected target directory for Python minor version link', True, True, 1),
    ('Failed to create Python minor version link directory', False, False, 1),
    ('Network download failed', True, False, 1),
])
def test_bootstrap_retries_only_python_version_link_errors(tmp_path, failure, local_available, retry_fails, exit_code):
    app = tmp_path / 'flow_base'
    app.mkdir()
    wheel = app / 'fixture.whl'
    wheel.write_bytes(b'valid fixture')
    source = (ROOT / 'tools/start.ps1.in').read_text(encoding='utf-8')
    script = app / 'start.ps1'
    script.write_text(source.replace('@UV_PS_CASES@', '').replace('@WHEEL@', wheel.name)
                      .replace('@WHEEL_HASH@', hashlib.sha256(wheel.read_bytes()).hexdigest())
                      .replace('@VERSION@', '0.2.12'), encoding='utf-8-sig')
    uv = tmp_path / 'uv'
    uv.write_text(r"""#!/bin/sh
case "$1" in
  --version) echo 'uv 0.13.6'; exit 0 ;;
  venv)
    printf '%s\n' "$UV_PYTHON_INSTALL_DIR" >> "$UV_CALLS"
    if [ -n "$UV_FIXTURE_FAILURE" ] &&
       { [ "$UV_PYTHON_INSTALL_DIR" = "$UV_PRIMARY" ] || [ "$UV_RETRY_FAILS" = 1 ]; }; then
        printf 'error: %s\n  cause: 功能錯誤。 (os error 1)\n' "$UV_FIXTURE_FAILURE" >&2
        exit 7
    fi
    for venv do :; done
    mkdir -p "$venv/Scripts"
    touch "$venv/pyvenv.cfg"
    printf '#!/bin/sh\nexit 0\n' > "$venv/Scripts/python.exe"
    chmod +x "$venv/Scripts/python.exe"
    ;;
  pip) exit 0 ;;
  *) exit 99 ;;
esac
""", encoding='utf-8')
    uv.chmod(0o755)
    home = tmp_path / 'share_states'
    primary = home / 'python'
    local_app_data = tmp_path / 'Local AppData'
    fallback = local_app_data / 'RoutineKeel' / 'python'
    calls = tmp_path / 'uv-calls'
    env = {
        **os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'],
        'PROCESSOR_ARCHITECTURE': 'AMD64', 'LOCALAPPDATA': str(local_app_data) if local_available else '',
        'UV_CALLS': str(calls), 'UV_PRIMARY': str(primary), 'UV_FIXTURE_FAILURE': failure,
        'UV_RETRY_FAILS': str(int(retry_fails)),
    }
    command = [PWSH, '-NoLogo', '-NoProfile', '-NonInteractive', '-File', str(script), '--bootstrap-only']
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == exit_code, result.stdout + result.stderr
    attempted = [primary]
    retried = 'Python minor version link' in failure and local_available
    if retried:
        attempted.append(fallback)
    assert calls.read_text().splitlines() == [str(path) for path in attempted]
    state_dir = home / 'launcher'
    state = json.loads(next(state_dir.glob('*.json')).read_text(encoding='utf-8'))
    assert state['status'] == ('failed' if exit_code else 'ready')
    log = Path(state['log']).read_text(encoding='utf-8-sig')
    for path in attempted:
        assert f'uv managed Python directory: {path}' in log
    if failure:
        assert failure in log
        assert '功能錯誤。 (os error 1)' in log
    if exit_code:
        assert not (home / 'base-environment').exists()
    if retried and not exit_code:
        assert (state_dir / 'use-local-python').is_file()
        (home / 'base-environment').unlink()
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        assert calls.read_text().splitlines() == [str(primary), str(fallback), str(fallback)]
        assert list((home / 'retired_envs').glob('*/pyvenv.cfg'))
    else:
        assert not (state_dir / 'use-local-python').exists()


@pytest.mark.parametrize('exit_code', [0, 2, 130])
def test_command_transcript_captures_both_streams_and_preserves_exit(tmp_path, exit_code):
    source = (ROOT/'tools/start.ps1.in').read_text()
    app = tmp_path/'flow_base'
    app.mkdir()
    binary = tmp_path/'fixture-python'
    binary.write_text('#!/bin/sh\necho "normal output 中文"\necho "invalid word timeline record_id=20" >&2\nexit '+str(exit_code)+'\n')
    binary.chmod(0o755)
    prefix = source.split("$env:UV_CACHE_DIR",1)[0]
    suffix = source[source.index('# Command execution:'):]
    script = app/'check.ps1'
    script.write_text(prefix + "\n$Python = Join-Path (Split-Path $PSScriptRoot) 'fixture-python'\n" + suffix, encoding='utf-8-sig')
    result = subprocess.run([PWSH,'-NoLogo','-NoProfile','-NonInteractive','-File',str(script),'run'],
                            capture_output=True,text=True,timeout=30)
    assert result.returncode == exit_code, result.stdout + result.stderr
    state = json.loads(next((tmp_path/'share_states/launcher').glob('*.json')).read_text())
    assert state['status'] == ('completed' if exit_code == 0 else 'failed')
    assert state['phase'] == 'command' and state['exit_code'] == exit_code
    log = Path(state['log']).read_text(encoding='utf-8-sig')
    for marker in ['normal output 中文','invalid word timeline record_id=20','flow_base exit code: '+str(exit_code)]:
        assert marker in log
        assert marker in result.stdout


def test_interactive_output_is_not_transcribed(tmp_path):
    source = (ROOT/'tools/start.ps1.in').read_text()
    app = tmp_path/'flow_base';app.mkdir()
    binary = tmp_path/'fixture-python'
    binary.write_text('#!/bin/sh\necho PRIVATE_CHAT_SENTINEL\nexit 0\n')
    binary.chmod(0o755)
    script = app/'check.ps1'
    script.write_text(source.split('$env:UV_CACHE_DIR',1)[0] +
                     "\n$Python = Join-Path (Split-Path $PSScriptRoot) 'fixture-python'\n" +
                     source[source.index('# Command execution:'):],encoding='utf-8-sig')
    result = subprocess.run([PWSH,'-NoLogo','-NoProfile','-NonInteractive','-File',str(script),'chat'],
                            capture_output=True,text=True,timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PRIVATE_CHAT_SENTINEL' in result.stdout
    log = next((tmp_path/'share_logs/launcher').glob('*.log')).read_text(encoding='utf-8-sig')
    assert 'PRIVATE_CHAT_SENTINEL' not in log
