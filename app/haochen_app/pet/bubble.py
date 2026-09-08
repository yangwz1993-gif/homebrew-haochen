"""L1 唤起气泡（visual-spec §6.1 + interaction-spec §2/§3/§5）。

- 宽 ≤420px、最大高度 ~70% 屏；米白底 + 2px 深描边 + 16px 圆角 + 右下小尾巴（指向桌宠）。
- 动态对话流：用户消息 / 感知提示 / 思考状态 / L1 短结 / 错误块 逐条弹出（淡入 180ms）。
- 输入框：回车发送、⌘回车换行；生成中出「停」按钮（Esc 亦可打断）。
- 读屏确认条「读吧 / 不读」（extension_ui_request → respond_ui）。
- 唤起：淡入+上滑 10px（200ms）；收起：淡出（150ms）。失焦不自动收起。
"""

from __future__ import annotations

from PyQt6.QtCore import (
    QEasingCurve,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PyQt6.QtWidgets import (
    QApplication,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..chat.widgets import QueueIndicator
from . import theme as T

BUBBLE_WIDTH = 400          # ≤420（visual-spec §6.1）
_MAX_H_RATIO = 0.70         # 最大高度 ~70% 屏
_SLIDE_PX = 10              # 唤起上滑距离


# ── 输入框 ────────────────────────────────────────────────────

class ChatInput(QTextEdit):
    """↩ 发送；⌘↩ / Ctrl↩ / Shift↩ 换行；Esc 交给外层（打断/收起）。"""

    submit_pressed = pyqtSignal()
    escape_pressed = pyqtSignal()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            mods = e.modifiers()
            if mods & (Qt.KeyboardModifier.ControlModifier
                       | Qt.KeyboardModifier.MetaModifier
                       | Qt.KeyboardModifier.ShiftModifier):
                self.insertPlainText("\n")
            else:
                self.submit_pressed.emit()
            e.accept()
            return
        if e.key() == Qt.Key.Key_Escape:
            self.escape_pressed.emit()
            e.accept()
            return
        super().keyPressEvent(e)


# ── 对话流气泡块 ──────────────────────────────────────────────

class _WrapLabel(QLabel):
    """折行文本标签：修正 Qt 对无空格 CJK 长句的尺寸缺陷。

    QLabel+wordWrap 的 minimumSizeHint 会把「整句一个词」的宽度当最小宽度，
    且最小高度按极窄宽度折行（爆炸值），把滚动内容最小高度撑破 viewport
    （0.2.0 用户实测：横向裁字 + 气泡异常高 + 大片空白）。
    这里把最小尺寸收敛为「一行高、宽度交给布局」，实际折行高度仍由
    heightForWidth 在布局时按真实宽度给出。
    """

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

    def minimumSizeHint(self):
        return QSize(1, self.fontMetrics().lineSpacing())


def _text_label(text: str, css: str, size: float = T.FONT_BODY) -> QLabel:
    lb = _WrapLabel(text)
    lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lb.setStyleSheet(f"font-size: {size}px; {css}")
    return lb


class UserBlock(QWidget):
    """用户消息：居右、accent 极浅 tint 底、无描边（v0.1.6 去"大框套小框"）。"""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")  # 中间容器不露面（防系统灰底）
        lay = QHBoxLayout(self)
        lay.setContentsMargins(48, 2, 4, 2)
        lay.addStretch(1)
        lb = _text_label(text, f"color: {T.COLOR_INK};")
        lb.setStyleSheet(
            f"QLabel {{ background: {T.COLOR_ACCENT_TINT}; border: none;"
            f" border-radius: {T.RADIUS_BUTTON}px; padding: 8px 10px;"
            f" color: {T.COLOR_INK}; font-size: {T.FONT_BODY}px; }}")
        lay.addWidget(lb)


class HintBlock(QWidget):
    """感知提示「我正看一下你的屏幕…」：color-info 弱化（interaction-spec §4.1）。
    v0.1.7：单行不折行（wordWrap=False，400px 气泡内完整显示）。"""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 48, 2)
        lb = QLabel("👀 " + text)
        lb.setWordWrap(False)  # 单行：折行会显得逼仄，文案宽度远小于气泡可用宽
        lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lb.setStyleSheet(f"QLabel {{ color: {T.COLOR_INFO}; font-size: {T.FONT_BODY_SM}px;"
                         f" padding: 2px 4px; }}")
        self.label = lb
        lay.addWidget(lb)
        lay.addStretch(1)


class StatusBlock(QWidget):
    """思考/收敛状态条：ink-soft 弱化文案，可原地更新。"""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 48, 2)
        self._lb = _text_label(text, "", T.FONT_BODY_SM)
        self._lb.setStyleSheet(f"QLabel {{ color: {T.COLOR_INK_SOFT};"
                               f" font-size: {T.FONT_BODY_SM}px; padding: 2px 4px; }}")
        lay.addWidget(self._lb)
        lay.addStretch(1)

    def set_text(self, text: str) -> None:
        self._lb.setText(text)


class SummaryBlock(QWidget):
    """L1 结果卡：简答 +「继续问 / 查看详情」两个明确动作。"""

    expand_clicked = pyqtSignal()
    continue_clicked = pyqtSignal()

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        card = QWidget()
        card.setStyleSheet(
            f"QWidget {{ background: {T.COLOR_SURFACE}; border: none;"
            f" border-radius: {T.RADIUS_BUBBLE}px; }}")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(12, 10, 12, 10)
        lb = _text_label(text, f"color: {T.COLOR_INK};")
        cl.addWidget(lb)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        continue_btn = QPushButton("继续问")
        continue_btn.setAccessibleName("继续提问")
        continue_btn.setToolTip("收起当前结果并继续提问")
        continue_btn.setStyleSheet(
            f"QPushButton {{ background: {T.COLOR_SURFACE}; color: {T.COLOR_INK_SOFT};"
            f" border: 1px solid {T.COLOR_LINE_SOFT}; border-radius: {T.RADIUS_BUTTON}px;"
            f" font-size: {T.FONT_BODY_SM}px; padding: 5px 10px; }}"
            f"QPushButton:hover {{ background: {T.COLOR_BG}; color: {T.COLOR_INK}; }}")
        continue_btn.clicked.connect(self.continue_clicked.emit)
        self.continue_button = continue_btn
        row.addWidget(continue_btn)
        detail_btn = QPushButton("查看详情")
        detail_btn.setAccessibleName("查看当前会话详情")
        detail_btn.setToolTip("打开完整对话窗口（Esc / ⌘W 收起）")
        detail_btn.setStyleSheet(
            f"QPushButton {{ background: {T.COLOR_ACCENT}; color: #fff; border: none;"
            f" border-radius: {T.RADIUS_BUTTON}px; font-weight: bold;"
            f" font-size: {T.FONT_BODY_SM}px; padding: 5px 12px; }}"
            f"QPushButton:hover {{ background: {T.COLOR_ACCENT_DEEP}; }}")
        detail_btn.clicked.connect(self.expand_clicked.emit)
        self.expand_button = detail_btn
        row.addWidget(detail_btn)
        cl.addLayout(row)
        lay.addWidget(card)


class GreetBlock(QWidget):
    """系统发言卡（v0.1.7 首启问称呼/称呼确认）：米白卡面无描边，同 SummaryBlock 一族但无按钮。"""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        card = QWidget()
        card.setStyleSheet(
            f"QWidget {{ background: {T.COLOR_SURFACE}; border: none;"
            f" border-radius: {T.RADIUS_BUBBLE}px; }}")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(12, 10, 12, 10)
        lb = _text_label(text, f"color: {T.COLOR_INK};")
        cl.addWidget(lb)
        lay.addWidget(card)


class ErrorBlock(QWidget):
    """错误块：color-danger 文案 + 重试入口（interaction-spec §6.4 / rpc-contract §6）。"""

    retry_clicked = pyqtSignal()

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lb = _text_label("⚠ " + text, "", T.FONT_BODY_SM)
        lb.setStyleSheet(f"QLabel {{ color: {T.COLOR_DANGER}; font-size: {T.FONT_BODY_SM}px;"
                         f" padding: 2px 4px; }}")
        lay.addWidget(lb, 1)
        btn = QPushButton("重试")
        btn.setObjectName("outlineBtn")
        btn.clicked.connect(self.retry_clicked.emit)
        lay.addWidget(btn)


class ConfirmBar(QWidget):
    """读屏确认条「读吧 / 不读」：color-warn 语境，主按钮实心、次按钮描边（§6.4）。"""

    resolved = pyqtSignal(bool)

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            f"ConfirmBar {{ background: {T.COLOR_SURFACE}; border: 1px solid {T.COLOR_WARN};"
            f" border-radius: {T.RADIUS_BUBBLE}px; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(8)
        lb = _text_label(text, "", T.FONT_BODY_SM)
        lb.setStyleSheet(f"QLabel {{ color: {T.COLOR_WARN}; border: none;"
                         f" font-size: {T.FONT_BODY_SM}px; }}")
        lay.addWidget(lb, 1)
        yes = QPushButton("读吧")
        yes.setObjectName("primaryBtn")
        # v0.1.6：允许按钮内联醒目绿（白字加粗、hover 加深），避免被容器样式冲淡看不清
        yes.setStyleSheet(
            f"QPushButton {{ background: {T.COLOR_ACCENT}; color: #fff; border: none;"
            f" border-radius: {T.RADIUS_BUTTON}px; padding: 4px 14px;"
            f" font-weight: bold; font-size: {T.FONT_BODY_SM}px; }}"
            f"QPushButton:hover {{ background: {T.COLOR_ACCENT_DEEP}; }}")
        # v0.1.7：确认条弹出即聚焦「读吧」，Enter/Return 直接确认
        yes.setDefault(True)
        yes.setAutoDefault(True)
        yes.clicked.connect(lambda: self.resolved.emit(True))
        self.btn_yes = yes
        lay.addWidget(yes)
        no = QPushButton("不读")
        no.setObjectName("outlineBtn")
        # 拒绝按钮保持中性次要：灰字无底无框
        no.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {T.COLOR_INK_SOFT}; border: none;"
            f" border-radius: {T.RADIUS_BUTTON}px; padding: 4px 14px;"
            f" font-size: {T.FONT_BODY_SM}px; }}"
            f"QPushButton:hover {{ background: {T.COLOR_BG}; }}")
        no.clicked.connect(lambda: self.resolved.emit(False))
        lay.addWidget(no)

    def resolve(self, ok: bool) -> None:
        """程序化确认/拒绝（v0.1.7：输入框回车 = 确认「读吧」）。"""
        self.resolved.emit(ok)


# ── 气泡本体 ──────────────────────────────────────────────────

class BubbleWindow(QWidget):
    """L1 气泡：标题行 + 动态对话流 + 输入行 + 右下尾巴。"""

    submitted = pyqtSignal(str)         # 用户回车/点发送
    abort_requested = pyqtSignal()      # 停 / Esc 打断
    escape_requested = pyqtSignal()     # Esc（外层决定打断还是收起）
    dismissed = pyqtSignal()            # 气泡收起（✕ / 点外 / Esc）
    expand_detail = pyqtSignal()        # 「查看详情」→ 对话窗口从气泡展开
    continue_requested = pyqtSignal()   # 结果卡「继续问」→ 回到轻量输入
    retry_requested = pyqtSignal()      # 错误块重试
    confirm_resolved = pyqtSignal(bool) # 读屏确认条结果
    moved = pyqtSignal(int, int)        # v0.1.6：气泡拖动中（外层联动桌宠跟随）
    drag_finished = pyqtSignal()        # v0.1.6：拖动结束（外层持久化位置）
    resized = pyqtSignal()              # v0.1.6：高度自适应变化（外层重新锚定防压住桌宠）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        self.setFixedWidth(BUBBLE_WIDTH)

        screen = QApplication.primaryScreen().availableGeometry()
        self._max_h = int(screen.height() * _MAX_H_RATIO)

        self.setStyleSheet(f"""
            QWidget {{ font-family: {T.FONT_FAMILY}; }}
            QLabel#title {{ font-weight: bold; color: {T.COLOR_INK}; font-size: {T.FONT_TITLE}px; }}
            QPushButton {{ border: 1px solid {T.COLOR_LINE_SOFT}; border-radius: {T.RADIUS_BUTTON}px;
                          padding: 4px 10px; background: {T.COLOR_SURFACE}; color: {T.COLOR_INK};
                          font-size: {T.FONT_BODY_SM}px; }}
            QPushButton:hover {{ background: {T.COLOR_BG}; }}
            QPushButton#primaryBtn {{ background: {T.COLOR_ACCENT}; color: #fff; border: none; }}
            QPushButton#primaryBtn:hover {{ background: #2f6a4d; }}
            QPushButton#sendBtn {{ background: {T.COLOR_ACCENT}; color: #fff; border: none;
                                   font-weight: bold; }}
            QPushButton#closeBtn {{ border: none; color: {T.COLOR_INK_SOFT}; font-weight: bold;
                                    padding: 2px 8px; }}
            QPushButton#closeBtn:hover {{ color: {T.COLOR_DANGER}; }}
            QTextEdit#input {{ border: none; border-radius: {T.RADIUS_INPUT}px;
                               padding: 6px; background: {T.COLOR_SURFACE}; color: {T.COLOR_INK};
                               font-size: {T.FONT_BODY}px; }}
            QTextEdit#input:focus {{ border: 1px solid {T.COLOR_ACCENT}; }}
            QScrollArea {{ border: none; background: transparent; }}
            QScrollBar:vertical {{ width: 8px; background: transparent; }}
            QScrollBar::handle:vertical {{ background: {T.COLOR_LINE_SOFT}; border-radius: 4px;
                                           min-height: 24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 0)  # 底部留给自绘尾巴
        root.setSpacing(6)

        # 标题行：小图标 + 名 + ✕
        head = QHBoxLayout()
        title = QLabel("👓 haochen")
        title.setObjectName("title")
        head.addWidget(title)
        head.addStretch(1)
        btn_close = QPushButton("✕")
        btn_close.setObjectName("closeBtn")
        btn_close.setToolTip("收起（Esc）")
        btn_close.clicked.connect(self.dismiss)
        head.addWidget(btn_close)
        root.addLayout(head)

        # 动态对话流（滚动区）
        # v0.1.4 hotfix 问题2：viewport / flow_host 默认会填系统灰底，必须显式透明，
        # 让 paintEvent 自绘的米白圆角底透出来
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setStyleSheet("background: transparent;")
        self.scroll.viewport().setAutoFillBackground(False)
        self.flow_host = QWidget()
        self.flow_host.setStyleSheet("background: transparent;")
        self.flow_host.setAutoFillBackground(False)
        self.flow = QVBoxLayout(self.flow_host)
        self.flow.setContentsMargins(0, 0, 0, 0)
        self.flow.setSpacing(4)
        self.flow.addStretch(1)
        self.scroll.setWidget(self.flow_host)
        root.addWidget(self.scroll, 1)

        # 读屏确认条（同一时刻最多一条）
        self._confirm_bar: ConfirmBar | None = None

        # 输入区（交互规范 §5：发送后收起，仅消息气泡流；需再输入时才出现）
        self._input_panel = QWidget()
        self._input_panel.setObjectName("inputPanel")
        ip = QVBoxLayout(self._input_panel)
        ip.setContentsMargins(0, 0, 0, 0)
        ip.setSpacing(6)

        self.input = ChatInput()
        self.input.setObjectName("input")
        self.input.setPlaceholderText("问点什么…（↩ 发送，⌘↩ 换行）")
        self.input.setFixedHeight(64)
        self.input.submit_pressed.connect(self._on_send)
        self.input.escape_pressed.connect(self.escape_requested.emit)
        ip.addWidget(self.input)

        row = QHBoxLayout()
        self.btn_stop = QPushButton("■ 停")
        self.btn_stop.setObjectName("outlineBtn")
        self.btn_stop.setAccessibleName("停止生成")
        self.btn_stop.setToolTip("打断当前生成（Esc）")
        self.btn_stop.setStyleSheet(f"""
            QPushButton {{
                border: 1px solid {T.COLOR_DANGER};
                border-radius: {T.RADIUS_BUTTON}px;
                padding: 4px 10px;
                background: {T.COLOR_SURFACE};
                color: {T.COLOR_DANGER};
                font-size: {T.FONT_BODY_SM}px;
            }}
            QPushButton:hover {{
                background: {T.COLOR_BG};
            }}
        """)
        self.btn_stop.clicked.connect(self.abort_requested.emit)
        self.btn_stop.hide()
        row.addWidget(self.btn_stop)
        row.addStretch(1)
        self.btn_send = QPushButton("发送 ➤")
        self.btn_send.setObjectName("sendBtn")
        self.btn_send.setAccessibleName("发送")
        self.btn_send.setStyleSheet(f"""
            QPushButton {{
                background: {T.COLOR_ACCENT};
                color: #ffffff;
                border: none;
                border-radius: {T.RADIUS_BUTTON}px;
                padding: 4px 12px;
                font-weight: bold;
                font-size: {T.FONT_BODY_SM}px;
            }}
            QPushButton:hover {{
                background: {T.COLOR_ACCENT_DEEP};
            }}
            QPushButton:disabled {{
                background: {T.COLOR_LINE_SOFT};
                color: {T.COLOR_SURFACE};
            }}
        """)
        self.btn_send.clicked.connect(self._on_send)
        row.addWidget(self.btn_send)
        ip.addLayout(row)
        root.addWidget(self._input_panel)
        root.addSpacing(T.TAIL_SIZE)  # 尾巴占位（paintEvent 自绘）

        # 对话流（消息区）在输入区上方自适应高度
        self._anims: list[QPropertyAnimation] = []
        self._shown = False
        self._min_h = 170
        self.setMinimumHeight(self._min_h)
        self._drag_pos: QPoint | None = None  # 拖动中（标题/空白区按下左键）

    # ── 输入区收起/弹出（§5 动态对话流）＋ 窗口自适应高度 ────────

    def _input_visible(self) -> bool:
        return self._input_panel.isVisible()

    def set_input_visible(self, on: bool) -> None:
        """发送后收起输入区；需再输入/可追问时弹出。"""
        if on == self._input_panel.isVisible():
            return
        self._input_panel.setVisible(on)
        if on:
            self.input.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._refresh_height()

    def _refresh_height(self) -> None:
        """自适应高度：内容 + 输入区（若可见）+ 标题 + 尾巴，封顶 _max_h。"""
        flow_h = self.flow.sizeHint().height()
        panel_h = self._input_panel.sizeHint().height() if self._input_panel.isVisible() else 0
        want = 34 + flow_h + panel_h + T.TAIL_SIZE + 8
        want = max(self._min_h, min(want, self._max_h))
        self.setFixedHeight(want)

    # ── 对话流 ────────────────────────────────────────────────

    def _append(self, w: QWidget) -> QWidget:
        """逐条弹出：插入流尾 + 淡入 180ms + 滚到底。"""
        self.flow.insertWidget(self.flow.count() - 1, w)
        w.show()
        eff = QGraphicsOpacityEffect(w)
        w.setGraphicsEffect(eff)
        a = QPropertyAnimation(eff, b"opacity", self)
        a.setDuration(T.ANIM_BLOCK_MS)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.setEasingCurve(QEasingCurve.Type.OutCubic)
        a.finished.connect(lambda: w.setGraphicsEffect(None))
        a.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._anims.append(a)
        QTimer.singleShot(0, self._scroll_bottom)
        QTimer.singleShot(T.ANIM_BLOCK_MS, self._scroll_bottom)
        self._refresh_height()
        return w

    def _scroll_bottom(self):
        sb = self.scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def add_user_message(self, text: str) -> None:
        self._append(UserBlock(text))

    def add_queue_indicator(self, preview: str) -> QueueIndicator:
        indicator = QueueIndicator(preview)
        self._append(indicator)
        return indicator

    def remove_widget(self, w: QWidget) -> None:
        self.flow.removeWidget(w)
        w.deleteLater()
        self._refresh_height()

    def add_perception_hint(self, text: str) -> None:
        self._append(HintBlock(text))

    def add_status(self, text: str) -> StatusBlock:
        return self._append(StatusBlock(text))

    def add_summary(self, text: str) -> None:
        block = SummaryBlock(text)
        block.expand_clicked.connect(self.expand_detail.emit)
        block.continue_clicked.connect(self.continue_requested.emit)
        self._append(block)

    def present_summary(self, text: str) -> None:
        """结果阶段只显示当前简答，不保留历史流、输入框或滚动条。"""
        self.clear_flow()
        self.set_input_visible(False)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.add_summary(text)

    def start_input(self) -> None:
        """进入 LISTENING：清掉旧临时层，只显示输入区。"""
        self.clear_flow()
        self.set_input_visible(True)
        self.focus_input()

    def add_greeting(self, text: str) -> None:
        """系统发言卡（v0.1.7：首启问称呼 / 称呼确认回复）。"""
        self._append(GreetBlock(text))

    def add_error(self, text: str) -> None:
        block = ErrorBlock(text)
        block.retry_clicked.connect(self.retry_requested.emit)
        self._append(block)

    def clear_flow(self) -> None:
        """新会话时清空对话流。"""
        while self.flow.count() > 1:
            item = self.flow.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._refresh_height()

    # ── 读屏确认条 ────────────────────────────────────────────

    def show_confirm(self, text: str) -> None:
        self.hide_confirm(emit_result=None)
        self._confirm_bar = ConfirmBar(text)
        self._confirm_bar.resolved.connect(self._on_confirm_resolved)
        self._append(self._confirm_bar)
        # v0.1.7：焦点给「读吧」，Enter/Return 直接确认（输入框回车路径见 _on_send）
        self._confirm_bar.btn_yes.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _on_confirm_resolved(self, ok: bool):
        self.hide_confirm(emit_result=None)
        self.confirm_resolved.emit(ok)

    def hide_confirm(self, emit_result=None) -> None:
        """收起确认条；emit_result=True/False 时才补发结果信号，None 静默。"""
        bar, self._confirm_bar = self._confirm_bar, None
        if bar:
            bar.hide()
            bar.deleteLater()
        if emit_result is True or emit_result is False:
            self.confirm_resolved.emit(emit_result)

    @property
    def confirm_pending(self) -> bool:
        return self._confirm_bar is not None

    # ── 输入 / 状态 ───────────────────────────────────────────

    def _on_send(self):
        # v0.1.7：确认条挂起期间输入框让位——回车 = 确认「读吧」（已输入文字保留）
        if self._confirm_bar is not None:
            self._confirm_bar.resolve(True)
            return
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()
        # §5 动态对话流：发送后收起输入区，只留消息气泡流
        self.set_input_visible(False)
        self.submitted.emit(text)

    def set_busy(self, busy: bool) -> None:
        self.btn_stop.setVisible(busy)
        self.btn_send.setEnabled(not busy)
        self.input.setFocus()

    def focus_input(self) -> None:
        self.raise_()
        self.activateWindow()
        self.input.setFocus(Qt.FocusReason.ShortcutFocusReason)

    # ── 唤起 / 收起 ───────────────────────────────────────────

    def summon(self, *, show_input: bool = True) -> None:
        """唤起：从桌宠方向淡入 + 上滑 ~10px（200ms，ease-out）。"""
        if self._shown:
            return
        self._shown = True
        # v0.1.7：先定输入区/内容高度再取锚点——否则 pos 动画终点按过期高度算出，
        # 之后内容撑高时动画仍把窗口拉回旧位置，气泡就和人物脱节了
        self.set_input_visible(show_input)
        self._refresh_height()
        target = self.pos()
        self.move(target + QPoint(0, _SLIDE_PX))
        self.setWindowOpacity(0.0)
        super().show()
        anim_pos = QPropertyAnimation(self, b"pos", self)
        anim_pos.setDuration(T.ANIM_SUMMON_MS)
        anim_pos.setStartValue(self.pos())
        anim_pos.setEndValue(target)
        anim_pos.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim_op = QPropertyAnimation(self, b"windowOpacity", self)
        anim_op.setDuration(T.ANIM_SUMMON_MS)
        anim_op.setStartValue(0.0)
        anim_op.setEndValue(1.0)
        anim_op.setEasingCurve(QEasingCurve.Type.OutCubic)
        # 动画期间若内容撑高触发过重新锚定（move 被动画覆盖），落地后再锚一次兜底
        anim_pos.finished.connect(self.resized.emit)
        anim_pos.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        anim_op.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._anims += [anim_pos, anim_op]
        if show_input:
            self.focus_input()
        else:
            self.raise_()

    def dismiss(self) -> None:
        """收起：淡出 150ms。失焦不走这里（interaction-spec §0.5/§2）。"""
        if not self._shown:
            return
        self._shown = False
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(T.ANIM_DISMISS_MS)
        anim.setStartValue(self.windowOpacity())
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        def _after():
            QWidget.hide(self)  # 嵌套闭包里不能用零参 super()
            self.setWindowOpacity(1.0)
            self.dismissed.emit()

        anim.finished.connect(_after)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._anims.append(anim)

    @property
    def summoned(self) -> bool:
        return self._shown

    # ── 自绘：气泡主体 + 右下尾巴（指向桌宠）──────────────────

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        body_h = h - T.TAIL_SIZE
        # 主体：米白圆角 + 2px 深描边
        rect = QRectF(1, 1, w - 2, body_h - 2)
        path = QPainterPath()
        path.addRoundedRect(rect, T.RADIUS_BUBBLE, T.RADIUS_BUBBLE)
        p.setPen(QPen(QColor(T.COLOR_LINE), T.BORDER_MAIN))
        p.setBrush(QColor(T.COLOR_BG))
        p.drawPath(path)
        # 右下小三角尾巴（~14×14，描边/填充与主体一致）
        tail_x = w - 44
        tri = QPolygonF([
            QPointF(tail_x, body_h - 2),
            QPointF(tail_x + 20, body_h - 2),
            QPointF(tail_x + 10, h - 2),
        ])
        p.setPen(QPen(QColor(T.COLOR_LINE), T.BORDER_MAIN))
        p.setBrush(QColor(T.COLOR_BG))
        p.drawPolygon(tri)
        # 盖掉三角形与主体衔接处的描边线
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(T.COLOR_BG))
        p.drawRect(int(tail_x) + 1, int(body_h) - 3, 18, 3)
        p.end()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.escape_requested.emit()
        else:
            super().keyPressEvent(e)

    # ── v0.1.6：气泡可拖动（标题/空白区），与桌宠联动由外层负责 ──

    @property
    def dragging(self) -> bool:
        return self._drag_pos is not None

    def mousePressEvent(self, e):
        # 子控件（按钮/输入框/文本）各自消费事件，只有落在窗口空白区才进这里
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.pos()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
            self.moved.emit(self.x(), self.y())
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self._drag_pos = None
            self.drag_finished.emit()
        super().mouseReleaseEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.resized.emit()

    def changeEvent(self, e):
        # 失焦不自动收起（interaction-spec §0.5「一直挂着」）：显式不处理 deactivation
        super().changeEvent(e)
