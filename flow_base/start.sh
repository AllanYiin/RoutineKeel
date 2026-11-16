#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
FLOW_BASE_WORKSPACE=$(CDPATH= cd -- "$HERE/.." && pwd)
FLOW_BASE_HOME="$FLOW_BASE_WORKSPACE/share_states"
export FLOW_BASE_WORKSPACE FLOW_BASE_HOME
FLOW_BASE_BOOTSTRAP="$FLOW_BASE_HOME/bootstrap/$(uname -s)-$(uname -m)"
mkdir -p "$FLOW_BASE_HOME" "$FLOW_BASE_WORKSPACE/share_logs"
export UV_CACHE_DIR="$FLOW_BASE_HOME/packages"
export UV_PYTHON_INSTALL_DIR="$FLOW_BASE_HOME/python"
export UV_NO_MODIFY_PATH=1 UV_NO_PROGRESS=1 UV_NO_ENV_FILE=1 UV_LINK_MODE=copy
export PYTHONDONTWRITEBYTECODE=1
if command -v uv >/dev/null 2>&1; then
  UV=$(command -v uv)
elif [ -x "$FLOW_BASE_BOOTSTRAP/uv" ]; then
  UV="$FLOW_BASE_BOOTSTRAP/uv"
else
  echo "Preparing pinned uv 0.13.6 binary from PyPI..." >&2
  command -v unzip >/dev/null 2>&1 || { echo "Install unzip or uv, then retry." >&2; exit 2; }
  mkdir -p "$FLOW_BASE_BOOTSTRAP"
  archive=$(mktemp "$FLOW_BASE_BOOTSTRAP/uv.XXXXXX")
  uv_binary="$archive.bin"
  trap 'rm -f "$archive" "$uv_binary"' EXIT HUP INT TERM
  platform="$(uname -s):$(uname -m)"
  case "$platform" in
    Linux:x86_64) uv_url="https://files.pythonhosted.org/packages/5c/43/0688ff5142934238f2812e348f4a24e09b57d48bfb38dd8f850325a0d9de/uv-0.13.6-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"; uv_hash="22759b8895e1103578de6fb4cd27e94b4399d021023378d29f01333717751fba" ;;
    Linux:aarch64) uv_url="https://files.pythonhosted.org/packages/05/3b/f78583af6504f1270da95fd0d0935e381f58330c007260f450e81a1e5e1b/uv-0.13.6-py3-none-manylinux_2_17_aarch64.manylinux2014_aarch64.musllinux_1_1_aarch64.whl"; uv_hash="7ec5486476d67a4214c460b5492393ee2300d84e1de17b293100a019b413568b" ;;
    Darwin:arm64) uv_url="https://files.pythonhosted.org/packages/f4/14/77230cc4f6a73a31578cb8070a5f67f58a76946ead643ae8a4155620d6da/uv-0.13.6-py3-none-macosx_11_0_arm64.whl"; uv_hash="51361029b7cf91185d0092c4b356d110b4ad629e9cb11593f1e006ac72a72ca9" ;;
    Darwin:x86_64) uv_url="https://files.pythonhosted.org/packages/33/55/ff10d3f756c39a239e492e57262bd22fcda412a9f85a268175447cf33b09/uv-0.13.6-py3-none-macosx_10_12_x86_64.whl"; uv_hash="95f042b95e67ef8c3d3569bb6e9d895e592002c5e871cf3f1afe407c9d77a294" ;;
    *) echo "Unsupported bootstrap platform: $platform. Install uv manually." >&2; exit 2 ;;
  esac
  if command -v curl >/dev/null 2>&1; then
    curl --fail --location --proto '=https' --tlsv1.2 --connect-timeout 20 --max-time 120 \
      "$uv_url" --output "$archive"
  elif command -v wget >/dev/null 2>&1; then
    wget --https-only --timeout=120 -O "$archive" "$uv_url"
  else
    echo "curl or wget is required for the first bootstrap. Install uv and retry." >&2
    exit 2
  fi
  if command -v sha256sum >/dev/null 2>&1; then
    uv_actual=$(sha256sum "$archive" | cut -d ' ' -f 1)
  else
    uv_actual=$(shasum -a 256 "$archive" | cut -d ' ' -f 1)
  fi
  [ "$uv_actual" = "$uv_hash" ] || { echo "uv integrity check failed." >&2; exit 2; }
  unzip -p "$archive" 'uv-0.13.6.data/scripts/uv' > "$uv_binary"
  chmod 755 "$uv_binary"
  mv "$uv_binary" "$FLOW_BASE_BOOTSTRAP/uv"
  unzip -p "$archive" 'uv-0.13.6.dist-info/licenses/*' > "$FLOW_BASE_BOOTSTRAP/UV-LICENSES.txt"
  rm -f "$archive"
  trap - EXIT HUP INT TERM
  UV="$FLOW_BASE_BOOTSTRAP/uv"
fi
export PATH="$(dirname "$UV"):$PATH"
wheel="$HERE/flow_base-0.2.10-py3-none-any.whl"
if command -v sha256sum >/dev/null 2>&1; then
  actual=$(sha256sum "$wheel" | cut -d ' ' -f 1)
else
  actual=$(shasum -a 256 "$wheel" | cut -d ' ' -f 1)
fi
if [ "$actual" != "78d51508789ce0d2738ee741e0bf7930236bbfa7cf6a29f324f42b67d1f8ac05" ]; then
  echo "Runtime package integrity check failed." >&2
  exit 2
fi
marker="$FLOW_BASE_HOME/base-environment"
venv="$FLOW_BASE_WORKSPACE/.venv"
python="$venv/bin/python"
python_check='import pathlib,sys; sys.exit(0 if sys.version_info[:2] == (3,12) and pathlib.Path(sys.prefix).resolve() == pathlib.Path(sys.argv[1]).resolve() else 1)'
runtime_check='import flow_base,flow_base.cli,sys; sys.exit(0 if flow_base.__version__ == sys.argv[1] else 1)'
valid_python() {
  [ -x "$python" ] && "$python" -I -c "$python_check" "$venv" >/dev/null 2>&1
}
valid_runtime() {
  valid_python && "$python" -I -c "$runtime_check" '0.2.10' >/dev/null 2>&1
}
expected="$actual|$FLOW_BASE_WORKSPACE|$(uname -s)|$(uname -m)"
if [ ! -f "$marker" ] || [ "$(cat "$marker")" != "$expected" ] || ! valid_runtime; then
  lock="$FLOW_BASE_HOME/bootstrap.lock"
  mkdir "$lock" 2>/dev/null || { echo "Another bootstrap is active. If it was interrupted, remove share_states/bootstrap.lock and retry." >&2; exit 2; }
  trap 'rmdir "$lock" 2>/dev/null || true' EXIT HUP INT TERM
  [ ! -L "$venv" ] || { echo "Refusing to replace a symbolic-link .venv." >&2; exit 2; }
  if [ -e "$venv" ]; then
    [ -f "$venv/pyvenv.cfg" ] || { echo ".venv is not a recognized virtual environment. Move that directory aside before retrying." >&2; exit 2; }
    mkdir -p "$FLOW_BASE_HOME/retired_envs"
    backup=$(mktemp -d "$FLOW_BASE_HOME/retired_envs/venv.XXXXXXXX")
    mv "$venv" "$backup/.venv"
    echo "Previous environment files preserved at: $backup/.venv" >&2
  fi
  echo "Creating local .venv from the pinned runtime package..." >&2
  : > "$FLOW_BASE_HOME/base-creating"
  "$UV" venv --no-config --no-project --python 3.12 "$venv"
  valid_python || { echo "Expected local .venv with Python 3.12; refusing to install into another interpreter." >&2; exit 2; }
  "$UV" pip install --no-config --python "$python" --no-build "$wheel"
  valid_runtime || { echo "Installed runtime did not pass its Python/version/import check." >&2; exit 2; }
  printf '%s\n' "$expected" > "$marker.tmp"
  mv "$marker.tmp" "$marker"
  rm -f "$FLOW_BASE_HOME/base-creating"
  rmdir "$lock"
  trap - EXIT HUP INT TERM
fi
if [ "${1:-}" = '--bootstrap-only' ]; then exit 0; fi
exec "$python" -I -m flow_base --workspace "$FLOW_BASE_WORKSPACE" "$@"
