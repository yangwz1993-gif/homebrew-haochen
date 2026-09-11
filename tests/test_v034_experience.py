"""User-operation regressions for the first-phase 0.3.4 acceptance candidate."""

from __future__ import annotations

import importlib
import json
import sys
import threading
import urllib.error
from pathlib import Path

from PyQt6.QtCore import QCoreApplication, QEvent, QRect, QSize, Qt, QTimer
from PyQt6.QtWidgets import QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import test_session_deletion_ui as harness
from haochen_app import a11y, key_validation
from haochen_app.app_shell import AppShell
from haochen_app.conversation import plain_visible_text
from haochen_app.keychain import KeychainInteractionRequired, KeychainStore, MemoryCredentialStore
from haochen_app.onboarding import KeyPage, OnboardingWizard
from haochen_app.pet.bubble import BubbleWindow, _action_icon
from haochen_app.settings.config_store import ConfigStore
from haochen_app.settings.settings_window import SettingsWindow

settings_module = importlib.import_module("haochen_app.settings.settings_window")


def store_at(tmp_path):
    store = ConfigStore(tmp_path, keychain=MemoryCredentialStore())
    store.ensure_initialized()
    return store


class LockedTestBackend:
    def __init__(self):
        self.calls = 0
        self.allowed = False

    def get_without_ui(self, *_):
        raise KeychainInteractionRequired("test authorization required")

    def authorize(self, *_):
        self.calls += 1
        if not self.allowed:
            raise KeychainInteractionRequired("test cancellation")
        return "test-only-credential"


def test_key_authorization_is_explicit_retryable_and_session_scoped(qtbot, tmp_path):
    backend = LockedTestBackend()
    store = ConfigStore(tmp_path, keychain=KeychainStore("test-only-service", backend=backend))
    store.ensure_initialized()
    store._save("auth.json", {"deepseek": {"type": "api_key", "key": "$HAOCHEN_DEEPSEEK_API_KEY"}})
    page = KeyPage(store, lambda *_: key_validation.ValidationResult(True, "ok"))
    qtbot.addWidget(page)
    page.show()
    assert page.verify_button.text() == "连接模型"
    assert not hasattr(page, "authorize_button")
    assert backend.calls == 0  # No system prompt during initialization/status reads.
    page.verify_button.click()
    qtbot.waitUntil(lambda: not page._working)
    assert not page.isComplete()
    assert "原 Key 未改变" in page.status.text()
    backend.allowed = True
    page.verify_button.click()
    qtbot.waitUntil(page.isComplete)
    assert backend.calls == 2
    assert store.key_status("deepseek")[0]  # One-time Allow is enough for this process.
    assert not store.key_access_required("deepseek")
    assert "test-only-credential" not in page.status.text()
    assert KeychainStore("test-only-service", backend=backend)._authorized == {}


def test_settings_new_key_validation_never_authorizes_old_key(qtbot, tmp_path, monkeypatch):
    backend = LockedTestBackend()
    backend.allowed = True
    store = ConfigStore(tmp_path, keychain=KeychainStore("test-only-service", backend=backend))
    store.ensure_initialized()
    store._save("auth.json", {"deepseek": {"type": "api_key", "key": "$HAOCHEN_DEEPSEEK_API_KEY"}})
    window = SettingsWindow(store=store)
    qtbot.addWidget(window)
    calls = []
    def validate(provider, key):
        calls.append((provider, key))
        return key_validation.ValidationResult(False, "test rejection")
    monkeypatch.setattr(settings_module, "validate_api_key", validate)
    edit, _badge, button = window._key_widgets["deepseek"]
    edit.setText("unsaved-test-draft")
    button.click()
    qtbot.waitUntil(lambda: not window._working)
    assert backend.calls == 0
    assert calls == [("deepseek", "unsaved-test-draft")]
    assert edit.text() == "unsaved-test-draft"
    assert store.get_key("deepseek") == "$HAOCHEN_DEEPSEEK_API_KEY"


def test_onboarding_save_explicitly_allows_keychain_authorization(qtbot, tmp_path, monkeypatch):
    store = store_at(tmp_path)
    calls = []
    original = store.set_key

    def save(provider, key, *, allow_keychain_authorization=False):
        calls.append(allow_keychain_authorization)
        return original(provider, key, allow_keychain_authorization=allow_keychain_authorization)

    monkeypatch.setattr(store, "set_key", save)
    page = KeyPage(store, lambda *_: key_validation.ValidationResult(True, "ok"))
    qtbot.addWidget(page)
    page.key_edit.setText("test-only-credential")
    page.verify_button.click()
    qtbot.waitUntil(page.isComplete)
    assert calls == [True]


def test_noninteractive_status_never_waits_for_pending_system_authorization():
    import pytest
    from haochen_app.keychain import _INTERACTION_LOCK, KeychainError, _NativeKeychainBackend
    entered, release = threading.Event(), threading.Event()

    def hold_authorization():
        with _INTERACTION_LOCK:
            entered.set()
            release.wait(2)

    worker = threading.Thread(target=hold_authorization)
    worker.start()
    try:
        assert entered.wait(1)
        backend = _NativeKeychainBackend.__new__(_NativeKeychainBackend)
        with pytest.raises(KeychainError, match="授权正在进行中"):
            backend.get_without_ui("test-service", "test-provider")
    finally:
        release.set()
        worker.join()


def test_key_save_does_not_block_gui_and_survives_runtime_refresh(qtbot, tmp_path, monkeypatch):
    store = store_at(tmp_path)
    window = SettingsWindow(store=store)
    qtbot.addWidget(window)
    entered, release = threading.Event(), threading.Event()
    main_thread = threading.get_ident()
    worker_threads = []
    original = store.set_key

    def slow_save(provider, candidate, **kwargs):
        assert kwargs == {"allow_keychain_authorization": True}
        worker_threads.append(threading.get_ident())
        entered.set()
        if not release.wait(2):
            raise TimeoutError("test barrier")
        return original(provider, candidate)

    monkeypatch.setattr(store, "set_key", slow_save)
    monkeypatch.setattr(settings_module, "validate_api_key",
                        lambda *_: key_validation.ValidationResult(True, "ok"))
    edit, badge, button = window._key_widgets["deepseek"]
    edit.setText("test-only-credential")
    window.show()
    button.click()
    try:
        qtbot.waitUntil(entered.is_set)
        pulses = []
        QTimer.singleShot(0, lambda: pulses.append(True))
        qtbot.waitUntil(lambda: bool(pulses))
        assert window.progress.isVisible()
        window.set_runtime_key_validation("deepseek", True, "回答完成")
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert window._key_widgets["deepseek"][0] is edit
        assert edit.text() == "test-only-credential"
    finally:
        release.set()
    qtbot.waitUntil(lambda: not window._working)
    assert worker_threads == [worker_threads[0]] and worker_threads[0] != main_thread
    assert store.keychain.get("deepseek") == "test-only-credential"
    assert "已连接" in badge.text()
    button.click()
    assert store.keychain.get("deepseek") == "test-only-credential"


def test_key_delete_is_explicit_and_cancellable(qtbot, tmp_path, monkeypatch):
    store = store_at(tmp_path)
    store.set_key("deepseek", "test-only-credential")
    window = SettingsWindow(store=store)
    qtbot.addWidget(window)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.No)
    window._delete_key("deepseek")
    assert store.keychain.get("deepseek")
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    window._delete_key("deepseek")
    qtbot.waitUntil(lambda: not window._working)
    assert store.keychain.get("deepseek") is None


def test_onboarding_freezes_exact_candidate_and_reports_saved_state(qtbot, tmp_path):
    store = store_at(tmp_path)
    started, release = threading.Event(), threading.Event()

    def verify(*_):
        started.set()
        release.wait(2)
        return key_validation.ValidationResult(True, "ok")

    page = KeyPage(store, verify, custom_verifier=verify)
    qtbot.addWidget(page)
    page.mode_combo.setCurrentIndex(1)
    page.url_edit.setText("https://example.invalid/v1")
    page.model_id_edit.setText("first-model")
    page.key_edit.setText("test-only-credential")
    page.verify_button.click()
    try:
        qtbot.waitUntil(started.is_set)
        assert not page.url_edit.isEnabled()
        assert not page.model_id_edit.isEnabled()
        assert not page.key_edit.isEnabled()
        assert not page.isComplete()
    finally:
        release.set()
    qtbot.waitUntil(page.isComplete)
    assert store.default_model()[1] == "first-model"
    assert "已连接" in page.verify_button.text()
    assert "已安全保存" in page.key_edit.placeholderText()


def test_permission_handoff_uses_normal_window_and_specific_settings(qtbot, tmp_path, monkeypatch):
    from haochen_app import permissions
    calls = []
    shell = AppShell(mock=True, home=tmp_path)
    qtbot.addWidget(shell.chat)
    shell.first_run_setup()
    qtbot.addWidget(shell.onboarding)
    assert not shell.onboarding.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    monkeypatch.setattr(permissions, "request_accessibility", lambda: False)
    monkeypatch.setattr(permissions, "open_permission_settings", calls.append)
    shell._request_onboarding_permission("accessibility")
    qtbot.waitUntil(lambda: calls == ["accessibility"])


def test_wizard_can_be_created_without_native_bundle(qtbot, tmp_path):
    wizard = OnboardingWizard(store_at(tmp_path))
    qtbot.addWidget(wizard)
    assert wizard.currentId() == 0


def test_normal_and_detail_close_return_to_pet(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(a11y, "reduce_motion_enabled", lambda: True)
    shell = AppShell(mock=True, home=tmp_path)
    for widget in (shell.chat, shell.pet.pet, shell.pet.bubble):
        qtbot.addWidget(widget)
    shell.pet.bubble.start_input()
    shell.pet.bubble.summon()
    shell.pet.bubble.input.setPlainText("我的草稿")
    shell.pet.bubble.btn_open_chat.click()
    assert shell.chat._detail_mode and shell.chat.isVisible()
    assert shell.chat.input.toPlainText() == "我的草稿"
    assert shell.pet.pet.isVisible()
    qtbot.keyClick(shell.chat.input, Qt.Key.Key_Escape)
    assert not shell.chat.isVisible()
    assert shell.pet.pet.isVisible()
    shell.chat.show_normal()
    shell.chat._on_close_shortcut()
    assert not shell.chat.isVisible()


def test_detail_anchors_current_question_not_history_start(qtbot, monkeypatch):
    monkeypatch.setattr(a11y, "reduce_motion_enabled", lambda: True)
    window = harness.ChatWindow(harness.FakeClient())
    qtbot.addWidget(window)
    window.open_from_bubble(QRect(), "Question 20")
    messages = [{"role": "user", "content": [{"type": "text", "text": f"Question {i}"}]}
                for i in range(30)]
    window._render_history({"success": True, "data": {"messages": messages}})
    assert window._detail_anchor_row is not None
    scroll = window.scroll.verticalScrollBar()
    qtbot.waitUntil(lambda: 0 < scroll.value() < scroll.maximum(), timeout=5000)
    qtbot.waitUntil(lambda: not window._detail_position_pending, timeout=5000)
    assert 0 < scroll.value() < scroll.maximum()


def test_compact_thinking_and_permission_cleanup_resize_immediately(qtbot):
    bubble = BubbleWindow()
    qtbot.addWidget(bubble)
    bubble.show()
    block = bubble.present_status("我想想", cancellable=True)
    qtbot.wait(50)
    assert block._lb.isHidden() and block.dots.isVisible()
    assert block.stop_button.isVisible()
    assert bubble.height() <= 84
    bubble.show_confirm("允许读取这个窗口吗？")
    qtbot.wait(50)
    old_height = bubble.height()
    bubble.hide_confirm()
    qtbot.wait(50)
    assert bubble.height() < old_height
    assert bubble.height() <= 84
    block._show_slow_status()
    assert not block._lb.isHidden()
    assert "等回复" in block._lb.text()


def test_retina_icons_are_not_upscaled_and_brief_removes_highlight_markup(qapp):
    pixmap = _action_icon("send", "#ffffff").pixmap(QSize(18, 18), 2.0)
    assert pixmap.width() >= 36
    assert pixmap.devicePixelRatio() >= 2
    assert plain_visible_text("重点是==具体结果==，以及**原因**。") == "重点是具体结果，以及原因。"
    assert plain_visible_text("a == b") == "a == b"


class Response:
    status = 200

    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def read(self, *_args):
        return self.data


def test_invalid_model_lists_never_pass(monkeypatch):
    for payload in (b"<html>wrong URL</html>", b'{"data":[]}'):
        monkeypatch.setattr(key_validation.urllib.request, "urlopen", lambda *_a, **_k: Response(payload))
        assert not key_validation.validate_custom_model("https://example.invalid/v1", "model", "fixture").ok


def test_compatible_endpoint_needs_a_real_model_response(monkeypatch):
    requests = []

    def respond(request, **_kwargs):
        requests.append(request)
        if request.full_url.endswith("/models"):
            return Response(b'{"data":[{"id":"model"}]}')
        return Response(b'{"choices":[{"message":{"role":"assistant","content":"OK"}}]}')

    monkeypatch.setattr(key_validation.urllib.request, "urlopen", respond)
    assert key_validation.validate_custom_model("https://example.invalid/v1", "model", "fixture").ok
    assert len(requests) == 2
    assert json.loads(requests[1].data)["model"] == "model"


def test_no_models_endpoint_can_validate_responses_protocol(monkeypatch):
    def respond(request, **_kwargs):
        if request.full_url.endswith("/models"):
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
        assert request.full_url.endswith("/responses")
        return Response(b'{"output":[{"type":"message","content":[{"text":"OK"}]}]}')
    monkeypatch.setattr(key_validation.urllib.request, "urlopen", respond)
    assert key_validation.validate_custom_model("https://example.invalid/v1", "model", "fixture",
                                                api="openai-responses").ok


def test_reader_selects_target_not_largest_or_foreign_window():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "reader"))
    from haochen_reader import select_capture_window
    big = {"kCGWindowLayer": 0, "kCGWindowOwnerPID": 42, "kCGWindowName": "big",
           "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 1400, "Height": 900}}
    small = {"kCGWindowLayer": 0, "kCGWindowOwnerPID": 42, "kCGWindowName": "target",
             "kCGWindowBounds": {"X": 30, "Y": 40, "Width": 400, "Height": 300}}
    assert select_capture_window([big, small], 42, "target", (30, 40, 400, 300)) is small
    assert select_capture_window([big, small], 42) is None
    assert select_capture_window([big, small], 77, "target") is None
    assert select_capture_window([big], 42, "target", (30, 40, 400, 300)) is None
