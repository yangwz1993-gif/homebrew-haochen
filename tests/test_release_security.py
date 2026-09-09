"""Release-channel security policy (updated for the owner-approved legacy mode).

Policy after the owner's informed decision (2026-09, see goal contract):
- `release` mode: Developer ID + hardened runtime + notarization still mandatory.
- `legacy` mode: self-signed stable identity, no notarization; the cask removes
  quarantine in postflight (same mechanism as published 0.1.x). The owner
  accepted the Gatekeeper-bypass consequence; it must stay documented.
- Hard limits that remain: no credential FILES in the repository, no leaked
  API keys, no secrets in git history, no Developer-ID-less `release` build.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_release_mode_still_requires_developer_id_hardened_runtime_and_notarization() -> None:
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


def test_legacy_mode_documents_owner_decision_and_unlock_source() -> None:
    build = read("packaging/build.sh")
    assert 'HAOCHEN_BUILD_MODE:-release' in build
    assert "legacy)" in build
    # 所有者知情决定的注释必须留在代码里
    assert "用户已知情选择此模式" in build or "owner-approved" in build
    # 钥匙串口令只允许从用户本机数据目录运行时读取，绝不硬编码
    assert "signing.keychain-pw" in build
    pw_path = "$HOME/Library/Application Support/haochen/signing/signing.keychain-pw"
    assert pw_path in build
    # 不允许把口令字面量写进脚本
    import re
    assert not re.search(r'signing\.keychain-pw"\s*\)\s*=\s*["\'][0-9a-f]{16}', build)


def test_main_cask_template_stays_notarized_only() -> None:
    source = read("cask/Casks/haochen.rb.template")
    assert "postflight" not in source
    assert "com.apple.quarantine" not in source
    assert "xattr" not in source


def test_release_preflight_independently_checks_public_distribution_gates() -> None:
    source = read("scripts/release_preflight.sh")
    assert "Developer ID Application:" in source
    assert "notarytool history" in source
    assert "codesign --verify --deep --strict" in source
    assert "CFBundleShortVersionString" in source
    assert "Contents/Resources/VERSION" in source
    assert "stapler validate" in source
    assert source.count("spctl --assess") == 2
    assert "TeamIdentifier" in source
    assert "shasum -a 256" in source
    assert "legacy" not in source


def test_v030_release_checklist_does_not_authorize_ad_hoc_distribution() -> None:
    checklist = read("docs/release-checklist.md")
    assert checklist.startswith("# v0.3.0 正式发布清单")
    assert "禁止上传 GitHub Release" in checklist
    assert "v0.3.0 不沿用" in checklist
    assert "scripts/release_preflight.sh artifacts" in checklist


def test_legacy_cask_template_documents_quarantine_removal() -> None:
    source = read("cask/Casks/haochen.rb.legacy.template")
    assert "postflight" in source
    assert "com.apple.quarantine" in source
    # 所有者决定必须写在模板注释里
    assert "所有者" in source or "owner" in source


def test_repository_has_no_credential_files() -> None:
    forbidden = list(ROOT.rglob("signing-key.pem")) + \
        list(ROOT.rglob("signing-id.p12")) + \
        list(ROOT.rglob("signing.keychain-pw")) + \
        list(ROOT.rglob("auth.json"))
    assert forbidden == []


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
