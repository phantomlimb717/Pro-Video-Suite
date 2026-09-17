"""Smoke test: the main window must construct and lay out without crashing.

Runs headless via Qt's ``offscreen`` platform plugin so it works in CI with no
display server. It does not exercise yt-dlp/ffmpeg.
"""

import os

# Must be set before any Qt import so QApplication can start without a display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_window_constructs(qapp):
    from pro_video_suite.app import VideoEditorApp

    window = VideoEditorApp()
    window.show()
    qapp.processEvents()

    # Both tabs should be present.
    assert window.tabs.count() == 2
    # Export starts disabled until a file is loaded.
    assert window.btn_run.isEnabled() is False

    window.close()


def test_encoder_options_present(qapp):
    from pro_video_suite.app import VideoEditorApp

    window = VideoEditorApp()
    values = [window.combo_encoder.itemData(i) for i in range(window.combo_encoder.count())]

    # libx264 and the audio-only options exist on every platform.
    assert "libx264" in values
    assert "audio_mp3" in values
    assert "audio_m4a" in values

    window.close()
