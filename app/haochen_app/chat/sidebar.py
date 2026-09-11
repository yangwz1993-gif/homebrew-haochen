"""会话侧栏（visual-spec §6.2）：220–260px，当前项高亮（color-accent 左条），
+ 新会话 / 重命名 / 删除。会话数据由窗口层提供，本组件只负责展示与发信号。"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .theme import BORDER, FONT, RADIUS_BTN, C, button_solid

_MODEL_LABELS = {
    "deepseek-v4-flash-vision-exp": "DeepSeek V4 Vision · 实验版",
    "deepseek-v4-flash": "DeepSeek V4 Flash",
    "deepseek-v4-pro": "DeepSeek V4 Pro",
}


def model_display_name(model: str) -> str:
    """Return a compact product label while preserving the exact id in the tooltip."""
    model_id = model.rsplit("/", 1)[-1] if model else ""
    if not model_id:
        return "未连接"
    if model_id in _MODEL_LABELS:
        return _MODEL_LABELS[model_id]
    words = [part for part in model_id.replace("_", "-").split("-") if part]
    return " ".join(word.upper() if len(word) <= 3 else word.capitalize() for word in words)


class SessionSidebar(QWidget):
    session_selected = pyqtSignal(str)          # session_path
    new_requested = pyqtSignal()
    rename_requested = pyqtSignal(str, str)     # session_path, new_name
    delete_requested = pyqtSignal(str)          # session_path

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(240)
        self.setStyleSheet(f"""
            SessionSidebar {{
                background: {C['surface']};
                border-right: {BORDER}px solid {C['line_soft']};
            }}
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 12, 10, 10)
        root.setSpacing(8)

        title = QLabel("haochen")
        title.setStyleSheet(f"font-size: {FONT['title']}px; font-weight: bold;")
        root.addWidget(title)

        self.list = QListWidget()
        self.list.setStyleSheet(f"""
            QListWidget {{
                background: transparent; border: none; outline: none;
            }}
            QListWidget::item {{
                height: 40px; padding: 0 10px;
                border-radius: {RADIUS_BTN}px;
                color: {C['ink']};
            }}
            QListWidget::item:hover {{ background: {C['bg']}; }}
            QListWidget::item:selected {{
                background: {C['bg']};
                border-left: 3px solid {C['accent']};
                color: {C['ink']}; font-weight: bold;
            }}
        """)
        self.list.itemClicked.connect(self._on_click)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._on_menu)
        root.addWidget(self.list, 1)

        btn_new = QPushButton("＋ 新会话")
        btn_new.setAccessibleName("新会话")
        self.new_button = btn_new
        btn_new.setStyleSheet(button_solid())
        btn_new.clicked.connect(self.new_requested)
        root.addWidget(btn_new)

        self.status = QLabel("")
        self.status.setWordWrap(False)
        self.status.setStyleSheet(f"color: {C['ink_soft']}; font-size: {FONT['body_sm']}px;")
        root.addWidget(self.status)

        self._path_of: dict[int, str] = {}   # id(item) -> session_path

    # ── 数据 ──────────────────────────────────────────────────

    def set_sessions(self, sessions: list[dict], current_path: str | None) -> None:
        """sessions: [{path, title}]，按新→旧顺序。"""
        self.list.clear()
        self._path_of.clear()
        current_row = -1
        for i, s in enumerate(sessions):
            item = QListWidgetItem(s.get("title") or "新会话")
            item.setToolTip(s.get("title") or "新会话")
            self.list.addItem(item)
            self._path_of[id(item)] = s["path"]
            if s["path"] == current_path:
                current_row = i
        if current_row >= 0:
            self.list.setCurrentRow(current_row)

    def update_title(self, path: str, title: str) -> None:
        for row in range(self.list.count()):
            item = self.list.item(row)
            if self._path_of.get(id(item)) == path:
                item.setText(title)
                item.setToolTip(title)
                return

    def current_path(self) -> str | None:
        item = self.list.currentItem()
        return self._path_of.get(id(item)) if item else None

    def set_status(self, text: str) -> None:
        self.status.setText(text)
        self.status.setToolTip(text)

    def set_model(self, model: str) -> None:
        """窄侧栏只显示可辨认的模型名，完整 provider/id 留在悬停提示。"""
        self.status.setText(f"模型  {model_display_name(model)}")
        self.status.setToolTip(model or "模型尚未连接")
        self.status.setAccessibleName(
            f"当前模型 {model_display_name(model)}" if model else "模型尚未连接"
        )

    # ── 交互 ──────────────────────────────────────────────────

    def _on_click(self, item: QListWidgetItem) -> None:
        path = self._path_of.get(id(item))
        if path:
            self.session_selected.emit(path)

    def _on_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if not item:
            return
        path = self._path_of.get(id(item))
        if not path:
            return
        menu = QMenu(self)
        act_rename = menu.addAction("重命名")
        act_delete = menu.addAction("删除")
        act = menu.exec(self.list.viewport().mapToGlobal(pos))
        if act is act_rename:
            from PyQt6.QtWidgets import QInputDialog
            name, ok = QInputDialog.getText(self, "重命名会话", "新名称：",
                                            text=item.text())
            name = name.strip()
            if ok and name:
                self.rename_requested.emit(path, name)
        elif act is act_delete:
            self.delete_requested.emit(path)
