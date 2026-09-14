"""Auto-supply CodeWiz (company internal LLM proxy) credentials to the engine.

The engine's `codewiz` provider (config/models.json) references `$CODEWIZ_*`
env vars. This module fills them at spawn time, best-effort:

* the SSO token is decoded fresh from the local codewiz-cc session each launch,
  so it never expires-in-place and is never written to disk or committed; and
* the static API key + billing email live in a private, non-repo local file
  ``<haochen_home>/codewiz.json`` (0600), so no secret ever enters git.

Everything here is read-only and failure-tolerant: if nothing is configured the
provider simply won't have credentials and won't appear in the model list.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from .secure_storage import atomic_write_private

_SESSION = Path.home() / ".cc-mirror" / "codewiz-cc" / "session.json"
_PREFIX = "common-internal-access-token-prod="
# Static adapter headers the proxy requires (缺一不可，见接入指南 1.4). The version must
# not be lower than the proxy's floor or it rejects with "客户端版本过低".
_ADAPTER_HEADERS = {
    "X-Adapter-Source": "codewiz-cli",
    "X-Adapter-Scenario": "codewiz-opencode-cli",
    "X-Adapter-Source-Version": "0.1.93",
}


def _sso_cookie() -> str | None:
    """Decode the current SSO token from the local codewiz-cc session."""
    try:
        data = json.loads(_SESSION.read_text(encoding="utf-8"))
        token = json.loads(base64.b64decode(data["accessToken"]))["accessToken"]
    except (OSError, ValueError, KeyError, json.JSONDecodeError, TypeError):
        return None
    return f"{_PREFIX}{token}" if token else None


def codewiz_env(home: Path) -> dict[str, str]:
    """Best-effort CODEWIZ_* env for the engine; empty dict if not set up."""
    env: dict[str, str] = {}
    try:
        local = json.loads((Path(home) / "codewiz.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        local = {}
    if isinstance(local, dict):
        if local.get("apiKey"):
            env["CODEWIZ_API_KEY"] = str(local["apiKey"])
        if local.get("email"):
            env["CODEWIZ_USER_EMAIL"] = str(local["email"])
    cookie = _sso_cookie()
    if cookie:
        env["CODEWIZ_SSO_COOKIE"] = cookie
    return env


def is_logged_in() -> bool:
    """Whether a live codewiz-cc SSO session is present (never reveals the token)."""
    return _sso_cookie() is not None


def validation_headers(email: str) -> dict[str, str] | None:
    """Headers to probe the CodeWiz proxy: SSO cookie + adapter headers (+ email).

    Returns None when not logged in — the proxy rejects with "无登录信息" without the
    cookie, so a missing session is surfaced as "please log in", not a bad key.
    """
    cookie = _sso_cookie()
    if not cookie:
        return None
    headers = dict(_ADAPTER_HEADERS)
    headers["Cookie"] = cookie
    if email.strip():
        headers["X-Adapter-User-Email"] = email.strip()
    return headers


def saved_email(home: Path) -> str:
    """The email previously saved in codewiz.json (empty if none). Never returns the key."""
    try:
        data = json.loads((Path(home) / "codewiz.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return ""
    return str(data.get("email", "")) if isinstance(data, dict) else ""


def credentials_ready(home: Path) -> bool:
    """Whether both the key (codewiz.json) and a live SSO cookie resolve right now."""
    env = codewiz_env(home)
    return bool(env.get("CODEWIZ_API_KEY") and env.get("CODEWIZ_SSO_COOKIE"))


def save_credentials(home: Path, api_key: str, email: str) -> Path:
    """Persist the user's own CodeWiz key + email to <home>/codewiz.json (0600, non-repo).

    This is the one file each user fills; the engine reads it at spawn to resolve
    $CODEWIZ_API_KEY / $CODEWIZ_USER_EMAIL. The short-lived SSO cookie is never stored
    here — it is decoded fresh from the codewiz-cc session on every launch.
    """
    payload = json.dumps({"apiKey": api_key.strip(), "email": email.strip()},
                         ensure_ascii=False, indent=2) + "\n"
    return atomic_write_private(Path(home) / "codewiz.json", payload)
