"""Methodology log templates: encoded headers, reviewed hashes, column classes, drift check."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from av_schedules.planning import RUN_SHEET_COLUMNS
from av_schedules.planning import TEMPLATE_SHA256 as SCHEDULES_TEMPLATE_SHA256

from av_analysis.cli import templates_dir_from_env
from av_analysis.templates import (
    COLUMN_CLASS,
    TEMPLATE_NAMES,
    TEMPLATE_SHA256,
    TEMPLATES,
    check_external,
    columns_of_class,
    template,
)


def crlf_template(name: str) -> bytes:
    """A template file as the methodology folder stores it (header only, CRLF)."""
    return (TEMPLATES[name].header + "\r\n").encode("utf-8")


def write_templates(directory: Path) -> None:
    for name in TEMPLATE_NAMES:
        (directory / TEMPLATES[name].filename).write_bytes(crlf_template(name))


def test_four_templates_with_unique_columns():
    assert set(TEMPLATES) == set(TEMPLATE_NAMES)
    assert [len(t.columns) for t in TEMPLATES.values()] == [46, 24, 12, 14]
    for t in TEMPLATES.values():
        assert len(set(t.columns)) == len(t.columns)
        assert t.header == ",".join(t.columns)
        assert t.raw_name == t.filename.replace("-template", "")
    assert template("deviations").columns[0] == "deviation_id"


def test_run_sheet_template_agrees_with_schedules_oracle():
    t = TEMPLATES["visit-run-sheet"]
    assert t.columns == RUN_SHEET_COLUMNS
    assert SCHEDULES_TEMPLATE_SHA256[t.filename] == t.sha256
    assert TEMPLATE_SHA256[t.filename] == t.sha256


def test_reconstructed_templates_hash_to_the_reviewed_values():
    # The external files are exactly the header line with CRLF: the encoded columns
    # reproduce the reviewed SHA-256 of every template.
    for name in TEMPLATE_NAMES:
        assert hashlib.sha256(crlf_template(name)).hexdigest() == TEMPLATES[name].sha256


def test_every_template_column_has_a_class():
    for t in TEMPLATES.values():
        for c in t.columns:
            assert c in COLUMN_CLASS, c
    used = {c for t in TEMPLATES.values() for c in t.columns}
    assert set(COLUMN_CLASS) == used
    assert columns_of_class("outcome") == {
        "exact_correct",
        "action_correct",
        "referent_correct",
        "response_time_ms",
    }
    assert columns_of_class("condition") >= {"method_masked", "role", "scaffold_family"}
    assert "deviations" in columns_of_class("free_text")


def test_check_external_accepts_identical_copies(tmp_path):
    write_templates(tmp_path)
    result = check_external(tmp_path)
    assert result.ok and result.drift == () and len(result.checked) == 4


def test_check_external_reports_drift_missing_and_header_changes(tmp_path):
    write_templates(tmp_path)
    lf = TEMPLATES["trial-log"]
    (tmp_path / lf.filename).write_bytes((lf.header + "\n").encode())  # LF: same header
    (tmp_path / TEMPLATES["deviations"].filename).unlink()
    ledger = TEMPLATES["exposure-ledger"]
    swapped = list(ledger.columns)
    swapped[0], swapped[1] = swapped[1], swapped[0]
    (tmp_path / ledger.filename).write_bytes((",".join(swapped) + "\r\n").encode())
    sheet = TEMPLATES["visit-run-sheet"]
    (tmp_path / sheet.filename).write_bytes((sheet.header + ",extra\r\n").encode())
    result = check_external(tmp_path)
    assert not result.ok
    text = "\n".join(result.problems)
    assert "deviations-template.csv: missing" in text
    assert "exposure-ledger-template.csv: header differs (column order)" in text
    assert "extra ['extra']" in text
    assert any(d.startswith("trial-log-template.csv") for d in result.drift)


def test_check_external_refuses_data_rows_and_bad_csv(tmp_path):
    write_templates(tmp_path)
    t = TEMPLATES["deviations"]
    row = ",".join(["x"] * len(t.columns)) + "\r\n"
    (tmp_path / t.filename).write_bytes(crlf_template("deviations") + row.encode())
    run = TEMPLATES["visit-run-sheet"]
    (tmp_path / run.filename).write_bytes(b"\xff\xfe")
    problems = "\n".join(check_external(tmp_path).problems)
    assert "deviations-template.csv: template has 1 data rows" in problems
    assert "visit-run-sheet-template.csv: not UTF-8" in problems


# ---------------------------------------------------------------------------------------
# External methodology folder (never copied into the repository)

TEMPLATES_DIR = templates_dir_from_env()
needs_templates = pytest.mark.skipif(
    TEMPLATES_DIR is None,
    reason="AV_TEMPLATES_DIR / AV_PLANNING_DIR not set: external templates not available",
)


@needs_templates
def test_external_templates_match_the_encoded_headers_and_hashes():
    result = check_external(Path(str(TEMPLATES_DIR)))
    assert result.ok, result.problems
    assert result.drift == (), result.drift
    assert len(result.checked) == 4
