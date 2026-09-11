"""One-button connection and permission semantics, using synthetic credentials only."""

from __future__ import annotations

import importlib
import sys
import threading
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QPushButton, QToolButton

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from haochen_app import permissions
from haochen_app.app_shell import AppShell
from haochen_app.key_validation import ValidationResult
from haochen_app.keychain import KeychainError, KeychainInteractionRequired, KeychainStore, MemoryCredentialStore
from haochen_app.model_connection import CONNECT, CONNECTED, CONNECTING, existing_key_for_connection
from haochen_app.onboarding import KeyPage, PermissionPage
from haochen_app.settings.config_store import ConfigStore
from haochen_app.settings.settings_window import SettingsWindow

settings = importlib.import_module("haochen_app.settings.settings_window")


class LockedBackend:
    def __init__(self, allowed=True):
        self.calls = 0
        self.allowed = allowed

    def get_without_ui(self, *_):
        raise KeychainInteractionRequired("test-only denial")

    def authorize(self, *_):
        self.calls += 1
        if not self.allowed:
            raise KeychainInteractionRequired("test-only cancellation")
        return "test-only-existing-key"


def make_store(tmp_path, backend=None):
    keychain = KeychainStore("test-only", backend=backend) if backend else MemoryCredentialStore()
    store = ConfigStore(tmp_path, keychain=keychain)
    store.ensure_initialized()
    if backend:
        store._save("auth.json", {"deepseek": {"type": "api_key", "key": "$HAOCHEN_DEEPSEEK_API_KEY"}})
    return store


def test_same_button_authorizes_then_validates_without_rewriting_old_key(qtbot, tmp_path, monkeypatch):
    backend = LockedBackend()
    store = make_store(tmp_path, backend)
    calls = []
    monkeypatch.setattr(settings, "validate_api_key", lambda p, k: calls.append((p, k)) or ValidationResult(True, "ok"))
    monkeypatch.setattr(store, "set_key", lambda *_a, **_kw: pytest.fail("existing key must not be rewritten"))
    window = SettingsWindow(store=store)
    qtbot.addWidget(window)
    edit, badge, button = window._key_widgets["deepseek"]
    assert backend.calls == 0
    assert button.text() == CONNECT and badge.objectName() != "badgeOk"
    assert not any("授权" in b.text() for b in window.findChildren(QPushButton))
    button.click()
    qtbot.waitUntil(lambda: not window._working)
    assert backend.calls == 1 and calls == [("deepseek", "test-only-existing-key")]
    assert button.text() == CONNECTED and not button.isEnabled()
    assert edit.text() == "" and badge.objectName() == "badgeOk"


def test_cancel_stops_chain_and_same_button_retries(qtbot, tmp_path, monkeypatch):
    backend = LockedBackend(False)
    store = make_store(tmp_path, backend)
    calls = []
    monkeypatch.setattr(settings, "validate_api_key", lambda *_: calls.append(True) or ValidationResult(True, "ok"))
    window = SettingsWindow(store=store)
    qtbot.addWidget(window)
    _, badge, button = window._key_widgets["deepseek"]
    button.click()
    qtbot.waitUntil(lambda: not window._working)
    assert not calls and button.text() == CONNECT and button.isEnabled()
    assert "系统授权未完成" in window._status.text() and badge.objectName() != "badgeOk"
    backend.allowed = True
    button.click()
    qtbot.waitUntil(lambda: not window._working)
    assert calls == [True] and backend.calls == 2 and button.text() == CONNECTED


def test_authorized_but_invalid_is_not_connected(qtbot, tmp_path, monkeypatch):
    store = make_store(tmp_path, LockedBackend())
    monkeypatch.setattr(settings, "validate_api_key", lambda *_: ValidationResult(False, "凭据无效"))
    window = SettingsWindow(store=store)
    qtbot.addWidget(window)
    _, badge, button = window._key_widgets["deepseek"]
    button.click()
    qtbot.waitUntil(lambda: not window._working)
    assert store.key_status("deepseek")[0]
    assert button.text() == CONNECT and badge.objectName() != "badgeOk"
    assert "凭据无效" in window._status.text()


def test_save_failure_retains_draft_and_same_retry_button(qtbot, tmp_path, monkeypatch):
    store = make_store(tmp_path)
    store.set_key("deepseek", "test-only-old-key")
    monkeypatch.setattr(settings, "validate_api_key", lambda *_: ValidationResult(True, "ok"))
    original = store.set_key
    def failed_save(*_args, **_kwargs):
        raise KeychainError("test-only-sensitive-error")
    monkeypatch.setattr(store, "set_key", failed_save)
    window = SettingsWindow(store=store)
    qtbot.addWidget(window)
    edit, badge, button = window._key_widgets["deepseek"]
    edit.setText("test-only-new-key")
    button.click()
    qtbot.waitUntil(lambda: not window._working)
    assert button.text() == CONNECT and edit.text() == "test-only-new-key"
    assert badge.objectName() != "badgeOk" and "保存未完成" in window._status.text()
    assert "test-only-sensitive-error" not in window._status.text()
    assert store.keychain.get("deepseek") == "test-only-old-key"
    monkeypatch.setattr(store, "set_key", original)
    button.click()
    qtbot.waitUntil(lambda: not window._working)
    assert button.text() == CONNECTED and store.keychain.get("deepseek") == "test-only-new-key"


def test_runtime_old_key_success_cannot_mark_new_draft_as_connected(qtbot, tmp_path):
    store = make_store(tmp_path)
    store.set_key("deepseek", "test-only-old-key")
    window = SettingsWindow(store=store)
    qtbot.addWidget(window)
    edit, badge, button = window._key_widgets["deepseek"]
    edit.setText("test-only-draft")
    window.set_runtime_key_validation("deepseek", True, "old key works")
    assert button.text() == CONNECT and button.isEnabled()
    assert badge.objectName() == "badgeOff" and "尚未连接" in badge.text()
    edit.clear()
    assert button.text() == CONNECTED and badge.objectName() == "badgeOk"
    provider_name = next(p.name for p in store.providers() if p.id == "deepseek")
    more = next(b for b in window.findChildren(QToolButton) if b.accessibleName() == f"{provider_name} 的更多操作")
    assert [a.text() for a in more.menu().actions()] == ["更换 Key", "删除 Key…"]
    assert not any(b.text() == "删除 Key" for b in window.findChildren(QPushButton))


def test_duplicate_click_during_connection_does_not_start_another_job(qtbot, tmp_path, monkeypatch):
    store = make_store(tmp_path)
    store.set_key("deepseek", "test-only-key")
    entered, release = threading.Event(), threading.Event()
    calls = []
    def verify(*_):
        calls.append(True)
        entered.set()
        release.wait(2)
        return ValidationResult(True, "ok")
    monkeypatch.setattr(settings, "validate_api_key", verify)
    window = SettingsWindow(store=store)
    qtbot.addWidget(window)
    _, _, button = window._key_widgets["deepseek"]
    button.click()
    try:
        qtbot.waitUntil(entered.is_set)
        button.click()
        assert button.text() == CONNECTING and not button.isEnabled() and calls == [True]
    finally:
        release.set()
    qtbot.waitUntil(lambda: not window._working)


@pytest.mark.parametrize("state,label,action", [
    (True, "管理权限", "manage_screen"), (False, "去开启", "screen"), (None, "重新检查", None),
])
def test_permission_button_represents_system_state(qtbot, monkeypatch, state, label, action):
    monkeypatch.setattr(permissions, "permission_status", lambda _: state)
    page = PermissionPage()
    qtbot.addWidget(page)
    requests = []
    page.permission_requested.connect(requests.append)
    page.refresh_status()
    assert requests == [] and page.isComplete()
    assert page.permission_buttons["screen"].text() == label
    page.permission_buttons["screen"].click()
    assert requests == ([action] if action else [])
    if state is True:
        assert "系统已授权，无需重复操作" in page.status.text()
    elif state is None:
        assert "暂时无法确认权限" in page.status.text()


def test_manage_permission_only_opens_settings(qtbot, tmp_path, monkeypatch):
    shell = AppShell(mock=True, home=tmp_path)
    qtbot.addWidget(shell.chat)
    opened = []
    monkeypatch.setattr(permissions, "open_permission_settings", opened.append)
    monkeypatch.setattr(permissions, "request_screen_recording", lambda: pytest.fail("already granted"))
    monkeypatch.setattr(permissions, "request_accessibility", lambda: pytest.fail("already granted"))
    shell._request_onboarding_permission("manage_screen")
    shell._request_onboarding_permission("manage_accessibility")
    assert opened == ["screen", "accessibility"]


def test_preflight_failure_is_unknown_not_granted(monkeypatch):
    import Quartz
    def failed():
        raise RuntimeError("synthetic failure")
    monkeypatch.setattr(Quartz, "CGPreflightScreenCaptureAccess", failed)
    assert permissions.permission_status("screen") is None
    assert not permissions.screen_recording_granted()
    assert permissions.permission_status("unknown") is None


def test_changed_custom_url_never_reads_or_sends_previous_key(qtbot, tmp_path, monkeypatch):
    store = make_store(tmp_path)
    provider_id, _ = store.upsert_custom_model(
        base_url="https://original.example/v1", model_id="test-model", key="test-only-old-key")
    provider = next(p for p in store.providers() if p.id == provider_id)
    window = SettingsWindow(store=store)
    qtbot.addWidget(window)
    window._edit_custom_model(provider, provider.models[0])
    window._custom_url_edit.setText("https://other.example/v1")
    monkeypatch.setattr(settings, "existing_key_for_connection", lambda *_: pytest.fail("old secret read"))
    monkeypatch.setattr(settings, "validate_custom_model", lambda *_a, **_kw: pytest.fail("network request"))
    window._custom_save_button.click()
    assert not window._working and "不能沿用" in window._status.text()
    assert store.keychain.get(provider_id) == "test-only-old-key"


def test_custom_reconnect_preserves_protocol_and_has_one_active_button(qtbot, tmp_path, monkeypatch):
    store = make_store(tmp_path)
    provider_id, _ = store.upsert_custom_model(base_url="https://original.example/v1", model_id="test-model",
                                             key="test-only-old-key", api="openai-responses")
    provider = next(p for p in store.providers() if p.id == provider_id)
    calls = []
    def validate(url, model, key, *, api):
        calls.append((url, model, key, api))
        return ValidationResult(True, "ok")
    monkeypatch.setattr(settings, "validate_custom_model", validate)
    window = SettingsWindow(store=store)
    qtbot.addWidget(window)
    window._connect_custom_model(provider, provider.models[0])
    assert window._custom_list.isHidden() and not window._custom_form.isHidden()
    qtbot.waitUntil(lambda: not window._working)
    assert calls == [(provider.base_url, "test-model", "test-only-old-key", "openai-responses")]
    assert window._custom_form.isHidden()
    assert [b.text() for b in window._custom_list.findChildren(QPushButton)] == [CONNECTED]


def test_onboarding_reconnect_existing_custom_keeps_responses_api(qtbot, tmp_path, monkeypatch):
    from haochen_app import onboarding
    store = make_store(tmp_path)
    provider_id, _ = store.upsert_custom_model(base_url="https://original.example/v1", model_id="test-model",
                                             key="test-only-old-key", api="openai-responses")
    calls = []
    def validate(url, model, key, *, api):
        calls.append(api)
        return ValidationResult(True, "ok")
    monkeypatch.setattr(onboarding, "validate_custom_model", validate)
    page = KeyPage(store, lambda *_: ValidationResult(True, "ok"), custom_verifier=validate)
    qtbot.addWidget(page)
    page.verify_button.click()
    qtbot.waitUntil(lambda: not page._working)
    assert page.verify_button.text() == CONNECTED and calls == ["openai-responses"]
    assert next(p for p in store.providers() if p.id == provider_id).api == "openai-responses"


@pytest.mark.parametrize("reference", ["$UNRELATED_EXTERNAL_KEY", "!external-command"])
def test_connection_resolver_never_executes_external_references(tmp_path, reference):
    store = make_store(tmp_path)
    store._save("auth.json", {"deepseek": {"type": "api_key", "key": reference}})
    with pytest.raises(KeychainError):
        existing_key_for_connection(store, "deepseek")
