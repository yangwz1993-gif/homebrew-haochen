"""haochen 设置面板（M-D 配置前端）。

视觉复用 appearance：中性底色、细描边、圆角、分组卡片。
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

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..background import run_in_background
from ..key_validation import validate_api_key, validate_custom_model
from ..model_connection import CONNECT, CONNECTED, CONNECTING, connection_error, existing_key_for_connection
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
        self._connected_custom: set[tuple[str, str]] = set()
        self._key_widgets: dict[str, tuple[QLineEdit, QLabel, QPushButton]] = {}
        self._key_delete_buttons: dict[str, QAction] = {}
        self._working = False
        self._rebuild_pending = False
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

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.hide()
        root.addWidget(self.progress)

        # 底部状态条：保存反馈 / 生效语义提示
        self._status = QLabel("", objectName="statusOk")
        self._status.setWordWrap(True)
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
        if self._working:
            self._rebuild_pending = True
            return
        self._key_widgets.clear()
        self._key_delete_buttons.clear()
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
            "桌面助手的名字是 haochen；这里只设置它如何称呼你。留空则自然称“你”。",
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
        self._custom_save_button = QPushButton(CONNECT)
        self._custom_save_button.setObjectName("primaryBtn")
        self._custom_save_button.clicked.connect(self._save_custom_model)
        actions.addWidget(self._custom_save_button)
        form.addLayout(actions)
        lay.addWidget(self._custom_form)
        self._custom_form.hide()
        self._custom_list = QWidget()
        custom_list = QVBoxLayout(self._custom_list)
        custom_list.setContentsMargins(0, 0, 0, 0)
        custom = [p for p in providers if not p.builtin]
        if custom:
            custom_list.addWidget(QLabel("已添加", objectName="cardTitle"))
        for provider in custom:
            for model in provider.models:
                row = QHBoxLayout()
                summary = QLabel(
                    f"{model.get('name') or model['id']}  ·  {provider.name}\n{provider.base_url}",
                    objectName="hint",
                    wordWrap=True,
                )
                row.addWidget(summary, 1)
                connected = (provider.id, model["id"]) in self._connected_custom
                connect = QPushButton(CONNECTED if connected else CONNECT)
                connect.setObjectName("connected" if connected else "primaryBtn")
                connect.setEnabled(not connected)
                connect.clicked.connect(
                    lambda _checked=False, p=provider, m=model: self._connect_custom_model(p, m)
                )
                row.addWidget(connect)
                more = QToolButton(text="⋯")
                more.setObjectName("moreButton")
                more.setAccessibleName(f"{model['id']} 的更多操作")
                more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
                menu = QMenu(more)
                edit = menu.addAction("编辑 / 更换 Key")
                edit.triggered.connect(
                    lambda _checked=False, p=provider, m=model: self._edit_custom_model(p, m)
                )
                remove = menu.addAction("删除模型…")
                remove.triggered.connect(
                    lambda _checked=False, pid=provider.id, mid=model["id"]: self._confirm_remove_custom_model(pid, mid)
                )
                more.setMenu(menu)
                row.addWidget(more)
                custom_list.addLayout(row)
        lay.addWidget(self._custom_list)
        return card

    def _connect_custom_model(self, provider, model: dict) -> None:
        self._edit_custom_model(provider, model)
        self._save_custom_model()

    def _toggle_custom_form(self) -> None:
        show = self._custom_form.isHidden()
        self._custom_form.setVisible(show)
        self._custom_list.setVisible(not show)
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
        self._custom_save_button.setText(CONNECT)
        self._custom_form.show()
        self._custom_list.hide()
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
        self._custom_save_button.setText(CONNECT)
        self._custom_form.hide()
        self._custom_list.show()
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
        if self._working:
            return
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
        if not candidate["key"]:
            original = next((p for p in self.store.providers() if p.id == editing_provider), None)
            if original is None or original.base_url.rstrip("/") != candidate["base_url"].rstrip("/"):
                self._set_status("请填写 API Key；修改服务地址时，不能沿用旧地址的 Key。", ok=False)
                return
        self._pending_custom_model = candidate
        self._set_working(True)
        self._custom_save_button.setEnabled(False)
        self._custom_form_toggle.setEnabled(False)
        self._custom_cancel_button.setEnabled(False)
        self._custom_save_button.setText(CONNECTING)
        if editing_provider and not candidate["key"] and self.store.key_access_required(editing_provider):
            self.lower()

        def validate():
            effective_key = candidate["key"]
            if editing_provider and not effective_key:
                effective_key = existing_key_for_connection(self.store, editing_provider)
            return validate_custom_model(candidate["base_url"], candidate["model_id"], effective_key,
                                         api=candidate["api"])

        def validated(result, error):
            self._on_custom_model_validation_finished(
                candidate, error is None and result.ok,
                (connection_error(error) if not candidate["key"] else "无法完成连接验证，请检查配置后重试")
                if error else result.message,
            )

        run_in_background(self, validate, validated)

    def _on_custom_model_validation_finished(self, candidate: dict, ok: bool, message: str) -> None:
        if self._pending_custom_model is not candidate:
            return
        if not ok:
            self._pending_custom_model = None
            self._finish_working()
            self._custom_save_button.setEnabled(True)
            self._custom_form_toggle.setEnabled(True)
            self._custom_cancel_button.setEnabled(True)
            self._custom_save_button.setText(CONNECT)
            self._set_status(f"连接失败：{message}；原配置未更改", ok=False)
            return
        self._custom_save_button.setText(CONNECTING)
        key = candidate["key"] if candidate["key"] else None

        def save():
            return self.store.upsert_custom_model(
                base_url=candidate["base_url"],
                model_id=candidate["model_id"],
                model_name=candidate["model_name"],
                key=key,
                api=candidate["api"],
                provider_id=candidate["provider_id"],
                original_model_id=candidate["original_model_id"],
                allow_keychain_authorization=True,
            )

        def saved(result, error):
            self._pending_custom_model = None
            self._finish_working()
            self._custom_save_button.setEnabled(True)
            self._custom_form_toggle.setEnabled(True)
            self._custom_cancel_button.setEnabled(True)
            if error:
                self._custom_save_button.setText(CONNECT)
                self._set_status("保存未完成，原配置已保留。再次点“连接模型”可重试。", ok=False)
                return
            provider, _effect = result
            self._editing_custom = None
            self._runtime_key_validation[provider] = (True, message)
            self._connected_custom.add((provider, candidate["model_id"]))
            self._set_status(f"已安全保存并切换到 {candidate['model_id']}", ok=True)
            self._build()
            self.restartRequired.emit("自定义模型配置已更新")

        run_in_background(self, save, saved)

    def _remove_custom_model(self, provider_id: str, model_id: str) -> None:
        if self._working:
            return
        self._set_working(True)

        def done(_result, error):
            self._finish_working()
            if error:
                self._set_status("删除未完成，请稍后重试。", ok=False)
                return
            self._set_status(f"已删除自定义模型 {model_id}", ok=True)
            self._connected_custom.discard((provider_id, model_id))
            self._build()
            self.restartRequired.emit("自定义模型已删除")

        run_in_background(
            self,
            lambda: self.store.remove_custom_model(
                provider_id, model_id, allow_keychain_authorization=True
            ),
            done,
        )

    def _keys_card(self, providers) -> QFrame:
        card, lay = _card(
            "API Key",
            "点击“连接模型”即可。已有 Key 会直接使用；填写新 Key 时，验证成功后才替换旧 Key。",
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
            save = QPushButton(CONNECT)
            save.setObjectName("primaryBtn")
            save.setEnabled(not edit.isReadOnly())
            save.clicked.connect(lambda _checked=False, pid=p.id, e=edit, b=badge, s=save: self._save_key(pid, e, b, s))
            self._key_widgets[p.id] = (edit, badge, save)
            edit.textChanged.connect(lambda _text, pid=p.id: self._refresh_key_action(pid))
            row.addWidget(edit, 1)
            row.addWidget(eye)
            row.addWidget(save)
            if not edit.isReadOnly():
                more = QToolButton(text="⋯")
                more.setObjectName("moreButton")
                more.setAccessibleName(f"{p.name} 的更多操作")
                more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
                menu = QMenu(more)
                change = menu.addAction("更换 Key")
                change.triggered.connect(lambda _checked=False, e=edit: e.setFocus())
                remove = menu.addAction("删除 Key…")
                remove.setEnabled(bool(key))
                self._key_delete_buttons[p.id] = remove
                remove.triggered.connect(lambda _checked=False, pid=p.id: self._delete_key(pid))
                more.setMenu(menu)
                row.addWidget(more)
            lay.addLayout(row)
            self._refresh_key_action(p.id)
        return card

    def _refresh_key_action(self, provider: str) -> None:
        edit, badge, button = self._key_widgets[provider]
        if self._working:
            return
        connected = bool(self._runtime_key_validation.get(provider, (False, ""))[0])
        if edit.text():
            connected = False
            text = "新 Key 尚未连接 · 旧 Key 保留" if self.store.get_key(provider) else "尚未连接"
            self._update_key_badge(badge, False, text)
        elif connected:
            self._update_key_badge(badge, True, "已连接 · 安全存储")
        else:
            runtime = self._runtime_key_validation.get(provider)
            text = "当前凭据验证失败" if runtime else (
                "已保存 · 点击连接模型" if self.store.get_key(provider) else "尚未配置"
            )
            self._update_key_badge(badge, False, text)
            if runtime:
                badge.setObjectName("badgeErr")
                badge.setStyleSheet("")
        button.setText(CONNECTED if connected else CONNECT)
        button.setObjectName("connected" if connected else "primaryBtn")
        button.setEnabled(not connected and not edit.isReadOnly())
        button.setStyleSheet("")

    def _connect_saved_key(self, provider: str, button: QPushButton) -> None:
        if self._working:
            return
        self._set_working(True)
        button.setText(CONNECTING)
        if self.store.key_access_required(provider):
            self.lower()
        self._set_status("正在连接；如弹出 macOS 钥匙串窗口，请确认许可，也可以取消。", ok=False)

        def connect():
            key = existing_key_for_connection(self.store, provider)
            return validate_api_key(provider, key)

        def done(result, error):
            self._finish_working()
            if error or not result.ok:
                button.setText(CONNECT)
                self._runtime_key_validation.pop(provider, None)
                edit, badge, _ = self._key_widgets[provider]
                self._update_key_badge(badge, False, "连接未完成 · 原 Key 保留")
                self._set_status(connection_error(error) if error else result.message, ok=False)
                return
            self._runtime_key_validation[provider] = (True, result.message)
            self._refresh_key_action(provider)
            self._set_status("已连接，继续使用已保存的 Key。", ok=True)
            self.restartRequired.emit(f"{provider} 已连接")

        run_in_background(self, connect, done)

    def set_runtime_key_validation(self, provider: str, valid: bool, message: str) -> None:
        """记录本次运行的连接事实；Keychain 中“有值”不再冒充“验证成功”。"""
        self._runtime_key_validation[provider] = (valid, message)
        if self._working:
            return
        widgets = self._key_widgets.get(provider)
        if widgets:
            _edit, badge, _save = widgets
            self._update_key_badge(badge, valid, "已连接 · 安全存储" if valid else "当前凭据验证失败")
            if not valid:
                badge.setObjectName("badgeErr")
                badge.setStyleSheet("")
            elif not _edit.text():
                _edit.setPlaceholderText("已安全保存 · 输入新 Key 可更换")
                if provider in self._key_delete_buttons:
                    self._key_delete_buttons[provider].setEnabled(True)
        if widgets:
            self._refresh_key_action(provider)
            if widgets[0].text():
                return  # A background result for the old key must not label a new draft as connected.
        if valid:
            self._set_status(f"{provider}：连接已验证，凭据安全存储", ok=True)
        else:
            self._set_status(f"{provider}：{message}，请重新输入并验证", ok=False)

    def _save_key(self, provider: str, edit: QLineEdit, badge: QLabel, save: QPushButton) -> None:
        if self._working:
            return
        candidate = edit.text().strip()
        if not candidate:
            if self.store.get_key(provider):
                self._connect_saved_key(provider, save)
            else:
                self._set_status("请先填写 API Key，再点“连接模型”。", ok=False)
            return
        self._set_working(True)
        save.setEnabled(False)
        save.setText(CONNECTING)
        self._pending_key_validations[provider] = (candidate, edit, badge, save)

        def validated(result, error):
            self._on_key_validation_finished(provider, candidate, error is None and result.ok,
                                             "连接验证未完成，请重试" if error else result.message)

        run_in_background(self, lambda: validate_api_key(provider, candidate), validated)

    def _set_working(self, working: bool) -> None:
        self._working = working
        self._body.parentWidget().setEnabled(not working)
        self.progress.setVisible(working)

    def _finish_working(self) -> None:
        self._set_working(False)
        # Explicit rebuild requests are handled after the completion callback
        # has finished with its widgets. Runtime status never rebuilds the form.
        if self._rebuild_pending:
            self._rebuild_pending = False
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, self._build)

    def _delete_key(self, provider: str) -> None:
        if self._working:
            return
        if QMessageBox.question(self, "删除 API Key", "删除后需要重新填写 Key 才能使用这个模型。确定删除吗？",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self._set_working(True)
        self._set_status("正在删除…", ok=True)

        def done(_result, error):
            self._finish_working()
            if error:
                self._set_status("删除未完成，请稍后重试。", ok=False)
                return
            self._runtime_key_validation.pop(provider, None)
            self._build()
            self._set_status("API Key 已删除。", ok=True)
            self.restartRequired.emit(f"{provider} 的 API Key 已清除")

        run_in_background(
            self,
            lambda: self.store.set_key(
                provider, "", allow_keychain_authorization=True
            ),
            done,
        )

    @staticmethod
    def _update_key_badge(badge: QLabel, configured: bool, status: str) -> None:
        badge.setText(status)
        badge.setObjectName("badgeOk" if configured else "badgeOff")
        badge.setStyleSheet("")

    def _on_key_validation_finished(self, provider: str, candidate: str, ok: bool, message: str) -> None:
        pending = self._pending_key_validations.get(provider)
        if pending is None or pending[0] != candidate:
            return
        _candidate, edit, badge, save = pending
        if not ok:
            self._pending_key_validations.pop(provider, None)
            self._finish_working()
            save.setEnabled(True)
            save.setText(CONNECT)
            self._set_status(f"验证失败：{message}；原 Key 未更改", ok=False)
            return
        save.setText(CONNECTING)
        self._set_status("连接成功，正在安全保存…", ok=True)

        def saved(effect, error):
            self._pending_key_validations.pop(provider, None)
            self._finish_working()
            save.setEnabled(True)
            if error:
                save.setText(CONNECT)
                self._set_status("连接成功，但保存未完成。再次点“连接模型”可重试，原 Key 未改变。", ok=False)
                return
            self._runtime_key_validation[provider] = (True, message)
            if edit.text().strip() == candidate:
                edit.clear()
            edit.setPlaceholderText("已安全保存 · 输入新 Key 可更换")
            save.setText(CONNECTED)
            self._update_key_badge(badge, True, "已连接 · 安全存储")
            if provider in self._key_delete_buttons:
                self._key_delete_buttons[provider].setEnabled(True)
            self._announce(f"{provider} 的 API Key", effect)
            self._refresh_key_action(provider)
            self.restartRequired.emit(f"{provider} 的 API Key 已更新")

        run_in_background(
            self,
            lambda: self.store.set_key(
                provider, candidate, allow_keychain_authorization=True
            ),
            saved,
        )

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
        card, lay = _card("主题", "当前使用统一的浅色主题，更多主题后续开放。")
        row = QHBoxLayout()
        row.addWidget(QLabel("界面主题"))
        combo = QComboBox()
        combo.addItem("默认（浅色）", "cream")
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
            "签名情况以当前安装包为准；自签名不等于 Apple 公证。签名凭据不会随 App 分发。",
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
