"""PetApp：桌宠模块装配 —— L0 桌宠 + L1 气泡 + 引擎会话 + 状态机。

共享只读依赖：haochen_app.engine_client.EngineClient（传输层）、
haochen_app.conversation.ConversationController（brief + detail 单回合编排）。

P4 集成接口（给对话窗口/集成负责人）：
- 共享会话：对话窗口用**同一个 EngineClient 实例**（或同一引擎进程）构造自己的
  ConversationController 并订阅 client.event；气泡 ↔ 窗口天然共享引擎侧同一会话。
  切换/恢复会话用 client.get_messages() 拉同一份历史（rpc-contract §2.5/§7）。
- 「查看详情」：打开完整对话窗口 ChatWindow 的「从气泡展开」
  模式（open_from_bubble）。PetApp 不直接持有 chat 引用，由壳层注入
  `pet.detail_opener = chat.open_from_bubble` 并接
  `chat.detail_collapsed.connect(pet.restore_bubble)`；未注入时按钮点击为空操作。
  expand_detail_answer 信号保留但不再 emit。
- 「设置」：PetApp.settings_requested = pyqtSignal()，由壳层打开配置面板。
- 状态机：PetApp.state（PetState 枚举），state_changed = pyqtSignal(str)。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QApplication, QWidget

from ..app_tracking import write_last_user_text
from ..conversation import ConversationController, make_session_title, plain_visible_text
from ..engine_client import EngineClient
from ..secure_storage import atomic_write_private
from ..session_coordinator import QueueItem, SessionCoordinator
from . import theme as T
from .bubble import BubbleWindow
from .hotkey import HOTKEY_LABEL, install_hotkey
from .pet_window import PetWindow
from .profile import save_user_name, should_ask_name
from .state import POSE_ALERT, POSE_FOR_STATE, STATUS_LINE, PetState

log = logging.getLogger("haochen.pet")

# v0.1.7 首启问称呼：这些回复视为「跳过」（落盘空名，不再问）
_NAME_SKIP_WORDS = ("算了", "跳过", "不用了", "不用", "skip")
RESULT_AUTO_DISMISS_MS = 20_000
ACK_MIN_VISIBLE_MS = 520
DISCOVERY_HINT_MS = 8_500
PRIVACY_DECISION_MIN_VISIBLE_MS = 1_600


class PetApp(QObject):
    """桌宠 + 气泡 + 会话编排 + 显式状态机（interaction-spec §1）。"""

    state_changed = pyqtSignal(str)         # PetState.value
    expand_detail_answer = pyqtSignal(str)  # 旧 P4 接口（详情改走 detail_opener 注入，不再 emit）
    chat_requested = pyqtSignal()           # 右键「打开完整对话」
    settings_requested = pyqtSignal()       # 右键「设置…」
    credential_validation = pyqtSignal(bool, str)  # 最近一次真实请求是否证明当前凭据可用
    read_permission_requested = pyqtSignal()  # 仅用户明确点「读吧」后请求系统权限

    def __init__(self, mock: bool | None = None, client: EngineClient | None = None,
                 supervisor=None, parent=None):
        super().__init__(parent)
        self._state = PetState.IDLE
        self._confirm_id: str | None = None
        self._last_answer = ""
        self._last_summary = ""
        self._last_user_text = ""
        self._retry_attempt = 0
        self._session_needs_title = True
        self._pending_session_title = ""
        self._known_session_path = ""
        self._aborted = False
        self._status_block = None
        self._perception_hint = None
        self._queue_requests: dict[str, str] = {}
        self._queue_indicators: dict[str, object] = {}
        self._detail_open = False                       # 详情（对话窗口展开模式）打开中
        self._settings_open = False                     # 设置打开时暂停临时层，而不是丢弃上下文
        self._resume_bubble_after_settings = False
        self._discovery_hint_active = False
        self._discovery_hint_retries = 0
        self._discovery_timer = QTimer(self)
        self._discovery_timer.setSingleShot(True)
        self._discovery_timer.timeout.connect(self._hide_discovery_hint)
        # v0.1.7 首启问称呼：等待用户输入称呼中 / 本次会话已问过（避免重复问候块）
        self._awaiting_name = False
        self._name_greeted = False
        # 壳层注入：callable(source_rect: QRect)，把「展开详细」路由到 ChatWindow
        self.detail_opener = None
        # 壳层注入：统一由 ChatWindow 创建新会话，保证侧栏能跟踪全部会话。
        self.new_session_opener = None
        # 壳层注入：首启向导显示期间阻止热键/双击绕过必经步骤。
        self.interaction_guard: Callable[[], bool] | None = None

        # 引擎 + 单回合分层结果（共享模块）；P4：注入共享 client/supervisor
        self.supervisor = supervisor
        self.client = client or EngineClient(mock=mock)
        self.coordinator = (
            supervisor.coordinator if supervisor is not None else SessionCoordinator(self.client.home, parent=self)
        )
        self.ctrl = ConversationController(self.client)
        if supervisor is not None:
            supervisor.register(self.ctrl)
            supervisor.restarting.connect(self._on_sup_restarting)
            supervisor.restarted.connect(self._on_sup_restarted)
            supervisor.restart_failed.connect(self._on_sup_restart_failed)
            supervisor.state_changed.connect(self._on_session_state)

        # 热键（不可用时降级为仅双击，README 有说明）
        ok, hint = install_hotkey(self._toggle_bubble)
        self.hotkey_ok = ok
        self.hotkey_hint = hint if not ok else f"全局热键 {HOTKEY_LABEL}"
        if not ok:
            log.warning("hotkey degraded: %s", hint)

        self.pet = PetWindow(hotkey_hint=HOTKEY_LABEL if ok else "双击")
        self.bubble = BubbleWindow()
        self._bubble_native_window_number: int | None = None
        self._result_timer = QTimer(self)
        self._result_timer.setSingleShot(True)
        self._result_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._result_timer.setInterval(RESULT_AUTO_DISMISS_MS)
        self._result_timer.timeout.connect(self._dismiss_result_if_idle)
        self._result_remaining_ms = RESULT_AUTO_DISMISS_MS
        self._result_timer_started_at = 0.0
        self._result_hovering = False
        # A native frameless Tool window does not reliably receive leaveEvent
        # when the pointer moves directly into another macOS application.  Keep
        # a cheap global-position watch active only while dismissal is paused.
        self._result_hover_watch = QTimer(self)
        self._result_hover_watch.setInterval(100)
        self._result_hover_watch.timeout.connect(self._sync_result_hover_state)
        self._ack_timer = QTimer(self)
        self._ack_timer.setSingleShot(True)
        self._ack_timer.setInterval(ACK_MIN_VISIBLE_MS)
        self._ack_timer.timeout.connect(self._finish_ack_dwell)
        self._deferred_work_state: tuple[PetState, str] | None = None
        self._privacy_timer = QTimer(self)
        self._privacy_timer.setSingleShot(True)
        self._privacy_timer.setInterval(PRIVACY_DECISION_MIN_VISIBLE_MS)
        self._privacy_timer.timeout.connect(self._finish_privacy_dwell)
        self._pending_privacy_summary: str | None = None
        self.bubble.destroyed.connect(self._on_bubble_destroyed)

        # ── L0 桌宠 ──
        self.pet.summon_requested.connect(self._toggle_bubble)
        self.pet.open_chat_requested.connect(self.chat_requested.emit)
        self.pet.new_session_requested.connect(self.new_session)
        self.pet.settings_requested.connect(self._on_settings)
        self.pet.quit_requested.connect(self.quit)

        # ── L1 气泡 ──
        self.bubble.submitted.connect(self.send)
        self.bubble.abort_requested.connect(self.abort)
        self.bubble.escape_requested.connect(self._on_escape)
        self.bubble.dismissed.connect(self._on_dismissed)
        self.bubble.expand_detail.connect(self._on_expand_detail)
        self.bubble.open_chat_requested.connect(self.chat_requested.emit)
        self.bubble.interaction_started.connect(self._pause_result_dismiss)
        self.bubble.interaction_ended.connect(self._resume_result_dismiss)
        self.bubble.continue_requested.connect(self._on_continue)
        self.bubble.retry_requested.connect(self._on_retry)
        self.bubble.settings_requested.connect(self._on_settings)
        self.bubble.confirm_resolved.connect(self._on_confirm_resolved)

        # ── v0.1.6：人物/气泡联动拖动（关系恒为「气泡在人物正上方」）──
        self.pet.moved.connect(self._on_pet_moved)
        self.bubble.moved.connect(self._on_bubble_moved)
        self.bubble.drag_finished.connect(self._on_bubble_drag_finished)
        self.bubble.resized.connect(self._on_bubble_resized)

        # ── 会话编排 ──
        self.ctrl.busy_changed.connect(self._on_busy_changed)
        self.ctrl.summarizing.connect(self._on_summarizing)
        self.ctrl.summary_done.connect(self._on_summary_done)
        self.ctrl.answer_done.connect(self._on_answer_done)
        self.ctrl.failed.connect(self._on_failed)
        self.ctrl.request_accepted.connect(self._on_request_accepted)
        self.ctrl.request_committed.connect(self._on_request_committed)
        self.ctrl.request_failed.connect(self._on_request_failed)
        self.coordinator.queue_changed.connect(lambda _queue: self._sync_queue_indicators())

        # ── 引擎事件（确认/感知/崩溃）──
        self.client.event.connect(self._on_engine_event)
        self.client.crashed.connect(self._on_crashed)

        # 点气泡外区域收起（显式点击，不是失焦——失焦不收起）
        QApplication.instance().installEventFilter(self)

    # ── 生命周期 ──────────────────────────────────────────────

    def start(self) -> None:
        self.client.start()
        self.pet.show()
        self._set_state(PetState.IDLE)
        QTimer.singleShot(900, self._maybe_show_discovery_hint)

    def quit(self) -> None:
        self._result_timer.stop()
        self._result_hover_watch.stop()
        try:
            if self.supervisor is not None:
                self.supervisor.stop()      # P4：整个 App 的引擎一起停
            else:
                self.client.stop()
        except Exception:
            pass
        QApplication.instance().quit()
        QTimer.singleShot(800, lambda: __import__("os")._exit(0))  # 兜底强退

    def _on_bubble_destroyed(self) -> None:
        """窗口销毁时解除长生命周期回调，避免计时器访问失效的 Qt 对象。"""
        self._result_timer.stop()
        self._result_hover_watch.stop()
        self._ack_timer.stop()
        self._discovery_timer.stop()
        self._privacy_timer.stop()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)

    # ── 状态机 ────────────────────────────────────────────────

    @property
    def state(self) -> PetState:
        return self._state

    def _set_state(self, s: PetState) -> None:
        if s is self._state:
            return
        log.info("state %s -> %s", self._state.value, s.value)
        self._state = s
        self.pet.set_floating(s is PetState.IDLE)
        self.pet.set_pose(POSE_FOR_STATE[s])
        self.state_changed.emit(s.value)

    def _alert_pose_then_idle(self) -> None:
        """出错：alert(angry) 姿态亮 2.5s 后回 idle（窗口销毁后静默跳过）。"""
        self.pet.set_pose(POSE_ALERT)
        state = self._state

        def _back_to_pose() -> None:
            try:
                self.pet.set_pose(POSE_FOR_STATE[state])
            except RuntimeError:
                pass  # C++ 对象已销毁（App 退出中）：静默

        QTimer.singleShot(2500, _back_to_pose)

    def _request_work_state(self, state: PetState, text: str) -> None:
        """真实工作不延迟；仅让首个“收到”视觉回执至少可被人眼识别。"""
        if self._ack_timer.isActive() and self._state is PetState.ACKNOWLEDGING:
            self._deferred_work_state = (state, text)
            return
        if self._status_block is not None:
            self._status_block.set_text(text)
        self._set_state(state)

    def _finish_ack_dwell(self) -> None:
        pending, self._deferred_work_state = self._deferred_work_state, None
        if pending is not None and self.ctrl.busy:
            try:
                self._request_work_state(*pending)
            except RuntimeError:
                pass  # 窗口已销毁，延迟状态无需再渲染

    # ── 唤起 / 收起 ───────────────────────────────────────────

    def _toggle_bubble(self) -> None:
        if self.interaction_guard is not None and not self.interaction_guard():
            return
        first_interaction = not (self.client.home / "interaction-hint-v1").exists()
        self._mark_discovery_complete()
        if self._discovery_hint_active:
            self._discovery_hint_active = False
            self._discovery_timer.stop()
            self.bubble.start_input()
            self._add_first_interaction_hint()
            self._place_bubble()
            self._set_state(PetState.LISTENING)
            return
        if self.bubble.summoned:
            self.bubble.dismiss()
        else:
            self._result_timer.stop()
            show_input = not self.ctrl.busy and self._confirm_id is None
            if show_input:
                self.bubble.start_input()
                if first_interaction:
                    self._add_first_interaction_hint()
            self._place_bubble()
            self.bubble.summon(show_input=show_input)
            self.pet.show()
            self.pet.raise_()
            if self._state is PetState.IDLE:
                self._set_state(PetState.LISTENING)

    def _maybe_show_discovery_hint(self) -> None:
        """全新数据目录只展示一次短促漫画提示，不依赖用户先悬停发现 tooltip。"""
        # First-run is authoritative for as long as it is visible.  The old
        # generic 30-second top-level-window retry eventually let this hint
        # appear over a user who was carefully completing onboarding.
        if self.interaction_guard is not None and not self.interaction_guard():
            return
        marker = self.client.home / "interaction-hint-v1"
        if marker.exists() or self.bubble.summoned:
            return
        other_windows = [
            window for window in QApplication.topLevelWidgets()
            if (
                window.isVisible()
                and window not in (self.pet, self.bubble)
                # macOS exposes the native application menu as a visible,
                # untitled top-level QWidget. It is not a blocking product
                # surface and must not postpone first-use help for 30 seconds.
                and bool(window.windowTitle().strip())
            )
        ]
        if other_windows and self._discovery_hint_retries < 30:
            self._discovery_hint_retries += 1
            QTimer.singleShot(1000, self._maybe_show_discovery_hint)
            return
        self.bubble.clear_flow()
        self.bubble.set_input_visible(False)
        self.bubble.add_greeting("点一下直接问我 · 右键打开完整对话和设置")
        self._place_bubble()
        self.bubble.summon(show_input=False)
        self.pet.raise_()
        self._discovery_hint_active = True
        self._discovery_timer.start(DISCOVERY_HINT_MS)

    def _mark_discovery_complete(self) -> None:
        """Only retire the first-action hint after the user actually interacts."""
        try:
            atomic_write_private(self.client.home / "interaction-hint-v1", "seen\n")
        except OSError:
            pass

    def _add_first_interaction_hint(self) -> None:
        """第一次真实点击也必须看得到操作说明，不能被启动定时器竞态跳过。"""
        self.bubble.add_greeting("直接在下方问我；右键人物可打开完整对话和设置。")
        self.bubble.set_input_visible(True)
        self.bubble.focus_input()

    def _hide_discovery_hint(self) -> None:
        if not self._discovery_hint_active:
            return
        self._discovery_hint_active = False
        if self.bubble.summoned:
            self.bubble.dismiss()

    # ── 首启问称呼（v0.1.7）───────────────────────────────────

    def _maybe_ask_name(self) -> None:
        """首次使用（无 user-profile.json）→ 气泡里问一次称呼；问过/跳过落盘后不再问。"""
        if self._name_greeted or not should_ask_name(self.client.home):
            return
        self._name_greeted = True
        self._awaiting_name = True
        # 等唤起动画差不多落地再弹问候块，避免和动画/高度自适应抢帧
        QTimer.singleShot(350, lambda: self.bubble.add_greeting(
            "第一次见面～怎么称呼你？直接在下边告诉我名字就行；不想说就回「算了」。"))

    @staticmethod
    def _looks_like_name(text: str) -> bool:
        """简单判别「输入的是称呼」：短、无句读/问叹标点——否则当正常提问放行。"""
        t = text.strip()
        return bool(t) and len(t) <= 16 and not any(
            ch in t for ch in "？?。！!，,、；;：:\n")

    def _handle_name_reply(self, text: str) -> None:
        """称呼回合：落盘 + 回一句确认，不进引擎。"""
        self._awaiting_name = False
        self.bubble.add_user_message(text)
        name = text.strip()
        if name in _NAME_SKIP_WORDS:
            save_user_name("", source="pet", home=self.client.home)   # 明确跳过：落盘标记已问过
            self.bubble.add_greeting("好，那就不问啦～想告诉我的时候随时说。")
        else:
            save_user_name(name, source="pet", home=self.client.home)
            self.bubble.add_greeting(f"好嘞，{name}！我记住啦～")
        self.bubble.set_input_visible(True)

    def _place_bubble(self) -> None:
        """气泡固定在桌宠正上方：尾尖与人物可见发顶保持约 8px，
        右下尾巴尖对准桌宠中心；顶部空间不足时落桌宠下方，同样不重叠（v0.1.6）。"""
        from ..a11y import screen_of

        b, p = self.bubble, self.pet
        b._refresh_height()  # v0.1.7：先按当前内容定高再锚定（旧高度会算出错误锚点）
        screen = screen_of(p).availableGeometry()  # task-4c：气泡锚定在桌宠所在屏
        x = p.x() + p.width() // 2 - (b.width() - 44 + 10)  # 尾巴尖 ≈ 桌宠中心
        x = max(screen.left() + 8, min(x, screen.right() - b.width() - 8))
        y_above = p.y() - b.height() - T.BUBBLE_PET_GAP  # 气泡底含尾巴，间隙即不压人物
        if y_above >= screen.top() + 8:
            y = y_above
            tail_side = "bottom"
        else:  # 上方空间不足 → 放桌宠下方
            y = p.y() + p.height() + T.BUBBLE_PET_GAP
            tail_side = "top"
        b.move(x, y)
        b.set_tail_anchor(p.x() + p.width() // 2, tail_side)

    def _on_pet_moved(self, _x: int, _y: int) -> None:
        """拖人物 → 气泡实时跟随，保持正上方（v0.1.6）。"""
        if self.bubble.summoned:
            self._place_bubble()

    def _on_bubble_moved(self, _x: int, _y: int) -> None:
        """拖气泡 → 人物跟到气泡尾巴正下方，二者作为整体移动（v0.1.6）。"""
        from ..a11y import screen_of

        b, p = self.bubble, self.pet
        screen = screen_of(b).availableGeometry()
        x = b.tail_tip_global_x - p.width() // 2
        if b.tail_side == "top":
            y = b.y() - p.height() - T.BUBBLE_PET_GAP
        else:
            y = b.y() + b.height() + T.BUBBLE_PET_GAP
        x = max(screen.left() + 4, min(x, screen.right() - p.width() - 4))
        y = max(screen.top() + 4, min(y, screen.bottom() - p.height() - 4))
        p.move(x, y)

    def _on_bubble_drag_finished(self) -> None:
        """拖气泡实际改了人物位置 → 持久化的是人物位置（气泡由人物派生）。"""
        self.pet.save_position()

    def _on_bubble_resized(self) -> None:
        """内容增长气泡变高时重新锚定，避免向下长压住桌宠（拖动中不干预）。"""
        if self.bubble.summoned and not self.bubble.dragging:
            self._place_bubble()

    def _on_dismissed(self) -> None:
        self._result_timer.stop()
        self._result_hover_watch.stop()
        self._result_hovering = False
        # 收起时确认条还悬着 → 按「取消」答复引擎（rpc-contract §5.3 cancelled）
        if self._confirm_id:
            self._resolve_confirm(cancelled=True)
        if self._state in (
            PetState.LISTENING, PetState.PRESENTING, PetState.CANCELLED, PetState.ERROR
        ):
            self._set_state(PetState.IDLE)

    def _on_escape(self) -> None:
        """Esc：生成中 = 打断；否则 = 收起（interaction-spec §2）。"""
        if self.ctrl.busy:
            self.abort()
        elif self.bubble.summoned:
            self.bubble.dismiss()

    def eventFilter(self, obj, ev):
        """点气泡/桌宠之外的区域 → 收起（显式点击收起；失焦本身不收起）。"""
        try:
            bubble_child = (
                isinstance(obj, QWidget)
                and (obj is self.bubble or self.bubble.isAncestorOf(obj))
            )
        except RuntimeError:
            # Qt can deliver final child events while the C++ BubbleWindow is
            # already being torn down, just before destroyed removes us.
            return False
        if bubble_child and ev.type() in (
            QEvent.Type.Enter,
            QEvent.Type.HoverEnter,
            QEvent.Type.MouseMove,
        ):
            # The compact result is composed of child widgets that can consume
            # native enter/move events before BubbleWindow sees them.
            self._pause_result_dismiss()
        elif bubble_child and ev.type() in (QEvent.Type.Leave, QEvent.Type.HoverLeave):
            # Defer until Qt has updated widgetAt/underMouse; child-to-child
            # transitions must not be mistaken for leaving the whole bubble.
            QTimer.singleShot(0, self._resume_result_dismiss_if_pointer_left)
        if ev.type() == QEvent.Type.ApplicationDeactivate:
            # On macOS a frameless Tool window can keep underMouse() latched
            # after the user clicks into another application.  Deactivation is
            # an unambiguous end to interaction with this result bubble.
            QTimer.singleShot(0, lambda: self._resume_result_dismiss(force=True))
        if (ev.type() == QEvent.Type.MouseButtonPress and self.bubble.summoned
                and not self._detail_open
                and not self._settings_open
                and obj not in (self.bubble, self.pet)):
            gp = ev.globalPosition().toPoint() if hasattr(ev, "globalPosition") else None
            if gp is not None:
                if not self.bubble.geometry().contains(gp) and not self.pet.geometry().contains(gp):
                    self.bubble.dismiss()
        return super().eventFilter(obj, ev)

    # ── 发送 / 打断 ───────────────────────────────────────────

    def send(self, text: str) -> None:
        self._result_timer.stop()
        self._retry_attempt = 0
        # v0.1.7 首启问称呼：等待称呼时，像称呼的输入拦截落盘，不进引擎；
        # 不像称呼（长句/带标点）则当正常提问放行，本次会话不再拦。
        if self._awaiting_name:
            if text.strip() in _NAME_SKIP_WORDS or self._looks_like_name(text):
                self._handle_name_reply(text)
                return
            self._awaiting_name = False
        # 每次新请求只占用当前临时层；历史仍由完整会话窗口保存。
        if not self.ctrl.busy and self._confirm_id is None and not self.bubble._input_visible():
            self.bubble.clear_flow()
        waiting = self.ctrl.busy or (
            self.supervisor is not None and self.supervisor.busy_except(self.ctrl)
        )
        item = self.coordinator.enqueue(text, "pet")
        if waiting:
            self.bubble.add_user_message(text)
            self._status_block = self.bubble.add_status("消息已排队，可在完整窗口取消")
            return
        if not self.client.alive:
            self._status_block = self.bubble.add_status("引擎重启中，消息已安全保留…")
            if self.supervisor is not None:
                if not self.supervisor._restarting:
                    self.supervisor.restart_now()
                return
            self.ensure_engine()
        self._send_queued_item(item)

    def _send_queued_item(self, item: QueueItem) -> None:
        if self.ctrl.busy or (self.supervisor is not None and self.supervisor.busy_except(self.ctrl)):
            return
        self._aborted = False
        self._privacy_timer.stop()
        self._pending_privacy_summary = None
        # 详情收起或旧结果退场可能仍有淡出回调在飞；新请求一开始就取得气泡所有权。
        self.bubble.cancel_dismiss()
        self._perception_hint = None
        self._last_user_text = item.text
        write_last_user_text(self.client.home, item.text)
        if self._session_needs_title:
            self._pending_session_title = make_session_title(item.text)
        status_text = (
            f"正在重试（第 {self._retry_attempt} 次）"
            if self._retry_attempt else STATUS_LINE[PetState.ACKNOWLEDGING]
        )
        self._status_block = self.bubble.present_status(
            status_text, cancellable=True)
        self._set_state(PetState.ACKNOWLEDGING)
        self._deferred_work_state = None
        self._ack_timer.start()
        self.bubble.set_busy(True)
        request_id = self.ctrl.send(item.text)
        if request_id is None:
            self.coordinator.hold_item_for_review(item.id)
            return
        self._queue_requests[request_id] = item.id
        self.coordinator.mark_inflight(item.id, request_id)

    def _drain_queue(self) -> None:
        if not self.client.alive or self.ctrl.busy:
            return
        if self.supervisor is not None and self.supervisor.busy_except(self.ctrl):
            return
        item = self.coordinator.next_ready("pet")
        if item is not None:
            self._send_queued_item(item)

    def _sync_queue_indicators(self) -> None:
        for item in self.coordinator.queue:
            if item.source != "pet" or item.id in self._queue_indicators:
                continue
            if item.needs_review or item.request_id is not None:
                continue
            preview = item.text if len(item.text) <= 60 else item.text[:60] + "…"
            indicator = self.bubble.add_queue_indicator(preview)

            def cancel(item_id=item.id, ind=indicator) -> None:
                try:
                    self.coordinator.cancel(item_id)
                except KeyError:
                    return
                ind.mark_cancelled()

            indicator.cancel_button.clicked.connect(lambda _checked=False: cancel())
            self._queue_indicators[item.id] = indicator
        # 只把“仍在等待”的项显示成排队；一旦已发给引擎就立即移除，避免和
        # “收到，我接住了 / 正在处理”同时出现造成状态矛盾。
        live_ids = {
            item.id for item in self.coordinator.queue
            if item.source == "pet" and item.request_id is None and not item.needs_review
        }
        for item_id in list(self._queue_indicators):
            if item_id not in live_ids:
                indicator = self._queue_indicators.pop(item_id)
                self.bubble.remove_widget(indicator)
        QTimer.singleShot(0, self._drain_queue)

    def _on_request_committed(self, request_id: str) -> None:
        if request_id in self._queue_requests:
            self._queue_requests.pop(request_id, None)
            self.coordinator.acknowledge(request_id)
        self._apply_pending_session_title()

    def _apply_pending_session_title(self) -> None:
        """Name only after the first user message created a real session file."""
        if not self._pending_session_title:
            return
        try:
            self.client.set_session_name(self._pending_session_title)
        except RuntimeError:
            return
        self._session_needs_title = False

    def _on_request_accepted(self, _request_id: str) -> None:
        """只有引擎确认接单后才进入组织阶段，避免用计时器伪造进度。"""
        self._request_work_state(PetState.COMPOSING, STATUS_LINE[PetState.COMPOSING])

    def _on_request_failed(self, request_id: str, _error: str) -> None:
        if request_id in self._queue_requests:
            self._queue_requests.pop(request_id, None)
            self.coordinator.hold_for_review(request_id)

    def abort(self) -> None:
        if self._confirm_id:
            self._resolve_confirm(cancelled=True)
        if self.ctrl.busy:
            self._privacy_timer.stop()
            self._pending_privacy_summary = None
            self._ack_timer.stop()
            self._deferred_work_state = None
            self._aborted = True
            self.ctrl.abort()
            if self._status_block is not None:
                self._status_block.set_text(
                    "已停止（保留已产内容）", animated=False, cancellable=False)
            self._set_state(PetState.CANCELLED)

    # ── 会话编排信号 ──────────────────────────────────────────

    def _on_busy_changed(self, busy: bool) -> None:
        self.bubble.set_busy(busy)
        if not busy:
            QTimer.singleShot(0, self._drain_queue)
            # 正常完成会紧接着进入 PRESENTING，错误路径也有自己的终态处理。
            # 此处不抢先切回 LISTENING，避免人物在“整理→呈现”之间闪一下 idle。

    def _on_answer_done(self, answer: str) -> None:
        self._last_answer = answer

    def _on_summarizing(self) -> None:
        self._ack_timer.stop()
        self._deferred_work_state = None
        if self._aborted:
            if self._status_block is not None:
                self._status_block.set_text(
                    "已停止（正在整理已有内容）", animated=False, cancellable=False)
            self._set_state(PetState.CANCELLED)
            return
        if self._privacy_timer.isActive():
            return  # 明确的隐私决定先说够时间，再换成结果态
        if self._status_block is not None:
            self._status_block.set_text(STATUS_LINE[PetState.PRESENTING])
        self._set_state(PetState.PRESENTING)

    def _on_summary_done(self, summary: str) -> None:
        self._ack_timer.stop()
        self._deferred_work_state = None
        if self._privacy_timer.isActive() and not self._aborted:
            self._pending_privacy_summary = summary
            return
        if self._status_block is not None:
            self._status_block.set_text(
                "想好了 ✓" if not self._aborted else "已停止（基于已产内容）",
                animated=False,
                cancellable=False,
            )
            self._status_block = None
        if self._aborted:
            # 终止竞争中引擎可能仍返回“写好了”等完成式 brief；停止后的 UI 不再信任它。
            brief = "我停下来了。停止前生成的内容已经保留。"
        else:
            source = summary.strip() or self._last_answer.strip()
            # L1 is a compact speech card, not a Markdown document. Exposed
            # markers such as **答案** make the character look like it is
            # reciting protocol syntax, so keep only the visible wording.
            brief = plain_visible_text(source) or "这次没有生成可显示的简答，请查看详情。"
            self.credential_validation.emit(True, "模型连接正常")
        self._last_summary = brief
        # 成功结果必须压过任何较早启动的收起动画，避免“详情里有答案、桌面结果消失”。
        self.bubble.cancel_dismiss()
        self.bubble.present_summary(brief, cancelled=self._aborted)
        # 短结是「需要用户注意」的时刻：气泡没挂着就轻提示唤起
        if not self.bubble.summoned:
            self._place_bubble()
            self.bubble.summon(show_input=False)
        else:
            self._place_bubble()
        self.pet.show()
        self.pet.raise_()
        self._start_result_dismiss()
        self._retry_attempt = 0
        self._set_state(PetState.CANCELLED if self._aborted else PetState.PRESENTING)

    def _on_continue(self) -> None:
        """用户明确追问时才恢复输入；旧结果不继续占据 L1。"""
        self._result_timer.stop()
        self._result_hover_watch.stop()
        self._result_hovering = False
        self.bubble.start_input()
        self._place_bubble()
        self._set_state(PetState.LISTENING)

    def _start_result_dismiss(self) -> None:
        # Keep test and accessibility overrides meaningful while the production
        # interval remains RESULT_AUTO_DISMISS_MS.
        # Qt may recreate the native Tool window between hidden/input/result
        # states, so capture its current stable number only after result layout.
        self._bubble_native_window_number = self._resolve_native_window_number()
        self._result_remaining_ms = self._result_timer.interval()
        self._result_timer_started_at = time.monotonic()
        self._result_hovering = False
        self._result_timer.start(self._result_remaining_ms)
        self._result_hover_watch.start()

    def _pause_result_dismiss(self) -> None:
        """Keep a result readable while the pointer is inside the compact bubble."""
        if not self._result_timer.isActive():
            return
        remaining = self._result_timer.remainingTime()
        if remaining >= 0:
            self._result_remaining_ms = max(1_000, remaining)
        else:
            elapsed = int((time.monotonic() - self._result_timer_started_at) * 1000)
            self._result_remaining_ms = max(1_000, self._result_remaining_ms - elapsed)
        self._result_timer.stop()
        self._result_hovering = True
        self._result_hover_watch.start()

    def _resume_result_dismiss(self, *, force: bool = False) -> None:
        # Native leave events can also arrive spuriously while the animated
        # frameless window is moving/resizing.  The global cursor position is
        # the source of truth while the hover watchdog owns the pause.
        if (not force and self._result_hover_watch.isActive()
                and self._pointer_inside_bubble()):
            self._result_hovering = True
            return
        if (self._state not in (PetState.PRESENTING, PetState.CANCELLED)
                or self.ctrl.busy or self._confirm_id is not None
                or self._detail_open or self._settings_open
                or self.bubble._input_visible() or not self.bubble.summoned):
            self._result_hover_watch.stop()
            self._result_hovering = False
            return
        self._result_hovering = False
        self._result_timer_started_at = time.monotonic()
        self._result_timer.start(max(1_000, self._result_remaining_ms))
        if force:
            # Do not let a stale underMouse bit immediately pause the timer
            # again after another application became active.  A real re-entry
            # will emit Enter/MouseMove and restart the watchdog.
            self._result_hover_watch.stop()
        else:
            self._result_hover_watch.start()

    def _sync_result_hover_state(self) -> None:
        """Use the global pointer as truth when native enter/leave events vanish."""
        if (self._state not in (PetState.PRESENTING, PetState.CANCELLED)
                or self.ctrl.busy or self._confirm_id is not None
                or self._detail_open or self._settings_open
                or self.bubble._input_visible() or not self.bubble.summoned):
            self._result_hover_watch.stop()
            self._result_hovering = False
            return
        if self._pointer_inside_bubble():
            self._pause_result_dismiss()
        elif self._result_hovering:
            self._resume_result_dismiss()

    def _pointer_inside_bubble(self) -> bool:
        """Combine native macOS geometry with Qt hit-testing fallbacks.

        Frameless tool windows can miss top-level enter/leave, while global
        Qt cursor coordinates can disagree across mixed-scale screens.  Cocoa's
        mouse location and NSWindow frame share one coordinate system, so that
        result is authoritative when available.  The Qt paths retain portable
        behavior and cover startup before a native window exists.
        """
        native_inside = self._native_pointer_inside_bubble()
        if native_inside is not None:
            return native_inside
        try:
            if self.bubble.underMouse():
                return True
            cursor_pos = QCursor.pos()
            hovered = QApplication.widgetAt(cursor_pos)
            if hovered is self.bubble or (
                isinstance(hovered, QWidget) and self.bubble.isAncestorOf(hovered)
            ):
                return True
            local_pos = self.bubble.mapFromGlobal(cursor_pos)
            return self.bubble.rect().contains(local_pos)
        except RuntimeError:
            return False

    def _resolve_native_window_number(self) -> int | None:
        """Resolve the bubble's Quartz window number without raw Cocoa pointers."""
        try:
            import os

            from Quartz import (
                CGWindowListCopyWindowInfo,
                kCGNullWindowID,
                kCGWindowListOptionOnScreenOnly,
            )

            expected_width = self.bubble.width()
            expected_height = self.bubble.height()
            descriptions = CGWindowListCopyWindowInfo(
                kCGWindowListOptionOnScreenOnly, kCGNullWindowID
            ) or ()
            for description in descriptions:
                if int(description.get("kCGWindowOwnerPID", -1)) != os.getpid():
                    continue
                bounds = description.get("kCGWindowBounds") or {}
                if (abs(float(bounds.get("Width", 0.0)) - expected_width) <= 2
                        and abs(float(bounds.get("Height", 0.0)) - expected_height) <= 2):
                    return int(description.get("kCGWindowNumber"))
            return None
        except (ImportError, RuntimeError, TypeError, ValueError, AttributeError):
            return None

    def _native_pointer_inside_bubble(self) -> bool | None:
        """Return Quartz containment without retaining a Cocoa object pointer."""
        number = self._bubble_native_window_number
        if number is None:
            number = self._resolve_native_window_number()
            self._bubble_native_window_number = number
            if number is None:
                return None
        try:
            from Quartz import (
                CGEventCreate,
                CGEventGetLocation,
                CGWindowListCreateDescriptionFromArray,
            )

            descriptions = CGWindowListCreateDescriptionFromArray((number,)) or ()
            for description in descriptions:
                if int(description.get("kCGWindowNumber", -1)) != number:
                    continue
                bounds = description.get("kCGWindowBounds") or {}
                x = float(bounds.get("X", 0.0))
                y = float(bounds.get("Y", 0.0))
                width = float(bounds.get("Width", 0.0))
                height = float(bounds.get("Height", 0.0))
                point = CGEventGetLocation(CGEventCreate(None))
                return (x <= float(point.x) < x + width
                        and y <= float(point.y) < y + height)
            return None
        except (ImportError, RuntimeError, TypeError, ValueError, AttributeError):
            return None

    def _resume_result_dismiss_if_pointer_left(self) -> None:
        if not self._pointer_inside_bubble():
            self._resume_result_dismiss()

    def _dismiss_result_if_idle(self) -> None:
        """结果卡无交互后退场；工作、确认和详情阶段绝不误收起。"""
        try:
            input_visible = self.bubble._input_visible()
            summoned = self.bubble.summoned
        except RuntimeError:
            self._result_timer.stop()
            return
        if (self.ctrl.busy or self._confirm_id is not None or self._detail_open
                or self._settings_open
                or input_visible or not summoned):
            return
        self.bubble.dismiss()

    def _on_failed(self, err: str) -> None:
        from ..conversation import humanize_error

        message = humanize_error(err)
        self._result_timer.stop()
        self._result_hover_watch.stop()
        self._result_hovering = False
        self._privacy_timer.stop()
        self._pending_privacy_summary = None
        self._ack_timer.stop()
        self._deferred_work_state = None
        self._status_block = None
        self._perception_hint = None
        self.bubble.clear_flow()
        self.bubble.set_input_visible(False)
        self.bubble.cancel_dismiss()
        if self._retry_attempt:
            message += f"\n刚刚完成第 {self._retry_attempt} 次重试，仍未连接成功。"
        self.bubble.add_error(message)
        if message.startswith("API Key 无效"):
            self.credential_validation.emit(False, message.split("。", 1)[0])
        self._set_state(PetState.ERROR)
        self._alert_pose_then_idle()
        if not self.bubble.summoned:
            self._place_bubble()
            self.bubble.summon()

    def _on_retry(self) -> None:
        if self.ctrl.busy:
            return
        self._retry_attempt += 1
        self._status_block = self.bubble.present_status(
            f"正在重试（第 {self._retry_attempt} 次）", cancellable=False
        )
        self._place_bubble()
        self._set_state(PetState.ACKNOWLEDGING)
        pending = next(
            (item for item in self.coordinator.queue if item.source == "pet" and item.needs_review),
            None,
        )
        if pending is not None:
            self.coordinator.retry(pending.id)
        elif self._last_user_text:
            self.coordinator.enqueue(self._last_user_text, "pet")

    def _on_session_state(self, data: dict) -> None:
        path = str(data.get("sessionFile") or "")
        if not path:
            return
        changed_session = path != self._known_session_path
        self._known_session_path = path
        name = str(data.get("sessionName") or "")
        if self._pending_session_title and name == self._pending_session_title:
            self._pending_session_title = ""
        elif self._pending_session_title and name in ("", "新会话"):
            # get_state can race the rename response at agent_end; retry now that
            # the supervisor has confirmed the concrete session file.
            self._apply_pending_session_title()
        if changed_session and not self._pending_session_title:
            self._session_needs_title = name in ("", "新会话")

    # ── 读屏确认 / 感知提示 ───────────────────────────────────

    def _on_engine_event(self, ev: dict) -> None:
        t = ev.get("type")
        if t == "agent_end" and not self.ctrl.busy:
            QTimer.singleShot(0, self._drain_queue)
        # P4 双入口：确认/感知事件只由「本轮发起方」处理（另一入口静默）
        if (self.supervisor is not None and t in (
                "extension_ui_request", "tool_execution_start", "tool_execution_end")
                and not self.ctrl.busy):
            return
        if t == "extension_ui_request":
            if ev.get("method") == "confirm":
                self._ack_timer.stop()
                self._deferred_work_state = None
                self._confirm_id = ev.get("id")
                text = "；".join(x for x in (str(ev.get("title", "")).strip(),
                                             str(ev.get("message", "")).strip()) if x)
                self.bubble.show_confirm(text or "允许这次操作吗？")
                self._set_state(PetState.PERCEIVING)
                if not self.bubble.summoned:  # 确认需要用户注意 → 唤起
                    self._place_bubble()
                    self.bubble.summon()
            else:
                # 未实现的 method 一律取消（rpc-contract §5.3）
                self.client.respond_ui(ev.get("id"), cancelled=True)
        elif t == "tool_execution_start" and ev.get("toolName") == "read_screen":
            self._perception_hint = self.bubble.add_perception_hint("准备读取当前屏幕")
            self._request_work_state(PetState.ACTING, "准备读取屏幕")
        elif t == "tool_execution_start":
            tool_name = str(ev.get("toolName") or "")
            label = {
                "bash": "正在执行命令",
                "browser": "正在查看网页",
                "write_file": "正在整理文件",
            }.get(tool_name, "正在使用工具")
            self._request_work_state(PetState.ACTING, label)
        elif t == "tool_execution_end" and self._state is PetState.ACTING:
            self._request_work_state(PetState.COMPOSING, STATUS_LINE[PetState.COMPOSING])
        elif t == "message_update" and self.ctrl.busy:
            update = ev.get("assistantMessageEvent") or {}
            if update.get("type") == "text_delta":
                self._request_work_state(PetState.COMPOSING, STATUS_LINE[PetState.COMPOSING])

    def _on_confirm_resolved(self, ok: bool) -> None:
        if not ok:
            self.bubble.cancel_dismiss()
            self._perception_hint = None
            self._status_block = self.bubble.present_status(
                "已拒绝，未读取屏幕。正在基于已有信息回答", cancellable=True
            )
            self._place_bubble()
            self.bubble.show()
            self.bubble.raise_()
            self.pet.show()
            self.pet.raise_()
            self._set_state(PetState.COMPOSING)
            self._pending_privacy_summary = None
            self._privacy_timer.start()
            self._resolve_confirm(confirmed=False)
            return
        if self._status_block is not None:
            self._status_block.set_text("正在读取屏幕", animated=True, cancellable=True)
        hint = getattr(self, "_perception_hint", None)
        if hint is not None:
            hint.label.setText("👀 已允许，正在读取当前屏幕")
            self._perception_hint = None
        self._set_state(PetState.ACTING)
        self._resolve_confirm(confirmed=True)
        self.read_permission_requested.emit()

    def _finish_privacy_dwell(self) -> None:
        """拒绝读屏至少保持一个可确认的首帧，再呈现已经到达的模型回答。"""
        summary, self._pending_privacy_summary = self._pending_privacy_summary, None
        if summary is not None:
            self._on_summary_done(summary)

    def _resolve_confirm(self, confirmed: bool = None, cancelled: bool = None) -> None:
        rid, self._confirm_id = self._confirm_id, None
        self.bubble.hide_confirm()
        if rid:
            self.client.respond_ui(rid, confirmed=confirmed, cancelled=cancelled)

    # ── 右键菜单动作 ──────────────────────────────────────────

    def new_session(self) -> None:
        if self._confirm_id:
            self._resolve_confirm(cancelled=True)
        if self.ctrl.busy:
            self.abort()
        self.bubble.clear_flow()
        self._last_answer = ""
        self._last_summary = ""
        self._last_user_text = ""
        self._retry_attempt = 0
        self._session_needs_title = True
        self._pending_session_title = ""
        self.ctrl.clear_resume_context()
        if self.new_session_opener is not None:
            self.new_session_opener()
        else:
            self.client.new_session()
        self._set_state(PetState.LISTENING if self.bubble.summoned else PetState.IDLE)

    def _on_settings(self) -> None:
        log.info("settings requested")
        self.settings_requested.emit()

    def suspend_for_settings(self) -> None:
        """打开设置时临时隐藏气泡，并完整保留错误卡、输入或当前结果。"""
        if self._settings_open:
            return
        self._settings_open = True
        self._resume_bubble_after_settings = self.bubble.summoned
        self._result_timer.stop()
        self._result_hover_watch.stop()
        self._result_hovering = False
        if self._resume_bubble_after_settings:
            self.bubble.cancel_dismiss()
            self.bubble.hide()

    def restore_after_settings(self) -> None:
        """关闭设置后恢复此前可见的气泡，避免错误与重试入口凭空消失。"""
        should_resume = self._resume_bubble_after_settings
        self._settings_open = False
        self._resume_bubble_after_settings = False
        if not should_resume or not self.bubble.summoned:
            return
        self._place_bubble()
        self.bubble.show()
        self.bubble.raise_()
        self.pet.show()
        self.pet.raise_()

    def _on_expand_detail(self) -> None:
        """「查看详情」：打开完整对话窗口的「从气泡展开」模式。

        PetApp 不持有 chat 引用：壳层注入 detail_opener（= ChatWindow.open_from_bubble），
        并接 ChatWindow.detail_collapsed → restore_bubble。未注入时为空操作。
        """
        if not self._last_answer and not self._last_summary:
            return
        if self.detail_opener is None:
            log.warning("detail_opener 未注入（应由壳层接线 chat.open_from_bubble）")
            return
        self._result_timer.stop()
        self._result_hover_watch.stop()
        self._result_hovering = False
        # 打开时气泡隐藏（不播收起动画、不动 _shown 标记），对话窗口从气泡 rect 长出
        rect = self.bubble.geometry()
        self._detail_open = True
        self.bubble.hide()
        # 详情是普通工作窗口；置顶桌宠继续显示会压住右下输入区。
        self.pet.hide()
        self.detail_opener(rect)

    def restore_bubble(self) -> None:
        """详情收起后回到纯桌宠，不让旧结果重新常驻。"""
        self._detail_open = False
        self.pet.show()
        self.pet.raise_()
        # 只收起打开详情时被隐藏的旧气泡。若用户已重新唤起可见气泡，晚到的
        # collapse 回调不能把新的输入、工作态或结果一起关掉。
        if self.bubble.summoned and not self.bubble.isVisible():
            self.bubble.dismiss()

    # ── 引擎崩溃 ──────────────────────────────────────────────

    def _on_crashed(self, code: int) -> None:
        log.error("engine crashed, exit code=%d", code)
        if self.supervisor is not None:
            return  # P4：崩溃提示由 supervisor 信号驱动（_on_sup_restarting 等）
        self._status_block = None
        self.bubble.add_error(f"引擎已退出（code {code}）。发送任意消息将尝试重启引擎。")
        self._alert_pose_then_idle()
        if not self.bubble.summoned:
            self._place_bubble()
            self.bubble.summon()

    # ── P4：supervisor 重启链路反馈 ───────────────────────────

    def _on_sup_restarting(self, attempt: int) -> None:
        self.bubble.cancel_dismiss()
        self._status_block = self.bubble.present_status(
            f"连接中断，正在自动恢复（第 {attempt} 次）…", cancellable=False
        )
        self._alert_pose_then_idle()
        if not self.bubble.summoned:
            self._place_bubble()
            self.bubble.summon()

    def _on_sup_restarted(self) -> None:
        self._status_block = self.bubble.present_status("连接已恢复 ✓ 会话还在")
        QTimer.singleShot(0, self._drain_queue)

    def _on_sup_restart_failed(self) -> None:
        self._status_block = None
        self.bubble.clear_flow()
        self.bubble.set_input_visible(False)
        self.bubble.add_error("引擎连续重启失败。请检查配置（API Key / 模型）后，发送任意消息重试。")
        self._alert_pose_then_idle()
        if not self.bubble.summoned:
            self._place_bubble()
            self.bubble.summon()

    def ensure_engine(self) -> None:
        """崩溃后下一条消息前重启引擎（interaction-spec §8.2：不白屏）。"""
        if self.supervisor is not None:
            return  # P4：重启由 supervisor 统一负责
        if not self.client.alive:
            log.info("restarting engine…")
            self.client.start()
