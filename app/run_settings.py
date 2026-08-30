#!/usr/bin/env python3
"""独立运行 haochen 设置面板（开发/联调用）。

    export HAOCHEN_HOME=/tmp/haochen-settings-test   # 别污染真实数据目录
    app/.venv/bin/python app/run_settings.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PyQt6.QtWidgets import QApplication

from haochen_app.settings import SettingsWindow


def main() -> int:
    app = QApplication(sys.argv)
    win = SettingsWindow()
    # P4 集成时接这里：modelChanged → engine.set_model；restartRequired → 重启提示
    win.modelChanged.connect(lambda p, m: print(f"[signal] modelChanged {p}/{m}"))
    win.thinkingLevelChanged.connect(lambda lv: print(f"[signal] thinkingLevelChanged {lv}"))
    win.restartRequired.connect(lambda why: print(f"[signal] restartRequired: {why}"))
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
