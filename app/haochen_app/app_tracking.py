"""追踪用户「最近浏览的非 haochen 窗口」，供读屏窗口选择（P7 修复）。

读者是独立进程（os.getpid() ≠ app pid），无法自己判断「前台是 haochen」或
「用户刚才在看哪个 app」。壳侧（本 app）用 NSWorkspace 监听激活，把用户最近
使用的**非 haochen** app pid 写到 HAOCHEN_HOME/last-user-app.txt；读到该数字。
读者在「前台=haochen」时优先用它，从而读到用户正在浏览的窗口（如 Chrome），
而非 haochen 自身或系统悬浮窗口。

v0.1.8 另管一个 sidecar：last-user-text.txt —— pet/chat 发送时把用户最新提问
原文落盘，引擎扩展 read_screen 读它做看图意图判定（v0.1.8 看图模式）。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("haochen.apptrack")


def _last_app_file(home: Path) -> Path:
    return home / "last-user-app.txt"


def write_last_user_app(home) -> None:
    """记录当前前台（若非 haochen 自身）到 sidecar。壳为 haochen 时记录的是其本身，跳过。"""
    try:
        home = Path(home)
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return
        pid = app.processIdentifier()
        if pid == os.getpid() or pid <= 1:
            return
        home.mkdir(parents=True, exist_ok=True)
        _last_app_file(home).write_text(str(pid), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log.debug("write_last_user_app failed: %s", exc)


def read_last_user_app(home) -> int:
    """读取最近用户 app pid；无则 0。"""
    try:
        home = Path(home)
        p = _last_app_file(home)
        if p.exists():
            return int(p.read_text().strip())
    except (ValueError, OSError):
        pass
    return 0


def write_last_user_text(home, text: str) -> None:
    """记录用户最新提问原文到 last-user-text.txt（v0.1.8 看图模式：
    引擎扩展 read_screen 读它判定「用户是不是在问图」）。覆盖写纯文本，失败静默。"""
    try:
        home = Path(home)
        home.mkdir(parents=True, exist_ok=True)
        (home / "last-user-text.txt").write_text(text, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log.debug("write_last_user_text failed: %s", exc)


def install_tracker(home) -> None:
    """安装 NSWorkspace 激活监听：每次用户切到某个 app 时若它非 haochen 自身，记录其 pid。"""
    try:
        home = Path(home)
        from AppKit import NSWorkspace, NSWorkspaceDidActivateApplicationNotification
        home.mkdir(parents=True, exist_ok=True)
        write_last_user_app(home)  # 初始
        # 正确：addObserver 挂在 workspace 的 notificationCenter 上（workspace 本身无此方法）
        NSWorkspace.sharedWorkspace().notificationCenter().addObserverForName_object_queue_usingBlock_(
            NSWorkspaceDidActivateApplicationNotification, None, None,
            lambda _n: write_last_user_app(home))
    except Exception as exc:  # noqa: BLE001
        log.warning("install_tracker failed: %s", exc)
