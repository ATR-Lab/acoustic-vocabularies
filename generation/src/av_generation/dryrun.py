"""Synthetic-panel dry run of a full batch (#22). INTERFACE ONLY in the skeleton.

Runs the real orchestrator, A2, A3 (on the LLM host; mock server here), the A1 web app
driven by a scripted bot designer and three `rater.BotRater`s, all labelled `synthetic`
(`DEMO-` IDs). Real-time timing needs the LLM GPU: **pending (hardware)**; the
accelerated run (`clock.ScaledClock`) checks the logs here.

The injected cases are written before the run as `dry-run-plan.json` in the run
directory (`DryRunPlan`, `dry-run-plan.schema.json`, owned by #22), so the completeness
check compares the logs with what was injected: zero-eligible atoms (bot raters rate the
book's rating slots unacceptable; `force_unacceptable_slots` comes from
`BatchConfig.rating_slot_ids`), invalid and timed-out designer slots.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from av_generation.records import Document


@dataclass(frozen=True, slots=True)
class InjectedFallback:
    """One atom forced to zero eligible candidates and the fallback it must cause."""

    book_id: str
    atom_id: str
    expect: Literal["bank", "book"]
    """`bank`: a `fallback_bank` commit; `book`: a failed scan and whole-book substitution."""


@dataclass(frozen=True, slots=True)
class DryRunPlan(Document):
    """`dry-run-plan.json`: everything the dry run injects (restricted like the logs)."""

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


@dataclass(frozen=True, slots=True)
class CompletenessReport:
    """Output of the automated log-completeness check (#22)."""

    ok: bool
    problems: tuple[str, ...]
    counts: dict[str, int]


def check_log_completeness(
    run_dir: str | os.PathLike[str], *, plan: DryRunPlan | None = None
) -> CompletenessReport:
    """One commit per atom in each book's final store book (48), 576 slot records, 576
    rating records per rater, 0 message plays, and fallback events equal to the injected
    ones (`plan`, else `<run_dir>/dry-run-plan.json`) (#22)."""
    raise NotImplementedError("#22: log completeness check")
