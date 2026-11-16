#!/bin/sh
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
sh "$HERE/run_app.sh" --action "recover-run" "$@"
code=$?
if [ -t 0 ]; then printf "請查看上方恢復狀態，按 Enter 關閉…"; read -r answer; fi
exit "$code"
