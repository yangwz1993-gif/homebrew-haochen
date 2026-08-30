#!/usr/bin/env python3
"""M-D 配置读写层自检（不依赖 GUI，可重复运行）。

覆盖验收要求的单元级验证：
1. 模板初始化（agent/ 缺失 → 三个配置文件从 config/ 模板生成）
2. 配置读写（默认模型/思考档/key，写盘后复读 JSON 原文确认）
3. 生效语义（同 provider 换模型 = 立即生效；换 provider / 改 key = 重启生效）
4. 损坏恢复（JSON 损坏 → ConfigCorruptError → 重置为默认 → 恢复可读）

用法（务必用临时 HAOCHEN_HOME，脚本自己也会建临时目录）：

    app/.venv/bin/python -m haochen_app.settings.selfcheck
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from haochen_app.settings.config_store import (  # noqa: E402
    EFFECT_IMMEDIATE,
    EFFECT_RESTART,
    ConfigCorruptError,
    ConfigStore,
)

_checks = 0
_failures = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _checks, _failures
    _checks += 1
    if not cond:
        _failures += 1
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  —— {detail}" if detail and not cond else ""))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="haochen-settings-selfcheck-"))
    try:
        store = ConfigStore(tmp)
        agent = tmp / "agent"

        # ── 1. 模板初始化 ─────────────────────────────────────
        created = store.ensure_initialized()
        check("初始化创建三个配置文件", len(created) == 3, f"created={created}")
        check("models/settings/auth 均存在",
              all((agent / n).exists() for n in ("models.json", "settings.json", "auth.json")))
        again = store.ensure_initialized()
        check("重复初始化幂等（不覆盖已有文件）", again == [])

        providers = store.providers()
        check("模板含 deepseek provider", [p.id for p in providers] == ["deepseek"])
        check("deepseek 含 3 个模型", len(providers[0].models) == 3)
        check("默认模型 = deepseek-v4-flash-vision-exp",
              store.default_model() == ("deepseek", "deepseek-v4-flash-vision-exp"))

        # ── 2. 模板占位 key 视为未配置 ─────────────────────────
        configured, _ = store.key_status("deepseek")
        check("模板占位 key 视为未配置", not configured)

        # ── 3. 写 key（重启生效）───────────────────────────────
        effect = store.set_key("deepseek", "sk-test-123456")
        check("写 key 返回「重启引擎后生效」", effect == EFFECT_RESTART)
        check("key_status = 已配置", store.key_status("deepseek")[0])
        on_disk = json.loads((agent / "auth.json").read_text(encoding="utf-8"))
        check("auth.json 落盘内容正确",
              on_disk["deepseek"] == {"type": "api_key", "key": "sk-test-123456"})
        check("间接引用 $ENV 状态标注",
              ConfigStore.key_status.__call__ and (lambda: (
                  store.set_key("deepseek", "$DEEPSEEK_API_KEY"),
                  "环境变量" in store.key_status("deepseek")[1]))()[1])

        # ── 4. 换默认模型 ─────────────────────────────────────
        store.set_key("deepseek", "sk-test-123456")  # 恢复明文 key
        effect = store.set_default_model("deepseek", "deepseek-v4-pro")
        check("同 provider 换模型 = 立即生效", effect == EFFECT_IMMEDIATE)
        on_disk = json.loads((agent / "settings.json").read_text(encoding="utf-8"))
        check("settings.json 落盘 defaultModel 正确", on_disk["defaultModel"] == "deepseek-v4-pro")
        check("defaultProvider 未被误改", on_disk["defaultProvider"] == "deepseek")
        check("telemetry/analytics 保持 false",
              on_disk["enableInstallTelemetry"] is False and on_disk["enableAnalytics"] is False)

        # 加第二个 provider → 换过去必须「重启生效」
        models = json.loads((agent / "models.json").read_text(encoding="utf-8"))
        models["providers"]["moonshot"] = {
            "baseUrl": "https://api.moonshot.cn/v1", "api": "openai-completions",
            "models": [{"id": "kimi-k2", "name": "Kimi K2"}],
        }
        (agent / "models.json").write_text(
            json.dumps(models, ensure_ascii=False, indent=2), encoding="utf-8")
        effect = store.set_default_model("moonshot", "kimi-k2")
        check("换 provider = 重启引擎后生效", effect == EFFECT_RESTART)
        check("providers() 列出新 provider", "moonshot" in [p.id for p in store.providers()])

        # ── 5. 思考档 ─────────────────────────────────────────
        effect = store.set_thinking_level("medium")
        check("思考档写入 = 立即生效", effect == EFFECT_IMMEDIATE)
        check("思考档复读正确", store.thinking_level() == "medium")
        try:
            store.set_thinking_level("bogus")
            check("非法思考档抛 ValueError", False)
        except ValueError:
            check("非法思考档抛 ValueError", True)

        # ── 6. 清空 key = 删除条目 ─────────────────────────────
        store.set_key("deepseek", "")
        check("清空 key 后回到未配置", not store.key_status("deepseek")[0])
        on_disk = json.loads((agent / "auth.json").read_text(encoding="utf-8"))
        check("auth.json 条目已删除", "deepseek" not in on_disk)

        # ── 7. 损坏恢复 ───────────────────────────────────────
        (agent / "settings.json").write_text("{not valid json!!!", encoding="utf-8")
        try:
            store.settings()
            check("损坏 JSON 抛 ConfigCorruptError", False)
        except ConfigCorruptError as e:
            check("损坏 JSON 抛 ConfigCorruptError", True)
            check("异常携带损坏文件路径", e.path.name == "settings.json")
        store.reset_to_default("settings.json")
        check("重置为默认后恢复可读",
              store.default_model() == ("deepseek", "deepseek-v4-flash-vision-exp"))

        # ── 8. 原子写不留 tmp 残渣 ─────────────────────────────
        leftovers = list(agent.glob(".*.tmp"))
        check("写盘后无临时文件残留", leftovers == [], f"leftovers={leftovers}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{_checks - _failures}/{_checks} 通过")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
