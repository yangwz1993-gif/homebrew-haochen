#!/usr/bin/env python3
"""Fail when source-controlled areas contain likely credentials or private keys."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", ".python", "node_modules", "build", "dist", "pi-source"}
FORBIDDEN_NAMES = {
    "auth.json",
    "signing-key.pem",
    "signing-id.p12",
    "signing.keychain-pw",
}
FORBIDDEN_SUFFIXES = {".p12", ".pfx", ".key", ".keychain-pw"}
PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "GitHub token": re.compile(r"\bgh(?:p|o|u|s|r)_[A-Za-z0-9]{30,}\b"),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
}
TEXT_SUFFIXES = {
    "", ".cfg", ".env", ".ini", ".json", ".md", ".py", ".rb", ".sh", ".toml", ".ts", ".txt", ".yaml", ".yml"
}


def files_to_scan() -> list[Path]:
    files: list[Path] = []
    for current, dirs, names in os.walk(ROOT):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        base = Path(current)
        for name in names:
            path = base / name
            if path.stat().st_size <= 2_000_000:
                files.append(path)
    return files


def main() -> int:
    findings: list[str] = []
    for path in files_to_scan():
        relative = path.relative_to(ROOT)
        if path.name in FORBIDDEN_NAMES or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            findings.append(f"forbidden credential filename: {relative}")
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{label}: {relative}")
    if findings:
        print("Potential secrets found:", file=sys.stderr)
        for finding in findings:
            print(f"  - {finding}", file=sys.stderr)
        return 1
    print("Secret scan OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
