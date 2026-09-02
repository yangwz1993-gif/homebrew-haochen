#!/usr/bin/env python3
"""haochen 集成 App 唯一入口（P4）。

    cd app
    .venv/bin/python run_app.py                  # 真引擎（默认）
    HAOCHEN_MOCK=1 .venv/bin/python run_app.py   # mock 引擎（联调）

形态：Accessory（LSUIElement 在 P5 打包时生效）；桌宠常驻，对话窗口/设置按需唤起。
退出：桌宠右键「退出」。
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # app/ 上 sys.path

from haochen_app.app_shell import AppShell
from haochen_app.version import __version__
from PyQt6.QtWidgets import QApplication


def _setup_file_logging() -> None:
    """冻结形态无控制台：日志落 HAOCHEN_HOME/logs/app.log（支持排障）。"""
    from haochen_app.engine_client import haochen_home
    from haochen_app.secure_storage import ensure_private_directory, ensure_private_file
    logdir = haochen_home() / "logs"
    try:
        ensure_private_directory(haochen_home())
        ensure_private_directory(logdir)
        log_file = logdir / "app.log"
        descriptor = os.open(log_file, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        os.close(descriptor)
        ensure_private_file(log_file)
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
        logging.getLogger().addHandler(handler)
    except OSError:
        pass


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    _setup_file_logging()
    log = logging.getLogger("haochen.main")
    app = QApplication(sys.argv)
    app.setApplicationName("haochen")
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)  # 桌宠常驻：关窗不退出

    from haochen_app import paths
    log.info("frozen=%s resources=%s", paths.is_frozen(), paths.resources_dir())
    log.info("engine=%s exists=%s", paths.engine_binary(), paths.engine_binary().exists())
    log.info("ext=%s exists=%s", paths.ext_entry(), paths.ext_entry().exists())
    log.info("assets=%s files=%s", paths.pet_assets(),
             sorted(p.name for p in paths.pet_assets().glob("*.png"))
             if paths.pet_assets().exists() else "MISSING")
    log.info("reader=%s", paths.reader_binary())

    shell = AppShell()
    shell.first_run_setup()
    from haochen_app.menu_bar import install_menu_bar
    install_menu_bar(app, shell)  # task-4c：LSUIElement 下提供原生菜单栏入口
    app.aboutToQuit.connect(shell.stop)
    shell.pet.pet.quit_requested.connect(app.quit)  # 桌宠右键退出 → 整个 App
    setattr(app, "_haochen_shell", shell)  # 供设置面板「一键修复签名」触达壳层

    shell.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
