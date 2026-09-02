"""macOS-native menu bar for the LSUIElement app (task-4c)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QApplication, QMenu, QMenuBar, QWidget

if TYPE_CHECKING:
    from .app_shell import AppShell

MENU_OBJECT_NAME = "haochen-menubar"


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
    open_chat.setShortcut(QKeySequence("Meta+1"))
    open_chat.triggered.connect(shell.show_chat)
    menu.addAction(open_chat)

    settings = QAction("设置…", menu)
    settings.setShortcut(QKeySequence("Meta+,"))
    settings.triggered.connect(shell.show_settings)
    menu.addAction(settings)

    new_session = QAction("新会话", menu)
    new_session.setShortcut(QKeySequence("Meta+N"))
    new_session.triggered.connect(shell.chat._new_session)
    menu.addAction(new_session)

    menu.addSeparator()

    about = QAction("关于 haochen", menu)
    about.setMenuRole(QAction.MenuRole.AboutRole)
    about.triggered.connect(lambda: _about(shell, shell.settings))
    menu.addAction(about)

    menu.addSeparator()

    quit_action = QAction("退出 haochen", menu)
    quit_action.setShortcut(QKeySequence("Meta+Q"))
    quit_action.setMenuRole(QAction.MenuRole.QuitRole)
    quit_action.triggered.connect(app.quit)
    menu.addAction(quit_action)

    bar.addMenu(menu)
    return bar
