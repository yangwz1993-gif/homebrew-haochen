"""Track the last non-haochen app and a privacy-preserving visual-intent bit.

The reader is a separate process, so the app stores the last foreground PID in a
private sidecar. User prompts are never persisted: only a derived boolean saying
whether the latest prompt explicitly requested image understanding is shared with
the engine extension.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from .secure_storage import atomic_write_private, ensure_private_directory, ensure_private_file

log = logging.getLogger("haochen.apptrack")

_VISUAL_INTENT_WORDS = (
    "看图", "看看这", "这张图", "图里", "图片", "照片", "截图", "画面", "长什么样",
    "帅", "美", "好看", "评价一下", "识别", "who is", "what's in", "image", "photo", "picture",
)


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
        ensure_private_directory(home)
        atomic_write_private(_last_app_file(home), str(pid))
    except Exception as exc:  # noqa: BLE001
        log.debug("write_last_user_app failed: %s", exc)


def read_last_user_app(home) -> int:
    """读取最近用户 app pid；无则 0。"""
    try:
        home = Path(home)
        p = _last_app_file(home)
        if p.exists():
            ensure_private_file(p)
            return int(p.read_text().strip())
    except (ValueError, OSError):
        pass
    return 0


def write_last_user_text(home, text: str) -> None:
    """Persist only a derived visual-intent boolean; never persist the prompt text."""
    try:
        home = Path(home)
        ensure_private_directory(home)
        normalized = text.casefold()
        visual = any(word in normalized for word in _VISUAL_INTENT_WORDS)
        atomic_write_private(
            home / "last-user-intent.json",
            json.dumps({"visual": visual}, separators=(",", ":")) + "\n",
        )
        # Remove the privacy-sensitive sidecar left by versions <=0.1.10.
        (home / "last-user-text.txt").unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        log.debug("write visual intent failed: %s", exc)


def install_tracker(home) -> None:
    """安装 NSWorkspace 激活监听：每次用户切到某个 app 时若它非 haochen 自身，记录其 pid。"""
    try:
        home = Path(home)
        from AppKit import NSWorkspace, NSWorkspaceDidActivateApplicationNotification
        ensure_private_directory(home)
        write_last_user_app(home)  # 初始
        # 正确：addObserver 挂在 workspace 的 notificationCenter 上（workspace 本身无此方法）
        NSWorkspace.sharedWorkspace().notificationCenter().addObserverForName_object_queue_usingBlock_(
            NSWorkspaceDidActivateApplicationNotification, None, None,
            lambda _n: write_last_user_app(home))
    except Exception as exc:  # noqa: BLE001
        log.warning("install_tracker failed: %s", exc)
