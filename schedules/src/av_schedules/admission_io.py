"""Bounded local private I/O for the allocation admission facade (stdlib only)."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import sys
from pathlib import Path
from typing import Any, cast

from .reveal import RevealError


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse(data: bytes) -> object:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise RevealError("DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def nonfinite(_: str) -> None:
        raise RevealError("NONFINITE_JSON")

    def walk(value: object, depth: int = 0) -> None:
        if depth > 20:
            raise RevealError("JSON_DEPTH_LIMIT")
        if isinstance(value, float) and not math.isfinite(value):
            raise RevealError("NONFINITE_JSON")
        if isinstance(value, (dict, list)):
            for child in value.values() if isinstance(value, dict) else value:
                walk(child, depth + 1)

    try:
        result = cast(
            object,
            json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=nonfinite),
        )
        walk(result)
        return result
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise RevealError("INVALID_JSON") from exc


def checked_path(path: Path, *, missing: bool = False, directory: bool = False) -> Path:
    raw = str(path)
    if raw.startswith(("\\\\", "//")) or not path.is_absolute() or ".." in path.parts:
        raise RevealError("LOCAL_ABSOLUTE_PATH_REQUIRED")
    if sys.platform == "win32":
        import ctypes

        if ":" in raw[2:] or ctypes.windll.kernel32.GetDriveTypeW(path.anchor) == 4:
            raise RevealError("LOCAL_PATH_REQUIRED")
    for item in reversed([path, *path.parents]):
        try:
            info = item.lstat()
        except FileNotFoundError:
            if item == path and missing:
                return path
            raise RevealError("FILE_MISSING") from None
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise RevealError("LINK_FORBIDDEN")
        if item != path or directory:
            if not stat.S_ISDIR(info.st_mode):
                raise RevealError("DIRECTORY_REQUIRED")
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RevealError("REGULAR_UNLINKED_FILE_REQUIRED")
    return path


def read(path: Path, limit: int = 8 * 1024 * 1024) -> bytes:
    checked_path(path)
    before = path.stat()
    if before.st_size > limit:
        raise RevealError("FILE_TOO_LARGE")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    last = checked_path(path).stat()
    identities = {
        (x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns) for x in (before, opened, after, last)
    }
    if len(data) > limit or len(identities) != 1 or len(data) != before.st_size:
        raise RevealError("FILE_CHANGED")
    return data


def sync_directory(path: Path) -> None:
    if sys.platform != "win32":
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class WriterLock:
    """One operation owns the lock across replay, policy decision, append and checkpoint."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd = -1

    def __enter__(self) -> WriterLock:
        checked_path(self.path, missing=True)
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            checked_path(self.path)
            if sys.platform == "win32":
                import msvcrt

                if os.fstat(self.fd).st_size == 0:
                    os.write(self.fd, b"0")
                    os.fsync(self.fd)
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, RevealError) as exc:
            os.close(self.fd)
            self.fd = -1
            if isinstance(exc, RevealError):
                raise
            raise RevealError("WRITER_BUSY") from exc
        return self

    def __exit__(self, *_: object) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1
