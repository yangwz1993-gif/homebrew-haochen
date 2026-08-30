"""haochen App 壳 ↔ 引擎的共享 RPC 客户端（P3 三个 UI 模块共用，只读不改）。

用法：

    client = EngineClient()                    # 默认真引擎；mock=True 打 mock-engine
    client.event.connect(on_event)             # 引擎事件（流式/工具/确认请求…），dict
    client.response.connect(on_response)       # 命令响应（含 id 匹配），dict
    client.crashed.connect(on_crash)           # 进程退出/EOF（int 退出码）
    client.start()
    req_id = client.prompt("你好")             # 返回请求 id
    client.respond_ui(req_id, confirmed=True)  # 回答读屏确认
    client.abort(); client.new_session(); client.switch_session(path)
    client.get_messages(); client.get_state(); client.set_session_name(name)
    client.stop()

契约：docs/rpc-contract.md（冻结）。本模块只做传输层，不含业务逻辑。
会话列表/删除是壳侧实现（契约 §2.8）：用 list_sessions()/delete_session()。

mock 模式：EngineClient(mock=True) 启动 mock-engine/mock_engine.py（确定性假事件流），
供 UI 无成本联调；UI 也可用环境变量 HAOCHEN_MOCK=1 让上层默认走 mock。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from . import paths

PROJECT_ROOT = paths.PROJECT_ROOT
DEFAULT_ENGINE = paths.engine_binary()
DEFAULT_MOCK = paths.mock_engine()
DEFAULT_EXT = paths.ext_entry()  # haochen 专属扩展（两步协议/read_screen）


def haochen_home() -> Path:
    """数据目录：HAOCHEN_HOME 环境变量优先，默认 ~/Library/Application Support/haochen。"""
    return Path(os.environ.get(
        "HAOCHEN_HOME", Path.home() / "Library" / "Application Support" / "haochen"))


def spawn_argv(engine: Path, ext: Path | None, home: Path) -> tuple[list[str], dict, Path]:
    """契约 §1.1 冻结的启动约定：返回 (argv, env, cwd)。"""
    env = dict(os.environ)
    env["PI_CODING_AGENT_DIR"] = str(home / "agent")
    env["HAOCHEN_PET"] = "1"          # 扩展两步协议强制开关（app/ext/index.ts）
    env["HAOCHEN_HOME"] = str(home)   # 扩展读屏签名等落点与壳一致
    env["HAOCHEN_APP_PID"] = str(os.getpid())  # 读屏窗口选择：读者据此识别「前台=haochen」
    reader = paths.reader_binary()
    if reader is not None:
        env["HAOCHEN_PETREAD"] = str(reader)  # 内嵌读屏执行体（P5，不依赖 ~/.local/bin/haochen）
    argv = [str(engine), "--mode", "rpc", "--no-extensions",
            "--session-dir", str(home / "pi-sessions")]
    if ext and ext.exists():
        argv += ["-e", str(ext)]
    cwd = home / "pi-home"
    cwd.mkdir(parents=True, exist_ok=True)
    (home / "pi-sessions").mkdir(parents=True, exist_ok=True)
    return argv, env, cwd


class EngineClient(QObject):
    """引擎 RPC 客户端（Qt 信号桥，reader 线程收 JSONL）。"""

    event = pyqtSignal(dict)      # 引擎事件（无 id 的推送：message_update / tool_* / extension_ui_request …）
    response = pyqtSignal(dict)   # 命令响应（{"id","type":"response","command","success",...}）
    crashed = pyqtSignal(int)     # 引擎进程退出（退出码）

    def __init__(self, engine: Path | None = None, mock: bool | None = None,
                 ext: Path | None = DEFAULT_EXT, home: Path | None = None, parent=None):
        super().__init__(parent)
        if mock is None:
            mock = os.environ.get("HAOCHEN_MOCK") == "1"
        self._mock = mock
        self._engine = Path(engine) if engine else (DEFAULT_MOCK if mock else DEFAULT_ENGINE)
        self._ext = None if mock else ext
        self._home = Path(home) if home else haochen_home()
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._write_lock = threading.Lock()

    # ── 生命周期 ─────────────────────────────────────────────

    def start(self) -> None:
        if self._proc:
            return
        if self._mock:
            argv = [sys.executable, str(self._engine)]
            env, cwd = dict(os.environ), PROJECT_ROOT / "mock-engine"
        else:
            argv, env, cwd = spawn_argv(self._engine, self._ext, self._home)
        self._proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env, cwd=str(cwd))
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                proc.kill()

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _read_loop(self) -> None:
        proc = self._proc
        assert proc and proc.stdout
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "response":
                self.response.emit(msg)
            else:
                self.event.emit(msg)
        code = proc.wait()
        if self._proc is proc:      # stop() 主动关的不算崩溃
            self._proc = None
            self.crashed.emit(code)

    # ── 命令（契约 §2）────────────────────────────────────────

    def _send(self, msg: dict) -> str:
        if not self.alive:
            raise RuntimeError("engine not running")
        msg.setdefault("id", uuid.uuid4().hex[:12])
        with self._write_lock:
            assert self._proc and self._proc.stdin
            self._proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()
        return msg["id"]

    def prompt(self, message: str) -> str:
        return self._send({"type": "prompt", "message": message})

    def abort(self) -> str:
        return self._send({"type": "abort"})

    def new_session(self) -> str:
        return self._send({"type": "new_session"})

    def switch_session(self, session_path: str) -> str:
        return self._send({"type": "switch_session", "sessionPath": session_path})

    def get_messages(self) -> str:
        return self._send({"type": "get_messages"})

    def get_state(self) -> str:
        return self._send({"type": "get_state"})

    def set_session_name(self, name: str) -> str:
        return self._send({"type": "set_session_name", "name": name})

    def get_available_models(self) -> str:
        return self._send({"type": "get_available_models"})

    def set_model(self, provider: str, model_id: str) -> str:
        """热切换当前会话模型（同 provider 内立即生效，config/README §6）。"""
        return self._send({"type": "set_model", "provider": provider, "modelId": model_id})

    def respond_ui(self, request_id: str, confirmed: bool = None,
                   cancelled: bool = None, value: str = None) -> str:
        """回答 extension_ui_request（契约 §5.3）。confirmed/cancelled/value 按需传。"""
        msg = {"type": "extension_ui_response", "id": request_id}
        if confirmed is not None:
            msg["confirmed"] = confirmed
        if cancelled is not None:
            msg["cancelled"] = cancelled
        if value is not None:
            msg["value"] = value
        return self._send(msg)


# ── 壳侧会话列表/删除（契约 §2.8，引擎 RPC 不支持）────────────

def list_sessions(home: Path | None = None) -> list[dict]:
    """扫描 session-dir 下 *.jsonl，返回 [{path, id, timestamp, preview}]，按时间倒序。"""
    home = home or haochen_home()
    out = []
    for p in (home / "pi-sessions").glob("*.jsonl"):
        try:
            with p.open() as f:
                first = json.loads(f.readline())
                preview = ""
                for line in f:
                    msg = json.loads(line)
                    if msg.get("type") == "message" and msg.get("message", {}).get("role") == "user":
                        for c in msg["message"].get("content", []):
                            if c.get("type") == "text":
                                preview = c["text"][:60]
                                break
                        break
            out.append({"path": str(p), "id": first.get("id", ""),
                        "timestamp": first.get("timestamp", ""), "preview": preview})
        except Exception:
            continue
    out.sort(key=lambda s: s["timestamp"], reverse=True)
    return out


def delete_session(session_path: str, home: Path | None = None) -> None:
    Path(session_path).unlink(missing_ok=True)


# ── 冒烟自检 ─────────────────────────────────────────────────

if __name__ == "__main__":
    from PyQt6.QtCore import QCoreApplication, QTimer

    app = QCoreApplication(sys.argv)
    client = EngineClient(mock=True)
    done = {"n": 0}

    def on_event(ev):
        if ev.get("type") == "agent_end":
            done["n"] += 1
            app.quit()

    client.event.connect(on_event)
    client.response.connect(lambda r: print("response:", r.get("command"), r.get("success")))
    client.start()
    QTimer.singleShot(200, lambda: client.prompt("自我介绍"))  # mock: 普通对话场景
    QTimer.singleShot(20000, app.quit)  # 兜底超时
    app.exec()
    client.stop()
    print("SMOKE", "OK" if done["n"] else "FAIL(no agent_end)")
