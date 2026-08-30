"""Read-only code-signing diagnostics for the currently running app bundle."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def app_bundle() -> Path | None:
    """Return the containing .app when frozen, otherwise None in source mode."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent.parent
    return None


def signing_details() -> str:
    bundle = app_bundle()
    if bundle is None:
        return ""
    try:
        result = subprocess.run(
            ["/usr/bin/codesign", "-dvv", str(bundle)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout + result.stderr


def is_developer_id_signed() -> bool:
    """Accept Apple Developer ID Application signatures only, never ad-hoc/self-signed."""
    details = signing_details()
    return "Authority=Developer ID Application:" in details and "TeamIdentifier=not set" not in details


def signing_summary() -> str:
    if app_bundle() is None:
        return "开发模式（未打包）"
    if is_developer_id_signed():
        return "Developer ID 签名 ✓"
    return "未通过 Developer ID 签名（仅限开发测试）"
