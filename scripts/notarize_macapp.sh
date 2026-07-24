#!/usr/bin/env bash
# Notarize + staple the signed dist/SongCoach.dmg.
set -euo pipefail
cd "$(dirname "$0")/.."

DMG="dist/SongCoach.dmg"
PROFILE="${NOTARY_PROFILE:-songcoach-notary}"

[ -f "$DMG" ] || { echo "!! $DMG not found — run scripts/make_dmg.sh first" >&2; exit 1; }

echo "==> Submitting to Apple notary (a few minutes)…"
if ! xcrun notarytool submit "$DMG" --keychain-profile "$PROFILE" --wait; then
  echo "!! Notarization failed. Inspect the log with:" >&2
  echo "   xcrun notarytool history --keychain-profile $PROFILE" >&2
  echo "   xcrun notarytool log <submission-id> --keychain-profile $PROFILE" >&2
  echo "   (Common fix: add an entitlement to packaging/entitlements.plist, re-sign, retry.)" >&2
  exit 1
fi

echo "==> Stapling…"
xcrun stapler staple "$DMG"
xcrun stapler validate "$DMG"

echo "==> Gatekeeper check…"
spctl -a -t open --context context:primary-signature -vv "$DMG" || true
echo "==> Done: $DMG signed, notarized, stapled."
