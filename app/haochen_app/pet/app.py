"""PetApp：桌宠模块装配 —— L0 桌宠 + L1 气泡 + 引擎会话 + 状态机。

共享只读依赖：haochen_app.engine_client.EngineClient（传输层）、
haochen_app.conversation.ConversationController（answer→summary 两步编排）。

P4 集成接口（给对话窗口/集成负责人）：
- 共享会话：对话窗口用**同一个 EngineClient 实例**（或同一引擎进程）构造自己的
  ConversationController 并订阅 client.event；气泡 ↔ 窗口天然共享引擎侧同一会话。
  切换/恢复会话用 client.get_messages() 拉同一份历史（rpc-contract §2.5/§7）。
- 「展开详细」（v0.1.4 hotfix）：改回打开完整对话窗口 ChatWindow 的「从气泡展开」
  模式（open_from_bubble）。PetApp 不直接持有 chat 引用，由壳层注入
  `pet.detail_opener = chat.open_from_bubble` 并接
  `chat.detail_collapsed.connect(pet.restore_bubble)`；未注入时按钮点击为空操作。
  expand_detail_answer 信号保留但不再 emit。
- 「设置」占位：PetApp.settings_requested = pyqtSignal()，P4 接配置面板。
- 状态机：PetApp.state（PetState 枚举），state_changed = pyqtSignal(str)。
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QEvent, QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication

from ..app_tracking import write_last_user_text
from ..conversation import ConversationController
from ..engine_client import EngineClient
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


class PetApp(QObject):
    """桌宠 + 气泡 + 会话编排 + 显式状态机（interaction-spec §1）。"""

    state_changed = pyqtSignal(str)         # PetState.value
    expand_detail_answer = pyqtSignal(str)  # 旧 P4 接口（详情改走 detail_opener 注入，不再 emit）
    settings_requested = pyqtSignal()       # 右键「设置（占位）」

    def __init__(self, mock: bool | None = None, client: EngineClient | None = None,
                 supervisor=None, parent=None):
        super().__init__(parent)
        self._state = PetState.IDLE
        self._confirm_id: str | None = None
        self._last_answer = ""
        self._last_summary = ""
        self._last_user_text = ""
        self._aborted = False
        self._status_block = None
        self._queue_requests: dict[str, str] = {}
        self._queue_indicators: dict[str, object] = {}
        self._detail_open = False                       # 详情（对话窗口展开模式）打开中
        # v0.1.7 首启问称呼：等待用户输入称呼中 / 本次会话已问过（避免重复问候块）
        self._awaiting_name = False
        self._name_greeted = False
        # 壳层注入：callable(source_rect: QRect)，把「展开详细」路由到 ChatWindow
        self.detail_opener = None

        # 引擎 + 两步编排（共享模块，只读使用）；P4：注入共享 client/supervisor
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

        # 热键（不可用时降级为仅双击，README 有说明）
        ok, hint = install_hotkey(self._toggle_bubble)
        self.hotkey_ok = ok
        self.hotkey_hint = hint if not ok else f"全局热键 {HOTKEY_LABEL}"
        if not ok:
            log.warning("hotkey degraded: %s", hint)

        self.pet = PetWindow(hotkey_hint=HOTKEY_LABEL if ok else "双击")
        self.bubble = BubbleWindow()

        # ── L0 桌宠 ──
        self.pet.summon_requested.connect(self._toggle_bubble)
        self.pet.new_session_requested.connect(self.new_session)
        self.pet.settings_requested.connect(self._on_settings)
        self.pet.quit_requested.connect(self.quit)

        # ── L1 气泡 ──
        self.bubble.submitted.connect(self.send)
        self.bubble.abort_requested.connect(self.abort)
        self.bubble.escape_requested.connect(self._on_escape)
        self.bubble.dismissed.connect(self._on_dismissed)
        self.bubble.expand_detail.connect(self._on_expand_detail)
        self.bubble.retry_requested.connect(self._on_retry)
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

    def quit(self) -> None:
        try:
            if self.supervisor is not None:
                self.supervisor.stop()      # P4：整个 App 的引擎一起停
            else:
                self.client.stop()
        except Exception:
            pass
        QApplication.instance().quit()
        QTimer.singleShot(800, lambda: __import__("os")._exit(0))  # 兜底强退

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
        """出错：alert(angry) 姿态亮 2.5s 后回 idle。"""
        self.pet.set_pose(POSE_ALERT)
        QTimer.singleShot(2500, lambda: self.pet.set_pose(POSE_FOR_STATE[self._state]))

    # ── 唤起 / 收起 ───────────────────────────────────────────

    def _toggle_bubble(self) -> None:
        if self.bubble.summoned:
            self.bubble.dismiss()
        else:
            self._place_bubble()
            self.bubble.summon()
            if self._state is PetState.IDLE:
                self._set_state(PetState.AWAKE)

    # ── 首启问称呼（v0.1.7）───────────────────────────────────

    def _maybe_ask_name(self) -> None:
        """首次使用（无 user-profile.json）→ 气泡里问一次称呼；问过/跳过落盘后不再问。"""
        if self._name_greeted or not should_ask_name():
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
            save_user_name("")   # 明确跳过：落盘标记已问过
            self.bubble.add_greeting("好，那就不问啦～想告诉我的时候随时说。")
        else:
            save_user_name(name)
            self.bubble.add_greeting(f"好嘞，{name}！我记住啦～")
        self.bubble.set_input_visible(True)

    def _place_bubble(self) -> None:
        """气泡固定在桌宠正上方：底边（含尾巴）与桌宠头顶留 BUBBLE_PET_GAP 间隙，
        右下尾巴尖对准桌宠中心；顶部空间不足时落桌宠下方，同样不重叠（v0.1.6）。"""
        b, p = self.bubble, self.pet
        b._refresh_height()  # v0.1.7：先按当前内容定高再锚定（旧高度会算出错误锚点）
        screen = QApplication.primaryScreen().availableGeometry()
        x = p.x() + p.width() // 2 - (b.width() - 44 + 10)  # 尾巴尖 ≈ 桌宠中心
        x = max(screen.left() + 8, min(x, screen.right() - b.width() - 8))
        y_above = p.y() - b.height() - T.BUBBLE_PET_GAP  # 气泡底含尾巴，间隙即不压人物
        if y_above >= screen.top() + 8:
            y = y_above
        else:  # 上方空间不足 → 放桌宠下方
            y = p.y() + p.height() + T.BUBBLE_PET_GAP
        b.move(x, y)

    def _on_pet_moved(self, _x: int, _y: int) -> None:
        """拖人物 → 气泡实时跟随，保持正上方（v0.1.6）。"""
        if self.bubble.summoned:
            self._place_bubble()

    def _on_bubble_moved(self, _x: int, _y: int) -> None:
        """拖气泡 → 人物跟到气泡尾巴正下方，二者作为整体移动（v0.1.6）。"""
        b, p = self.bubble, self.pet
        screen = QApplication.primaryScreen().availableGeometry()
        x = b.x() + (b.width() - 44 + 10) - p.width() // 2  # 桌宠中心对尾巴尖
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
        # 收起时确认条还悬着 → 按「取消」答复引擎（rpc-contract §5.3 cancelled）
        if self._confirm_id:
            self._resolve_confirm(cancelled=True)
        if self._state is PetState.AWAKE:
            self._set_state(PetState.IDLE)

    def _on_escape(self) -> None:
        """Esc：生成中 = 打断；否则 = 收起（interaction-spec §2）。"""
        if self.ctrl.busy:
            self.abort()
        elif self.bubble.summoned:
            self.bubble.dismiss()

    def eventFilter(self, obj, ev):
        """点气泡/桌宠之外的区域 → 收起（显式点击收起；失焦本身不收起）。"""
        if (ev.type() == QEvent.Type.MouseButtonPress and self.bubble.summoned
                and not self._detail_open
                and obj not in (self.bubble, self.pet)):
            gp = ev.globalPosition().toPoint() if hasattr(ev, "globalPosition") else None
            if gp is not None:
                if not self.bubble.geometry().contains(gp) and not self.pet.geometry().contains(gp):
                    self.bubble.dismiss()
        return super().eventFilter(obj, ev)

    # ── 发送 / 打断 ───────────────────────────────────────────

    def send(self, text: str) -> None:
        # v0.1.7 首启问称呼：等待称呼时，像称呼的输入拦截落盘，不进引擎；
        # 不像称呼（长句/带标点）则当正常提问放行，本次会话不再拦。
        if self._awaiting_name:
            if text.strip() in _NAME_SKIP_WORDS or self._looks_like_name(text):
                self._handle_name_reply(text)
                return
            self._awaiting_name = False
        item = self.coordinator.enqueue(text, "pet")
        self.bubble.add_user_message(text)
        if self.ctrl.busy or (self.supervisor is not None and self.supervisor.busy_except(self.ctrl)):
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
        self._last_user_text = item.text
        write_last_user_text(self.client.home, item.text)
        self._status_block = self.bubble.add_status(STATUS_LINE[PetState.THINK])
        self._set_state(PetState.THINK)
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
        live_ids = {item.id for item in self.coordinator.queue if item.source == "pet"}
        for item_id in list(self._queue_indicators):
            if item_id not in live_ids:
                indicator = self._queue_indicators.pop(item_id)
                self.bubble.remove_widget(indicator)
        QTimer.singleShot(0, self._drain_queue)

    def _on_request_committed(self, request_id: str) -> None:
        if request_id in self._queue_requests:
            self._queue_requests.pop(request_id, None)
            self.coordinator.acknowledge(request_id)

    def _on_request_failed(self, request_id: str, _error: str) -> None:
        if request_id in self._queue_requests:
            self._queue_requests.pop(request_id, None)
            self.coordinator.hold_for_review(request_id)

    def abort(self) -> None:
        if self._confirm_id:
            self._resolve_confirm(cancelled=True)
        if self.ctrl.busy:
            self._aborted = True
            self.ctrl.abort()
            if self._status_block is not None:
                self._status_block.set_text("已停止（保留已产内容）")

    # ── 会话编排信号 ──────────────────────────────────────────

    def _on_busy_changed(self, busy: bool) -> None:
        self.bubble.set_busy(busy)
        if not busy:
            QTimer.singleShot(0, self._drain_queue)
            if self._state not in (PetState.IDLE,) and self._state is not PetState.AWAKE:
                self._set_state(PetState.AWAKE if self.bubble.summoned else PetState.IDLE)

    def _on_answer_done(self, answer: str) -> None:
        self._last_answer = answer

    def _on_summarizing(self) -> None:
        if self._status_block is not None:
            self._status_block.set_text(STATUS_LINE[PetState.CONVERGE])
        self._set_state(PetState.CONVERGE)

    def _on_summary_done(self, summary: str) -> None:
        if self._status_block is not None:
            self._status_block.set_text("想好了 ✓" if not self._aborted else "已停止（基于已产内容）")
            self._status_block = None
        if summary:
            self._last_summary = summary
            self.bubble.add_summary(summary)
        # §5 动态对话流：回合结束、可追问时再弹出输入区
        self.bubble.set_input_visible(True)
        # 短结是「需要用户注意」的时刻：气泡没挂着就轻提示唤起
        if not self.bubble.summoned:
            self._place_bubble()
            self.bubble.summon()
        self._set_state(PetState.AWAKE)

    def _on_failed(self, err: str) -> None:
        self._status_block = None
        self.bubble.add_error(err)
        self.bubble.set_input_visible(True)  # 出错可重试/重新提问
        self._alert_pose_then_idle()
        if not self.bubble.summoned:
            self._place_bubble()
            self.bubble.summon()
        self._set_state(PetState.AWAKE)

    def _on_retry(self) -> None:
        if self.ctrl.busy:
            return
        pending = next(
            (item for item in self.coordinator.queue if item.source == "pet" and item.needs_review),
            None,
        )
        if pending is not None:
            self.coordinator.retry(pending.id)
        elif self._last_user_text:
            self.send(self._last_user_text)

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
                self._confirm_id = ev.get("id")
                text = "；".join(x for x in (str(ev.get("title", "")).strip(),
                                             str(ev.get("message", "")).strip()) if x)
                self.bubble.show_confirm(text or "允许这次操作吗？")
                self._set_state(PetState.PERCEIVE)
                if not self.bubble.summoned:  # 确认需要用户注意 → 唤起
                    self._place_bubble()
                    self.bubble.summon()
            else:
                # 未实现的 method 一律取消（rpc-contract §5.3）
                self.client.respond_ui(ev.get("id"), cancelled=True)
        elif t == "tool_execution_start" and ev.get("toolName") == "read_screen":
            self.bubble.add_perception_hint(STATUS_LINE[PetState.PERCEIVE])
            if self._status_block is not None:
                self._status_block.set_text(STATUS_LINE[PetState.ACT])
            self._set_state(PetState.ACT)
        elif t == "tool_execution_end" and self._state is PetState.ACT:
            self._set_state(PetState.THINK)  # 工具完毕，回第二 turn 生成

    def _on_confirm_resolved(self, ok: bool) -> None:
        self._resolve_confirm(confirmed=ok)

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
        self.client.new_session()
        self._set_state(PetState.AWAKE if self.bubble.summoned else PetState.IDLE)

    def _on_settings(self) -> None:
        log.info("settings placeholder clicked (P4 接配置面板)")
        self.settings_requested.emit()

    def _on_expand_detail(self) -> None:
        """「展开详细」（v0.1.4 hotfix）：改回打开完整对话窗口的「从气泡展开」模式。

        PetApp 不持有 chat 引用：壳层注入 detail_opener（= ChatWindow.open_from_bubble），
        并接 ChatWindow.detail_collapsed → restore_bubble。未注入时为空操作。
        """
        if not self._last_answer and not self._last_summary:
            return
        if self.detail_opener is None:
            log.warning("detail_opener 未注入（应由壳层接线 chat.open_from_bubble）")
            return
        # 打开时气泡隐藏（不播收起动画、不动 _shown 标记），对话窗口从气泡 rect 长出
        rect = self.bubble.geometry()
        self._detail_open = True
        self.bubble.hide()
        self.detail_opener(rect)

    def restore_bubble(self) -> None:
        """详情收起（ChatWindow.detail_collapsed）→ 气泡原样恢复显示。"""
        self._detail_open = False
        if self.bubble.summoned:
            self.bubble.show()
            self.bubble.raise_()

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
        self._status_block = None
        self.bubble.add_error(f"引擎已退出，自动重启中（第 {attempt} 次）…")
        self._alert_pose_then_idle()
        if not self.bubble.summoned:
            self._place_bubble()
            self.bubble.summon()

    def _on_sup_restarted(self) -> None:
        self.bubble.add_status("引擎已自动重启 ✓ 会话已恢复")
        QTimer.singleShot(0, self._drain_queue)

    def _on_sup_restart_failed(self) -> None:
        self._status_block = None
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
