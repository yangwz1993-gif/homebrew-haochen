"""The runtime, not credential persistence, is the connection success boundary."""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QObject, pyqtSignal

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from haochen_app.config_activation import ConfigActivation
from haochen_app.engine_client import EngineClient
from haochen_app.key_validation import ValidationResult
from haochen_app.keychain import MemoryCredentialStore
from haochen_app.onboarding import KeyPage, OnboardingWizard
from haochen_app.settings.config_store import ConfigStore
from haochen_app.settings.settings_window import SettingsWindow
from haochen_app.supervisor import EngineSupervisor

ROOT = Path(__file__).resolve().parents[1]


class FakeClient(QObject):
    response = pyqtSignal(dict)
    alive = True
    configuration_blocked = False

    def __init__(self):
        super().__init__()
        self.calls = []

    def set_model(self, p, m):
        self.calls.append(("set_model", p, m))
        return f"select-{len(self.calls)}"

    def get_state(self):
        self.calls.append(("get_state",))
        return f"state-{len(self.calls)}"


class FakeSupervisor(QObject):
    restarted = pyqtSignal()
    restarting = pyqtSignal(int)
    restart_failed = pyqtSignal()
    crashed = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.client = FakeClient()
        self._ctrls = []
        self._restarting = False
        self.restarts = 0

    def restart_now(self, *, reason="recovery"):
        self.restart_reason = reason
        self.restarts += 1


def test_ready_requires_reload_selection_ack_and_matching_engine_state(qtbot):
    sup = FakeSupervisor()
    flow = ConfigActivation(sup)
    results = []
    flow.apply("deepseek", "m", lambda *r: results.append(r))
    assert sup.restarts == 1 and sup.client.configuration_blocked and not results
    sup.restarted.emit()
    assert sup.client.calls == [("set_model", "deepseek", "m")] and not results
    sup.client.response.emit({"id": "unrelated", "success": True})
    assert not results
    sup.client.response.emit({"id": "select-1", "success": True})
    assert sup.client.calls[-1] == ("get_state",) and not results
    sup.client.response.emit({"id": "state-2", "success": True,
                              "data": {"model": {"provider": "deepseek", "id": "m"}}})
    assert results[0][0] and not sup.client.configuration_blocked
    flow.ensure_ready("deepseek", "m", lambda *r: results.append(r))
    assert len(results) == 2 and sup.restarts == 1


@pytest.mark.parametrize("failure", ["restart", "select", "mismatch", "timeout"])
def test_failed_activation_never_releases_send_gate_and_can_retry(qtbot, failure):
    sup = FakeSupervisor()
    flow = ConfigActivation(sup)
    results = []
    flow.apply("deepseek", "m", lambda *r: results.append(r))
    if failure == "restart":
        sup.restart_failed.emit()
    elif failure == "timeout":
        flow._deadline.timeout.emit()
    else:
        sup.restarted.emit()
        sup.client.response.emit({"id": "select-1", "success": failure != "select"})
        if failure == "mismatch":
            sup.client.response.emit({"id": "state-2", "success": True,
                                      "data": {"model": {"provider": "other", "id": "old"}}})
    assert len(results) == 1 and not results[0][0] and sup.client.configuration_blocked
    assert "无需重新填写" in results[0][1]
    flow.apply("deepseek", "m", lambda *_: None)
    assert sup.restarts == 2
    flow.stop()


def test_busy_turn_finishes_before_reload_and_duplicate_request_cannot_replace_it(qtbot):
    sup = FakeSupervisor()
    ctrl = SimpleNamespace(busy=True)
    sup._ctrls.append(ctrl)
    flow = ConfigActivation(sup)
    flow.apply("deepseek", "m", lambda *_: None)
    assert sup.restarts == 0 and sup.client.configuration_blocked
    duplicate = []
    flow.apply("other", "new", lambda *r: duplicate.append(r))
    assert duplicate and not duplicate[0][0] and flow._target == ("deepseek", "m")
    ctrl.busy = False
    qtbot.waitUntil(lambda: sup.restarts == 1)
    flow.stop()


def test_model_only_change_uses_same_ack_gate_without_restarting(qtbot):
    sup = FakeSupervisor()
    flow = ConfigActivation(sup)
    flow.apply("deepseek", "m", lambda *_: None, reload=False)
    assert sup.restarts == 0 and sup.client.calls == [("set_model", "deepseek", "m")]
    assert sup.client.configuration_blocked
    flow.stop()


def test_settings_does_not_show_connected_until_runtime_ready(qtbot, tmp_path, monkeypatch):
    import importlib
    module = importlib.import_module("haochen_app.settings.settings_window")
    store = ConfigStore(tmp_path, keychain=MemoryCredentialStore())
    store.ensure_initialized()
    callbacks = []
    window = SettingsWindow(store=store, activate=lambda p, m, done: callbacks.append(done))
    qtbot.addWidget(window)
    monkeypatch.setattr(module, "validate_api_key", lambda *_: ValidationResult(True, "ok"))
    edit, badge, button = window._key_widgets["deepseek"]
    edit.setText("test-only-key")
    button.click()
    qtbot.waitUntil(lambda: bool(callbacks))
    assert store.key_status("deepseek")[0] and window._working
    assert button.text() == "连接中…" and badge.objectName() != "badgeOk"
    callbacks[0](False, "模型尚未就绪")
    assert not window._working and button.text() == "连接模型"
    button.click()
    qtbot.waitUntil(lambda: len(callbacks) == 2)
    callbacks[1](True, "ready")
    assert button.text() == "已连接 ✓" and badge.objectName() == "badgeOk"


def test_no_prompt_can_bypass_configuration_gate(tmp_path, monkeypatch):
    client = EngineClient(mock=True, home=tmp_path)
    client.configuration_blocked = True
    monkeypatch.setattr(client, "_send", lambda _: pytest.fail("prompt crossed the gate"))
    with pytest.raises(RuntimeError, match="尚未就绪"):
        client.prompt("test-only")


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
def test_wizard_completion_and_trial_wait_for_runtime(qtbot, tmp_path, outcome):
    store = ConfigStore(tmp_path, keychain=MemoryCredentialStore())
    store.ensure_initialized()
    callbacks, prompts = [], []
    wizard = OnboardingWizard(store, prepare=lambda p, m, done: callbacks.append(done))
    qtbot.addWidget(wizard)
    wizard.trial_requested.connect(prompts.append)
    wizard.accept()
    assert not wizard.state.completed and not prompts
    if outcome == "cancel":
        wizard.reject()
    callbacks[0](outcome != "failure", "not ready")
    assert wizard.state.completed == (outcome == "success")
    assert bool(prompts) == (outcome == "success")


@pytest.mark.parametrize("provider", ["deepseek", "custom-local-test"])
def test_real_engine_first_key_and_replacement_reach_first_reply_without_manual_restart(
    qtbot, tmp_path, monkeypatch, provider,
):
    """Real bundled engine + loopback HTTP; no external model or OS Keychain."""
    engine = ROOT / "engine" / "haochen-engine"
    if not engine.is_file():
        pytest.skip("build the engine to run the process-level acceptance test")
    received = []
    class Endpoint(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.headers.get("Authorization"), body.get("model")))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            payload = {"id": "test", "object": "chat.completion.chunk", "created": 1, "model": "qa-model",
                       "choices": [{"index": 0, "delta": {"role": "assistant", "content": "ready-test"},
                                    "finish_reason": None}]}
            self.wfile.write(("data: " + json.dumps(payload) + "\n\n").encode())
            payload["choices"] = [{"index": 0, "delta": {}, "finish_reason": "stop"}]
            self.wfile.write(("data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n").encode())
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("HAOCHEN_HOME", str(tmp_path))
    # Keep this fixture strictly loopback even when the developer shell has a proxy.
    for variable in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    sup = EngineSupervisor(mock=False, engine=engine, home=tmp_path)
    sup.BACKOFF_MS = [10, 10, 10]
    sup.client.credentials = MemoryCredentialStore()
    sup.client._ext = None
    store = ConfigStore(tmp_path, keychain=sup.client.credentials)
    store.ensure_initialized()
    url = f"http://127.0.0.1:{server.server_port}/v1"
    store._save("models.json", {"providers": {provider: {
        "baseUrl": url, "api": "openai-completions", "models": [{
            "id": "qa-model", "name": "QA", "reasoning": False, "input": ["text"],
            "contextWindow": 4096, "maxTokens": 128,
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        }],
    }}})
    store._save("auth.json", {})
    store.set_default_model(provider, "qa-model")
    flow = ConfigActivation(sup)
    events = []
    sup.client.event.connect(events.append)
    page = KeyPage(store, lambda *_: ValidationResult(True, "synthetic probe"),
                   custom_verifier=lambda *_: ValidationResult(True, "synthetic probe"), activate=flow.apply)
    qtbot.addWidget(page)
    if provider == "deepseek":
        page.mode_combo.setCurrentIndex(0)  # Exercise the previously broken preset path.
    try:
        sup.start()  # Deliberately start BEFORE any credential exists.
        qtbot.waitUntil(lambda: bool(sup.coordinator.current_session), timeout=8000)
        session = sup.coordinator.current_session
        for number in (1, 2):
            key = f"test-only-key-{number}"
            qtbot.keyClicks(page.key_edit, key)
            page.verify_button.click()
            assert not page.isComplete()
            qtbot.waitUntil(page.isComplete, timeout=15000)
            assert page.verify_button.text() == "已连接 ✓" and not sup.client.configuration_blocked
            if number == 2:  # A never-used empty session need not have a file yet.
                assert sup.coordinator.current_session == session
            events.clear()
            sup.client.prompt("Return ready-test; this is local fixture data.")
            qtbot.waitUntil(lambda: any(e.get("type") == "agent_end" for e in events), timeout=10000)
            assert received, [e.get("message", {}).get("errorMessage") for e in events
                              if e.get("type") == "message_end"]
            assert received[-1] == (f"Bearer {key}", "qa-model")
            assert any(e.get("type") == "message_end" and e.get("message", {}).get("stopReason") == "stop"
                       for e in events)
            session = sup.coordinator.current_session
        assert len(received) == 2
    finally:
        flow.stop()
        sup.stop()
        assert sup.client.wait_stopped(3)
        server.shutdown()
        server.server_close()
        thread.join(2)
