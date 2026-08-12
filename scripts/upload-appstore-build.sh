#!/bin/sh
set -eu

# Submits an already signed App Store .pkg (from build-appstore-release.sh)
# to App Store Connect using an App Store Connect API key. This is the
# explicitly authorized upload step — it publishes a build to your app's
# TestFlight/App Store pipeline, so it must never be wired into
# build-appstore-release.sh or run automatically.
#
# Requires an App Store Connect API key (Users and Access > Keys in App
# Store Connect): the private key .p8 file, its Key ID, and your Issuer ID.
#
# Usage:
#   ./scripts/upload-appstore-build.sh [path/to/AI-Video Synthesizer-0.2.1-appstore.pkg]
#
#   SYNVID_ASC_KEY_ID, SYNVID_ASC_ISSUER_ID, and SYNVID_ASC_KEY_PATH must be set.
#   altool looks up the key by ID/issuer from a key file named
#   AuthKey_<KEY_ID>.p8 in ~/.appstoreconnect/private_keys/,
#   ~/private_keys/, or ./private_keys/ — SYNVID_ASC_KEY_PATH is copied there
#   if it isn't already in one of those locations.

root_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
source_pkg=${1:-"$root_dir/dist/appstore/AI-Video Synthesizer-0.2.1-appstore.pkg"}
key_id=${SYNVID_ASC_KEY_ID:?"set SYNVID_ASC_KEY_ID to the App Store Connect API key ID"}
issuer_id=${SYNVID_ASC_ISSUER_ID:?"set SYNVID_ASC_ISSUER_ID to the App Store Connect API issuer ID"}
key_path=${SYNVID_ASC_KEY_PATH:?"set SYNVID_ASC_KEY_PATH to the AuthKey_<KEY_ID>.p8 file path"}

test -f "$source_pkg"
test -f "$key_path"

key_dir="$HOME/.appstoreconnect/private_keys"
mkdir -p "$key_dir"
installed_key="$key_dir/AuthKey_$key_id.p8"
if [ ! -f "$installed_key" ]; then
    echo "installing API key into $installed_key..."
    cp "$key_path" "$installed_key"
    chmod 600 "$installed_key"
fi

echo "verifying input package is signed before submitting..."
codesign --verify --deep --strict --verbose=2 "$source_pkg" 2>&1 \
    || pkgutil --check-signature "$source_pkg"

echo "validating $source_pkg against App Store Connect before upload..."
xcrun altool --validate-app \
    --type macos \
    --file "$source_pkg" \
    --apiKey "$key_id" \
    --apiIssuer "$issuer_id"

echo "uploading $source_pkg to App Store Connect (can take several minutes)..."
xcrun altool --upload-app \
    --type macos \
    --file "$source_pkg" \
    --apiKey "$key_id" \
    --apiIssuer "$issuer_id"

printf '%s\n' "uploaded: $source_pkg" \
    "Processing happens on App Store Connect; check the TestFlight tab for the build once Apple finishes processing (usually a few minutes to an hour)."
