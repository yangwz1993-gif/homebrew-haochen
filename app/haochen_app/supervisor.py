"""引擎进程管家（P4 集成核心）：唯一 EngineClient + 崩溃自动重启 + 会话恢复。

职责（开发总纲 §二 P4「进程管理：引擎随 App 启停、崩溃重启」）：

- 持有全 App 唯一的 EngineClient（对话窗口 / 桌宠 / 设置共用 → 引擎侧天然同一会话）；
- 崩溃感知 → 指数退避自动重启（1s/2s/5s，3 次失败 → restart_failed，不白屏）；
- 重启成功后 switch_session 回崩溃前会话（「追问不丢上下文」在重启后依然成立）；
- turn 仲裁：register() 收集各 UI 的 ConversationController，busy_except() 供
  发送方判断「另一入口正在生成」，避免双入口并发 prompt 打爆引擎。

信号语义：
    crashed(int)        引擎意外退出（UI 弹横幅：自动重启中）
    restarting(int)     第 n 次重启尝试开始
    restarted()         重启成功且会话已恢复（UI 清横幅、刷新状态）
    restart_failed()    连续 MAX_ATTEMPTS 次失败（UI 给手动重试入口 → restart_now()）
    state_changed(dict) get_state 数据（sessionFile/model），每轮 agent_end 后刷新

Standalone 兼容：不传 supervisor 的 UI 模块（run_chat.py / run_pet.py）行为不变。
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .engine_client import EngineClient
from .session_coordinator import SessionCoordinator

log = logging.getLogger("haochen.supervisor")


class EngineSupervisor(QObject):
    MAX_ATTEMPTS = 3
    BACKOFF_MS = [1000, 2000, 5000]
    PROBE_TIMEOUT_MS = 8000
    HEARTBEAT_MS = 30_000

    crashed = pyqtSignal(int)
    restarting = pyqtSignal(int)
    restarted = pyqtSignal()
    restart_failed = pyqtSignal()
    state_changed = pyqtSignal(dict)

    def __init__(self, mock: bool | None = None, engine: Path | None = None,
                 home: Path | None = None, parent: QObject | None = None,
                 request_timeout_s: float = 5.0):
        super().__init__(parent)
        self.client = EngineClient(
            mock=mock,
            engine=engine,
            home=home,
            request_timeout_s=request_timeout_s,
        )
        self.coordinator = SessionCoordinator(self.client.home, parent=self)
        self._pending: dict[str, callable] = {}
        self._ctrls: list = []
        self._attempt = 0
        self._restarting = False
        self._stopping = False
        self._started_once = False
        self._probe_timer: QTimer | None = None
        self._heartbeat_pending = False
        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.setInterval(self.HEARTBEAT_MS)
        self._heartbeat_timer.timeout.connect(self._heartbeat)

        self.client.response.connect(self._on_response)
        self.client.event.connect(self._on_event)
        self.client.crashed.connect(self._on_crashed)

    # ── turn 仲裁（双入口不同时打引擎）─────────────────────────

    def register(self, ctrl) -> None:
        if ctrl not in self._ctrls:
            self._ctrls.append(ctrl)

    def unregister(self, ctrl) -> None:
        if ctrl in self._ctrls:
            self._ctrls.remove(ctrl)

    def busy_except(self, ctrl) -> bool:
        """除 ctrl 自己外，是否有别的入口在一轮对话中。"""
        return any(c.busy for c in self._ctrls if c is not ctrl)

    # ── 生命周期 ──────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self.client.alive

    def start(self) -> None:
        """启动引擎（首次）；失败自动进入重启流程（3 次后 restart_failed）。"""
        self._stopping = False
        try:
            self.client.start()
        except Exception as exc:  # noqa: BLE001 — 二进制缺失等
            log.error("engine start failed: %s", exc)
            if not self._restarting:
                self._attempt = 0
                self._schedule_restart()
            return
        self._started_once = True
        self._heartbeat_timer.start()
        self._ask_state(self._initial_state)

    def stop(self) -> None:
        self._stopping = True
        if self._probe_timer:
            self._probe_timer.stop()
        self._heartbeat_timer.stop()
        self._heartbeat_pending = False
        self._pending.clear()
        self.client.stop()

    def restart_now(self) -> None:
        """手动重试（restart_failed 后的入口；或配置变更要求重启）。"""
        if self._restarting:
            return
        self._attempt = 0
        self.client.stop()
        self._schedule_restart()

    # ── 崩溃 → 自动重启 ───────────────────────────────────────

    def _on_crashed(self, code: int) -> None:
        self._heartbeat_pending = False
        if self._stopping:
            return
        log.warning("engine exited code=%s (restarting=%s)", code, self._restarting)
        if self._restarting:
            # 重启探活窗口内再次崩溃 → 视为本次尝试失败，直接进下一次
            self._cancel_probe()
            self._schedule_restart()
            return
        self.crashed.emit(code)
        self._attempt = 0
        self._schedule_restart()

    def _schedule_restart(self) -> None:
        self._attempt += 1
        if self._attempt > self.MAX_ATTEMPTS:
            log.error("engine restart gave up after %d attempts", self.MAX_ATTEMPTS)
            self._restarting = False
            self.restart_failed.emit()
            return
        self._restarting = True
        self.restarting.emit(self._attempt)
        QTimer.singleShot(self.BACKOFF_MS[self._attempt - 1], self._try_start)

    def _try_start(self) -> None:
        if self._stopping or not self._restarting:
            return
        try:
            self.client.start()
        except Exception as exc:  # noqa: BLE001
            log.error("restart attempt %d spawn failed: %s", self._attempt, exc)
            self._schedule_restart()
            return
        # 探活：PROBE_TIMEOUT 内 get_state 有响应才算成功
        self._probe_timer = QTimer(self)
        self._probe_timer.setSingleShot(True)
        self._probe_timer.timeout.connect(self._probe_timeout)
        self._probe_timer.start(self.PROBE_TIMEOUT_MS)
        self._ask_state(self._probe_ok)

    def _probe_timeout(self) -> None:
        log.error("restart attempt %d probe timeout", self._attempt)
        self.client.stop()
        self._schedule_restart()

    def _probe_ok(self, resp: dict) -> None:
        if not self._restarting:
            return  # 迟到响应
        if not resp.get("success"):
            self._cancel_probe()
            self.client.stop()
            self._schedule_restart()
            return
        self._cancel_probe()
        current = (resp.get("data") or {}).get("sessionFile") or ""
        # 恢复崩溃前会话（追问不丢上下文，跨重启也成立）
        # mock 会话是 mock-session:// 伪路径（非文件），存在性检查仅对真实文件路径
        restore_path = self.coordinator.current_session
        restore_ok = (restore_path and current != restore_path
                      and ("://" in restore_path or Path(restore_path).exists()))
        if restore_ok:
            log.info("restoring session %s", restore_path)
            self._rpc(self.client.switch_session, self._restore_session_done, restore_path)
        else:
            self._finish_restart()

    def _restore_session_done(self, resp: dict) -> None:
        if resp.get("success") and not (resp.get("data") or {}).get("cancelled"):
            self._finish_restart()
            return
        self._restarting = False
        log.error("engine restarted but session restore failed")
        self.restart_failed.emit()

    def _cancel_probe(self) -> None:
        if self._probe_timer:
            self._probe_timer.stop()
            self._probe_timer = None

    def _finish_restart(self) -> None:
        self._restarting = False
        self._attempt = 0
        self._heartbeat_timer.start()
        self._ask_state(self._stash_state)
        log.info("engine restarted ok")
        self.restarted.emit()

    # ── 状态跟踪（崩溃恢复依据 + UI 状态栏）────────────────────

    def _on_event(self, ev: dict) -> None:
        if ev.get("type") == "agent_end" and self.client.alive and not self._restarting:
            self._ask_state(self._stash_state)  # 每轮结束刷新当前会话路径

    def _initial_state(self, resp: dict) -> None:
        if resp.get("success"):
            self._stash_state(resp)
        elif resp.get("errorCode") == "timeout":
            self._handle_unresponsive()

    def _heartbeat(self) -> None:
        if self._stopping or self._restarting or self._heartbeat_pending or not self.client.alive:
            return
        self._heartbeat_pending = True
        self._ask_state(self._heartbeat_result)

    def _heartbeat_result(self, resp: dict) -> None:
        self._heartbeat_pending = False
        if resp.get("success"):
            self._stash_state(resp)
        elif resp.get("errorCode") == "timeout":
            self._handle_unresponsive()

    def _handle_unresponsive(self) -> None:
        if self._stopping or self._restarting:
            return
        log.error("engine heartbeat timed out")
        self.client.stop()
        self.crashed.emit(-1)
        self._attempt = 0
        self._schedule_restart()

    def _stash_state(self, resp: dict) -> None:
        if not resp.get("success"):
            return
        data = resp.get("data") or {}
        path = data.get("sessionFile") or ""
        if path:
            self.coordinator.set_current_session(path)
        self.state_changed.emit(data)

    # ── 自家 RPC（id 关联，契约 §1.2）──────────────────────────

    def _rpc(self, sender, callback, *args) -> str | None:
        try:
            rid = sender(*args)
        except RuntimeError:
            return None
        self._pending[rid] = callback
        return rid

    def _ask_state(self, callback) -> str | None:
        return self._rpc(self.client.get_state, callback)

    def _on_response(self, resp: dict) -> None:
        cb = self._pending.pop(resp.get("id", ""), None)
        if cb:
            cb(resp)
