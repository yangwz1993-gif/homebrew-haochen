"""haochen 完整对话窗口（M-C，visual-spec §6.2）。

布局：左侧会话栏（240px，高亮/+新会话/重命名/删除）+ 右侧动态对话流
（用户气泡居右、haochen 居左、最大宽 ~72%、逐条淡入 180ms）。

数据流：
- 单回合分层结果（brief + detail）走共享的 ConversationController（只订阅信号）；
- 工具卡 / 读屏确认条直接订阅 EngineClient.event；
- 命令响应按 id 关联（rpc-contract §1.2），_rpc() 挂回调。

嵌入方式：ChatWindow(client=None) — 不传 client 则自建（HAOCHEN_MOCK=1 走 mock）。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from PyQt6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QRect,
    Qt,
    QTimer,
    pyqtSignal,
    pyqtSlot,
)
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

from ..a11y import screen_of
from ..app_tracking import publish_question_target, write_last_user_text
from ..conversation import (
    SUMMARY_KICK_PREFIX,
    ConversationController,
    humanize_error,
    make_session_title,
    parse_paired,
    parse_turn_result,
    same_visible_text,
    sanitize_runtime_details,
    strip_tags,
    visible_user_text,
)
from ..engine_client import EngineClient, delete_session, list_sessions, restore_session
from ..reading_status import ReadingStatus, event_phase
from ..secure_storage import atomic_write_private
from ..session_coordinator import QueueItem, SessionCoordinator
from .chrome import ConversationHeader, configure_native_chrome
from .placement import detail_rect
from .sidebar import SessionSidebar
from .theme import FONT, RADIUS_INPUT, C, button_outline, button_solid
from .widgets import (
    ActionBanner,
    AssistantBubble,
    BubbleRow,
    ConfirmBar,
    ErrorBanner,
    QueueIndicator,
    QueueRecoveryBanner,
    StatusBubble,
    ToolCard,
    UserBubble,
)

log = logging.getLogger("haochen.chat.window")


class _InputBox(QPlainTextEdit):
    """回车发送 / ⌘回车换行（interaction-spec §3）。"""

    def __init__(self, on_send, parent=None):
        super().__init__(parent)
        self._on_send = on_send
        self.setPlaceholderText("和 haochen 说点什么…（⏎ 发送，⌘⏎ 换行）")
        self.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {C['surface']};
                border: 1px solid {C['line_soft']};
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
            # Qt maps the macOS Command key to ControlModifier; accept Meta as
            # well so the contract remains stable on every platform/backend.
            if ev.modifiers() & (
                Qt.KeyboardModifier.ControlModifier
                | Qt.KeyboardModifier.MetaModifier
                | Qt.KeyboardModifier.ShiftModifier
            ):
                self.insertPlainText("\n")
            else:
                self._on_send()
            return
        super().keyPressEvent(ev)


class ChatWindow(QWidget):
    """完整对话窗口。P4 集成：win = ChatWindow(engine_client) 后 show() 即可。

    从气泡展开时在人物周围的可用空间中以固定尺寸淡入；Esc / ⌘W / closeEvent
    仅收起窗口（detail_collapsed 通知 pet 侧恢复），不移动人物、不退出 app。
    """

    STREAM_THROTTLE_MS = 50          # interaction-spec §3：流式重绘节流
    SCROLL_FOLLOW_THRESHOLD = 24     # 距底小于该像素视为“用户在底部”

    detail_collapsed = pyqtSignal()   # 详情模式收起动画播完、窗口已隐藏
    normal_closed = pyqtSignal()      # 普通完整对话窗关闭，壳层恢复桌宠
    read_permission_requested = pyqtSignal()  # 用户明确同意后才请求系统读屏权限

    def __init__(self, client: EngineClient | None = None, supervisor=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("haochen")
        if QApplication.platformName() == "cocoa":
            self.setWindowFlags(self.windowFlags() | Qt.WindowType.ExpandedClientAreaHint
                                | Qt.WindowType.NoTitleBarBackgroundHint)
            # Our header reserves the actual traffic-light area itself.
            self.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)
            self.setAttribute(Qt.WidgetAttribute.WA_LayoutOnEntireRect, True)
        self.resize(980, 680)
        self.setMinimumSize(820, 560)
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
        self._accept_pending_empty_session = False
        self.coordinator = (
            supervisor.coordinator if supervisor is not None else SessionCoordinator(self.client.home, parent=self)
        )
        self._queue_requests: dict[str, str] = {}  # request id -> queue item id
        self._queue_banners: dict[str, QueueRecoveryBanner] = {}
        self._queue_indicators: dict[str, tuple[QueueIndicator, BubbleRow]] = {}
        self._last_user_text = ""
        self._stream_row: BubbleRow | None = None
        self._stream_buf = ""
        self._stream_dirty = False      # 节流窗口内已有新增量待渲染
        self._pending_answer = ""       # brief 到达后再按“结论→详情”落位
        self._turn_aborted = False       # 中止回合绝不能伪装成正常完成答案
        self._stream_timer: QTimer | None = None
        # Use window-owned timers for deferred UI work. Static singleShot callbacks
        # can outlive a test/window and call into an already deleted Qt object.
        self._deferred_timers: set[QTimer] = set()
        self._deferred_callbacks: dict[QTimer, callable] = {}
        self._follow_stream = True      # 用户是否在底部（决定是否自动跟随）
        self._thinking_row: BubbleRow | None = None
        self._status_row: BubbleRow | None = None   # 提炼结论等轻状态
        self._tool_cards: dict[str, ToolCard] = {}
        self._confirm: tuple[str, BubbleRow] | None = None  # (req_id, row)
        self._engine_crashed = False

        # ── 详情模式（从气泡展开）状态 ──
        self._detail_mode = False
        self._detail_collapsing = False
        self._detail_source_rect = QRect()
        self._detail_pet_rect = QRect()
        self._detail_bookmarks: dict[tuple[str | None, str], int] = {}
        self._detail_restore_scroll: int | None = None
        self._detail_anchor_text = ""
        self._detail_anchor_row = None
        self._detail_position_pending = False
        self._anchor_settle = QTimer(self)
        self._anchor_settle.setSingleShot(True)
        self._anchor_settle.setInterval(120)
        self._anchor_settle.timeout.connect(self._finish_detail_position)
        self._normal_geometry = QRect()
        self._geom_anim: QParallelAnimationGroup | None = None

        self._build_ui()
        self._reload_persisted_sessions()
        self._wire()
        self._sync_queue_banners()
        self._restore_geometry()
        self._remember_normal_geometry()

        # ⌘W：详情模式 = 收起；正常模式不拦截（行为不变）
        sc = QShortcut(QKeySequence.StandardKey.Close, self)
        sc.setContext(Qt.ShortcutContext.WindowShortcut)
        sc.activated.connect(self._on_close_shortcut)

    # ── UI 骨架 ────────────────────────────────────────────────

    # ── 窗口几何记忆（task-4c）─────────────────────────────────

    def _geometry_file(self) -> Path:
        return self.client.home / "chat-window-geometry.json"

    def _restore_geometry(self) -> None:
        """恢复上次尺寸/位置；不存在或不合法则用默认。"""
        try:
            data = json.loads(self._geometry_file().read_text(encoding="utf-8"))
            x, y = int(data["x"]), int(data["y"])
            w, h = int(data["width"]), int(data["height"])
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return
        if w < self.minimumWidth() or h < self.minimumHeight():
            return
        rect = QRect(x, y, w, h)
        if not any(s.availableGeometry().intersects(rect) for s in QApplication.screens()):
            return  # 屏幕布局变了（如拔掉副屏）：回到默认位置
        self.setGeometry(x, y, w, h)

    def _save_geometry(self) -> None:
        """关闭时保存当前几何（0600，原子写）。"""
        geo = self.geometry()
        self._normal_geometry = QRect(geo)
        try:
            atomic_write_private(
                self._geometry_file(),
                json.dumps(
                    {"x": geo.x(), "y": geo.y(), "width": geo.width(), "height": geo.height()},
                    separators=(",", ":"),
                )
                + "\n",
            )
        except (OSError, ValueError) as exc:  # noqa: BLE001 — 记不住尺寸不阻断关闭
            log.warning("save chat geometry failed: %s", exc)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.detail_header = ConversationHeader()
        self.detail_title = self.detail_header.title
        self.detail_close_button = self.detail_header.collapse
        self.detail_close_button.clicked.connect(self.collapse_detail)
        self.detail_close_button.hide()
        root.addWidget(self.detail_header)
        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        root.addLayout(content, 1)
        self.sidebar = SessionSidebar()
        content.addWidget(self.sidebar)

        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(0, 0, 0, 10)
        rlay.setSpacing(0)
        content.addWidget(right, 1)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { border: none; }")
        bar = self.scroll.verticalScrollBar()
        bar.valueChanged.connect(self._on_scroll_moved)
        # 内容高度变化（流式增长）时，若用户在底部则继续跟随。
        bar.rangeChanged.connect(self._on_range_changed)
        self.flow_host = QWidget()
        self.flow = QVBoxLayout(self.flow_host)
        self.flow.setContentsMargins(0, 12, 0, 12)
        self.flow.setSpacing(6)
        # Standard reading order: short conversations begin at the top. Keep
        # any unused space *after* the messages so people do not have to scan
        # an empty screen before finding the current answer at the bottom.
        self.flow.addStretch(1)
        self.scroll.setWidget(self.flow_host)
        rlay.addWidget(self.scroll, 1)

        self.jump_to_latest_button = QPushButton("↓ 回到最新")
        self.jump_to_latest_button.setAccessibleName("回到最新")
        self.jump_to_latest_button.setStyleSheet(button_outline())
        self.jump_to_latest_button.clicked.connect(self._jump_to_latest)
        self.jump_to_latest_button.hide()
        jump_row = QHBoxLayout()
        self._jump_row = jump_row
        jump_row.setContentsMargins(16, 0, 16, 0)
        jump_row.addStretch(1)
        jump_row.addWidget(self.jump_to_latest_button)
        rlay.addLayout(jump_row)

        input_bar = QHBoxLayout()
        self._input_bar = input_bar
        input_bar.setContentsMargins(16, 8, 16, 0)
        input_bar.setSpacing(8)
        self.input = _InputBox(self._on_send)
        input_bar.addWidget(self.input, 1)
        self.btn_send = QPushButton("发送")
        self.btn_send.setAccessibleName("发送")
        self.btn_send.setFixedWidth(92)
        self.btn_send.setStyleSheet(button_solid())
        self.btn_send.clicked.connect(self._on_send)
        input_bar.addWidget(self.btn_send)
        self.btn_stop = QPushButton("■ 停止")
        self.btn_stop.setAccessibleName("停止生成")
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
        self.coordinator.queue_changed.connect(self._on_queue_changed)
        if self._supervisor is not None:
            # P4：崩溃/重启由 supervisor 统一编排；镜像/泄流挂钩总线
            sup = self._supervisor
            sup.crashed.connect(self._on_crash)
            sup.restarting.connect(self._on_sup_restarting)
            sup.restarted.connect(self._on_sup_restarted)
            sup.restart_failed.connect(self._on_sup_restart_failed)
            sup.state_changed.connect(self._on_supervisor_state)
            self.client.event.connect(self._on_foreign_turn_end)
        else:
            self.client.crashed.connect(self._on_crash)

    def _make_controller(self) -> None:
        old = getattr(self, "ctrl", None)
        if old is not None:
            try:
                self.client.event.disconnect(old._on_event)
                self.client.response.disconnect(old._on_response)
                self.client.crashed.disconnect(old._on_crashed)
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
        self.ctrl.request_committed.connect(self._on_request_committed)
        self.ctrl.request_failed.connect(self._on_request_failed)
        self.ctrl.turn_aborted.connect(self._on_turn_aborted)
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
        self.sidebar.set_model(model)
        name = data.get("sessionName") or "新会话"
        if self._is_transient_startup_session(path, name):
            return
        if path:
            self._accept_pending_empty_session = False
        if path:
            self.coordinator.set_current_session(path)
        if path:
            record = next((s for s in self._sessions if s["path"] == path), None)
            if record is None:
                self._sessions.insert(0, {"path": path, "title": name})
            elif record["title"] == "新会话" and name != "新会话":
                record["title"] = name
            changed_session = path != self._current_path
            self._current_path = path
            self._refresh_sidebar()
            self._update_detail_title()
            if changed_session:
                self._detail_restore_scroll = None
                self._detail_anchor_text = ""
                if self._detail_mode:
                    self._detail_position_pending = True
                self._rpc(self.client.get_messages, self._render_history)

    def _is_transient_startup_session(self, path: str, name: str) -> bool:
        """Ignore the engine's prospective empty file while restoring durable history."""
        if self._mock or self._accept_pending_empty_session or not path or name != "新会话":
            return False
        saved = self.coordinator.current_session
        return bool(
            saved
            and saved != path
            and Path(saved).is_file()
            and not Path(path).exists()
        )

    def _reload_persisted_sessions(self) -> None:
        """Rebuild the sidebar from durable JSONL files after app/engine restart."""
        if self._mock:
            return
        records = []
        for session in list_sessions(self.client.home):
            preview = str(session.get("preview") or "").strip()
            title = str(session.get("title") or "").strip()
            if not title and preview:
                title = make_session_title(preview)
            records.append({
                "path": str(session["path"]),
                "title": title or "新会话",
            })
        self._sessions = records
        if hasattr(self, "sidebar"):
            self._refresh_sidebar()

    def _on_supervisor_state(self, data: dict) -> None:
        """Keep hidden-window session titles in sync with pet-originated turns."""
        self._on_state({"success": True, "data": data})

    # ── 对话流渲染 ─────────────────────────────────────────────

    def _bubble_max_w(self) -> int:
        content_width = min(920, max(320, self.scroll.viewport().width()))
        return min(680, int(content_width * 0.72))

    def _apply_responsive_margins(self) -> None:
        """Keep conversation and composer on one centered reading column on wide screens."""
        side = max(0, (self.scroll.viewport().width() - 920) // 2)
        self.flow.setContentsMargins(side, 12, side, 12)
        self._jump_row.setContentsMargins(side + 16, 0, side + 16, 0)
        self._input_bar.setContentsMargins(side + 16, 8, side + 16, 0)

    def _add_row(self, content: QWidget, align: str, before: QWidget | None = None) -> BubbleRow:
        row = BubbleRow(content, align)
        row.set_max_content_width(self._bubble_max_w())
        if before is None:
            self.flow.insertWidget(self.flow.count() - 1, row)
        else:
            self.flow.insertWidget(self.flow.indexOf(before), row)
        if not self._detail_mode:
            self._defer(0, self._scroll_bottom)
        return row

    def _defer(self, delay_ms: int, callback) -> None:
        """Run UI work later, but cancel it automatically with this window."""
        timer = QTimer(self)
        timer.setSingleShot(True)
        self._deferred_timers.add(timer)
        self._deferred_callbacks[timer] = callback
        # A Qt receiver-owned slot disconnects safely with the window. Avoid a
        # self-referential timer/closure surviving Python garbage collection.
        timer.timeout.connect(self._run_deferred)
        timer.start(delay_ms)

    @pyqtSlot()
    def _run_deferred(self) -> None:
        timer = self.sender()
        callback = self._deferred_callbacks.pop(timer, None)
        self._deferred_timers.discard(timer)
        if timer is not None:
            timer.deleteLater()
        if callback is not None:
            callback()

    def _cancel_deferred_work(self) -> None:
        self._anchor_settle.stop()
        self._detail_position_pending = False
        for timer in self._deferred_timers:
            timer.stop()
            timer.deleteLater()
        self._deferred_timers.clear()
        self._deferred_callbacks.clear()

    def _scroll_bottom(self) -> None:
        sb = self.scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _scroll_top(self) -> None:
        """Start an opened detail at the conversation's beginning, never its tail."""
        sb = self.scroll.verticalScrollBar()
        sb.setValue(sb.minimum())
        # valueChanged sees min==max as "at bottom" for a not-yet-laid-out flow.
        # Pin this after the write so later range changes cannot pull detail down.
        self._follow_stream = False
        self.jump_to_latest_button.hide()

    def _scroll_current_turn(self) -> None:
        if not self._detail_mode or self._detail_collapsing:
            return
        self._follow_stream = False
        if self._detail_restore_scroll is not None:
            self.scroll.verticalScrollBar().setValue(self._detail_restore_scroll)
            return
        row = self._detail_anchor_row
        if row is not None:
            try:
                y = row.mapTo(self.scroll.widget(), row.rect().topLeft()).y()
                self.scroll.verticalScrollBar().setValue(max(0, y - 12))
            except RuntimeError:
                pass
        else:
            sb = self.scroll.verticalScrollBar()
            sb.setValue(sb.maximum())
        self._follow_stream = False

    def _finish_detail_position(self) -> None:
        if self._detail_mode and self._detail_position_pending:
            # A busy event loop can deliver this timer before QScrollArea has
            # applied the newly inserted history's layout. Do not end anchoring
            # while overflowing content still has a zero scroll range: the later
            # rangeChanged event must be allowed to position the current turn.
            if (self.scroll.verticalScrollBar().maximum() == 0
                    and self.flow.sizeHint().height() > self.scroll.viewport().height()):
                self._anchor_settle.start()
                return
            self._scroll_current_turn()
        self._detail_position_pending = False

    def _on_scroll_moved(self) -> None:
        """用户主动滚动后重估“是否在底部”；离开底部即停止强制跟随。"""
        if self._detail_position_pending:
            return
        sb = self.scroll.verticalScrollBar()
        at_bottom = sb.maximum() - sb.value() <= self.SCROLL_FOLLOW_THRESHOLD
        was_following = self._follow_stream
        self._follow_stream = at_bottom
        self.jump_to_latest_button.setVisible(
            not at_bottom and (self._stream_row is not None or bool(self._tool_cards))
        )
        if not at_bottom and was_following and self._stream_timer is not None:
            return  # 保持当前节流状态，只是不再自动滚底

    def _jump_to_latest(self) -> None:
        self._follow_stream = True
        self.jump_to_latest_button.hide()
        self._scroll_bottom()

    def _maybe_follow(self) -> None:
        if self._follow_stream:
            # rangeChanged 会在布局完成后触发 _on_range_changed 完成跟随；
            # 这里再补一次同步滚动，覆盖“高度未变但内容变了”的场景。
            self._defer(0, self._scroll_bottom)

    def _on_range_changed(self) -> None:
        if self._detail_mode and self._detail_position_pending:
            self._defer(0, self._scroll_current_turn)
            self._anchor_settle.start()
            return
        if self._follow_stream:
            sb = self.scroll.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _drop_row(self, row: BubbleRow | None) -> None:
        if row is None:
            return
        self.flow.removeWidget(row)
        row.setParent(None)
        row.deleteLater()

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._apply_responsive_margins()
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
        self.coordinator.enqueue(text, "chat")
        self._drain_queue()

    @property
    def _queue(self) -> list[str]:
        """Compatibility view for existing verification scripts."""
        return self.coordinator.texts("chat")

    def _send_queued_item(self, item: QueueItem) -> None:
        self._turn_aborted = False
        self._last_user_text = item.text
        # 仅落盘派生的看图意图 boolean，绝不持久化用户原文
        write_last_user_text(self.client.home, item.text)
        publish_question_target(self.client.home, item, self.coordinator.current_session)
        self._auto_title(item.text)
        request_id = self.ctrl.send(item.text)
        if request_id is None:
            self.coordinator.hold_item_for_review(item.id)
            return
        self._queue_requests[request_id] = item.id
        self.coordinator.mark_inflight(item.id, request_id)

    def _on_stop(self) -> None:
        if self._confirm:
            self._answer_confirm(None)        # Esc/停止时取消确认（契约 §5.3 cancelled）
        if self.ctrl.busy:
            self._turn_aborted = True
        self.ctrl.abort()

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key.Key_Escape:
            if self._detail_mode:
                # 详情模式 Esc = 收起（优先于打断生成）；正常打开时行为不变
                self.collapse_detail()
            else:
                self.close()
            return
        super().keyPressEvent(ev)

    def _on_close_shortcut(self) -> None:
        """Close both entry modes; hiding a window does not cancel its task."""
        if self._detail_mode:
            self.collapse_detail()
        else:
            self.close()

    def closeEvent(self, ev) -> None:
        # 详情模式下系统级关闭（⌘W/Mission Control 等）也走收起，绝不退出 app
        if self._detail_mode:
            ev.ignore()
            self.collapse_detail()
            return
        self._save_geometry()
        self._cancel_deferred_work()
        ev.ignore()
        self.hide()
        self.normal_closed.emit()

    # ── 详情模式：从气泡展开 / 收回气泡 ─────────────────────────

    def open_from_bubble(self, source_rect: QRect | None = None, anchor_text: str = "",
                         pet_rect: QRect | None = None) -> None:
        """Open beside the stationary character; lay out at final size before fading in."""
        self._remember_normal_geometry()
        self._detail_mode = True
        self._detail_collapsing = False
        self._follow_stream = False
        self._detail_source_rect = source_rect or QRect()
        self._detail_pet_rect = pet_rect or QRect()
        self._detail_anchor_text = anchor_text
        self._detail_restore_scroll = self._detail_bookmarks.get((self._current_path, anchor_text))
        self._detail_position_pending = True
        self._anchor_settle.start(350)
        self.sidebar.hide()
        self.detail_close_button.show()
        self._update_detail_title()
        self.input.setPlaceholderText("继续这个话题…（⏎ 发送，⌘⏎ 换行）")
        target = self._detail_target_rect()
        self._stop_transition()
        self.setMinimumSize(min(420, target.width()), min(300, target.height()))
        self.setGeometry(target)
        self.layout().activate()
        self._animate_detail(True)
        self.raise_()
        self.activateWindow()
        # Restore the current turn (or its saved reading offset) after layout.
        self._defer(0, self._scroll_current_turn)
        self._defer(300, self._scroll_current_turn)
        self.input.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _restore_min_size(self) -> None:
        if self._detail_mode and not self._detail_collapsing:
            self.setMinimumSize(min(420, self.width()), min(300, self.height()))

    def collapse_detail(self) -> None:
        """Esc / ⌘W: remember reading position and gently dismiss at fixed size."""
        if not self._detail_mode or self._detail_collapsing:
            return
        self._detail_collapsing = True
        self._detail_bookmarks[(self._current_path, self._detail_anchor_text)] = self.scroll.verticalScrollBar().value()
        self._animate_detail(False, self._after_detail_collapse)

    def _after_detail_collapse(self) -> None:
        self._cancel_deferred_work()
        self._detail_mode = False
        self._detail_collapsing = False
        self.hide()
        self.setWindowOpacity(1)
        self.sidebar.show()
        self.detail_close_button.hide()
        self._update_detail_title()
        self.input.setPlaceholderText("和 haochen 说点什么…（⏎ 发送，⌘⏎ 换行）")
        self.setMinimumSize(820, 560)
        self.setGeometry(self._normal_target_rect())
        self.detail_collapsed.emit()

    def _remember_normal_geometry(self) -> None:
        """Keep detail animations and their tiny source rect out of normal-window state."""
        geo = self.geometry()
        if (not self._detail_mode and geo.width() >= 820 and geo.height() >= 560
                and any(s.availableGeometry().intersects(geo) for s in QApplication.screens())):
            self._normal_geometry = QRect(geo)

    def _normal_target_rect(self) -> QRect:
        if (self._normal_geometry.isValid()
                and self._normal_geometry.width() >= 820
                and self._normal_geometry.height() >= 560
                and any(s.availableGeometry().intersects(self._normal_geometry)
                        for s in QApplication.screens())):
            return QRect(self._normal_geometry)
        screen = screen_of(self).availableGeometry()
        w = min(980, max(820, screen.width() - 32))
        h = min(680, max(560, screen.height() - 32))
        return QRect(
            screen.center().x() - w // 2,
            screen.center().y() - h // 2,
            w,
            h,
        )

    def show_normal(self) -> None:
        """Open the full workspace with a sane geometry after any detail animation."""
        self._stop_transition()
        self.setWindowOpacity(1)
        self._detail_mode = False
        self._detail_collapsing = False
        self._follow_stream = True
        self.sidebar.show()
        self.detail_close_button.hide()
        self._update_detail_title()
        self.input.setPlaceholderText("和 haochen 说点什么…（⏎ 发送，⌘⏎ 换行）")
        self.setMinimumSize(820, 560)
        self.setGeometry(self._normal_target_rect())
        self.show()
        self.raise_()
        self.activateWindow()

    def _detail_target_rect(self) -> QRect:
        anchor = self._detail_pet_rect if self._detail_pet_rect.isValid() else self._detail_source_rect
        screen = QApplication.screenAt(anchor.center()) if anchor.isValid() else None
        screen = screen or screen_of(self)
        return detail_rect(screen.availableGeometry(), self._detail_pet_rect, self._detail_source_rect)

    def _stop_transition(self) -> None:
        old, self._geom_anim = self._geom_anim, None
        if old is not None:
            try:
                old.stop()
                old.deleteLater()
            except RuntimeError:
                pass

    def _animate_detail(self, opening: bool, finished=None) -> None:
        from ..a11y import reduce_motion_enabled
        self._stop_transition()
        if reduce_motion_enabled():
            self.setWindowOpacity(1)
            if opening:
                self.show()
            if finished is not None:
                finished()
            return
        target = self.pos()
        dy = 8 if self._detail_source_rect.center().y() >= self.geometry().center().y() else -8
        offset = QPoint(0, dy)
        anim = QParallelAnimationGroup(self)
        position = QPropertyAnimation(self, b"pos", anim)
        position.setStartValue(target + offset if opening else target)
        position.setEndValue(target if opening else target + offset)
        position.setDuration(180 if opening else 130)
        position.setEasingCurve(QEasingCurve.Type.OutCubic)
        opacity = QPropertyAnimation(self, b"windowOpacity", anim)
        opacity.setStartValue(0.0 if opening else self.windowOpacity())
        opacity.setEndValue(1.0 if opening else 0.0)
        opacity.setDuration(180 if opening else 130)
        anim.addAnimation(position)
        anim.addAnimation(opacity)
        if finished is not None:
            anim.finished.connect(finished)
        self._geom_anim = anim
        if opening:
            self.setWindowOpacity(0)
            self.move(target + offset)
            self.show()
        anim.start()

    def _auto_title(self, text: str) -> None:
        """首条用户消息自动命名会话（§6 标题自动/可重命名）。"""
        for s in self._sessions:
            if s["path"] == self._current_path and s["title"] == "新会话":
                title = make_session_title(text)
                s["title"] = title
                self.sidebar.update_title(s["path"], title)
                self._update_detail_title()
                self._rpc(self.client.set_session_name, lambda r: None, title)
                return

    def _update_detail_title(self) -> None:
        title = next(
            (
                str(session.get("title") or "")
                for session in self._sessions
                if session.get("path") == self._current_path
            ),
            "",
        )
        text = title if title and title != "新会话" else "会话详情"
        if text.startswith("你好，请用一句话介绍"):
            text = "会话详情"
        self.detail_header.set_title(text if self._detail_mode else "haochen")
        if title:
            self.detail_title.setToolTip(f"会话：{title}")

    # ── ConversationController 信号（单回合分层结果）────────────

    def _on_busy_changed(self, busy: bool) -> None:
        self.btn_stop.setVisible(busy)
        self.btn_send.setVisible(not busy)
        if busy:
            self._thinking_row = self._add_row(StatusBubble("haochen 正在想", "thinking"), "left")
        else:
            # 回合结束仍未收到 end 事件的工具卡 → 视为已取消（如被 abort）。
            for card in self._tool_cards.values():
                if card.status.text() == "运行中…":
                    card.mark_cancelled()
            self._tool_cards.clear()
            # busy(False) 先于 summary_done/failed 同步发出，推迟一拍让结论先落位
            self._defer(0, self._drain_queue)

    def _drain_queue(self) -> None:
        if self.ctrl.busy or self._engine_crashed or self._foreign_busy() or not self.client.alive:
            return
        item = self.coordinator.next_ready("chat")
        if item is not None:
            self._send_queued_item(item)

    def _on_request_committed(self, request_id: str) -> None:
        if request_id in self._queue_requests:
            self._queue_requests.pop(request_id, None)
            self.coordinator.acknowledge(request_id)

    def _on_request_failed(self, request_id: str, _error: str) -> None:
        if request_id in self._queue_requests:
            self._queue_requests.pop(request_id, None)
            self.coordinator.hold_for_review(request_id)

    def _on_queue_changed(self) -> None:
        self._sync_queue_banners()
        self._defer(0, self._drain_queue)

    def _sync_queue_banners(self) -> None:
        for item in self.coordinator.queue:
            if item.source != "chat":
                continue
            if item.needs_review:
                self._ensure_recovery_banner(item)
            elif item.request_id is None:
                self._ensure_queue_indicator(item)
        self._prune_queue_widgets()

    def _ensure_recovery_banner(self, item: QueueItem) -> None:
        if item.id in self._queue_banners:
            return
        preview = item.text if len(item.text) <= 80 else item.text[:80] + "…"
        banner = QueueRecoveryBanner(preview)
        self._queue_banners[item.id] = banner
        self._add_row(banner, "left")

        def resend(item_id=item.id, recovery_banner=banner) -> None:
            try:
                self.coordinator.retry(item_id)
            except KeyError:
                return
            recovery_banner.mark_done("已选择重新发送")

        def cancel(item_id=item.id, recovery_banner=banner) -> None:
            try:
                self.coordinator.cancel(item_id)
            except KeyError:
                return
            recovery_banner.mark_done("已取消未发送消息")

        banner.resend_requested.connect(resend)
        banner.cancel_requested.connect(cancel)

    def _ensure_queue_indicator(self, item: QueueItem) -> None:
        if item.id in self._queue_indicators or item.id in self._queue_banners:
            return
        preview = item.text if len(item.text) <= 60 else item.text[:60] + "…"
        indicator = QueueIndicator(preview)
        row = self._add_row(indicator, "left")
        self._queue_indicators[item.id] = (indicator, row)

        def cancel(item_id=item.id, ind=indicator) -> None:
            try:
                self.coordinator.cancel(item_id)
            except KeyError:
                return
            ind.mark_cancelled()

        indicator.cancel_button.clicked.connect(lambda _checked=False: cancel())

    def _prune_queue_widgets(self) -> None:
        live_ids = {item.id for item in self.coordinator.queue if item.source == "chat"}
        for item_id in list(self._queue_indicators):
            if item_id not in live_ids:
                indicator, row = self._queue_indicators.pop(item_id)
                self._drop_row(row)
        for item_id in list(self._queue_banners):
            if item_id not in live_ids:
                self._queue_banners.pop(item_id)
                # banner 由恢复流程 mark_done 收尾，这里只解除登记。

    def _on_answer_delta(self, delta: str) -> None:
        self._drop_thinking()
        self._drop_status()
        if self._stream_row is None:
            bubble = AssistantBubble("answer")
            self._stream_row = self._add_row(bubble, "left")
            self._stream_buf = ""
        self._stream_buf += delta
        self._stream_dirty = True
        if self._stream_timer is None:
            self._stream_timer = QTimer(self)
            self._stream_timer.setSingleShot(True)
            self._stream_timer.timeout.connect(self._flush_stream)
            self._stream_timer.start(self.STREAM_THROTTLE_MS)
        self._maybe_follow()

    def _flush_stream(self) -> None:
        """节流窗口到期：把累计缓冲真正渲染一次；无增量则不发定时器。"""
        if self._stream_timer is not None:
            self._stream_timer.stop()
            self._stream_timer = None
        if self._stream_row is None:
            return
        if self._stream_dirty:
            self._stream_row.content.append_stream(self._stream_buf)
            self._stream_dirty = False
            self._maybe_follow()

    def _on_answer_done(self, answer: str) -> None:
        self._drop_thinking()
        self._flush_stream()
        self._pending_answer = answer
        if self._stream_row is not None:
            # 流式内容先保持可见；brief 到达后再重排为“结论在前、详情在后”。
            self._stream_row.content.set_text(answer)
        self._defer(0, self._maybe_follow)

    def _on_turn_aborted(self, _partial: str) -> None:
        self._turn_aborted = True

    def _on_summarizing(self) -> None:
        self._status_row = self._add_row(StatusBubble("正在提炼结论", "thinking"), "left")

    def _on_summary_done(self, summary: str) -> None:
        self._drop_thinking()
        self._drop_status()
        if self._stream_row is not None:
            self._drop_row(self._stream_row)
            self._stream_row = None
        self._stream_buf = ""
        self._stream_dirty = False
        if self._stream_timer is not None:
            self._stream_timer.stop()
            self._stream_timer = None
        if self._turn_aborted:
            self._add_row(
                StatusBubble("已停止生成，以下是停止前的未完成内容", "warn"), "left"
            )
            partial = self._pending_answer.strip() or summary.strip()
            if partial:
                bubble = AssistantBubble("partial")
                bubble.set_text(partial)
                self._add_row(bubble, "left")
                self._add_row(StatusBubble("已停止生成 · 上述内容未完成", "warn"), "left")
            self._pending_answer = ""
            self._turn_aborted = False
            self._defer(0, self._maybe_follow)
            return
        if summary:
            bubble = AssistantBubble("summary")
            bubble.set_text(summary)
            self._add_row(bubble, "left")
        if self._pending_answer and not same_visible_text(self._pending_answer, summary):
            detail = AssistantBubble("answer")
            detail.set_text(self._pending_answer)
            self._add_row(detail, "left")
        self._pending_answer = ""
        self._defer(0, self._maybe_follow)

    def _on_failed(self, err: str) -> None:
        self._drop_thinking()
        self._drop_status()
        self._stream_row = None
        self._stream_buf = ""
        self._stream_dirty = False
        self._pending_answer = ""
        self._turn_aborted = False
        if self._stream_timer is not None:
            self._stream_timer.stop()
            self._stream_timer = None
        banner = ErrorBanner(humanize_error(err))
        banner.retry.connect(self._retry_last_message)
        self._add_row(banner, "left")

    def _retry_last_message(self) -> None:
        pending = next(
            (
                item
                for item in self.coordinator.queue
                if item.source == "chat" and item.needs_review and item.text == self._last_user_text
            ),
            None,
        )
        if pending is not None:
            self.coordinator.retry(pending.id)
        elif self._last_user_text:
            self.coordinator.enqueue(self._last_user_text, "chat")

    def _open_artifact(self, path: Path) -> None:
        """用系统默认程序打开工具产物（write/edit 的文件）。"""
        if not path.is_file():
            self._add_row(ErrorBanner(f"产物不存在：{path}", retryable=False), "left")
            return
        from PyQt6.QtGui import QDesktopServices, QUrl
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

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
                self._reading_status = ReadingStatus()
                self._reading_tool_id = ev.get("toolCallId", "")
                self._add_row(self._reading_status, "left")
            card = ToolCard(ev.get("toolCallId", ""), name, ev.get("args") or {})
            card.start_clock()
            # 运行中可取消（中止回合）；失败可重试；产物可打开。
            card.cancel_button.clicked.connect(lambda _checked=False: self._on_stop())
            card.retry_button.clicked.connect(lambda _checked=False: self._retry_last_message())
            artifact = card.artifact_path()
            if artifact is not None:
                card.open_button.clicked.connect(
                    lambda _checked=False, path=artifact: self._open_artifact(path))
            self._tool_cards[card.tool_call_id] = card
            self._add_row(card, "left")
        elif t == "tool_execution_end":
            card = self._tool_cards.pop(ev.get("toolCallId", ""), None)
            if card:
                result = ev.get("result") or {}
                text = "".join(c.get("text", "") for c in result.get("content", [])
                               if c.get("type") == "text")
                card.mark_done(
                    text,
                    bool(ev.get("isError") or result.get("isError")),
                    card.elapsed_ms(),
                    result.get("details") or {},
                )
        elif t == "extension_ui_request":
            self._on_ui_request(ev)
        elif t == "extension_error":
            self._add_row(ErrorBanner(f"扩展错误：{ev.get('error', '')}", retryable=False), "left")
        phase = event_phase(ev)
        if phase and ev.get("toolCallId", "") == getattr(self, "_reading_tool_id", None):
            detail = ((ev.get("partialResult") or {}).get("details") or {})
            log.info("read_phase tool=%s phase=%s elapsed_ms=%s", ev.get("toolCallId", ""),
                     phase, detail.get("elapsedMs", "-"))
            from PyQt6.sip import isdeleted
            if not isdeleted(self._reading_status):
                self._reading_status.set_phase(phase)

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
            terminal_reason = "已取消"
        else:
            self.client.respond_ui(rid, confirmed=confirmed)
            note = "已授权读屏" if confirmed else "已拒绝读屏"
            kind = "perceive" if confirmed else "notice"
            terminal_reason = "" if confirmed else "已拒绝"
            if confirmed:
                self.read_permission_requested.emit()
        if terminal_reason:
            for card in reversed(list(self._tool_cards.values())):
                if card.tool_name == "read_screen":
                    card.mark_not_run(terminal_reason)
                    break
        self._drop_row(row)
        self._add_row(StatusBubble(note, kind), "left")

    # ── 会话管理（契约 §2.3–2.8）────────────────────────────────

    def _refresh_sidebar(self) -> None:
        self.sidebar.set_sessions(self._sessions, self._current_path)

    def _new_session(self) -> None:
        if self.ctrl.busy:
            return
        self._accept_pending_empty_session = True
        self._rpc(self.client.new_session, lambda r: self._rpc(self.client.get_state, self._on_state))

    def _switch_session(self, path: str) -> None:
        if path == self._current_path or self.ctrl.busy:
            return
        def done(resp: dict) -> None:
            if resp.get("success") and not (resp.get("data") or {}).get("cancelled"):
                self._current_path = path
                self.coordinator.set_current_session(path)
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
                    self.coordinator.set_current_session(path)
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
        if self._detail_mode and not self._detail_position_pending:
            self._detail_restore_scroll = self.scroll.verticalScrollBar().value()
        self._clear_flow()
        self._detail_anchor_row = None
        last_user_row = None
        messages = self._collapse_repeated_error_retries(
            (resp.get("data") or {}).get("messages") or []
        )
        for msg in messages:
            role = msg.get("role")
            if role == "user":
                text = "".join(c.get("text", "") for c in msg.get("content", [])
                               if c.get("type") == "text")
                if text.startswith(SUMMARY_KICK_PREFIX):
                    continue          # 契约 §4.4：summary 踢令永不渲染
                if text:
                    self.ctrl.clear_resume_context()
                    row = self._add_row(UserBubble(visible_user_text(text)), "right")
                    last_user_row = row
                    if visible_user_text(text).strip() == self._detail_anchor_text.strip():
                        self._detail_anchor_row = row
            elif role == "assistant":
                self._render_history_assistant(msg)
            elif role == "toolResult":
                card = ToolCard(msg.get("toolCallId", ""), msg.get("toolName", "tool"))
                text = "".join(c.get("text", "") for c in msg.get("content", [])
                               if c.get("type") == "text")
                card.mark_done(
                    text,
                    bool(msg.get("isError")),
                    details=msg.get("details") or {},
                )
                self._add_row(card, "left")
        if self._detail_mode:
            self._detail_position_pending = True
            self._anchor_settle.start(350)
            if self._detail_anchor_row is None:
                self._detail_anchor_row = last_user_row
            self._follow_stream = False
            self._defer(0, self._scroll_current_turn)
        else:
            self._follow_stream = True
            self._defer(0, self._scroll_bottom)
        self.jump_to_latest_button.hide()

    @staticmethod
    def _collapse_repeated_error_retries(messages: list[dict]) -> list[dict]:
        """Render an unchanged user/error retry pair once instead of flooding history."""
        collapsed: list[dict] = []
        previous_pair: tuple[str, str] | None = None
        index = 0
        while index < len(messages):
            current = messages[index]
            following = messages[index + 1] if index + 1 < len(messages) else None
            if current.get("role") == "user" and following and (
                    following.get("role") == "assistant"
                    and following.get("stopReason") == "error"):
                user_text = "".join(
                    part.get("text", "") for part in current.get("content", [])
                    if part.get("type") == "text"
                ).strip()
                error_text = humanize_error(
                    following.get("errorMessage") or "引擎错误"
                )
                pair = (user_text, error_text)
                if pair != previous_pair:
                    collapsed.extend((current, following))
                previous_pair = pair
                index += 2
                continue
            previous_pair = None
            collapsed.append(current)
            index += 1
        return collapsed

    def _render_history_assistant(self, msg: dict) -> None:
        if msg.get("stopReason") == "error":
            self._add_row(
                ErrorBanner(humanize_error(msg.get("errorMessage") or "引擎错误"), retryable=False), "left")
            return
        text = "".join(c.get("text", "") for c in msg.get("content", [])
                       if c.get("type") == "text")
        if msg.get("stopReason") in ("aborted", "cancelled"):
            result = parse_turn_result(text)
            partial = result.detail or result.brief or strip_tags(text)
            self._add_row(
                StatusBubble("已停止生成，以下是停止前的未完成内容", "warn"), "left"
            )
            if partial:
                bubble = AssistantBubble("partial")
                bubble.set_text(partial)
                self._add_row(bubble, "left")
                self.ctrl.remember_aborted_context(partial)
                self._add_row(StatusBubble("已停止生成 · 上述内容未完成", "warn"), "left")
            return
        if not text:
            return
        answer = parse_paired(text, "answer")
        summary = parse_paired(text, "summary")
        has_current_protocol = any(tag in text for tag in (
            "【brief】", "==brief==", "【detail】", "==detail==",
        ))
        if has_current_protocol:
            result = parse_turn_result(text)
            brief, detail = result.brief, result.detail
            if brief:
                bubble = AssistantBubble("summary")
                bubble.set_text(brief)
                self._add_row(bubble, "left")
            if detail and not same_visible_text(detail, brief):
                bubble = AssistantBubble("answer")
                bubble.set_text(detail)
                self._add_row(bubble, "left")
        elif summary:
            bubble = AssistantBubble("summary")
            bubble.set_text(sanitize_runtime_details(summary))
            self._add_row(bubble, "left")
        elif answer:
            bubble = AssistantBubble("answer")
            bubble.set_text(sanitize_runtime_details(answer))
            self._add_row(bubble, "left")
        elif text.strip():
            # A plain response is itself the visible conclusion, not hidden
            # implementation detail. The controller uses the same fallback.
            bubble = AssistantBubble("summary")
            bubble.set_text(sanitize_runtime_details(strip_tags(text)))
            self._add_row(bubble, "left")

    def _clear_flow(self) -> None:
        self._thinking_row = self._status_row = self._stream_row = None
        self._pending_answer = ""
        self._turn_aborted = False
        self.ctrl.clear_resume_context()
        self._confirm = None
        self._tool_cards.clear()
        self._queue_banners.clear()
        self._queue_indicators.clear()
        while self.flow.count() > 1:
            item = self.flow.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._defer(0, self._sync_queue_banners)

    # ── P4：双入口镜像 / supervisor 重启联动 ─────────────────────

    def _on_foreign_turn_end(self, ev: dict) -> None:
        """气泡入口的回合结束（agent_end 且非我方回合）→ 可见则镜像历史；泄流排队。"""
        if ev.get("type") != "agent_end" or self.ctrl.busy:
            return
        if self.isVisible() and self.client.alive:
            self._rpc(self.client.get_state, self._on_state)      # 侧栏/会话同步
            self._rpc(self.client.get_messages, self._render_history)
        self._defer(0, self._drain_queue)

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        configure_native_chrome(self)
        # P4：窗口重新可见时同步最新会话（气泡入口可能已推进历史/切会话）
        if self._supervisor is not None and self.client.alive and not self.ctrl.busy:
            self._rpc(self.client.get_state, self._on_state)
            self._rpc(self.client.get_messages, self._render_history)

    def _on_sup_restarting(self, attempt: int) -> None:
        if getattr(self._supervisor, "restart_reason", "recovery") == "configuration":
            return
        self.sidebar.set_status(f"引擎重启中（第 {attempt} 次）…")

    def _on_sup_restarted(self) -> None:
        if getattr(self._supervisor, "restart_reason", "recovery") == "configuration":
            self._rpc(self.client.get_state, self._on_state)
            return
        self._engine_crashed = False
        self._make_controller()     # 旧 controller 可能卡在中途相位，重建并重注册
        self._reload_persisted_sessions()
        self._current_path = None
        self._clear_flow()
        self._add_row(StatusBubble("引擎已自动重启 ✓ 会话已恢复", "notice"), "left")
        self._rpc(self.client.get_state, self._on_state)

    def _on_sup_restart_failed(self) -> None:
        if getattr(self._supervisor, "restart_reason", "recovery") == "configuration":
            return
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
        self._sync_queue_banners()
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
        self._reload_persisted_sessions()
        self._current_path = None
        self._clear_flow()
        self._add_row(StatusBubble("引擎已重启，新会话已就绪", "notice"), "left")
        self._rpc(self.client.get_state, self._on_state)
