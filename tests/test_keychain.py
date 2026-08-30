from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
keychain = importlib.import_module("haochen_app.keychain")
validation = importlib.import_module("haochen_app.key_validation")
config_module = importlib.import_module("haochen_app.settings.config_store")
engine_module = importlib.import_module("haochen_app.engine_client")


def test_keychain_write_passes_secret_on_stdin_not_argv(monkeypatch) -> None:
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(keychain.subprocess, "run", run)
    secret = "private-test-credential"
    keychain.KeychainStore().set("deepseek", secret)

    command, kwargs = calls[0]
    assert secret not in command
    assert command[-1] == "-w"
    assert kwargs["input"] == secret + "\n"


def test_keychain_errors_never_include_secret(monkeypatch) -> None:
    monkeypatch.setattr(
        keychain.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr="private-test-credential"),
    )
    with pytest.raises(keychain.KeychainError) as error:
        keychain.KeychainStore().set("deepseek", "private-test-credential")
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
