# Phase 2 packaging: sign, notarize, DMG

**Date:** 2026-07-24
**Status:** Approved (design)

## Goal

Turn the working unsigned `dist/SongCoach.app` (PyInstaller, arm64, ~590 MB, ~133
nested mach-O binaries) into a **Developer ID–signed, notarized, stapled `.dmg`** that
a handful of other Mac users can download and double-click — with the Screen & System
Audio Recording permission working reliably (which requires the hardened runtime +
notarization).

This extends `docs/packaging.md` Phase 2. Phases 0–1 are done and the app is verified
end-to-end (capture → stem → playback from the `.app`).

## Split of responsibility

Signing/notarization runs on the user's Mac with their certificate and Apple credentials
(the agent sandbox has neither, and can't reach Apple's notary service). So:

- **Agent builds:** entitlements plist, the shell scripts, and the runbook in the docs.
- **User does (guided + verified by agent):** create the Developer ID cert, store notary
  credentials, `brew install create-dmg`, and run the release script.

## User prerequisites (one-time, interactive)

1. **Developer ID Application certificate** — via Xcode (Settings → Accounts → Apple ID →
   Manage Certificates → + → "Developer ID Application"). Verify with
   `security find-identity -v -p codesigning` → shows `Developer ID Application: … (TEAMID)`.
   (Current machine has only an "Apple Development" cert; membership is active.)
2. **Notary credentials** — app-specific password from appleid.apple.com, stored once:
   `xcrun notarytool store-credentials songcoach-notary --apple-id nigel@nigel.in --team-id <TEAMID> --password <app-specific-pw>`.
3. `brew install create-dmg`.

Environment already present: full Xcode, `notarytool` 1.1.2, `codesign`.

## Deliverables (agent-built)

### `packaging/entitlements.plist`
Hardened-runtime entitlements for a PyInstaller + torch app:
- `com.apple.security.cs.allow-jit` = true
- `com.apple.security.cs.allow-unsigned-executable-memory` = true
- `com.apple.security.cs.disable-library-validation` = true (torch's many unsigned
  `.dylib`/`.so` won't load under strict validation)

These are the usual culprits; the real notary log may require adding
`allow-dyld-environment-variables` — treated as a known iteration point, not a blocker.

### `scripts/sign_macapp.sh`
Deep sign **inside-out** (NOT `codesign --deep`):
1. Find every mach-O under `dist/SongCoach.app` (dylibs, `.so`, and the helper
   executables `syscap`/`ffmpeg`/`ffprobe`), sign each with
   `--force --options runtime --timestamp --sign "$IDENTITY"`.
2. Sign the app bundle **last** with the same flags plus
   `--entitlements packaging/entitlements.plist`.
3. Verify: `codesign --verify --deep --strict --verbose=2 dist/SongCoach.app`.

Identity resolved from a `DEVID` env var or auto-detected via `security find-identity`.

### `scripts/make_dmg.sh`
`create-dmg` with an `/Applications` symlink and the app; then codesign the `.dmg` with
the Developer ID identity. Falls back to `hdiutil` if `create-dmg` is absent (with a note).

### `scripts/notarize_macapp.sh`
Notarize the **DMG** (one pass; stapling the DMG covers the signed app inside):
`xcrun notarytool submit dist/SongCoach.dmg --keychain-profile songcoach-notary --wait`
→ on Accepted, `xcrun stapler staple dist/SongCoach.dmg` → verify with
`spctl -a -t open --context context:primary-signature -v dist/SongCoach.dmg`. On failure,
print `notarytool log <submission-id>` guidance.

### `scripts/release_macapp.sh`
One-shot orchestrator: `build_macapp.sh` → `sign_macapp.sh` → `make_dmg.sh` →
`notarize_macapp.sh`. Fails fast with a clear message if the cert/creds/tools are missing.

### `docs/packaging.md`
Rewrite Phase 2 from a TODO sketch into the exact runbook (prereqs, each script, the
notary-log iteration loop, verification commands).

## Flow

```
build_macapp.sh   → dist/SongCoach.app  (unsigned, existing)
sign_macapp.sh    → app + all 133 nested binaries signed, hardened runtime + entitlements
make_dmg.sh       → dist/SongCoach.dmg  (signed)
notarize_macapp.sh→ notarytool submit --wait → stapler staple → spctl verify
```

## Testing / verification

No automated tests (signing needs the real cert; can't run in CI/sandbox). Verification is
the built-in checks each script runs (`codesign --verify --strict`, `spctl`,
`stapler validate`) plus the real end user test: download the DMG on a *second* Mac (or a
fresh user account), double-click, and confirm it opens without a Gatekeeper block and
that screen-recording capture works.

## Non-goals (Phase 3, deferred)

Auto-update (Sparkle), universal2/Intel build, and a lighter CoreML/ONNX separation engine
to shrink the download.
