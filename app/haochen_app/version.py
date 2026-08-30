"""Application version loaded from the repository/build VERSION resource."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _version_candidates() -> tuple[Path, ...]:
    candidates = []
    if getattr(sys, "frozen", False):
        bundle = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidates.extend((bundle / "VERSION", bundle / "Resources" / "VERSION"))
    candidates.append(Path(__file__).resolve().parents[2] / "VERSION")
    return tuple(candidates)


def application_version() -> str:
    override = os.environ.get("HAOCHEN_VERSION", "").strip()
    if override:
        return override
    for path in _version_candidates():
        try:
            version = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if version:
            return version
    return "0.0.0+unknown"


__version__ = application_version()
