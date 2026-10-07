"""The Study B bank builder (#26; Study B protocol §4).

For one dyad bank, an attempt traverses the 16 atoms in the stored order
(`permutation.json` `atom_order`) under each profile P1, P2, P3. Each cell (profile,
atom) gets at most 12 slots. A slot:

1. is reserved in the attempt's ledger (`SlotLedger.reserve`, cap key
   `B|<bank>|<attempt>|<profile>|<atom>`) before any work: a 13th slot of a cell raises
   `SlotCapExceeded` and is logged as a `slot_refusal`;
2. gets one proposal (`proposer.SlotProposer`; the model under the B seed key
   `seeds.b_seed_key(seed_namespace, attempt, profile, atom, slot)`) from a `BCellState`
   holding the atom's meaning label, the options retained so far under the profile and
   the cell's validation history, and nothing else;
3. is validated with `av_sound.validate` against the retained options of the *other*
   atoms under the profile (syntax, limits, rendering, clipping, reserved signals,
   duplicates and the frozen separation rule; threshold from the generation config) and
   against the cell's retained waveforms (`outcomes.outcome_from_validation(...,
   mode="B", cell_duplicate=)`): `valid`, a technical code, `duplicate` or `incompatible`;
4. is consumed with exactly one `SlotRecord`, whatever the outcome.

The first 4 `valid` slots of a cell are its options in order (ranks 1-3 the displayed
menu, rank 4 the reserve); the cell stops at 4. A cell that uses 12 slots without 4
options fails the attempt: it is archived (its directory stays with its slots, reasons
and timing) and the next, independently seeded attempt starts from nothing. The first
complete attempt is used; after 4 failed attempts the bank is `unavailable`. A 5th
attempt raises `AttemptCapExceeded`. Option WAVs are written only for the attempt used.

Parallel execution: profiles are independent (compatibility is checked within a profile
only), so `workers` (1-3) runs one stream per profile in threads. A complete attempt is
identical whatever the interleaving; when a profile fails, the other streams stop before
their next slot, so how many slots a failed attempt used may depend on timing with
`workers > 1` (with `workers=1` everything is deterministic). Several banks run in
parallel with `av_banks.run.build_banks(..., parallel_banks=)` or as separate processes.

The builder never composes or renders a complete message: it renders single atoms only
(through `validate`), and it never reads ratings, participant data or test data.
"""

from __future__ import annotations

import re
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal, Protocol

from av_generation.clock import Clock, utc_text
from av_generation.constants import (
    B_MAX_ATTEMPTS,
    B_OPTIONS_PER_CELL,
    B_SLOTS_PER_ATTEMPT,
    B_SLOTS_PER_CELL,
    PROFILES,
)
from av_generation.genconfig import GenerationConfig, check_run_config
from av_generation.ids import (
    PUBLIC_RUN_KINDS,
    Method,
    RunKind,
    Study,
    bank_set,
    bank_slot_id,
    check_id,
)
from av_generation.jsonio import file_sha256
from av_generation.ledger import AttemptCapExceeded, SlotCapExceeded, SlotLedger, SlotTicket
from av_generation.outcomes import SlotOutcome, outcome_from_validation
from av_generation.proposers import BCellState, RetainedOption
from av_generation.records import RecordWriter, SlotRecord, SlotRefusal, TimingEvent, cap_key
from av_generation.rundir import check_run_location
from av_generation.seeds import PART_RE, b_seed_key
from av_sound.features import parse_threshold
from av_sound.recipe import Profile
from av_sound.reserved import ReservedRegistry, load_reserved_registry
from av_sound.validate import Reference, ValidationResult, validate
from av_sound.wav import file_sha256 as wav_file_sha256
from av_sound.wav import write_wav

from av_banks.layout import BankLayout, option_wav
from av_banks.manifest import (
    AttemptSummary,
    BankManifest,
    CellRef,
    CellSummary,
    ProfileSummary,
    manifest_from_files,
)
from av_banks.permutation import UnitPermutation, check_bank_unit
from av_banks.proposer import SlotProposer
from av_banks.throughput import throughput

FIRST_VERSION: Final = "1.0.0"
VERSION_RE: Final = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
RUN_KIND_OF_SET: Final[dict[str, RunKind]] = {
    "demo": RunKind.DEMO,
    "pilot": RunKind.PILOT,
    "confirmatory": RunKind.CONFIRMATORY,
}
COMPONENT: Final = "bank"
BANK_REFUSALS_NAME: Final = "slot-refusals.jsonl"
"""Bank-level refusals (a 5th attempt), beside the per-attempt refusal logs."""

E_EXISTS: Final = "E_EXISTS"
E_ORDER: Final = "E_ORDER"
E_SPEC: Final = "E_SPEC"
E_INTERNAL: Final = "E_INTERNAL"


class BankBuildError(RuntimeError):
    """The builder refused or could not finish; `.code` names the rule."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class LedgerLike(Protocol):
    """The part of `av_generation.ledger.SlotLedger` (#17) the builder uses."""

    def reserve(self, cap_key: str, slot_id: str, *, study: Study, method: Method) -> SlotTicket:
        """Open one slot under the cap (refusals raise and are logged)."""
        ...

    def consume(self, record: SlotRecord) -> SlotRecord:
        """Close an open ticket with its record and append it."""
        ...


class LedgerFactory(Protocol):
    def __call__(
        self,
        path: Path,
        *,
        run_id: str,
        clock: Clock,
        refusals: RecordWriter | None,
        timing: RecordWriter | None,
        cap: int,
    ) -> LedgerLike:
        """A ledger for one attempt's `slots.jsonl`."""
        ...


def slot_ledger(
    path: Path,
    *,
    run_id: str,
    clock: Clock,
    refusals: RecordWriter | None,
    timing: RecordWriter | None,
    cap: int,
) -> LedgerLike:
    """The default ledger: #17's `SlotLedger`, one per attempt."""
    return SlotLedger(path, run_id=run_id, clock=clock, refusals=refusals, timing=timing, cap=cap)


# ---------------------------------------------------------------------------
# What is built


@dataclass(frozen=True, slots=True)
class BankSpec:
    """One bank to build: its ID, version, seed namespace and unit permutation."""

    bank_id: str
    bank_version: str
    seed_namespace: str
    permutation: UnitPermutation

    @property
    def set(self) -> str:
        return bank_set(self.bank_id)

    @property
    def labels(self) -> dict[str, str]:
        return dict(self.permutation.labels)

    @property
    def atom_order(self) -> tuple[str, ...]:
        return self.permutation.atom_order


def default_seed_namespace(bank_id: str, bank_version: str) -> str:
    """The bank ID for the first version (`1.0.0`), else `<bank_id>-v<version>`: a
    rebuild under a new bank version never repeats the seeds of an earlier build."""
    return bank_id if bank_version == FIRST_VERSION else f"{bank_id}-v{bank_version}"


def seed_namespace_error(bank_id: str, bank_version: str, namespace: str) -> str | None:
    """Why `namespace` cannot be the seed namespace of version `bank_version` of bank
    `bank_id`, or `None` when it can.

    A namespace is the default (`default_seed_namespace`), `<bank_id>-v<version>` or
    `<bank_id>-v<version>-<suffix>`. It names its bank and its version, so two banks, or
    two versions of one bank, never share seed keys (Study B protocol §4: every bank and
    attempt is independently seeded; pilot and confirmatory seeds stay disjoint). The
    rule is unambiguous because bank IDs hold no dot and versions only digits and dots.
    """
    if not isinstance(namespace, str) or len(namespace) > 64 or not PART_RE.fullmatch(namespace):
        return f"seed namespace {namespace!r} must match {PART_RE.pattern} (at most 64 characters)"
    default = default_seed_namespace(bank_id, bank_version)
    versioned = f"{bank_id}-v{bank_version}"
    if namespace in (default, versioned) or namespace.startswith(f"{versioned}-"):
        return None
    return (
        f"seed namespace {namespace!r} does not name bank {bank_id} version {bank_version}: "
        f"use {default!r} or '{versioned}-<suffix>'"
    )


def bank_spec(
    bank_id: str,
    permutation: UnitPermutation,
    *,
    bank_version: str = FIRST_VERSION,
    seed_namespace: str | None = None,
) -> BankSpec:
    """Check and bind a bank ID to its unit permutation (`permutation.check_bank_unit`)
    and to a seed namespace of the bank and version (`seed_namespace_error`)."""
    check_id(bank_id, "bank ID")
    bank_set(bank_id)
    check_bank_unit(bank_id, permutation)
    if not VERSION_RE.fullmatch(bank_version):
        raise BankBuildError(E_SPEC, f"bank_version {bank_version!r} must look like 1.0.0")
    namespace = (
        seed_namespace
        if seed_namespace is not None
        else default_seed_namespace(bank_id, bank_version)
    )
    error = seed_namespace_error(bank_id, bank_version, namespace)
    if error is not None:
        raise BankBuildError(E_SPEC, error)
    return BankSpec(bank_id, bank_version, namespace, permutation)


@dataclass(frozen=True, slots=True)
class BuildResult:
    """What `BankBuilder.build` produced."""

    bank_id: str
    bank_dir: Path
    status: Literal["complete", "unavailable"]
    attempt_used: int | None
    bank_sha256: str
    manifest: BankManifest
    attempts: tuple[AttemptSummary, ...]

    @property
    def slots_used(self) -> int:
        return sum(a.slots_used for a in self.attempts)


# ---------------------------------------------------------------------------
# One attempt


@dataclass
class _Cell:
    profile: str
    atom_id: str
    used: int = 0
    records: list[SlotRecord] = field(default_factory=list)
    options: list[RetainedOption] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _ProfileRun:
    profile: str
    status: Literal["complete", "failed", "stopped", "not_started"]
    failed_atom: str | None
    t_start_ms: int
    t_end_ms: int


class AttemptRun:
    """The state of one attempt: its ledger, cells and retained options per profile.

    `BankBuilder.run_attempt` drives it; tests may call `run_slot` directly (a 13th slot
    of a cell, or a 577th of the attempt, raises `SlotCapExceeded`).
    """

    def __init__(self, builder: BankBuilder, attempt: int) -> None:
        self.builder = builder
        self.attempt = attempt
        layout = builder.layout
        layout.attempt_dir(attempt).mkdir(parents=True, exist_ok=False)
        self.refusals = RecordWriter(
            layout.refusals(attempt), types=(SlotRefusal,), fsync=builder.fsync
        )
        self.ledger = builder.ledger_factory(
            layout.slots(attempt),
            run_id=builder.run_id,
            clock=builder.clock,
            refusals=self.refusals,
            timing=builder.timing,
            cap=B_SLOTS_PER_CELL,
        )
        self.stop = threading.Event()
        self._lock = threading.Lock()
        self._cells: dict[tuple[str, str], _Cell] = {}
        self._retained: dict[str, list[RetainedOption]] = {p: [] for p in PROFILES}
        self._references: dict[str, list[tuple[str, Reference]]] = {p: [] for p in PROFILES}
        self._pcm: dict[str, bytes] = {}
        self._total = 0

    # -- state -------------------------------------------------------------

    def cell(self, profile: str, atom_id: str) -> _Cell:
        with self._lock:
            key = (profile, atom_id)
            if key not in self._cells:
                if profile not in PROFILES or atom_id not in self.builder.spec.atom_order:
                    raise BankBuildError(E_SPEC, f"no cell {profile} {atom_id}")
                self._cells[key] = _Cell(profile, atom_id)
            return self._cells[key]

    @property
    def slots_used(self) -> int:
        return self._total

    def retained(self, profile: str) -> tuple[RetainedOption, ...]:
        with self._lock:
            return tuple(self._retained[profile])

    def records(self) -> list[SlotRecord]:
        """Every slot record of the attempt, in traversal order (profile, atom, slot)."""
        order = {a: i for i, a in enumerate(self.builder.spec.atom_order)}
        cells = sorted(
            self._cells.values(), key=lambda c: (PROFILES.index(c.profile), order[c.atom_id])
        )
        return [r for c in cells for r in c.records]

    # -- one slot ----------------------------------------------------------

    def _refuse(self, cap: str, requested: str, used: int, detail: str) -> None:
        b = self.builder
        self.refusals.append(
            SlotRefusal(
                run_id=b.run_id,
                study=Study.B,
                method=Method.B,
                cap_key=cap,
                reason="slot_cap",
                requested=requested,
                used=used,
                t_ms=b.clock.now_ms(),
                detail=detail,
            )
        )

    def run_slot(self, profile: str, atom_id: str) -> SlotRecord:
        """Run the next slot of a cell (reserve, propose, validate, consume)."""
        b = self.builder
        spec = b.spec
        cell = self.cell(profile, atom_id)
        cap = cap_key(Study.B, atom_id, bank_id=spec.bank_id, attempt=self.attempt, profile=profile)
        with self._lock:
            requested = f"{spec.bank_id}.t{self.attempt}.{profile}.{atom_id}.s{cell.used + 1:02d}"
            if cell.used >= B_SLOTS_PER_CELL:
                self._refuse(cap, requested, cell.used, "12 slots per cell (Study B section 4)")
                raise SlotCapExceeded(f"{cap}: a cell has at most {B_SLOTS_PER_CELL} slots")
            if self._total >= B_SLOTS_PER_ATTEMPT:
                self._refuse(
                    cap, requested, self._total, "576 slots per attempt (Study B section 4)"
                )
                raise SlotCapExceeded(
                    f"attempt {self.attempt}: at most {B_SLOTS_PER_ATTEMPT} slots"
                )
            if len(cell.options) >= B_OPTIONS_PER_CELL:
                raise BankBuildError(E_INTERNAL, f"{cap}: the cell already has its 4 options")
            cell.used += 1
            self._total += 1
            slot = cell.used
            retained = tuple(self._retained[profile])
            references = tuple(r for a, r in self._references[profile] if a != atom_id)
        slot_id = bank_slot_id(spec.bank_id, self.attempt, profile, atom_id, slot)
        state = BCellState(
            bank_id=spec.bank_id,
            attempt=self.attempt,
            profile=Profile(profile),
            atom_id=atom_id,
            slot=slot,
            semantic_label=spec.permutation.labels[atom_id],
            retained=retained,
            history=tuple(cell.records),
        )
        ticket = self.ledger.reserve(cap, slot_id, study=Study.B, method=Method.B)
        if ticket.slot_index != slot or ticket.slot_id != slot_id:
            raise BankBuildError(
                E_INTERNAL, f"{slot_id}: the ledger opened slot {ticket.slot_index}, not {slot}"
            )
        seed_key = b_seed_key(spec.seed_namespace, self.attempt, profile, atom_id, slot)
        proposal = b.proposer.propose(state, seed_key=seed_key, slot_id=slot_id)
        result: ValidationResult | None = None
        outcome = proposal.forced
        candidate = proposal.candidate
        if outcome is None and candidate is None:
            outcome = SlotOutcome.INVALID_JSON
        if outcome is None and candidate is not None:
            result = validate(
                candidate, profile, references, reserved=b.reserved, threshold=b.threshold
            )
            duplicate = result.pcm_sha256 is not None and result.pcm_sha256 in state.cell_hashes()
            outcome = outcome_from_validation(result, mode="B", cell_duplicate=duplicate)
        assert outcome is not None
        valid = outcome is SlotOutcome.VALID
        pcm: bytes | None = None
        if valid:
            assert result is not None and result.rendered is not None
            pcm = result.rendered.pcm
        recipe = result.recipe if result is not None else None
        record = SlotRecord(
            run_id=b.run_id,
            study=Study.B,
            method=Method.B,
            slot_id=slot_id,
            profile=Profile(profile),
            atom_id=atom_id,
            slot=slot,
            slot_index=ticket.slot_index,
            outcome=outcome,
            t_open_ms=ticket.t_open_ms,
            t_ms=max(b.clock.now_ms(), ticket.t_open_ms),
            bank_id=spec.bank_id,
            attempt=self.attempt,
            seed_key=seed_key,
            seed=proposal.seed,
            prompt_sha256=proposal.prompt_sha256,
            schema_sha256=proposal.schema_sha256,
            llm_status=proposal.llm_status,
            tokens_in=proposal.tokens_in,
            tokens_out=proposal.tokens_out,
            latency_ms=proposal.latency_ms,
            raw_output=proposal.raw_output,
            recipe=None if recipe is None else recipe.to_dict(),
            recipe_sha256=None if recipe is None else recipe.sha256(),
            validator_codes=() if result is None else result.codes,
            validator_messages=() if result is None else result.messages,
            pcm_sha256=None if result is None else result.pcm_sha256,
            file_sha256=None if pcm is None else wav_file_sha256(pcm),
        )
        self.ledger.consume(record)
        with self._lock:
            cell.records.append(record)
            if valid:
                assert recipe is not None and pcm is not None and record.pcm_sha256 is not None
                option = RetainedOption(
                    profile=Profile(profile),
                    atom_id=atom_id,
                    rank=len(cell.options) + 1,
                    recipe=recipe,
                    pcm_sha256=record.pcm_sha256,
                    slot_id=slot_id,
                )
                cell.options.append(option)
                self._retained[profile].append(option)
                self._references[profile].append(
                    (atom_id, Reference(slot_id, recipe, record.pcm_sha256, Profile(profile)))
                )
                self._pcm[slot_id] = pcm
        return record

    # -- cells, profiles, the attempt ----------------------------------------

    def run_cell(self, profile: str, atom_id: str) -> Literal["complete", "failed", "stopped"]:
        """Fill one cell: slots until 4 options or 12 slots (or a stop request)."""
        b = self.builder
        cell = self.cell(profile, atom_id)
        b.event("cell_start", attempt=self.attempt, profile=profile, atom_id=atom_id)
        t0 = b.clock.now_ms()
        status: Literal["complete", "failed", "stopped"] = "failed"
        while True:
            if len(cell.options) >= B_OPTIONS_PER_CELL:
                status = "complete"
                break
            if cell.used >= B_SLOTS_PER_CELL:
                break
            if self.stop.is_set():
                status = "stopped"
                break
            self.run_slot(profile, atom_id)
        b.event(
            "cell_end",
            attempt=self.attempt,
            profile=profile,
            atom_id=atom_id,
            duration_ms=b.clock.now_ms() - t0,
            detail=f"{status}: {len(cell.options)} options in {cell.used} slots",
        )
        return status

    def run_profile(self, profile: str) -> _ProfileRun:
        """One profile's stream: its 16 cells in the stored order."""
        b = self.builder
        t0 = b.clock.now_ms()
        if self.stop.is_set():
            return _ProfileRun(profile, "not_started", None, t0, t0)
        try:
            for atom in b.spec.atom_order:
                status = self.run_cell(profile, atom)
                if status == "stopped":
                    return _ProfileRun(profile, "stopped", None, t0, b.clock.now_ms())
                if status == "failed":
                    self.stop.set()
                    return _ProfileRun(profile, "failed", atom, t0, b.clock.now_ms())
        except BaseException:
            self.stop.set()
            raise
        return _ProfileRun(profile, "complete", None, t0, b.clock.now_ms())

    def run(self) -> AttemptSummary:
        """Run the attempt, write its WAVs (complete only) and `attempt.json`."""
        b = self.builder
        spec = b.spec
        b.event("attempt_start", attempt=self.attempt, detail=f"workers={b.workers}")
        t_start = b.clock.now_ms()
        started = utc_text(b.clock.utc_now())
        if b.workers == 1:
            runs = [self.run_profile(p) for p in PROFILES]
        else:
            with ThreadPoolExecutor(max_workers=b.workers, thread_name_prefix="bank") as pool:
                futures = [pool.submit(self.run_profile, p) for p in PROFILES]
                runs = [f.result() for f in futures]
        complete = all(r.status == "complete" for r in runs)
        failed = [CellRef(r.profile, r.failed_atom) for r in runs if r.failed_atom is not None]
        if complete:
            self._write_options()
        t_end = max(b.clock.now_ms(), t_start)
        records = self.records()
        counts: dict[tuple[str, str], Counter[str]] = {}
        for record in records:
            counts.setdefault((record.profile.value, record.atom_id), Counter())[
                SlotOutcome(record.outcome).value
            ] += 1
        order = {a: i for i, a in enumerate(spec.atom_order)}
        cells = tuple(
            CellSummary(c.profile, c.atom_id, c.used, len(c.options), dict(counts[key]))
            for key, c in sorted(
                self._cells.items(), key=lambda kv: (PROFILES.index(kv[0][0]), order[kv[0][1]])
            )
            if c.used
        )
        reason = None
        if failed:
            first = failed[0]
            cell = self._cells[(first.profile, first.atom_id)]
            reason = (
                f"cell {first.profile} {first.atom_id} used {cell.used} slots and retained "
                f"{len(cell.options)} of {B_OPTIONS_PER_CELL} options"
            )
        summary = AttemptSummary(
            bank_id=spec.bank_id,
            bank_version=spec.bank_version,
            seed_namespace=spec.seed_namespace,
            attempt=self.attempt,
            status="complete" if complete else "failed",
            reason=reason,
            slots_used=len(records),
            slots_sha256=file_sha256(b.layout.slots(self.attempt)),
            failed_cell=failed[0] if failed else None,
            failed_cells=tuple(failed),
            workers=b.workers,
            t_start_ms=t_start,
            t_end_ms=t_end,
            wall_ms=t_end - t_start,
            started_utc=started,
            ended_utc=utc_text(b.clock.utc_now()),
            profiles=tuple(
                ProfileSummary(
                    profile=r.profile,
                    status=r.status,
                    slots_used=sum(c.used for c in self._cells.values() if c.profile == r.profile),
                    cells_complete=sum(
                        1
                        for c in self._cells.values()
                        if c.profile == r.profile and len(c.options) == B_OPTIONS_PER_CELL
                    ),
                    wall_ms=r.t_end_ms - r.t_start_ms,
                )
                for r in runs
            ),
            cells=cells,
            throughput=throughput(records, t_end - t_start),
        )
        summary.write(b.layout.attempt_summary(self.attempt))
        tp = summary.throughput
        b.event(
            "attempt_end",
            attempt=self.attempt,
            duration_ms=summary.wall_ms,
            detail=(
                f"{summary.status}: {summary.slots_used} slots; "
                f"{tp.slots_per_minute} slots/min; latency p95 {tp.latency_ms.p95} ms; "
                f"slot p95 {tp.slot_ms.p95} ms; over cap {tp.slots_over_cap}"
            ),
        )
        return summary

    def _write_options(self) -> None:
        b = self.builder
        for (profile, atom_id), cell in self._cells.items():
            for option in cell.options:
                path = b.layout.option(option_wav(profile, atom_id, option.rank))
                path.parent.mkdir(parents=True, exist_ok=True)
                digest = write_wav(self._pcm[option.slot_id], path)
                expected = next(r.file_sha256 for r in cell.records if r.slot_id == option.slot_id)
                if digest != expected:
                    raise BankBuildError(E_INTERNAL, f"{path.name}: WAV hash differs from its slot")


# ---------------------------------------------------------------------------
# The bank


class BankBuilder:
    """Build one bank into `bank_dir` (see the module docstring)."""

    def __init__(
        self,
        spec: BankSpec,
        bank_dir: str | Path,
        *,
        config: GenerationConfig,
        proposer: SlotProposer,
        clock: Clock,
        run_id: str,
        kind: RunKind | str | None = None,
        freeze_manifest: dict[str, Any] | None = None,
        ledger_factory: LedgerFactory = slot_ledger,
        workers: int = 1,
        reserved: ReservedRegistry | None = None,
        fsync: bool = True,
    ) -> None:
        self.spec = spec
        self.layout = BankLayout(Path(bank_dir))
        self.config = config
        self.proposer = proposer
        self.clock = clock
        self.run_id = run_id
        self.kind = RunKind(kind) if kind is not None else RUN_KIND_OF_SET[spec.set]
        self.freeze_manifest = freeze_manifest
        self.ledger_factory = ledger_factory
        if not 1 <= workers <= len(PROFILES):
            raise BankBuildError(E_SPEC, f"workers must be 1..{len(PROFILES)}, got {workers}")
        self.workers = workers
        self.reserved = reserved if reserved is not None else load_reserved_registry()
        self.threshold = parse_threshold(config.separation_threshold)
        self.fsync = fsync
        self.timing: RecordWriter | None = None
        if self.layout.bank_id != spec.bank_id:
            raise BankBuildError(E_SPEC, f"bank directory {self.layout.root} is not {spec.bank_id}")
        namespace_error = seed_namespace_error(spec.bank_id, spec.bank_version, spec.seed_namespace)
        if namespace_error is not None:  # a BankSpec made without bank_spec
            raise BankBuildError(E_SPEC, namespace_error)

    def check(self) -> None:
        """The checks before slot 1: run kind, generation config (and the G4 freeze for
        confirmatory banks) and the proposer's inputs."""
        if (self.kind in PUBLIC_RUN_KINDS) != (self.spec.set == "demo"):
            raise BankBuildError(E_SPEC, f"a {self.spec.set} bank cannot be built as {self.kind}")
        check_run_config(self.config, kind=self.kind, freeze_manifest=self.freeze_manifest)
        self.proposer.check_config(self.config)

    def event(self, name: str, **fields: Any) -> None:  # noqa: ANN401
        """Append a bank timing event (`timing.jsonl` in the bank directory)."""
        if self.timing is None:
            return
        self.timing.append(
            TimingEvent(
                run_id=self.run_id,
                event=name,
                t_ms=self.clock.now_ms(),
                wall_utc=utc_text(self.clock.utc_now()),
                bank_id=self.spec.bank_id,
                component=COMPONENT,
                **fields,
            )
        )

    def open(self) -> None:
        """Create the bank directory with its config, permutation copy and timing log
        (refused when it exists and is not empty, and for a pilot or confirmatory bank
        inside a git work tree: `rundir.check_run_location`)."""
        root = self.layout.root
        check_run_location(root, self.kind)
        if root.exists() and any(root.iterdir()):
            raise BankBuildError(E_EXISTS, f"bank directory {root} already exists")
        root.mkdir(parents=True, exist_ok=True)
        self.config.write(self.layout.generation_config)
        with open(self.layout.permutation, "xb") as handle:
            handle.write(self.spec.permutation.data)
        self.timing = RecordWriter(self.layout.timing, types=(TimingEvent,), fsync=self.fsync)

    def attempts_on_disk(self) -> tuple[int, ...]:
        return self.layout.attempts()

    def run_attempt(self, attempt: int) -> AttemptSummary:
        """Run attempt `attempt` (the next one); a 5th attempt raises `AttemptCapExceeded`."""
        done = self.attempts_on_disk()
        if attempt > B_MAX_ATTEMPTS:
            if self.layout.root.is_dir():
                RecordWriter(
                    self.layout.root / BANK_REFUSALS_NAME, types=(SlotRefusal,), fsync=self.fsync
                ).append(
                    SlotRefusal(
                        run_id=self.run_id,
                        study=Study.B,
                        method=Method.B,
                        cap_key=f"B|{self.spec.bank_id}",
                        reason="attempt_cap",
                        requested=f"attempt-{attempt}",
                        used=len(done),
                        t_ms=self.clock.now_ms(),
                        detail="at most 4 attempts per bank (Study B section 4)",
                    )
                )
            raise AttemptCapExceeded(f"{self.spec.bank_id}: at most {B_MAX_ATTEMPTS} attempts")
        if self.timing is None:
            raise BankBuildError(E_ORDER, "build() opens the bank directory before any attempt")
        if attempt != len(done) + 1:
            raise BankBuildError(E_ORDER, f"attempt {attempt} requested after attempts {done}")
        for number in done:
            if AttemptSummary.read(self.layout.attempt_summary(number)).status == "complete":
                raise BankBuildError(E_ORDER, f"attempt {number} is complete: it is the bank")
        return AttemptRun(self, attempt).run()

    def build(self) -> BuildResult:
        """Run attempts until one completes or 4 fail; write the manifest and bank hash."""
        self.check()
        self.open()
        self.event("bank_start", detail=f"{self.spec.bank_id} v{self.spec.bank_version}")
        t0 = self.clock.now_ms()
        summaries = []
        for attempt in range(1, B_MAX_ATTEMPTS + 1):
            summary = self.run_attempt(attempt)
            summaries.append(summary)
            if summary.status == "complete":
                break
        manifest = manifest_from_files(self.layout.root)
        manifest.write(self.layout.manifest)
        digest = manifest.bank_sha256()
        with open(self.layout.bank_hash, "x", encoding="utf-8", newline="\n") as handle:
            handle.write(digest + "\n")
        self.event(
            "bank_end",
            duration_ms=self.clock.now_ms() - t0,
            detail=f"{manifest.status}; attempt {manifest.attempt_used}; bank {digest}",
        )
        return BuildResult(
            bank_id=self.spec.bank_id,
            bank_dir=self.layout.root,
            status=manifest.status,
            attempt_used=manifest.attempt_used,
            bank_sha256=digest,
            manifest=manifest,
            attempts=tuple(summaries),
        )
