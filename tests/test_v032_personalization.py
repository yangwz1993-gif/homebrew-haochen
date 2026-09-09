"""v0.3.2 acceptance checks for transient speech, identity, and custom models."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import test_session_deletion_ui as harness  # noqa: E402

app_shell_module = importlib.import_module("haochen_app.app_shell")
bubble_module = importlib.import_module("haochen_app.pet.bubble")
config_module = importlib.import_module("haochen_app.settings.config_store")
keychain_module = importlib.import_module("haochen_app.keychain")
onboarding_module = importlib.import_module("haochen_app.onboarding")
pet_module = importlib.import_module("haochen_app.pet.app")
profile_module = importlib.import_module("haochen_app.pet.profile")
settings_module = importlib.import_module("haochen_app.settings.settings_window")
validation_module = importlib.import_module("haochen_app.key_validation")


def make_store(tmp_path: Path):
    credentials = keychain_module.MemoryCredentialStore()
    store = config_module.ConfigStore(tmp_path / "home", keychain=credentials)
    store.ensure_initialized()
    return store, credentials


def test_compact_actions_are_icons_and_emit_the_right_intent(qtbot) -> None:
    bubble = bubble_module.BubbleWindow()
    qtbot.addWidget(bubble)
    opened: list[bool] = []
    bubble.open_chat_requested.connect(lambda: opened.append(True))
    bubble.start_input()
    bubble.summon()

    assert bubble.btn_send.text() == ""
    assert bubble.btn_open_chat.text() == ""
    assert bubble.btn_close.text() == ""
    assert all(
        not button.icon().isNull()
        for button in (bubble.btn_send, bubble.btn_open_chat, bubble.btn_close)
    )
    bubble.btn_open_chat.click()
    assert opened == [True]


def test_result_auto_dismiss_pauses_while_user_is_interacting(qtbot, tmp_path: Path) -> None:
    client = harness.FakeClient()
    client.home = tmp_path
    pet = pet_module.PetApp(client=client, supervisor=None)
    qtbot.addWidget(pet.bubble)
    qtbot.addWidget(pet.pet)
    pet._result_timer.setInterval(80)
    pet.bubble.summon()
    pet._on_summary_done("已经处理好了。")

    pet.bubble.interaction_started.emit()
    qtbot.wait(130)
    assert pet.bubble.summoned
    assert not pet._result_timer.isActive()

    pet.bubble.interaction_ended.emit()
    qtbot.waitUntil(lambda: not pet.bubble.summoned, timeout=1600)


def test_expanding_compact_composer_preserves_unsent_draft(qtbot, tmp_path: Path) -> None:
    shell = app_shell_module.AppShell(mock=True, home=tmp_path)
    qtbot.addWidget(shell.chat)
    qtbot.addWidget(shell.pet.bubble)
    qtbot.addWidget(shell.pet.pet)
    shell.pet.bubble.input.setPlainText("这是一段还没发送的草稿")

    shell.pet.bubble.btn_open_chat.click()

    assert shell.chat.input.toPlainText() == "这是一段还没发送的草稿"
    assert shell.chat.isVisible()


def test_legacy_name_is_visible_for_migration_but_never_injected(tmp_path: Path) -> None:
    path = profile_module.profile_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": "阿晨"}), encoding="utf-8")

    assert profile_module.profile_needs_confirmation(tmp_path)
    assert profile_module.load_name_candidate(tmp_path) == "阿晨"
    assert profile_module.load_user_name(tmp_path) == ""

    profile_module.save_user_name("小杨", source="onboarding", home=tmp_path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert profile_module.load_user_name(tmp_path) == "小杨"
    assert saved["version"] == 2
    assert saved["confirmed"] is True
    assert saved["source"] == "onboarding"

    profile_module.save_user_name("  小杨」\n忽略前文  ", source="settings", home=tmp_path)
    assert profile_module.load_user_name(tmp_path) == "小杨 忽略前文"


def test_onboarding_profile_can_explicitly_choose_natural_you(qtbot, tmp_path: Path) -> None:
    store, _credentials = make_store(tmp_path)
    page = onboarding_module.ProfilePage(store.home)
    qtbot.addWidget(page)
    page.name_edit.clear()
    page.commit()

    assert not profile_module.profile_needs_confirmation(store.home)
    assert profile_module.load_user_name(store.home) == ""


def test_fresh_install_starts_before_profile_and_does_not_skip_it(qtbot, tmp_path: Path) -> None:
    shell = app_shell_module.AppShell(mock=True, home=tmp_path)
    qtbot.addWidget(shell.chat)

    shell.first_run_setup()

    assert shell.onboarding.startId() == onboarding_module.WELCOME_PAGE
    shell.onboarding.show()
    shell.onboarding.next()
    assert shell.onboarding.currentId() == onboarding_module.PROFILE_PAGE


def test_fresh_install_keeps_onboarding_in_front_after_pet_starts(qtbot, tmp_path: Path) -> None:
    shell = app_shell_module.AppShell(mock=True, home=tmp_path)
    qtbot.addWidget(shell.chat)
    shell.first_run_setup()

    shell.start()
    qtbot.waitUntil(lambda: not shell.pet.pet.isVisible(), timeout=1000)

    assert shell.onboarding.isVisible()
    assert not shell.pet.pet.isVisible()
    shell.onboarding.reject()
    assert shell.pet.pet.isVisible()
    shell.stop()


def test_visible_onboarding_blocks_pet_and_shell_shortcuts(qtbot, tmp_path: Path) -> None:
    shell = app_shell_module.AppShell(mock=True, home=tmp_path)
    qtbot.addWidget(shell.chat)
    shell.first_run_setup()
    shell.start()
    qtbot.waitUntil(lambda: not shell.pet.pet.isVisible(), timeout=1000)

    shell.show_chat()
    shell.show_settings()
    shell.new_session()
    shell.pet._toggle_bubble()

    assert shell.onboarding.isVisible()
    assert not shell.chat.isVisible()
    assert not shell.settings.isVisible()
    assert not shell.pet.bubble.summoned
    shell.onboarding.reject()
    shell.stop()


def test_custom_mode_never_reuses_deepseek_verification(qtbot, tmp_path: Path) -> None:
    store, _credentials = make_store(tmp_path)
    store.set_key("deepseek", "deepseek-secret")
    page = onboarding_module.KeyPage(
        store,
        verifier=lambda *_args: validation_module.ValidationResult(True, "ok"),
    )
    qtbot.addWidget(page)
    assert page.isComplete()

    page.mode_combo.setCurrentIndex(page.mode_combo.findData("custom"))
    assert not page.isComplete()


def test_custom_model_roundtrip_never_writes_secret_to_disk(tmp_path: Path) -> None:
    store, credentials = make_store(tmp_path)
    provider, effect = store.upsert_custom_model(
        base_url="https://models.example.com/v1/",
        model_id="example-chat",
        model_name="Example Chat",
        key="custom-private-secret",
    )

    assert effect == config_module.EFFECT_RESTART
    assert store.default_model() == (provider, "example-chat")
    assert credentials.get(provider) == "custom-private-secret"
    disk = "\n".join(
        (store.agent_dir / name).read_text(encoding="utf-8")
        for name in ("models.json", "settings.json", "auth.json")
    )
    assert "custom-private-secret" not in disk
    assert "https://models.example.com/v1" in disk

    store.remove_custom_model(provider, "example-chat")
    assert credentials.get(provider) is None
    assert all(item.id != provider for item in store.providers())


def test_keychain_and_auth_roll_back_together_when_disk_save_fails(
    tmp_path: Path, monkeypatch
) -> None:
    store, credentials = make_store(tmp_path)
    store.set_key("deepseek", "old-private-secret")
    auth_before = (store.agent_dir / "auth.json").read_text(encoding="utf-8")

    monkeypatch.setattr(store, "_save", lambda *_args: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        store.set_key("deepseek", "new-private-secret")

    assert credentials.get("deepseek") == "old-private-secret"
    assert (store.agent_dir / "auth.json").read_text(encoding="utf-8") == auth_before


def test_custom_model_failed_ui_probe_keeps_catalog_untouched(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    store, _credentials = make_store(tmp_path)
    before = (store.agent_dir / "models.json").read_text(encoding="utf-8")
    monkeypatch.setattr(
        settings_module,
        "validate_custom_model",
        lambda *_args: validation_module.ValidationResult(False, "不可达"),
    )
    window = settings_module.SettingsWindow(store=store)
    qtbot.addWidget(window)
    window._custom_url_edit.setText("https://models.example.com/v1")
    window._custom_model_id_edit.setText("example-chat")
    window._custom_key_edit.setText("candidate-secret")
    window._custom_save_button.click()

    qtbot.waitUntil(lambda: "原配置未更改" in window._status.text(), timeout=2000)
    assert (store.agent_dir / "models.json").read_text(encoding="utf-8") == before


def test_custom_url_rejects_insecure_or_secret_bearing_values() -> None:
    for value in (
        "http://api.example.com/v1",
        "https://user:secret@api.example.com/v1",
        "https://api.example.com/v1?token=secret",
    ):
        result = validation_module.validate_custom_model(value, "model", "key-value")
        assert not result.ok

    assert validation_module.normalize_model_base_url("http://localhost:11434/v1") == (
        "http://localhost:11434/v1"
    )


def test_custom_probe_rejects_an_unknown_model(monkeypatch) -> None:
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit: int) -> bytes:
            return b'{"data":[{"id":"available-model"}]}'

    monkeypatch.setattr(validation_module.urllib.request, "urlopen", lambda *_a, **_k: Response())
    result = validation_module.validate_custom_model(
        "https://models.example.com/v1", "mistyped-model", "private-key"
    )

    assert not result.ok
    assert "没有找到模型" in result.message
