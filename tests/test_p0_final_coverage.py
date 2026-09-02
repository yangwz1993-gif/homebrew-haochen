"""Final edge-path coverage for engine_client and app_tracking (P0 ≥90%)."""

from __future__ import annotations

import importlib
import os
import signal
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

engine_module = importlib.import_module("haochen_app.engine_client")
tracking = importlib.import_module("haochen_app.app_tracking")
EngineClient = engine_module.EngineClient


def wait_until(predicate, timeout: float = 3.0, what: str = "") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(f"timeout: {what}")


def fake_engine_script(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "engine.py"
    script.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    script.chmod(0o700)
    return script


def test_group_exists_permission_and_alive_branches(monkeypatch) -> None:
    calls: list[tuple[int, int]] = []

    def killpg(group_id: int, sig: int) -> None:
        calls.append((group_id, sig))

    monkeypatch.setattr(engine_module.os, "killpg", killpg)
    assert EngineClient._group_exists(4242) is True  # 无异常 → 存活
    assert calls == [(4242, 0)]

    def raise_not_found(group_id, sig):
        raise ProcessLookupError()

    monkeypatch.setattr(engine_module.os, "killpg", raise_not_found)
    assert EngineClient._group_exists(4242) is False

    def raise_permission(group_id, sig):
        raise PermissionError()

    monkeypatch.setattr(engine_module.os, "killpg", raise_permission)
    assert EngineClient._group_exists(4242) is True


def test_signal_group_falls_back_to_send_signal(monkeypatch) -> None:
    sent: list[int] = []

    def killpg(group_id, sig):
        raise OSError("no such group")

    proc = SimpleNamespace(
        pid=1,
        send_signal=lambda sig: sent.append(sig),
    )
    monkeypatch.setattr(engine_module.os, "killpg", killpg)
    EngineClient._signal_group(proc, signal.SIGTERM)  # type: ignore[arg-type]
    assert sent == [signal.SIGTERM]

    def killpg2(group_id, sig):
        raise OSError("again")

    monkeypatch.setattr(engine_module.os, "killpg", killpg2)
    proc2 = SimpleNamespace(pid=1, send_signal=lambda sig: (_ for _ in ()).throw(OSError("gone")))
    EngineClient._signal_group(proc2, signal.SIGKILL)  # type: ignore[arg-type]  # 双失败静默


def test_shutdown_closes_streams_and_joins_threads(qtbot, tmp_path: Path) -> None:
    script = fake_engine_script(
        tmp_path,
        "import signal, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "while True: time.sleep(0.1)\n",
    )
    client = EngineClient(engine=script, mock=True, home=tmp_path / "home", shutdown_timeout_s=0.05)
    client.start()
    wait_until(lambda: client.alive)
    client.stop()
    assert client.wait_stopped(timeout=3)
    proc_pid = client._proc  # None after stop
    assert proc_pid is None


def test_read_last_user_app_invalid_content_returns_zero(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "last-user-app.txt").write_text("not-a-number", encoding="utf-8")
    assert tracking.read_last_user_app(home) == 0


def test_read_last_user_app_missing_returns_zero(tmp_path: Path) -> None:
    assert tracking.read_last_user_app(tmp_path / "nope") == 0


def test_write_last_user_app_skips_self_and_none(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()

    appkit = SimpleNamespace(
        NSWorkspace=SimpleNamespace(
            sharedWorkspace=lambda: SimpleNamespace(frontmostApplication=lambda: None)
        )
    )
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    tracking.write_last_user_app(home)  # frontmost None → 静默返回
    assert not (home / "last-user-app.txt").exists()

    class FakeApp:
        processIdentifier = lambda self: os.getpid()  # noqa: E731 - 自身 pid

    appkit.NSWorkspace.sharedWorkspace = lambda: SimpleNamespace(
        frontmostApplication=lambda: FakeApp()
    )
    tracking.write_last_user_app(home)  # 自身 → 跳过
    assert not (home / "last-user-app.txt").exists()


def test_install_tracker_uses_notification_center(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    observers: list[str] = []

    class FakeNotificationCenter:
        def addObserverForName_object_queue_usingBlock_(self, name, obj, queue, block):
            observers.append(str(name))

    class FakeWorkspace:
        @staticmethod
        def sharedWorkspace():
            return SimpleNamespace(
                frontmostApplication=lambda: None,
                notificationCenter=lambda: FakeNotificationCenter(),
            )

    appkit = SimpleNamespace(NSWorkspace=FakeWorkspace, NSWorkspaceDidActivateApplicationNotification="did-activate")
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    tracking.install_tracker(home)
    assert observers == ["did-activate"]
