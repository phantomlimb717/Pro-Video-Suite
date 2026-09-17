"""The main application window (:class:`VideoEditorApp`)."""

from __future__ import annotations

import json
import os
import platform
import subprocess

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QTextCursor
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import __app_name__
from .platform_utils import (
    MONOSPACE_FONT,
    default_download_dir,
    find_tool,
    hidden_process_startupinfo,
    install_hint,
    missing_tools,
    user_data_dir,
)
from .widgets import CollapsibleSection, RangeBar, VideoDisplay
from .workers import ConversionWorker, DownloadWorker, ProxyWorker


class VideoEditorApp(QWidget):
    """Two-tab window: a yt-dlp downloader and an ffmpeg-backed trimmer/converter."""

    # Codecs the macOS Qt preview backend can't decode: playback runs and audio
    # plays, but the video frames stay black. Trim/export still work (they use the
    # system ffmpeg, which can decode these).
    PREVIEW_UNSUPPORTED_CODECS = {"av1"}

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{__app_name__} (Downloader + Editor)")
        self.resize(900, 700)
        self.setMinimumSize(680, 560)

        # Data
        self.input_file: str | None = None
        self.fps: float = 30.0
        self.video_codec: str = ""
        self.proxy_file: str | None = None
        self.output_dir: str | None = None  # None = same folder as the source
        self.last_output: str | None = None
        self._load_gen = 0  # bumped each load so stale preview checks are ignored
        self.download_dir: str = str(default_download_dir())  # where downloads are saved
        self.duration_ms: int = 0
        self.start_ms: int = 0
        self.end_ms: int = 0
        self.was_playing_before_scrub = False
        self.loop_enabled = False

        # UI Setup
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # Tab 1: Downloader
        self.tab_download = QWidget()
        self.setup_downloader_tab()
        self.tabs.addTab(self.tab_download, "1. Download")

        # Tab 2: Editor
        self.tab_editor = QWidget()
        self.setup_editor_tab()
        self.tabs.addTab(self.tab_editor, "2. Editor")

        # Audio/Video Backend
        self.setup_player()

    # ==========================
    # TAB 1: DOWNLOADER SETUP
    # ==========================
    def setup_downloader_tab(self) -> None:
        layout = QVBoxLayout()
        layout.setSpacing(15)
        layout.setContentsMargins(40, 40, 40, 40)

        lbl_title = QLabel("YouTube Downloader")
        lbl_title.setStyleSheet("font-size: 18px; font-weight: bold; color: #0078D7;")
        layout.addWidget(lbl_title)

        input_group = QGroupBox("Video URL")
        ig_layout = QVBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Paste YouTube link here...")
        self.url_input.setStyleSheet(
            "padding: 8px; font-size: 14px; background-color: #222; color: white; border: 1px solid #555;"
        )
        ig_layout.addWidget(self.url_input)
        input_group.setLayout(ig_layout)
        layout.addWidget(input_group)

        dl_dir_layout = QHBoxLayout()
        lbl_dl_dir = QLabel("Save to:")
        lbl_dl_dir.setStyleSheet("color: #aaa;")
        self.txt_download_dir = QLineEdit()
        self.txt_download_dir.setReadOnly(True)
        self.txt_download_dir.setText(self.download_dir)
        self.txt_download_dir.setStyleSheet(
            "background-color: #222; color: #ccc; border: 1px solid #444; padding: 6px;"
        )
        self.btn_download_dir = QPushButton("Choose…")
        self.btn_download_dir.clicked.connect(self.choose_download_dir)
        self.btn_download_dir.setStyleSheet(
            "background-color: #444; color: white; border: 1px solid #666; "
            "border-radius: 4px; padding: 6px 12px;"
        )
        dl_dir_layout.addWidget(lbl_dl_dir)
        dl_dir_layout.addWidget(self.txt_download_dir, 1)
        dl_dir_layout.addWidget(self.btn_download_dir)
        layout.addLayout(dl_dir_layout)

        self.btn_download = QPushButton("DOWNLOAD && LOAD")
        self.btn_download.setFixedHeight(50)
        self.btn_download.setStyleSheet(
            "background-color: #2e7d32; color: white; font-weight: bold; font-size: 14px;"
        )
        self.btn_download.clicked.connect(self.start_download)
        layout.addWidget(self.btn_download)

        self.dl_console = QTextEdit()
        self.dl_console.setReadOnly(True)
        self.dl_console.setFixedHeight(180)
        self.dl_console.setStyleSheet(
            f"background-color: #111; color: #0f0; font-family: {MONOSPACE_FONT}; font-size: 11px;"
        )
        layout.addWidget(self.dl_console)

        # Editing a file you already have is a first-class path, not just downloads.
        divider = QLabel("— or —")
        divider.setAlignment(Qt.AlignCenter)
        divider.setStyleSheet("color: #777; font-size: 12px; margin-top: 6px;")
        layout.addWidget(divider)

        self.btn_open_from_download = QPushButton("📂  Open a Video File to Edit…")
        self.btn_open_from_download.setFixedHeight(46)
        self.btn_open_from_download.clicked.connect(self.open_for_edit)
        self.btn_open_from_download.setStyleSheet(
            "QPushButton { background-color: #444; color: white; font-weight: bold; font-size: 14px; "
            "border: 1px solid #666; border-radius: 4px; } "
            "QPushButton:hover { background-color: #555; }"
        )
        layout.addWidget(self.btn_open_from_download)

        layout.addStretch()
        self.tab_download.setLayout(layout)

    # ==========================
    # TAB 2: EDITOR SETUP
    # ==========================
    def setup_editor_tab(self) -> None:
        main_layout = QVBoxLayout()
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # Prominent entry point: open a local file to edit (a first-class path).
        self.btn_browse = QPushButton("📂  Open Video File…")
        self.btn_browse.setFixedHeight(36)
        self.btn_browse.clicked.connect(self.browse_file)
        self.btn_browse.setStyleSheet(
            "QPushButton { background-color: #444; color: white; font-weight: bold; font-size: 13px; "
            "border: 1px solid #666; border-radius: 4px; } "
            "QPushButton:hover { background-color: #555; }"
        )
        main_layout.addWidget(self.btn_browse)

        # Video Preview — a custom painter (VideoDisplay) rather than QVideoWidget,
        # which on macOS bleeds its native layer over the controls below. It's the
        # star of the editor: it takes all the room the collapsed panels free up.
        self.video_display = VideoDisplay()
        self.video_display.setMinimumHeight(180)
        main_layout.addWidget(self.video_display, 1)

        # Timeline
        timeline_group = QGroupBox("Timeline")
        timeline_group.setStyleSheet(
            "QGroupBox { border: 1px solid #333; margin-top: 10px; padding-top: 10px; font-weight: bold; color: #ccc; }"
        )
        t_layout = QVBoxLayout()

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.sliderPressed.connect(self.slider_pressed)
        self.slider.sliderReleased.connect(self.slider_released)
        self.slider.sliderMoved.connect(self.set_position)
        self.slider.setStyleSheet(
            "QSlider::groove:horizontal { height: 4px; background: #333; } "
            "QSlider::handle:horizontal { background: #ddd; width: 12px; margin: -4px 0; border-radius: 6px; }"
        )
        t_layout.addWidget(self.slider)

        self.range_bar = RangeBar()
        t_layout.addWidget(self.range_bar)

        c_layout = QHBoxLayout()
        self.btn_play = QPushButton("Play")
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_play.setFixedWidth(80)
        c_layout.addWidget(self.btn_play)

        self.lbl_time = QLabel("00:00 / 00:00")
        self.lbl_time.setStyleSheet(f"color: #888; font-family: {MONOSPACE_FONT};")
        c_layout.addWidget(self.lbl_time)
        c_layout.addStretch()

        self.btn_in = QPushButton("[ Set IN ]")
        self.btn_in.clicked.connect(self.set_in_point)
        self.btn_in.setStyleSheet(
            "background-color: #2e4d34; color: #8fbc8f; border: none; padding: 6px 12px; border-radius: 3px;"
        )

        self.btn_out = QPushButton("[ Set OUT ]")
        self.btn_out.clicked.connect(self.set_out_point)
        self.btn_out.setStyleSheet(
            "background-color: #4d2e2e; color: #bc8f8f; border: none; padding: 6px 12px; border-radius: 3px;"
        )

        self.btn_reset = QPushButton("Reset")
        self.btn_reset.clicked.connect(self.reset_cut)
        self.btn_reset.setStyleSheet(
            "background-color: #333; color: #aaa; border: none; padding: 6px 12px; border-radius: 3px;"
        )

        c_layout.addWidget(self.btn_in)
        c_layout.addWidget(self.btn_out)
        c_layout.addWidget(self.btn_reset)
        t_layout.addLayout(c_layout)

        self.lbl_trim_info = QLabel("Export Range: Full Video")
        self.lbl_trim_info.setAlignment(Qt.AlignCenter)
        self.lbl_trim_info.setStyleSheet("color: #666; font-size: 11px; margin-top: 5px;")
        t_layout.addWidget(self.lbl_trim_info)

        timeline_group.setLayout(t_layout)
        # Pin the controls so the expanding video widget can never squeeze them out.
        timeline_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        main_layout.addWidget(timeline_group)

        # Settings + Export — collapsed by default so the video preview gets the room.
        self.config_section = CollapsibleSection("Configuration & Export", expanded=False)
        cols_layout = QHBoxLayout()

        col1 = QVBoxLayout()

        name_layout = QHBoxLayout()
        lbl_name = QLabel("Output Name:")
        lbl_name.setStyleSheet("color: #aaa;")
        self.txt_output_name = QLineEdit()
        self.txt_output_name.setPlaceholderText("File name (no extension)")
        self.txt_output_name.setStyleSheet(
            "background-color: #222; color: white; border: 1px solid #444; padding: 4px;"
        )
        name_layout.addWidget(lbl_name)
        name_layout.addWidget(self.txt_output_name)
        col1.addLayout(name_layout)

        dir_layout = QHBoxLayout()
        lbl_dir = QLabel("Output Folder:")
        lbl_dir.setStyleSheet("color: #aaa;")
        self.txt_output_dir = QLineEdit()
        self.txt_output_dir.setReadOnly(True)
        self.txt_output_dir.setPlaceholderText("Same folder as the source video")
        self.txt_output_dir.setStyleSheet(
            "background-color: #222; color: #ccc; border: 1px solid #444; padding: 4px;"
        )
        self.btn_output_dir = QPushButton("Choose…")
        self.btn_output_dir.clicked.connect(self.choose_output_dir)
        self.btn_output_dir.setStyleSheet(
            "background-color: #444; color: white; border: 1px solid #666; "
            "border-radius: 4px; padding: 4px 10px;"
        )
        dir_layout.addWidget(lbl_dir)
        dir_layout.addWidget(self.txt_output_dir, 1)
        dir_layout.addWidget(self.btn_output_dir)
        col1.addLayout(dir_layout)

        self.chk_gop = QCheckBox("Force Smart Keyframes")
        self.chk_gop.setChecked(True)
        col1.addWidget(self.chk_gop)
        cols_layout.addLayout(col1)

        col2 = QFormLayout()
        self.combo_encoder = QComboBox()
        self.populate_encoders()
        col2.addRow("Format:", self.combo_encoder)

        self.combo_speed = QComboBox()
        self.combo_speed.addItems(
            ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]
        )
        self.combo_speed.setCurrentText("medium")
        col2.addRow("Speed:", self.combo_speed)

        self.combo_mode = QComboBox()
        self.combo_mode.addItems(["Auto Quality (CRF)", "Target Size (MB)"])
        self.combo_mode.currentIndexChanged.connect(self.toggle_mode)
        col2.addRow("Mode:", self.combo_mode)

        self.spin_size = QDoubleSpinBox()
        self.spin_size.setRange(1, 5000)
        self.spin_size.setValue(95.0)
        self.spin_size.setEnabled(False)
        self.spin_size.setSuffix(" MB")
        col2.addRow("Size:", self.spin_size)

        self.combo_aspect = QComboBox()
        self.combo_aspect.addItems(["Original", "16:9", "9:16", "4:3", "3:4"])
        col2.addRow("Aspect Ratio:", self.combo_aspect)

        cols_layout.addLayout(col2)

        # EXPORT / CANCEL live inside this section, so they're tucked away until you
        # expand "Configuration & Export" and are ready to render.
        btn_layout = QHBoxLayout()
        self.btn_run = QPushButton("EXPORT")
        self.btn_run.setFixedHeight(40)
        self.btn_run.setStyleSheet(
            "QPushButton { background-color: #0078D7; color: white; font-weight: bold; font-size: 15px; } "
            "QPushButton:hover { background-color: #008ae6; } "
            "QPushButton:disabled { background-color: #333; color: #555; }"
        )
        self.btn_run.setEnabled(False)
        self.btn_run.clicked.connect(self.start_encoding)
        btn_layout.addWidget(self.btn_run)

        self.btn_stop = QPushButton("CANCEL")
        self.btn_stop.setFixedHeight(40)
        self.btn_stop.setFixedWidth(100)
        self.btn_stop.setStyleSheet(
            "QPushButton { background-color: #7d2e2e; color: white; font-weight: bold; font-size: 15px; } "
            "QPushButton:hover { background-color: #a63d3d; }"
        )
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_encoding)
        btn_layout.addWidget(self.btn_stop)

        config_outer = QVBoxLayout()
        config_outer.setContentsMargins(6, 2, 6, 6)
        config_outer.addLayout(cols_layout)
        config_outer.addSpacing(6)
        config_outer.addLayout(btn_layout)
        self.config_section.setContentLayout(config_outer)
        self.config_section.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        main_layout.addWidget(self.config_section)

        # Log — collapsed by default; auto-expands when there's something to show.
        self.log_section = CollapsibleSection("Log", expanded=False)
        self.console = QTextEdit()
        self.console.setFixedHeight(90)
        self.console.setReadOnly(True)
        self.console.setStyleSheet(
            f"background-color: #111; color: #0f0; font-family: {MONOSPACE_FONT}; font-size: 10px; border: 1px solid #333;"
        )
        log_layout = QVBoxLayout()
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.addWidget(self.console)
        self.log_section.setContentLayout(log_layout)
        self.log_section.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        main_layout.addWidget(self.log_section)

        self.tab_editor.setLayout(main_layout)

    # ==========================
    # STARTUP CHECKS
    # ==========================
    def warn_if_tools_missing(self) -> None:
        """Show a non-fatal warning if ffmpeg/ffprobe/yt-dlp are not installed."""
        missing = missing_tools()
        if not missing:
            return
        QMessageBox.warning(
            self,
            "Missing tools",
            "The following required tools are not on your PATH:\n\n    "
            + ", ".join(missing)
            + "\n\nThe app will open, but downloads/exports won't work until they're installed.\n\n"
            + install_hint(),
        )

    # ==========================
    # DOWNLOAD LOGIC
    # ==========================
    def choose_download_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "Choose Download Folder", self.download_dir
        )
        if directory:
            self.download_dir = directory
            self.txt_download_dir.setText(directory)

    def start_download(self) -> None:
        url = self.url_input.text().strip()
        if not url:
            self.dl_console.append(">> Error: Please enter a URL.")
            return

        self.btn_download.setEnabled(False)
        self.dl_console.clear()

        self.dl_worker = DownloadWorker(url, self.download_dir)
        self.dl_worker.progress.connect(self.dl_console.append)
        self.dl_worker.finished.connect(self.on_download_complete)
        self.dl_worker.start()

    def on_download_complete(self, success: bool, result: str) -> None:
        self.btn_download.setEnabled(True)
        if success:
            self.dl_console.append(f">> SUCCESS: Downloaded {result}")
            self.load_video_file(result)
            self.tabs.setCurrentIndex(1)
            QMessageBox.information(
                self, "Download Complete", f"Loaded: {result}\n\nSwitched to Editor tab."
            )
        else:
            self.dl_console.append(f">> FAILED: {result}")
            QMessageBox.critical(self, "Error", result)

    def open_for_edit(self) -> None:
        """Switch to the Editor tab and open a local file to edit."""
        self.tabs.setCurrentIndex(1)
        self.browse_file()

    # ==========================
    # EDITOR LOGIC
    # ==========================
    def setup_player(self) -> None:
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoSink(self.video_display.sink)
        self.player.durationChanged.connect(self.duration_changed)
        self.player.positionChanged.connect(self.position_changed)
        self.player.mediaStatusChanged.connect(self.media_status_changed)

    def browse_file(self) -> None:
        file, _ = QFileDialog.getOpenFileName(
            self, "Open Video", "", "Videos (*.mp4 *.mov *.mkv *.webm *.avi)"
        )
        if file:
            self.load_video_file(file)

    def choose_output_dir(self) -> None:
        start = self.output_dir or (
            os.path.dirname(self.input_file) if self.input_file else ""
        )
        directory = QFileDialog.getExistingDirectory(self, "Choose Output Folder", start)
        if directory:
            self.output_dir = directory
            self._refresh_output_dir_display()

    def _refresh_output_dir_display(self) -> None:
        """Show where the export will land (chosen folder, else the source's folder)."""
        if self.output_dir:
            self.txt_output_dir.setText(self.output_dir)
        elif self.input_file:
            self.txt_output_dir.setText(os.path.dirname(self.input_file))
        else:
            self.txt_output_dir.clear()

    def _reveal_in_file_manager(self, path: str) -> None:
        """Open the OS file manager with the exported file selected."""
        system = platform.system()
        try:
            if system == "Darwin":
                subprocess.Popen(["open", "-R", path])
            elif system == "Windows":
                subprocess.Popen(["explorer", f"/select,{os.path.normpath(path)}"])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(path)])
        except OSError:
            pass

    def load_video_file(self, filepath: str) -> None:
        # input_file always stays the ORIGINAL — export uses it, never a proxy.
        self.input_file = filepath
        self._load_gen += 1
        self._discard_proxy()
        self.video_display.clear()
        self.btn_run.setEnabled(True)

        filename = os.path.basename(filepath)
        self.btn_browse.setText(f"Loaded: {filename}")
        self.txt_output_name.setText(f"{os.path.splitext(filename)[0]}_edit")
        self._refresh_output_dir_display()

        self.log(f">> Loaded: {filename}")
        self.probe_video()

        if self.video_codec in self.PREVIEW_UNSUPPORTED_CODECS:
            # Known-bad codec (e.g. AV1) — go straight to a proxy, no black wait.
            self._start_preview_proxy(filepath)
        else:
            # Try to preview directly; if no frames render, fall back to a proxy. This
            # catches any codec the backend can't decode, not just a hardcoded list.
            self._preview_source(filepath)
            self._schedule_preview_check(filepath)

    def _preview_source(self, path: str) -> None:
        """Point the player at a path and start playing it."""
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()
        self.btn_play.setText("Pause")

    def _schedule_preview_check(self, source: str) -> None:
        """After a grace period, verify the preview actually rendered frames."""
        gen = self._load_gen
        QTimer.singleShot(3000, lambda: self._verify_preview(gen, source))

    def _verify_preview(self, gen: int, source: str) -> None:
        # Ignore if another file was loaded, or we already switched to a proxy.
        if gen != self._load_gen or self.proxy_file is not None:
            return
        playable = (
            QMediaPlayer.MediaStatus.BufferedMedia,
            QMediaPlayer.MediaStatus.BufferingMedia,
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.StalledMedia,
        )
        undecodable = (
            self.video_display.frames_received == 0
            and self.player.hasVideo()
            and self.player.mediaStatus() in playable
        )
        if undecodable:
            self.log(
                ">> This video's format can't be previewed on macOS. Building a temporary "
                "preview (your original file is untouched and is what EXPORT uses)…"
            )
            self.log_section.set_expanded(True)
            self._start_preview_proxy(source)

    def _start_preview_proxy(self, source: str) -> None:
        """Build a temporary H.264 preview for a codec the player can't decode (e.g. AV1)."""
        if find_tool("ffmpeg") is None:
            self.log(
                f">> NOTE: This is {self.video_codec.upper()} video, which macOS can't "
                "preview, and ffmpeg isn't installed to build a preview. Install ffmpeg "
                "to preview it (trim/export also need ffmpeg)."
            )
            self.log_section.set_expanded(True)
            return
        self.log(
            f">> {self.video_codec.upper()} video — macOS can't preview it directly. "
            "Building a temporary preview (your original file is untouched and is what "
            "EXPORT uses)…"
        )
        self.log_section.set_expanded(True)
        proxy_path = str(user_data_dir() / "preview_proxy.mp4")
        self.proxy_worker = ProxyWorker(source, proxy_path)
        self.proxy_worker.finished.connect(self._on_proxy_ready)
        self.proxy_worker.start()

    def _on_proxy_ready(self, success: bool, result: str) -> None:
        if success:
            self.proxy_file = result
            self.log(">> Preview ready.")
            self._preview_source(result)
        else:
            self.log(
                f">> Preview unavailable ({result}), but trimming and EXPORT still work "
                "on the original."
            )

    def _discard_proxy(self) -> None:
        """Stop any running proxy job and delete the single temp proxy (no accumulation)."""
        worker = getattr(self, "proxy_worker", None)
        if worker is not None and worker.isRunning():
            worker.stop()
            worker.wait(2000)
        # Release the player's handle so the file can be removed cleanly.
        self.player.setSource(QUrl())
        if self.proxy_file:
            try:
                os.remove(self.proxy_file)
            except OSError:
                pass
            self.proxy_file = None

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        # Stop every worker thread before teardown, or Qt aborts with
        # "QThread: Destroyed while thread is still running".
        for attr in ("dl_worker", "worker", "proxy_worker"):
            worker = getattr(self, attr, None)
            if worker is not None and worker.isRunning():
                worker.stop()
                worker.wait(5000)
        self._discard_proxy()
        super().closeEvent(event)

    def duration_changed(self, duration: int) -> None:
        self.duration_ms = duration
        self.slider.setRange(0, duration)
        self.end_ms = duration
        self.start_ms = 0
        self.update_range_ui()

    def position_changed(self, position: int) -> None:
        if not self.slider.isSliderDown():
            self.slider.setValue(position)
        self.update_time_label(position)

        # Loop within the trim range while previewing.
        if self.loop_enabled and self.player.playbackState() == QMediaPlayer.PlayingState:
            if position >= self.end_ms:
                self.player.setPosition(self.start_ms)

    def update_time_label(self, current_ms: int) -> None:
        def fmt(ms: int) -> str:
            s = (ms // 1000) % 60
            m = ms // 60000
            return f"{m:02}:{s:02}"

        self.lbl_time.setText(f"{fmt(current_ms)} / {fmt(self.duration_ms)}")

    def slider_pressed(self) -> None:
        self.was_playing_before_scrub = self.player.playbackState() == QMediaPlayer.PlayingState
        self.player.pause()

    def slider_released(self) -> None:
        self.player.setPosition(self.slider.value())
        if self.was_playing_before_scrub:
            self.player.play()

    def set_position(self, p: int) -> None:
        self.update_time_label(p)

    def toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.btn_play.setText("Play")
        else:
            self.player.play()
            self.btn_play.setText("Pause")

    def media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.EndOfMedia and not self.loop_enabled:
            self.btn_play.setText("Play")

    def set_in_point(self) -> None:
        self.start_ms = self.player.position()
        if self.start_ms >= self.end_ms:
            self.end_ms = self.duration_ms
        self.loop_enabled = True
        self.update_range_ui()
        self.log(f">> Cut Start: {self.start_ms / 1000:.2f}s (Loop Active)")

    def set_out_point(self) -> None:
        self.end_ms = self.player.position()
        if self.end_ms <= self.start_ms:
            self.start_ms = 0
        self.loop_enabled = True
        self.update_range_ui()
        self.log(f">> Cut End: {self.end_ms / 1000:.2f}s (Loop Active)")

    def reset_cut(self) -> None:
        self.start_ms = 0
        self.end_ms = self.duration_ms
        self.loop_enabled = False
        self.update_range_ui()
        self.log(">> Range Reset")

    def update_range_ui(self) -> None:
        self.range_bar.update_range(self.start_ms, self.end_ms, self.duration_ms)
        s = self.start_ms / 1000.0
        e = self.end_ms / 1000.0
        self.lbl_trim_info.setText(f"Trim: {s:.2f}s to {e:.2f}s (Duration: {e - s:.2f}s)")

    def populate_encoders(self) -> None:
        system = platform.system()
        self.combo_encoder.clear()
        # Video options. Hardware encoders are Windows-only here; macOS/Linux use libx264.
        if system == "Windows":
            self.combo_encoder.addItem("Video - Best (libx264)", "libx264")
            self.combo_encoder.addItem("Video - NVIDIA (h264_nvenc)", "h264_nvenc")
            self.combo_encoder.addItem("Video - AMD (h264_amf)", "h264_amf")
        else:
            self.combo_encoder.addItem("Video - Standard (libx264)", "libx264")

        # Audio-only options.
        self.combo_encoder.addItem("Audio Only (MP3)", "audio_mp3")
        self.combo_encoder.addItem("Audio Only (M4A)", "audio_m4a")

    def toggle_mode(self) -> None:
        self.spin_size.setEnabled(self.combo_mode.currentIndex() == 1)

    def probe_video(self) -> None:
        """Read frame rate + video codec via ffprobe; warn if the preview can't show it."""
        self.fps = 30.0
        self.video_codec = ""
        if self.input_file is None or find_tool("ffprobe") is None:
            return
        try:
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=r_frame_rate,codec_name",
                "-of",
                "json",
                self.input_file,
            ]
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                startupinfo=hidden_process_startupinfo(),
            )
            stream = (json.loads(result.stdout or "{}").get("streams") or [{}])[0]
            self.video_codec = (stream.get("codec_name") or "").lower()
            rate = stream.get("r_frame_rate", "")
            if "/" in rate:
                num, den = rate.split("/")
                self.fps = float(num) / float(den)
            elif rate:
                self.fps = float(rate)
        except (ValueError, ZeroDivisionError, OSError, KeyError, json.JSONDecodeError):
            self.fps = 30.0

    def start_encoding(self) -> None:
        if not self.input_file:
            return

        custom_name = self.txt_output_name.text().strip() or "output_video"
        out_dir = self.output_dir or os.path.dirname(self.input_file)

        encoder_data = self.combo_encoder.currentData()
        if encoder_data == "audio_mp3":
            ext = ".mp3"
        elif encoder_data == "audio_m4a":
            ext = ".m4a"
        else:
            ext = ".mp4"

        output_file = os.path.join(out_dir, f"{custom_name}{ext}")
        self.last_output = output_file

        if os.path.exists(output_file):
            reply = QMessageBox.question(
                self,
                "Overwrite?",
                f"File '{custom_name}{ext}' exists. Overwrite?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.No:
                return

        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.console.clear()
        self.log_section.set_expanded(True)

        start_sec = self.start_ms / 1000.0
        end_sec = self.end_ms / 1000.0
        duration = end_sec - start_sec
        if duration <= 0:
            duration = self.duration_ms / 1000.0

        cmd = ["ffmpeg", "-y"]
        if start_sec > 0:
            cmd.extend(["-ss", f"{start_sec:.3f}"])
        if end_sec < (self.duration_ms / 1000.0):
            cmd.extend(["-to", f"{end_sec:.3f}"])

        cmd.extend(["-i", self.input_file])

        if "audio" in encoder_data:
            # AUDIO MODE
            cmd.append("-vn")  # No video
            if encoder_data == "audio_mp3":
                cmd.extend(["-c:a", "libmp3lame", "-q:a", "2"])  # High quality VBR
            else:
                cmd.extend(["-c:a", "aac", "-b:a", "192k"])  # High quality AAC
        else:
            # VIDEO MODE
            cmd.extend(["-c:v", encoder_data])
            speed = self.combo_speed.currentText()

            if self.combo_mode.currentIndex() == 1:
                target_mb = self.spin_size.value()
                bitrate = int(((target_mb * 8192) / duration) - 128)
                bitrate = max(bitrate, 100)
                cmd.extend(["-b:v", f"{bitrate}k"])
                if "libx264" in encoder_data:
                    cmd.extend(["-preset", speed])
                self.log(f">> Target: {target_mb}MB -> {bitrate}k bitrate")
            else:
                if "libx264" in encoder_data:
                    cmd.extend(["-crf", "23", "-preset", speed])
                else:
                    cmd.extend(["-b:v", "4000k", "-preset", speed])

            vf = []

            aspect_ratio = self.combo_aspect.currentText()
            if aspect_ratio != "Original":
                w, h = aspect_ratio.split(":")
                # Crop to aspect ratio, truncating to even dimensions (required by H.264).
                crop_filter = (
                    f"crop='trunc(min(iw, ih*({w}/{h}))/2)*2':'trunc(min(ih, iw*({h}/{w}))/2)*2'"
                )
                vf.append(crop_filter)

            vf.extend(["scale='min(1920,iw)':-2", "format=yuv420p"])
            cmd.extend(["-vf", ",".join(vf)])

            if self.chk_gop.isChecked():
                gop = int(round(self.fps))
                cmd.extend(["-g", str(gop), "-bf", "0"])

            cmd.extend(["-c:a", "aac", "-b:a", "128k"])
            cmd.extend(["-movflags", "+faststart", "-use_editlist", "0"])

        cmd.append(output_file)

        self.worker = ConversionWorker(cmd)
        self.worker.log_output.connect(self.log)
        self.worker.finished.connect(self.done)
        self.worker.start()

    def stop_encoding(self) -> None:
        if hasattr(self, "worker") and self.worker.isRunning():
            self.worker.stop()
            self.btn_stop.setEnabled(False)
            self.log(">> STOP REQUESTED...")

    def log(self, msg: str) -> None:
        self.console.append(msg)
        c = self.console.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        self.console.setTextCursor(c)

    def done(self, success: bool, msg: str) -> None:
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)
        if success:
            self._show_success(msg)
        else:
            self.log_section.set_expanded(True)
            QMessageBox.critical(self, "Error/Stopped", msg)

    def _show_success(self, msg: str) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("Success")
        box.setText(msg)
        reveal_btn = None
        if self.last_output and os.path.exists(self.last_output):
            box.setInformativeText(f"Saved to:\n{self.last_output}")
            label = "Show in Finder" if platform.system() == "Darwin" else "Open Folder"
            reveal_btn = box.addButton(label, QMessageBox.ActionRole)
        box.addButton(QMessageBox.Ok)
        box.exec()
        if reveal_btn is not None and box.clickedButton() is reveal_btn:
            self._reveal_in_file_manager(self.last_output)
