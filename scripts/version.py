#!/usr/bin/env python3
"""Read, validate and synchronize haochen's single-source version."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "VERSION"
PYPROJECT = ROOT / "pyproject.toml"
ENGINE_MANIFEST = ROOT / "engine" / "package.json"
CASK = ROOT / "cask" / "Casks" / "haochen.rb"
CASK_TEMPLATE = ROOT / "cask" / "Casks" / "haochen.rb.template"
CASK_LEGACY_TEMPLATE = ROOT / "cask" / "Casks" / "haochen.rb.legacy.template"
SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<label>dev|alpha|beta|rc)\.(?P<sequence>0|[1-9]\d*))?$"
)


def read_version() -> str:
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not SEMVER_RE.fullmatch(version):
        raise ValueError(f"VERSION is not an accepted SemVer: {version!r}")
    return version


def pep440(version: str) -> str:
    match = SEMVER_RE.fullmatch(version)
    assert match
    core = f"{match['major']}.{match['minor']}.{match['patch']}"
    label = match["label"]
    if not label:
        return core
    marker = {"dev": ".dev", "alpha": "a", "beta": "b", "rc": "rc"}[label]
    return f"{core}{marker}{match['sequence']}"


def apple_marketing(version: str) -> str:
    return version.split("-", 1)[0]


def bundle_build(version: str) -> str:
    match = SEMVER_RE.fullmatch(version)
    assert match
    # CFBundleVersion accepts period-separated integers. Reserve 0 for a final build.
    return match["sequence"] or "0"


def engine_manifest(version: str) -> dict[str, str | bool]:
    # Deliberately omit piConfig: changing APP_NAME would change PI_CODING_AGENT_DIR
    # and break the app's existing isolated engine configuration contract.
    return {"name": "haochen-engine", "private": True, "version": version}


def sync_metadata() -> None:
    version = read_version()
    pyproject = PYPROJECT.read_text(encoding="utf-8")
    updated, count = re.subn(
        r'^version = "[^"]+"$',
        f'version = "{pep440(version)}"',
        pyproject,
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise ValueError(f"Could not update project version in {PYPROJECT}")
    PYPROJECT.write_text(updated, encoding="utf-8")
    ENGINE_MANIFEST.write_text(json.dumps(engine_manifest(version), indent=2) + "\n", encoding="utf-8")
    print(f"Synchronized Python and engine metadata for {version}")


def extract(pattern: str, text: str, source: Path) -> str:
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        raise ValueError(f"Could not find version metadata in {source}")
    return match.group(1)


def check() -> None:
    version = read_version()
    expected_pyproject = pep440(version)
    pyproject_version = extract(r'^version = "([^"]+)"$', PYPROJECT.read_text(encoding="utf-8"), PYPROJECT)
    if pyproject_version != expected_pyproject:
        raise ValueError(f"pyproject version {pyproject_version!r} != {expected_pyproject!r}")

    manifest = json.loads(ENGINE_MANIFEST.read_text(encoding="utf-8"))
    if manifest != engine_manifest(version):
        raise ValueError("engine/package.json is stale; run scripts/version.py sync")

    packaging = (ROOT / "packaging" / "build.sh").read_text(encoding="utf-8")
    required_snippets = ['VERSION_FILE="$ROOT/VERSION"', 'ENGINE_MANIFEST="$ROOT/engine/package.json"']
    missing = [snippet for snippet in required_snippets if snippet not in packaging]
    if missing:
        raise ValueError(f"packaging/build.sh does not consume version metadata: {missing}")

    for template_path in (CASK_TEMPLATE, CASK_LEGACY_TEMPLATE):
        template = template_path.read_text(encoding="utf-8")
        if "@VERSION@" not in template or "@SHA256@" not in template:
            raise ValueError(f"Cask template must contain @VERSION@ and @SHA256@: {template_path}")

    print(
        f"Version OK: semver={version}, python={expected_pyproject}, "
        f"apple={apple_marketing(version)}, build={bundle_build(version)}"
    )


def render_cask(dmg: Path, *, legacy: bool = False) -> None:
    version = read_version()
    if not dmg.is_file():
        raise FileNotFoundError(dmg)
    digest = hashlib.sha256(dmg.read_bytes()).hexdigest()
    template_path = CASK_LEGACY_TEMPLATE if legacy else CASK_TEMPLATE
    rendered = (
        template_path.read_text(encoding="utf-8")
        .replace("@VERSION@", version)
        .replace("@SHA256@", digest)
    )
    CASK.write_text(rendered, encoding="utf-8")
    mode = "legacy" if legacy else "notarized"
    print(f"Rendered {CASK.relative_to(ROOT)} ({mode}) for {dmg.name} ({digest})")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    sub.add_parser("sync")
    render = sub.add_parser("render-cask")
    render.add_argument("dmg", type=Path)
    render.add_argument(
        "--legacy", action="store_true",
        help="use the 0.1.x-style cask (postflight quarantine removal)",
    )
    args = parser.parse_args()
    try:
        if args.command == "check":
            check()
        elif args.command == "sync":
            sync_metadata()
        else:
            render_cask(args.dmg, legacy=args.legacy)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"version error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
