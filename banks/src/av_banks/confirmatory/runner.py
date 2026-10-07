"""Run the campaign's banks in parallel, with progress monitoring (#28).

`run_campaign` builds every *pending* bank of the plan (current version, no run directory
yet) with the #26 builder, one run per bank (`av_banks.run.build_banks` with one spec,
run ID `<campaign>-<bank>`), `parallel_banks` banks at once and `workers` profile streams
each. Before the first bank it re-checks the campaign: the stored freeze manifest and
config are the planned ones and `genconfig.check_run_config` passes (code pins, frozen
G4 manifest, config hash), so it refuses to run under any other configuration.

- **Budget.** A bank is built once per version: complete and unavailable banks are never
  built again, and the builder caps every attempt (12 slots per cell, 576 per attempt,
  4 attempts). Nothing generates beyond a bank's budget.
- **Progress.** A monitor thread appends a snapshot to `progress.jsonl` every
  `monitor_interval_s` seconds (and calls `on_progress`): banks per state, slots used,
  slots per minute, the running banks' attempt and slot counts, an estimate of the time
  left. `campaign_status(root)` gives the same view from another process.
- **Model-server failures.** A slot whose model call failed (`llm_status` `server_error`
  or `timeout`) is consumed as usual. After `breaker_threshold` such slots in a row
  (across all banks) the campaign halts: the next slot of every running bank raises
  `CampaignHalted`, so an outage cannot burn the banks' attempts. Halted or failed builds
  leave their bank *crashed* (a run directory without a manifest). After a failed build no
  further bank starts (`stop_on_error`); running banks finish. Ctrl-C does the same.
- **Crashes.** A crashed bank is never resumed (#26). `rebuild_bank` records a rebuild
  under the next bank version (`1.0.0` -> `1.0.1`, seed namespace `<bank>-v1.0.1`,
  rechecked against every other namespace and the pilot) in `rebuilds.jsonl`; the next
  `run_campaign` builds it. The crashed run stays in the campaign and the archive, and
  the register reports it.
- **One runner.** `runner.lock` admits one runner per campaign; `break_lock=True` removes
  a lock left by a killed runner.
"""

from __future__ import annotations

import os
import shutil
import threading
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal

from av_generation.clock import Clock, utc_text
from av_generation.genconfig import ConfigMismatch, GenerationConfig, check_run_config
from av_generation.ids import RunKind
from av_generation.jsonio import canonical_line, file_sha256, read_json, to_json_value
from av_generation.outcomes import LlmStatus
from av_generation.proposers import BCellState
from av_generation.rundir import RunLayout

from av_banks.builder import LedgerFactory, bank_spec, default_seed_namespace, slot_ledger
from av_banks.layout import BankLayout
from av_banks.permutation import load_permutation
from av_banks.proposer import Proposal, SlotProposer
from av_banks.run import build_banks

from .common import (
    E_FREEZE,
    E_INPUT,
    E_LOCKED,
    E_SEEDS,
    E_STATE,
    E_UNITS,
    CampaignError,
    CampaignLayout,
    run_id_for,
)
from .plan import (
    CampaignPlan,
    PlannedBank,
    Rebuild,
    append_event,
    bank_history,
    next_version,
    read_plan,
    read_rebuilds,
)
from .seed_check import PilotSeeds, SeedEntry, check_seeds

BankState = Literal["pending", "running", "complete", "unavailable", "crashed"]
STATES: Final[tuple[BankState, ...]] = ("pending", "running", "complete", "unavailable", "crashed")
INFRASTRUCTURE: Final = frozenset({LlmStatus.SERVER_ERROR, LlmStatus.TIMEOUT})
DEFAULT_BREAKER: Final = 24
"""Consecutive failed model calls that halt the campaign (two cells' worth of slots)."""

ProposerFactory = Callable[[RunLayout, PlannedBank], SlotProposer]
"""Makes the proposer of one bank run (called with the new run's layout)."""


class CampaignHalted(RuntimeError):
    """The campaign stopped before this slot (repeated model-server failures)."""


# ---------------------------------------------------------------------------
# Status


@dataclass(frozen=True, slots=True)
class BankProgress:
    bank_id: str
    bank_version: str
    run_id: str
    state: BankState
    attempts: int
    """Attempts on disk."""
    slots_attempt: int
    """Slots of the last attempt on disk."""
    slots_total: int
    bank_sha256: str | None


@dataclass(frozen=True, slots=True)
class CampaignStatus:
    campaign_id: str
    at_utc: str
    runner_active: bool
    counts: Mapping[str, int]
    slots_total: int
    crashed_versions: int
    """Earlier versions of rebuilt banks (crashed before their rebuild)."""
    banks: tuple[BankProgress, ...]

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = to_json_value(self)
        return data

    def line(self, *, rate: float | None = None, eta_hours: float | None = None) -> str:
        c = self.counts
        done = c.get("complete", 0) + c.get("unavailable", 0)
        running = [
            f"{b.bank_id} t{b.attempts} {b.slots_attempt}/576"
            for b in self.banks
            if b.state == "running"
        ]
        text = (
            f"{self.campaign_id}: {done}/{len(self.banks)} done ({c.get('complete', 0)} complete, "
            f"{c.get('unavailable', 0)} unavailable), {c.get('running', 0)} running, "
            f"{c.get('crashed', 0)} crashed, {c.get('pending', 0)} pending; "
            f"{self.slots_total} slots"
        )
        if rate is not None:
            text += f"; {rate:.1f} slots/min"
        if eta_hours is not None:
            text += f"; about {eta_hours:.1f} h left"
        if running:
            text += "; running: " + ", ".join(running[:6]) + (" ..." if len(running) > 6 else "")
        return text


def _lines(path: Path) -> int:
    try:
        return path.read_bytes().count(b"\n")
    except OSError:
        return 0


def bank_progress(layout: CampaignLayout, bank: PlannedBank, *, active: bool) -> BankProgress:
    """The state of one bank version from its files."""
    bank_dir = layout.bank_dir(bank.run_id, bank.bank_id)
    blayout = BankLayout(bank_dir)
    attempts = blayout.attempts()
    slot_counts = [_lines(blayout.slots(a)) for a in attempts]
    state: BankState = "pending"
    digest = None
    if blayout.manifest.is_file() and blayout.bank_hash.is_file():
        try:
            manifest = read_json(blayout.manifest)
            status = manifest.get("status") if isinstance(manifest, dict) else None
            if status in ("complete", "unavailable"):
                state = status
                digest = blayout.bank_hash.read_text(encoding="utf-8").strip()
        except (OSError, ValueError):
            state = "pending"
    if state == "pending" and layout.run_dir(bank.run_id).exists():
        state = "running" if active else "crashed"
    return BankProgress(
        bank_id=bank.bank_id,
        bank_version=bank.bank_version,
        run_id=bank.run_id,
        state=state,
        attempts=len(attempts),
        slots_attempt=slot_counts[-1] if slot_counts else 0,
        slots_total=sum(slot_counts),
        bank_sha256=digest,
    )


def campaign_status(
    root: str | os.PathLike[str], *, clock: Clock | None = None, active: bool | None = None
) -> CampaignStatus:
    """Every bank's state (`active`: whether a runner holds the lock; default: the lock
    file decides)."""
    layout = CampaignLayout.at(root)
    plan = read_plan(root)
    history = bank_history(plan, read_rebuilds(root))
    runner = layout.lock.exists() if active is None else active
    banks = tuple(bank_progress(layout, history[b.bank_id][-1], active=runner) for b in plan.banks)
    counts: dict[str, int] = {s: sum(1 for b in banks if b.state == s) for s in STATES}
    old = [v for versions in history.values() for v in versions[:-1]]
    old_slots = sum(bank_progress(layout, v, active=False).slots_total for v in old)
    now = clock.utc_now() if clock is not None else datetime.now(UTC)
    return CampaignStatus(
        campaign_id=plan.campaign_id,
        at_utc=utc_text(now),
        runner_active=runner,
        counts=counts,
        slots_total=sum(b.slots_total for b in banks) + old_slots,
        crashed_versions=len(old),
        banks=banks,
    )


# ---------------------------------------------------------------------------
# Model-server breaker


class InfrastructureBreaker:
    """Counts consecutive failed model calls across all banks; trips at `threshold`."""

    def __init__(self, threshold: int = DEFAULT_BREAKER) -> None:
        if threshold < 1:
            raise CampaignError(E_INPUT, "the breaker threshold must be at least 1")
        self.threshold = threshold
        self._run = 0
        self._lock = threading.Lock()
        self.tripped: str | None = None

    def check(self) -> None:
        """Raise `CampaignHalted` once tripped."""
        if self.tripped is not None:
            raise CampaignHalted(self.tripped)

    def trip(self, reason: str) -> None:
        with self._lock:
            if self.tripped is None:
                self.tripped = reason

    def record(self, proposal: Proposal, slot_id: str) -> None:
        with self._lock:
            failed = proposal.llm_status in INFRASTRUCTURE
            self._run = self._run + 1 if failed else 0
            if self._run >= self.threshold and self.tripped is None:
                self.tripped = (
                    f"{self._run} consecutive failed model calls (last {slot_id}, "
                    f"{proposal.llm_status}); check the model server, then rebuild the "
                    "crashed banks"
                )


class GuardedProposer:
    """A `SlotProposer` that stops at the breaker before each proposal."""

    def __init__(self, inner: SlotProposer, breaker: InfrastructureBreaker) -> None:
        self.inner = inner
        self.breaker = breaker

    def check_config(self, config: GenerationConfig) -> None:
        self.inner.check_config(config)

    def propose(self, cell: BCellState, *, seed_key: str, slot_id: str) -> Proposal:
        self.breaker.check()
        proposal = self.inner.propose(cell, seed_key=seed_key, slot_id=slot_id)
        self.breaker.record(proposal, slot_id)
        return proposal


# ---------------------------------------------------------------------------
# The run


@dataclass(frozen=True, slots=True)
class BankRun:
    bank_id: str
    bank_version: str
    run_id: str
    outcome: Literal["complete", "unavailable", "crashed", "skipped"]
    bank_sha256: str | None = None
    slots_used: int | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class CampaignRun:
    campaign_id: str
    banks: tuple[BankRun, ...]
    halted: str | None
    """Why the breaker halted the running banks (repeated failed model calls)."""
    stopped: str | None
    """The first build error after which no further bank was started."""
    status: CampaignStatus


def check_campaign(
    root: str | os.PathLike[str], config: GenerationConfig | None = None
) -> tuple[CampaignPlan, GenerationConfig, dict[str, Any], RunKind]:
    """Re-check a campaign before running it: the stored freeze manifest and config are
    the planned ones, `config` (if given) is the stored one, and `check_run_config`
    passes. Raises `CampaignError(E_FREEZE)`."""
    layout = CampaignLayout.at(root)
    plan = read_plan(root)
    if file_sha256(layout.freeze_manifest) != plan.freeze.manifest_sha256:
        raise CampaignError(E_FREEZE, "freeze-manifest.json is not the planned freeze manifest")
    stored = GenerationConfig.read(layout.generation_config)
    if stored.frozen_sha256() != plan.generation_config_sha256:
        raise CampaignError(E_FREEZE, "generation-config.json is not the planned config")
    if config is not None and config.frozen_sha256() != plan.generation_config_sha256:
        raise CampaignError(
            E_FREEZE,
            f"config hash {config.frozen_sha256()} differs from the campaign's "
            f"{plan.generation_config_sha256} (the G4 freeze value)",
        )
    freeze = read_json(layout.freeze_manifest)
    kind = RunKind(plan.kind)
    try:
        check_run_config(stored, kind=kind, freeze_manifest=freeze)
    except ConfigMismatch as err:
        raise CampaignError(E_FREEZE, f"refused by the freeze check: {err}") from err
    return plan, stored, freeze, kind


def _event(layout: CampaignLayout, clock: Clock, event: str, **fields: Any) -> None:  # noqa: ANN401
    append_event(layout, {"event": event, "at_utc": utc_text(clock.utc_now()), **fields})


class _Monitor:
    def __init__(
        self,
        layout: CampaignLayout,
        clock: Clock,
        interval_s: float,
        on_progress: Callable[[CampaignStatus, str], None] | None,
        start_slots: int,
    ) -> None:
        self.layout = layout
        self.clock = clock
        self.interval_s = interval_s
        self.on_progress = on_progress
        self.start_slots = start_slots
        self.t0 = clock.now_ms()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="campaign-monitor", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            self.snapshot()

    def snapshot(self, *, active: bool = True) -> CampaignStatus:
        status = campaign_status(self.layout.root, clock=self.clock, active=active)
        minutes = (self.clock.now_ms() - self.t0) / 60_000
        rate = (status.slots_total - self.start_slots) / minutes if minutes > 0 else None
        done = [b for b in status.banks if b.state in ("complete", "unavailable")]
        left = [b for b in status.banks if b.state in ("pending", "running")]
        eta = None
        if rate and done:
            mean = sum(b.slots_total for b in done) / len(done)
            remaining = sum(max(mean - b.slots_total, 0.0) for b in left)
            eta = remaining / rate / 60
        line = status.line(rate=rate, eta_hours=eta)
        record = {
            "at_utc": status.at_utc,
            "counts": dict(status.counts),
            "slots_total": status.slots_total,
            "slots_per_minute": None if rate is None else round(rate, 3),
            "eta_hours": None if eta is None else round(eta, 3),
            "running": [
                {"bank_id": b.bank_id, "attempt": b.attempts, "slots": b.slots_attempt}
                for b in status.banks
                if b.state == "running"
            ],
        }
        with open(self.layout.progress, "ab") as handle:
            handle.write(canonical_line(record))
        if self.on_progress is not None:
            self.on_progress(status, line)
        return status


def _discard_unstarted(layout: CampaignLayout, bank: PlannedBank) -> bool:
    """Remove the run directory of a build that failed before its first slot (a refused
    check: no attempt directory, no model call), so the bank stays pending under the same
    version. Returns whether it did; a build that reserved any slot is kept (crashed)."""
    run_dir = layout.run_dir(bank.run_id)
    if not run_dir.is_dir():
        return True
    if BankLayout(layout.bank_dir(bank.run_id, bank.bank_id)).attempts():
        return False
    requests = run_dir / "logs" / "llm-requests.jsonl"
    if requests.is_file() and requests.stat().st_size > 0:
        return False
    shutil.rmtree(run_dir)
    return True


def _acquire(layout: CampaignLayout, clock: Clock, break_lock: bool) -> None:
    try:
        with open(layout.lock, "x", encoding="utf-8", newline="\n") as handle:
            handle.write(utc_text(clock.utc_now()) + "\n")
    except FileExistsError:
        if not break_lock:
            raise CampaignError(
                E_LOCKED,
                "another runner holds runner.lock (if it was killed, run again with break_lock)",
            ) from None
        layout.lock.unlink()
        _event(layout, clock, "lock_broken")
        _acquire(layout, clock, False)


def run_campaign(
    root: str | os.PathLike[str],
    *,
    proposer_factory: ProposerFactory,
    clock: Clock,
    config: GenerationConfig | None = None,
    bank_clock: Callable[[PlannedBank], Clock] | None = None,
    parallel_banks: int = 1,
    workers: int = 3,
    only: Sequence[str] | None = None,
    ledger_factory: LedgerFactory = slot_ledger,
    fsync: bool = True,
    breaker_threshold: int = DEFAULT_BREAKER,
    monitor_interval_s: float = 60.0,
    on_progress: Callable[[CampaignStatus, str], None] | None = None,
    stop_on_error: bool = True,
    break_lock: bool = False,
    llm_runtime: str | None = None,
) -> CampaignRun:
    """Build the pending banks (module docstring). `bank_clock(bank)` gives each bank its
    own clock (deterministic rehearsals); by default all use `clock`."""
    layout = CampaignLayout.at(root)
    plan, stored, freeze, kind = check_campaign(root, config)
    if parallel_banks < 1:
        raise CampaignError(E_INPUT, "parallel_banks must be at least 1")
    history = bank_history(plan, read_rebuilds(root))
    current = [history[b.bank_id][-1] for b in plan.banks]
    if only is not None:
        unknown = sorted(set(only) - {b.bank_id for b in current})
        if unknown:
            raise CampaignError(E_INPUT, f"not banks of this campaign: {unknown}")
    breaker = InfrastructureBreaker(breaker_threshold)
    _acquire(layout, clock, break_lock)
    stop = threading.Event()
    stopped: list[str] = []
    results: list[BankRun] = []
    monitor: _Monitor | None = None
    try:
        before = campaign_status(root, clock=clock, active=False)
        for progress in before.banks:
            if progress.state == "crashed":
                _event(
                    layout, clock, "bank_crashed", bank_id=progress.bank_id, run_id=progress.run_id
                )
        pending = [
            b
            for b, p in zip(current, before.banks, strict=True)
            if p.state == "pending" and (only is None or b.bank_id in only)
        ]
        _event(
            layout,
            clock,
            "runner_start",
            banks=[b.bank_id for b in pending],
            parallel_banks=parallel_banks,
            workers=workers,
            config_sha256=plan.generation_config_sha256,
        )
        monitor = _Monitor(layout, clock, monitor_interval_s, on_progress, before.slots_total)
        monitor.start()

        def build_one(bank: PlannedBank) -> BankRun:
            if stop.is_set():
                return BankRun(bank.bank_id, bank.bank_version, bank.run_id, "skipped")
            permutation = load_permutation(layout.unit(bank.dyad_slot))
            if permutation.sha256 != bank.permutation_sha256:
                raise CampaignError(E_UNITS, f"{bank.dyad_slot}: permutation differs from the plan")
            spec = bank_spec(
                bank.bank_id,
                permutation,
                bank_version=bank.bank_version,
                seed_namespace=bank.seed_namespace,
            )
            _event(layout, clock, "bank_start", bank_id=bank.bank_id, run_id=bank.run_id)
            try:
                result = build_banks(
                    [spec],
                    runs_root=layout.runs,
                    run_id=bank.run_id,
                    config=stored,
                    proposer=lambda run: GuardedProposer(proposer_factory(run, bank), breaker),
                    clock=bank_clock(bank) if bank_clock is not None else clock,
                    kind=kind,
                    freeze_manifest=freeze,
                    freeze_manifest_sha256=plan.freeze.manifest_sha256,
                    llm_runtime=llm_runtime,
                    workers=workers,
                    parallel_banks=1,
                    ledger_factory=ledger_factory,
                    fsync=fsync,
                )
            except Exception as err:  # the bank is crashed; keep the campaign's record
                text = f"{type(err).__name__}: {err}"[:400]
                if stop_on_error or isinstance(err, CampaignHalted):
                    stop.set()  # no new bank starts; running banks finish
                    stopped.append(f"{bank.bank_id}: {text}")
                if _discard_unstarted(layout, bank):
                    _event(layout, clock, "bank_not_started", bank_id=bank.bank_id, error=text)
                    return BankRun(
                        bank.bank_id, bank.bank_version, bank.run_id, "skipped", error=text
                    )
                _event(layout, clock, "bank_error", bank_id=bank.bank_id, error=text)
                return BankRun(bank.bank_id, bank.bank_version, bank.run_id, "crashed", error=text)
            built = result.banks[0]
            _event(
                layout,
                clock,
                "bank_end",
                bank_id=bank.bank_id,
                status=built.status,
                bank_sha256=built.bank_sha256,
                slots=built.slots_used,
            )
            return BankRun(
                bank.bank_id,
                bank.bank_version,
                bank.run_id,
                built.status,
                built.bank_sha256,
                built.slots_used,
            )

        try:
            if parallel_banks == 1:
                results = [build_one(b) for b in pending]
            else:
                with ThreadPoolExecutor(parallel_banks, thread_name_prefix="campaign") as pool:
                    results = list(pool.map(build_one, pending))
        except BaseException:  # e.g. Ctrl-C: start no further bank, let running ones finish
            stop.set()
            raise
    finally:
        if monitor is not None:
            monitor.stop()
        _event(
            layout,
            clock,
            "runner_end",
            halted=breaker.tripped,
            stopped=stopped[0] if stopped else None,
        )
        layout.lock.unlink(missing_ok=True)
    final = (
        monitor.snapshot(active=False)
        if monitor is not None
        else campaign_status(root, clock=clock)
    )
    return CampaignRun(
        plan.campaign_id, tuple(results), breaker.tripped, stopped[0] if stopped else None, final
    )


# ---------------------------------------------------------------------------
# Rebuild after a crash


def rebuild_bank(
    root: str | os.PathLike[str], bank_id: str, *, reason: str, clock: Clock
) -> PlannedBank:
    """Record the rebuild of a crashed bank under the next bank version (module
    docstring); the next `run_campaign` builds it. Refused for a bank that is pending,
    running, complete or unavailable: an unavailable bank is final (Study B §4)."""
    layout = CampaignLayout.at(root)
    if not reason.strip():
        raise CampaignError(E_INPUT, "a rebuild needs a reason")
    plan = read_plan(root)
    rebuilds = read_rebuilds(root)
    history = bank_history(plan, rebuilds)
    if bank_id not in history:
        raise CampaignError(E_INPUT, f"{bank_id} is not a bank of this campaign")
    current = history[bank_id][-1]
    state = bank_progress(layout, current, active=layout.lock.exists()).state
    if state != "crashed":
        raise CampaignError(E_STATE, f"{bank_id} is {state}; only a crashed bank is rebuilt")
    version = next_version(current.bank_version)
    entry = Rebuild(
        bank_id=bank_id,
        from_version=current.bank_version,
        to_version=version,
        seed_namespace=default_seed_namespace(bank_id, version),
        run_id=run_id_for(plan.campaign_id, bank_id, version),
        reason=reason.strip()[:400],
        at_utc=utc_text(clock.utc_now()),
    )
    new = bank_history(plan, (*rebuilds, entry))
    entries = [
        SeedEntry(v.bank_id, v.bank_version, v.seed_namespace) for vs in new.values() for v in vs
    ]
    check = check_seeds(
        entries,
        expected_set=plan.set,
        pilot=PilotSeeds(frozenset(plan.pilot.namespaces)),
    )
    if not check.ok:
        raise CampaignError(E_SEEDS, f"the rebuild's seeds are not unique or not disjoint: {entry}")
    with open(layout.rebuilds, "ab") as handle:
        handle.write(canonical_line(to_json_value(entry)))
    _event(
        layout, clock, "bank_rebuild", bank_id=bank_id, bank_version=version, reason=entry.reason
    )
    return new[bank_id][-1]


def current_banks(root: str | os.PathLike[str]) -> Mapping[str, Sequence[PlannedBank]]:
    """Every version of every bank (`bank_history` of the stored plan and rebuilds)."""
    return bank_history(read_plan(root), read_rebuilds(root))
