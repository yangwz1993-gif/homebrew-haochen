"""Small, dependency-free helpers for private on-disk application data."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


def ensure_private_directory(path: Path) -> Path:
    """Create a directory and repair permissions that may come from an old install."""
    path = Path(path).expanduser()
    path.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIRECTORY_MODE)
    if path.is_symlink():
        raise ValueError(f"private directory must not be a symbolic link: {path}")
    path.chmod(PRIVATE_DIRECTORY_MODE)
    return path


def ensure_private_file(path: Path) -> Path:
    """Repair permissions on an existing regular, non-symlink file."""
    path = Path(path).expanduser()
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"private path must be a regular file: {path}")
    path.chmod(PRIVATE_FILE_MODE)
    return path


def atomic_write_private(path: Path, content: str, *, encoding: str = "utf-8") -> Path:
    """Atomically replace a UTF-8 file while enforcing parent 0700 and file 0600."""
    path = Path(path).expanduser()
    parent = ensure_private_directory(path.parent)
    fd, temporary = tempfile.mkstemp(dir=parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary_path = Path(temporary)
    try:
        os.fchmod(fd, PRIVATE_FILE_MODE)
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        path.chmod(PRIVATE_FILE_MODE)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        temporary_path.unlink(missing_ok=True)
        raise
    return path
