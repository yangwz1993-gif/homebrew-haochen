#!/usr/bin/env python3
"""UI 截图验证：驱动设置面板走各修改路径，grab 存 PNG，并 dump 写盘后的 JSON 原文。

    cd app && QT_QPA_PLATFORM=offscreen .venv/bin/python haochen_app/settings/verification/capture.py

产物（本目录下）：
- 01-初始面板.png / 02-填写key后.png / 03-切换模型.png / 04-key重启提示.png / 05-损坏恢复.png
- writes.txt —— 每步操作后 cat 对应 JSON 的原文记录
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from PyQt6.QtWidgets import QApplication, QComboBox, QLineEdit  # noqa: E402

from haochen_app.settings import SettingsWindow  # noqa: E402

HERE = Path(__file__).resolve().parent
log_lines: list[str] = []


def snap(win, name: str) -> None:
    app.processEvents()
    win.grab().save(str(HERE / name))
    print("saved", name)


def dump(home: Path, title: str) -> None:
    log_lines.append(f"\n===== {title} =====")
    for n in ("settings.json", "auth.json"):
        p = home / "agent" / n
        log_lines.append(f"--- {n} ---\n{p.read_text(encoding='utf-8')}")


app = QApplication(sys.argv)
home = Path(tempfile.mkdtemp(prefix="haochen-settings-shots-"))

# 1. 初始面板（模板刚初始化，key 未配置）
win = SettingsWindow(home)
win.resize(560, 720)
win.show()
snap(win, "01-初始面板.png")
dump(home, "初始（模板初始化后）")

# 2. 填 key（第一个 QLineEdit = deepseek key 输入框）
edit = win.findChildren(QLineEdit)[0]
edit.setText("sk-screenshot-test-key")
edit.editingFinished.emit()
snap(win, "02-填写key后.png")
dump(home, "填写 deepseek key 后")

# 3. 同 provider 切换默认模型 → 立即生效
model_combo = win.findChildren(QComboBox)[1]
model_combo.setCurrentIndex(1)  # deepseek-v4-pro
snap(win, "03-切换模型.png")
dump(home, "切换默认模型为 deepseek-v4-pro 后")

# 4. 再改一次 key → 底部状态条显示「重启引擎后生效」
edit.setText("sk-screenshot-test-key-v2")
edit.editingFinished.emit()
snap(win, "04-key重启提示.png")

# 4b. 拉高窗口展示全部卡片（含主题占位）
win.resize(560, 980)
snap(win, "06-完整面板.png")

# 5. 思考档调整
think_combo = win.findChildren(QComboBox)[2]
think_combo.setCurrentIndex(3)  # medium
dump(home, "思考档改为 medium 后")

# 6. 损坏恢复：写坏 settings.json 重开面板
(home / "agent" / "settings.json").write_text("{broken", encoding="utf-8")
win2 = SettingsWindow(home)
win2.resize(560, 420)
win2.show()
snap(win2, "05-损坏恢复.png")

(HERE / "writes.txt").write_text("\n".join(log_lines), encoding="utf-8")
print("saved writes.txt")
shutil.rmtree(home, ignore_errors=True)
