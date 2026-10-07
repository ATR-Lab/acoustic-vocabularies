"""Which deviation records explain a visit (#33 review follow-up): study-wide log records
concern one visit only, deviation IDs never resolve across visits in ``derive``, and
``derive`` refuses a report older than an input that appeared later."""

from __future__ import annotations

import shutil

import pytest

from av_analysis.derive import derive_tables
from av_analysis.fileio import csv_bytes, parse_csv, read_bytes
from av_analysis.loaders import RefusedInputError, raw_visit_ids
from av_analysis.paths import DataRoot, remove_synthetic_input, write_synthetic_input
from av_analysis.reconcile import partner_slot, reconcile_many, reconcile_visit, write_report
from av_analysis.references import load_references
from av_analysis.synthetic_logs import (
    build_synthetic_root,
    inject_fault,
    refresh_exit_manifest,
    suite_visits,
)

SEED = "DEMO-test-33-scope"


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    return build_synthetic_root(
        tmp_path_factory.mktemp("scope") / "base", seed_label=SEED, max_persons=2
    )


@pytest.fixture(scope="module")
def base_b2(tmp_path_factory):
    """Study B with two dyads."""
    return build_synthetic_root(
        tmp_path_factory.mktemp("scope-b2") / "base", seed_label=SEED, study="B", units=2
    )


def copy(base, tmp_path, name="root"):
    target = tmp_path / name
    shutil.copytree(base.path, target)
    return DataRoot.open(target)


@pytest.fixture
def root(base, tmp_path):
    return copy(base, tmp_path)


def read_rows(root, rel):
    header, records = parse_csv(read_bytes(root.input_path("raw", rel)))
    return list(header), [dict(zip(header, r, strict=True)) for r in records]


def write_rows(root, rel, header, rows):
    data = csv_bytes(header, ([r[c] for c in header] for r in rows))
    write_synthetic_input(root, "raw", rel, data)


def add_record(root, rel, **fields):
    """Append a deviation record to ``rel`` (a visit's deviations.csv or the log)."""
    header, rows = read_rows(root, rel)
    row = dict.fromkeys(header, "")
    row.update(
        deviation_id=f"LOG-{len(rows) + 1}",
        timestamp="2027-03-20T12:00:00+01:00",
        protocol_version="v0.1",
        operator="S01",
        category="correction",
    )
    row.update(fields)
    write_rows(root, rel, header, [*rows, row])
    if rel != "deviations-log.csv":
        refresh_exit_manifest(root, rel.split("/", 1)[0])
    return row["deviation_id"]


def log(root, **fields):
    return add_record(root, "deviations-log.csv", **fields)


def hits(report, code):
    return [d for c in report.checks for d in c.discrepancies if d.code == code]


# ---------------------------------------------------------------------------------------
# Study-wide log records concern one visit


def test_log_records_about_another_visit_never_explain_this_one(root):
    visits = suite_visits(root)
    d0, d7 = visits["D0"], visits["D7"]
    other = next(v for v in raw_visit_ids(root) if v.endswith("-D0") and v != d0)
    inject_fault(root, d0, "unlinked_discrepancy")
    row = hits(reconcile_visit(root, d0), "COUNT_RUN_SHEET")[0].rows[0]
    assert row.startswith("visit-run-sheet.csv:")  # a row name every visit has
    coded_other = load_references(root, other).participant_id
    log(root, event_id=row, dyad_or_batch=d0[:5])  # unqualified: which visit?
    log(root, event_id=f"{d7}/{row}")  # the same person's other visit
    log(root, event_id=f"{other}/{row}")  # another learner's visit
    log(root, event_id=f"{d0}/{row}", participant_id=coded_other)  # names another learner
    log(root, event_id=f"{d0}/{row}", dyad_or_batch="A-P09")  # names another batch
    report = reconcile_visit(root, d0)
    assert [d.resolved for d in hits(report, "COUNT_RUN_SHEET")] == [False]
    assert not report.passed
    assert not hits(report, "RAW_FORMAT")  # the log's lines about other visits are not ours
    dev = log(root, event_id=f"{d0}/{row}", dyad_or_batch=d0[:5])
    report = reconcile_visit(root, d0)
    assert [(d.deviation_id, d.resolved) for d in hits(report, "COUNT_RUN_SHEET")] == [(dev, True)]
    assert report.passed
    other_report = reconcile_visit(root, d7)
    assert other_report.passed and not hits(other_report, "RAW_FORMAT")


def test_log_records_about_another_dyad_never_explain_a_suspension_event(base_b2, tmp_path):
    root = copy(base_b2, tmp_path)
    vid = "B-P01-M1-V2"
    inject_fault(root, vid, "changed_old_atom")
    atom = hits(reconcile_visit(root, vid), "OLD_ATOM_CHANGED")[0].rows[0]
    log(root, event_id=atom, dyad_or_batch="B-P02")
    log(root, event_id=atom)
    log(root, event_id=f"B-P02-M1-V2/{atom}")
    log(root, event_id="B-P02-M1-V2", category="technical")
    report = reconcile_visit(root, vid)
    assert [d.resolved for d in hits(report, "OLD_ATOM_CHANGED")] == [False]
    assert report.document()["summary"]["suspension_events"] == ["OLD_WAVEFORM_CHANGED"]
    dev = log(root, event_id=f"{vid}/{atom}", dyad_or_batch="B-P01")
    found = hits(reconcile_visit(root, vid), "OLD_ATOM_CHANGED")
    assert [(d.deviation_id, d.resolved) for d in found] == [(dev, True)]


def test_person_participant_and_row_named_log_records(root):
    d0 = suite_visits(root)["D0"]
    person = d0.rsplit("-", 1)[0]
    coded = load_references(root, d0).participant_id
    inject_fault(root, d0, "wrong_hash")
    tid = hits(reconcile_visit(root, d0), "WAVEFORM_HASH_MISMATCH")[0].rows[0]
    assert tid.startswith(f"{d0}-")  # scheduled trial IDs carry the visit ID
    log(root, event_id=person, category="window")  # category does not fit the code
    log(root, participant_id=coded, category="window")
    assert not hits(reconcile_visit(root, d0), "WAVEFORM_HASH_MISMATCH")[0].resolved
    dev = log(root, event_id=tid, category="audio")  # a row with the visit ID, any category
    assert hits(reconcile_visit(root, d0), "WAVEFORM_HASH_MISMATCH")[0].deviation_id == dev


def test_a_row_link_to_a_log_record_is_known_and_ids_are_unique_per_visit(root):
    d0 = suite_visits(root)["D0"]
    th, trials = read_rows(root, f"{d0}/trial-log.csv")
    trials[3]["deviation_id"] = "LOG-1"
    write_rows(root, f"{d0}/trial-log.csv", th, trials)
    refresh_exit_manifest(root, d0)
    assert hits(reconcile_visit(root, d0), "DEVIATION_UNKNOWN")
    log(root, event_id="note-without-a-visit")  # LOG-1: named by the row, so it is known
    report = reconcile_visit(root, d0)
    assert not hits(report, "DEVIATION_UNKNOWN") and report.passed
    add_record(root, f"{d0}/deviations.csv", deviation_id="LOG-1", event_id=d0)
    found = hits(reconcile_visit(root, d0), "RAW_FORMAT")
    assert [(d.rows, d.detail) for d in found] == [
        (("LOG-1",), "deviation ID used by more than one record of the visit")
    ]


# ---------------------------------------------------------------------------------------
# derive looks deviation records up per visit


def test_a_deviation_id_reused_by_another_visit_does_not_change_the_endpoint(root):
    visits = suite_visits(root)
    d0, d7 = visits["D0"], visits["D7"]
    inject_fault(root, d0, "missing_trial")
    tid = hits(reconcile_visit(root, d0), "COUNT_MISSING_TRIAL")[0].rows[0]
    add_record(
        root,
        f"{d0}/deviations.csv",
        deviation_id="DEV-1",
        event_id=tid,
        category="technical",
        prior_audio_exposure="none",
    )
    add_record(root, f"{d7}/deviations.csv", deviation_id="DEV-1", event_id=d7, category="comfort")
    for report in reconcile_many(root, raw_visit_ids(root)):
        write_report(root, report)
    tables = derive_tables(root)
    lost = [t for t in tables["trials"] if t["visit_id"] == d0 and t["row_source"] == "deviation"]
    assert [t["trial_id"] for t in lost] == [tid] and lost[0]["deviation_ids"] == ("DEV-1",)
    trained = next(
        e for e in tables["endpoints"] if e["visit_id"] == d0 and e["battery"] == "trained"
    )
    assert (trained["accounted_n"], trained["lost_n"], trained["status"]) == (36, 1, "complete")
    assert trained["missing_reason"] is None
    status = {s["visit_id"]: s for s in tables["visit-status"]}
    assert status[d0]["comfort_deviations_n"] == 0 and status[d7]["comfort_deviations_n"] == 1


def test_visit_status_counts_only_log_records_of_the_visit(root):
    visits = suite_visits(root)
    d0, d7 = visits["D0"], visits["D7"]
    log(root, event_id="visit-run-sheet.csv:trained", category="comfort")  # no visit
    log(root, event_id=f"{d7}/visit-run-sheet.csv:trained", category="comfort")
    for report in reconcile_many(root, raw_visit_ids(root)):
        write_report(root, report)
    status = {s["visit_id"]: s for s in derive_tables(root)["visit-status"]}
    assert (status[d0]["deviations_n"], status[d7]["deviations_n"]) == (0, 1)


# ---------------------------------------------------------------------------------------
# derive refuses a report older than an input that appeared later


def test_derive_refuses_a_report_written_before_the_partner_visit_was_imported(root, tmp_path):
    vid = suite_visits(root)["V1"]
    partner = f"{partner_slot(vid.rsplit('-', 1)[0])}-V1"
    held = tmp_path / "held"
    shutil.move(str(root.raw_visit_dir(partner)), str(held))
    report = reconcile_visit(root, vid)
    assert [d.code for d in hits(report, "YOKED_SOURCE_MISSING")] == ["YOKED_SOURCE_MISSING"]
    write_report(root, report)
    shutil.move(str(held), str(root.raw_visit_dir(partner)))
    with pytest.raises(RefusedInputError, match=f"raw/{partner}/.* appeared"):
        derive_tables(root)
    write_report(root, reconcile_visit(root, vid))
    assert derive_tables(root)["discrepancies"] == []


def test_derive_refuses_a_report_older_than_the_log_or_a_missing_reference(root):
    visits = suite_visits(root)
    log_bytes = read_bytes(root.input_path("raw", "deviations-log.csv"))
    remove_synthetic_input(root, "raw", "deviations-log.csv")
    write_report(root, reconcile_visit(root, visits["D0"]))
    write_synthetic_input(root, "raw", "deviations-log.csv", log_bytes)
    with pytest.raises(RefusedInputError, match="raw/deviations-log.csv appeared"):
        derive_tables(root)
    write_report(root, reconcile_visit(root, visits["D0"]))
    derive_tables(root)
    vid = visits["V2"]
    rel = f"store-snapshots/{vid[:5]}/V1.json"
    snapshot = read_bytes(root.input_path("inputs", rel))
    remove_synthetic_input(root, "inputs", rel)
    report = reconcile_visit(root, vid)
    assert [d.rows for d in hits(report, "REFERENCE_INPUT")] == [(f"inputs/{rel}",)]
    write_report(root, report)
    write_synthetic_input(root, "inputs", rel, snapshot)
    with pytest.raises(RefusedInputError, match=f"inputs/{rel} appeared"):
        derive_tables(root)


def test_invalid_log_lines_are_reported_only_for_their_visit(root):
    visits = suite_visits(root)
    d0, d7 = visits["D0"], visits["D7"]
    th, trials = read_rows(root, f"{d0}/trial-log.csv")
    trials[0]["deviation_id"] = "LOG-3"
    write_rows(root, f"{d0}/trial-log.csv", th, trials)
    refresh_exit_manifest(root, d0)
    log(root, event_id=d0, category="no-such-category")  # line 2: about D0
    log(root, event_id=f"{d7}/visit-run-sheet.csv:trained", category="?")  # line 3: D7
    log(root, event_id="free-note", category="?")  # line 4, LOG-3: named by a D0 row

    def raw_format(vid):
        return sorted(d.rows for d in hits(reconcile_visit(root, vid), "RAW_FORMAT"))

    assert raw_format(d0) == [("deviations-log.csv:2",), ("deviations-log.csv:4",)]
    assert raw_format(d7) == [("deviations-log.csv:3",)]
