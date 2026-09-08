"""对话流组件：气泡 / 思考状态 / 感知提示 / 工具卡 / 读屏确认条 / 错误条。

视觉全部走 theme.py 的 token（docs/visual-spec.md）；动效：每条淡入 180ms ease-out（§5）。
"""

from __future__ import annotations

import re
from pathlib import Path

from PyQt6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .theme import ANIM_FADE_MS, BORDER, FONT, RADIUS_BTN, RADIUS_CARD, C, button_outline

# 新旧协议标记（流式渲染时抑制，最终由 ConversationController 分层）。
_KNOWN_TAGS = tuple(
    f"【{slash}{kind}】"
    for kind in ("brief", "detail", "answer", "summary")
    for slash in ("", "/")
)

# 渲染前清洗（v0.1.10）：Qt setMarkdown 不认 ==高亮==/===标题===/==闭合标记==，
# 模型又实际在输出这些（见 conversation.py 配对正则注释）。渲染入口统一转换，
# 纯兜底，不换渲染器。
_RE_FULL_TAG = re.compile(r"==/?(?:brief|detail|answer|summary)==")
_RE_SETEXT_EQ = re.compile(r"===([^=\n]+)===")
_RE_HIGHLIGHT = re.compile(r"==([^=\n]+)==")


def _sanitize_markdown(text: str) -> str:
    """==标题===/==高亮== → Qt 认的粗体；残留协议标记剥净。"""
    out = _RE_FULL_TAG.sub("", text)          # ==answer==/==/answer== 等整标记
    for tag in _KNOWN_TAGS:
        out = out.replace(tag, "")
    out = _RE_SETEXT_EQ.sub(r"**\1**", out)   # ===文字=== → 粗体（先三元，防被二元吃掉）
    out = _RE_HIGHLIGHT.sub(r"**\1**", out)   # ==文字== → 粗体
    return out

# 存活中的淡入动画。PyQt6 把 Python 槽对象挂在信号发送者（anim）的 wrapper 上；
# BubbleRow 装进布局后只剩 C++ 父子关系，没有任何 Python 引用链保住
# 「row wrapper ↔ anim wrapper ↔ 槽」这个引用岛，循环 GC 会把它整个收掉——
# 之后 finished 触发时调用的是已释放的槽对象，直接段错误（v0.1.7 场景 2b 实测，
# 确认条 180ms 内被回车确认移除时必现）。因此在动画存活期间根住 anim，
# finished 后释放。anim 是 widget 的子对象：widget 先销毁时 anim 一并销毁、
# 不会再发 finished，集合里只残留一个死 wrapper 壳（仅 180ms 内被移除的行，
# 体量可忽略）。
_live_fade_anims: set[QPropertyAnimation] = set()


def fade_in(widget: QWidget) -> None:
    """visual-spec §5：每条气泡淡入 180ms，ease-out；Reduce Motion 时直接显示。"""
    from ..a11y import reduce_motion_enabled
    if reduce_motion_enabled():
        return  # 尊重系统设置：不叠加透明度动画
    eff = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(eff)
    anim = QPropertyAnimation(eff, b"opacity", widget)
    anim.setDuration(ANIM_FADE_MS)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _clear() -> None:
        _live_fade_anims.discard(anim)
        widget.setGraphicsEffect(None)

    _live_fade_anims.add(anim)   # 根住发送者 wrapper，防槽被 GC（见上方注释）
    anim.finished.connect(_clear)
    anim.start()
    widget._fade_anim = anim


def renderable_prefix(buf: str) -> str:
    """流式缓冲中可安全渲染的前缀：抑制未闭合的协议标记，去掉完整标记。

    mock/真引擎的流式 delta 会带着【answer】等标记逐字到达，直接渲染会闪标记。
    """
    out = buf
    for tag in _KNOWN_TAGS:
        out = out.replace(tag, "")
    tail = out.rfind("【")
    if tail != -1:
        frag = out[tail:]
        if any(t.startswith(frag) for t in _KNOWN_TAGS):
            out = out[:tail]
    return out


class MarkdownView(QTextBrowser):
    """正文 Markdown 渲染（Qt 原生 setMarkdown），按内容自适应宽高，不滚动。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenExternalLinks(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("QTextBrowser { background: transparent; border: none; }")
        self.document().setDefaultStyleSheet(f"""
            body {{ color: {C['ink']}; font-family: {FONT['family']};
                   font-size: {FONT['body']}px; line-height: 1.6; }}
            h1, h2, h3, h4 {{ color: {C['ink']}; font-size: {FONT['title']}px; }}
            code {{ font-family: {FONT['mono']}; font-size: {FONT['code']}px;
                    background: {C['bg']}; color: {C['ink']}; }}
            pre {{ font-family: {FONT['mono']}; font-size: {FONT['code']}px;
                   background: {C['bg']}; color: {C['ink']};
                   border-radius: 8px;
                   padding: 8px; line-height: 1.5; }}
            a {{ color: {C['info']}; }}
            blockquote {{ color: {C['ink_soft']}; }}
        """)
        f = QFont()
        f.setFamilies(["PingFang SC", "SF Pro Text"])
        f.setPointSizeF(FONT["body"])
        self.setFont(f)
        self.document().contentsChanged.connect(self._fit)
        self._max_w = 560

    def set_markdown(self, text: str) -> None:
        self.setMarkdown(_sanitize_markdown(text or ""))
        self._fit()

    def set_max_width(self, w: int) -> None:
        self._max_w = max(160, w)
        self._fit()

    def _fit(self) -> None:
        doc = self.document()
        doc.setTextWidth(-1)
        ideal = doc.idealWidth() + 4
        w = min(ideal, self._max_w)
        doc.setTextWidth(w)
        h = doc.size().height() + 6
        self.setFixedSize(int(w), int(max(h, FONT["body"] * 1.6 + 6)))


class BubbleRow(QWidget):
    """一行气泡：负责左右对齐 + 最大宽 ~72%（visual-spec §6.2）。"""

    def __init__(self, content: QWidget, align: str, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 4, 16, 4)
        lay.setSpacing(0)
        self.content = content
        if align == "right":
            lay.addStretch(1)
            lay.addWidget(content, 0, Qt.AlignmentFlag.AlignTop)
        elif align == "left":
            lay.addWidget(content, 0, Qt.AlignmentFlag.AlignTop)
            lay.addStretch(1)
        else:  # center（状态提示类）
            lay.addStretch(1)
            lay.addWidget(content, 0, Qt.AlignmentFlag.AlignTop)
            lay.addStretch(1)
        fade_in(self)

    def set_max_content_width(self, w: int) -> None:
        if hasattr(self.content, "set_max_width"):
            self.content.set_max_width(w)


class _BubbleFrame(QFrame):
    def __init__(self, bg: str, parent=None):
        super().__init__(parent)
        # v0.1.7：详情页（大窗）加回清晰框线区分内容块（浅黄底看不清分隔）；
        # 小对话窗气泡仍保持 v0.1.6 去描边，两处互不影响。
        self.setStyleSheet(f"""
            _BubbleFrame {{
                background: {bg};
                border: 1px solid {C['line']};
                border-radius: {RADIUS_CARD}px;
            }}
        """)


class UserBubble(_BubbleFrame):
    """用户气泡：居右，accent 极浅 tint 底（v0.1.7，与小对话窗用户气泡一致）。"""

    def __init__(self, text: str, parent=None):
        super().__init__(C["accent_tint"], parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        self.view = MarkdownView()
        self.view.set_markdown(text)
        lay.addWidget(self.view)

    def set_max_width(self, w: int) -> None:
        self.view.set_max_width(w - 28 - 2 * BORDER)


class AssistantBubble(_BubbleFrame):
    """haochen 气泡：居左，color-surface 卡面浮在 color-bg 底上（v0.1.4 §2）。
    kind: answer（详答）/ summary（短结，结论在详答之后）。"""

    def __init__(self, kind: str = "answer", parent=None):
        super().__init__(C["surface"], parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(4)
        self.kind = kind
        self.tag: QLabel | None = None
        if kind == "summary":
            self.tag = QLabel("结论")
            self.tag.setStyleSheet(f"""
                color: {C['bg']}; background: {C['accent']};
                border-radius: 6px; padding: 1px 8px;
                font-size: {FONT['body_sm']}px; font-weight: bold;
            """)
            lay.addWidget(self.tag, 0, Qt.AlignmentFlag.AlignLeft)
        self.view = MarkdownView()
        lay.addWidget(self.view)
        self._text = ""

    def set_text(self, text: str) -> None:
        self._text = text
        self.view.set_markdown(text)

    def append_stream(self, buf: str) -> None:
        """打字机流式：传入累计缓冲，内部做部分标记抑制。"""
        self._text = buf
        self.view.set_markdown(renderable_prefix(buf))

    def set_max_width(self, w: int) -> None:
        self.view.set_max_width(w - 28 - 2 * BORDER)


class StatusBubble(QWidget):
    """轻量状态气泡（居中、弱化）：思考中 / 感知提示 / 已停止 等。

    kind: thinking（color-ink-soft）| perceive（color-info，§4.1）| notice | warn（color-warn）
    """

    _COLOR = {"thinking": C["ink_soft"], "perceive": C["info"],
              "notice": C["ink_soft"], "warn": C["warn"]}

    def __init__(self, text: str, kind: str = "thinking", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel(text)
        self.label.setStyleSheet(f"""
            color: {self._COLOR.get(kind, C['ink_soft'])};
            font-size: {FONT['body_sm']}px;
        """)
        lay.addWidget(self.label)
        self._base = text
        self._dots = 0
        self._timer: QTimer | None = None
        if kind == "thinking":
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._tick)
            self._timer.start(400)

    def _tick(self) -> None:
        self._dots = (self._dots + 1) % 4
        self.label.setText(self._base + "…" if self._dots == 0
                           else self._base + "…"[:1] + "." * self._dots)

    def stop(self) -> None:
        if self._timer:
            self._timer.stop()


class ToolCard(QFrame):
    """工具卡（visual-spec §6.3）：图标 + 工具名 + 状态 + 摘要，可折叠看 args/输出。

    状态机（task-4b）：运行中 → 完成/失败/已取消；运行中可取消，失败可重试，
    有产物路径（write/edit）可打开。
    """

    _ICON = {"read_screen": "🖥", "bash": "⌘", "read": "📄", "write": "✏️", "edit": "✏️"}
    _ARTIFACT_KEYS = ("file_path", "path", "filePath")

    def __init__(self, tool_call_id: str, tool_name: str, args: dict | None = None,
                 parent=None):
        super().__init__(parent)
        self.tool_call_id = tool_call_id
        self.tool_name = tool_name
        self.args = args or {}
        self.output = ""
        self.is_error = False
        self._not_run_reason = ""
        self._expanded = False
        self._started_at: float | None = None

        self.setStyleSheet(f"""
            ToolCard {{
                background: {C['surface']};
                border: 1px solid {C['line']};
                border-radius: {RADIUS_CARD}px;
            }}
        """)  # v0.1.7：与气泡一致加回清晰描边
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(8)
        self.toggle = QToolButton()
        self.toggle.setText("▸")
        self.toggle.setCheckable(True)
        self.toggle.setAccessibleName("工具详情")
        self.toggle.setStyleSheet(f"QToolButton {{ border: none; color: {C['ink_soft']}; }}")
        self.toggle.toggled.connect(self._on_toggle)
        header.addWidget(self.toggle)

        icon = QLabel(self._ICON.get(tool_name, "🔧"))
        header.addWidget(icon)

        name = QLabel(tool_name)
        name.setStyleSheet(f"font-weight: bold; font-size: {FONT['body']}px;")
        header.addWidget(name)

        self.status = QLabel("运行中…")
        self._set_status_color(C["info"])
        header.addWidget(self.status)

        self.summary = QLabel(self._summary_text())
        self.summary.setStyleSheet(f"color: {C['ink_soft']}; font-size: {FONT['body_sm']}px;")
        header.addWidget(self.summary, 1)

        # 操作区（task-4b）：运行中→取消；失败→重试；有产物→打开。
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setAccessibleName("取消工具")
        self.cancel_button.setStyleSheet(button_outline())
        self.cancel_button.setToolTip("停止当前回合")
        self.cancel_button.hide()
        header.addWidget(self.cancel_button)

        self.retry_button = QPushButton("重试")
        self.retry_button.setAccessibleName("重试")
        self.retry_button.setStyleSheet(button_outline())
        self.retry_button.setToolTip("重新发送上一条消息")
        self.retry_button.hide()
        header.addWidget(self.retry_button)

        self.open_button = QPushButton("打开")
        self.open_button.setAccessibleName("打开产物")
        self.open_button.setStyleSheet(button_outline())
        artifact = self.artifact_path()
        if artifact is not None:
            self.open_button.setToolTip(str(artifact))
        else:
            self.open_button.hide()
        header.addWidget(self.open_button)
        root.addLayout(header)

        self.detail = QTextBrowser()
        self.detail.setFrameShape(QFrame.Shape.NoFrame)
        self.detail.setMaximumHeight(180)
        self.detail.setStyleSheet(f"""
            QTextBrowser {{
                background: {C['bg']}; border: none;
                border-radius: 8px; padding: 6px;
                font-family: {FONT['mono']}; font-size: {FONT['code']}px;
                color: {C['ink']};
            }}
        """)
        self.detail.hide()
        root.addWidget(self.detail)

    def _set_status_color(self, color: str) -> None:
        self.status.setStyleSheet(
            f"color: {color}; font-size: {FONT['body_sm']}px; font-weight: bold;")

    def _summary_text(self) -> str:
        if self.tool_name == "read_screen":
            return "读取当前窗口屏幕内容"
        if "command" in self.args:
            cmd = str(self.args["command"])
            return cmd if len(cmd) <= 42 else cmd[:42] + "…"
        return ""

    def start_clock(self) -> None:
        """记录工具开始时刻，用于完成时显示耗时（本地测量，不依赖引擎字段）。"""
        import time
        self._started_at = time.monotonic()

    def elapsed_ms(self) -> int | None:
        if self._started_at is None:
            return None
        import time
        return int((time.monotonic() - self._started_at) * 1000)

    def mark_done(self, result_text: str, is_error: bool, elapsed_ms: int | None = None) -> None:
        self.is_error = is_error
        self.output = result_text or ""
        self.cancel_button.hide()
        denied_read = self.tool_name == "read_screen" and any(
            token in self.output for token in ("用户拒绝了读屏", "超时未确认")
        )
        if self._not_run_reason or denied_read:
            reason = self._not_run_reason or "已拒绝/超时"
            self.status.setText(f"未执行 · {reason}")
            self._set_status_color(C["ink_soft"])
            self.retry_button.hide()
        elif is_error:
            self.status.setText("✗ 失败")
            self._set_status_color(C["danger"])
            self.retry_button.show()
        else:
            elapsed = self._format_elapsed(elapsed_ms)
            self.status.setText(f"✓ 完成{elapsed}")
            self._set_status_color(C["accent"])
        self._refresh_detail()

    def mark_not_run(self, reason: str) -> None:
        """用户拒绝或取消敏感工具时使用中性终态，不展示成功对勾。"""
        self._not_run_reason = reason
        self.cancel_button.hide()
        self.status.setText(f"未执行 · {reason}")
        self._set_status_color(C["ink_soft"])
        self._refresh_detail()

    def mark_cancelled(self) -> None:
        self.is_error = False
        self.cancel_button.hide()
        self.status.setText("已取消")
        self._set_status_color(C["ink_soft"])
        self._refresh_detail()

    @staticmethod
    def _format_elapsed(elapsed_ms: int | None) -> str:
        if elapsed_ms is None or elapsed_ms < 0:
            return ""
        if elapsed_ms < 1000:
            return f" · {elapsed_ms}ms"
        return f" · {elapsed_ms / 1000:.1f}s"

    def artifact_path(self) -> Path | None:
        """write/edit 类工具的产物路径（用于“打开”）"""
        for key in self._ARTIFACT_KEYS:
            value = self.args.get(key)
            if isinstance(value, str) and value.strip():
                return Path(value.strip())
        return None

    def _refresh_detail(self) -> None:
        import json
        parts = []
        if self.args:
            parts.append("参数：\n" + json.dumps(self.args, ensure_ascii=False, indent=2))
        if self.output:
            excerpt = self.output if len(self.output) <= 1200 else self.output[:1200] + "\n…（截断）"
            parts.append("输出：\n" + excerpt)
        self.detail.setPlainText("\n\n".join(parts) or "（无详情）")

    def _on_toggle(self, checked: bool) -> None:
        self._expanded = checked
        self.toggle.setText("▾" if checked else "▸")
        if checked:
            self._refresh_detail()
        self.detail.setVisible(checked)

    def set_max_width(self, w: int) -> None:
        self.setMinimumWidth(min(320, w))
        self.setMaximumWidth(w)


class ConfirmBar(QFrame):
    """读屏确认条（rpc-contract §5 / interaction-spec §4.1）：「读吧」实心 /「不读」描边。"""

    answered = pyqtSignal(bool)   # True=读吧 False=不读

    def __init__(self, title: str, message: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            ConfirmBar {{
                background: {C['surface']};
                border: 1px solid {C['warn']};
                border-radius: {RADIUS_CARD}px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(8)
        t = QLabel(f"⚠ {title}")
        t.setStyleSheet(f"color: {C['warn']}; font-weight: bold; font-size: {FONT['body']}px;")
        lay.addWidget(t)
        m = QLabel(message)
        m.setWordWrap(True)
        m.setStyleSheet(f"color: {C['ink']}; font-size: {FONT['body_sm']}px;")
        lay.addWidget(m)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_no = QPushButton("不读")
        # 拒绝按钮保持中性次要：灰字无底无框
        self.btn_no.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C['ink_soft']};
                border: none; border-radius: {RADIUS_BTN}px; padding: 5px 16px; }}
            QPushButton:hover {{ background: {C['bg']}; }}
        """)
        self.btn_no.clicked.connect(lambda: self._answer(False))
        row.addWidget(self.btn_no)
        self.btn_yes = QPushButton("读吧")
        # v0.1.6：允许按钮醒目品牌绿（白字加粗、无深框、hover 加深）
        self.btn_yes.setStyleSheet(f"""
            QPushButton {{ background: {C['accent']}; color: #fff;
                border: none; border-radius: {RADIUS_BTN}px;
                padding: 5px 16px; font-weight: bold; }}
            QPushButton:hover {{ background: #2f6a4d; }}
        """)
        self.btn_yes.setDefault(True)
        self.btn_yes.setAutoDefault(True)
        self.btn_yes.clicked.connect(lambda: self._answer(True))
        row.addWidget(self.btn_yes)
        lay.addLayout(row)
        # v0.1.7：确认条弹出即接管焦点，Enter/Return 直接确认「读吧」
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        self.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def keyPressEvent(self, ev) -> None:
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._answer(True)
            return
        super().keyPressEvent(ev)

    def _answer(self, confirmed: bool) -> None:
        self.btn_yes.setEnabled(False)
        self.btn_no.setEnabled(False)
        self.answered.emit(confirmed)

    def set_max_width(self, w: int) -> None:
        self.setMaximumWidth(w)
        self.setMinimumWidth(min(360, w))


class ActionBanner(QFrame):
    """Non-destructive status with one explicit recovery action."""

    action_requested = pyqtSignal()

    def __init__(self, text: str, action_text: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            ActionBanner {{
                background: {C['surface']};
                border: 1px solid {C['line']};
                border-radius: {RADIUS_CARD}px;
            }}
        """)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        self.message = QLabel(text)
        self.message.setWordWrap(True)
        self.message.setStyleSheet(f"color: {C['ink']}; font-size: {FONT['body_sm']}px;")
        lay.addWidget(self.message, 1)
        self.action_button = QPushButton(action_text)
        self.action_button.setStyleSheet(button_outline())
        self.action_button.clicked.connect(self.action_requested)
        lay.addWidget(self.action_button)

    def mark_done(self, text: str) -> None:
        self.message.setText(text)
        self.action_button.hide()

    def set_max_width(self, w: int) -> None:
        self.setMaximumWidth(w)
        self.setMinimumWidth(min(360, w))


class QueueIndicator(QFrame):
    """排队中消息的可见指示条：显示内容预览并可取消（interaction-spec §3）。"""

    def __init__(self, preview: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QueueIndicator {{
                background: {C['surface']};
                border: 1px dashed {C['line']};
                border-radius: {RADIUS_CARD}px;
            }}
        """)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 6, 14, 6)
        self.label = QLabel(f"已排队 · {preview}")
        self.label.setStyleSheet(
            f"color: {C['ink_soft']}; font-size: {FONT['body_sm']}px;")
        lay.addWidget(self.label, 1)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setStyleSheet(button_outline())
        lay.addWidget(self.cancel_button)

    def mark_cancelled(self) -> None:
        self.label.setText("已取消")
        self.cancel_button.hide()

    def set_max_width(self, w: int) -> None:
        self.setMaximumWidth(w)
        self.setMinimumWidth(min(360, w))


class QueueRecoveryBanner(QFrame):
    """Recovered unsent input with explicit resend/cancel choices."""

    resend_requested = pyqtSignal()
    cancel_requested = pyqtSignal()

    def __init__(self, preview: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QueueRecoveryBanner {{
                background: {C['surface']};
                border: 1px solid {C['warn']};
                border-radius: {RADIUS_CARD}px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        self.message = QLabel(f"有一条未确认发送的消息：{preview}")
        self.message.setWordWrap(True)
        lay.addWidget(self.message)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setStyleSheet(button_outline())
        self.cancel_button.clicked.connect(self.cancel_requested)
        actions.addWidget(self.cancel_button)
        self.resend_button = QPushButton("重新发送")
        self.resend_button.setStyleSheet(button_outline())
        self.resend_button.clicked.connect(self.resend_requested)
        actions.addWidget(self.resend_button)
        lay.addLayout(actions)

    def mark_done(self, text: str) -> None:
        self.message.setText(text)
        self.cancel_button.hide()
        self.resend_button.hide()

    def set_max_width(self, w: int) -> None:
        self.setMaximumWidth(w)
        self.setMinimumWidth(min(360, w))


class ErrorBanner(QFrame):
    """错误条（interaction-spec §8.2 / visual-spec §6.4）：color-danger + 重试入口。"""

    retry = pyqtSignal()

    def __init__(self, text: str, retryable: bool = True, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            ErrorBanner {{
                background: {C['surface']};
                border: 1px solid {C['danger']};
                border-radius: {RADIUS_CARD}px;
            }}
        """)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        msg = QLabel(f"✗ {text}")
        msg.setWordWrap(True)
        msg.setStyleSheet(f"color: {C['danger']}; font-size: {FONT['body_sm']}px;")
        lay.addWidget(msg, 1)
        if retryable:
            btn = QPushButton("重试")
            btn.setStyleSheet(button_outline())
            btn.clicked.connect(self.retry)
            lay.addWidget(btn)

    def set_max_width(self, w: int) -> None:
        self.setMaximumWidth(w)
        self.setMinimumWidth(min(360, w))
