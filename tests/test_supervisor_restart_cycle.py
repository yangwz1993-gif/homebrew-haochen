"""Supervisor restart-cycle coverage via real processes (P0 module ≥90%)."""

from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

supervisor_module = importlib.import_module("haochen_app.supervisor")
EngineSupervisor = supervisor_module.EngineSupervisor


def fake_engine(tmp_path: Path, body: str, name: str = "engine.py") -> Path:
    script = tmp_path / name
    script.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    script.chmod(0o700)
    return script


RESPONDING_ENGINE = (
    "import json,sys\n"
    "for line in sys.stdin:\n"
    "    if not line.strip(): continue\n"
    "    cmd = json.loads(line)\n"
    "    out = {'id':cmd['id'],'type':'response','command':cmd.get('type'),'success':True,'data':{}}\n"
    "    if cmd.get('type') == 'get_state':\n"
    "        out['data'] = {'sessionFile':'mock-session://r.jsonl','model':{'id':'m'}}\n"
    "    print(json.dumps(out), flush=True)\n"
)


def wait_until(predicate, timeout: float = 3.0, what: str = "") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(f"timeout: {what}")


def make_supervisor(tmp_path: Path, script: Path, **kwargs) -> EngineSupervisor:
    supervisor = EngineSupervisor(engine=script, mock=True, home=tmp_path / "home", **kwargs)
    supervisor.BACKOFF_MS = [10, 10, 10]
    supervisor.PROBE_TIMEOUT_MS = 400
    return supervisor


def test_start_failure_exhausts_attempts_and_emits_restart_failed(qtbot, tmp_path: Path) -> None:
    supervisor = make_supervisor(tmp_path, Path("/nonexistent-engine"))
    supervisor.MAX_ATTEMPTS = 2
    failed: list[bool] = []
    attempts: list[int] = []
    supervisor.restart_failed.connect(lambda: failed.append(True))
    supervisor.restarting.connect(attempts.append)

    supervisor.start()
    qtbot.waitUntil(lambda: len(failed) >= 1, timeout=2000)

    assert failed == [True]
    assert attempts == [1, 2]
    assert supervisor._restarting is False


def test_restart_now_recovers_with_responding_engine(qtbot, tmp_path: Path) -> None:
    script = fake_engine(tmp_path, RESPONDING_ENGINE)
    supervisor = make_supervisor(tmp_path, script)
    supervisor.start()
    wait_until(lambda: supervisor.client.alive)
    restarted: list[bool] = []
    supervisor.restarted.connect(lambda: restarted.append(True))

    supervisor.restart_now()
    qtbot.waitUntil(lambda: len(restarted) >= 1, timeout=3000)
    assert supervisor._restarting is False
    assert supervisor.coordinator.current_session == "mock-session://r.jsonl"
    supervisor.stop()
    assert supervisor.client.wait_stopped(timeout=2)


def test_crash_of_healthy_engine_triggers_visible_restart(qtbot, tmp_path: Path) -> None:
    script = fake_engine(tmp_path, RESPONDING_ENGINE)
    supervisor = make_supervisor(tmp_path, script)
    supervisor.start()
    wait_until(lambda: supervisor.client.alive)
    crashes: list[int] = []
    restarted: list[bool] = []
    supervisor.crashed.connect(crashes.append)
    supervisor.restarted.connect(lambda: restarted.append(True))

    supervisor.client._proc.kill()
    qtbot.waitUntil(lambda: len(restarted) >= 1, timeout=3000)

    assert crashes == [-9]
    assert supervisor.client.alive
    supervisor.stop()
    assert supervisor.client.wait_stopped(timeout=2)


def test_probe_timeout_kills_and_schedules_next_attempt(qtbot, tmp_path: Path) -> None:
    # 探活无响应（不回答 get_state）→ probe 超时 → 第二次尝试
    script = fake_engine(tmp_path, "import sys\nfor line in sys.stdin: pass\n")
    supervisor = make_supervisor(tmp_path, script)
    attempts: list[int] = []
    supervisor.restarting.connect(attempts.append)

    supervisor._restarting = True
    supervisor._attempt = 0
    supervisor._try_start()
    qtbot.waitUntil(lambda: len(attempts) >= 2, timeout=2000)
    assert attempts == [1, 2]
    supervisor.stop()
    supervisor.client.wait_stopped(timeout=2)


def test_try_start_aborts_when_stopping(qtbot, tmp_path: Path) -> None:
    script = fake_engine(tmp_path, RESPONDING_ENGINE)
    supervisor = make_supervisor(tmp_path, script)
    supervisor._restarting = True
    supervisor._stopping = True
    supervisor._try_start()  # stopping → 直接返回，不启动
    assert supervisor.client._proc is None


def test_probe_ok_without_restore_finishes_restart(qtbot, tmp_path: Path) -> None:
    script = fake_engine(tmp_path, RESPONDING_ENGINE)
    supervisor = make_supervisor(tmp_path, script)
    supervisor.coordinator.set_current_session("mock-session://r.jsonl")
    restarted: list[bool] = []
    supervisor.restarted.connect(lambda: restarted.append(True))
    supervisor._restarting = True
    supervisor._started_once = True

    supervisor._probe_ok(
        {"success": True, "data": {"sessionFile": "mock-session://r.jsonl"}}
    )
    qtbot.waitUntil(lambda: bool(restarted), timeout=1000)
    assert supervisor._restarting is False


def test_initial_state_timeout_triggers_unresponsive_restart(qtbot, tmp_path: Path) -> None:
    script = fake_engine(tmp_path, "import sys\nfor line in sys.stdin: pass\n")
    supervisor = make_supervisor(tmp_path, script, request_timeout_s=0.05)
    crashes: list[int] = []
    supervisor.crashed.connect(crashes.append)

    supervisor._initial_state({"success": False, "errorCode": "timeout"})
    assert crashes == [-1]
    assert supervisor._restarting is True
    supervisor.stop()
    supervisor.client.wait_stopped(timeout=2)


def test_initial_state_restores_durable_session_before_overwriting_pointer(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    script = fake_engine(tmp_path, RESPONDING_ENGINE)
    supervisor = make_supervisor(tmp_path, script)
    previous = "mock-session://previous.jsonl"
    supervisor.coordinator.set_current_session(previous)
    switched: list[str] = []

    def switch_session(path: str) -> str:
        switched.append(path)
        return "initial-switch"

    monkeypatch.setattr(supervisor.client, "switch_session", switch_session)
    supervisor._initial_state({
        "success": True,
        "data": {"sessionFile": "mock-session://fresh.jsonl"},
    })

    assert switched == [previous]
    assert supervisor.coordinator.current_session == previous


def test_heartbeat_result_paths(qtbot, tmp_path: Path) -> None:
    script = fake_engine(tmp_path, RESPONDING_ENGINE)
    supervisor = make_supervisor(tmp_path, script)
    states: list[dict] = []
    supervisor.state_changed.connect(states.append)

    supervisor._heartbeat_pending = True
    supervisor._heartbeat_result({"success": True, "data": {"sessionFile": "mock-session://h.jsonl"}})
    assert supervisor._heartbeat_pending is False
    assert states and states[0]["sessionFile"] == "mock-session://h.jsonl"

    crashes: list[int] = []
    supervisor.crashed.connect(crashes.append)
    supervisor._heartbeat_pending = True
    supervisor._heartbeat_result({"success": False, "errorCode": "timeout"})
    assert crashes == [-1]
    supervisor.stop()
    supervisor.client.wait_stopped(timeout=2)


def test_running_property_and_rpc_routing(qtbot, tmp_path: Path) -> None:
    script = fake_engine(tmp_path, RESPONDING_ENGINE)
    supervisor = make_supervisor(tmp_path, script)
    supervisor.start()
    wait_until(lambda: supervisor.client.alive)
    assert supervisor.running is True

    supervisor._ask_state(lambda r: results.append(r))
    results: list[dict] = []
    qtbot.waitUntil(lambda: bool(results), timeout=2000)
    supervisor.stop()
    assert supervisor.client.wait_stopped(timeout=2)


def test_on_event_agent_end_refreshes_state(qtbot, tmp_path: Path) -> None:
    script = fake_engine(tmp_path, RESPONDING_ENGINE)
    supervisor = make_supervisor(tmp_path, script)
    supervisor.start()
    wait_until(lambda: supervisor.client.alive)
    sent: list[str] = []
    original = supervisor.client.get_state
    supervisor.client.get_state = lambda: sent.append("state") or original()
    supervisor._on_event({"type": "agent_end"})
    assert sent == ["state"]
    supervisor.stop()
    supervisor.client.wait_stopped(timeout=2)
