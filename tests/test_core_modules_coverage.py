"""Coverage for config_store, keychain, app_tracking, engine_client (P0 modules)."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

config_module = importlib.import_module("haochen_app.settings.config_store")
keychain = importlib.import_module("haochen_app.keychain")
engine_module = importlib.import_module("haochen_app.engine_client")
tracking = importlib.import_module("haochen_app.app_tracking")
paths_module = importlib.import_module("haochen_app.paths")

ConfigStore = config_module.ConfigStore
ConfigCorruptError = config_module.ConfigCorruptError


def make_store(tmp_path: Path, templates: Path | None = None):
    if templates is None:
        templates = ROOT / "config"
    original = config_module.TEMPLATE_DIR
    config_module.TEMPLATE_DIR = templates
    try:
        credentials = keychain.MemoryCredentialStore()
        store = ConfigStore(tmp_path / "home", keychain=credentials)
        return store, credentials
    finally:
        config_module.TEMPLATE_DIR = original


# ── config_store ──────────────────────────────────────────────

def test_providers_parses_template(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    store.ensure_initialized()
    providers = store.providers()
    assert [p.id for p in providers] == ["deepseek"]
    assert providers[0].name == "deepseek"  # 模板无 name → 回退 id
    assert providers[0].builtin is True
    assert len(providers[0].models) == 3


def test_providers_skips_invalid_entries(tmp_path: Path) -> None:
    templates = tmp_path / "tpl"
    templates.mkdir()
    (templates / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "good": {"name": "G", "models": [{"id": "m1"}]},
                    "bad": "not-a-dict",
                    "nomodels": {"name": "N", "models": [{"no_id": True}, {"id": "ok"}]},
                }
            }
        ),
        encoding="utf-8",
    )
    (templates / "settings.json").write_text("{}", encoding="utf-8")
    (templates / "auth.json.template").write_text("{}", encoding="utf-8")
    original = config_module.TEMPLATE_DIR
    config_module.TEMPLATE_DIR = templates
    try:
        credentials = keychain.MemoryCredentialStore()
        store = ConfigStore(tmp_path / "home", keychain=credentials)
        store.ensure_initialized()
    finally:
        config_module.TEMPLATE_DIR = original
    providers = store.providers()
    ids = [p.id for p in providers]
    assert "good" in ids and "bad" not in ids
    nomodels = next(p for p in providers if p.id == "nomodels")
    assert [m["id"] for m in nomodels.models] == ["ok"]


def test_default_model_roundtrip_and_effect(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    store.ensure_initialized()
    provider, model = store.default_model()
    assert provider == "deepseek"
    assert store.set_default_model("deepseek", "deepseek-v4-pro") == config_module.EFFECT_IMMEDIATE
    assert store.set_default_model("moonshot", "k1") == config_module.EFFECT_RESTART
    assert store.default_model() == ("moonshot", "k1")


def test_thinking_level_and_theme(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    store.ensure_initialized()
    assert store.thinking_level() == "high"
    assert store.set_thinking_level("medium") == config_module.EFFECT_IMMEDIATE
    assert store.thinking_level() == "medium"
    with pytest.raises(ValueError):
        store.set_thinking_level("bogus")
    assert store.theme() == "dark"
    assert store.set_theme("light") == config_module.EFFECT_IMMEDIATE
    assert store.theme() == "light"


def test_corrupt_settings_raises_with_path(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    store.ensure_initialized()
    (store.agent_dir / "settings.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(ConfigCorruptError) as err:
        store.settings()
    assert err.value.path.name == "settings.json"


def test_reset_to_default_restores_template(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    store.ensure_initialized()
    (store.agent_dir / "settings.json").write_text("{}", encoding="utf-8")
    restored = store.reset_to_default(config_module.SETTINGS_FILE)
    assert restored == store.agent_dir / "settings.json"
    assert "defaultProvider" in restored.read_text(encoding="utf-8")


def test_key_status_branches(tmp_path: Path) -> None:
    store, credentials = make_store(tmp_path)
    store.ensure_initialized()
    # 模板占位 → 未配置
    assert store.key_status("deepseek") == (False, "未配置")
    # Keychain 引用且后端有值 → 已安全存储
    store.set_key("deepseek", "secret-value")
    assert store.key_status("deepseek") == (True, "已安全存储在 Keychain")
    # Keychain 引用但后端丢失 → 缺少凭据
    credentials.delete("deepseek")
    assert store.key_status("deepseek") == (False, "Keychain 中缺少凭据")
    # 外部环境变量引用 → 原样显示
    store.set_key("deepseek", "$MY_ENV")
    assert store.key_status("deepseek") == (True, "已配置（环境变量 $MY_ENV）")
    # 命令间接引用
    store.set_key("deepseek", "!echo hi")
    assert store.key_status("deepseek") == (True, "已配置（命令间接引用）")


def test_set_key_rejects_keychain_readback_failure(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    store.ensure_initialized()

    class FailingStore:
        def set(self, provider, secret):
            pass

        def get(self, provider):
            return None  # 写后读不回来

        def delete(self, provider):
            pass

    store.keychain = FailingStore()
    with pytest.raises(RuntimeError):
        store.set_key("deepseek", "secret")


# ── keychain ─────────────────────────────────────────────────

class FakeNativeKeychain:
    def __init__(self, *, value=None, error=None):
        self.value = value
        self.error = error
        self.deleted = []

    def get(self, _service, _provider):
        if self.error:
            raise self.error
        return self.value

    def set(self, _service, _provider, secret):
        self.value = secret

    def delete(self, service, provider):
        self.deleted.append((service, provider))


def test_keychain_get_missing_returns_none() -> None:
    assert keychain.KeychainStore(backend=FakeNativeKeychain()).get("p") is None


def test_keychain_get_empty_output_returns_none() -> None:
    assert keychain.KeychainStore(backend=FakeNativeKeychain(value=None)).get("p") is None


def test_keychain_get_other_error_raises() -> None:
    with pytest.raises(keychain.KeychainError):
        keychain.KeychainStore(
            backend=FakeNativeKeychain(error=keychain.KeychainError("无法读取 macOS Keychain"))
        ).get("p")


def test_keychain_delete_tolerates_missing() -> None:
    backend = FakeNativeKeychain()
    store = keychain.KeychainStore("test-service", backend=backend)
    store.delete("p")
    assert backend.deleted == [("test-service", "p")]


def test_keychain_set_empty_secret_rejected(monkeypatch) -> None:
    with pytest.raises(ValueError):
        keychain.KeychainStore().set("p", "")
    with pytest.raises(ValueError):
        keychain.MemoryCredentialStore().set("p", "")


def test_credential_env_name_normalization() -> None:
    assert keychain.credential_env_name("deepseek") == "HAOCHEN_DEEPSEEK_API_KEY"
    assert keychain.credential_env_name("My Provider-2") == "HAOCHEN_MY_PROVIDER_2_API_KEY"
    with pytest.raises(ValueError):
        keychain.credential_env_name("///")


def test_export_ignores_broken_auth(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text("{not json", encoding="utf-8")
    env: dict[str, str] = {}
    keychain.export_keychain_credentials(auth, env, keychain.MemoryCredentialStore())
    assert env == {}
    auth.write_text("[1,2]", encoding="utf-8")
    keychain.export_keychain_credentials(auth, env, keychain.MemoryCredentialStore())
    assert env == {}


# ── app_tracking ─────────────────────────────────────────────

def test_visual_intent_word_boundaries(tmp_path: Path) -> None:
    home = tmp_path / "home"
    for word in ("看图", "帅", "image", "照片", "识别"):
        tracking.write_last_user_text(home, f"请{word}一下")
        assert json.loads((home / "last-user-intent.json").read_text(encoding="utf-8"))["visual"] is True
    for text in ("帮我写段代码", "总结一下这个文件"):
        tracking.write_last_user_text(home, text)
        assert json.loads((home / "last-user-intent.json").read_text(encoding="utf-8"))["visual"] is False


def test_read_last_user_app_roundtrip(tmp_path: Path) -> None:
    home = tmp_path / "home"
    assert tracking.read_last_user_app(home) == 0
    tracking.write_last_user_app(home)
    # 无 NSWorkspace 环境（headless Linux）会静默失败；macOS 上会写入 pid
    if (home / "last-user-app.txt").exists():
        assert tracking.read_last_user_app(home) > 0


def test_write_failures_are_silent(tmp_path: Path, monkeypatch) -> None:
    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(tracking, "atomic_write_private", boom)
    tracking.write_last_user_text(tmp_path / "h", "看图")  # 不抛
    tracking.write_last_user_app(tmp_path / "h")  # 不抛
    monkeypatch.setattr(tracking, "ensure_private_directory", boom)
    tracking.install_tracker(tmp_path / "h")  # 不抛


# ── engine_client ────────────────────────────────────────────

def test_send_when_not_alive_raises() -> None:
    client = engine_module.EngineClient(mock=True, home=Path("/tmp/haochen-x"))
    with pytest.raises(RuntimeError):
        client.prompt("hi")


def test_spawn_argv_env_and_directories(tmp_path: Path) -> None:
    home = tmp_path / "home"
    argv, env, cwd = engine_module.spawn_argv(Path("/bin/engine"), Path("/ext.ts"), home)
    assert argv[:2] == ["/bin/engine", "--mode"]
    assert env["PI_CODING_AGENT_DIR"] == str(home / "agent")
    assert env["HAOCHEN_PET"] == "1"
    assert env["HAOCHEN_HOME"] == str(home)
    assert "--session-dir" in argv
    assert cwd == home / "pi-home"
    for name in ("agent", "pi-home", "pi-sessions", "logs"):
        assert (home / name).is_dir()


def test_spawn_argv_without_ext(tmp_path: Path) -> None:
    argv, _env, _cwd = engine_module.spawn_argv(Path("/bin/engine"), None, tmp_path / "h")
    assert "-e" not in argv


def test_haochen_home_env_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HAOCHEN_HOME", str(tmp_path))
    assert engine_module.haochen_home() == tmp_path


def test_stop_when_never_started_is_noop() -> None:
    client = engine_module.EngineClient(mock=True, home=Path("/tmp/haochen-y"))
    client.stop()
    assert client.wait_stopped(timeout=0.1) is True


def test_diagnostic_log_rotates_at_threshold(tmp_path: Path) -> None:
    home = tmp_path / "home"
    log = home / "logs" / "engine.log"
    log.parent.mkdir(parents=True)
    log.write_bytes(b"x" * 1_000_000)
    client = engine_module.EngineClient(mock=True, home=home)
    client._record_diagnostic("stderr", "line")
    assert log.stat().st_size < 1_000_000
    assert (home / "logs" / "engine.log.1").exists()


def test_list_sessions_skips_unreadable(tmp_path: Path) -> None:
    home = tmp_path / "home"
    sessions = home / "pi-sessions"
    sessions.mkdir(parents=True)
    (sessions / "good.jsonl").write_text(
        '{"type":"session","id":"g","timestamp":"2026-01-02"}\n'
        '{"type":"message","message":{"role":"user","content":[{"type":"text","text":"你好世界的问题"}]}}\n',
        encoding="utf-8",
    )
    (sessions / "bad.jsonl").write_text("{not json", encoding="utf-8")
    result = engine_module.list_sessions(home)
    assert len(result) == 1
    assert result[0]["id"] == "g"
    assert result[0]["preview"] == "你好世界的问题"
