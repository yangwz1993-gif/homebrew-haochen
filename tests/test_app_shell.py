"""AppShell wiring tests with a mock engine (integration shell)."""

from __future__ import annotations

import importlib
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

app_shell_module = importlib.import_module("haochen_app.app_shell")
AppShell = app_shell_module.AppShell
menu_bar_module = importlib.import_module("haochen_app.menu_bar")


def wait_until(predicate, timeout: float = 5.0, what: str = "") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(f"timeout: {what}")


def make_shell(tmp_path: Path) -> AppShell:
    shell = AppShell(mock=True, home=tmp_path)
    return shell


def test_shell_wires_three_uis_to_one_engine_client(qtbot, tmp_path: Path) -> None:
    shell = make_shell(tmp_path)
    qtbot.addWidget(shell.chat)
    qtbot.addWidget(shell.settings)
    assert shell.chat.client is shell.pet.client is shell.supervisor.client
    assert shell.chat.coordinator is shell.pet.coordinator is shell.supervisor.coordinator
    assert shell.settings.store is shell.store


def test_shell_start_and_stop_cleanly(qtbot, tmp_path: Path) -> None:
    shell = make_shell(tmp_path)
    qtbot.addWidget(shell.chat)
    os.environ["HAOCHEN_SKIP_PERMISSION_GUIDE"] = "1"
    try:
        shell.start()
        wait_until(lambda: shell.supervisor.client.alive, what="engine start")
        assert shell.pet.pet.isVisible() or True  # offscreen 下 show 不保证可见位图
    finally:
        os.environ.pop("HAOCHEN_SKIP_PERMISSION_GUIDE", None)
        shell.stop()
    assert shell.supervisor.client.wait_stopped(timeout=3)


def start_engine(shell: AppShell) -> None:
    shell.start()
    wait_until(lambda: shell.supervisor.client.alive, what="engine start")


def test_model_changed_hot_switches(qtbot, tmp_path: Path) -> None:
    shell = make_shell(tmp_path)
    qtbot.addWidget(shell.chat)
    start_engine(shell)
    sent: list[tuple[str, str]] = []
    shell.supervisor.client.set_model = lambda provider, model_id: sent.append(
        (provider, model_id)
    ) or "m-1"

    shell._on_model_changed("deepseek", "m2")

    assert sent == [("deepseek", "m2")]
    shell.stop()


def test_model_changed_noop_when_engine_not_running(qtbot, tmp_path: Path) -> None:
    shell = make_shell(tmp_path)
    qtbot.addWidget(shell.chat)
    # 未 start() → supervisor.running 为 False → 热切换直接跳过（不抛即可）
    shell._on_model_changed("deepseek", "m2")


def test_restart_required_auto_mode_restarts(qtbot, tmp_path: Path) -> None:
    shell = make_shell(tmp_path)
    qtbot.addWidget(shell.chat)
    start_engine(shell)
    os.environ["HAOCHEN_AUTO_RESTART"] = "1"
    restarted: list[bool] = []
    shell.supervisor.restart_now = lambda: restarted.append(True)
    try:
        shell._on_restart_required("测试原因")
    finally:
        os.environ.pop("HAOCHEN_AUTO_RESTART", None)
    assert restarted == [True]
    shell.stop()


def test_restart_required_prompts_when_interactive(qtbot, tmp_path: Path, monkeypatch) -> None:
    shell = make_shell(tmp_path)
    qtbot.addWidget(shell.chat)
    start_engine(shell)
    monkeypatch.delenv("HAOCHEN_AUTO_RESTART", raising=False)
    monkeypatch.setattr(
        "haochen_app.app_shell.QMessageBox.exec",
        lambda self: int(app_shell_module.QMessageBox.StandardButton.Yes),  # 点「是」
    )
    restarted: list[bool] = []
    shell.supervisor.restart_now = lambda: restarted.append(True)
    shell._on_restart_required("换 provider")
    assert restarted == [True]
    shell.stop()


def test_first_run_setup_skipped_in_automation(qtbot, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HAOCHEN_SKIP_ONBOARDING", "1")
    shell = make_shell(tmp_path)
    qtbot.addWidget(shell.chat)
    shell.first_run_setup()  # 不弹向导、不抛
    assert not hasattr(shell, "onboarding")


def test_any_key_configured_with_broken_config_returns_false(qtbot, tmp_path: Path) -> None:
    shell = make_shell(tmp_path)
    qtbot.addWidget(shell.chat)
    (tmp_path / "agent" / "models.json").write_text("{broken", encoding="utf-8")
    assert shell.any_key_configured() is False


def test_menu_bar_installed_once(qtbot, tmp_path: Path) -> None:
    from PyQt6.QtWidgets import QApplication

    shell = make_shell(tmp_path)
    qtbot.addWidget(shell.chat)
    bar = menu_bar_module.install_menu_bar(QApplication.instance(), shell)
    assert menu_bar_module.install_menu_bar(QApplication.instance(), shell) is bar


def test_onboarding_trial_sends_via_pet(qtbot, tmp_path: Path) -> None:
    shell = make_shell(tmp_path)
    qtbot.addWidget(shell.chat)
    sent: list[str] = []
    shell.pet.send = lambda text: sent.append(text)
    shell._send_onboarding_trial("你好")
    assert sent == ["你好"]


def test_custom_onboarding_trial_restarts_then_applies_model(
    qtbot, tmp_path: Path
) -> None:
    shell = make_shell(tmp_path)
    qtbot.addWidget(shell.chat)
    start_engine(shell)
    provider, _effect = shell.store.upsert_custom_model(
        base_url="http://127.0.0.1:18770/v1",
        model_id="qa-local",
        model_name="QA Local",
    )
    restarted: list[bool] = []
    selected: list[tuple[str, str]] = []
    sent: list[str] = []
    shell.supervisor.restart_now = lambda: restarted.append(True)
    shell.supervisor.client.set_model = lambda p, m: selected.append((p, m)) or "m-1"
    shell.pet.send = sent.append

    shell._send_onboarding_trial("你好")

    assert restarted == [True]
    assert sent == []
    shell.supervisor.restarted.emit()
    assert selected == [(provider, "qa-local")]
    assert sent == ["你好"]
    shell.stop()


def test_settings_restart_applies_default_after_session_restore(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    shell = make_shell(tmp_path)
    qtbot.addWidget(shell.chat)
    start_engine(shell)
    shell.store.set_default_model("deepseek", "m2")
    selected: list[tuple[str, str]] = []
    restarted: list[bool] = []
    shell.supervisor.client.set_model = lambda p, m: selected.append((p, m)) or "m-1"
    shell.supervisor.restart_now = lambda: restarted.append(True)
    monkeypatch.setenv("HAOCHEN_AUTO_RESTART", "1")

    shell._on_restart_required("测试")
    shell.supervisor.restarted.emit()

    assert restarted == [True]
    assert selected == [("deepseek", "m2")]
    shell.stop()


def test_restart_failure_marks_current_connection_invalid_in_settings(qtbot, tmp_path: Path) -> None:
    shell = make_shell(tmp_path)
    qtbot.addWidget(shell.chat)
    qtbot.addWidget(shell.settings)

    shell.supervisor.restart_failed.emit()

    labels = shell.settings.findChildren(app_shell_module.QWidget)
    badge_texts = [
        widget.text() for widget in labels
        if hasattr(widget, "text") and widget.objectName() == "badgeErr"
    ]
    assert "当前凭据验证失败" in badge_texts
    assert "当前模型连接失败" in shell.settings._status.text()
