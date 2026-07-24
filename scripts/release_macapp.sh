#!/usr/bin/env bash
# One-shot release: build → sign → dmg → notarize + staple.
# Prereqs: Developer ID cert in Keychain, notary keychain profile, create-dmg.
set -euo pipefail
cd "$(dirname "$0")/.."

PROFILE="${NOTARY_PROFILE:-songcoach-notary}"

echo "==> Preflight…"
security find-identity -v -p codesigning | grep -q "Developer ID Application" \
  || { echo "!! No 'Developer ID Application' cert. Create one in Xcode first." >&2; exit 1; }
xcrun notarytool history --keychain-profile "$PROFILE" >/dev/null 2>&1 \
  || { echo "!! Notary profile '$PROFILE' missing/invalid. Run:" >&2; \
       echo "   xcrun notarytool store-credentials $PROFILE --apple-id <you> --team-id <TEAMID> --password <app-specific-pw>" >&2; exit 1; }

scripts/build_macapp.sh
scripts/sign_macapp.sh
scripts/make_dmg.sh
scripts/notarize_macapp.sh
echo "==> Release complete → dist/SongCoach.dmg"
