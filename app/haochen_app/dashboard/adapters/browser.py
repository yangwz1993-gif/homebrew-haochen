"""Read-only browser adapter and explicit native-host installation actions."""

from __future__ import annotations

import json
import os
import select
import shlex
import sqlite3
import stat
import sys
import time
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from haochen_app.dashboard.browser_host import (
    EXTENSION_ID,
    HOST_NAME,
    RETENTION_SECONDS,
    STALE_AFTER,
    BrowserStore,
    PrivateSignal,
    bridge_directory,
    notify_signal,
    source_target,
    valid_url,
)
from haochen_app.secure_storage import atomic_write_private

# 已关闭标签页超过该时长即归档：不再参与连接状态降级（B-9，旧账不许拖黄现在的卡片）。
CLOSED_ARCHIVE_AFTER = 12 * 3600

MESSAGES = {
    "available": "已读取授权页面的主框架文字；图片、跨域框架及未加载内容不在本次范围内",
    "permission_required": "此网站的读取授权已撤回或尚未授予，请在 Chrome 扩展中授权",
    "tab_closed": "追踪的标签页已关闭；重新打开原网址后，在扩展中重新绑定",
    "suspended": "标签页已休眠或被 Chrome 丢弃，未将旧内容当作新读取",
    "target_changed": "原标签页已切换到其他网址，未改读新页面",
    "disconnected": "Chrome 扩展已断开，当前无法检查页面变化",
    "stale": "近期未完成页面检查，旧记录不代表当前页面无变化",
    "reading": "正在读取已授权页面",
    "error": "本次页面采集失败，可在 Chrome 扩展中重试",
}

# Public manifest key in browser-extension/manifest.json fixes the unpacked ID
# across install directories. This is not a credential or a Web Store claim.
BUNDLED_EXTENSION_ID = "hjbiopmhlpcnpaojialipdhdemnafecb"


def _runtime_command() -> list[str]:
    return (
        [str(Path(sys.executable).resolve()), "--browser-host"]
        if getattr(sys, "frozen", False)
        else [str(Path(sys.executable).resolve()), str(Path(__file__).resolve().parents[3] / "run_app.py"),
              "--browser-host"]
    )


def _launcher_text(home: Path, command: list[str]) -> str:
    return "#!/bin/sh\numask 077\nexport HAOCHEN_HOME=" + shlex.quote(str(home.expanduser().absolute())) + \
        "\nexec " + shlex.join(command) + ' "$@"\n'


def _read_setup(path: Path) -> str | None:
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("浏览器桥接路径异常，请重新配置。")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 64_000:
            raise ValueError("浏览器桥接配置不可用。")
        content = handle.read(64_001)
        if len(content) > 64_000:
            raise ValueError("浏览器桥接配置过大。")
        return content.decode("utf-8")


def iso_time(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat().replace("+00:00", "Z")


class BrowserAdapter:
    def __init__(self, home: Path):
        self.home = Path(home)

    def setup_state(self, *, chrome_home: Path | None = None, command: list[str] | None = None) -> dict:
        """Read-only setup chain. Never creates storage or reads Chrome profiles.

        Missing host connectivity cannot prove that the extension is absent.
        Only a fresh native handshake establishes the extension step as complete.
        """
        directory = self.home / "dashboard/browser-bridge"
        chrome_home = chrome_home or Path.home() / "Library/Application Support/Google/Chrome"
        manifest_path = chrome_home / "NativeMessagingHosts" / (HOST_NAME + ".json")
        launcher = directory / "native-host-launcher"
        steps = [{"id": identifier, "status": "unknown" if identifier == "extension" else "pending"}
                 for identifier in ("extension", "bridge", "authorization", "ready")]
        result = {"stage": "extension", "reasonCode": "bridge_missing", "stepResults": steps,
                  "extensionId": BUNDLED_EXTENSION_ID, "extensionInstalled": "unknown",
                  "extensionConnected": False, "bridgeInstalled": False, "runtimeValid": False,
                  "sourceCount": 0, "availableSourceCount": 0, "missingPermissionCount": 0,
                  "message": "尚未建立 Chrome 连接。先加载配套扩展，再完成本机连接；不会自动读取网页。"}
        try:
            texts = [_read_setup(p) for p in (manifest_path, directory / "host-config.json", launcher)]
            if not all(texts):
                if any(texts):
                    result.update(stage="bridge", reasonCode="bridge_incomplete",
                                  message="本机桥接配置不完整，请点击完成本机连接进行修复。")
                    steps[1]["status"] = "attention"
                return result
            manifest, config = json.loads(texts[0] or "{}"), json.loads(texts[1] or "{}")
            extension_id = config.get("extensionId")
            if (not isinstance(extension_id, str) or not EXTENSION_ID.fullmatch(extension_id)
                    or manifest.get("name") != HOST_NAME or manifest.get("type") != "stdio"
                    or manifest.get("path") != str(launcher)
                    or manifest.get("allowed_origins") != [f"chrome-extension://{extension_id}/"]
                    or config.get("launcherPath") != str(launcher)
                    or config.get("manifestPath") != str(manifest_path)):
                raise ValueError("inconsistent host configuration")
            result.update(extensionId=extension_id, bridgeInstalled=True, stage="bridge")
            runtime = command or _runtime_command()
            if (texts[2] != _launcher_text(self.home, runtime) or not os.access(launcher, os.X_OK)
                    or not Path(runtime[0]).is_file()):
                result.update(reasonCode="bridge_runtime_changed",
                              message="应用位置或桥接启动程序已变化，请重新完成本机连接。")
                steps[1]["status"] = "attention"
                return result
            result["runtimeValid"] = True
            steps[1]["status"] = "complete"
            result.update(reasonCode="extension_not_connected",
                          message="本机桥接已就绪，尚未收到扩展连接。请在 Chrome 加载扩展并点击重新连接。")
            database = directory / "observations.sqlite3"
            if not database.is_file():
                return result
            if any(p.is_symlink() for p in (database, *database.parents)):
                raise ValueError("invalid storage path")
            with closing(sqlite3.connect(database.absolute().as_uri() + "?mode=ro", uri=True, timeout=0.2)) as db:
                enabled = db.execute("SELECT value FROM options WHERE key='enabled'").fetchone()
                if enabled and not enabled[0]:
                    result.update(reasonCode="connector_disabled", message="Chrome 连接已关闭，请先在 haochen 中开启。")
                    return result
                now = time.time()
                fresh = db.execute("SELECT COUNT(*) FROM sessions WHERE connected=1 AND seen>?",
                                   (now - STALE_AFTER,)).fetchone()[0]
                rows = db.execute(
                    "SELECT sources.status, COUNT(*) FROM sources JOIN sessions ON sessions.id=sources.session "
                    "WHERE sessions.connected=1 AND sessions.seen>? AND sources.checked>? "
                    "GROUP BY sources.status", (now - STALE_AFTER, now - STALE_AFTER),
                ).fetchall()
            if not fresh:
                return result
            counts = dict(rows)
            total, available = sum(counts.values()), counts.get("available", 0)
            result.update(extensionInstalled="confirmed", extensionConnected=True, sourceCount=total,
                          availableSourceCount=available, missingPermissionCount=counts.get("permission_required", 0))
            steps[0]["status"] = "complete"
            result.update(stage="authorization", reasonCode="page_not_selected",
                          message="Chrome 已连接。打开要关注的网页，在扩展中授权该网站并选择追踪此页。")
            if available:
                steps[2]["status"] = steps[3]["status"] = "complete"
                result.update(stage="ready", reasonCode="observing", message=f"已实际读取 {available} 个授权页面。")
            elif counts.get("permission_required"):
                steps[2]["status"] = "attention"
                result.update(reasonCode="site_permission_required",
                              message="已有追踪页面需要网站授权，请在扩展中重新授权。")
            elif total:
                steps[3]["status"] = "attention"
                result.update(reasonCode="source_not_readable",
                              message="Chrome 已连接，但还没有新鲜可读的页面；请在扩展中检查页面是否关闭、切换或休眠。")
            return result
        except (OSError, ValueError, UnicodeError, sqlite3.Error, AttributeError, IndexError):
            result.update(stage="bridge", reasonCode="bridge_invalid",
                          message="本机桥接配置暂不可用，请重新完成本机连接；不会读取其他浏览器数据。")
            steps[1]["status"] = "attention"
            return result

    def _load(self):
        store = BrowserStore(self.home)
        try:
            return store.records(), store.latest_session()
        finally:
            store.close()

    def set_enabled(self, enabled: bool):
        store = BrowserStore(self.home)
        try:
            store.set_enabled(enabled)
        finally:
            store.close()

    @staticmethod
    def _status(row: dict, now: float) -> str:
        if not row.get("connected"):
            return "disconnected"
        if now - (row.get("seen") or 0) > STALE_AFTER:
            return "disconnected"
        if row["status"] == "available" and now - row["checked"] > STALE_AFTER:
            return "stale"
        return row["status"]

    def snapshot(self) -> dict:
        now = time.time()
        try:
            rows, session = self._load()
        except (OSError, ValueError, sqlite3.Error):
            return {"status": "error", "message": "浏览器连接数据不可用", "checkedAt": iso_time(now), "events": []}
        connected = bool(session and session["connected"] and now - session["seen"] <= STALE_AFTER)
        events = []
        for row in rows:
            if now - row["checked"] > RETENTION_SECONDS:
                continue
            status = self._status(row, now)
            # 保质期（B-9）：已关闭的标签页超过 12 小时属「历史已结束」，不再把整体
            # 连接拖成「能力受限」；事件本身保留在动态里如实展示。
            archived = status == "tab_closed" and now - row["checked"] > CLOSED_ARCHIVE_AFTER
            events.append(
                {
                    "id": "browser:" + row["id"],
                    "source": "browser",
                    "sourceId": row["id"],
                    "title": row["title"] or "已选择的网页",
                    "summary": row["content"][:180] if status == "available" and row["content"] else MESSAGES[status],
                    "fingerprint": row["digest"],
                    "state": status,
                    "archived": archived,
                    "updatedAt": iso_time(row["updated"]),
                    "checkedAt": iso_time(row["checked"]),
                    "target": source_target(row),
                    "evidence": [
                        {
                            "sourceId": row["id"],
                            "url": row["url"],
                            "status": status,
                            "label": "网页正文预览",
                            "text": row["content"][:6000] if status == "available" else MESSAGES[status],
                            "checkedAt": iso_time(row["checked"]),
                            "updatedAt": iso_time(row["updated"]),
                            "coverage": row["coverage"] if status == "available" else "none",
                            "untrusted": True,
                            "previewTruncated": status == "available" and len(row["content"]) > 6000,
                        }
                    ],
                }
            )
        status = "connected" if connected else ("disconnected" if session else "not_connected")
        if connected and any(event["state"] != "available" and not event.get("archived") for event in events):
            status = "partial"
        message = (
            "Chrome 已连接；仅追踪在扩展中明确选择的页面"
            if connected
            else "请安装并连接 Chrome 配套扩展，再选择要追踪的页面"
        )
        return {"status": status, "message": message, "checkedAt": iso_time(now), "events": events}

    def evidence(self, source: str | dict) -> dict:
        source_id = (
            source.get("sourceId") or source.get("locator") or source.get("url") or source.get("id", "")
            if isinstance(source, dict)
            else source
        )
        if isinstance(source_id, str) and source_id.startswith("browser:"):
            source_id = source_id[8:]
        now = time.time()
        try:
            rows, _ = self._load()
        except (OSError, ValueError, sqlite3.Error):
            return {"status": "error", "content": "", "coverage": "none", "checkedAt": iso_time(now)}
        matches = [
            row
            for row in rows
            if (row["id"] == source_id or row["url"] == source_id) and now - row["checked"] <= RETENTION_SECONDS
        ]
        if len(matches) > 1:
            return {
                "status": "ambiguous",
                "content": "",
                "coverage": "none",
                "checkedAt": iso_time(now),
                "message": "有多个浏览器配置正在追踪此网址，请选择精确来源 ID",
            }
        for row in matches:
            status = self._status(row, now)
            return {
                "sourceId": row["id"],
                "url": row["url"],
                "title": row["title"],
                "status": status,
                "content": row["content"] if status == "available" else "",
                "updatedAt": iso_time(row["updated"]),
                "checkedAt": iso_time(row["checked"]),
                "coverage": row["coverage"] if status == "available" else "none",
                "message": MESSAGES[status],
                "untrusted": True,
            }
        return {
            "status": "not_watched",
            "content": "",
            "coverage": "none",
            "checkedAt": iso_time(now),
            "message": "尚未找到该已授权网页来源",
        }

    def install_host(
        self, extension_id: str, *, chrome_home: Path | None = None, command: list[str] | None = None
    ) -> dict:
        """Explicit user action only. Never starts Chrome, prompts TCC, or reads tabs.

        Test callers supply chrome_home/command. GUI calls use the current installed
        executable; reinstall after moving the app or changing the extension ID.
        """
        if not isinstance(extension_id, str) or not EXTENSION_ID.fullmatch(extension_id):
            raise ValueError("扩展 ID 必须是 Chrome 显示的 32 位 a–p 字母")
        directory = bridge_directory(self.home)
        launcher = directory / "native-host-launcher"
        if command is None:
            command = _runtime_command()
        if not command or not Path(command[0]).is_absolute() or not Path(command[0]).is_file():
            raise ValueError("无法定位 haochen 的浏览器桥接启动程序")
        chrome_home = Path(chrome_home) if chrome_home else Path.home() / "Library/Application Support/Google/Chrome"
        manifest = chrome_home / "NativeMessagingHosts" / (HOST_NAME + ".json")
        if manifest.is_symlink():
            raise ValueError("不能覆盖符号链接形式的浏览器配置")
        existing = _read_setup(manifest)
        if existing:
            prior = json.loads(existing)
            if prior.get("name") != HOST_NAME or prior.get("path") != str(launcher):
                raise ValueError("现有 Chrome 桥接属于另一个安装位置，未覆盖；请先从原应用移除连接。")
        text = _launcher_text(self.home, command)
        atomic_write_private(launcher, text)
        launcher.chmod(0o700)
        atomic_write_private(
            directory / "host-config.json",
            json.dumps({"extensionId": extension_id, "manifestPath": str(manifest), "launcherPath": str(launcher)}),
        )
        atomic_write_private(
            manifest,
            json.dumps(
                {
                    "name": HOST_NAME,
                    "description": "haochen 已授权网页只读桥接",
                    "path": str(launcher),
                    "type": "stdio",
                    "allowed_origins": [f"chrome-extension://{extension_id}/"],
                },
                indent=2,
            ),
        )
        return {
            "status": "installed",
            "manifestPath": str(manifest),
            "extensionId": extension_id,
            "message": "桥接已安装。回到 Chrome 扩展点击重新连接，然后选择追踪页面。",
        }

    def uninstall_host(self, *, chrome_home: Path | None = None) -> dict:
        """Remove only our manifest/launcher/config; keep observed user data intact."""
        directory = bridge_directory(self.home)
        chrome_home = Path(chrome_home) if chrome_home else Path.home() / "Library/Application Support/Google/Chrome"
        manifest = chrome_home / "NativeMessagingHosts" / (HOST_NAME + ".json")
        if manifest.exists():
            if manifest.is_symlink():
                raise ValueError("浏览器配置路径异常，未移除")
            current = json.loads(manifest.read_text())
            if current.get("name") != HOST_NAME or current.get("path") != str(directory / "native-host-launcher"):
                raise ValueError("此浏览器桥接不属于当前 haochen 数据目录，未移除")
            manifest.unlink()
        for name in ("native-host-launcher", "host-config.json"):
            path = directory / name
            if path.is_symlink():
                raise ValueError("浏览器桥接路径异常，未移除")
            path.unlink(missing_ok=True)
        return {"status": "removed", "message": "已移除当前 haochen 的 Chrome 桥接；可重新安装，既有观测未删除"}

    def untrack(self, source_id: str) -> str | None:
        """停止追踪某个来源：桥存储删记录 + 尽力通知扩展同步移除（B-11 完整版）。"""
        from ..browser_host import BrowserStore
        store = BrowserStore(self.home)
        try:
            return store.queue_untrack(source_id)
        finally:
            store.close()

    def open_target(self, target: dict) -> dict:
        # Return a validated intent for the app's URL opener, not an unverifiable
        # assertion that we focused an exact tab from a different browser profile.
        return {
            "url": valid_url(target.get("url")),
            "mode": "url_fallback",
            "message": "打开原网址；不保证恢复到原 Chrome 标签页",
        }

    def focus(self, target: dict, timeout: float = 3.0) -> dict:
        """Explicit app click only; run off the GUI thread. Never opens another URL.

        The private FIFO wakes the original native-host session immediately. The
        extension checks its own saved binding, current permission and Chrome tab
        before focusing, then acknowledges. Neither timeout nor stale IDs imply
        success. No browser permission is requested by this operation.
        """
        messages = {
            "focused": "已回到原 Chrome 配置中的追踪标签页",
            "timeout": "标签页跳转未及时确认；未自动打开其他网页",
            "target_changed": "追踪标签页或浏览器会话已变化，请刷新动态后重试",
            "not_watched": "此网页已不在追踪列表中",
            "disabled": "浏览器连接已停用",
            "busy": "已有标签页跳转正在处理，请稍后重试",
            "error": "无法确认标签页跳转；未自动打开其他网页",
        }
        store, wake = None, None
        status = "error"
        try:
            if not isinstance(target, dict) or target.get("kind") != "browser":
                raise ValueError("invalid browser target")
            valid_url(target.get("url"))
            wait = min(5.0, max(0.1, float(timeout)))
            deadline = time.time() + wait
            store = BrowserStore(self.home)
            command_id = "focus-" + str(uuid.uuid4())
            wake = PrivateSignal(store.path.parent, command_id)
            status = store.queue_focus(command_id, target, deadline)
            if status is None:
                if not notify_signal(store.path.parent, "host-" + target["sessionId"]):
                    store.cancel_focus(command_id, "disconnected")
                    status = "disconnected"
                else:
                    # One blocking event wait, not DB polling; deadline bounds any
                    # lost/disconnected native port. Drain no body/content here.
                    select.select([wake.fd], [], [], max(0.0, deadline - time.time()))
                    status = store.focus_result(command_id)
                    if status is None:
                        store.cancel_focus(command_id)
                        status = "timeout"
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
            status = "error"
        finally:
            if wake is not None:
                wake.close()
            if store is not None:
                store.close()
        return {
            "ok": status == "focused",
            "status": status,
            "mode": "exact_tab",
            "message": messages.get(status, MESSAGES.get(status, messages["error"])),
        }
