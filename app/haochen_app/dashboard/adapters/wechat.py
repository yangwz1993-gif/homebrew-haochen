"""Opt-in, bounded reading of WeChat's public Dock badge only.

No private databases, notification archives, message bodies, screenshots or
chat-list scraping. An absent AXStatusLabel is UNKNOWN, never zero unread.
All platform reads run on DashboardService's worker, with no permission prompt.
"""

from __future__ import annotations

import re
import time

from ..store import now

BUNDLE_ID = "com.tencent.xinWeChat"


def unread_badge(value):
    """Only accept a numeric badge; arbitrary localized text is not a count."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,4}\+?", value.strip()):
        return None
    label = value.strip()
    return {"count": int(label.rstrip("+")), "atLeast": label.endswith("+"), "label": label}


def probe():
    from AppKit import NSRunningApplication, NSWorkspace
    from ApplicationServices import (
        AXIsProcessTrustedWithOptions,
        AXUIElementCopyAttributeValue,
        AXUIElementCreateApplication,
        AXUIElementSetMessagingTimeout,
    )

    workspace = NSWorkspace.sharedWorkspace()
    app_url = workspace.URLForApplicationWithBundleIdentifier_(BUNDLE_ID)
    if not app_url:
        return {"state": "not_installed"}
    if not NSRunningApplication.runningApplicationsWithBundleIdentifier_(BUNDLE_ID):
        return {"state": "not_running"}
    if not AXIsProcessTrustedWithOptions({"AXTrustedCheckOptionPrompt": False}):
        return {"state": "permission_required"}
    docks = NSRunningApplication.runningApplicationsWithBundleIdentifier_("com.apple.dock")
    if not docks:
        return {"state": "dock_unavailable"}
    root = AXUIElementCreateApplication(docks[0].processIdentifier())
    AXUIElementSetMessagingTimeout(root, 0.08)
    deadline = time.monotonic() + 1.0
    queue = [root]
    visited = 0

    def attribute(element, name):
        if time.monotonic() >= deadline:
            return None
        error, result = AXUIElementCopyAttributeValue(element, name, None)
        return result if not error else None

    # Identity is the actual installed application URL, not a fuzzy chat title.
    app_path = str(app_url.path()).rstrip("/")
    while queue and visited < 120 and time.monotonic() < deadline:
        element = queue.pop(0)
        visited += 1
        url = attribute(element, "AXURL")
        if url and hasattr(url, "path") and str(url.path()).rstrip("/") == app_path:
            label = attribute(element, "AXStatusLabel")
            badge = unread_badge(str(label)) if label is not None else None
            return {"state": "badge" if badge is not None else "badge_unavailable", "badge": badge}
        children = attribute(element, "AXChildren")
        if children:
            queue.extend(list(children)[:50])
    return {"state": "dock_unavailable"}


class WeChatAdapter:
    def __init__(self, reader=None):
        self.reader = reader or probe

    def snapshot(self):
        result = {"checkedAt": now(), "events": []}
        try:
            raw = self.reader()
        except Exception:
            raw = {"state": "unavailable"}
        state = raw.get("state")
        result["reasonCode"] = state
        if state == "badge" and isinstance(raw.get("badge"), dict):
            badge = raw["badge"]
            label = badge["label"]
            summary = f"微信 Dock 的未读标记为 {label}；仅反映应用标记，不含消息正文或单聊定位。"
            result.update(status="ready", message=f"已读到 Dock 未读标记：{label}；不读取聊天正文")
            if badge["count"] > 0:
                result["events"] = [{
                    "id": "wechat:dock-badge", "sourceId": "wechat:dock-badge",
                    "title": "微信未读提醒", "state": "available", "summary": summary,
                    "unreadCount": badge["count"], "fingerprint": label,
                    "reasonCode": "dock_badge_only", "updatedAt": result["checkedAt"],
                    "target": {"kind": "wechat", "bundleId": BUNDLE_ID},
                    "evidence": [{"label": "微信 Dock 标记", "text": summary}],
                }]
        elif state == "permission_required":
            result.update(status="permission_required",
                          message="需要 haochen 的辅助功能权限来读取微信 Dock 标记；不会读取聊天正文")
        elif state in {"not_running", "not_installed"}:
            result.update(status="not_running", message="请先打开并登录微信；本连接仅观察 Dock 未读标记")
        else:
            result.update(status="partial", message="微信当前未暴露可读的 Dock 未读标记；未读数未知，不代表没有新消息")
        return result
