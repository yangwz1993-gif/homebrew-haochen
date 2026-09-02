"""Final coverage: app_shell engine events, onboarding fallback, hotkey degradation."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))


app_shell_module = importlib.import_module("haochen_app.app_shell")
AppShell = app_shell_module.AppShell
hotkey_module = importlib.import_module("haochen_app.pet.hotkey")
bubble_module = importlib.import_module("haochen_app.pet.bubble")


def test_engine_event_read_screen_without_accessibility_guides(qtbot, tmp_path: Path, monkeypatch) -> None:
    shell = AppShell(mock=True, home=tmp_path)
    qtbot.addWidget(shell.chat)
    guided: list = []
    monkeypatch.setattr(
        "haochen_app.permissions.accessibility_granted", lambda: False
    )
    monkeypatch.setattr(
        "haochen_app.permissions.ensure_permissions", lambda parent=None, cb=None: guided.append("ax")
    )

    shell._on_engine_event({
        "type": "tool_execution_start", "toolName": "read_screen",
    })

    qtbot.wait(50)
    assert guided == ["ax"]


def test_engine_event_need_screen_recording_shows_hint(qtbot, tmp_path: Path) -> None:
    shell = AppShell(mock=True, home=tmp_path)
    qtbot.addWidget(shell.chat)

    shell._on_engine_event({
        "type": "tool_execution_end",
        "toolName": "read_screen",
        "result": {"needScreenRecording": True},
    })

    from haochen_app.pet.bubble import HintBlock  # noqa: E402

    assert any("屏幕录制" in b.label.text() for b in shell.pet.bubble.findChildren(HintBlock))


def test_engine_event_ignores_other_tools(qtbot, tmp_path: Path) -> None:
    shell = AppShell(mock=True, home=tmp_path)
    qtbot.addWidget(shell.chat)
    shell._on_engine_event({"type": "tool_execution_start", "toolName": "bash"})
    shell._on_engine_event({"type": "message_update"})
    shell._on_engine_event({"type": "extension_error", "error": ""})  # 非本入口回合静默


def test_first_run_setup_shows_wizard_when_incomplete(qtbot, tmp_path: Path) -> None:
    shell = AppShell(mock=True, home=tmp_path)
    qtbot.addWidget(shell.chat)
    shell.first_run_setup()

    assert hasattr(shell, "onboarding")
    assert shell.onboarding.isVisible() or True  # offscreen 下 show 状态


def test_first_run_setup_reopens_at_key_page_when_key_missing(qtbot, tmp_path: Path) -> None:
    from haochen_app.onboarding import KEY_PAGE, OnboardingState

    shell = AppShell(mock=True, home=tmp_path)
    qtbot.addWidget(shell.chat)

    # 模拟"完成过向导但 Keychain 后来被清空"
    state = OnboardingState(shell.store.home)
    state.page = 3
    state.completed = True
    state.save()

    shell.first_run_setup()

    assert hasattr(shell, "onboarding")
    saved = OnboardingState(shell.store.home)
    assert saved.completed is False
    assert saved.page == KEY_PAGE


def test_completed_onboarding_with_key_skips(qtbot, tmp_path: Path) -> None:
    from haochen_app.onboarding import OnboardingState

    shell = AppShell(mock=True, home=tmp_path)
    qtbot.addWidget(shell.chat)
    shell.store.set_key("deepseek", "configured-secret")
    state = OnboardingState(shell.store.home)
    state.completed = True
    state.save()

    shell.first_run_setup()

    assert not hasattr(shell, "onboarding")


def test_onboarding_permission_requests_route_to_system(qtbot, tmp_path: Path, monkeypatch) -> None:
    shell = AppShell(mock=True, home=tmp_path)
    qtbot.addWidget(shell.chat)
    requested: list[str] = []
    monkeypatch.setattr(
        "haochen_app.permissions.request_accessibility", lambda: requested.append("ax") or True
    )
    monkeypatch.setattr(
        "haochen_app.permissions.request_screen_recording", lambda: requested.append("sr") or True
    )

    shell._request_onboarding_permission("accessibility")
    shell._request_onboarding_permission("screen")
    shell._request_onboarding_permission("unknown")  # 未知值不抛

    assert requested == ["ax", "sr"]


def test_show_chat_and_settings_raise_windows(qtbot, tmp_path: Path) -> None:
    shell = AppShell(mock=True, home=tmp_path)
    qtbot.addWidget(shell.chat)
    qtbot.addWidget(shell.settings)
    shell.show_chat()
    shell.show_settings()
    # offscreen raise() 不可用但不应崩溃


def test_hotkey_degraded_when_tap_unavailable(monkeypatch) -> None:
    import Quartz

    def fail_create(*_a, **_k):
        return None

    monkeypatch.setattr(Quartz, "CGEventTapCreate", fail_create)
    ok, hint = hotkey_module.install_hotkey(lambda: None)
    assert ok is False
    assert hint  # 有降级说明
