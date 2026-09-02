"""Permissions TCC reconciliation, mocked end-to-end (P1 module)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

permissions = importlib.import_module("haochen_app.permissions")


def make_run(responses: list):
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        response = responses.pop(0) if responses else SimpleNamespace(
            returncode=0, stdout="", stderr=""
        )
        return response

    return run, calls


def test_reconcile_keeps_tcc_when_fingerprint_unchanged(monkeypatch, tmp_path: Path) -> None:
    run, calls = make_run([])
    monkeypatch.setattr(permissions, "build_fingerprint", lambda: "CERTAAA")
    monkeypatch.setattr(permissions.subprocess, "run", run)
    fp_file = tmp_path / permissions._FINGERPRINT_FILE
    fp_file.write_text("CERTAAA\n", encoding="utf-8")

    changed = permissions.reconcile_tcc_with_build(tmp_path)

    assert changed is False
    assert calls == []  # 无 tccutil 调用
    assert fp_file.read_text(encoding="utf-8") == "CERTAAA\n"


def test_reconcile_resets_and_records_on_fingerprint_change(monkeypatch, tmp_path: Path) -> None:
    run, calls = make_run([])
    monkeypatch.setattr(permissions, "build_fingerprint", lambda: "CERTBBB")
    monkeypatch.setattr(permissions.subprocess, "run", run)

    changed = permissions.reconcile_tcc_with_build(tmp_path)

    assert changed is True
    assert calls and calls[0][0] == "tccutil"
    saved = (tmp_path / permissions._FINGERPRINT_FILE).read_text(encoding="utf-8")
    assert saved == "CERTBBB\n"
    assert (tmp_path / permissions._FINGERPRINT_FILE).stat().st_mode & 0o777 == 0o600


def test_reconcile_missing_fingerprint_file_treats_as_change(monkeypatch, tmp_path: Path) -> None:
    run, calls = make_run([])
    monkeypatch.setattr(permissions, "build_fingerprint", lambda: "CERTCCC")
    monkeypatch.setattr(permissions.subprocess, "run", run)

    assert permissions.reconcile_tcc_with_build(tmp_path) is True
    assert (tmp_path / permissions._FINGERPRINT_FILE).exists()


def test_reconcile_write_failure_is_non_fatal(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(permissions, "build_fingerprint", lambda: "CERTDDD")
    monkeypatch.setattr(
        permissions.subprocess,
        "run",
        lambda cmd, **kw: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    def failing_write(path, content):
        raise OSError("read-only")

    monkeypatch.setattr(permissions, "atomic_write_private", failing_write)
    assert permissions.reconcile_tcc_with_build(tmp_path) is True  # 不抛


def test_reconcile_tolerates_read_error(monkeypatch, tmp_path: Path) -> None:
    fp_file = tmp_path / permissions._FINGERPRINT_FILE
    fp_file.symlink_to(tmp_path / "does-not-exist")  # 悬空链接 → read 报错
    monkeypatch.setattr(permissions, "build_fingerprint", lambda: "CERTEEE")
    monkeypatch.setattr(
        permissions.subprocess,
        "run",
        lambda cmd, **kw: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    assert permissions.reconcile_tcc_with_build(tmp_path) is True


def test_accessibility_granted_reads_ax_status(monkeypatch) -> None:
    import ApplicationServices  # noqa: F401 — 真实框架可用

    value = permissions.accessibility_granted()
    assert isinstance(value, bool)


def test_screen_recording_granted_reads_cg_status() -> None:
    value = permissions.screen_recording_granted()
    assert isinstance(value, bool)
