"""Synthetic whole-set dry run of Study A (#22, #266): every batch of an N-batch set.

The full-batch dry run (`dryrun`, #22) runs one batch over HTTP in real or accelerated
time. This module runs every batch of a set of N batches in process with the manual
clock, so a whole set takes minutes, and counts what such a set produces. It feeds the
sample-size decision (#266: 18 batches under `provisional-18-64`, 30 under
`revised-30-84` and `revised-30-88`, whose Study A parts are the same).

What runs, and what stands in for people or hardware (as in `_batch_sim` and `dryrun`):

- real: the orchestrator, selector, panel session host and the commit and fallback rules
  (#20; run kind `synthetic`, purpose `dry_run`), one slot ledger per batch shared by the
  three proposers (#17), the validators (`av_sound.validate`) and the A2 proposer (#18);
- A3 (#17): the real `a3.A3Proposer` with the committed prompt set; the model is the
  repository's fake client (`llm_fake.ScriptedLlmClient`) answering what #16's mock
  server answers (`mock_llm.mock_output_text`, `mock_llm.count_tokens`): no model, no
  network;
- A1 (#19): `ScriptedDesigner`, the scripted bot designer of the #22 dry run in process
  (a uniform recipe per slot; per appointment 2 invalid and 2 skipped slots drawn by
  `dryrun.make_plan`) on the shared ledger;
- rater panel (#21): `_batch_sim.SyntheticPanel` (it drives the manual clock) with the
  #22 bot policy, `rater.bot_rating` under `rater.BotRatingPolicy(0.9)`: comfort
  acceptable with probability 0.9, association and distinguishability uniform on 1..7.

No zero-eligible atom is forced: bank and whole-book fallbacks happen only where the bots
and stand-ins produce them, so their rates are those of the bot policy, not of people.

The set (`set_plan`): batch i (1..N) has profile P1, P2, P3 in turn (N/3 each), batch ID
`<run_id>-B<ii>`, seed namespace `<batch>-s1`, panel `<batch>-N1` with its order index
from `orchestrator.panel_order_schedule(run_id, panels, profiles=...)` and its aliases
from `panel_aliases`, three bot seats, a hand designer `D1`..`D3` rotated within each
profile, three `DEMO-BK-XXXX` book IDs drawn for the set, the DEMO label permutation and
the DEMO atom order with its four appointment blocks in a seeded order per batch. Every
ID is `DEMO-`; every run is labelled `synthetic`. Draws use
`seeds.bot_seed_key(run_id, "driver", "set", ...)`.

Outputs: one run directory per batch under `<out>/<run_id>/` (logs, store; keep outside
git), and `summary.json` (`av-generation/set-dry-run-summary` v1) and `summary.md` beside
them: per batch and in total, proposals per method, slot outcomes, rating-slot records
per rater, rater-candidate evaluations (rated, placeholders, missing), commits by source,
bank and whole-book fallbacks per method with their rates per atom and per book, the
panel orders used, the simulation's own wall time, and the human time the protocol
budget projects for the set (`projected_time`). Wall times and the machine line are the
only values that change between two runs of the same set.

    uv run --project generation python -m av_generation.set_dry_run --batches 18 \\
        --out generation/out/set --run-id DEMO-set-dry-run-18 [--summary DIR]

Exit codes: 0 when every batch has the counts of a complete batch (`batch_problems`), 1
when one does not, 2 when the set is refused (`error: ...`, for example N not a positive
multiple of 3).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from fractions import Fraction
from pathlib import Path
from typing import Any, Final, Literal

from av_sound.fallback import FallbackSet
from av_sound.recipe import Profile
from av_sound.store import VocabularyStore, check_book_id
from av_sound.validate import validate
from av_sound.wav import file_sha256

from av_generation import _batch_sim as sim
from av_generation import batch_runner as br
from av_generation import dryrun
from av_generation.a2 import A2Proposer
from av_generation.a3 import A3Proposer
from av_generation.clock import Clock, ManualClock
from av_generation.config import BatchConfig, PanelAssignment, RaterSeat
from av_generation.constants import (
    APPOINTMENT_BOOKING_MS,
    APPOINTMENT_BUDGET_MS,
    APPOINTMENTS_PER_BATCH,
    ATOM_BUDGET_MS,
    ATOMS_PER_APPOINTMENT,
    ATOMS_PER_BOOK,
    PANEL_ORDERS,
    RATERS_PER_PANEL,
    RATING_SLOTS_PER_RATER,
    SLOTS_PER_BATCH,
    SLOTS_PER_BOOK,
    SLOTS_PER_ROUND,
)
from av_generation.ids import (
    ID_RE,
    PANEL_ALIAS_ALPHABET,
    STUDY_A_METHODS,
    Method,
    RunKind,
    Study,
    proposal_slot_id,
)
from av_generation.jsonio import document_text
from av_generation.ledger import SlotLedger
from av_generation.llm import ChatMessage, decoding_schema
from av_generation.llm_fake import ScriptedLlmClient
from av_generation.llm_manifest import load_llm_manifest, manifest_sha256
from av_generation.meanings import MeaningSet
from av_generation.mock_llm import count_tokens, mock_output_text
from av_generation.orchestrator import Orchestrator, panel_aliases, panel_order_schedule
from av_generation.outcomes import SlotOutcome, outcome_from_validation
from av_generation.panel_session import PanelSlot
from av_generation.prompts import PromptSet, default_prompt_set_dir, load_prompt_set
from av_generation.proposers import RoundProposer, RoundRequest, RoundResult
from av_generation.rater import BotRatingPolicy, bot_rating
from av_generation.records import (
    CommitRecord,
    DecisionRecord,
    RatingRecord,
    RecordWriter,
    SlotRecord,
    TimingEvent,
    cap_key,
    read_records,
)
from av_generation.rundir import RunLayout, create_run_dir
from av_generation.seeds import a1_seed_key, bot_seed_key, rng_for, seed_from_key, wire_seed

__all__ = [
    "SUMMARY_FORMAT",
    "ScriptedDesigner",
    "SetBatch",
    "SetInputs",
    "batch_problems",
    "count_batch",
    "load_set_inputs",
    "main",
    "projected_time",
    "run_set_batch",
    "run_set_dry_run",
    "set_batch_config",
    "set_plan",
    "summary_markdown",
    "write_summary",
]

SUMMARY_FORMAT: Final = "av-generation/set-dry-run-summary"
MAX_BATCHES: Final = 99
DESIGNERS: Final[tuple[str, ...]] = ("D1", "D2", "D3")
P_COMFORT_ACCEPTABLE: Final = 0.9
"""The #22 bot policy (Proposed in #22): comfort acceptable with p = 0.9."""
PROFILES: Final[tuple[Profile, ...]] = tuple(Profile)
_BATCH_SUFFIX_LEN: Final = len("-B00-s1")


# ---------------------------------------------------------------------------
# The set


@dataclass(frozen=True, slots=True)
class SetBatch:
    """One batch of the synthetic set (`set_plan`)."""

    index: int
    batch_id: str
    """Also the batch's run ID (its run directory under `<out>/<run_id>/`)."""
    profile: Profile
    panel_id: str
    order_index: int
    designer_id: str
    book_ids: tuple[str, str, str]
    """The A1, A2 and A3 books, in method order (restricted in a real set)."""
    block_order: tuple[int, ...]
    """The DEMO atom order's appointment blocks (0..3) in this batch's order."""


def _check_n(n_batches: int) -> None:
    if isinstance(n_batches, bool) or not isinstance(n_batches, int):
        raise ValueError(f"the number of batches must be an integer, got {n_batches!r}")
    if not 0 < n_batches <= MAX_BATCHES or n_batches % len(PROFILES):
        raise ValueError(
            f"the number of batches must be a positive multiple of {len(PROFILES)} "
            f"(equal profiles) up to {MAX_BATCHES}, got {n_batches}"
        )


def _check_run_id(run_id: str) -> None:
    if not run_id.startswith("DEMO-") or not ID_RE.fullmatch(run_id + "-B00-s1"):
        raise ValueError(
            f"run ID {run_id!r} must start with DEMO- and leave room for batch IDs "
            f"(at most {64 - _BATCH_SUFFIX_LEN} characters)"
        )


def _book_ids(run_id: str, n: int) -> list[str]:
    """`n` distinct anonymous `DEMO-BK-XXXX` book IDs (redrawn when `check_book_id`
    refuses one or it repeats)."""
    rng = rng_for(bot_seed_key(run_id, "driver", "set", "books"))
    out: list[str] = []
    while len(out) < n:
        chars = "".join(
            PANEL_ALIAS_ALPHABET[int(i)] for i in rng.integers(0, len(PANEL_ALIAS_ALPHABET), size=4)
        )
        book_id = f"DEMO-BK-{chars}"
        try:
            check_book_id(book_id)
        except ValueError:
            continue
        if book_id not in out:
            out.append(book_id)
    return out


def set_plan(n_batches: int, *, run_id: str) -> tuple[SetBatch, ...]:
    """The N batches of the synthetic set `run_id` (see the module docstring); raises
    `ValueError` unless N is a positive multiple of 3 (at most 99)."""
    _check_n(n_batches)
    _check_run_id(run_id)
    batch_ids = [f"{run_id}-B{i:02d}" for i in range(1, n_batches + 1)]
    profiles = [PROFILES[(i - 1) % len(PROFILES)] for i in range(1, n_batches + 1)]
    panels = [f"{b}-N1" for b in batch_ids]
    orders = panel_order_schedule(run_id, panels, profiles=dict(zip(panels, profiles, strict=True)))
    books = _book_ids(run_id, 3 * n_batches)
    seen: Counter[Profile] = Counter()
    out = []
    for i, (batch_id, profile, panel_id) in enumerate(
        zip(batch_ids, profiles, panels, strict=True), start=1
    ):
        k = seen[profile]
        seen[profile] += 1
        rng = rng_for(bot_seed_key(run_id, "driver", "set", "atoms", i))
        out.append(
            SetBatch(
                index=i,
                batch_id=batch_id,
                profile=profile,
                panel_id=panel_id,
                order_index=orders[panel_id],
                designer_id=DESIGNERS[(k + PROFILES.index(profile)) % len(DESIGNERS)],
                book_ids=(books[3 * i - 3], books[3 * i - 2], books[3 * i - 1]),
                block_order=tuple(int(b) for b in rng.permutation(APPOINTMENTS_PER_BATCH)),
            )
        )
    return tuple(out)


def set_batch_config(
    batch: SetBatch, *, run_id: str, base: BatchConfig | None = None
) -> BatchConfig:
    """The batch config of one set batch: the DEMO batch config (`base`) with the set's
    batch, profile, books, atom order, panel and seed namespace."""
    base = base if base is not None else sim.demo_batch_config()
    blocks = [
        base.atom_order[b * ATOMS_PER_APPOINTMENT : (b + 1) * ATOMS_PER_APPOINTMENT]
        for b in range(APPOINTMENTS_PER_BATCH)
    ]
    atom_order = tuple(atom for b in batch.block_order for atom in blocks[b])
    books = tuple(
        replace(
            base.book_of(method),
            book_id=book_id,
            designer_id=batch.designer_id if method is Method.A1 else None,
        )
        for method, book_id in zip(STUDY_A_METHODS, batch.book_ids, strict=True)
    )
    by_method = {
        method.value: book_id
        for method, book_id in zip(STUDY_A_METHODS, batch.book_ids, strict=True)
    }
    order = PANEL_ORDERS[batch.order_index - 1]
    panel = PanelAssignment(
        panel_id=batch.panel_id,
        order_index=batch.order_index,
        order=(by_method[order[0]], by_method[order[1]], by_method[order[2]]),
        aliases=dict(panel_aliases(run_id, batch.panel_id, batch.book_ids)),
        raters=dryrun.BOT_SEATS,
    )
    config = replace(
        base,
        batch_id=batch.batch_id,
        profile=batch.profile,
        atom_order=atom_order,
        books=books,
        panel=panel,
        seed_namespace=f"{batch.batch_id}-s1",
    )
    return config.check().check_consistency()


# ---------------------------------------------------------------------------
# Stand-ins: the A1 bot designer, the mock model, the bot raters


class ScriptedDesigner:
    """A1 stand-in in process (`proposers.RoundProposer`): the scripted bot designer of
    the #22 dry run on the batch's shared slot ledger.

    Each slot is reserved on the ledger, then gets a uniform recipe from
    `rng_for(a1_seed_key(...))` (validated like every candidate), `"{"` on
    `invalid_slots` (`invalid_json`) or nothing on `timeout_slots` (`timeout`), and is
    consumed. No think time: the manual clock does not move during proposals."""

    method = Method.A1

    def __init__(
        self,
        ledger: SlotLedger,
        *,
        clock: Clock,
        designer_id: str,
        invalid_slots: frozenset[str] = frozenset(),
        timeout_slots: frozenset[str] = frozenset(),
    ) -> None:
        self._ledger = ledger
        self._clock = clock
        self._designer_id = designer_id
        self._invalid = invalid_slots
        self._timeouts = timeout_slots

    def propose_round(self, request: RoundRequest) -> RoundResult:
        records = []
        for slot in range(1, SLOTS_PER_ROUND + 1):
            slot_id = proposal_slot_id(request.book_id, request.atom_id, request.round, slot)
            ticket = self._ledger.reserve(
                cap_key(Study.A, request.atom_id, book_id=request.book_id),
                slot_id,
                study=Study.A,
                method=Method.A1,
            )
            key = a1_seed_key(request.seed_namespace, request.atom_id, request.round, slot)
            fields: dict[str, Any] = {
                "run_id": request.run_id,
                "study": Study.A,
                "method": Method.A1,
                "slot_id": slot_id,
                "profile": request.profile,
                "atom_id": request.atom_id,
                "slot": slot,
                "slot_index": ticket.slot_index,
                "t_open_ms": ticket.t_open_ms,
                "t_ms": self._clock.now_ms(),
                "batch_id": request.batch_id,
                "book_id": request.book_id,
                "round": request.round,
                "designer_id": self._designer_id,
                "seed_key": key,
                "seed": seed_from_key(key),
                "latency_ms": 0,
            }
            if slot_id in self._timeouts:
                record = SlotRecord(outcome=SlotOutcome.TIMEOUT, **fields)
            elif slot_id in self._invalid:
                record = SlotRecord(outcome=SlotOutcome.INVALID_JSON, raw_output="{", **fields)
            else:
                recipe = sim.uniform_recipe(rng_for(key))
                result = validate(
                    recipe,
                    request.profile,
                    request.book.references(),
                    threshold=request.book.threshold,
                )
                record = SlotRecord(
                    outcome=outcome_from_validation(result),
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
            records.append(self._ledger.consume(record))
        return RoundResult(
            Method.A1, request.book_id, request.atom_id, request.round, tuple(records)
        )


def mock_model_client(calls: int = SLOTS_PER_BOOK) -> ScriptedLlmClient:
    """The fake model of one batch: every call answers what #16's mock server answers
    (a recipe sampled from the decoding schema, seeded by the slot seed and the prompt),
    and prompt tokens are counted by the mock tokenizer."""

    def answer(messages: Sequence[ChatMessage], schema: Mapping[str, Any], seed_key: str) -> str:
        return mock_output_text(schema, wire_seed(seed_from_key(seed_key)), messages)

    def tokens(messages: Sequence[ChatMessage]) -> int:
        return count_tokens(messages)

    return ScriptedLlmClient([answer] * calls, token_counter=tokens)


def bot_policy(run_id: str, policy: BotRatingPolicy) -> sim.RatingPolicy:
    """The #22 bot raters' draws (`rater.bot_rating`, stream `BOT|run|rater|rating|slot`)
    as a `_batch_sim.SyntheticPanel` policy."""

    def rate(seat: RaterSeat, slot: PanelSlot) -> sim.SimRating | None:
        draw = bot_rating(
            run_id,
            seat.rater_id,
            slot.rating_slot_id,
            ask_distinguishability=slot.ask_distinguishability,
            policy=policy,
        )
        if draw.missing:
            return None
        comfort: Literal["acceptable", "unacceptable"] = (
            "acceptable" if draw.comfort == "acceptable" else "unacceptable"
        )
        return sim.SimRating(draw.association, draw.distinguishability, comfort, draw.rt_ms)

    return rate


# ---------------------------------------------------------------------------
# Running a batch


@dataclass(frozen=True, slots=True)
class SetInputs:
    """What every batch of the set reads (the committed DEMO and #16/#17 files)."""

    meanings: MeaningSet
    fallback: FallbackSet
    prompt_set: PromptSet
    llm_manifest_sha256: str
    base: BatchConfig


def load_set_inputs() -> SetInputs:
    meanings = sim.demo_meanings()
    return SetInputs(
        meanings=meanings,
        fallback=sim.demo_fallback(),
        prompt_set=load_prompt_set(default_prompt_set_dir(), meanings=meanings),
        llm_manifest_sha256=manifest_sha256(),
        base=sim.demo_batch_config(),
    )


def run_set_batch(
    runs_root: str | os.PathLike[str],
    batch: SetBatch,
    *,
    run_id: str,
    inputs: SetInputs,
) -> dict[str, Any]:
    """Run one batch of the set to the end on a `ManualClock` and return its counts
    (`count_batch`) with the simulation's wall time."""
    config = set_batch_config(batch, run_id=run_id, base=inputs.base)
    gen = br.demo_generation_config(
        config,
        inputs.meanings,
        inputs.fallback,
        prompt_set=inputs.prompt_set,
        llm_manifest_sha256=inputs.llm_manifest_sha256,
    )
    batch_inputs = br.BatchInputs(
        config=config,
        generation_config=gen,
        meanings=inputs.meanings,
        fallback=inputs.fallback,
        prompt_set=inputs.prompt_set,
        llm_manifest=load_llm_manifest(),
        llm_manifest_sha256=inputs.llm_manifest_sha256,
    )
    br.check_batch_start(batch_inputs, kind=RunKind.SYNTHETIC, run_id=batch.batch_id)
    clock = ManualClock()
    plan = dryrun.make_plan(
        config,
        run_id=batch.batch_id,
        clock=clock,
        zero_eligible=(),
        designer_think_ms=None,
        mock_llm_latency_ms=None,
    )
    started = time.perf_counter()
    layout = create_run_dir(runs_root, batch.batch_id, RunKind.SYNTHETIC)
    ledger = SlotLedger(
        layout.log("slot"),
        run_id=layout.run_id,
        clock=clock,
        refusals=RecordWriter(layout.log("slot_refusal")),
        timing=RecordWriter(layout.log("timing")),
    )
    proposers: dict[Method, RoundProposer] = {
        Method.A1: ScriptedDesigner(
            ledger,
            clock=clock,
            designer_id=batch.designer_id,
            invalid_slots=frozenset(plan.designer_invalid_slots),
            timeout_slots=frozenset(plan.designer_timeout_slots),
        ),
        Method.A2: A2Proposer(ledger, clock=clock),
        Method.A3: A3Proposer(
            mock_model_client(), ledger, inputs.prompt_set, decoding_schema(), clock=clock
        ),
    }
    orchestrator = Orchestrator(
        config,
        layout,
        proposers,
        VocabularyStore(layout.store_dir, clock=clock.utc_now),
        inputs.fallback,
        clock=clock,
        generation_config=gen,
        meanings=inputs.meanings,
        kind=RunKind.SYNTHETIC,
        purpose="dry_run",
        slot_event_lead_ms=0,
    )
    panel = sim.SyntheticPanel(
        orchestrator.panel_host(),
        clock,
        bot_policy(batch.batch_id, BotRatingPolicy(p_comfort_acceptable=P_COMFORT_ACCEPTABLE)),
    )
    with panel:
        orchestrator.run_batch()
    wall_s = time.perf_counter() - started
    counts = count_batch(layout, config)
    counts["designer_injected"] = {
        "invalid": len(plan.designer_invalid_slots),
        "skipped": len(plan.designer_timeout_slots),
    }
    counts["sim_wall_s"] = round(wall_s, 3)
    return counts


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def count_batch(layout: RunLayout, config: BatchConfig) -> dict[str, Any]:
    """The counts of one finished batch, from its run directory."""
    method_of = {b.book_id: b.method.value for b in config.books}
    # every record was schema-checked when it was written (`RecordWriter`)
    slots = read_records(layout.log("slot"), SlotRecord, validate=False)
    ratings = read_records(layout.log("rating"), RatingRecord, validate=False)
    commits = read_records(layout.log("commit"), CommitRecord, validate=False)
    decisions = read_records(layout.log("decision"), DecisionRecord, validate=False)
    timing = read_records(layout.log("timing"), TimingEvent, validate=False)
    plays = _jsonl(layout.log("play"))
    substituted = {e.book_id for e in timing if e.event == "book_substituted" and e.book_id}
    final = {b: f"{b}-FB" if b in substituted else b for b in method_of}
    methods = [m.value for m in STUDY_A_METHODS]
    outcomes = {
        m: {
            code: n
            for code, n in sorted(
                Counter(s.outcome.value for s in slots if s.method.value == m).items()
            )
        }
        for m in methods
    }
    valid = SlotOutcome.VALID.value
    manifest = json.loads(layout.manifest.read_text(encoding="utf-8"))
    return {
        "batch_id": config.batch_id,
        "profile": config.profile.value,
        "panel_id": config.panel.panel_id,
        "order_index": config.panel.order_index,
        "order": "|".join(PANEL_ORDERS[config.panel.order_index - 1]),
        "designer_id": config.book_of(Method.A1).designer_id,
        "closed": manifest.get("closed_utc") is not None,
        "proposals": {m: sum(outcomes[m].values()) for m in methods},
        "slot_outcomes": outcomes,
        "valid_slots": {m: outcomes[m].get(valid, 0) for m in methods},
        "invalid_slots": {
            m: sum(outcomes[m].values()) - outcomes[m].get(valid, 0) for m in methods
        },
        "rating_slot_records_per_rater": dict(sorted(Counter(r.rater_id for r in ratings).items())),
        "evaluations": {
            "records": len(ratings),
            "rated": sum(1 for r in ratings if not r.placeholder and not r.missing),
            "placeholders": sum(1 for r in ratings if r.placeholder),
            "missing": sum(1 for r in ratings if r.missing),
        },
        "decisions": len(decisions),
        "commits": dict(sorted(Counter(c.source for c in commits).items())),
        "commits_in_final_books": sum(1 for c in commits if c.store_book_id == final[c.book_id]),
        "selector_commits": {
            m: sum(1 for c in commits if c.source == "selector" and method_of[c.book_id] == m)
            for m in methods
        },
        "bank_fallbacks": {
            m: sum(1 for c in commits if c.source == "fallback_bank" and method_of[c.book_id] == m)
            for m in methods
        },
        "whole_book_fallbacks": {
            m: sum(1 for b in substituted if method_of[b] == m) for m in methods
        },
        "fallback_scans": len(_jsonl(layout.log("fallback_scan"))),
        "slot_refusals": len(_jsonl(layout.log("slot_refusal"))),
        "message_plays": sum(1 for p in plays if p.get("audio_kind") == "message"),
    }


def batch_problems(counts: Mapping[str, Any]) -> list[str]:
    """What a complete synthetic batch must show: 192 proposals per method, 576
    rating-slot records per rater (1,728 in all), 192 decisions, 48 commits in the final
    books, a closed run manifest, no slot refusal and no message play."""
    problems = []
    for method, n in counts["proposals"].items():
        if n != SLOTS_PER_BOOK:
            problems.append(f"{method}: {n} proposals, expected {SLOTS_PER_BOOK}")
    per_rater = counts["rating_slot_records_per_rater"]
    if len(per_rater) != RATERS_PER_PANEL or set(per_rater.values()) != {RATING_SLOTS_PER_RATER}:
        problems.append(f"rating-slot records per rater {per_rater}")
    expected = {
        "decisions": SLOTS_PER_BATCH // SLOTS_PER_ROUND,
        "commits_in_final_books": ATOMS_PER_BOOK * len(STUDY_A_METHODS),
        "slot_refusals": 0,
        "message_plays": 0,
    }
    for key, want in expected.items():
        if counts[key] != want:
            problems.append(f"{key} {counts[key]}, expected {want}")
    if not counts["closed"]:
        problems.append("run manifest not closed")
    return [f"{counts['batch_id']}: {p}" for p in problems]


# ---------------------------------------------------------------------------
# The set: totals, projections, summary


def projected_time(n_batches: int) -> dict[str, Any]:
    """Human time the protocol budget projects for N batches (Study A §3.3): 4 panel
    sessions per batch booked at 90 min, at most 80 min of slots per session (20 min per
    atom), 3 raters per panel."""
    sessions = n_batches * APPOINTMENTS_PER_BATCH
    booked_h = Fraction(sessions * APPOINTMENT_BOOKING_MS, 3_600_000)
    slot_h = Fraction(n_batches * ATOMS_PER_BOOK * ATOM_BUDGET_MS, 3_600_000)
    return {
        "panel_sessions": sessions,
        "booked_minutes_per_session": APPOINTMENT_BOOKING_MS // 60_000,
        "max_slot_minutes_per_session": APPOINTMENT_BUDGET_MS // 60_000,
        "max_slot_minutes_per_atom": ATOM_BUDGET_MS // 60_000,
        "booked_panel_hours": float(booked_h),
        "max_slot_hours": float(slot_h),
        "rater_hours_booked": float(booked_h * RATERS_PER_PANEL),
    }


def _sum_maps(maps: Sequence[Mapping[str, int]]) -> dict[str, int]:
    total: Counter[str] = Counter()
    for m in maps:
        total.update(m)
    return dict(sorted(total.items()))


def _totals(batches: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    methods = [m.value for m in STUDY_A_METHODS]
    n = len(batches)
    bank = _sum_maps([b["bank_fallbacks"] for b in batches])
    book = _sum_maps([b["whole_book_fallbacks"] for b in batches])
    evaluations = _sum_maps([b["evaluations"] for b in batches])
    return {
        "batches": n,
        "proposals": _sum_maps([b["proposals"] for b in batches]),
        "slot_outcomes": {m: _sum_maps([b["slot_outcomes"][m] for b in batches]) for m in methods},
        "valid_slots": _sum_maps([b["valid_slots"] for b in batches]),
        "invalid_slots": _sum_maps([b["invalid_slots"] for b in batches]),
        "rating_slot_records": evaluations["records"],
        "rating_slot_records_per_rater_per_batch": sorted(
            {v for b in batches for v in b["rating_slot_records_per_rater"].values()}
        ),
        "evaluations": evaluations,
        "decisions": sum(b["decisions"] for b in batches),
        "commits": _sum_maps([b["commits"] for b in batches]),
        "commits_in_final_books": sum(b["commits_in_final_books"] for b in batches),
        "selector_commits": _sum_maps([b["selector_commits"] for b in batches]),
        "bank_fallbacks": bank,
        "bank_fallback_rate_per_atom": {m: bank.get(m, 0) / (n * ATOMS_PER_BOOK) for m in methods},
        "whole_book_fallbacks": book,
        "whole_book_fallback_rate_per_book": {m: book.get(m, 0) / n for m in methods},
        "fallback_scans": sum(b["fallback_scans"] for b in batches),
        "slot_refusals": sum(b["slot_refusals"] for b in batches),
        "message_plays": sum(b["message_plays"] for b in batches),
        "designer_injected": _sum_maps([b["designer_injected"] for b in batches]),
        "panel_orders": {
            str(i): sum(1 for b in batches if b["order_index"] == i)
            for i in range(1, len(PANEL_ORDERS) + 1)
        },
        "panel_orders_by_profile": {
            p.value: {
                str(i): sum(1 for b in batches if b["profile"] == p.value and b["order_index"] == i)
                for i in range(1, len(PANEL_ORDERS) + 1)
            }
            for p in PROFILES
        },
        "batches_by_profile": dict(sorted(Counter(b["profile"] for b in batches).items())),
        "designers": dict(sorted(Counter(b["designer_id"] for b in batches).items())),
        "sim_wall_s": round(sum(b["sim_wall_s"] for b in batches), 3),
    }


def run_set_dry_run(
    out: str | os.PathLike[str],
    run_id: str,
    n_batches: int,
    *,
    progress: bool = False,
) -> dict[str, Any]:
    """Run every batch of the set and return the summary (`summary.json` contents)."""
    plan = set_plan(n_batches, run_id=run_id)
    inputs = load_set_inputs()
    root = Path(out) / run_id
    root.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    batches = []
    for batch in plan:
        counts = run_set_batch(root, batch, run_id=run_id, inputs=inputs)
        batches.append(counts)
        if progress:
            sys.stderr.write(
                f"{batch.batch_id} {batch.profile.value} order {batch.order_index}: "
                f"{counts['sim_wall_s']:.1f} s\n"
            )
    problems = [p for b in batches for p in batch_problems(b)]
    return {
        "format": SUMMARY_FORMAT,
        "format_version": 1,
        "synthetic": True,
        "note": (
            "SYNTHETIC: bot raters, a scripted bot designer and a fake model; DEMO IDs only. "
            "Counts and fallback rates describe the stand-ins, not people."
        ),
        "run_id": run_id,
        "set_ns": run_id,
        "clock": "manual",
        "bot_policy": {
            "p_comfort_acceptable": P_COMFORT_ACCEPTABLE,
            "association": "uniform 1..7",
            "distinguishability": "uniform 1..7",
        },
        "stand_ins": {
            "A1": "scripted bot designer (uniform recipes; per appointment 2 invalid, 2 skipped)",
            "A2": "real A2 proposer",
            "A3": "real A3 proposer; fake model client answering as the #16 mock server",
            "panel": "synthetic panel, three bot seats with the #22 policy",
        },
        "expected_per_batch": {
            "proposals_per_method": SLOTS_PER_BOOK,
            "proposals": SLOTS_PER_BATCH,
            "rating_slot_records_per_rater": RATING_SLOTS_PER_RATER,
            "rating_slot_records": RATERS_PER_PANEL * RATING_SLOTS_PER_RATER,
            "decisions": SLOTS_PER_BATCH // SLOTS_PER_ROUND,
            "commits_in_final_books": ATOMS_PER_BOOK * len(STUDY_A_METHODS),
        },
        "totals": _totals(batches),
        "projected_time": projected_time(n_batches),
        "problems": problems,
        "ok": not problems,
        "sim_wall_s_total": round(time.perf_counter() - started, 3),
        "machine": {
            "system": platform.system(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
            "python": platform.python_version(),
        },
        "batches": batches,
    }


def _pct(x: float) -> str:
    return f"{100 * x:.2f}%"


def _codes(outcomes: Mapping[str, int]) -> str:
    """Invalid outcome codes and counts, e.g. `event_too_short 12, timeout 8`."""
    bad = [(code, n) for code, n in outcomes.items() if code != SlotOutcome.VALID.value and n]
    return ", ".join(f"{code} {n}" for code, n in bad) or "-"


def summary_markdown(summary: Mapping[str, Any]) -> str:
    """`summary.md`: the totals, the per-method counts and the projected human time."""
    t = summary["totals"]
    p = summary["projected_time"]
    e = t["evaluations"]
    lines = [
        f"# Synthetic whole-set dry run `{summary['run_id']}`",
        "",
        f"SYNTHETIC. {summary['note'].removeprefix('SYNTHETIC: ')}",
        "",
        "| Quantity | Value |",
        "| --- | ---: |",
        f"| Batches (P1 / P2 / P3) | {t['batches']} "
        f"({' / '.join(str(t['batches_by_profile'].get(x.value, 0)) for x in PROFILES)}) |",
        f"| Panel sessions | {p['panel_sessions']} |",
        f"| Proposals | {sum(t['proposals'].values())} |",
        f"| Rating-slot records (per rater per batch) | {t['rating_slot_records']} "
        f"({', '.join(map(str, t['rating_slot_records_per_rater_per_batch']))}) |",
        f"| Rater-candidate evaluations rated | {e.get('rated', 0)} |",
        f"| Placeholder records (invalid candidates, not rated) | {e.get('placeholders', 0)} |",
        f"| Missing ratings | {e.get('missing', 0)} |",
        f"| Decisions | {t['decisions']} |",
        f"| Commits in final books | {t['commits_in_final_books']} |",
        f"| Panel orders 1-6 | {' / '.join(str(t['panel_orders'][str(i)]) for i in range(1, 7))} |",
        f"| Message plays, slot refusals | {t['message_plays']}, {t['slot_refusals']} |",
        f"| Simulation wall time | {summary['sim_wall_s_total']:.0f} s |",
        "",
        "| Method | Proposals | Valid | Invalid (outcome codes) | Selector commits "
        "| Bank fallbacks (per atom) | Whole-book fallbacks (per book) |",
        "| --- | ---: | ---: | --- | ---: | ---: | ---: |",
    ]
    for method in (m.value for m in STUDY_A_METHODS):
        lines.append(
            f"| {method} | {t['proposals'].get(method, 0)} | {t['valid_slots'].get(method, 0)} "
            f"| {t['invalid_slots'].get(method, 0)} ({_codes(t['slot_outcomes'][method])}) "
            f"| {t['selector_commits'].get(method, 0)} "
            f"| {t['bank_fallbacks'].get(method, 0)} "
            f"({_pct(t['bank_fallback_rate_per_atom'][method])}) "
            f"| {t['whole_book_fallbacks'].get(method, 0)} "
            f"({_pct(t['whole_book_fallback_rate_per_book'][method])}) |"
        )
    lines += [
        "",
        "Projected human time from the protocol budget (Study A §3.3):",
        "",
        "| Quantity | Value |",
        "| --- | ---: |",
        f"| Panel sessions | {p['panel_sessions']} |",
        f"| Booked panel hours ({p['booked_minutes_per_session']} min per session) "
        f"| {p['booked_panel_hours']:g} |",
        f"| Rater hours booked (3 raters per panel) | {p['rater_hours_booked']:g} |",
        f"| Maximum slot time ({p['max_slot_minutes_per_atom']} min per atom, "
        f"{p['max_slot_minutes_per_session']} min per session) | {p['max_slot_hours']:g} h |",
        "",
        f"Checks: {'every batch complete' if summary['ok'] else '; '.join(summary['problems'])}.",
        "",
    ]
    return "\n".join(lines)


def write_summary(summary: Mapping[str, Any], out_dir: str | os.PathLike[str]) -> None:
    """Write `summary.json` and `summary.md` into `out_dir`."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(document_text(summary), encoding="utf-8", newline="\n")
    (out / "summary.md").write_text(summary_markdown(summary), encoding="utf-8", newline="\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m av_generation.set_dry_run",
        description="Synthetic whole-set dry run of Study A (every batch, manual clock).",
    )
    parser.add_argument("--batches", type=int, required=True, help="N, a multiple of 3")
    parser.add_argument("--out", required=True, help="runs root (keep it outside git)")
    parser.add_argument("--run-id", required=True, help="DEMO-... run and set namespace")
    parser.add_argument("--summary", help="also write summary.json and summary.md here")
    parser.add_argument("--quiet", action="store_true", help="no progress line per batch")
    args = parser.parse_args(argv)
    try:
        summary = run_set_dry_run(args.out, args.run_id, args.batches, progress=not args.quiet)
    except (ValueError, OSError) as err:
        sys.stderr.write(f"error: {err}\n")
        return 2
    write_summary(summary, Path(args.out) / args.run_id)
    if args.summary:
        write_summary(summary, args.summary)
    sys.stdout.write(summary_markdown(summary))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
