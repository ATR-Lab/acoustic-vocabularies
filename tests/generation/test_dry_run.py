"""Synthetic-panel dry run (#22 owns this module and `dry-run-plan.schema.json`).

- The plan: the synthetic batch config, the injected cases and their link to the rating
  slots the stations see (`BatchConfig.rating_slot_ids`), the single-recipe bank of the
  whole-book run.
- The log-completeness checker on a deterministic synthetic batch (virtual clock,
  simulated proposers, the injected cases scripted the same way the driver injects them)
  and on copies of it with one defect each: every acceptance count is checked and every
  defect is reported.
- The timing CSV: exact durations on the virtual clock, the 20-min and 80-min checks,
  events of two processes.
- The independent tally against the #24 audit, the deterministic bundle and the CLI.
- An accelerated end-to-end run of a reduced batch (appointment 1) with the real
  components: the A1 app worked by the bot designer over HTTP, A2, A3 against #16's mock
  server, keyed bot stations on the real panel server; its logs pass the completeness
  check and the forced bank and whole-book fallbacks are logged as injected.
"""

import csv
import dataclasses
import json
import os
import shutil
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from av_sound.fallback import scan_fallback
from av_sound.recipe import Profile
from av_sound.store import VocabularyStore

from av_generation import _batch_sim as sim
from av_generation import dryrun
from av_generation.audit import build_audit
from av_generation.clock import ManualClock, ScaledClock, utc_text
from av_generation.config import BatchConfig
from av_generation.constants import ATOM_BUDGET_MS, PANEL_ORDERS
from av_generation.dryrun import DryRunPlan, InjectedFallback
from av_generation.ids import Method, RunKind, proposal_slot_id
from av_generation.orchestrator import Orchestrator
from av_generation.records import (
    CommitRecord,
    FallbackScanRecord,
    RecordError,
    RecordWriter,
    TimingEvent,
    read_records,
)
from av_generation.rundir import create_run_dir

ROOT = Path(__file__).resolve().parents[2]
CI_OUT = ROOT / "generation/out/ci/dry-run"


@contextmanager
def no_fsync():
    """`os.fsync` as a no-op while a fixture builds its run: durability is not what these
    tests check, and fsynced log lines dominate the run time on Windows runners."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(os, "fsync", lambda fd: None)
        yield


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
    with pytest.raises(RecordError):
        dataclasses.replace(plan, appointments=(0,)).check()


# ---------------------------------------------------------------------------
# Synthetic config and plan


def test_dry_run_config_is_a_demo_batch_with_bot_seats_and_its_own_namespaces():
    base = sim.demo_batch_config()
    config = dryrun.dry_run_config(base)
    assert config.batch_id == "DEMO-DRY-P01" and config.seed_namespace == "DEMO-DRY-P01-s1"
    assert config.set == "demo" and config.profile is base.profile
    assert config.atom_order == base.atom_order and config.labels == base.labels
    assert {s.kind for s in config.panel.raters} == {"bot"}
    methods = tuple(config.method_of(b).value for b in config.panel.order)
    assert methods == PANEL_ORDERS[config.panel.order_index - 1]
    assert config.panel.panel_id == "DEMO-DRY-P01-N1"
    assert set(config.panel.aliases) == {b.book_id for b in config.books}
    # deterministic: the same namespaces give the same config
    assert dryrun.dry_run_config(base).sha256() == config.sha256()


def test_plan_injections_follow_the_config_and_the_seeded_draws():
    config = dryrun.dry_run_config()
    plan = dryrun.make_plan(config, run_id="DEMO-dry-plan-01", clock=ScaledClock(20))
    assert plan.appointments == (1, 2, 3, 4) and plan.clock == "scaled"
    assert plan.clock_speed == 20.0 and plan.token_count_cap_ms == 40_000
    assert plan.p_comfort_acceptable == 0.9
    [z] = plan.zero_eligible
    assert (z.book_id, z.atom_id, z.expect) == (
        config.book_of(Method.A2).book_id,
        config.atom_order[6],
        "bank",
    )
    assert plan.force_unacceptable_slots == tuple(
        sorted(config.rating_slot_ids(z.book_id, z.atom_id))
    )
    a1 = config.book_of(Method.A1).book_id
    assert len(plan.designer_invalid_slots) == 8 and len(plan.designer_timeout_slots) == 8
    assert not set(plan.designer_invalid_slots) & set(plan.designer_timeout_slots)
    for slot_id in (*plan.designer_invalid_slots, *plan.designer_timeout_slots):
        assert slot_id.startswith(f"{a1}.")
    rounds = [s.rsplit("s", 1)[0] for s in plan.designer_timeout_slots]
    assert len(set(rounds)) == len(rounds)  # one skipped slot per round at most
    again = dryrun.make_plan(config, run_id="DEMO-dry-plan-01", clock=ScaledClock(20))
    other = dryrun.make_plan(config, run_id="DEMO-dry-plan-02", clock=ScaledClock(20))
    assert again == plan and other.designer_timeout_slots != plan.designer_timeout_slots
    real = dryrun.make_plan(config, run_id="DEMO-dry-plan-01", clock=ManualClock())
    assert real.clock == "real" and real.clock_speed is None and real.token_count_cap_ms is None


def test_plan_scopes_and_whole_book_expectations():
    config = dryrun.dry_run_config()
    one = dryrun.make_plan(config, run_id="DEMO-dry-plan-03", clock=ManualClock(), appointments=[1])
    assert one.atoms(config) == config.atom_order[:4]
    assert [(z.atom_id, z.expect) for z in one.zero_eligible] == [(config.atom_order[2], "bank")]
    assert len(one.designer_invalid_slots) == 2 and len(one.designer_timeout_slots) == 2
    book = dryrun.make_plan(
        config,
        run_id="DEMO-dry-plan-04",
        clock=ManualClock(),
        fallback_bank="single_recipe",
        zero_eligible=["A3@1", "A3@2", "A2@3"],
    )
    a3, a2 = config.book_of(Method.A3).book_id, config.book_of(Method.A2).book_id
    assert [(z.book_id, z.atom_id, z.expect) for z in book.zero_eligible] == [
        (a3, config.atom_order[0], "bank"),
        (a3, config.atom_order[1], "book"),
        (a2, config.atom_order[2], "bank"),
    ]
    assert len(book.force_unacceptable_slots) == 36
    for bad, message in (
        (["A2@5"], "not in appointments"),
        (["A9@1"], "METHOD@POSITION"),
        (["A2@17"], "not 1..16"),
        (["A2@1", "A2@1"], "twice"),
    ):
        with pytest.raises(ValueError, match=message):
            dryrun.make_plan(
                config, run_id="DEMO-x-01", clock=ManualClock(), appointments=[1], zero_eligible=bad
            )
    with pytest.raises(ValueError, match="appointments"):
        dryrun.make_plan(config, run_id="DEMO-x-01", clock=ManualClock(), appointments=[5])


def test_single_recipe_bank_is_exhausted_once_the_book_holds_its_recipe():
    demo = sim.demo_fallback()
    fallback = dryrun.single_recipe_fallback(demo, "P1")
    bank, first = fallback.bank(Profile.P1), demo.bank(Profile.P1)[0]
    assert len(bank) == 64 and {e.recipe.sha256() for e in bank} == {first.recipe.sha256()}
    assert fallback.book(Profile.P1) == demo.book(Profile.P1)
    assert fallback.bank(Profile.P2) == demo.bank(Profile.P2)
    assert fallback.fallback_bank_hash != demo.fallback_bank_hash
    assert scan_fallback(bank, (), threshold="0.10").index == 0
    held = scan_fallback(bank, [first.reference()], used=[0], threshold="0.10")
    assert held.selected is None and {s.outcome for s in held.log} == {"used", "rejected"}


# ---------------------------------------------------------------------------
# A deterministic synthetic dry run (virtual clock, simulated proposers)


def _sim_dry_run(root, run_id, *, appointments=(1, 2, 3, 4), fallback_bank="demo", zero=None):
    """The driver's injections on the virtual clock: the plan, its forced rating slots
    for the bots, its designer slots scripted into the A1 stand-in, startup events."""
    clock = ManualClock()
    base, fallback = sim.demo_batch_config(), sim.demo_fallback()
    if fallback_bank == "single_recipe":
        fallback = dryrun.single_recipe_fallback(fallback, base.profile.value)
    config = dryrun.dry_run_config(base, fallback=fallback)
    plan = dryrun.make_plan(
        config,
        run_id=run_id,
        clock=ManualClock(),
        appointments=appointments,
        zero_eligible=zero,
        fallback_bank=fallback_bank,
        designer_think_ms=None,
        mock_llm_latency_ms=None,
    )
    invalid, skipped = set(plan.designer_invalid_slots), set(plan.designer_timeout_slots)

    def designer(request, slot, rng):
        slot_id = proposal_slot_id(request.book_id, request.atom_id, request.round, slot)
        if slot_id in invalid:
            return sim.SimProposal("invalid_json")
        if slot_id in skipped:
            return sim.SimProposal("timeout")
        return sim.SimProposal("recipe", sim.uniform_recipe(rng))

    layout = create_run_dir(root, run_id, RunKind.SYNTHETIC)
    proposers = sim.sim_proposers(
        config,
        RecordWriter(layout.log("slot")),
        clock=clock,
        p_failure=0.0,
        propose={Method.A1: designer},
    )
    meanings = sim.demo_meanings()
    orch = Orchestrator(
        config,
        layout,
        proposers,
        VocabularyStore(layout.store_dir, clock=clock.utc_now),
        fallback,
        clock=clock,
        generation_config=sim.demo_generation_config(meanings, fallback, threshold="0.10"),
        meanings=meanings,
        kind=RunKind.SYNTHETIC,
        purpose="dry_run",
        slot_event_lead_ms=0,
    )
    writer = RecordWriter(layout.log("timing"))
    for component in dryrun.STARTUP_COMPONENTS:
        for event, duration in (("startup_start", None), ("startup_end", 0)):
            writer.append(
                TimingEvent(
                    run_id=run_id,
                    event=event,
                    t_ms=0,
                    wall_utc=utc_text(clock.utc_now()),
                    batch_id=config.batch_id,
                    component=component,
                    duration_ms=duration,
                )
            )
    plan.write(layout.dry_run_plan)
    policy = sim.seeded_policy(run_id, force_unacceptable=frozenset(plan.force_unacceptable_slots))
    with sim.SyntheticPanel(orch.panel_host(), clock, policy):
        for k in plan.appointments:
            orch.run_appointment(k)
    return layout, plan


@pytest.fixture(scope="module")
def sim_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("dry-sim")
    with no_fsync():
        layout, plan = _sim_dry_run(root, "DEMO-dry-sim-01")
    return layout.root, plan


@pytest.fixture(scope="module")
def sim_small(tmp_path_factory):
    """Appointment 1 only (a reduced run): the defect, injection and CLI tests."""
    root = tmp_path_factory.mktemp("dry-sim-small")
    with no_fsync():
        layout, plan = _sim_dry_run(root, "DEMO-dry-sim-02", appointments=(1,))
    return layout.root, plan


def _copy(run_dir, tmp_path):
    target = tmp_path / run_dir.name
    shutil.copytree(run_dir, target)
    return target


def _lines(path):
    return path.read_text(encoding="utf-8").splitlines(keepends=True)


def test_complete_logs_pass_every_count(sim_run):
    run_dir, plan = sim_run
    before = {p: p.stat().st_mtime_ns for p in run_dir.rglob("*") if p.is_file()}
    report = dryrun.check_log_completeness(run_dir)
    assert report.ok, report.problems
    c = report.counts
    assert c["commits_final"] == 48 and c["commits_with_store_sha256"] == 48
    assert c["slot_records"] == 576
    assert (c["slot_records_A1"], c["slot_records_A2"], c["slot_records_A3"]) == (192, 192, 192)
    assert c["slots_without_status"] == 0 and c["slots_replenished"] == 0
    assert c["rating_records_R01"] == c["rating_records_R02"] == c["rating_records_R03"] == 576
    assert c["rating_placeholders"] > 0 and c["message_plays"] == 0
    assert c["fallback_injected"] == c["fallback_scans"] == c["fallback_bank_commits"] == 1
    assert c["decision_records"] == 192 and c["run_closed"] == 1
    assert c["designer_invalid_injected"] == c["designer_timeout_injected"] == 8
    # the check reads the run and changes nothing
    after = {p: p.stat().st_mtime_ns for p in run_dir.rglob("*") if p.is_file()}
    assert after == before
    assert plan == DryRunPlan.read(run_dir / "dry-run-plan.json")
    assert report.text().startswith("log completeness: OK\n")


def _break(run_dir, rel, edit):
    path = run_dir / rel
    path.write_text("".join(edit(_lines(path))), encoding="utf-8", newline="\n")


@pytest.mark.parametrize(
    ("defect", "expected"),
    [
        ("drop_slot", "143 slot records"),
        ("duplicate_slot", "1 replenished slots"),
        ("drop_rating", "rater R02: 143 rating records"),
        ("message_play", "1 complete-message plays"),
        ("drop_commit", "11 commits in final store books"),
        ("commit_hash", "store waveform hash differs"),
        ("drop_scan", "fallback scans [] differ from the injected cases"),
        ("drop_startup", "no startup_end event of renderer"),
    ],
)
def test_each_defect_is_reported(sim_small, tmp_path, defect, expected):
    run_dir = _copy(sim_small[0], tmp_path)
    if defect == "drop_slot":
        _break(run_dir, "logs/slots.jsonl", lambda lines: lines[:-1])
    elif defect == "duplicate_slot":
        _break(run_dir, "logs/slots.jsonl", lambda lines: [*lines, lines[5]])
    elif defect == "drop_rating":

        def drop_first_r02(lines):
            i = next(i for i, ln in enumerate(lines) if '"rater_id":"R02"' in ln)
            return lines[:i] + lines[i + 1 :]

        _break(run_dir, "logs/ratings.jsonl", drop_first_r02)
    elif defect == "message_play":

        def add_message(lines):
            play = json.loads(lines[0])
            play["audio_kind"] = "message"
            return [*lines, json.dumps(play, sort_keys=True, separators=(",", ":")) + "\n"]

        _break(run_dir, "logs/plays.jsonl", add_message)
    elif defect == "drop_commit":
        _break(run_dir, "logs/commits.jsonl", lambda lines: lines[:-1])
    elif defect == "commit_hash":

        def flip(lines):
            commit = json.loads(lines[3])
            digest = commit["pcm_sha256"]
            commit["pcm_sha256"] = ("0" if digest[0] != "0" else "1") + digest[1:]
            lines[3] = json.dumps(commit, sort_keys=True, separators=(",", ":")) + "\n"
            return lines

        _break(run_dir, "logs/commits.jsonl", flip)
    elif defect == "drop_scan":
        _break(run_dir, "logs/fallback-scans.jsonl", lambda lines: [])
    elif defect == "drop_startup":
        _break(
            run_dir,
            "logs/timing.jsonl",
            lambda lines: [
                ln for ln in lines if not ('"startup_end"' in ln and '"renderer"' in ln)
            ],
        )
    report = dryrun.check_log_completeness(run_dir)
    assert not report.ok
    assert any(expected in p for p in report.problems), report.problems


def test_fallback_events_must_equal_the_injected_cases(sim_small):
    run_dir, plan = sim_small
    config = BatchConfig.read(run_dir / "config.json")
    a3 = config.book_of(Method.A3).book_id
    extra = InjectedFallback(a3, config.atom_order[1], "bank")
    more = dataclasses.replace(
        plan,
        zero_eligible=(*plan.zero_eligible, extra),
        force_unacceptable_slots=tuple(
            sorted((*plan.force_unacceptable_slots, *config.rating_slot_ids(a3, extra.atom_id)))
        ),
    )
    report = dryrun.check_log_completeness(run_dir, plan=more)
    assert any("fallback scans" in p for p in report.problems)
    assert any("fallback_bank commits" in p for p in report.problems)
    none = dataclasses.replace(plan, zero_eligible=(), force_unacceptable_slots=())
    report = dryrun.check_log_completeness(run_dir, plan=none)
    assert any("fallback_scan decisions" in p for p in report.problems)
    wrong = dataclasses.replace(plan, force_unacceptable_slots=plan.force_unacceptable_slots[1:])
    report = dryrun.check_log_completeness(run_dir, plan=wrong)
    assert "force_unacceptable_slots are not the rating slots of the zero-eligible atoms" in (
        report.problems
    )
    slot = plan.designer_timeout_slots[0]
    swapped = dataclasses.replace(
        plan,
        designer_invalid_slots=(slot,),
        designer_timeout_slots=plan.designer_invalid_slots[:1],
    )
    report = dryrun.check_log_completeness(run_dir, plan=swapped)
    assert f"designer invalid slot {slot}: outcome timeout" in report.problems


def test_a_reduced_whole_book_run_counts_sixteen_fallback_book_commits(tmp_path):
    with no_fsync():
        layout, plan = _sim_dry_run(
            tmp_path,
            "DEMO-dry-sim-book-01",
            appointments=(1,),
            fallback_bank="single_recipe",
            zero=["A3@1", "A3@2"],
        )
    report = dryrun.check_log_completeness(layout.root)
    assert report.ok, report.problems
    config = BatchConfig.read(layout.config)
    a3 = config.book_of(Method.A3).book_id
    c = report.counts
    assert c[f"commits_final_{a3}"] == 16 and c["commits_final"] == 16 + 4 + 4
    assert c["book_substitutions"] == 1 and c["fallback_scans"] == 2
    assert c["commit_records"] == 16 + 4 + 4 + 1 and c["run_closed"] == 0
    scans = read_records(layout.log("fallback_scan"), FallbackScanRecord)
    assert [s.scan["outcome"] for s in scans] == ["selected", "exhausted"]
    fb = [c for c in read_records(layout.log("commit"), CommitRecord) if c.book_id == a3]
    assert [c.source for c in fb].count("fallback_book") == 16
    assert {c.store_book_id for c in fb if c.source == "fallback_book"} == {f"{a3}-FB"}


# ---------------------------------------------------------------------------
# Timing CSV


def test_timing_rows_on_the_virtual_clock(sim_run, tmp_path):
    run_dir, _ = sim_run
    summary = dryrun.write_timing(run_dir, tmp_path)
    with (tmp_path / "timing.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert tuple(rows[0]) == dryrun.TIMING_COLUMNS
    levels = [r["level"] for r in rows]
    assert levels.count("startup") == 2 and levels.count("appointment") == 4
    assert levels.count("atom") == 16 and levels.count("round") == 64
    rounds = [r for r in rows if r["level"] == "round"]
    # simulated proposals take no clock time: preload lead 1 s + 9 slots of 20 s
    assert {r["duration_ms"] for r in rounds} == {"181000"}
    assert {r["rating_window_ms"] for r in rounds} == {"181000"}
    assert {r["within_budget"] for r in rows if r["level"] != "startup"} == {"1"}
    atoms = [r for r in rows if r["level"] == "atom"]
    assert {r["duration_ms"] for r in atoms} == {"724000"}
    assert [r["atom_id"] for r in atoms] == list(
        BatchConfig.read(run_dir / "config.json").atom_order
    )
    assert summary.max_atom_ms == 724_000 and summary.max_appointment_ms == 4 * 724_000
    assert summary.atom_ok and summary.appointment_ok and summary.rounds == 64
    saved = json.loads((tmp_path / "timing-summary.json").read_text(encoding="utf-8"))
    assert saved["atom_ok"] is True and saved["atom_limit_ms"] == ATOM_BUDGET_MS
    assert saved["startup_ms"] == {"llm": 0, "renderer": 0}


def _timing_run(sim_dir, tmp_path, events):
    run_dir = tmp_path / "DEMO-dry-timing-01"
    (run_dir / "logs").mkdir(parents=True)
    for name in ("config.json", "run-manifest.json"):
        shutil.copy(sim_dir / name, run_dir / name)
    writer = RecordWriter(run_dir / "logs/timing.jsonl")
    for event in events:
        writer.append(event)
    return run_dir


def test_over_budget_and_two_process_intervals(sim_run, tmp_path):
    config = BatchConfig.read(sim_run[0] / "config.json")
    atom, atom2 = config.atom_order[:2]
    long_atom = ATOM_BUDGET_MS + 1
    first = datetime(2026, 1, 1, tzinfo=UTC)
    second = first + timedelta(milliseconds=long_atom + 10_000)  # a new process, 10 s later

    def ev(origin, event, t_ms, **fields):
        wall = utc_text(origin + timedelta(milliseconds=t_ms))
        return TimingEvent(
            run_id="DEMO-dry-sim-01", event=event, t_ms=t_ms, wall_utc=wall, **fields
        )

    events = [
        ev(first, "appointment_start", 0, appointment=1),
        ev(first, "atom_start", 0, atom_id=atom, appointment=1),
        ev(first, "round_start", 0, atom_id=atom, round=1),
        ev(first, "proposal_window_start", 0, atom_id=atom, round=1),
        ev(first, "proposal_window_end", 120_000, atom_id=atom, round=1),
        ev(first, "round_end", 301_000, atom_id=atom, round=1),
        ev(first, "atom_end", long_atom, atom_id=atom, appointment=1),
        ev(first, "appointment_end", long_atom, appointment=1),
        ev(first, "atom_start", long_atom, atom_id=atom2, appointment=1),
        ev(second, "atom_end", 20_000, atom_id=atom2, appointment=1),
    ]
    run_dir = _timing_run(sim_run[0], tmp_path, events)
    rows, summary = dryrun.timing_rows(run_dir)
    by_level = {(r.level, r.atom_id, r.round): r for r in rows}
    assert by_level[("round", atom, 1)].duration_ms == 301_000
    assert by_level[("round", atom, 1)].within_budget is False
    assert by_level[("round", atom, 1)].proposal_window_ms == 120_000
    assert by_level[("atom", atom, None)].within_budget is False
    # the second atom ended in another process: measured on the wall clock
    assert by_level[("atom", atom2, None)].duration_ms == 30_000
    assert summary.max_atom_ms == long_atom and not summary.atom_ok
    assert summary.max_round == f"{atom} r1" and summary.max_appointment == 1


# ---------------------------------------------------------------------------
# Independent tally, bundle, evidence and CLI


def test_independent_tally_matches_the_audit_and_finds_a_difference(sim_run, tmp_path):
    run_dir = _copy(sim_run[0], tmp_path)
    audit = build_audit(run_dir, run_dir / "audit")
    assert audit.ok, audit.problems
    ok, n = dryrun.compare_with_audit(run_dir, run_dir / "audit", tmp_path / "tally.csv")
    assert ok and n == 3 * 28 + 2
    tally = dryrun.tally_logs(run_dir)
    config = BatchConfig.read(run_dir / "config.json")
    for book in config.books:
        assert tally[book.book_id]["slots_total"] == 192
        assert tally[book.book_id]["rating_records"] == 576
        assert tally[book.book_id]["decision_records"] == 64
    assert tally["*"]["fallback_scans"] == 1 and tally["*"]["message_plays"] == 0
    books = run_dir / "audit/unmasked/books.csv"
    text = books.read_text(encoding="utf-8").splitlines(keepends=True)
    header = text[0].strip().split(",")
    cells = text[1].strip().split(",")
    cells[header.index("slots_valid")] = str(int(cells[header.index("slots_valid")]) + 1)
    text[1] = ",".join(cells) + "\n"
    books.write_text("".join(text), encoding="utf-8", newline="\n")
    ok, _ = dryrun.compare_with_audit(run_dir, run_dir / "audit", tmp_path / "tally2.csv")
    assert not ok
    rows = list(csv.DictReader((tmp_path / "tally2.csv").open(encoding="utf-8")))
    assert [r["quantity"] for r in rows if r["match"] == "0"] == ["slots_valid"]


def test_bundle_is_deterministic(sim_run, tmp_path):
    run_dir = _copy(sim_run[0], tmp_path)
    first = dryrun.write_bundle(run_dir, tmp_path / "a" / "b.tar.gz")
    second = dryrun.write_bundle(run_dir, tmp_path / "c" / "b.tar.gz")
    assert first["sha256"] == second["sha256"] and first["files"] == second["files"]
    (run_dir / "logs/timing.jsonl").write_bytes(b"")
    assert dryrun.write_bundle(run_dir, tmp_path / "d.tar.gz")["sha256"] != first["sha256"]


def test_evidence_and_command_line(sim_small, tmp_path, capsys):
    run_dir = _copy(sim_small[0], tmp_path)
    evidence = tmp_path / "evidence"
    bundle_dir = tmp_path / "bundle"
    command = ["evidence", str(run_dir), "--out", str(evidence), "--bundle-dir", str(bundle_dir)]
    assert dryrun.main(command) == 0
    assert capsys.readouterr().out.startswith("dry run DEMO-dry-sim-02: completeness OK")
    doc = json.loads((evidence / "manifest.json").read_text(encoding="utf-8"))
    assert doc["completeness"]["ok"] and doc["tally"]["all_match"]
    assert not doc["audit"]["ok"]  # the #24 audit expects 16 atoms; this run has 4
    assert doc["label"] == "synthetic" and doc["run_closed"] is False
    assert doc["appointments"] == [1] and doc["timing"]["atoms"] == 4
    assert sorted(p.name for p in evidence.iterdir()) == [
        "audit-books.csv",
        "audit-summary.md",
        "completeness.txt",
        "hand-tally.csv",
        "manifest.json",
        "tally-comparison.csv",
        "timing-summary.json",
        "timing.csv",
    ]
    assert all(p.stat().st_size < 1_000_000 for p in evidence.iterdir())
    text = (evidence / "manifest.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in text and "\\\\" not in text
    bundle = bundle_dir / "DEMO-dry-sim-02-bundle.tar.gz"
    assert (
        doc["bundle"]["sha256"] == dryrun.write_bundle(run_dir, tmp_path / "again.tar.gz")["sha256"]
    )
    assert bundle.is_file() and doc["bundle"]["bytes"] == bundle.stat().st_size
    assert dryrun.main(["check", str(run_dir)]) == 0
    assert capsys.readouterr().out.startswith("log completeness: OK")
    assert dryrun.main(["timing", str(run_dir), "--out", str(tmp_path / "t")]) == 0
    assert dryrun.main(["tally", str(run_dir), "--out", str(tmp_path / "t.csv")]) == 0
    _break(run_dir, "logs/slots.jsonl", lambda lines: lines[:-1])
    assert dryrun.main(["check", str(run_dir)]) == 1
    assert "problem: 143 slot records" in capsys.readouterr().out
    assert dryrun.main(["check", str(tmp_path / "missing")]) == 2


# ---------------------------------------------------------------------------
# Accelerated end to end: the real components on a reduced batch


@pytest.fixture(scope="module")
def e2e_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("dry-e2e")
    if os.environ.get("CI") == "true":
        out = CI_OUT
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True, exist_ok=True)
    lines = []
    with no_fsync():
        result = dryrun.run_dry_run(
            out,
            "DEMO-dry-run-e2e-01",
            clock=ScaledClock(100),
            appointments=(1,),
            fallback_bank="single_recipe",
            zero_eligible=("A3@1", "A3@2"),
            mock_latency_ms=500,
            designer_think_ms=(0, 2_000),
            station_timeout_s=60,
            log=lines.append,
        )
        doc = dryrun.write_evidence(
            result.layout.root, out / "DEMO-dry-run-e2e-01-evidence", bundle_dir=out, result=result
        )
    return result, doc, lines


def test_reduced_batch_end_to_end_passes_the_completeness_check(e2e_run):
    result, doc, lines = e2e_run
    report = dryrun.check_log_completeness(result.layout.root)
    assert report.ok, report.problems
    c = report.counts
    assert c["slot_records"] == 144 and c["slots_replenished"] == 0
    assert (c["slot_records_A1"], c["slot_records_A2"], c["slot_records_A3"]) == (48, 48, 48)
    assert c["rating_records_R01"] == c["rating_records_R02"] == c["rating_records_R03"] == 144
    assert c["message_plays"] == 0 and c["startup_events"] == 4
    assert doc["completeness"]["ok"] and doc["tally"]["all_match"]
    assert doc["timing"]["atoms"] == 4 and doc["timing"]["rounds"] == 16
    # one keyed station session: three bot stations followed every slot
    [session] = result.sessions
    assert sorted(session.stations) == ["S1", "S2", "S3"]
    for station in session.stations.values():
        assert station.end_reason == "appointment_complete" and len(station.slots) == 144
    assert any(line.startswith("Rater station S1 (R01): http://127.0.0.1:") for line in lines)
    assert result.startup_ms.keys() == {"llm", "renderer"} and result.llm == "mock"
    # the bot designer worked the A1 app: scripted invalid slots; skipped ones timed out
    outcomes = {
        s["slot_id"]: s["outcome"]
        for s in map(json.loads, _lines(result.layout.log("slot")))
        if s["method"] == "A1"
    }
    assert all(outcomes[s] == "invalid_json" for s in result.plan.designer_invalid_slots)
    assert all(outcomes[s] == "timeout" for s in result.plan.designer_timeout_slots)
    assert session.designer_plays > 0


def test_reduced_batch_logs_the_forced_fallbacks_as_injected(e2e_run):
    result, doc, _ = e2e_run
    layout, plan = result.layout, result.plan
    config = BatchConfig.read(layout.config)
    a3 = config.book_of(Method.A3).book_id
    first, second = config.atom_order[:2]
    scans = read_records(layout.log("fallback_scan"), FallbackScanRecord)
    assert [(s.book_id, s.atom_id, s.scan["outcome"]) for s in scans] == [
        (a3, first, "selected"),
        (a3, second, "exhausted"),
    ]
    commits = read_records(layout.log("commit"), CommitRecord)
    banked = {(c.book_id, c.atom_id): c.bank_index for c in commits if c.source == "fallback_bank"}
    assert banked == {(a3, first): 0}
    assert sum(1 for c in commits if c.source == "fallback_book") == 16
    subs = [
        (e.book_id, e.atom_id)
        for e in read_records(layout.log("timing"), TimingEvent)
        if e.event == "book_substituted"
    ]
    assert subs == [(a3, second)]
    assert [(z.book_id, z.atom_id, z.expect) for z in plan.zero_eligible] == [
        (a3, first, "bank"),
        (a3, second, "book"),
    ]
    assert doc["completeness"]["counts"]["fallback_injected"] == 2
    assert doc["completeness"]["counts"][f"commits_final_{a3}"] == 16


def test_resume_reopens_the_run_with_its_stored_plan(e2e_run):
    """`--resume` after an interruption: the stored plan, a new startup, the remaining
    appointments only (none here), and a refused change of clock kind."""
    result, _, _ = e2e_run
    out, run_id = result.layout.root.parent, result.layout.run_id
    with pytest.raises(dryrun.br.RunnerError, match="scaled clock"):
        dryrun.run_dry_run(out, run_id, clock=ManualClock(), resume=True, log=lambda _: None)
    with no_fsync():
        again = dryrun.run_dry_run(
            out, run_id, clock=ScaledClock(100), resume=True, appointments=(2,), log=lambda _: None
        )
    assert again.plan == result.plan and again.sessions == []
    report = dryrun.check_log_completeness(result.layout.root)
    assert report.ok, report.problems
    assert report.counts["startup_events"] == 8
