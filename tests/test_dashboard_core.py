"""Independent 0.5 core acceptance: real evidence boundaries, not UI mock data.

All files, app events and model processes in these tests are synthetic. No live
model call, permission prompt, application message or personal document is used.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from haochen_app.dashboard import store as store_module  # noqa: E402
from haochen_app.dashboard import tracking  # noqa: E402
from haochen_app.dashboard.store import DashboardStore  # noqa: E402


def track_payload(**overrides):
    return {
        "title": "Example delivery", "goal": "Watch acceptance feedback", "frequency": "hourly",
        "aiEnabled": False,
        "sources": [{"id": "source-example", "type": "url", "label": "Selected page",
                     "locator": "https://example.test/review"}], **overrides,
    }


def event(source="calendar", marker="SYNTHETIC-PRIVATE-EVENT"):
    return {
        "id": source + ":event-1", "source": source, "title": marker, "summary": "Example metadata",
        "state": "upcoming", "updatedAt": "2026-09-13T09:00:00+08:00", "target": {"kind": source},
        "evidence": [{"label": "source", "text": marker}],
    }


def revision(track):
    return track.get("revision", track["updatedAt"])


def source_snapshot():
    return {"files": [], "events": [], "settings": {"connectors": {"browser": True}}}


@pytest.fixture
def store(tmp_path):
    return DashboardStore(tmp_path / "profile")


def test_track_create_edit_pause_resume_delete_are_persistent_and_reference_only(store, tmp_path):
    selected = tmp_path / "selected.md"
    selected.write_text("example body", encoding="utf-8")
    item = store.add_files([str(selected)])[0]
    created = store.track_save(track_payload(sources=[
        {"id": "file-source", "type": "file", "label": "Selected file", "locator": item["id"]}
    ]), create=True)
    assert created["status"] == "pending"
    edited = store.track_save({**created, "title": "Updated goal", "frequency": "manual"})
    assert edited["id"] == created["id"]
    assert len(store.snapshot()["tracks"]) == 1
    store.track_pause(created["id"], True)
    assert store.snapshot()["tracks"][0]["paused"] is True
    store.track_pause(created["id"], False)
    assert DashboardStore(store.path.parent.parent).snapshot()["tracks"][0]["paused"] is False
    store.track_delete(created["id"])
    store.remove_file(item["id"])
    assert store.snapshot()["tracks"] == []
    assert selected.read_text(encoding="utf-8") == "example body"


def test_track_edited_in_same_second_rejects_older_worker(monkeypatch, store):
    monkeypatch.setattr(store_module, "now", lambda: "2026-09-13T09:00:00+08:00")
    old = store.track_save(track_payload(), create=True)
    updated = store.track_save({**old, "goal": "Different goal and sources"})
    store.track_result(old["id"], {"conclusion": "OBSOLETE-RESULT", "status": "ready"}, revision=revision(old))
    actual = store.snapshot()["tracks"][0]
    assert actual["goal"] == updated["goal"]
    assert actual["conclusion"] != "OBSOLETE-RESULT", "Second-resolution timestamps are not revisions"


def test_pausing_track_invalidates_inflight_result(store):
    old = store.track_save(track_payload(), create=True)
    store.track_pause(old["id"], True)
    store.track_result(old["id"], {"conclusion": "RESULT-AFTER-PAUSE", "status": "ready"}, revision=revision(old))
    assert store.snapshot()["tracks"][0]["conclusion"] != "RESULT-AFTER-PAUSE"


def test_deleted_track_cannot_be_resurrected_by_worker(store):
    old = store.track_save(track_payload(), create=True)
    store.track_delete(old["id"])
    store.track_result(old["id"], {"conclusion": "late"}, revision=revision(old))
    assert store.snapshot()["tracks"] == []


def test_source_file_must_be_explicitly_selected(store, tmp_path):
    secret = tmp_path / "not-selected.md"
    secret.write_text("synthetic unselected content", encoding="utf-8")
    with pytest.raises(ValueError):
        store.track_save(track_payload(sources=[
            {"id": "x", "type": "file", "label": "file", "locator": str(secret)}
        ]), create=True)
    assert store.snapshot()["tracks"] == []


def test_no_entire_home_or_disk_in_file_shelf(store):
    for value in ("/", str(Path.home())):
        with pytest.raises(ValueError):
            store.add_files([value])


def test_permission_revocation_does_not_resurrect_cached_events(store):
    store.observe("calendar", {"status": "ready", "events": [event()]})
    store.observe("calendar", {"status": "permission_required", "authorization": "denied", "events": []})
    assert not [e for e in store.snapshot()["events"] if e["source"] == "calendar"]


def test_disabling_connector_scrubs_derived_private_history_reports_and_track_evidence(store):
    marker = "SYNTHETIC-REVOKED-CONTENT"
    first = event(marker=marker)
    store.observe("calendar", {"status": "ready", "events": [first]})
    store.observe("calendar", {"status": "ready", "events": [{**first, "summary": "Updated example"}]})
    saved = store.track_save(track_payload(sources=[
        {"id": "calendar-source", "type": "connector", "label": "Calendar", "locator": first["id"]}
    ]), create=True)
    store.track_result(saved["id"], {
        "conclusion": marker, "evidence": [{"sourceId": "calendar-source", "text": marker}],
    })
    store.put_report({"date": "2026-09-13", "sections": [{
        "title": "Example", "items": [{"text": marker, "source": "calendar", "evidence": first["evidence"]}]
    }]})
    store.enable("calendar", False)
    assert marker not in json.dumps(store.snapshot()), "Disconnect must also remove sensitive derived snapshots"


def test_disconnect_scrubs_track_evidence_even_when_source_event_has_disappeared(store):
    marker = "SYNTHETIC-OLD-CALENDAR-CONTENT"
    source_event = event(marker=marker)
    store.observe("calendar", {"status": "ready", "events": [source_event]})
    saved = store.track_save(track_payload(sources=[
        {"id": "old-calendar", "type": "connector", "label": "Calendar", "locator": source_event["id"]}
    ]), create=True)
    store.track_result(saved["id"], {
        "conclusion": marker, "evidence": [{"sourceId": "old-calendar", "text": marker}],
    })
    store.observe("calendar", {"status": "ready", "events": []})
    store.enable("calendar", False)
    assert marker not in json.dumps(store.snapshot())


def test_individual_browser_site_revocation_scrubs_derived_track_content(store):
    marker = "SYNTHETIC-REVOKED-SITE-CONTENT"
    source_event = {
        **event("browser", "Selected page"), "state": "available", "sourceId": "browser-source-1",
        "target": {"kind": "browser", "url": "https://example.test/review"},
    }
    store.observe("browser", {"status": "connected", "events": [source_event]})
    saved = store.track_save(track_payload(), create=True)
    store.track_result(saved["id"], {
        "conclusion": marker, "evidence": [{"sourceId": "source-example", "text": marker}],
    })
    store.observe("browser", {"status": "partial", "events": [
        {**source_event, "state": "permission_required", "summary": "Site permission withdrawn"}
    ]})
    assert marker not in json.dumps(store.snapshot())


def test_real_network_failure_is_not_no_changes_even_when_repeated():
    selected = track_payload()
    browser = SimpleNamespace(evidence=lambda _: {"status": "permission_required", "content": ""})
    result = tracking.refresh_track(selected, source_snapshot(), browser)
    again = tracking.refresh_track({**selected, **result}, source_snapshot(), browser)
    assert again["status"] == "unavailable"
    assert again["incomplete"] is True
    assert "无法" in again["conclusion"] or "没有取得" in again["conclusion"]
    assert "没有变化" not in again["conclusion"]
    assert "一致" not in again["conclusion"]


def test_model_is_not_called_without_readable_evidence():
    selected = track_payload(aiEnabled=True)
    browser = SimpleNamespace(evidence=lambda _: {"status": "unavailable", "content": ""})
    summary = SimpleNamespace(summarize=lambda *_: pytest.fail("must not fabricate evidence"))
    result = tracking.refresh_track(selected, source_snapshot(), browser, summary)
    assert result["status"] == "unavailable"


def test_ai_failure_keeps_evidence_and_marks_partial():
    selected = track_payload(aiEnabled=True)
    browser = SimpleNamespace(evidence=lambda _: {"status": "available", "content": "example observation"})

    def fail(*args):
        raise ValueError("Example provider unavailable")

    result = tracking.refresh_track(selected, source_snapshot(), browser, SimpleNamespace(summarize=fail))
    assert result["status"] == "partial"
    assert result["evidence"][0]["text"] == "example observation"
    assert result["error"]
    assert result["aiSummary"] is False


def test_partial_source_never_claims_fully_checked():
    selected = track_payload()
    browser = SimpleNamespace(evidence=lambda _: {"status": "partial", "content": "Only first section"})
    result = tracking.refresh_track(selected, source_snapshot(), browser)
    assert result["status"] == "partial"
    assert result["incomplete"] is True
    assert "不能判断整体没有变化" in result["error"]


@pytest.mark.parametrize("length", [48_000, 48_001, 60_000])
def test_browser_local_text_limit_is_explicit_and_no_content_alias_reaches_model(length):
    text = "字" * min(length, tracking.MAX_SOURCE_TEXT) + "尾" * max(0, length - tracking.MAX_SOURCE_TEXT)
    observed = {"status": "available", "content": text, "coverage": "dom-visible"}
    browser = SimpleNamespace(evidence=lambda _: observed)
    sent = []

    def summarize(_, __, evidence):
        sent.extend(evidence)
        return "仅依据本次取得的内容，仍需确认完整进展。"

    result = tracking.refresh_track(track_payload(aiEnabled=True), source_snapshot(), browser,
                                    SimpleNamespace(summarize=summarize))
    partial = length > tracking.MAX_SOURCE_TEXT
    assert result["status"] == ("partial" if partial else "ready")
    assert result["incomplete"] is partial
    assert sent == result["evidence"] and len(sent) == 1
    assert sent[0]["text"] == text[:tracking.MAX_SOURCE_TEXT]
    assert "content" not in sent[0] and "尾" not in json.dumps(sent, ensure_ascii=False)
    assert observed["content"] == text  # Never mutate the adapter cache.
    if partial:
        assert sent[0]["truncated"] is True
        assert "未完整读取" in sent[0]["coverage"] and f"{length:,}" in sent[0]["coverage"]
        assert "不能判断整体没有变化" in result["error"]
    else:
        assert sent[0]["coverage"] == "dom-visible" and not result["error"]


def test_truncated_browser_tail_changes_still_change_comparison_fingerprint():
    observed = {"status": "available", "content": "x" * 48_000 + "old-tail",
                "coverage": "dom-visible", "updatedAt": "2026-09-13T09:00:00+08:00"}
    browser = SimpleNamespace(evidence=lambda _: observed)
    first = tracking.refresh_track(track_payload(), source_snapshot(), browser)
    observed["content"] = "x" * 48_000 + "new-tail"
    second = tracking.refresh_track({**track_payload(), **first}, source_snapshot(), browser)
    assert first["evidence"][0]["text"] == second["evidence"][0]["text"]
    assert first["digest"] != second["digest"] and second["changed"] is True
    assert second["incomplete"] is True and second["status"] == "partial"


def test_upstream_fingerprint_is_kept_even_when_supplied_preview_is_unchanged():
    observed = {"status": "available", "content": "same upstream excerpt", "coverage": "dom-visible-truncated",
                "fingerprint": "synthetic-upstream-first", "updatedAt": "2026-09-13T09:00:00+08:00"}
    browser = SimpleNamespace(evidence=lambda _: observed)
    first = tracking.refresh_track(track_payload(), source_snapshot(), browser)
    observed["fingerprint"] = "synthetic-upstream-second"
    second = tracking.refresh_track({**track_payload(), **first}, source_snapshot(), browser)
    assert second["evidence"][0]["fingerprint"] == "synthetic-upstream-second"
    assert first["digest"] != second["digest"] and second["changed"] is True
    assert second["incomplete"] is True


def test_disabled_browser_connector_is_not_read_or_sent_to_model():
    selected = track_payload(aiEnabled=True)
    browser = SimpleNamespace(evidence=lambda _: pytest.fail("disabled connector cannot supply evidence"))
    summary = SimpleNamespace(summarize=lambda *_: pytest.fail("revoked evidence cannot reach model"))
    snapshot = {"files": [], "events": [], "settings": {"connectors": {"browser": False}}}
    result = tracking.refresh_track(selected, snapshot, browser, summary)
    assert result["status"] == "unavailable"
    assert not any(item.get("text") for item in result["evidence"])


def test_cancel_during_delayed_file_capture_never_reaches_ai(monkeypatch):
    cancellation, started, release = threading.Event(), threading.Event(), threading.Event()
    errors, calls = [], []
    track = track_payload(aiEnabled=True, sources=[
        {"id": "selected-file", "type": "file", "label": "Selected file", "locator": "synthetic-file"},
    ])
    snapshot = {**source_snapshot(), "files": [{"id": "synthetic-file", "path": "/synthetic-selected-file.md"}]}

    def delayed_file(_):
        started.set()
        assert release.wait(3)
        return {"status": "ready", "text": "SYNTHETIC-REVOKED-BEFORE-AI"}

    def run():
        try:
            tracking.refresh_track(track, snapshot, None, SimpleNamespace(summarize=lambda *a, **kw: calls.append(a)),
                                   cancel=cancellation)
        except ValueError as exc:
            errors.append(str(exc))

    monkeypatch.setattr(tracking, "file_evidence", delayed_file)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert started.wait(3)
        cancellation.set()
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive() and calls == []
    assert len(errors) == 1 and "取消" in errors[0]


def test_cancelled_collection_does_not_start_even_first_channel():
    cancellation = threading.Event()
    cancellation.set()
    browser = SimpleNamespace(evidence=lambda _: pytest.fail("cancelled job cannot read a channel"))
    with pytest.raises(ValueError, match="取消"):
        tracking.refresh_track(track_payload(), source_snapshot(), browser, cancel=cancellation)


def test_summary_receives_same_collection_token_and_late_cancel_rejects_result():
    cancellation = threading.Event()
    browser = SimpleNamespace(evidence=lambda _: {"status": "available", "content": "Synthetic evidence"})

    def late_cancel(*_, cancel=None):
        assert cancel is cancellation
        cancellation.set()
        return "MUST-NOT-BE-COMMITTED-AFTER-WITHDRAWAL"

    with pytest.raises(ValueError, match="取消"):
        tracking.refresh_track(track_payload(aiEnabled=True), source_snapshot(), browser,
                               SimpleNamespace(summarize=late_cancel), cancel=cancellation)


def test_daily_report_uses_observed_history_not_initial_or_unrelated_events(store):
    today = datetime.now().astimezone().date().isoformat()
    store.observe("otty", {"status": "ready", "events": [event("otty", "INITIAL-SNAPSHOT-ONLY")]})
    report = tracking.daily_report(store.snapshot(), today)
    assert report["sections"][0]["items"] == []
    changed = event("otty", "OBSERVED-CHANGE")
    store.observe("otty", {"status": "ready", "events": [changed]})
    report = tracking.daily_report(store.snapshot(), today)
    assert "OBSERVED-CHANGE" in json.dumps(report)
    assert "INITIAL-SNAPSHOT-ONLY" not in json.dumps(report)
    assert report["incomplete"] is True


def test_calendar_agenda_baseline_is_not_fabricated_as_daily_activity(store):
    today = datetime.now().astimezone().date().isoformat()
    store.enable("calendar", True)
    store.observe("calendar", {"status": "ready", "events": [event("calendar", "SYNTHETIC-FUTURE-AGENDA")]})
    report = tracking.daily_report(store.snapshot(), today)
    assert report["sections"][0]["items"] == []
    assert "SYNTHETIC-FUTURE-AGENDA" not in json.dumps(report)
    assert report["status"] == "partial" and report["incomplete"] is True
    assert "不是全天所有应用记录" in report["coverage"]


def test_empty_valid_snapshot_establishes_baseline_and_next_entry_is_appeared(store):
    store.observe("otty", {"status": "ready", "events": []})
    incoming = {**event("otty", "NEW-OBSERVED-PANE"), "state": "idle"}
    store.observe("otty", {"status": "ready", "events": [incoming]})
    history = store.snapshot()["history"]
    assert len(history) == 1 and history[0]["changeType"] == "appeared"
    assert history[0]["id"] == incoming["id"] and history[0]["state"] != "completed"
    assert "新出现" in history[0]["summary"]
    store.observe("otty", {"status": "ready", "events": [incoming]})
    assert store.snapshot()["history"] == history


def test_removed_entry_is_observed_absence_not_task_completion_and_enters_daily_report(store):
    incoming = {**event("otty", "DISAPPEARED-PANE"), "state": "processing"}
    store.observe("otty", {"status": "ready", "events": [incoming]})
    assert store.snapshot()["history"] == []
    store.observe("otty", {"status": "ready", "events": []})
    history = store.snapshot()["history"]
    assert len(history) == 1 and history[0]["changeType"] == "removed"
    assert history[0]["id"] == incoming["id"] and history[0]["state"] != "completed"
    assert "不代表" in history[0]["summary"]
    assert store.snapshot()["events"] == []
    report = tracking.daily_report(store.snapshot(), datetime.now().astimezone().date().isoformat())
    assert "DISAPPEARED-PANE" in json.dumps(report) and "不代表" in json.dumps(report, ensure_ascii=False)
    store.observe("otty", {"status": "ready", "events": []})
    assert store.snapshot()["history"] == history


@pytest.mark.parametrize("status", ["partial", "error", "unavailable", "disconnected", "not_running",
                                     "permission_required"])
def test_incomplete_or_disconnected_snapshot_never_synthesizes_removal(store, status):
    store.observe("otty", {"status": "ready", "events": [event("otty")]})
    store.observe("otty", {"status": status, "events": []})
    assert store.snapshot()["history"] == []


def test_partial_subset_cannot_replace_complete_presence_baseline(store):
    first = event("otty", "FIRST-PANE")
    second = {**event("otty", "SECOND-PANE"), "id": "otty:event-2"}
    store.observe("otty", {"status": "ready", "events": [first, second]})
    store.observe("otty", {"status": "partial", "events": [first]})
    assert store.snapshot()["history"] == []
    store.observe("otty", {"status": "ready", "events": []})
    history = store.snapshot()["history"]
    assert {e["id"] for e in history if e.get("changeType") == "removed"} == {first["id"], second["id"]}


@pytest.mark.parametrize("status", ["disconnected", "not_running", "permission_required"])
def test_reconnection_establishes_new_baseline_not_offline_activity(store, status):
    store.observe("otty", {"status": "ready", "events": [event("otty", "BEFORE-DISCONNECTION")]})
    store.observe("otty", {"status": status, "events": []})
    replacement = {**event("otty", "AFTER-RECONNECTION"), "id": "otty:replacement"}
    store.observe("otty", {"status": "ready", "events": [replacement]})
    assert store.snapshot()["history"] == []
    store.observe("otty", {"status": "ready", "events": []})
    assert [e["id"] for e in store.snapshot()["history"] if e.get("changeType") == "removed"] == [replacement["id"]]


def test_disabled_source_and_stale_revision_cannot_seed_a_presence_baseline(store):
    old_revision = store.source_revision("otty")
    store.enable("otty", False)
    store.observe("otty", {"status": "ready", "events": []})
    store.enable("otty", True)
    store.observe("otty", {"status": "ready", "events": []}, revision=old_revision)
    store.observe("otty", {"status": "ready", "events": [event("otty")]}, revision=store.source_revision("otty"))
    assert store.snapshot()["history"] == []


def test_restart_first_valid_snapshot_is_not_claimed_as_activity_while_app_was_closed(store):
    store.observe("otty", {"status": "ready", "events": [event("otty", "BEFORE-RESTART")]})
    reloaded = DashboardStore(store.path.parent.parent)
    reloaded.observe("otty", {"status": "ready", "events": []})
    assert reloaded.snapshot()["history"] == []
    reloaded.observe("otty", {"status": "ready", "events": [event("otty", "AFTER-RESTART")]})
    assert [e.get("changeType") for e in reloaded.snapshot()["history"]] == ["appeared"]


@pytest.mark.parametrize("initial", [
    {"status": "partial", "events": []},
    {"status": "ready", "events": [{"missing": "event-id"}]},
    {"status": "ready", "events": [{**event("otty"), "stale": True}]},
    {"status": "ready", "events": [{**event("otty"), "id": f"otty:item-{n}"} for n in range(201)]},
])
def test_non_authoritative_snapshot_cannot_be_initial_presence_baseline(store, initial):
    store.observe("otty", initial)
    store.observe("otty", {"status": "ready", "events": [event("otty", "FIRST-VALID-BASELINE")]})
    assert store.snapshot()["history"] == []


def test_historical_report_is_not_regenerated_using_todays_state(store):
    yesterday = (datetime.now().astimezone() - timedelta(days=1)).date().isoformat()
    original = {"date": yesterday, "title": "Recorded yesterday", "sections": []}
    store.put_report(original)
    store.observe("otty", {"status": "ready", "events": [event("otty", "ONLY-TODAY")]})
    result = tracking.daily_report(store.snapshot(), yesterday)
    assert result == original
    assert "ONLY-TODAY" not in json.dumps(result)


def test_file_evidence_reads_only_selected_utf8_document(tmp_path):
    path = tmp_path / "example.md"
    path.write_text("# example\nA complete body", encoding="utf-8")
    result = tracking.file_evidence({"path": str(path)})
    assert result["status"] == "ready"
    assert result["text"] == "# example\nA complete body"


def test_symlink_final_component_is_not_read(tmp_path):
    private = tmp_path / "not-selected.md"
    private.write_text("DO-NOT-READ", encoding="utf-8")
    entry = tmp_path / "selected.md"
    entry.symlink_to(private)
    result = tracking.file_evidence({"path": str(entry)})
    assert result["status"] == "unavailable"
    assert "DO-NOT-READ" not in json.dumps(result)


def test_substituted_symlink_ancestor_does_not_escape_selected_path(tmp_path, store):
    work = tmp_path / "work"
    work.mkdir()
    selected = work / "example.md"
    selected.write_text("permitted example", encoding="utf-8")
    entry = store.add_files([str(selected)])[0]
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "example.md").write_text("UNSELECTED-SECRET-SENTINEL", encoding="utf-8")
    work.rename(tmp_path / "work-before")
    work.symlink_to(outside, target_is_directory=True)
    result = tracking.file_evidence(entry)
    assert result["status"] == "unavailable"
    assert "UNSELECTED-SECRET-SENTINEL" not in json.dumps(result)


def test_directory_is_only_direct_metadata_not_recursive_text(tmp_path):
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "top.md").write_text("TOP-BODY-NOT-FOR-DIRECTORY-LIST", encoding="utf-8")
    nested = selected / "nested"
    nested.mkdir()
    (nested / "inside.md").write_text("DEEP-BODY", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("OUTSIDE-BODY", encoding="utf-8")
    (selected / "shortcut").symlink_to(outside)
    result = tracking.file_evidence({"path": str(selected)})
    assert result["status"] == "ready"
    assert "top.md" in result["text"] and "nested" in result["text"]
    assert "inside.md" not in result["text"]
    assert "shortcut" not in result["text"]
    assert "BODY" not in result["text"]


def test_oversized_or_non_text_file_is_metadata_not_claimed_complete(tmp_path):
    for filename in ("large.md", "unknown.binary"):
        path = tmp_path / filename
        path.write_bytes(b"x" * 256_001)
        result = tracking.file_evidence({"path": str(path)})
        assert result["status"] == "partial"
        assert len(result["text"]) < 1000


def test_48k_text_limit_is_explicit_partial(tmp_path):
    path = tmp_path / "long.md"
    path.write_text("x" * 48_001, encoding="utf-8")
    result = tracking.file_evidence({"path": str(path)})
    assert result["status"] == "partial"
    assert len(result["text"]) <= 48_000
    assert "未完整读取" in result["coverage"]


@pytest.fixture
def summary_setup(monkeypatch, tmp_path):
    binary = tmp_path / "synthetic-engine"
    binary.touch()
    keychain = SimpleNamespace(get_without_ui=lambda provider: "FAKE-SUMMARY-KEY")
    config = SimpleNamespace(home=tmp_path, agent_dir=tmp_path / "agent", keychain=keychain,
                             default_model=lambda: ("example-provider", "example-model"))
    monkeypatch.setattr(tracking.paths, "engine_binary", lambda: binary)
    calls = []

    class Process:
        returncode = 0
        pid = 900001
        stdin = None
        stdout = None

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

    def spawn(argv, **kwargs):
        calls.append(("spawn", argv, {**kwargs, "env": dict(kwargs["env"])}))
        return Process()

    monkeypatch.setattr(tracking.subprocess, "Popen", spawn)
    worker = tracking.SummaryWorker(config)

    def exchange(process, payload, deadline, cancellation):
        calls.append(("exchange", payload.decode("utf-8"), deadline))
        return "A concise evidence-based conclusion"

    monkeypatch.setattr(worker, "_exchange", exchange)
    return worker, calls, Process


def test_summary_is_isolated_tool_free_and_evidence_goes_via_stdin(summary_setup):
    worker, calls, _ = summary_setup
    result = worker.summarize("Example title", "Example goal", [{"text": "SELECTED-EVIDENCE"}])
    assert result
    _, argv, kwargs = calls[0]
    assert all(flag in argv for flag in [
        "--print", "--no-session", "--no-tools", "--no-extensions", "--no-skills",
        "--no-context-files", "--no-prompt-templates", "--no-approve",
    ])
    assert "SELECTED-EVIDENCE" not in " ".join(argv)
    assert "SELECTED-EVIDENCE" in calls[1][1]
    assert kwargs["cwd"].name == "work"
    assert kwargs["cwd"].parent.parent.name == "summary-work"
    assert not kwargs["cwd"].exists()  # Per-job directory removed after completion.
    assert kwargs["stderr"] == subprocess.DEVNULL
    assert kwargs["start_new_session"] is True


def test_stop_prevents_launching_model(summary_setup):
    worker, calls, _ = summary_setup
    worker.stop()
    with pytest.raises(ValueError, match="停止"):
        worker.summarize("Example", "Example", [{"text": "evidence"}])
    assert calls == []


def test_summary_input_limit_does_not_silently_truncate(summary_setup):
    worker, calls, _ = summary_setup
    with pytest.raises(ValueError, match="超过"):
        worker.summarize("Example", "Example", [{"text": "x" * 180_001}])
    assert calls == []


def test_summary_timeout_kills_only_owned_process(monkeypatch, summary_setup):
    worker, calls, process_type = summary_setup
    killed = []
    monkeypatch.setattr(worker, "_kill", lambda process: killed.append(process.pid))

    def timeout(*args):
        raise ValueError("模型摘要超时")

    monkeypatch.setattr(worker, "_exchange", timeout)
    monkeypatch.setattr(process_type, "poll", lambda _: None)
    with pytest.raises(ValueError, match="超时"):
        worker.summarize("Example", "Example", [{"text": "evidence"}])
    assert killed == [900001]
    assert worker._process is None


def test_cancellation_during_model_call_does_not_return_success(monkeypatch, summary_setup):
    worker, _, process_type = summary_setup
    monkeypatch.setattr(worker, "_kill", lambda _: None)

    def after_cancellation(*args):
        worker.stop()
        return "SHOULD-NOT-BE-USED-AFTER-CANCEL"

    monkeypatch.setattr(worker, "_exchange", after_cancellation)
    with pytest.raises(ValueError):
        worker.summarize("Example", "Example", [{"text": "evidence"}])


@pytest.fixture
def real_summary_setup(monkeypatch, tmp_path):
    """A real local process pretending to be the engine, never a model request."""
    binary = tmp_path / "synthetic-engine"
    profile = tmp_path / "profile"
    agent = profile / "agent"
    agent.mkdir(parents=True)
    key_reads = []

    def key(provider):
        key_reads.append(provider)
        return "SYNTHETIC-SELECTED-KEY"

    config = SimpleNamespace(
        home=profile, agent_dir=agent,
        keychain=SimpleNamespace(get_without_ui=key, get=lambda _: pytest.fail("no interactive key read")),
        default_model=lambda: ("custom-example", "example-model"),
    )
    catalog = {"providers": {"custom-example": {
        "baseUrl": "https://model.example.test/v1", "api": "openai-completions",
        "models": [{"id": "example-model", "name": "Example model", "reasoning": False,
                    "input": ["text"], "maxTokens": 4096}],
    }}}
    (agent / "models.json").write_text(json.dumps(catalog), encoding="utf-8")
    monkeypatch.setattr(tracking.paths, "engine_binary", lambda: binary)
    processes = []
    real_popen = subprocess.Popen

    def launch(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(tracking.subprocess, "Popen", launch)

    def script(body):
        binary.write_text(f"#!{sys.executable}\n" + body + "\n", encoding="utf-8")
        binary.chmod(0o700)

    script("import sys; sys.stdin.read(); print('Example result')")
    return SimpleNamespace(worker=tracking.SummaryWorker(config), config=config, catalog=catalog,
                           script=script, processes=processes, key_reads=key_reads, tmp=tmp_path)


def test_real_summary_config_copies_only_selected_model_and_no_secret_to_disk(monkeypatch, real_summary_setup):
    setup = real_summary_setup
    provider = setup.catalog["providers"]["custom-example"]
    provider["models"].append({"id": "other-model", "headers": {"Authorization": "!do-not-execute"}})
    setup.catalog["providers"]["other-provider"] = {"apiKey": "!do-not-execute"}
    (setup.config.agent_dir / "models.json").write_text(json.dumps(setup.catalog), encoding="utf-8")
    (setup.config.agent_dir / "auth.json").write_text(
        json.dumps({"custom-example": {"type": "api_key", "key": "!do-not-execute-shared-auth"}}), encoding="utf-8"
    )
    (setup.config.agent_dir / "settings.json").write_text(
        json.dumps({"packages": ["do-not-load"], "extensions": ["do-not-load"]}), encoding="utf-8"
    )
    monkeypatch.setenv("OPENAI_API_KEY", "SYNTHETIC-UNRELATED-KEY")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "SYNTHETIC-UNRELATED-KEY")
    monkeypatch.setenv("NODE_OPTIONS", "--require do-not-load")
    monkeypatch.setenv("PI_SESSION_ID", "do-not-join-current-chat")
    setup.script("""import sys, os, json, pathlib
agent = pathlib.Path(os.environ['PI_CODING_AGENT_DIR'])
models = json.loads((agent / 'models.json').read_text())
auth = json.loads((agent / 'auth.json').read_text())
settings = json.loads((agent / 'settings.json').read_text())
payload = sys.stdin.read()
print(json.dumps({
 'providers': list(models['providers']),
 'models': [m['id'] for m in models['providers']['custom-example']['models']],
 'authProviders': list(auth), 'authRef': auth['custom-example']['key'],
 'diskHasSecret': any('SYNTHETIC-SELECTED-KEY' in f.read_text() for f in agent.iterdir() if f.is_file()),
 'keyPresent': os.getenv('HAOCHEN_SUMMARY_API_KEY') == 'SYNTHETIC-SELECTED-KEY',
 'unrelatedEnv': [k for k in ['OPENAI_API_KEY','ANTHROPIC_API_KEY','NODE_OPTIONS','PI_SESSION_ID'] if k in os.environ],
 'payloadPresent': 'ONLY-SELECTED-EVIDENCE' in payload,
 'packages': settings.get('packages'), 'extensions': settings.get('extensions'),
 'fileModes': [f.stat().st_mode & 0o777 for f in agent.iterdir() if f.is_file()],
 'directoryMode': agent.stat().st_mode & 0o777,
}))""")
    result = json.loads(setup.worker.summarize("Example", "Example", [{"text": "ONLY-SELECTED-EVIDENCE"}]))
    assert result["providers"] == result["authProviders"] == ["custom-example"]
    assert result["models"] == ["example-model"]
    assert result["authRef"] == "$HAOCHEN_SUMMARY_API_KEY"
    assert result["diskHasSecret"] is False
    assert result["keyPresent"] is True
    assert result["unrelatedEnv"] == []
    assert result["payloadPresent"] is True
    assert result["packages"] == result["extensions"] == []
    assert set(result["fileModes"]) == {0o600}
    assert result["directoryMode"] == 0o700
    assert setup.key_reads == ["custom-example"]
    assert not list((setup.config.home / "dashboard" / "summary-work").iterdir())
    assert "do-not-execute-shared-auth" in (setup.config.agent_dir / "auth.json").read_text()


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(apiKey="!do-not-execute"),
    lambda p: p.update(apiKey="SYNTHETIC-INLINE-SECRET"),
    lambda p: p.update(headers={"Authorization": "!do-not-execute"}),
    lambda p: p["models"][0].update(headers={"Authorization": "$UNRELATED_KEY"}),
    lambda p: p.update(oauth="radius"),
    lambda p: p.update(api="bedrock-converse-stream"),
    lambda p: p.update(baseUrl="https://user:secret@example.test/v1"),
    lambda p: p.update(samplingParams={"tools": [{"name": "not-a-summary-tool"}]}),
])
def test_unsafe_selected_provider_never_launches_process(real_summary_setup, mutate):
    setup = real_summary_setup
    mutate(setup.catalog["providers"]["custom-example"])
    (setup.config.agent_dir / "models.json").write_text(json.dumps(setup.catalog), encoding="utf-8")
    with pytest.raises(ValueError):
        setup.worker.summarize("Example", "Example", [{"text": "example"}])
    assert setup.processes == []


@pytest.mark.parametrize(("definition", "expected"), [
    ({}, 4096),
    ({"models": [{"id": "fixture-model", "maxTokens": 384_000}]}, 4096),
    ({"models": [{"id": "fixture-model", "maxTokens": 128}]}, 128),
    ({"modelOverrides": {"fixture-model": {"maxTokens": 384_000}}}, 4096),
    ({"modelOverrides": {"fixture-model": {"maxTokens": 128}}}, 128),
    ({"models": [{"id": "fixture-model", "maxTokens": 128}],
      "modelOverrides": {"fixture-model": {"maxTokens": 2048}}}, 2048),
])
def test_summary_budget_caps_builtin_and_override_models(real_summary_setup, definition, expected):
    setup = real_summary_setup
    catalog = {"providers": {"example-built-in": definition}}
    (setup.config.agent_dir / "models.json").write_text(json.dumps(catalog), encoding="utf-8")
    safe = setup.worker._model_catalog("example-built-in", "fixture-model")["providers"]["example-built-in"]
    assert safe["modelOverrides"]["fixture-model"]["maxTokens"] == expected
    assert all(model["maxTokens"] <= 4096 for model in safe.get("models", []))
    assert setup.key_reads == [] and setup.processes == []


@pytest.mark.parametrize("budget", [True, 0, -1, "4096", 0.5])
def test_summary_rejects_invalid_output_budget_before_key_read(real_summary_setup, budget):
    setup = real_summary_setup
    setup.catalog["providers"]["custom-example"]["models"][0]["maxTokens"] = budget
    (setup.config.agent_dir / "models.json").write_text(json.dumps(setup.catalog), encoding="utf-8")
    with pytest.raises(ValueError, match="输出预算无效"):
        setup.worker.summarize("Example", "Example", [])
    assert setup.key_reads == [] and setup.processes == []


def test_summary_never_falls_back_to_interactive_keychain(real_summary_setup):
    setup = real_summary_setup
    setup.config.keychain = SimpleNamespace(get=lambda _: pytest.fail("must never call interactive get"))
    with pytest.raises(ValueError, match="无弹窗"):
        setup.worker.summarize("Example", "Example", [])
    assert setup.processes == []


def test_summary_keychain_failure_is_safe_and_does_not_launch(real_summary_setup):
    setup = real_summary_setup

    def denied(_):
        raise RuntimeError("SYNTHETIC-KEYCHAIN-ERROR-PRIVATE-DATA")

    setup.config.keychain = SimpleNamespace(get_without_ui=denied)
    with pytest.raises(ValueError) as result:
        setup.worker.summarize("Example", "Example", [])
    assert "PRIVATE-DATA" not in str(result.value)
    assert setup.processes == []


@pytest.mark.parametrize("body", [
    "import sys; sys.stdin.read(); print('x' * 200000)",
    "import sys; sys.stdin.read(); print('字' * 12001)",
])
def test_real_stdout_character_limit_aborts_and_reaps_child(real_summary_setup, body):
    setup = real_summary_setup
    setup.script(body)
    start = time.monotonic()
    with pytest.raises(ValueError, match="过长|安全输出"):
        setup.worker.summarize("Example", "Example", [])
    assert time.monotonic() - start < 2
    assert setup.processes[0].poll() is not None
    assert not list((setup.config.home / "dashboard" / "summary-work").iterdir())


def test_real_stdout_byte_limit_is_independent(monkeypatch, real_summary_setup):
    setup = real_summary_setup
    monkeypatch.setattr(setup.worker, "MAX_OUTPUT_CHARS", 1_000_000)
    setup.script("import sys; sys.stdin.read(); print('x' * 100000)")
    with pytest.raises(ValueError, match="安全输出"):
        setup.worker.summarize("Example", "Example", [])
    assert setup.processes[0].poll() is not None


@pytest.mark.parametrize("close_stdout", [False, True])
def test_real_summary_timeout_even_after_stdout_closes(monkeypatch, real_summary_setup, close_stdout):
    setup = real_summary_setup
    setup.script("import sys, os, time; sys.stdin.read(); "
                 + ("os.close(1); " if close_stdout else "") + "time.sleep(10)")
    monkeypatch.setattr(setup.worker, "TIMEOUT_SECONDS", 0.08)
    start = time.monotonic()
    with pytest.raises(ValueError, match="超时"):
        setup.worker.summarize("Example", "Example", [])
    assert time.monotonic() - start < 2
    assert setup.processes[0].poll() is not None


def test_real_cancel_is_reusable_and_does_not_return_stale_result(real_summary_setup):
    setup = real_summary_setup
    began = setup.tmp / "synthetic-began"
    setup.script(f"""import sys, pathlib, time
sys.stdin.read()
pathlib.Path({str(began)!r}).touch()
time.sleep(10)
print('STALE-AFTER-CANCEL')""")
    outcomes = []

    def run():
        try:
            outcomes.append(setup.worker.summarize("Example", "Example", []))
        except ValueError as exc:
            outcomes.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        deadline = time.monotonic() + 6
        while not began.exists() and time.monotonic() < deadline:
            time.sleep(0.005)
        assert began.exists()
    finally:
        setup.worker.cancel()
        thread.join(2)
    assert not thread.is_alive()
    assert len(outcomes) == 1 and isinstance(outcomes[0], ValueError)
    assert "取消" in str(outcomes[0])
    assert setup.processes[0].poll() is not None
    setup.script("import sys; sys.stdin.read(); print('New summary after cancel')")
    assert setup.worker.summarize("New", "New", []) == "New summary after cancel"


def test_real_summary_nonzero_exit_does_not_expose_stdout_stderr(real_summary_setup):
    setup = real_summary_setup
    setup.script("import sys; sys.stdin.read(); print('PRIVATE-OUT'); "
                 "print('PRIVATE-ERR', file=sys.stderr); sys.exit(2)")
    with pytest.raises(ValueError) as result:
        setup.worker.summarize("Example", "Example", [])
    assert "PRIVATE" not in str(result.value)


def test_real_summary_drains_output_before_child_reads_large_stdin(real_summary_setup):
    setup = real_summary_setup
    setup.script("import sys; print('prefix' * 1000, flush=True); data=sys.stdin.read(); print('finished')")
    result = setup.worker.summarize("Example", "Example", [{"text": "x" * 100000}])
    assert result.endswith("finished")


def test_real_summary_valid_utf8_across_chunk_boundaries(real_summary_setup):
    setup = real_summary_setup
    setup.script("import sys, os; sys.stdin.read(); os.write(1, ('字' * 10000).encode())")
    assert setup.worker.summarize("Example", "Example", []) == "字" * 10000


def test_real_summary_invalid_utf8_is_rejected_without_partial_result(real_summary_setup):
    setup = real_summary_setup
    setup.script("import sys, os; sys.stdin.read(); os.write(1, b'valid-prefix' + bytes([255]))")
    with pytest.raises(ValueError, match="编码"):
        setup.worker.summarize("Example", "Example", [])


def test_cancel_during_credential_preparation_prevents_spawn(real_summary_setup):
    setup = real_summary_setup

    def credential(_):
        setup.worker.cancel()
        return "SYNTHETIC-KEY"

    setup.config.keychain = SimpleNamespace(get_without_ui=credential)
    with pytest.raises(ValueError, match="取消"):
        setup.worker.summarize("Example", "Example", [])
    assert setup.processes == []
    assert setup.worker._active_cancel is None


def test_only_one_summary_job_runs_at_a_time(monkeypatch, summary_setup):
    worker, _, _ = summary_setup

    def exchange(*args):
        with pytest.raises(ValueError, match="已有摘要"):
            worker.summarize("Second", "Second", [])
        return "First summary"

    monkeypatch.setattr(worker, "_exchange", exchange)
    assert worker.summarize("First", "First", []) == "First summary"


def test_final_cancel_after_exchange_cannot_publish_result(monkeypatch, summary_setup):
    worker, _, process_type = summary_setup

    def wait(self, timeout=None):
        worker.cancel()
        return 0

    monkeypatch.setattr(process_type, "wait", wait)
    with pytest.raises(ValueError, match="取消"):
        worker.summarize("Example", "Example", [])


@pytest.fixture
def service_setup(monkeypatch, tmp_path):
    from haochen_app.dashboard import service as service_module

    config = SimpleNamespace(home=tmp_path / "profile")
    controller = service_module.DashboardService(config)
    jobs = []
    monkeypatch.setattr(service_module, "run_in_background", lambda owner, work, done: jobs.append((work, done)))
    return controller, jobs, service_module


def test_collector_exception_invalidates_old_running_state(service_setup):
    controller, jobs, _ = service_setup
    controller.store.observe("otty", {"status": "ready", "events": [
        {**event("otty"), "state": "processing"}]})

    def broken():
        raise OSError("SYNTHETIC-PRIVATE-ERROR")

    controller.adapters["otty"] = SimpleNamespace(snapshot=broken)
    controller._collect("otty")
    result = jobs[0][0]()
    jobs[0][1](result, None)
    assert controller.store.snapshot()["events"][0]["stale"] is True
    assert result["status"] == "error"
    assert "SYNTHETIC-PRIVATE-ERROR" not in str(result)
    # 行为变更（0.6.2-beta.3）：连接器自身的错误在连接卡片上常驻展示，刘海不常驻提醒；
    # 卡片上能看到错误状态
    connector = next(c for c in controller.state()["connectors"] if c["id"] == "otty")
    assert connector["status"] == "error"
    assert controller.state()["activity"]["kind"] == "idle"


def test_lost_connector_remembers_was_connected_until_explicit_disconnect(service_setup):
    controller, jobs, _ = service_setup
    controller.adapters["otty"] = SimpleNamespace(snapshot=lambda: {"status": "ready", "events": []})
    controller._collect("otty")
    jobs[0][1](jobs[0][0](), None)
    controller.adapters["otty"] = SimpleNamespace(snapshot=lambda: {"status": "not_running", "events": []})
    controller._collect("otty")
    jobs[1][1](jobs[1][0](), None)
    # 行为变更（0.6.2-beta.3）：连接器掉线不再在刘海常驻提醒；连接卡片显示「暂不可用」
    connector = next(c for c in controller.state()["connectors"] if c["id"] == "otty")
    assert connector["connection"] == "unavailable"
    assert controller.state()["activity"]["kind"] == "idle"
    controller.enable("otty", False)
    assert controller.state()["activity"]["kind"] == "idle"


def test_disabled_source_drops_inflight_collection(service_setup):
    controller, jobs, _ = service_setup

    def snapshot():
        controller.enable("otty", False)
        return {"status": "ready", "events": [event("otty", "OLD-COLLECTION")]}

    controller.adapters["otty"] = SimpleNamespace(snapshot=snapshot)
    controller._collect("otty")
    result = jobs[0][0]()
    jobs[0][1](result, None)
    assert "OLD-COLLECTION" not in json.dumps(controller.store.snapshot())
    controller.stop()


def test_disable_then_reenable_does_not_accept_old_generation(service_setup):
    controller, jobs, _ = service_setup

    def snapshot():
        # The collector began before these user actions; bool(enabled) alone
        # cannot distinguish this old result from a newly authorized collection.
        controller.enable("otty", False)
        controller.enable("otty", True)
        return {"status": "ready", "events": [event("otty", "PREVIOUS-GENERATION")]}

    controller.adapters["otty"] = SimpleNamespace(snapshot=snapshot)
    controller._collect("otty")
    result = jobs[0][0]()
    jobs[0][1](result, None)
    assert "PREVIOUS-GENERATION" not in json.dumps(controller.store.snapshot())
    controller.stop()


def test_disable_at_collection_commit_boundary_does_not_restore_data(monkeypatch, service_setup):
    controller, jobs, _ = service_setup
    controller.adapters["otty"] = SimpleNamespace(snapshot=lambda: {
        "status": "ready", "events": [event("otty", "COMMIT-RACE-CONTENT")]
    })
    observe = controller.store.observe

    def raced_observe(source, result, *args, **kwargs):
        # Revoke after the worker's optimistic enabled check but before it
        # obtains the store lock. Authorization must be checked at commit too.
        controller.enable("otty", False)
        return observe(source, result, *args, **kwargs)

    monkeypatch.setattr(controller.store, "observe", raced_observe)
    controller._collect("otty")
    result = jobs[0][0]()
    jobs[0][1](result, None)
    assert "COMMIT-RACE-CONTENT" not in json.dumps(controller.store.snapshot())
    controller.stop()


def test_service_stop_prevents_inflight_result_mutation(service_setup):
    controller, jobs, _ = service_setup

    def snapshot():
        controller.stop()
        return {"status": "ready", "events": [event("otty", "AFTER-STOP")]}

    controller.adapters["otty"] = SimpleNamespace(snapshot=snapshot)
    controller._collect("otty")
    result = jobs[0][0]()
    jobs[0][1](result, None)
    assert "AFTER-STOP" not in json.dumps(controller.store.snapshot())


def test_connection_and_coverage_are_separate_dimensions(service_setup):
    """C-11：连接态与内容态分离——partial（内容降级）也是「已连接」；失效计数不含归档/过期。"""
    controller, _jobs, _ = service_setup
    store = controller.store
    store.enable("browser", True)
    # 连接器快照直接放到 service 的 connectors 表（采集器回调路径），事件走 store
    controller.connectors["browser"] = {"status": "partial", "checkedAt": "now"}
    store.observe("browser", {"status": "partial", "checkedAt": "now", "events": [
        {"id": "browser:ok", "state": "available", "fingerprint": "a"},
        {"id": "browser:closed", "state": "tab_closed", "fingerprint": "b"},
        {"id": "browser:old", "state": "tab_closed", "fingerprint": "c", "archived": True},
    ]})
    state = controller.state()
    connector = next(c for c in state["connectors"] if c["id"] == "browser")
    assert connector["connection"] == "connected", "内容降级不得拖成未连接"
    assert connector["coverage"]["level"] == "partial"
    assert connector["coverage"]["broken"] == 1, "归档的旧追踪页不计入失效"
    assert connector["coverage"]["total"] == 3


def test_connection_dimensions_cover_disabled_and_unavailable(service_setup):
    controller, _jobs, _ = service_setup
    state = controller.state()
    by_id = {c["id"]: c for c in state["connectors"]}
    assert by_id["wechat"]["connection"] == "disabled"       # 默认未开启
    assert by_id["browser"]["connection"] == "unavailable"   # 默认开启但无数据 → 暂不可用
    store = controller.store
    store.enable("hi", True)
    store.observe("hi", {"status": "not_running", "checkedAt": "now", "events": []})
    state = controller.state()
    hi = next(c for c in state["connectors"] if c["id"] == "hi")
    assert hi["connection"] == "unavailable" and hi["coverage"]["level"] == "empty"


def test_dismiss_removes_event_and_filters_until_fingerprint_changes(store):
    """B-11：忽略后立即消失、持久化；同指纹的后续观察不再出现；内容真变允许重现。"""
    store.enable("hi", True)
    snapshot = {"status": "ready", "checkedAt": "now", "events": [
        {"id": "hi:msg:1", "state": "available", "fingerprint": "fp1", "title": "旧消息"},
    ]}
    store.observe("hi", snapshot)
    assert [e["id"] for e in store.snapshot()["events"]] == ["hi:msg:1"]

    store.dismiss_event("hi:msg:1", "fp1")
    assert store.snapshot()["events"] == []  # 立即移除

    store.observe("hi", snapshot)  # 同指纹再来 → 仍被忽略
    assert store.snapshot()["events"] == []

    # 内容真变（新指纹）→ 视为新动态，允许重现
    store.observe("hi", {"status": "ready", "checkedAt": "now", "events": [
        {"id": "hi:msg:1", "state": "available", "fingerprint": "fp2", "title": "内容更新了"},
    ]})
    assert [e["id"] for e in store.snapshot()["events"]] == ["hi:msg:1"]


def test_dismiss_persists_across_store_reopen(store, tmp_path):
    store.enable("hi", True)
    store.observe("hi", {"status": "ready", "checkedAt": "now", "events": [
        {"id": "hi:msg:9", "state": "available", "fingerprint": "fp", "title": "t"}]})
    store.dismiss_event("hi:msg:9", "fp")
    reopened = type(store)(tmp_path / "profile")
    reopened.enable("hi", True)
    reopened.observe("hi", {"status": "ready", "checkedAt": "now", "events": [
        {"id": "hi:msg:9", "state": "available", "fingerprint": "fp", "title": "t"}]})
    assert reopened.snapshot()["events"] == []


def test_dismiss_rejects_invalid_id(store):
    import pytest
    with pytest.raises(ValueError):
        store.dismiss_event("")
    with pytest.raises(ValueError):
        store.dismiss_event(None)
