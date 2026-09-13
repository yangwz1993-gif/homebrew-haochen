"""Scope/collection races with isolated stores and inert adapters; never TCC."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QCoreApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from haochen_app.dashboard import service as module  # noqa: E402
from haochen_app.dashboard.store import DashboardStore  # noqa: E402


def calendar_event():
    return {
        "id": "calendar:synthetic",
        "sourceId": "opaque-synthetic",
        "calendarId": "old-calendar",
        "source": "calendar",
        "title": "SYNTHETIC-DESELECTED",
        "summary": "Synthetic old metadata",
        "target": {"kind": "calendar", "calendarId": "old-calendar"},
        "evidence": [{"text": "SYNTHETIC-CALENDAR-EVIDENCE"}],
    }


def track_payload(locator="opaque-synthetic", kind="connector"):
    return {
        "title": "Keep my user-defined goal",
        "goal": "Keep this unchanged",
        "frequency": "manual",
        "aiEnabled": False,
        "sources": [{"type": kind, "locator": locator, "label": "Selected source"}],
    }


@pytest.fixture
def service(tmp_path, monkeypatch):
    app = QCoreApplication.instance() or QCoreApplication([])
    jobs = []
    monkeypatch.setattr(module, "run_in_background", lambda owner, work, done: jobs.append((work, done)))
    value = module.DashboardService(SimpleNamespace(home=tmp_path))
    value.store.enable("otty", False)
    value.store.enable("browser", False)
    value.store.enable("calendar", True)
    yield value, jobs, app
    value.stop()


def test_calendar_deselection_clears_generated_data_but_keeps_definitions(tmp_path):
    store = DashboardStore(tmp_path)
    store.enable("calendar", True)
    old_revision = store.source_revision("calendar")
    store.observe("calendar", {"status": "ready", "events": [calendar_event()]})
    store.observe("calendar", {"status": "ready", "events": [calendar_event() | {"summary": "changed"}]})
    track = store.track_save(track_payload(), create=True)
    store.track_result(track["id"], {"evidence": [{"text": "SYNTHETIC-OLD"}], "conclusion": "SYNTHETIC-OLD"})
    store.put_report({"date": "2026-09-13", "sections": [{"items": [{"text": "SYNTHETIC-OLD"}]}]})
    assert store.calendars_update([]) is True
    state = store.snapshot()
    assert state["events"] == state["history"] == state["reports"] == []
    remaining = state["tracks"][0]
    assert remaining["title"] == track["title"] and remaining["goal"] == track["goal"]
    assert remaining["sources"] == track["sources"] and remaining["evidence"] == []
    assert remaining["revision"] != track["revision"]
    store.observe("calendar", {"status": "ready", "events": [calendar_event()]}, revision=old_revision)
    store.track_result(track["id"], {"conclusion": "OLD-WORKER"}, revision=track["revision"])
    assert not store.snapshot()["events"]
    assert store.snapshot()["tracks"][0]["conclusion"] != "OLD-WORKER"
    assert store.calendars_update([]) is False  # Saving identical scope does not delete new work.


def test_calendar_scope_applies_only_latest_generation_off_main_thread(service):
    value, jobs, _app = service
    entered, released = threading.Event(), threading.Event()
    selected = []

    def setter(ids):
        if ids == ["first"]:
            entered.set()
            assert released.wait(2)
        selected.append(ids)

    value.adapters["calendar"] = SimpleNamespace(
        set_selected_calendar_ids=setter,
        snapshot=lambda: {
            "status": "ready",
            "events": [],
            "checkedAt": "2026-09-13T00:00:00Z",
        },
    )
    value.select_calendars(["first"])
    first_work, first_done = jobs.pop(0)
    worker = threading.Thread(target=first_work)
    worker.start()
    try:
        assert entered.wait(1)
        started = time.monotonic()
        value.select_calendars(["latest"])
        assert time.monotonic() - started < 0.2  # Never wait for EventKit's lock on GUI thread.
        latest_work, latest_done = jobs.pop(0)
        assert value._calendar_selection_generation is not None
        value._collect("calendar")
        assert jobs == []  # Do not collect the old adapter scope during selection.
    finally:
        released.set()
        worker.join(timeout=2)
    first_done(None, None)
    latest_work()
    latest_done(None, None)
    assert selected[-1] == ["latest"] and value._calendar_selection_generation is None
    assert len(jobs) == 1  # Fresh collection starts promptly, no 120-second stale wait.


def test_obsolete_selection_and_old_collector_cannot_restore_previous_scope(service):
    value, jobs, _app = service
    selected = []
    value.adapters["calendar"] = SimpleNamespace(
        set_selected_calendar_ids=selected.append, snapshot=lambda: {"status": "ready", "events": [calendar_event()]}
    )
    value._collect("calendar")
    old_work, old_done = jobs.pop(0)
    value.select_calendars(["obsolete"])
    obsolete_work, obsolete_done = jobs.pop(0)
    value.select_calendars(["latest"])
    latest_work, latest_done = jobs.pop(0)
    latest_work()
    obsolete_work()
    assert selected == [["latest"]]
    obsolete_done(None, None)
    latest_done(None, None)
    assert not jobs  # Old collector still owns active, latest collection deferred.
    old_done(old_work(), None)
    assert not value.store.snapshot()["events"]
    assert len(jobs) == 1  # Obsolete collector completion releases active and retries latest.


def test_failed_calendar_selection_blocks_old_scope_until_explicit_retry(service):
    value, jobs, _app = service
    selected = []
    value.adapters["calendar"] = SimpleNamespace(
        set_selected_calendar_ids=selected.append, snapshot=lambda: {"status": "ready", "events": []}
    )
    value.select_calendars(["latest"])
    _work, done = jobs.pop(0)
    done(None, RuntimeError("synthetic apply failure"))
    assert value._calendar_selection_error
    value._collect("calendar")
    assert jobs == []
    value.select_calendars(["latest"])
    work, done = jobs.pop(0)
    work()
    done(None, None)
    assert not value._calendar_selection_error and selected == [["latest"]]


@pytest.mark.parametrize("action", ["cancel", "disable", "scope", "stop", "external-revoke"])
def test_job_token_exists_before_collection_and_all_revocations_cancel_it(service, monkeypatch, action):
    value, jobs, _app = service
    value.store.observe("calendar", {"status": "ready", "events": [calendar_event()]})
    track = value.store.track_save(track_payload(), create=True)
    calls = []
    monkeypatch.setattr(module, "refresh_track", lambda *args, **kwargs: calls.append(kwargs["cancel"]))
    value.refresh_track(track["id"])
    work, _done = jobs.pop(0)
    token = value._current_cancel
    assert isinstance(token, threading.Event) and not token.is_set()
    if action == "cancel":
        value.cancel_track(track["id"])
    elif action == "disable":
        value.enable("calendar", False)
    elif action == "scope":
        value.select_calendars([])
    elif action == "stop":
        value.stop()
    else:
        value.adapters["calendar"] = SimpleNamespace(
            snapshot=lambda: {
                "status": "permission_required",
                "events": [],
            }
        )
        value._collect("calendar")
        collect_work, _collect_done = jobs.pop(0)
        collect_work()
    assert token.is_set()
    work()
    assert calls == []  # A cancelled, not-yet-started collector never reaches AI.


def test_cancellation_during_collection_prevents_commit_even_if_backend_returns(service, monkeypatch):
    value, jobs, _app = service
    track = value.store.track_save(track_payload("https://example.test", "url"), create=True)

    def work(*args, cancel=None):
        assert cancel is value._current_cancel
        value.cancel_track(track["id"])
        return {"conclusion": "MUST-NOT-COMMIT", "status": "ready"}

    monkeypatch.setattr(module, "refresh_track", work)
    value.refresh_track(track["id"])
    queued, done = jobs.pop(0)
    done(queued(), None)
    assert value.store.snapshot()["tracks"][0]["conclusion"] != "MUST-NOT-COMMIT"
    assert value._current_cancel is None
