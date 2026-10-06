"""Archive a directory of bank runs: hash every file, then make every file read-only (#27).

`archive_tree(root, label=, created_utc=)` writes two files at the root and then removes
the write permission of every file under it (`os.chmod`; on Windows this sets the
read-only attribute):

- `archive-manifest.json` (`av-banks/archive-manifest` v1): `label`, `created_utc`,
  `files` (relative POSIX path -> SHA-256 of every other file under the root), `n_files`,
  `bytes` and `archive_sha256`;
- `archive-sha256.txt`: the archive hash, `jsonio.file_set_sha256(files)` (the shared
  file-set hash: canonical JSON of the sorted path -> hash map, so it depends on neither
  listing order nor the platform's path separator).

Everything under the root is archived: every attempt, failed ones included, with its slot
log, refusals, summary and timing, the run manifests and request logs, the register and
the summaries. `archive_problems(root)` reports a missing, changed, added or writable
file and a manifest that does not hash to the recorded archive hash. An archive is
written once; a second `archive_tree` on the same root is refused.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from av_generation.jsonio import file_set_sha256, file_sha256, read_json, write_document
from av_generation.rundir import relative_files

ARCHIVE_FORMAT: Final = "av-banks/archive-manifest"
ARCHIVE_VERSION: Final = 1
ARCHIVE_MANIFEST_NAME: Final = "archive-manifest.json"
ARCHIVE_HASH_NAME: Final = "archive-sha256.txt"
ARCHIVE_NAMES: Final = frozenset({ARCHIVE_MANIFEST_NAME, ARCHIVE_HASH_NAME})
WRITE_BITS: Final = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH


class ArchiveError(RuntimeError):
    """An archive cannot be written (already archived, nothing to archive)."""


@dataclass(frozen=True, slots=True)
class ArchiveResult:
    """What `archive_tree` wrote."""

    root: Path
    files: Mapping[str, str]
    n_files: int
    bytes: int
    archive_sha256: str
    manifest_sha256: str
    """File SHA-256 of `archive-manifest.json`."""


def is_read_only(path: str | os.PathLike[str]) -> bool:
    """True when no write permission bit is set (the read-only attribute on Windows)."""
    return not stat.S_IMODE(os.stat(path).st_mode) & WRITE_BITS


def make_read_only(path: str | os.PathLike[str]) -> None:
    """Remove every write permission bit of one file."""
    mode = stat.S_IMODE(os.stat(path).st_mode)
    os.chmod(path, mode & ~WRITE_BITS)


def make_writable(root: str | os.PathLike[str]) -> None:
    """Give the owner write permission on every file under `root` again (to delete a
    DEMO or test archive; never needed for a real one)."""
    for name in relative_files(root):
        path = Path(root).joinpath(*name.split("/"))
        os.chmod(path, stat.S_IMODE(os.stat(path).st_mode) | stat.S_IWUSR)


def archived_files(root: str | os.PathLike[str]) -> dict[str, str]:
    """Relative POSIX path -> SHA-256 of every file under `root` except the two archive
    files at the root."""
    base = Path(root)
    return {
        name: file_sha256(base.joinpath(*name.split("/")))
        for name in relative_files(base)
        if name not in ARCHIVE_NAMES
    }


def archive_tree(root: str | os.PathLike[str], *, label: str, created_utc: str) -> ArchiveResult:
    """Hash every file under `root`, write the archive manifest and hash, then make
    every file read-only (module docstring)."""
    base = Path(root)
    if any((base / name).exists() for name in ARCHIVE_NAMES):
        raise ArchiveError(f"{base} is already archived")
    files = archived_files(base)
    if not files:
        raise ArchiveError(f"{base}: nothing to archive")
    total = sum(os.path.getsize(base.joinpath(*name.split("/"))) for name in files)
    digest = file_set_sha256(files)
    manifest_sha256 = write_document(
        base / ARCHIVE_MANIFEST_NAME,
        {
            "format": ARCHIVE_FORMAT,
            "format_version": ARCHIVE_VERSION,
            "label": label,
            "created_utc": created_utc,
            "n_files": len(files),
            "bytes": total,
            "archive_sha256": digest,
            "files": files,
        },
        exclusive=True,
    )
    with open(base / ARCHIVE_HASH_NAME, "x", encoding="utf-8", newline="\n") as handle:
        handle.write(digest + "\n")
    for name in relative_files(base):
        make_read_only(base.joinpath(*name.split("/")))
    return ArchiveResult(base, files, len(files), total, digest, manifest_sha256)


def read_archive_manifest(root: str | os.PathLike[str]) -> dict[str, Any]:
    """The archive manifest at `root` (raises `ValueError` for another format)."""
    doc = read_json(Path(root) / ARCHIVE_MANIFEST_NAME)
    if not isinstance(doc, dict) or doc.get("format") != ARCHIVE_FORMAT:
        raise ValueError(f"{ARCHIVE_MANIFEST_NAME}: not an {ARCHIVE_FORMAT} document")
    if doc.get("format_version") != ARCHIVE_VERSION or not isinstance(doc.get("files"), dict):
        raise ValueError(f"{ARCHIVE_MANIFEST_NAME}: unsupported version or no file list")
    return doc


def archive_problems(root: str | os.PathLike[str]) -> tuple[str, ...]:
    """Every difference between an archive and its manifest (empty when intact)."""
    base = Path(root)
    try:
        doc = read_archive_manifest(base)
        written = (base / ARCHIVE_HASH_NAME).read_text(encoding="utf-8").strip()
    except (OSError, ValueError) as err:
        return (f"archive: {err}",)
    problems: list[str] = []
    listed: dict[str, str] = {str(k): str(v) for k, v in doc["files"].items()}
    try:
        digest = file_set_sha256(listed)
    except ValueError as err:
        return (f"archive: {err}",)
    if digest != doc.get("archive_sha256") or digest != written:
        problems.append(
            f"archive hash: manifest lists {doc.get('archive_sha256')}, {ARCHIVE_HASH_NAME} "
            f"holds {written}, the file list hashes to {digest}"
        )
    if doc.get("n_files") != len(listed):
        problems.append(f"archive: n_files {doc.get('n_files')} != {len(listed)} listed")
    found = archived_files(base)
    problems.extend(f"{name}: missing" for name in sorted(set(listed) - set(found)))
    problems.extend(f"{name}: not in the archive" for name in sorted(set(found) - set(listed)))
    problems.extend(
        f"{name}: changed since archiving"
        for name in sorted(set(listed) & set(found))
        if listed[name] != found[name]
    )
    problems.extend(
        f"{name}: writable"
        for name in relative_files(base)
        if not is_read_only(base.joinpath(*name.split("/")))
    )
    return tuple(problems)
