"""L0 桌宠窗口：像素眼镜小哥常驻主屏右下角（96×96，可缩放留口）。

- 无边框 / 置顶 / 透明背景；脚本形态用 Qt WindowStaysOnTopHint 模拟，
  LSUIElement / NSStatusWindowLevel 留待 P4/P5 打包落地（README 有说明）。
- 可拖拽换位；拖动结束位置持久化到 haochen_home()/pet-pos.json，启动恢复（v0.1.6）。
- 双击唤起 L1 气泡；右键菜单（退出/新会话/设置占位）。
- 姿态切换 idle/thinking/alert 带轻过渡（淡出 100ms + 淡入 100ms）。
- idle 时轻微浮动（±2px / 600ms），有生命感。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QPixmap
from PyQt6.QtWidgets import QApplication, QGraphicsOpacityEffect, QLabel, QMenu, QVBoxLayout, QWidget

from .. import a11y, paths
from ..engine_client import haochen_home
from ..secure_storage import atomic_write_private, ensure_private_file
from .theme import ANIM_POSE_MS

log = logging.getLogger("haochen.pet.window")

ASSETS_DIR = paths.pet_assets()

PET_SIZE = 96            # 常驻尺寸（visual-spec §4）；set_pet_size 留缩放口
_FLOAT_MS = 600
_FLOAT_PX = 2
_SINGLE_CLICK_MS = 220
_SCREEN_MARGIN = 8


class PetWindow(QWidget):
    """L0 桌面宠物本体。"""

    summon_requested = pyqtSignal()      # 双击 / 热键唤起（或收起）气泡
    open_chat_requested = pyqtSignal()   # 直接打开完整对话窗口
    new_session_requested = pyqtSignal()
    settings_requested = pyqtSignal()    # 设置占位（P4 接配置面板）
    quit_requested = pyqtSignal()
    moved = pyqtSignal(int, int)         # v0.1.6：拖动中（外层联动气泡跟随）

    def __init__(self, hotkey_hint: str = "⌃⌥P", parent=None):
        super().__init__(parent)
        self._hotkey_hint = hotkey_hint
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        self.setAccessibleName("haochen 桌宠，点击开始对话")
        self.setAccessibleDescription("单击人物打开输入气泡；右键打开菜单")

        self._size = PET_SIZE
        self._pose = ""
        self._pixmaps: dict[str, QPixmap] = {}
        self._pose_anim = None

        self.label = QLabel()
        self.label.setAccessibleName("haochen，点击开始对话")
        self.label.setStyleSheet("background: transparent;")
        # The image covers the whole window. Route pointer events to the parent
        # deliberately so one implementation owns click, drag, double-click,
        # right-click and the native macOS Control-click gesture.
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)
        self.set_pose("idle", animate=False)

        # 默认落主屏右下角（留边距，避开贴边死角）；有记忆位置则恢复（v0.1.6）
        screen = QApplication.primaryScreen().availableGeometry()
        self._restore_position(screen)

        self._drag_pos = None
        self._moved = False
        self.setToolTip("单击和我说话 · 右键打开菜单")
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(_SINGLE_CLICK_MS)
        self._click_timer.timeout.connect(self.summon_requested.emit)
        self._context_menu: QMenu | None = None

        # idle 轻微浮动（视觉生命感；拖拽/非 idle 时暂停）
        self._float_dir = 1
        self._floating_enabled = True
        self._float_timer = QTimer(self)
        self._float_timer.timeout.connect(self._float)
        if not a11y.reduce_motion_enabled():
            self._float_timer.start(_FLOAT_MS)

    # ── 姿态 ──────────────────────────────────────────────────

    def set_pet_size(self, size: int) -> None:
        """缩放留口：姿态图按新尺寸重载。"""
        self._size = max(48, min(int(size), 256))
        self._pixmaps.clear()
        self.set_pose(self._pose or "idle", animate=False)

    def _pixmap(self, pose: str) -> QPixmap | None:
        if pose not in self._pixmaps:
            p = ASSETS_DIR / f"{pose}.png"
            if not p.exists():
                log.warning("pet asset missing: %s", p)
                return None
            dpr = max(1.0, float(self.devicePixelRatioF()))
            physical_size = round(self._size * dpr)
            pm = QPixmap(str(p)).scaled(
                physical_size, physical_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            if pm.isNull():
                log.warning("pet asset load failed: %s", p)
                return None
            # QLabel 仍占 96×96 逻辑像素，但 Retina 屏使用 2× 物理采样，避免先缩成
            # 96px 再由系统放大；人物风格不变，只提升眼镜、肤色和轮廓的清晰度。
            pm.setDevicePixelRatio(dpr)
            self._pixmaps[pose] = pm
        return self._pixmaps[pose]

    def set_pose(self, pose: str, animate: bool = True) -> None:
        """切换姿态：idle / thinking / angry(alert)。轻过渡 = 淡出→换图→淡入。"""
        try:
            if pose == self._pose:
                return
        except RuntimeError:
            return  # C++ 对象已销毁（延迟回调触发）：静默
        self._pose = pose
        pm = self._pixmap(pose)
        if animate and not a11y.reduce_motion_enabled() and pm is not None and self.isVisible():
            eff = QGraphicsOpacityEffect(self.label)
            self.label.setGraphicsEffect(eff)
            out = QPropertyAnimation(eff, b"opacity", self)
            out.setDuration(ANIM_POSE_MS // 2)
            out.setStartValue(1.0)
            out.setEndValue(0.0)
            out.setEasingCurve(QEasingCurve.Type.OutCubic)

            def _swap():
                self._apply_pixmap(pm)
                inn = QPropertyAnimation(eff, b"opacity", self)
                inn.setDuration(ANIM_POSE_MS // 2)
                inn.setStartValue(0.0)
                inn.setEndValue(1.0)
                inn.setEasingCurve(QEasingCurve.Type.OutCubic)
                inn.finished.connect(lambda: self.label.setGraphicsEffect(None))
                inn.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
                self._pose_anim = inn

            out.finished.connect(_swap)
            out.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
            self._pose_anim = out
        else:
            self._apply_pixmap(pm)

    def _apply_pixmap(self, pm: QPixmap | None) -> None:
        if pm is not None:
            self.label.setPixmap(pm)
        else:  # 贴图缺失兜底：emoji
            self.label.setText("👓")
            self.label.setStyleSheet("font-size: 56px; background: transparent;")
        self.adjustSize()

    @property
    def pose(self) -> str:
        return self._pose

    # ── idle 浮动 ──────────────────────────────────────────────

    def set_floating(self, on: bool) -> None:
        self._floating_enabled = on

    def _float(self) -> None:
        if self._floating_enabled and self._drag_pos is None:
            proposed = QPoint(self.x(), self.y() + self._float_dir * _FLOAT_PX)
            self.move(self._clamped_position(proposed, self.geometry().center()))
            self._float_dir *= -1

    def _clamped_position(self, proposed: QPoint, pointer: QPoint | None = None) -> QPoint:
        """Keep the whole character inside the target screen's visible work area."""
        anchor = pointer or QPoint(
            proposed.x() + self.width() // 2,
            proposed.y() + self.height() // 2,
        )
        screen = QApplication.screenAt(anchor) or a11y.screen_of(self)
        area = screen.availableGeometry()
        width = max(1, self.width())
        height = max(1, self.height())
        return QPoint(
            max(area.left() + _SCREEN_MARGIN,
                min(proposed.x(), area.right() - width + 1 - _SCREEN_MARGIN)),
            max(area.top() + _SCREEN_MARGIN,
                min(proposed.y(), area.bottom() - height + 1 - _SCREEN_MARGIN)),
        )

    # ── 位置记忆（v0.1.6）──────────────────────────────────────

    @staticmethod
    def _pos_path() -> Path:
        return haochen_home() / "pet-pos.json"

    def _restore_position(self, screen) -> None:
        """启动恢复上次拖动的位置；文件缺失/损坏/越出所有屏幕可用区 → 回退默认右下角。"""
        try:
            path = self._pos_path()
            ensure_private_file(path)
            data = json.loads(path.read_text(encoding="utf-8"))
            rect = QRect(int(data["x"]), int(data["y"]), self._size, self._size)
            candidates = [s.availableGeometry() for s in QApplication.screens()
                          if s.availableGeometry().intersects(rect)]
            if candidates:
                target = max(candidates, key=lambda area: area.intersected(rect).width()
                             * area.intersected(rect).height())
                x = max(target.left(), min(rect.x(), target.right() - self._size + 1))
                y = max(target.top(), min(rect.y(), target.bottom() - self._size + 1))
                self.move(x, y)
                return
        except Exception:
            pass
        self.move(screen.right() - self._size - 40, screen.bottom() - self._size - 60)

    def save_position(self) -> None:
        """拖动结束持久化人物位置（气泡位置由人物派生，不单独存）。"""
        try:
            atomic_write_private(
                self._pos_path(),
                json.dumps({"x": self.x(), "y": self.y()}, separators=(",", ":")) + "\n",
            )
        except Exception as e:
            log.warning("save pet position failed: %s", e)

    # ── 鼠标：拖拽 / 双击唤起 / 右键菜单 ──────────────────────

    @staticmethod
    def _is_secondary_click(e) -> bool:
        return e.button() == Qt.MouseButton.RightButton or (
            e.button() == Qt.MouseButton.LeftButton
            # On Apple platforms Qt maps MetaModifier to the physical Control
            # key (and ControlModifier to Command).
            and bool(e.modifiers() & Qt.KeyboardModifier.MetaModifier)
        )

    def mousePressEvent(self, e):
        if self._is_secondary_click(e):
            e.accept()
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.pos()
            self._moved = False
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None:
            pointer = e.globalPosition().toPoint()
            self.move(self._clamped_position(pointer - self._drag_pos, pointer))
            self._moved = True
            self.moved.emit(self.x(), self.y())  # v0.1.6：气泡跟随
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._is_secondary_click(e):
            self._show_context_menu(e.globalPosition().toPoint())
            e.accept()
            return
        if e.button() == Qt.MouseButton.LeftButton:
            if self._drag_pos is not None and self._moved:
                self.save_position()  # v0.1.6：拖动结束记位置
            elif self._drag_pos is not None:
                self._click_timer.start()
            self._drag_pos = None
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and not self._moved:
            self._click_timer.stop()
            self.summon_requested.emit()
        else:
            super().mouseDoubleClickEvent(e)

    def _show_context_menu(self, global_pos: QPoint) -> None:
        if self._context_menu is not None and self._context_menu.isVisible():
            self._context_menu.raise_()
            return
        menu = QMenu(self)
        act_key = QAction(f"唤起气泡 {self._hotkey_hint}", self)
        act_key.setEnabled(False)
        menu.addAction(act_key)
        act_open = QAction("打开气泡", self)
        act_open.triggered.connect(self.summon_requested.emit)
        menu.addAction(act_open)
        act_chat = QAction("打开完整对话", self)
        act_chat.triggered.connect(self.open_chat_requested.emit)
        menu.addAction(act_chat)
        menu.addSeparator()
        act_new = QAction("新会话", self)
        act_new.triggered.connect(self.new_session_requested.emit)
        menu.addAction(act_new)
        act_settings = QAction("设置…", self)
        act_settings.triggered.connect(self.settings_requested.emit)
        menu.addAction(act_settings)
        menu.addSeparator()
        act_quit = QAction("退出", self)
        act_quit.triggered.connect(self.quit_requested.emit)
        menu.addAction(act_quit)
        # `exec()` keeps the pointer event handler in a nested event loop. Some
        # macOS accessibility/CGEvent right-click paths then wait for the
        # handler to return and immediately swallow the menu. `popup()` is
        # non-blocking, so the menu remains visible after the real click ends.
        self._context_menu = menu

        def _release_menu() -> None:
            if self._context_menu is menu:
                self._context_menu = None
            menu.deleteLater()

        menu.aboutToHide.connect(_release_menu)
        menu.popup(global_pos)

    def contextMenuEvent(self, e):
        self._show_context_menu(e.globalPos())
