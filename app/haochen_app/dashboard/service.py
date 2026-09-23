"""Bounded collector scheduling, durable observations and independent summaries."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from ..background import run_in_background
from .adapters.browser import BrowserAdapter
from .adapters.calendar import CalendarAdapter
from .adapters.hi import HiAdapter
from .adapters.otty import OttyAdapter
from .adapters.tianti import TiantiAdapter
from .adapters.wechat import WeChatAdapter
from .attention import activity
from .store import FREQUENCIES, DashboardStore, now
from .tracking import SummaryWorker, daily_report, refresh_track

log = logging.getLogger("haochen.dashboard")

NAMES = {"otty": "Otty", "browser": "Chrome", "calendar": "日历", "wechat": "微信", "hi": "Hi",
         "tianti": "天梯日报"}

# 2026-09-17 天梯日报功能暂缓：用户反馈看板与提醒效果不达预期，后续做产品优化后再开放。
# 代码（适配器/登录窗/追踪同步）全部保留，仅不注册、不轮询、不出卡片；重新开放把开关拨回 True 即可。
ENABLE_TIANTI = False

# C-11 二维状态：内容态的「失效」按来源定义（连接态由适配器 status 映射）。
# 归档（archived，如关闭超 12h 的旧追踪页）与过期（stale）不参与失效计数。
_COVERAGE_BROKEN = {
    "browser": {"tab_closed", "suspended", "target_changed", "permission_required", "error", "failed"},
    "otty": {"unknown"},
    "hi": {"error", "failed", "permission_required"},
    "wechat": {"error", "failed", "permission_required"},
    "calendar": {"error", "failed", "permission_required"},
}


class DashboardService(QObject):
    changed = pyqtSignal()
    notice = pyqtSignal(str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.store = DashboardStore(config.home)
        settings = self.store.snapshot()["settings"]
        self.adapters = {"otty": OttyAdapter(), "browser": BrowserAdapter(config.home),
                         "calendar": CalendarAdapter(settings.get("calendarIds")), "wechat": WeChatAdapter(),
                         "hi": HiAdapter()}
        if ENABLE_TIANTI:
            self.adapters["tianti"] = TiantiAdapter(config.home, keychain=getattr(config, "keychain", None))
        self.adapters["browser"].set_enabled(settings["connectors"].get("browser", False))
        self.summarizer = SummaryWorker(config)
        self.connectors = {}
        self.otty_diagnostics = {}
        self._seen_connected = set()
        self.active = set()
        self.last_poll = {}
        self.generations = dict.fromkeys(self.adapters, 0)
        self.visible = False
        self.stopped = False
        self._track_busy = False
        self._current_track = None
        self._current_cancel = None
        self._queued_tracks = []
        self._calendar_selection_lock = threading.Lock()
        self._calendar_selection_generation = None
        self._calendar_selection_error = False
        self._last_report = 0
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)

    def start(self):
        self.timer.start()
        self.refresh()

    def stop(self):
        self.stopped = True
        self.timer.stop()
        if self._current_cancel is not None:
            self._current_cancel.set()
        self._queued_tracks.clear()
        self.summarizer.stop()

    def _tick(self):
        if self.stopped:
            return
        snapshot = self.store.snapshot()
        for identifier in self.adapters:
            # Hi and calendar hit network APIs with the user's OAuth; poll them
            # on a slow cadence regardless of visibility to avoid rate limits.
            if identifier == "calendar":
                interval = 120
            elif identifier == "hi":
                interval = 60
            elif self.visible or identifier == "otty":
                interval = 3
            else:
                interval = 20
            if time.monotonic() - self.last_poll.get(identifier, 0) >= interval:
                self._collect(identifier)
        if not self._track_busy:
            for track in snapshot["tracks"]:
                if track.get("paused") or track.get("completed") or not FREQUENCIES[track["frequency"]]:
                    continue
                if not track.get("nextCheckAt") or track["nextCheckAt"] <= now():
                    self.refresh_track(track["id"])
                    break
        if time.monotonic() - self._last_report > 300:
            self.report(datetime.now().astimezone().date().isoformat())
            self._last_report = time.monotonic()

    def refresh(self):
        for identifier in self.adapters:
            self._collect(identifier)

    def _collect(self, identifier, *, force=False):
        if self.stopped or identifier in self.active:
            return
        # 日报类内容 15 分钟一轮足够；跟 Otty 的 4 秒节奏跑会把日志和服务器都刷爆。
        # force=True 用于用户刚连上/手动刷新等明确时机，不受节流限制。
        if (identifier == "tianti" and not force
                and time.monotonic() - self.last_poll.get("tianti", -900) < 900):
            return
        if identifier == "calendar" and (
            self._calendar_selection_generation is not None or self._calendar_selection_error
        ):
            return
        if not self.store.snapshot()["settings"]["connectors"].get(identifier):
            return
        self.active.add(identifier)
        self.last_poll[identifier] = time.monotonic()
        generation = self.generations[identifier]
        source_revision = self.store.source_revision(identifier)

        def work():
            try:
                result = self.adapters[identifier].snapshot()
            except Exception:
                result = {"status": "error", "message": "来源检查失败，旧信息已标记失效，稍后会重试",
                          "checkedAt": now(), "events": []}
            if identifier == "browser":
                try:
                    result["setup"] = self.adapters["browser"].setup_state()
                except Exception:
                    result["setup"] = {"stage": "bridge", "message": "连接诊断暂不可用，请重试"}
            if (identifier == "otty" and not self.otty_diagnostics and not self.stopped
                    and generation == self.generations[identifier]):
                diagnostic = getattr(self.adapters["otty"], "diagnostic", None)
                if diagnostic:
                    try:
                        result["diagnostics"] = diagnostic()
                    except Exception:
                        # Setup guidance must not discard a successful live snapshot.
                        result["diagnostics"] = {"message": "集成检查暂不可用，请重试", "agents": []}
            # Disabled/revoked sources cannot be repopulated by an old worker.
            if (not self.stopped and generation == self.generations[identifier]
                    and self.store.snapshot()["settings"]["connectors"].get(identifier)):
                if result.get("status") == "permission_required" or any(
                    event.get("state", event.get("status")) == "permission_required"
                    for event in result.get("events", [])
                ):
                    self._cancel_for_source(identifier)
                self.store.observe(identifier, result, revision=source_revision)
                # 天梯日报：核心事实卡全自动同步到「我在追踪」（连接成功且有项目数据时）
                if identifier == "tianti" and result.get("status") == "ready" and result.get("projects"):
                    try:
                        created, updated = self.store.sync_tianti_tracks(result["projects"])
                        if created or updated:
                            self.notice.emit(f"天梯日报：已同步 {created + updated} 个核心事实卡到「我在追踪」")
                    except Exception:  # noqa: BLE001 - 同步失败不影响主快照入库
                        log.exception("tianti track sync failed")
            return result

        def done(result, error):
            self.active.discard(identifier)
            if self.stopped:
                return
            if generation != self.generations[identifier]:
                # A scope update may finish before an older collector's queued
                # completion. Restart after releasing active, not 120s later.
                self._collect(identifier)
                return
            self.connectors[identifier] = result if error is None else {
                "status": "error", "message": "连接暂不可用，请稍后重试", "checkedAt": now()}
            if result and (result.get("status") in {"ready", "connected"}
                           or result.get("status") == "partial" and result.get("events")):
                self._seen_connected.add(identifier)
            if identifier == "otty" and result and result.get("diagnostics"):
                self.otty_diagnostics = result["diagnostics"]
            self.changed.emit()

        run_in_background(self, work, done)

    def state(self):
        state = self.store.snapshot()
        settings = state["settings"]
        connectors = []
        for identifier in self.adapters:
            enabled = settings["connectors"].get(identifier, False)
            raw = self.connectors.get(identifier, {})
            status = raw.get("status", "pending") if enabled else "disabled"
            status = {"ready": "connected", "partial": "limited", "not_running": "unavailable",
                      "pending": "unavailable", "not_connected": "unavailable",
                      "disconnected": "unavailable"}.get(status, status)
            message = raw.get("message", "准备连接" if enabled else "尚未连接；点击后按需授权")
            # 二维状态（C-11）：连接态只管管道活没活（partial 也是已连接）；
            # 内容态只管追踪的东西还产不产出。两者分离，互不污染。
            connection = "disabled" if not enabled else {
                "ready": "connected", "connected": "connected", "partial": "connected",
                "not_running": "unavailable", "pending": "unavailable",
                "not_connected": "unavailable", "disconnected": "unavailable",
                "error": "error", "permission_required": "permission_required",
            }.get(raw.get("status", "pending"), raw.get("status", "pending"))
            source_events = [e for e in state["events"] if e.get("source") == identifier]
            if identifier == "otty":
                # 有 agent 但未上报生命周期才算失效；无 agent 的普通终端不算
                broken = [e for e in source_events
                          if e.get("reasonCode") == "lifecycle_not_reported" and not e.get("stale")]
            else:
                broken_states = _COVERAGE_BROKEN.get(identifier, {"error", "failed"})
                broken = [e for e in source_events
                          if (e.get("state") or e.get("status")) in broken_states
                          and not e.get("stale") and not e.get("archived")]
            coverage = {"total": len(source_events), "broken": len(broken),
                        "level": "partial" if broken else ("full" if source_events else "empty")}
            connector = {"id": identifier, "name": NAMES[identifier], "enabled": enabled,
                         "status": status, "summary": message, "checkedAt": raw.get("checkedAt"),
                         "connection": connection, "coverage": coverage,
                         "hasConnected": identifier in self._seen_connected,
                         "checking": identifier in self.active or (
                             identifier == "calendar" and self._calendar_selection_generation is not None)}
            if identifier == "otty":
                connector["diagnostics"] = self.otty_diagnostics
                connector["helpUrl"] = "https://docs.otty.sh/agents/setup"
                connector["capabilities"] = ["官方 CLI 约每 3 秒状态快照，不是完整事件流", "精确跳转 Agent",
                                             "生命周期需安装 Otty 官方集成；极短状态可能处于两次检查之间"]
            if identifier == "browser":
                connector["setup"] = raw.get("setup", {"stage": "extension", "message": "正在检查 Chrome 连接"})
                connector["capabilities"] = ["扩展中明确选择追踪的页面", "逐站授权", "登录态 DOM 正文"]
            if identifier == "wechat":
                connector["reasonCode"] = raw.get("reasonCode")
                connector["capabilities"] = ["仅公开 Dock 未读标记", "打开微信，不定位单聊",
                                             "不读取聊天正文或私人数据库"]
                if not enabled:
                    connector["summary"] = "可按需观察微信 Dock 未读标记；不含聊天正文，未暴露标记时明确显示未知。"
            if identifier == "hi":
                connector["reasonCode"] = raw.get("reasonCode")
                connector["capabilities"] = ["仅聚合你本人的待跟进日程与待处理任务", "只读，不发消息、不读聊天正文",
                                             "任务计数不是聊天未读数"]
                if not enabled:
                    connector["summary"] = ("可按需聚合 Hi 的今日待跟进日程与待处理任务；"
                                            "只读、不发消息，不代表真实未读数。")
            if identifier == "tianti":
                connector["reasonCode"] = raw.get("reasonCode")
                connector["capabilities"] = ["只读拉取你的日报与核心事实卡", "cookie 存 Keychain、不过第三方",
                                             "失效时点一下即可重连，不用手动找 cookie"]
                if not enabled:
                    connector["summary"] = "连接后每天自动读你的天梯日报，核心事实卡自动进追踪。"
            connectors.append(connector)
        connectors.extend([
            {"id": "lark", "name": "飞书", "enabled": False, "status": "disabled", "summary": "按计划延期接入。"},
        ])
        state["connectors"] = connectors
        state["activity"] = activity(state["events"], connectors)
        state["calendar"] = [self._calendar_view(e) for e in state["events"] if e.get("source") == "calendar"]
        state["events"] = [e for e in state["events"] if e.get("source") != "calendar"]
        state["updatedAt"] = now()
        state.pop("history", None)
        state.pop("schema", None)
        state.pop("readVersions", None)
        # Local file references and selected evidence only; never configuration credentials.
        return state

    @staticmethod
    def _calendar_view(event):
        return {**event, "startAt": event.get("startAt", event.get("start", event.get("occurredAt"))),
                "endAt": event.get("endAt", event.get("end")), "eventId": event["id"]}

    def enable(self, identifier, enabled):
        if identifier not in self.adapters:
            raise ValueError("此来源尚无可靠连接器")
        self.generations[identifier] += 1
        if identifier == "calendar" and self._calendar_selection_generation is not None:
            self._calendar_selection_generation = None
            self._calendar_selection_error = True  # Reapply desired scope on reconnect.
        if not enabled:
            self._seen_connected.discard(identifier)
            self._cancel_for_source(identifier)
        self.store.enable(identifier, enabled)
        if identifier == "browser":
            self.adapters["browser"].set_enabled(enabled)
        if enabled and identifier == "calendar":
            if self._calendar_selection_error:
                self.select_calendars(self.store.snapshot()["settings"].get("calendarIds", []))
            # This is called only in response to an explicit Connect button.
            def permission_result(granted, message):
                if self.stopped:
                    return
                self.notice.emit(message)
                self._collect("calendar")
                self.changed.emit()
            self.adapters["calendar"].request_access(permission_result)
        elif enabled:
            self._collect(identifier)
        self.changed.emit()

    def _cancel_for_source(self, identifier, snapshot=None):
        if self._current_track is None:
            return
        snapshot = self.store.snapshot() if snapshot is None else snapshot
        source_ids = {value for event in snapshot["events"] if event.get("source") == identifier
                      for value in (event["id"], event.get("sourceId")) if value}
        track = next((track for track in snapshot["tracks"] if track["id"] == self._current_track), None)
        if track and any(
            (source["type"] == "url" and identifier == "browser")
            or (source["type"] == "connector" and (
                source["locator"].startswith(identifier + ":") or source["locator"] in source_ids
            )) for source in track["sources"]
        ):
            self.cancel_track(track["id"])

    def select_calendars(self, ids):
        # Clear scope-derived state/revisions synchronously, before any result
        # can cross back to the UI. EventKit's own lock is never waited on here.
        before = self.store.snapshot()
        if (not isinstance(ids, list) or len(ids) > 100
                or any(not isinstance(item, str) or not item or len(item) > 1024 for item in ids)):
            raise ValueError("请选择有效日历")
        if before["settings"].get("calendarIds") != list(dict.fromkeys(ids)):
            # Set cancellation before the store's disk write too: an existing
            # job must not start its model request while scope cleanup is saving.
            self._cancel_for_source("calendar", before)
        changed = self.store.calendars_update(ids)
        if not changed and not self._calendar_selection_error:
            return
        self.generations["calendar"] += 1
        generation = self.generations["calendar"]
        selected = list(self.store.snapshot()["settings"].get("calendarIds", []))
        self._calendar_selection_generation = generation
        self._calendar_selection_error = False
        self.connectors.pop("calendar", None)
        self.changed.emit()

        def work():
            # Serialize selection workers, then check the generation *inside*
            # the lock. An obsolete worker must never overwrite a newer scope
            # after waiting for EventKit or another selection worker.
            with self._calendar_selection_lock:
                if self.stopped or generation != self.generations["calendar"]:
                    return
                self.adapters["calendar"].set_selected_calendar_ids(selected)

        def done(_result, error):
            if self.stopped or generation != self.generations["calendar"]:
                return
            self._calendar_selection_generation = None
            self._calendar_selection_error = error is not None
            if error:
                self.connectors["calendar"] = {
                    "status": "unavailable", "message": "日历选择未能应用，请重新保存选择；旧范围不会继续采集",
                    "checkedAt": now(),
                }
            else:
                self._collect("calendar")
            self.changed.emit()

        run_in_background(self, work, done)

    def refresh_track(self, identifier):
        if self.stopped:
            return
        snapshot = self.store.snapshot()
        track = next((t for t in snapshot["tracks"] if t["id"] == identifier), None)
        if track is None:
            raise ValueError("此事项已不存在")
        if self._track_busy:
            if identifier not in self._queued_tracks:
                self._queued_tracks.append(identifier)
            self.notice.emit("已加入检查队列")
            return
        self._track_busy = True
        self._current_track = identifier
        cancellation = self._current_cancel = threading.Event()
        self.store.track_result(identifier, {"status": "checking"})
        self.changed.emit()

        def work():
            if cancellation.is_set() or self.stopped:
                return
            result = refresh_track(track, snapshot, self.adapters["browser"], self.summarizer, cancel=cancellation)
            if not self.stopped and not cancellation.is_set():
                self.store.track_result(identifier, result, revision=track["revision"])

        def done(_result, error):
            self._track_busy = False
            self._current_track = None
            self._current_cancel = None
            if self.stopped:
                return
            if error and not cancellation.is_set():
                self.store.track_result(identifier, {"status": "unavailable", "error": "检查未完成，请重试",
                                                     "lastCheckedAt": now()}, revision=track["revision"])
            self.changed.emit()
            if self._queued_tracks:
                next_id = self._queued_tracks.pop(0)
                if any(t["id"] == next_id for t in self.store.snapshot()["tracks"]):
                    self.refresh_track(next_id)

        run_in_background(self, work, done)

    def cancel_track(self, identifier):
        self._queued_tracks = [value for value in self._queued_tracks if value != identifier]
        if self._current_track == identifier:
            if self._current_cancel is not None:
                self._current_cancel.set()
            self.summarizer.cancel()

    def dismiss_event(self, event_id):
        """忽略一条动态（B-11）：从列表移除并持久化；浏览器来源同时停止追踪。"""
        snapshot = self.store.snapshot()
        event = next((e for e in snapshot["events"] if e.get("id") == event_id), None)
        if event is None:
            raise ValueError("此动态已不存在，请刷新后查看")
        if event.get("source") == "otty":
            raise ValueError("Agent 实时状态不可忽略（它反映当前实况）")
        self.store.dismiss_event(event_id, event.get("fingerprint"))
        if event.get("source") == "browser" and event.get("sourceId"):
            try:
                self.adapters["browser"].untrack(event["sourceId"])
            except Exception:  # noqa: BLE001 — 桥不可用不阻塞忽略动作
                pass
        self.changed.emit()

    def report(self, date):
        report = daily_report(self.store.snapshot(), date)
        self.store.put_report(report)
        self.changed.emit()
        return report
