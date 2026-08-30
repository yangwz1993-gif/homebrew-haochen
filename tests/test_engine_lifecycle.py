from __future__ import annotations

import importlib
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
engine_module = importlib.import_module("haochen_app.engine_client")
EngineClient = engine_module.EngineClient
EngineSupervisor = importlib.import_module("haochen_app.supervisor").EngineSupervisor


def fake_engine(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "fake_engine.py"
    script.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    script.chmod(0o700)
    return script


def wait_for_exit(proc, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert proc.poll() is not None


def test_stop_returns_immediately_then_kills_sigterm_ignoring_engine(tmp_path: Path) -> None:
    script = fake_engine(
        tmp_path,
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "while True: time.sleep(0.1)\n",
    )
    client = EngineClient(engine=script, mock=True, home=tmp_path / "home", shutdown_timeout_s=0.1)
    client.start()
    proc = client._proc
    assert proc is not None

    started = time.monotonic()
    client.stop()
    elapsed = time.monotonic() - started

    assert elapsed < 0.1
    wait_for_exit(proc)
    assert client.wait_stopped(timeout=1)


def test_stop_kills_engine_descendants_in_same_process_group(tmp_path: Path) -> None:
    child_pid_file = tmp_path / "child.pid"
    child_code = "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)"
    script = fake_engine(
        tmp_path,
        "import subprocess, sys\n"
        f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        f"open({str(child_pid_file)!r}, 'w').write(str(child.pid))\n"
        "for _line in sys.stdin: pass\n",
    )
    client = EngineClient(engine=script, mock=True, home=tmp_path / "home", shutdown_timeout_s=0.1)
    client.start()
    deadline = time.monotonic() + 2
    while not child_pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    child_pid = int(child_pid_file.read_text())

    client.stop()
    assert client.wait_stopped(timeout=3)

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.01)
    else:
        raise AssertionError(f"engine descendant {child_pid} survived shutdown")


def test_pending_request_times_out_and_is_settled(qtbot, tmp_path: Path) -> None:
    script = fake_engine(tmp_path, "import sys, time\nfor _line in sys.stdin: time.sleep(1)\n")
    client = EngineClient(engine=script, mock=True, home=tmp_path / "home", request_timeout_s=0.05)
    responses: list[dict] = []
    client.response.connect(responses.append)
    client.start()
    request_id = client.get_state()

    qtbot.waitUntil(lambda: any(item.get("id") == request_id for item in responses), timeout=1000)
    response = next(item for item in responses if item.get("id") == request_id)
    assert response["success"] is False
    assert response["errorCode"] == "timeout"
    assert client.pending_request_count == 0
    client.stop()
    assert client.wait_stopped(timeout=2)


def test_crash_settles_pending_request(qtbot, tmp_path: Path) -> None:
    script = fake_engine(tmp_path, "import sys\nsys.stdin.readline()\nraise SystemExit(7)\n")
    client = EngineClient(engine=script, mock=True, home=tmp_path / "home", request_timeout_s=5)
    responses: list[dict] = []
    client.response.connect(responses.append)
    client.start()
    request_id = client.get_state()

    qtbot.waitUntil(lambda: any(item.get("id") == request_id for item in responses), timeout=2000)
    response = next(item for item in responses if item.get("id") == request_id)
    assert response["success"] is False
    assert response["errorCode"] == "engine_exited"
    assert client.pending_request_count == 0


def test_supervisor_restarts_engine_after_health_probe_timeout(qtbot, tmp_path: Path) -> None:
    script = fake_engine(tmp_path, "import sys, time\nfor _line in sys.stdin: time.sleep(1)\n")
    supervisor = EngineSupervisor(
        engine=script,
        mock=True,
        home=tmp_path / "home",
        request_timeout_s=0.05,
    )
    crashes: list[int] = []
    attempts: list[int] = []
    supervisor.crashed.connect(crashes.append)
    supervisor.restarting.connect(attempts.append)

    supervisor.start()
    qtbot.waitUntil(lambda: bool(attempts), timeout=1500)

    assert crashes == [-1]
    assert attempts == [1]
    supervisor.stop()
    assert supervisor.client.wait_stopped(timeout=2)


def test_malformed_and_partial_json_are_reported(qtbot, tmp_path: Path) -> None:
    script = fake_engine(
        tmp_path,
        "import sys\n"
        "sys.stdout.write('not-json\\n')\n"
        "sys.stdout.write('{\\\"partial\\\":')\n"
        "sys.stdout.flush()\n",
    )
    client = EngineClient(engine=script, mock=True, home=tmp_path / "home")
    errors: list[str] = []
    client.protocol_error.connect(errors.append)
    client.start()

    qtbot.waitUntil(lambda: len(errors) >= 2, timeout=2000)
    assert all("not-json" not in error and "partial" not in error for error in errors)


def test_stderr_diagnostic_is_redacted(qtbot, tmp_path: Path) -> None:
    secret = "sensitive-value-must-not-appear"
    script = fake_engine(
        tmp_path,
        f"import sys\nsys.stderr.write('request text {secret}\\n')\nsys.stderr.flush()\n",
    )
    home = tmp_path / "home"
    client = EngineClient(engine=script, mock=True, home=home)
    diagnostics: list[str] = []
    client.diagnostic.connect(diagnostics.append)
    client.start()

    qtbot.waitUntil(lambda: bool(diagnostics), timeout=2000)
    log_path = home / "logs" / "engine.log"
    qtbot.waitUntil(log_path.exists, timeout=1000)
    log_text = log_path.read_text(encoding="utf-8")
    assert secret not in log_text
    assert secret not in "".join(diagnostics)
    assert "sha256=" in log_text
    assert log_path.stat().st_mode & 0o077 == 0
