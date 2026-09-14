# pyright: reportMissingImports=false
"""内网模型延迟实测（手动工具，不进 CI；§8.5 的 opt-in live probe）。

用法：
    app/.venv/bin/python scripts/probe_model_latency.py [model ...]
    # 不传参数时测 codewiz 全部内置模型

对每个模型发一个极小的 chat completion（非流式），报告总耗时与 HTTP 状态。
凭证解析与 haochen 运行时完全一致：codewiz.json（key+email）+ 本机 codewiz-cc
SSO 会话 cookie（现解，不落盘）+ 适配器头。慢模型无所遁形。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))


def probe(base_url: str, model: str, headers: dict[str, str], timeout: float = 120.0) -> dict:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "用一句话介绍你自己"}],
        "max_tokens": 64, "stream": False,
    }).encode()
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions", data=body,
        headers={**headers, "Content-Type": "application/json"},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read(1_000_000)
        elapsed = time.monotonic() - started
        data = json.loads(payload)
        choices = data.get("choices") or []
        text = ""
        if choices and isinstance(choices[0], dict):
            text = str((choices[0].get("message") or {}).get("content", ""))[:40]
        return {"model": model, "ok": True, "seconds": round(elapsed, 1), "reply": text}
    except Exception as exc:  # noqa: BLE001 — 诊断工具，如实汇报
        return {"model": model, "ok": False, "seconds": round(time.monotonic() - started, 1),
                "error": str(exc)[:160]}


def main() -> int:
    from haochen_app import codewiz  # 延迟导入：pyright 只管静态可解析性

    home = Path.home() / "Library" / "Application Support" / "haochen"
    headers = codewiz.validation_headers(codewiz.saved_email(home))
    if headers is None:
        print("未检测到 codewiz 登录态（SSO cookie 缺失）；请先登录 codewiz-cc。", file=sys.stderr)
        return 2
    env = codewiz.codewiz_env(home)
    key = env.get("CODEWIZ_API_KEY", "")
    if not key:
        print("codewiz.json 里没有 apiKey；请先在向导里连接内网模型。", file=sys.stderr)
        return 2
    headers["Authorization"] = f"Bearer {key}"

    config = json.loads((ROOT / "config" / "models.json").read_text(encoding="utf-8"))
    provider = config["providers"]["codewiz"]
    base_url = provider["baseUrl"]
    models = sys.argv[1:] or [m["id"] for m in provider["models"]]

    print(f"端点：{base_url}")
    print(f"{'模型':<28} {'耗时(s)':>8}  结果")
    print("-" * 64)
    for model in models:
        result = probe(base_url, model, headers)
        if result["ok"]:
            print(f"{result['model']:<28} {result['seconds']:>8}  OK  {result['reply']}…")
        else:
            print(f"{result['model']:<28} {result['seconds']:>8}  FAIL {result['error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
