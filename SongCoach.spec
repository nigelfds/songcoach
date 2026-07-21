# PyInstaller spec for the SongCoach macOS .app  (Phase 1 — unsigned build)
#
#   pyinstaller SongCoach.spec        # or: scripts/build_macapp.sh
#
# Vendored artifacts you must place before building (see docs/packaging.md):
#   vendor/ffmpeg, vendor/ffprobe                         (arm64 LGPL builds)
#   vendor/torch/hub/checkpoints/955717e8-8726e21a.th     (htdemucs weights)
#   native/syscap                                         (swiftc -O native/syscap.swift -o native/syscap)
#
# Freezing torch/demucs is fiddly: expect to add hiddenimports as PyInstaller
# reports missing modules on your machine.
from PyInstaller.utils.hooks import collect_all

datas = [
    ("songcoach/templates", "songcoach/templates"),
    ("songcoach/static", "songcoach/static"),
    # htdemucs weights -> resource_dir()/torch, seeded to Application Support at runtime
    ("vendor/torch", "torch"),
]
binaries = [
    ("native/syscap", "native"),   # -> resource_dir()/native/syscap
    ("vendor/ffmpeg", "."),        # -> resource_dir()/ffmpeg  (on PATH at runtime)
    ("vendor/ffprobe", "."),
]
hiddenimports = [
    # uvicorn picks these up dynamically
    "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
]

# Pull in data + submodules for the pipeline deps (torch itself is covered by
# PyInstaller's built-in hook). Add more here if a build reports missing modules.
for pkg in ("demucs", "torchaudio", "julius", "openunmix", "dora", "lameenc", "einops"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:  # noqa: BLE001
        print(f"[SongCoach.spec] collect_all({pkg}) skipped: {exc}")


a = Analysis(
    ["songcoach/desktop.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="SongCoach",
    console=False,          # windowed app, no terminal
    disable_windowed_traceback=False,
    argv_emulation=True,    # let macOS pass file-open events cleanly
    target_arch="arm64",    # Apple Silicon v1
)
coll = COLLECT(exe, a.binaries, a.datas, name="SongCoach")

app = BUNDLE(
    coll,
    name="SongCoach.app",
    icon=None,   # TODO: add assets/SongCoach.icns
    bundle_identifier="in.nigel.songcoach",
    info_plist={
        "CFBundleName": "SongCoach",
        "CFBundleDisplayName": "SongCoach",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "13.0",
        "LSApplicationCategoryType": "public.app-category.music",
        # Capture uses ScreenCaptureKit; the user grants "Screen & System Audio
        # Recording" in System Settings. Mic string included for good measure.
        "NSMicrophoneUsageDescription": "SongCoach captures the audio playing on your Mac to separate it into practice stems.",
    },
)
