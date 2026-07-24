#!/usr/bin/env bash
# Deep-sign dist/SongCoach.app for Developer ID distribution (hardened runtime).
# Signs every nested mach-O inside-out, then the .app last with entitlements.
#
#   scripts/sign_macapp.sh
#   DEVID="Developer ID Application: Name (TEAMID)" scripts/sign_macapp.sh
set -euo pipefail
cd "$(dirname "$0")/.."

APP="dist/SongCoach.app"
ENTITLEMENTS="packaging/entitlements.plist"

[ -d "$APP" ] || { echo "!! $APP not found — run scripts/build_macapp.sh first" >&2; exit 1; }
[ -f "$ENTITLEMENTS" ] || { echo "!! $ENTITLEMENTS not found" >&2; exit 1; }

if [ -z "${DEVID:-}" ]; then
  DEVID=$(security find-identity -v -p codesigning \
    | grep "Developer ID Application" | head -1 | sed -E 's/.*"(.*)"$/\1/')
fi
[ -n "$DEVID" ] || { echo "!! No 'Developer ID Application' identity. Create one in Xcode (Settings > Accounts > Manage Certificates) and retry." >&2; exit 1; }
echo "==> Signing with: $DEVID"

# 1. Sign nested mach-O binaries deepest-first (dylibs/.so/executables incl.
#    syscap, ffmpeg, ffprobe). Each must be signed before the bundle.
echo "==> Signing nested binaries (a minute or two for ~130 files)…"
while IFS= read -r f; do
  if file -b "$f" | grep -q "Mach-O"; then
    codesign --force --options runtime --timestamp --sign "$DEVID" "$f" >/dev/null
  fi
done < <(find "$APP/Contents" -type f \( -name '*.dylib' -o -name '*.so' -o -perm +111 \) | sort -r)

# 2. Sign the bundle last, with entitlements on the main executable.
echo "==> Signing the app bundle…"
codesign --force --options runtime --timestamp \
  --entitlements "$ENTITLEMENTS" --sign "$DEVID" "$APP"

# 3. Verify the signature (Gatekeeper still rejects until notarized — that's next).
echo "==> Verifying…"
codesign --verify --deep --strict --verbose=2 "$APP"
echo "==> Signed OK."
