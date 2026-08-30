from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_cask_never_removes_quarantine() -> None:
    for relative in ("cask/Casks/haochen.rb", "cask/Casks/haochen.rb.template"):
        source = read(relative)
        assert "postflight" not in source
        assert "com.apple.quarantine" not in source
        assert "xattr" not in source


def test_repository_has_no_self_signing_workflow() -> None:
    assert not (ROOT / "packaging" / "setup-signing.sh").exists()
    assert not (ROOT / "app" / "haochen_app" / "app_signing_repair.py").exists()
    product_source = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for base in (ROOT / "app", ROOT / "packaging")
        for path in base.rglob("*")
        if path.is_file() and path.suffix in {".py", ".sh"}
    )
    assert "openssl req" not in product_source
    assert "signing.keychain-pw" not in product_source
    assert "--sign -" not in product_source


def test_release_build_requires_developer_id_hardened_runtime_and_notarization() -> None:
    build = read("packaging/build.sh")
    assert 'HAOCHEN_BUILD_MODE:-release' in build
    assert "HAOCHEN_SIGNING_IDENTITY" in build
    assert "Developer ID Application" in build
    assert "--options runtime" in build
    assert "--timestamp" in build
    assert "HAOCHEN_NOTARY_PROFILE" in build
    assert 'notarize.sh" app' in build
    assert 'notarize.sh" dmg' in build
    assert "codesign --verify --deep --strict" in build


def test_signing_status_accepts_developer_id_only(monkeypatch) -> None:
    status = importlib.import_module("haochen_app.signing_status")
    bundle = Path("/Applications/haochen.app")
    monkeypatch.setattr(status, "app_bundle", lambda: bundle)
    monkeypatch.setattr(
        status.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="",
            stderr=(
                "Authority=Developer ID Application: Example (TEAM123456)\n"
                "TeamIdentifier=TEAM123456\n"
            ),
        ),
    )
    assert status.is_developer_id_signed()

    monkeypatch.setattr(
        status.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="",
            stderr="Authority=haochen Local Signing\nTeamIdentifier=not set\n",
        ),
    )
    assert not status.is_developer_id_signed()
