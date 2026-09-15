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
    # 0.6.2-beta.3：默认档已切为 codewiz 内网；本用例测外网 DeepSeek 的 Key 流程，显式选档。
    page.mode_combo.setCurrentIndex(page.mode_combo.findData("deepseek"))
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
    # 默认档已切为 codewiz 内网；本用例测外网 DeepSeek 的 Keychain 引用，显式选档。
    page.mode_combo.setCurrentIndex(page.mode_combo.findData("deepseek"))
    page.key_edit.setText("new-private-value")
    page.verify_button.click()
    qtbot.waitUntil(page.isComplete, timeout=1000)

    assert credentials.get("deepseek") == "new-private-value"
    auth_text = (store.agent_dir / "auth.json").read_text(encoding="utf-8")
    assert "new-private-value" not in auth_text
    assert json.loads(auth_text)["deepseek"]["key"] == "$HAOCHEN_DEEPSEEK_API_KEY"


def _select_codewiz(page, model_prefix: str = "codewiz:"):
    idx = page.mode_combo.findData("codewiz")
    assert idx >= 0, "内网应作为单独一档「CodeWiz 内网模型」出现"
    page.mode_combo.setCurrentIndex(idx)
    midx = next((i for i in range(page.model_combo.count())
                 if str(page.model_combo.itemData(i)).startswith(model_prefix)), -1)
    assert midx >= 0, f"内网模型下拉应含 {model_prefix}*"
    page.model_combo.setCurrentIndex(midx)
    return page


def test_codewiz_single_internal_entry_and_all_models_listed(qtbot, tmp_path: Path) -> None:
    # 下拉应只有一档「内网」（不是每个内网 provider 一行）；模型下拉里列出所有内网模型。
    store, _credentials = make_store(tmp_path)
    wizard = onboarding.OnboardingWizard(store)
    qtbot.addWidget(wizard)
    page = wizard.key_page
    modes = [page.mode_combo.itemData(i) for i in range(page.mode_combo.count())]
    assert modes.count("codewiz") == 1
    page.mode_combo.setCurrentIndex(page.mode_combo.findData("codewiz"))
    model_ids = {str(page.model_combo.itemData(i)) for i in range(page.model_combo.count())}
    assert "codewiz:kimi-k3" in model_ids and "codewiz-gemini:gemini-3.7-flash" in model_ids


def test_codewiz_fill_key_and_validate_writes_credentials(qtbot, tmp_path: Path, monkeypatch) -> None:
    # 目标闭环：选内网 → 选模型 → 填邮箱+Key → 校验（自动带已登录 SSO）→ 通过 → 写好 codewiz.json。
    codewiz = importlib.import_module("haochen_app.codewiz")
    monkeypatch.setattr(codewiz, "validation_headers", lambda _email: {"Cookie": "sso", "X-Adapter-Source": "x"})
    monkeypatch.setattr(onboarding, "validate_custom_model",
                        lambda *a, **k: validation.ValidationResult(True, "ok"))
    store, _credentials = make_store(tmp_path)
    wizard = onboarding.OnboardingWizard(store, verifier=lambda _p, _k: validation.ValidationResult(False, "no"))
    qtbot.addWidget(wizard)
    page = _select_codewiz(wizard.key_page)
    assert not page.email_edit.isHidden() and not page.key_edit.isHidden() and not page.model_combo.isHidden()
    assert not page.isComplete()  # 还没填、没校验
    page.email_edit.setText("me@xiaohongshu.com")
    page.key_edit.setText("QST-abc123")
    page.verify_button.click()
    qtbot.waitUntil(page.isComplete, timeout=1000)
    data = json.loads((store.home / "codewiz.json").read_text(encoding="utf-8"))
    assert data == {"apiKey": "QST-abc123", "email": "me@xiaohongshu.com"}
    # 0.6.2-beta.3：codewiz 默认模型切为 deepseek-v4-flash-0731-baidu（实测最快档）
    assert store.default_model() == ("codewiz", "deepseek-v4-flash-0731-baidu")


def test_codewiz_gemini_selection_validates_via_openai_and_routes(qtbot, tmp_path: Path, monkeypatch) -> None:
    # 选 Gemini（Google 协议）时，校验走 OpenAI 探测端点（避免 404），但默认模型落到 Gemini。
    codewiz = importlib.import_module("haochen_app.codewiz")
    monkeypatch.setattr(codewiz, "validation_headers", lambda _email: {"Cookie": "sso"})
    probed: list = []

    def fake_validate(base_url, model_id, key, **kwargs):
        probed.append((base_url, model_id))
        return validation.ValidationResult(True, "ok")

    monkeypatch.setattr(onboarding, "validate_custom_model", fake_validate)
    store, _credentials = make_store(tmp_path)
    wizard = onboarding.OnboardingWizard(store, verifier=lambda _p, _k: validation.ValidationResult(False, "no"))
    qtbot.addWidget(wizard)
    page = _select_codewiz(wizard.key_page, model_prefix="codewiz-gemini:")
    page.email_edit.setText("me@xiaohongshu.com")
    page.key_edit.setText("QST-abc123")
    page.verify_button.click()
    qtbot.waitUntil(page.isComplete, timeout=1000)
    # 校验用的是 openai 端点（kimi），不是 gemini 端点。
    assert probed and "openai/v1" in probed[0][0] and "gemini" not in probed[0][0]
    # 但默认模型落到用户选的 Gemini。
    assert store.default_model() == ("codewiz-gemini", "gemini-3.7-flash")


def test_codewiz_without_login_asks_to_login_and_stays_incomplete(qtbot, tmp_path: Path, monkeypatch) -> None:
    codewiz = importlib.import_module("haochen_app.codewiz")
    monkeypatch.setattr(codewiz, "validation_headers", lambda _email: None)  # 未登录 codewiz
    store, _credentials = make_store(tmp_path)
    wizard = onboarding.OnboardingWizard(store, verifier=lambda _p, _k: validation.ValidationResult(False, "no"))
    qtbot.addWidget(wizard)
    page = _select_codewiz(wizard.key_page)
    page.email_edit.setText("me@xiaohongshu.com")
    page.key_edit.setText("QST-abc123")
    page.verify_button.click()
    assert "登录" in page.status.text()
    assert not page.isComplete()
    assert not (store.home / "codewiz.json").exists()


def test_permissions_are_requested_only_by_separate_user_actions(qtbot, tmp_path: Path, monkeypatch) -> None:
    store, _credentials = make_store(tmp_path)
    wizard = onboarding.OnboardingWizard(store)
    qtbot.addWidget(wizard)
    requests: list[str] = []
    wizard.permission_requested.connect(requests.append)
    monkeypatch.setattr("haochen_app.permissions.permission_status", lambda _: False)
    wizard.permission_page.refresh_status()
    buttons = wizard.permission_page.findChildren(onboarding.QPushButton)

    assert requests == []
    buttons[0].click()
    assert requests == ["accessibility"]
    buttons[1].click()
    assert requests == ["accessibility", "screen"]


def test_interrupted_wizard_resumes_and_completion_persists(qtbot, tmp_path: Path) -> None:
    store, _credentials = make_store(tmp_path)
    store.set_key("deepseek", "existing-private-value")
    # 0.6.2-beta.3：默认档是 codewiz 内网；本用例只有 deepseek Key，把默认模型设为
    # deepseek 使 Key 页完整（否则恢复逻辑会按 codewiz 未配置把人留在 Key 页——这也是
    # 期望行为，但本用例测的是「页面位置恢复」）。
    store.set_default_model("deepseek", "deepseek-v4-flash")
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


def test_upgrade_with_missing_custom_key_keeps_model_fields_and_explains_reentry(
    qtbot, tmp_path: Path
) -> None:
    store, credentials = make_store(tmp_path)
    store.upsert_custom_model(
        base_url="http://127.0.0.1:18766/v1",
        model_id="qa-local-upgrade",
        model_name="QA Local Upgrade",
        key="non-sensitive-test-key",
    )
    provider_id, _model_id = store.default_model()
    credentials.delete(provider_id)

    wizard = onboarding.OnboardingWizard(store, requires_key_reentry=True)
    qtbot.addWidget(wizard)

    assert wizard.key_page.mode_combo.currentData() == "custom"
    assert wizard.key_page.url_edit.text() == "http://127.0.0.1:18766/v1"
    assert wizard.key_page.model_id_edit.text() == "qa-local-upgrade"
    assert wizard.key_page.model_name_edit.text() == "QA Local Upgrade"
    assert not wizard.key_page.isComplete()
    assert "连接模型" in wizard.key_page.status.text()


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
