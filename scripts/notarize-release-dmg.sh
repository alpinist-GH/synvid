#!/bin/sh
set -eu

# Submits an already signed, hardened-runtime DMG (from build-release-app.sh)
# to Apple's notary service, staples the ticket, and validates the result
# with stapler/spctl. This is the "explicitly authorized" notarization step
# called out in PLAN.md/AGENTS.md — it uploads the DMG to Apple, so it must
# never be wired into build-release-app.sh or any other automatic path.
#
# Usage:
#   xcrun notarytool store-credentials PROFILE_NAME \
#       --apple-id you@example.com --team-id TEAMID --password APP_SPECIFIC_PW
#   ./scripts/notarize-release-dmg.sh [path/to/AI-Video Synthesizer-0.2.0-signed-unnotarized.dmg]
#   (defaults to the "synvid-notary" keychain profile; override with
#   SYNVID_NOTARY_PROFILE=OTHER_PROFILE)

root_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
source_dmg=${1:-"$root_dir/dist/release/AI-Video Synthesizer-0.2.0-signed-unnotarized.dmg"}
notary_profile=${SYNVID_NOTARY_PROFILE:-synvid-notary}

test -f "$source_dmg"

echo "verifying input DMG is signed before submitting..."
codesign --verify --verbose=2 "$source_dmg"

case "$source_dmg" in
    *-signed-unnotarized.dmg)
        notarized_dmg=$(printf '%s' "$source_dmg" | sed 's/-signed-unnotarized\.dmg$/-notarized.dmg/')
        ;;
    *)
        notarized_dmg="$root_dir/dist/release/$(basename "${source_dmg%.dmg}")-notarized.dmg"
        ;;
esac

mkdir -p "$(dirname "$notarized_dmg")"
rm -f "$notarized_dmg"
cp "$source_dmg" "$notarized_dmg"

mount_point=$(mktemp -d "${TMPDIR:-/tmp}/synvid-notarize-mount.XXXXXX")
cleanup() {
    hdiutil detach "$mount_point" -quiet >/dev/null 2>&1 || true
    rmdir "$mount_point" 2>/dev/null || true
}
trap cleanup EXIT HUP INT TERM

echo "submitting $notarized_dmg to Apple's notary service (uploads the DMG; can take several minutes)..."
xcrun notarytool submit "$notarized_dmg" --keychain-profile "$notary_profile" --wait

echo "stapling notarization ticket to the DMG..."
xcrun stapler staple "$notarized_dmg"

echo "validating stapled ticket..."
xcrun stapler validate "$notarized_dmg"

echo "verifying Gatekeeper accepts the notarized DMG..."
spctl -a -t open --context context:primary-signing-identifier -v "$notarized_dmg"

echo "mounting DMG to verify the stapled .app passes Gatekeeper execution assessment..."
hdiutil attach "$notarized_dmg" -mountpoint "$mount_point" -nobrowse -quiet
app_path="$mount_point/AI-Video Synthesizer.app"
test -d "$app_path"
spctl -a -t exec -vv "$app_path"

printf '%s\n' "notarized and stapled DMG: $notarized_dmg"
