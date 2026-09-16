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


@dataclass(frozen=True)
class ServingProbe:
    """服务方实测结果：向真实 API 发一个最小请求，回包里的 model 字段才是真相。"""

    ok: bool
    requested: str   # 请求时声明的模型
    served: str      # 服务方回包 model 字段（空 = 没拿到）
    message: str


def probe_serving_model(
    base_url: str,
    model_id: str,
    *,
    key: str | None = None,
    extra_headers: dict | None = None,
    timeout: float = 12.0,
) -> ServingProbe:
    """向 OpenAI 兼容端点发 max_tokens=8 的 ping，读回包的 model 字段。

    这是「我到底在跟哪个模型说话」的唯一可信来源：引擎目录/配置文件都是声明，
    只有服务方回包是事实。
    """
    base = (base_url or "").strip().rstrip("/")
    if not base or not model_id:
        return ServingProbe(False, model_id or "", "", "缺少服务地址或模型 ID")
    payload = json.dumps({
        "model": model_id,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
        "stream": False,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "haochen/0.6"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    for name, value in (extra_headers or {}).items():
        headers[name] = value
    request = urllib.request.Request(
        f"{base}/chat/completions", data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(65536)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return ServingProbe(False, model_id, "", "凭据无效或无权限（HTTP 401/403）")
        return ServingProbe(False, model_id, "", f"服务返回 HTTP {exc.code}")
    except (OSError, urllib.error.URLError):
        return ServingProbe(False, model_id, "", "网络连接失败")
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return ServingProbe(False, model_id, "", "回包不是合法 JSON")
    served = str(data.get("model") or "").strip()
    if not served:
        return ServingProbe(False, model_id, "", "回包缺少 model 字段，无法证实")
    return ServingProbe(True, model_id, served, "ok")


# 内建 provider 的默认服务地址（models.json 里 baseUrl 为空时用）
_BUILTIN_BASE_URLS = {"deepseek": "https://api.deepseek.com"}


def probe_serving_model_for(store, provider_id: str, model_id: str, *, timeout: float = 12.0) -> ServingProbe:
    """按 provider 解析真实凭证并实测服务方模型。

    codewiz 家族用 codewiz.json + SSO 会话头；其余用 Keychain 免 UI 读取；
    Gemini 协议端点暂不支持实测，如实告知。
    """
    catalog = {p.id: p for p in store.providers()}
    info = catalog.get(provider_id)
    base_url = (info.base_url if info else "") or _BUILTIN_BASE_URLS.get(provider_id, "")
    if not base_url:
        return ServingProbe(False, model_id, "", "找不到该供应商的服务地址")
    if info is not None and info.api == "google-generative-ai":
        return ServingProbe(False, model_id, "", "该供应商是 Gemini 协议，暂不支持实测")
    if provider_id.startswith("codewiz"):
        from . import codewiz
        env = codewiz.codewiz_env(store.home)
        key = env.get("CODEWIZ_API_KEY", "")
        headers = codewiz.validation_headers(env.get("CODEWIZ_USER_EMAIL", ""))
        if not key or headers is None:
            return ServingProbe(False, model_id, "", "内网凭证未就绪（请先在向导连接内网）")
        return probe_serving_model(base_url, model_id, key=key, extra_headers=headers, timeout=timeout)
    from .keychain import KeychainError, KeychainInteractionRequired, read_credential_without_ui
    try:
        key = read_credential_without_ui(store.keychain, provider_id)
    except (KeychainInteractionRequired, KeychainError):
        key = None
    if not key:
        return ServingProbe(False, model_id, "", "本地没有可读出的 Key，无法实测")
    return probe_serving_model(base_url, model_id, key=key, timeout=timeout)


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
    extra_headers: dict[str, str] | None = None,
    probe_completion_only: bool = False,
) -> ValidationResult:
    """Probe an OpenAI-compatible endpoint before committing any local configuration.

    ``extra_headers`` lets a gated provider (e.g. CodeWiz 内网) attach the auth headers
    it needs to probe successfully — SSO cookie + adapter headers — beyond the Bearer key.
    ``probe_completion_only`` skips the ``GET /models`` listing and validates straight
    through a chat completion: the CodeWiz proxy returns 500 (not 404) on ``/models`` even
    though ``/chat/completions`` works, so listing-first would spuriously fail.
    """
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
    if extra_headers:
        headers.update({k: v for k, v in extra_headers.items() if isinstance(v, str)})
    if probe_completion_only:
        return _probe_completion(normalized, model_id.strip(), headers, timeout, api)
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
