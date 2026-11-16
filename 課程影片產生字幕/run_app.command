#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BASE="$HERE/../flow_base"
if [ ! -f "$BASE/run_app.sh" ]; then
  printf '找不到共用底座。請將 flow_base 套件解壓到本流程資料夾的同一層，再重新開啟。\n' >&2
  if [ -t 0 ]; then printf '按 Enter 關閉…'; read -r answer || true; fi
  exit 2
fi
exec sh "$BASE/run_app.sh" launch --folder "$HERE" "$@"
