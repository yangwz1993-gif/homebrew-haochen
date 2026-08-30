"""Packaging spike: 最小 PyQt6 窗口 + 内嵌假引擎 stdio 往返。

验证点：
- PyInstaller --onedir --windowed 直出 .app
- Info.plist LSUIElement=true（Accessory 形态，无 Dock 图标）
- .app Contents/Resources/ 内嵌可执行可被定位并 spawn
"""
import os
import subprocess
import sys

from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget


def engine_path() -> str:
    """定位内嵌的假引擎可执行。

    frozen (.app): sys.executable = SpikeProbe.app/Contents/MacOS/SpikeProbe
                   引擎放在        SpikeProbe.app/Contents/Resources/fake-engine
    脚本直跑:      与本文件同目录的 fake-engine.sh
    """
    if getattr(sys, "frozen", False):
        macos_dir = os.path.dirname(os.path.abspath(sys.executable))
        return os.path.normpath(os.path.join(macos_dir, "..", "Resources", "fake-engine"))
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake-engine.sh")


class SpikeWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Packaging Spike")
        self.label = QLabel("尚未联系引擎")
        self.button = QPushButton("Ping 引擎")
        self.button.clicked.connect(self.ping_engine)
        layout = QVBoxLayout(self)
        layout.addWidget(self.label)
        layout.addWidget(self.button)
        self.resize(360, 120)

    def ping_engine(self) -> None:
        engine = engine_path()
        if not os.path.exists(engine):
            self.label.setText(f"FAIL: 引擎不存在: {engine}")
            return
        try:
            proc = subprocess.Popen(
                [engine],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            reply, err = proc.communicate(input="PING\n", timeout=5)
            if proc.returncode == 0 and reply.strip():
                self.label.setText(f"OK 引擎回复: {reply.strip()}")
            else:
                self.label.setText(f"FAIL: rc={proc.returncode} err={err.strip()}")
        except Exception as exc:  # noqa: BLE001 - spike 里直接展示错误
            self.label.setText(f"FAIL: {exc}")


def main() -> int:
    app = QApplication(sys.argv)
    win = SpikeWindow()
    win.show()
    # 启动后自动做一次往返，双击运行时无需点按钮即可验证
    win.ping_engine()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
