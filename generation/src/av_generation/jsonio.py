"""Canonical JSON and JSONL I/O plus the dataclass <-> JSON codec used by every record.

- One JSONL line = `canonical_line(obj)`: compact JSON, sorted keys, ASCII only
  (`ensure_ascii=True`), no NaN/Infinity, then `\\n`. Files are opened in binary
  append mode, so line endings never depend on the platform.
- Documents (manifests, configs) = `document_text(obj)`: `indent=2`, sorted keys,
  `ensure_ascii=False`, trailing `\\n`, written UTF-8 with `newline="\\n"`.
- Reading is strict (`av_sound.recipe.strict_json_loads`: UTF-8, unique keys, no NaN).

The codec maps frozen dataclasses to JSON objects field by field: tuples to lists,
`StrEnum` to their values, nested dataclasses to objects, `Mapping` fields to objects.
`from_json_value(tp, value)` reverses it for the same type hints, so
`decode(encode(x)) == x` for every record.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import threading
import types
import typing
from collections.abc import Iterator, Mapping
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from av_sound.recipe import StrictJsonError, strict_json_loads


class CodecError(ValueError):
    """A JSON value does not fit the declared type."""


# ---------------------------------------------------------------------------
# Canonical text


def canonical_line(obj: object) -> bytes:
    """One canonical JSONL line (ASCII bytes ending in `\\n`)."""
    text = json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return text.encode("ascii") + b"\n"


def canonical_sha256(obj: object) -> str:
    """SHA-256 of the canonical line without its newline (the hash of a JSON value)."""
    return hashlib.sha256(canonical_line(obj)[:-1]).hexdigest()


def document_text(obj: object) -> str:
    """Pretty canonical JSON document text (indent 2, sorted keys, trailing newline)."""
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"


def write_document(path: str | os.PathLike[str], obj: object, *, exclusive: bool = False) -> str:
    """Write `document_text(obj)` (UTF-8, `\\n`) and return the file's SHA-256."""
    text = document_text(obj)
    with open(path, "x" if exclusive else "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path: str | os.PathLike[str]) -> Any:  # noqa: ANN401 - any JSON value
    """Strictly decode a JSON file."""
    try:
        return strict_json_loads(Path(path).read_bytes())
    except StrictJsonError as err:
        raise CodecError(f"{path}: {err}") from err


def file_sha256(path: str | os.PathLike[str]) -> str:
    """SHA-256 of a file's bytes."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class JsonlAppender:
    """Thread-safe append-only writer of canonical lines (flushed and fsynced per line)."""

    def __init__(self, path: str | os.PathLike[str], *, fsync: bool = True) -> None:
        self.path = Path(path)
        self._fsync = fsync
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append_obj(self, obj: object) -> bytes:
        """Append one JSON value; returns the line written."""
        line = canonical_line(obj)
        with self._lock, open(self.path, "ab") as handle:
            handle.write(line)
            handle.flush()
            if self._fsync:
                os.fsync(handle.fileno())
        return line


def iter_jsonl(path: str | os.PathLike[str]) -> Iterator[Any]:
    """Decode each line of a JSONL file strictly. A torn last line raises `CodecError`."""
    with open(path, "rb") as handle:
        for number, raw in enumerate(handle, start=1):
            if not raw.endswith(b"\n"):
                raise CodecError(f"{path}:{number}: line is not terminated (torn write?)")
            try:
                yield strict_json_loads(raw[:-1])
            except StrictJsonError as err:
                raise CodecError(f"{path}:{number}: {err}") from err


# ---------------------------------------------------------------------------
# Dataclass codec


def to_json_value(value: object) -> Any:  # noqa: ANN401 - any JSON value
    """Encode a dataclass/enum/tuple/mapping tree into plain JSON values."""
    if value is None or isinstance(value, bool | int | float | str) and not isinstance(value, Enum):
        return value
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_json_value(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(k): to_json_value(v) for k, v in value.items()}
    if isinstance(value, tuple | list):
        return [to_json_value(v) for v in value]
    raise CodecError(f"cannot encode {type(value).__name__}")


def from_json_value(tp: Any, value: Any, where: str = "$") -> Any:  # noqa: ANN401
    """Decode a JSON value into type `tp` (see the module docstring for supported types)."""
    if tp is Any:
        return value
    origin = get_origin(tp)
    if origin is Union or origin is types.UnionType:
        args = get_args(tp)
        if value is None:
            if type(None) in args:
                return None
            raise CodecError(f"{where}: null is not allowed")
        errors = []
        for arg in args:
            if arg is type(None):
                continue
            try:
                return from_json_value(arg, value, where)
            except CodecError as err:
                errors.append(str(err))
        raise CodecError("; ".join(errors))
    if origin is Literal:
        if value not in get_args(tp) or isinstance(value, bool) != any(
            isinstance(a, bool) for a in get_args(tp)
        ):
            raise CodecError(f"{where}: {value!r} is not one of {list(get_args(tp))}")
        return value
    if origin is tuple:
        args = get_args(tp)
        if not isinstance(value, list):
            raise CodecError(f"{where}: expected an array")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(from_json_value(args[0], v, f"{where}[{i}]") for i, v in enumerate(value))
        if len(args) != len(value):
            raise CodecError(f"{where}: expected {len(args)} items")
        return tuple(
            from_json_value(a, v, f"{where}[{i}]")
            for i, (a, v) in enumerate(zip(args, value, strict=True))
        )
    if origin in (dict, Mapping, typing.Mapping) or (
        isinstance(origin, type) and issubclass(origin, Mapping)
    ):
        key_type, value_type = get_args(tp)
        if not isinstance(value, dict):
            raise CodecError(f"{where}: expected an object")
        return {str(k): from_json_value(value_type, v, f"{where}.{k}") for k, v in value.items()}
    if isinstance(tp, type) and issubclass(tp, Enum):
        try:
            return tp(value)
        except ValueError as err:
            raise CodecError(f"{where}: {value!r} is not a valid {tp.__name__}") from err
    if isinstance(tp, type) and dataclasses.is_dataclass(tp):
        return decode_dataclass(tp, value, where)
    if tp is bool:
        if not isinstance(value, bool):
            raise CodecError(f"{where}: expected a boolean")
        return value
    if tp is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise CodecError(f"{where}: expected an integer")
        return value
    if tp is float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise CodecError(f"{where}: expected a number")
        return float(value)
    if tp is str:
        if not isinstance(value, str):
            raise CodecError(f"{where}: expected a string")
        return value
    raise CodecError(f"{where}: unsupported type {tp!r}")


_HINTS: dict[type, dict[str, Any]] = {}


def _hints(cls: type) -> dict[str, Any]:
    hints = _HINTS.get(cls)
    if hints is None:
        hints = get_type_hints(cls)
        _HINTS[cls] = hints
    return hints


T = typing.TypeVar("T")


def decode_dataclass(cls: type[T], value: Any, where: str = "$") -> T:  # noqa: ANN401
    """Build dataclass `cls` from a JSON object with exactly its fields."""
    if not isinstance(value, dict):
        raise CodecError(f"{where}: expected an object for {cls.__name__}")
    assert dataclasses.is_dataclass(cls)
    fields = [f for f in dataclasses.fields(cls) if f.init]
    names = {f.name for f in fields}
    extra = sorted(set(value) - names)
    missing = sorted(names - set(value))
    if extra or missing:
        raise CodecError(f"{where}: {cls.__name__} missing {missing}, unexpected {extra}")
    hints = _hints(cls)
    kwargs = {
        f.name: from_json_value(hints[f.name], value[f.name], f"{where}.{f.name}") for f in fields
    }
    return cls(**kwargs)
