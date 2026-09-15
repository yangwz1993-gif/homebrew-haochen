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


@pytest.mark.parametrize("failure", ["restart", "select", "mismatch", "timeout"])
def test_failed_switch_releases_gate_when_verified_fallback_exists(qtbot, failure):
    """终态不变量：已有验证过的模型时，切换失败必须放行聊天并回退到旧就绪态。

    与 test_failed_activation_never_releases_send_gate_and_can_retry 互补：
    那条钉「从未就绪过 → 失败保持关闭」（没有可回退的安全配置）；
    这条钉「就绪过 → 失败必须释放」（旧模型还在跑，锁死是 bug）。
    """
    sup = FakeSupervisor()
    flow = ConfigActivation(sup)
    ok = []
    flow.apply("deepseek", "m", lambda *r: ok.append(r))
    sup.restarted.emit()
    sup.client.response.emit({"id": "select-1", "success": True})
    sup.client.response.emit({"id": "state-2", "success": True,
                              "data": {"model": {"provider": "deepseek", "id": "m"}}})
    assert ok == [(True, "模型已就绪，可以开始对话。")]
    assert not sup.client.configuration_blocked
    results = []
    flow.apply("codewiz", "kimi-k3", lambda *r: results.append(r))
    if failure == "restart":
        sup.restart_failed.emit()
    elif failure == "timeout":
        flow._deadline.timeout.emit()
    else:
        sup.restarted.emit()
        sup.client.response.emit({"id": "select-3", "success": failure != "select"})
        if failure == "mismatch":
            sup.client.response.emit({"id": "state-4", "success": True,
                                      "data": {"model": {"provider": "deepseek", "id": "m"}}})
    assert len(results) == 1 and not results[0][0]
    assert not sup.client.configuration_blocked, "切换失败不得锁死聊天（与启动态语义一致）"
    # 重启路径下引擎状态不确定：不伪装回退成功，清空就绪态交下次 ensure_ready 重验
    assert flow._ready_target is None
    flow.stop()


def test_failed_hot_switch_restores_verified_fallback(qtbot):
    """热切换（不重启引擎）失败：引擎未被触碰，旧模型仍在跑，可以直接回退。"""
    sup = FakeSupervisor()
    flow = ConfigActivation(sup)
    ok = []
    flow.apply("deepseek", "m", lambda *r: ok.append(r))
    sup.restarted.emit()
    sup.client.response.emit({"id": "select-1", "success": True})
    sup.client.response.emit({"id": "state-2", "success": True,
                              "data": {"model": {"provider": "deepseek", "id": "m"}}})
    assert ok and not sup.client.configuration_blocked
    results = []
    flow.apply("codewiz", "kimi-k3", lambda *r: results.append(r), reload=False)
    assert sup.restarts == 1  # 热切换不再重启
    sup.client.response.emit({"id": "select-3", "success": False})
    assert len(results) == 1 and not results[0][0]
    assert not sup.client.configuration_blocked
    assert flow._ready_target == ("deepseek", "m"), "热切换失败应回退到旧的就绪模型"
    # 回退后就绪态复用：ensure_ready 不再触发重启
    again = []
    flow.ensure_ready("deepseek", "m", lambda *r: again.append(r))
    assert again and again[0][0] and sup.restarts == 1
    flow.stop()


def test_prompt_passes_gate_after_failure_release(tmp_path, monkeypatch):
    """与 test_no_prompt_can_bypass_configuration_gate 互为正反：闸门释放后 prompt 正常走。"""
    client = EngineClient(mock=True, home=tmp_path)
    client.configuration_blocked = False
    monkeypatch.setattr(client, "_send", lambda msg: "ok-id")
    assert client.prompt("test-only") == "ok-id"


def test_busy_turn_finishes_before_reload_and_queued_switch_runs_next(qtbot):
    """行为变更（reject → queue）：进行中的激活不被打断，新请求排队后自动接续。"""
    sup = FakeSupervisor()
    flow = ConfigActivation(sup)
    a_done, b_done = [], []
    flow.apply("deepseek", "m", lambda *r: a_done.append(r))
    assert sup.restarts == 1 and sup.client.configuration_blocked  # A 已进入重启（引擎已被触碰）
    flow.apply("other", "new", lambda *r: b_done.append(r))
    assert not b_done and flow._target == ("deepseek", "m")  # 排队等待，不立刻应答也不打断
    sup.restarted.emit()
    sup.client.response.emit({"id": "select-1", "success": True})
    sup.client.response.emit({"id": "state-2", "success": True,
                              "data": {"model": {"provider": "deepseek", "id": "m"}}})
    assert a_done and a_done[0][0]
    qtbot.waitUntil(lambda: sup.restarts == 2)  # 排队请求自动接续
    assert flow._target == ("other", "new")
    flow.stop()


def test_waiting_activation_is_superseded_without_touching_engine(qtbot):
    """还在等引擎空闲的激活被新请求立即取代；旧回退链不丢、引擎不被多余重启。"""
    sup = FakeSupervisor()
    ctrl = SimpleNamespace(busy=True)
    sup._ctrls.append(ctrl)
    flow = ConfigActivation(sup)
    a_done, b_done = [], []
    flow.apply("deepseek", "m", lambda *r: a_done.append(r))
    assert sup.restarts == 0
    flow.apply("codewiz", "kimi-k3", lambda *r: b_done.append(r))
    assert a_done and not a_done[0][0] and "取代" in a_done[0][1]
    assert flow._target == ("codewiz", "kimi-k3")
    ctrl.busy = False
    qtbot.waitUntil(lambda: sup.restarts == 1)
    flow.stop()


def test_queued_switch_is_superseded_by_newer_request(qtbot):
    """最新优先：排队中的请求被更新的请求取代并被告之，最新请求最终执行。"""
    sup = FakeSupervisor()
    flow = ConfigActivation(sup)
    a_done, b_done, c_done = [], [], []
    flow.apply("deepseek", "m", lambda *r: a_done.append(r))
    flow.apply("codewiz", "kimi-k3", lambda *r: b_done.append(r))
    flow.apply("zhipu", "glm", lambda *r: c_done.append(r))
    assert b_done and not b_done[0][0] and "取代" in b_done[0][1] and not c_done
    sup.restarted.emit()
    sup.client.response.emit({"id": "select-1", "success": True})
    sup.client.response.emit({"id": "state-2", "success": True,
                              "data": {"model": {"provider": "deepseek", "id": "m"}}})
    assert a_done and a_done[0][0]
    qtbot.waitUntil(lambda: sup.restarts == 2)
    sup.restarted.emit()
    sup.client.response.emit({"id": "select-3", "success": True})
    sup.client.response.emit({"id": "state-4", "success": True,
                              "data": {"model": {"provider": "zhipu", "id": "glm"}}})
    assert c_done and c_done[0][0]
    flow.stop()


def test_hot_switch_escalates_to_restart_when_model_not_in_catalog(qtbot):
    """热切换时目标模型不在引擎目录 → 自动升级为重启生效，而不是直接失败。"""
    sup = FakeSupervisor()
    flow = ConfigActivation(sup)
    results = []
    flow.apply("codewiz", "kimi-k3", lambda *r: results.append(r), reload=False)
    assert sup.restarts == 0
    sup.client.response.emit({"id": "select-1", "success": False,
                              "error": "Model not found: codewiz/kimi-k3"})
    assert sup.restarts == 1 and not results  # 自动升级，而不是失败
    sup.restarted.emit()
    sup.client.response.emit({"id": "select-2", "success": True})
    sup.client.response.emit({"id": "state-3", "success": True,
                              "data": {"model": {"provider": "codewiz", "id": "kimi-k3"}}})
    assert results and results[0][0] and not sup.client.configuration_blocked
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
    combo = window._provider_combo
    for i in range(combo.count()):
        if any(p.id == "deepseek" for p in combo.itemData(i)):
            combo.setCurrentIndex(i)
            break
    edit, badge, button = window._key_widgets["deepseek"]
    edit.setText("test-only-key")
    button.click()
    qtbot.waitUntil(lambda: bool(callbacks))
    assert store.key_status("deepseek")[0] and window._working
    assert button.text() == "连接中…" and badge.objectName() != "badgeOk"
    callbacks[0](False, "模型尚未就绪")
    assert not window._working and button.text() == "连接模型"
    # Key 已保存但模型未就绪 → 走「重新连接」（授权 + 验证已保存的 Key）
    reconnect = next(a for a in window._key_more_menu.actions() if a.text() == "重新连接")
    reconnect.trigger()
    qtbot.waitUntil(lambda: len(callbacks) == 2)
    callbacks[1](True, "ready")
    assert badge.objectName() == "badgeOk" and "已连接" in badge.text()


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
