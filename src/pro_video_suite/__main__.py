"""Application entry point: ``python -m pro_video_suite`` or the ``pro-video-suite`` command."""

from __future__ import annotations

import os
import platform
import sys

# Quiet down FFmpeg/libav logging before the multimedia backend loads.
os.environ.setdefault(
    "QT_LOGGING_RULES", "qt.multimedia.ffmpeg*=false;qt.multimedia.ffmpeg.libav*=false"
)
os.environ.setdefault("AV_LOG_LEVEL", "quiet")

from PySide6.QtCore import Qt, qInstallMessageHandler  # noqa: E402
from PySide6.QtGui import QColor, QIcon, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from .app import VideoEditorApp  # noqa: E402
from .platform_utils import asset_path  # noqa: E402

# Extra ffmpeg/libav noise that slips past QT_LOGGING_RULES on some platforms.
_SUPPRESSED_LOG_FRAGMENTS = (
    "Late SEI is not implemented",
    "If you want to help, upload a sample",
    "[h264 @",
)


def _qt_message_handler(mode, context, message: str) -> None:
    if any(fragment in message for fragment in _SUPPRESSED_LOG_FRAGMENTS):
        return
    print(message)


def _dark_palette() -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(53, 53, 53))
    palette.setColor(QPalette.WindowText, Qt.white)
    palette.setColor(QPalette.Base, QColor(25, 25, 25))
    palette.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
    palette.setColor(QPalette.ToolTipBase, Qt.white)
    palette.setColor(QPalette.ToolTipText, Qt.white)
    palette.setColor(QPalette.Text, Qt.white)
    palette.setColor(QPalette.Button, QColor(53, 53, 53))
    palette.setColor(QPalette.ButtonText, Qt.white)
    palette.setColor(QPalette.BrightText, Qt.red)
    palette.setColor(QPalette.Link, QColor(42, 130, 218))
    palette.setColor(QPalette.Highlight, QColor(0, 120, 215))
    palette.setColor(QPalette.HighlightedText, Qt.black)
    return palette


def _app_icon() -> QIcon:
    system = platform.system()
    if system == "Windows":
        icon_file = "videoplayflat_106010.ico"
    elif system == "Darwin":
        icon_file = "videoplayflat_106010.icns"
    else:
        icon_file = "videoplayflat_106010.png"
    return QIcon(asset_path(icon_file))


def main() -> int:
    qInstallMessageHandler(_qt_message_handler)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(_dark_palette())

    # On Windows, give the taskbar its own app identity so it uses our icon.
    if platform.system() == "Windows":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "provideosuite.app"
            )
        except Exception:  # noqa: BLE001 - cosmetic only
            pass

    icon = _app_icon()
    app.setWindowIcon(icon)

    window = VideoEditorApp()
    window.setWindowIcon(icon)
    window.show()
    window.warn_if_tools_missing()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
