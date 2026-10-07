"""Round orchestrator, panel session host and commit/fallback (#20; Study A protocol §3.1-§3.7).

Per atom (in `BatchConfig.atom_order`), for rounds 1..4: open the three proposal windows
in parallel (`proposers.RoundProposer`, at most 3 x 40 s), then play the 9 rating slots
in the panel's book order (`BatchConfig.panel.order`, blocks of three candidates in slot
order), then compute the selector state and return each book's own feedback. After round
4 commit the incumbent, or scan the fallback bank, or substitute the whole fallback
book. State is persisted after every slot, so a batch pauses between atoms and resumes.

Before the first atom: `genconfig.check_run_config(generation_config, kind=...,
freeze_manifest=...)` (confirmatory batches refuse to start unless the frozen config hash
matches), and the batch config, generation config, fallback set and meaning set must pin
the same threshold and hashes. On open and on `resume`, every log is repaired with
`jsonio.repair_torn_tail` (logged as a `log_repaired` timing event).

Proposer inputs: A2 gets `BookState.without_labels()` and `semantic_label=None`; A1 and A3
get the atom's meaning label (texts from `meanings`). Each request holds one book's own
state and history only. Stations show meaning texts and never a book name; the operator
console (`Orchestrator.console`) shows panel aliases (`BatchConfig.panel.aliases`) only,
never book IDs.

Whole-book substitution (architecture §3.2; Study A protocol §3.7). When the bank scan of
atom k of book X finds nothing: void X's store book (`cause="failed_generation"`), commit
the 16 atoms of the profile's frozen fallback book to a new store book `<X>-FB` (16
`commit` records, `source="fallback_book"`, `failed_generation=true`), and log a
`book_substituted` timing event. The method then keeps proposing, and its candidates keep
being rated, for atoms k+1..16 with unchanged budget and panel schedule: its `BookState`
is the continued book (atoms committed before k plus the incumbents archived since),
round-4 decisions are `archive` / `archive_none` with `book_substituted=true`, nothing is
committed and no further scan runs. So a batch always has 576 slot records, 576 rating
records per rater, 192 decisions and one commit per atom in each book's final store book
(48); a book substituted at atom k adds its k-1 superseded commits.

Rater withdrawal (§3.3): every record is kept, a `batch_incomplete` timing event is
logged, the batch's store books are voided (`cause="batch_rebuild"`) and the run stops
with `BatchIncomplete`. The batch is rebuilt in a new run with `rebuild_batch_config`
(new panel, new aliases, new seed namespace).

Logs (`rundir.LOG_FILES`): `decision`, `commit`, `fallback_scan`, `timing`, and, as the
panel session host (`panel_session`), `rating`, `play` and the panel timing events;
`slot` records come from the proposers' ledger. Operator guide and decisions:
`generation/docs/orchestrator.md`. A study batch is built from the real components and
run by `av_generation.batch_runner` (`python -m av_generation.batch_runner run ...`).
"""

from __future__ import annotations

import csv
import hashlib
import io
import itertools
import json
import os
import threading
from collections import Counter
from collections.abc import Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Any, Final

from av_sound.fallback import FallbackSet, scan_fallback
from av_sound.features import features, parse_threshold
from av_sound.recipe import Profile, Recipe
from av_sound.renderer import render
from av_sound.store import CommitRejected, StoreError, VocabularyStore, check_book_id
from av_sound.tables import SAMPLES_PER_MS
from av_sound.validate import nearest_reference
from av_sound.wav import wav_bytes

from av_generation import __version__
from av_generation._orch_host import PanelHost, RatingSlotPlan, printable
from av_generation._orch_index import LogIndex, is_valid_candidate, recipe_or_none
from av_generation.clock import Clock, ManualClock, ScaledClock, utc_text
from av_generation.config import (
    BatchConfig,
    BookAssignment,
    ConfigSources,
    PanelAssignment,
    RaterSeat,
)
from av_generation.constants import (
    APPOINTMENTS_PER_BATCH,
    ATOMS_PER_APPOINTMENT,
    PANEL_ORDERS,
    PROPOSAL_WINDOW_MS,
    RATING_SLOT_MS,
    REFERENCE_ONSET_MS,
    ROUNDS_PER_ATOM,
    SLOT_CAP_MS,
    SLOTS_PER_ROUND,
)
from av_generation.genconfig import (
    GenerationConfig,
    check_run_config,
    current_code,
    fallback_pins,
)
from av_generation.ids import (
    PANEL_ALIAS_ALPHABET,
    STUDY_A_METHODS,
    Method,
    RunKind,
    Study,
    check_id,
    is_demo,
    proposal_slot_id,
    rating_slot_id,
    slot_index,
)
from av_generation.jsonio import document_text, file_sha256, repair_torn_tail
from av_generation.meanings import MeaningSet
from av_generation.panel_session import AssetRef, PanelSessionHost, PanelSlot
from av_generation.proposers import (
    BookState,
    CommittedAtom,
    RoundProposer,
    RoundRequest,
    RoundResult,
)
from av_generation.records import (
    CommitRecord,
    DecisionRecord,
    FallbackScanRecord,
    RecordWriter,
    RunBook,
    RunCode,
    RunManifest,
    TimingEvent,
)
from av_generation.rundir import LOG_FILES, MANIFEST_NAME, RunLayout, check_run_id, relative_files
from av_generation.seeds import panel_seed_key, rng_for, seed_from_key

__all__ = [
    "BatchDefinition",
    "BatchIncomplete",
    "BookConsole",
    "ConsoleView",
    "Orchestrator",
    "OrchestratorError",
    "PANEL_ORDER_COLUMNS",
    "RatingSlotPlan",
    "build_batch_config",
    "check_batch_pins",
    "check_permutation",
    "nearest_committed",
    "panel_aliases",
    "panel_order_rows",
    "panel_order_schedule",
    "read_batch_table",
    "read_book_key",
    "rebuild_batch_config",
    "substitute_book_id",
    "write_panel_order_csv",
]

PRELOAD_LEAD_MS: Final = 1_000
"""Default time between the round's `preload` event and the start of rating slot 1."""
SLOT_EVENT_LEAD_MS: Final = 500
"""Default lead of each later `slot` event before its start (stations schedule audio)."""
WINDOW_GRACE_MS: Final = 500
"""A proposal window that ends later than its deadline plus this is logged as overrun."""
SUBSTITUTE_SUFFIX: Final = "-FB"

E_CONFIG: Final = "E_CONFIG"
E_KIND: Final = "E_KIND"
E_PROPOSERS: Final = "E_PROPOSERS"
E_PROPOSER_RESULT: Final = "E_PROPOSER_RESULT"
E_PROPOSER_FAILED: Final = "E_PROPOSER_FAILED"
E_ATOM_ORDER: Final = "E_ATOM_ORDER"
E_ATOM_DONE: Final = "E_ATOM_DONE"
E_APPOINTMENT: Final = "E_APPOINTMENT"
E_RESUME_PARTIAL_WINDOW: Final = "E_RESUME_PARTIAL_WINDOW"
E_RUN_MISMATCH: Final = "E_RUN_MISMATCH"
E_BATCH_INCOMPLETE: Final = "E_BATCH_INCOMPLETE"
E_STORE: Final = "E_STORE"
E_SCHEDULE: Final = "E_SCHEDULE"
E_BATCH_TABLE: Final = "E_BATCH_TABLE"
E_REBUILD: Final = "E_REBUILD"


class OrchestratorError(RuntimeError):
    """The orchestrator refuses to start or continue; `.code` names the rule."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class BatchIncomplete(OrchestratorError):
    """A rater withdrew (or the operator stopped the batch): the batch must be rebuilt."""

    def __init__(self, message: str) -> None:
        super().__init__(E_BATCH_INCOMPLETE, message)


# ---------------------------------------------------------------------------
# Panel order schedule and per-panel aliases (Study A protocol §3.1)


_ORDER_PERMUTATIONS: Final[tuple[tuple[int, ...], ...]] = tuple(
    itertools.permutations(range(1, len(PANEL_ORDERS) + 1))
)
"""The 720 permutations of the order indices 1..6, in lexicographic order."""


def _group_orders(set_ns: str, sizes: Mapping[str, int]) -> dict[str, list[int]]:
    """Order indices for groups of `sizes[g]` panels from one seeded stream.

    Each group's panels take whole permutations of 1..6 (chunk c covers its panels
    6c..6c+5). Within a chunk the groups draw in sorted order, each uniformly among the
    permutations that differ at every position from the groups drawn before it (a Latin
    rectangle; at most 3 profile groups, so one always exists)."""
    rng = rng_for(panel_seed_key(set_ns, "orders"))
    n_orders = len(PANEL_ORDERS)
    out: dict[str, list[int]] = {g: [] for g in sizes}
    chunks = max((-(-n // n_orders) for n in sizes.values()), default=0)
    for chunk in range(chunks):
        rows: list[tuple[int, ...]] = []
        for group in sorted(sizes):
            if sizes[group] <= chunk * n_orders:
                continue
            allowed = [
                p
                for p in _ORDER_PERMUTATIONS
                if all(p[i] != row[i] for row in rows for i in range(n_orders))
            ]
            if not allowed:  # pragma: no cover - impossible for fewer than 6 groups
                raise OrchestratorError(E_SCHEDULE, "too many profile groups")
            pick = allowed[int(rng.integers(len(allowed)))]
            rows.append(pick)
            out[group].extend(pick)
    return {g: orders[: sizes[g]] for g, orders in out.items()}


def panel_order_schedule(
    set_ns: str,
    panel_ids: Sequence[str],
    *,
    profiles: Mapping[str, Profile | str] | None = None,
) -> Mapping[str, int]:
    """Panel ID -> order index 1..6 (stored seed `seeds.panel_seed_key(set_ns, "orders")`).

    `profiles` maps every panel ID to its batch's profile (the batch table's `profile`
    column). The panels of each profile, in the given order, take one seeded permutation
    of the six orders per six panels, so a profile with six batches uses every order
    exactly once and 18 panels (3 profiles x 6) use each order exactly 3 times; panels at
    the same position within their profiles get different orders, so the three pilot
    panels (one per profile) use distinct orders. Without `profiles` every panel is in
    one group: each six consecutive panels use every order once (any 6k panels use each
    order k times), with no balance within a profile.
    `set_ns` must be a restricted namespace for pilot and confirmatory sets (the
    schedule would otherwise be computable from public code; `docs/orchestrator.md`).
    """
    ids = [check_id(p, "panel ID") for p in panel_ids]
    if len(set(ids)) != len(ids):
        raise OrchestratorError(E_SCHEDULE, "panel IDs must be distinct")
    if profiles is None:
        group_of = dict.fromkeys(ids, "")
    else:
        if set(profiles) != set(ids):
            raise OrchestratorError(E_SCHEDULE, "profiles must name exactly the panel IDs")
        try:
            group_of = {p: Profile(profiles[p]).value for p in ids}
        except ValueError as err:
            raise OrchestratorError(E_SCHEDULE, f"unknown profile: {err}") from err
    sizes = Counter(group_of.values())
    orders = _group_orders(set_ns, sizes)
    taken = dict.fromkeys(sizes, 0)
    out: dict[str, int] = {}
    for panel_id in ids:
        group = group_of[panel_id]
        out[panel_id] = orders[group][taken[group]]
        taken[group] += 1
    return out


def panel_aliases(set_ns: str, panel_id: str, book_ids: Sequence[str]) -> Mapping[str, str]:
    """Book ID -> distinct panel alias (`ids.PANEL_ALIAS_RE`) for one panel, drawn with
    `seeds.rng_for(seeds.panel_seed_key(set_ns, "aliases", panel_id))`; stored in
    `BatchConfig.panel.aliases`. Books are drawn in sorted order (the result does not
    depend on the order of `book_ids`); a repeated alias is drawn again."""
    rng = rng_for(panel_seed_key(set_ns, "aliases", check_id(panel_id, "panel ID")))
    alphabet = PANEL_ALIAS_ALPHABET
    out: dict[str, str] = {}
    for book_id in sorted(set(book_ids)):
        while True:
            chars = "".join(alphabet[int(i)] for i in rng.integers(0, len(alphabet), size=4))
            alias = f"PB-{chars}"
            if alias not in out.values():
                break
        out[book_id] = alias
    return out


PANEL_ORDER_COLUMNS: Final[tuple[str, ...]] = (
    "panel_id",
    "batch_id",
    "profile",
    "order_index",
    "order",
    "seed_key",
    "seed",
    "aliases_seed_key",
)
"""Columns of the panel order schedule CSV (restricted unless the set is DEMO): the
batch's profile (empty without `profiles`), the order seed (one per set) and the seed
key of each panel's book aliases (ID rotation)."""


def panel_order_rows(
    set_ns: str,
    panels: Sequence[tuple[str, str]],
    *,
    profiles: Mapping[str, Profile | str] | None = None,
) -> list[dict[str, str]]:
    """Rows of the panel order schedule for `(panel_id, batch_id)` pairs in batch order
    (`profiles`: panel ID -> profile, as in `panel_order_schedule`)."""
    schedule = panel_order_schedule(set_ns, [p for p, _ in panels], profiles=profiles)
    key = panel_seed_key(set_ns, "orders")
    seed = str(seed_from_key(key))
    return [
        {
            "panel_id": panel_id,
            "batch_id": check_id(batch_id, "batch ID"),
            "profile": "" if profiles is None else Profile(profiles[panel_id]).value,
            "order_index": str(schedule[panel_id]),
            "order": "|".join(PANEL_ORDERS[schedule[panel_id] - 1]),
            "seed_key": key,
            "seed": seed,
            "aliases_seed_key": panel_seed_key(set_ns, "aliases", panel_id),
        }
        for panel_id, batch_id in panels
    ]


def write_panel_order_csv(
    path: str | os.PathLike[str],
    set_ns: str,
    panels: Sequence[tuple[str, str]],
    *,
    profiles: Mapping[str, Profile | str] | None = None,
) -> str:
    """Write the schedule CSV (`PANEL_ORDER_COLUMNS`, `\\n` line ends); returns its SHA-256."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=PANEL_ORDER_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(panel_order_rows(set_ns, panels, profiles=profiles))
    data = buffer.getvalue().encode("utf-8")
    with open(path, "wb") as handle:
        handle.write(data)
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Batch definitions from the schedules files (#29 batch table, #31 book key)


@dataclass(frozen=True, slots=True)
class BatchDefinition:
    """One row of the schedules batch table: profile, stored atom order, permutation."""

    unit_id: str
    set: str
    profile: Profile
    designer: str | None
    atom_order: tuple[str, ...]
    labels: Mapping[str, str]
    """Atom ID -> semantic label."""


_TABLE_COLUMNS: Final = (
    "unit_id",
    "set",
    "profile",
    "designer",
    "K_action",
    "K_referent",
    "Q_action",
    "Q_referent",
    "atom_order",
)


def read_batch_table(path: str | os.PathLike[str]) -> tuple[BatchDefinition, ...]:
    """Parse a schedules batch table (`<set>-batch-table.csv`, #29).

    `K_action` etc. list the labels of matrix indices 1..4 (`|`-separated); `atom_order`
    is the stored generation order."""
    with open(path, encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = sorted(set(_TABLE_COLUMNS) - set(reader.fieldnames or ()))
        if missing:
            raise OrchestratorError(E_BATCH_TABLE, f"{path}: missing columns {missing}")
        rows = list(reader)
    out = []
    for row in rows:
        labels: dict[str, str] = {}
        for family in ("K", "Q"):
            for role in ("action", "referent"):
                values = row[f"{family}_{role}"].split("|")
                if len(values) != 4:
                    raise OrchestratorError(E_BATCH_TABLE, f"{row['unit_id']}: {family}_{role}")
                for i, label in enumerate(values, start=1):
                    labels[f"{family}-{role[0]}{i}"] = label
        out.append(
            BatchDefinition(
                unit_id=check_id(row["unit_id"], "unit ID"),
                set=row["set"],
                profile=Profile(row["profile"]),
                designer=row["designer"] or None,
                atom_order=tuple(row["atom_order"].split("|")),
                labels=dict(sorted(labels.items())),
            )
        )
    return tuple(out)


def check_permutation(definition: BatchDefinition, permutation: Mapping[str, Any]) -> None:
    """Refuse a batch-table row that disagrees with the unit's `permutation.json` (#29)."""
    order = tuple(permutation.get("atom_order", ()))
    labels = {
        a["atom_id"]: a["semantic_label"]
        for a in permutation.get("atoms", ())
        if isinstance(a, Mapping)
    }
    if order != definition.atom_order or labels != dict(definition.labels):
        raise OrchestratorError(
            E_BATCH_TABLE, f"{definition.unit_id}: batch table and permutation.json differ"
        )


def read_book_key(
    source: str | os.PathLike[str] | Mapping[str, Any], unit_id: str
) -> tuple[BookAssignment, ...]:
    """The three books of one unit from a schedules book key (`<set>-book-key.json`, #31;
    restricted unless DEMO)."""
    if isinstance(source, Mapping):
        data: Mapping[str, Any] = source
    else:
        with open(source, encoding="utf-8") as handle:
            data = json.load(handle)
    books = tuple(
        BookAssignment(b["book_id"], Method(b["method"]), b.get("designer"))
        for b in data.get("books", ())
        if b.get("unit_id") == unit_id
    )
    if sorted(b.method for b in books) != sorted(STUDY_A_METHODS):
        raise OrchestratorError(E_BATCH_TABLE, f"{unit_id}: the book key needs A1, A2 and A3")
    return books


def build_batch_config(
    definition: BatchDefinition,
    books: Sequence[BookAssignment],
    *,
    set_ns: str,
    panel_id: str,
    order_index: int,
    raters: Sequence[RaterSeat],
    threshold: str,
    fallback: FallbackSet,
    fallback_manifest_sha256: str,
    batch_id: str | None = None,
    seed_namespace: str | None = None,
    sources: ConfigSources | None = None,
) -> BatchConfig:
    """The batch config of one batch (checked with `BatchConfig.check_consistency`).

    - `order_index` comes from `panel_order_schedule`; `panel.order` lists the books in
      that method order; aliases are `panel_aliases(set_ns, panel_id, ...)`.
    - `batch_id` defaults to the unit ID (`DEMO-` IDs make a demo config);
      `seed_namespace` defaults to the batch ID.
    - A1's designer must equal the batch table's designer.
    """
    bid = batch_id if batch_id is not None else definition.unit_id
    if not 1 <= order_index <= len(PANEL_ORDERS):
        raise OrchestratorError(E_SCHEDULE, f"order index {order_index} is not 1..6")
    by_method = {b.method: b for b in books}
    a1 = by_method.get(Method.A1)
    if a1 is not None and definition.designer is not None and a1.designer_id != definition.designer:
        raise OrchestratorError(E_BATCH_TABLE, f"{bid}: A1 designer differs from the batch table")
    order = tuple(by_method[Method(m)].book_id for m in PANEL_ORDERS[order_index - 1])
    book_ids = [b.book_id for b in books]
    config = BatchConfig(
        batch_id=bid,
        set="demo" if is_demo(bid) else definition.set,  # type: ignore[arg-type]
        profile=definition.profile,
        atom_order=definition.atom_order,
        labels=dict(definition.labels),
        books=tuple(sorted(books, key=lambda b: b.method.value)),
        panel=PanelAssignment(
            panel_id=panel_id,
            order_index=order_index,
            order=(order[0], order[1], order[2]),
            aliases=dict(panel_aliases(set_ns, panel_id, book_ids)),
            raters=tuple(raters),
        ),
        seed_namespace=seed_namespace if seed_namespace is not None else bid,
        threshold=threshold,
        fallback_manifest_sha256=fallback_manifest_sha256,
        fallback_bank_hash=fallback.fallback_bank_hash,
        sources=sources if sources is not None else ConfigSources(),
    )
    return config.check().check_consistency()


def rebuild_batch_config(
    config: BatchConfig,
    *,
    set_ns: str,
    panel_id: str,
    raters: Sequence[RaterSeat],
    seed_namespace: str,
) -> BatchConfig:
    """The config of a rebuilt batch (Study A protocol §3.3): the same books, profile,
    atom order, permutation and presentation order, with a new independent panel, new
    per-panel aliases and a new seed namespace. Run it in a new run directory."""
    if panel_id == config.panel.panel_id or seed_namespace == config.seed_namespace:
        raise OrchestratorError(E_REBUILD, "a rebuild needs a new panel ID and seed namespace")
    if {s.rater_id for s in raters} & {s.rater_id for s in config.panel.raters}:
        raise OrchestratorError(E_REBUILD, "a rebuild needs an independent panel")
    panel = replace(
        config.panel,
        panel_id=panel_id,
        aliases=dict(panel_aliases(set_ns, panel_id, [b.book_id for b in config.books])),
        raters=tuple(raters),
    )
    rebuilt = replace(config, panel=panel, seed_namespace=seed_namespace)
    return rebuilt.check().check_consistency()


def substitute_book_id(book_id: str) -> str:
    """Store book ID of the fallback book that replaces `book_id` (`<book>-FB`)."""
    return check_book_id(f"{book_id}{SUBSTITUTE_SUFFIX}")


# ---------------------------------------------------------------------------
# Nearest reference (Study A protocol §3.3)


def nearest_committed(book: BookState, recipe: Recipe) -> CommittedAtom | None:
    """The committed atom of the same book closest to `recipe` by 12-feature distance;
    ties go to the lowest commit index; `None` when nothing is committed (silence)."""
    found = nearest_reference(features(recipe), book.references(), profile=book.profile)
    return None if found is None else book.committed[found.index]


# ---------------------------------------------------------------------------
# Operator console


@dataclass(frozen=True, slots=True)
class BookConsole:
    """One book as the operator console shows it: by panel alias only."""

    alias: str
    slots_used: int
    atoms_done: int
    """Atoms whose round-4 step (commit, bank fallback, substitution or archive) is done."""
    bank_fallbacks: int
    substituted: bool
    incumbent_score: str | None
    """Incumbent score of the atom in progress (after its last closed round)."""


@dataclass(frozen=True, slots=True)
class ConsoleView:
    """Operator console state: aliases only, never a book ID or method."""

    run_id: str
    batch_id: str
    panel_id: str
    state: str
    next_atom: str | None
    atoms_finished: int
    incomplete: bool
    books: tuple[BookConsole, ...]
    """In rating (block) order."""
    stations: tuple[tuple[str, bool], ...]


# ---------------------------------------------------------------------------
# The orchestrator


_SET_KINDS: Final[Mapping[str, tuple[RunKind, ...]]] = {
    "demo": (RunKind.DEMO, RunKind.SYNTHETIC),
    "pilot": (RunKind.PILOT,),
    "confirmatory": (RunKind.CONFIRMATORY,),
}


def check_batch_pins(
    config: BatchConfig,
    generation_config: GenerationConfig,
    fallback: FallbackSet,
    meanings: MeaningSet,
    *,
    kind: RunKind | str,
) -> None:
    """Refuse a batch whose inputs disagree (`Orchestrator` runs it before anything is
    written; the batch runner before it creates the run directory).

    The batch config, generation config, fallback set and meaning set must pin the same
    separation threshold, fallback hashes and meaning-set hash (`E_CONFIG`), and the
    batch's set must fit the run kind: demo -> demo/synthetic, pilot -> pilot,
    confirmatory -> confirmatory (`E_KIND`)."""
    problems = []
    if generation_config.separation_threshold != config.threshold:
        problems.append("generation config and batch config thresholds differ")
    if parse_threshold(config.threshold) != fallback.threshold:
        problems.append("the fallback set was built for another threshold")
    if config.fallback_bank_hash != fallback.fallback_bank_hash:
        problems.append("batch config fallback_bank_hash differs from the fallback set")
    if generation_config.fallback != fallback_pins(fallback):
        problems.append("generation config fallback pins differ from the fallback set")
    if meanings.sha256() != generation_config.meanings_sha256:
        problems.append("meaning set hash differs from the generation config")
    if problems:
        raise OrchestratorError(E_CONFIG, "; ".join(problems))
    run_kind = RunKind(kind)
    if run_kind not in _SET_KINDS[config.set]:
        raise OrchestratorError(E_KIND, f"a {config.set} batch cannot run as {run_kind}")


def _clock_kind(clock: Clock) -> tuple[str, float | None]:
    if isinstance(clock, ManualClock):
        return "manual", None
    if isinstance(clock, ScaledClock):
        return "scaled", clock.speed
    return "real", None


def _store_kind(book_id: str) -> str:
    return "synthetic" if is_demo(book_id) else "study"


class Orchestrator:
    """Runs one Study A batch (#20); see the module docstring.

    Keyword options beyond the contract: `purpose` (run-manifest purpose, `batch` or
    `dry_run`), `preload_lead_ms` and `slot_event_lead_ms` (panel event leads; a
    synthetic panel that drives a `ManualClock` uses `slot_event_lead_ms=0`).
    """

    def __init__(
        self,
        config: BatchConfig,
        layout: RunLayout,
        proposers: Mapping[Method, RoundProposer],
        store: VocabularyStore,
        fallback: FallbackSet,
        *,
        clock: Clock,
        generation_config: GenerationConfig,
        meanings: MeaningSet,
        kind: RunKind,
        freeze_manifest: Mapping[str, Any] | None = None,
        purpose: str = "batch",
        preload_lead_ms: int = PRELOAD_LEAD_MS,
        slot_event_lead_ms: int = SLOT_EVENT_LEAD_MS,
    ) -> None:
        self._config = config.check().check_consistency()
        self._layout = layout
        self._kind = RunKind(kind)
        self._clock = clock
        self._gen = generation_config
        self._meanings = meanings
        self._store = store
        self._fallback = fallback
        self._freeze = freeze_manifest
        self._purpose = purpose
        self._preload_lead_ms = max(0, int(preload_lead_ms))
        self._slot_event_lead_ms = max(0, int(slot_event_lead_ms))
        check_run_id(layout.run_id, self._kind)
        self._check_pins()
        check_run_config(generation_config, kind=self._kind, freeze_manifest=freeze_manifest)
        self._proposers = self._check_proposers(proposers)
        self._lock = threading.RLock()
        self._incomplete = False
        self._voided = False

        layout.logs_dir.mkdir(parents=True, exist_ok=True)
        self._write_documents()
        repaired = [repair_torn_tail(layout.log(name)) for name in LOG_FILES]
        self._timing_w = RecordWriter(layout.log("timing"))
        self._decisions_w = RecordWriter(layout.log("decision"))
        self._commits_w = RecordWriter(layout.log("commit"))
        self._scans_w = RecordWriter(layout.log("fallback_scan"))
        self._ratings_w = RecordWriter(layout.log("rating"))
        self._plays_w = RecordWriter(layout.log("play"))
        self._index = LogIndex.load(self._config, layout)
        for torn in repaired:
            if torn is not None:
                self._timing(
                    "log_repaired",
                    detail=f"{torn.name} offset={torn.offset} bytes={torn.n_bytes} "
                    f"sha256={torn.sha256}",
                )
        self._incomplete = self._index.incomplete()
        self._ensure_store_books()
        self._host = PanelHost(
            run_id=layout.run_id,
            config=self._config,
            clock=clock,
            ratings=self._ratings_w,
            plays=self._plays_w,
            timing=self._timing,
            on_withdrawal=self._on_withdrawal,
        )
        if not self._index.event_logged("run_start"):
            self._timing("run_start", detail=f"purpose={purpose} kind={self._kind.value}")

    # ------------------------------------------------------------------ checks

    def _check_pins(self) -> None:
        check_batch_pins(self._config, self._gen, self._fallback, self._meanings, kind=self._kind)

    @staticmethod
    def _check_proposers(proposers: Mapping[Method, RoundProposer]) -> dict[Method, RoundProposer]:
        out = {Method(k): v for k, v in proposers.items()}
        if sorted(out) != sorted(STUDY_A_METHODS):
            raise OrchestratorError(E_PROPOSERS, "proposers must be exactly A1, A2 and A3")
        for method, proposer in out.items():
            if Method(proposer.method) is not method:
                raise OrchestratorError(
                    E_PROPOSERS, f"the {method} proposer says {proposer.method}"
                )
        return out

    def _manifest(self) -> RunManifest:
        clock_kind, speed = _clock_kind(self._clock)
        code = current_code()
        freeze_sha = (
            None
            if self._freeze is None
            else hashlib.sha256(document_text(dict(self._freeze)).encode("utf-8")).hexdigest()
        )
        return RunManifest(
            run_id=self._layout.run_id,
            kind=self._kind,
            study=Study.A,
            purpose=self._purpose,  # type: ignore[arg-type]
            clock=clock_kind,  # type: ignore[arg-type]
            created_utc=utc_text(self._clock.utc_now()),
            code=RunCode(
                av_generation=__version__,
                renderer_version=code.renderer_version,
                renderer_hash=code.renderer_hash,
                validator_version=code.validator_version,
                validator_hash=code.validator_hash,
            ),
            threshold=self._config.threshold,
            clock_speed=speed,
            config_sha256=self._config.sha256(),
            seed_namespace=self._config.seed_namespace,
            books=tuple(RunBook(b.book_id, b.method, b.designer_id) for b in self._config.books),
            llm_manifest_sha256=self._gen.llm_manifest_sha256,
            generation_config_sha256=self._gen.frozen_sha256(),
            meanings_sha256=self._meanings.sha256(),
            freeze_manifest_sha256=freeze_sha,
        )

    def _write_documents(self) -> None:
        layout = self._layout
        if layout.config.exists():
            if BatchConfig.read(layout.config).sha256() != self._config.sha256():
                raise OrchestratorError(E_RUN_MISMATCH, "config.json differs from this batch")
        else:
            self._config.write(layout.config)
        if layout.generation_config.exists():
            stored = GenerationConfig.read(layout.generation_config)
            if stored.frozen_sha256() != self._gen.frozen_sha256():
                raise OrchestratorError(E_RUN_MISMATCH, "generation-config.json differs")
        else:
            self._gen.write(layout.generation_config)
        if layout.manifest.exists():
            stored_m = RunManifest.read(layout.manifest)
            mine = self._manifest()
            for name in ("run_id", "kind", "config_sha256", "generation_config_sha256"):
                if getattr(stored_m, name) != getattr(mine, name):
                    raise OrchestratorError(E_RUN_MISMATCH, f"run manifest {name} differs")
        else:
            self._manifest().write(layout.manifest)

    def _ensure_store_books(self) -> None:
        existing = set(self._store.books())
        for book in self._config.books:
            if book.book_id in existing:
                info = self._store.book(book.book_id)
                if info.profile is not self._config.profile or info.threshold != parse_threshold(
                    self._config.threshold
                ):
                    raise OrchestratorError(E_STORE, f"store book {book.book_id} differs")
                continue
            self._store.create_book(
                book.book_id,
                self._config.profile,
                kind=_store_kind(book.book_id),
                threshold=self._config.threshold,
            )

    # ------------------------------------------------------------------ logging

    def _timing(self, event: str, **fields: Any) -> None:  # noqa: ANN401
        component = fields.pop("component", "orchestrator")
        record = TimingEvent(
            run_id=self._layout.run_id,
            event=event,
            t_ms=self._clock.now_ms(),
            wall_utc=utc_text(self._clock.utc_now()),
            batch_id=self._config.batch_id,
            component=component,
            **fields,
        )
        self._timing_w.append(record)
        with self._lock:
            self._index.add(record)

    def _now(self) -> int:
        return self._clock.now_ms()

    def _sleep_until(self, t_ms: int) -> None:
        while True:
            delta = t_ms - self._clock.now_ms()
            if delta <= 0:
                return
            self._clock.sleep(delta / 1000)

    # ------------------------------------------------------------------ public API

    @property
    def config(self) -> BatchConfig:
        return self._config

    @property
    def layout(self) -> RunLayout:
        return self._layout

    @property
    def incomplete(self) -> bool:
        return self._incomplete

    def next_atom(self) -> str | None:
        """The next atom to run (`None` when the batch is finished)."""
        with self._lock:
            return self._index.next_atom()

    def panel_host(self) -> PanelSessionHost:
        """The session API for the panel server (`panel.create_panel_app`, #21)."""
        return self._host

    def book_state(self, book_id: str, atom_id: str) -> BookState:
        """A book's state when `atom_id` starts (restricted; for tests and the operator)."""
        with self._lock:
            return self._index.book_state(book_id, atom_id)

    def run_batch(self) -> None:
        """Run every remaining appointment (1..4) of the batch."""
        for appointment in range(1, APPOINTMENTS_PER_BATCH + 1):
            self.run_appointment(appointment)

    def run_appointment(self, appointment: int) -> None:
        """Run the 4 atoms of appointment 1..4 (finished atoms are skipped)."""
        if not 1 <= appointment <= APPOINTMENTS_PER_BATCH:
            raise OrchestratorError(E_APPOINTMENT, f"appointment {appointment} is not 1..4")
        self._check_incomplete()
        first = (appointment - 1) * ATOMS_PER_APPOINTMENT
        atoms = self._config.atom_order[first : first + ATOMS_PER_APPOINTMENT]
        earlier = self._config.atom_order[:first]
        if not all(self._index.atom_finished(a) for a in earlier):
            raise OrchestratorError(E_ATOM_ORDER, f"appointment {appointment}: earlier atoms open")
        if all(self._index.atom_finished(a) for a in atoms):
            self._close_if_finished()
            return
        self._timing("appointment_start", appointment=appointment)
        for atom in atoms:
            if not self._index.atom_finished(atom):
                self.run_atom(atom)
        self._timing("appointment_end", appointment=appointment)
        if self._index.next_atom() is None:
            self._host.publish("end", "batch_complete")
            self._close_if_finished()
        else:
            self._host.publish("end", "appointment_complete")

    def run_atom(self, atom_id: str) -> None:
        """Run 4 rounds for one atom in every book and commit (or fall back)."""
        self._check_incomplete()
        expected = self._index.next_atom()
        if atom_id != expected:
            code = E_ATOM_DONE if self._index.atom_finished(atom_id) else E_ATOM_ORDER
            raise OrchestratorError(code, f"next atom is {expected}, not {atom_id}")
        position = self._config.atom_order.index(atom_id)
        appointment = position // ATOMS_PER_APPOINTMENT + 1
        resumed = self._index.touched(atom_id)
        if self._host.state in ("paused", "between_atoms", "ended"):
            self._host.publish("resume")
        self._timing(
            "atom_start",
            atom_id=atom_id,
            appointment=appointment,
            detail="resumed" if resumed else None,
        )
        states = {b.book_id: self._index.book_state(b.book_id, atom_id) for b in self._config.books}
        for round_ in range(1, ROUNDS_PER_ATOM + 1):
            self._run_round(atom_id, round_, states, appointment)
        self._finish_atom(atom_id, appointment)
        self._timing("atom_end", atom_id=atom_id, appointment=appointment)
        last_of_appointment = (position + 1) % ATOMS_PER_APPOINTMENT == 0
        if not last_of_appointment:
            self._host.publish("pause", "between_atoms")

    def resume(self) -> str | None:
        """Repair torn log tails, rebuild state from the run's logs and finish an atom
        that was interrupted; returns the next atom to run (`None`: batch finished).
        Later atoms run with `run_appointment` at the next appointment."""
        for name in LOG_FILES:
            torn = repair_torn_tail(self._layout.log(name))
            if torn is not None:
                self._timing(
                    "log_repaired",
                    detail=f"{torn.name} offset={torn.offset} bytes={torn.n_bytes} "
                    f"sha256={torn.sha256}",
                )
        with self._lock:
            self._index = LogIndex.load(self._config, self._layout)
            self._incomplete = self._index.incomplete()
        nxt = self._index.next_atom()
        self._timing("resume", atom_id=nxt, detail="orchestrator resume from logs")
        self._check_incomplete()
        if nxt is not None and self._index.touched(nxt):
            self.run_atom(nxt)
        self._close_if_finished()
        return self._index.next_atom()

    def mark_incomplete(self, reason: str) -> None:
        """Mark the batch incomplete outside a running atom (e.g. a rater withdraws
        between appointments) and void its store books for the rebuild."""
        with self._lock:
            first = not self._incomplete
            self._incomplete = True
        if first:
            self._timing("batch_incomplete", detail=printable(reason))
            self._host.publish("end", "aborted")
        self._void_for_rebuild()

    def console(self) -> ConsoleView:
        """The operator console: panel aliases only (never book IDs or methods).

        Thread-safe: an operator UI may poll it while the batch runs (it reads the index
        under the lock the orchestrator holds while adding records)."""
        with self._lock:
            return self._console()

    def _console(self) -> ConsoleView:
        index, config = self._index, self._config
        nxt = index.next_atom()
        books = []
        for book_id in config.panel.order:
            done = sum(1 for a in config.atom_order if index.book_atom_finished(book_id, a))
            score = None
            if nxt is not None:
                closed = [r for r in range(1, 5) if (book_id, nxt, r) in index.decisions]
                if closed:
                    score = index.decisions[(book_id, nxt, max(closed))].incumbent_score
            books.append(
                BookConsole(
                    alias=config.panel.aliases[book_id],
                    slots_used=sum(1 for s in index.slots.values() if s.book_id == book_id),
                    atoms_done=done,
                    bank_fallbacks=len(index.used_bank_indices(book_id)),
                    substituted=index.substituted_at(book_id) is not None,
                    incumbent_score=score,
                )
            )
        return ConsoleView(
            run_id=self._layout.run_id,
            batch_id=config.batch_id,
            panel_id=config.panel.panel_id,
            state=self._host.state,
            next_atom=nxt,
            atoms_finished=sum(1 for a in config.atom_order if index.atom_finished(a)),
            incomplete=self._incomplete,
            books=tuple(books),
            stations=self._host.connected(),
        )

    # ------------------------------------------------------------------ incomplete batch

    def _on_withdrawal(self, rater_id: str, station: str, reason: str) -> None:
        with self._lock:
            first = not self._incomplete
            self._incomplete = True
        if first:
            self._timing(
                "batch_incomplete",
                station=station,
                actor_id=rater_id,
                detail=printable(f"rater withdrawal: {reason}"),
            )

    def _check_incomplete(self) -> None:
        if self._incomplete:
            self._void_for_rebuild()
            raise BatchIncomplete(
                f"batch {self._config.batch_id} is incomplete; rebuild it with a new panel "
                "and seed namespace (rebuild_batch_config)"
            )

    def _void_for_rebuild(self) -> None:
        with self._lock:
            if self._voided:
                return
            self._voided = True
        existing = set(self._store.books())
        reason = (
            "batch incomplete (rater withdrawal or operator stop); rebuilt with a new panel "
            "and seed namespace (Study A protocol 3.3)"
        )
        for book in self._config.books:
            for store_id in (book.book_id, f"{book.book_id}{SUBSTITUTE_SUFFIX}"):
                if store_id in existing and not self._store.book(store_id).void:
                    self._store.void(store_id, cause="batch_rebuild", reason=reason)

    # ------------------------------------------------------------------ one round

    def _run_round(
        self, atom: str, round_: int, states: Mapping[str, BookState], appointment: int
    ) -> None:
        index = self._index
        if index.round_closed(atom, round_):
            return
        self._timing("round_start", atom_id=atom, round=round_, appointment=appointment)
        counts = {
            b.book_id: len(index.round_slots(b.book_id, atom, round_)) for b in self._config.books
        }
        partial = sorted(b for b, n in counts.items() if 0 < n < SLOTS_PER_ROUND)
        if partial:
            raise OrchestratorError(
                E_RESUME_PARTIAL_WINDOW,
                f"{atom} round {round_}: an interrupted proposal window left 1-2 slots of "
                f"{len(partial)} book(s); record a deviation (docs/orchestrator.md)",
            )
        ask = [b for b in self._config.books if counts[b.book_id] == 0]
        if ask:
            self._proposal_window(atom, round_, ask, states, appointment)
        self._check_incomplete()
        self._rating_window(atom, round_, states, appointment)
        self._decide(atom, round_, states)
        self._timing("round_end", atom_id=atom, round=round_, appointment=appointment)

    def _request(
        self, book: BookAssignment, atom: str, round_: int, state: BookState, window_end: int
    ) -> RoundRequest:
        seat_order = [s.rater_id for s in self._config.panel.raters]
        feedback = self._index.feedback(book.book_id, atom, round_ - 1, seat_order=seat_order)
        blind = book.method is Method.A2
        return RoundRequest(
            run_id=self._layout.run_id,
            batch_id=self._config.batch_id,
            book_id=book.book_id,
            method=book.method,
            atom_id=atom,
            round=round_,
            profile=self._config.profile,
            seed_namespace=self._config.seed_namespace,
            book=state.without_labels() if blind else state,
            feedback=feedback,
            window_end_ms=window_end,
            semantic_label=None if blind else self._config.labels[atom],
        )

    def _proposal_window(
        self,
        atom: str,
        round_: int,
        books: Sequence[BookAssignment],
        states: Mapping[str, BookState],
        appointment: int,
    ) -> None:
        start = self._now()
        end = start + PROPOSAL_WINDOW_MS
        requests = {
            b.book_id: self._request(b, atom, round_, states[b.book_id], end) for b in books
        }
        if round_ > 1:
            for book in books:
                self._timing(
                    "feedback_sent",
                    atom_id=atom,
                    round=round_,
                    book_id=book.book_id,
                    detail=f"rounds_closed={round_ - 1}",
                )
        self._timing("proposal_window_start", atom_id=atom, round=round_, appointment=appointment)
        futures: dict[str, Future[RoundResult]] = {}
        with ThreadPoolExecutor(max_workers=len(books), thread_name_prefix="proposal") as pool:
            for book in books:
                proposer = self._proposers[book.method]
                futures[book.book_id] = pool.submit(proposer.propose_round, requests[book.book_id])
        failures: list[BaseException] = []
        for book in books:
            try:
                result = futures[book.book_id].result()
            except Exception as err:  # noqa: BLE001 - re-raised below after logging
                failures.append(err)
                continue
            self._check_result(book, requests[book.book_id], result)
            with self._lock:
                self._index.add_all(result.records)
        finished = self._now()
        notes = []
        overrun = finished - (end + WINDOW_GRACE_MS)
        if overrun > 0:
            notes.append(f"window_overrun_ms={overrun}")
        long_slots = sum(
            1
            for book in books
            for s in self._index.round_slots(book.book_id, atom, round_)
            if s.t_ms - s.t_open_ms > SLOT_CAP_MS + WINDOW_GRACE_MS
        )
        if long_slots:
            notes.append(f"slots_over_cap={long_slots}")
        self._timing(
            "proposal_window_end",
            atom_id=atom,
            round=round_,
            appointment=appointment,
            duration_ms=finished - start,
            detail=" ".join(notes) or None,
        )
        if failures:
            raise OrchestratorError(
                E_PROPOSER_FAILED, f"{len(failures)} proposer(s) failed: {failures[0]!r}"
            ) from failures[0]

    def _check_result(
        self, book: BookAssignment, request: RoundRequest, result: RoundResult
    ) -> None:
        problems = []
        if result.method is not book.method or result.book_id != book.book_id:
            problems.append("method or book differs")
        if result.atom_id != request.atom_id or result.round != request.round:
            problems.append("atom or round differs")
        if len(result.records) != SLOTS_PER_ROUND:
            problems.append(f"{len(result.records)} records instead of {SLOTS_PER_ROUND}")
        for i, record in enumerate(result.records, start=1):
            expected = proposal_slot_id(book.book_id, request.atom_id, request.round, i)
            if (
                record.slot_id != expected
                or record.slot != i
                or record.slot_index != slot_index(request.round, i)
                or record.method is not book.method
                or record.book_id != book.book_id
                or record.batch_id != self._config.batch_id
                or record.run_id != self._layout.run_id
                or record.round != request.round
                or record.atom_id != request.atom_id
                or record.profile is not self._config.profile
                or record.study is not Study.A
                or record.practice
            ):
                problems.append(f"record {i} ({record.slot_id}) does not match its slot")
        if problems:
            raise OrchestratorError(E_PROPOSER_RESULT, "; ".join(problems))

    # ------------------------------------------------------------------ rating window

    def _asset(self, recipe: Recipe, pcm_sha256: str) -> tuple[AssetRef, bytes]:
        rendered = render(recipe, self._config.profile)
        if rendered.pcm_sha256 != pcm_sha256:
            raise OrchestratorError(E_PROPOSER_RESULT, "a recipe renders to another waveform")
        data = wav_bytes(rendered)
        ref = AssetRef(hashlib.sha256(data).hexdigest(), pcm_sha256, rendered.n_samples, len(data))
        return ref, data

    def _plan_round(
        self, atom: str, round_: int, states: Mapping[str, BookState]
    ) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
        """Per position 1..9: everything of the slot except its start time."""
        config, meanings = self._config, self._meanings
        assets: dict[str, bytes] = {}
        protos: list[dict[str, Any]] = []
        for block, book_id in enumerate(config.panel.order):
            state = states[book_id]
            first_atom = not state.committed
            for slot in self._index.round_slots(book_id, atom, round_):
                proto: dict[str, Any] = {
                    "position": block * SLOTS_PER_ROUND + slot.slot,
                    "book_id": book_id,
                    "slot_id": slot.slot_id,
                    "first_atom": first_atom,
                    "placeholder": True,
                    "meaning": None,
                    "candidate": None,
                    "reference": None,
                    "reference_meaning": None,
                    "unlock_offset_ms": REFERENCE_ONSET_MS,
                }
                recipe = recipe_or_none(slot.recipe)
                if is_valid_candidate(slot) and recipe is not None and slot.pcm_sha256:
                    cand, data = self._asset(recipe, slot.pcm_sha256)
                    assets[cand.asset_id] = data
                    proto.update(
                        placeholder=False,
                        meaning=meanings.for_atom(atom, config.labels),
                        candidate=cand,
                    )
                    nearest = nearest_committed(state, recipe)
                    if nearest is not None:
                        ref, ref_data = self._asset(nearest.recipe, nearest.pcm_sha256)
                        assets[ref.asset_id] = ref_data
                        duration = -(-ref.n_samples // SAMPLES_PER_MS)
                        proto.update(
                            reference=ref,
                            reference_meaning=meanings.text(config.labels[nearest.atom_id]),
                            unlock_offset_ms=REFERENCE_ONSET_MS + duration,
                        )
                protos.append(proto)
        protos.sort(key=lambda p: p["position"])
        return protos, assets

    def _rating_window(
        self, atom: str, round_: int, states: Mapping[str, BookState], appointment: int
    ) -> None:
        config, index = self._config, self._index
        protos, assets = self._plan_round(atom, round_, states)
        seats = {s.rater_id for s in config.panel.raters}
        todo = []
        for proto in protos:
            rsid = rating_slot_id(config.batch_id, atom, round_, proto["position"])
            have = index.seat_records(rsid)
            if set(have) >= seats:
                continue
            todo.append((proto, rsid, have))
        if not todo:
            return
        self._timing(
            "rating_window_start",
            atom_id=atom,
            round=round_,
            appointment=appointment,
            detail="resumed" if len(todo) < len(protos) else None,
        )
        t_first = self._now() + self._preload_lead_ms
        plans: list[tuple[RatingSlotPlan, dict[str, Any]]] = []
        for k, (proto, rsid, have) in enumerate(todo):
            panel = PanelSlot(
                rating_slot_id=rsid,
                position=proto["position"],
                start_ms=t_first + k * RATING_SLOT_MS,
                placeholder=proto["placeholder"],
                first_atom=proto["first_atom"],
                meaning=proto["meaning"],
                candidate=proto["candidate"],
                reference=proto["reference"],
                reference_meaning=proto["reference_meaning"],
                unlock_offset_ms=proto["unlock_offset_ms"],
            )
            plan = RatingSlotPlan(
                panel=panel,
                batch_id=config.batch_id,
                book_id=proto["book_id"],
                alias=config.panel.aliases[proto["book_id"]],
                slot_id=proto["slot_id"],
                atom_id=atom,
                round=round_,
            )
            plans.append((plan, have))
        partial = [(p, h) for p, h in plans if h]
        if partial:
            # A position recorded for some seats only (crash while writing at lock):
            # complete it with `missing` records; nothing is replayed.
            for plan, have in partial:
                with self._lock:
                    index.add_all(self._host.close_slot(plan, existing=have))
            plans = [(p, h) for p, h in plans if not h]
        if plans:
            run = [p for p, _ in plans]
            self._host.schedule(run)
            refs: dict[str, AssetRef] = {}
            for plan in run:
                for ref in (plan.panel.candidate, plan.panel.reference):
                    if ref is not None:
                        refs[ref.asset_id] = ref
            self._host.publish_preload(assets, sorted(refs.values(), key=lambda r: r.asset_id))
            self._host.publish_slot(run[0])
            lead = self._slot_event_lead_ms
            for k, plan in enumerate(run):
                nxt = run[k + 1] if k + 1 < len(run) else None
                if nxt is not None and lead > 0:
                    self._sleep_until(plan.lock_ms - lead)
                    # After a withdrawal the slot in progress still runs to its lock (its
                    # records are kept), but the next one is never announced; the host
                    # also refuses it once the session has ended.
                    if not self._incomplete:
                        self._host.publish_slot(nxt)
                self._sleep_until(plan.lock_ms)
                records = self._host.close_slot(plan)
                with self._lock:
                    index.add_all(records)
                self._check_incomplete()
                if nxt is not None and lead == 0:
                    self._host.publish_slot(nxt)
        self._timing("rating_window_end", atom_id=atom, round=round_, appointment=appointment)

    # ------------------------------------------------------------------ selector

    def _decide(self, atom: str, round_: int, states: Mapping[str, BookState]) -> None:
        index = self._index
        for book in self._config.books:
            book_id = book.book_id
            if (book_id, atom, round_) in index.decisions:
                continue
            first_atom = not states[book_id].committed
            inc_id, inc_score, candidates = index.incumbent(
                book_id, atom, round_, first_atom=first_atom
            )
            previous = index.decisions.get((book_id, atom, round_ - 1))
            prev_id = previous.incumbent_slot_id if previous is not None else None
            substituted = index.substituted_before(book_id, atom)
            final = round_ == ROUNDS_PER_ATOM
            if not final:
                action = "continue"
            elif substituted:
                action = "archive" if inc_id is not None else "archive_none"
            else:
                action = "commit" if inc_id is not None else "fallback_scan"
            record = DecisionRecord(
                run_id=self._layout.run_id,
                batch_id=self._config.batch_id,
                book_id=book_id,
                atom_id=atom,
                round=round_,
                first_atom=first_atom,
                candidates=tuple(candidates),
                incumbent_slot_id=inc_id,
                incumbent_score=None if inc_score is None else str(inc_score),
                incumbent_changed=inc_id != prev_id,
                final=final,
                action=action,  # type: ignore[arg-type]
                book_substituted=substituted,
                t_ms=self._now(),
            )
            self._decisions_w.append(record)
            with self._lock:
                index.add(record)

    # ------------------------------------------------------------------ commit and fallback

    def _commit(
        self,
        book_id: str,
        store_book_id: str,
        atom: str,
        recipe: Recipe,
        *,
        source: str,
        store_source: str,
        pcm_sha256: str,
        slot_id: str | None = None,
        bank_index: int | None = None,
        failed_generation: bool = False,
    ) -> CommitRecord:
        label = self._config.labels[atom]
        try:
            entry, head = self._store.commit(
                store_book_id,
                atom,
                label,
                recipe,
                source=store_source,
                profile=self._config.profile,
                pcm_sha256=pcm_sha256,
                expected_head=self._index.last_head(book_id, store_book_id),
            )
        except (CommitRejected, StoreError) as err:
            raise OrchestratorError(E_STORE, f"commit of {atom} to {store_book_id}: {err}") from err
        record = CommitRecord(
            run_id=self._layout.run_id,
            batch_id=self._config.batch_id,
            book_id=book_id,
            store_book_id=store_book_id,
            atom_id=atom,
            semantic_label=label,
            source=source,  # type: ignore[arg-type]
            store_source=store_source,
            recipe=entry.recipe.to_dict(),
            recipe_sha256=entry.recipe.sha256(),
            pcm_sha256=entry.pcm_sha256,
            file_sha256=entry.file_sha256,
            chain_head=head,
            failed_generation=failed_generation,
            t_ms=self._now(),
            slot_id=slot_id,
            bank_index=bank_index,
        )
        self._commits_w.append(record)
        with self._lock:
            self._index.add(record)
        return record

    def _finish_atom(self, atom: str, appointment: int) -> None:
        for book in self._config.books:
            if not self._index.book_atom_finished(book.book_id, atom):
                self._finish_book_atom(book.book_id, atom, appointment)

    def _finish_book_atom(self, book_id: str, atom: str, appointment: int) -> None:
        index = self._index
        final = index.decisions[(book_id, atom, ROUNDS_PER_ATOM)]
        if final.action == "commit":
            assert final.incumbent_slot_id is not None
            slot = index.slots[final.incumbent_slot_id]
            recipe = recipe_or_none(slot.recipe)
            assert recipe is not None and slot.pcm_sha256 is not None
            self._commit(
                book_id,
                book_id,
                atom,
                recipe,
                source="selector",
                store_source=slot.slot_id,
                pcm_sha256=slot.pcm_sha256,
                slot_id=slot.slot_id,
            )
            return
        if final.action != "fallback_scan":
            return
        scan = index.scans.get((book_id, atom))
        if scan is None:
            result = scan_fallback(
                self._fallback.bank(self._config.profile),
                self._store.list(book_id),
                used=index.used_bank_indices(book_id),
                threshold=self._config.threshold,
            )
            scan = FallbackScanRecord(
                run_id=self._layout.run_id,
                batch_id=self._config.batch_id,
                book_id=book_id,
                atom_id=atom,
                scan=result.to_dict(),
                t_ms=self._now(),
            )
            self._scans_w.append(scan)
            with self._lock:
                index.add(scan)
        selected = scan.scan.get("selected_index")
        if isinstance(selected, int):
            entry = self._fallback.bank(self._config.profile)[selected]
            self._commit(
                book_id,
                book_id,
                atom,
                entry.recipe,
                source="fallback_bank",
                store_source=entry.source,
                pcm_sha256=entry.pcm_sha256,
                bank_index=entry.index,
            )
            return
        self._substitute(book_id, atom, appointment)

    def _substitute(self, book_id: str, atom: str, appointment: int) -> None:
        """Whole-book substitution (Study A protocol §3.7; architecture §3.2)."""
        new_id = substitute_book_id(book_id)
        existing = set(self._store.books())
        if not self._store.book(book_id).void:
            self._store.void(
                book_id,
                cause="failed_generation",
                reason=f"whole-book fallback substitution at {atom}: no bank recipe passed "
                "(Study A protocol 3.7)",
                superseded_by=new_id,
            )
        if new_id not in existing:
            self._store.create_book(
                new_id,
                self._config.profile,
                kind=_store_kind(new_id),
                threshold=self._config.threshold,
            )
        done = self._index.commits_for(book_id, new_id)
        for item in self._fallback.book(self._config.profile).atoms:
            if item.atom_id in done:
                continue
            self._commit(
                book_id,
                new_id,
                item.atom_id,
                item.recipe,
                source="fallback_book",
                store_source=item.source,
                pcm_sha256=item.pcm_sha256,
                failed_generation=True,
            )
        self._timing(
            "book_substituted",
            atom_id=atom,
            book_id=book_id,
            appointment=appointment,
            detail=f"store_book_id={new_id}",
        )

    # ------------------------------------------------------------------ closing

    def _close_if_finished(self) -> None:
        """Close the run once every atom is finished (idempotent)."""
        if self._index.next_atom() is not None:
            return
        if RunManifest.read(self._layout.manifest).closed_utc is not None:
            return
        self._close_run()

    def _close_run(self) -> None:
        self._timing("run_end")
        root = self._layout.root
        files = {
            rel: file_sha256(root / rel) for rel in relative_files(root) if rel != MANIFEST_NAME
        }
        manifest = replace(
            RunManifest.read(self._layout.manifest),
            closed_utc=utc_text(self._clock.utc_now()),
            files=files,
        )
        manifest.write(self._layout.manifest, exclusive=False)
