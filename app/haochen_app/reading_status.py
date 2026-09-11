"""Truthful shared read-stage indicator. No simulated percentages or early success."""
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QWidget

LABELS = {
    "binding": "正在确认阅读目标",
    "bound": "等待本次阅读授权",
    "capturing": "正在固定画面，请暂勿切换",
    "snapshot": "画面已固定，可以切换；正在获取原图",
    "complete": "✓ 已读取，可以切换",
    "failed": "未读到，请重试",
    "cancelled": "已取消，未完成读取",
}


class ReadingStatus(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("readingStatus")
        self.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 28, 4)
        layout.setSpacing(8)
        self.scan = QProgressBar()
        self.scan.setRange(0, 0)
        self.scan.setTextVisible(False)
        self.scan.setFixedSize(24, 4)
        self.scan.setStyleSheet("QProgressBar {border: none; background: #e2e9e5; border-radius: 2px;}"
                               "QProgressBar::chunk {background: #087f5b; border-radius: 2px;}")
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setObjectName("readingStatusLabel")
        layout.addWidget(self.scan)
        layout.addWidget(self.label, 1)
        self.set_phase("binding")

    def set_phase(self, phase):
        if phase not in LABELS:
            return
        from .a11y import reduce_motion_enabled
        self.phase = phase
        color = "#087f5b" if phase == "complete" else "#b34035" if phase == "failed" else "#64716d"
        self.label.setStyleSheet(f"color: {color}; font-size: 13px; border: none; background: transparent;")
        self.label.setText(LABELS[phase])
        self.setAccessibleName(LABELS[phase])
        self.scan.setVisible(phase in ("binding", "capturing", "snapshot") and not reduce_motion_enabled())


def event_phase(event):
    if event.get("toolName") != "read_screen":
        return None
    if event.get("type") == "tool_execution_start":
        return "binding"
    if event.get("type") == "tool_execution_update":
        return ((event.get("partialResult") or {}).get("details") or {}).get("readPhase")
    if event.get("type") == "tool_execution_end":
        details = (event.get("result") or {}).get("details") or {}
        if event.get("isError") or details.get("readError"):
            return "failed"
        return "complete" if details.get("readSuccess") else "failed"
    return None
