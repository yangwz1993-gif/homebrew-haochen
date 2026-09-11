"""Single resumable first-run wizard for configuration, permissions and a trial prompt."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QStyleFactory,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from .background import run_in_background
from .key_validation import ValidationResult, validate_api_key, validate_custom_model
from .model_connection import CONNECT, CONNECTED, CONNECTING, connection_error, existing_key_for_connection
from .pet.profile import load_name_candidate, profile_needs_confirmation, save_user_name
from .secure_storage import atomic_write_private, ensure_private_file
from .settings.config_store import ConfigStore
from .settings.theme import APP_QSS

WELCOME_PAGE = 0
PROFILE_PAGE = 1
KEY_PAGE = 2
PERMISSIONS_PAGE = 3
TRIAL_PAGE = 4
STATE_VERSION = 2


class OnboardingState:
    def __init__(self, home: Path):
        self.path = Path(home) / "onboarding-state.json"
        self.page = WELCOME_PAGE
        self.completed = False
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            ensure_private_file(self.path)
            data = json.loads(self.path.read_text(encoding="utf-8"))
            page = int(data.get("page", WELCOME_PAGE))
            if data.get("version") != STATE_VERSION:
                page = {0: WELCOME_PAGE, 1: KEY_PAGE, 2: PERMISSIONS_PAGE, 3: TRIAL_PAGE}.get(
                    page, WELCOME_PAGE
                )
            self.page = page if WELCOME_PAGE <= page <= TRIAL_PAGE else WELCOME_PAGE
            self.completed = data.get("completed") is True
        except (OSError, ValueError, json.JSONDecodeError):
            self.page = WELCOME_PAGE
            self.completed = False

    def save(self) -> None:
        atomic_write_private(
            self.path,
            json.dumps(
                {"version": STATE_VERSION, "page": self.page, "completed": self.completed},
                separators=(",", ":"),
            ) + "\n",
        )


class ProfilePage(QWizardPage):
    """Optional, explicit user naming. The pet identity never lives in this profile."""

    def __init__(self, home: Path, parent=None):
        super().__init__(parent)
        self.home = Path(home)
        self.setTitle("我该怎么称呼你？")
        layout = QVBoxLayout(self)
        note = QLabel(
            "haochen 是桌面助手的名字。这里填的是它对你的称呼，"
            "可以留空，以后也能在设置中修改。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.name_edit = QLineEdit()
        candidate = load_name_candidate(self.home)
        self.name_edit.setText(candidate)
        self.name_edit.setPlaceholderText("例如：小杨（可选）")
        self.name_edit.setMaxLength(32)
        layout.addWidget(self.name_edit)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        if candidate and profile_needs_confirmation(self.home):
            migration = QLabel(f"旧版本曾记录为「{candidate}」，请确认、修改或清空。")
            migration.setWordWrap(True)
            layout.addWidget(migration)

    def commit(self) -> bool:
        saved = save_user_name(self.name_edit.text(), source="onboarding", home=self.home)
        self.status.setText("" if saved else "称呼暂时无法保存，请检查磁盘权限后重试。")
        return saved


class KeyPage(QWizardPage):
    validation_finished = pyqtSignal(str, bool, str)

    def __init__(
        self,
        store: ConfigStore,
        verifier: Callable[[str, str], ValidationResult],
        custom_verifier: Callable[[str, str, str], ValidationResult] = validate_custom_model,
        requires_key_reentry: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.store = store
        self.verifier = verifier
        self.custom_verifier = custom_verifier
        self.provider = "deepseek"
        self.requires_key_reentry = requires_key_reentry
        self._verified = store.key_status(self.provider)[0]
        self._connected = False
        self._connecting_existing = False
        self._configured_custom_signature: tuple[str, str, str] | None = None
        self._pending_custom: dict | None = None
        self._working = False
        self.setTitle("连接模型")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("连接成功后，API Key 会安全保存在 macOS 钥匙串中。"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("DeepSeek（预设）", "deepseek")
        self.mode_combo.addItem("自定义 OpenAI 兼容模型", "custom")
        self.mode_combo.setMinimumHeight(36)
        layout.addWidget(self.mode_combo)
        self.custom_panel = QWidget()
        custom_layout = QVBoxLayout(self.custom_panel)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        self.url_edit = QLineEdit()
        self.url_edit.setMaxLength(2048)
        self.url_edit.setMinimumHeight(36)
        self.url_edit.setPlaceholderText("API URL，例如 https://example.com/v1")
        self.model_id_edit = QLineEdit()
        self.model_id_edit.setMaxLength(128)
        self.model_id_edit.setMinimumHeight(36)
        self.model_id_edit.setPlaceholderText("模型 ID")
        self.model_name_edit = QLineEdit()
        self.model_name_edit.setMaxLength(64)
        self.model_name_edit.setMinimumHeight(36)
        self.model_name_edit.setPlaceholderText("显示名称（可选）")
        custom_layout.addWidget(self.url_edit)
        custom_layout.addWidget(self.model_id_edit)
        custom_layout.addWidget(self.model_name_edit)
        layout.addWidget(self.custom_panel)
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setMinimumHeight(36)
        self.key_edit.setPlaceholderText("输入 DeepSeek API Key")
        layout.addWidget(self.key_edit)
        self.verify_button = QPushButton(CONNECT)
        self.verify_button.setMinimumHeight(38)
        self.verify_button.setObjectName("primaryBtn")
        self.verify_button.clicked.connect(self._verify)
        layout.addWidget(self.verify_button)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.status = QLabel(self._connection_status())
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.validation_finished.connect(self._finish_validation)
        self._load_configured_custom_model()
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        self.url_edit.textEdited.connect(self._custom_value_edited)
        self.model_id_edit.textEdited.connect(self._custom_value_edited)
        self.model_name_edit.textEdited.connect(self._custom_value_edited)
        self.key_edit.textEdited.connect(self._custom_value_edited)
        self._mode_changed()

    def _load_configured_custom_model(self) -> None:
        provider_id, model_id = self.store.default_model()
        provider = next(
            (item for item in self.store.providers() if item.id == provider_id and not item.builtin),
            None,
        )
        if provider is None:
            return
        model = next(
            (item for item in provider.models if item.get("id") == model_id),
            None,
        )
        if model is None:
            return
        model_name = str(model.get("name") or model_id)
        self.url_edit.setText(provider.base_url)
        self.model_id_edit.setText(model_id)
        self.model_name_edit.setText(model_name)
        if self.store.key_status(provider_id)[0]:
            self._configured_custom_signature = (provider.base_url, model_id, model_name)
        self.mode_combo.setCurrentIndex(self.mode_combo.findData("custom"))

    def _connection_status(self) -> str:
        if self._connected:
            return "连接成功，可以继续。"
        if self._verified:
            return "已有配置，可继续；点击“连接模型”可检查连接。"
        provider = self._stored_provider()
        if provider and self.store.get_key(provider):
            return "已有保存的 Key，直接点“连接模型”；如需系统许可，会提示你确认。"
        return "填写 API Key 后点“连接模型”，验证成功后会安全保存。"

    def _custom_signature(self) -> tuple[str, str, str]:
        return (
            self.url_edit.text().strip(),
            self.model_id_edit.text().strip(),
            self.model_name_edit.text().strip(),
        )

    def _custom_value_edited(self, _value: str) -> None:
        self._connected = False
        self.verify_button.setText(CONNECT)
        self.verify_button.setEnabled(not self._working)
        if self.mode_combo.currentData() != "custom":
            self._verified = False
            self.status.setText("修改后请重新验证")
            self.completeChanged.emit()
            return
        self._verified = bool(
            not self.key_edit.text()
            and self._configured_custom_signature == self._custom_signature()
        )
        self.status.setText("已配置，可继续" if self._verified else "修改后请重新验证")
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return self._verified and not self._working

    def _set_working(self, working: bool) -> None:
        self._working = working
        for widget in (self.verify_button, self.mode_combo, self.key_edit,
                       self.url_edit, self.model_id_edit, self.model_name_edit):
            widget.setEnabled(not working)
        if self._connected:
            self.verify_button.setEnabled(False)
        self.progress.setVisible(working)
        self.completeChanged.emit()

    def _stored_provider(self) -> str | None:
        if self.mode_combo.currentData() != "custom":
            return self.provider
        for provider in self.store.providers():
            if (not provider.builtin and provider.base_url == self.url_edit.text().strip()
                    and any(model.get("id") == self.model_id_edit.text().strip()
                            for model in provider.models)):
                return provider.id
        return None

    def _verify(self) -> None:
        if self._working:
            return
        candidate = self.key_edit.text().strip()
        mode = self.mode_combo.currentData()
        stored_provider = self._stored_provider()
        self._connecting_existing = not bool(candidate)
        if not candidate and (not stored_provider or not self.store.get_key(stored_provider)):
            self.status.setText("请输入 API Key；已有 Key 只能用于原来的服务地址。")
            return
        if mode == "custom":
            stored_model = next((p for p in self.store.providers() if p.id == stored_provider), None)
            self._pending_custom = {
                "base_url": self.url_edit.text().strip(),
                "model_id": self.model_id_edit.text().strip(),
                "model_name": self.model_name_edit.text().strip(),
                "key": candidate,
                "provider_id": stored_provider,
                "api": stored_model.api if stored_model else "openai-completions",
            }
            if not self._pending_custom["base_url"] or not self._pending_custom["model_id"]:
                self.status.setText("请填写 API URL 和模型 ID")
                return
        self._set_working(True)
        self._verified = False
        self.verify_button.setText(CONNECTING)
        self.status.setText("正在连接；如弹出 macOS 钥匙串窗口，请确认许可，也可以取消。")
        if not candidate and stored_provider and self.store.key_access_required(stored_provider):
            self.window().lower()
        pending_custom = dict(self._pending_custom) if self._pending_custom is not None else None

        def run():
            effective_key = candidate or existing_key_for_connection(self.store, stored_provider or "")
            if pending_custom is not None:
                if self.custom_verifier is validate_custom_model:
                    return validate_custom_model(
                        pending_custom["base_url"], pending_custom["model_id"], effective_key,
                        api=pending_custom["api"],
                    )
                return self.custom_verifier(
                    pending_custom["base_url"],
                    pending_custom["model_id"],
                    effective_key,
                )
            return self.verifier(self.provider, effective_key)

        def done(result, error):
            if error:
                self._set_working(False)
                self.verify_button.setText(CONNECT)
                self.status.setText(connection_error(error) if not candidate else "连接验证未完成，请重试。")
                self._pending_custom = None
                return
            self._finish_validation(candidate, error is None and result.ok,
                                    "连接验证未完成，请重试" if error else result.message)

        run_in_background(self, run, done)

    def _finish_validation(self, candidate: str, ok: bool, message: str) -> None:
        if not ok:
            self._set_working(False)
            self.verify_button.setText(CONNECT)
            self.status.setText(f"验证失败：{message}；原 Key 未更改")
            self._pending_custom = None
            return
        pending = dict(self._pending_custom) if self._pending_custom is not None else None
        existing = self._connecting_existing
        self._set_working(True)
        self.verify_button.setText(CONNECTING)
        self.status.setText("连接成功，正在完成配置…")

        def save():
            if pending is not None:
                return self.store.upsert_custom_model(
                    base_url=pending["base_url"],
                    model_id=pending["model_id"],
                    model_name=pending["model_name"],
                    key=candidate or None,
                    provider_id=pending["provider_id"],
                    api=pending["api"],
                    allow_keychain_authorization=True,
                )
            if not existing:
                return self.store.set_key(
                    self.provider, candidate, allow_keychain_authorization=True
                )
            return None  # Reading an existing key never rewrites it or its ACL.

        def saved(_result, error):
            self._pending_custom = None
            self._set_working(False)
            if error:
                self.verify_button.setText(CONNECT)
                self.status.setText("连接成功，但保存未完成。再次点“连接模型”可重试，原 Key 未改变。")
                return
            if pending:
                self._configured_custom_signature = tuple(
                    pending[field] for field in ("base_url", "model_id", "model_name")
                )
            if self.key_edit.text().strip() == candidate:
                self.key_edit.clear()
            self._verified = not self.key_edit.text() and (
                pending is None or self._configured_custom_signature == self._custom_signature()
            )
            self._connected = self._verified
            self.key_edit.setPlaceholderText("已安全保存 · 输入新 Key 可更换")
            self.verify_button.setText(CONNECTED if self._connected else CONNECT)
            self.verify_button.setEnabled(not self._connected)
            self.status.setText("验证成功，已安全保存。可以点“下一步”了。" if self._verified
                                else "原配置已保存，修改后的内容请重新验证。")
            self.completeChanged.emit()

        run_in_background(self, save, saved)

    def _mode_changed(self) -> None:
        self._connected = False
        self.verify_button.setText(CONNECT)
        self.verify_button.setEnabled(not self._working)
        custom = self.mode_combo.currentData() == "custom"
        self.custom_panel.setVisible(custom)
        self.key_edit.setPlaceholderText("输入 API Key" if custom else "输入 DeepSeek API Key")
        self._pending_custom = None
        # A configured DeepSeek key must not silently authorize an untested custom URL.
        self._verified = (
            bool(
                not self.key_edit.text()
                and self._configured_custom_signature == self._custom_signature()
            )
            if custom
            else self.store.key_status(self.provider)[0]
        )
        self.status.setText(self._connection_status())
        self.completeChanged.emit()


class PermissionPage(QWizardPage):
    permission_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("按需权限")
        layout = QVBoxLayout(self)
        note = QLabel("权限不是使用聊天的前提。只有需要读屏或看图时再授权，并且一次只打开一个系统页面。")
        note.setWordWrap(True)
        layout.addWidget(note)
        self._permission_states: dict[str, bool | None] = {}
        self.permission_buttons: dict[str, QPushButton] = {}
        for permission, label in (("accessibility", "读文字 · 辅助功能"), ("screen", "看图片 · 屏幕录制")):
            layout.addWidget(QLabel(label))
            button = QPushButton("检查权限中…")
            button.setAccessibleName(label)
            button.clicked.connect(lambda _checked=False, p=permission: self._act(p))
            self.permission_buttons[permission] = button
            layout.addWidget(button)
        layout.addWidget(QLabel("可直接点“下一步”，稍后在真正使用相关能力时再授权。"))
        self.status = QLabel("尚未检查权限；聊天可直接使用。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self._poll = QTimer(self)
        self._poll.setInterval(1500)
        self._poll.timeout.connect(self.refresh_status)

    def _act(self, permission: str) -> None:
        state = self._permission_states.get(permission)
        if state is None:
            self.refresh_status()
        elif state:
            self.permission_requested.emit(f"manage_{permission}")
        else:
            self.permission_requested.emit(permission)

    def refresh_status(self) -> None:
        from .permissions import permission_status
        notes = []
        for permission, label in (("accessibility", "读文字"), ("screen", "看图片")):
            state = permission_status(permission)
            self._permission_states[permission] = state
            self.permission_buttons[permission].setText(
                "管理权限" if state is True else "去开启" if state is False else "重新检查")
            description = ("系统已授权，无需重复操作" if state is True else
                           "未授权，可稍后开启" if state is False else "暂时无法确认权限")
            notes.append(f"{label}：{description}")
        if any(state is True for state in self._permission_states.values()):
            notes.append("重装应用可能保留系统之前的授权。")
        self.status.setText("\n".join(notes))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh_status()
        self._poll.start()

    def hideEvent(self, event) -> None:
        self._poll.stop()
        super().hideEvent(event)


class TrialPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("试着问一句")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("完成后会把下面这句话发送给 haochen："))
        self.prompt = QLineEdit("你好，请用一句话介绍你能帮我做什么")
        layout.addWidget(self.prompt)


class OnboardingWizard(QWizard):
    permission_requested = pyqtSignal(str)
    trial_requested = pyqtSignal(str)

    def __init__(
        self,
        store: ConfigStore,
        verifier: Callable[[str, str], ValidationResult] = validate_api_key,
        requires_key_reentry: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.store = store
        self.state = OnboardingState(store.home)
        # The wizard uses our own styled controls in both a .app and test runners.
        # Native macOS wizard decorations require a bundle even in offscreen Qt.
        wizard_style = QStyleFactory.create("Fusion")
        wizard_style.setParent(self)
        self.setStyle(wizard_style)
        self.setWindowTitle("欢迎使用 haochen")
        self.setMinimumSize(560, 400)
        self.resize(600, 460)
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setStyleSheet(APP_QSS)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self.setButtonText(QWizard.WizardButton.BackButton, "上一步")
        self.setButtonText(QWizard.WizardButton.NextButton, "下一步")
        self.setButtonText(QWizard.WizardButton.FinishButton, "开始使用")
        self.setButtonText(QWizard.WizardButton.CancelButton, "稍后继续")
        self.button(QWizard.WizardButton.NextButton).setObjectName("primaryBtn")
        self.button(QWizard.WizardButton.FinishButton).setObjectName("primaryBtn")
        self.setStyleSheet(APP_QSS)  # Re-polish the footer after assigning its primary roles.

        welcome = QWizardPage()
        welcome.setTitle("欢迎使用 haochen")
        welcome_layout = QVBoxLayout(welcome)
        intro = QLabel("接下来依次完成安全连接、按需权限和一次试问。中途关闭后可从当前步骤继续。")
        intro.setWordWrap(True)
        welcome_layout.addWidget(intro)

        self.profile_page = ProfilePage(store.home)
        self.key_page = KeyPage(
            store,
            verifier,
            requires_key_reentry=requires_key_reentry,
        )
        if self.state.page > PROFILE_PAGE and profile_needs_confirmation(store.home):
            self.state.page = PROFILE_PAGE
            self.state.save()
        elif self.state.page > KEY_PAGE and not self.key_page.isComplete():
            self.state.page = KEY_PAGE
            self.state.save()
        self.permission_page = PermissionPage()
        self.trial_page = TrialPage()
        self.setPage(WELCOME_PAGE, welcome)
        self.setPage(PROFILE_PAGE, self.profile_page)
        self.setPage(KEY_PAGE, self.key_page)
        self.setPage(PERMISSIONS_PAGE, self.permission_page)
        self.setPage(TRIAL_PAGE, self.trial_page)
        # QWizard treats ``startId`` as the beginning of navigation history.  If
        # we start directly on a resumed page, Qt therefore hides/disables Back
        # even though earlier configuration pages still matter.  Rebuild the
        # real page history instead: users resume on the exact saved page and
        # can still go back to review or change their model/profile choices.
        resume_page = self.state.page
        self.setStartId(WELCOME_PAGE)
        self.restart()
        for _page in range(WELCOME_PAGE, resume_page):
            self.next()
        self.currentIdChanged.connect(self._page_changed)
        self.permission_page.permission_requested.connect(self.permission_requested)

    def _page_changed(self, page: int) -> None:
        if WELCOME_PAGE <= page <= TRIAL_PAGE:
            self.state.page = page
            self.state.save()

    def validateCurrentPage(self) -> bool:  # noqa: N802 - Qt virtual method
        if self.currentId() == PROFILE_PAGE:
            return self.profile_page.commit()
        return super().validateCurrentPage()

    def accept(self) -> None:
        self.state.page = TRIAL_PAGE
        self.state.completed = True
        self.state.save()
        prompt = self.trial_page.prompt.text().strip()
        super().accept()
        if prompt:
            self.trial_requested.emit(prompt)
