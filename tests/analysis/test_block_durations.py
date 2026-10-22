"""Block durations against booked visit lengths (#81 checklist): booking source, per-block
actual versus scheduled seconds, nulls for unrecorded times, refusals."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from av_analysis import block_durations as bd
from av_analysis.cli import main
from av_analysis.fileio import sha256_bytes
from av_analysis.paths import DataRoot
from av_analysis.references import revealed_persons
from av_analysis.schemas import validator
from av_analysis.vocab import VISITS

REPO = Path(__file__).resolve().parents[2]
FROZEN = REPO / "schedules" / "examples" / "demo-run-sheets"
CONSOLE = REPO / "docs" / "operator-console" / "synthetic-exports"
HEADER = (
    "participant_id,visit,block,expected_count,actual_count,start_time,end_time,"
    "comfort_check,phone_locked,hash_check,deviations,operator_signoff\n"
)


def sheet(*rows: str) -> bytes:
    return (HEADER + "".join(r + "\n" for r in rows)).encode()


@pytest.fixture(scope="module")
def synthetic_root(tmp_path_factory):
    path = tmp_path_factory.mktemp("durations") / "root"
    args = ["synth-logs", "--demo-seed", "DEMO-o6.1.4-durations", "--out", str(path)]
    assert (
        main([*args, "--study", "A", "--set", "pilot", "--units", "1", "--max-persons", "1"]) == 0
    )
    root = DataRoot.open(path)
    ((slot, _),) = revealed_persons(root).items()
    return path, slot


@pytest.mark.parametrize("study", ["A", "B"])
def test_booking_equals_the_rendered_run_sheets_manifest(study):
    data = (FROZEN / study / "pilot-run-sheets-manifest.json").read_bytes()
    for visit in VISITS[study]:
        bd.check_run_sheets_manifest(data, study, "pilot", visit)
        assert json.loads(data)["visits"][visit] == bd.booking(study, visit)


def test_frozen_manifest_drift_is_refused():
    doc = json.loads((FROZEN / "A" / "pilot-run-sheets-manifest.json").read_bytes())
    doc["visits"]["D7"]["booked_minutes"] += 1
    with pytest.raises(bd.BlockDurationError):
        bd.check_run_sheets_manifest(json.dumps(doc).encode(), "A", "pilot", "D7")
    for data in (b"\xff", json.dumps(dict(doc, format="x")).encode()):
        with pytest.raises(bd.BlockDurationError):
            bd.check_run_sheets_manifest(data, "A", "pilot", "D7")
    with pytest.raises(bd.BlockDurationError):
        bd.booking("A", "W4")


def test_recorded_blocks_report_actual_against_scheduled_seconds():
    report = bd.compare(
        sheet(
            "A-P01-L01,D7,trained,36,36,2027-03-08T09:10:00+01:00,2027-03-08T09:19:00+01:00,ok,true,,,S01",
            "A-P01-L01,D7,novel,4,4,2027-03-08T09:20:00+01:00,2027-03-08T09:20:56+01:00,ok,true,,,S01",
            "A-P01-L01,D7,atomic,16,16,2027-03-08T09:21:00+01:00,,ok,true,,,S01",
        ),
        "A",
        "D7",
    )
    blocks = {b["block"]: b for b in report["blocks"]}
    assert [b["block"] for b in report["blocks"]] == ["trained", "novel", "atomic", "validity"]
    assert (blocks["trained"]["scheduled_seconds"], blocks["trained"]["actual_seconds"]) == (
        504,
        540,
    )
    assert blocks["trained"]["difference_seconds"] == 36
    assert blocks["novel"]["difference_seconds"] == 0
    assert blocks["atomic"]["actual_seconds"] is None  # no end time: never inferred
    assert blocks["validity"]["in_run_sheet"] is False
    totals = report["visit_totals"]
    assert totals["booked_minutes"] == 30 and totals["blocks_timed"] == 2
    assert totals["block_seconds"] is None and totals["span_seconds"] == 656
    assert totals["actual_minutes"] == 11 and totals["overrun"] is False


def test_console_export_without_block_times_reports_nulls():
    report = bd.compare((CONSOLE / "demo-a-run-sheet.csv").read_bytes(), "A", "D7")
    assert all(b["actual_seconds"] is None for b in report["blocks"])
    assert report["visit_totals"]["overrun"] is None
    assert report["visit_totals"]["blocks_untimed"] == 4


def test_overrun_follows_the_derive_rule():
    report = bd.compare(
        sheet("x,W1,trained,36,36,2027-03-08T09:00:00Z,2027-03-08T10:21:00Z,ok,true,,,S01"),
        "B",
        "W1",
    )
    totals = report["visit_totals"]
    assert totals["actual_minutes"] == 81
    assert totals["overrun"] is (totals["actual_minutes"] > totals["booked_minutes"] + 10)


@pytest.mark.parametrize(
    "data",
    [
        HEADER.replace("phone_locked", "phone").encode(),
        sheet("x,D0,trained,36,36,,,ok,true,,,S01"),
        sheet("x,D7,lessons,1,1,,,ok,true,,,S01"),
        sheet("x,D7,novel,4,4,,,ok,true,,,S01", "x,D7,novel,4,4,,,ok,true,,,S01"),
        sheet("x,D7,novel,4,4,2027-03-08T09:00:00,,ok,true,,,S01"),
        sheet("x,D7,novel,4,4,2027-03-08T09:00:00Z,2027-03-08T08:00:00Z,ok,true,,,S01"),
        b"a,a\n",
    ],
)
def test_malformed_run_sheets_are_refused(data):
    with pytest.raises(bd.BlockDurationError):
        bd.compare(data, "A", "D7")


def test_cli_reports_a_raw_visit_and_writes_once(synthetic_root, tmp_path, capsys):
    path, slot = synthetic_root
    out = tmp_path / "d0.json"
    assert main(["block-durations", f"{slot}-D0", "--root", str(path), "--out", str(out)]) == 0
    report = json.loads(out.read_bytes())
    assert not list(validator(bd.SCHEMA_NAME).iter_errors(report))
    assert report["data_kind"] == "SYNTHETIC" and report["visit_totals"]["blocks_untimed"] == 0
    assert all(b["difference_seconds"] == 0 for b in report["blocks"])
    assert "booked 75 min" in capsys.readouterr().out
    assert main(["block-durations", f"{slot}-D0", "--root", str(path), "--out", str(out)]) == 2


def test_run_sheet_differing_from_the_exit_manifest_is_refused(synthetic_root, tmp_path, capsys):
    src, slot = synthetic_root
    path = tmp_path / "root"
    shutil.copytree(src, path)
    root = DataRoot.open(path)
    frozen = path / "inputs" / "schedules" / "A" / "pilot-run-sheets-manifest.json"
    shutil.copy(FROZEN / "A" / "pilot-run-sheets-manifest.json", frozen)
    assert bd.visit_report(root, f"{slot}-D7")["visit"] == "D7"  # frozen booking agrees
    sheet_path = root.raw_visit_dir(f"{slot}-D7") / "visit-run-sheet.csv"
    data = sheet_path.read_bytes()
    sheet_path.write_bytes(data + b"\n")
    assert sha256_bytes(sheet_path.read_bytes()) != sha256_bytes(data)
    assert main(["block-durations", f"{slot}-D7", "--root", str(path)]) == 2
    assert "differs from the exit manifest" in capsys.readouterr().err
    (root.raw_visit_dir(f"{slot}-D7") / "exit-manifest.json").unlink()
    with pytest.raises(bd.BlockDurationError):
        bd.visit_report(root, f"{slot}-D7")
