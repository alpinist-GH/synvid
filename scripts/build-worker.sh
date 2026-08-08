#!/bin/sh
set -eu

root_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python_bin=${SYNVID_PYTHON:-"$root_dir/venv/bin/python"}
resource_dir="$root_dir/app/src-tauri/resources/worker"
work_dir="$root_dir/build/pyinstaller"

PYINSTALLER_CONFIG_DIR="$work_dir/config" "$python_bin" -m PyInstaller \
    --noconfirm \
    --clean \
    --onedir \
    --name synvid-worker \
    --distpath "$resource_dir" \
    --workpath "$work_dir" \
    --specpath "$work_dir" \
    --paths "$root_dir" \
    --copy-metadata accelerate \
    --copy-metadata diffusers \
    --copy-metadata huggingface_hub \
    --copy-metadata requests \
    --copy-metadata safetensors \
    --copy-metadata transformers \
    --collect-all imageio \
    --collect-all imageio_ffmpeg \
    --collect-all requests \
    "$root_dir/packaging/worker_launcher.py"

worker_bin="$resource_dir/synvid-worker/synvid-worker"
test -x "$worker_bin"
printf '%s\n' 'worker bundle created:' "$worker_bin"
