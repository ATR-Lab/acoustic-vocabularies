"""Learner (Study A) and dyad (Study B) packages: build, seal, load and leak scan (#13).

A package is the frozen, hashed bundle the experiment app loads for one Study A book or
one Study B dyad (Study A protocol §3.7; Study B protocol §4-§7). Format 1 is described by
`sound/schema/package.schema.json`; the contract for the Unity audio subsystem (#64) and
the session engine (#67) is `docs/interfaces/package-format.md`.

Study A package, built from one frozen store book (`build_package`):

    manifest.json           identity, SHA-256 and size of every other file, package hash
    answers.json            hidden-answer manifest: meaning of every atom and message
    audio.json              audio index: which WAV is which atom or trained message, and
                            the expected composite hash and length of all 32 messages
    atoms/<atom_id>.wav     16 canonical atom WAVs
    messages/<id>.wav       18 trained-message WAVs (composer, #10)

Study B package, built from one frozen dyad bank (`build_dyad_package`): the same JSON
files and `options/<profile>/<atom_id>-<rank>.wav` (16 atoms x 3 profiles x 4 options).
The app composes trained messages at run time; audio.json carries the expected composite
hash of every option combination of every message.

Held-out messages never exist as complete audio: the builder computes their hashes with
`composite_hash` and writes no samples. `seal()` fills the reserved slots after
allocation (`permutation.json` from #29, `schedules/<person_id>/<visit>.json` from #30,
`allocation.json` with the swap-dependent novel sets) and recomputes the package hash.
`load_package()` verifies a package the way #64 will; `scan_package()` is the leak scan.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, Final

from jsonschema import Draft202012Validator

from av_sound._paths import data_root
from av_sound._schemas import StrictJsonError, _registry, load_schema, strict_loads
from av_sound.composer import (
    GAP_SAMPLES,
    MOTIF_SAMPLES,
    AtomAudio,
    compose_message,
    composite_hash,
    message_length,
    write_message_wav,
)
from av_sound.dyad_bank import PROFILES, DyadBank
from av_sound.grammar import (
    ATOM_IDS,
    FAMILIES,
    HELDOUT_MESSAGE_IDS,
    HELDOUT_SETS,
    INDICES,
    MATRIX,
    MESSAGES,
    ROLES,
    TRAINED_MESSAGE_IDS,
    GrammarError,
    atom_id,
    parse_atom_id,
    parse_message_id,
)
from av_sound.renderer import RENDERER_VERSION, render
from av_sound.store import (
    SEMANTIC_LABELS,
    BookInfo,
    StoreError,
    VocabularyStore,
    canonical_json,
    snapshot_digest,
)
from av_sound.tables import SAMPLES_PER_MS
from av_sound.wav import pcm_from_wav, wav_bytes, write_wav

PACKAGE_FORMAT: Final = "av-sound/package"
PACKAGE_FORMAT_VERSION: Final = 1
ANSWERS_FORMAT: Final = "av-sound/package-answers"
AUDIO_FORMAT: Final = "av-sound/package-audio"
ALLOCATION_FORMAT: Final = "av-sound/package-allocation"
DOCUMENT_FORMAT_VERSION: Final = 1
"""Version of answers.json, audio.json and allocation.json (bumped with the package)."""
PACKAGE_BUILDER_VERSION: Final = "0.1.0"
COMPOSITION_CONTRACT: Final = "1.0.0"
"""Message byte contract version (`sound/docs/composition.md`)."""
PACKAGE_SCHEMA: Final = "package.schema.json"

PERMUTATION_FORMAT: Final = "av-schedules/permutation"
PERMUTATION_FORMAT_VERSION: Final = 2
VISIT_SCHEDULE_FORMAT: Final = "av-schedules/visit-schedule"
VISIT_SCHEDULE_FORMAT_VERSION: Final = 1
PACKAGE_HASHES_FORMAT: Final = "av-schedules/package-hashes"
PACKAGE_HASHES_FORMAT_VERSION: Final = 1
"""Run-sheet input of #32 (`schedules/schema/package-hashes.schema.json`)."""
SETS: Final[tuple[str, ...]] = ("pilot", "confirmatory")

MANIFEST: Final = "manifest.json"
ANSWERS: Final = "answers.json"
AUDIO: Final = "audio.json"
PERMUTATION: Final = "permutation.json"
ALLOCATION: Final = "allocation.json"
SCHEDULES_DIR: Final = "schedules"

STUDY_VISITS: Final[Mapping[str, tuple[str, ...]]] = {
    "A": ("D0", "D7"),
    "B": ("V1", "V2", "V3", "W1", "W4"),
}
NOVEL_SET_BY_VISIT: Final[Mapping[str, Mapping[str, str]]] = {
    "A": {"D0": "H-W1", "D7": "H-W4"},
    "B": {"V1": "H-V1", "V2": "H-V2", "V3": "H-V3", "W1": "H-W1", "W4": "H-W4"},
}
"""Held-out set tested at each visit before the H-W1/H-W4 swap (Study A protocol §4;
Protocol constants)."""
_SWAP: Final[Mapping[str, str]] = {"H-W1": "H-W4", "H-W4": "H-W1"}

# Package errors (`PackageError.code`, `PackageProblem.code`).
E_INPUT: Final = "E_INPUT"
E_POLICY: Final = "E_POLICY"
E_BOOK: Final = "E_BOOK"
E_BANK: Final = "E_BANK"
E_JSON: Final = "E_JSON"
E_SCHEMA: Final = "E_SCHEMA"
E_MANIFEST: Final = "E_MANIFEST"
E_PACKAGE_HASH: Final = "E_PACKAGE_HASH"
E_FILE_MISSING: Final = "E_FILE_MISSING"
E_FILE_EXTRA: Final = "E_FILE_EXTRA"
E_HASH_MISMATCH: Final = "E_HASH_MISMATCH"
E_WAV_FORMAT: Final = "E_WAV_FORMAT"
E_CONTENT: Final = "E_CONTENT"
E_COMPOSITE: Final = "E_COMPOSITE"
E_PERMUTATION: Final = "E_PERMUTATION"
E_MATRIX: Final = "E_MATRIX"
E_DEMO: Final = "E_DEMO"
E_SCHEDULE: Final = "E_SCHEDULE"
E_ALLOCATION: Final = "E_ALLOCATION"
E_SLOT: Final = "E_SLOT"
E_LEAK: Final = "E_LEAK"
E_INTEGRITY: Final = "E_INTEGRITY"

# Leak-scan findings (`LeakFinding.code`).
L_HELDOUT_AUDIO: Final = "E_HELDOUT_AUDIO"
L_MESSAGE_AUDIO: Final = "E_MESSAGE_AUDIO"
L_WAV_FORMAT: Final = "E_WAV_FORMAT"
L_METHOD_LABEL: Final = "E_METHOD_LABEL"
L_DESIGNER_ID: Final = "E_DESIGNER_ID"
L_METHOD_WORD: Final = "E_METHOD_WORD"
L_SOURCE_KEY: Final = "E_SOURCE_KEY"
L_FORBIDDEN_STRING: Final = "E_FORBIDDEN_STRING"
L_UNREADABLE: Final = "E_UNREADABLE"
HELDOUT_AUDIO_CODES: Final = (L_HELDOUT_AUDIO, L_MESSAGE_AUDIO)
METHOD_STRING_CODES: Final = (
    L_METHOD_LABEL,
    L_DESIGNER_ID,
    L_METHOD_WORD,
    L_SOURCE_KEY,
    L_FORBIDDEN_STRING,
)

METHOD_LABEL_TOKENS: Final = frozenset({"A1", "A2", "A3"})
"""Study A method labels. Case-sensitive: lowercase `a1` is a matrix index (`K-a1`)."""
DESIGNER_TOKENS: Final = frozenset({"D1", "D2", "D3"})
"""Anonymous hand-designer IDs (#29 batch table). Visits are D0 and D7."""
METHOD_WORDS: Final[tuple[str, ...]] = (
    "hand",
    "human",
    "design",
    "optim",
    "evolution",
    "genetic",
    "transformer",
    "llm",
    "gpt",
    "model",
    "method",
    "rating",
    "rater",
    "candidate",
    "provenance",
)
"""Substrings refused in any alphanumeric token of a package text file (case-insensitive):
the store's book-ID method words plus rating and candidate-history words."""
MIN_FORBIDDEN_LENGTH: Final = 6
"""`forbidden_strings` shorter than this are not searched (too many false matches)."""

_GAP_BYTES: Final = bytes(2 * GAP_SAMPLES)
_NONZERO: Final = re.compile(rb"[^\x00]")
_TOKEN: Final = re.compile(r"[A-Za-z0-9]+")
_SHA256: Final = re.compile(r"[0-9a-f]{64}")
_RUN_SHEET_KEYS: Final[Mapping[str, re.Pattern[str]]] = {
    "A": re.compile(r"BK-([PC])-[BCFGHJKMNPQRTVWXY4-9]{6}"),
    "B": re.compile(r"B-([PCS])[0-9]{2}"),
}
"""Study A book IDs of the learner-facing slot list (#31); Study B dyad slot IDs."""
_SET_OF_PREFIX: Final[Mapping[str, str]] = {"P": "pilot", "C": "confirmatory", "S": "confirmatory"}
_SCHEDULE_REL: Final = re.compile(
    r"(A-[PC][0-9]{2}-L[0-9]{2}/D[07]|B-[PCS][0-9]{2}-M[12]/(?:V[123]|W[14]))\.json"
)


def _atom_waves() -> dict[str, int]:
    """Wave that introduces each atom: the first wave of a trained message using it."""
    waves: dict[str, int] = {}
    for m in MESSAGES:
        wave = m.training_wave
        if wave is not None:
            for atom in (m.action.atom_id, m.referent.atom_id):
                waves[atom] = min(waves.get(atom, wave), wave)
    return {a: waves[a] for a in ATOM_IDS}


ATOM_WAVES: Final[Mapping[str, int]] = _atom_waves()
"""Atom ID -> wave 1..3 (V1: a1, a2, r1, r2; V2: a3, r3; V3: a4, r4), from the matrix."""


# ---------------------------------------------------------------------------
# Errors and results


@dataclass(frozen=True, slots=True)
class PackageProblem:
    """One problem found in a package or a seal input."""

    code: str
    path: str | None
    message: str

    def __str__(self) -> str:
        return f"{self.code} {self.path or '-'}: {self.message}"

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "path": self.path, "message": self.message}


class PackageError(Exception):
    """A package could not be built, sealed or loaded; `.code` names the reason."""

    def __init__(self, code: str, message: str, problems: Iterable[PackageProblem] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.problems = tuple(problems)

    @property
    def codes(self) -> tuple[str, ...]:
        """Distinct problem codes in first-seen order (or `(code,)`)."""
        seen = dict.fromkeys(p.code for p in self.problems)
        return tuple(seen) if seen else (self.code,)


class PackageIntegrityError(PackageError):
    """`load_package` refused a package; `.problems` lists every problem found.

    The codes match the fault #64 raises: `E_HASH_MISMATCH` (a file's bytes differ from
    the manifest), `E_FILE_MISSING`, `E_FILE_EXTRA`, `E_PACKAGE_HASH`, `E_WAV_FORMAT`,
    `E_MANIFEST`, `E_JSON`, `E_SCHEMA`, `E_CONTENT`, `E_COMPOSITE` and the slot codes.
    """

    def __init__(self, path: Path, problems: Iterable[PackageProblem]) -> None:
        found = tuple(problems)
        shown = "; ".join(str(p) for p in found[:5])
        more = f" (+{len(found) - 5} more)" if len(found) > 5 else ""
        super().__init__(E_INTEGRITY, f"package {path} refused: {shown}{more}", found)
        self.path = path


@dataclass(frozen=True, slots=True)
class LeakFinding:
    """One leak-scan finding: held-out or unlisted message audio, or a forbidden string."""

    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(frozen=True, slots=True)
class LeakReport:
    """Result of `scan_package`. `ok` is true exactly when there is no finding."""

    findings: tuple[LeakFinding, ...]
    files_scanned: int
    heldout_hashes: int
    """Number of held-out composite hashes checked (A: 14; B: 3 x 14 x 16 = 672)."""

    @property
    def ok(self) -> bool:
        return not self.findings

    @property
    def heldout_audio(self) -> int:
        """Findings of held-out or unlisted message audio."""
        return sum(f.code in HELDOUT_AUDIO_CODES for f in self.findings)

    @property
    def method_strings(self) -> int:
        """Findings of method labels, designer IDs, method words, `source` keys."""
        return sum(f.code in METHOD_STRING_CODES for f in self.findings)

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(f.code for f in self.findings))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "files_scanned": self.files_scanned,
            "heldout_hashes": self.heldout_hashes,
            "heldout_audio": self.heldout_audio,
            "method_strings": self.method_strings,
            "findings": [f.to_dict() for f in self.findings],
        }


@dataclass(frozen=True, slots=True)
class PackageResult:
    """A built package: its directory, package hash, manifest and leak-scan report."""

    path: Path
    package_sha256: str
    manifest: dict[str, Any]
    leak_report: LeakReport


@dataclass(frozen=True, slots=True)
class LoadedPackage:
    """A package that passed `load_package`. Documents are decoded JSON."""

    path: Path
    study: str
    package_id: str
    demo: bool
    package_sha256: str
    manifest: dict[str, Any]
    answers: dict[str, Any]
    audio: dict[str, Any]
    permutation: dict[str, Any] | None
    allocation: dict[str, Any] | None
    schedules: dict[str, dict[str, Any]]
    """Package path (`schedules/<person_id>/<visit>.json`) -> visit schedule."""
    files: tuple[str, ...]
    """Every file except manifest.json, sorted."""
    combinations_checked: int
    """Message option combinations whose composite hash and length were recomputed
    (A: 32; B: 1,536); 0 when `check_composites=False`."""

    def file_path(self, rel: str) -> Path:
        if rel not in self.manifest["files"]:
            raise KeyError(f"{rel} is not a file of this package")
        return self.path.joinpath(*rel.split("/"))

    def read_file(self, rel: str) -> bytes:
        """The bytes of a listed file, checked again against the manifest."""
        data = self.file_path(rel).read_bytes()
        entry = self.manifest["files"][rel]
        if len(data) != entry["bytes"] or _sha256(data) != entry["sha256"]:
            raise PackageIntegrityError(
                self.path,
                [PackageProblem(E_HASH_MISMATCH, rel, "file changed after the package loaded")],
            )
        return data

    def pcm(self, rel: str) -> bytes:
        """The int16 LE samples of a listed WAV file."""
        return pcm_from_wav(self.read_file(rel))


# ---------------------------------------------------------------------------
# Helpers


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(obj: object) -> bytes:
    """Repository JSON: indent 2, sorted keys, trailing LF, UTF-8."""
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def package_sha256(manifest: Mapping[str, Any]) -> str:
    """SHA-256 of the compact canonical JSON of `manifest` without `package_sha256`.

    Canonical JSON: keys sorted by code point, separators `,` and `:`, ASCII only, no
    whitespace (the store's `canonical_json`).
    """
    return _sha256(canonical_json({k: v for k, v in manifest.items() if k != "package_sha256"}))


@cache
def _validator(definition: str | None) -> Draft202012Validator:
    if definition is None:
        return Draft202012Validator(load_schema(PACKAGE_SCHEMA), registry=_registry())
    schema_id = load_schema(PACKAGE_SCHEMA)["$id"]
    return Draft202012Validator({"$ref": f"{schema_id}#/$defs/{definition}"}, registry=_registry())


def _schema_problems(doc: object, definition: str | None, path: str) -> list[PackageProblem]:
    errors = sorted(_validator(definition).iter_errors(doc), key=lambda e: list(e.absolute_path))
    problems = []
    for error in errors[:3]:
        where = "/".join(str(p) for p in error.absolute_path) or "(root)"
        problems.append(PackageProblem(E_SCHEMA, path, f"at {where}: {error.message[:300]}"))
    return problems


def _decode(data: bytes, path: str) -> tuple[Any, list[PackageProblem]]:
    try:
        return strict_loads(data), []
    except StrictJsonError as err:
        return None, [PackageProblem(E_JSON, path, str(err))]


def _input_bytes(value: bytes | bytearray | memoryview | str | os.PathLike[str]) -> bytes:
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value)
    if isinstance(value, str | os.PathLike):
        return Path(value).read_bytes()
    raise TypeError(f"expected bytes or a file path, got {type(value).__name__}")


def _walk(root: Path) -> tuple[list[str], list[str]]:
    """Regular files under `root` (relative POSIX paths, sorted) and other entries."""
    files: list[str] = []
    odd: list[str] = []
    for current, dirs, names in os.walk(root, followlinks=False):
        here = Path(current)
        for name in sorted(dirs):
            if (here / name).is_symlink():
                odd.append((here / name).relative_to(root).as_posix())
        for name in sorted(names):
            path = here / name
            rel = path.relative_to(root).as_posix()
            (files if path.is_file() and not path.is_symlink() else odd).append(rel)
    return sorted(files), sorted(odd)


def _repo_root() -> Path | None:
    try:
        return data_root().parent.resolve()
    except FileNotFoundError:  # pragma: no cover - only outside the source tree
        return None


def _check_target(out_dir: str | os.PathLike[str], *, demo: bool) -> Path:
    target = Path(out_dir)
    repo = _repo_root()
    if not demo and repo is not None:
        resolved = target.resolve()
        if resolved == repo or repo in resolved.parents:
            raise PackageError(
                E_POLICY,
                "study packages live in restricted storage outside the repository "
                "(docs/interfaces/package-format.md)",
            )
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise PackageError(E_INPUT, f"{target} must not exist or must be an empty directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


@contextmanager
def _staging(target: Path) -> Iterator[Path]:
    """A fresh directory next to `target`; renamed to `target` on success, else removed."""
    stage = Path(tempfile.mkdtemp(prefix=f".{target.name}.", suffix=".partial", dir=target.parent))
    try:
        yield stage
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    if target.exists():
        target.rmdir()
    os.replace(stage, target)


class _Writer:
    """Writes package files and records the manifest entry of each."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.files: dict[str, dict[str, Any]] = {}

    def path(self, rel: str) -> Path:
        if rel in self.files:
            raise PackageError(E_INPUT, f"{rel} written twice")
        path = self.root.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def record(self, rel: str, sha256: str, size: int) -> str:
        self.files[rel] = {"sha256": sha256, "bytes": size}
        return sha256

    def write(self, rel: str, data: bytes) -> str:
        with open(self.path(rel), "wb") as f:
            f.write(data)
        return self.record(rel, _sha256(data), len(data))


def _labels_problems(labels: Mapping[str, Any], path: str) -> list[PackageProblem]:
    """Each family and role binds its 4 ontology labels to indices 1..4 exactly once."""
    if set(labels) != set(ATOM_IDS):
        return [PackageProblem(E_CONTENT, path, "labels must cover the 16 atoms exactly")]
    problems = []
    for (family, role), allowed in SEMANTIC_LABELS.items():
        bound = [labels[atom_id(family, role, i)] for i in INDICES]
        if sorted(map(str, bound)) != sorted(allowed):
            problems.append(
                PackageProblem(
                    E_CONTENT,
                    path,
                    f"{family} {role} labels {bound} are not a permutation of {list(allowed)}",
                )
            )
    return problems


def _answers_doc(
    study: str, package_id: str, demo: bool, labels: Mapping[str, str]
) -> dict[str, Any]:
    atoms = []
    for atom in ATOM_IDS:
        ref = parse_atom_id(atom)
        atoms.append(
            {
                "atom_id": atom,
                "family": ref.family,
                "role": ref.role,
                "index": ref.index,
                "semantic_label": labels[atom],
                "wave": ATOM_WAVES[atom],
            }
        )
    messages = []
    for m in MESSAGES:
        messages.append(
            {
                "message_id": m.message_id,
                "family": m.family,
                "action_atom": m.action.atom_id,
                "referent_atom": m.referent.atom_id,
                "action_index": m.action_index,
                "referent_index": m.referent_index,
                "semantic_action": labels[m.action.atom_id],
                "semantic_referent": labels[m.referent.atom_id],
                "status": "heldout" if m.is_heldout else "trained",
                "training_wave": m.training_wave,
                "heldout_set": m.heldout_set,
            }
        )
    return {
        "format": ANSWERS_FORMAT,
        "format_version": DOCUMENT_FORMAT_VERSION,
        "study": study,
        "package_id": package_id,
        "demo": demo,
        "hidden_answer": True,
        "atoms": atoms,
        "messages": messages,
        "trained_message_ids": list(TRAINED_MESSAGE_IDS),
        "heldout_message_ids": list(HELDOUT_MESSAGE_IDS),
        "heldout_sets": {
            s: [m.message_id for m in MESSAGES if m.heldout_set == s] for s in HELDOUT_SETS
        },
    }


def _waves() -> list[dict[str, Any]]:
    return [
        {"wave": w, "visit": f"V{w}", "atoms": [a for a in ATOM_IDS if ATOM_WAVES[a] == w]}
        for w in (1, 2, 3)
    ]


def _option_path(profile: str, atom: str, rank: int) -> str:
    return f"options/{profile}/{atom}-{rank}.wav"


def _manifest_doc(
    study: str,
    package_id: str,
    demo: bool,
    files: Mapping[str, Mapping[str, Any]],
    extra: Mapping[str, Any],
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "format": PACKAGE_FORMAT,
        "format_version": PACKAGE_FORMAT_VERSION,
        "study": study,
        "package_id": package_id,
        "demo": demo,
        "builder": {"name": "av-sound", "version": PACKAGE_BUILDER_VERSION},
        "renderer_version": RENDERER_VERSION,
        "composition_contract": COMPOSITION_CONTRACT,
        "files": {rel: dict(files[rel]) for rel in sorted(files)},
        **extra,
    }
    manifest["package_sha256"] = package_sha256(manifest)
    return manifest


def _finish(stage: Path, manifest: dict[str, Any], forbidden: Iterable[str]) -> LeakReport:
    (stage / MANIFEST).write_bytes(_json_bytes(manifest))
    load_package(stage)
    report = scan_package(stage, forbidden_strings=forbidden)
    if not report.ok:
        problems = [PackageProblem(f.code, f.path, f.message) for f in report.findings]
        raise PackageError(E_LEAK, f"leak scan failed: {problems[0]}", problems)
    return report


# ---------------------------------------------------------------------------
# Builders


def _frozen_book(
    store: VocabularyStore, book_id: str, expected_head: str | None
) -> tuple[BookInfo, str]:
    """Book facts and the frozen head (chain head of the `freeze` record) of a packageable
    book; raises `PackageError(E_BOOK)` otherwise."""
    info = store.book(book_id, expected_head=expected_head)
    if info.void:
        raise PackageError(E_BOOK, f"book {book_id} is void (superseded); never package it")
    if info.kind not in ("study", "synthetic"):
        raise PackageError(E_BOOK, f"book {book_id} is a {info.kind} book, not a learner book")
    if not info.frozen:
        raise PackageError(E_BOOK, f"book {book_id} is not frozen; freeze it before packaging")
    if info.n_entries != len(ATOM_IDS):
        raise PackageError(E_BOOK, f"book {book_id} has {info.n_entries} atoms, not 16")
    if not (_SHA256.fullmatch(info.renderer_hash) and _SHA256.fullmatch(info.validator_hash)):
        raise PackageError(E_BOOK, f"book {book_id} records no renderer or validator hash")
    records = store.records(book_id, expected_head=expected_head)
    freeze = next((r for r in records if r["event"] == "freeze"), None)
    if freeze is None:  # pragma: no cover - a closed book that is not void has a freeze
        raise PackageError(E_BOOK, f"book {book_id} has no freeze record")
    frozen_head = _sha256(canonical_json(freeze))
    if expected_head is not None and expected_head != frozen_head:
        raise PackageError(
            E_BOOK, f"book {book_id}: expected_head is not the chain head of its freeze record"
        )
    return info, frozen_head


def build_package(
    store: VocabularyStore,
    book_id: str,
    out_dir: str | os.PathLike[str],
    *,
    expected_head: str | None = None,
    rerender: bool = True,
) -> PackageResult:
    """Build the Study A package of one frozen store book into `out_dir`.

    The book must be frozen (a `freeze` record), not void, have all 16 atoms with
    meanings and pass `store.verify(book_id, rerender=rerender)`. Every store read is
    anchored on the frozen head: the chain head of the `freeze` record, which the
    manifest records (`book.frozen_head`, with the book's `renderer_hash` and
    `validator_hash`) and which `expected_head`, when given (the head `freeze()`
    returned), must equal. Later log lines (rejected commits to the frozen book) do not
    change the package; a later `void` does, and is refused. `kind` `study` gives a
    participant package, `synthetic` a DEMO package, and `fallback` is refused.

    `out_dir` must not exist or be empty; a study package cannot be written inside this
    repository (`E_POLICY`). Writes 16 atom WAVs, the 18 trained-message
    WAVs (`compose_message`), `answers.json`, `audio.json` (composite hash and length of
    all 32 messages; held-out ones from `composite_hash` only) and `manifest.json`, then
    runs `load_package` and `scan_package` (with the book's `source` values as forbidden
    strings) and returns the package hash. The same book gives the same bytes.
    """
    try:
        info, frozen_head = _frozen_book(store, book_id, expected_head)
        report = store.verify(book_id, rerender=rerender, expected_head=frozen_head)
        if not report.ok:
            raise PackageError(
                E_BOOK, f"book {book_id} failed store verification: {', '.join(report.codes)}"
            )
        entries = {e.atom_id: e for e in store.list(book_id, expected_head=frozen_head)}
        snapshot = store.snapshot_hashes(book_id, expected_head=frozen_head)
    except StoreError as err:
        raise PackageError(E_BOOK, f"book {book_id}: {err}") from err
    labels: dict[str, str] = {}
    for atom in ATOM_IDS:
        label = entries[atom].semantic_label
        if label is None:
            raise PackageError(E_BOOK, f"book {book_id}: {atom} has no semantic label")
        labels[atom] = label
    problems = _labels_problems(labels, f"book {book_id}")
    if problems:
        raise PackageError(E_BOOK, str(problems[0]), problems)
    demo = info.kind == "synthetic"
    target = _check_target(out_dir, demo=demo)
    profile = info.profile.value
    with _staging(target) as stage:
        writer = _Writer(stage)
        atom_rows = []
        for atom in ATOM_IDS:
            entry = entries[atom]
            rel = f"atoms/{atom}.wav"
            file_hash = write_wav(entry.pcm, writer.path(rel))
            if file_hash != entry.file_sha256:  # pragma: no cover - the store verified it
                raise PackageError(E_BOOK, f"{atom}: WAV differs from the store record")
            writer.record(rel, file_hash, len(wav_bytes(entry.pcm)))
            atom_rows.append(
                {
                    "atom_id": atom,
                    "path": rel,
                    "n_samples": entry.n_samples,
                    "pcm_sha256": entry.pcm_sha256,
                    "file_sha256": entry.file_sha256,
                }
            )
        message_rows = []
        for m in MESSAGES:
            action, referent = entries[m.action.atom_id], entries[m.referent.atom_id]
            expected = composite_hash(action, referent)
            n = message_length(action, referent)
            row: dict[str, Any] = {
                "message_id": m.message_id,
                "action_atom": m.action.atom_id,
                "referent_atom": m.referent.atom_id,
                "status": "heldout" if m.is_heldout else "trained",
                "n_samples": n,
                "duration_ms": n // SAMPLES_PER_MS,
                "composite_sha256": expected,
                "path": None,
                "file_sha256": None,
            }
            if not m.is_heldout:
                message = compose_message(action, referent)
                if message.pcm_sha256 != expected or message.n_samples != n:  # pragma: no cover
                    raise PackageError(E_COMPOSITE, f"{m.message_id}: composer disagrees")
                rel = f"messages/{m.message_id}.wav"
                file_hash = write_message_wav(message, writer.path(rel))
                writer.record(rel, file_hash, len(wav_bytes(message.pcm)))
                row["path"], row["file_sha256"] = rel, file_hash
            message_rows.append(row)
        audio = {
            "format": AUDIO_FORMAT,
            "format_version": DOCUMENT_FORMAT_VERSION,
            "study": "A",
            "package_id": book_id,
            "profile": profile,
            "atoms": atom_rows,
            "messages": message_rows,
        }
        writer.write(ANSWERS, _json_bytes(_answers_doc("A", book_id, demo, labels)))
        writer.write(AUDIO, _json_bytes(audio))
        extra = {
            "profile": profile,
            "book": {
                "frozen_head": frozen_head,
                "snapshot_sha256": snapshot_digest(snapshot),
                "renderer_hash": info.renderer_hash,
                "validator_hash": info.validator_hash,
            },
        }
        manifest = _manifest_doc("A", book_id, demo, writer.files, extra)
        sources = sorted({e.source for e in entries.values()})
        leak = _finish(stage, manifest, sources)
        try:  # the book must still be the same, unvoided book after the build
            _frozen_book(store, book_id, frozen_head)
        except StoreError as err:  # pragma: no cover - concurrent damage to the store
            raise PackageError(E_BOOK, f"book {book_id}: {err}") from err
    return PackageResult(target, manifest["package_sha256"], manifest, leak)


def build_dyad_package(bank: DyadBank, out_dir: str | os.PathLike[str]) -> PackageResult:
    """Build the Study B package of one frozen dyad bank (see `av_sound.dyad_bank`) into
    `out_dir`. A provisional bank is recorded as `av-sound/provisional-bank`; a bank with
    a verified #26 `handoff` is recorded as `av-banks/bank-manifest` with the #26 bank hash,
    and every written WAV must equal the bank's `file_sha256`.

    Re-renders every option and checks it against the bank's hash (no overflow, no short
    event, distinct waveforms within a profile), writes the 192 option WAVs, `answers.json`
    (meanings from the bank's labels), `audio.json` (wave manifest; composite hash and
    length of all 3 x 32 x 16 option combinations, held-out ones included, from
    `composite_hash` only) and `manifest.json`. No message WAV is written: the app
    composes trained messages at run time. Then runs `load_package` and `scan_package`.
    """
    target = _check_target(out_dir, demo=bank.demo)
    atoms: dict[tuple[str, str, int], AtomAudio] = {}
    with _staging(target) as stage:
        writer = _Writer(stage)
        option_rows = []
        for profile in PROFILES:
            seen: dict[str, str] = {}
            for atom in ATOM_IDS:
                for option in bank.cells[(profile.value, atom)]:
                    where = f"{profile.value} {atom} rank {option.rank}"
                    rendered = render(option.recipe, profile)
                    if rendered.overflow or rendered.short_event:
                        raise PackageError(E_BANK, f"{where}: overflow or short event")
                    if rendered.pcm_sha256 != option.pcm_sha256:
                        raise PackageError(E_BANK, f"{where}: waveform differs from the bank hash")
                    if rendered.pcm_sha256 in seen:
                        raise PackageError(
                            E_BANK, f"{where}: same waveform as {seen[rendered.pcm_sha256]}"
                        )
                    seen[rendered.pcm_sha256] = where
                    rel = _option_path(profile.value, atom, option.rank)
                    pcm = rendered.pcm
                    file_hash = writer.write(rel, wav_bytes(pcm))
                    if option.file_sha256 is not None and file_hash != option.file_sha256:
                        raise PackageError(
                            E_BANK, f"{where}: WAV file differs from the bank's file_sha256"
                        )
                    atoms[(profile.value, atom, option.rank)] = AtomAudio(atom, profile, pcm)
                    option_rows.append(
                        {
                            "profile": profile.value,
                            "atom_id": atom,
                            "rank": option.rank,
                            "menu": option.menu,
                            "path": rel,
                            "n_samples": rendered.n_samples,
                            "pcm_sha256": rendered.pcm_sha256,
                            "file_sha256": file_hash,
                        }
                    )
        message_rows = []
        for m in MESSAGES:
            combinations = []
            for profile in PROFILES:
                for action_rank in range(1, 5):
                    for referent_rank in range(1, 5):
                        action = atoms[(profile.value, m.action.atom_id, action_rank)]
                        referent = atoms[(profile.value, m.referent.atom_id, referent_rank)]
                        n = message_length(action, referent)
                        combinations.append(
                            {
                                "profile": profile.value,
                                "action_rank": action_rank,
                                "referent_rank": referent_rank,
                                "n_samples": n,
                                "duration_ms": n // SAMPLES_PER_MS,
                                "composite_sha256": composite_hash(action, referent),
                            }
                        )
            message_rows.append(
                {
                    "message_id": m.message_id,
                    "action_atom": m.action.atom_id,
                    "referent_atom": m.referent.atom_id,
                    "status": "heldout" if m.is_heldout else "trained",
                    "combinations": combinations,
                }
            )
        audio = {
            "format": AUDIO_FORMAT,
            "format_version": DOCUMENT_FORMAT_VERSION,
            "study": "B",
            "package_id": bank.bank_id,
            "profiles": [p.value for p in PROFILES],
            "waves": _waves(),
            "options": option_rows,
            "messages": message_rows,
        }
        answers = _answers_doc("B", bank.bank_id, bank.demo, bank.labels)
        writer.write(ANSWERS, _json_bytes(answers))
        writer.write(AUDIO, _json_bytes(audio))
        extra = {"bank": bank.package_bank()}
        manifest = _manifest_doc("B", bank.bank_id, bank.demo, writer.files, extra)
        sources = sorted({o.source for opts in bank.cells.values() for o in opts if o.source})
        leak = _finish(stage, manifest, sources)
    return PackageResult(target, manifest["package_sha256"], manifest, leak)


# ---------------------------------------------------------------------------
# Reserved slots: permutation (#29), allocation, schedules (#30)


def novel_by_visit(study: str, swap_w1_w4: bool) -> dict[str, list[str]]:
    """Visit -> held-out message IDs tested there (novel block), after the swap.

    Study A: D0 tests H-W1 and D7 tests H-W4, swapped when `swap_w1_w4`; H-V1..H-V3 are
    unused. Study B: V1, V2, V3 test H-V1, H-V2, H-V3; W1 and W4 test H-W1 and H-W4,
    swapped when `swap_w1_w4`.
    """
    if study not in NOVEL_SET_BY_VISIT:
        raise ValueError(f"study must be 'A' or 'B', got {study!r}")
    result = {}
    for visit, base in NOVEL_SET_BY_VISIT[study].items():
        held_set = _SWAP.get(base, base) if swap_w1_w4 else base
        result[visit] = [m.message_id for m in MESSAGES if m.heldout_set == held_set]
    return result


def permutation_matrix(doc: Mapping[str, Any]) -> dict[str, tuple[tuple[str, ...], ...]]:
    """Family -> 4 x 4 status matrix implied by a permutation document's messages.

    Cells read `Train V<wave>` or the held-out set, as in `av_sound.grammar.MATRIX`.
    Raises `PackageError(E_PERMUTATION)` if the messages are not the 32 legal messages
    with consistent fields.
    """
    cells: dict[tuple[str, int, int], str] = {}
    try:
        for msg in doc["messages"]:
            ref = parse_message_id(msg["message_id"])
            if (msg["family"], msg["action_atom"], msg["referent_atom"]) != (
                ref.family,
                ref.action.atom_id,
                ref.referent.atom_id,
            ):
                raise ValueError(f"{ref.message_id}: family or atoms do not match the ID")
            status, wave, held = msg["status"], msg["training_wave"], msg["heldout_set"]
            if status == "trained" and type(wave) is int and wave in (1, 2, 3) and held is None:
                cell = f"Train V{wave}"
            elif status == "heldout" and wave is None and held in HELDOUT_SETS:
                cell = str(held)
            else:
                raise ValueError(f"{ref.message_id}: inconsistent status fields")
            key = (ref.family, ref.action_index, ref.referent_index)
            if key in cells:
                raise ValueError(f"{ref.message_id} listed twice")
            cells[key] = cell
    except (KeyError, TypeError, ValueError, GrammarError) as err:
        raise PackageError(E_PERMUTATION, f"permutation messages are malformed: {err}") from err
    if len(cells) != len(MESSAGES):
        raise PackageError(E_PERMUTATION, f"permutation lists {len(cells)} messages, not 32")
    return {f: tuple(tuple(cells[(f, a, r)] for r in INDICES) for a in INDICES) for f in FAMILIES}


def _permutation_problems(
    doc: Any,  # noqa: ANN401 - decoded JSON
    study: str,
    demo: bool,
    labels: Mapping[str, str],
) -> list[PackageProblem]:
    def problem(code: str, message: str) -> PackageProblem:
        return PackageProblem(code, PERMUTATION, message)

    if not isinstance(doc, dict):
        return [problem(E_PERMUTATION, "not a JSON object")]
    if doc.get("format") != PERMUTATION_FORMAT or doc.get("format_version") != (
        PERMUTATION_FORMAT_VERSION
    ):
        return [
            problem(
                E_PERMUTATION,
                f"expected format {PERMUTATION_FORMAT} version {PERMUTATION_FORMAT_VERSION}",
            )
        ]
    out: list[PackageProblem] = []
    if doc.get("demo") is True and not demo:
        out.append(problem(E_DEMO, "a DEMO permutation cannot go into a participant package"))
    elif not isinstance(doc.get("demo"), bool):
        out.append(problem(E_PERMUTATION, "demo must be true or false"))
    if doc.get("study") != study:
        out.append(problem(E_PERMUTATION, f"study {doc.get('study')!r}, package is {study}"))
    kinds = ("batch",) if study == "A" else ("dyad", "spare")
    if doc.get("unit_kind") not in kinds:
        out.append(problem(E_PERMUTATION, f"unit_kind must be one of {kinds}"))
    try:
        matrices = permutation_matrix(doc)
    except PackageError as err:
        return [*out, problem(err.code, str(err))]
    for family, matrix in matrices.items():
        if matrix != MATRIX:
            out.append(
                problem(
                    E_MATRIX,
                    f"family {family}: trained/held-out cells differ from the "
                    "fixed matrix (av_sound.grammar.MATRIX)",
                )
            )
    try:
        for family in FAMILIES:
            for role in ROLES:
                bound = doc["labels"][family][role]
                if [labels[atom_id(family, role, i)] for i in INDICES] != bound:
                    out.append(
                        problem(
                            E_PERMUTATION,
                            f"labels {family} {role} {bound} differ from answers.json",
                        )
                    )
        atoms = {a["atom_id"]: a for a in doc["atoms"]}
        if sorted(atoms) != sorted(ATOM_IDS) or len(doc["atoms"]) != len(ATOM_IDS):
            out.append(problem(E_PERMUTATION, "atoms must list the 16 atoms once each"))
        for atom, entry in sorted(atoms.items()):
            ref = parse_atom_id(atom)
            actual = (entry["family"], entry["role"], entry["index"], entry["semantic_label"])
            if actual != (ref.family, ref.role, ref.index, labels[atom]):
                out.append(problem(E_PERMUTATION, f"atom {atom} differs from answers.json"))
            if entry["matrix_wave"] != ATOM_WAVES[atom]:
                out.append(problem(E_MATRIX, f"atom {atom}: matrix_wave {entry['matrix_wave']}"))
        for msg in doc["messages"]:
            if (msg["semantic_action"], msg["semantic_referent"]) != (
                labels[msg["action_atom"]],
                labels[msg["referent_atom"]],
            ):
                out.append(
                    problem(E_PERMUTATION, f"{msg['message_id']}: labels differ from answers.json")
                )
        order = doc["atom_order"]
        if sorted(order) != sorted(ATOM_IDS) or len(order) != len(ATOM_IDS):
            out.append(problem(E_PERMUTATION, "atom_order must list the 16 atoms once each"))
        if study == "B":
            waves = doc["wave_atom_order"]
            for wave in (1, 2, 3):
                expected = sorted(a for a in ATOM_IDS if ATOM_WAVES[a] == wave)
                if sorted(waves[str(wave)]) != expected:
                    out.append(problem(E_PERMUTATION, f"wave_atom_order {wave} is not {expected}"))
            if order != [a for w in ("1", "2", "3") for a in waves[w]]:
                out.append(problem(E_PERMUTATION, "atom_order is not the wave orders in turn"))
        elif "wave_atom_order" in doc:
            out.append(problem(E_PERMUTATION, "a Study A permutation has no wave_atom_order"))
    except (KeyError, TypeError, ValueError, GrammarError) as err:
        out.append(problem(E_PERMUTATION, f"malformed permutation: {err!r}"))
    return out


def _allocation_doc(study: str, package_id: str, extras: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {"swap_w1_w4"} | ({"structured_family"} if study == "B" else set())
    unknown = sorted(set(extras) - allowed)
    if unknown:
        raise PackageError(
            E_ALLOCATION,
            f"allocation_extras for study {study} accept only {sorted(allowed)}; got {unknown}",
        )
    swap = extras.get("swap_w1_w4")
    if not isinstance(swap, bool):
        raise PackageError(E_ALLOCATION, "allocation_extras needs swap_w1_w4 (true or false)")
    doc: dict[str, Any] = {
        "format": ALLOCATION_FORMAT,
        "format_version": DOCUMENT_FORMAT_VERSION,
        "study": study,
        "package_id": package_id,
        "swap_w1_w4": swap,
        "novel_by_visit": novel_by_visit(study, swap),
    }
    if "structured_family" in extras:
        if extras["structured_family"] not in FAMILIES:
            raise PackageError(E_ALLOCATION, "structured_family must be 'K' or 'Q'")
        doc["structured_family"] = extras["structured_family"]
    return doc


def _allocation_problems(doc: Any, study: str, package_id: str) -> list[PackageProblem]:  # noqa: ANN401
    problems = _schema_problems(doc, "allocation", ALLOCATION)
    if problems:
        return problems
    out = []
    if (doc["study"], doc["package_id"]) != (study, package_id):
        out.append(PackageProblem(E_ALLOCATION, ALLOCATION, "study or package_id differs"))
    if doc["novel_by_visit"] != novel_by_visit(study, doc["swap_w1_w4"]):
        out.append(
            PackageProblem(E_ALLOCATION, ALLOCATION, "novel_by_visit does not follow swap_w1_w4")
        )
    return out


def _schedule_problems(
    rel: str,
    doc: Any,  # noqa: ANN401 - decoded JSON
    *,
    study: str,
    demo: bool,
    answers: Mapping[str, Any],
    permutation_sha256: str | None,
    permutation: Mapping[str, Any] | None,
    allocation: Mapping[str, Any] | None,
) -> list[PackageProblem]:
    def problem(code: str, message: str) -> PackageProblem:
        return PackageProblem(code, rel, message)

    if not isinstance(doc, dict):
        return [problem(E_SCHEDULE, "not a JSON object")]
    if (
        doc.get("format") != VISIT_SCHEDULE_FORMAT
        or doc.get("format_version") != VISIT_SCHEDULE_FORMAT_VERSION
        or doc.get("hidden_answer") is not True
    ):
        return [
            problem(
                E_SCHEDULE,
                f"expected format {VISIT_SCHEDULE_FORMAT} version {VISIT_SCHEDULE_FORMAT_VERSION} "
                "with hidden_answer true",
            )
        ]
    out: list[PackageProblem] = []
    try:
        if doc["demo"] is True and not demo:
            out.append(problem(E_DEMO, "a DEMO schedule cannot go into a participant package"))
        if doc["study"] != study:
            out.append(problem(E_SCHEDULE, f"study {doc['study']!r}, package is {study}"))
        visit, person = doc["visit"], doc["person_id"]
        if rel != f"{SCHEDULES_DIR}/{person}/{visit}.json":
            out.append(problem(E_SCHEDULE, f"path does not match person {person} visit {visit}"))
        if visit not in STUDY_VISITS[study]:
            out.append(problem(E_SCHEDULE, f"visit {visit} is not a Study {study} visit"))
            return out
        if permutation_sha256 is None or permutation is None:
            out.append(problem(E_SCHEDULE, "schedules need the package's permutation.json"))
        else:
            if doc["permutation_json_sha256"] != permutation_sha256:
                out.append(problem(E_SCHEDULE, "permutation_json_sha256 is not this package's"))
            unit = permutation.get("unit_id")
            if doc["unit_id"] != unit or not str(person).startswith(f"{unit}-"):
                out.append(problem(E_SCHEDULE, f"person {person} is not in unit {unit}"))
        swap = None if allocation is None else allocation["swap_w1_w4"]
        if swap is not None and doc["swap_w1_w4"] != swap:
            out.append(problem(E_SCHEDULE, "swap_w1_w4 differs from allocation.json"))
        base = NOVEL_SET_BY_VISIT[study][visit]
        if swap is None:
            allowed = {base, _SWAP.get(base, base)}
        else:
            allowed = {_SWAP.get(base, base) if swap else base}
        heldout = set(HELDOUT_MESSAGE_IDS)
        leaked = sorted(heldout & set(doc["dictionary_messages"]))
        if leaked:
            out.append(problem(E_SCHEDULE, f"held-out messages in dictionary_messages: {leaked}"))
        messages = {m["message_id"]: m for m in answers["messages"]}
        atoms = {a["atom_id"]: a for a in answers["atoms"]}
        structured = None if allocation is None else allocation.get("structured_family")
        for block in doc["blocks"]:
            for item in block["items"]:
                out.extend(
                    problem(E_SCHEDULE, f"{item.get('trial_id')}: {text}")
                    for text in _item_problems(item, study, allowed, messages, atoms, structured)
                )
    except (KeyError, TypeError, ValueError, GrammarError) as err:
        out.append(problem(E_SCHEDULE, f"malformed visit schedule: {err!r}"))
    return out


_INTENDED_MESSAGE_FIELDS: Final = (
    "family",
    "action_index",
    "referent_index",
    "semantic_action",
    "semantic_referent",
)
_INTENDED_ATOM_FIELDS: Final = ("family", "role", "index", "semantic_label")


def _item_problems(
    item: Mapping[str, Any],
    study: str,
    allowed_sets: set[str],
    messages: Mapping[str, Mapping[str, Any]],
    atoms: Mapping[str, Mapping[str, Any]],
    structured: str | None,
) -> list[str]:
    out = []
    kind = item["trial_type"]
    message = item["message_id"]
    if message is not None:
        ref = parse_message_id(message)
        if ref.is_heldout and kind != "novel":
            out.append(f"held-out message {message} in a {kind} item")
        elif ref.is_heldout and ref.heldout_set not in allowed_sets:
            out.append(f"novel message {message} ({ref.heldout_set}) is not due at this visit")
        elif not ref.is_heldout and kind == "novel":
            out.append(f"novel item plays trained message {message}")
        if kind == "message_lesson":
            expected = "structured"
            if study == "B":
                expected = (
                    ""
                    if structured is None
                    else ("structured" if ref.family == structured else "dictionary")
                )
            if expected and item["presentation"] != expected:
                out.append(f"presentation {item['presentation']!r}, expected {expected!r}")
    intended = item["intended"]
    if isinstance(intended, Mapping):
        if intended["kind"] == "message":
            answer = messages[intended["message_id"]]
            if any(intended[k] != answer[k] for k in _INTENDED_MESSAGE_FIELDS):
                out.append(f"intended {intended['message_id']} differs from answers.json")
        else:
            answer = atoms[intended["atom_id"]]
            if any(intended[k] != answer[k] for k in _INTENDED_ATOM_FIELDS):
                out.append(f"intended {intended['atom_id']} differs from answers.json")
    return out


def _slot_problems(
    *,
    study: str,
    demo: bool,
    package_id: str,
    answers: Mapping[str, Any],
    permutation_bytes: bytes | None,
    allocation: Any,  # noqa: ANN401 - decoded JSON
    schedules: Mapping[str, Any],
) -> tuple[list[PackageProblem], dict[str, Any] | None]:
    problems: list[PackageProblem] = []
    permutation: dict[str, Any] | None = None
    labels = {a["atom_id"]: a["semantic_label"] for a in answers["atoms"]}
    if permutation_bytes is not None:
        doc, decode_problems = _decode(permutation_bytes, PERMUTATION)
        problems += decode_problems
        if not decode_problems:
            found = _permutation_problems(doc, study, demo, labels)
            problems += found
            if not found:
                permutation = doc
    allocation_ok = None
    if allocation is not None:
        found = _allocation_problems(allocation, study, package_id)
        problems += found
        allocation_ok = None if found else allocation
    perm_sha = None if permutation_bytes is None else _sha256(permutation_bytes)
    for rel in sorted(schedules):
        problems += _schedule_problems(
            rel,
            schedules[rel],
            study=study,
            demo=demo,
            answers=answers,
            permutation_sha256=perm_sha if permutation is not None else None,
            permutation=permutation,
            allocation=allocation_ok,
        )
    return problems, permutation


def _schedule_inputs(
    schedules: Mapping[str, bytes | str | os.PathLike[str]] | str | os.PathLike[str],
) -> dict[str, bytes]:
    items: dict[str, bytes] = {}
    if isinstance(schedules, Mapping):
        for rel, value in schedules.items():
            items[str(rel)] = _input_bytes(value)
    else:
        root = Path(schedules)
        if not root.is_dir():
            raise PackageError(E_SCHEDULE, f"{root} is not a directory of visit schedules")
        files, odd = _walk(root)
        if odd:
            raise PackageError(E_SCHEDULE, f"{root}: not regular files: {odd}")
        for rel in files:
            items[rel] = root.joinpath(*rel.split("/")).read_bytes()
    for rel in sorted(items):
        if not _SCHEDULE_REL.fullmatch(rel):
            raise PackageError(
                E_SCHEDULE, f"{rel!r} is not <person_id>/<visit>.json (e.g. A-C01-L01/D0.json)"
            )
    return {f"{SCHEDULES_DIR}/{rel}": items[rel] for rel in sorted(items)}


def seal(
    package_dir: str | os.PathLike[str],
    *,
    permutation: bytes | str | os.PathLike[str] | None = None,
    schedules: Mapping[str, bytes | str | os.PathLike[str]] | str | os.PathLike[str] | None = None,
    allocation_extras: Mapping[str, Any] | None = None,
) -> str:
    """Fill the reserved slots of a built package and return the new package hash.

    - `permutation`: the unit's `permutation.json` (#29, format 2) as bytes or a path;
      copied byte for byte. Its labels must equal `answers.json`, its trained/held-out
      cells must equal the fixed matrix (`E_MATRIX`), and `demo: true` is refused unless
      the package is a DEMO package (`E_DEMO`).
    - `schedules`: visit schedules (#30) as `{"<person_id>/<visit>.json": bytes or path}`
      or the unit's `schedules/` directory. They need the permutation (their
      `permutation_json_sha256`), may play a held-out message only in a novel item due at
      that visit, and their intended tuples must equal `answers.json`.
    - `allocation_extras`: restricted allocation facts, `{"swap_w1_w4": bool}` plus, for
      Study B, optionally `"structured_family": "K" | "Q"`; written as `allocation.json`
      with the novel messages of each visit (`novel_by_visit`).

    Each slot is filled once: adding identical bytes again changes nothing, different
    bytes raise `E_SLOT`. All inputs are checked before anything is written; the package
    is then verified (`load_package`, `scan_package`). With nothing to add, the current
    hash is returned.
    """
    pkg = load_package(package_dir)
    files = pkg.manifest["files"]
    new: dict[str, bytes] = {}

    def stage(rel: str, data: bytes) -> None:
        if rel in files:
            if _sha256(data) != files[rel]["sha256"]:
                raise PackageError(E_SLOT, f"{rel} is already sealed with different content")
            return
        new[rel] = data

    permutation_bytes = pkg.read_file(PERMUTATION) if PERMUTATION in files else None
    if permutation is not None:
        data = _input_bytes(permutation)
        stage(PERMUTATION, data)
        permutation_bytes = data
    allocation = pkg.allocation
    if allocation_extras is not None:
        allocation = _allocation_doc(pkg.study, pkg.package_id, allocation_extras)
        stage(ALLOCATION, _json_bytes(allocation))
    schedule_docs: dict[str, Any] = dict(pkg.schedules)
    if schedules is not None:
        for rel, data in _schedule_inputs(schedules).items():
            doc, decode_problems = _decode(data, rel)
            if decode_problems:
                raise PackageError(E_SCHEDULE, str(decode_problems[0]), decode_problems)
            stage(rel, data)
            schedule_docs[rel] = doc
    if not new:
        return pkg.package_sha256
    problems, _ = _slot_problems(
        study=pkg.study,
        demo=pkg.demo,
        package_id=pkg.package_id,
        answers=pkg.answers,
        permutation_bytes=permutation_bytes,
        allocation=allocation,
        schedules=schedule_docs,
    )
    if problems:
        shown = "; ".join(str(p) for p in problems[:5])
        raise PackageError(problems[0].code, f"seal refused: {shown}", problems)
    writer = _Writer(pkg.path)
    for rel in sorted(new):
        writer.write(rel, new[rel])
    manifest = {k: v for k, v in pkg.manifest.items() if k != "package_sha256"}
    manifest["files"] = {**files, **writer.files}
    manifest["files"] = {rel: manifest["files"][rel] for rel in sorted(manifest["files"])}
    manifest["package_sha256"] = package_sha256(manifest)
    tmp = pkg.path / f".{MANIFEST}.tmp"
    tmp.write_bytes(_json_bytes(manifest))
    os.replace(tmp, pkg.path / MANIFEST)
    load_package(pkg.path)
    report = scan_package(pkg.path)
    if not report.ok:  # pragma: no cover - inputs were checked above
        raise PackageError(E_LEAK, f"leak scan failed after sealing: {report.findings[0]}")
    return str(manifest["package_sha256"])


# ---------------------------------------------------------------------------
# Package-hash mapping for the run sheets (#32)


def package_hashes(
    packages: Iterable[str | os.PathLike[str] | LoadedPackage],
    *,
    set_name: str,
    keys: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """The package-hash mapping the run-sheet generator (#32) reads to fill `hash_check`.

    Matches `schedules/schema/package-hashes.schema.json` (format
    `av-schedules/package-hashes` version 1): `{format, format_version, study, set, demo,
    placeholder: false, packages: {key: package_sha256}}`. Every package is verified with
    `load_package` first; all must be of one study and one DEMO status. Keys:

    - Study A: the book ID of the learner-facing slot list (`BK-C-7QX4MN`). By default the
      package ID; `keys` maps a package ID to its slot-list book ID (needed for DEMO
      books, whose IDs start with `DEMO-`).
    - Study B: the dyad slot ID (`B-C01`, spare `B-S01`): by default the `unit_id` of the
      sealed `permutation.json`, else the package ID; `keys` may name it.

    A key must fit its study's pattern and its P/C/S prefix the set; a sealed
    permutation's `set` must equal `set_name`; keys must be unique. Raises
    `PackageError(E_INPUT)`.
    """
    if set_name not in SETS:
        raise PackageError(E_INPUT, f"set must be one of {SETS}, got {set_name!r}")
    loaded = [p if isinstance(p, LoadedPackage) else load_package(p) for p in packages]
    if not loaded:
        raise PackageError(E_INPUT, "package_hashes needs at least one package")
    studies = {p.study for p in loaded}
    demos = {p.demo for p in loaded}
    if len(studies) != 1 or len(demos) != 1:
        raise PackageError(E_INPUT, "all packages of a mapping share one study and DEMO status")
    study = loaded[0].study
    names = dict(keys or {})
    mapping: dict[str, str] = {}
    for pkg in loaded:
        unit = pkg.permutation["unit_id"] if pkg.permutation is not None else None
        if pkg.permutation is not None and pkg.permutation["set"] != set_name:
            raise PackageError(
                E_INPUT, f"{pkg.package_id}: sealed for the {pkg.permutation['set']} set"
            )
        default = unit if study == "B" and unit is not None else pkg.package_id
        key = names.get(pkg.package_id, default)
        match = _RUN_SHEET_KEYS[study].fullmatch(key)
        if match is None:
            raise PackageError(
                E_INPUT,
                f"{pkg.package_id}: key {key!r} is not a Study {study} run-sheet key "
                f"({_RUN_SHEET_KEYS[study].pattern}); pass keys={{package_id: key}}",
            )
        if _SET_OF_PREFIX[match.group(1)] != set_name:
            raise PackageError(E_INPUT, f"{key} does not belong to the {set_name} set")
        if study == "B" and unit is not None and key != unit:
            raise PackageError(E_INPUT, f"{pkg.package_id}: key {key} but sealed for {unit}")
        if key in mapping:
            raise PackageError(E_INPUT, f"two packages for {key}")
        mapping[key] = pkg.package_sha256
    return {
        "format": PACKAGE_HASHES_FORMAT,
        "format_version": PACKAGE_HASHES_FORMAT_VERSION,
        "study": study,
        "set": set_name,
        "demo": demos.pop(),
        "placeholder": False,
        "packages": {key: mapping[key] for key in sorted(mapping)},
    }


def write_package_hashes(doc: Mapping[str, Any], path: str | os.PathLike[str]) -> str:
    """Write a `package_hashes()` mapping (repository JSON) and return its SHA-256.

    Name it `<set>-package-hashes.json` next to the package manifests. A non-DEMO mapping
    cannot be written inside this repository (`E_POLICY`): it belongs with the packages
    in restricted storage.
    """
    target = Path(path)
    repo = _repo_root()
    if doc.get("demo") is not True and repo is not None:
        resolved = target.resolve()
        if repo in resolved.parents:
            raise PackageError(
                E_POLICY, "a study package-hash mapping stays outside the repository"
            )
    data = _json_bytes(dict(doc))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return _sha256(data)


# ---------------------------------------------------------------------------
# Loader (the checks #64 runs before a session)


def load_package(
    package_dir: str | os.PathLike[str],
    *,
    expected_package_sha256: str | None = None,
    check_composites: bool = True,
) -> LoadedPackage:
    """Verify a package and return it; raise `PackageIntegrityError` on any problem.

    In order, as the Unity audio subsystem (#64) will:

    1. `manifest.json` is strict JSON and matches `package.schema.json`; its
       `package_sha256` equals the recomputed hash (and `expected_package_sha256`, the
       value recorded in the run sheet, when given): `E_PACKAGE_HASH`.
    2. The directory holds exactly the listed files, plus `manifest.json`: a missing file
       is `E_FILE_MISSING`, any other file, link or folder entry is `E_FILE_EXTRA`.
    3. Every file has the listed size and SHA-256 (`E_HASH_MISMATCH`); every WAV is a
       canonical 48 kHz mono 16-bit WAV (`E_WAV_FORMAT`).
    4. `answers.json`, `audio.json` and `allocation.json` match the schema and agree with
       the manifest, the fixed matrix and each other (`E_CONTENT`); the slots agree with
       them (`E_PERMUTATION`, `E_MATRIX`, `E_DEMO`, `E_ALLOCATION`, `E_SCHEDULE`).
    5. With `check_composites`, every message (A: 32; B: all 1,536 option combinations)
       is recomputed from the atom WAVs with `composite_hash` and `message_length`, and
       every trained-message WAV must hold exactly its composite (`E_COMPOSITE`). Nothing
       is played and no held-out buffer is made.
    """
    root = Path(package_dir)
    problems: list[PackageProblem] = []

    def refuse() -> PackageIntegrityError:
        return PackageIntegrityError(root, problems)

    manifest_path = root / MANIFEST
    if not manifest_path.is_file() or manifest_path.is_symlink():
        problems.append(PackageProblem(E_MANIFEST, MANIFEST, "no manifest.json"))
        raise refuse()
    manifest, decode_problems = _decode(manifest_path.read_bytes(), MANIFEST)
    problems += decode_problems or _schema_problems(manifest, None, MANIFEST)
    if problems:
        raise refuse()
    if package_sha256(manifest) != manifest["package_sha256"]:
        problems.append(
            PackageProblem(E_PACKAGE_HASH, MANIFEST, "package_sha256 does not match the manifest")
        )
    if (
        expected_package_sha256 is not None
        and expected_package_sha256 != manifest["package_sha256"]
    ):
        problems.append(PackageProblem(E_PACKAGE_HASH, MANIFEST, "not the expected package"))
    listed: dict[str, dict[str, Any]] = manifest["files"]
    present, odd = _walk(root)
    present = [rel for rel in present if rel != MANIFEST]
    for rel in sorted(set(listed) - set(present)):
        problems.append(PackageProblem(E_FILE_MISSING, rel, "listed in the manifest, not found"))
    for rel in sorted(set(present) - set(listed)) + odd:
        problems.append(PackageProblem(E_FILE_EXTRA, rel, "not listed in the manifest"))
    data: dict[str, bytes] = {}
    for rel in sorted(set(listed) & set(present)):
        content = root.joinpath(*rel.split("/")).read_bytes()
        entry = listed[rel]
        if len(content) != entry["bytes"] or _sha256(content) != entry["sha256"]:
            problems.append(PackageProblem(E_HASH_MISMATCH, rel, "bytes differ from the manifest"))
        else:
            data[rel] = content
    if problems:
        raise refuse()
    pcms: dict[str, bytes] = {}
    for rel in sorted(data):
        if rel.endswith(".wav"):
            try:
                pcms[rel] = pcm_from_wav(data[rel])
            except ValueError as err:
                problems.append(PackageProblem(E_WAV_FORMAT, rel, str(err)))
    docs: dict[str, Any] = {}
    for rel, definition in ((ANSWERS, "answers"), (AUDIO, "audio"), (ALLOCATION, "allocation")):
        if rel in data:
            doc, found = _decode(data[rel], rel)
            problems += found or _schema_problems(doc, definition, rel)
            docs[rel] = doc
    schedules: dict[str, Any] = {}
    for rel in sorted(data):
        if rel.startswith(f"{SCHEDULES_DIR}/"):
            doc, found = _decode(data[rel], rel)
            problems += found
            schedules[rel] = doc
    if problems:
        raise refuse()
    study, package_id, demo = manifest["study"], manifest["package_id"], manifest["demo"]
    answers, audio = docs[ANSWERS], docs[AUDIO]
    problems += _answers_problems(answers, study, package_id, demo)
    problems += _audio_problems(manifest, audio, pcms)
    if problems:
        raise refuse()
    slot_problems, permutation = _slot_problems(
        study=study,
        demo=demo,
        package_id=package_id,
        answers=answers,
        permutation_bytes=data.get(PERMUTATION),
        allocation=docs.get(ALLOCATION),
        schedules=schedules,
    )
    problems += slot_problems
    checked = 0
    if check_composites and not problems:
        found, checked = _composite_problems(audio, pcms)
        problems += found
    if problems:
        raise refuse()
    return LoadedPackage(
        path=root,
        study=study,
        package_id=package_id,
        demo=demo,
        package_sha256=manifest["package_sha256"],
        manifest=manifest,
        answers=answers,
        audio=audio,
        permutation=permutation,
        allocation=docs.get(ALLOCATION),
        schedules=schedules,
        files=tuple(sorted(listed)),
        combinations_checked=checked,
    )


def _answers_problems(
    answers: Mapping[str, Any], study: str, package_id: str, demo: bool
) -> list[PackageProblem]:
    if [a["atom_id"] for a in answers["atoms"]] != list(ATOM_IDS):
        return [PackageProblem(E_CONTENT, ANSWERS, "atoms are not the 16 atoms in order")]
    labels = {a["atom_id"]: a["semantic_label"] for a in answers["atoms"]}
    problems = _labels_problems(labels, ANSWERS)
    if not problems and dict(answers) != _answers_doc(study, package_id, demo, labels):
        problems.append(
            PackageProblem(
                E_CONTENT,
                ANSWERS,
                "answers.json differs from the manifest identity, the fixed matrix or its own "
                "atom labels",
            )
        )
    return problems


def _audio_problems(
    manifest: Mapping[str, Any], audio: Mapping[str, Any], pcms: Mapping[str, bytes]
) -> list[PackageProblem]:
    problems: list[PackageProblem] = []

    def problem(message: str) -> None:
        problems.append(PackageProblem(E_CONTENT, AUDIO, message))

    files = manifest["files"]
    if (audio["study"], audio["package_id"]) != (manifest["study"], manifest["package_id"]):
        problem("study or package_id differs from the manifest")
        return problems
    wav_rows: list[tuple[str, Mapping[str, Any], str]] = []
    if manifest["study"] == "A":
        if audio["profile"] != manifest["profile"]:
            problem("profile differs from the manifest")
        if [a["atom_id"] for a in audio["atoms"]] != list(ATOM_IDS):
            problem("atoms are not the 16 atoms in order")
            return problems
        for row in audio["atoms"]:
            wav_rows.append((f"atoms/{row['atom_id']}.wav", row, "pcm_sha256"))
        if [m["message_id"] for m in audio["messages"]] != [m.message_id for m in MESSAGES]:
            problem("messages are not the 32 messages in order")
            return problems
        for row, ref in zip(audio["messages"], MESSAGES, strict=True):
            expected = (
                ref.action.atom_id,
                ref.referent.atom_id,
                "heldout" if ref.is_heldout else "trained",
            )
            if (row["action_atom"], row["referent_atom"], row["status"]) != expected:
                problem(f"{ref.message_id}: atoms or status differ from the fixed matrix")
            if row["duration_ms"] * SAMPLES_PER_MS != row["n_samples"]:
                problem(f"{ref.message_id}: duration_ms does not match n_samples")
            if not ref.is_heldout:
                wav_rows.append((f"messages/{ref.message_id}.wav", row, "composite_sha256"))
    else:
        if audio["waves"] != _waves():
            problem("waves differ from the matrix waves (V1 8 atoms, V2 4, V3 4)")
        keys = [(p.value, a, r) for p in PROFILES for a in ATOM_IDS for r in range(1, 5)]
        if [(o["profile"], o["atom_id"], o["rank"]) for o in audio["options"]] != keys:
            problem("options are not profile x atom x rank in order")
            return problems
        for row in audio["options"]:
            if row["menu"] != ("reserve" if row["rank"] == 4 else "shown"):
                problem(f"{row['path']}: menu does not match rank {row['rank']}")
            rel = _option_path(row["profile"], row["atom_id"], row["rank"])
            wav_rows.append((rel, row, "pcm_sha256"))
        if [m["message_id"] for m in audio["messages"]] != [m.message_id for m in MESSAGES]:
            problem("messages are not the 32 messages in order")
            return problems
        combo_keys = [(p.value, a, r) for p in PROFILES for a in range(1, 5) for r in range(1, 5)]
        for row, ref in zip(audio["messages"], MESSAGES, strict=True):
            expected = (
                ref.action.atom_id,
                ref.referent.atom_id,
                "heldout" if ref.is_heldout else "trained",
            )
            if (row["action_atom"], row["referent_atom"], row["status"]) != expected:
                problem(f"{ref.message_id}: atoms or status differ from the fixed matrix")
            combos = row["combinations"]
            if [(c["profile"], c["action_rank"], c["referent_rank"]) for c in combos] != combo_keys:
                problem(f"{ref.message_id}: combinations are not profile x rank x rank in order")
            if any(c["duration_ms"] * SAMPLES_PER_MS != c["n_samples"] for c in combos):
                problem(f"{ref.message_id}: duration_ms does not match n_samples")
    for rel, row, hash_key in wav_rows:
        if row["path"] != rel:
            problem(f"{rel}: path is {row['path']!r}")
        elif rel not in pcms:
            problem(f"{rel}: not a WAV file of the package")
        else:
            pcm = pcms[rel]
            if row["file_sha256"] != files[rel]["sha256"]:
                problem(f"{rel}: file_sha256 differs from the manifest")
            if _sha256(pcm) != row[hash_key] or len(pcm) // 2 != row["n_samples"]:
                problem(f"{rel}: samples do not match {hash_key} and n_samples")
    unreferenced = sorted(set(pcms) - {rel for rel, _, _ in wav_rows})
    if unreferenced:
        problem(f"WAV files not in the audio index: {unreferenced}")
    return problems


def _composite_problems(
    audio: Mapping[str, Any], pcms: Mapping[str, bytes]
) -> tuple[list[PackageProblem], int]:
    """Recompute every message's composite hash and length from the atom samples."""
    problems: list[PackageProblem] = []
    checked = 0
    if audio["study"] == "A":
        profile = audio["profile"]
        atoms = {a: AtomAudio(a, profile, pcms[f"atoms/{a}.wav"]) for a in ATOM_IDS}
        rows = [
            (row, atoms[row["action_atom"]], atoms[row["referent_atom"]], row["message_id"])
            for row in audio["messages"]
        ]
    else:
        options = {
            (o["profile"], o["atom_id"], o["rank"]): AtomAudio(
                o["atom_id"], o["profile"], pcms[o["path"]]
            )
            for o in audio["options"]
        }
        rows = [
            (
                combo,
                options[(combo["profile"], msg["action_atom"], combo["action_rank"])],
                options[(combo["profile"], msg["referent_atom"], combo["referent_rank"])],
                f"{msg['message_id']} {combo['profile']} "
                f"{combo['action_rank']}x{combo['referent_rank']}",
            )
            for msg in audio["messages"]
            for combo in msg["combinations"]
        ]
    for row, action, referent, label in rows:
        checked += 1
        if composite_hash(action, referent) != row["composite_sha256"]:
            problems.append(PackageProblem(E_COMPOSITE, AUDIO, f"{label}: composite hash differs"))
        if message_length(action, referent) != row["n_samples"]:
            problems.append(PackageProblem(E_COMPOSITE, AUDIO, f"{label}: length differs"))
    return problems, checked


# ---------------------------------------------------------------------------
# Leak scan


@dataclass
class _LeakIndex:
    heldout: set[str] = field(default_factory=set)
    trained_paths: dict[str, str] = field(default_factory=dict)
    """Trained-message WAV path -> composite hash (Study A)."""
    actions: list[tuple[tuple[str, str], str, bytes]] = field(default_factory=list)
    """(profile, family), atom ID, samples of every action atom or option."""
    referents: dict[tuple[str, str], list[tuple[str, bytes]]] = field(default_factory=dict)
    problems: list[LeakFinding] = field(default_factory=list)

    def add(self, profile: str, atom: str, pcm: bytes) -> None:
        ref = parse_atom_id(atom)
        group = (profile, ref.family)
        if ref.role == "action":
            self.actions.append((group, atom, pcm))
        else:
            self.referents.setdefault(group, []).append((atom, pcm))

    def embedded(self, data: bytes) -> list[str]:
        """Message IDs whose action + 9,600 zero samples + referent occur in `data`."""
        found: list[str] = []
        view = memoryview(data)
        start = 0
        while (begin := data.find(_GAP_BYTES, start)) >= 0:
            match = _NONZERO.search(data, begin + len(_GAP_BYTES))
            end = match.start() if match else len(data)
            for boundary in range(begin, end - len(_GAP_BYTES) + 1):
                for group, action, a_pcm in self.actions:
                    if boundary < len(a_pcm) or view[boundary - len(a_pcm) : boundary] != a_pcm:
                        continue
                    onset = boundary + len(_GAP_BYTES)
                    for referent, r_pcm in self.referents.get(group, []):
                        if view[onset : onset + len(r_pcm)] == r_pcm:
                            a, r = parse_atom_id(action), parse_atom_id(referent)
                            found.append(f"{a.family}-a{a.index}-r{r.index}")
            start = end
        return sorted(set(found))


def _leak_index(root: Path, audio: Mapping[str, Any]) -> _LeakIndex:
    index = _LeakIndex()

    def samples(rel: str) -> bytes | None:
        try:
            return pcm_from_wav(root.joinpath(*rel.split("/")).read_bytes())
        except (OSError, ValueError) as err:
            index.problems.append(LeakFinding(L_UNREADABLE, rel, f"cannot read atom audio: {err}"))
            return None

    if audio["study"] == "A":
        for row in audio["atoms"]:
            pcm = samples(row["path"])
            if pcm is not None:
                index.add(audio["profile"], row["atom_id"], pcm)
        for row in audio["messages"]:
            if row["status"] == "heldout":
                index.heldout.add(row["composite_sha256"])
            elif row["path"] is not None:
                index.trained_paths[row["path"]] = row["composite_sha256"]
    else:
        for row in audio["options"]:
            pcm = samples(row["path"])
            if pcm is not None:
                index.add(row["profile"], row["atom_id"], pcm)
        for row in audio["messages"]:
            if row["status"] == "heldout":
                index.heldout.update(c["composite_sha256"] for c in row["combinations"])
    return index


def _scan_audio(rel: str, data: bytes, index: _LeakIndex, study: str) -> list[LeakFinding]:
    out: list[LeakFinding] = []
    if _sha256(data) in index.heldout:
        out.append(LeakFinding(L_HELDOUT_AUDIO, rel, "file hash equals a held-out composite hash"))
    if rel.lower().endswith(".wav"):
        try:
            pcm = pcm_from_wav(data)
        except ValueError as err:
            out.append(LeakFinding(L_WAV_FORMAT, rel, f"not a canonical WAV: {err}"))
        else:
            pcm_hash = _sha256(pcm)
            n = len(pcm) // 2
            if pcm_hash in index.heldout:
                out.append(
                    LeakFinding(L_HELDOUT_AUDIO, rel, "PCM hash equals a held-out composite hash")
                )
            elif n not in MOTIF_SAMPLES and index.trained_paths.get(rel) != pcm_hash:
                out.append(
                    LeakFinding(
                        L_MESSAGE_AUDIO,
                        rel,
                        f"{n} samples: not an atom and not a listed trained-message WAV",
                    )
                )
    trained_here = index.trained_paths.get(rel)
    for message in index.embedded(data):
        ref = parse_message_id(message)
        if ref.is_heldout:
            out.append(
                LeakFinding(L_HELDOUT_AUDIO, rel, f"contains held-out message {message} as audio")
            )
        elif study == "B" or trained_here is None or rel != f"messages/{message}.wav":
            out.append(LeakFinding(L_MESSAGE_AUDIO, rel, f"contains message {message} as audio"))
    return out


def _strings(value: Any, keys: list[str], values: list[str]) -> None:  # noqa: ANN401
    if isinstance(value, dict):
        for key, item in value.items():
            keys.append(key)
            _strings(item, keys, values)
    elif isinstance(value, list):
        for item in value:
            _strings(item, keys, values)
    elif isinstance(value, str):
        values.append(value)


def _token_findings(rel: str, text: str) -> list[LeakFinding]:
    out = []
    for token in _TOKEN.findall(text):
        if token in METHOD_LABEL_TOKENS:
            out.append(LeakFinding(L_METHOD_LABEL, rel, f"method label token {token!r}"))
        elif token in DESIGNER_TOKENS:
            out.append(LeakFinding(L_DESIGNER_ID, rel, f"designer ID token {token!r}"))
        else:
            folded = token.casefold()
            for word in METHOD_WORDS:
                if word in folded:
                    out.append(
                        LeakFinding(L_METHOD_WORD, rel, f"method word {word!r} in {token!r}")
                    )
    return out


def _scan_text(rel: str, data: bytes, forbidden: Iterable[str]) -> list[LeakFinding]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return [LeakFinding(L_UNREADABLE, rel, "neither a canonical WAV nor UTF-8 text")]
    out: list[LeakFinding] = []
    keys: list[str] = []
    strings: list[str] = []
    if rel.endswith(".json"):
        doc, problems = _decode(data, rel)
        if problems:
            out.append(LeakFinding(L_UNREADABLE, rel, "not strict JSON"))
            strings.append(text)
        else:
            _strings(doc, keys, strings)
    else:
        strings.append(text)
    for key in keys:
        if key.casefold() == "source":
            out.append(LeakFinding(L_SOURCE_KEY, rel, "store provenance key 'source'"))
    for value in [*keys, *strings]:
        out.extend(_token_findings(rel, value))
    for needle in forbidden:
        if len(needle) >= MIN_FORBIDDEN_LENGTH and needle in text:
            out.append(LeakFinding(L_FORBIDDEN_STRING, rel, f"forbidden string {needle!r}"))
    return out


def scan_package(
    package_dir: str | os.PathLike[str], *, forbidden_strings: Iterable[str] = ()
) -> LeakReport:
    """Leak scan of every file in a package directory, listed or not.

    Held-out audio (the composite hashes come from the package's `audio.json`): no file
    whose bytes, or whose WAV samples, hash to a held-out composite; no file containing
    a held-out message as action + 9,600 zero samples + referent at any byte offset
    (Study B: no message audio at all); no WAV longer than a motif unless it is a listed
    trained-message WAV holding exactly its composite; every WAV canonical (no metadata
    chunks). Strings, in every other file (keys and values of JSON): no method label
    token `A1`/`A2`/`A3`, no designer ID `D1`..`D3`, no `METHOD_WORDS`, no `source` key,
    and none of `forbidden_strings` (for example the store's `source` values; strings
    shorter than 6 characters are skipped). Never raises for findings; raises
    `PackageError` if `audio.json` cannot be read.
    """
    root = Path(package_dir)
    raw = (root / AUDIO).read_bytes() if (root / AUDIO).is_file() else None
    if raw is None:
        raise PackageError(E_INPUT, f"{root}: no audio.json; cannot scan for held-out audio")
    audio, problems = _decode(raw, AUDIO)
    problems = problems or _schema_problems(audio, "audio", AUDIO)
    if problems:
        raise PackageError(problems[0].code, f"cannot scan {root}: {problems[0]}", problems)
    index = _leak_index(root, audio)
    files, odd = _walk(root)
    findings = list(index.problems)
    findings += [LeakFinding(L_UNREADABLE, rel, "not a regular file") for rel in odd]
    needles = sorted(set(forbidden_strings))
    for rel in files:
        data = root.joinpath(*rel.split("/")).read_bytes()
        findings += _scan_audio(rel, data, index, audio["study"])
        if not rel.lower().endswith(".wav"):
            findings += _scan_text(rel, data, needles)
    unique = tuple(dict.fromkeys(findings))
    return LeakReport(unique, len(files), len(index.heldout))
