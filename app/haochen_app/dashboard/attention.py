"""One content-versioned read state shared by the notch and dashboard.

Timestamps and poll counters are deliberately excluded: a heartbeat is not a
new message. No message text is displayed at the desktop entrance.
"""

from __future__ import annotations

import hashlib
import json

WAITING = {"awaiting", "waiting", "needs_attention", "awaiting_input"}
ERRORS = {"error", "failed", "permission_required", "tab_closed", "target_changed", "suspended",
          "disconnected", "stale"}
RUNNING = {"processing", "running", "working", "busy", "checking", "reading"}
RESULTS = {"idle", "available", "completed", "done", "upcoming", "ongoing", "scheduled"}


def version(event):
    fields = {key: event.get(key) for key in (
        "id", "sourceId", "title", "summary", "status", "state", "fingerprint", "sessionId", "unreadCount",
        "startAt", "endAt", "allDay", "calendarId", "location",
    )}
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def decorate(events, read_versions):
    result = []
    for event in events:
        fingerprint = version(event)
        result.append({**event, "attentionVersion": fingerprint,
                       "unread": read_versions.get(event["id"]) != fingerprint})
    return result


_SOURCE_NAMES = {"hi": "Hi 消息", "otty": "Otty", "browser": "网页", "wechat": "微信", "calendar": "日历"}


def _source_names(events) -> list[str]:
    """事件中涉及的来源名（去重、保序、最多两个）。"""
    names: list[str] = []
    for item in events:
        name = _SOURCE_NAMES.get(item.get("source"), "动态")
        if name not in names:
            names.append(name)
    return names[:2]


def _is_notifiable_unread(event) -> bool:
    """未读且值得提醒：Otty 的常态实时状态卡（无 kind）不算「新内容」，不打红点。"""
    if not event.get("unread"):
        return False
    if event.get("source") == "otty" and not event.get("kind"):
        return False
    return True


def activity(events, connectors):
    """只提醒「未读且待处理」的事，并指名是哪个应用（0.6.2-beta.3 行为变更）。

    旧语义「已读但没解决也继续提醒」会让刘海在零待办时仍挂「等你确认」（误报）；
    连接类问题不再在刘海常驻提醒（它们持续显示在连接卡片上），避免警报疲劳。
    """
    reliable = [event for event in events if not event.get("stale")]
    waiting = [event for event in reliable
               if event.get("status") in WAITING and event.get("unread")]
    errors = [event for event in reliable
              if event.get("status") in ERRORS and event.get("unread")]
    unread = [event for event in reliable
              if _is_notifiable_unread(event) and event.get("status") in RESULTS]
    running = [event for event in reliable if event.get("status") in RUNNING]
    if waiting:
        kind, count = "attention", len(waiting)
        names = _source_names(waiting)
        label = f"{'、'.join(names)}等你处理" if names else "有事项等你处理"
    elif errors:
        kind, count, label = "error", len(errors), "有连接需处理"
    elif unread:
        kind, count = "new", len(unread)
        names = _source_names(unread)
        label = f"{'、'.join(names)}有新结果" if names else "有新结果"
    elif running:
        kind, count, label = "running", len(running), "正在处理"
    else:
        kind, count, label = "idle", 0, "haochen"
    count = min(count, 999)
    return {"kind": kind, "count": count, "label": label,
            "accessibleLabel": f"haochen，{label}" + (f"，{count} 项" if count else "") + "，打开桌面总览"}
