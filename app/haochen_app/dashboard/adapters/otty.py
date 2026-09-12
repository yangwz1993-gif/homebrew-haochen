"""Read Otty's documented CLI metadata; never scrape transcripts or install hooks.

Otty 1.4.1 exposes pane/tab/window IDs and hook-reported lifecycle in ``--json``
output. An empty lifecycle is *unknown*, not a completed task. The UI explicitly
opts into this connector and schedules snapshot() on a worker thread.
"""

from __future__ import annotations

import copy
import json
import os
import re
import selectors
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from haochen_app.dashboard.adapters.otty_setup import LABELS, OttyIntegrationSetup, agent_kind

BUNDLE_ID = "io.appmakes.otty"
HOOKS_HELP_URL = "https://docs.otty.sh/agents/setup"
_ID = re.compile(r"[ptw]_[A-Za-z0-9_-]{1,160}\Z")
_MAX_OUTPUT = 1024 * 1024
_MAX_PANES = 500
_STATE_TEXT = {
    "processing": "正在处理",
    "awaiting": "等待你输入或授权",
    "idle": "当前空闲（不代表事项已完成）",
    "unknown": "已找到这个终端，但 Agent 尚未上报运行状态；请检查 Otty 的 Agent 集成。",
}


class OttyError(ValueError):
    """A safe, non-transcript error at the connector boundary."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _text(value, limit: int = 400) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(c for c in value if c >= " " or c in "\n\t")[:limit].strip()


def _identifier(value, prefix: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value) or not value.startswith(prefix + "_"):
        raise OttyError("invalid_target", "Otty 返回了无法识别的窗口标识，请刷新后重试。")
    return value


def _bounded_process(argv: list[str], *, timeout: float = 2.0, max_output: int = _MAX_OUTPUT) -> bytes:
    """Drain only a bounded stdout buffer and kill/reap our own child on failure.

    stderr is discarded: provider errors sometimes contain working directories
    or terminal titles and must not become a dashboard log or an exception body.
    """
    child = subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        shell=False, start_new_session=True,
    )
    output = bytearray()
    deadline = time.monotonic() + timeout
    try:
        assert child.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise OttyError("timeout", "Otty 响应超时，稍后会重新连接。")
                for key, _ in selector.select(min(remaining, 0.1)):
                    block = os.read(key.fd, min(65536, max_output + 1 - len(output)))
                    if not block:
                        selector.unregister(key.fileobj)
                        continue
                    output.extend(block)
                    if len(output) > max_output:
                        raise OttyError("output_limit", "Otty 返回的数据过大，本次未更新。")
            try:
                code = child.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as exc:
                raise OttyError("timeout", "Otty 响应超时，稍后会重新连接。") from exc
        if code == 4:
            raise OttyError("removed", "这个 Otty 终端已关闭或不再存在，请刷新列表。")
        if code:
            raise OttyError("connection_failed", "暂时无法读取 Otty，请确认应用正在运行。")
        return bytes(output)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()
        if child.stdout is not None:
            child.stdout.close()


class OttyAdapter:
    def __init__(self, cli_path: str | Path | None = None, *, user_home: Path | None = None):
        self._explicit_cli = Path(cli_path) if cli_path is not None else None
        self._cli: Path | None = None
        self._previous: dict[str, dict] = {}
        self._baseline_valid = False
        self._last_success: str | None = None
        self._lock = threading.RLock()
        self._user_home = user_home

    def _find_cli(self) -> Path | None:
        if self._explicit_cli is not None:
            candidates = [self._explicit_cli]
        else:
            candidates = []
            try:
                from AppKit import NSWorkspace

                url = NSWorkspace.sharedWorkspace().URLForApplicationWithBundleIdentifier_(BUNDLE_ID)
                if url is not None:
                    candidates.append(Path(str(url.path())) / "Contents/MacOS/otty-cli")
            except Exception:
                pass
            candidates.extend([
                Path("/Applications/Otty.app/Contents/MacOS/otty-cli"),
                Path.home() / "Applications/Otty.app/Contents/MacOS/otty-cli",
            ])
        return next((p for p in candidates if p.is_file() and os.access(p, os.X_OK)), None)

    def _is_running(self) -> bool | None:
        try:
            from AppKit import NSRunningApplication

            return bool(NSRunningApplication.runningApplicationsWithBundleIdentifier_(BUNDLE_ID))
        except Exception:
            return None  # Let the bounded CLI report availability; do not launch Otty.

    def discover(self) -> dict:
        """Read installation/running metadata without launching another app."""
        with self._lock:
            self._cli = self._find_cli()
            return {"installed": self._cli is not None, "running": self._is_running()}

    def diagnostic(self, snapshot: dict | None = None) -> dict:
        """Explicit/background check; never return terminal titles or transcripts."""
        if snapshot is None:
            # Checking setup must not consume lifecycle transitions which belong
            # to the service's next observation/history update.
            with self._lock:
                previous, baseline, success = self._previous, self._baseline_valid, self._last_success
                try:
                    snapshot = self.snapshot()
                finally:
                    self._previous, self._baseline_valid, self._last_success = previous, baseline, success
        groups: dict[str, list[dict]] = {}
        unrecognized = 0
        for event in snapshot.get("events", []):
            if not event.get("agent"):
                continue
            kind = agent_kind(event["agent"])
            if kind is None:
                unrecognized += 1
            else:
                groups.setdefault(kind, []).append(event)
        setup = OttyIntegrationSetup(self._cli, self._user_home)
        agents = []
        for kind, events in sorted(groups.items()):
            result = setup.inspect(kind)
            reporting = sum(bool(e.get("sessionId")) and e.get("state") in {"processing", "idle", "awaiting"}
                            and not e.get("stale") for e in events)
            result.update(panes=len(events), reportingCount=reporting)
            if reporting == len(events):
                result.update(reasonCode="reporting", message=f"{LABELS[kind]} 已实际上报运行状态。",
                              nextAction="none", canInstall=False, needsRestart=False)
            elif result["integrationStatus"] == "present":
                result["reasonCode"] = "installed_but_not_reporting"
            agents.append(result)
        return {"status": snapshot["status"], "checkedAt": snapshot["checkedAt"],
                "message": snapshot["message"], "agents": agents, "unrecognizedAgents": unrecognized,
                "paneCount": len(snapshot.get("events", [])), "helpUrl": HOOKS_HELP_URL}

    def setup(self, kind: str, *, apply: bool = False) -> dict:
        """Plan first; only an explicit confirmed click may request apply=True."""
        with self._lock:
            self._cli = self._find_cli()
            return OttyIntegrationSetup(self._cli, self._user_home).setup(kind, apply=apply)

    def _run_cli(self, *args: str):
        if args not in (("pane", "list"), ("tab", "list"), ("window", "list")):
            if len(args) != 4 or args[:2] not in (("pane", "show"), ("pane", "focus")):
                raise OttyError("unsupported_operation", "不支持这个 Otty 操作。")
            if args[2] != "--pane":
                raise OttyError("invalid_target", "缺少精确的 Otty 终端标识。")
            _identifier(args[3], "p")
        if self._cli is None:
            self._cli = self._find_cli()
        if self._cli is None:
            raise OttyError("not_installed", "未找到支持连接的 Otty，请先安装 Otty。")
        try:
            raw = _bounded_process([str(self._cli), "--json", "--timeout", "1200", *args])
            result = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OttyError("invalid_response", "Otty 返回了无法识别的数据，请更新 Otty 后重试。") from exc
        if not isinstance(result, dict) or result.get("ok") is not True or "data" not in result:
            raise OttyError("invalid_response", "Otty 未返回有效的状态数据。")
        if result.get("command") not in (None, " ".join(args[:2])):
            raise OttyError("invalid_response", "Otty 返回的数据与请求不一致。")
        return result["data"]

    @staticmethod
    def _pane(raw: dict) -> dict:
        if not isinstance(raw, dict):
            raise OttyError("invalid_response", "Otty 终端数据格式不正确。")
        return {
            "paneId": _identifier(raw.get("id"), "p"),
            "tabId": _identifier(raw.get("tab_id"), "t"),
            "windowId": _identifier(raw.get("window_id"), "w"),
            "agent": _text(raw.get("agent"), 80),
            "sessionId": _text(raw.get("agent_session_id"), 200),
            "rawState": _text(raw.get("agent_state"), 80),
            "title": _text(raw.get("process")),
            "workingDirectory": _text(raw.get("cwd"), 2048),
            "active": raw.get("active") if isinstance(raw.get("active"), bool) else None,
        }

    def _failure(self, checked: str, status: str, message: str, code: str) -> dict:
        self._baseline_valid = False
        old = []
        for event in self._previous.values():
            item = copy.deepcopy(event)
            item.update(stale=True, previousState=item["state"], state="unknown")
            item["summary"] = "当前无法确认状态，以下为上次成功读取的记录。"
            old.append(item)
        return {
            "status": status, "message": message, "errorCode": code,
            "checkedAt": checked, "lastSuccessAt": self._last_success,
            "events": old, "changes": [], "removedIds": [], "stale": bool(old),
        }

    def snapshot(self) -> dict:
        """Current metadata plus observed transitions; no initial completion events."""
        with self._lock:
            checked = _now()
            self._cli = self._find_cli()
            if self._cli is None:
                return self._failure(checked, "unavailable", "未找到 Otty，请先安装应用。", "not_installed")
            if self._is_running() is False:
                return self._failure(checked, "not_running", "Otty 尚未运行。", "not_running")
            try:
                rows = self._run_cli("pane", "list")
                if not isinstance(rows, list):
                    raise OttyError("invalid_response", "Otty 未返回终端列表。")
            except OttyError as exc:
                status = "not_running" if self._is_running() is False else "unavailable"
                return self._failure(checked, status, str(exc), exc.code)

            incomplete = len(rows) > _MAX_PANES
            panes = {}
            for raw in rows[:_MAX_PANES]:
                try:
                    pane = self._pane(raw)
                    if pane["paneId"] in panes:
                        incomplete = True
                    else:
                        panes[pane["paneId"]] = pane
                except OttyError:
                    incomplete = True

            visibility_available = True
            tabs, windows = {}, {}
            if panes:
                try:
                    tab_rows = self._run_cli("tab", "list")
                    window_rows = self._run_cli("window", "list")
                    if not isinstance(tab_rows, list) or not isinstance(window_rows, list):
                        raise OttyError("invalid_response", "Otty 窗口元数据不完整。")
                    tabs = {r["id"]: r for r in tab_rows if isinstance(r, dict) and isinstance(r.get("id"), str)}
                    windows = {
                        r["id"]: r for r in window_rows if isinstance(r, dict) and isinstance(r.get("id"), str)
                    }
                except OttyError:
                    visibility_available = False

            current, changes = {}, []
            unknown_count = 0
            for pane in panes.values():
                state = pane["rawState"] if pane["rawState"] in _STATE_TEXT else "unknown"
                if state == "unknown" and pane["agent"]:
                    unknown_count += 1
                event_id = "otty:" + pane["paneId"]
                prior = self._previous.get(event_id)
                tab, window = tabs.get(pane["tabId"], {}), windows.get(pane["windowId"], {})
                focused = window.get("focused")
                tab_active = tab.get("active")
                visible = None
                if all(isinstance(v, bool) for v in (focused, tab_active, pane["active"])):
                    visible = focused and tab_active and pane["active"]
                item = {
                    "id": event_id, "source": "otty", "title": pane["title"] or pane["agent"] or "Otty 终端",
                    "summary": _STATE_TEXT[state], "state": state, "rawState": pane["rawState"],
                    "agent": pane["agent"], "sessionId": pane["sessionId"],
                    "workingDirectory": pane["workingDirectory"], "isVisible": visible,
                    "isWindowFocused": focused if isinstance(focused, bool) else None,
                    "observedAt": checked, "updatedAt": checked, "stale": False,
                    "stateSource": "otty_hook" if state != "unknown" else "unavailable",
                    "agentKind": agent_kind(pane["agent"]),
                    "reasonCode": "lifecycle_not_reported" if state == "unknown" and pane["agent"] else None,
                    "nextAction": "check_otty_integration" if state == "unknown" and pane["agent"] else None,
                    "target": {"kind": "otty", **{key: pane[key] for key in ("paneId", "tabId", "windowId")}},
                    "evidence": [
                        {"label": "来源", "text": "Otty 本地 CLI 的终端元数据（不含会话正文）"},
                        {"label": "状态", "text": _STATE_TEXT[state]},
                    ],
                }
                signature = ("state", "rawState", "sessionId", "title", "workingDirectory")
                if prior and all(prior.get(key) == item.get(key) for key in signature):
                    item["updatedAt"] = prior["updatedAt"]
                same_session = prior and item["sessionId"] and (
                    prior.get("sessionId") == item["sessionId"] and prior.get("agent") == item["agent"]
                )
                if self._baseline_valid and same_session:
                    previous_state = prior["state"]
                    if state != previous_state and state != "unknown" and previous_state != "unknown":
                        change = copy.deepcopy(item)
                        finished = previous_state == "processing" and state == "idle"
                        change["kind"] = "turn_finished" if finished else "state_changed"
                        change["id"] = f"{event_id}:{change['kind']}:{checked}"
                        change["previousState"] = previous_state
                        if change["kind"] == "turn_finished":
                            change["summary"] = "本轮处理已结束；事项是否完成仍由你确认。"
                        changes.append(change)
                current[event_id] = item

            removed = sorted(set(self._previous) - set(current)) if not incomplete else []
            if incomplete:
                for event_id in set(self._previous) - set(current):
                    item = copy.deepcopy(self._previous[event_id])
                    item.update(stale=True, previousState=item["state"], state="unknown")
                    item["summary"] = "本次未获得完整记录，暂时保留上次读取的信息。"
                    current[event_id] = item
            self._previous = copy.deepcopy(current)
            self._last_success = checked
            self._baseline_valid = not incomplete
            partial = incomplete or not visibility_available or unknown_count > 0
            message = f"已连接 Otty，发现 {len(panes)} 个终端。"
            if unknown_count:
                message += f"其中 {unknown_count} 个 Agent 尚未上报状态，请在 Otty 中检查集成。"
            if incomplete or not visibility_available:
                message += "部分元数据暂不可用。"
            return {
                "status": "partial" if partial else "ready", "message": message, "checkedAt": checked,
                "lastSuccessAt": checked, "events": list(current.values()), "changes": changes,
                "removedIds": removed, "stale": incomplete, "hooksHelpURL": HOOKS_HELP_URL,
                "unknownStateCount": unknown_count,
                # A missing hook is not a missing pane. The complete CLI list
                # remains authoritative for presence even if lifecycle is unknown.
                "presenceComplete": not incomplete,
            }

    def focus(self, target: dict) -> None:
        """User-initiated, exact pane focus. Never fall back to the current pane."""
        if not isinstance(target, dict) or target.get("kind") != "otty":
            raise OttyError("invalid_target", "这不是有效的 Otty 跳转目标。")
        pane_id = _identifier(target.get("paneId"), "p")
        tab_id = _identifier(target.get("tabId"), "t")
        window_id = _identifier(target.get("windowId"), "w")
        with self._lock:
            self._cli = self._find_cli()
            actual = self._pane(self._run_cli("pane", "show", "--pane", pane_id))
            if (actual["paneId"], actual["tabId"], actual["windowId"]) != (pane_id, tab_id, window_id):
                raise OttyError("target_changed", "这个终端的位置已变化，请刷新后重新打开。")
            self._run_cli("pane", "focus", "--pane", pane_id)
            try:
                from AppKit import NSApplicationActivateIgnoringOtherApps, NSRunningApplication

                for app in NSRunningApplication.runningApplicationsWithBundleIdentifier_(BUNDLE_ID):
                    app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
            except Exception as exc:
                raise OttyError("activation_failed", "已选中目标终端，但无法唤起 Otty，请从 Dock 打开。") from exc
