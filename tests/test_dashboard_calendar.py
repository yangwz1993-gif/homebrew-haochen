"""EventKit adapter contract without reading personal calendars or requesting TCC."""

from __future__ import annotations

import json
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from haochen_app.dashboard.adapters import calendar  # noqa: E402

NOW = datetime(2026, 9, 13, 3, 0, tzinfo=UTC)


def obj(**fields):
    return SimpleNamespace(**{key: (lambda value=value: value) for key, value in fields.items()})


def calendar_object(identifier="cal-example", title="Example Calendar"):
    return obj(calendarIdentifier=identifier, title=title, source=obj(title="Example account"))


def event(identifier="event-example", start=None, end=None, **kwargs):
    entry = obj(
        eventIdentifier=identifier, title="Example meeting", calendar=calendar_object(),
        startDate=start or NOW + timedelta(hours=1), endDate=end or NOW + timedelta(hours=2),
        status=1, isAllDay=False, location="Example room", **kwargs,
    )
    entry.notes = lambda: pytest.fail("must not read notes")
    entry.attendees = lambda: pytest.fail("must not read attendees")
    return entry


class FakeStore:
    def __init__(self):
        self.events = []
        self.calendar_list = [calendar_object()]
        self.query_calls = []
        self.request_calls = []
        self.error = None

    def calendarsForEntityType_(self, entity):
        if self.error:
            raise self.error
        return self.calendar_list

    def predicateForEventsWithStartDate_endDate_calendars_(self, start, end, calendars):
        self.query_calls.append((start, end, calendars))
        return "predicate"

    def eventsMatchingPredicate_(self, predicate):
        assert predicate == "predicate"
        return self.events

    def requestFullAccessToEventsWithCompletion_(self, callback):
        self.request_calls.append(callback)


@pytest.fixture
def setup(monkeypatch):
    connector = calendar.CalendarAdapter()
    state = {"authorization": 3, "description": "Read synchronized calendar events."}
    store = FakeStore()
    queue = []
    connector._store = store
    connector._kit = SimpleNamespace(
        EKEntityTypeEvent=0, EKEventStatusCanceled=3,
        EKEventStore=SimpleNamespace(authorizationStatusForEntityType_=lambda entity: state["authorization"]),
    )
    connector._foundation = SimpleNamespace(
        NSDate=SimpleNamespace(dateWithTimeIntervalSince1970_=lambda ts: datetime.fromtimestamp(ts, UTC)),
        NSBundle=SimpleNamespace(mainBundle=lambda: SimpleNamespace(
            objectForInfoDictionaryKey_=lambda key: state["description"]
        )),
        NSOperationQueue=SimpleNamespace(mainQueue=lambda: SimpleNamespace(addOperationWithBlock_=queue.append)),
    )
    monkeypatch.setattr(calendar, "_now", lambda: NOW)
    return connector, store, state, queue


@pytest.mark.parametrize("code, authorization", [
    (0, "not_determined"), (1, "restricted"), (2, "denied"), (4, "write_only"), (99, "unknown"),
])
def test_snapshot_never_prompts_and_distinguishes_missing_permissions(setup, code, authorization):
    connector, store, state, _ = setup
    state["authorization"] = code
    result = connector.snapshot()
    assert result["status"] == "permission_required"
    assert result["authorization"] == authorization
    assert result["events"] == []
    assert connector.calendars() == []
    assert store.request_calls == []
    assert store.query_calls == []


def test_granted_empty_is_ready_not_missing_permission(setup):
    connector, store, _, _ = setup
    result = connector.snapshot()
    assert result["status"] == "ready"
    assert result["events"] == []
    assert "暂无日程" in result["message"]
    assert store.query_calls[0][:2] == (NOW, NOW + timedelta(days=7))
    assert store.request_calls == []


def test_granted_no_synced_calendars_is_distinct(setup):
    connector, store, _, _ = setup
    store.calendar_list = []
    result = connector.snapshot()
    assert result["status"] == "ready"
    assert "尚无已同步" in result["message"]
    assert store.query_calls == []


def test_events_are_real_metadata_only_and_in_time_order(setup):
    connector, store, _, _ = setup
    first = event("event-first", NOW + timedelta(minutes=15), NOW + timedelta(minutes=45))
    store.events = [event(), first]
    result = connector.snapshot()
    assert result["status"] == "ready"
    rows = result["events"]
    assert rows[0]["target"] == {
        "kind": "calendar", "eventId": "event-first", "calendarId": "cal-example",
        "startAt": (NOW + timedelta(minutes=15)).isoformat(),
    }
    assert rows[0]["state"] == "upcoming"
    assert rows[0]["title"] == "Example meeting"
    assert "notes" not in json.dumps(rows)
    assert "attendees" not in json.dumps(rows)


def test_recurrent_occurrences_have_distinct_stable_ids(setup):
    connector, store, _, _ = setup
    store.events = [event(), event(start=NOW + timedelta(days=1), end=NOW + timedelta(days=1, hours=1))]
    rows = connector.snapshot()["events"]
    assert len({r["id"] for r in rows}) == 2
    assert rows == connector.snapshot()["events"]


def test_ongoing_all_day_out_of_range_and_cancelled(setup):
    connector, store, _, _ = setup
    ongoing = event("ongoing", NOW - timedelta(minutes=15), NOW + timedelta(minutes=15))
    all_day = event("all-day")
    all_day.isAllDay = lambda: True
    cancelled = event("cancelled")
    cancelled.status = lambda: 3
    store.events = [
        ongoing, all_day, cancelled,
        event("past", NOW - timedelta(hours=2), NOW - timedelta(hours=1)),
        event("far", NOW + timedelta(days=8), NOW + timedelta(days=8, hours=1)),
    ]
    rows = connector.snapshot()["events"]
    assert len(rows) == 2
    assert rows[0]["state"] == "ongoing"
    assert rows[1]["allDay"] is True
    assert "全天" in rows[1]["summary"]


def test_explicit_empty_selection_does_not_query_all(setup):
    connector, store, _, _ = setup
    connector.set_selected_calendar_ids([])
    result = connector.snapshot()
    assert result["events"] == []
    assert "尚未选择" in result["message"]
    assert store.query_calls == []
    assert connector.calendars()[0]["selected"] is False


def test_specific_selection_is_applied_to_query_and_return_values(setup):
    connector, store, _, _ = setup
    selected = calendar_object("selected", "Selected")
    ignored = calendar_object("ignored", "Ignored")
    store.calendar_list = [selected, ignored]
    good = event("good")
    good.calendar = lambda: selected
    bad = event("bad")
    bad.calendar = lambda: ignored
    store.events = [good, bad]
    connector.set_selected_calendar_ids(["selected"])
    result = connector.snapshot()
    assert [r["target"]["eventId"] for r in result["events"]] == ["good"]
    assert store.query_calls[0][2] == [selected]
    assert [r["selected"] for r in connector.calendars()] == [True, False]


def test_missing_selected_calendar_is_partial_not_empty_success(setup):
    connector, store, _, _ = setup
    connector.set_selected_calendar_ids(["cal-removed"])
    result = connector.snapshot()
    assert result["status"] == "partial"
    assert result["missingCalendarIds"] == ["cal-removed"]
    assert store.query_calls == []


@pytest.mark.parametrize("selection", ["cal-id", [1], [""], {"id": "cal-example"}])
def test_invalid_selection_does_not_broaden_access(setup, selection):
    connector, _, _, _ = setup
    with pytest.raises(ValueError):
        connector.set_selected_calendar_ids(selection)


def test_revoked_permission_clears_previous_event_cache(setup):
    connector, store, state, _ = setup
    store.events = [event()]
    assert connector.snapshot()["events"]
    state["authorization"] = 2
    result = connector.snapshot()
    assert result["events"] == []
    assert connector._last_events == []
    assert result["stale"] is False


def test_deselected_calendar_is_not_retained_on_read_failure(setup):
    connector, store, _, _ = setup
    store.events = [event()]
    connector.snapshot()
    connector.set_selected_calendar_ids([])
    store.error = RuntimeError("private account information")
    result = connector.snapshot()
    assert result["events"] == []
    assert "private" not in json.dumps(result)


def test_read_failure_retains_marked_stale_metadata_only(setup):
    connector, store, _, _ = setup
    store.events = [event()]
    connector.snapshot()
    store.error = RuntimeError("private account information")
    result = connector.snapshot()
    assert result["status"] == "unavailable"
    assert result["events"][0]["stale"] is True
    assert "private" not in result["message"]


def test_invalid_event_does_not_block_healthy_events(setup):
    connector, store, _, _ = setup
    store.events = [obj(startDate=None), event()]
    result = connector.snapshot()
    assert result["status"] == "partial"
    assert len(result["events"]) == 1


def test_limit_keeps_earliest_events_not_native_unsorted_order(monkeypatch, setup):
    connector, store, _, _ = setup
    monkeypatch.setattr(calendar, "_MAX_EVENTS", 1)
    early = event("early", NOW + timedelta(minutes=10), NOW + timedelta(minutes=20))
    store.events = [event("late"), early]
    result = connector.snapshot()
    assert result["status"] == "partial"
    assert result["events"][0]["target"]["eventId"] == "early"


def test_missing_dependency_is_unavailable_not_empty_calendar(monkeypatch, setup):
    connector, store, _, _ = setup
    monkeypatch.setattr(connector, "_load", lambda: False)
    assert connector.snapshot()["status"] == "unavailable"
    assert store.query_calls == []


def test_explicit_request_uses_full_access_and_marshals_completion(setup):
    connector, store, state, queue = setup
    state["authorization"] = 0
    results = []
    connector.request_access(lambda *args: results.append(args))
    assert len(store.request_calls) == 1
    assert results == []
    state["authorization"] = 3
    store.request_calls[0](True, None)
    assert results == []  # OS callback never directly touches the UI.
    assert len(queue) == 1
    queue.pop()()
    assert results[0][0] is True
    assert connector._pending_access is False


def test_write_only_can_explicitly_request_full_access(setup):
    connector, store, state, _ = setup
    state["authorization"] = 4
    connector.request_access(lambda *_: None)
    assert len(store.request_calls) == 1


@pytest.mark.parametrize("authorization", [1, 2, 99])
def test_denied_restricted_unknown_requests_do_not_reprompt(setup, authorization):
    connector, store, state, _ = setup
    state["authorization"] = authorization
    results = []
    connector.request_access(lambda *args: results.append(args))
    assert results[0][0] is False
    assert store.request_calls == []


def test_usage_description_required_before_permission_request(setup):
    connector, store, state, _ = setup
    state.update(authorization=0, description=None)
    results = []
    connector.request_access(lambda *args: results.append(args))
    assert results[0][0] is False
    assert "完整的 haochen 安装包" in results[0][1]
    assert store.request_calls == []


def test_permission_request_is_rejected_from_worker(setup):
    connector, store, state, _ = setup
    state["authorization"] = 0
    errors = []

    def worker():
        try:
            connector.request_access(lambda *_: None)
        except ValueError as exc:
            errors.append(str(exc))

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(1)
    assert len(errors) == 1
    assert store.request_calls == []


def test_only_one_permission_request_in_flight(setup):
    connector, store, state, _ = setup
    state["authorization"] = 0
    connector.request_access(lambda *_: None)
    results = []
    connector.request_access(lambda *args: results.append(args))
    assert len(store.request_calls) == 1
    assert "正在进行" in results[0][1]


def test_completion_does_not_claim_read_access_when_grant_is_write_only(setup):
    connector, store, state, queue = setup
    state["authorization"] = 0
    results = []
    connector.request_access(lambda *args: results.append(args))
    state["authorization"] = 4
    store.request_calls[0](True, None)
    queue.pop()()
    assert results[0][0] is False


def test_unchanged_without_provider_timestamp_keeps_observed_update(monkeypatch, setup):
    connector, store, _, _ = setup
    store.events = [event()]
    first = connector.snapshot()["events"][0]
    monkeypatch.setattr(calendar, "_now", lambda: NOW + timedelta(seconds=1))
    second = connector.snapshot()["events"][0]
    assert first["updatedAt"] == second["updatedAt"]
    assert first["observedAt"] != second["observedAt"]
