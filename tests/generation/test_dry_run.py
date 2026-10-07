"""Dry-run plan format (#22 owns this module and `dry-run-plan.schema.json`).

Skeleton test: the plan document and its link to the batch config. #22 extends this module
with the dry-run and completeness-check tests.
"""

import dataclasses
from pathlib import Path

import pytest

from av_generation.config import BatchConfig
from av_generation.dryrun import DryRunPlan, InjectedFallback
from av_generation.records import RecordError

ROOT = Path(__file__).resolve().parents[2]


def test_plan_is_keyed_by_what_stations_see():
    config = BatchConfig.read(ROOT / "generation/examples/demo-batch-config.json")
    book = config.book_of("A2").book_id
    slots = config.rating_slot_ids(book, "K-a1")
    plan = DryRunPlan(
        run_id="DEMO-dry-01",
        batch_id=config.batch_id,
        clock="scaled",
        clock_speed=100.0,
        zero_eligible=(InjectedFallback(book, "K-a1", "bank"),),
        force_unacceptable_slots=slots,
        designer_invalid_slots=(f"{config.book_of('A1').book_id}.Q-a2.r1s2",),
        designer_timeout_slots=(),
    ).check()
    assert DryRunPlan.from_dict(plan.to_dict()) == plan
    assert all(book not in s for s in plan.force_unacceptable_slots)
    with pytest.raises(RecordError):
        dataclasses.replace(plan, run_id="dry-01").check()
