"""Cross-platform helpers: asset lookup, external-tool discovery, and the Deno runtime.

Everything OS-specific lives here so the UI and worker code stay platform-agnostic.
Primary targets are macOS and Linux; Windows is still supported.
"""

from __future__ import annotations

import os
import platform
import shutil
import stat
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

# A monospace font stack that resolves on every OS. Qt style sheets accept a
# comma-separated fallback list, so we name the platform-native face first
# (Menlo on macOS, Consolas on Windows) and fall back to the generic family.
MONOSPACE_FONT = "Menlo, Monaco, Consolas, 'Courier New', monospace"

# External command-line tools the app relies on.
REQUIRED_TOOLS = ("ffmpeg", "ffprobe", "yt-dlp")

IS_WINDOWS = os.name == "nt"


def asset_path(name: str) -> str:
    """Absolute path to a bundled asset (icons, etc.).

    Handles both a normal checkout and a PyInstaller ``--onefile`` bundle, where
    data files are unpacked into ``sys._MEIPASS``.
    """
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS")) / "assets"
    else:
        base = Path(__file__).resolve().parent / "assets"
    return str(base / name)


def user_data_dir() -> Path:
    """Per-user directory for downloaded tools and caches (e.g. the Deno binary)."""
    path = Path.home() / ".pro-video-suite"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_download_dir() -> Path:
    """Sensible default folder for downloaded videos (the user's Downloads, else home)."""
    downloads = Path.home() / "Downloads"
    return downloads if downloads.is_dir() else Path.home()


def hidden_process_startupinfo() -> Optional["subprocess.STARTUPINFO"]:
    """Return startup info that hides the console window on Windows, else ``None``.

    On macOS/Linux there is no console window to hide, so this is a no-op.
    """
    if not IS_WINDOWS:
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESTDHANDLES | subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return startupinfo


def ensure_tool_path() -> None:
    """Add common CLI tool locations to ``PATH`` (call once at startup).

    A GUI app launched from Finder/Dock inherits a minimal ``PATH`` that omits
    Homebrew (``/opt/homebrew/bin``, ``/usr/local/bin``), so ffmpeg/yt-dlp would look
    "missing" even when installed. Terminal launches are unaffected. No-op on Windows.
    """
    if IS_WINDOWS:
        return
    extra = [
        "/opt/homebrew/bin",  # Apple Silicon Homebrew
        "/usr/local/bin",  # Intel Homebrew / common installs
        "/usr/bin",
        "/bin",
        str(Path.home() / ".local" / "bin"),  # pipx / user installs
    ]
    current = os.environ.get("PATH", "").split(os.pathsep)
    additions = [d for d in extra if d and d not in current]
    if additions:
        os.environ["PATH"] = os.pathsep.join(current + additions)


def find_tool(name: str) -> Optional[str]:
    """Full path to an executable on ``PATH``, or ``None`` if it isn't installed."""
    return shutil.which(name)


def missing_tools(names: tuple[str, ...] = REQUIRED_TOOLS) -> list[str]:
    """Names of required tools that are not on ``PATH``."""
    return [name for name in names if find_tool(name) is None]


def install_hint() -> str:
    """A short, OS-appropriate hint for installing the missing tools."""
    system = platform.system()
    if system == "Darwin":
        return "Install them with Homebrew:\n    brew install ffmpeg yt-dlp"
    if system == "Windows":
        return "Install them with winget:\n    winget install ffmpeg yt-dlp"
    return (
        "Install them with your package manager, e.g.:\n"
        "    sudo apt install ffmpeg && pipx install yt-dlp"
    )


def _deno_download() -> tuple[str, str]:
    """Return ``(download_url, binary_name)`` for the current OS/architecture."""
    system = platform.system()
    machine = platform.machine().lower()
    is_arm = machine in ("arm64", "aarch64")
    base = "https://github.com/denoland/deno/releases/latest/download"

    if system == "Windows":
        return f"{base}/deno-x86_64-pc-windows-msvc.zip", "deno.exe"
    if system == "Darwin":
        arch = "aarch64" if is_arm else "x86_64"
        return f"{base}/deno-{arch}-apple-darwin.zip", "deno"
    arch = "aarch64" if is_arm else "x86_64"
    return f"{base}/deno-{arch}-unknown-linux-gnu.zip", "deno"


def ensure_deno(progress: Optional[Callable[[str], None]] = None) -> Optional[str]:
    """Return a path to a Deno binary, downloading it on first use.

    yt-dlp uses Deno as a JavaScript runtime for some extractors. The binary is
    cached under :func:`user_data_dir` so it is downloaded at most once, and we
    never write into the (possibly read-only) install directory.

    ``progress`` receives human-readable status lines. Returns ``None`` if the
    download fails, in which case the caller should continue without Deno.
    """
    def report(message: str) -> None:
        if progress is not None:
            progress(message)

    url, deno_bin = _deno_download()
    bin_dir = user_data_dir() / "bin"
    deno_path = bin_dir / deno_bin

    if deno_path.exists():
        return str(deno_path)

    report(">> Downloading JavaScript runtime (Deno)...")
    bin_dir.mkdir(parents=True, exist_ok=True)
    zip_path = bin_dir / "deno.zip"

    try:
        urllib.request.urlretrieve(url, zip_path)
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(bin_dir)
        if not IS_WINDOWS:
            current_mode = deno_path.stat().st_mode
            deno_path.chmod(current_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    except Exception as exc:  # noqa: BLE001 - surface any failure to the UI, keep going
        report(f">> Warning: Failed to download Deno ({exc}). Continuing without it.")
        deno_path.unlink(missing_ok=True)
        return None
    finally:
        zip_path.unlink(missing_ok=True)

    report(">> Deno downloaded successfully.")
    return str(deno_path)
