"""Reconciled and derived table specifications: encoding, validation, ordering."""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from av_analysis.derived import (
    DISCREPANCIES,
    ENDPOINTS,
    EXPOSURE_CUMULATIVE,
    TABLES,
    TRIALS,
    VISIT_STATUS,
    Column,
    TableFormatError,
    format_row,
    format_value,
    parse_row,
    parse_table,
    parse_value,
    row_problems,
    table,
    table_bytes,
)
from av_analysis.vocab import FAULT_CODES


def test_five_tables_with_watermark_first():
    assert list(TABLES) == [
        "trials",
        "endpoints",
        "visit-status",
        "discrepancies",
        "exposure-cumulative",
    ]
    for spec in TABLES.values():
        assert spec.header[0] == "data_kind"
        assert len(set(spec.header)) == len(spec.header)
        assert set(spec.key) <= set(spec.header) and set(spec.sort) <= set(spec.header)
        assert spec.producer == "#33"
        assert table(spec.name) is spec
        assert spec.filename == f"{spec.name}.csv"
    assert {s.area for s in TABLES.values()} == {"derived", "reconciled"}
    assert TRIALS.area == ENDPOINTS.area == "derived"
    assert [f"fault_{f}_n" for f in FAULT_CODES] == [
        c for c in VISIT_STATUS.header if c.startswith("fault_") and c != "fault_n"
    ]
    with pytest.raises(KeyError):
        TRIALS.column("nope")


@pytest.mark.parametrize("name", list(TABLES))
def test_sample_rows_are_valid_and_round_trip(name, sample_row):
    spec = TABLES[name]
    row = sample_row(spec)
    assert row_problems(spec, row) == []
    assert parse_row(spec, format_row(spec, row)) == row
    data = table_bytes(spec, [row], "SYNTHETIC")
    assert data.endswith(b"\n") and b"\r" not in data
    assert parse_table(spec, data, data_kind="SYNTHETIC") == [row]


def test_rows_are_sorted_and_keys_unique(sample_row):
    rows = [
        sample_row(DISCREPANCIES, seq=3),
        sample_row(DISCREPANCIES, seq=1),
        sample_row(DISCREPANCIES, seq=2, visit_seq=1),
    ]
    data = table_bytes(DISCREPANCIES, rows, "SYNTHETIC")
    assert [r["seq"] for r in parse_table(DISCREPANCIES, data)] == [1, 2, 3]
    assert table_bytes(DISCREPANCIES, list(reversed(rows)), "SYNTHETIC") == data
    with pytest.raises(TableFormatError, match="duplicate key"):
        table_bytes(DISCREPANCIES, [rows[0], dict(rows[0])], "SYNTHETIC")


def test_watermark_and_schema_are_enforced(sample_row):
    row = sample_row(ENDPOINTS)
    with pytest.raises(TableFormatError, match="data_kind"):
        table_bytes(ENDPOINTS, [row], "REAL")
    with pytest.raises(TableFormatError, match="status"):
        table_bytes(ENDPOINTS, [dict(row, status="done")], "SYNTHETIC")
    data = table_bytes(ENDPOINTS, [row], "SYNTHETIC")
    with pytest.raises(TableFormatError, match="data_kind is not REAL"):
        parse_table(ENDPOINTS, data, data_kind="REAL")
    bad_header = data.replace(b"battery", b"batteries", 1)
    with pytest.raises(TableFormatError, match="header"):
        parse_table(ENDPOINTS, bad_header)
    with pytest.raises(TableFormatError, match="fields"):
        parse_table(ENDPOINTS, data + b"x\n")
    duplicated = data + data.split(b"\n", 1)[1]
    with pytest.raises(TableFormatError, match="duplicate key"):
        parse_table(ENDPOINTS, duplicated)
    invalid = data.replace(b",complete,", b",done,")
    with pytest.raises(TableFormatError, match="line 2"):
        parse_table(ENDPOINTS, invalid)


def test_patterns_reject_malformed_ids(sample_row):
    assert row_problems(TRIALS, sample_row(TRIALS, person_id="P-0412"))
    assert row_problems(TRIALS, sample_row(TRIALS, visit_id="A-C01-L01-V1"))
    assert row_problems(TRIALS, sample_row(TRIALS, item_id="K-a5-r1"))
    assert row_problems(VISIT_STATUS, sample_row(VISIT_STATUS, report_sha256="ABC"))
    assert row_problems(TRIALS, sample_row(TRIALS, deviation_ids=("a|b",)))
    assert row_problems(TRIALS, sample_row(TRIALS, actual_delay_hours=math.inf))
    assert row_problems(EXPOSURE_CUMULATIVE, sample_row(EXPOSURE_CUMULATIVE, test_plays_n=-1))


def test_cell_encoding():
    flt = Column("x", "float", "x", nullable=True)
    assert format_value(flt, -0.0) == "0.0"
    assert format_value(flt, 2) == "2.0"
    assert format_value(flt, None) == ""
    assert parse_value(flt, "") is None
    for bad in (math.nan, "1.0", True):
        with pytest.raises(TableFormatError):
            format_value(flt, bad)
    with pytest.raises(TableFormatError):
        parse_value(flt, "inf")
    integer = Column("n", "int", "n")
    assert parse_value(integer, "-3") == -3
    for text in ("", "01", "1.0", "+1"):
        with pytest.raises(TableFormatError):
            parse_value(integer, text)
    with pytest.raises(TableFormatError):
        format_value(integer, True)
    with pytest.raises(TableFormatError):
        format_value(integer, None)
    boolean = Column("b", "bool", "b")
    assert format_value(boolean, False) == "false" and parse_value(boolean, "true") is True
    for text in ("True", "1", "yes"):
        with pytest.raises(TableFormatError):
            parse_value(boolean, text)
    with pytest.raises(TableFormatError):
        format_value(boolean, 1)
    day = Column("d", "date", "d")
    with pytest.raises(TableFormatError):
        parse_value(day, "2027-02-30")
    lst = Column("l", "list", "l")
    assert format_value(lst, None) == "" and parse_value(lst, "") == ()
    with pytest.raises(TableFormatError):
        format_value(lst, ["a"])
    text = Column("s", "str", "s")
    with pytest.raises(TableFormatError):
        format_value(text, "")
    with pytest.raises(TableFormatError):
        format_row(TRIALS, {"data_kind": "SYNTHETIC"})
    with pytest.raises(TableFormatError):
        parse_row(TRIALS, ["SYNTHETIC"])


@given(st.floats(allow_nan=False, allow_infinity=False))
def test_floats_round_trip(value):
    col = Column("x", "float", "x")
    assert parse_value(col, format_value(col, value)) == value + 0.0


@given(st.integers(min_value=-(2**63), max_value=2**63))
def test_ints_round_trip(value):
    col = Column("n", "int", "n")
    assert parse_value(col, format_value(col, value)) == value


@given(st.lists(st.text(alphabet="ABCDEF-_0123456789", min_size=1), max_size=5).map(tuple))
def test_lists_round_trip(value):
    col = Column("l", "list", "l")
    assert parse_value(col, format_value(col, value)) == value
