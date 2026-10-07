"""Raw-log loaders (#33): template headers, value domains, read-only loading, refusals."""

from __future__ import annotations

import json
import shutil

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from av_analysis.fileio import csv_bytes, sha256_file
from av_analysis.loaders import (
    DOMAINS,
    RefusedInputError,
    load_deviations_log,
    load_raw_visit,
    load_template_csv,
    raw_visit_ids,
    table_from_bytes,
    value_problems,
)
from av_analysis.paths import DataRoot
from av_analysis.synthetic_logs import build_synthetic_root
from av_analysis.templates import EXTENSION_COLUMNS, TEMPLATES

SEED = "DEMO-test-33-loaders"


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    return build_synthetic_root(
        tmp_path_factory.mktemp("loaders") / "root", seed_label=SEED, max_persons=1
    )


def valid_row(name):
    """A valid row of a template (a row of the synthetic logs)."""
    return {
        "trial-log": {
            **dict.fromkeys(TEMPLATES["trial-log"].columns, ""),
            "study": "A",
            "protocol_version": "v0.1",
            "participant_id": "DEMO-A0001",
            "batch_id": "A-P01",
            "codebook_id": "BK-P-7QX4MN",
            "session_id": "SYNTHETIC-1",
            "visit": "D0",
            "trial_id": "A-P01-L01-D0-TR-01",
            "semantic_family": "K",
            "trial_type": "trained",
            "message_id": "K-a1-r1",
            "target_action": "ADD_ONE",
            "target_referent": "A",
            "trained_status": "trained",
            "prior_complete_phrase_exposures": "6",
            "prior_atom_exposures": "8",
            "waveform_sha256": "a" * 64,
            "playback_status": "observed_complete",
            "response_code": "commit",
            "technical_fault_code": "AUDIO_UNDERRUN;FRAME_FREEZE",
            "exposure_consumed": "true",
            "feedback_shown": "false",
            "dictionary_available": "false",
        },
        "exposure-ledger": {
            **dict.fromkeys(TEMPLATES["exposure-ledger"].columns, ""),
            "participant_id": "DEMO-B001M1",
            "dyad_id": "B-P01",
            "session_id": "SYNTHETIC-2",
            "wave": "1",
            "event_id": "E1",
            "stage": "atom_menu",
            "atom_or_message_id": "K-a1",
            "candidate_id": "K-a1-2",
            "accepted_or_rejected": "rejected",
            "whole_phrase": "false",
            "presentation_index": "3",
            "audible_status": "uncertain",
            "retrieval_opportunity": "false",
            "active_choice_or_default": "default",
        },
        "visit-run-sheet": {
            **dict.fromkeys(TEMPLATES["visit-run-sheet"].columns, ""),
            "participant_id": "A-P01-L01",
            "visit": "D0",
            "block": "trained",
            "expected_count": "36",
            "start_time": "2027-03-01T09:30:00+01:00",
            "end_time": "2027-03-01T09:39:00+01:00",
            "comfort_check": "adjusted",
            "hash_check": "sha256:" + "b" * 64,
            "operator_signoff": "S03",
        },
        "deviations": {
            **dict.fromkeys(TEMPLATES["deviations"].columns, ""),
            "deviation_id": "DEV-1",
            "timestamp": "2027-03-01T10:00:00Z",
            "protocol_version": "v0.1",
            "operator": "S03",
            "category": "technical",
            "observed_problem": "free text, with commas",
        },
    }[name]


@pytest.mark.parametrize("name", list(TEMPLATES))
def test_valid_rows_have_no_problems(name):
    assert value_problems(TEMPLATES[name], valid_row(name)) == []


@pytest.mark.parametrize(
    ("name", "column", "value", "fragment"),
    [
        ("trial-log", "playback_status", "played", "not one of"),
        ("trial-log", "playback_status", "", "required"),
        ("trial-log", "response_code", "TIMEOUT", "not one of"),
        ("trial-log", "technical_fault_code", "AUDIO_UNDERRUN;", "invalid"),
        ("trial-log", "technical_fault_code", "lowercase", "invalid"),
        ("trial-log", "exposure_consumed", "yes", "not one of"),
        ("trial-log", "scheduled_onset_mono_ms", "1.5", "integer"),
        ("trial-log", "onset_uncertainty_ms", "-1", "below 0"),
        ("trial-log", "message_id", "K-a1-r9", "not a valid"),
        ("trial-log", "waveform_sha256", "A" * 64, "SHA-256"),
        ("trial-log", "sim_time", "abc", "number"),
        ("trial-log", "participant_id", "", "required"),
        ("exposure-ledger", "audible_status", "audible", "not one of"),
        ("exposure-ledger", "stage", "lesson", "not one of"),
        ("exposure-ledger", "presentation_index", "0", "below 1"),
        ("visit-run-sheet", "start_time", "2027-03-01T09:30:00", "UTC offset"),
        ("visit-run-sheet", "operator_signoff", "Jane", "staff"),
        ("visit-run-sheet", "comfort_check", "fine", "not one of"),
        ("visit-run-sheet", "hash_check", "sha256:xyz", "package hash"),
        ("deviations", "category", "misc", "not one of"),
        ("deviations", "timestamp", "", "required"),
        ("deviations", "prior_audio_exposure", "yes", "not one of"),
    ],
)
def test_values_outside_their_domain(name, column, value, fragment):
    row = {**valid_row(name), column: value}
    problems = value_problems(TEMPLATES[name], row)
    assert any(p.startswith(f"{column}: ") and fragment in p for p in problems), problems


def test_cross_column_rules():
    ledger = TEMPLATES["exposure-ledger"]
    assert any(
        "whole_phrase" in p
        for p in value_problems(ledger, {**valid_row("exposure-ledger"), "whole_phrase": "true"})
    )
    trial = TEMPLATES["trial-log"]
    assert any(
        "semantic_family" in p
        for p in value_problems(trial, {**valid_row("trial-log"), "semantic_family": "Q"})
    )
    sheet = TEMPLATES["visit-run-sheet"]
    row = {**valid_row("visit-run-sheet"), "end_time": "2027-03-01T08:00:00+01:00"}
    assert any("before start_time" in p for p in value_problems(sheet, row))
    assert value_problems(trial, {"unknown": "x"}) == ["unknown: not a column of trial-log"]


@settings(max_examples=60, deadline=None)
@given(
    name=st.sampled_from(sorted(TEMPLATES)),
    value=st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=40),
    data=st.data(),
)
def test_value_rules_never_raise(name, value, data):
    column = data.draw(st.sampled_from(sorted(DOMAINS[name])))
    problems = value_problems(TEMPLATES[name], {**valid_row(name), column: value})
    assert all(isinstance(p, str) and ": " in p for p in problems)


def csv_of(name, rows, header=None):
    header = header or (*TEMPLATES[name].columns, *EXTENSION_COLUMNS[name])
    return csv_bytes(header, ([row.get(c, "") for c in header] for row in rows))


def test_headers_accept_extension_columns_in_order_only():
    name = "exposure-ledger"
    cols = TEMPLATES[name].columns
    row = {**valid_row(name), "trial_ref": "T1"}
    for header in (cols, (*cols, "trial_ref"), (*cols, "pcm_sha256", "trial_ref")):
        table = table_from_bytes("x.csv", csv_of(name, [row], header), name)
        assert table.problems == () and len(table.rows) == 1, header
    for header in ((*cols, "trial_ref", "pcm_sha256"), (*cols, "headset_x"), cols[1:]):
        table = table_from_bytes("x.csv", csv_of(name, [row], header), name)
        assert table.rows == () and table.problems[0].line == 1, header
    bad = table_from_bytes("x.csv", b"\xff\xfe", name)
    assert bad.rows == () and bad.problems[0].line == 1


def test_duplicate_row_keys_and_line_numbers():
    rows = [valid_row("trial-log"), valid_row("trial-log")]
    rows[1]["playback_status"] = "?"
    table = table_from_bytes("raw/v/trial-log.csv", csv_of("trial-log", rows), "trial-log")
    assert {(p.line, p.column) for p in table.problems} == {
        (3, "playback_status"),
        (3, "trial_id"),
    }


def test_load_raw_visit_reads_every_file_read_only(root):
    visit_id = raw_visit_ids(root)[0]
    folder = root.raw_visit_dir(visit_id)
    before = {p.name: sha256_file(p) for p in folder.iterdir()}
    raw = load_raw_visit(root, visit_id)
    assert set(raw.files) == set(before) and raw.files == before
    assert raw.exit_manifest is not None and raw.exit_manifest["visit_id"] == visit_id
    for table in (raw.trial_log, raw.exposure_ledger, raw.run_sheet, raw.deviations):
        assert table is not None and table.problems == ()
    assert raw.deviations is not None and raw.deviations.rows == ()
    assert raw.rel("trial-log.csv") == f"raw/{visit_id}/trial-log.csv"
    assert {p.name: sha256_file(p) for p in folder.iterdir()} == before
    table = load_template_csv(root, folder / "trial-log.csv", "trial-log")
    assert table.path == f"raw/{visit_id}/trial-log.csv" and table.rows == raw.trial_log.rows
    with pytest.raises(FileNotFoundError):
        load_raw_visit(root, "A-P03-L06-D7")
    with pytest.raises(ValueError):
        load_raw_visit(root, "not-a-visit")


def test_deviations_log_and_visit_listing(root, tmp_path):
    log = load_deviations_log(root)
    assert log is not None and log.rows == () and log.path == "raw/deviations-log.csv"
    ids = raw_visit_ids(root)
    order = ("D0", "D7", "V1", "V2", "V3", "W1", "W4")
    assert ids == sorted(ids, key=lambda v: (v.rsplit("-", 1)[0], order.index(v[-2:])))
    assert len(ids) == 2 + 10
    empty = DataRoot.create(tmp_path / "empty", "SYNTHETIC", label="DEMO-empty")
    assert raw_visit_ids(empty) == [] and load_deviations_log(empty) is None


def test_real_root_refuses_synthetic_exports(root, tmp_path):
    real = DataRoot.create(tmp_path / "real", "REAL", label="lab-root")
    visit_id = raw_visit_ids(root)[0]
    target = real.raw_visit_dir(visit_id)
    shutil.copytree(root.raw_visit_dir(visit_id), target)  # simulates an import
    with pytest.raises(RefusedInputError, match="SYNTHETIC"):
        load_raw_visit(real, visit_id)
    manifest = json.loads((target / "exit-manifest.json").read_text(encoding="utf-8"))
    manifest["data_kind"] = "REAL"
    (target / "exit-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RefusedInputError, match="export source"):
        load_raw_visit(real, visit_id)
    manifest["source"] = {"unacknowledged_torn_tail": True}
    (target / "exit-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RefusedInputError, match="torn tail"):
        load_raw_visit(real, visit_id)
    manifest["source"] = {"unacknowledged_torn_tail": False}
    (target / "exit-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RefusedInputError, match="synthetic marker"):
        load_raw_visit(real, visit_id)
    manifest["session_id"], manifest["station_id"] = "S-1", "ST-1"
    (target / "exit-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RefusedInputError, match="synthetic marker"):
        load_raw_visit(real, visit_id)  # participant IDs carry DEMO-
    (real.path / "raw" / "deviations-log.csv").write_bytes(
        csv_of("deviations", [{**valid_row("deviations"), "participant_id": "DEMO-A0001"}])
    )
    with pytest.raises(RefusedInputError):
        load_deviations_log(real)


def test_synthetic_root_refuses_a_real_exit_manifest(root, tmp_path):
    visit_id = raw_visit_ids(root)[0]
    copy = DataRoot.create(tmp_path / "copy", "SYNTHETIC", label="DEMO-copy")
    target = copy.raw_visit_dir(visit_id)
    shutil.copytree(root.raw_visit_dir(visit_id), target)
    manifest = json.loads((target / "exit-manifest.json").read_text(encoding="utf-8"))
    manifest["data_kind"] = "REAL"
    (target / "exit-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RefusedInputError, match="REAL data in a SYNTHETIC root"):
        load_raw_visit(copy, visit_id)
    (target / "exit-manifest.json").write_text("[1]", encoding="utf-8")
    assert load_raw_visit(copy, visit_id).exit_manifest is None
