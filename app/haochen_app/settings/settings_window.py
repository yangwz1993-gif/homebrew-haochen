"""haochen 设置面板（M-D 配置前端）。

视觉按 docs/visual-spec.md：米白底、深描边、圆角、分组卡片。
交互按 docs/interaction-spec.md §7：改完即写盘，立即生效或明确提示重启。

P4 集成入口：

    from haochen_app.settings import SettingsWindow
    win = SettingsWindow()                       # 或传 home=Path(...) 指定数据目录
    win.modelChanged.connect(lambda p, m: engine.set_model(p, m))   # 同 provider 热切换
    win.restartRequired.connect(show_restart_hint)                  # 需要重启引擎
    win.show()

入口来源（interaction-spec §7）：桌宠右键「设置」/ 窗口侧栏「设置」→ 均 show() 本窗口。
"""

from __future__ import annotations

import threading
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..key_validation import validate_api_key, validate_custom_model
from ..pet.profile import load_name_candidate, profile_needs_confirmation, save_user_name
from . import theme
from .config_store import (
    EFFECT_IMMEDIATE,
    EFFECT_LABEL,
    EFFECT_RESTART,
    THINKING_LEVELS,
    ConfigCorruptError,
    ConfigStore,
    is_indirect_reference,
)

_THINKING_LABELS = {
    "off": "关闭", "minimal": "极简", "low": "低", "medium": "中",
    "high": "高（默认）", "xhigh": "极高", "max": "最大",
}


def _card(title: str, hint: str = "") -> tuple[QFrame, QVBoxLayout]:
    """一张分组卡片：16px 圆角、2px 深描边、color-surface 底。"""
    frame = QFrame(objectName="card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(16, 14, 16, 16)
    lay.setSpacing(10)
    lay.addWidget(QLabel(title, objectName="cardTitle"))
    if hint:
        h = QLabel(hint, objectName="hint", wordWrap=True)
        lay.addWidget(h)
    return frame, lay


class SettingsWindow(QWidget):
    """设置面板主窗口。所有修改即写盘（ConfigStore 原子写）。"""

    # ── 给 P4 的信号 ──────────────────────────────────────────
    modelChanged = pyqtSignal(str, str)      # 同 provider 换模型 → 接 engine.set_model
    thinkingLevelChanged = pyqtSignal(str)   # 默认思考档变化
    restartRequired = pyqtSignal(str)        # 改动需重启引擎生效（原因描述）
    keyValidationFinished = pyqtSignal(str, str, bool, str)
    customModelValidationFinished = pyqtSignal(object, bool, str)
    closed = pyqtSignal()                    # 壳层用于恢复打开设置前的临时气泡上下文

    def __init__(
        self,
        home: Path | None = None,
        parent: QWidget | None = None,
        store: ConfigStore | None = None,
    ):
        super().__init__(parent)
        self.store = store or ConfigStore(home)
        self._pending_key_validations: dict[str, tuple[str, QLineEdit, QLabel, QPushButton]] = {}
        self._pending_custom_model: dict | None = None
        self._editing_custom: tuple[str, str] | None = None
        self._runtime_key_validation: dict[str, tuple[bool, str]] = {}
        self.keyValidationFinished.connect(self._on_key_validation_finished)
        self.customModelValidationFinished.connect(self._on_custom_model_validation_finished)
        self.setWindowTitle("haochen 设置")
        self.setMinimumWidth(580)
        self.resize(640, 720)
        self.setStyleSheet(theme.APP_QSS)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.Shape.NoFrame)
        root.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        self._body = QVBoxLayout(body)
        self._body.setContentsMargins(16, 16, 16, 8)
        self._body.setSpacing(14)

        # 底部状态条：保存反馈 / 生效语义提示
        self._status = QLabel("", objectName="statusOk")
        self._status.setContentsMargins(18, 4, 18, 8)
        root.addWidget(self._status)

        self.store.ensure_initialized()
        self._build()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        """通知壳层设置已关闭；窗口本身仍沿用 Qt 默认的隐藏语义。"""
        super().closeEvent(event)
        self.closed.emit()

    # ── 构建 / 重建（损坏恢复后整体重绘）───────────────────────

    def _build(self) -> None:
        while self._body.count():
            item = self._body.takeAt(0)
            if w := item.widget():
                w.deleteLater()
        try:
            providers = self.store.providers()
            settings = self.store.settings()
        except ConfigCorruptError as e:
            self._body.addWidget(self._corrupt_card(e))
            self._body.addStretch(1)
            return
        self._providers = providers
        self._body.addWidget(self._model_card(providers, settings))
        self._body.addWidget(self._keys_card(providers))
        self._body.addWidget(self._custom_model_card(providers))
        self._body.addWidget(self._profile_card())
        self._body.addWidget(self._behavior_card(settings))
        self._body.addWidget(self._signature_card())
        self._body.addWidget(self._theme_card())
        self._body.addStretch(1)

    def _profile_card(self) -> QFrame:
        card, lay = _card(
            "称呼",
            "桌宠的名字固定是 haochen；这里只设置它如何称呼你。留空则自然称“你”。",
        )
        row = QHBoxLayout()
        self._profile_name_edit = QLineEdit(load_name_candidate(self.store.home))
        self._profile_name_edit.setMaxLength(32)
        self._profile_name_edit.setPlaceholderText("例如：小杨（可选）")
        row.addWidget(self._profile_name_edit, 1)
        save = QPushButton("保存称呼")
        save.setObjectName("primaryBtn")
        save.clicked.connect(self._save_profile_name)
        row.addWidget(save)
        lay.addLayout(row)
        if profile_needs_confirmation(self.store.home) and load_name_candidate(self.store.home):
            warning = QLabel("这是旧版本留下的未确认称呼；保存前不会注入对话。", objectName="statusWarn")
            warning.setWordWrap(True)
            lay.addWidget(warning)
        return card

    def _save_profile_name(self) -> None:
        value = self._profile_name_edit.text().strip()
        if not save_user_name(value, source="settings", home=self.store.home):
            self._set_status("称呼保存失败，请检查磁盘权限后重试", ok=False)
            return
        self._set_status("称呼已清除，haochen 会自然称“你”" if not value else f"已保存称呼：{value}", ok=True)

    def _corrupt_card(self, err: ConfigCorruptError) -> QFrame:
        card, lay = _card("配置文件损坏")
        msg = QLabel(f"{err.path}\n{err.detail}", objectName="statusErr", wordWrap=True)
        msg.setStyleSheet(f"font-family: \"SF Mono\", Menlo, monospace; font-size: {theme.FONT_CODE}pt;")
        lay.addWidget(msg)
        btn = QPushButton("重置为默认", objectName="danger")
        btn.clicked.connect(lambda: self._reset(err))
        lay.addWidget(btn, alignment=Qt.AlignmentFlag.AlignLeft)
        return card

    def _reset(self, err: ConfigCorruptError) -> None:
        self.store.reset_to_default(err.path.name)
        self._set_status(f"已重置 {err.path.name} 为默认配置", ok=True)
        self._build()

    # ── 模型 / provider ───────────────────────────────────────

    def _model_card(self, providers, settings) -> QFrame:
        card, lay = _card(
            "模型",
            "同模型供应商内切换立即生效；切换供应商需重启引擎。",
        )
        cur_provider = settings.get("defaultProvider", "")
        cur_model = settings.get("defaultModel", "")

        row = QHBoxLayout()
        row.addWidget(QLabel("默认供应商"))
        self._provider_combo = QComboBox()
        for p in providers:
            self._provider_combo.addItem(p.name, p.id)
        if (i := self._provider_combo.findData(cur_provider)) >= 0:
            self._provider_combo.setCurrentIndex(i)
        row.addWidget(self._provider_combo, 1)
        lay.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("默认模型　"))
        self._model_combo = QComboBox()
        row.addWidget(self._model_combo, 1)
        lay.addLayout(row)
        self._fill_models(self._provider_combo.currentData(), select=cur_model)

        # 全部 provider / 模型清单（models.json 只读展示）
        lines = []
        for p in providers:
            names = "、".join(m.get("name") or m["id"] for m in p.models) or "（无模型）"
            tag = "内建" if p.builtin else "自定义"
            lines.append(f"· {p.name}（{tag}）：{names}")
        listing = QLabel("\n".join(lines), objectName="hint", wordWrap=True)
        lay.addWidget(listing)

        self._provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        self._model_combo.currentIndexChanged.connect(self._on_model_changed)
        return card

    def _fill_models(self, provider_id: str, select: str = "") -> None:
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        for p in self._providers:
            if p.id == provider_id:
                for m in p.models:
                    self._model_combo.addItem(m.get("name") or m["id"], m["id"])
                break
        if (i := self._model_combo.findData(select)) >= 0:
            self._model_combo.setCurrentIndex(i)
        self._model_combo.blockSignals(False)

    def _on_provider_changed(self) -> None:
        provider = self._provider_combo.currentData()
        self._fill_models(provider)
        model = self._model_combo.currentData()
        if not provider or not model:
            return
        effect = self.store.set_default_model(provider, model)
        self._announce("默认供应商", effect)
        self.restartRequired.emit(f"默认供应商已切换为 {provider}")

    def _on_model_changed(self) -> None:
        provider = self._provider_combo.currentData()
        model = self._model_combo.currentData()
        if not provider or not model:
            return
        effect = self.store.set_default_model(provider, model)
        self._announce("默认模型", effect)
        if effect == EFFECT_IMMEDIATE:
            self.modelChanged.emit(provider, model)
        else:
            self.restartRequired.emit(f"默认模型已切换为 {provider}/{model}")

    # ── API Key ───────────────────────────────────────────────

    def _custom_model_card(self, providers) -> QFrame:
        card, lay = _card(
            "自定义模型",
            "适用于 OpenAI 兼容服务。连接测试通过后才保存并切换；Key 只进入 macOS Keychain。",
        )
        self._custom_form_toggle = QPushButton("＋ 添加自定义模型")
        self._custom_form_toggle.setAccessibleName("展开自定义模型配置")
        self._custom_form_toggle.clicked.connect(self._toggle_custom_form)
        lay.addWidget(self._custom_form_toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        self._custom_form = QWidget()
        form = QVBoxLayout(self._custom_form)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)
        self._custom_url_edit = QLineEdit()
        self._custom_url_edit.setMaxLength(2048)
        self._custom_url_edit.setPlaceholderText("API 根地址，例如 https://example.com/v1")
        self._custom_model_id_edit = QLineEdit()
        self._custom_model_id_edit.setMaxLength(128)
        self._custom_model_id_edit.setPlaceholderText("模型 ID，例如 gpt-4.1-mini")
        self._custom_model_name_edit = QLineEdit()
        self._custom_model_name_edit.setMaxLength(64)
        self._custom_model_name_edit.setPlaceholderText("显示名称（可选）")
        self._custom_key_edit = QLineEdit()
        self._custom_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._custom_key_edit.setPlaceholderText("API Key（编辑时留空表示不替换）")
        self._custom_api_combo = QComboBox()
        self._custom_api_combo.addItem("OpenAI Chat Completions", "openai-completions")
        self._custom_api_combo.addItem("OpenAI Responses", "openai-responses")
        for label, widget in (
            ("API URL", self._custom_url_edit),
            ("模型 ID", self._custom_model_id_edit),
            ("显示名称", self._custom_model_name_edit),
            ("协议", self._custom_api_combo),
            ("API Key", self._custom_key_edit),
        ):
            row = QHBoxLayout()
            field_label = QLabel(label)
            field_label.setMinimumWidth(82)
            row.addWidget(field_label)
            row.addWidget(widget, 1)
            form.addLayout(row)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self._custom_cancel_button = QPushButton("取消编辑")
        self._custom_cancel_button.clicked.connect(self._clear_custom_form)
        self._custom_cancel_button.hide()
        actions.addWidget(self._custom_cancel_button)
        self._custom_save_button = QPushButton("测试并保存")
        self._custom_save_button.setObjectName("primaryBtn")
        self._custom_save_button.clicked.connect(self._save_custom_model)
        actions.addWidget(self._custom_save_button)
        form.addLayout(actions)
        lay.addWidget(self._custom_form)
        self._custom_form.hide()
        custom = [p for p in providers if not p.builtin]
        if custom:
            lay.addWidget(QLabel("已添加", objectName="cardTitle"))
        for provider in custom:
            for model in provider.models:
                row = QHBoxLayout()
                summary = QLabel(
                    f"{model.get('name') or model['id']}  ·  {provider.name}\n{provider.base_url}",
                    objectName="hint",
                    wordWrap=True,
                )
                row.addWidget(summary, 1)
                edit = QPushButton("编辑")
                edit.clicked.connect(
                    lambda _checked=False, p=provider, m=model: self._edit_custom_model(p, m)
                )
                row.addWidget(edit)
                remove = QPushButton("删除", objectName="danger")
                remove.clicked.connect(
                    lambda _checked=False, pid=provider.id, mid=model["id"]: self._confirm_remove_custom_model(pid, mid)
                )
                row.addWidget(remove)
                lay.addLayout(row)
        return card

    def _toggle_custom_form(self) -> None:
        show = self._custom_form.isHidden()
        self._custom_form.setVisible(show)
        self._custom_form_toggle.setText("收起配置" if show else "＋ 添加自定义模型")
        self._custom_form_toggle.setAccessibleName(
            "收起自定义模型配置" if show else "展开自定义模型配置"
        )
        if show:
            self._custom_url_edit.setFocus()

    def _edit_custom_model(self, provider, model: dict) -> None:
        self._editing_custom = (provider.id, model["id"])
        self._custom_url_edit.setText(provider.base_url)
        self._custom_model_id_edit.setText(model["id"])
        self._custom_model_name_edit.setText(model.get("name") or "")
        if (index := self._custom_api_combo.findData(provider.api or "openai-completions")) >= 0:
            self._custom_api_combo.setCurrentIndex(index)
        self._custom_key_edit.clear()
        self._custom_cancel_button.show()
        self._custom_save_button.setText("测试并更新")
        self._custom_form.show()
        self._custom_form_toggle.setText("收起编辑")
        self._custom_form_toggle.setAccessibleName("收起自定义模型编辑")
        self._custom_url_edit.setFocus()

    def _clear_custom_form(self) -> None:
        self._editing_custom = None
        for edit in (
            self._custom_url_edit,
            self._custom_model_id_edit,
            self._custom_model_name_edit,
            self._custom_key_edit,
        ):
            edit.clear()
        self._custom_cancel_button.hide()
        self._custom_save_button.setText("测试并保存")
        self._custom_form.hide()
        self._custom_form_toggle.setText("＋ 添加自定义模型")
        self._custom_form_toggle.setAccessibleName("展开自定义模型配置")

    def _confirm_remove_custom_model(self, provider_id: str, model_id: str) -> None:
        answer = QMessageBox.question(
            self,
            "删除自定义模型",
            f"确定删除「{model_id}」吗？如果这是该服务的最后一个模型，对应 Key 也会从 Keychain 删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._remove_custom_model(provider_id, model_id)

    def _save_custom_model(self) -> None:
        editing_provider, editing_model = self._editing_custom or (None, None)
        candidate = {
            "base_url": self._custom_url_edit.text().strip(),
            "model_id": self._custom_model_id_edit.text().strip(),
            "model_name": self._custom_model_name_edit.text().strip(),
            "api": self._custom_api_combo.currentData(),
            "key": self._custom_key_edit.text().strip(),
            "provider_id": editing_provider,
            "original_model_id": editing_model,
        }
        if not candidate["base_url"] or not candidate["model_id"]:
            self._set_status("请填写 API URL 和模型 ID", ok=False)
            return
        effective_key = candidate["key"]
        if editing_provider and not effective_key:
            effective_key = self.store.keychain.get(editing_provider) or ""
        self._pending_custom_model = candidate
        self._custom_save_button.setEnabled(False)
        self._custom_form_toggle.setEnabled(False)
        self._custom_cancel_button.setEnabled(False)
        self._custom_save_button.setText("测试中…")

        def validate() -> None:
            result = validate_custom_model(
                candidate["base_url"], candidate["model_id"], effective_key
            )
            self.customModelValidationFinished.emit(candidate, result.ok, result.message)

        threading.Thread(target=validate, name="haochen-custom-model-check", daemon=True).start()

    def _on_custom_model_validation_finished(self, candidate: dict, ok: bool, message: str) -> None:
        if self._pending_custom_model is not candidate:
            return
        self._pending_custom_model = None
        self._custom_save_button.setEnabled(True)
        self._custom_form_toggle.setEnabled(True)
        self._custom_cancel_button.setEnabled(True)
        self._custom_save_button.setText("测试并更新" if self._editing_custom else "测试并保存")
        if not ok:
            self._set_status(f"连接失败：{message}；原配置未更改", ok=False)
            return
        key = candidate["key"] if candidate["key"] else None
        try:
            provider, _effect = self.store.upsert_custom_model(
                base_url=candidate["base_url"],
                model_id=candidate["model_id"],
                model_name=candidate["model_name"],
                key=key,
                api=candidate["api"],
                provider_id=candidate["provider_id"],
                original_model_id=candidate["original_model_id"],
            )
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"保存失败：{exc}；已恢复原配置", ok=False)
            return
        self._editing_custom = None
        self._runtime_key_validation[provider] = (True, message)
        self._set_status(f"已安全保存并切换到 {candidate['model_id']}", ok=True)
        self.restartRequired.emit("自定义模型配置已更新")
        self._build()

    def _remove_custom_model(self, provider_id: str, model_id: str) -> None:
        try:
            self.store.remove_custom_model(provider_id, model_id)
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"删除失败：{exc}", ok=False)
            return
        self._set_status(f"已删除自定义模型 {model_id}", ok=True)
        self.restartRequired.emit("自定义模型已删除")
        self._build()

    def _keys_card(self, providers) -> QFrame:
        card, lay = _card(
            "API Key",
            "新 Key 会先验证连接，成功后才保存到 macOS Keychain；失败不会覆盖当前 Key。",
        )
        for p in providers:
            if not p.builtin:
                continue  # 自定义端点的 Key 与 URL/模型作为一个事务管理
            configured, status = self.store.key_status(p.id)
            key = self.store.get_key(p.id)
            runtime = self._runtime_key_validation.get(p.id)
            if runtime is not None and not runtime[0]:
                badge_text, badge_name = "当前凭据验证失败", "badgeErr"
                placeholder = "重新输入有效 API Key"
            elif runtime is not None and runtime[0]:
                badge_text, badge_name = "已验证 · 安全存储", "badgeOk"
                placeholder = "已验证，输入新 Key 可替换"
            elif configured:
                badge_text, badge_name = "已存储 · 尚未验证", "badgeOff"
                placeholder = "已存储，输入新 Key 可验证或替换"
            else:
                badge_text, badge_name = status, "badgeOff"
                placeholder = "输入 API Key"

            head = QHBoxLayout()
            head.addWidget(QLabel(p.name))
            badge = QLabel(badge_text, objectName=badge_name)
            head.addWidget(badge)
            head.addStretch(1)
            lay.addLayout(head)

            row = QHBoxLayout()
            edit = QLineEdit()
            edit.setEchoMode(QLineEdit.EchoMode.Password)
            edit.setPlaceholderText(placeholder)
            managed_reference = bool(key and key.startswith("$HAOCHEN_") and key.endswith("_API_KEY"))
            if key and is_indirect_reference(key) and not managed_reference:
                edit.setReadOnly(True)
                edit.setToolTip("外部间接引用由高级用户维护，面板不覆盖")
            eye = QToolButton(text="显示")
            eye.setCheckable(True)
            eye.toggled.connect(
                lambda on, e=edit: e.setEchoMode(
                    QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
            save = QPushButton("保存并验证")
            save.setObjectName("primaryBtn")
            save.setEnabled(not edit.isReadOnly())
            save.clicked.connect(lambda _checked=False, pid=p.id, e=edit, b=badge, s=save: self._save_key(pid, e, b, s))
            row.addWidget(edit, 1)
            row.addWidget(eye)
            row.addWidget(save)
            lay.addLayout(row)
        return card

    def set_runtime_key_validation(self, provider: str, valid: bool, message: str) -> None:
        """记录本次运行的连接事实；Keychain 中“有值”不再冒充“验证成功”。"""
        self._runtime_key_validation[provider] = (valid, message)
        self._build()
        if valid:
            self._set_status(f"{provider}：连接已验证，凭据安全存储", ok=True)
        else:
            self._set_status(f"{provider}：{message}，请重新输入并验证", ok=False)

    def _save_key(self, provider: str, edit: QLineEdit, badge: QLabel, save: QPushButton) -> None:
        candidate = edit.text().strip()
        if not candidate:
            self.store.set_key(provider, "")
            self._runtime_key_validation.pop(provider, None)
            configured, status = self.store.key_status(provider)
            self._update_key_badge(badge, configured, status)
            edit.setPlaceholderText("输入 API Key")
            self._announce(f"{provider} 的 API Key 已清除", EFFECT_IMMEDIATE)
            self.restartRequired.emit(f"{provider} 的 API Key 已清除")
            return
        save.setEnabled(False)
        save.setText("验证中…")
        self._pending_key_validations[provider] = (candidate, edit, badge, save)

        def validate() -> None:
            result = validate_api_key(provider, candidate)
            self.keyValidationFinished.emit(provider, candidate, result.ok, result.message)

        threading.Thread(target=validate, name=f"haochen-key-check-{provider}", daemon=True).start()

    @staticmethod
    def _update_key_badge(badge: QLabel, configured: bool, status: str) -> None:
        badge.setText(status)
        badge.setObjectName("badgeOk" if configured else "badgeOff")
        badge.setStyleSheet("")

    def _on_key_validation_finished(self, provider: str, candidate: str, ok: bool, message: str) -> None:
        pending = self._pending_key_validations.get(provider)
        if pending is None or pending[0] != candidate:
            return
        self._pending_key_validations.pop(provider, None)
        _candidate, edit, badge, save = pending
        save.setEnabled(True)
        save.setText("保存并验证")
        if not ok:
            self._set_status(f"验证失败：{message}；原 Key 未更改", ok=False)
            return
        try:
            effect = self.store.set_key(provider, candidate)
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"Keychain 保存失败：{exc}", ok=False)
            return
        self._runtime_key_validation[provider] = (True, message)
        edit.clear()
        edit.setPlaceholderText("已验证，输入新 Key 可替换")
        configured, status = self.store.key_status(provider)
        self._update_key_badge(badge, configured, "已验证 · 安全存储")
        self._announce(f"{provider} 的 API Key", effect)
        self.restartRequired.emit(f"{provider} 的 API Key 已更新")

    # ── 行为 ──────────────────────────────────────────────────

    def _behavior_card(self, settings) -> QFrame:
        card, lay = _card("行为", "默认思考档越高，推理越充分、响应越慢。默认值即好用。")
        row = QHBoxLayout()
        row.addWidget(QLabel("默认思考档"))
        combo = QComboBox()
        for lv in THINKING_LEVELS:
            combo.addItem(_THINKING_LABELS.get(lv, lv), lv)
        if (i := combo.findData(settings.get("defaultThinkingLevel", "high"))) >= 0:
            combo.setCurrentIndex(i)
        row.addWidget(combo, 1)
        lay.addLayout(row)

        def on_changed():
            level = combo.currentData()
            effect = self.store.set_thinking_level(level)
            self._announce("默认思考档", effect)
            self.thinkingLevelChanged.emit(level)

        combo.currentIndexChanged.connect(on_changed)
        return card

    # ── 主题（占位）────────────────────────────────────────────

    def _theme_card(self) -> QFrame:
        card, lay = _card("主题", "目前仅提供默认米白主题，更多主题后续版本开放。")
        row = QHBoxLayout()
        row.addWidget(QLabel("界面主题"))
        combo = QComboBox()
        combo.addItem("默认（米白）", "cream")
        combo.setEnabled(False)  # 扩展位：暗色主题在 token 层映射（visual-spec §1）
        row.addWidget(combo, 1)
        lay.addLayout(row)
        return card

    def _signature_card(self) -> QFrame:
        """Read-only release-signing status; the app never creates signing identities."""
        from ..signing_status import is_developer_id_signed, signing_summary

        signed = is_developer_id_signed()
        card, lay = _card(
            "签名与权限",
            "正式版由 Developer ID 签名并经 Apple 公证；签名凭据不会存放在 App 或项目目录中。",
        )
        status = QLabel(f"当前：{signing_summary()}")
        status.setObjectName("statusOk" if signed else "statusWarn")
        status.setWordWrap(True)
        lay.addWidget(status)
        return card

    # ── 状态反馈 ───────────────────────────────────────────────

    def _announce(self, what: str, effect: str) -> None:
        if effect == EFFECT_RESTART:
            self._set_status(f"已保存 · {what}将在重启引擎后生效", ok=False)
        else:
            self._set_status(f"已保存 · {what}{EFFECT_LABEL[EFFECT_IMMEDIATE]}", ok=True)

    def _set_status(self, text: str, ok: bool) -> None:
        self._status.setText(text)
        self._status.setObjectName("statusOk" if ok else "statusWarn")
        self._status.setStyleSheet("")  # 触发 QSS 按 objectName 重算
