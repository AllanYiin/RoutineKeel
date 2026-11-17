"""Explicit CLI/workspace extension to the supplied project launcher/validator."""
from pathlib import Path
import json
import subprocess
import sys
import tomllib

BAT = r'''@echo off
setlocal DisableDelayedExpansion
title flow_base
echo flow_base: preparing environment and starting...
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
set "FLOW_BASE_EXIT=%ERRORLEVEL%"
if not "%FLOW_BASE_EXIT%"=="0" (
  echo.
  echo flow_base returned code %FLOW_BASE_EXIT%. Review the result above and share_logs\launcher.
  echo If an action failed, address its diagnostic and retry when appropriate.
  pause
)
exit /b %FLOW_BASE_EXIT%
'''

POSIX = '''#!/bin/sh
set -u
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/.." && pwd)
id="launch_$(date -u +%Y%m%dT%H%M%S)_$$"
mkdir -p "$ROOT/share_logs/launcher" "$ROOT/share_states/launcher" || exit 1
log="$ROOT/share_logs/launcher/$id.log"
state="$ROOT/share_states/launcher/$id.json"
result="$ROOT/share_states/launcher/$id.exit"
printf '{"launch_id":"%s","status":"preparing"}\\n' "$id" > "$state"
printf 'flow_base：正在準備環境，首次啟動會下載必要工具。\\n安裝日誌：%s\\n' "$log"
trap 'rm -f "$result"' EXIT
(
  sh "$HERE/start.sh" --bootstrap-only
  code=$?
  printf '%s\\n' "$code" > "$result"
) 2>&1 | tee "$log"
tee_code=$?
code=1
if [ -s "$result" ]; then code=$(cat "$result"); fi
if [ "$tee_code" -ne 0 ]; then code="$tee_code"; fi
if [ "$code" -ne 0 ]; then
  printf '{"launch_id":"%s","status":"failed","exit_code":%s}\\n' "$id" "$code" > "$state"
  printf '\\n環境準備失敗。請複製上方訊息及日誌給維護者；排除問題後重新開啟即可重試。\\n' >&2
  if [ -t 0 ]; then printf '按 Enter 關閉…'; read -r answer; fi
  exit "$code"
fi
printf '{"launch_id":"%s","status":"ready"}\\n' "$id" > "$state"
printf '\\n環境準備完成，進入 flow_base。\\n'
if [ "$#" -eq 0 ]; then set -- chat; fi
sh "$HERE/start.sh" "$@"
code=$?
if [ "$code" -ne 0 ]; then
  printf '\\n流程入口發生錯誤，請保留上方診斷訊息。\\n' >&2
  if [ -t 0 ]; then printf '按 Enter 關閉…'; read -r answer; fi
fi
exit "$code"
'''


TASK_BAT = r'''@echo off
setlocal DisableDelayedExpansion
title flow_base workflow
if not exist "%~dp0..\flow_base\start.ps1" (
  echo Missing shared flow_base. Extract the flow_base package beside this workflow folder.
  pause
  exit /b 2
)
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\flow_base\start.ps1" launch --folder "%~dp0." %*
set "FLOW_BASE_EXIT=%ERRORLEVEL%"
if not "%FLOW_BASE_EXIT%"=="0" (
  echo.
  echo Workflow returned code %FLOW_BASE_EXIT%. Review the result above and share_logs\launcher.
  echo If an action failed, address its diagnostic and retry when appropriate.
  pause
)
exit /b %FLOW_BASE_EXIT%
'''

TASK_POSIX = '''#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BASE="$HERE/../flow_base"
if [ ! -f "$BASE/run_app.sh" ]; then
  printf '找不到共用底座。請將 flow_base 套件解壓到本流程資料夾的同一層，再重新開啟。\\n' >&2
  if [ -t 0 ]; then printf '按 Enter 關閉…'; read -r answer || true; fi
  exit 2
fi
exec sh "$BASE/run_app.sh" launch --folder "$HERE" "$@"
'''


def generate_task(root):
    root = Path(root)
    (root/'run_app.bat').write_text(TASK_BAT, encoding='ascii', newline='\r\n')
    for name in ('run_app.sh', 'run_app.command'):
        path = root/name
        path.write_text(TASK_POSIX, encoding='utf-8', newline='\n')
        path.chmod(0o755)
    manifest = json.loads((root/'flow.json').read_text(encoding='utf-8')) if (root/'flow.json').is_file() else {}
    action = manifest.get('recovery_action')
    if action:
        import re
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', action) or action not in manifest.get('actions', []):
            raise ValueError('Invalid declared recovery_action')
        bat = ('@echo off\nsetlocal DisableDelayedExpansion\n'
               'call "%~dp0run_app.bat" --action "' + action + '" %*\n'
               'set "FLOW_BASE_EXIT=%ERRORLEVEL%"\n'
               'echo Review the recovery status and output path above.\npause\n'
               'exit /b %FLOW_BASE_EXIT%\n')
        (root/'recover_failed_run.bat').write_text(bat, encoding='ascii', newline='\r\n')
        posix = ('#!/bin/sh\nHERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
                 'sh "$HERE/run_app.sh" --action "' + action + '" "$@"\ncode=$?\n'
                 'if [ -t 0 ]; then printf "請查看上方恢復狀態，按 Enter 關閉…"; read -r answer; fi\nexit "$code"\n')
        for name in ('recover_failed_run.sh', 'recover_failed_run.command'):
            path = root/name; path.write_text(posix, encoding='utf-8'); path.chmod(0o755)
    return 0


def generate(root):
    root = Path(root)
    (root/'run_app.bat').write_text(BAT, encoding='ascii', newline='\r\n')
    for name in ('run_app.sh', 'run_app.command'):
        path = root/name
        path.write_text(POSIX, encoding='utf-8', newline='\n')
        path.chmod(0o755)
    project = root/'pyproject.toml'
    if project.exists():
        deps = tomllib.loads(project.read_text(encoding='utf-8'))['project']['dependencies']
        (root/'requirements.txt').write_text('\n'.join(deps)+'\n', encoding='utf-8')
    return 0


def validate(root):
    root = Path(root)
    cfg = json.loads((root/'project.config.json').read_text(encoding='utf-8'))
    errors = []
    def require(ok, path, message):
        if not ok:
            errors.append({'code': 'flow_base_contract', 'path': path, 'message': message})
    require((cfg.get('archetype'),cfg.get('architecture'),cfg.get('frontend'),cfg.get('backend')) ==
            ('workflow_runtime','shared_workspace','terminal','python_cli'), 'project.config.json', 'Unexpected CLI extension mapping')
    require(cfg.get('apsm_extension') == 'flow_base_cli_v1', 'project.config.json', 'Extension must be explicit')
    require(cfg.get('usage_scene') == 'scene_b_shared_tool', 'project.config.json', 'Expected shared learner tool')
    for key in ('user_type','usage_duration','change_frequency','failure_cost'):
        require(bool(cfg.get('project_profile',{}).get(key)), 'project.config.json', 'Missing project profile '+key)
    for name in ('AGENTS.md','specs/requirements.md','todo.md','README.md','requirements.txt','uv.lock',
                 'scripts/project_launcher.py','scripts/apsm_validate.py','scripts/flow_base_profile.py',
                 'run_app.bat','run_app.sh','run_app.command'):
        require((root/name).is_file(), name, 'Required development file missing')
    if (root/'run_app.bat').exists():
        require((root/'run_app.bat').read_bytes().isascii(), 'run_app.bat', 'CMD wrapper must be ASCII')
        require((root/'run_app.bat').read_text() == BAT, 'run_app.bat', 'Wrapper differs from generator')
    for name in ('run_app.sh','run_app.command'):
        if (root/name).exists():
            require((root/name).read_text(encoding='utf-8') == POSIX, name, 'Wrapper differs from generator')
    return {'valid': not errors, 'errors': errors, 'warnings': [], 'config': cfg,
            'validation_scope': 'flow_base_cli_v1 extension; original web templates are not applicable'}
