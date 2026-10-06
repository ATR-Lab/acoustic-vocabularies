"""Canonical bytes, hashes and strict CSV reading shared by every analysis output.

Rules (repository convention): UTF-8 without BOM, ``\\n`` line endings, JSON with
``indent=2``, sorted keys, ``ensure_ascii=False`` and a trailing newline; CSV through
``csv.writer(lineterminator="\\n")``. The same inputs give the same bytes on every
platform. Raw files are only ever opened for reading (``read_bytes``, ``sha256_file``).
Writes into a data root go through ``paths.write_output``, which checks the area and
the SYNTHETIC/REAL watermark before calling :func:`write_bytes`.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

_CHUNK = 1 << 20


class CsvFormatError(ValueError):
    """A CSV file is not strict UTF-8 CSV with a unique, complete header and full rows."""


def sha256_bytes(data: bytes) -> str:
    """Lowercase hex SHA-256 of ``data``."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Lowercase hex SHA-256 of a file, read in binary mode (never opened for writing)."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_bytes(path: Path) -> bytes:
    """The bytes of a file, opened read-only."""
    with path.open("rb") as f:
        return f.read()


def file_entry(path: Path, name: str) -> dict[str, Any]:
    """``{"path": name, "bytes": size, "sha256": hash}`` for a manifest ``files`` list."""
    data = read_bytes(path)
    return {"path": name, "bytes": len(data), "sha256": sha256_bytes(data)}


def json_bytes(obj: object) -> bytes:
    """Indented JSON with sorted keys and a trailing newline (the repository format)."""
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def canonical_json_bytes(obj: object) -> bytes:
    """Compact canonical JSON (sorted keys, separators ``,`` and ``:``), for hashing."""
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return text.encode("utf-8")


def csv_bytes(header: Sequence[str], rows: Iterable[Sequence[str]]) -> bytes:
    """CSV with ``\\n`` line endings and minimal quoting; every row must match the header."""
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    for i, row in enumerate(rows, start=2):
        if len(row) != len(header):
            raise CsvFormatError(f"line {i}: {len(row)} fields, header has {len(header)}")
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


def parse_csv(data: bytes) -> tuple[tuple[str, ...], list[tuple[str, ...]]]:
    """Header and rows of a strict CSV document.

    Accepts UTF-8 with or without a BOM and ``\\n`` or ``\\r\\n`` line endings (the
    methodology templates use CRLF). Refuses undecodable bytes, an empty or duplicated
    header name, and rows whose field count differs from the header.
    """
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CsvFormatError(f"not UTF-8: {exc}") from exc
    try:
        records = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as exc:
        raise CsvFormatError(str(exc)) from exc
    if not records:
        raise CsvFormatError("empty file: no header")
    header = tuple(records[0])
    if any(not name for name in header) or len(set(header)) != len(header):
        raise CsvFormatError(f"header has empty or duplicate names: {header}")
    rows: list[tuple[str, ...]] = []
    for i, record in enumerate(records[1:], start=2):
        if len(record) != len(header):
            raise CsvFormatError(f"line {i}: {len(record)} fields, header has {len(header)}")
        rows.append(tuple(record))
    return header, rows


def write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically (temporary file in the same folder, then rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with tmp.open("wb") as f:
        f.write(data)
    os.replace(tmp, path)
