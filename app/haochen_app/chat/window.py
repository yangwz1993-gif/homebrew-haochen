"""haochen 完整对话窗口（M-C，visual-spec §6.2）。

布局：左侧会话栏（240px，高亮/+新会话/重命名/删除）+ 右侧动态对话流
（用户气泡居右、haochen 居左、最大宽 ~72%、逐条淡入 180ms）。

数据流：
- 两步协议（answer→summary）走共享的 ConversationController（只订阅信号）；
- 工具卡 / 读屏确认条直接订阅 EngineClient.event；
- 命令响应按 id 关联（rpc-contract §1.2），_rpc() 挂回调。

嵌入方式：ChatWindow(client=None) — 不传 client 则自建（HAOCHEN_MOCK=1 走 mock）。
"""

from __future__ import annotations

import os

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..app_tracking import write_last_user_text
from ..conversation import SUMMARY_KICK_PREFIX, ConversationController, parse_paired, strip_tags
from ..engine_client import EngineClient, delete_session, haochen_home, restore_session
from .sidebar import SessionSidebar
from .theme import FONT, RADIUS_INPUT, C, button_solid
from .widgets import (
    ActionBanner,
    AssistantBubble,
    BubbleRow,
    ConfirmBar,
    ErrorBanner,
    StatusBubble,
    ToolCard,
    UserBubble,
)


class _InputBox(QPlainTextEdit):
    """回车发送 / ⌘回车换行（interaction-spec §3）。"""

    def __init__(self, on_send, parent=None):
        super().__init__(parent)
        self._on_send = on_send
        self.setPlaceholderText("和 haochen 说点什么…（⏎ 发送，⌘⏎ 换行）")
        self.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {C['surface']};
                border: none;
                border-radius: {RADIUS_INPUT}px;
                padding: 8px 10px;
                font-size: {FONT['body']}px;
            }}
            QPlainTextEdit:focus {{ border: 1px solid {C['accent']}; }}
        """)
        self.setFixedHeight(self._line_h() * 2 + 22)
        self.textChanged.connect(self._auto_grow)

    def _line_h(self) -> int:
        return self.fontMetrics().lineSpacing()

    def _auto_grow(self) -> None:
        lines = max(2, min(6, self.document().lineCount()))
        self.setFixedHeight(self._line_h() * lines + 22)

    def keyPressEvent(self, ev) -> None:
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if ev.modifiers() & Qt.KeyboardModifier.MetaModifier:
                self.insertPlainText("\n")
            else:
                self._on_send()
            return
        super().keyPressEvent(ev)


class ChatWindow(QWidget):
    """完整对话窗口。P4 集成：win = ChatWindow(engine_client) 后 show() 即可。

    v0.1.4 hotfix：支持「从气泡展开」模式（open_from_bubble）——窗口从短会话
    气泡的 rect 平滑扩展到正常尺寸；Esc / ⌘W / closeEvent 只收回气泡 rect 后
    隐藏（detail_collapsed 通知 pet 侧恢复气泡），任何情况下不触发 app 退出。
    """

    detail_collapsed = pyqtSignal()   # 详情模式收起动画播完、窗口已隐藏

    def __init__(self, client: EngineClient | None = None, supervisor=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("haochen")
        self.resize(1200, 800)
        self.setMinimumSize(900, 600)
        # 防御性加固（v0.1.4 hotfix 问题4）：关窗永不退出 app
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)

        self.client = client or EngineClient()
        self._supervisor = supervisor            # P4：注入则双入口仲裁/崩溃联动
        self._mock = bool(os.environ.get("HAOCHEN_MOCK") == "1") or getattr(
            self.client, "_mock", False)
        self._make_controller()

        self._pending_rpc: dict[str, callable] = {}
        self._sessions: list[dict] = []        # [{path, title}]，新→旧
        self._current_path: str | None = None
        self._queue: list[str] = []            # 生成中排队的问题（§3 不被覆盖）
        self._last_user_text = ""
        self._stream_row: BubbleRow | None = None
        self._stream_buf = ""
        self._thinking_row: BubbleRow | None = None
        self._status_row: BubbleRow | None = None   # 提炼结论等轻状态
        self._tool_cards: dict[str, ToolCard] = {}
        self._confirm: tuple[str, BubbleRow] | None = None  # (req_id, row)
        self._engine_crashed = False

        # ── 详情模式（从气泡展开）状态 ──
        self._detail_mode = False
        self._detail_collapsing = False
        self._detail_source_rect = QRect()
        self._geom_anim: QPropertyAnimation | None = None

        self._build_ui()
        self._wire()

        # ⌘W：详情模式 = 收起；正常模式不拦截（行为不变）
        sc = QShortcut(QKeySequence.StandardKey.Close, self)
        sc.setContext(Qt.ShortcutContext.WindowShortcut)
        sc.activated.connect(self._on_close_shortcut)

    # ── UI 骨架 ────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = SessionSidebar()
        root.addWidget(self.sidebar)

        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(0, 0, 0, 10)
        rlay.setSpacing(0)
        root.addWidget(right, 1)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { border: none; }")
        self.flow_host = QWidget()
        self.flow = QVBoxLayout(self.flow_host)
        self.flow.setContentsMargins(0, 12, 0, 12)
        self.flow.setSpacing(6)
        self.flow.addStretch(1)
        self.scroll.setWidget(self.flow_host)
        rlay.addWidget(self.scroll, 1)

        input_bar = QHBoxLayout()
        input_bar.setContentsMargins(16, 8, 16, 0)
        input_bar.setSpacing(8)
        self.input = _InputBox(self._on_send)
        input_bar.addWidget(self.input, 1)
        self.btn_send = QPushButton("➤")
        self.btn_send.setFixedWidth(56)
        self.btn_send.setStyleSheet(button_solid())
        self.btn_send.clicked.connect(self._on_send)
        input_bar.addWidget(self.btn_send)
        self.btn_stop = QPushButton("■ 停止")
        self.btn_stop.setStyleSheet(button_solid().replace(C["accent"], C["danger"]))
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_stop.hide()
        input_bar.addWidget(self.btn_stop)
        rlay.addLayout(input_bar)

    def _wire(self) -> None:
        self.client.response.connect(self._on_response)
        self.client.event.connect(self._on_engine_event)
        self.sidebar.new_requested.connect(self._new_session)
        self.sidebar.session_selected.connect(self._switch_session)
        self.sidebar.rename_requested.connect(self._rename_session)
        self.sidebar.delete_requested.connect(self._delete_session)
        if self._supervisor is not None:
            # P4：崩溃/重启由 supervisor 统一编排；镜像/泄流挂钩总线
            sup = self._supervisor
            sup.crashed.connect(self._on_crash)
            sup.restarting.connect(self._on_sup_restarting)
            sup.restarted.connect(self._on_sup_restarted)
            sup.restart_failed.connect(self._on_sup_restart_failed)
            self.client.event.connect(self._on_foreign_turn_end)
        else:
            self.client.crashed.connect(self._on_crash)

    def _make_controller(self) -> None:
        old = getattr(self, "ctrl", None)
        if old is not None:
            try:
                self.client.event.disconnect(old._on_event)
            except TypeError:
                pass
            if self._supervisor is not None:
                self._supervisor.unregister(old)
        self.ctrl = ConversationController(self.client)
        self.ctrl.answer_delta.connect(self._on_answer_delta)
        self.ctrl.answer_done.connect(self._on_answer_done)
        self.ctrl.summarizing.connect(self._on_summarizing)
        self.ctrl.summary_done.connect(self._on_summary_done)
        self.ctrl.failed.connect(self._on_failed)
        self.ctrl.busy_changed.connect(self._on_busy_changed)
        if self._supervisor is not None:
            self._supervisor.register(self.ctrl)

    # ── 生命周期 ────────────────────────────────────────────────

    def start(self) -> None:
        self.client.start()
        self._rpc(self.client.get_state, self._on_state)

    # ── RPC 响应按 id 关联（契约 §1.2）─────────────────────────

    def _rpc(self, sender, callback, *args) -> None:
        try:
            rid = sender(*args)
        except RuntimeError:
            return
        self._pending_rpc[rid] = callback

    def _on_response(self, resp: dict) -> None:
        cb = self._pending_rpc.pop(resp.get("id", ""), None)
        if cb:
            cb(resp)

    def _on_state(self, resp: dict) -> None:
        if not resp.get("success"):
            return
        data = resp.get("data") or {}
        path = data.get("sessionFile") or ""
        model = (data.get("model") or {}).get("id", "")
        self.sidebar.set_status(f"模型：{model}")
        if path and path != self._current_path:
            self._current_path = path
            if not any(s["path"] == path for s in self._sessions):
                name = data.get("sessionName") or "新会话"
                self._sessions.insert(0, {"path": path, "title": name})
            self._refresh_sidebar()
            self._rpc(self.client.get_messages, self._render_history)

    # ── 对话流渲染 ─────────────────────────────────────────────

    def _bubble_max_w(self) -> int:
        return int(self.scroll.viewport().width() * 0.72)

    def _add_row(self, content: QWidget, align: str, before: QWidget | None = None) -> BubbleRow:
        row = BubbleRow(content, align)
        row.set_max_content_width(self._bubble_max_w())
        idx = self.flow.count() - 1 if before is None else self.flow.indexOf(before)
        self.flow.insertWidget(idx, row)
        QTimer.singleShot(0, self._scroll_bottom)
        return row

    def _scroll_bottom(self) -> None:
        sb = self.scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _drop_row(self, row: BubbleRow | None) -> None:
        if row is None:
            return
        self.flow.removeWidget(row)
        row.deleteLater()

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        w = self._bubble_max_w()
        for i in range(self.flow.count()):
            item = self.flow.itemAt(i)
            if item and isinstance(item.widget(), BubbleRow):
                item.widget().set_max_content_width(w)

    # ── 发送 / 停止 ─────────────────────────────────────────────

    def _foreign_busy(self) -> bool:
        """P4：另一入口（气泡）是否正在生成。"""
        return (self._supervisor is not None
                and self._supervisor.busy_except(self.ctrl))

    def _on_send(self) -> None:
        text = self.input.toPlainText().strip()
        if not text or self._engine_crashed:
            return
        self.input.clear()
        # 确认条挂起时，输入 y/n 也算回答（mock 提示语约定）
        if self._confirm and text.lower() in ("y", "n"):
            self._answer_confirm(text.lower() == "y")
            return
        self._add_row(UserBubble(text), "right")
        if self.ctrl.busy or self._foreign_busy():
            self._queue.append(text)          # §3：生成期间可继续输入（排队不丢）
            return
        self._send_now(text)

    def _send_now(self, text: str) -> None:
        self._last_user_text = text
        # v0.1.8 看图模式：落盘用户原文，供引擎扩展 read_screen 判定看图意图
        write_last_user_text(haochen_home(), text)
        self._auto_title(text)
        self.ctrl.send(text)

    def _on_stop(self) -> None:
        if self._confirm:
            self._answer_confirm(None)        # Esc/停止时取消确认（契约 §5.3 cancelled）
        self.ctrl.abort()

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key.Key_Escape:
            if self._detail_mode:
                # 详情模式 Esc = 收起（优先于打断生成）；正常打开时行为不变
                self.collapse_detail()
            else:
                self._on_stop()
            return
        super().keyPressEvent(ev)

    def _on_close_shortcut(self) -> None:
        """⌘W：仅详情模式拦截为「收起」；正常模式不处理（保持旧行为）。"""
        if self._detail_mode:
            self.collapse_detail()

    def closeEvent(self, ev) -> None:
        # 详情模式下系统级关闭（⌘W/Mission Control 等）也走收起，绝不退出 app
        if self._detail_mode:
            ev.ignore()
            self.collapse_detail()
            return
        super().closeEvent(ev)

    # ── 详情模式：从气泡展开 / 收回气泡 ─────────────────────────

    def open_from_bubble(self, source_rect: QRect | None = None) -> None:
        """「展开详细」：从短会话气泡 rect 平滑扩展（OutCubic ~220ms）到正常尺寸，
        并滚动定位到当前轮（对话流尾部）。"""
        self._detail_mode = True
        self._detail_collapsing = False
        self._detail_source_rect = source_rect or QRect()
        target = self._detail_target_rect()
        if self._detail_source_rect.isValid() and not self._detail_source_rect.isNull():
            # 动画期间放开最小尺寸，否则起点 rect 会被 minimumSize 钳大
            self.setMinimumSize(1, 1)
            self.setGeometry(self._detail_source_rect)
            self.show()
            self._animate_geom_to(target, finished=self._restore_min_size)
        else:  # 无来源 rect → 直接落到目标位
            self.setGeometry(target)
            self.show()
        self.raise_()
        self.activateWindow()
        # 定位到当前轮：历史镜像渲染（showEvent）与动画落地后各滚一次底
        QTimer.singleShot(0, self._scroll_bottom)
        QTimer.singleShot(300, self._scroll_bottom)

    def _restore_min_size(self) -> None:
        if self._detail_mode and not self._detail_collapsing:
            self.setMinimumSize(900, 600)

    def collapse_detail(self) -> None:
        """Esc / ⌘W：反向收回气泡 rect，播完隐藏并发 detail_collapsed。"""
        if not self._detail_mode or self._detail_collapsing:
            return
        self._detail_collapsing = True
        rect = self._detail_source_rect
        if rect.isValid() and not rect.isNull():
            self.setMinimumSize(1, 1)   # 同样放开，窗口才能收回气泡大小
            self._animate_geom_to(rect, finished=self._after_detail_collapse)
        else:
            self._after_detail_collapse()

    def _after_detail_collapse(self) -> None:
        self._detail_mode = False
        self._detail_collapsing = False
        self.hide()
        self.setMinimumSize(900, 600)
        self.detail_collapsed.emit()

    def _detail_target_rect(self) -> QRect:
        """正常尺寸（默认 1200×800，夹回屏幕），以气泡中心锚定。"""
        screen = QApplication.primaryScreen().availableGeometry()
        w = min(1200, screen.width() - 16)
        h = min(800, screen.height() - 16)
        if self._detail_source_rect.isValid() and not self._detail_source_rect.isNull():
            cx = self._detail_source_rect.center().x()
            cy = self._detail_source_rect.center().y()
        else:
            cx, cy = screen.center().x(), screen.center().y()
        x = max(screen.left() + 8, min(cx - w // 2, screen.right() - w - 8))
        y = max(screen.top() + 8, min(cy - h // 2, screen.bottom() - h - 8))
        return QRect(x, y, w, h)

    def _animate_geom_to(self, rect: QRect, finished=None) -> None:
        old, self._geom_anim = self._geom_anim, None
        if old is not None:
            try:
                old.stop()
            except RuntimeError:
                pass  # DeleteWhenStopped 后底层 C++ 对象已删，忽略
        anim = QPropertyAnimation(self, b"geometry", self)
        anim.setDuration(220)
        anim.setStartValue(self.geometry())
        anim.setEndValue(rect)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        if finished is not None:
            anim.finished.connect(finished)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._geom_anim = anim

    def _auto_title(self, text: str) -> None:
        """首条用户消息自动命名会话（§6 标题自动/可重命名）。"""
        for s in self._sessions:
            if s["path"] == self._current_path and s["title"] == "新会话":
                title = text[:20] + ("…" if len(text) > 20 else "")
                s["title"] = title
                self.sidebar.update_title(s["path"], title)
                self._rpc(self.client.set_session_name, lambda r: None, title)
                return

    # ── ConversationController 信号（两步协议）──────────────────

    def _on_busy_changed(self, busy: bool) -> None:
        self.btn_stop.setVisible(busy)
        self.btn_send.setVisible(not busy)
        if busy:
            self._thinking_row = self._add_row(StatusBubble("haochen 正在想", "thinking"), "left")
        else:
            # busy(False) 先于 summary_done/failed 同步发出，推迟一拍让结论先落位
            QTimer.singleShot(0, self._drain_queue)

    def _drain_queue(self) -> None:
        if (self._queue and not self.ctrl.busy and not self._engine_crashed
                and not self._foreign_busy()):
            self._send_now(self._queue.pop(0))

    def _on_answer_delta(self, delta: str) -> None:
        self._drop_thinking()
        self._drop_status()
        if self._stream_row is None:
            bubble = AssistantBubble("answer")
            self._stream_row = self._add_row(bubble, "left")
            self._stream_buf = ""
        self._stream_buf += delta
        self._stream_row.content.append_stream(self._stream_buf)
        QTimer.singleShot(0, self._scroll_bottom)

    def _on_answer_done(self, answer: str) -> None:
        self._drop_thinking()
        if self._stream_row is not None:
            self._stream_row.content.set_text(answer)
            self._stream_row = None
            self._stream_buf = ""
        else:
            self._add_row(AssistantBubble("answer"), "left").content.set_text(answer)

    def _on_summarizing(self) -> None:
        self._status_row = self._add_row(StatusBubble("正在提炼结论", "thinking"), "left")

    def _on_summary_done(self, summary: str) -> None:
        self._drop_thinking()
        self._drop_status()
        if not summary:
            return
        bubble = AssistantBubble("summary")
        bubble.set_text(summary)
        # 结论后置（v0.1.4 §1a）：summary 气泡追加到本轮 answer 气泡之后
        self._add_row(bubble, "left")

    def _on_failed(self, err: str) -> None:
        self._drop_thinking()
        self._drop_status()
        self._stream_row = None
        banner = ErrorBanner(f"{err}")
        banner.retry.connect(lambda: self._send_now(self._last_user_text))
        self._add_row(banner, "left")

    def _drop_thinking(self) -> None:
        if self._thinking_row:
            self._thinking_row.content.stop()
        self._drop_row(self._thinking_row)
        self._thinking_row = None

    def _drop_status(self) -> None:
        if self._status_row:
            self._status_row.content.stop()
        self._drop_row(self._status_row)
        self._status_row = None

    # ── 引擎事件（工具卡 / 读屏确认 / 感知提示）──────────────────

    def _on_engine_event(self, ev: dict) -> None:
        # P4 双入口：工具/确认/感知事件只由「本轮发起方」渲染（另一入口静默，
        # 气泡回合结束后由 _on_foreign_turn_end 统一镜像历史）。
        if self._supervisor is not None and not self.ctrl.busy:
            return
        t = ev.get("type")
        if t == "tool_execution_start":
            self._drop_thinking()
            name = ev.get("toolName", "tool")
            if name == "read_screen":
                # 感知提示（interaction-spec §4.1：绝不默默读屏）
                self._add_row(StatusBubble("我正看一下你的屏幕…", "perceive"), "left")
            card = ToolCard(ev.get("toolCallId", ""), name, ev.get("args") or {})
            self._tool_cards[card.tool_call_id] = card
            self._add_row(card, "left")
        elif t == "tool_execution_end":
            card = self._tool_cards.pop(ev.get("toolCallId", ""), None)
            if card:
                result = ev.get("result") or {}
                text = "".join(c.get("text", "") for c in result.get("content", [])
                               if c.get("type") == "text")
                card.mark_done(text, bool(ev.get("isError")))
        elif t == "extension_ui_request":
            self._on_ui_request(ev)
        elif t == "extension_error":
            self._add_row(ErrorBanner(f"扩展错误：{ev.get('error', '')}", retryable=False), "left")

    def _on_ui_request(self, ev: dict) -> None:
        if self._supervisor is not None and not self.ctrl.busy:
            return  # P4：确认只路由到本轮发起方
        method = ev.get("method")
        rid = ev.get("id", "")
        if method == "confirm":
            self._drop_thinking()
            bar = ConfirmBar(ev.get("title", "确认"), ev.get("message", ""))
            row = self._add_row(bar, "left")
            self._confirm = (rid, row)
            bar.answered.connect(self._answer_confirm)
        elif method in ("select", "input", "editor"):
            self.client.respond_ui(rid, cancelled=True)   # 契约 §5.3：未实现一律 cancelled
        # notify / setStatus 等 fire-and-forget 忽略

    def _answer_confirm(self, confirmed) -> None:
        if not self._confirm:
            return
        rid, row = self._confirm
        self._confirm = None
        if confirmed is None:
            self.client.respond_ui(rid, cancelled=True)
            note, kind = "已取消读屏", "notice"
        else:
            self.client.respond_ui(rid, confirmed=confirmed)
            note = "已授权读屏" if confirmed else "已拒绝读屏"
            kind = "perceive" if confirmed else "notice"
        self._drop_row(row)
        self._add_row(StatusBubble(note, kind), "left")

    # ── 会话管理（契约 §2.3–2.8）────────────────────────────────

    def _refresh_sidebar(self) -> None:
        self.sidebar.set_sessions(self._sessions, self._current_path)

    def _new_session(self) -> None:
        if self.ctrl.busy:
            return
        self._rpc(self.client.new_session, lambda r: self._rpc(self.client.get_state, self._on_state))

    def _switch_session(self, path: str) -> None:
        if path == self._current_path or self.ctrl.busy:
            return
        def done(resp: dict) -> None:
            if resp.get("success") and not (resp.get("data") or {}).get("cancelled"):
                self._current_path = path
                self._refresh_sidebar()
                self._rpc(self.client.get_messages, self._render_history)
        self._rpc(self.client.switch_session, done, path)

    def _rename_session(self, path: str, name: str) -> None:
        def apply(_resp: dict) -> None:
            for s in self._sessions:
                if s["path"] == path:
                    s["title"] = name
            self.sidebar.update_title(path, name)
        if path == self._current_path:
            self._rpc(self.client.set_session_name, apply, name)
        else:  # 引擎只能改当前会话名：先切过去再改
            def after_switch(resp: dict) -> None:
                if resp.get("success"):
                    self._current_path = path
                    self._refresh_sidebar()
                    self._rpc(self.client.get_messages, self._render_history)
                    self._rpc(self.client.set_session_name, apply, name)
            self._rpc(self.client.switch_session, after_switch, path)

    def _delete_session(self, path: str) -> None:
        if self.ctrl.busy:
            return
        record = next((dict(session) for session in self._sessions if session["path"] == path), None)
        if record is None:
            self._add_row(ErrorBanner("删除失败：会话已不存在。", retryable=False), "left")
            return
        title = record.get("title") or "新会话"
        answer = QMessageBox.question(
            self,
            "删除会话",
            f"确定删除“{title}”吗？删除后可以立即撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        original_index = self._sessions.index(next(session for session in self._sessions if session["path"] == path))

        def really_delete() -> bool:
            deletion = None
            if not self._mock:
                try:
                    deletion = delete_session(path, self.client.home)
                except (OSError, ValueError) as exc:
                    self._add_row(ErrorBanner(f"删除失败：{exc}", retryable=False), "left")
                    self.sidebar.set_status("会话删除失败，原会话仍保留")
                    return False
            self._sessions = [session for session in self._sessions if session["path"] != path]
            self._refresh_sidebar()
            banner = ActionBanner(f"已删除“{title}”", "撤销")
            self._add_row(banner, "left")

            def undo() -> None:
                if deletion is not None:
                    try:
                        restore_session(deletion, self.client.home)
                    except (OSError, ValueError) as exc:
                        self._add_row(ErrorBanner(f"恢复失败：{exc}", retryable=False), "left")
                        return
                insert_at = min(original_index, len(self._sessions))
                self._sessions.insert(insert_at, record)
                self._refresh_sidebar()
                banner.mark_done(f"已恢复“{title}”")

            banner.action_requested.connect(undo)
            return True

        if path != self._current_path:
            really_delete()
            return

        # 契约 §2.8：删除当前会话前必须成功创建并确认切到新会话。
        def after_new(resp: dict) -> None:
            if not resp.get("success") or (resp.get("data") or {}).get("cancelled"):
                self._add_row(ErrorBanner("删除取消：无法创建替代会话。", retryable=False), "left")
                return

            def after_state(state_resp: dict) -> None:
                new_path = (state_resp.get("data") or {}).get("sessionFile")
                if not state_resp.get("success") or not new_path or new_path == path:
                    self._add_row(ErrorBanner("删除取消：未确认已切换到新会话。", retryable=False), "left")
                    return
                if really_delete():
                    self._on_state(state_resp)
                    return

                # The file still exists: return the engine to the original session so
                # a failed delete never changes the user's active conversation.
                def after_rollback(rollback_resp: dict) -> None:
                    if rollback_resp.get("success"):
                        self._rpc(self.client.get_messages, self._render_history)
                    else:
                        self._add_row(
                            ErrorBanner("原会话仍在，但自动切回失败；请从侧栏重新选择。", retryable=False),
                            "left",
                        )

                self._rpc(self.client.switch_session, after_rollback, path)

            self._rpc(self.client.get_state, after_state)

        self._rpc(self.client.new_session, after_new)

    # ── 历史渲染（get_messages → 对话流）────────────────────────

    def _render_history(self, resp: dict) -> None:
        if not resp.get("success"):
            return
        self._clear_flow()
        messages = (resp.get("data") or {}).get("messages") or []
        for msg in messages:
            role = msg.get("role")
            if role == "user":
                text = "".join(c.get("text", "") for c in msg.get("content", [])
                               if c.get("type") == "text")
                if text.startswith(SUMMARY_KICK_PREFIX):
                    continue          # 契约 §4.4：summary 踢令永不渲染
                if text:
                    self._add_row(UserBubble(text), "right")
            elif role == "assistant":
                self._render_history_assistant(msg)
            elif role == "toolResult":
                card = ToolCard(msg.get("toolCallId", ""), msg.get("toolName", "tool"))
                text = "".join(c.get("text", "") for c in msg.get("content", [])
                               if c.get("type") == "text")
                card.mark_done(text, bool(msg.get("isError")))
                self._add_row(card, "left")
        QTimer.singleShot(0, self._scroll_bottom)

    def _render_history_assistant(self, msg: dict) -> None:
        if msg.get("stopReason") == "error":
            self._add_row(
                ErrorBanner(msg.get("errorMessage") or "引擎错误", retryable=False), "left")
            return
        text = "".join(c.get("text", "") for c in msg.get("content", [])
                       if c.get("type") == "text")
        if not text:
            return
        answer = parse_paired(text, "answer")
        summary = parse_paired(text, "summary")
        if summary:
            bubble = AssistantBubble("summary")
            bubble.set_text(summary)
            # 结论后置（v0.1.4 §1a）：历史按时间序渲染，结论落在本轮详答之后
            self._add_row(bubble, "left")
        elif answer:
            bubble = AssistantBubble("answer")
            bubble.set_text(answer)
            self._add_row(bubble, "left")
        elif text.strip():
            bubble = AssistantBubble("answer")
            bubble.set_text(strip_tags(text))
            self._add_row(bubble, "left")

    def _clear_flow(self) -> None:
        self._thinking_row = self._status_row = self._stream_row = None
        self._confirm = None
        self._tool_cards.clear()
        while self.flow.count() > 1:
            item = self.flow.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    # ── P4：双入口镜像 / supervisor 重启联动 ─────────────────────

    def _on_foreign_turn_end(self, ev: dict) -> None:
        """气泡入口的回合结束（agent_end 且非我方回合）→ 可见则镜像历史；泄流排队。"""
        if ev.get("type") != "agent_end" or self.ctrl.busy:
            return
        if self.isVisible() and self.client.alive:
            self._rpc(self.client.get_state, self._on_state)      # 侧栏/会话同步
            self._rpc(self.client.get_messages, self._render_history)
        QTimer.singleShot(0, self._drain_queue)

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        # P4：窗口重新可见时同步最新会话（气泡入口可能已推进历史/切会话）
        if self._supervisor is not None and self.client.alive and not self.ctrl.busy:
            self._rpc(self.client.get_state, self._on_state)
            self._rpc(self.client.get_messages, self._render_history)

    def _on_sup_restarting(self, attempt: int) -> None:
        self.sidebar.set_status(f"引擎重启中（第 {attempt} 次）…")

    def _on_sup_restarted(self) -> None:
        self._engine_crashed = False
        self._make_controller()     # 旧 controller 可能卡在中途相位，重建并重注册
        self._sessions.clear()
        self._current_path = None
        self._clear_flow()
        self._add_row(StatusBubble("引擎已自动重启 ✓ 会话已恢复", "notice"), "left")
        self._rpc(self.client.get_state, self._on_state)

    def _on_sup_restart_failed(self) -> None:
        banner = ErrorBanner("引擎连续重启失败。请检查设置（API Key / 模型）后点「重试」。",
                             retryable=True)
        banner.retry.connect(self._supervisor.restart_now)
        self._add_row(banner, "left")
        self.sidebar.set_status("引擎重启失败")

    # ── 崩溃路径（契约 §1.3 / §6：不白屏）───────────────────────

    def _on_crash(self, code: int) -> None:
        self._engine_crashed = True
        self._drop_thinking()
        self._drop_status()
        self._queue.clear()
        if self._supervisor is not None:
            banner = ErrorBanner(f"引擎已退出（代码 {code}），自动重启中…", retryable=False)
        else:
            banner = ErrorBanner(f"引擎已退出（代码 {code}）。点「重试」重启引擎。", retryable=True)
            banner.retry.connect(self._restart_engine)
        self._add_row(banner, "left")
        self.sidebar.set_status("引擎已退出")

    def _restart_engine(self) -> None:
        if self._supervisor is not None:
            self._supervisor.restart_now()   # P4：重启收口到 supervisor
            return
        try:
            self.client.start()
        except Exception as exc:  # noqa: BLE001
            self._add_row(ErrorBanner(f"引擎重启失败：{exc}", retryable=True), "left")
            return
        self._engine_crashed = False
        self._make_controller()     # 旧 controller 可能卡在中途相位，重建
        self._sessions.clear()
        self._current_path = None
        self._clear_flow()
        self._add_row(StatusBubble("引擎已重启，新会话已就绪", "notice"), "left")
        self._rpc(self.client.get_state, self._on_state)
