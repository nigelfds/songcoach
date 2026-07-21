#!/usr/bin/env bash
# Build the SongCoach macOS .app (Phase 1 — unsigned). Run from anywhere:
#   scripts/build_macapp.sh
#
# Gathers the vendored artifacts (syscap, htdemucs weights, ffmpeg/ffprobe) then
# runs PyInstaller against SongCoach.spec → dist/SongCoach.app
set -euo pipefail
cd "$(dirname "$0")/.."

VENDOR=vendor
CKPT=955717e8-8726e21a.th          # htdemucs weights

echo "==> Building syscap helper"
swiftc -O native/syscap.swift -o native/syscap

echo "==> Gathering htdemucs weights"
mkdir -p "$VENDOR/torch/hub/checkpoints"
if [ ! -f "$VENDOR/torch/hub/checkpoints/$CKPT" ]; then
  SRC="$HOME/.cache/torch/hub/checkpoints/$CKPT"
  if [ -f "$SRC" ]; then
    cp "$SRC" "$VENDOR/torch/hub/checkpoints/"
  else
    echo "!! $CKPT not in torch cache. Run one separation so demucs downloads it, then re-run." >&2
    exit 1
  fi
fi

echo "==> Checking ffmpeg/ffprobe (need STATIC arm64 builds for a portable app)"
for b in ffmpeg ffprobe; do
  if [ ! -x "$VENDOR/$b" ]; then
    echo "!! $VENDOR/$b missing. Drop a static arm64 $b there (e.g. from a static build" >&2
    echo "   like evermeet/osxexperts). A dynamically-linked system $b won't run in the bundle." >&2
    exit 1
  fi
done

echo "==> PyInstaller"
.venv/bin/pyinstaller --noconfirm SongCoach.spec

echo "==> Done → dist/SongCoach.app"
echo "   First launch: grant Screen & System Audio Recording, then relaunch."
echo "   (Unsigned: right-click → Open the first time.)"
