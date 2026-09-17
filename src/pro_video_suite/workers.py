"""Background worker threads for downloading (yt-dlp) and converting (ffmpeg).

Each worker is a :class:`QThread` so the long-running subprocess never blocks the
Qt event loop / UI.
"""

from __future__ import annotations

import os
import subprocess

from PySide6.QtCore import QThread, Signal

from .platform_utils import (
    ensure_deno,
    find_tool,
    hidden_process_startupinfo,
    resolve_cookies_browser,
)

# ffmpeg/libav log lines that are noise for our use case.
_FFMPEG_NOISE = (
    "Late SEI is not implemented",
    "If you want to help, upload a sample",
    "[h264 @",
)


def _is_noise(line: str) -> bool:
    return any(fragment in line for fragment in _FFMPEG_NOISE)


class DownloadWorker(QThread):
    """Download a URL with yt-dlp, forcing an H.264 MP4 for preview compatibility."""

    progress = Signal(str)
    finished = Signal(bool, str)

    def __init__(self, url: str, download_dir: str, cookies_browser: str | None = None) -> None:
        super().__init__()
        self.url = url
        self.download_dir = download_dir
        # e.g. "safari"/"chrome" -> pass --cookies-from-browser for age-restricted/private videos
        self.cookies_browser = cookies_browser
        self.process: subprocess.Popen | None = None

    def run(self) -> None:
        try:
            if find_tool("yt-dlp") is None:
                self.finished.emit(
                    False,
                    "yt-dlp is not installed or not on your PATH. See the README for "
                    "install instructions (macOS: brew install yt-dlp).",
                )
                return

            os.makedirs(self.download_dir, exist_ok=True)
            # Absolute output template so files land in the chosen folder (not the cwd)
            # and --get-filename returns a full path we can load afterwards.
            out_template = os.path.join(self.download_dir, "%(title)s.%(ext)s")

            self.progress.emit(f">> Starting Download: {self.url}")
            self.progress.emit(f">> Saving to: {self.download_dir}")
            self.progress.emit(">> Forcing H.264 (Safe Mode) for preview compatibility...")

            deno_path = ensure_deno(self.progress.emit)
            js_runtime_args = ["--js-runtimes", f"deno:{deno_path}"] if deno_path else []

            cookie_args: list[str] = []
            if self.cookies_browser:
                value = resolve_cookies_browser(self.cookies_browser)
                if value:
                    cookie_args = ["--cookies-from-browser", value]
                    self.progress.emit(
                        f">> Using {self.cookies_browser} cookies for authentication."
                    )
                else:
                    self.progress.emit(
                        f">> Warning: couldn't find {self.cookies_browser} cookies on this "
                        "machine; continuing without them."
                    )

            # 1. Resolve the output filename first, then force an .mp4 preview file.
            cmd_name = (
                ["yt-dlp"]
                + js_runtime_args
                + cookie_args
                + ["--get-filename", "-o", out_template, "--restrict-filenames", self.url]
            )
            name_proc = subprocess.run(
                cmd_name,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
            filename = os.path.splitext(name_proc.stdout.strip())[0] + ".mp4"

            # 2. Download. Force H.264 video + AAC audio in an mp4 so the Qt preview
            # can always decode it. A plain `-S vcodec:h264` sort only *prefers* H.264 —
            # YouTube would still serve AV1/Opus, which macOS's bundled FFmpeg can't
            # preview (black video). This format *filter* requires avc1+mp4a, falling
            # back to a progressive H.264 stream, then to anything as a last resort.
            cmd = (
                ["yt-dlp"]
                + js_runtime_args
                + cookie_args
                + [
                    "-f",
                    "bv*[vcodec^=avc1]+ba[acodec^=mp4a]/b[vcodec^=avc1]/b",
                    "--merge-output-format",
                    "mp4",
                    "-o",
                    out_template,
                    "--restrict-filenames",
                    self.url,
                ]
            )

            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                startupinfo=hidden_process_startupinfo(),
            )

            assert self.process.stdout is not None
            last_error = ""
            for line in self.process.stdout:
                line = line.strip()
                if not line:
                    continue
                if "[download]" in line:
                    self.progress.emit(line)
                elif "ERROR" in line or "age-restricted" in line or "Sign in to confirm" in line:
                    self.progress.emit(line)  # surface the real reason live
                if "ERROR" in line:
                    last_error = line

            self.process.wait()

            if self.process.returncode == 0:
                if os.path.exists(filename):
                    self.finished.emit(True, filename)
                else:
                    self.finished.emit(False, "Download finished but file not found.")
            else:
                reason = last_error or "yt-dlp returned an error (see the log above)."
                if "sign in" in reason.lower() or "age" in reason.lower():
                    reason = (
                        "This video is age-restricted. Enable “Use browser cookies” below and "
                        "pick a browser you're signed in to YouTube with, then try again.\n\n"
                        + reason
                    )
                self.finished.emit(False, reason)

        except Exception as exc:  # noqa: BLE001 - report any failure to the UI
            self.finished.emit(False, str(exc))

    def stop(self) -> None:
        if self.process:
            self.process.terminate()


class ConversionWorker(QThread):
    """Run an ffmpeg command, streaming its output and allowing cancellation."""

    finished = Signal(bool, str)
    log_output = Signal(str)

    def __init__(self, command: list[str]) -> None:
        super().__init__()
        self.command = command
        self.process: subprocess.Popen | None = None
        self.is_cancelled = False

    def run(self) -> None:
        try:
            if find_tool("ffmpeg") is None:
                self.finished.emit(
                    False,
                    "ffmpeg is not installed or not on your PATH. See the README for "
                    "install instructions (macOS: brew install ffmpeg).",
                )
                return

            self.log_output.emit(f">> COMMAND:\n{' '.join(self.command)}\n")

            self.process = subprocess.Popen(
                self.command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                startupinfo=hidden_process_startupinfo(),
            )

            assert self.process.stdout is not None
            while True:
                if self.is_cancelled:
                    self.process.kill()
                    break

                line = self.process.stdout.readline()
                if not line and self.process.poll() is not None:
                    break
                if line:
                    line_str = line.strip()
                    if _is_noise(line_str):
                        continue
                    self.log_output.emit(line_str)

            if self.is_cancelled:
                self.finished.emit(False, "Export Cancelled by User.")
            elif self.process.returncode == 0:
                self.finished.emit(True, "Conversion Complete!")
            else:
                self.finished.emit(False, "FFmpeg Error (Check Log)")

        except Exception as exc:  # noqa: BLE001 - report any failure to the UI
            self.finished.emit(False, str(exc))

    def stop(self) -> None:
        self.is_cancelled = True
        if self.process:
            self.process.kill()


class ProxyWorker(QThread):
    """Transcode a small, temporary H.264 preview for a file the player can't decode.

    macOS's bundled FFmpeg (used by the preview) can't decode e.g. AV1, but the
    system ffmpeg can. We make a low-res H.264 proxy purely so the preview works —
    the original file is never modified and is what EXPORT uses. The proxy is written
    to a single fixed path (overwritten each time), so it never accumulates copies.
    """

    finished = Signal(bool, str)  # (success, proxy_path or error message)

    def __init__(self, source: str, proxy_path: str) -> None:
        super().__init__()
        self.source = source
        self.proxy_path = proxy_path
        self.process: subprocess.Popen | None = None
        self._cancelled = False

    def run(self) -> None:
        try:
            if find_tool("ffmpeg") is None:
                self.finished.emit(False, "ffmpeg is not installed or not on your PATH")
                return
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                self.source,
                # Small, fast preview: cap height at 480px (even width), speed over size.
                "-vf",
                "scale=-2:'min(480,ih)'",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "28",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                self.proxy_path,
            ]
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                startupinfo=hidden_process_startupinfo(),
            )
            self.process.wait()

            if self._cancelled:
                self.finished.emit(False, "cancelled")
            elif self.process.returncode == 0 and os.path.exists(self.proxy_path):
                self.finished.emit(True, self.proxy_path)
            else:
                self.finished.emit(False, "preview generation failed")
        except Exception as exc:  # noqa: BLE001 - report any failure to the UI
            self.finished.emit(False, str(exc))

    def stop(self) -> None:
        self._cancelled = True
        if self.process:
            self.process.kill()
