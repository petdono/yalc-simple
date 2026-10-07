#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != Linux ]]; then
    echo "Build on Linux (or WSL); PyInstaller does not cross-compile." >&2
    exit 1
fi

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"
VERSION="${VERSION:-0.2.2}"
case "$(uname -m)" in
    x86_64) ARCH=x86_64; TOOL_SHA256=ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0 ;;
    aarch64) ARCH=aarch64; TOOL_SHA256=f0837e7448a0c1e4e650a93bb3e85802546e60654ef287576f46c71c126a9158 ;;
    *) echo "Supported build architectures: x86_64 and aarch64." >&2; exit 1 ;;
esac
if [[ ! "$VERSION" =~ ^[0-9A-Za-z][0-9A-Za-z._-]*$ ]]; then
    echo "VERSION must contain only letters, numbers, dots, underscores or hyphens." >&2
    exit 1
fi

# Use native Linux storage even when sources are on a Windows/OneDrive mount.
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/yalcs"
VENV_DIR="${VENV_DIR:-$CACHE_DIR/build-venv}"
DIST_DIR="${DIST_DIR:-$ROOT_DIR/dist}"
mkdir -p "$CACHE_DIR" "$DIST_DIR"
DIST_DIR="$(cd -- "$DIST_DIR" && pwd)"
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    "$PYTHON" -m venv "$VENV_DIR"
fi
if ! "$VENV_DIR/bin/python" -c 'import tkinter; tkinter.Tcl()' >/dev/null 2>&1; then
    echo "Install Tkinter for the build Python (Ubuntu/Debian: sudo apt install python3-tk python3-venv)." >&2
    exit 1
fi
"$VENV_DIR/bin/python" -m pip install -r "$ROOT_DIR/requirements-build.txt"
export PYTHONDONTWRITEBYTECODE=1
(cd "$ROOT_DIR" && "$VENV_DIR/bin/python" -m unittest discover -v)

BUILD_TMP="$(mktemp -d "$CACHE_DIR/build.XXXXXX")"
trap 'rm -rf -- "$BUILD_TMP"' EXIT
APPDIR="$BUILD_TMP/YALCS.AppDir"
mkdir -p "$APPDIR/usr/lib" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/scalable/apps"

# onedir keeps resources in a normal tree, avoiding nested onefile extraction.
"$VENV_DIR/bin/python" -m PyInstaller \
    --noconfirm --clean --onedir \
    --hidden-import tkinter --hidden-import _tkinter \
    --collect-submodules bitstring \
    --collect-all tinytuya --collect-all psutil \
    --collect-all tuya_sharing --collect-all qrcode --collect-all certifi \
    --workpath "$BUILD_TMP/work" --specpath "$BUILD_TMP/spec" \
    --distpath "$BUILD_TMP/payload" --name yalcs "$ROOT_DIR/main.py"
cp -a "$BUILD_TMP/payload/yalcs" "$APPDIR/usr/lib/yalcs"
install -m 755 "$ROOT_DIR/packaging/linux/AppRun" "$APPDIR/AppRun"
install -m 644 "$ROOT_DIR/packaging/linux/yalcs.desktop" "$APPDIR/yalcs.desktop"
printf 'X-AppImage-Version=%s\n' "$VERSION" >> "$APPDIR/yalcs.desktop"
cp "$APPDIR/yalcs.desktop" "$APPDIR/usr/share/applications/yalcs.desktop"
install -m 644 "$ROOT_DIR/packaging/linux/yalcs.svg" "$APPDIR/yalcs.svg"
cp "$APPDIR/yalcs.svg" "$APPDIR/usr/share/icons/hicolor/scalable/apps/yalcs.svg"
ln -s yalcs.svg "$APPDIR/.DirIcon"
if command -v desktop-file-validate >/dev/null; then
    desktop-file-validate "$APPDIR/yalcs.desktop"
fi
"$APPDIR/AppRun" --self-check --headless

# The folder archive needs neither FUSE nor a mount.
tar -C "$BUILD_TMP" -czf "$BUILD_TMP/YALCS-$VERSION-linux-$ARCH.tar.gz" YALCS.AppDir

if [[ "${APPDIR_ONLY:-0}" != 1 ]]; then
    TOOL="${APPIMAGETOOL:-$CACHE_DIR/appimagetool-1.9.1-$ARCH.AppImage}"
    if [[ -z "${APPIMAGETOOL:-}" ]]; then
        if [[ ! -f "$TOOL" ]] || ! printf '%s  %s\n' "$TOOL_SHA256" "$TOOL" | sha256sum -c - >/dev/null 2>&1; then
            curl --fail --location --retry 3 \
                "https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-$ARCH.AppImage" \
                -o "$BUILD_TMP/appimagetool"
            printf '%s  %s\n' "$TOOL_SHA256" "$BUILD_TMP/appimagetool" | sha256sum -c -
            install -m 755 "$BUILD_TMP/appimagetool" "$TOOL"
        fi
    fi
    # Extract the packaging tool itself so builders need no FUSE either.
    (cd "$BUILD_TMP" && "$TOOL" --appimage-extract >/dev/null)
    runtime_args=()
    if [[ -n "${APPIMAGE_RUNTIME:-}" ]]; then
        runtime_args=(--runtime-file "$APPIMAGE_RUNTIME")
    fi
    ARCH="$ARCH" "$BUILD_TMP/squashfs-root/AppRun" \
        "${runtime_args[@]}" --comp zstd \
        --mksquashfs-opt -processors --mksquashfs-opt 2 \
        "$APPDIR" "$BUILD_TMP/YALCS-$VERSION-$ARCH.AppImage"
    chmod +x "$BUILD_TMP/YALCS-$VERSION-$ARCH.AppImage"
    "$BUILD_TMP/YALCS-$VERSION-$ARCH.AppImage" --appimage-extract-and-run --self-check --headless
    cp "$BUILD_TMP/YALCS-$VERSION-$ARCH.AppImage" "$DIST_DIR/"
fi
cp "$BUILD_TMP/YALCS-$VERSION-linux-$ARCH.tar.gz" "$DIST_DIR/"
(cd "$DIST_DIR" && sha256sum "YALCS-$VERSION-linux-$ARCH.tar.gz" > "YALCS-$VERSION-linux-$ARCH.tar.gz.sha256")
if [[ "${APPDIR_ONLY:-0}" != 1 ]]; then
    (cd "$DIST_DIR" && sha256sum "YALCS-$VERSION-$ARCH.AppImage" > "YALCS-$VERSION-$ARCH.AppImage.sha256")
fi
echo "Linux packages created in $DIST_DIR"
