"""Bounded provider-specific API-key probes used before replacing a saved key."""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass

_PROBE_URLS = {
    "deepseek": "https://api.deepseek.com/models",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4/models",
}


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    message: str


def validate_api_key(provider: str, key: str, *, timeout: float = 10.0) -> ValidationResult:
    """Validate against a fixed public endpoint; never use editable model base URLs."""
    key = key.strip()
    if len(key) < 8:
        return ValidationResult(False, "Key 格式过短")
    url = _PROBE_URLS.get(provider)
    if url is None:
        return ValidationResult(False, "该供应商暂不支持安全的自动验证")
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {key}", "User-Agent": "haochen/0.2"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return ValidationResult(False, "Key 无效或无权限")
        return ValidationResult(False, f"服务返回 HTTP {exc.code}")
    except (OSError, urllib.error.URLError):
        return ValidationResult(False, "网络连接失败，请检查网络后重试")
    if 200 <= status < 300:
        return ValidationResult(True, "连接验证成功")
    return ValidationResult(False, f"服务返回 HTTP {status}")
