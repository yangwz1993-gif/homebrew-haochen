from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
keychain = importlib.import_module("haochen_app.keychain")
validation = importlib.import_module("haochen_app.key_validation")
config_module = importlib.import_module("haochen_app.settings.config_store")
engine_module = importlib.import_module("haochen_app.engine_client")


def test_keychain_write_uses_native_backend_without_process_arguments() -> None:
    calls = []

    class Backend:
        def set(self, service, provider, secret):
            calls.append((service, provider, secret))

        def get(self, _service, _provider):
            return None

        def delete(self, _service, _provider):
            pass

    secret = "private-test-credential"
    keychain.KeychainStore("test-service", backend=Backend()).set("deepseek", secret)

    assert calls == [("test-service", "deepseek", secret)]


def test_keychain_service_can_be_isolated_for_development(monkeypatch) -> None:
    monkeypatch.setenv("HAOCHEN_KEYCHAIN_SERVICE", "com.haochen.app.development.api-key")

    assert keychain.KeychainStore().service == "com.haochen.app.development.api-key"
    assert keychain.KeychainStore("explicit.service").service == "explicit.service"


def test_keychain_errors_never_include_secret() -> None:
    class Backend:
        def set(self, _service, _provider, _secret):
            raise keychain.KeychainError("无法写入 macOS Keychain")

    with pytest.raises(keychain.KeychainError) as error:
        keychain.KeychainStore(backend=Backend()).set("deepseek", "private-test-credential")
    assert "private-test-credential" not in str(error.value)


def test_export_resolves_only_expected_haochen_reference(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps(
            {
                "deepseek": {"type": "api_key", "key": "$HAOCHEN_DEEPSEEK_API_KEY"},
                "other": {"type": "api_key", "key": "$UNTRUSTED_ENV"},
            }
        ),
        encoding="utf-8",
    )
    store = keychain.MemoryCredentialStore()
    store.set("deepseek", "secret-one")
    store.set("other", "secret-two")
    env: dict[str, str] = {}

    keychain.export_keychain_credentials(auth, env, store)

    assert env == {"HAOCHEN_DEEPSEEK_API_KEY": "secret-one"}


def test_config_migrates_plaintext_only_after_keychain_readback(tmp_path: Path) -> None:
    home = tmp_path / "home"
    credentials = keychain.MemoryCredentialStore()
    store = config_module.ConfigStore(home, keychain=credentials)
    store.ensure_initialized()
    auth_path = home / "agent" / "auth.json"
    auth_path.write_text(
        json.dumps({"deepseek": {"type": "api_key", "key": "legacy-private-value"}}),
        encoding="utf-8",
    )

    store.ensure_initialized()

    assert credentials.get("deepseek") == "legacy-private-value"
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    assert auth["deepseek"]["key"] == "$HAOCHEN_DEEPSEEK_API_KEY"
    assert "legacy-private-value" not in auth_path.read_text(encoding="utf-8")


def test_config_can_reference_existing_key_without_writing_secret(tmp_path: Path) -> None:
    credentials = keychain.MemoryCredentialStore()
    credentials.set("deepseek", "runtime-private-value")
    store = config_module.ConfigStore(tmp_path / "home", keychain=credentials)
    store.ensure_initialized()

    assert store.reference_existing_key("deepseek") is True
    auth_text = (store.agent_dir / "auth.json").read_text(encoding="utf-8")
    assert "$HAOCHEN_DEEPSEEK_API_KEY" in auth_text
    assert "runtime-private-value" not in auth_text


def test_engine_env_resolves_keychain_reference_without_changing_disk(tmp_path: Path) -> None:
    home = tmp_path / "home"
    credentials = keychain.MemoryCredentialStore()
    store = config_module.ConfigStore(home, keychain=credentials)
    store.ensure_initialized()
    store.set_key("deepseek", "runtime-private-value")

    _argv, env, _cwd = engine_module.spawn_argv(Path("/bin/true"), None, home, credentials)

    assert env["HAOCHEN_DEEPSEEK_API_KEY"] == "runtime-private-value"
    assert "runtime-private-value" not in (home / "agent" / "auth.json").read_text(encoding="utf-8")


def test_validation_uses_fixed_endpoint_and_bearer_header(monkeypatch) -> None:
    seen = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["authorization"] = request.get_header("Authorization")
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr(validation.urllib.request, "urlopen", urlopen)
    result = validation.validate_api_key("deepseek", "candidate-secret", timeout=3)

    assert result.ok
    assert seen == {
        "url": "https://api.deepseek.com/models",
        "authorization": "Bearer candidate-secret",
        "timeout": 3,
    }


def test_validation_failure_is_actionable_and_does_not_expose_key(monkeypatch) -> None:
    key = "candidate-secret"
    monkeypatch.setattr(
        validation.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            validation.urllib.error.HTTPError("url", 401, "unauthorized", {}, None)
        ),
    )
    result = validation.validate_api_key("deepseek", key)
    assert not result.ok
    assert "无效" in result.message
    assert key not in result.message


def test_config_migration_failure_does_not_crash_startup(tmp_path: Path, monkeypatch) -> None:
    """启动时 Keychain 不可用：保留明文条目，不抛异常（0.2.0 启动崩溃回归）。"""
    config_module = importlib.import_module("haochen_app.settings.config_store")

    class BrokenStore:
        def set(self, _provider, _secret):
            raise keychain.KeychainError("无法写入 macOS Keychain")

        def get(self, _provider):
            return None

        def delete(self, _provider):
            pass

    home = tmp_path / "home"
    store = config_module.ConfigStore(home, keychain=BrokenStore())
    store.ensure_initialized()
    auth_path = home / "agent" / "auth.json"
    auth_path.write_text(
        json.dumps({"deepseek": {"type": "api_key", "key": "legacy-plaintext-value"}}),
        encoding="utf-8",
    )

    store.ensure_initialized()  # 不得抛出

    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    assert auth["deepseek"]["key"] == "legacy-plaintext-value"  # 明文保留，待下次重试
