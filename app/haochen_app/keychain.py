"""macOS Keychain storage for provider API keys without argv or disk exposure."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Protocol

SERVICE = "com.haochen.app.api-key"
SERVICE_ENV = "HAOCHEN_KEYCHAIN_SERVICE"


class CredentialStore(Protocol):
    def get(self, provider: str) -> str | None: ...
    def set(self, provider: str, secret: str) -> None: ...
    def delete(self, provider: str) -> None: ...


class KeychainError(RuntimeError):
    pass


class KeychainStore:
    """Use Apple's security CLI; new secrets are provided on stdin, never argv."""

    def __init__(self, service: str | None = None):
        self.service = service or os.environ.get(SERVICE_ENV, SERVICE)

    def get(self, provider: str) -> str | None:
        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-s",
                self.service,
                "-a",
                provider,
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 44:  # errSecItemNotFound as returned by security(1)
            return None
        if result.returncode != 0:
            raise KeychainError("无法读取 macOS Keychain")
        return result.stdout.rstrip("\n") or None

    def set(self, provider: str, secret: str) -> None:
        if not secret:
            raise ValueError("secret must not be empty")
        command = [
            "/usr/bin/security",
            "add-generic-password",
            "-U",
            "-s",
            self.service,
            "-a",
            provider,
            "-w",  # at the end: security prompts twice; both lines come from stdin
        ]
        result = subprocess.run(
            command,
            input=f"{secret}\n{secret}\n",  # password + retype confirmation
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            raise KeychainError("无法写入 macOS Keychain")

    def delete(self, provider: str) -> None:
        result = subprocess.run(
            [
                "/usr/bin/security",
                "delete-generic-password",
                "-s",
                self.service,
                "-a",
                provider,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode not in (0, 44):
            raise KeychainError("无法删除 macOS Keychain 凭据")


class MemoryCredentialStore:
    """Deterministic, process-local backend for mock and automated tests."""

    def __init__(self):
        self.values: dict[str, str] = {}

    def get(self, provider: str) -> str | None:
        return self.values.get(provider)

    def set(self, provider: str, secret: str) -> None:
        if not secret:
            raise ValueError("secret must not be empty")
        self.values[provider] = secret

    def delete(self, provider: str) -> None:
        self.values.pop(provider, None)


def credential_env_name(provider: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", provider).strip("_").upper()
    if not normalized:
        raise ValueError("provider cannot produce an environment name")
    return f"HAOCHEN_{normalized}_API_KEY"


def export_keychain_credentials(
    auth_path: Path,
    env: dict[str, str],
    store: CredentialStore,
) -> None:
    """Resolve only haochen-owned `$ENV` references from Keychain into child env."""
    try:
        auth = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return
    if not isinstance(auth, dict):
        return
    for provider, entry in auth.items():
        if not isinstance(provider, str) or not isinstance(entry, dict):
            continue
        reference = entry.get("key")
        expected = f"${credential_env_name(provider)}"
        if reference != expected:
            continue
        secret = store.get(provider)
        if secret:
            env[expected[1:]] = secret
