"""Single resumable first-run wizard for configuration, permissions and a trial prompt."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from .key_validation import ValidationResult, validate_api_key
from .secure_storage import atomic_write_private, ensure_private_file
from .settings.config_store import ConfigStore

WELCOME_PAGE = 0
KEY_PAGE = 1
PERMISSIONS_PAGE = 2
TRIAL_PAGE = 3


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
            self.page = page if WELCOME_PAGE <= page <= TRIAL_PAGE else WELCOME_PAGE
            self.completed = data.get("completed") is True
        except (OSError, ValueError, json.JSONDecodeError):
            self.page = WELCOME_PAGE
            self.completed = False

    def save(self) -> None:
        atomic_write_private(
            self.path,
            json.dumps({"page": self.page, "completed": self.completed}, separators=(",", ":")) + "\n",
        )


class KeyPage(QWizardPage):
    validation_finished = pyqtSignal(str, bool, str)

    def __init__(
        self,
        store: ConfigStore,
        verifier: Callable[[str, str], ValidationResult],
        parent=None,
    ):
        super().__init__(parent)
        self.store = store
        self.verifier = verifier
        self.provider = "deepseek"
        self._verified = store.key_status(self.provider)[0]
        self.setTitle("连接模型")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("API Key 只会在验证成功后保存到 macOS Keychain。"))
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("输入 DeepSeek API Key")
        layout.addWidget(self.key_edit)
        self.verify_button = QPushButton("保存并验证")
        self.verify_button.clicked.connect(self._verify)
        layout.addWidget(self.verify_button)
        self.status = QLabel("已配置，可继续" if self._verified else "尚未验证")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.validation_finished.connect(self._finish_validation)

    def isComplete(self) -> bool:
        return self._verified

    def _verify(self) -> None:
        candidate = self.key_edit.text().strip()
        if not candidate:
            self.status.setText("请输入 API Key")
            return
        self.verify_button.setEnabled(False)
        self.status.setText("正在验证…")

        def run() -> None:
            result = self.verifier(self.provider, candidate)
            self.validation_finished.emit(candidate, result.ok, result.message)

        threading.Thread(target=run, name="haochen-onboarding-key-check", daemon=True).start()

    def _finish_validation(self, candidate: str, ok: bool, message: str) -> None:
        self.verify_button.setEnabled(True)
        if not ok:
            self.status.setText(f"验证失败：{message}；原 Key 未更改")
            return
        try:
            self.store.set_key(self.provider, candidate)
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"Keychain 保存失败：{exc}")
            return
        self.key_edit.clear()
        self._verified = True
        self.status.setText("验证成功，已保存到 Keychain")
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
        accessibility = QPushButton("授权辅助功能（读文字）")
        accessibility.clicked.connect(lambda: self.permission_requested.emit("accessibility"))
        layout.addWidget(accessibility)
        screen = QPushButton("授权屏幕录制（看图片，可稍后）")
        screen.clicked.connect(lambda: self.permission_requested.emit("screen"))
        layout.addWidget(screen)
        layout.addWidget(QLabel("可直接点“下一步”，稍后在真正使用相关能力时再授权。"))


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
        parent=None,
    ):
        super().__init__(parent)
        self.store = store
        self.state = OnboardingState(store.home)
        self.setWindowTitle("欢迎使用 haochen")
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)

        welcome = QWizardPage()
        welcome.setTitle("欢迎使用 haochen")
        welcome_layout = QVBoxLayout(welcome)
        intro = QLabel("接下来依次完成安全连接、按需权限和一次试问。中途关闭后可从当前步骤继续。")
        intro.setWordWrap(True)
        welcome_layout.addWidget(intro)

        self.key_page = KeyPage(store, verifier)
        if self.state.page > KEY_PAGE and not self.key_page.isComplete():
            self.state.page = KEY_PAGE
            self.state.save()
        self.permission_page = PermissionPage()
        self.trial_page = TrialPage()
        self.setPage(WELCOME_PAGE, welcome)
        self.setPage(KEY_PAGE, self.key_page)
        self.setPage(PERMISSIONS_PAGE, self.permission_page)
        self.setPage(TRIAL_PAGE, self.trial_page)
        self.setStartId(self.state.page)
        self.currentIdChanged.connect(self._page_changed)
        self.permission_page.permission_requested.connect(self.permission_requested)

    def _page_changed(self, page: int) -> None:
        if WELCOME_PAGE <= page <= TRIAL_PAGE:
            self.state.page = page
            self.state.save()

    def accept(self) -> None:
        self.state.page = TRIAL_PAGE
        self.state.completed = True
        self.state.save()
        prompt = self.trial_page.prompt.text().strip()
        super().accept()
        if prompt:
            self.trial_requested.emit(prompt)
