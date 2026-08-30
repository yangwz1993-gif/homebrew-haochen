#!/usr/bin/env python3
"""独立运行对话窗口（M-C）。

    app/.venv/bin/python app/run_chat.py            # 真引擎
    HAOCHEN_MOCK=1 app/.venv/bin/python app/run_chat.py   # mock 引擎（联调）
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from haochen_app.chat import ChatWindow
from haochen_app.chat.theme import app_stylesheet
from PyQt6.QtWidgets import QApplication


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("haochen")
    app.setStyleSheet(app_stylesheet())
    win = ChatWindow()
    win.start()
    win.show()
    code = app.exec()
    win.client.stop()
    return code


if __name__ == "__main__":
    sys.exit(main())
