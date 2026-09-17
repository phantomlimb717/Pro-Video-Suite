# Changelog

## 1.0.0 — 2026-09-16

A cleanup and cross-platform refresh focused on **macOS** (and Linux), keeping Windows working.

### Added
- **Preview proxy for undecodable formats (macOS).** macOS can't decode some codecs (e.g.
  AV1) for playback. Opening such a file now builds a small, temporary H.264 preview proxy in
  `~/.pro-video-suite/`. The original file is never modified and is what EXPORT uses; only one
  temp preview ever exists and it's cleaned up automatically. Known-bad codecs (AV1) proxy
  immediately; anything else is detected automatically — if the preview renders zero frames,
  the app falls back to a proxy — so it's not limited to a hardcoded codec list.
- **Prominent "open a local file" workflow.** A big "Open Video File" button atop the Editor,
  plus an "Open a Video File to Edit" button on the Download tab — editing local files is now
  a first-class path, not just downloading.
- **Collapsible panels.** The Editor's "Configuration & Export" and Log sections start
  collapsed to give the video preview the room; the Log auto-expands for exports and errors.
  The EXPORT/CANCEL buttons live inside the Configuration section.
- **Choosable output folder.** An "Output Folder" field + "Choose…" button in the export
  panel (defaults to the source video's folder), and a "Show in Finder" button on the export
  success dialog.
- **Friendly startup check** that warns (without crashing) when `ffmpeg`/`ffprobe`/`yt-dlp`
  are missing, with an OS-specific install hint.
- **macOS `.app` and Linux binary builds** via GitHub Actions, plus a CI test workflow that
  runs the smoke tests on every push/PR.
- `pyproject.toml` (pip-installable, `pro-video-suite` command), a cross-platform PyInstaller
  spec, a `.gitignore`, and a headless smoke test.

### Changed
- **Restructured** the single 783-line `downloader.py` into a proper `src/pro_video_suite/`
  package (`app`, `workers`, `widgets`, `platform_utils`, `__main__`) with type hints.
- **Downloads now truly force H.264/AAC** (a yt-dlp format filter instead of a soft `-S`
  sort), so downloaded videos always preview.
- **Video preview** is painted from a `QVideoSink` (custom `VideoDisplay` widget) instead of
  `QVideoWidget`, which on macOS overpainted the controls below it.
- Cross-platform monospace font stack (was the Windows-only "Consolas").
- More compact editor layout that fits smaller screens.

### Fixed
- Crash (SIGABRT, "QThread: Destroyed while thread is still running") when quitting while a
  download, export, or preview-proxy thread was still running — all worker threads are now
  stopped cleanly on close.
- Broken macOS app icon — the `.icns` was actually HTML with an icon extension; regenerated a
  real multi-resolution icon.
- `QVideoWidget` bleeding its native layer over the timeline controls on macOS.
- The editor timeline being squeezed out of view when a video loaded.
- The "DOWNLOAD & LOAD" button rendering as "DOWNLOAD _LOAD" (unescaped `&` mnemonic).
- Replaced a bare `except:` in frame-rate detection with specific exceptions.

### Removed
- Junk files: a duplicate screenshot, an empty `wget-log`, test scratch files
  (`dummy.mp4`, `dummy_vid.sh`), and the ad-hoc `verify_ui.py` (replaced by a real test).
