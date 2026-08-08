#!/bin/sh
set -eu

root_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
"$root_dir/scripts/build-worker.sh"
npm --prefix "$root_dir/app" run build

source_worker="$root_dir/app/src-tauri/resources/worker"
app_bundle="$root_dir/app/src-tauri/target/release/bundle/macos/SynVid.app"
destination_worker="$app_bundle/Contents/Resources/resources/worker"
test -d "$source_worker/synvid-worker"
test -d "$app_bundle"

# Tauri's resource copier omits nested framework/symlink content from a
# PyInstaller one-folder payload. `ditto` preserves that macOS bundle layout.
ditto "$source_worker" "$destination_worker"
test -x "$destination_worker/synvid-worker/synvid-worker"
printf '%s\n' "local app created: $app_bundle"
