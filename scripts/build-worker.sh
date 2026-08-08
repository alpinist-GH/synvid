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
    --copy-metadata kokoro-onnx \
    --copy-metadata requests \
    --copy-metadata safetensors \
    --copy-metadata transformers \
    --collect-all imageio \
    --collect-all imageio_ffmpeg \
    --collect-all espeakng_loader \
    --collect-all kokoro_onnx \
    --collect-all language_tags \
    --collect-all requests \
    "$root_dir/packaging/worker_launcher.py"

worker_bin="$resource_dir/synvid-worker/synvid-worker"
test -x "$worker_bin"
# eSpeak's native library resolves phontab relative to its own directory even
# though the Python loader refers to `espeak-ng-data`. Keep both layouts in the
# one-folder bundle; a system espeak installation is never an allowed fallback.
espeak_data_dir=$("$python_bin" -c 'import espeakng_loader; from pathlib import Path; print(Path(espeakng_loader.__file__).parent / "espeak-ng-data")')
test -f "$espeak_data_dir/phontab"
ditto "$espeak_data_dir" "$resource_dir/synvid-worker/_internal/espeakng_loader"
test -f "$resource_dir/synvid-worker/_internal/espeakng_loader/phontab"
printf '%s\n' 'worker bundle created:' "$worker_bin"
