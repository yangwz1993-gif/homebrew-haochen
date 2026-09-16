"""模型身份真相链（0.6.2-beta.3，用户痛点：问模型自报身份得到的是复读的旧答案）。

保证为真的机制：身份问题由 App 拦截，用三层结构化事实回答——
引擎实况（get_state）→ 服务方实测（真实 API 回包 model 字段）→ 磁盘默认配置。
"""

from __future__ import annotations

import importlib
import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest
from PyQt6.QtCore import QObject, pyqtSignal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

conversation = importlib.import_module("haochen_app.conversation")
keychain = importlib.import_module("haochen_app.keychain")
validation = importlib.import_module("haochen_app.key_validation")
config_module = importlib.import_module("haochen_app.settings.config_store")

ConversationController = conversation.ConversationController
ServingProbe = validation.ServingProbe


# ── 服务方实测探针 ─────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()

    def read(self, _limit: int) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_probe_serving_model_returns_response_model_field(monkeypatch) -> None:
    seen = {}

    class FakeUrlopen:
        def __call__(self, request, timeout=0):
            seen["url"] = request.full_url
            seen["auth"] = request.headers.get("Authorization")
            return _FakeResponse({"model": "deepseek-v4-flash-0731-baidu", "choices": []})

    monkeypatch.setattr(validation.urllib.request, "urlopen", FakeUrlopen())
    result = validation.probe_serving_model(
        "https://gw.example/v1/", "deepseek-v4-flash-0731-baidu", key="k" * 16)
    assert result.ok and result.served == "deepseek-v4-flash-0731-baidu"
    assert seen["url"] == "https://gw.example/v1/chat/completions"
    assert seen["auth"] == f"Bearer {'k' * 16}"


def test_probe_serving_model_http_error_is_honest(monkeypatch) -> None:
    def raise_http(request, timeout=0):
        raise urllib.error.HTTPError(request.full_url, 401, "no", {}, io.BytesIO(b""))

    monkeypatch.setattr(validation.urllib.request, "urlopen", raise_http)
    result = validation.probe_serving_model("https://gw.example/v1", "m1", key="k" * 16)
    assert not result.ok and "401" in result.message


def test_probe_serving_model_missing_model_field_cannot_confirm(monkeypatch) -> None:
    monkeypatch.setattr(
        validation.urllib.request, "urlopen",
        lambda request, timeout=0: _FakeResponse({"choices": []}))
    result = validation.probe_serving_model("https://gw.example/v1", "m1")
    assert not result.ok and "无法证实" in result.message


def test_probe_for_codewiz_without_credentials_says_not_ready(tmp_path: Path) -> None:
    store = config_module.ConfigStore(tmp_path, keychain=keychain.MemoryCredentialStore())
    store.ensure_initialized()
    result = validation.probe_serving_model_for(store, "codewiz", "kimi-k3")
    assert not result.ok and "内网凭证未就绪" in result.message


def test_probe_for_deepseek_without_key_is_honest(tmp_path: Path) -> None:
    store = config_module.ConfigStore(tmp_path, keychain=keychain.MemoryCredentialStore())
    store.ensure_initialized()
    result = validation.probe_serving_model_for(store, "deepseek", "deepseek-v4-flash")
    assert not result.ok and "无法实测" in result.message


def test_probe_for_gemini_protocol_is_honest_about_unsupported(tmp_path: Path) -> None:
    store = config_module.ConfigStore(tmp_path, keychain=keychain.MemoryCredentialStore())
    store.ensure_initialized()
    result = validation.probe_serving_model_for(store, "codewiz-gemini", "gemini-3.7-flash")
    assert not result.ok and "暂不支持实测" in result.message


# ── 身份问题识别与回答合成 ──────────────────────────────────────


@pytest.mark.parametrize("text", [
    "你是什么模型", "你现在是啥模型", "你用的什么模型？", "你的模型是什么",
    "what model are you", "你现在是什么模型呀",
])
def test_identity_question_patterns(text: str) -> None:
    assert conversation.is_identity_question(text)


@pytest.mark.parametrize("text", ["帮我看看这个页面", "你是谁", "模型怎么用", "切换模型"])
def test_non_identity_questions_pass_through(text: str) -> None:
    assert not conversation.is_identity_question(text)


def test_compose_identity_answer_consistent() -> None:
    brief, detail = conversation.compose_identity_answer(
        {"model": {"id": "m1", "provider": "codewiz"}, "thinkingLevel": "low"},
        ServingProbe(True, "m1", "m1", "ok"),
        ("codewiz", "m1"),
    )
    assert "m1" in brief and "实测一致" in brief
    assert "引擎实况" in detail and "codewiz / m1" in detail and "low" in detail
    assert "注意" not in detail  # 一致时不多嘴


def test_compose_identity_answer_mismatch_and_default_divergence() -> None:
    brief, detail = conversation.compose_identity_answer(
        {"model": {"id": "old-model", "provider": "codewiz"}, "thinkingLevel": "off"},
        ServingProbe(True, "old-model", "served-else", "ok"),
        ("codewiz", "new-default"),
    )
    assert "实测不一致" in brief
    assert "以服务方为准" in detail
    assert "默认配置是 codewiz / new-default" in detail


def test_compose_identity_answer_probe_failed_still_truthful() -> None:
    brief, detail = conversation.compose_identity_answer(
        {"model": {"id": "m1", "provider": "codewiz"}, "thinkingLevel": "high"},
        ServingProbe(False, "m1", "", "网络连接失败"),
        ("codewiz", "m1"),
    )
    assert brief == "m1（codewiz）"
    assert "网络连接失败" in detail and "以引擎实况为准" in detail


# ── 身份回合：不发 prompt 给引擎，直接用事实回答 ─────────────────


class FakeIdentityClient(QObject):
    event = pyqtSignal(dict)  # pyright: ignore[reportAssignmentType]
    response = pyqtSignal(dict)
    crashed = pyqtSignal(int)

    def __init__(self, home: Path) -> None:
        super().__init__()
        self.sent: list[str] = []
        self._home = home
        self.credentials = keychain.MemoryCredentialStore()

    @property
    def home(self) -> Path:
        return self._home

    def prompt(self, text: str) -> str:
        self.sent.append(text)
        return f"req-{len(self.sent)}"

    def abort(self) -> str:
        return "abort"

    def get_state(self) -> str:
        return "state-1"


def test_identity_turn_never_prompts_engine_and_answers_from_facts(qtbot, tmp_path: Path, monkeypatch) -> None:
    client = FakeIdentityClient(tmp_path)
    controller = ConversationController(client)
    monkeypatch.setattr(
        conversation, "probe_serving_model_for",
        lambda store, provider, model_id: ServingProbe(True, model_id, model_id, "ok"))
    turns = []
    controller.turn_done.connect(turns.append)

    rid = controller.send("你现在是什么模型？")
    assert rid == "state-1"
    client.response.emit({"id": "state-1", "success": True, "data": {
        "model": {"id": "live-model-x", "provider": "codewiz"}, "thinkingLevel": "low"}})

    qtbot.waitUntil(lambda: bool(turns), timeout=2000)
    assert client.sent == []  # 身份问题没有发给引擎（模型没机会背课文）
    result = turns[0]
    assert "live-model-x" in result.detail
    assert "与实况一致" in result.detail and "实测一致" in result.brief
    assert not controller.busy


def test_identity_turn_probe_failure_still_answers_with_engine_truth(qtbot, tmp_path: Path, monkeypatch) -> None:
    client = FakeIdentityClient(tmp_path)
    controller = ConversationController(client)
    monkeypatch.setattr(
        conversation, "probe_serving_model_for",
        lambda store, provider, model_id: ServingProbe(False, model_id, "", "内网凭证未就绪"))
    turns = []
    controller.turn_done.connect(turns.append)

    controller.send("你是什么模型")
    client.response.emit({"id": "state-1", "success": True, "data": {
        "model": {"id": "live-model-x", "provider": "codewiz"}, "thinkingLevel": "high"}})

    qtbot.waitUntil(lambda: bool(turns), timeout=2000)
    assert "live-model-x" in turns[0].detail
    assert "内网凭证未就绪" in turns[0].detail
    assert "以引擎实况为准" in turns[0].detail


def test_identity_turn_survives_get_state_failure(qtbot, tmp_path: Path) -> None:
    client = FakeIdentityClient(tmp_path)
    controller = ConversationController(client)
    turns = []
    controller.turn_done.connect(turns.append)

    controller.send("你是什么模型")
    client.response.emit({"id": "state-1", "success": False, "error": "engine gone"})

    qtbot.waitUntil(lambda: bool(turns), timeout=2000)
    assert "未知" in turns[0].detail  # 如实标注，不编造
    assert not controller.busy
