#!/bin/sh
set -eu

root_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
"$root_dir/scripts/build-worker.sh"
npm --prefix "$root_dir/app" run build

source_worker="$root_dir/app/src-tauri/resources/worker"
app_bundle="$root_dir/app/src-tauri/target/release/bundle/macos/SynVid.app"
destination_worker="$app_bundle/Contents/Resources/resources/worker"
artifact_dir="$root_dir/dist"
dmg_path="$artifact_dir/SynVid-0.2.0-unsigned.dmg"
stage_dir=$(mktemp -d "${TMPDIR:-/tmp}/synvid-dmg.XXXXXX")
cleanup() {
    rm -rf "$stage_dir"
}
trap cleanup EXIT HUP INT TERM
test -d "$source_worker/synvid-worker"
test -d "$app_bundle"

# Tauri's resource copier omits nested framework/symlink content from a
# PyInstaller one-folder payload. `ditto` preserves that macOS bundle layout.
# Finder metadata is neither runtime content nor distributable payload. Remove
# it from the generated worker tree before copying so an inherited extended
# attribute cannot make the package build fail.
find "$source_worker" -type f -name .DS_Store -delete
ditto "$source_worker" "$destination_worker"
test -x "$destination_worker/synvid-worker/synvid-worker"
mkdir -p "$artifact_dir"
rm -f "$dmg_path"
find "$app_bundle" -type f -name .DS_Store -delete
ditto "$app_bundle" "$stage_dir/SynVid.app"
ln -s /Applications "$stage_dir/Applications"
# Explicit APFS avoids hdiutil's unreliable automatic filesystem selection on
# recent macOS releases (which can otherwise fail with "device not configured").
hdiutil create -volname SynVid -srcfolder "$stage_dir" -fs APFS -ov -format UDZO "$dmg_path" >/dev/null
test -s "$dmg_path"
printf '%s\n' "local app created: $app_bundle"
printf '%s\n' "unsigned local DMG created: $dmg_path"
