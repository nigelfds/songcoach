# Phase 2 Packaging — Sign, Notarize, DMG — Implementation Plan

> **For agentic workers:** Executed inline (single-agent). Agent writes the config/scripts/docs (Tasks 1–3); Task 4 is user-run (signing needs the real cert + Apple creds), agent guides and interprets. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Turn the working unsigned `dist/SongCoach.app` into a Developer ID–signed, notarized, stapled `.dmg` that a few other Mac users can download and double-click.

**Architecture:** An entitlements plist + four shell scripts (sign → dmg → notarize → orchestrator) + a docs runbook. Signing/notarization runs on the user's Mac with their certificate and notary credentials; the agent builds the tooling only.

**Tech Stack:** `codesign`, `xcrun notarytool`/`stapler`, `create-dmg` (Homebrew), hardened runtime. Xcode + notarytool 1.1.2 already installed.

## Global Constraints

- arm64-only app; bundle id `in.nigel.songcoach` (already in `SongCoach.spec`).
- **No automated tests** — signing requires the real Developer ID cert. Per-file verification is `bash -n` (syntax), `shellcheck` (if installed), and `plutil -lint` for the plist. Real verification is the user running the scripts + a second-Mac download test.
- Scripts live in `scripts/`, are `chmod +x`, and `cd` to repo root so they run from anywhere. Match the existing `scripts/build_macapp.sh` style (bash, `set -euo pipefail`, `==>` progress echoes).
- Do NOT commit `dist/`, `vendor/`, `*.dmg`, or the cert — those are build outputs/secrets (confirm they're gitignored).
- Signing identity resolved from `$DEVID` or auto-detected via `security find-identity`. Notary keychain profile name: `songcoach-notary` (overridable via `$NOTARY_PROFILE`).
- Commit after each agent task; end every commit message with:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

### Task 1: Entitlements + signing script

**Files:**
- Create: `packaging/entitlements.plist`
- Create: `scripts/sign_macapp.sh`

**Interfaces:**
- Produces: a signed `dist/SongCoach.app` (hardened runtime). Consumed by `make_dmg.sh` (Task 2). Reads `$DEVID` or auto-detects.

- [ ] **Step 1: Write the entitlements**

Create `packaging/entitlements.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <!-- PyInstaller/CPython + torch need these under the hardened runtime -->
  <key>com.apple.security.cs.allow-jit</key>
  <true/>
  <key>com.apple.security.cs.allow-unsigned-executable-memory</key>
  <true/>
  <!-- torch ships ~130 unsigned .dylib/.so; strict library validation would block them -->
  <key>com.apple.security.cs.disable-library-validation</key>
  <true/>
</dict>
</plist>
```

- [ ] **Step 2: Validate the plist**

Run: `plutil -lint packaging/entitlements.plist`
Expected: `packaging/entitlements.plist: OK`

- [ ] **Step 3: Write the signing script**

Create `scripts/sign_macapp.sh`:

```bash
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
```

- [ ] **Step 4: Make executable + syntax check**

Run:
```bash
chmod +x scripts/sign_macapp.sh
bash -n scripts/sign_macapp.sh && echo "syntax OK"
command -v shellcheck >/dev/null && shellcheck scripts/sign_macapp.sh || echo "(shellcheck not installed — skipped)"
```
Expected: `syntax OK` (shellcheck clean or skipped).

- [ ] **Step 5: Commit**

```bash
git add packaging/entitlements.plist scripts/sign_macapp.sh
git commit -m "feat(packaging): entitlements + Developer ID signing script

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: DMG + notarization scripts

**Files:**
- Create: `scripts/make_dmg.sh`
- Create: `scripts/notarize_macapp.sh`

**Interfaces:**
- Consumes: signed `dist/SongCoach.app` (Task 1). Produces: signed, notarized, stapled `dist/SongCoach.dmg`.

- [ ] **Step 1: Write the DMG script**

Create `scripts/make_dmg.sh`:

```bash
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
```

- [ ] **Step 2: Write the notarize script**

Create `scripts/notarize_macapp.sh`:

```bash
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
```

- [ ] **Step 3: Make executable + syntax check both**

Run:
```bash
chmod +x scripts/make_dmg.sh scripts/notarize_macapp.sh
bash -n scripts/make_dmg.sh && bash -n scripts/notarize_macapp.sh && echo "syntax OK"
command -v shellcheck >/dev/null && shellcheck scripts/make_dmg.sh scripts/notarize_macapp.sh || echo "(shellcheck skipped)"
```
Expected: `syntax OK`.

- [ ] **Step 4: Commit**

```bash
git add scripts/make_dmg.sh scripts/notarize_macapp.sh
git commit -m "feat(packaging): DMG build + notarize/staple scripts

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Release orchestrator + docs runbook

**Files:**
- Create: `scripts/release_macapp.sh`
- Modify: `docs/packaging.md` (rewrite Phase 2)

**Interfaces:**
- Consumes: all scripts from Tasks 1–2 + the existing `scripts/build_macapp.sh`.

- [ ] **Step 1: Write the orchestrator**

Create `scripts/release_macapp.sh`:

```bash
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
```

- [ ] **Step 2: Make executable + syntax check**

Run: `chmod +x scripts/release_macapp.sh && bash -n scripts/release_macapp.sh && echo "syntax OK"`
Expected: `syntax OK`.

- [ ] **Step 3: Rewrite `docs/packaging.md` Phase 2**

Replace the "## Phase 2 — sign, notarize, DMG — TODO" section with a runbook that documents: the one-time prereqs (Developer ID cert via Xcode; `store-credentials`; `brew install create-dmg`), the four scripts and `release_macapp.sh`, the notary-log iteration loop, and how to verify (a second-Mac download → double-click → screen-recording capture). Keep the existing Phase 0/1/3 sections. Mark Phase 1 as DONE (verified end-to-end this session).

- [ ] **Step 4: Commit**

```bash
git add scripts/release_macapp.sh docs/packaging.md
git commit -m "feat(packaging): release orchestrator + Phase 2 runbook

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Guided execution (user-run) + iterate

The agent cannot sign/notarize (no cert, no Apple network). The agent guides each step and interprets output the user pastes back.

- [ ] **Step 1: Confirm gitignore** — verify `dist/`, `vendor/`, `*.dmg` are ignored so no build output/secret gets committed; add any that aren't.

- [ ] **Step 2: User creates the Developer ID cert** (Xcode → Settings → Accounts → Manage Certificates → + → "Developer ID Application"). Verify: `security find-identity -v -p codesigning` shows `Developer ID Application: … (TEAMID)`. Record the TEAMID.

- [ ] **Step 3: User stores notary creds** — app-specific password from appleid.apple.com, then `xcrun notarytool store-credentials songcoach-notary --apple-id nigel@nigel.in --team-id <TEAMID> --password <pw>`.

- [ ] **Step 4: `brew install create-dmg`.**

- [ ] **Step 5: Run `scripts/release_macapp.sh`.** If notarization fails, get `xcrun notarytool log <id> --keychain-profile songcoach-notary`, adjust `packaging/entitlements.plist` (likely add `com.apple.security.cs.allow-dyld-environment-variables`), re-run. Commit any entitlement change.

- [ ] **Step 6: Verify on a second Mac / fresh user** — download `dist/SongCoach.dmg`, double-click, confirm it opens with no Gatekeeper block and that capture (Screen & System Audio Recording) works. This is the real acceptance test.

---

## Self-Review Notes

- **Spec coverage:** entitlements (T1), inside-out signing (T1), DMG + sign (T2), notarize + staple (T2), orchestrator (T3), docs runbook (T3), user prereqs + execution + notary-log iteration (T4). All spec deliverables covered.
- **Split of responsibility honored:** agent writes T1–T3 (no cert needed); T4 is user-run with agent guidance.
- **No placeholders in scripts** — full content given; identity/profile resolved via documented env vars with auto-detect fallback.
