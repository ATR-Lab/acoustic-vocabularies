"""Synthetic batch runs of the orchestrator (#20; private): simulated proposers, an
in-process synthetic panel and the DEMO batch run used as #20's evidence.

Nothing here is a study component. The real proposers are A1 (#19), A2 (#18) and A3
(#17); the real stations and bot raters talk to the panel server (#21) over WebSockets.
These stand-ins speak the same contracts (`proposers.RoundProposer`,
`panel_session.PanelSessionHost`) so the orchestrator can be run end to end before the
other issues land:

- `SimProposer`: fills three slots per round with seeded uniform recipes (the A1 stand-in
  is a scripted "bot designer" with seed keys in the `A1` namespace), validates them with
  `av_sound.validate` against the book's committed references and writes one `SlotRecord`
  per slot. A `propose` callback scripts exact candidates for fixtures.
- `SyntheticPanel`: three bot seats that read the host's events and submit ratings drawn
  from `seeds.bot_seed_key(run_id, rater, "rating", rating_slot_id)` (or a scripted
  policy). With a `ManualClock` it drives the clock itself (event by event), so a whole
  batch runs in seconds and every record is deterministic; with a `ScaledClock` each seat
  runs in its own thread in accelerated real time.

`python -m av_generation._batch_sim --out DIR --clock manual|scaled` runs one DEMO batch
and prints its summary (counts and log digests).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

import numpy as np
from av_sound._paths import data_root as sound_root
from av_sound.fallback import FallbackSet, load_fallback
from av_sound.recipe import Recipe
from av_sound.store import VocabularyStore
from av_sound.validate import validate
from av_sound.wav import file_sha256

from av_generation._paths import examples_path
from av_generation.clock import Clock, ManualClock, ScaledClock
from av_generation.config import BatchConfig, RaterSeat
from av_generation.constants import RATING_SLOT_MS, REFERENCE_ONSET_MS, SLOTS_PER_ROUND
from av_generation.domain import COORDINATES, values_to_recipe
from av_generation.genconfig import (
    GenerationConfig,
    PromptHashes,
    build_generation_config,
    fallback_pins,
)
from av_generation.ids import Method, RunKind, Study, proposal_slot_id, slot_index
from av_generation.jsonio import canonical_sha256, document_text
from av_generation.meanings import MeaningSet, load_meanings
from av_generation.orchestrator import Orchestrator, write_panel_order_csv
from av_generation.outcomes import SlotOutcome, outcome_from_validation
from av_generation.panel_session import PanelSessionHost, PanelSlot, PlayReport, RatingSubmission
from av_generation.proposers import RoundRequest, RoundResult
from av_generation.records import (
    A2Detail,
    CommitRecord,
    RatingRecord,
    RecordWriter,
    SlotRecord,
    read_records,
)
from av_generation.rundir import LOG_FILES, RunLayout, create_run_dir
from av_generation.seeds import (
    a1_seed_key,
    a2_seed_key,
    a3_seed_key,
    bot_seed_key,
    rng_for,
    seed_from_key,
)

DEMO_FALLBACK_MANIFEST: Final = Path("testvectors") / "fallback" / "demo-manifest.json"
DEMO_PROMPT_HASH: Final = canonical_sha256({"demo": "no prompt set: simulated proposers (#20)"})
SUMMARY_FORMAT: Final = "av-generation/sim-batch-summary"

# ---------------------------------------------------------------------------
# Simulated proposers


@dataclass(frozen=True, slots=True)
class SimProposal:
    """What a simulated proposer submits in one slot."""

    kind: Literal["recipe", "timeout", "invalid_json"]
    recipe: Recipe | None = None


ProposeFn = Callable[[RoundRequest, int, np.random.Generator], SimProposal]
"""`propose(request, slot, rng)`: script one slot (rng is the slot's seeded stream)."""

_SEED_KEYS: Final[Mapping[Method, Callable[[str, str, int, int], str]]] = {
    Method.A1: a1_seed_key,
    Method.A2: a2_seed_key,
    Method.A3: a3_seed_key,
}
_FAILURE: Final[Mapping[Method, Literal["timeout", "invalid_json"] | None]] = {
    Method.A1: "timeout",
    Method.A2: None,
    Method.A3: "invalid_json",
}


def uniform_recipe(rng: np.random.Generator) -> Recipe:
    """One recipe drawn uniformly over the 12 coordinates."""
    return values_to_recipe([c.values[int(rng.integers(len(c.values)))] for c in COORDINATES])


class SimProposer:
    """A `RoundProposer` stand-in (see the module docstring); writes its own slot records."""

    def __init__(
        self,
        method: Method,
        slots: RecordWriter,
        *,
        clock: Clock,
        designer_id: str | None = None,
        p_failure: float = 0.0,
        propose: ProposeFn | None = None,
    ) -> None:
        self.method = Method(method)
        self._slots = slots
        self._clock = clock
        self._designer_id = designer_id
        self._p_failure = p_failure
        self._propose = propose
        self.requests: list[RoundRequest] = []
        self._lock = threading.Lock()

    def _default(self, request: RoundRequest, slot: int, rng: np.random.Generator) -> SimProposal:
        failure = _FAILURE[self.method]
        draw = float(rng.random())
        recipe = uniform_recipe(rng)
        if failure is not None and draw < self._p_failure:
            return SimProposal(failure)
        return SimProposal("recipe", recipe)

    def propose_round(self, request: RoundRequest) -> RoundResult:
        with self._lock:
            self.requests.append(request)
        records = []
        for slot in range(1, SLOTS_PER_ROUND + 1):
            t_open = self._clock.now_ms()
            key = _SEED_KEYS[self.method](
                request.seed_namespace, request.atom_id, request.round, slot
            )
            rng = rng_for(key)
            proposal = (self._propose or self._default)(request, slot, rng)
            records.append(self._record(request, slot, key, t_open, proposal))
        return RoundResult(
            self.method, request.book_id, request.atom_id, request.round, tuple(records)
        )

    def _record(
        self, request: RoundRequest, slot: int, key: str, t_open: int, proposal: SimProposal
    ) -> SlotRecord:
        fields: dict[str, Any] = {
            "run_id": request.run_id,
            "study": Study.A,
            "method": self.method,
            "slot_id": proposal_slot_id(request.book_id, request.atom_id, request.round, slot),
            "profile": request.profile,
            "atom_id": request.atom_id,
            "slot": slot,
            "slot_index": slot_index(request.round, slot),
            "t_open_ms": t_open,
            "batch_id": request.batch_id,
            "book_id": request.book_id,
            "round": request.round,
            "designer_id": self._designer_id if self.method is Method.A1 else None,
            "seed_key": key,
            "seed": seed_from_key(key),
            "latency_ms": 0,
            "a2": A2Detail("uniform", None) if self.method is Method.A2 else None,
        }
        if proposal.kind == "timeout":
            record = SlotRecord(outcome=SlotOutcome.TIMEOUT, t_ms=self._clock.now_ms(), **fields)
        elif proposal.kind == "invalid_json" or proposal.recipe is None:
            record = SlotRecord(
                outcome=SlotOutcome.INVALID_JSON,
                t_ms=self._clock.now_ms(),
                raw_output="{",
                **fields,
            )
        else:
            recipe = proposal.recipe
            result = validate(
                recipe,
                request.profile,
                request.book.references(),
                threshold=request.book.threshold,
            )
            record = SlotRecord(
                outcome=outcome_from_validation(result),
                t_ms=self._clock.now_ms(),
                raw_output=recipe.canonical_json(),
                recipe=recipe.to_dict(),
                recipe_sha256=recipe.sha256(),
                validator_codes=result.codes,
                validator_messages=result.messages,
                pcm_sha256=result.pcm_sha256,
                file_sha256=(
                    None
                    if result.rendered is None or result.pcm_sha256 is None
                    else file_sha256(result.rendered)
                ),
                **fields,
            )
        self._slots.append(record)
        return record


# ---------------------------------------------------------------------------
# Synthetic panel


@dataclass(frozen=True, slots=True)
class SimRating:
    """One bot rating: the three judgments and the response time after unlock."""

    association: int
    distinguishability: int | None
    comfort: Literal["acceptable", "unacceptable"]
    rt_ms: int


RatingPolicy = Callable[[RaterSeat, PanelSlot], SimRating | None]
"""`policy(seat, slot)`: the seat's rating of a rateable slot, or `None` (missing)."""


def seeded_policy(
    run_id: str,
    *,
    p_comfort_acceptable: float = 0.9,
    force_unacceptable: frozenset[str] = frozenset(),
    p_missing: float = 0.0,
    rt_range_ms: tuple[int, int] = (400, 2_500),
) -> RatingPolicy:
    """Bot ratings from `rng_for(bot_seed_key(run_id, rater, "rating", rating_slot_id))`:
    association and distinguishability uniform on 1..7, comfort acceptable with
    `p_comfort_acceptable` (always unacceptable on `force_unacceptable` rating slots)."""

    def policy(seat: RaterSeat, slot: PanelSlot) -> SimRating | None:
        rng = rng_for(bot_seed_key(run_id, seat.rater_id, "rating", slot.rating_slot_id))
        association = int(rng.integers(1, 8))
        distinguishability = int(rng.integers(1, 8))
        comfort_draw = float(rng.random())
        missing_draw = float(rng.random())
        rt = int(rng.integers(rt_range_ms[0], rt_range_ms[1] + 1))
        if missing_draw < p_missing:
            return None
        acceptable = (
            slot.rating_slot_id not in force_unacceptable and comfort_draw < p_comfort_acceptable
        )
        return SimRating(
            association,
            None if slot.first_atom else distinguishability,
            "acceptable" if acceptable else "unacceptable",
            rt,
        )

    return policy


@dataclass
class SyntheticPanel:
    """Three in-process bot seats on a `PanelSessionHost` (see the module docstring)."""

    host: PanelSessionHost
    clock: Clock
    policy: RatingPolicy
    report_plays: bool = True
    probe: Callable[[PanelSessionHost, PanelSlot], None] | None = None
    """Called at each slot's start before the ratings (drive mode): tests use it to send
    extra station calls (bad ratings, reconnects, a withdrawal)."""
    acks: list[tuple[str, str, str | None]] = field(default_factory=list)
    """(rater, rating slot, refusal code or None) of every submission."""
    errors: list[BaseException] = field(default_factory=list)
    _stop: threading.Event = field(default_factory=threading.Event)
    _threads: list[threading.Thread] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def drives_clock(self) -> bool:
        return isinstance(self.clock, ManualClock)

    def start(self) -> SyntheticPanel:
        for seat in self.host.seats():
            self.host.station_joined(seat.rater_id, seat.station, seat.kind)
            self.host.clock_synced(seat.rater_id, seat.station, 0.0, 1.0)
        targets: list[tuple[str, Callable[[], None]]]
        if self.drives_clock:
            targets = [("sim-panel-driver", self._drive)]
        else:
            targets = [
                (f"sim-seat-{seat.station}", self._seat_target(seat)) for seat in self.host.seats()
            ]
        for name, target in targets:
            thread = threading.Thread(target=self._guard(target), name=name, daemon=True)
            self._threads.append(thread)
            thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=10)
        if self.errors:
            raise RuntimeError(f"synthetic panel failed: {self.errors[0]!r}") from self.errors[0]

    def __enter__(self) -> SyntheticPanel:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def _seat_target(self, seat: RaterSeat) -> Callable[[], None]:
        return lambda: self._seat_loop(seat)

    def _guard(self, target: Callable[[], None]) -> Callable[[], None]:
        def run() -> None:
            try:
                target()
            except BaseException as err:  # noqa: BLE001 - reported by stop()
                self.errors.append(err)

        return run

    def _events(self) -> Any:  # noqa: ANN401 - generator of PanelEvent
        seq = 0
        while not self._stop.is_set():
            for event in self.host.wait_events(seq, 0.05):
                seq = event.seq
                yield event

    def _submit(self, seat: RaterSeat, slot: PanelSlot, rating: SimRating, received: int) -> None:
        ack = self.host.submit_rating(
            RatingSubmission(
                seat.rater_id,
                seat.station,
                slot.rating_slot_id,
                rating.association,
                rating.distinguishability,
                rating.comfort,
                rating.rt_ms,
                received,
            )
        )
        with self._lock:
            self.acks.append((seat.rater_id, slot.rating_slot_id, ack.code))

    def _play(
        self, seat: RaterSeat, slot: PanelSlot, role: Literal["candidate", "reference"]
    ) -> None:
        asset = slot.candidate if role == "candidate" else slot.reference
        if asset is None or not self.report_plays:
            return
        offset = 0 if role == "candidate" else REFERENCE_ONSET_MS
        onset = slot.start_ms + offset
        self.host.report_play(
            PlayReport(
                seat.rater_id,
                seat.station,
                slot.rating_slot_id,
                role,
                asset.asset_id,
                onset,
                onset,
                self.clock.now_ms(),
            )
        )

    def _drive(self) -> None:
        clock = self.clock
        assert isinstance(clock, ManualClock)
        seats = list(self.host.seats())
        for event in self._events():
            if event.kind != "slot" or event.slot is None:
                continue
            slot = event.slot
            clock.set_ms(slot.start_ms)
            if self.probe is not None:
                self.probe(self.host, slot)
            if not slot.placeholder:
                for seat in seats:
                    self._play(seat, slot, "candidate")
                if slot.reference is not None:
                    clock.set_ms(slot.start_ms + REFERENCE_ONSET_MS)
                    for seat in seats:
                        self._play(seat, slot, "reference")
                planned = []
                for i, seat in enumerate(seats):
                    rating = self.policy(seat, slot)
                    if rating is not None:
                        at = slot.start_ms + slot.unlock_offset_ms + rating.rt_ms
                        planned.append((at, i, seat, rating))
                for at, _, seat, rating in sorted(planned, key=lambda p: (p[0], p[1])):
                    clock.set_ms(at)
                    self._submit(seat, slot, rating, at)
            clock.set_ms(slot.start_ms + RATING_SLOT_MS)

    def _sleep_until(self, t_ms: int) -> None:
        while not self._stop.is_set():
            delta = t_ms - self.clock.now_ms()
            if delta <= 0:
                return
            self.clock.sleep(min(delta, 2_000) / 1000)

    def _seat_loop(self, seat: RaterSeat) -> None:
        for event in self._events():
            if event.kind != "slot" or event.slot is None or event.slot.placeholder:
                continue
            slot = event.slot
            # Accelerated real time: the rating goes first and the plays (scheduled
            # onsets) are reported after it, so fsynced play writes never delay a rating
            # past the lock on a slow machine.
            rating = self.policy(seat, slot)
            if rating is not None:
                self._sleep_until(slot.start_ms + slot.unlock_offset_ms + rating.rt_ms)
                if self.clock.now_ms() < slot.start_ms + RATING_SLOT_MS:
                    self._submit(seat, slot, rating, self.clock.now_ms())
            self._play(seat, slot, "candidate")
            self._play(seat, slot, "reference")


# ---------------------------------------------------------------------------
# DEMO inputs and one synthetic batch


def demo_fallback() -> FallbackSet:
    return load_fallback(sound_root() / DEMO_FALLBACK_MANIFEST)


def demo_meanings() -> MeaningSet:
    return load_meanings(examples_path("demo-meanings"))


def demo_batch_config() -> BatchConfig:
    return BatchConfig.read(examples_path("demo-batch-config.json")).check_consistency()


def demo_generation_config(
    meanings: MeaningSet, fallback: FallbackSet, *, threshold: str = "0.10"
) -> GenerationConfig:
    """A DEMO generation config for simulated proposers (no LLM manifest, no prompts)."""
    return build_generation_config(
        "DEMO-orchestrator-sim",
        llm_manifest_sha256=None,
        decoding_schema_sha256=DEMO_PROMPT_HASH,
        prompts=PromptHashes(DEMO_PROMPT_HASH, DEMO_PROMPT_HASH),
        meanings_sha256=meanings.sha256(),
        separation_threshold=threshold,
        fallback=fallback_pins(fallback),
    )


@dataclass
class SimBatch:
    """Everything of one synthetic batch run (for tests and the evidence run)."""

    orchestrator: Orchestrator
    layout: RunLayout
    panel: SyntheticPanel
    proposers: dict[Method, SimProposer]
    store: VocabularyStore
    clock: Clock


def make_sim_batch(
    runs_root: str | Path,
    run_id: str,
    *,
    clock: Clock,
    config: BatchConfig | None = None,
    policy: RatingPolicy | None = None,
    propose: Mapping[Method, ProposeFn] | None = None,
    p_failure: float = 0.05,
    fallback: FallbackSet | None = None,
    kind: RunKind = RunKind.SYNTHETIC,
    resume: bool = False,
) -> SimBatch:
    """Create (or, with `resume=True`, reopen) a synthetic run with simulated proposers
    and a synthetic panel (not started)."""
    config = config if config is not None else demo_batch_config()
    fallback = fallback if fallback is not None else demo_fallback()
    meanings = demo_meanings()
    layout = (
        RunLayout(Path(runs_root) / run_id, run_id)
        if resume
        else create_run_dir(runs_root, run_id, kind)
    )
    slots = RecordWriter(layout.log("slot"))
    proposers = {
        Method.A1: SimProposer(
            Method.A1,
            slots,
            clock=clock,
            designer_id=config.book_of(Method.A1).designer_id,
            p_failure=p_failure,
            propose=(propose or {}).get(Method.A1),
        ),
        Method.A2: SimProposer(
            Method.A2,
            slots,
            clock=clock,
            p_failure=p_failure,
            propose=(propose or {}).get(Method.A2),
        ),
        Method.A3: SimProposer(
            Method.A3,
            slots,
            clock=clock,
            p_failure=p_failure,
            propose=(propose or {}).get(Method.A3),
        ),
    }
    store = VocabularyStore(layout.store_dir, clock=clock.utc_now)
    orchestrator = Orchestrator(
        config,
        layout,
        proposers,
        store,
        fallback,
        clock=clock,
        generation_config=demo_generation_config(meanings, fallback, threshold=config.threshold),
        meanings=meanings,
        kind=kind,
        slot_event_lead_ms=0 if isinstance(clock, ManualClock) else 500,
    )
    panel = SyntheticPanel(
        orchestrator.panel_host(),
        clock,
        policy if policy is not None else seeded_policy(run_id),
    )
    return SimBatch(orchestrator, layout, panel, proposers, store, clock)


DEMO_PANEL_SETS: Final[Mapping[str, tuple[str, int]]] = {
    "DEMO-A-P-panel-orders.csv": ("DEMO-A-P", 3),
    "DEMO-A-C-panel-orders.csv": ("DEMO-A-C", 18),
}
"""DEMO panel order schedules (pilot-sized and confirmatory-sized), committed under
`generation/examples/demo-panel-orders/`."""


def demo_panels(set_ns: str, n: int) -> list[tuple[str, str]]:
    """`(panel_id, batch_id)` of a DEMO set: `DEMO-A-C01-N1` rates batch `DEMO-A-C01`."""
    return [(f"{set_ns}{i:02d}-N1", f"{set_ns}{i:02d}") for i in range(1, n + 1)]


def write_demo_panel_orders(out_dir: str | Path) -> dict[str, str]:
    """Write the DEMO panel order schedules; returns file name -> SHA-256."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    return {
        name: write_panel_order_csv(out / name, set_ns, demo_panels(set_ns, n))
        for name, (set_ns, n) in DEMO_PANEL_SETS.items()
    }


def _sorted_digest(path: Path) -> str:
    """SHA-256 of a JSONL file's lines in sorted order (independent of thread interleaving)."""
    if not path.exists():
        return hashlib.sha256(b"").hexdigest()
    lines = sorted(path.read_bytes().splitlines(keepends=True))
    return hashlib.sha256(b"".join(lines)).hexdigest()


def summarize(layout: RunLayout, config: BatchConfig) -> dict[str, Any]:
    """Counts and digests of a finished synthetic run (no recipes, no audio)."""
    slots = read_records(layout.log("slot"), SlotRecord)
    ratings = read_records(layout.log("rating"), RatingRecord)
    commits = read_records(layout.log("commit"), CommitRecord)
    decisions = [json.loads(line) for line in layout.log("decision").read_text().splitlines()]
    plays = [json.loads(line) for line in layout.log("play").read_text().splitlines()]
    timing = [json.loads(line) for line in layout.log("timing").read_text().splitlines()]
    final_books = {}
    for book in config.books:
        sub = [
            e for e in timing if e["event"] == "book_substituted" and e["book_id"] == book.book_id
        ]
        final_books[book.book_id] = f"{book.book_id}-FB" if sub else book.book_id
    final_commits = [c for c in commits if c.store_book_id == final_books[c.book_id]]
    heads = {}
    for c in commits:
        heads[c.store_book_id] = c.chain_head
    manifest = json.loads(layout.manifest.read_text(encoding="utf-8"))
    return {
        "format": SUMMARY_FORMAT,
        "format_version": 1,
        "run_id": layout.run_id,
        "batch_id": config.batch_id,
        "clock": manifest["clock"],
        "closed": manifest.get("closed_utc") is not None,
        "config_sha256": config.sha256(),
        "generation_config_sha256": manifest["generation_config_sha256"],
        "counts": {
            "slot_records": len(slots),
            "slot_outcomes": dict(sorted(Counter(s.outcome.value for s in slots).items())),
            "rating_records_per_rater": dict(sorted(Counter(r.rater_id for r in ratings).items())),
            "rating_placeholders": sum(1 for r in ratings if r.placeholder) // 3,
            "rating_missing": sum(1 for r in ratings if r.missing),
            "decision_records": len(decisions),
            "decision_actions": dict(sorted(Counter(d["action"] for d in decisions).items())),
            "commit_records": len(commits),
            "commits_in_final_books": len(final_commits),
            "commit_sources": dict(sorted(Counter(c.source for c in commits).items())),
            "fallback_scans": sum(1 for _ in _lines(layout.log("fallback_scan"))),
            "play_records": len(plays),
            "message_plays": sum(1 for p in plays if p["audio_kind"] == "message"),
            "timing_events": len(timing),
        },
        "final_chain_heads": dict(sorted(heads.items())),
        "log_digests_sorted_lines": {
            name: _sorted_digest(layout.root / rel) for name, rel in sorted(LOG_FILES.items())
        },
    }


def _lines(path: Path) -> list[bytes]:
    return path.read_bytes().splitlines() if path.exists() else []


def run_sim_batch(
    runs_root: str | Path,
    run_id: str,
    *,
    clock: Clock,
    config: BatchConfig | None = None,
    policy: RatingPolicy | None = None,
    p_failure: float = 0.05,
) -> dict[str, Any]:
    """Run one complete synthetic batch and return its `summarize` output."""
    sim = make_sim_batch(
        runs_root, run_id, clock=clock, config=config, policy=policy, p_failure=p_failure
    )
    with sim.panel:
        sim.orchestrator.run_batch()
    return summarize(sim.layout, sim.orchestrator.config)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--out", required=True, help="runs root (a DEMO run is created in it)")
    parser.add_argument("--run-id", default="DEMO-A-sim-01")
    parser.add_argument("--clock", choices=("manual", "scaled"), default="manual")
    parser.add_argument("--speed", type=float, default=500.0, help="ScaledClock speed")
    parser.add_argument("--summary", help="write the summary JSON here as well")
    parser.add_argument(
        "--panel-orders", action="store_true", help="only write the DEMO panel order CSVs to --out"
    )
    args = parser.parse_args(argv)
    if args.panel_orders:
        for name, digest in write_demo_panel_orders(args.out).items():
            sys.stdout.write(f"{digest}  {name}\n")
        return 0
    clock: Clock = ManualClock() if args.clock == "manual" else ScaledClock(args.speed)
    summary = run_sim_batch(args.out, args.run_id, clock=clock)
    text = document_text(summary)
    if args.summary:
        Path(args.summary).write_text(text, encoding="utf-8", newline="\n")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
