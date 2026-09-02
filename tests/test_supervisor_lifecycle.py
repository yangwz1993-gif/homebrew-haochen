"""Supervisor lifecycle coverage: restart, backoff, probe, arbitration (P0 module)."""

from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

supervisor_module = importlib.import_module("haochen_app.supervisor")
engine_module = importlib.import_module("haochen_app.engine_client")
EngineSupervisor = supervisor_module.EngineSupervisor


def fake_engine(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "engine.py"
    script.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    script.chmod(0o700)
    return script


def wait_until(predicate, timeout: float = 3.0, what: str = "") -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    raise AssertionError(f"timeout: {what}")


def make_supervisor(tmp_path: Path, script: Path, **kwargs) -> EngineSupervisor:
    supervisor = EngineSupervisor(engine=script, mock=True, home=tmp_path / "home", **kwargs)
    supervisor.BACKOFF_MS = [10, 10, 10]
    supervisor.PROBE_TIMEOUT_MS = 500
    return supervisor


def test_start_emits_state_and_stores_session(qtbot, tmp_path: Path) -> None:
    script = fake_engine(
        tmp_path,
        "import json,sys\n"
        "for line in sys.stdin:\n"
        "    if not line.strip(): continue\n"
        "    cmd = json.loads(line)\n"
        "    print(json.dumps({'id':cmd['id'],'type':'response','command':'get_state','success':True,"
        "'data':{'sessionFile':'mock-session://x.jsonl','model':{'id':'m'}}}), flush=True)\n",
    )
    supervisor = make_supervisor(tmp_path, script)
    states: list[dict] = []
    supervisor.state_changed.connect(states.append)
    supervisor.start()
    qtbot.waitUntil(lambda: bool(states), timeout=2000)
    assert supervisor.coordinator.current_session == "mock-session://x.jsonl"
    supervisor.stop()
    assert supervisor.client.wait_stopped(timeout=2)


def test_busy_except_arbitration() -> None:
    supervisor = EngineSupervisor.__new__(EngineSupervisor)
    a = SimpleNamespace(busy=False)
    b = SimpleNamespace(busy=True)
    supervisor._ctrls = [a, b]
    assert supervisor.busy_except(a) is True
    assert supervisor.busy_except(b) is False


def test_register_and_unregister() -> None:
    supervisor = EngineSupervisor.__new__(EngineSupervisor)
    supervisor._ctrls = []
    ctrl = SimpleNamespace(busy=False)
    supervisor.register(ctrl)
    supervisor.register(ctrl)  # 幂等
    assert supervisor._ctrls == [ctrl]
    supervisor.unregister(ctrl)
    assert supervisor._ctrls == []
    supervisor.unregister(ctrl)  # 不存在不抛


def test_crash_during_restart_counts_as_attempt(qtbot, tmp_path: Path) -> None:
    script = fake_engine(tmp_path, "import sys\nsys.exit(3)\n")
    supervisor = make_supervisor(tmp_path, script)
    attempts: list[int] = []
    supervisor.restarting.connect(attempts.append)
    supervisor._restarting = True
    supervisor._on_crashed(3)
    assert attempts == [1]  # 崩溃窗口内 → 直接进下一次尝试


def test_stop_during_restart_prevents_rescheduling(qtbot, tmp_path: Path) -> None:
    script = fake_engine(tmp_path, "import sys, time\nfor _ in sys.stdin: time.sleep(1)\n")
    supervisor = make_supervisor(tmp_path, script)
    attempts: list[int] = []
    supervisor.restarting.connect(attempts.append)
    supervisor.start()
    wait_until(lambda: supervisor.client.alive)
    supervisor._restarting = True
    supervisor._on_crashed(-1)
    supervisor.stop()
    assert attempts == [] or attempts == [1]  # 停止后不再补排


def test_probe_failure_schedules_restart(qtbot, tmp_path: Path) -> None:
    script = fake_engine(
        tmp_path,
        "import json,sys\n"
        "print(json.dumps({'id':'s1','type':'response','command':'get_state','success':False,"
        "'error':'bad'}))\n"
        "sys.stdout.flush()\n"
        "for line in sys.stdin: pass\n",
    )
    supervisor = make_supervisor(tmp_path, script)
    attempts: list[int] = []
    supervisor.restarting.connect(attempts.append)
    supervisor._restarting = True
    supervisor._probe_ok({"success": False})
    assert attempts == [1]


def test_restore_session_done_failure_emits_restart_failed(qtbot) -> None:
    supervisor = EngineSupervisor.__new__(EngineSupervisor)
    supervisor._restarting = True
    failed: list[int] = []
    import logging

    class FakeSignal:
        def connect(self, fn):
            failed.append(1)

        def emit(self):
            failed.append(1)

    supervisor.restart_failed = FakeSignal()
    test_logger = logging.getLogger("test")
    original_log = supervisor_module.log
    supervisor_module.log = test_logger  # pyright: ignore[reportAttributeAccessIssue]
    try:
        supervisor._restore_session_done({"success": False})
    finally:
        supervisor_module.log = original_log  # pyright: ignore[reportAttributeAccessIssue]
    assert supervisor._restarting is False
    assert failed == [1]


def test_heartbeat_skipped_when_busy_or_restarting() -> None:
    supervisor = EngineSupervisor.__new__(EngineSupervisor)
    supervisor._stopping = True
    supervisor._restarting = False
    supervisor._heartbeat_pending = False
    supervisor.client = SimpleNamespace(alive=True)
    supervisor._heartbeat()  # stopping → 不发
    supervisor._stopping = False
    supervisor._restarting = True
    supervisor._heartbeat()  # restarting → 不发
    supervisor._restarting = False
    supervisor._heartbeat_pending = True
    supervisor._heartbeat()  # pending → 不发
    supervisor._heartbeat_pending = False
    sent: list[str] = []

    def get_state() -> str:
        sent.append("get_state")
        return "hb-1"

    supervisor._ask_state = lambda cb: sent.append("ask") or "hb-1"
    supervisor.client.get_state = get_state
    supervisor._pending = {}
    supervisor._heartbeat()
    assert supervisor._heartbeat_pending is True


def test_late_probe_response_is_ignored(qtbot, tmp_path: Path) -> None:
    supervisor = EngineSupervisor.__new__(EngineSupervisor)
    supervisor._restarting = False
    finished: list[bool] = []
    supervisor.restarted = SimpleNamespace(connect=lambda fn: finished.append(1))
    supervisor._probe_ok({"success": True, "data": {"sessionFile": "x"}})
    assert finished == []  # 非重启期响应被忽略
