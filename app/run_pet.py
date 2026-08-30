#!/usr/bin/env python3
"""桌宠独立运行入口（M-E）。

    cd app
    HAOCHEN_MOCK=1 .venv/bin/python run_pet.py     # mock 引擎（联调）
    .venv/bin/python run_pet.py                     # 真引擎

注意：全局热键 ⌃⌥P 需要 pyobjc + 辅助功能权限，缺则自动降级为双击唤起。
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # app/ 上 sys.path

from PyQt6.QtWidgets import QApplication

from haochen_app.pet import PetApp


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    app = QApplication(sys.argv)
    app.setApplicationName("haochen")
    app.setQuitOnLastWindowClosed(False)  # 桌宠常驻，关窗不退出
    pet_app = PetApp()
    pet_app.pet.quit_requested.connect(app.quit)
    pet_app.start()
    code = app.exec()
    pet_app.client.stop()
    return code


if __name__ == "__main__":
    sys.exit(main())
