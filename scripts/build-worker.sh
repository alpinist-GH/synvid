#!/bin/sh
set -eu

root_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python_bin=${SYNVID_PYTHON:-"$root_dir/venv/bin/python"}
resource_dir="$root_dir/app/src-tauri/resources/worker"
work_dir="$root_dir/build/pyinstaller"

# transformers' Auto* classes (video/image processors, tokenizers, configs)
# resolve each model's module via a runtime-built importlib.import_module()
# string, which PyInstaller's static analysis cannot trace. Without
# --collect-submodules, only per-model files that happen to be imported
# elsewhere in the traced graph get bundled, silently dropping others (e.g.
# transformers/models/qwen2_vl/video_processing_qwen2_vl.py) and breaking
# AutoVideoProcessor resolution only in the frozen build.
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
    --collect-data mlx \
    --hidden-import mlx._reprlib_fix \
    --collect-all kokoro_onnx \
    --collect-all language_tags \
    --collect-all requests \
    --collect-data transformers \
    --collect-submodules transformers.models \
    "$root_dir/packaging/worker_launcher.py"

worker_bin="$resource_dir/synvid-worker/synvid-worker"
test -x "$worker_bin"
# Homebrew's framework Python can leave PyInstaller's base_library.zip out of
# an otherwise successful one-folder collection. The embedded interpreter
# cannot start without stdlib encodings, so create a deterministic stdlib zip
# from the exact locked build Python when that collector defect occurs.
base_library="$resource_dir/synvid-worker/_internal/base_library.zip"
if [ ! -f "$base_library" ]; then
    stdlib_dir=$("$python_bin" -c 'import sysconfig; print(sysconfig.get_path("stdlib"))')
    "$python_bin" - "$stdlib_dir" "$base_library" <<'PY'
from pathlib import Path
import sys
import zipfile

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
    for path in source.rglob("*.py"):
        relative = path.relative_to(source)
        if "__pycache__" not in relative.parts and "site-packages" not in relative.parts:
            archive.write(path, relative.as_posix())
PY
fi
test -f "$base_library"
# eSpeak's native library resolves phontab relative to its own directory even
# though the Python loader refers to `espeak-ng-data`. Keep both layouts in the
# one-folder bundle; a system espeak installation is never an allowed fallback.
espeak_data_dir=$("$python_bin" -c 'import espeakng_loader; from pathlib import Path; print(Path(espeakng_loader.__file__).parent / "espeak-ng-data")')
test -f "$espeak_data_dir/phontab"
ditto "$espeak_data_dir" "$resource_dir/synvid-worker/_internal/espeakng_loader"
test -f "$resource_dir/synvid-worker/_internal/espeakng_loader/phontab"
printf '%s\n' 'worker bundle created:' "$worker_bin"
