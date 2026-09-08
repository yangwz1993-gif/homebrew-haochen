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
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..key_validation import validate_api_key
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
        self._runtime_key_validation: dict[str, tuple[bool, str]] = {}
        self.keyValidationFinished.connect(self._on_key_validation_finished)
        self.setWindowTitle("haochen 设置")
        self.setMinimumWidth(520)
        self.resize(560, 640)
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
        self._body.addWidget(self._behavior_card(settings))
        self._body.addWidget(self._signature_card())
        self._body.addWidget(self._theme_card())
        self._body.addStretch(1)

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

    def _keys_card(self, providers) -> QFrame:
        card, lay = _card(
            "API Key",
            "新 Key 会先验证连接，成功后才保存到 macOS Keychain；失败不会覆盖当前 Key。",
        )
        for p in providers:
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
