"""Bounded provider-specific API-key probes used before replacing a saved key."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
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
        return ValidationResult(True, "凭据验证成功；模型可用性会在首次对话时确认")
    return ValidationResult(False, f"服务返回 HTTP {status}")


def normalize_model_base_url(value: str) -> str:
    """Validate and normalize a user-owned model endpoint without accepting embedded secrets."""
    raw = value.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(raw)
    host = (parsed.hostname or "").lower()
    is_local = host in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme not in ({"http", "https"} if is_local else {"https"}):
        raise ValueError("公网模型地址必须使用 HTTPS；本机 localhost 可使用 HTTP")
    if not host:
        raise ValueError("请输入完整的模型 API 地址")
    if parsed.username or parsed.password:
        raise ValueError("URL 中不能携带账号或密钥")
    if parsed.query or parsed.fragment:
        raise ValueError("模型 API 地址不能带查询参数或锚点")
    return raw


def validate_custom_model(
    base_url: str,
    model_id: str,
    key: str,
    *,
    timeout: float = 10.0,
    api: str = "openai-completions",
) -> ValidationResult:
    """Probe an OpenAI-compatible endpoint before committing any local configuration."""
    try:
        normalized = normalize_model_base_url(base_url)
    except ValueError as exc:
        return ValidationResult(False, str(exc))
    if not model_id.strip():
        return ValidationResult(False, "请输入模型 ID")
    if not key.strip():
        return ValidationResult(False, "请输入 API Key")
    headers = {"User-Agent": "haochen/0.3.4", "Accept": "application/json"}
    if key.strip():
        headers["Authorization"] = f"Bearer {key.strip()}"
    request = urllib.request.Request(f"{normalized}/models", headers=headers)
    payload: bytes = b""
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
            if callable(reader := getattr(response, "read", None)):
                raw_payload = reader(1_000_001)
                if isinstance(raw_payload, bytes):
                    payload = raw_payload
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 405):
            return _probe_completion(normalized, model_id, headers, timeout, api)
        if exc.code in (401, 403):
            return ValidationResult(False, "Key 无效或没有访问权限")
        return ValidationResult(False, f"模型服务返回 HTTP {exc.code}")
    except (OSError, urllib.error.URLError, ValueError):
        return ValidationResult(False, "无法连接该模型地址")
    if 200 <= status < 300:
        if len(payload) > 1_000_000:
            return ValidationResult(False, "模型列表响应过大，无法安全验证")
        try:
            data = json.loads(payload) if payload else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            data = None
        entries = data.get("data") if isinstance(data, dict) else None
        if not isinstance(entries, list) or not entries:
            return ValidationResult(False, "该地址没有返回有效模型列表，请检查 API URL")
        ids = {
            item.get("id")
            for item in entries or []
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        if model_id.strip() not in ids:
            return ValidationResult(False, f"服务可连接，但没有找到模型 {model_id.strip()}")
        return _probe_completion(normalized, model_id, headers, timeout, api)
    return ValidationResult(False, f"模型服务返回 HTTP {status}")


def _probe_completion(base_url: str, model_id: str, headers: dict, timeout: float, api: str) -> ValidationResult:
    """Verify the selected model can answer, including services without /models."""
    responses = api == "openai-responses"
    body = ({"model": model_id, "input": "Reply OK.", "max_output_tokens": 32, "stream": False}
            if responses else {"model": model_id, "messages": [{"role": "user", "content": "Reply OK."}],
                               "max_tokens": 32, "stream": False})
    request = urllib.request.Request(
        base_url + ("/responses" if responses else "/chat/completions"),
        data=json.dumps(body).encode(), headers={**headers, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            return ValidationResult(False, "模型响应过大，未完成验证")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("invalid response")
        if responses:
            valid = isinstance(data.get("output"), list) and bool(data["output"]) and not data.get("error")
        else:
            choices = data.get("choices")
            valid = (isinstance(choices, list) and bool(choices) and isinstance(choices[0], dict)
                     and isinstance(choices[0].get("message"), dict) and not data.get("error"))
        if valid:
            return ValidationResult(True, "模型已完成试答，连接成功")
        return ValidationResult(False, "服务返回了异常回答，请检查模型 ID 和协议")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return ValidationResult(False, "Key 无效或没有访问该模型的权限")
        return ValidationResult(False, f"模型试答失败（HTTP {exc.code}），请检查模型和协议")
    except (OSError, ValueError, urllib.error.URLError):
        return ValidationResult(False, "模型试答未完成，请检查连接后重试")
