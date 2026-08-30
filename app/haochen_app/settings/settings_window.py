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

    def __init__(self, home: Path | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.store = ConfigStore(home)
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
            "输入后回车即保存。也支持间接引用：$ENV_VAR 从环境变量读取、!command 执行命令获取"
            "（高级用法，已配置的间接引用会锁定为只读）。",
        )
        for p in providers:
            configured, status = self.store.key_status(p.id)
            key = self.store.get_key(p.id)

            head = QHBoxLayout()
            head.addWidget(QLabel(p.name))
            badge = QLabel(status, objectName="badgeOk" if configured else "badgeOff")
            head.addWidget(badge)
            head.addStretch(1)
            lay.addLayout(head)

            row = QHBoxLayout()
            edit = QLineEdit()
            edit.setEchoMode(QLineEdit.EchoMode.Password)
            edit.setPlaceholderText("sk-…（未配置）")
            if key is not None:
                edit.setText(key)
            if key and is_indirect_reference(key):
                edit.setReadOnly(True)
                edit.setToolTip("间接引用由高级用户手工维护，面板不覆盖")
            eye = QToolButton(text="显示")
            eye.setCheckable(True)
            eye.toggled.connect(
                lambda on, e=edit: e.setEchoMode(
                    QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
            row.addWidget(edit, 1)
            row.addWidget(eye)
            lay.addLayout(row)

            edit.editingFinished.connect(
                lambda e=edit, pid=p.id, b=badge, orig=(key or ""): self._save_key(pid, e, b, orig))
        return card

    def _save_key(self, provider: str, edit: QLineEdit, badge: QLabel, orig: str) -> None:
        text = edit.text().strip()
        if text == orig:
            return
        effect = self.store.set_key(provider, text)
        configured, status = self.store.key_status(provider)
        badge.setText(status)
        badge.setObjectName("badgeOk" if configured else "badgeOff")
        badge.setStyleSheet("")  # 触发 QSS 重算
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
        """P8：签名与授权（一键修复）+ 权限状态。"""
        from ..app_signing_repair import app_bundle, is_stable_signed

        card, lay = _card("签名与权限", "用稳定签名后，辅助功能/屏幕录制授权跨构建持久（无需密码一键完成）。")
        stable = bool(app_bundle() is not None and is_stable_signed())
        status = QLabel("当前：已用稳定签名 ✓（授权持久）" if stable
                        else "当前：临时签名（ad-hoc），授权可能不持久")
        status.setObjectName("statusOk" if stable else "statusWarn")
        lay.addWidget(status)
        btn_fix = QPushButton("一键修复签名权限", objectName="primaryBtn")
        btn_fix.setEnabled(not stable)
        btn_fix.clicked.connect(self._fix_signing_clicked)
        lay.addWidget(btn_fix, alignment=Qt.AlignmentFlag.AlignLeft)
        return card

    def _fix_signing_clicked(self) -> None:
        from PyQt6.QtWidgets import QApplication

        # 触发壳层签名修复（经 app_shell 或独立调用 repair + quit）
        # 复用当前运行中的 shell（若已被某处持有）；否则独立修
        shell = getattr(QApplication.instance(), "_haochen_shell", None)
        if shell is not None:
            shell.fix_signing()
        else:
            from ..app_signing_repair import repair_signing
            from ..engine_client import haochen_home
            res = repair_signing(haochen_home())
            from PyQt6.QtWidgets import QMessageBox
            if res.get("ok") and res.get("needs_quit"):
                QMessageBox.information(self, "签名修复",
                    "已修复并调度自动重签重开（无需密码）。App 将退出重启，重启后请授权一次。")
            else:
                QMessageBox.warning(self, "签名修复", res.get("msg", "未完成"))

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
