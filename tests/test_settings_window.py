"""Settings window construction and interactions (model card rework, 0.6.2-beta.3).

交互模型（v4 demo 定稿）：
- 一张「模型」卡管完：供应商 / 模型 / API Key 三行联动，底部「应用切换」才写盘
- 浏览供应商/模型绝不写 settings.json（默认值不会被 UI 找不到模型给冲掉）
- codewiz 家族（codewiz + codewiz-gemini，同一把内网 Key）折叠为一个供应商项
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

from PyQt6.QtCore import Qt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

config_module = importlib.import_module("haochen_app.settings.config_store")
keychain = importlib.import_module("haochen_app.keychain")
validation = importlib.import_module("haochen_app.key_validation")
settings_module = importlib.import_module("haochen_app.settings.settings_window")
SettingsWindow = settings_module.SettingsWindow


def make_window(qtbot, tmp_path: Path):
    credentials = keychain.MemoryCredentialStore()
    store = config_module.ConfigStore(tmp_path, keychain=credentials)
    store.ensure_initialized()
    window = SettingsWindow(store=store)
    qtbot.addWidget(window)
    return window, store, credentials


def select_family(window, pid: str) -> int:
    """把供应商下拉切到包含 pid 的家族项（浏览不写盘）。"""
    combo = window._provider_combo
    for i in range(combo.count()):
        members = combo.itemData(i)
        if any(p.id == pid for p in members):
            combo.setCurrentIndex(i)
            return i
    raise AssertionError(f"family containing {pid} not found")


def select_model(window, pid: str, mid: str) -> None:
    combo = window._model_combo
    # 同生产代码：findData 对 tuple 不可靠，手动逐项匹配
    index = next(
        (i for i in range(combo.count()) if combo.itemData(i) == (pid, mid)), -1)
    assert index >= 0, f"model {(pid, mid)} not in combo"
    combo.setCurrentIndex(index)


def test_window_builds_all_cards(qtbot, tmp_path: Path) -> None:
    window, _store, _cred = make_window(qtbot, tmp_path)
    assert window.windowTitle() == "haochen 设置"
    labels = [label.text() for label in window.findChildren(settings_module.QLabel)]
    joined = " ".join(labels)
    assert "API Key" in joined
    assert "签名与权限" in joined


def test_escape_closes_settings_and_default_viewport_is_roomy(qtbot, tmp_path: Path) -> None:
    window, _store, _cred = make_window(qtbot, tmp_path)
    window.show()

    assert window.width() >= 600
    assert window.height() >= 700
    qtbot.keyClick(window, Qt.Key.Key_Escape)

    assert not window.isVisible()


def test_env_reference_key_area_is_readonly_managed(qtbot, tmp_path: Path) -> None:
    window, store, _cred = make_window(qtbot, tmp_path)
    store.set_key("deepseek", "$MY_EXTERNAL_VAR")
    window._build()
    select_family(window, "deepseek")

    assert window._key_edit.isReadOnly()
    assert window._key_edit.toolTip()
    assert window._key_edit_button.isHidden()


def test_stored_key_is_not_presented_as_runtime_validated(qtbot, tmp_path: Path) -> None:
    """Keychain 里"有值"只是已保存，绝不冒充"已验证连接"。"""
    window, store, credentials = make_window(qtbot, tmp_path)
    credentials.set("deepseek", "stored-secret")
    store.set_key("deepseek", "stored-secret")
    window._build()
    select_family(window, "deepseek")

    assert window._key_badge.text() == "已保存"
    assert window._key_badge.objectName() == "badgeOff"
    assert window._key_edit.text() != "stored-secret"  # 防窥占位，不回显真实 Key


def test_runtime_key_failure_overrides_storage_badge(qtbot, tmp_path: Path) -> None:
    window, store, credentials = make_window(qtbot, tmp_path)
    credentials.set("deepseek", "invalid-secret")
    store.set_key("deepseek", "invalid-secret")
    window._build()
    select_family(window, "deepseek")

    window.set_runtime_key_validation("deepseek", False, "API Key 无效或格式不正确")

    assert window._key_badge.text() == "当前凭据验证失败"
    assert window._key_badge.objectName() == "badgeErr"
    assert "API Key 无效" in window._status.text()


def test_key_save_flow_validate_fail_keeps_old(qtbot, tmp_path: Path, monkeypatch) -> None:
    window, store, credentials = make_window(qtbot, tmp_path)
    credentials.set("deepseek", "old-secret")
    store.set_key("deepseek", "old-secret")
    window._build()
    select_family(window, "deepseek")

    monkeypatch.setattr(
        settings_module, "validate_api_key",
        lambda provider, key: validation.ValidationResult(False, "无效"),
    )
    window._key_edit_button.click()  # 修改 → 编辑态，预填原 Key
    window._key_edit.setText("bad-candidate")
    window._key_save_button.click()

    qtbot.waitUntil(lambda: "原 Key 未更改" in window._status.text(), timeout=2000)
    assert credentials.get("deepseek") == "old-secret"


def test_key_save_flow_validate_success_saves(qtbot, tmp_path: Path, monkeypatch) -> None:
    window, store, credentials = make_window(qtbot, tmp_path)
    select_family(window, "deepseek")  # 未配置 → 直接可编辑 + 连接模型
    monkeypatch.setattr(
        settings_module, "validate_api_key",
        lambda provider, key: validation.ValidationResult(True, "连接成功"),
    )
    window._key_edit.setText("new-good-secret")
    window._key_save_button.click()

    qtbot.waitUntil(lambda: credentials.get("deepseek") == "new-good-secret", timeout=2000)
    qtbot.waitUntil(
        lambda: window._key_badge.text() == "已连接 · 安全存储", timeout=2000)


def test_empty_save_in_edit_mode_preserves_keychain_entry(qtbot, tmp_path: Path) -> None:
    """编辑态清空后点保存 → 明确提示并保留原 Key（空输入绝不解释为删除）。"""
    window, store, credentials = make_window(qtbot, tmp_path)
    credentials.set("deepseek", "existing")
    store.set_key("deepseek", "existing")
    window._build()
    select_family(window, "deepseek")

    window._key_edit_button.click()
    window._key_edit.setText("")
    window._key_save_button.click()

    assert "保留原 Key" in window._status.text()
    assert credentials.get("deepseek") == "existing"


def test_key_edit_mode_shows_save_and_cancel_then_restores(qtbot, tmp_path: Path) -> None:
    """点「修改」后出现独立的保存/取消；点「取消」回到防窥常态。"""
    window, store, credentials = make_window(qtbot, tmp_path)
    credentials.set("deepseek", "stored-secret")
    store.set_key("deepseek", "stored-secret")
    window._build()
    select_family(window, "deepseek")

    assert window._key_save_button.isHidden()
    window._key_edit_button.click()
    assert not window._key_save_button.isHidden()
    assert not window._key_cancel_button.isHidden()
    assert window._key_edit_button.isHidden()
    assert window._key_edit.text() == "stored-secret"  # 编辑态预填真实 Key

    window._key_cancel_button.click()
    assert window._key_save_button.isHidden()
    assert window._key_edit.isReadOnly()
    assert window._key_edit.text() != "stored-secret"  # 回到防窥占位


def test_corrupt_config_shows_recovery_card(qtbot, tmp_path: Path) -> None:
    credentials = keychain.MemoryCredentialStore()
    store = config_module.ConfigStore(tmp_path, keychain=credentials)
    store.ensure_initialized()
    (tmp_path / "agent" / "models.json").write_text("{broken", encoding="utf-8")
    window = SettingsWindow(store=store)
    qtbot.addWidget(window)
    buttons = [b.text() for b in window.findChildren(settings_module.QPushButton)]
    assert any("重置" in text for text in buttons)


def test_reset_card_restores_and_rebuilds(qtbot, tmp_path: Path) -> None:
    credentials = keychain.MemoryCredentialStore()
    store = config_module.ConfigStore(tmp_path, keychain=credentials)
    store.ensure_initialized()
    (tmp_path / "agent" / "models.json").write_text("{broken", encoding="utf-8")
    window = SettingsWindow(store=store)
    qtbot.addWidget(window)

    reset_button = next(
        (b for b in window.findChildren(settings_module.QPushButton) if "重置" in b.text()),
        None,
    )
    if reset_button is not None:
        reset_button.click()
        content = (tmp_path / "agent" / "models.json").read_text(encoding="utf-8")
        assert "deepseek" in content


def test_theme_and_signature_cards_render(qtbot, tmp_path: Path) -> None:
    window, _store, _cred = make_window(qtbot, tmp_path)
    combos = window.findChildren(settings_module.QComboBox)
    assert combos
    disabled = [c for c in combos if not c.isEnabled()]
    assert disabled
    labels = [label.text() for label in window.findChildren(settings_module.QLabel)]
    assert any("Developer ID" in text or "开发模式" in text for text in labels)


def test_provider_combo_marks_configuration_status(qtbot, tmp_path: Path) -> None:
    """供应商下拉直接标明可用性（已配置/未配置 Key），减少模型管理的迷路感。"""
    window, store, _cred = make_window(qtbot, tmp_path)
    store.set_key("deepseek", "test-only-key")
    window._build()
    combo = window._provider_combo
    texts = [combo.itemText(i) for i in range(combo.count())]
    deepseek_text = next(t for t in texts if t.startswith("deepseek"))
    assert "已配置" in deepseek_text
    # codewiz 是外部 $ENV 引用：没有 codewiz.json/SSO 会话时必须如实显示「未生效」，
    # 不得只因占位引用就标"已配置"。
    codewiz_text = next(t for t in texts if t.startswith("codewiz"))
    assert "需在向导连接内网" in codewiz_text, texts


def test_browsing_providers_never_rewrites_default(qtbot, tmp_path: Path) -> None:
    """③号事故回归：浏览供应商/模型只是看，默认值只能被「应用切换」改变。"""
    window, store, _cred = make_window(qtbot, tmp_path)
    before = store.default_model()
    assert before == ("codewiz", "deepseek-v4-flash-0731-baidu")

    select_family(window, "deepseek")
    select_model(window, "deepseek", "deepseek-v4-pro")
    select_family(window, "codewiz")

    assert store.default_model() == before  # 一轮浏览后存储的默认原封不动


def test_apply_switch_commits_and_emits(qtbot, tmp_path: Path) -> None:
    window, store, _cred = make_window(qtbot, tmp_path)
    emitted: list[tuple[str, str]] = []
    window.modelChanged.connect(lambda p, m: emitted.append((p, m)))

    select_family(window, "deepseek")
    select_model(window, "deepseek", "deepseek-v4-flash")
    window._apply_button.click()

    assert store.default_model() == ("deepseek", "deepseek-v4-flash")
    assert emitted == [("deepseek", "deepseek-v4-flash")]


def test_apply_switch_with_missing_default_placeholder_is_rejected(qtbot, tmp_path: Path) -> None:
    window, store, _cred = make_window(qtbot, tmp_path)
    s = store.settings()
    s["defaultModel"] = "ghost-model"
    store._save(config_module.SETTINGS_FILE, s)
    window._build()

    assert "目录缺失" in window._model_combo.currentText()
    window._apply_button.click()
    assert "不在当前目录" in window._status.text()
    assert store.default_model() == ("codewiz", "ghost-model")  # 依旧不被改写


def test_desynced_runtime_catalog_is_healed_on_open(qtbot, tmp_path: Path) -> None:
    """端到端：运行时目录缺了模板里的 0731（昨天的事故现场），打开设置即被迁移补回，
    默认值原样保留且不再是「目录缺失」占位。"""
    credentials = keychain.MemoryCredentialStore()
    store = config_module.ConfigStore(tmp_path, keychain=credentials)
    store.ensure_initialized()
    s = store.settings()
    s["defaultModel"] = "deepseek-v4-flash-0731-baidu"
    store._save(config_module.SETTINGS_FILE, s)
    # 模拟昨天的旧世界：运行时目录里没有 0731
    catalog_path = tmp_path / "agent" / "models.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    codewiz = catalog["providers"]["codewiz"]
    codewiz["models"] = [m for m in codewiz["models"] if m["id"] != "deepseek-v4-flash-0731-baidu"]
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

    window = SettingsWindow(store=store)
    qtbot.addWidget(window)

    current_data = window._model_combo.currentData()
    assert current_data == ("codewiz", "deepseek-v4-flash-0731-baidu")
    assert "目录缺失" not in window._model_combo.currentText()
    assert store.default_model() == ("codewiz", "deepseek-v4-flash-0731-baidu")


def test_codewiz_family_folds_gemini_models(qtbot, tmp_path: Path) -> None:
    """同一把内网 Key 的 codewiz/codewiz-gemini 折叠为一个供应商；Gemini 模型出现在其列表。"""
    window, _store, _cred = make_window(qtbot, tmp_path)
    combo = window._provider_combo
    assert combo.count() == 2  # codewiz 家族 + deepseek，而不是三个条目
    select_family(window, "codewiz")
    datas = [window._model_combo.itemData(i) for i in range(window._model_combo.count())]
    assert ("codewiz", "deepseek-v4-flash-0731-baidu") in datas
    assert ("codewiz-gemini", "gemini-3.7-flash") in datas


def test_codewiz_key_area_is_managed_readonly(qtbot, tmp_path: Path) -> None:
    """内网托管的 Key：只读说明，不给查看/修改（没有用户可维护的 secret）。"""
    window, _store, _cred = make_window(qtbot, tmp_path)
    select_family(window, "codewiz")
    assert window._key_edit.isReadOnly()
    assert "托管" in window._key_edit.placeholderText()
    assert window._key_peek_button.isHidden()
    assert window._key_edit_button.isHidden()
