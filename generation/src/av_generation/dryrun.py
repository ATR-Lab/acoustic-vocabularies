"""Synthetic-panel dry run of a full batch (#22).

One Study A batch (16 atoms x 3 methods) runs through the real system: the orchestrator
and its study-mode runner (#20, `batch_runner`), A2 (#18), A3 (#17) against an
OpenAI-compatible server (#16's mock server here; the pinned model on the LLM GPU host
for the real-time run, **pending (hardware)**), the A1 web app (#19) worked over HTTP by
a scripted bot designer, and three bot raters (`rater.BotRater`, #21) that join the real
panel server through their keyed station URLs. Everything is labelled `synthetic`
(`DEMO-` run, batch and book IDs, the `DEMO-DRY` seed namespace, run kind `synthetic`,
run purpose `dry_run`); nothing enters a pilot or confirmatory book.

The driver (`run_dry_run`, CLI `run`):

1. builds the synthetic batch config (`dry_run_config`: profile and atom order of the
   DEMO batch, the panel order and aliases drawn from the `DEMO-DRY` set namespace, a
   `DEMO-DRY-...` seed namespace, three `bot` seats);
2. preloads the model (starts the mock server, or waits for an external one, and makes
   one warm-up call) and the renderer (render and validate one recipe), and logs both
   as `startup_start` / `startup_end` timing events (components `llm`, `renderer`),
   apart from the slots;
3. writes the injected cases before the first slot (`DryRunPlan`, `dry-run-plan.json`):
   zero-eligible atoms (every bot rates that book's 12 rating slots of the atom
   unacceptable; the slots come from `BatchConfig.rating_slot_ids`, so no book ID reaches
   a station), invalid and skipped (`timeout`) designer slots, the bot designer's think
   time and the mock model latency;
4. runs one station session per appointment with `batch_runner.run_session(...,
   panel="stations", on_panel=...)`: the hook starts the bot designer on the served A1
   page and one `BotRater.from_station_url` per keyed URL; every later appointment runs
   on the reopened run (`open_batch(..., resume=True)`), with new seat keys.

The whole-book fallback runs separately (`fallback_bank="single_recipe"`): every bank
entry of the profile is the bank's recipe 0, so after a zero-eligible atom commits it, the
next zero-eligible atom of that book finds no passing bank recipe and the frozen fallback
book is substituted (Study A protocol §3.7).

The checks read the run directory only:

- `check_log_completeness(run_dir, *, plan=None)`: every count of the acceptance
  criteria (commits with their SHA-256 in the vocabulary store, slot records with a status
  and none replenished, rating records per rater with placeholders, zero message plays,
  fallback events equal to the injected cases), scaled to the plan's appointments;
- `timing_rows` / `write_timing` (`timing.csv`, `timing-summary.json`): wall time per
  round, atom and appointment against the 20-min and 80-min limits, and the startup;
- `tally_logs` / `compare_with_audit`: an independent recount from the raw JSONL lines
  (standard library only, no record classes, no audit code) compared with the #24 audit
  tables;
- `write_bundle` / `write_evidence`: a deterministic tar.gz of the run directory and a
  small text evidence directory (`manifest.json` with every hash).

Command line (from the repository root):

    uv run --project generation python -m av_generation.dryrun run --out generation/out/dry \\
        --run-id DEMO-dry-run-accel-01 --clock scaled --speed 20 \\
        --evidence generation/runs/DEMO-dry-run-accel-01
    uv run --project generation python -m av_generation.dryrun check <run_dir>
    uv run --project generation python -m av_generation.dryrun timing <run_dir> --out DIR
    uv run --project generation python -m av_generation.dryrun tally <run_dir> --audit DIR

`generation/docs/dry-run.md` has every mode and the pending GPU run.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import platform
import sys
import tarfile
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Literal

import httpx
from av_sound._paths import data_root as sound_root
from av_sound.fallback import BankEntry, FallbackSet
from av_sound.store import VocabularyStore
from av_sound.validate import validate

from av_generation import _batch_sim as sim
from av_generation import batch_runner as br
from av_generation._paths import examples_path
from av_generation.audit import TALLY_COLUMNS, build_audit, tally_sheet
from av_generation.clock import Clock, ScaledClock, SystemClock, utc_text
from av_generation.config import BatchConfig, PanelAssignment, RaterSeat
from av_generation.constants import (
    APPOINTMENT_BOOKING_MS,
    APPOINTMENT_BUDGET_MS,
    APPOINTMENTS_PER_BATCH,
    ATOM_BUDGET_MS,
    ATOMS_PER_APPOINTMENT,
    ATOMS_PER_BOOK,
    PANEL_ORDERS,
    RATING_SLOTS_PER_ROUND,
    ROUND_BUDGET_MS,
    ROUNDS_PER_ATOM,
    SLOT_CAP_MS,
    SLOTS_PER_ATOM,
    SLOTS_PER_ROUND,
)
from av_generation.ids import Method, RunKind, proposal_slot_id, rating_slot_id
from av_generation.llm import OpenAICompatibleClient, decoding_schema
from av_generation.mock_llm import MockLlmServer, MockReply
from av_generation.orchestrator import panel_aliases, panel_order_schedule
from av_generation.outcomes import OUTCOME_CODES
from av_generation.rater import BotRater, BotRatingPolicy, BotRunResult
from av_generation.records import (
    CommitRecord,
    DecisionRecord,
    Document,
    FallbackScanRecord,
    PlayEvent,
    RatingRecord,
    RecordWriter,
    RunManifest,
    SlotRecord,
    SlotRefusal,
    TimingEvent,
    read_records,
)
from av_generation.rundir import DRY_RUN_PLAN_NAME, LOG_FILES, RunLayout, relative_files
from av_generation.seeds import a3_seed_key, bot_seed_key, rng_for
from av_generation.webserve import serve_in_thread

__all__ = [
    "BUNDLE_SUFFIX",
    "DRIVER_SUFFIX",
    "DRY_RUN_SET_NS",
    "EVIDENCE_FORMAT",
    "STARTUP_COMPONENTS",
    "TIMING_COLUMNS",
    "CompletenessReport",
    "DryRunPlan",
    "DryRunResult",
    "InjectedFallback",
    "SessionResult",
    "ThinkingBotDesigner",
    "TimingRow",
    "TimingSummary",
    "check_log_completeness",
    "compare_with_audit",
    "default_zero_eligible",
    "driver_summary",
    "dry_run_config",
    "main",
    "make_plan",
    "run_dry_run",
    "single_recipe_fallback",
    "tally_logs",
    "timing_rows",
    "write_bundle",
    "write_evidence",
    "write_hand_tally",
    "write_timing",
]

DRY_RUN_SET_NS: Final = "DEMO-DRY"
"""Set namespace of the dry-run panel order and aliases (`PANEL|DEMO-DRY|...`)."""
DRY_RUN_BATCH_ID: Final = "DEMO-DRY-P01"
DRY_RUN_PANEL_ID: Final = "DEMO-DRY-P01-N1"
DRY_RUN_SEED_NAMESPACE: Final = "DEMO-DRY-P01-s1"
"""Part `<batch_ns>` of the A1/A2/A3 seed keys of the dry run (a DEMO namespace)."""
BOT_SEATS: Final[tuple[RaterSeat, ...]] = (
    RaterSeat("R01", "S1", "bot"),
    RaterSeat("R02", "S2", "bot"),
    RaterSeat("R03", "S3", "bot"),
)
DEFAULT_THINK_MS: Final = (10_000, 35_000)
"""Bot designer think time per slot (clock ms, uniform): the A1 window is then about
70 s, like a designer who uses most of the 40-s slots (Proposed, #22)."""
DEFAULT_MOCK_LATENCY_MS: Final = 3_000
"""Clock time the mock server takes per call (a 7B model on one GPU, Proposed)."""
DESIGNER_INVALID_PER_APPOINTMENT: Final = 2
DESIGNER_TIMEOUT_PER_APPOINTMENT: Final = 2
EVIDENCE_FORMAT: Final = "av-generation/dry-run-evidence"
BUNDLE_SUFFIX: Final = "-bundle.tar.gz"
DRIVER_SUFFIX: Final = "-driver.json"
STARTUP_COMPONENTS: Final[tuple[str, ...]] = ("llm", "renderer")

FallbackBankKind = Literal["demo", "single_recipe"]


# ---------------------------------------------------------------------------
# The plan: everything the dry run injects


@dataclass(frozen=True, slots=True)
class InjectedFallback:
    """One atom forced to zero eligible candidates and the fallback it must cause."""

    book_id: str
    atom_id: str
    expect: Literal["bank", "book"]
    """`bank`: a `fallback_bank` commit; `book`: a failed scan and whole-book substitution."""


@dataclass(frozen=True, slots=True)
class DryRunPlan(Document):
    """`dry-run-plan.json`: everything the dry run injects (restricted like the logs).

    Written before the first slot; `check_log_completeness` compares the logs with it."""

    TAG = "av-generation/dry-run-plan"
    VERSION = 1
    SCHEMA = "dry-run-plan.schema.json"

    run_id: str
    batch_id: str
    clock: Literal["real", "scaled"]
    clock_speed: float | None
    zero_eligible: tuple[InjectedFallback, ...]
    force_unacceptable_slots: tuple[str, ...]
    """Rating-slot IDs given to every bot (`BotRatingPolicy.force_unacceptable_slots`)."""
    designer_invalid_slots: tuple[str, ...]
    """Proposal-slot IDs where the bot designer submits an invalid recipe."""
    designer_timeout_slots: tuple[str, ...]
    """Proposal-slot IDs where the bot designer submits nothing (`timeout`)."""
    appointments: tuple[int, ...] = (1, 2, 3, 4)
    """The appointments the run covers (a reduced run covers fewer than four)."""
    fallback_bank: FallbackBankKind = "demo"
    """`demo`: the committed DEMO fallback set; `single_recipe`: every bank entry of the
    profile is the bank's recipe 0 (the whole-book fallback run)."""
    designer_think_ms: tuple[int, int] | None = None
    """Bot designer think time per slot, clock ms, uniform (`None`: submits at once)."""
    mock_llm_latency_ms: int | None = None
    """Clock time the mock server waits before each answer (`None`: an external server)."""
    p_comfort_acceptable: float = 0.9
    """Bot raters' probability of comfort `acceptable` (association and
    distinguishability uniform on 1..7; #22 proposed)."""
    token_count_cap_ms: int | None = None
    """A3 token-count cap on the run clock when it differs from #16's 5 s (`None`):
    accelerated runs use the slot cap, because HTTP overhead does not speed up with the
    clock (5 s of clock time is 50 ms of real time at 100x); the slot deadline still
    bounds the count."""

    def atoms(self, config: BatchConfig) -> tuple[str, ...]:
        """The atoms of the plan's appointments, in generation order."""
        return tuple(
            config.atom_order[(k - 1) * ATOMS_PER_APPOINTMENT + i]
            for k in self.appointments
            for i in range(ATOMS_PER_APPOINTMENT)
        )


def dry_run_config(
    base: BatchConfig | None = None,
    *,
    batch_id: str = DRY_RUN_BATCH_ID,
    seed_namespace: str = DRY_RUN_SEED_NAMESPACE,
    set_ns: str = DRY_RUN_SET_NS,
    panel_id: str = DRY_RUN_PANEL_ID,
    fallback: FallbackSet | None = None,
) -> BatchConfig:
    """The synthetic batch config of the dry run.

    Profile, atom order, label permutation and books come from `base` (default: the
    committed DEMO batch config); the panel order index and the aliases are drawn from
    `set_ns` (`orchestrator.panel_order_schedule`, `panel_aliases`) and the seats are the
    three bot seats. `fallback` pins another fallback set's bank hash (whole-book run)."""
    base = base if base is not None else sim.demo_batch_config()
    order_index = panel_order_schedule(set_ns, [panel_id], profiles={panel_id: base.profile})[
        panel_id
    ]
    by_method = {b.method.value: b.book_id for b in base.books}
    methods = PANEL_ORDERS[order_index - 1]
    order = (by_method[methods[0]], by_method[methods[1]], by_method[methods[2]])
    panel = PanelAssignment(
        panel_id=panel_id,
        order_index=order_index,
        order=order,
        aliases=dict(panel_aliases(set_ns, panel_id, [b.book_id for b in base.books])),
        raters=BOT_SEATS,
    )
    config = replace(
        base,
        batch_id=batch_id,
        seed_namespace=seed_namespace,
        panel=panel,
        fallback_bank_hash=(
            base.fallback_bank_hash if fallback is None else fallback.fallback_bank_hash
        ),
    )
    return config.check().check_consistency()


def single_recipe_fallback(fallback: FallbackSet, profile: str) -> FallbackSet:
    """`fallback` with every bank entry of `profile` replaced by that bank's recipe 0
    (same waveform hashes): once a book commits it, every later scan of that book is
    exhausted (entry 0 is used, the others duplicate it). The fallback books are kept."""
    bank = fallback.bank(profile)
    first = bank[0]
    entries = tuple(
        BankEntry(first.profile, i, i, first.recipe, first.pcm_sha256, first.file_sha256)
        for i in range(len(bank))
    )
    new_bank = replace(bank, entries=entries, draws=len(entries))
    banks = tuple(new_bank if b.profile is bank.profile else b for b in fallback.banks)
    return replace(fallback, banks=banks)


def _parse_zero_eligible(spec: str, config: BatchConfig) -> tuple[str, str]:
    """`A2@7` -> (book of method A2, the 7th atom of the stored order)."""
    method, _, position = spec.partition("@")
    try:
        book = config.book_of(Method(method)).book_id
        index = int(position)
    except ValueError as err:
        raise ValueError(f"--zero-eligible takes METHOD@POSITION (A2@7), got {spec!r}") from err
    if not 1 <= index <= ATOMS_PER_BOOK:
        raise ValueError(f"atom position {index} is not 1..{ATOMS_PER_BOOK}")
    return book, config.atom_order[index - 1]


def default_zero_eligible(
    appointments: Sequence[int], *, fallback_bank: FallbackBankKind
) -> tuple[str, ...]:
    """The injected zero-eligible atoms when none are given: the third atom of the second
    appointment of the run (of its only one for a one-appointment run) in the A2 book; for
    the whole-book run, atoms 1 and 2 of the first appointment in the A3 book."""
    first = (appointments[0] - 1) * ATOMS_PER_APPOINTMENT
    if fallback_bank == "single_recipe":
        return (f"A3@{first + 1}", f"A3@{first + 2}")
    k = appointments[1] if len(appointments) > 1 else appointments[0]
    return (f"A2@{(k - 1) * ATOMS_PER_APPOINTMENT + 3}",)


def make_plan(
    config: BatchConfig,
    *,
    run_id: str,
    clock: Clock,
    appointments: Sequence[int] = (1, 2, 3, 4),
    zero_eligible: Sequence[str] | None = None,
    fallback_bank: FallbackBankKind = "demo",
    designer_think_ms: tuple[int, int] | None = DEFAULT_THINK_MS,
    mock_llm_latency_ms: int | None = DEFAULT_MOCK_LATENCY_MS,
    invalid_per_appointment: int = DESIGNER_INVALID_PER_APPOINTMENT,
    timeout_per_appointment: int = DESIGNER_TIMEOUT_PER_APPOINTMENT,
) -> DryRunPlan:
    """The dry-run plan of `config` (see `DryRunPlan`).

    `zero_eligible` holds `METHOD@POSITION` items (default `default_zero_eligible`); with
    the single-recipe bank the first item of a book expects `bank`, every later one
    `book`, otherwise every item expects `bank`. Designer slots are drawn from
    `rng_for(bot_seed_key(run_id, "driver", "inject", appointment))`: per appointment
    `timeout_per_appointment` skipped slots in distinct rounds (so no A1 window can reach
    120 s) and `invalid_per_appointment` invalid slots."""
    apps = tuple(sorted(set(appointments)))
    if not apps or any(not 1 <= k <= APPOINTMENTS_PER_BATCH for k in apps):
        raise ValueError(f"appointments must be in 1..{APPOINTMENTS_PER_BATCH}, got {apps}")
    specs = (
        tuple(zero_eligible)
        if zero_eligible is not None
        else default_zero_eligible(apps, fallback_bank=fallback_bank)
    )
    scope = {
        config.atom_order[(k - 1) * ATOMS_PER_APPOINTMENT + i]
        for k in apps
        for i in range(ATOMS_PER_APPOINTMENT)
    }
    injected: list[InjectedFallback] = []
    seen_books: set[str] = set()
    for spec in specs:
        book, atom = _parse_zero_eligible(spec, config)
        if atom not in scope:
            raise ValueError(f"{spec}: atom {atom} is not in appointments {apps}")
        expect: Literal["bank", "book"] = (
            "book" if fallback_bank == "single_recipe" and book in seen_books else "bank"
        )
        seen_books.add(book)
        injected.append(InjectedFallback(book, atom, expect))
    if len({(z.book_id, z.atom_id) for z in injected}) != len(injected):
        raise ValueError("a zero-eligible atom is given twice")
    forced = tuple(
        sorted(s for z in injected for s in config.rating_slot_ids(z.book_id, z.atom_id))
    )
    a1 = config.book_of(Method.A1).book_id
    invalid: list[str] = []
    timeouts: list[str] = []
    for k in apps:
        rng = rng_for(bot_seed_key(run_id, "driver", "inject", k))
        atoms = config.atom_order[(k - 1) * ATOMS_PER_APPOINTMENT : k * ATOMS_PER_APPOINTMENT]
        cells = [(a, r) for a in atoms for r in range(1, ROUNDS_PER_ATOM + 1)]
        picks = rng.permutation(len(cells))
        for i in picks[:timeout_per_appointment]:
            atom, round_ = cells[int(i)]
            slot = int(rng.integers(1, SLOTS_PER_ROUND + 1))
            timeouts.append(proposal_slot_id(a1, atom, round_, slot))
        taken = set(timeouts)
        slots_all = [
            proposal_slot_id(a1, a, r, s)
            for a, r in cells
            for s in range(1, SLOTS_PER_ROUND + 1)
            if proposal_slot_id(a1, a, r, s) not in taken
        ]
        for i in rng.permutation(len(slots_all))[:invalid_per_appointment]:
            invalid.append(slots_all[int(i)])
    speed = getattr(clock, "speed", None)
    return DryRunPlan(
        run_id=run_id,
        batch_id=config.batch_id,
        clock="scaled" if isinstance(clock, ScaledClock) else "real",
        clock_speed=float(speed) if isinstance(clock, ScaledClock) and speed else None,
        zero_eligible=tuple(injected),
        force_unacceptable_slots=forced,
        designer_invalid_slots=tuple(sorted(invalid)),
        designer_timeout_slots=tuple(sorted(timeouts)),
        appointments=apps,
        fallback_bank=fallback_bank,
        designer_think_ms=designer_think_ms,
        mock_llm_latency_ms=mock_llm_latency_ms,
        token_count_cap_ms=SLOT_CAP_MS if isinstance(clock, ScaledClock) else None,
    ).check()


# ---------------------------------------------------------------------------
# The bot designer with think time


class ThinkingBotDesigner(sim.BotDesigner):
    """`_batch_sim.BotDesigner` (#20: works the real A1 app over HTTP, scripted invalid and
    skipped slots) that waits a seeded think time on the run clock before each submit:
    `rng_for(bot_seed_key(run_id, designer_id, "think", slot_id))`, uniform on
    `think_ms`, so the A1 proposal window lasts as long as a designer's would."""

    def __init__(
        self,
        page_url: str,
        *,
        run_id: str,
        designer_id: str,
        clock: Clock,
        think_ms: tuple[int, int] | None,
        invalid_slots: frozenset[str] = frozenset(),
        timeout_slots: frozenset[str] = frozenset(),
    ) -> None:
        super().__init__(
            page_url,
            run_id=run_id,
            designer_id=designer_id,
            invalid_slots=invalid_slots,
            timeout_slots=timeout_slots,
        )
        self._clock = clock
        self._think_ms = think_ms
        self._speed = float(getattr(clock, "speed", 1.0))
        self.think_total_ms = 0

    def think_ms(self, slot_id: str) -> int:
        """The think time before submitting `slot_id` (clock ms)."""
        if self._think_ms is None:
            return 0
        low, high = self._think_ms
        rng = rng_for(bot_seed_key(self._run_id, self._designer_id, "think", slot_id))
        return int(rng.integers(low, high + 1))

    def _submit(self, http: httpx.Client, slot_id: str) -> None:
        delay = 0 if slot_id in self._invalid else self.think_ms(slot_id)
        target = self._clock.now_ms() + delay
        while (remaining := target - self._clock.now_ms()) > 0:
            if self._stop.wait(min(0.2, remaining / 1000 / self._speed)):
                return
        self.think_total_ms += delay
        super()._submit(http, slot_id)


# ---------------------------------------------------------------------------
# Startup: model and renderer preload


@dataclass
class _Startup:
    """Startup intervals timed before the run directory exists (logged after)."""

    events: list[tuple[str, int, str, str | None, int | None]] = field(default_factory=list)
    """(event, t_ms, wall_utc, detail, duration_ms) per component, in order."""
    components: list[str] = field(default_factory=list)

    @contextmanager
    def timed(self, clock: Clock, component: str, detail: str) -> Iterator[None]:
        start = clock.now_ms()
        self.events.append(("startup_start", start, utc_text(clock.utc_now()), detail, None))
        self.components.append(component)
        yield
        end = clock.now_ms()
        self.events.append(("startup_end", end, utc_text(clock.utc_now()), detail, end - start))
        self.components.append(component)

    def write(self, layout: RunLayout, batch_id: str) -> dict[str, int]:
        writer = RecordWriter(layout.log("timing"))
        durations: dict[str, int] = {}
        for (event, t_ms, wall, detail, duration), component in zip(
            self.events, self.components, strict=True
        ):
            writer.append(
                TimingEvent(
                    run_id=layout.run_id,
                    event=event,
                    t_ms=t_ms,
                    wall_utc=wall,
                    batch_id=batch_id,
                    component=component,
                    duration_ms=duration,
                    detail=detail,
                )
            )
            if duration is not None:
                durations[component] = durations.get(component, 0) + duration
        return durations


def _warm_up_llm(url: str, model: str, clock: Clock, timeout_s: float = 600.0) -> None:
    """Wait for the server's `GET /health`, then one warm-up call (not logged in the run)."""
    deadline = time.monotonic() + timeout_s
    with httpx.Client(base_url=url, trust_env=False, timeout=5.0) as http:
        while True:
            try:
                if http.get("/health").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                raise br.RunnerError(br.E_LLM_SERVER, f"{url} is not healthy")
            time.sleep(0.2)
    client = OpenAICompatibleClient(url, model, run_id="DEMO-warm-up", clock=clock)
    client.propose(
        [{"role": "user", "content": "Warm-up call before the dry run."}],
        decoding_schema(),
        a3_seed_key("DEMO-WARMUP", "K-a1", 1, 1),
    )


def _warm_up_renderer(fallback: FallbackSet, profile: str) -> None:
    """Render and validate one recipe (the profile's bank recipe 0) with no references."""
    entry = fallback.bank(profile)[0]
    result = validate(entry.recipe, profile, ())
    if result.pcm_sha256 != entry.pcm_sha256:
        raise RuntimeError("renderer self-test: the bank recipe renders to another waveform")


# ---------------------------------------------------------------------------
# The driver


@dataclass
class SessionResult:
    """What one appointment's station session did (bots and designer)."""

    appointment: int
    stations: dict[str, BotRunResult]
    designer_submitted: dict[str, str]
    designer_late: list[str]
    designer_plays: int
    designer_think_ms: int
    lines: list[str]
    real_s: float


@dataclass
class DryRunResult:
    """A finished dry run: its layout, plan and per-session results."""

    layout: RunLayout
    plan: DryRunPlan
    sessions: list[SessionResult]
    startup_ms: dict[str, int]
    llm: str
    """`mock` or the external server's runtime label."""
    elapsed_real_s: float


def _inputs(config: BatchConfig, fallback: FallbackSet, *, base: br.BatchInputs) -> br.BatchInputs:
    """The dry run's inputs: the DEMO meanings, prompt set and LLM manifest of `base` with
    the dry-run batch config and fallback set, and a DEMO generation config for them."""
    gen = br.demo_generation_config(
        config,
        base.meanings,
        fallback,
        prompt_set=base.prompt_set,
        llm_manifest_sha256=base.llm_manifest_sha256,
    )
    return replace(base, config=config, fallback=fallback, generation_config=gen)


def _session(
    batch: br.StudyBatch,
    plan: DryRunPlan,
    appointment: int,
    *,
    station_timeout_s: float,
    log: Callable[[str], None],
) -> SessionResult:
    """One appointment over keyed bot stations and the bot designer (see the module
    docstring); returns what the bots and the designer did."""
    run_id = batch.layout.run_id
    policy = BotRatingPolicy(
        p_comfort_acceptable=plan.p_comfort_acceptable,
        force_unacceptable_slots=frozenset(plan.force_unacceptable_slots),
    )
    a1 = batch.a1
    assert a1 is not None, "the dry run uses the real A1 service"
    page: list[str] = []
    designer: list[ThinkingBotDesigner] = []
    results: dict[str, BotRunResult] = {}
    errors: list[BaseException] = []
    threads: list[threading.Thread] = []
    lines: list[str] = []

    def stop_designer() -> None:
        for bot in designer:
            bot.stop()

    def capture(line: str) -> None:
        lines.append(line)
        log(line)
        if line.startswith("A1 designer page: "):
            page.append(line.removeprefix("A1 designer page: ").strip())
        if line == f"Appointment {appointment}: done":
            stop_designer()  # before the A1 server stops with the session

    def run_bot(bot: BotRater) -> None:
        try:
            results[bot.station] = bot.run()
        except BaseException as err:  # noqa: BLE001 - reported after the session
            errors.append(err)

    def on_panel(_base_url: str) -> None:
        if not page:
            raise br.RunnerError(br.E_MODE, "the A1 page was not served")
        bot_designer = ThinkingBotDesigner(
            page[0],
            run_id=run_id,
            designer_id=a1.designer_id,
            clock=batch.clock,
            think_ms=plan.designer_think_ms,
            invalid_slots=frozenset(plan.designer_invalid_slots),
            timeout_slots=frozenset(plan.designer_timeout_slots),
        )
        designer.append(bot_designer)
        bot_designer.start()
        for url in br.station_urls(batch).values():
            bot = BotRater.from_station_url(url, run_id=run_id, policy=policy, clock=batch.clock)
            thread = threading.Thread(target=run_bot, args=(bot,), daemon=True)
            threads.append(thread)
            thread.start()

    started = time.monotonic()
    try:
        br.run_session(
            batch,
            appointment=appointment,
            panel="stations",
            designer="kiosk",
            a1_port=0,
            panel_port=0,
            station_timeout_s=station_timeout_s,
            on_panel=on_panel,
            log=capture,
        )
    finally:
        stop_designer()
        for thread in threads:
            thread.join(timeout=60)
    if errors:
        raise RuntimeError(f"a bot station failed: {errors[0]!r}") from errors[0]
    bot = designer[0] if designer else None
    return SessionResult(
        appointment=appointment,
        stations=dict(sorted(results.items())),
        designer_submitted={} if bot is None else dict(sorted(bot.submitted.items())),
        designer_late=[] if bot is None else sorted(bot.late),
        designer_plays=0 if bot is None else bot.plays,
        designer_think_ms=0 if bot is None else bot.think_total_ms,
        lines=lines,
        real_s=round(time.monotonic() - started, 1),
    )


def run_dry_run(
    out: str | os.PathLike[str],
    run_id: str,
    *,
    clock: Clock,
    appointments: Sequence[int] = (1, 2, 3, 4),
    fallback_bank: FallbackBankKind = "demo",
    zero_eligible: Sequence[str] | None = None,
    llm_url: str | None = None,
    mock_latency_ms: int = DEFAULT_MOCK_LATENCY_MS,
    designer_think_ms: tuple[int, int] | None = DEFAULT_THINK_MS,
    station_timeout_s: float = 120.0,
    resume: bool = False,
    log: Callable[[str], None] = br._stdout,
) -> DryRunResult:
    """Run the dry run into `out/run_id` (a new `synthetic` run, purpose `dry_run`).

    `llm_url=None` starts #16's mock server in this process (its answers wait
    `mock_latency_ms` of clock time); otherwise the server at `llm_url` must pass the
    runner's probe (the pinned vLLM, or the mock). `resume=True` reopens a started dry
    run after an interruption: its stored plan is used (the other arguments that shape
    the plan are ignored), the startup is timed again for this process and the
    appointments not yet finished run. See the module docstring."""
    started = time.monotonic()
    base = br.load_batch_inputs(
        config=examples_path("demo-batch-config.json"),
        meanings=examples_path("demo-meanings"),
        fallback=sound_root() / sim.DEMO_FALLBACK_MANIFEST,
        proposers="real",
    )
    run_dir = Path(out) / run_id
    stored: DryRunPlan | None = None
    if resume:
        stored = DryRunPlan.read(run_dir / DRY_RUN_PLAN_NAME)
        fallback_bank = stored.fallback_bank
        if stored.clock != ("scaled" if isinstance(clock, ScaledClock) else "real"):
            raise br.RunnerError(br.E_MODE, f"the dry run started with a {stored.clock} clock")
    fallback = base.fallback
    if fallback_bank == "single_recipe":
        fallback = single_recipe_fallback(fallback, base.config.profile.value)
    config = dry_run_config(base.config, fallback=fallback)
    if fallback_bank == "single_recipe":  # pin the in-memory set, not the DEMO manifest
        config = replace(
            config,
            fallback_manifest_sha256=hashlib.sha256(
                json.dumps(fallback.manifest(), sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        )
    inputs = _inputs(config, fallback, base=base)
    assert inputs.llm_manifest is not None
    plan = stored or make_plan(
        config,
        run_id=run_id,
        clock=clock,
        appointments=appointments,
        zero_eligible=zero_eligible,
        fallback_bank=fallback_bank,
        designer_think_ms=designer_think_ms,
        mock_llm_latency_ms=mock_latency_ms if llm_url is None else None,
    )
    startup = _Startup()
    sessions: list[SessionResult] = []
    with ExitStack() as stack:
        model = inputs.llm_manifest.model.id
        if llm_url is None:
            speed = float(getattr(clock, "speed", 1.0))
            latency_s = (plan.mock_llm_latency_ms or 0) / 1000 / speed
            server = MockLlmServer(default=MockReply(delay_s=latency_s))
            with startup.timed(clock, "llm", "mock server start (no model weights), warm-up call"):
                url = stack.enter_context(serve_in_thread(server.app))
                _warm_up_llm(url, model, clock)
            label = "mock"
        else:
            url = llm_url
            with startup.timed(clock, "llm", "external server health and warm-up call"):
                _warm_up_llm(url, model, clock)
            label = br.probe_llm_server(url, inputs.llm_manifest, kind=RunKind.SYNTHETIC)
        with startup.timed(clock, "renderer", "render and validate one recipe"):
            _warm_up_renderer(fallback, config.profile.value)

        def open_run(reopen: bool) -> br.StudyBatch:
            batch = br.open_batch(
                inputs,
                run_dir,
                kind=RunKind.SYNTHETIC,
                clock=clock,
                proposers="real",
                llm_url=url,
                resume=reopen,
                purpose="dry_run",
            )
            if batch.llm is not None and plan.token_count_cap_ms is not None:
                batch.llm.count_timeout_ms = plan.token_count_cap_ms
            return batch

        batch = open_run(resume)
        startup_ms = startup.write(batch.layout, config.batch_id)
        if not resume:
            plan.write(batch.layout.dry_run_plan)
        nxt = batch.orchestrator.next_atom()
        first = 0 if nxt is None else config.atom_order.index(nxt) // ATOMS_PER_APPOINTMENT + 1
        todo = [k for k in plan.appointments if nxt is not None and k >= first]
        for i, appointment in enumerate(todo):
            if i > 0:
                batch = open_run(True)
            sessions.append(
                _session(batch, plan, appointment, station_timeout_s=station_timeout_s, log=log)
            )
    return DryRunResult(
        layout=batch.layout,
        plan=plan,
        sessions=sessions,
        startup_ms=startup_ms,
        llm=label,
        elapsed_real_s=round(time.monotonic() - started, 1),
    )


# ---------------------------------------------------------------------------
# Log completeness


@dataclass(frozen=True, slots=True)
class CompletenessReport:
    """Output of the automated log-completeness check (#22)."""

    ok: bool
    problems: tuple[str, ...]
    counts: dict[str, int]

    def text(self) -> str:
        """The report as text (one `name value` line per count, then the problems)."""
        lines = [f"log completeness: {'OK' if self.ok else 'FAILED'}"]
        lines += [f"  {name} {value}" for name, value in sorted(self.counts.items())]
        lines += [f"problem: {p}" for p in self.problems]
        return "\n".join(lines) + "\n"

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "problems": list(self.problems), "counts": dict(self.counts)}


def _final_store_books(config: BatchConfig, timing: Sequence[TimingEvent]) -> dict[str, str]:
    finals = {b.book_id: b.book_id for b in config.books}
    for event in timing:
        if event.event == "book_substituted" and event.book_id in finals:
            detail = event.detail or ""
            store_id = detail.removeprefix("store_book_id=") if detail else ""
            finals[event.book_id] = store_id or f"{event.book_id}-FB"
    return finals


def check_log_completeness(
    run_dir: str | os.PathLike[str], *, plan: DryRunPlan | None = None
) -> CompletenessReport:
    """One commit per atom in each book's final store book (48), 576 slot records, 576
    rating records per rater, 0 message plays, and fallback events equal to the injected
    ones (`plan`, else `<run_dir>/dry-run-plan.json`) (#22).

    Counts scale with the plan's appointments (a one-appointment run: 4 atoms, 144 slot
    and rating records); a book substituted by its fallback book holds 16 commits in its
    final store book whatever the scope. Reads the run directory only and changes
    nothing; every problem is listed, none raises."""
    root = Path(run_dir)
    layout = RunLayout(root, root.name)
    plan = plan if plan is not None else DryRunPlan.read(layout.dry_run_plan)
    config = BatchConfig.read(layout.config)
    manifest = RunManifest.read(layout.manifest)
    problems: list[str] = []
    counts: dict[str, int] = {}

    def need(condition: bool, text: str) -> None:
        if not condition:
            problems.append(text)

    atoms = plan.atoms(config)
    n_atoms = len(atoms)
    methods = {b.book_id: b.method for b in config.books}
    need(plan.run_id == layout.run_id == manifest.run_id, "run IDs of plan, directory and manifest")
    need(plan.batch_id == config.batch_id, "the plan names another batch")
    need(manifest.purpose == "dry_run", f"run purpose is {manifest.purpose}, not dry_run")
    need(manifest.kind in (RunKind.SYNTHETIC, RunKind.DEMO), f"run kind {manifest.kind}")
    expected_forced = sorted(
        s for z in plan.zero_eligible for s in config.rating_slot_ids(z.book_id, z.atom_id)
    )
    need(
        sorted(plan.force_unacceptable_slots) == expected_forced,
        "force_unacceptable_slots are not the rating slots of the zero-eligible atoms",
    )
    for z in plan.zero_eligible:
        need(z.atom_id in atoms, f"zero-eligible {z.book_id} {z.atom_id} is outside the run")

    # -- slots --------------------------------------------------------------
    slots = read_records(layout.log("slot"), SlotRecord)
    counts["slot_records"] = len(slots)
    need(len(slots) == n_atoms * SLOTS_PER_ATOM * 3, f"{len(slots)} slot records")
    per_method = Counter(s.method.value for s in slots)
    for method in (Method.A1, Method.A2, Method.A3):
        n = per_method.get(method.value, 0)
        counts[f"slot_records_{method.value}"] = n
        need(n == n_atoms * SLOTS_PER_ATOM, f"{n} slot records of {method.value}")
    counts["slots_without_status"] = sum(1 for s in slots if s.outcome.value not in OUTCOME_CODES)
    need(counts["slots_without_status"] == 0, "slot records without an outcome")
    by_cell: dict[tuple[str, str], list[SlotRecord]] = defaultdict(list)
    for s in slots:
        if s.book_id is None or s.atom_id not in atoms or methods.get(s.book_id) is not s.method:
            problems.append(f"slot {s.slot_id}: not a slot of this run's books and atoms")
            continue
        by_cell[(s.book_id, s.atom_id)].append(s)
    replenished = 0
    for book_id in methods:
        for atom in atoms:
            cell = by_cell.get((book_id, atom), [])
            indexes = sorted(s.slot_index for s in cell if s.slot_index is not None)
            replenished += max(0, len(cell) - SLOTS_PER_ATOM)
            if indexes != list(range(1, SLOTS_PER_ATOM + 1)):
                problems.append(f"{book_id} {atom}: slot indexes {indexes}, not 1..12 once each")
    counts["slots_replenished"] = replenished
    need(replenished == 0, f"{replenished} replenished slots")
    counts["slot_ids_repeated"] = len(slots) - len({s.slot_id for s in slots})
    need(counts["slot_ids_repeated"] == 0, f"{counts['slot_ids_repeated']} repeated slot IDs")
    outcome_of = {s.slot_id: s.outcome.value for s in slots}
    for slot_id in plan.designer_invalid_slots:
        outcome = outcome_of.get(slot_id)
        need(outcome == "invalid_json", f"designer invalid slot {slot_id}: outcome {outcome}")
    for slot_id in plan.designer_timeout_slots:
        outcome = outcome_of.get(slot_id)
        need(outcome == "timeout", f"designer timeout slot {slot_id}: outcome {outcome}")
    counts["designer_invalid_injected"] = len(plan.designer_invalid_slots)
    counts["designer_timeout_injected"] = len(plan.designer_timeout_slots)
    refusals_path = layout.log("slot_refusal")
    refusals = read_records(refusals_path, SlotRefusal) if refusals_path.exists() else []
    for reason, n in sorted(Counter(r.reason for r in refusals).items()):
        counts[f"slot_refusals_{reason}"] = n

    # -- ratings --------------------------------------------------------------
    ratings = read_records(layout.log("rating"), RatingRecord)
    expected_rsids = {
        rating_slot_id(config.batch_id, atom, round_, position)
        for atom in atoms
        for round_ in range(1, ROUNDS_PER_ATOM + 1)
        for position in range(1, RATING_SLOTS_PER_ROUND + 1)
    }
    for seat in config.panel.raters:
        mine = [r for r in ratings if r.rater_id == seat.rater_id]
        counts[f"rating_records_{seat.rater_id}"] = len(mine)
        need(
            len(mine) == n_atoms * SLOTS_PER_ATOM * 3,
            f"rater {seat.rater_id}: {len(mine)} rating records",
        )
        rsids = [r.rating_slot_id for r in mine]
        need(len(set(rsids)) == len(rsids), f"rater {seat.rater_id}: a rating slot twice")
        need(set(rsids) == expected_rsids, f"rater {seat.rater_id}: rating slots differ")
        need(
            all(r.rater_kind == "bot" and r.station == seat.station for r in mine),
            f"rater {seat.rater_id}: a record of another kind or station",
        )
    counts["rating_records_other"] = sum(
        1 for r in ratings if r.rater_id not in {s.rater_id for s in config.panel.raters}
    )
    need(counts["rating_records_other"] == 0, "rating records of unknown raters")
    placeholders = [r for r in ratings if r.placeholder]
    counts["rating_placeholders"] = len(placeholders)
    counts["rating_missing"] = sum(1 for r in ratings if r.missing)
    for r in ratings:
        valid = outcome_of.get(r.slot_id) == "valid"
        if r.placeholder == valid:
            problems.append(f"rating {r.rating_slot_id} {r.rater_id}: placeholder={r.placeholder}")

    # -- decisions ------------------------------------------------------------
    decisions = read_records(layout.log("decision"), DecisionRecord)
    counts["decision_records"] = len(decisions)
    need(len(decisions) == n_atoms * ROUNDS_PER_ATOM * 3, f"{len(decisions)} decision records")
    finals = {(d.book_id, d.atom_id): d for d in decisions if d.final}
    need(len(finals) == n_atoms * 3, f"{len(finals)} final decisions")

    # -- commits and the vocabulary store -----------------------------------
    timing = read_records(layout.log("timing"), TimingEvent)
    commits = read_records(layout.log("commit"), CommitRecord)
    final_store = _final_store_books(config, timing)
    counts["commit_records"] = len(commits)
    store = VocabularyStore(layout.store_dir)
    n_final = 0
    n_store_ok = 0
    for book_id, store_id in sorted(final_store.items()):
        kept = [c for c in commits if c.book_id == book_id and c.store_book_id == store_id]
        substituted = store_id != book_id
        expected = ATOMS_PER_BOOK if substituted else n_atoms
        n_final += len(kept)
        counts[f"commits_final_{book_id}"] = len(kept)
        need(len(kept) == expected, f"{book_id}: {len(kept)} commits in {store_id}")
        want = set(config.atom_order) if substituted else set(atoms)
        need(
            {c.atom_id for c in kept} == want and len(kept) == len(want),
            f"{book_id}: committed atoms differ from the run's atoms",
        )
        heads = [c.chain_head for c in kept]
        report = store.verify(store_id, rerender=False, expected_head=heads[-1] if heads else None)
        need(report.ok, f"store book {store_id}: verify {report.codes}")
        for c in kept:
            entry = store.get(store_id, c.atom_id)
            same = (
                entry.pcm_sha256 == c.pcm_sha256
                and entry.file_sha256 == c.file_sha256
                and hashlib.sha256(entry.pcm).hexdigest() == c.pcm_sha256
            )
            n_store_ok += same
            need(same, f"commit {store_id} {c.atom_id}: store waveform hash differs")
    counts["commits_final"] = n_final
    counts["commits_with_store_sha256"] = n_store_ok
    expected_final = sum(ATOMS_PER_BOOK if final_store[b] != b else n_atoms for b in methods)
    need(n_final == expected_final, f"{n_final} commits in final store books")

    # -- plays ------------------------------------------------------------------
    plays = read_records(layout.log("play"), PlayEvent)
    counts["play_records"] = len(plays)
    counts["message_plays"] = sum(1 for p in plays if p.audio_kind == "message")
    counts["non_atom_plays"] = sum(1 for p in plays if p.audio_kind != "atom")
    need(counts["message_plays"] == 0, f"{counts['message_plays']} complete-message plays")
    need(counts["non_atom_plays"] == 0, "plays of non-atomic audio")

    # -- fallback events against the injected cases ---------------------------
    scans_path = layout.log("fallback_scan")
    scans = read_records(scans_path, FallbackScanRecord) if scans_path.exists() else []
    bank_expected = {(z.book_id, z.atom_id) for z in plan.zero_eligible if z.expect == "bank"}
    book_expected = {(z.book_id, z.atom_id) for z in plan.zero_eligible if z.expect == "book"}
    scanned = [(s.book_id, s.atom_id) for s in scans]
    scan_actions = {k for k, d in finals.items() if d.action == "fallback_scan"}
    bank_commits = {(c.book_id, c.atom_id): c for c in commits if c.source == "fallback_bank"}
    substitutions = [(e.book_id, e.atom_id) for e in timing if e.event == "book_substituted"]
    counts["fallback_injected"] = len(plan.zero_eligible)
    counts["fallback_scans"] = len(scans)
    counts["fallback_bank_commits"] = len(bank_commits)
    counts["book_substitutions"] = len(substitutions)
    need(
        sorted(scanned) == sorted(bank_expected | book_expected)
        and len(set(scanned)) == len(scanned),
        f"fallback scans {sorted(scanned)} differ from the injected cases",
    )
    need(
        scan_actions == bank_expected | book_expected,
        f"fallback_scan decisions {sorted(scan_actions)} differ from the injected cases",
    )
    need(
        set(bank_commits) == bank_expected,
        f"fallback_bank commits {sorted(bank_commits)} differ from the injected cases",
    )
    need(
        sorted(k for k in substitutions if k[0] is not None) == sorted(book_expected),
        f"book substitutions {sorted(substitutions)} differ from the injected cases",
    )
    by_scan = {(s.book_id, s.atom_id): s.scan for s in scans}
    for key in bank_expected:
        scan, commit = by_scan.get(key), bank_commits.get(key)
        need(
            scan is not None
            and commit is not None
            and scan.get("outcome") == "selected"
            and scan.get("selected_index") == commit.bank_index,
            f"bank fallback {key}: scan and commit disagree",
        )
    for book_id, atom in book_expected:
        scan = by_scan.get((book_id, atom))
        need(
            scan is not None and scan.get("outcome") == "exhausted",
            f"book fallback {book_id} {atom}: scan is not exhausted",
        )
        fb = [c for c in commits if c.book_id == book_id and c.source == "fallback_book"]
        need(
            len(fb) == ATOMS_PER_BOOK and all(c.failed_generation for c in fb),
            f"book fallback {book_id}: {len(fb)} fallback_book commits",
        )
    substituted_books = {b for b, _ in book_expected}
    others = [
        c for c in commits if c.source == "fallback_book" and c.book_id not in substituted_books
    ]
    need(not others, "fallback_book commits of a book without an injected book fallback")

    # -- timing events --------------------------------------------------------
    events = Counter(e.event for e in timing)
    for name in ("atom_start", "atom_end"):
        n = sum(1 for e in timing if e.event == name and e.atom_id in atoms)
        need(n >= n_atoms, f"{n} {name} events")
    for name in ("round_start", "round_end"):
        keys = {(e.atom_id, e.round) for e in timing if e.event == name}
        need(len(keys) == n_atoms * ROUNDS_PER_ATOM, f"{len(keys)} rounds with {name}")
    for name in ("appointment_start", "appointment_end"):
        logged = {e.appointment for e in timing if e.event == name}
        need(set(plan.appointments) <= logged, f"{name} missing for an appointment")
    for component in STARTUP_COMPONENTS:
        for name in ("startup_start", "startup_end"):
            need(
                any(e.event == name and e.component == component for e in timing),
                f"no {name} event of {component}",
            )
    counts["timing_events"] = len(timing)
    counts["startup_events"] = events["startup_start"] + events["startup_end"]

    # -- run manifest -----------------------------------------------------------
    full = n_atoms == ATOMS_PER_BOOK
    counts["run_closed"] = int(manifest.closed_utc is not None)
    need((manifest.closed_utc is not None) == full, "manifest closed state does not fit the scope")
    if manifest.closed_utc is not None and manifest.files is not None:
        changed = [
            rel
            for rel, digest in sorted(manifest.files.items())
            if not (root / rel).is_file() or _file_sha256(root / rel) != digest
        ]
        counts["manifest_files"] = len(manifest.files)
        need(not changed, f"{len(changed)} run files differ from the closed manifest")
    return CompletenessReport(ok=not problems, problems=tuple(problems), counts=dict(counts))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Timing per round, atom and appointment

TIMING_COLUMNS: Final[tuple[str, ...]] = (
    "level",
    "component",
    "appointment",
    "atom_position",
    "atom_id",
    "round",
    "start_ms",
    "end_ms",
    "duration_ms",
    "proposal_window_ms",
    "rating_window_ms",
    "budget_ms",
    "within_budget",
)


@dataclass(frozen=True, slots=True)
class TimingRow:
    """One interval of `timing.csv` (clock ms of the run)."""

    level: Literal["startup", "appointment", "atom", "round"]
    component: str | None
    appointment: int | None
    atom_position: int | None
    atom_id: str | None
    round: int | None
    start_ms: int
    end_ms: int
    duration_ms: int
    proposal_window_ms: int | None = None
    rating_window_ms: int | None = None
    budget_ms: int | None = None

    @property
    def within_budget(self) -> bool | None:
        return None if self.budget_ms is None else self.duration_ms <= self.budget_ms

    def cells(self) -> list[str]:
        values: list[object] = [
            self.level,
            self.component,
            self.appointment,
            self.atom_position,
            self.atom_id,
            self.round,
            self.start_ms,
            self.end_ms,
            self.duration_ms,
            self.proposal_window_ms,
            self.rating_window_ms,
            self.budget_ms,
            self.within_budget,
        ]
        return ["" if v is None else str(int(v)) if isinstance(v, bool) else str(v) for v in values]


@dataclass(frozen=True, slots=True)
class TimingSummary:
    """Maxima and limits of a run's timing (`timing-summary.json`)."""

    clock: str
    clock_speed: float | None
    rounds: int
    atoms: int
    appointments: int
    max_round_ms: int | None
    max_round: str | None
    max_atom_ms: int | None
    max_atom: str | None
    max_appointment_ms: int | None
    max_appointment: int | None
    max_proposal_window_ms: int | None
    mean_round_ms: int | None
    mean_atom_ms: int | None
    startup_ms: dict[str, int]
    atom_limit_ms: int = ATOM_BUDGET_MS
    appointment_limit_ms: int = APPOINTMENT_BUDGET_MS
    booking_ms: int = APPOINTMENT_BOOKING_MS

    @property
    def atom_ok(self) -> bool:
        return self.max_atom_ms is not None and self.max_atom_ms <= self.atom_limit_ms

    @property
    def appointment_ok(self) -> bool:
        return (
            self.max_appointment_ms is not None
            and self.max_appointment_ms <= self.appointment_limit_ms
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "clock": self.clock,
            "clock_speed": self.clock_speed,
            "rounds": self.rounds,
            "atoms": self.atoms,
            "appointments": self.appointments,
            "max_round_ms": self.max_round_ms,
            "max_round": self.max_round,
            "max_atom_ms": self.max_atom_ms,
            "max_atom": self.max_atom,
            "max_appointment_ms": self.max_appointment_ms,
            "max_appointment": self.max_appointment,
            "max_proposal_window_ms": self.max_proposal_window_ms,
            "mean_round_ms": self.mean_round_ms,
            "mean_atom_ms": self.mean_atom_ms,
            "startup_ms": dict(sorted(self.startup_ms.items())),
            "atom_limit_ms": self.atom_limit_ms,
            "appointment_limit_ms": self.appointment_limit_ms,
            "appointment_booking_ms": self.booking_ms,
            "atom_ok": self.atom_ok,
            "appointment_ok": self.appointment_ok,
            "appointment_within_booking": (
                self.max_appointment_ms is not None and self.max_appointment_ms <= self.booking_ms
            ),
        }


def _origin(event: TimingEvent) -> float | None:
    """Clock origin of an event (`wall_utc - t_ms`, seconds): one value per process."""
    if event.wall_utc is None:
        return None
    wall = datetime.strptime(event.wall_utc, "%Y-%m-%dT%H:%M:%S.%fZ")
    return wall.timestamp() - event.t_ms / 1000


def _span(start: TimingEvent, end: TimingEvent) -> int:
    """Duration between two events: the end's `duration_ms` when given, `t_ms` on one run
    clock, else the `wall_utc` difference (events of two processes)."""
    if end.duration_ms is not None:
        return end.duration_ms
    o1, o2 = _origin(start), _origin(end)
    if o1 is None or o2 is None or abs(o1 - o2) < 0.002:
        return end.t_ms - start.t_ms
    w1 = datetime.strptime(str(start.wall_utc), "%Y-%m-%dT%H:%M:%S.%fZ")
    w2 = datetime.strptime(str(end.wall_utc), "%Y-%m-%dT%H:%M:%S.%fZ")
    return round((w2 - w1).total_seconds() * 1000)


_PAIRS: Final[Mapping[str, tuple[str, str]]] = {
    "startup": ("startup_start", "startup_end"),
    "appointment": ("appointment_start", "appointment_end"),
    "atom": ("atom_start", "atom_end"),
    "round": ("round_start", "round_end"),
    "proposal": ("proposal_window_start", "proposal_window_end"),
    "rating": ("rating_window_start", "rating_window_end"),
}


def _intervals(timing: Sequence[TimingEvent]) -> dict[tuple[Any, ...], tuple[TimingEvent, int]]:
    """(kind, key...) -> (start event, duration) for every paired start/end; a start
    logged again while its interval is open (a resumed atom) keeps the first start."""
    start_of = {start: kind for kind, (start, _) in _PAIRS.items()}
    end_of = {end: kind for kind, (_, end) in _PAIRS.items()}
    open_: dict[tuple[Any, ...], TimingEvent] = {}
    done: dict[tuple[Any, ...], tuple[TimingEvent, int]] = {}
    seq: Counter[tuple[Any, ...]] = Counter()
    for event in timing:
        kind = start_of.get(event.event) or end_of.get(event.event)
        if kind is None:
            continue
        if kind == "startup":
            base: tuple[Any, ...] = (kind, event.component)
        elif kind == "appointment":
            base = (kind, event.appointment)
        elif kind == "atom":
            base = (kind, event.atom_id)
        else:
            base = (kind, event.atom_id, event.round)
        key = (*base, seq[base])
        if event.event in start_of:
            open_.setdefault(key, event)
        elif key in open_:
            start = open_.pop(key)
            done[key] = (start, _span(start, event))
            seq[base] += 1
    return done


def timing_rows(run_dir: str | os.PathLike[str]) -> tuple[list[TimingRow], TimingSummary]:
    """`timing.csv` rows (startup, then per appointment its row, and per atom its row and
    its four rounds) and the summary with the 20-min and 80-min checks."""
    root = Path(run_dir)
    layout = RunLayout(root, root.name)
    config = BatchConfig.read(layout.config)
    manifest = RunManifest.read(layout.manifest)
    timing = read_records(layout.log("timing"), TimingEvent)
    done = _intervals(timing)
    rows: list[TimingRow] = []
    startup_ms: dict[str, int] = {}
    for key, (start, duration) in done.items():
        if key[0] == "startup":
            component = str(key[1])
            startup_ms[component] = startup_ms.get(component, 0) + duration
            rows.append(
                TimingRow(
                    "startup",
                    component,
                    None,
                    None,
                    None,
                    None,
                    start.t_ms,
                    start.t_ms + duration,
                    duration,
                )
            )

    def found(kind: str, *parts: Any) -> list[tuple[TimingEvent, int]]:  # noqa: ANN401
        return [v for k, v in done.items() if k[0] == kind and k[1 : 1 + len(parts)] == parts]

    max_round: tuple[int, str] | None = None
    max_atom: tuple[int, str] | None = None
    max_app: tuple[int, int] | None = None
    max_window = 0
    round_ms: list[int] = []
    atom_ms: list[int] = []
    n_apps = 0
    for k in range(1, APPOINTMENTS_PER_BATCH + 1):
        apps = found("appointment", k)
        atoms = config.atom_order[(k - 1) * ATOMS_PER_APPOINTMENT : k * ATOMS_PER_APPOINTMENT]
        if not apps and not any(found("atom", a) for a in atoms):
            continue
        n_apps += 1
        if apps:
            start, duration = apps[0][0], sum(d for _, d in apps)
            rows.append(
                TimingRow(
                    "appointment",
                    None,
                    k,
                    None,
                    None,
                    None,
                    start.t_ms,
                    start.t_ms + duration,
                    duration,
                    budget_ms=APPOINTMENT_BUDGET_MS,
                )
            )
            if max_app is None or duration > max_app[0]:
                max_app = (duration, k)
        for atom in atoms:
            spans = found("atom", atom)
            if not spans:
                continue
            position = config.atom_order.index(atom) + 1
            start, duration = spans[0][0], sum(d for _, d in spans)
            atom_ms.append(duration)
            rows.append(
                TimingRow(
                    "atom",
                    None,
                    k,
                    position,
                    atom,
                    None,
                    start.t_ms,
                    start.t_ms + duration,
                    duration,
                    budget_ms=ATOM_BUDGET_MS,
                )
            )
            if max_atom is None or duration > max_atom[0]:
                max_atom = (duration, atom)
            for round_ in range(1, ROUNDS_PER_ATOM + 1):
                rounds = found("round", atom, round_)
                if not rounds:
                    continue
                proposal = sum(d for _, d in found("proposal", atom, round_)) or None
                rating = sum(d for _, d in found("rating", atom, round_)) or None
                r_start, r_duration = rounds[0][0], sum(d for _, d in rounds)
                round_ms.append(r_duration)
                max_window = max(max_window, proposal or 0)
                rows.append(
                    TimingRow(
                        "round",
                        None,
                        k,
                        position,
                        atom,
                        round_,
                        r_start.t_ms,
                        r_start.t_ms + r_duration,
                        r_duration,
                        proposal,
                        rating,
                        ROUND_BUDGET_MS,
                    )
                )
                label = f"{atom} r{round_}"
                if max_round is None or r_duration > max_round[0]:
                    max_round = (r_duration, label)
    summary = TimingSummary(
        clock=manifest.clock,
        clock_speed=manifest.clock_speed,
        rounds=len(round_ms),
        atoms=len(atom_ms),
        appointments=n_apps,
        max_round_ms=None if max_round is None else max_round[0],
        max_round=None if max_round is None else max_round[1],
        max_atom_ms=None if max_atom is None else max_atom[0],
        max_atom=None if max_atom is None else max_atom[1],
        max_appointment_ms=None if max_app is None else max_app[0],
        max_appointment=None if max_app is None else max_app[1],
        max_proposal_window_ms=max_window or None,
        mean_round_ms=round(sum(round_ms) / len(round_ms)) if round_ms else None,
        mean_atom_ms=round(sum(atom_ms) / len(atom_ms)) if atom_ms else None,
        startup_ms=startup_ms,
    )
    return rows, summary


def write_timing(run_dir: str | os.PathLike[str], out_dir: str | os.PathLike[str]) -> TimingSummary:
    """Write `timing.csv` and `timing-summary.json` to `out_dir`; returns the summary."""
    rows, summary = timing_rows(run_dir)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(TIMING_COLUMNS)
    for row in rows:
        writer.writerow(row.cells())
    (out / "timing.csv").write_text(buffer.getvalue(), encoding="utf-8", newline="\n")
    _write_json(out / "timing-summary.json", summary.to_dict())
    return summary


def _write_json(path: Path, obj: object) -> str:
    text = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Independent tally (standard library only: no record classes, no audit code)


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def tally_logs(run_dir: str | os.PathLike[str]) -> dict[str, dict[str, int]]:
    """Per book: counts recounted from the raw JSONL lines of the run (`config.json`,
    `slots.jsonl`, `commits.jsonl`, `timing.jsonl`, `ratings.jsonl`,
    `decisions.jsonl`): slots per outcome, valid and invalid slots, server errors, atoms
    of the final store book by source, the failed-generation and nonfallback flags, the
    committed `total_ms` values, rating records and placeholders. Book `*` holds the
    batch counts (fallback scans, refusals, message plays)."""
    root = Path(run_dir)
    config = json.loads((root / "config.json").read_text(encoding="utf-8"))
    books = [b["book_id"] for b in config["books"]]
    slots = _jsonl(root / "logs" / "slots.jsonl")
    commits = _jsonl(root / "logs" / "commits.jsonl")
    timing = _jsonl(root / "logs" / "timing.jsonl")
    ratings = _jsonl(root / "logs" / "ratings.jsonl")
    decisions = _jsonl(root / "logs" / "decisions.jsonl")
    final = {b: b for b in books}
    for event in timing:
        if event["event"] == "book_substituted" and event.get("book_id") in final:
            final[event["book_id"]] = f"{event['book_id']}-FB"
    out: dict[str, dict[str, int]] = {}
    for book in books:
        counts: Counter[str] = Counter()
        mine = [s for s in slots if s.get("book_id") == book]
        counts["slots_total"] = len(mine)
        for s in mine:
            counts[f"n_{s['outcome']}"] += 1
            counts["slots_valid" if s["outcome"] == "valid" else "slots_invalid"] += 1
            if s.get("llm_status") == "server_error":
                counts["n_llm_server_error"] += 1
        kept = [c for c in commits if c["book_id"] == book and c["store_book_id"] == final[book]]
        counts["atoms_committed"] = len(kept)
        names = {
            "selector": "atoms_selector",
            "fallback_bank": "atoms_bank_fallback",
            "fallback_book": "atoms_book_fallback",
        }
        for c in kept:
            counts[names[c["source"]]] += 1
            counts[f"total_ms_{c['recipe']['total_ms']}"] += 1
        counts["failed_generation"] = int(final[book] != book)
        counts["nonfallback"] = int(
            counts["atoms_selector"] == ATOMS_PER_BOOK and final[book] == book
        )
        own = [r for r in ratings if r["book_id"] == book]
        counts["rating_records"] = len(own)
        counts["rating_placeholders"] = sum(1 for r in own if r["placeholder"])
        counts["decision_records"] = sum(1 for d in decisions if d["book_id"] == book)
        out[book] = dict(counts)
    plays = _jsonl(root / "logs" / "plays.jsonl")
    out["*"] = {
        "fallback_scans": len(_jsonl(root / "logs" / "fallback-scans.jsonl")),
        "slot_refusals": len(_jsonl(root / "logs" / "slot-refusals.jsonl")),
        "message_plays": sum(1 for p in plays if p["audio_kind"] == "message"),
        "commit_records": len(commits),
    }
    return out


TALLY_COMPARE_COLUMNS: Final[tuple[str, ...]] = (
    "book_id",
    "quantity",
    "log_count",
    "audit_count",
    "match",
)


def compare_with_audit(
    run_dir: str | os.PathLike[str],
    audit_dir: str | os.PathLike[str],
    path: str | os.PathLike[str],
) -> tuple[bool, int]:
    """Compare `tally_logs` with the #24 audit of the run (`audit_dir/unmasked/books.csv`
    and `summary.json` `checks`) and write the comparison sheet to `path`
    (`TALLY_COMPARE_COLUMNS`). Every count column of `books.csv` (slots, outcome codes,
    server errors, atoms by source, flags, `total_ms_*`) and the batch's fallback scans
    and slot refusals are compared. Returns (all match, rows)."""
    tally = tally_logs(run_dir)
    audit = Path(audit_dir) / "unmasked"
    with (audit / "books.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    summary = json.loads((audit / "summary.json").read_text(encoding="utf-8"))
    compared = [
        name
        for name in (rows[0].keys() if rows else ())
        if name.startswith(("n_", "slots_", "atoms_", "total_ms_"))
        or name in ("failed_generation", "nonfallback")
    ]
    lines: list[list[str]] = []
    ok = True
    for row in rows:
        book = row["book_id"]
        mine = tally.get(book, {})
        for name in compared:
            log_count = mine.get(name, 0)
            audit_count = int(row[name]) if row[name] != "" else 0
            match = log_count == audit_count
            ok &= match
            lines.append([book, name, str(log_count), str(audit_count), str(int(match))])
    checks = summary.get("checks", {})
    for name in ("fallback_scans", "slot_refusals"):
        log_count = tally["*"][name]
        value = checks.get(name)
        audit_count = len(value) if isinstance(value, list) else int(value or 0)
        match = log_count == audit_count
        ok &= match
        lines.append(["*", name, str(log_count), str(audit_count), str(int(match))])
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(TALLY_COMPARE_COLUMNS)
    writer.writerows(lines)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(buffer.getvalue(), encoding="utf-8", newline="\n")
    return ok, len(lines)


def write_hand_tally(
    run_dir: str | os.PathLike[str], path: str | os.PathLike[str]
) -> tuple[bool, int]:
    """The #24 tally sheet (`audit.tally_sheet`: its own raw-line count and the audit's
    count per book and quantity) with the `hand_count` column filled by `tally_logs`, the
    recount #24 asks a person to make. Returns (every row matches three ways, rows)."""
    target = Path(path)
    tally_sheet(run_dir, target)
    mine = tally_logs(run_dir)
    with target.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    ok = bool(rows)
    for row in rows:
        count = mine.get(row["book_id"], {}).get(row["quantity"], 0)
        row["hand_count"] = str(count)
        ok &= row["match"] == "1" and str(count) == row["log_count"]
        ok &= row["log_count"] == row["audit_count"]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(TALLY_COLUMNS), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    target.write_text(buffer.getvalue(), encoding="utf-8", newline="\n")
    return ok, len(rows)


# ---------------------------------------------------------------------------
# Bundle and evidence


def write_bundle(run_dir: str | os.PathLike[str], path: str | os.PathLike[str]) -> dict[str, Any]:
    """A deterministic `tar.gz` of every file of the run directory (sorted POSIX paths
    under `<run_id>/`, mtime 0, owner 0, mode 0644, gzip mtime 0): the same files give
    the same bytes. Returns its SHA-256, size and file count."""
    root = Path(run_dir)
    files = relative_files(root)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with (
        target.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz,
        tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as tar,
    ):
        for rel in files:
            data = (root / rel).read_bytes()
            info = tarfile.TarInfo(f"{root.name}/{rel}")
            info.size = len(data)
            info.mtime = 0
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data))
    return {
        "name": target.name,
        "sha256": _file_sha256(target),
        "bytes": target.stat().st_size,
        "files": len(files),
    }


def _machine() -> dict[str, Any]:
    """The machine of a timing run (no host name, no user, no path)."""
    return {
        "os": platform.system(),
        "os_release": platform.release(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
    }


def write_evidence(
    run_dir: str | os.PathLike[str],
    evidence_dir: str | os.PathLike[str],
    *,
    bundle_dir: str | os.PathLike[str] | None = None,
    result: DryRunResult | None = None,
) -> dict[str, Any]:
    """Check, time, audit and tally a finished dry run and write the small text evidence
    to `evidence_dir`: `completeness.txt`, `timing.csv`, `timing-summary.json`,
    `audit-books.csv` / `audit-summary.md` (#24, unmasked: a DEMO run),
    `tally-comparison.csv`, `hand-tally.csv` and `manifest.json` (every hash, the
    bundle's included). The #24 audit itself goes to `<run_dir>/audit/`; the bundle
    (`<run_id>-bundle.tar.gz`) to `bundle_dir` (default: next to the run directory),
    outside git."""
    root = Path(run_dir)
    layout = RunLayout(root, root.name)
    out = Path(evidence_dir)
    out.mkdir(parents=True, exist_ok=True)
    report = check_log_completeness(root)
    (out / "completeness.txt").write_text(report.text(), encoding="utf-8", newline="\n")
    summary = write_timing(root, out)
    audit = build_audit(root, root / "audit")
    unmasked = root / "audit" / "unmasked"
    for name, target in (("books.csv", "audit-books.csv"), ("summary.md", "audit-summary.md")):
        (out / target).write_bytes((unmasked / name).read_bytes())
    tally_ok, tally_rows = compare_with_audit(root, root / "audit", out / "tally-comparison.csv")
    hand_ok, hand_rows = write_hand_tally(root, out / "hand-tally.csv")
    bundle_root = Path(bundle_dir) if bundle_dir is not None else root.parent
    bundle = write_bundle(root, bundle_root / f"{layout.run_id}{BUNDLE_SUFFIX}")
    manifest = RunManifest.read(layout.manifest)
    plan = DryRunPlan.read(layout.dry_run_plan)
    evidence_files = {
        p.name: _file_sha256(p) for p in sorted(out.iterdir()) if p.name != "manifest.json"
    }
    doc: dict[str, Any] = {
        "format": EVIDENCE_FORMAT,
        "format_version": 1,
        "label": "synthetic",
        "run_id": layout.run_id,
        "batch_id": plan.batch_id,
        "kind": manifest.kind.value,
        "purpose": manifest.purpose,
        "clock": manifest.clock,
        "clock_speed": manifest.clock_speed,
        "appointments": list(plan.appointments),
        "fallback_bank": plan.fallback_bank,
        "zero_eligible": [
            {"book_id": z.book_id, "atom_id": z.atom_id, "expect": z.expect}
            for z in plan.zero_eligible
        ],
        "designer_think_ms": plan.designer_think_ms,
        "mock_llm_latency_ms": plan.mock_llm_latency_ms,
        "run_closed": manifest.closed_utc is not None,
        "documents": {
            name: _file_sha256(root / name)
            for name in (
                "config.json",
                "dry-run-plan.json",
                "generation-config.json",
                "run-manifest.json",
            )
        },
        "config_sha256": manifest.config_sha256,
        "generation_config_sha256": manifest.generation_config_sha256,
        "code": {
            "av_generation": manifest.code.av_generation,
            "renderer_hash": manifest.code.renderer_hash,
            "validator_hash": manifest.code.validator_hash,
        },
        "logs": {
            rel: _file_sha256(root / rel)
            for rel in sorted(LOG_FILES.values())
            if (root / rel).is_file()
        },
        "audit": {
            "ok": audit.ok,
            "problems": list(audit.problems),
            "notes": list(audit.notes),
            "files": audit.files,
        },
        "completeness": report.to_dict(),
        "timing": summary.to_dict(),
        "tally": {
            "all_match": tally_ok and hand_ok,
            "rows": tally_rows,
            "hand_tally_rows": hand_rows,
            "hand_tally_match": hand_ok,
        },
        "bundle": bundle,
        "evidence_files": evidence_files,
        "machine": _machine(),
    }
    driver = bundle_root / f"{layout.run_id}{DRIVER_SUFFIX}"
    if result is not None:
        _write_json(driver, driver_summary(result))
    if driver.is_file():
        doc["driver"] = json.loads(driver.read_text(encoding="utf-8"))
    _write_json(out / "manifest.json", doc)
    return doc


def driver_summary(result: DryRunResult) -> dict[str, Any]:
    """What the driver saw (`<run_id>-driver.json`, next to the bundle): real elapsed
    time, the LLM label, the startup times and each session's bots and designer."""
    return {
        "run_id": result.layout.run_id,
        "elapsed_real_s": result.elapsed_real_s,
        "llm": result.llm,
        "startup_ms": dict(sorted(result.startup_ms.items())),
        "sessions": [_session_summary(s) for s in result.sessions],
    }


def _session_summary(session: SessionResult) -> dict[str, Any]:
    stations = {}
    for station, r in session.stations.items():
        refused = Counter(code for ok, code in r.ratings.values() if not ok)
        stations[station] = {
            "end_reason": r.end_reason,
            "slots": len(r.slots),
            "plays": len(r.plays),
            "ratings_accepted": sum(1 for ok, _ in r.ratings.values() if ok),
            "ratings_refused": dict(sorted((str(k), v) for k, v in refused.items())),
            "missing": len(r.missing),
            "reconnects": r.reconnects,
        }
    outcomes = Counter(session.designer_submitted.values())
    return {
        "appointment": session.appointment,
        "real_s": session.real_s,
        "stations": stations,
        "designer": {
            "submitted": dict(sorted(outcomes.items())),
            "late": len(session.designer_late),
            "plays": session.designer_plays,
            "think_ms": session.designer_think_ms,
        },
    }


# ---------------------------------------------------------------------------
# Command line


def _appointments_arg(text: str) -> tuple[int, ...]:
    if text == "all":
        return tuple(range(1, APPOINTMENTS_PER_BATCH + 1))
    return tuple(int(part) for part in text.split(","))


def _range_arg(text: str) -> tuple[int, int] | None:
    if text == "none":
        return None
    low, _, high = text.partition(":")
    return int(low), int(high or low)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m av_generation.dryrun",
        description="Synthetic-panel dry run of a full Study A batch (#22).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run the dry run, then write the evidence")
    run.add_argument("--out", required=True, help="runs root (outside git for real logs)")
    run.add_argument("--run-id", default="DEMO-dry-run-01", help="a DEMO- run ID")
    run.add_argument("--clock", choices=("real", "scaled"), default="scaled")
    run.add_argument("--speed", type=float, default=20.0, help="ScaledClock speed")
    run.add_argument("--appointments", default="all", help="all, or e.g. 1 or 1,2")
    run.add_argument(
        "--whole-book",
        action="store_true",
        help="single-recipe bank: the second zero-eligible atom of a book substitutes it",
    )
    run.add_argument(
        "--zero-eligible",
        action="append",
        help="METHOD@POSITION of an atom to force to zero eligible candidates (repeat)",
    )
    run.add_argument("--llm-url", help="an external OpenAI-compatible server (default: mock)")
    run.add_argument("--mock-latency-ms", type=int, default=DEFAULT_MOCK_LATENCY_MS)
    run.add_argument(
        "--think-ms",
        default=f"{DEFAULT_THINK_MS[0]}:{DEFAULT_THINK_MS[1]}",
        help="bot designer think time per slot, LOW:HIGH clock ms, or none",
    )
    run.add_argument("--station-timeout-s", type=float, default=120.0)
    run.add_argument(
        "--resume", action="store_true", help="reopen an interrupted dry run (its stored plan)"
    )
    run.add_argument("--evidence", help="evidence directory (default: <out>/<run_id>-evidence)")
    check = sub.add_parser("check", help="the log-completeness check of a run directory")
    check.add_argument("run_dir")
    check.add_argument("--plan", help="dry-run plan (default: <run_dir>/dry-run-plan.json)")
    timing = sub.add_parser("timing", help="timing.csv and timing-summary.json of a run")
    timing.add_argument("run_dir")
    timing.add_argument("--out", required=True)
    tally = sub.add_parser("tally", help="independent recount compared with the #24 audit")
    tally.add_argument("run_dir")
    tally.add_argument("--audit", help="audit directory (default: <run_dir>/audit)")
    tally.add_argument("--out", default="tally-comparison.csv")
    evidence = sub.add_parser("evidence", help="check, time, audit, tally and bundle a run")
    evidence.add_argument("run_dir")
    evidence.add_argument("--out", required=True)
    evidence.add_argument("--bundle-dir")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m av_generation.dryrun run|check|timing|tally|evidence ...`: exit 0 when
    every check passes, 1 when a check fails, 2 when the run or a file is refused."""
    args = _parser().parse_args(argv)
    try:
        if args.command == "check":
            plan = DryRunPlan.read(args.plan) if args.plan else None
            report = check_log_completeness(args.run_dir, plan=plan)
            sys.stdout.write(report.text())
            return 0 if report.ok else 1
        if args.command == "timing":
            summary = write_timing(args.run_dir, args.out)
            sys.stdout.write(json.dumps(summary.to_dict(), indent=2, sort_keys=True) + "\n")
            return 0 if summary.atom_ok and summary.appointment_ok else 1
        if args.command == "tally":
            audit = args.audit or str(Path(args.run_dir) / "audit")
            ok, n = compare_with_audit(args.run_dir, audit, args.out)
            sys.stdout.write(f"tally: {n} rows, {'all match' if ok else 'MISMATCH'}\n")
            return 0 if ok else 1
        if args.command == "evidence":
            doc = write_evidence(args.run_dir, args.out, bundle_dir=args.bundle_dir)
            return _report(doc)
        clock: Clock = SystemClock() if args.clock == "real" else ScaledClock(args.speed)
        result = run_dry_run(
            args.out,
            args.run_id,
            clock=clock,
            appointments=_appointments_arg(args.appointments),
            fallback_bank="single_recipe" if args.whole_book else "demo",
            zero_eligible=args.zero_eligible,
            llm_url=args.llm_url,
            mock_latency_ms=args.mock_latency_ms,
            designer_think_ms=_range_arg(args.think_ms),
            station_timeout_s=args.station_timeout_s,
            resume=args.resume,
        )
        evidence = args.evidence or str(Path(args.out) / f"{args.run_id}-evidence")
        doc = write_evidence(result.layout.root, evidence, bundle_dir=args.out, result=result)
        return _report(doc)
    except (RuntimeError, ValueError, OSError) as err:
        sys.stderr.write(f"error: {err}\n")
        return 2


def _report(doc: Mapping[str, Any]) -> int:
    timing = doc["timing"]
    ok = (
        doc["completeness"]["ok"]
        and doc["tally"]["all_match"]
        and timing["atom_ok"]
        and timing["appointment_ok"]
    )
    sys.stdout.write(
        f"dry run {doc['run_id']}: completeness {'OK' if doc['completeness']['ok'] else 'FAILED'}, "
        f"max atom {timing['max_atom_ms']} ms, max appointment {timing['max_appointment_ms']} ms, "
        f"audit ok={doc['audit']['ok']}, tally match={doc['tally']['all_match']}, "
        f"bundle {doc['bundle']['sha256']}\n"
    )
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
