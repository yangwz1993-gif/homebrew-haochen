"""Otty metadata, lifecycle and focus contracts; no real agent modifications."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from haochen_app.dashboard.adapters import otty  # noqa: E402


def pane(state="processing", **kwargs):
    return {
        "id": "p_demo_1", "tab_id": "t_demo_1", "window_id": "w_demo_1", "agent": "Demo Agent",
        "agent_session_id": "session-demo", "agent_state": state, "process": "Example task",
        "cwd": "/example/project", "active": True, **kwargs,
    }


@pytest.fixture
def adapter(monkeypatch):
    connector = otty.OttyAdapter()
    monkeypatch.setattr(connector, "_find_cli", lambda: Path("/example/Otty.app/Contents/MacOS/otty-cli"))
    monkeypatch.setattr(connector, "_is_running", lambda: True)
    return connector


def feed(monkeypatch, adapter, rows, *, active=True, focused=True):
    calls = []

    def run(*args):
        calls.append(args)
        if args == ("pane", "list"):
            if isinstance(rows, Exception):
                raise rows
            return rows
        if args == ("tab", "list"):
            return [{"id": "t_demo_1", "active": active}]
        if args == ("window", "list"):
            return [{"id": "w_demo_1", "focused": focused}]
        raise AssertionError(args)

    monkeypatch.setattr(adapter, "_run_cli", run)
    return calls


def test_initial_idle_is_not_a_completion(monkeypatch, adapter):
    calls = feed(monkeypatch, adapter, [pane("idle")])
    result = adapter.snapshot()
    assert result["status"] == "ready"
    assert result["changes"] == []
    assert result["events"][0]["state"] == "idle"
    assert "不代表事项已完成" in result["events"][0]["summary"]
    assert calls == [("pane", "list"), ("tab", "list"), ("window", "list")]


def test_unrecognized_agent_is_named_not_guessed(monkeypatch, adapter):
    # Reproduces the documented "kind=null" case: a terminal whose agent name is
    # not in the recognized set must be surfaced by name, never guessed a kind.
    feed(monkeypatch, adapter, [pane("processing", agent="cmux")])
    snap = adapter.snapshot()
    diagnosis = adapter.diagnostic(snap)
    assert diagnosis["unrecognizedAgents"] == 1
    assert diagnosis["unrecognizedNames"] == ["cmux"]
    assert diagnosis["agents"] == []  # no recognized kind → no install plan fabricated


@pytest.mark.parametrize("state", ["", "unexpected-value", "completed", None, 7])
def test_missing_or_unknown_lifecycle_never_inferred(monkeypatch, adapter, state):
    feed(monkeypatch, adapter, [pane(state)])
    result = adapter.snapshot()
    assert result["status"] == "partial"
    assert result["unknownStateCount"] == 1
    assert result["events"][0]["state"] == "unknown"
    assert result["events"][0]["stateSource"] == "unavailable"
    assert result["changes"] == []


def test_processing_to_idle_is_one_turn_finished_not_item_complete(monkeypatch, adapter):
    rows = [pane()]
    feed(monkeypatch, adapter, rows)
    adapter.snapshot()
    rows[0]["agent_state"] = "idle"
    second = adapter.snapshot()
    assert len(second["changes"]) == 1
    assert second["changes"][0]["kind"] == "turn_finished"
    assert "仍由你确认" in second["changes"][0]["summary"]
    assert adapter.snapshot()["changes"] == []


def test_awaiting_is_explicit_state(monkeypatch, adapter):
    rows = [pane()]
    feed(monkeypatch, adapter, rows)
    adapter.snapshot()
    rows[0]["agent_state"] = "awaiting"
    change = adapter.snapshot()["changes"][0]
    assert change["kind"] == "state_changed"
    assert change["state"] == "awaiting"


def test_session_replacement_not_false_completion(monkeypatch, adapter):
    rows = [pane()]
    feed(monkeypatch, adapter, rows)
    adapter.snapshot()
    rows[0].update(agent_state="idle", agent_session_id="new-session")
    assert adapter.snapshot()["changes"] == []


@pytest.mark.parametrize("replacement", [{"agent": "Different agent"}, {"agent_session_id": ""}])
def test_agent_replacement_or_missing_session_never_finishes_prior_turn(monkeypatch, adapter, replacement):
    rows = [pane()]
    feed(monkeypatch, adapter, rows)
    adapter.snapshot()
    rows[0].update(agent_state="idle", **replacement)
    assert adapter.snapshot()["changes"] == []


def test_disappearance_is_removed_not_completed(monkeypatch, adapter):
    rows = [pane()]
    feed(monkeypatch, adapter, rows)
    adapter.snapshot()
    rows.clear()
    result = adapter.snapshot()
    assert result["events"] == []
    assert result["changes"] == []
    assert result["removedIds"] == ["otty:p_demo_1"]
    assert result["status"] == "ready"


def test_timeout_marks_previous_stale_and_resets_transition_baseline(monkeypatch, adapter):
    feed(monkeypatch, adapter, [pane()])
    adapter.snapshot()
    feed(monkeypatch, adapter, otty.OttyError("timeout", "Otty 响应超时。"))
    result = adapter.snapshot()
    assert result["status"] == "unavailable"
    assert result["errorCode"] == "timeout"
    assert result["events"][0]["stale"] is True
    assert result["events"][0]["state"] == "unknown"
    assert result["removedIds"] == []
    feed(monkeypatch, adapter, [pane("idle")])
    assert adapter.snapshot()["changes"] == []


def test_partial_schema_does_not_falsely_remove_previous_panes(monkeypatch, adapter):
    feed(monkeypatch, adapter, [pane()])
    adapter.snapshot()
    feed(monkeypatch, adapter, [{"id": "p_invalid"}])
    result = adapter.snapshot()
    assert result["status"] == "partial"
    assert result["removedIds"] == []
    assert result["events"][0]["stale"] is True


def test_visibility_is_joined_not_pane_active_alone(monkeypatch, adapter):
    feed(monkeypatch, adapter, [pane()], active=False, focused=True)
    assert adapter.snapshot()["events"][0]["isVisible"] is False
    feed(monkeypatch, adapter, [pane()], active=True, focused=False)
    assert adapter.snapshot()["events"][0]["isVisible"] is False
    feed(monkeypatch, adapter, [pane()], active=True, focused=True)
    assert adapter.snapshot()["events"][0]["isVisible"] is True


def test_unchanged_metadata_does_not_change_updated_at(monkeypatch, adapter):
    feed(monkeypatch, adapter, [pane()])
    first = adapter.snapshot()
    second = adapter.snapshot()
    assert first["events"][0]["updatedAt"] == second["events"][0]["updatedAt"]
    # Callers cannot mutate the connector baseline via returned JSON.
    first["events"][0]["state"] = "idle"
    assert adapter.snapshot()["events"][0]["state"] == "processing"


def test_not_running_does_not_invoke_cli(monkeypatch, adapter):
    monkeypatch.setattr(adapter, "_is_running", lambda: False)
    monkeypatch.setattr(adapter, "_run_cli", lambda *a: pytest.fail("must not launch app"))
    assert adapter.snapshot()["status"] == "not_running"


def test_missing_app_is_distinct(monkeypatch, adapter):
    monkeypatch.setattr(adapter, "_find_cli", lambda: None)
    assert adapter.snapshot()["errorCode"] == "not_installed"


def test_focus_validates_target_before_focusing(monkeypatch, adapter):
    calls = []

    def run(*args):
        calls.append(args)
        return pane(tab_id="t_moved")

    monkeypatch.setattr(adapter, "_run_cli", run)
    with pytest.raises(otty.OttyError, match="位置已变化"):
        adapter.focus({"kind": "otty", "paneId": "p_demo_1", "tabId": "t_demo_1", "windowId": "w_demo_1"})
    assert calls == [("pane", "show", "--pane", "p_demo_1")]


@pytest.mark.parametrize("bad_id", ["1", "", "--pane", "p_foo;echo hi", "p_foo\n", None])
def test_focus_never_accepts_index_shell_or_active_selector(adapter, bad_id):
    with pytest.raises(ValueError):
        adapter.focus({"kind": "otty", "paneId": bad_id, "tabId": "t_demo_1", "windowId": "w_demo_1"})


def test_cli_uses_fixed_argument_array_and_schema(monkeypatch, adapter):
    calls = []

    def process(argv):
        calls.append(argv)
        return json.dumps({"ok": True, "command": "pane list", "data": []}).encode()

    monkeypatch.setattr(otty, "_bounded_process", process)
    assert adapter._run_cli("pane", "list") == []
    assert calls[0][1:] == ["--json", "--timeout", "1200", "pane", "list"]
    with pytest.raises(ValueError):
        adapter._run_cli("pane", "send-text", "--pane", "p_demo_1")


@pytest.mark.parametrize("response", [b"not json", b"[]", b'{"ok":false,"data":[]}',
                                      b'{"ok":true,"command":"pane capture","data":[]}'])
def test_bad_envelopes_rejected(monkeypatch, adapter, response):
    monkeypatch.setattr(otty, "_bounded_process", lambda _: response)
    with pytest.raises(otty.OttyError):
        adapter._run_cli("pane", "list")


def test_process_timeout_is_bounded_and_own_child_is_reaped():
    start = time.monotonic()
    with pytest.raises(otty.OttyError) as error:
        otty._bounded_process([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.05)
    assert error.value.code == "timeout"
    assert time.monotonic() - start < 2


def test_process_output_is_bounded():
    with pytest.raises(otty.OttyError) as error:
        otty._bounded_process([sys.executable, "-c", "print('x' * 10000)"], max_output=100)
    assert error.value.code == "output_limit"


def test_removed_exit_does_not_surface_process_stderr():
    with pytest.raises(otty.OttyError) as error:
        otty._bounded_process([sys.executable, "-c", "import sys; print('private', file=sys.stderr); sys.exit(4)"])
    assert error.value.code == "removed"
    assert "private" not in str(error.value)
