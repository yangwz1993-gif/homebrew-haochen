"""macOS Keychain storage for provider API keys without argv or disk exposure."""

from __future__ import annotations

import ctypes
import json
import os
import re
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


class _NativeKeychainBackend:
    """Small Security.framework adapter that never puts secrets in argv or files."""

    ITEM_NOT_FOUND = -25300

    def __init__(self) -> None:
        security = ctypes.CDLL(
            "/System/Library/Frameworks/Security.framework/Security"
        )
        core_foundation = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        void_p = ctypes.c_void_p
        uint32 = ctypes.c_uint32
        int32 = ctypes.c_int32

        security.SecKeychainFindGenericPassword.argtypes = [
            void_p, uint32, void_p, uint32, void_p,
            ctypes.POINTER(uint32), ctypes.POINTER(void_p), ctypes.POINTER(void_p),
        ]
        security.SecKeychainFindGenericPassword.restype = int32
        security.SecKeychainAddGenericPassword.argtypes = [
            void_p, uint32, void_p, uint32, void_p, uint32, void_p,
            ctypes.POINTER(void_p),
        ]
        security.SecKeychainAddGenericPassword.restype = int32
        security.SecKeychainItemModifyAttributesAndData.argtypes = [
            void_p, void_p, uint32, void_p,
        ]
        security.SecKeychainItemModifyAttributesAndData.restype = int32
        security.SecKeychainItemDelete.argtypes = [void_p]
        security.SecKeychainItemDelete.restype = int32
        security.SecKeychainItemFreeContent.argtypes = [void_p, void_p]
        security.SecKeychainItemFreeContent.restype = int32
        core_foundation.CFRelease.argtypes = [void_p]
        core_foundation.CFRelease.restype = None
        self.security = security
        self.core_foundation = core_foundation

    @staticmethod
    def _bytes(value: str) -> tuple[bytes, ctypes.Array]:
        encoded = value.encode("utf-8")
        return encoded, ctypes.create_string_buffer(encoded)

    def _find(self, service: str, account: str) -> tuple[int, int, int, int]:
        service_bytes, service_buffer = self._bytes(service)
        account_bytes, account_buffer = self._bytes(account)
        length = ctypes.c_uint32()
        data = ctypes.c_void_p()
        item = ctypes.c_void_p()
        status = self.security.SecKeychainFindGenericPassword(
            None,
            len(service_bytes),
            ctypes.cast(service_buffer, ctypes.c_void_p),
            len(account_bytes),
            ctypes.cast(account_buffer, ctypes.c_void_p),
            ctypes.byref(length),
            ctypes.byref(data),
            ctypes.byref(item),
        )
        return status, length.value, data.value or 0, item.value or 0

    def get(self, service: str, account: str) -> str | None:
        status, length, data, item = self._find(service, account)
        if status == self.ITEM_NOT_FOUND:
            return None
        if status != 0:
            raise KeychainError("无法读取 macOS Keychain")
        try:
            return ctypes.string_at(data, length).decode("utf-8") or None
        except UnicodeDecodeError as exc:
            raise KeychainError("macOS Keychain 中的凭据格式无效") from exc
        finally:
            if data:
                self.security.SecKeychainItemFreeContent(None, ctypes.c_void_p(data))
            if item:
                self.core_foundation.CFRelease(ctypes.c_void_p(item))

    def set(self, service: str, account: str, secret: str) -> None:
        status, _length, data, item = self._find(service, account)
        if data:
            self.security.SecKeychainItemFreeContent(None, ctypes.c_void_p(data))
        secret_bytes, secret_buffer = self._bytes(secret)
        try:
            if status == 0 and item:
                result = self.security.SecKeychainItemModifyAttributesAndData(
                    ctypes.c_void_p(item),
                    None,
                    len(secret_bytes),
                    ctypes.cast(secret_buffer, ctypes.c_void_p),
                )
            elif status == self.ITEM_NOT_FOUND:
                service_bytes, service_buffer = self._bytes(service)
                account_bytes, account_buffer = self._bytes(account)
                created = ctypes.c_void_p()
                result = self.security.SecKeychainAddGenericPassword(
                    None,
                    len(service_bytes),
                    ctypes.cast(service_buffer, ctypes.c_void_p),
                    len(account_bytes),
                    ctypes.cast(account_buffer, ctypes.c_void_p),
                    len(secret_bytes),
                    ctypes.cast(secret_buffer, ctypes.c_void_p),
                    ctypes.byref(created),
                )
                if created.value:
                    self.core_foundation.CFRelease(created)
            else:
                raise KeychainError("无法写入 macOS Keychain")
        finally:
            if item:
                self.core_foundation.CFRelease(ctypes.c_void_p(item))
        if result != 0:
            raise KeychainError("无法写入 macOS Keychain")

    def delete(self, service: str, account: str) -> None:
        status, _length, data, item = self._find(service, account)
        if data:
            self.security.SecKeychainItemFreeContent(None, ctypes.c_void_p(data))
        if status == self.ITEM_NOT_FOUND:
            return
        if status != 0 or not item:
            raise KeychainError("无法删除 macOS Keychain 凭据")
        try:
            result = self.security.SecKeychainItemDelete(ctypes.c_void_p(item))
        finally:
            self.core_foundation.CFRelease(ctypes.c_void_p(item))
        if result not in (0, self.ITEM_NOT_FOUND):
            raise KeychainError("无法删除 macOS Keychain 凭据")


class KeychainStore:
    """Use Security.framework directly; secrets never enter argv or disk."""

    def __init__(self, service: str | None = None, backend=None):
        self.service = service or os.environ.get(SERVICE_ENV, SERVICE)
        self._backend = backend or _NativeKeychainBackend()

    def get(self, provider: str) -> str | None:
        return self._backend.get(self.service, provider)

    def set(self, provider: str, secret: str) -> None:
        if not secret:
            raise ValueError("secret must not be empty")
        self._backend.set(self.service, provider, secret)

    def delete(self, provider: str) -> None:
        self._backend.delete(self.service, provider)


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
