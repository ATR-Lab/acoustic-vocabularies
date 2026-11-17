"""Synthetic whole-set dry run (#22, #266): every batch of an N-batch set in process.

- The set plan: profiles N/3 each, the panel orders from `panel_order_schedule` (each
  order N/6 times for N = 18 and 30), designers, DEMO IDs, and the refusal of N not a
  positive multiple of 3.
- The projected human time from the protocol budget.
- A 6-batch set end to end through the CLI on the manual clock (real orchestrator,
  ledger, A2 and A3 with the fake model, the scripted designer, the bot panel): exact
  counts, pinned (the run is deterministic apart from wall times).
- The counting of bank and whole-book fallbacks on a batch that has both.
"""

import collections
import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path

import pytest
from av_sound.store import check_book_id

from av_generation import _batch_sim as sim
from av_generation import dryrun
from av_generation import set_dry_run as sdr
from av_generation.clock import ManualClock
from av_generation.constants import PANEL_ORDERS
from av_generation.ids import Method, RunKind, is_demo

ROOT = Path(__file__).resolve().parents[2]
CI_OUT = ROOT / "generation/out/ci/set-dry-run"


@contextmanager
def no_fsync():
    """`os.fsync` as a no-op while the set runs (durability is not under test here)."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(os, "fsync", lambda fd: None)
        yield


# ---------------------------------------------------------------------------
# The set plan


@pytest.mark.parametrize("n", [18, 30])
def test_set_plan_balances_profiles_orders_and_designers(n):
    plan = sdr.set_plan(n, run_id=f"DEMO-set-dry-run-{n}")
    assert [b.index for b in plan] == list(range(1, n + 1))
    assert collections.Counter(b.profile.value for b in plan) == {
        "P1": n // 3,
        "P2": n // 3,
        "P3": n // 3,
    }
    assert collections.Counter(b.order_index for b in plan) == {i: n // 6 for i in range(1, 7)}
    for profile in ("P1", "P2", "P3"):
        mine = collections.Counter(b.order_index for b in plan if b.profile.value == profile)
        counts = [mine[i] for i in range(1, 7)]
        assert max(counts) - min(counts) <= 1
    assert collections.Counter(b.designer_id for b in plan) == {d: n // 3 for d in sdr.DESIGNERS}
    books = [book for b in plan for book in b.book_ids]
    assert len(set(books)) == 3 * n
    assert all(is_demo(x) and check_book_id(x) == x for x in books)
    assert all(is_demo(b.batch_id) and is_demo(b.panel_id) for b in plan)
    assert all(sorted(b.block_order) == [0, 1, 2, 3] for b in plan)
    assert sdr.set_plan(n, run_id=f"DEMO-set-dry-run-{n}") == plan
    # 30/84 and 30/88 share the Study A set: the plan depends on N and the run ID only
    config = sdr.set_batch_config(plan[0], run_id=f"DEMO-set-dry-run-{n}")
    assert config.batch_id == plan[0].batch_id and config.profile is plan[0].profile
    assert config.panel.order_index == plan[0].order_index
    methods = tuple(config.method_of(book).value for book in config.panel.order)
    assert methods == PANEL_ORDERS[plan[0].order_index - 1]
    assert config.book_of(Method.A1).designer_id == plan[0].designer_id
    assert config.seed_namespace == f"{plan[0].batch_id}-s1"
    assert sorted(config.atom_order) == sorted(sim.demo_batch_config().atom_order)


@pytest.mark.parametrize("n", [0, -3, 4, 10, 29, 102, True, 6.0])
def test_set_plan_refuses_n_not_a_positive_multiple_of_3(n):
    with pytest.raises(ValueError, match="multiple of 3|integer"):
        sdr.set_plan(n, run_id="DEMO-set")


@pytest.mark.parametrize("run_id", ["set-18", "A-C-secret", "DEMO-" + "x" * 60])
def test_set_plan_refuses_non_demo_or_long_run_ids(run_id):
    with pytest.raises(ValueError, match="DEMO-"):
        sdr.set_plan(18, run_id=run_id)


def test_cli_refuses_n_not_divisible_by_3(tmp_path, capsys):
    assert sdr.main(["--batches", "20", "--out", str(tmp_path), "--run-id", "DEMO-set"]) == 2
    assert "multiple of 3" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_projected_time_from_the_protocol_budget():
    assert sdr.projected_time(18) == {
        "panel_sessions": 72,
        "booked_minutes_per_session": 90,
        "max_slot_minutes_per_session": 80,
        "max_slot_minutes_per_atom": 20,
        "booked_panel_hours": 108.0,
        "max_slot_hours": 96.0,
        "rater_hours_booked": 324.0,
    }
    p30 = sdr.projected_time(30)
    assert (p30["panel_sessions"], p30["booked_panel_hours"], p30["max_slot_hours"]) == (
        120,
        180.0,
        160.0,
    )
    assert p30["rater_hours_booked"] == 540.0


# ---------------------------------------------------------------------------
# A 6-batch set end to end


@pytest.fixture(scope="module")
def six_batch_set(tmp_path_factory):
    out = tmp_path_factory.mktemp("set-dry-run")
    summary_dir = out / "summary"
    if os.environ.get("CI") == "true":  # uploaded with the generation-ci-<os> artifact
        summary_dir = CI_OUT
        shutil.rmtree(summary_dir, ignore_errors=True)
    with no_fsync():
        code = sdr.main(
            [
                "--batches",
                "6",
                "--out",
                str(out),
                "--run-id",
                "DEMO-set-dry-run-ci",
                "--summary",
                str(summary_dir),
                "--quiet",
            ]
        )
    return code, out / "DEMO-set-dry-run-ci", summary_dir


def test_six_batch_set_has_exact_counts(six_batch_set):
    code, run_root, summary_dir = six_batch_set
    assert code == 0
    summary = json.loads((run_root / "summary.json").read_text(encoding="utf-8"))
    assert (summary_dir / "summary.json").read_text(encoding="utf-8") == (
        run_root / "summary.json"
    ).read_text(encoding="utf-8")
    assert summary["format"] == sdr.SUMMARY_FORMAT and summary["synthetic"] is True
    assert summary["ok"] and summary["problems"] == []
    t = summary["totals"]
    assert t["batches"] == 6 and t["batches_by_profile"] == {"P1": 2, "P2": 2, "P3": 2}
    assert t["proposals"] == {"A1": 1152, "A2": 1152, "A3": 1152}
    assert t["rating_slot_records"] == 10_368
    assert t["rating_slot_records_per_rater_per_batch"] == [576]
    assert t["decisions"] == 1152
    assert t["commits_in_final_books"] == 288 and t["commits"] == {"selector": 288}
    assert t["selector_commits"] == {"A1": 96, "A2": 96, "A3": 96}
    assert t["message_plays"] == 0 and t["slot_refusals"] == 0
    assert t["panel_orders"] == {str(i): 1 for i in range(1, 7)}
    assert t["designers"] == {"D1": 2, "D2": 2, "D3": 2}
    assert t["designer_injected"] == {"invalid": 48, "skipped": 48}
    # pinned: the manual clock makes every count deterministic
    assert t["slot_outcomes"] == {
        "A1": {"event_too_short": 91, "invalid_json": 48, "timeout": 48, "valid": 965},
        "A2": {"event_too_short": 50, "valid": 1102},
        "A3": {"event_too_short": 94, "valid": 1058},
    }
    assert t["evaluations"] == {"missing": 0, "placeholders": 993, "rated": 9375, "records": 10368}
    # placeholders: one per rater for every invalid candidate
    assert t["evaluations"]["placeholders"] == 3 * sum(t["invalid_slots"].values())
    assert t["bank_fallbacks"] == {"A1": 0, "A2": 0, "A3": 0}
    assert t["whole_book_fallbacks"] == {"A1": 0, "A2": 0, "A3": 0}
    assert t["bank_fallback_rate_per_atom"] == {"A1": 0.0, "A2": 0.0, "A3": 0.0}
    assert summary["projected_time"] == sdr.projected_time(6)
    for batch in summary["batches"]:
        assert sdr.batch_problems(batch) == []
        assert batch["proposals"] == {"A1": 192, "A2": 192, "A3": 192}
        assert set(batch["rating_slot_records_per_rater"].values()) == {576}
        assert batch["sim_wall_s"] > 0
    # one run directory per batch, all synthetic DEMO runs
    runs = sorted(p.name for p in run_root.iterdir() if p.is_dir())
    assert runs == [f"DEMO-set-dry-run-ci-B{i:02d}" for i in range(1, 7)]
    manifest = json.loads((run_root / runs[0] / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["kind"] == RunKind.SYNTHETIC.value
    text = (summary_dir / "summary.md").read_text(encoding="utf-8")
    assert text.startswith("# Synthetic whole-set dry run `DEMO-set-dry-run-ci`\n\nSYNTHETIC.")
    assert "| Rating-slot records (per rater per batch) | 10368 (576) |" in text
    assert "Checks: every batch complete." in text


def test_batch_problems_name_every_shortfall():
    counts = {
        "batch_id": "DEMO-x-B01",
        "proposals": {"A1": 191, "A2": 192, "A3": 192},
        "rating_slot_records_per_rater": {"R01": 576, "R02": 575},
        "decisions": 191,
        "commits_in_final_books": 47,
        "slot_refusals": 1,
        "message_plays": 2,
        "closed": False,
    }
    problems = sdr.batch_problems(counts)
    assert len(problems) == 7 and all(p.startswith("DEMO-x-B01: ") for p in problems)


# ---------------------------------------------------------------------------
# Counting fallbacks


def test_count_batch_counts_bank_and_whole_book_fallbacks(tmp_path):
    """The #22 whole-book setup on the virtual clock: A3 atom 1 commits from the bank, A3
    atom 2 finds the single-recipe bank exhausted and the fallback book is substituted."""
    base = sim.demo_batch_config()
    fallback = dryrun.single_recipe_fallback(sim.demo_fallback(), base.profile.value)
    config = dryrun.dry_run_config(base, fallback=fallback)
    a3 = config.book_of(Method.A3).book_id
    forced = frozenset(
        s for atom in config.atom_order[:2] for s in config.rating_slot_ids(a3, atom)
    )
    run_id = "DEMO-set-count-01"
    with no_fsync():
        batch = sim.make_sim_batch(
            tmp_path,
            run_id,
            clock=ManualClock(),
            config=config,
            policy=sim.seeded_policy(run_id, force_unacceptable=forced),
            fallback=fallback,
            kind=RunKind.SYNTHETIC,
        )
        with batch.panel:
            batch.orchestrator.run_batch()
    counts = sdr.count_batch(batch.layout, config)
    assert counts["bank_fallbacks"] == {"A1": 0, "A2": 0, "A3": 1}
    assert counts["whole_book_fallbacks"] == {"A1": 0, "A2": 0, "A3": 1}
    assert counts["commits"]["fallback_book"] == 16
    assert counts["commits_in_final_books"] == 48
    assert counts["selector_commits"]["A3"] == 0
    assert counts["fallback_scans"] == 2
    counts["designer_injected"], counts["sim_wall_s"] = {"invalid": 0, "skipped": 0}, 1.0
    assert sdr.batch_problems(counts) == []
    totals = sdr._totals([counts])
    assert totals["bank_fallback_rate_per_atom"]["A3"] == 1 / 16
    assert totals["whole_book_fallback_rate_per_book"]["A3"] == 1.0
