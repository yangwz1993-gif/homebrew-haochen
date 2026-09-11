"""Shared, explicit-user-action credential resolution for the Connect button.

Never call this from startup, status polling, or a background refresh. Only an
explicit Connect click may ask macOS to grant access to a previously saved key.
"""

from .keychain import KeychainError, KeychainInteractionRequired, credential_env_name, read_credential_without_ui

CONNECT = "连接模型"
CONNECTING = "连接中…"
CONNECTED = "已连接 ✓"


def existing_key_for_connection(store, provider: str) -> str:
    reference = store.get_key(provider)
    if not reference:
        raise KeychainError("没有已保存的 Key，请先填写 API Key")
    if reference == f"${credential_env_name(provider)}":
        try:
            key = read_credential_without_ui(store.keychain, provider)
        except KeychainInteractionRequired:
            if not store.authorize_key(provider):
                raise KeychainInteractionRequired("授权未完成") from None
            key = read_credential_without_ui(store.keychain, provider)
        if not key:
            raise KeychainError("已保存的 Key 不可用，请填写新的 API Key")
        return key
    if reference.startswith(("$", "!")):
        # Do not execute commands or silently resolve arbitrary external secrets.
        raise KeychainError("此 Key 由外部配置管理，请在对应配置中维护")
    return reference


def connection_error(error: Exception) -> str:
    # Do not interpolate backend exceptions: they may contain credentials/URLs.
    if isinstance(error, KeychainInteractionRequired):
        return "系统授权未完成，原 Key 未改变。准备好后再点“连接模型”。"
    return "暂时无法读取已保存的 Key，原 Key 未改变。请重试，或填写新的 API Key 后连接。"
