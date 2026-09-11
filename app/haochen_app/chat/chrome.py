"""A single restrained header inside macOS's native window controls."""

from PyQt6.QtCore import QPoint, QPointF, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from .theme import C


def configure_native_chrome(widget: QWidget) -> None:
    """Keep real macOS controls but remove the duplicate native title text."""
    if QApplication.platformName() != "cocoa":
        return
    from ctypes import c_void_p

    import objc
    from AppKit import NSWindowStyleMaskFullSizeContentView, NSWindowTitleHidden

    native_view = objc.objc_object(c_void_p=c_void_p(int(widget.winId())))
    native_window = native_view.window()
    if native_window is not None:
        native_window.setStyleMask_(native_window.styleMask() | NSWindowStyleMaskFullSizeContentView)
        native_window.setTitleVisibility_(NSWindowTitleHidden)
        native_window.setTitlebarAppearsTransparent_(True)


def collapse_icon() -> QIcon:
    pixmap = QPixmap(54, 54)
    pixmap.setDevicePixelRatio(3)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(C["ink_soft"]), 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.drawLine(QPointF(4, 4), QPointF(9, 9))
    painter.drawLine(QPointF(14, 4), QPointF(9, 9))
    painter.drawLine(QPointF(4, 13), QPointF(14, 13))
    painter.end()
    return QIcon(pixmap)


class ConversationHeader(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._title = "会话详情"
        self._drag_offset: QPoint | None = None
        self.setFixedHeight(32)
        self.setStyleSheet(f"ConversationHeader {{ background: {C['bg']}; }}")
        layout = QHBoxLayout(self)
        # The traffic lights remain actual macOS controls, never imitations.
        layout.setContentsMargins(88 if QApplication.platformName() == "cocoa" else 18, 0, 12, 0)
        self.title = QLabel(self._title)
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.title.setStyleSheet(f"background: transparent; color: {C['ink_soft']}; font-size: 13px;")
        layout.addWidget(self.title, 1)
        self.collapse = QPushButton()
        self.collapse.setFixedSize(26, 26)
        self.collapse.setIcon(collapse_icon())
        self.collapse.setIconSize(QSize(18, 18))
        self.collapse.setAccessibleName("收起会话详情")
        self.collapse.setToolTip("收起会话详情（Esc / ⌘W）")
        self.collapse.setCursor(Qt.CursorShape.PointingHandCursor)
        self.collapse.setStyleSheet(f"""
            QPushButton {{ border: none; border-radius: 9px; background: transparent; }}
            QPushButton:hover {{ background: {C['hover']}; }}
            QPushButton:pressed {{ background: {C['line_soft']}; }}
        """)
        layout.addWidget(self.collapse)

    def set_title(self, title: str) -> None:
        self._title = title
        self.title.setToolTip(title)
        self.title.setAccessibleName(f"当前会话：{title}")
        self._elide()

    def _elide(self) -> None:
        self.title.setText(self.title.fontMetrics().elidedText(
            self._title, Qt.TextElideMode.ElideRight, max(1, self.title.width())))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            # QWidget's expanded client area does not consistently initiate
            # a native drag on Cocoa. Keep drag handling on this header only,
            # so selecting messages and editing text never moves the window.
            self._drag_offset = event.globalPosition().toPoint() - self.window().pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)
