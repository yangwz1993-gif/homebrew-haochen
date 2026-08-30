from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("haochen_version_tool", ROOT / "scripts" / "version.py")
assert SPEC and SPEC.loader
VERSION_TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERSION_TOOL)


def test_semver_is_valid_and_has_expected_views() -> None:
    version = VERSION_TOOL.read_version()
    match = VERSION_TOOL.SEMVER_RE.fullmatch(version)
    assert match is not None
    assert VERSION_TOOL.pep440(version)
    assert VERSION_TOOL.apple_marketing(version) == ".".join(
        (match["major"], match["minor"], match["patch"])
    )
    assert VERSION_TOOL.bundle_build(version).isdigit()


def test_version_metadata_is_synchronized() -> None:
    VERSION_TOOL.check()
