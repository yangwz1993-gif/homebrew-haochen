from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

app_version = importlib.import_module("haochen_app.version")


def test_application_version_uses_root_version(monkeypatch) -> None:
    monkeypatch.delenv("HAOCHEN_VERSION", raising=False)
    assert app_version.application_version() == (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def test_application_version_allows_packaging_override(monkeypatch) -> None:
    monkeypatch.setenv("HAOCHEN_VERSION", "9.8.7-test.1")
    reloaded = importlib.reload(app_version)
    assert reloaded.__version__ == "9.8.7-test.1"
    monkeypatch.delenv("HAOCHEN_VERSION")
    importlib.reload(app_version)
