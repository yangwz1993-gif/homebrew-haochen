"""Read-only EventKit calendar adapter with explicit, main-thread authorization.

No EventKit import or permission prompt occurs when importing this module. macOS
14+ requires *full access* to read calendars; write-only access cannot satisfy
this connector. Even with that system grant this adapter never mutates events.
"""

from __future__ import annotations

import copy
import hashlib
import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

_MAX_EVENTS = 500
_MAX_CALENDARS = 300
_PERMISSION_MESSAGES = {
    "not_determined": "连接日历需要你授权读取已同步到 macOS 的日程。",
    "restricted": "系统策略限制了日历访问，请检查设备管理或家长控制。",
    "denied": "日历访问已被关闭，请在系统设置中允许 haochen 读取日历。",
    "write_only": "当前只有新增日程权限；显示已有日程需要日历完整访问权限。",
    "unknown": "无法确认日历读取权限，请在系统设置中检查。",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _text(value, limit: int = 400) -> str:
    if value is None:
        return ""
    return "".join(c for c in str(value) if c >= " " or c in "\n\t")[:limit].strip()


def _value(obj: Any, name: str, default=None):
    method = getattr(obj, name, None)
    return method() if callable(method) else default


def _date(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    try:
        return datetime.fromtimestamp(float(value.timeIntervalSince1970()), UTC)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


class CalendarAdapter:
    def __init__(self, selected_calendar_ids: Sequence[str] | None = None):
        self._lock = threading.RLock()
        self._kit = None
        self._foundation = None
        self._store = None
        self._selected: frozenset[str] | None = None
        self._last_events: list[dict] = []
        self._last_success: str | None = None
        self._pending_access = False
        self.set_selected_calendar_ids(selected_calendar_ids)

    def set_selected_calendar_ids(self, ids: Sequence[str] | None) -> None:
        """None means all readable calendars; an empty sequence selects none."""
        if ids is not None and (
            isinstance(ids, (str, bytes)) or not isinstance(ids, Sequence)
            or len(ids) > _MAX_CALENDARS
            or any(not isinstance(i, str) or not i or len(i) > 1024 for i in ids)
        ):
            raise ValueError("日历选择无效，请从可用日历中重新选择。")
        with self._lock:
            self._selected = None if ids is None else frozenset(ids)
            # Never expose a deselected calendar via an error fallback.
            if self._selected is not None:
                self._last_events = [e for e in self._last_events if e["calendarId"] in self._selected]

    def _load(self) -> bool:
        if self._kit is not None and self._foundation is not None:
            return True
        try:
            import EventKit
            import Foundation

            self._kit = EventKit
            self._foundation = Foundation
            return True
        except (ImportError, OSError):
            return False

    def _authorization(self) -> str:
        kit = self._kit
        value = int(kit.EKEventStore.authorizationStatusForEntityType_(kit.EKEntityTypeEvent))
        # EKAuthorizationStatusAuthorized is the deprecated alias of FullAccess.
        states = {
            int(getattr(kit, "EKAuthorizationStatusNotDetermined", 0)): "not_determined",
            int(getattr(kit, "EKAuthorizationStatusRestricted", 1)): "restricted",
            int(getattr(kit, "EKAuthorizationStatusDenied", 2)): "denied",
            int(getattr(kit, "EKAuthorizationStatusFullAccess", 3)): "full_access",
            int(getattr(kit, "EKAuthorizationStatusWriteOnly", 4)): "write_only",
        }
        return states.get(value, "unknown")

    def _get_store(self):
        if self._store is None:
            # init is the non-prompting initializer, not initWithAccessToEntityTypes:.
            self._store = self._kit.EKEventStore.alloc().init()
        return self._store

    def _calendar_objects(self) -> list:
        return list(self._get_store().calendarsForEntityType_(self._kit.EKEntityTypeEvent) or [])

    def _calendar_info(self, calendar) -> dict:
        identifier = _text(_value(calendar, "calendarIdentifier"), 1024)
        if not identifier:
            raise ValueError("缺少日历标识。")
        source = _value(calendar, "source")
        return {
            "id": identifier, "title": _text(_value(calendar, "title")) or "未命名日历",
            "account": _text(_value(source, "title")),
            "selected": self._selected is None or identifier in self._selected,
        }

    def calendars(self) -> list[dict]:
        """List calendar names after authorization; never request access here.

        An empty list alone says nothing about access. Inspect snapshot().status
        when presenting setup/error UI so denial is not rendered as 'no calendars'.
        """
        with self._lock:
            try:
                if not self._load() or self._authorization() != "full_access":
                    return []
                result = []
                for calendar in self._calendar_objects()[:_MAX_CALENDARS]:
                    try:
                        result.append(self._calendar_info(calendar))
                    except (ValueError, TypeError):
                        continue
                return result
            except Exception:
                return []

    def _permission_result(self, checked: str, authorization: str) -> dict:
        # Permission revocation must clear even previously authorized event data.
        self._last_events = []
        return {
            "status": "permission_required", "message": _PERMISSION_MESSAGES[authorization],
            "checkedAt": checked, "authorization": authorization, "events": [],
            "calendars": [], "stale": False, "lastSuccessAt": self._last_success,
        }

    def _failure(self, checked: str, message: str) -> dict:
        items = copy.deepcopy(self._last_events)
        for item in items:
            item["stale"] = True
        return {
            "status": "unavailable", "message": message, "checkedAt": checked,
            "events": items, "calendars": [], "stale": bool(items),
            "lastSuccessAt": self._last_success,
        }

    def _event(self, event, now: datetime, end: datetime) -> dict | None:
        if _value(event, "status") == getattr(self._kit, "EKEventStatusCanceled", 3):
            return None
        start_at = _date(_value(event, "startDate"))
        end_at = _date(_value(event, "endDate"))
        identifier = _text(_value(event, "eventIdentifier"), 2048)
        if start_at is None or end_at is None or not identifier or end_at < start_at:
            raise ValueError("日程的标识或时间无效。")
        if end_at <= now or start_at >= end:
            return None
        calendar = self._calendar_info(_value(event, "calendar"))
        if self._selected is not None and calendar["id"] not in self._selected:
            return None
        title = _text(_value(event, "title")) or "未命名日程"
        location = _text(_value(event, "location"))
        all_day = bool(_value(event, "isAllDay", False))
        modified = _date(_value(event, "lastModifiedDate"))
        state = "ongoing" if start_at <= now < end_at else "upcoming"
        time_label = start_at.astimezone().strftime("%m月%d日 %H:%M")
        if all_day:
            time_label = start_at.astimezone().strftime("%m月%d日 · 全天")
        summary = f"{time_label} · {calendar['title']}"
        if location:
            summary += f" · {location}"
        # Recurring occurrences may share eventIdentifier, so retain their start.
        occurrence_key = f"{identifier}\0{start_at.isoformat()}"
        digest = hashlib.sha256(occurrence_key.encode("utf-8")).hexdigest()[:32]
        return {
            "id": "calendar:" + digest, "source": "calendar", "title": title,
            "summary": summary, "state": state, "startAt": start_at.isoformat(),
            "endAt": end_at.isoformat(), "allDay": all_day, "location": location,
            "calendarId": calendar["id"], "calendarTitle": calendar["title"],
            "updatedAt": modified.isoformat() if modified else now.isoformat(),
            "updatedAtBasis": "provider" if modified else "observed", "observedAt": now.isoformat(),
            "stale": False,
            "target": {"kind": "calendar", "eventId": identifier, "calendarId": calendar["id"],
                       "startAt": start_at.isoformat()},
            "evidence": [
                {"label": "来源", "text": f"macOS 日历 / {calendar['title']}（当前已同步的数据）"},
                {"label": "时间", "text": time_label},
            ],
        }

    def snapshot(self) -> dict:
        """Read ongoing/upcoming events in seven days on a worker thread."""
        with self._lock:
            now = _now()
            checked = now.isoformat()
            if not self._load():
                self._last_events = []
                return self._failure(checked, "当前安装包缺少日历组件，请安装完整版本。")
            try:
                authorization = self._authorization()
                if authorization != "full_access":
                    return self._permission_result(checked, authorization)
                objects = self._calendar_objects()
                incomplete = len(objects) > _MAX_CALENDARS
                info, chosen = [], []
                for calendar in objects[:_MAX_CALENDARS]:
                    try:
                        entry = self._calendar_info(calendar)
                    except (ValueError, TypeError):
                        incomplete = True
                        continue
                    info.append(entry)
                    if entry["selected"]:
                        chosen.append(calendar)
                available_ids = {row["id"] for row in info}
                missing_ids = sorted(self._selected - available_ids) if self._selected is not None else []
                end = now + timedelta(days=7)
                items = []
                if chosen:
                    foundation = self._foundation
                    predicate = self._get_store().predicateForEventsWithStartDate_endDate_calendars_(
                        foundation.NSDate.dateWithTimeIntervalSince1970_(now.timestamp()),
                        foundation.NSDate.dateWithTimeIntervalSince1970_(end.timestamp()), chosen,
                    )
                    events = self._get_store().eventsMatchingPredicate_(predicate) or []
                    # Avoid unbounded per-event bridging and string conversion.
                    incomplete |= len(events) > _MAX_EVENTS
                    # EventKit explicitly makes no ordering guarantee. Native
                    # sorting gives the earliest occurrences before truncation.
                    if hasattr(events, "sortedArrayUsingDescriptors_"):
                        descriptor = foundation.NSSortDescriptor.sortDescriptorWithKey_ascending_("startDate", True)
                        events = events.sortedArrayUsingDescriptors_([descriptor])
                    else:
                        events = sorted(events, key=lambda e: _date(_value(e, "startDate")) or end)
                    for event in events[:_MAX_EVENTS]:
                        try:
                            item = self._event(event, now, end)
                            if item is not None:
                                items.append(item)
                        except (ValueError, TypeError, AttributeError):
                            incomplete = True
                # A revoked grant during the native query must not leak its cache.
                authorization = self._authorization()
                if authorization != "full_access":
                    return self._permission_result(checked, authorization)
                items.sort(key=lambda item: (item["startAt"], item["title"]))
                previous = {item["id"]: item for item in self._last_events}
                for item in items:
                    prior = previous.get(item["id"])
                    signature = ("title", "summary", "state", "startAt", "endAt", "calendarId")
                    if prior and item["updatedAtBasis"] == "observed" and all(
                        prior.get(k) == item.get(k) for k in signature
                    ):
                        item["updatedAt"] = prior["updatedAt"]
                self._last_events = copy.deepcopy(items)
                self._last_success = checked
                if self._selected == frozenset():
                    message = "尚未选择要显示的日历。"
                elif not info:
                    message = "已获得权限，但 macOS 尚无已同步的日历。"
                elif not items:
                    message = "所选日历未来 7 天暂无日程。"
                else:
                    message = f"未来 7 天有 {len(items)} 个日程（来自 macOS 已同步的数据）。"
                if missing_ids:
                    message += "部分已选日历已移除或不再可访问，请重新选择。"
                if incomplete:
                    message += "部分日程未能完整读取。"
                return {
                    "status": "partial" if incomplete or missing_ids else "ready", "message": message,
                    "checkedAt": checked, "lastSuccessAt": checked, "authorization": authorization,
                    "events": items, "calendars": info, "missingCalendarIds": missing_ids,
                    "rangeStart": checked, "rangeEnd": end.isoformat(), "stale": False,
                }
            except Exception:
                # Do not interpolate NSError: it can contain account/event content.
                try:
                    authorization = self._authorization()
                    if authorization != "full_access":
                        return self._permission_result(checked, authorization)
                except Exception:
                    # If access cannot be established, do not expose a private
                    # event from the last authorized snapshot as a fallback.
                    self._last_events = []
                return self._failure(checked, "暂时无法读取 macOS 日历，请稍后重试。")

    def request_access(self, callback: Callable[[bool, str], None]) -> None:
        """Explicit click only. Start on the main thread; callback on the main queue.

        No change to event data, selected calendars, or account configuration is
        performed. The app bundle must provide calendar usage descriptions.
        """
        if threading.current_thread() is not threading.main_thread():
            raise ValueError("请从主界面的连接按钮申请日历权限。")
        if not callable(callback):
            raise ValueError("缺少日历授权结果回调。")
        if not self._load():
            callback(False, "当前安装包缺少日历组件，请安装完整版本。")
            return
        notified = False

        def notify(granted: bool, message: str):
            nonlocal notified
            if notified:
                return
            notified = True
            callback(granted, message)

        try:
            authorization = self._authorization()
            if authorization == "full_access":
                notify(True, "日历读取权限已开启。")
                return
            if authorization in ("denied", "restricted", "unknown"):
                notify(False, _PERMISSION_MESSAGES[authorization])
                return
            if self._pending_access:
                notify(False, "日历授权正在进行，请先完成系统提示。")
                return
            store = self._get_store()
            full_request = getattr(store, "requestFullAccessToEventsWithCompletion_", None)
            usage_key = (
                "NSCalendarsFullAccessUsageDescription" if callable(full_request) else "NSCalendarsUsageDescription"
            )
            description = self._foundation.NSBundle.mainBundle().objectForInfoDictionaryKey_(usage_key)
            if not description:
                notify(False, "当前运行方式未配置日历权限说明，请使用完整的 haochen 安装包。")
                return
            self._pending_access = True

            def completion(granted, error):
                def deliver():
                    self._pending_access = False
                    try:
                        readable = bool(granted) and self._authorization() == "full_access"
                    except Exception:
                        readable = False
                    message = "日历读取权限已开启。" if readable else "未获得日历读取权限，可在系统设置中调整。"
                    notify(readable, message)

                self._foundation.NSOperationQueue.mainQueue().addOperationWithBlock_(deliver)

            if callable(full_request):
                full_request(completion)
            else:
                store.requestAccessToEntityType_completion_(self._kit.EKEntityTypeEvent, completion)
        except Exception:
            self._pending_access = False
            notify(False, "无法发起日历授权，请在系统设置中检查权限。")
