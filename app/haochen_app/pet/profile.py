"""用户称呼档案（v0.1.7）：haochen_home()/user-profile.json，形如 {"name": "阿晨"}。

首次使用（档案不存在）时气泡会问一次称呼；落盘后（含主动跳过存的空名）不再问。
引擎侧 ext/index.ts 也会读这份档案，在 answer 阶段注入「用户希望被称呼为 X」。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..engine_client import haochen_home
from ..secure_storage import atomic_write_private, ensure_private_file

log = logging.getLogger("haochen.pet.profile")


def profile_path() -> Path:
    return haochen_home() / "user-profile.json"


def should_ask_name() -> bool:
    """档案不存在 = 首次使用，需要问称呼（问过/跳过都会落盘）。"""
    return not profile_path().exists()


def load_user_name() -> str:
    """读称呼；无档案/损坏/空名 → ""（静默）。"""
    try:
        path = profile_path()
        ensure_private_file(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        name = data.get("name")
        return name.strip() if isinstance(name, str) else ""
    except Exception:
        return ""


def save_user_name(name: str) -> None:
    """落盘称呼（空串 = 用户跳过，同样不再问）。"""
    try:
        atomic_write_private(
            profile_path(),
            json.dumps({"name": name}, ensure_ascii=False, separators=(",", ":")) + "\n",
        )
    except Exception as e:
        log.warning("save user profile failed: %s", e)
