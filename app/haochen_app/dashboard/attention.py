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


def activity(events, connectors):
    """Prioritize actionable states without pretending unknown means working."""
    reliable = [event for event in events if not event.get("stale")]
    waiting = [event for event in reliable if event.get("status") in WAITING]
    errors = [event for event in reliable if event.get("status") in ERRORS]
    unread = [event for event in reliable if event.get("unread") and event.get("status") in RESULTS]
    running = [event for event in reliable if event.get("status") in RUNNING]
    broken = [connector for connector in connectors if connector.get("enabled") and
              (connector.get("status") in {"error", "permission_required"}
               or connector.get("status") == "unavailable" and connector.get("hasConnected"))]
    if waiting:
        kind, count, label = "attention", sum(bool(item.get("unread")) for item in waiting), "等你确认"
    elif errors or broken:
        error_sources = {event.get("source") for event in errors}
        count = sum(bool(item.get("unread")) for item in errors)
        count += sum(connector.get("id") not in error_sources for connector in broken)
        kind, label = "error", "连接需处理"
    elif unread:
        kind, count, label = "new", len(unread), "有新结果"
    elif running:
        kind, count, label = "running", len(running), "正在处理"
    else:
        kind, count, label = "idle", 0, "haochen"
    count = min(count, 999)
    return {"kind": kind, "count": count, "label": label,
            "accessibleLabel": f"haochen，{label}" + (f"，{count} 项" if count else "") + "，打开桌面总览"}
