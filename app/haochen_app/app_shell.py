"""P4 集成壳：三 UI + 唯一引擎合成一个 App（装配与接线）。

`build_app()` 返回 (supervisor, chat, pet, settings)；`run_app.py` 与
P4 集成验证脚本共用本装配，保证「测的就是跑的」。

接线清单（开发总纲 §二 P4）：
- 唯一引擎：EngineSupervisor 持有唯一 EngineClient，三 UI 共用 → 引擎侧同一会话；
- 双入口联动：气泡「展开详细」→ 对话窗口从气泡 rect 动画展开为详情（v0.1.4 hotfix）；桌宠右键「设置」→ 设置面板；
- 配置生效链：modelChanged → set_model 热切换；restartRequired → 询问重启引擎；
- 首启引导：单一可续办向导依次完成 Keychain 验证、按需权限和试问；不读取全局 pi 凭据。

环境变量：
    HAOCHEN_MOCK=1             用 mock 引擎（联调/测试）
    HAOCHEN_HOME=<path>        数据目录（测试隔离）
    HAOCHEN_KEYCHAIN_SERVICE   Keychain 服务名（开发配置隔离）
    HAOCHEN_SKIP_ONBOARDING=1  自动化环境不显示首启向导
    HAOCHEN_AUTO_RESTART=1     配置要求重启引擎时免询问直接重启（自动化）
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QMessageBox, QWidget

from .chat import ChatWindow
from .chat.theme import app_stylesheet
from .pet import PetApp
from .settings import SettingsWindow
from .settings.config_store import ConfigStore
from .supervisor import EngineSupervisor

log = logging.getLogger("haochen.shell")


class AppShell:
    """集成 App 装配体（不持有 QApplication，由入口负责）。"""

    def __init__(self, mock: bool | None = None, home: Path | None = None):
        self.supervisor = EngineSupervisor(mock=mock, home=home)
        self.store = ConfigStore(home, keychain=self.supervisor.client.credentials)
        self.chat = ChatWindow(client=self.supervisor.client, supervisor=self.supervisor)
        self.chat.setStyleSheet(app_stylesheet())
        self.pet = PetApp(client=self.supervisor.client, supervisor=self.supervisor)
        self.settings = SettingsWindow(home=home, store=self.store)
        self._apply_default_model_after_restart = False
        self._pending_onboarding_trial: str | None = None
        self._syncing_draft = False
        self._wire()

    # ── 接线 ──────────────────────────────────────────────────

    def _wire(self) -> None:
        sup = self.supervisor

        # 双入口联动：气泡「展开详细」→ 对话窗口从气泡 rect 动画展开（v0.1.4 hotfix）
        self.pet.detail_opener = self._open_pet_detail
        self.pet.new_session_opener = self.chat._new_session
        self.pet.interaction_guard = self._interaction_allowed
        self.pet.chat_requested.connect(self.show_chat)
        self.chat.detail_collapsed.connect(self.pet.restore_bubble)
        self.chat.normal_closed.connect(self._restore_pet_after_chat)
        self.pet.bubble.input.textChanged.connect(
            lambda: self._sync_draft(self.pet.bubble.input, self.chat.input))
        self.chat.input.textChanged.connect(
            lambda: self._sync_draft(self.chat.input, self.pet.bubble.input))
        self.pet.settings_requested.connect(self.show_settings)
        self.pet.credential_validation.connect(self._on_credential_validation)
        self.pet.read_permission_requested.connect(self._request_read_permission)
        self.chat.read_permission_requested.connect(self._request_read_permission)
        sup.restarted.connect(self._on_config_restart_completed)
        sup.restart_failed.connect(self._on_config_restart_failed)
        self.settings.closed.connect(self.pet.restore_after_settings)

        # 配置 → 引擎生效链（M-D 预留信号，P4 接线）
        self.settings.modelChanged.connect(self._on_model_changed)
        self.settings.restartRequired.connect(self._on_restart_required)

        # 读屏时刻权限再引导：启动时被按「暂不」的用户，真正要读屏时再次引导
        sup.client.event.connect(self._on_engine_event)

    # ── 读屏权限再引导（§0.1：权限引导要友好）──────────────────

    def _on_engine_event(self, ev: dict) -> None:
        t = ev.get("type")
        result = ev.get("result") or {}
        details = result.get("details") or {}
        if (t == "tool_execution_end" and ev.get("toolName") == "read_screen"
              and (result.get("needScreenRecording") or details.get("needScreenRecording"))):
            # P7：未授权屏幕录制 → 友好提示（图片缺失，文本正常）
            self.pet.bubble.add_perception_hint("如需看图识人，请在设置开启「屏幕录制」")

    def _request_read_permission(self) -> None:
        """敏感权限只在用户明确同意本次读屏后引导，拒绝前绝不抢焦点。"""
        from PyQt6.QtCore import QTimer as _QTimer

        from .permissions import accessibility_granted, ensure_permissions
        if not accessibility_granted():
            _QTimer.singleShot(0, lambda: ensure_permissions(self.settings))

    # ── 双入口动作 ─────────────────────────────────────────────

    def show_chat(self) -> None:
        if not self._interaction_allowed():
            return
        self.pet._result_timer.stop()
        if self.pet.bubble.summoned:
            rect = self.pet.bubble.geometry()
            self.pet.bubble.hide()
            self.pet._detail_open = True
            self._open_pet_detail(rect)
        else:
            self.chat.show_normal()
        self._keep_pet_beside_chat()

    def _open_pet_detail(self, rect) -> None:
        self.chat.open_from_bubble(rect, self.pet._last_user_text, self.pet.pet.geometry())
        self._keep_pet_beside_chat()

    def _sync_draft(self, source, target) -> None:
        if self._syncing_draft or source.toPlainText() == target.toPlainText():
            return
        self._syncing_draft = True
        try:
            target.setPlainText(source.toPlainText())
        finally:
            self._syncing_draft = False

    def _keep_pet_beside_chat(self) -> None:
        # The panel chooses free space around the pet, never the reverse.
        if not self.chat.isVisible():
            return
        pet = self.pet.pet
        pet.show()
        pet.raise_()

    def new_session(self) -> None:
        """Create through the pet state reset and the chat session tracker exactly once."""
        if not self._interaction_allowed():
            return
        self.pet.new_session()

    def _restore_pet_after_chat(self) -> None:
        self.pet.pet.show()
        self.pet.pet.raise_()

    def show_settings(self) -> None:
        if not self._interaction_allowed():
            return
        self.pet.suspend_for_settings()
        self.settings.show()
        self.settings.raise_()
        self.settings.activateWindow()

    def _on_credential_validation(self, valid: bool, message: str) -> None:
        """把真实请求结果带回设置页，区分“凭据存在”和“凭据可用”。"""
        provider, _model = self.store.default_model()
        if provider:
            self.settings.set_runtime_key_validation(provider, valid, message)

    # ── 配置生效链 ─────────────────────────────────────────────

    def _on_model_changed(self, provider: str, model_id: str) -> None:
        """同 provider 换模型 → 引擎热切换（config/README §6）。"""
        if not self.supervisor.running:
            return  # 引擎未起：启动时自然读到新配置
        log.info("hot set_model %s/%s", provider, model_id)
        self.supervisor.client.set_model(provider, model_id)

    def _on_restart_required(self, reason: str) -> None:
        if not self.supervisor.running:
            return  # 引擎还没起：下次启动即生效，无需打扰
        if os.environ.get("HAOCHEN_AUTO_RESTART") == "1":
            self._apply_default_model_after_restart = True
            self.supervisor.restart_now()
            return
        box = QMessageBox(self.settings)
        box.setWindowTitle("重启引擎")
        box.setText(f"{reason}，重启引擎后生效。现在重启吗？")
        box.setInformativeText("重启约 1 秒，当前会话自动恢复，历史不丢。")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.Yes)
        if box.exec() == QMessageBox.StandardButton.Yes:
            self._apply_default_model_after_restart = True
            self.supervisor.restart_now()

    def _on_config_restart_completed(self) -> None:
        """Apply the configured default after Supervisor restores the old session."""
        if self._apply_default_model_after_restart:
            provider, model_id = self.store.default_model()
            if provider and model_id:
                self.supervisor.client.set_model(provider, model_id)
            self._apply_default_model_after_restart = False
        prompt, self._pending_onboarding_trial = self._pending_onboarding_trial, None
        if prompt:
            self._dispatch_onboarding_trial(prompt)

    def _on_config_restart_failed(self) -> None:
        self._apply_default_model_after_restart = False
        self._pending_onboarding_trial = None
        self._on_credential_validation(
            False, "当前模型连接失败，请检查凭据或模型设置"
        )

    # ── 首启引导 ───────────────────────────────────────────────

    def first_run_setup(self, parent: QWidget | None = None) -> None:
        """Show one resumable wizard; never read credentials from global pi config."""
        created = self.store.ensure_initialized()
        if created:
            log.info("config initialized: %s", [path.name for path in created])
        from .onboarding import KEY_PAGE, PROFILE_PAGE, OnboardingState, OnboardingWizard
        from .pet.profile import profile_needs_confirmation

        state = OnboardingState(self.store.home)
        if os.environ.get("HAOCHEN_SKIP_ONBOARDING") == "1":
            return
        needs_profile = profile_needs_confirmation(self.store.home)
        has_key = self.any_key_configured()
        requires_key_reentry = state.completed and not has_key
        if state.completed and has_key and not needs_profile:
            return
        if state.completed and needs_profile:
            state.completed = False
            state.page = PROFILE_PAGE
            state.save()
        elif state.completed and not has_key:
            state.completed = False
            state.page = KEY_PAGE
            state.save()
        elif not state.completed and needs_profile and state.page > PROFILE_PAGE:
            # An interrupted/migrated wizard must not jump over explicit identity consent.
            state.page = PROFILE_PAGE
            state.save()
        self.onboarding = OnboardingWizard(
            self.store,
            requires_key_reentry=requires_key_reentry,
            parent=parent,
        )
        self.onboarding.permission_requested.connect(self._request_onboarding_permission)
        self.onboarding.trial_requested.connect(self._send_onboarding_trial)
        self.onboarding.finished.connect(self._on_onboarding_finished)
        self._present_onboarding()

    def _present_onboarding(self) -> None:
        """Keep the first-run wizard in front of the always-on-top desktop pet."""
        if not hasattr(self, "onboarding") or self.onboarding.state.completed:
            return
        self.pet.pet.hide()
        self.onboarding.show()
        self.onboarding.raise_()
        self.onboarding.activateWindow()

    def _interaction_allowed(self) -> bool:
        """Do not let global shortcuts or the pet route around a visible wizard."""
        if hasattr(self, "onboarding") and self.onboarding.isVisible():
            self._present_onboarding()
            return False
        return True

    def _on_onboarding_finished(self, _result: int) -> None:
        self.pet.pet.show()
        self.pet.pet.raise_()
        # Discovery help is intentionally suppressed while onboarding is
        # visible; give it a fresh chance only after that surface is gone.
        self.chat._defer(900, self.pet._maybe_show_discovery_hint)

    def any_key_configured(self) -> bool:
        try:
            return any(self.store.key_status(provider.id)[0] for provider in self.store.providers())
        except Exception:  # noqa: BLE001 — 配置损坏时不阻塞启动
            return False

    def _request_onboarding_permission(self, permission: str) -> None:
        from .permissions import open_permission_settings, request_accessibility, request_screen_recording

        if permission not in ("accessibility", "screen"):
            return
        if hasattr(self, "onboarding"):
            self.onboarding.lower()

        def handoff():
            granted = request_accessibility() if permission == "accessibility" else request_screen_recording()
            if not granted:
                open_permission_settings(permission)
            if hasattr(self, "onboarding"):
                self.onboarding.permission_page.refresh_status()

        QTimer.singleShot(0, handoff)

    def _send_onboarding_trial(self, prompt: str) -> None:
        provider, model_id = self.store.default_model()
        if self.supervisor.running and provider.startswith("custom-"):
            # The custom provider was written after the engine started. Restart
            # to reload its catalog, then override the restored session's old
            # model before sending the first trial prompt.
            self._pending_onboarding_trial = prompt
            self._apply_default_model_after_restart = True
            self.supervisor.restart_now()
            return
        if self.supervisor.running and provider and model_id:
            self.supervisor.client.set_model(provider, model_id)
        self._dispatch_onboarding_trial(prompt)

    def _dispatch_onboarding_trial(self, prompt: str) -> None:
        if not self.pet.bubble.summoned:
            self.pet._toggle_bubble()
        self.pet.send(prompt)

    # ── 生命周期 ───────────────────────────────────────────────

    def start(self) -> None:
        self.supervisor.start()
        self.pet.start()          # 桌宠常驻（内部 client.start 幂等）
        self.chat.start()         # 拉 get_state 就绪（窗口默认不显示）
        self._install_app_tracker()
        self._reconcile_tcc()
        if hasattr(self, "onboarding") and not self.onboarding.state.completed:
            # pet.start() shows its always-on-top window after first_run_setup().
            # Re-present once the event loop starts so the wizard cannot end up behind it.
            QTimer.singleShot(0, self._present_onboarding)

    def _reconcile_tcc(self) -> None:
        """构建指纹检查（v0.1.4 hotfix）：版本/签名变更 → 清历史 TCC 记录，强制重新授权。

        稳定签名使 designated requirement 跨构建固定，旧版 TCC 记录对新构建依然有效
        （用户未对本版授权却能读屏）。指纹不一致即清记录；随后的 _guide_permissions
        自然检测到未授权 → 触发系统弹窗重授，无需额外接线。
        """
        if getattr(self.supervisor.client, "_mock", False):
            return  # mock/自动化：不碰系统 TCC，不污染测试环境
        if os.environ.get("HAOCHEN_SKIP_PERMISSION_GUIDE") == "1":
            return  # 自动化/无头测试
        from .engine_client import haochen_home
        from .permissions import reconcile_tcc_with_build
        reconcile_tcc_with_build(haochen_home())

    def _install_app_tracker(self) -> None:
        """追踪用户最近浏览的非 haochen 窗口（读屏窗口选择用）。"""
        from .app_tracking import install_tracker
        from .engine_client import haochen_home
        install_tracker(haochen_home())

    def stop(self) -> None:
        self.supervisor.stop()
