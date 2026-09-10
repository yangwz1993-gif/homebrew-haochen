from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
keychain = importlib.import_module("haochen_app.keychain")
config_module = importlib.import_module("haochen_app.settings.config_store")
onboarding = importlib.import_module("haochen_app.onboarding")
profile = importlib.import_module("haochen_app.pet.profile")
validation = importlib.import_module("haochen_app.key_validation")


def make_store(tmp_path: Path):
    credentials = keychain.MemoryCredentialStore()
    store = config_module.ConfigStore(tmp_path / "home", keychain=credentials)
    store.ensure_initialized()
    return store, credentials


def test_failed_validation_does_not_replace_existing_key(qtbot, tmp_path: Path) -> None:
    store, credentials = make_store(tmp_path)
    store.set_key("deepseek", "old-private-value")
    wizard = onboarding.OnboardingWizard(
        store,
        verifier=lambda _provider, _key: validation.ValidationResult(False, "无效"),
    )
    qtbot.addWidget(wizard)
    page = wizard.key_page
    page.key_edit.setText("new-private-value")
    page.verify_button.click()
    qtbot.waitUntil(lambda: page.verify_button.isEnabled(), timeout=1000)

    assert credentials.get("deepseek") == "old-private-value"
    assert "原 Key 未更改" in page.status.text()


def test_successful_validation_saves_keychain_reference_only(qtbot, tmp_path: Path) -> None:
    store, credentials = make_store(tmp_path)
    wizard = onboarding.OnboardingWizard(
        store,
        verifier=lambda _provider, _key: validation.ValidationResult(True, "连接成功"),
    )
    qtbot.addWidget(wizard)
    page = wizard.key_page
    page.key_edit.setText("new-private-value")
    page.verify_button.click()
    qtbot.waitUntil(page.isComplete, timeout=1000)

    assert credentials.get("deepseek") == "new-private-value"
    auth_text = (store.agent_dir / "auth.json").read_text(encoding="utf-8")
    assert "new-private-value" not in auth_text
    assert json.loads(auth_text)["deepseek"]["key"] == "$HAOCHEN_DEEPSEEK_API_KEY"


def test_permissions_are_requested_only_by_separate_user_actions(qtbot, tmp_path: Path) -> None:
    store, _credentials = make_store(tmp_path)
    wizard = onboarding.OnboardingWizard(store)
    qtbot.addWidget(wizard)
    requests: list[str] = []
    wizard.permission_requested.connect(requests.append)
    buttons = wizard.permission_page.findChildren(onboarding.QPushButton)

    assert requests == []
    buttons[0].click()
    assert requests == ["accessibility"]
    buttons[1].click()
    assert requests == ["accessibility", "screen"]


def test_interrupted_wizard_resumes_and_completion_persists(qtbot, tmp_path: Path) -> None:
    store, _credentials = make_store(tmp_path)
    store.set_key("deepseek", "existing-private-value")
    wizard = onboarding.OnboardingWizard(store)
    qtbot.addWidget(wizard)
    wizard.show()
    wizard.next()
    wizard.next()
    wizard.next()
    assert wizard.currentId() == onboarding.PERMISSIONS_PAGE
    wizard.close()

    resumed = onboarding.OnboardingWizard(store)
    qtbot.addWidget(resumed)
    resumed.show()
    assert resumed.currentId() == onboarding.PERMISSIONS_PAGE
    resumed.back()
    assert resumed.currentId() == onboarding.KEY_PAGE
    resumed.next()
    assert resumed.currentId() == onboarding.PERMISSIONS_PAGE
    resumed.next()
    prompts: list[str] = []
    resumed.trial_requested.connect(prompts.append)
    resumed.accept()

    state = onboarding.OnboardingState(store.home)
    assert state.completed
    assert prompts == ["你好，请用一句话介绍你能帮我做什么"]


def test_resume_cannot_skip_profile_or_missing_key(qtbot, tmp_path: Path) -> None:
    store, _credentials = make_store(tmp_path)
    state = onboarding.OnboardingState(store.home)
    state.page = onboarding.TRIAL_PAGE
    state.save()

    wizard = onboarding.OnboardingWizard(store)
    qtbot.addWidget(wizard)
    wizard.show()

    assert wizard.startId() == onboarding.WELCOME_PAGE
    assert wizard.currentId() == onboarding.PROFILE_PAGE
    assert not wizard.key_page.isComplete()


def test_custom_model_resume_keeps_verified_page_and_prefills_fields(qtbot, tmp_path: Path) -> None:
    store, _credentials = make_store(tmp_path)
    profile.save_user_name("", source="onboarding", home=store.home)
    store.upsert_custom_model(
        base_url="http://127.0.0.1:18766/v1",
        model_id="qa-local-r02",
        model_name="QA Local",
        key="non-sensitive-test-key",
    )
    state = onboarding.OnboardingState(store.home)
    state.page = onboarding.PERMISSIONS_PAGE
    state.save()

    wizard = onboarding.OnboardingWizard(store)
    qtbot.addWidget(wizard)
    wizard.show()

    assert wizard.startId() == onboarding.WELCOME_PAGE
    assert wizard.currentId() == onboarding.PERMISSIONS_PAGE
    assert wizard.key_page.mode_combo.currentData() == "custom"
    assert wizard.key_page.url_edit.text() == "http://127.0.0.1:18766/v1"
    assert wizard.key_page.model_id_edit.text() == "qa-local-r02"
    assert wizard.key_page.isComplete()


def test_resumed_trial_can_go_back_to_hydrated_custom_model(qtbot, tmp_path: Path) -> None:
    store, _credentials = make_store(tmp_path)
    profile.save_user_name("", source="onboarding", home=store.home)
    store.upsert_custom_model(
        base_url="http://127.0.0.1:18766/v1",
        model_id="qa-local-r03",
        model_name="QA Local Round 03",
        key="non-sensitive-test-key",
    )
    state = onboarding.OnboardingState(store.home)
    state.page = onboarding.TRIAL_PAGE
    state.save()

    wizard = onboarding.OnboardingWizard(store)
    qtbot.addWidget(wizard)
    wizard.show()

    assert wizard.currentId() == onboarding.TRIAL_PAGE
    assert wizard.button(onboarding.QWizard.WizardButton.BackButton).isEnabled()
    wizard.back()
    wizard.back()
    assert wizard.currentId() == onboarding.KEY_PAGE
    assert wizard.key_page.mode_combo.currentData() == "custom"
    assert wizard.key_page.url_edit.text() == "http://127.0.0.1:18766/v1"
    assert wizard.key_page.model_id_edit.text() == "qa-local-r03"
    assert wizard.key_page.model_name_edit.text() == "QA Local Round 03"
    assert wizard.key_page.isComplete()


def test_editing_prefilled_custom_model_requires_revalidation(qtbot, tmp_path: Path) -> None:
    store, _credentials = make_store(tmp_path)
    store.upsert_custom_model(
        base_url="http://127.0.0.1:18766/v1",
        model_id="qa-local-r02",
        model_name="QA Local",
        key="non-sensitive-test-key",
    )
    page = onboarding.KeyPage(store, lambda *_args: validation.ValidationResult(True, "ok"))
    qtbot.addWidget(page)
    assert page.isComplete()

    page.model_id_edit.setFocus()
    qtbot.keyClicks(page.model_id_edit, "-changed")

    assert not page.isComplete()
    assert "重新验证" in page.status.text()
