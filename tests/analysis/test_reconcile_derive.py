"""Reconciled and derived tables (#33 -> #34, #35): content rules, lost opportunities,
withdrawal, visit states, enrollment, manifests, determinism and masking."""

from __future__ import annotations

import json
import shutil
from collections import Counter

import pytest

from av_analysis import masking
from av_analysis.derive import derive_tables, fault_info, write_tables
from av_analysis.derived import TABLES, parse_table
from av_analysis.fileio import csv_bytes, parse_csv, read_bytes
from av_analysis.ledger import build_ledger, components, fold, visit_logs
from av_analysis.loaders import RefusedInputError, load_raw_visit, raw_visit_ids
from av_analysis.paths import DataRoot, write_synthetic_input
from av_analysis.reconcile import reconcile_many, write_report
from av_analysis.schemas import validator
from av_analysis.synthetic_logs import (
    build_synthetic_root,
    inject_fault,
    refresh_exit_manifest,
    suite_visits,
)

SEED = "DEMO-test-33-derive"


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    return build_synthetic_root(
        tmp_path_factory.mktemp("derive") / "base", seed_label=SEED, max_persons=2
    )


@pytest.fixture
def root(base, tmp_path):
    target = tmp_path / "root"
    shutil.copytree(base.path, target)
    return DataRoot.open(target)


def reconcile_all(root):
    for report in reconcile_many(root, raw_visit_ids(root)):
        write_report(root, report)


def by_visit(rows, visit_id):
    return [r for r in rows if r["visit_id"] == visit_id]


def add_deviation(root, vid, **fields):
    header, records = parse_csv(read_bytes(root.raw_visit_dir(vid) / "deviations.csv"))
    rows = [dict(zip(header, r, strict=True)) for r in records]
    row = dict.fromkeys(header, "")
    row.update(
        deviation_id=f"DEV-{vid}-{len(rows) + 1}",
        timestamp="2027-03-20T12:00:00+01:00",
        protocol_version="v0.1",
        operator="S01",
        category="technical",
    )
    row.update(fields)
    rows.append(row)
    data = csv_bytes(header, ([r[c] for c in header] for r in rows))
    write_synthetic_input(root, "raw", f"{vid}/deviations.csv", data)
    refresh_exit_manifest(root, vid)


def test_clean_tables_round_trip_and_match_the_logs(root):
    reconcile_all(root)
    tables = derive_tables(root)
    paths = write_tables(root, tables)
    assert len(paths) == len(TABLES) + 2
    for name, spec in TABLES.items():
        data = read_bytes(root.path / spec.area / spec.filename)
        parsed = parse_table(spec, data, data_kind="SYNTHETIC")
        assert len(parsed) == len(tables[name]), name
        assert masking.forbidden_columns(spec.header, spec.policy) == {}
    trials = tables["trials"]
    visits = suite_visits(root)
    assert len(by_visit(trials, visits["D0"])) == 16 + 36 + 36 + 4 + 16
    assert len(by_visit(trials, visits["V1"])) == 1 + 8 + 8 + 8 + 4 + 2 + 8
    assert all(t["row_source"] == "logged" and t["valid_delivery"] for t in trials)
    assert all(t["exposure_consumed"] == (t["trial_type"] != "no_cue") for t in trials)
    novel = [t for t in trials if t["trained_status"] == "heldout"]
    assert novel and {t["novelty"] for t in novel} == {"first"}
    trained = [t for t in trials if t["visit_id"] == visits["D7"] and t["trial_type"] == "trained"]
    assert all(t["prior_phrase_exposures"] >= 7 for t in trained)  # 6 lesson plays + 2 tests
    endpoints = tables["endpoints"]
    d0 = {e["battery"]: e for e in by_visit(endpoints, visits["D0"])}
    assert {k: e["scheduled_n"] for k, e in d0.items()} == {"trained": 36, "novel": 4, "atomic": 16}
    assert all(e["status"] == "complete" and e["planned_endpoint"] for e in endpoints)
    assert {e["battery"] for e in by_visit(endpoints, visits["W4"])} == {
        "trained",
        "novel",
        "atomic",
        "validity",
    }
    status = tables["visit-status"]
    assert len(status) == 14 and {s["visit_state"] for s in status} == {"held"}
    assert all(s["reconciliation"] == "pass" and s["overrun"] is False for s in status)
    gaps = {s["visit_id"]: (s["pair_gap_hours"], s["pair_gap_ok"]) for s in status}
    assert gaps["B-P01-M1-V1"] == gaps["B-P01-M2-V1"] == (5.0, True)
    assert gaps["B-P01-M1-W1"] == (None, None)
    assert tables["discrepancies"] == []
    enrollment = {r["study"]: r for r in tables["enrollment"]}
    assert enrollment["A"]["revealed_persons_n"] == 2 and enrollment["B"]["revealed_units_n"] == 1
    assert enrollment["B"]["planned_units_n"] == 8 and enrollment["A"]["spares_used_n"] is None
    ledger = tables["exposure-cumulative"]
    counts = Counter(r["person_id"] for r in ledger)
    assert counts["B-P01-M1"] == 16 + 18 + 14  # atoms, trained and held-out messages
    assert all(r["violations"] == () for r in ledger)
    for area in ("reconciled", "derived"):
        doc = json.loads(read_bytes(root.path / area / "manifest.json"))
        assert list(validator("outputs-manifest.schema.json").iter_errors(doc)) == []
        assert not any(i["path"].startswith("keys/") for i in doc["inputs"])
    assert build_ledger(root, [visits["D0"], visits["D7"]]) == [
        r for r in ledger if r["person_id"] == visits["D0"][:9]
    ]


def test_tables_are_deterministic(root):
    reconcile_all(root)

    def written():
        paths = write_tables(root, derive_tables(root))
        return {p.relative_to(root.path): read_bytes(p) for p in paths}

    assert written() == written()


def test_lost_opportunity_becomes_a_deviation_row(root):
    vid = suite_visits(root)["W1"]
    inject_fault(root, vid, "missing_trial", documented=True)
    reconcile_all(root)
    tables = derive_tables(root)
    lost = [t for t in by_visit(tables["trials"], vid) if t["row_source"] == "deviation"]
    assert len(lost) == 1
    row = lost[0]
    assert row["fault_codes"] == ("OPPORTUNITY_LOST",) and row["fault_types"] == ("other",)
    assert row["valid_delivery"] is False and row["response_code"] is None
    assert row["exposure_consumed"] is False  # the record says no audio was presented
    assert row["deviation_ids"] and row["discrepancy_codes"] == ("COUNT_MISSING_TRIAL",)
    trained = next(e for e in by_visit(tables["endpoints"], vid) if e["battery"] == "trained")
    assert (trained["accounted_n"], trained["lost_n"], trained["fault_n"]) == (36, 1, 1)
    assert trained["valid_delivery_n"] == 35 and trained["status"] == "complete"
    status = next(s for s in tables["visit-status"] if s["visit_id"] == vid)
    assert status["fault_other_n"] == 1 and status["deviations_n"] == 1
    assert status["open_deviations_n"] == 1 and status["reconciliation"] == "pass"


def test_withdrawal_mid_battery_leaves_no_row(root):
    vid = suite_visits(root)["D7"]
    inject_fault(root, vid, "missing_trial")
    trial_id = next(
        r["rows"][0]
        for c in reconcile_many(root, [vid])[0].document()["discrepancies"]
        for r in [c]
        if c["code"] == "COUNT_MISSING_TRIAL"
    )
    add_deviation(root, vid, event_id=trial_id, category="withdrawal")
    reconcile_all(root)
    tables = derive_tables(root)
    assert not [t for t in by_visit(tables["trials"], vid) if t["row_source"] == "deviation"]
    trained = next(e for e in by_visit(tables["endpoints"], vid) if e["battery"] == "trained")
    assert trained["status"] == "partial" and trained["missing_reason"] == "withdrawn_mid_battery"
    assert trained["planned_endpoint"] is False
    status = next(s for s in tables["visit-status"] if s["visit_id"] == vid)
    assert status["withdrawal_deviations_n"] == 1


def test_visit_states_missed_withdrawn_pending(root):
    visits = suite_visits(root)
    b_w1, b_w4 = visits["W1"], visits["W4"]
    a_d7 = visits["D7"]
    for vid in (b_w1, b_w4, a_d7):
        shutil.rmtree(root.raw_visit_dir(vid))
    log = root.input_path("raw", "deviations-log.csv")
    header, _ = parse_csv(read_bytes(log))
    rows = []
    for dev, event, category in (("L1", b_w1, "missed_visit"), ("L2", a_d7[:9], "withdrawal")):
        row = dict.fromkeys(header, "")
        row.update(
            deviation_id=dev,
            timestamp="2027-04-01T10:00:00Z",
            protocol_version="v0.1",
            operator="S02",
            event_id=event,
            category=category,
        )
        rows.append(row)
    write_synthetic_input(
        root, "raw", "deviations-log.csv", csv_bytes(header, ([r[c] for c in header] for r in rows))
    )
    reconcile_all(root)
    tables = derive_tables(root)
    states = {s["visit_id"]: s for s in tables["visit-status"]}
    assert states[b_w1]["visit_state"] == "missed" and states[b_w1]["deviations_n"] == 1
    assert states[b_w4]["visit_state"] == "pending"
    assert states[a_d7]["visit_state"] == "withdrawn"
    assert states[b_w4]["reconciliation"] == "not_run" and states[b_w4]["timing"] == "unknown"
    reasons = {(e["visit_id"], e["missing_reason"]) for e in tables["endpoints"]}
    assert (b_w1, "missed") in reasons and (b_w4, "pending") in reasons
    assert (a_d7, "withdrawn") in reasons


def test_derive_refuses_a_stale_report(root):
    reconcile_all(root)
    vid = suite_visits(root)["D0"]
    inject_fault(root, vid, "extra_play")
    with pytest.raises(RefusedInputError, match="rerun reconcile"):
        derive_tables(root)


def test_violations_and_discrepancy_rows(root):
    vid = suite_visits(root)["V2"]
    inject_fault(root, vid, "holdout_in_lesson")
    reconcile_all(root)
    tables = derive_tables(root)
    flagged = [r for r in tables["exposure-cumulative"] if r["violations"]]
    assert flagged and "HOLDOUT_OUTSIDE_TEST" in flagged[0]["violations"]
    rows = by_visit(tables["discrepancies"], vid)
    assert [r["seq"] for r in rows] == list(range(1, len(rows) + 1))
    assert {r["code"] for r in rows} >= {"HOLDOUT_OUTSIDE_TEST", "DEVIATION_MISSING"}
    lesson = [
        t for t in by_visit(tables["trials"], vid) if "COUNT_EXTRA_PLAY" in t["discrepancy_codes"]
    ]
    assert len(lesson) == 1 and lesson[0]["valid_delivery"] is False
    status = next(s for s in tables["visit-status"] if s["visit_id"] == vid)
    assert status["checks_failed"] == ("C2", "C4", "C8")


def test_fault_info_and_fold_helpers(root):
    assert fault_info(
        {
            "technical_fault_code": "AUDIO_UNDERRUN;X_Y",
            "frame_freeze_ms": "300",
            "reset_ok": "false",
        }
    ) == (
        ("AUDIO_UNDERRUN", "X_Y"),
        ("audio_underrun", "failed_reset", "presentation_freeze", "other"),
    )
    assert fault_info({"technical_fault_code": "bad;"}) == ((), ())
    assert components("K-a1-r2") == ("K-a1", "K-r2") and components("Q-r3") == ("Q-r3",)
    assert components("K-ADD_ONE-A") == ()
    logs = [visit_logs(load_raw_visit(root, v)) for v in raw_visit_ids(root)[:2]]
    result = fold(logs)
    assert result.items and all(s.audible > 0 for s in result.items.values())
