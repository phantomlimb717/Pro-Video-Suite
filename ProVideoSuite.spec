# PyInstaller spec — cross-platform, used by the macOS/Linux/Windows CI builds.
# Build with:  pyinstaller --noconfirm ProVideoSuite.spec
#
# A spec file (rather than a long command line) keeps the --add-data path
# separator (";" on Windows, ":" elsewhere) and the macOS .app bundling in one
# place that works identically on every runner.

import sys
from pathlib import Path

ROOT = Path(SPECPATH)
ASSETS = ROOT / "src" / "pro_video_suite" / "assets"
ENTRY = ROOT / "src" / "pro_video_suite" / "__main__.py"

# Bundle every asset file under a top-level "assets" folder (matches asset_path()).
datas = [(str(p), "assets") for p in ASSETS.iterdir() if p.is_file()]

if sys.platform == "win32":
    icon = str(ASSETS / "videoplayflat_106010.ico")
elif sys.platform == "darwin":
    icon = str(ASSETS / "videoplayflat_106010.icns")
else:
    icon = str(ASSETS / "videoplayflat_106010.png")

a = Analysis(
    [str(ENTRY)],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    # macOS: onedir inside a proper .app bundle (onefile + .app is deprecated and
    # slower — it unpacks to a temp dir on every launch).
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="ProVideoSuite",
        debug=False,
        strip=False,
        upx=False,
        console=False,
        icon=icon,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="ProVideoSuite",
    )
    app = BUNDLE(
        coll,
        name="Pro Video Suite.app",
        icon=icon,
        bundle_identifier="com.provideosuite.app",
        info_plist={
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.video",
        },
    )
else:
    # Windows / Linux: a single self-contained executable.
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="ProVideoSuite",
        debug=False,
        strip=False,
        upx=False,
        console=False,
        icon=icon,
    )
