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

import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from . import paths
from .keychain import CredentialStore, KeychainStore, MemoryCredentialStore, export_keychain_credentials
from .secure_storage import ensure_private_directory, ensure_private_file

PROJECT_ROOT = paths.PROJECT_ROOT
DEFAULT_ENGINE = paths.engine_binary()
DEFAULT_MOCK = paths.mock_engine()
DEFAULT_EXT = paths.ext_entry()  # haochen 专属扩展（分层结果协议/read_screen）


def haochen_home() -> Path:
    """数据目录：HAOCHEN_HOME 环境变量优先，默认 ~/Library/Application Support/haochen。"""
    return Path(os.environ.get(
        "HAOCHEN_HOME", Path.home() / "Library" / "Application Support" / "haochen"))


def spawn_argv(
    engine: Path,
    ext: Path | None,
    home: Path,
    credentials: CredentialStore | None = None,
) -> tuple[list[str], dict, Path]:
    """契约 §1.1 冻结的启动约定：返回 (argv, env, cwd)。"""
    env = dict(os.environ)
    env["PI_CODING_AGENT_DIR"] = str(home / "agent")
    env["HAOCHEN_PET"] = "1"          # 扩展分层结果协议开关（app/ext/index.ts）
    env["HAOCHEN_HOME"] = str(home)   # 扩展读屏签名等落点与壳一致
    env["HAOCHEN_APP_PID"] = str(os.getpid())  # 读屏窗口选择：读者据此识别「前台=haochen」
    reader = paths.reader_binary()
    if reader is not None:
        env["HAOCHEN_PETREAD"] = str(reader)  # 内嵌读屏执行体（P5，不依赖 ~/.local/bin/haochen）
    argv = [str(engine), "--mode", "rpc", "--no-extensions",
            "--session-dir", str(home / "pi-sessions")]
    if ext and ext.exists():
        argv += ["-e", str(ext)]
    ensure_private_directory(home)
    ensure_private_directory(home / "agent")
    cwd = ensure_private_directory(home / "pi-home")
    ensure_private_directory(home / "pi-sessions")
    ensure_private_directory(home / "logs")
    if credentials is not None:
        export_keychain_credentials(home / "agent" / "auth.json", env, credentials)
    return argv, env, cwd


class EngineClient(QObject):
    """引擎 RPC 客户端（Qt 信号桥，reader 线程收 JSONL）。"""

    event = pyqtSignal(dict)      # 引擎事件（无 id 的推送：message_update / tool_* / extension_ui_request …）
    response = pyqtSignal(dict)   # 命令响应（{"id","type":"response","command","success",...}）
    crashed = pyqtSignal(int)     # 引擎进程退出（退出码）
    protocol_error = pyqtSignal(str)  # 脱敏的 stdout 协议错误元数据
    diagnostic = pyqtSignal(str)      # 脱敏的 stderr/transport 诊断元数据

    def __init__(self, engine: Path | None = None, mock: bool | None = None,
                 ext: Path | None = DEFAULT_EXT, home: Path | None = None, parent=None,
                 request_timeout_s: float = 5.0, shutdown_timeout_s: float = 0.5,
                 credentials: CredentialStore | None = None):
        super().__init__(parent)
        if mock is None:
            mock = os.environ.get("HAOCHEN_MOCK") == "1"
        self._mock = mock
        self.credentials = credentials or (MemoryCredentialStore() if mock else KeychainStore())
        self._engine = Path(engine) if engine else (DEFAULT_MOCK if mock else DEFAULT_ENGINE)
        self._ext = None if mock else ext
        self._home = Path(home) if home else haochen_home()
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._diagnostic_lock = threading.Lock()
        self._pending_requests: dict[str, tuple[str, threading.Timer]] = {}
        self._request_timeout_s = request_timeout_s
        self._shutdown_timeout_s = shutdown_timeout_s
        self._stopped = threading.Event()
        self._stopped.set()

    # ── 生命周期 ─────────────────────────────────────────────

    def start(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        self._proc = None
        ensure_private_directory(self._home)
        ensure_private_directory(self._home / "logs")
        if self._mock:
            argv = [sys.executable, str(self._engine)]
            env, cwd = dict(os.environ), PROJECT_ROOT / "mock-engine"
            env["HAOCHEN_HOME"] = str(self._home)
        else:
            argv, env, cwd = spawn_argv(self._engine, self._ext, self._home, self.credentials)
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            cwd=str(cwd),
            umask=0o077,
            start_new_session=True,
        )
        self._proc = proc
        self._stopped.clear()
        self._reader = threading.Thread(target=self._read_loop, args=(proc,), daemon=True)
        self._stderr_reader = threading.Thread(target=self._stderr_loop, args=(proc,), daemon=True)
        self._reader.start()
        self._stderr_reader.start()

    def stop(self) -> None:
        """Begin shutdown and return immediately; a non-daemon reaper guarantees cleanup."""
        proc, self._proc = self._proc, None
        self._fail_all_pending("engine_stopped", "engine stopped")
        if proc is None:
            self._stopped.set()
            return
        reader, stderr_reader = self._reader, self._stderr_reader
        reaper = threading.Thread(
            target=self._shutdown_process,
            args=(proc, reader, stderr_reader),
            name="haochen-engine-reaper",
            daemon=False,
        )
        reaper.start()

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def home(self) -> Path:
        """Isolated application data root used by this client."""
        return self._home

    def _read_loop(self, proc: subprocess.Popen) -> None:
        assert proc.stdout
        for raw_line in proc.stdout:
            line = raw_line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                metadata = self._record_diagnostic("stdout-invalid-json", raw_line)
                self.protocol_error.emit(metadata)
                continue
            if not isinstance(msg, dict):
                metadata = self._record_diagnostic("stdout-non-object", raw_line)
                self.protocol_error.emit(metadata)
                continue
            if msg.get("type") == "response":
                self._complete_request(str(msg.get("id", "")))
                self.response.emit(msg)
            else:
                self.event.emit(msg)
        code = proc.wait()
        if self._proc is proc:      # stop() 主动关的不算崩溃
            self._proc = None
            self._fail_all_pending("engine_exited", f"engine exited with code {code}")
            self._stopped.set()
            self.crashed.emit(code)

    def _stderr_loop(self, proc: subprocess.Popen) -> None:
        assert proc.stderr
        for line in proc.stderr:
            if line.strip():
                self.diagnostic.emit(self._record_diagnostic("stderr", line))

    def _record_diagnostic(self, source: str, content: str) -> str:
        encoded = content.encode("utf-8", errors="replace")
        metadata = f"{source} bytes={len(encoded)} sha256={hashlib.sha256(encoded).hexdigest()[:16]}"
        log_path = self._home / "logs" / "engine.log"
        with self._diagnostic_lock:
            ensure_private_directory(log_path.parent)
            if log_path.exists() and log_path.stat().st_size >= 1_000_000:
                rotated = log_path.with_suffix(".log.1")
                rotated.unlink(missing_ok=True)
                log_path.replace(rotated)
                rotated.chmod(0o600)
            with open(  # noqa: PTH123 - opener is required to enforce 0600 at creation
                log_path,
                "a",
                encoding="utf-8",
                opener=lambda path, flags: os.open(path, flags, 0o600),
            ) as handle:
                handle.write(metadata + "\n")
            ensure_private_file(log_path)
        return metadata

    @staticmethod
    def _signal_group(proc: subprocess.Popen, sig: signal.Signals) -> None:
        try:
            os.killpg(proc.pid, sig)
        except OSError:
            try:
                proc.send_signal(sig)
            except OSError:
                pass

    @staticmethod
    def _group_exists(group_id: int) -> bool:
        try:
            os.killpg(group_id, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _shutdown_process(
        self,
        proc: subprocess.Popen,
        reader: threading.Thread | None,
        stderr_reader: threading.Thread | None,
    ) -> None:
        try:
            if proc.stdin:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            self._signal_group(proc, signal.SIGTERM)
            try:
                proc.wait(timeout=self._shutdown_timeout_s)
            except subprocess.TimeoutExpired:
                self._signal_group(proc, signal.SIGKILL)
                proc.wait(timeout=2)
            # The engine may have spawned helpers that outlive the group leader.
            deadline = time.monotonic() + self._shutdown_timeout_s
            while self._group_exists(proc.pid) and time.monotonic() < deadline:
                time.sleep(0.01)
            if self._group_exists(proc.pid):
                self._signal_group(proc, signal.SIGKILL)
        finally:
            for stream in (proc.stdout, proc.stderr):
                if stream:
                    try:
                        stream.close()
                    except OSError:
                        pass
            current = threading.current_thread()
            for thread in (reader, stderr_reader):
                if thread and thread is not current:
                    thread.join(timeout=0.2)
            self._stopped.set()

    def wait_stopped(self, timeout: float | None = None) -> bool:
        """Wait for background cleanup in tests/CLI shutdown; never call from the Qt UI thread."""
        return self._stopped.wait(timeout)

    @property
    def pending_request_count(self) -> int:
        with self._pending_lock:
            return len(self._pending_requests)

    # ── 命令（契约 §2）────────────────────────────────────────

    def _send(self, msg: dict) -> str:
        if not self.alive:
            raise RuntimeError("engine not running")
        msg.setdefault("id", uuid.uuid4().hex[:12])
        request_id = str(msg["id"])
        command = str(msg.get("type", "unknown"))
        timer = threading.Timer(self._request_timeout_s, self._request_timed_out, args=(request_id,))
        timer.daemon = True
        with self._pending_lock:
            self._pending_requests[request_id] = (command, timer)
        timer.start()
        try:
            with self._write_lock:
                assert self._proc and self._proc.stdin
                self._proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
                self._proc.stdin.flush()
        except (OSError, ValueError) as exc:
            self._settle_request(request_id, "write_failed", str(exc))
            raise RuntimeError("failed to write to engine") from exc
        return request_id

    def _complete_request(self, request_id: str) -> None:
        with self._pending_lock:
            pending = self._pending_requests.pop(request_id, None)
        if pending:
            pending[1].cancel()

    def _settle_request(self, request_id: str, error_code: str, error: str) -> None:
        with self._pending_lock:
            pending = self._pending_requests.pop(request_id, None)
        if not pending:
            return
        command, timer = pending
        timer.cancel()
        self.response.emit({
            "id": request_id,
            "type": "response",
            "command": command,
            "success": False,
            "errorCode": error_code,
            "error": error,
        })

    def _request_timed_out(self, request_id: str) -> None:
        self._settle_request(request_id, "timeout", "engine request timed out")

    def _fail_all_pending(self, error_code: str, error: str) -> None:
        with self._pending_lock:
            request_ids = list(self._pending_requests)
        for request_id in request_ids:
            self._settle_request(request_id, error_code, error)

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


@dataclass(frozen=True)
class DeletedSession:
    """A reversible session deletion kept inside haochen's private data directory."""

    original_path: Path
    recycled_path: Path


def _session_root(home: Path) -> Path:
    return ensure_private_directory(home.expanduser() / "pi-sessions").resolve(strict=True)


def _validated_session_path(session_path: str, home: Path, *, must_exist: bool) -> Path:
    root = _session_root(home)
    raw = Path(session_path).expanduser()
    if ".." in raw.parts:
        raise ValueError("session path traversal is not allowed")
    candidate = raw if raw.is_absolute() else root / raw
    if candidate.suffix != ".jsonl" or candidate.parent.resolve(strict=False) != root:
        raise ValueError("session must be a direct .jsonl child of the session directory")
    if candidate.is_symlink():
        raise ValueError("symbolic-link sessions are not allowed")
    if must_exist:
        resolved = candidate.resolve(strict=True)
        if resolved.parent != root or resolved != candidate.absolute():
            raise ValueError("session resolves outside the session directory")
        if not resolved.is_file():
            raise ValueError("session path is not a regular file")
        return resolved
    return candidate.absolute()


def _recycle_root(home: Path) -> Path:
    return ensure_private_directory(home.expanduser() / "session-recycle-bin").resolve(strict=True)


def delete_session(session_path: str, home: Path | None = None) -> DeletedSession:
    """Move one validated session into a private recycle bin and return an undo token."""
    selected_home = Path(home) if home is not None else haochen_home()
    original = _validated_session_path(session_path, selected_home, must_exist=True)
    recycle_root = _recycle_root(selected_home)
    recycled = recycle_root / f"{original.stem}-{uuid.uuid4().hex}.jsonl"
    original.replace(recycled)
    recycled.chmod(0o600)
    return DeletedSession(original_path=original, recycled_path=recycled)


def restore_session(deletion: DeletedSession, home: Path | None = None) -> Path:
    """Undo a prior deletion without ever replacing a newer session file."""
    selected_home = Path(home) if home is not None else haochen_home()
    original = _validated_session_path(str(deletion.original_path), selected_home, must_exist=False)
    recycle_root = _recycle_root(selected_home)
    recycled = deletion.recycled_path
    if recycled.is_symlink() or recycled.parent.resolve(strict=False) != recycle_root:
        raise ValueError("recycled session is outside the private recycle bin")
    recycled = recycled.resolve(strict=True)
    if original.exists():
        raise FileExistsError(original)
    recycled.replace(original)
    original.chmod(0o600)
    return original


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
