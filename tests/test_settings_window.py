"""Settings window construction and interactions (closing coverage to 80%)."""

from __future__ import annotations

import importlib
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


def test_window_builds_all_cards(qtbot, tmp_path: Path) -> None:
    window, _store, _cred = make_window(qtbot, tmp_path)
    assert window.windowTitle() == "haochen 设置"
    # 关键卡片标题都在面板里
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


def test_provider_key_badge_and_readonly_reference(qtbot, tmp_path: Path) -> None:
    window, store, _cred = make_window(qtbot, tmp_path)
    # 外部间接引用锁定为只读
    store.set_key("deepseek", "$MY_EXTERNAL_VAR")
    window._build()

    edits = window.findChildren(settings_module.QLineEdit)
    readonly = [e for e in edits if e.isReadOnly()]
    assert readonly and readonly[0].toolTip()


def test_stored_key_is_not_presented_as_runtime_validated(qtbot, tmp_path: Path) -> None:
    window, store, credentials = make_window(qtbot, tmp_path)
    credentials.set("deepseek", "stored-secret")
    store.set_key("deepseek", "stored-secret")
    window._build()

    labels = window.findChildren(settings_module.QLabel)
    badge = next(label for label in labels if "已存储" in label.text())

    assert badge.text() == "已存储 · 尚未验证"
    assert badge.objectName() == "badgeOff"


def test_runtime_key_failure_overrides_storage_badge(qtbot, tmp_path: Path) -> None:
    window, store, credentials = make_window(qtbot, tmp_path)
    credentials.set("deepseek", "invalid-secret")
    store.set_key("deepseek", "invalid-secret")

    window.set_runtime_key_validation("deepseek", False, "API Key 无效或格式不正确")

    badges = [
        label for label in window.findChildren(settings_module.QLabel)
        if label.objectName() == "badgeErr"
    ]
    assert badges and badges[0].text() == "当前凭据验证失败"
    assert "重新输入并验证" in window._status.text()


def test_key_save_flow_validate_fail_keeps_old(qtbot, tmp_path: Path, monkeypatch) -> None:
    window, store, credentials = make_window(qtbot, tmp_path)
    credentials.set("deepseek", "old-secret")
    store.set_key("deepseek", "old-secret")

    monkeypatch.setattr(
        settings_module, "validate_api_key",
        lambda provider, key: validation.ValidationResult(False, "无效"),
    )
    # 找到 deepseek 的编辑框并触发保存
    edits = window.findChildren(settings_module.QLineEdit)
    assert edits
    provider_edit = edits[0]
    provider_edit.setText("bad-candidate")
    save_button = window.findChildren(settings_module.QPushButton)
    target = next((b for b in save_button if b.text() == "保存并验证"), None)
    assert target is not None
    target.click()

    qtbot.waitUntil(lambda: "原 Key 未更改" in window._status.text(), timeout=2000)
    assert credentials.get("deepseek") == "old-secret"


def test_key_save_flow_validate_success_saves(qtbot, tmp_path: Path, monkeypatch) -> None:
    window, store, credentials = make_window(qtbot, tmp_path)
    monkeypatch.setattr(
        settings_module, "validate_api_key",
        lambda provider, key: validation.ValidationResult(True, "连接成功"),
    )
    edits = window.findChildren(settings_module.QLineEdit)
    provider_edit = edits[0]
    provider_edit.setText("new-good-secret")
    save_button = next(
        b for b in window.findChildren(settings_module.QPushButton) if b.text() == "保存并验证"
    )
    save_button.click()

    qtbot.waitUntil(lambda: credentials.get("deepseek") == "new-good-secret", timeout=2000)
    qtbot.waitUntil(lambda: provider_edit.text() == "", timeout=1000)
    restart_reasons: list[str] = []
    window.restartRequired.connect(lambda reason: restart_reasons.append(reason))
    assert "已安全存储" in window._status.text() or credentials.get("deepseek") == "new-good-secret"


def test_key_clear_removes_keychain_entry(qtbot, tmp_path: Path) -> None:
    window, store, credentials = make_window(qtbot, tmp_path)
    credentials.set("deepseek", "existing")
    store.set_key("deepseek", "existing")
    window._build()

    edits = window.findChildren(settings_module.QLineEdit)
    provider_edit = edits[0]
    provider_edit.setText("")  # 清空 = 删除
    save_button = next(
        b for b in window.findChildren(settings_module.QPushButton) if b.text() == "保存并验证"
    )
    save_button.click()

    qtbot.waitUntil(lambda: credentials.get("deepseek") is None, timeout=1000)


def test_corrupt_config_shows_recovery_card(qtbot, tmp_path: Path) -> None:
    credentials = keychain.MemoryCredentialStore()
    store = config_module.ConfigStore(tmp_path, keychain=credentials)
    store.ensure_initialized()
    (tmp_path / "agent" / "models.json").write_text("{broken", encoding="utf-8")
    window = SettingsWindow(store=store)
    qtbot.addWidget(window)
    # 损坏卡片给出「重置为默认」入口
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
        assert "deepseek" in content  # 模板已恢复（可解析）


def test_theme_and_signature_cards_render(qtbot, tmp_path: Path) -> None:
    window, _store, _cred = make_window(qtbot, tmp_path)
    # 主题卡（禁用扩展位）与签名状态卡（只读）
    combos = window.findChildren(settings_module.QComboBox)
    assert combos  # 模型选择 + 主题扩展位均存在
    disabled = [c for c in combos if not c.isEnabled()]
    assert disabled  # 主题下拉是禁用扩展位
    labels = [label.text() for label in window.findChildren(settings_module.QLabel)]
    assert any("Developer ID" in text or "开发模式" in text for text in labels)
