#!/bin/sh
set -u
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/.." && pwd)
id="launch_$(date -u +%Y%m%dT%H%M%S)_$$"
mkdir -p "$ROOT/share_logs/launcher" "$ROOT/share_states/launcher" || exit 1
log="$ROOT/share_logs/launcher/$id.log"
state="$ROOT/share_states/launcher/$id.json"
result="$ROOT/share_states/launcher/$id.exit"
printf '{"launch_id":"%s","status":"preparing"}\n' "$id" > "$state"
printf 'flow_base：正在準備環境，首次啟動會下載必要工具。\n安裝日誌：%s\n' "$log"
trap 'rm -f "$result"' EXIT
(
  sh "$HERE/start.sh" --bootstrap-only
  code=$?
  printf '%s\n' "$code" > "$result"
) 2>&1 | tee "$log"
tee_code=$?
code=1
if [ -s "$result" ]; then code=$(cat "$result"); fi
if [ "$tee_code" -ne 0 ]; then code="$tee_code"; fi
if [ "$code" -ne 0 ]; then
  printf '{"launch_id":"%s","status":"failed","exit_code":%s}\n' "$id" "$code" > "$state"
  printf '\n環境準備失敗。請複製上方訊息及日誌給維護者；排除問題後重新開啟即可重試。\n' >&2
  if [ -t 0 ]; then printf '按 Enter 關閉…'; read -r answer; fi
  exit "$code"
fi
printf '{"launch_id":"%s","status":"ready"}\n' "$id" > "$state"
printf '\n環境準備完成，進入 flow_base。\n'
if [ "$#" -eq 0 ]; then set -- chat; fi
sh "$HERE/start.sh" "$@"
code=$?
if [ "$code" -ne 0 ]; then
  printf '\n流程入口發生錯誤，請保留上方診斷訊息。\n' >&2
  if [ -t 0 ]; then printf '按 Enter 關閉…'; read -r answer; fi
fi
exit "$code"
