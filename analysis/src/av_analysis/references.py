"""Frozen reference inputs of a visit (#33).

Reads, from ``inputs/`` of a data root (paths ``paths.INPUT_PATHS``), everything a visit
is reconciled against: the person's visit schedules (``av-schedules/visit-schedule``;
hidden-answer material), the visit's generated run sheet, the learner-facing slot list
(Study A) or dyad list (Study B, roles needed for C6), the reveal log (coded participant
ID to person slot), the package JSON documents (``av-sound/package`` manifest and
``audio.json``, package hash), the package-hash mapping and, for Study B, the store
snapshots and selection receipts of the dyad's book. These are file contracts with the
schedules, sound and apparatus components; no module of another stack is imported for
them (``av_schedules`` is on this stack and is imported: the run sheet and schedule
checks, the reveal-log replay and the package-hash mapping parser).

**Expected waveform hashes (C3).** ``audio.json`` gives two hashes per playable item: the
PCM-sample hash (``pcm_sha256`` of atoms and Study B options, ``composite_sha256`` of
messages) and the WAV file hash (``file_sha256``; ``null`` for audio that exists only
when composed in memory: held-out messages and all Study B messages). A logged
``waveform_sha256`` is the file hash for file playback and the PCM hash for composed
audio (``vocab``, **Pending** #64/#72): C3 accepts a logged hash equal to either hash of
the scheduled item (:class:`ExpectedHash`), reports ``WAVEFORM_HASH_MISMATCH`` when it
equals neither, and ``WAVEFORM_HASH_MISSING`` when it is empty, except for composed audio
whose PCM hash the export carries in the ``pcm_sha256`` extension column. Keys of
:attr:`References.expected_hashes`: Study A the message or atom ID; Study B
``<atom_id>/<profile>/<rank>`` for options and
``<message_id>/<profile>/<action_rank>/<referent_rank>`` for message combinations.

**Store snapshots (C5, Study B).** ``inputs/store-snapshots/{unit_id}/{visit}.json`` is the
menu-store bridge's ``verified_snapshot`` result after the visit's selections (a plain
verification for W1 and W4; ``docs/interfaces/menu-store-bridge.md``, #70):
``book_head``, ``snapshot_sha256``, ``journal_head``, ``profile``,
``profile_selection_receipt_sha256``, ``manifest_sha256`` and ``entries`` (``atom_id``,
``profile``, ``rank``, ``pcm_sha256``, ``file_sha256``, ``selection_receipt_sha256``);
``receipts.jsonl`` holds the bridge's selection receipts in order. The selected profile
and the committed rank of every atom at a visit come from that visit's snapshot (or the
latest earlier one when it is missing, reported as ``REFERENCE_INPUT``). C5 rules are in
``reconcile_checks``. The bridge returns no recipe or semantic label, so per-atom recipe
identity is covered only through ``snapshot_sha256`` and the head chain: **Pending**
(#70, #26) a store-verification artifact that lists recipe hashes per atom. A REAL root
refuses a snapshot or receipt whose ``source_kind`` is ``synthetic``.

**Packages.** ``inputs/packages/{package_id}/`` is named by the anonymous book ID (A, from
the slot list) or the bank ID (B, from the dyad list); its manifest's own ``package_id``
may be the ``DEMO-`` form of a synthetic package. The expected package hash comes from the
package-hash mapping (key: book ID or dyad slot ID); the manifest's recomputed hash is
kept separately so C3 can report ``PACKAGE_HASH_MISMATCH``.

Masking: never reads ``keys/``; refuses an ``inputs/`` tree that contains a book key.
DEMO inputs (``demo: true``) are refused in a REAL root. The Study B roles read from the
dyad list are used only inside C6 (and the pair-gap rule of ``derive``) and never written
to any output.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from av_schedules.orders import check_visit_schedule, study_visits
from av_schedules.reveal import RevealError, RevealLog
from av_schedules.run_sheets import parse_package_hashes, read_run_sheet, run_sheet_findings

from .fileio import read_bytes, sha256_bytes
from .loaders import RefusedInputError
from .paths import INPUT_PATHS, DataRoot, parse_visit_id

PACKAGE_FORMAT: Final = "av-sound/package"
PACKAGE_AUDIO_FORMAT: Final = "av-sound/package-audio"
SET_OF_CODE: Final[dict[str, str]] = {"P": "pilot", "C": "confirmatory", "S": "confirmatory"}
SNAPSHOT_KEYS: Final = frozenset(
    {
        "book_head",
        "snapshot_sha256",
        "profile",
        "profile_selection_receipt_sha256",
        "entries",
        "manifest_sha256",
        "source_kind",
    }
)
ENTRY_KEYS: Final = frozenset(
    {"atom_id", "profile", "rank", "pcm_sha256", "file_sha256", "selection_receipt_sha256"}
)
RECEIPT_KEYS: Final = frozenset(
    {"before_head", "after_head", "status", "menu_key", "rank", "profile", "receipt_sha256"}
)


class ReferenceError(ValueError):
    """A required reference input is missing or invalid (``path`` names it)."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


@dataclass(frozen=True)
class ExpectedHash:
    """The two hashes a played item may be logged with (``audio.json``)."""

    item_key: str  # message or atom ID (A); option or message combination key (B)
    pcm_sha256: str  # PCM-sample hash: composite_sha256 (messages) or pcm_sha256
    file_sha256: str | None  # WAV file hash; None when the audio exists only when composed

    def matches(self, logged: str) -> bool:
        """True if a logged ``waveform_sha256`` equals either hash of the item."""
        return logged == self.pcm_sha256 or (
            self.file_sha256 is not None and logged == self.file_sha256
        )


@dataclass(frozen=True)
class References:
    """Reference inputs of one visit."""

    study: str
    set_name: str
    unit_id: str
    person_id: str
    visit: str
    schedules: Mapping[str, Mapping[str, Any]]  # visit -> schedule document (all visits)
    run_sheet_rows: tuple[Mapping[str, str], ...]
    package_id: str  # book ID (A) or bank ID (B)
    package_sha256: str  # expected, from the package-hash mapping
    # Study A: message or atom ID. Study B: ``<atom_id>/<profile>/<rank>`` for options and
    # ``<message_id>/<profile>/<action_rank>/<referent_rank>`` for message combinations.
    expected_hashes: Mapping[str, ExpectedHash]
    store_snapshots: Mapping[str, Mapping[str, Any]]  # B: visit -> verified_snapshot
    store_receipts: tuple[Mapping[str, Any], ...]  # B: bridge selection receipts, in order
    partner_person_id: str | None  # B: the other member of the dyad
    active_person_id: str | None  # B: the dyad's active member; C6 only, never written
    inputs: Mapping[str, str]  # every file read (relative path) -> SHA-256
    # Added by #33 (the skeleton fixed the fields above):
    participant_id: str = ""  # coded participant ID bound to the slot in the reveal log
    package_manifest_sha256: str = ""  # recomputed from inputs/packages/<id>/manifest.json
    profile: str | None = None  # A: the book's profile (slot list); B: see committed()
    input_bytes: Mapping[str, int] = field(default_factory=dict)  # path -> size
    problems: tuple[tuple[str, str], ...] = ()  # non-fatal reference problems (path, why)

    @property
    def visit_id(self) -> str:
        return f"{self.person_id}-{self.visit}"


class InputReader:
    """Reads files of the read-only areas of a data root and records every file read
    with its hash and size."""

    def __init__(self, root: DataRoot) -> None:
        self.root = root
        self.sha: dict[str, str] = {}
        self.size: dict[str, int] = {}

    def exists(self, rel: str) -> bool:
        area, _, rest = rel.partition("/")
        return self.root.input_path(area, rest).is_file()  # type: ignore[arg-type]

    def read(self, rel: str) -> bytes:
        area, _, rest = rel.partition("/")
        path = self.root.input_path(area, rest)  # type: ignore[arg-type]
        if not path.is_file():
            raise ReferenceError(rel, "missing")
        data = read_bytes(path)
        self.sha[rel] = sha256_bytes(data)
        self.size[rel] = len(data)
        return data

    def json(self, rel: str) -> Any:  # noqa: ANN401 - parsed JSON of any shape
        try:
            return json.loads(self.read(rel).decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ReferenceError(rel, f"not UTF-8 JSON ({exc})") from None


def set_of_unit(unit_id: str) -> str:
    """``pilot`` or ``confirmatory`` from a unit ID (``A-P01``, ``B-C12``, ``B-S03``)."""
    return SET_OF_CODE[unit_id[2]]


def input_path(key: str, **fields: str) -> str:
    """A reference input path (``paths.INPUT_PATHS``) with its fields filled in."""
    return INPUT_PATHS[key].format(**fields)


def canonical_sha256(doc: Mapping[str, Any], omit: str) -> str:
    """SHA-256 of compact, sorted-key, ASCII JSON of ``doc`` without the field ``omit``
    (the self-hash rule of package manifests and the menu-store bridge)."""
    body = {k: v for k, v in doc.items() if k != omit}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def _refuse_demo(root: DataRoot, rel: str, doc: object) -> None:
    if not root.synthetic and isinstance(doc, Mapping) and doc.get("demo") is True:
        raise RefusedInputError(f"{rel}: DEMO input in a REAL data root")


# ---------------------------------------------------------------------------------------
# Reveal log and lists


@dataclass(frozen=True)
class RevealData:
    """A study and set's allocation list and validated reveal log."""

    study: str
    set_name: str
    list_path: str
    list_doc: Mapping[str, Any]
    log_path: str | None
    lines: tuple[Mapping[str, Any], ...]  # parsed log lines (empty without a log)
    log_sha256: str | None

    def reveals(self) -> Iterator[Mapping[str, Any]]:
        for line in self.lines:
            if line.get("event") == "reveal":
                yield line["entry"]

    def bindings(self) -> dict[str, str]:
        """Person slot -> coded participant ID for every revealed slot."""
        out: dict[str, str] = {}
        for entry in self.reveals():
            if self.study == "A":
                out[entry["slot_id"]] = entry["participant_id"]
            else:
                for member in entry["members"]:
                    out[member["slot_id"]] = member["participant_id"]
        return out


def _list_rel(study: str, set_name: str) -> str:
    return input_path("slots" if study == "A" else "dyads", set=set_name)


def load_reveal(
    root: DataRoot, study: str, set_name: str, reader: InputReader | None = None
) -> RevealData | None:
    """The list and reveal log of a study and set (None if the list is absent).

    Raises :class:`ReferenceError` for a damaged list or log (the hash chain is replayed
    with ``av_schedules.reveal.RevealLog``, which never writes when only reading) and
    :class:`~av_analysis.loaders.RefusedInputError` for DEMO lists in a REAL root.
    """
    reader = reader or InputReader(root)
    list_rel = _list_rel(study, set_name)
    if not reader.exists(list_rel):
        return None
    doc = reader.json(list_rel)
    _refuse_demo(root, list_rel, doc)
    log_rel = input_path("reveal_log", study=study, set=set_name)
    if not reader.exists(log_rel):
        return RevealData(study, set_name, list_rel, doc, None, (), None)
    data = reader.read(log_rel)
    try:
        RevealLog(
            root.input_path("inputs", list_rel.removeprefix("inputs/")),
            root.input_path("inputs", log_rel.removeprefix("inputs/")),
        )
    except (RevealError, ValueError, OSError) as exc:
        raise ReferenceError(log_rel, f"reveal log or list does not validate ({exc})") from None
    lines = tuple(json.loads(line) for line in data.splitlines() if line.strip())
    return RevealData(study, set_name, list_rel, doc, log_rel, lines, sha256_bytes(data))


def revealed_persons(root: DataRoot) -> Mapping[str, str]:
    """Person slot -> coded participant ID for every revealed slot (reveal log)."""
    out: dict[str, str] = {}
    for study in ("A", "B"):
        for set_name in ("pilot", "confirmatory"):
            data = load_reveal(root, study, set_name)
            if data is not None:
                out.update(data.bindings())
    return dict(sorted(out.items()))


def _refuse_book_key(root: DataRoot) -> None:
    folder = root.input_path("inputs", "schedules/A")
    if folder.is_dir() and any(folder.glob("*book-key*")):
        raise RefusedInputError(
            "inputs/schedules/A holds a book key: it belongs in keys/ (read by #34 only)"
        )


# ---------------------------------------------------------------------------------------
# Packages


def _sha(value: object) -> str | None:
    if isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value):
        return value
    return None


def _expected_a(audio: Mapping[str, Any], rel: str) -> dict[str, ExpectedHash]:
    out: dict[str, ExpectedHash] = {}
    for atom in audio.get("atoms", []):
        pcm, fil = _sha(atom.get("pcm_sha256")), _sha(atom.get("file_sha256"))
        if not isinstance(atom.get("atom_id"), str) or pcm is None:
            raise ReferenceError(rel, "atom entry without atom_id or pcm_sha256")
        out[atom["atom_id"]] = ExpectedHash(atom["atom_id"], pcm, fil)
    for message in audio.get("messages", []):
        comp, fil = _sha(message.get("composite_sha256")), _sha(message.get("file_sha256"))
        if not isinstance(message.get("message_id"), str) or comp is None:
            raise ReferenceError(rel, "message entry without message_id or composite_sha256")
        out[message["message_id"]] = ExpectedHash(message["message_id"], comp, fil)
    return out


def option_key(atom_id: str, profile: str, rank: int) -> str:
    """Study B expected-hash key of an option."""
    return f"{atom_id}/{profile}/{rank}"


def combination_key(message_id: str, profile: str, action_rank: int, referent_rank: int) -> str:
    """Study B expected-hash key of a message combination."""
    return f"{message_id}/{profile}/{action_rank}/{referent_rank}"


def _expected_b(audio: Mapping[str, Any], rel: str) -> dict[str, ExpectedHash]:
    out: dict[str, ExpectedHash] = {}
    for option in audio.get("options", []):
        pcm, fil = _sha(option.get("pcm_sha256")), _sha(option.get("file_sha256"))
        try:
            key = option_key(option["atom_id"], option["profile"], int(option["rank"]))
        except (KeyError, TypeError, ValueError):
            raise ReferenceError(rel, "option entry without atom_id, profile or rank") from None
        if pcm is None:
            raise ReferenceError(rel, f"option {key} without pcm_sha256")
        out[key] = ExpectedHash(key, pcm, fil)
    for message in audio.get("messages", []):
        for combo in message.get("combinations", []):
            comp = _sha(combo.get("composite_sha256"))
            try:
                key = combination_key(
                    message["message_id"],
                    combo["profile"],
                    int(combo["action_rank"]),
                    int(combo["referent_rank"]),
                )
            except (KeyError, TypeError, ValueError):
                raise ReferenceError(rel, "message combination without its keys") from None
            if comp is None:
                raise ReferenceError(rel, f"combination {key} without composite_sha256")
            out[key] = ExpectedHash(key, comp, None)
    return out


def _load_package(
    root: DataRoot, reader: InputReader, study: str, package_id: str
) -> tuple[str, dict[str, ExpectedHash], str | None]:
    """(recomputed package hash, expected hashes, A profile) of a package folder."""
    manifest_rel = input_path("package_manifest", package_id=package_id)
    audio_rel = input_path("package_audio", package_id=package_id)
    manifest = reader.json(manifest_rel)
    if (
        not isinstance(manifest, dict)
        or manifest.get("format") != PACKAGE_FORMAT
        or manifest.get("format_version") != 1
        or manifest.get("study") != study
        or not isinstance(manifest.get("files"), dict)
    ):
        raise ReferenceError(manifest_rel, f"not a Study {study} {PACKAGE_FORMAT} manifest")
    _refuse_demo(root, manifest_rel, manifest)
    audio_bytes = reader.read(audio_rel)
    entry = manifest["files"].get("audio.json")
    if (
        not isinstance(entry, dict)
        or entry.get("sha256") != sha256_bytes(audio_bytes)
        or entry.get("bytes") != len(audio_bytes)
    ):
        raise ReferenceError(audio_rel, "differs from the manifest's audio.json entry")
    try:
        audio = json.loads(audio_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ReferenceError(audio_rel, "not UTF-8 JSON") from None
    if not isinstance(audio, dict) or audio.get("format") != PACKAGE_AUDIO_FORMAT:
        raise ReferenceError(audio_rel, f"not a {PACKAGE_AUDIO_FORMAT} document")
    expected = _expected_a(audio, audio_rel) if study == "A" else _expected_b(audio, audio_rel)
    profile = audio.get("profile") if study == "A" else None
    return canonical_sha256(manifest, "package_sha256"), expected, profile


# ---------------------------------------------------------------------------------------
# Study B store


def _snapshot_problem(doc: object) -> str | None:
    if not isinstance(doc, dict) or not set(doc) >= SNAPSHOT_KEYS:
        return f"not a verified_snapshot (needs {sorted(SNAPSHOT_KEYS)})"
    entries = doc["entries"]
    if not isinstance(entries, list) or any(
        not isinstance(e, dict) or set(e) != ENTRY_KEYS for e in entries
    ):
        return f"entries must hold exactly {sorted(ENTRY_KEYS)}"
    return None


def _load_store(
    root: DataRoot, reader: InputReader, unit_id: str, upto: str
) -> tuple[dict[str, Mapping[str, Any]], tuple[Mapping[str, Any], ...], list[tuple[str, str]]]:
    snapshots: dict[str, Mapping[str, Any]] = {}
    problems: list[tuple[str, str]] = []
    visits = study_visits("B")
    for v in visits[: visits.index(upto) + 1]:
        rel = input_path("store_snapshot", unit_id=unit_id, visit=v)
        if not reader.exists(rel):
            problems.append((rel, "store snapshot missing"))
            continue
        try:
            doc = reader.json(rel)
        except ReferenceError as exc:
            problems.append((rel, exc.message))
            continue
        why = _snapshot_problem(doc)
        if why is not None:
            problems.append((rel, why))
            continue
        if not root.synthetic and doc.get("source_kind") == "synthetic":
            raise RefusedInputError(f"{rel}: synthetic store snapshot in a REAL root")
        snapshots[v] = doc
    receipts: list[Mapping[str, Any]] = []
    rel = input_path("store_receipts", unit_id=unit_id)
    if not reader.exists(rel):
        problems.append((rel, "store receipts missing"))
    else:
        for n, line in enumerate(reader.read(rel).splitlines(), start=1):
            if not line.strip():
                continue
            try:
                receipt = json.loads(line)
            except ValueError:
                problems.append((rel, f"line {n} is not JSON"))
                continue
            if not isinstance(receipt, dict) or not set(receipt) >= RECEIPT_KEYS:
                problems.append((rel, f"line {n} is not a selection receipt"))
                continue
            if not root.synthetic and receipt.get("source_kind") == "synthetic":
                raise RefusedInputError(f"{rel}: synthetic receipt in a REAL root")
            receipts.append(receipt)
    return snapshots, tuple(receipts), problems


def committed(refs: References, visit: str | None = None) -> tuple[str | None, dict[str, int]]:
    """Study B: the selected profile and atom -> committed rank at ``visit`` (default: the
    reconciled visit), from its store snapshot or the latest earlier one."""
    visits = study_visits("B")
    target = visit or refs.visit
    for v in reversed(visits[: visits.index(target) + 1]):
        snap = refs.store_snapshots.get(v)
        if snap is not None:
            ranks = {e["atom_id"]: int(e["rank"]) for e in snap["entries"]}
            profile = snap.get("profile")
            return (profile if isinstance(profile, str) else None), ranks
    return None, {}


def heldout_visits(refs: References) -> dict[str, str]:
    """Held-out message ID -> visit of its novel test, from the person's schedules."""
    out: dict[str, str] = {}
    for visit, doc in refs.schedules.items():
        for block in doc["blocks"]:
            if block["block"] == "novel":
                for item in block["items"]:
                    out[item["message_id"]] = visit
    return out


def schedule_items(doc: Mapping[str, Any]) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    """(block, item) pairs of a schedule document in run order."""
    return [(b, item) for b in doc["blocks"] for item in b["items"]]


def cue_of(item: Mapping[str, Any]) -> str:
    """The cue ID of a schedule item (message, atom or speech ID; empty if none)."""
    for key in ("message_id", "atom_id", "speech_id"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


# ---------------------------------------------------------------------------------------
# Visit references


def _schedule(
    root: DataRoot, reader: InputReader, study: str, unit_id: str, person: str, visit: str
) -> Mapping[str, Any]:
    rel = input_path("schedule", study=study, unit_id=unit_id, person_id=person, visit=visit)
    doc = reader.json(rel)
    if not isinstance(doc, dict) or doc.get("format") != "av-schedules/visit-schedule":
        raise ReferenceError(rel, "not an av-schedules/visit-schedule document")
    _refuse_demo(root, rel, doc)
    who = (doc.get("study"), doc.get("unit_id"), doc.get("person_id"), doc.get("visit"))
    if who != (study, unit_id, person, visit):
        raise ReferenceError(rel, "schedule identity differs from its path")
    problems = check_visit_schedule(doc)
    if problems:
        raise ReferenceError(rel, f"schedule check fails: {problems[0]}")
    return doc


def load_references(root: DataRoot, visit_id: str) -> References:
    """Reference inputs of a visit; raises :class:`ReferenceError` (a ``ValueError``)
    naming the missing or invalid input, and
    :class:`~av_analysis.loaders.RefusedInputError` for inputs of the other data kind."""
    return load_references_with(root, visit_id, InputReader(root))


def load_references_with(root: DataRoot, visit_id: str, reader: InputReader) -> References:
    """:func:`load_references` recording every file read in ``reader`` (also when it
    raises, so a report can list the inputs it read)."""
    person, visit = parse_visit_id(visit_id)
    study, unit_id = person[0], person[:5]
    set_name = set_of_unit(unit_id)
    _refuse_book_key(root)
    reveal = load_reveal(root, study, set_name, reader)
    list_rel = _list_rel(study, set_name)
    if reveal is None:
        raise ReferenceError(list_rel, "missing")
    if reveal.log_path is None:
        raise ReferenceError(input_path("reveal_log", study=study, set=set_name), "missing")
    bindings = reveal.bindings()
    if person not in bindings:
        raise ReferenceError(reveal.log_path, f"person slot {person} is not revealed")
    partner = active = None
    profile: str | None = None
    if study == "A":
        slots = {s["slot_id"]: s for s in reveal.list_doc.get("slots", [])}
        if person not in slots:
            raise ReferenceError(list_rel, f"no slot {person}")
        package_id = key = slots[person]["book_id"]
    else:
        dyads = {d["unit_id"]: d for d in reveal.list_doc.get("dyads", [])}
        if unit_id not in dyads:
            raise ReferenceError(list_rel, f"no dyad slot {unit_id}")
        members = dyads[unit_id]["members"]
        partner = next(m["slot_id"] for m in members if m["slot_id"] != person)
        active = next(m["slot_id"] for m in members if m["role"] == "active")
        package_id, key = dyads[unit_id]["bank_id"], unit_id
    mapping_rel = input_path("package_hashes", study=study, set=set_name)
    try:
        mapping = parse_package_hashes(reader.read(mapping_rel))
    except ValueError as exc:
        raise ReferenceError(mapping_rel, str(exc)) from None
    if not root.synthetic and mapping.demo:
        raise RefusedInputError(f"{mapping_rel}: DEMO mapping in a REAL data root")
    if (mapping.study, mapping.set_name) != (study, set_name):
        raise ReferenceError(mapping_rel, f"mapping is not for {study} {set_name}")
    hash_cell = mapping.cell(key)
    if hash_cell is None:
        raise ReferenceError(mapping_rel, f"no package hash for {key}")
    expected_package = hash_cell.partition(":")[2]
    schedules: dict[str, Mapping[str, Any]] = {}
    problems: list[tuple[str, str]] = []
    for v in study_visits(study):  # type: ignore[arg-type]
        try:
            schedules[v] = _schedule(root, reader, study, unit_id, person, v)
        except ReferenceError as exc:
            if v == visit:
                raise
            problems.append((exc.path, exc.message))
    sheet_rel = input_path("run_sheet", study=study, unit_id=unit_id, person_id=person, visit=visit)
    sheet = reader.read(sheet_rel)
    findings = run_sheet_findings(sheet, schedules[visit], hash_cell)
    if findings:
        raise ReferenceError(sheet_rel, f"run sheet check fails: {findings[0]}")
    _, sheet_rows = read_run_sheet(sheet)
    manifest_sha, expected, a_profile = _load_package(root, reader, study, package_id)
    snapshots: dict[str, Mapping[str, Any]] = {}
    receipts: tuple[Mapping[str, Any], ...] = ()
    if study == "A":
        profile = a_profile
    else:
        snapshots, receipts, store_problems = _load_store(root, reader, unit_id, visit)
        problems.extend(store_problems)
    return References(
        study=study,
        set_name=set_name,
        unit_id=unit_id,
        person_id=person,
        visit=visit,
        schedules=schedules,
        run_sheet_rows=tuple(sheet_rows),
        package_id=package_id,
        package_sha256=expected_package,
        expected_hashes=expected,
        store_snapshots=snapshots,
        store_receipts=receipts,
        partner_person_id=partner,
        active_person_id=active,
        inputs=dict(sorted(reader.sha.items())),
        participant_id=bindings[person],
        package_manifest_sha256=manifest_sha,
        profile=profile,
        input_bytes=dict(sorted(reader.size.items())),
        problems=tuple(problems),
    )
