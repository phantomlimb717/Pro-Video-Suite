"""Custom Qt widgets."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPaintEvent, QPainter
from PySide6.QtMultimedia import QVideoFrame, QVideoSink
from PySide6.QtWidgets import QLayout, QSizePolicy, QToolButton, QVBoxLayout, QWidget


class CollapsibleSection(QWidget):
    """A section with a clickable header that shows/hides its content.

    Used to keep the editor focused on the video: Configuration and the log start
    collapsed so the preview gets the room, and expand on demand.
    """

    def __init__(self, title: str, expanded: bool = False) -> None:
        super().__init__()
        self.toggle = QToolButton()
        # Escape "&" so QToolButton doesn't turn it into a keyboard mnemonic.
        self.toggle.setText(title.replace("&", "&&"))
        self.toggle.setCheckable(True)
        self.toggle.setChecked(expanded)
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.toggle.setStyleSheet(
            "QToolButton { border: none; color: #ccc; font-weight: bold; font-size: 12px; "
            "padding: 4px 2px; text-align: left; } QToolButton:hover { color: #fff; }"
        )
        self.toggle.clicked.connect(lambda: self.set_expanded(self.toggle.isChecked()))

        self.content = QWidget()
        self.content.setVisible(expanded)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self.toggle)
        outer.addWidget(self.content)

    def setContentLayout(self, layout: QLayout) -> None:  # noqa: N802 - Qt-style name
        self.content.setLayout(layout)

    def set_expanded(self, expanded: bool) -> None:
        self.toggle.setChecked(expanded)
        self.toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.content.setVisible(expanded)


class VideoDisplay(QWidget):
    """Preview surface that paints frames from a QVideoSink itself.

    We deliberately avoid QVideoWidget: on macOS it renders through a native layer
    that bleeds over the controls beneath it and ignores normal size constraints
    (see the Qt forum threads on QVideoWidget overpaint). Painting QVideoSink frames
    into a plain widget fixes both. It uses the same decoder as QVideoWidget, so
    codecs the backend can't decode (e.g. AV1 on macOS) still show black — that case
    is flagged separately via the app's PREVIEW_UNSUPPORTED_CODECS.
    """

    def __init__(self) -> None:
        super().__init__()
        self.sink = QVideoSink()
        self._image = QImage()
        # How many valid frames we've actually rendered since the last clear(). Used to
        # detect a codec the backend can't decode (0 frames while the media claims video).
        self.frames_received = 0
        self.sink.videoFrameChanged.connect(self._on_frame)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAutoFillBackground(True)

    def clear(self) -> None:
        """Drop the current frame and reset the counter (e.g. when loading a new file)."""
        self._image = QImage()
        self.frames_received = 0
        self.update()

    def _on_frame(self, frame: QVideoFrame) -> None:
        if frame.isValid():
            self.frames_received += 1
            self._image = frame.toImage()
        else:
            self._image = QImage()
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#000000"))
        if self._image.isNull():
            return
        scaled = self._image.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        painter.drawImage(x, y, scaled)


class RangeBar(QWidget):
    """A thin bar under the timeline that highlights the selected trim range."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(15)
        self.duration: float = 100
        self.start_pos: float = 0
        self.end_pos: float = 100

    def update_range(self, start: float, end: float, duration: float) -> None:
        self.start_pos = start
        self.end_pos = end
        self.duration = duration if duration > 0 else 100
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        width = self.width()
        painter.fillRect(0, 0, width, self.height(), QColor("#333333"))  # Background
        if self.duration <= 0:
            return

        x1 = int((self.start_pos / self.duration) * width)
        x2 = int((self.end_pos / self.duration) * width)
        x1 = max(0, min(x1, width))
        x2 = max(0, min(x2, width))
        w_rect = max(x2 - x1, 2)

        painter.fillRect(x1, 0, w_rect, self.height(), QColor("#0078D7"))  # Selection
