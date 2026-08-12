#!/bin/sh
set -eu

# Builds, sandboxes, and signs (Apple Distribution certificate + embedded
# provisioning profile) a Mac App Store release .app, then wraps it in a
# signed installer .pkg via productbuild. This is the App Store / TestFlight
# counterpart to build-release-app.sh, which instead produces a Developer ID
# DMG for direct distribution (PLAN.md's documented v1 default). Both paths
# build from the same source and worker bundle; only signing identity,
# entitlements, and the final artifact format differ.
#
# Does NOT upload anything. scripts/upload-appstore-build.sh is the separate,
# explicitly authorized step that submits the .pkg to App Store Connect.
#
# Requires:
#   - An Apple Distribution certificate in the login keychain.
#   - A "3rd Party Mac Developer Installer" certificate in the login keychain.
#   - A Mac App Store provisioning profile for the app's bundle identifier
#     (app/src-tauri/tauri.conf.json's "identifier"), downloaded from
#     developer.apple.com and passed via SYNVID_APPSTORE_PROVISIONING_PROFILE.
#
# Usage:
#   SYNVID_APPSTORE_DISTRIBUTION_IDENTITY="Apple Distribution: Your Name (TEAMID)" \
#   SYNVID_APPSTORE_INSTALLER_IDENTITY="3rd Party Mac Developer Installer: Your Name (TEAMID)" \
#   SYNVID_APPSTORE_PROVISIONING_PROFILE="$HOME/Library/MobileDevice/Provisioning Profiles/XXXX.provisionprofile" \
#   ./scripts/build-appstore-release.sh

root_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
distribution_identity=${SYNVID_APPSTORE_DISTRIBUTION_IDENTITY:?"set SYNVID_APPSTORE_DISTRIBUTION_IDENTITY to the Apple Distribution identity (name or SHA-1)"}
installer_identity=${SYNVID_APPSTORE_INSTALLER_IDENTITY:?"set SYNVID_APPSTORE_INSTALLER_IDENTITY to the 3rd Party Mac Developer Installer identity (name or SHA-1)"}
provisioning_profile=${SYNVID_APPSTORE_PROVISIONING_PROFILE:?"set SYNVID_APPSTORE_PROVISIONING_PROFILE to the path of the Mac App Store .provisionprofile for this app's bundle identifier"}
build_number=${SYNVID_APPSTORE_BUILD_NUMBER:-$(date -u +%Y%m%d.%H%M)}
entitlements="$root_dir/app/src-tauri/entitlements/appstore.entitlements"
test -f "$entitlements"
test -f "$provisioning_profile"

app_version=$(node -pe "require('$root_dir/app/src-tauri/tauri.conf.json').version")
bundle_identifier=$(node -pe "require('$root_dir/app/src-tauri/tauri.conf.json').identifier")

"$root_dir/scripts/build-worker.sh"
npm --prefix "$root_dir/app" run build

source_worker="$root_dir/app/src-tauri/resources/worker"
app_bundle="$root_dir/app/src-tauri/target/release/bundle/macos/AI-Video Synthesizer.app"
destination_worker="$app_bundle/Contents/Resources/resources/worker"
artifact_dir="$root_dir/dist/appstore"
pkg_path="$artifact_dir/AI-Video Synthesizer-$app_version-appstore.pkg"
work_dir=$(mktemp -d "${TMPDIR:-/tmp}/synvid-appstore-work.XXXXXX")
cleanup() { rm -rf "$work_dir"; }
trap cleanup EXIT HUP INT TERM

test -d "$source_worker/synvid-worker"
test -d "$app_bundle"

find "$source_worker" -type f -name .DS_Store -delete
rm -rf "$destination_worker"
ditto "$source_worker" "$destination_worker"
test -x "$destination_worker/synvid-worker/synvid-worker"

echo "setting App Store Info.plist keys (build $build_number, bundle $bundle_identifier)..."
info_plist="$app_bundle/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $build_number" "$info_plist"
/usr/libexec/PlistBuddy -c "Add :LSApplicationCategoryType string public.app-category.video" "$info_plist" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Set :LSApplicationCategoryType public.app-category.video" "$info_plist"
# The app only ever speaks standard HTTPS (model downloads, Hugging Face Hub);
# it uses no proprietary/non-exempt cryptography, so export compliance is
# answerable from this key instead of a manual per-build App Store Connect prompt.
/usr/libexec/PlistBuddy -c "Add :ITSAppUsesNonExemptEncryption bool false" "$info_plist" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Set :ITSAppUsesNonExemptEncryption false" "$info_plist"

echo "embedding provisioning profile..."
cp "$provisioning_profile" "$app_bundle/Contents/embedded.provisionprofile"

echo "signing nested Mach-O binaries in the worker payload..."
# Deepest paths first, same inside-out signing requirement as the Developer
# ID build — see build-release-app.sh.
find "$destination_worker" -type f \( -perm -u+x -o -name "*.dylib" -o -name "*.so" \) -print0 \
    | xargs -0 -n1 file --mime-type \
    | grep -E ': (application/x-mach-binary|application/x-sharedlib)$' \
    | cut -d: -f1 > "$work_dir/macho-files.txt" || true
wc -l < "$work_dir/macho-files.txt"
while IFS= read -r f; do
    codesign --force --options runtime --timestamp \
        --entitlements "$entitlements" \
        --sign "$distribution_identity" "$f"
done < "$work_dir/macho-files.txt"

echo "signing Frameworks..."
find "$destination_worker" -type d -name "*.framework" -print0 | while IFS= read -r -d '' fw; do
    codesign --force --options runtime --timestamp \
        --entitlements "$entitlements" \
        --sign "$distribution_identity" "$fw"
done

echo "signing outer app bundle..."
find "$app_bundle" -type f -name .DS_Store -delete
codesign --force --options runtime --timestamp \
    --entitlements "$entitlements" \
    --sign "$distribution_identity" "$app_bundle"

echo "verifying signature and sandbox entitlement..."
codesign --verify --deep --strict --verbose=2 "$app_bundle"
codesign -d --entitlements :- "$app_bundle" | grep -q "com.apple.security.app-sandbox"

mkdir -p "$artifact_dir"
rm -f "$pkg_path"
productbuild --component "$app_bundle" /Applications \
    --sign "$installer_identity" \
    "$pkg_path"
test -s "$pkg_path"

printf '%s\n' "signed App Store app: $app_bundle" "signed installer package: $pkg_path"
