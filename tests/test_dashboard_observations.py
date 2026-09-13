"""Observation semantics use isolated events, no applications or user contents."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
DashboardStore = importlib.import_module("haochen_app.dashboard.store").DashboardStore


def event(identifier="one", **extra):
    return {"id": identifier, "title": "Synthetic observation", "state": "processing",
            "summary": "Synthetic source details", "evidence": [], **extra}


def snapshot(*events, status="ready", **extra):
    return {"status": status, "events": list(events), **extra}


@pytest.fixture
def store(tmp_path):
    return DashboardStore(tmp_path)


def test_initial_baselines_are_independent_for_each_source(store):
    store.observe("otty", snapshot())
    store.observe("browser", snapshot(event("page"), status="connected"))
    assert store.snapshot()["history"] == []
    store.observe("otty", snapshot(event("pane")))
    history = store.snapshot()["history"]
    assert [(item["id"], item["changeType"]) for item in history] == [("pane", "appeared")]


def test_unknown_otty_hooks_do_not_hide_real_known_pane_changes_or_removal(store):
    unknown = event("unknown-pane", state="unknown")
    running = event("real-pane", state="processing")
    store.observe("otty", snapshot(unknown, running, status="partial", presenceComplete=True))
    assert all(not row["incomplete"] for row in store.snapshot()["events"])
    store.observe("otty", snapshot(unknown, {**running, "state": "idle"},
                                   status="partial", presenceComplete=True))
    assert store.snapshot()["history"][-1]["changeType"] == "changed"
    store.observe("otty", snapshot(unknown, status="partial", presenceComplete=True))
    assert [row["id"] for row in store.snapshot()["events"]] == ["unknown-pane"]
    assert store.snapshot()["history"][-1]["changeType"] == "removed"


def test_other_connector_cannot_claim_otty_presence_contract(store):
    store.observe("browser", snapshot(event("browser-page"), status="connected"))
    store.observe("browser", snapshot(status="partial", presenceComplete=True))
    assert store.snapshot()["events"][0]["stale"] is True


def test_truncated_otty_presence_claim_does_not_remove_existing_rows(store):
    store.observe("otty", snapshot(event("keep")))
    store.observe("otty", snapshot(status="partial", presenceComplete=True, truncated=True))
    assert store.snapshot()["events"][0]["stale"] is True


def test_partial_known_updates_are_recorded_once_without_losing_presence(store):
    first, second = event(), event("two")
    store.observe("otty", snapshot(first, second))
    updated = {**first, "state": "awaiting", "summary": "Synthetic permission request"}
    store.observe("otty", snapshot(updated, status="partial"))
    observed = store.snapshot()
    assert [(item["id"], item["changeType"]) for item in observed["history"]] == [("one", "changed")]
    assert observed["history"][0]["incomplete"] is True
    assert next(item for item in observed["events"] if item["id"] == "two")["stale"] is True
    store.observe("otty", snapshot(updated, second))
    assert len(store.snapshot()["history"]) == 1
    store.observe("otty", snapshot())
    removals = [item for item in store.snapshot()["history"] if item["changeType"] == "removed"]
    assert [item["id"] for item in removals] == ["one", "two"]
    assert all(item["state"] == "unknown" and item["status"] == "unknown" for item in removals)


@pytest.mark.parametrize("status", ["error", "unavailable", "partial"])
def test_empty_unreliable_response_keeps_last_known_rows_without_creating_history(store, status):
    store.observe("otty", snapshot(event()))
    store.observe("otty", snapshot(status=status))
    observed = store.snapshot()
    assert observed["events"][0]["stale"] is True
    assert observed["events"][0]["incomplete"] is True
    assert observed["history"] == []
    store.observe("otty", snapshot(event()))
    assert store.snapshot()["events"][0].get("stale") is not True
    assert store.snapshot()["history"] == []


@pytest.mark.parametrize("status", ["disconnected", "not_running", "not_connected", "permission_required"])
def test_reconnect_same_id_does_not_fabricate_changes_while_disconnected(store, status):
    store.observe("otty", snapshot(event(summary="Before connection gap")))
    store.observe("otty", snapshot(status=status))
    store.observe("otty", snapshot(event(summary="After connection gap", state="idle")))
    assert store.snapshot()["history"] == []
    store.observe("otty", snapshot(event(summary="Real later observation", state="awaiting")))
    assert [item["changeType"] for item in store.snapshot()["history"]] == ["changed"]


def test_restart_same_id_changed_contents_are_only_a_new_baseline(store):
    store.observe("otty", snapshot(event(summary="Before restart")))
    reloaded = DashboardStore(store.path.parent.parent)
    reloaded.observe("otty", snapshot(event(summary="After restart", state="idle")))
    assert reloaded.snapshot()["history"] == []
    assert "_observation_baselines" not in json.loads(store.path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("invalid", [
    {"status": "ready"},
    {"status": "ready", "events": None},
    {"status": "ready", "events": {"one": event()}},
    snapshot(None),
    snapshot({"id": ""}),
    snapshot({"missing": "id"}),
    snapshot(event(), event()),
    snapshot(event(stale=True)),
    snapshot(event(incomplete=True)),
    snapshot(incomplete=True),
    snapshot(truncated=True),
    snapshot(error="Synthetic partial failure"),
    snapshot(*(event(str(number)) for number in range(201))),
])
def test_malformed_or_truncated_snapshot_never_replaces_complete_presence_set(store, invalid):
    store.observe("otty", snapshot(event()))
    store.observe("otty", invalid)
    assert store.snapshot()["history"] == []
    store.observe("otty", snapshot())
    assert [(item["id"], item["changeType"]) for item in store.snapshot()["history"]] == [("one", "removed")]


def test_individual_revocation_cannot_repopulate_sensitive_history_as_a_removal(store):
    private = event("page", title="SYNTHETIC-REVOKED-TITLE", state="available",
                    target={"url": "https://example.test/selected"})
    store.observe("browser", snapshot(private, status="connected"))
    store.observe("browser", snapshot({**private, "summary": "changed"}, status="connected"))
    revoked = event("page", title="Permission required", summary="Site permission withdrawn",
                    state="permission_required", target=private["target"])
    store.observe("browser", snapshot(revoked, status="partial"))
    store.observe("browser", snapshot(status="connected"))
    assert store.snapshot()["history"] == []
    assert "SYNTHETIC-REVOKED-TITLE" not in json.dumps(store.snapshot())


def test_calendar_selection_resets_baseline_and_rejects_old_scope_snapshot(store):
    store.enable("calendar", True)
    store.calendars_update(["selected-before"])
    old_revision = store.source_revision("calendar")
    store.observe("calendar", snapshot(event("calendar-before")), revision=old_revision)
    store.calendars_update(["selected-after"])
    store.observe("calendar", snapshot(), revision=old_revision)
    store.observe("calendar", snapshot(event("calendar-after")), revision=store.source_revision("calendar"))
    assert store.snapshot()["history"] == []
    store.observe("calendar", snapshot())
    assert [item["id"] for item in store.snapshot()["history"]] == ["calendar-after"]


def test_presence_observation_retains_source_information_and_does_not_mutate_adapter_input(store):
    incoming = event("one")
    original = json.dumps(incoming, sort_keys=True)
    store.observe("otty", snapshot())
    store.observe("otty", snapshot(incoming))
    store.observe("otty", snapshot())
    history = store.snapshot()["history"]
    assert all("Synthetic source details" in item["summary"] for item in history)
    assert [item["changeType"] for item in history] == ["appeared", "removed"]
    assert json.dumps(incoming, sort_keys=True) == original
