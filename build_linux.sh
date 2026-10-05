#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "This build script must run on Linux; PyInstaller does not cross-compile." >&2
    exit 1
fi

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv-linux}"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    "$PYTHON" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install -r "$ROOT_DIR/requirements-build.txt"

BUILD_TMP="$(mktemp -d "${TMPDIR:-/tmp}/yalcs-linux-build.XXXXXX")"
trap 'rm -rf "$BUILD_TMP"' EXIT

"$VENV_DIR/bin/python" -m PyInstaller \
    --noconfirm \
    --clean \
    --onefile \
    --windowed \
    --collect-submodules bitstring \
    --collect-all tinytuya \
    --collect-all psutil \
    --collect-all tuya_sharing \
    --collect-all qrcode \
    --workpath "$BUILD_TMP/work" \
    --specpath "$BUILD_TMP/spec" \
    --distpath "$ROOT_DIR/dist" \
    --name YALCS-0.2.1 \
    "$ROOT_DIR/main.py"

echo "Executable created: $ROOT_DIR/dist/YALCS-0.2.1"
