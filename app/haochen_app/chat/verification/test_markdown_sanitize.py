#!/usr/bin/env python3
"""v0.1.10 渲染加固断言：错误闭合剥离 / ==高亮==转粗体 / ===标题===转换。

    cd app && QT_QPA_PLATFORM=offscreen .venv/bin/python haochen_app/chat/verification/test_markdown_sanitize.py

覆盖（docs/v0110-todo.md 验收）：
- parse_paired 兼容 ==answer==/==/answer== 错误闭合（视同【】系），正常【】路径不受影响；
- 兜底 strip_tags 剥净 == 系整标记；
- 渲染前 _sanitize_markdown：==文字== → **文字**、===文字=== → **文字**、残留整标记剥净；
- MarkdownView 端到端：重放真机式内容（错误闭合 + ==高亮== + ●列表）→ HTML 无裸标记。
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from PyQt6.QtWidgets import QApplication

from haochen_app.conversation import parse_paired, strip_tags
from haochen_app.chat.widgets import MarkdownView, _sanitize_markdown

app = QApplication([])
_checks = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _checks.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail and not ok else ""))


# ── 解析层：错误闭合兼容 ──
check("混合闭合 【answer】…==/answer==",
      parse_paired("【answer】\n正文一段\n==/answer==", "answer") == "正文一段")
check("纯等号配对 ==answer==…==/answer==",
      parse_paired("==answer==正文==/answer==", "answer") == "正文")
check("summary 错误闭合",
      parse_paired("【summary】短结==/summary==", "summary") == "短结")
check("正常【】路径不受影响",
      parse_paired("【answer】正常详答【/answer】", "answer") == "正常详答")
check("兜底 strip_tags 剥净 == 系整标记",
      strip_tags("开头【answer】残文==/answer==尾巴==/summary==") == "开头残文尾巴")

# ── 渲染前清洗 ──
check("==高亮== → **粗体**", _sanitize_markdown("这是==重点==词") == "这是**重点**词")
check("===标题=== → **粗体**", _sanitize_markdown("===小节标题===\n正文") == "**小节标题**\n正文")
check("清洗剥残留整标记", _sanitize_markdown("正文==/answer==") == "正文")

# ── 端到端：重放真机式内容（错误闭合 + ==高亮== + ●列表）──
raw = ("【answer】\n==外形==：线条不错。\n● 要点一\n● 要点二\n"
       "==不确定的一个点==：信息有限。==/answer==")
answer = parse_paired(raw, "answer")
check("端到端解析剥净错误闭合标记",
      bool(answer) and "==/answer==" not in answer and "【" not in answer and "】" not in answer,
      answer[:40])
v = MarkdownView()
v.set_markdown(answer)
html = v.document().toHtml()
check("端到端 HTML 高亮已转粗体", html.count("font-weight:700") >= 2 and "==" not in html)
check("端到端 HTML 保留列表文本", "要点一" in html and "要点二" in html)

passed = sum(1 for _, ok in _checks if ok)
print(f"\n==== {passed}/{len(_checks)} 通过 ====")
sys.exit(0 if passed == len(_checks) else 1)
