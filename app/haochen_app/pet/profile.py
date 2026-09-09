"""用户称呼档案 v2；只把用户明确确认的称呼注入对话。

旧版只有 ``name`` 的记录仅作为迁移候选展示，绝不会静默进入模型提示词。
桌宠自身身份固定为 ``haochen``，不存放在这份用户档案里。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from ..engine_client import haochen_home
from ..secure_storage import atomic_write_private, ensure_private_file

log = logging.getLogger("haochen.pet.profile")
PROFILE_VERSION = 2


def _normalize_name(value: str) -> str:
    """Keep a display name single-line and bounded before it reaches a system prompt."""
    return " ".join(value.replace("「", "").replace("」", "").split())[:32]


def profile_path(home: Path | None = None) -> Path:
    return (Path(home) if home is not None else haochen_home()) / "user-profile.json"


def should_ask_name(home: Path | None = None) -> bool:
    """No confirmed v2 profile means onboarding must ask or reconfirm once."""
    return profile_needs_confirmation(home)


def _load_profile_data(home: Path | None = None) -> dict:
    try:
        path = profile_path(home)
        ensure_private_file(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def profile_needs_confirmation(home: Path | None = None) -> bool:
    data = _load_profile_data(home)
    return not (
        data.get("version") == PROFILE_VERSION
        and data.get("confirmed") is True
        and isinstance(data.get("name"), str)
    )


def load_name_candidate(home: Path | None = None) -> str:
    """Return a legacy/unconfirmed value for a visible migration prompt only."""
    name = _load_profile_data(home).get("name")
    return _normalize_name(name) if isinstance(name, str) else ""


def load_user_name(home: Path | None = None) -> str:
    """Read only a user-confirmed name; legacy values must never silently leak into prompts."""
    data = _load_profile_data(home)
    if data.get("version") != PROFILE_VERSION or data.get("confirmed") is not True:
        return ""
    name = data.get("name")
    return _normalize_name(name) if isinstance(name, str) else ""


def save_user_name(name: str, *, source: str = "user", home: Path | None = None) -> bool:
    """Persist a confirmed structured profile (empty name means an explicit skip)."""
    try:
        atomic_write_private(
            profile_path(home),
            json.dumps(
                {
                    "version": PROFILE_VERSION,
                    "name": _normalize_name(name),
                    "confirmed": True,
                    "source": source,
                    "updatedAt": datetime.now(UTC).isoformat(),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ) + "\n",
        )
        return True
    except Exception as e:
        log.warning("save user profile failed: %s", e)
        return False
