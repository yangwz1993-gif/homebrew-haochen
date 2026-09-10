"""macOS Keychain storage for provider API keys without argv or disk exposure."""

from __future__ import annotations

import ctypes
import json
import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

# 0.3.1 created items while the app was ad-hoc signed.  Their ACL therefore
# trusts that exact build hash and macOS displays a blocking SecurityAgent
# prompt when a later, stably signed build reads them.  Never probe that legacy
# namespace during normal startup. The v2 namespace avoids that legacy item.
# Even a stable self-signed designated requirement may have a cdhash-bound
# Keychain partition. Upgrades therefore expose explicit authorization, never
# assume silent access, and never weaken or rewrite the item's ACL.
LEGACY_SERVICE = "com.haochen.app.api-key"
SERVICE = "com.haochen.app.api-key.v2"
SERVICE_ENV = "HAOCHEN_KEYCHAIN_SERVICE"
_INTERACTION_LOCK = threading.RLock()


class CredentialStore(Protocol):
    def get(self, provider: str) -> str | None: ...
    def set(self, provider: str, secret: str) -> None: ...
    def delete(self, provider: str) -> None: ...


class KeychainError(RuntimeError):
    pass


class KeychainInteractionRequired(KeychainError):
    """The item exists but macOS would need to show authentication UI."""


class _NativeKeychainBackend:
    """Small Security.framework adapter that never puts secrets in argv or files."""

    ITEM_NOT_FOUND = -25300
    INTERACTION_NOT_ALLOWED = -25308
    AUTH_FAILED = -25293
    USER_CANCELED = -128

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
        security.SecKeychainGetUserInteractionAllowed.argtypes = [
            ctypes.POINTER(ctypes.c_ubyte)
        ]
        security.SecKeychainGetUserInteractionAllowed.restype = int32
        security.SecKeychainSetUserInteractionAllowed.argtypes = [ctypes.c_ubyte]
        security.SecKeychainSetUserInteractionAllowed.restype = int32
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

    def get_without_ui(self, service: str, account: str) -> str | None:
        """Read without allowing SecurityAgent to display an authorization dialog."""
        # An explicit authorization can remain open while the user switches
        # windows. Never let an incidental GUI status query wait behind it.
        if not _INTERACTION_LOCK.acquire(blocking=False):
            raise KeychainError("钥匙串授权正在进行中")
        try:
            previous = ctypes.c_ubyte()
            status = self.security.SecKeychainGetUserInteractionAllowed(
                ctypes.byref(previous)
            )
            if status != 0:
                raise KeychainError("无法读取 macOS Keychain 交互状态")
            status = self.security.SecKeychainSetUserInteractionAllowed(False)
            if status != 0:
                raise KeychainError("无法关闭 macOS Keychain 授权弹窗")
            try:
                status, length, data, item = self._find(service, account)
                if status == self.ITEM_NOT_FOUND:
                    return None
                if status in (
                    self.INTERACTION_NOT_ALLOWED,
                    self.AUTH_FAILED,
                    self.USER_CANCELED,
                ):
                    raise KeychainInteractionRequired(
                        "Keychain 凭据需要在设置中重新授权或填写"
                    )
                if status != 0:
                    raise KeychainError("无法读取 macOS Keychain")
                try:
                    return ctypes.string_at(data, length).decode("utf-8") or None
                except UnicodeDecodeError as exc:
                    raise KeychainError("macOS Keychain 中的凭据格式无效") from exc
                finally:
                    if data:
                        self.security.SecKeychainItemFreeContent(
                            None, ctypes.c_void_p(data)
                        )
                    if item:
                        self.core_foundation.CFRelease(ctypes.c_void_p(item))
            finally:
                self.security.SecKeychainSetUserInteractionAllowed(previous.value)
        finally:
            _INTERACTION_LOCK.release()

    def authorize(self, service: str, account: str) -> str | None:
        """Interactive read ONLY in response to the user's explicit button click."""
        with _INTERACTION_LOCK:
            previous = ctypes.c_ubyte()
            if self.security.SecKeychainGetUserInteractionAllowed(ctypes.byref(previous)) != 0:
                raise KeychainError("无法读取钥匙串状态")
            if self.security.SecKeychainSetUserInteractionAllowed(True) != 0:
                raise KeychainError("无法请求钥匙串授权")
            try:
                return self.get(service, account)
            finally:
                self.security.SecKeychainSetUserInteractionAllowed(previous.value)

    @contextmanager
    def _without_interaction(self):
        with _INTERACTION_LOCK:
            previous = ctypes.c_ubyte()
            if self.security.SecKeychainGetUserInteractionAllowed(ctypes.byref(previous)) != 0:
                raise KeychainError("无法读取钥匙串状态")
            if self.security.SecKeychainSetUserInteractionAllowed(False) != 0:
                raise KeychainError("无法设置钥匙串访问方式")
            try:
                yield
            finally:
                self.security.SecKeychainSetUserInteractionAllowed(previous.value)

    def set(self, service: str, account: str, secret: str) -> None:
        with self._without_interaction():
            self._set(service, account, secret)

    def _set(self, service: str, account: str, secret: str) -> None:
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
        with self._without_interaction():
            self._delete(service, account)

    def _delete(self, service: str, account: str) -> None:
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
        self._authorized: dict[str, str] = {}

    def get(self, provider: str) -> str | None:
        return self.get_without_ui(provider)

    def get_without_ui(self, provider: str) -> str | None:
        if provider in self._authorized:
            return self._authorized[provider]
        getter = getattr(self._backend, "get_without_ui", None)
        if getter is None:
            return self._backend.get(self.service, provider)
        return getter(self.service, provider)

    def authorize(self, provider: str) -> bool:
        secret = self._backend.authorize(self.service, provider)
        if secret:
            # Respect "Allow" for this process without demanding "Always
            # Allow". Never persist this session cache or send it to Qt UI.
            self._authorized[provider] = secret
        return bool(secret)

    def set(self, provider: str, secret: str) -> None:
        if not secret:
            raise ValueError("secret must not be empty")
        self._backend.set(self.service, provider, secret)
        if provider in self._authorized:
            self._authorized[provider] = secret

    def delete(self, provider: str) -> None:
        self._backend.delete(self.service, provider)
        self._authorized.pop(provider, None)


class MemoryCredentialStore:
    """Deterministic, process-local backend for mock and automated tests."""

    def __init__(self):
        self.values: dict[str, str] = {}

    def get(self, provider: str) -> str | None:
        return self.values.get(provider)

    def get_without_ui(self, provider: str) -> str | None:
        return self.get(provider)

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


def read_credential_without_ui(store: CredentialStore, provider: str) -> str | None:
    """Read a credential while guaranteeing native stores cannot open system UI."""
    getter = getattr(store, "get_without_ui", None)
    return getter(provider) if getter is not None else store.get(provider)


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
        try:
            secret = read_credential_without_ui(store, provider)
        except KeychainError:
            # Startup must remain usable even when an older item's ACL no
            # longer trusts this build.  Settings/onboarding can collect a new
            # key without leaving an orphaned SecurityAgent dialog behind.
            continue
        if secret:
            env[expected[1:]] = secret
