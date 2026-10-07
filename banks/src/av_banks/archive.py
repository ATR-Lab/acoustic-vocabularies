"""Archive a directory of bank runs: hash every file, then make the files read-only (#27).

`archive_tree(root, label=, created_utc=, append_only=())` writes two files at the root
and then removes the write permission of the archived files (`os.chmod`; on Windows this
sets the read-only attribute):

- `archive-manifest.json` (`av-banks/archive-manifest` v1): `label`, `created_utc`,
  `files` (relative POSIX path -> SHA-256 of every other file under the root at archive
  time), `n_files`, `bytes`, `archive_sha256` and `append_only` (`patterns`, and `bytes`:
  relative path -> size at archive time of each append-only log that existed then);
- `archive-sha256.txt`: the archive hash, `jsonio.file_set_sha256(files)` (the shared
  file-set hash: canonical JSON of the sorted path -> hash map, so it depends on neither
  listing order nor the platform's path separator).

Everything under the root is archived: every attempt, failed ones included, with its slot
log, refusals, summary and timing, the run manifests and request logs, the register and
the summaries. An archive is written once; a second `archive_tree` on the same root is
refused.

**Append-only logs.** `append_only` holds glob patterns of relative POSIX paths (`*`
matches within one path component), e.g. `runs/*/banks/*/amendments.jsonl`. They name
logs that may still grow after the archive: the Study B reserve rule (protocol §4) lets a
pilot session amend an archived bank, and `amend.amend_bank` appends to the bank's
`amendments.jsonl` or creates it. Such a file stays writable. The archive pins the bytes
it held at archive time (its first `bytes[path]` bytes must still hash to `files[path]`),
and a new file that matches a pattern is accepted. The lines added later are not covered
by the archive hash; their own rules check them (the amendment chain:
`pilot.check_pilot`).

**What permissions freeze.** Every other file is made read-only. Directories stay
writable: the reserve rule may have to create a bank's first `amendments.jsonl`, and
Windows does not stop a file being added to or removed from a read-only directory. So
permissions freeze file contents only. `archive_problems(root)` finds the rest by
comparing the tree with the manifest: a missing, changed or added file, an archived file
that is writable again, an append-only log whose archived bytes changed, and a manifest
that does not hash to the recorded archive hash.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
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
    """An archive cannot be written (already archived, nothing to archive, a bad
    append-only pattern)."""


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


def check_patterns(patterns: Sequence[str]) -> tuple[str, ...]:
    """Validated append-only patterns, sorted and unique (raises `ArchiveError`)."""
    if isinstance(patterns, str):
        raise ArchiveError("append_only takes a sequence of patterns, not one string")
    for pattern in patterns:
        parts = pattern.split("/") if isinstance(pattern, str) else []
        if (
            not parts
            or "\\" in pattern
            or any(part in ("", ".", "..") for part in parts)
            or matches(ARCHIVE_MANIFEST_NAME, [pattern])
            or matches(ARCHIVE_HASH_NAME, [pattern])
        ):
            raise ArchiveError(f"append-only pattern {pattern!r} must be a relative POSIX path")
    return tuple(sorted(set(patterns)))


def matches(name: str, patterns: Sequence[str]) -> bool:
    """True when the relative POSIX path `name` matches a pattern exactly (same number of
    components, each matched with `fnmatch` rules)."""
    path = PurePosixPath(name)
    return any(len(path.parts) == len(PurePosixPath(p).parts) and path.match(p) for p in patterns)


def archived_files(root: str | os.PathLike[str]) -> dict[str, str]:
    """Relative POSIX path -> SHA-256 of every file under `root` except the two archive
    files at the root."""
    base = Path(root)
    return {
        name: file_sha256(base.joinpath(*name.split("/")))
        for name in relative_files(base)
        if name not in ARCHIVE_NAMES
    }


def archive_tree(
    root: str | os.PathLike[str],
    *,
    label: str,
    created_utc: str,
    append_only: Sequence[str] = (),
) -> ArchiveResult:
    """Hash every file under `root`, write the archive manifest and hash, then make every
    file read-only except the append-only logs (module docstring)."""
    base = Path(root)
    patterns = check_patterns(append_only)
    if any((base / name).exists() for name in ARCHIVE_NAMES):
        raise ArchiveError(f"{base} is already archived")
    files = archived_files(base)
    if not files:
        raise ArchiveError(f"{base}: nothing to archive")
    sizes = {name: os.path.getsize(base.joinpath(*name.split("/"))) for name in files}
    digest = file_set_sha256(files)
    manifest_sha256 = write_document(
        base / ARCHIVE_MANIFEST_NAME,
        {
            "format": ARCHIVE_FORMAT,
            "format_version": ARCHIVE_VERSION,
            "label": label,
            "created_utc": created_utc,
            "n_files": len(files),
            "bytes": sum(sizes.values()),
            "archive_sha256": digest,
            "files": files,
            "append_only": {
                "patterns": list(patterns),
                "bytes": {name: size for name, size in sizes.items() if matches(name, patterns)},
            },
        },
        exclusive=True,
    )
    with open(base / ARCHIVE_HASH_NAME, "x", encoding="utf-8", newline="\n") as handle:
        handle.write(digest + "\n")
    for name in relative_files(base):
        if not matches(name, patterns):
            make_read_only(base.joinpath(*name.split("/")))
    return ArchiveResult(base, files, len(files), sum(sizes.values()), digest, manifest_sha256)


def read_archive_manifest(root: str | os.PathLike[str]) -> dict[str, Any]:
    """The archive manifest at `root` (raises `ValueError` for another format)."""
    doc = read_json(Path(root) / ARCHIVE_MANIFEST_NAME)
    if not isinstance(doc, dict) or doc.get("format") != ARCHIVE_FORMAT:
        raise ValueError(f"{ARCHIVE_MANIFEST_NAME}: not an {ARCHIVE_FORMAT} document")
    if doc.get("format_version") != ARCHIVE_VERSION or not isinstance(doc.get("files"), dict):
        raise ValueError(f"{ARCHIVE_MANIFEST_NAME}: unsupported version or no file list")
    section = doc.get("append_only", {"patterns": [], "bytes": {}})
    if (
        not isinstance(section, dict)
        or not isinstance(section.get("patterns"), list)
        or not all(isinstance(p, str) for p in section["patterns"])
        or not isinstance(section.get("bytes"), dict)
        or not all(
            isinstance(n, int) and not isinstance(n, bool) and n >= 0
            for n in section["bytes"].values()
        )
    ):
        raise ValueError(f"{ARCHIVE_MANIFEST_NAME}: malformed append_only section")
    return doc


def _prefix_sha256(path: Path, size: int) -> str | None:
    """SHA-256 of the first `size` bytes of a file (`None` when it is shorter)."""
    with open(path, "rb") as handle:
        data = handle.read(size)
    return hashlib.sha256(data).hexdigest() if len(data) == size else None


def archive_problems(
    root: str | os.PathLike[str], *, append_only: Sequence[str] | None = None
) -> tuple[str, ...]:
    """Every difference between an archive and its manifest (empty when intact).

    `append_only`, when given, is the policy the caller expects; a manifest that records
    other patterns is a problem (so nobody widens the policy by editing the manifest).
    """
    base = Path(root)
    try:
        doc = read_archive_manifest(base)
        written = (base / ARCHIVE_HASH_NAME).read_text(encoding="utf-8").strip()
        section = doc.get("append_only", {"patterns": [], "bytes": {}})
        patterns = check_patterns(section["patterns"])
    except (OSError, ValueError, ArchiveError) as err:
        return (f"archive: {err}",)
    problems: list[str] = []
    if append_only is not None and list(check_patterns(append_only)) != section["patterns"]:
        problems.append(
            f"archive: append-only patterns {section['patterns']} are not the expected "
            f"{list(check_patterns(append_only))}"
        )
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
    sizes: dict[str, int] = {
        str(k): int(v) for k, v in section["bytes"].items() if matches(str(k), patterns)
    }
    found = archived_files(base)
    problems.extend(f"{name}: missing" for name in sorted(set(listed) - set(found)))
    problems.extend(
        f"{name}: not in the archive"
        for name in sorted(set(found) - set(listed))
        if not matches(name, patterns)
    )
    for name in sorted(set(listed) & set(found)):
        if name in sizes:
            prefix = _prefix_sha256(base.joinpath(*name.split("/")), sizes[name])
            if prefix != listed[name]:
                problems.append(f"{name}: append-only log changed before its archived end")
        elif listed[name] != found[name]:
            problems.append(f"{name}: changed since archiving")
    frozen = sorted(
        [name for name in set(listed) & set(found) if not matches(name, patterns)]
        + [name for name in ARCHIVE_NAMES if (base / name).is_file()]
    )
    problems.extend(
        f"{name}: writable" for name in frozen if not is_read_only(base.joinpath(*name.split("/")))
    )
    return tuple(problems)
