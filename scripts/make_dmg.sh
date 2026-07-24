#!/usr/bin/env bash
# Build a signed dist/SongCoach.dmg from the signed dist/SongCoach.app.
set -euo pipefail
cd "$(dirname "$0")/.."

APP="dist/SongCoach.app"
DMG="dist/SongCoach.dmg"
VOLNAME="SongCoach"

[ -d "$APP" ] || { echo "!! $APP not found — build + sign first" >&2; exit 1; }
rm -f "$DMG"

if command -v create-dmg >/dev/null 2>&1; then
  # create-dmg can exit non-zero on cosmetic AppleScript issues; check the file after.
  create-dmg \
    --volname "$VOLNAME" \
    --window-size 640 360 \
    --icon "SongCoach.app" 160 180 \
    --app-drop-link 480 180 \
    "$DMG" "$APP" || true
else
  echo "!! create-dmg not found; using hdiutil (plain layout). brew install create-dmg for a nicer one." >&2
  TMP=$(mktemp -d)
  cp -R "$APP" "$TMP/"
  ln -s /Applications "$TMP/Applications"
  hdiutil create -volname "$VOLNAME" -srcfolder "$TMP" -ov -format UDZO "$DMG"
  rm -rf "$TMP"
fi

[ -f "$DMG" ] || { echo "!! DMG was not created" >&2; exit 1; }

if [ -z "${DEVID:-}" ]; then
  DEVID=$(security find-identity -v -p codesigning \
    | grep "Developer ID Application" | head -1 | sed -E 's/.*"(.*)"$/\1/')
fi
[ -n "$DEVID" ] || { echo "!! No Developer ID identity to sign the DMG" >&2; exit 1; }
codesign --force --sign "$DEVID" --timestamp "$DMG"
echo "==> Built + signed $DMG"
