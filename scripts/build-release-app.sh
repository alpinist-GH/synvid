#!/bin/sh
set -eu

# Builds and signs (hardened runtime, Developer ID Application) a release
# .app and DMG. Does NOT notarize; that remains a separate explicitly
# authorized step. Every nested Mach-O is signed before the outer app is
# signed, and the outer app is signed before the DMG is created and signed,
# per Apple's inside-out signing requirement. Signatures use a secure
# timestamp (network call to Apple's TSA) because the notary service rejects
# any signature that lacks one.

root_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
signing_identity=${SYNVID_SIGNING_IDENTITY:?"set SYNVID_SIGNING_IDENTITY to the Developer ID Application identity (name or SHA-1)"}
entitlements="$root_dir/app/src-tauri/entitlements/release.entitlements"
test -f "$entitlements"

"$root_dir/scripts/build-worker.sh"
npm --prefix "$root_dir/app" run build

source_worker="$root_dir/app/src-tauri/resources/worker"
app_bundle="$root_dir/app/src-tauri/target/release/bundle/macos/AI-Video Synthesizer.app"
destination_worker="$app_bundle/Contents/Resources/resources/worker"
artifact_dir="$root_dir/dist/release"
dmg_path="$artifact_dir/AI-Video Synthesizer-0.2.1-signed-unnotarized.dmg"
work_dir=$(mktemp -d "${TMPDIR:-/tmp}/synvid-release-work.XXXXXX")
dmg_stage_dir=$(mktemp -d "${TMPDIR:-/tmp}/synvid-dmg.XXXXXX")
cleanup() { rm -rf "$work_dir" "$dmg_stage_dir"; }
trap cleanup EXIT HUP INT TERM

test -d "$source_worker/synvid-worker"
test -d "$app_bundle"

find "$source_worker" -type f -name .DS_Store -delete
rm -rf "$destination_worker"
ditto "$source_worker" "$destination_worker"
test -x "$destination_worker/synvid-worker/synvid-worker"

echo "signing nested Mach-O binaries in the worker payload..."
# Deepest paths first so a signed outer bundle is never re-walked into after
# its contents change; codesign refuses to sign a directory whose nested
# signatures it already validated if they change afterward.
find "$destination_worker" -type f \( -perm -u+x -o -name "*.dylib" -o -name "*.so" \) -print0 \
    | xargs -0 -n1 file --mime-type \
    | grep -E ': (application/x-mach-binary|application/x-sharedlib)$' \
    | cut -d: -f1 > "$work_dir/macho-files.txt" || true
wc -l < "$work_dir/macho-files.txt"
while IFS= read -r f; do
    codesign --force --options runtime --timestamp \
        --entitlements "$entitlements" \
        --sign "$signing_identity" "$f"
done < "$work_dir/macho-files.txt"

echo "signing Frameworks..."
find "$destination_worker" -type d -name "*.framework" -print0 | while IFS= read -r -d '' fw; do
    codesign --force --options runtime --timestamp \
        --entitlements "$entitlements" \
        --sign "$signing_identity" "$fw"
done

echo "signing outer app bundle..."
find "$app_bundle" -type f -name .DS_Store -delete
codesign --force --options runtime --timestamp \
    --entitlements "$entitlements" \
    --sign "$signing_identity" "$app_bundle"

echo "verifying signature..."
codesign --verify --deep --strict --verbose=2 "$app_bundle"

mkdir -p "$artifact_dir"
rm -f "$dmg_path"
ditto "$app_bundle" "$dmg_stage_dir/$(basename "$app_bundle")"
ln -s /Applications "$dmg_stage_dir/Applications"
hdiutil create -volname "AI-Video Synthesizer" -srcfolder "$dmg_stage_dir" -fs APFS -ov -format UDZO "$dmg_path" >/dev/null
test -s "$dmg_path"

echo "signing DMG..."
codesign --force --timestamp --sign "$signing_identity" "$dmg_path"
codesign --verify --verbose=2 "$dmg_path"

printf '%s\n' "signed app: $app_bundle" "signed unnotarized DMG: $dmg_path"
