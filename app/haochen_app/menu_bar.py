"""macOS-native menu bar for the LSUIElement app (task-4c)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QApplication, QMenu, QMenuBar, QWidget

if TYPE_CHECKING:
    from .app_shell import AppShell

MENU_OBJECT_NAME = "haochen-menubar"


def _standard_shortcut(key: QKeySequence.StandardKey, fallback: str) -> QKeySequence:
    """Return the platform shortcut, with a fallback for headless Qt platforms."""
    sequence = QKeySequence(key)
    return sequence if not sequence.isEmpty() else QKeySequence(fallback)


def _about(shell: AppShell, parent: QWidget) -> None:
    from PyQt6.QtWidgets import QMessageBox

    from .version import __version__

    box = QMessageBox(parent)
    box.setWindowTitle("关于 haochen")
    box.setText(f"haochen {__version__}")
    box.setInformativeText(
        "常驻桌面上的技术伙伴：读屏、看图、干活。\n数据保存在本机的 haochen 目录。"
    )
    box.exec()


def install_menu_bar(app: QApplication, shell: AppShell):
    """Install (or return the existing) haochen menu bar with native shortcuts."""
    existing = app.findChild(QMenu, "haochen-app-menu")
    if existing is not None:
        return existing  # 已安装：返回挂住的菜单对象（幂等）

    bar = getattr(app, "_haochen_menu_bar", None)
    if bar is not None:
        return bar
    # macOS：首个无父 QMenuBar 成为原生菜单栏（LSUIElement 应用也适用）。
    bar = QMenuBar()
    bar.setObjectName(MENU_OBJECT_NAME)
    setattr(app, "_haochen_menu_bar", bar)  # 根住引用，防被 GC

    menu = QMenu("haochen", bar)
    menu.setObjectName("haochen-app-menu")

    open_chat = QAction("打开对话", menu)
    # Qt on macOS maps ControlModifier to the native Command key.
    open_chat.setShortcut(QKeySequence("Ctrl+1"))
    # The standalone native menu bar is not owned by whichever haochen window
    # is frontmost. Application scope keeps the shortcut active when only the
    # frameless desktop pet has focus.
    open_chat.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
    open_chat.triggered.connect(shell.show_chat)
    menu.addAction(open_chat)

    settings = QAction("设置…", menu)
    # Give macOS an explicit Preferences action instead of relying on text
    # heuristics. This both places it in the native application menu and makes
    # the platform-standard Command-, shortcut dependable.
    settings.setMenuRole(QAction.MenuRole.PreferencesRole)
    settings.setShortcut(
        _standard_shortcut(QKeySequence.StandardKey.Preferences, "Ctrl+,")
    )
    settings.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
    settings.triggered.connect(shell.show_settings)
    menu.addAction(settings)

    new_session = QAction("新会话", menu)
    new_session.setShortcut(_standard_shortcut(QKeySequence.StandardKey.New, "Ctrl+N"))
    new_session.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
    new_session.triggered.connect(shell.new_session)
    menu.addAction(new_session)

    menu.addSeparator()

    about = QAction("关于 haochen", menu)
    about.setMenuRole(QAction.MenuRole.AboutRole)
    about.triggered.connect(lambda: _about(shell, shell.settings))
    menu.addAction(about)

    menu.addSeparator()

    quit_action = QAction("退出 haochen", menu)
    quit_action.setShortcut(_standard_shortcut(QKeySequence.StandardKey.Quit, "Ctrl+Q"))
    quit_action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
    quit_action.setMenuRole(QAction.MenuRole.QuitRole)
    quit_action.triggered.connect(app.quit)
    menu.addAction(quit_action)

    bar.addMenu(menu)
    return bar
