"""Bounded Chrome Native Messaging bridge; no Qt, network, or credential access.

Only the installed, explicitly allowed extension origin may connect. Every body is
untrusted source material, never a command. The database is private to app home.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import select
import sqlite3
import stat
import struct
import sys
import time
import uuid
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlsplit

from haochen_app.secure_storage import ensure_private_directory

HOST_NAME = "com.haochen.browser"
MAX_FRAME = 512 * 1024
MAX_CONTENT = 60_000
MAX_SOURCES = 64
STALE_AFTER = 95
RETENTION_SECONDS = 7 * 86400
MAX_FOCUS_COMMANDS = 8
FOCUS_STATES = {
    "focused",
    "timeout",
    "not_watched",
    "target_changed",
    "disconnected",
    "permission_required",
    "tab_closed",
    "suspended",
    "error",
    "disabled",
    "busy",
}
SOURCE_ID = re.compile(r"[A-Za-z0-9_-]{8,80}\Z")
EXTENSION_ID = re.compile(r"[a-p]{32}\Z")
STATES = {"available", "permission_required", "tab_closed", "suspended", "target_changed", "error", "reading"}


def bridge_directory(home: Path) -> Path:
    # Do not silently follow an attacker-substituted app/bridge parent.
    home = Path(home).expanduser().absolute()
    for directory in (home, home / "dashboard", home / "dashboard" / "browser-bridge"):
        ensure_private_directory(directory)
        if directory.stat().st_uid != os.getuid():
            raise ValueError("browser bridge directory has a different owner")
    return home / "dashboard" / "browser-bridge"


def valid_url(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 8192:
        raise ValueError("invalid page URL")
    if any(ord(char) < 32 for char in value):
        raise ValueError("control character in URL")
    parsed = urlsplit(value)
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("only explicitly selected HTTP(S) pages are supported")
    _ = parsed.port  # Reject malformed ports. No DNS lookup and no HTTP fetch.
    return value


def identifier(value: object) -> str:
    if not isinstance(value, str) or not SOURCE_ID.fullmatch(value):
        raise ValueError("invalid source identifier")
    return value


def read_frame(stream: BinaryIO) -> dict | None:
    header = stream.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise ValueError("incomplete native message header")
    length = struct.unpack("=I", header)[0]
    if not 0 < length <= MAX_FRAME:
        raise ValueError("native message exceeds size limit")
    chunks, remaining = [], length
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise ValueError("incomplete native message")
        chunks.append(chunk)
        remaining -= len(chunk)
    result = json.loads(b"".join(chunks).decode("utf-8"))
    if not isinstance(result, dict):
        raise ValueError("native message must be an object")
    return result


def write_frame(stream: BinaryIO, value: dict) -> None:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_FRAME:
        raise ValueError("native response too large")
    stream.write(struct.pack("=I", len(payload)))
    stream.write(payload)
    stream.flush()


class PrivateSignal:
    """A one-byte wakeup, not a payload channel; works with long macOS home paths."""

    def __init__(self, directory: Path, name: str):
        self.path = directory / (identifier(name) + ".fifo")
        os.mkfifo(self.path, 0o600)  # Never reuse an existing file or symlink.
        try:
            self.fd = os.open(self.path, os.O_RDWR | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            self.path.unlink(missing_ok=True)
            raise

    def close(self):
        os.close(self.fd)
        self.path.unlink(missing_ok=True)


def notify_signal(directory: Path, name: str) -> bool:
    try:
        fd = os.open(
            directory / (identifier(name) + ".fifo"),
            os.O_WRONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            info = os.fstat(fd)
            if not stat.S_ISFIFO(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                return False
            os.write(fd, b"1")
            return True
        finally:
            os.close(fd)
    except (OSError, ValueError):
        return False


def source_target(row: dict | sqlite3.Row) -> dict:
    return {
        "kind": "browser",
        "sourceId": row["id"],
        "clientId": row["client"],
        "sessionId": row["session"],
        "url": row["url"],
        "tabId": row["tab_id"],
        "windowId": row["window_id"],
        "navigation": "exact_tab",
    }


def native_messages(stream: BinaryIO, signal):
    """Multiplex Chrome input and app clicks without threads or idle polling.

    Read the fd directly: buffered read(4) can prefetch the next native frame,
    causing select() to wait despite already-buffered bytes. BytesIO stays useful
    for deterministic protocol tests and deliberately has no async IPC path.
    """
    try:
        input_fd = stream.fileno()
    except (AttributeError, OSError, ValueError):
        while (message := read_frame(stream)) is not None:
            yield message
        return
    buffered = bytearray()
    while True:
        if len(buffered) >= 4:
            length = struct.unpack("=I", buffered[:4])[0]
            if not 0 < length <= MAX_FRAME:
                raise ValueError("native message exceeds size limit")
            if len(buffered) >= 4 + length:
                message = read_frame(io.BytesIO(buffered[: 4 + length]))
                del buffered[: 4 + length]
                yield message
                continue
        wake = signal()
        watched = [input_fd] + ([wake.fd] if wake else [])
        readable, _, _ = select.select(watched, [], [])
        if wake and wake.fd in readable:
            os.read(wake.fd, 65536)
            yield None
        if input_fd in readable:
            chunk = os.read(input_fd, 65536)
            if not chunk:
                if buffered:
                    raise ValueError("incomplete native message")
                return
            buffered.extend(chunk)


class BrowserStore:
    def __init__(self, home: Path):
        directory = bridge_directory(home)
        self.path = directory / "observations.sqlite3"
        for path in (
            self.path,
            Path(str(self.path) + "-journal"),
            Path(str(self.path) + "-wal"),
            Path(str(self.path) + "-shm"),
        ):
            if path.is_symlink():
                raise ValueError("browser storage may not be a symlink")
        if self.path.exists() and self.path.stat().st_uid != os.getuid():
            raise ValueError("browser storage has a different owner")
        # Reserve the file with private mode before SQLite opens it.
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(self.path, timeout=2)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, client TEXT NOT NULL, seen REAL NOT NULL,
                connected INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sources (
                id TEXT PRIMARY KEY, client TEXT NOT NULL, session TEXT NOT NULL,
                url TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL,
                digest TEXT NOT NULL, status TEXT NOT NULL, coverage TEXT NOT NULL,
                checked REAL NOT NULL, updated REAL NOT NULL,
                tab_id INTEGER, window_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS options (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
            INSERT OR IGNORE INTO options VALUES ('enabled', 1);
            CREATE TABLE IF NOT EXISTS focus_commands (
                id TEXT PRIMARY KEY, session TEXT NOT NULL, target TEXT NOT NULL,
                deadline REAL NOT NULL, state TEXT NOT NULL, result TEXT
            );
        """)

    def close(self):
        self.db.close()

    def connect(self, client: str, now: float | None = None) -> str:
        now = time.time() if now is None else now
        identifier(client)
        session = str(uuid.uuid4())
        old_sessions = self.db.execute("SELECT id FROM sessions WHERE client=? AND connected=1", (client,)).fetchall()
        # A profile has one native port. Reconnection retires old ports so a late
        # observation cannot rebind a source back to an obsolete process.
        for old in old_sessions:
            self.disconnect(old["id"])
        with self.db:
            if not self.db.execute("SELECT value FROM options WHERE key='enabled'").fetchone()[0]:
                raise ValueError("browser collection is disabled")
            self.db.execute("DELETE FROM sources WHERE checked < ?", (now - RETENTION_SECONDS,))
            self.db.execute(
                "DELETE FROM sessions WHERE seen < ? AND id NOT IN (SELECT session FROM sources)",
                (now - RETENTION_SECONDS,),
            )
            self.db.execute("INSERT INTO sessions VALUES (?, ?, ?, 1)", (session, client, now))
        return session

    def ping(self, session: str, now: float | None = None):
        with self.db:
            if not self.db.execute(
                "UPDATE sessions SET seen=? WHERE id=? AND connected=1",
                (time.time() if now is None else now, session),
            ).rowcount:
                raise ValueError("browser session was revoked")

    def disconnect(self, session: str):
        with self.db:
            self.db.execute("UPDATE sessions SET connected=0 WHERE id=?", (session,))
            pending = self.db.execute(
                "SELECT id FROM focus_commands WHERE session=? AND state != 'done'", (session,)
            ).fetchall()
            self.db.execute(
                "UPDATE focus_commands SET state='done', result='disconnected' WHERE session=? AND state != 'done'",
                (session,),
            )
        for row in pending:
            notify_signal(self.path.parent, row["id"])

    def set_enabled(self, enabled: bool):
        pending = []
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute("UPDATE options SET value=? WHERE key='enabled'", (int(enabled),))
            if not enabled:
                pending = self.db.execute("SELECT id FROM focus_commands WHERE state != 'done'").fetchall()
                self.db.execute("DELETE FROM sources")
                self.db.execute("UPDATE sessions SET connected=0")
                self.db.execute("UPDATE focus_commands SET state='done', result='disabled' WHERE state != 'done'")
        for row in pending:
            notify_signal(self.path.parent, row["id"])

    def forget(self, source: str, client: str):
        with self.db:
            self.db.execute("DELETE FROM sources WHERE id=? AND client=?", (identifier(source), client))

    def observe(self, value: dict, session: str, client: str, now: float | None = None):
        now = time.time() if now is None else now
        source = identifier(value.get("sourceId"))
        url = valid_url(value.get("url"))
        status = value.get("status")
        if status not in STATES:
            raise ValueError("unknown observation state")
        title, content = value.get("title", ""), value.get("content", "")
        if not isinstance(title, str) or len(title) > 500 or not isinstance(content, str) or len(content) > MAX_CONTENT:
            raise ValueError("observation text exceeds size limit")
        coverage = value.get("coverage", "none")
        if coverage not in {"main_frame_text", "main_frame_text_truncated", "none"}:
            raise ValueError("invalid coverage")
        if status != "available":
            content, coverage = "", "none"
        tab_id, window_id = value.get("tabId"), value.get("windowId")
        if any(item is not None and (type(item) is not int or not 0 <= item < 2**31) for item in (tab_id, window_id)):
            raise ValueError("invalid browser object ID")
        digest = hashlib.sha256((title + "\n" + content).encode()).hexdigest()
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if not self.db.execute("SELECT value FROM options WHERE key='enabled'").fetchone()[0]:
                raise ValueError("browser collection is disabled")
            previous = self.db.execute("SELECT * FROM sources WHERE id=?", (source,)).fetchone()
            if previous and (previous["client"] != client or previous["url"] != url):
                raise ValueError("source identity cannot change implicitly")
            if not self.db.execute(
                "SELECT 1 FROM sessions WHERE id=? AND client=? AND connected=1", (session, client)
            ).fetchone():
                raise ValueError("browser session was revoked")
            if not previous and self.db.execute("SELECT COUNT(*) FROM sources").fetchone()[0] >= MAX_SOURCES:
                raise ValueError("too many tracked pages; remove a source first")
            if previous and status != "available":
                digest = previous["digest"]
            # A status-only refresh does not manufacture a content change timestamp.
            updated = (
                now
                if status == "available" and (not previous or previous["digest"] != digest)
                else (previous["updated"] if previous else now)
            )
            self.db.execute(
                """INSERT OR REPLACE INTO sources
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    source,
                    client,
                    session,
                    url,
                    title,
                    content,
                    digest,
                    status,
                    coverage,
                    now,
                    updated,
                    tab_id,
                    window_id,
                ),
            )
            self.db.execute("UPDATE sessions SET seen=?, connected=1 WHERE id=?", (now, session))

    def records(self) -> list[dict]:
        return [
            dict(row)
            for row in self.db.execute(
                """SELECT sources.*, sessions.seen,
            sessions.connected FROM sources LEFT JOIN sessions ON sources.session=sessions.id
            ORDER BY updated DESC LIMIT ?""",
                (MAX_SOURCES,),
            )
        ]

    def latest_session(self) -> dict | None:
        row = self.db.execute("SELECT * FROM sessions ORDER BY seen DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def _focus_failure(self, target: dict, now: float) -> str | None:
        if not self.db.execute("SELECT value FROM options WHERE key='enabled'").fetchone()[0]:
            return "disabled"
        row = self.db.execute(
            """SELECT sources.*, sessions.seen, sessions.connected FROM sources
            LEFT JOIN sessions ON sources.session=sessions.id WHERE sources.id=?""",
            (target.get("sourceId"),),
        ).fetchone()
        if row is None:
            return "not_watched"
        expected = source_target(row)
        if any(target.get(key) != value for key, value in expected.items() if key != "navigation"):
            return "target_changed"
        if type(target.get("tabId")) is not int or type(target.get("windowId")) is not int:
            return "target_changed"
        if not row["connected"] or now - (row["seen"] or 0) > STALE_AFTER:
            return "disconnected"
        if row["status"] in {"permission_required", "tab_closed", "suspended", "target_changed"}:
            return row["status"]
        return None

    def queue_focus(self, command_id: str, target: dict, deadline: float) -> str | None:
        """Only explicit app actions call this; page bodies never enter this queue."""
        identifier(command_id)
        now = time.time()
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute("DELETE FROM focus_commands WHERE deadline < ?", (now,))
            self.db.execute(
                """DELETE FROM focus_commands WHERE id IN (SELECT id FROM focus_commands
                WHERE state='done' ORDER BY deadline DESC LIMIT -1 OFFSET 128)"""
            )
            failure = self._focus_failure(target, now)
            if failure:
                return failure
            if (
                self.db.execute(
                    "SELECT COUNT(*) FROM focus_commands WHERE state != 'done' AND deadline > ?", (now,)
                ).fetchone()[0]
                >= MAX_FOCUS_COMMANDS
            ):
                return "busy"
            # Rebuild from named fields; arbitrary payload/commands are not forwarded.
            row = self.db.execute("SELECT * FROM sources WHERE id=?", (target["sourceId"],)).fetchone()
            self.db.execute(
                "INSERT INTO focus_commands VALUES (?, ?, ?, ?, 'queued', NULL)",
                (command_id, target["sessionId"], json.dumps(source_target(row)), deadline),
            )
        return None

    def take_focus(self, session: str) -> list[dict]:
        commands, completed = [], []
        now = time.time()
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            for row in self.db.execute(
                "SELECT * FROM focus_commands WHERE session=? AND state='queued' LIMIT ?",
                (session, MAX_FOCUS_COMMANDS),
            ).fetchall():
                target = json.loads(row["target"])
                failure = "timeout" if row["deadline"] <= now else self._focus_failure(target, now)
                if failure:
                    self.db.execute(
                        "UPDATE focus_commands SET state='done', result=? WHERE id=?",
                        (failure, row["id"]),
                    )
                    completed.append(row["id"])
                else:
                    self.db.execute("UPDATE focus_commands SET state='sent' WHERE id=?", (row["id"],))
                    commands.append(
                        {
                            "type": "focus",
                            "commandId": row["id"],
                            "target": target,
                            "expiresAt": int(row["deadline"] * 1000),
                        }
                    )
        for command_id in completed:
            notify_signal(self.path.parent, command_id)
        return commands

    def finish_focus(self, command_id: str, session: str, status: str):
        identifier(command_id)
        if status not in FOCUS_STATES:
            raise ValueError("unknown focus result")
        with self.db:
            row = self.db.execute("SELECT * FROM focus_commands WHERE id=?", (command_id,)).fetchone()
            # Late/duplicate ack cannot revive a cancelled command; a different
            # Chrome profile cannot acknowledge this profile's work.
            if not row or row["session"] != session or row["state"] != "sent":
                return
            if row["deadline"] <= time.time():
                status = "timeout"
            elif status == "focused":
                status = self._focus_failure(json.loads(row["target"]), time.time()) or status
            self.db.execute("UPDATE focus_commands SET state='done', result=? WHERE id=?", (status, command_id))
        notify_signal(self.path.parent, command_id)

    def focus_result(self, command_id: str) -> str | None:
        row = self.db.execute("SELECT state,result FROM focus_commands WHERE id=?", (command_id,)).fetchone()
        return row["result"] if row and row["state"] == "done" else None

    def cancel_focus(self, command_id: str, status: str = "timeout"):
        with self.db:
            self.db.execute(
                "UPDATE focus_commands SET state='done', result=? WHERE id=? AND state != 'done'",
                (status, command_id),
            )


def run_browser_host(
    home: Path, argv: list[str] | None = None, stdin: BinaryIO | None = None, stdout: BinaryIO | None = None
) -> int:
    """Dispatch before GUI imports; Chrome supplies chrome-extension://<id>/ in argv."""
    argv = sys.argv[1:] if argv is None else argv
    owned_streams = []
    # PyInstaller --windowed may set sys.stdin/stdout=None even though Chrome
    # supplied valid stdio file descriptors. Duplicate only in native-host mode.
    try:
        if stdin is None:
            stdin = getattr(sys.stdin, "buffer", None)
            if stdin is None:
                stdin = os.fdopen(os.dup(0), "rb")
                owned_streams.append(stdin)
        if stdout is None:
            stdout = getattr(sys.stdout, "buffer", None)
            if stdout is None:
                stdout = os.fdopen(os.dup(1), "wb")
                owned_streams.append(stdout)
    except OSError:
        for stream in owned_streams:
            stream.close()
        return 2
    session, store, wake = None, None, None
    try:
        directory = bridge_directory(home)
        config_path = directory / "host-config.json"
        if config_path.is_symlink() or not config_path.is_file() or config_path.stat().st_size > 16_384:
            return 2
        config = json.loads(config_path.read_text())
        extension_id = config.get("extensionId", "")
        origin = f"chrome-extension://{extension_id}/"
        if not EXTENSION_ID.fullmatch(extension_id) or origin not in argv:
            return 2
        store = BrowserStore(home)
        for message in native_messages(stdin, lambda: wake):
            # Removing/replacing the host registration revokes an already-open
            # native port too; never accept more observations under an old ID.
            if (
                config_path.is_symlink()
                or not config_path.is_file()
                or config_path.stat().st_size > 16_384
                or json.loads(config_path.read_text()).get("extensionId") != extension_id
            ):
                raise ValueError("browser bridge registration revoked")
            if message is None:
                for command in store.take_focus(session):
                    write_frame(stdout, command)
                continue
            operation = message.get("type")
            if operation == "hello" and session is None:
                if message.get("version") != 1:
                    raise ValueError("unsupported browser bridge version")
                client = identifier(message.get("clientId"))
                session = store.connect(client)
                wake = PrivateSignal(directory, "host-" + session)
            elif session is None:
                raise ValueError("hello must be first")
            elif operation == "ping":
                store.ping(session)
            elif operation == "observation":
                store.observe(message, session, client)
            elif operation == "forget":
                store.forget(message.get("sourceId"), client)
            elif operation == "focus_result":
                store.finish_focus(message.get("commandId"), session, message.get("status"))
            else:
                raise ValueError("unsupported browser bridge operation")
            reply = {"type": "ack", "operation": operation, "ok": True, "version": 1}
            if operation == "forget":
                reply["sourceId"] = message["sourceId"]
            if operation == "hello":
                reply["sessionId"] = session
            write_frame(stdout, reply)
            for command in store.take_focus(session):
                write_frame(stdout, command)
        return 0
    except (OSError, ValueError, TypeError, sqlite3.Error, UnicodeError):
        # Never write exception details or observed content to Chrome/diagnostic logs.
        try:
            write_frame(stdout, {"type": "error", "ok": False, "message": "浏览器桥接消息无效或本地存储不可用"})
        except (OSError, ValueError):
            pass
        return 1
    finally:
        if store is not None:
            if session:
                store.disconnect(session)
            store.close()
        if wake is not None:
            wake.close()
        for stream in owned_streams:
            stream.close()
