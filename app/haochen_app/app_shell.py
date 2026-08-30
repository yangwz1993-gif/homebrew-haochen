"""P4 集成壳：三 UI + 唯一引擎合成一个 App（装配与接线）。

`build_app()` 返回 (supervisor, chat, pet, settings)；`run_app.py` 与
P4 集成验证脚本共用本装配，保证「测的就是跑的」。

接线清单（开发总纲 §二 P4）：
- 唯一引擎：EngineSupervisor 持有唯一 EngineClient，三 UI 共用 → 引擎侧同一会话；
- 双入口联动：气泡「展开详细」→ 对话窗口从气泡 rect 动画展开为详情（v0.1.4 hotfix）；桌宠右键「设置」→ 设置面板；
- 配置生效链：modelChanged → set_model 热切换；restartRequired → 询问重启引擎；
- 首启引导：配置缺失 → 模板初始化；key 未配 → 经用户同意从 ~/.pi 只读导入 / 展开设置页。

环境变量：
    HAOCHEN_MOCK=1            用 mock 引擎（联调/测试）
    HAOCHEN_HOME=<path>       数据目录（测试隔离）
    HAOCHEN_AUTO_IMPORT_KEY=1 key 导入免询问（自动化/无头环境）
    HAOCHEN_AUTO_RESTART=1    配置要求重启引擎时免询问直接重启（自动化）
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from PyQt6.QtWidgets import QMessageBox, QWidget

from .chat import ChatWindow
from .chat.theme import app_stylesheet
from .pet import PetApp
from .settings import SettingsWindow
from .settings.config_store import ConfigStore
from .supervisor import EngineSupervisor

log = logging.getLogger("haochen.shell")

PLACEHOLDER_PREFIX = "sk-在此填入"


class AppShell:
    """集成 App 装配体（不持有 QApplication，由入口负责）。"""

    def __init__(self, mock: bool | None = None, home: Path | None = None):
        self.supervisor = EngineSupervisor(mock=mock, home=home)
        self.chat = ChatWindow(client=self.supervisor.client, supervisor=self.supervisor)
        self.chat.setStyleSheet(app_stylesheet())
        self.pet = PetApp(client=self.supervisor.client, supervisor=self.supervisor)
        self.settings = SettingsWindow(home=home)
        self.store = ConfigStore(home)
        self._wire()

    # ── 接线 ──────────────────────────────────────────────────

    def _wire(self) -> None:
        sup = self.supervisor

        # 双入口联动：气泡「展开详细」→ 对话窗口从气泡 rect 动画展开（v0.1.4 hotfix）
        self.pet.detail_opener = self.chat.open_from_bubble
        self.chat.detail_collapsed.connect(self.pet.restore_bubble)
        self.pet.settings_requested.connect(self.show_settings)

        # 配置 → 引擎生效链（M-D 预留信号，P4 接线）
        self.settings.modelChanged.connect(self._on_model_changed)
        self.settings.restartRequired.connect(self._on_restart_required)

        # 读屏时刻权限再引导：启动时被按「暂不」的用户，真正要读屏时再次引导
        sup.client.event.connect(self._on_engine_event)

    # ── 读屏权限再引导（§0.1：权限引导要友好）──────────────────

    def _on_engine_event(self, ev: dict) -> None:
        t = ev.get("type")
        if t == "tool_execution_start" and ev.get("toolName") == "read_screen":
            from PyQt6.QtCore import QTimer as _QTimer

            from .permissions import accessibility_granted, ensure_permissions
            if not accessibility_granted():
                _QTimer.singleShot(0, lambda: ensure_permissions(self.settings))
        elif (t == "tool_execution_end" and ev.get("toolName") == "read_screen"
              and (ev.get("result") or {}).get("needScreenRecording")):
            # P7：未授权屏幕录制 → 友好提示（图片缺失，文本正常）
            self.pet.bubble.add_perception_hint("如需看图识人，请在设置开启「屏幕录制」")

    # ── 双入口动作 ─────────────────────────────────────────────

    def show_chat(self) -> None:
        self.chat.show()
        self.chat.raise_()
        self.chat.activateWindow()

    def show_settings(self) -> None:
        self.settings.show()
        self.settings.raise_()
        self.settings.activateWindow()

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
            self.supervisor.restart_now()
            return
        box = QMessageBox(self.settings)
        box.setWindowTitle("重启引擎")
        box.setText(f"{reason}，重启引擎后生效。现在重启吗？")
        box.setInformativeText("重启约 1 秒，当前会话自动恢复，历史不丢。")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.Yes)
        if box.exec() == QMessageBox.StandardButton.Yes:
            self.supervisor.restart_now()

    # ── 首启引导 ───────────────────────────────────────────────

    def first_run_setup(self, parent: QWidget | None = None) -> None:
        """配置初始化 + key 导入 + 无 key 引导（config/README §2）。"""
        created = self.store.ensure_initialized()
        if created:
            log.info("config initialized: %s", [p.name for p in created])
        imported = self.maybe_import_key(parent=parent)
        if not self.any_key_configured() and not imported:
            log.info("no API key configured → open settings for guidance")
            self.settings._set_status("首次使用：请先在下方填入 API Key（如 DeepSeek）", ok=False)
            self.show_settings()

    def any_key_configured(self) -> bool:
        try:
            return any(self.store.key_status(p.id)[0] for p in self.store.providers())
        except Exception:  # noqa: BLE001 — 配置损坏时不阻塞启动
            return False

    def maybe_import_key(self, parent: QWidget | None = None) -> bool:
        """key 还是模板占位 → 经用户同意，从 ~/.pi/agent/auth.json 只读导入 deepseek。

        返回是否发生了导入。绝不写 ~/.pi（隔离原则）。
        """
        current = self.store.get_key("deepseek")
        if current and not current.startswith(PLACEHOLDER_PREFIX):
            return False  # 已有真 key
        pi_auth = Path.home() / ".pi" / "agent" / "auth.json"
        try:
            with pi_auth.open(encoding="utf-8") as f:
                key = (json.load(f).get("deepseek") or {}).get("key", "")
        except Exception:  # noqa: BLE001 — 没有全局 pi / 无 deepseek 条目
            return False
        if not key or key.startswith(PLACEHOLDER_PREFIX):
            return False
        auto = os.environ.get("HAOCHEN_AUTO_IMPORT_KEY") == "1"
        if not auto:
            box = QMessageBox(parent or self.settings)
            box.setWindowTitle("导入 API Key")
            box.setText("检测到本机 pi 已配置 DeepSeek API Key。")
            box.setInformativeText("是否只读复制到 haochen？（不会修改全局 pi 任何文件）")
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            box.setDefaultButton(QMessageBox.StandardButton.Yes)
            if box.exec() != QMessageBox.StandardButton.Yes:
                return False
        self.store.set_key("deepseek", key)
        log.info("deepseek key imported from ~/.pi (read-only)")
        return True

    # ── 生命周期 ───────────────────────────────────────────────

    def start(self) -> None:
        self.supervisor.start()
        self.pet.start()          # 桌宠常驻（内部 client.start 幂等）
        self.chat.start()         # 拉 get_state 就绪（窗口默认不显示）
        self._install_app_tracker()
        self._reconcile_tcc()
        self._guide_permissions()

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

    def _guide_permissions(self) -> None:
        """首启权限引导（DoD#5）：未授权辅助功能/屏幕录制 → 直接触发系统授权（v0.1.4 §0）。"""
        if getattr(self.supervisor.client, "_mock", False):
            return  # mock 联调不读屏
        if os.environ.get("HAOCHEN_SKIP_PERMISSION_GUIDE") == "1":
            return  # 自动化/无头测试
        from PyQt6.QtCore import QTimer

        from .permissions import accessibility_granted, ensure_permissions, screen_recording_granted
        ax = accessibility_granted()
        sr = screen_recording_granted()
        log.info("permissions at startup: accessibility=%s screen_recording=%s", ax, sr)
        if not (ax and sr):
            QTimer.singleShot(1200, lambda: self._first_run_guide())

    def _first_run_guide(self) -> None:
        """首启引导：若为 ad-hoc（未稳定签名）先引导一键修复；否则直接触发系统授权。"""
        from .permissions import ensure_permissions

        if self.signing_needs_fix():
            from PyQt6.QtWidgets import QMessageBox

            box = QMessageBox(self.settings)
            box.setWindowTitle("haochen 签名与授权")
            box.setText("当前 haochen 用临时签名，授权可能不持久（重新构建后需再授权）。")
            box.setInformativeText(
                "点「一键修复」：生成稳定签名并自动重签重开（无需密码），"
                "之后授权一次即持久，重新构建/升级不再需授权。")
            btn = box.addButton("一键修复签名权限", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("暂不", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is btn:
                self.fix_signing()
            else:
                ensure_permissions(self.settings)
        else:
            ensure_permissions(self.settings)

    def signing_needs_fix(self) -> bool:
        """是否需一键修复（当前 app 为 ad-hoc，非稳定签名）。"""
        if getattr(self.supervisor.client, "_mock", False):
            return False
        from .app_signing_repair import app_bundle, is_stable_signed
        return app_bundle() is not None and not is_stable_signed()

    def fix_signing(self) -> None:
        """一键修复签名权限（设置/首启按钮触发）。"""
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QMessageBox

        from .app_signing_repair import repair_signing
        from .engine_client import haochen_home

        if not self.signing_needs_fix():
            QMessageBox.information(self.settings, "签名", "当前已用稳定签名，无需修复。")
            return
        res = repair_signing(haochen_home())
        log.info("fix_signing result: %s", res)
        if res.get("ok") and res.get("needs_quit"):
            QMessageBox.information(
                self.settings, "签名修复",
                "已修复并调度自动重签重开（无需密码）。App 将自动退出重启——重启后请授权一次（辅助功能+屏幕录制）。")
            QTimer.singleShot(400, lambda: self.pet.quit())  # 整个 App 退出
        else:
            QMessageBox.warning(self.settings, "签名修复", res.get("msg", "未完成"))

    def stop(self) -> None:
        self.supervisor.stop()
