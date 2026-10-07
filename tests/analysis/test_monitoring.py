"""Integrity dashboard (#35): column allowlist, masking gates and the output schema.

Acceptance criteria covered here: the allowlist fixture (outcome and condition columns
render 0 fields; the build fails) and "output contains 0 personal name or contact fields
(schema test)".
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from av_analysis import cli
from av_analysis.derived import PERSON_RE, TABLES, UNIT_RE, VISIT_ID_RE
from av_analysis.masking import forbidden_keys, forbidden_reason, free_text_columns
from av_analysis.monitoring import (
    ALLOWED_COLUMNS,
    EXCLUDED_COLUMNS,
    PANELS,
    SCHEMAS,
    SOURCE_TABLES,
    DisallowedColumnError,
    MonitoringData,
    allowlist,
    check_monitoring_data,
    disallowed_columns,
    load_monitoring_data,
    render,
    write_dashboard,
)
from av_analysis.monitoring_demo import write_demo_root
from av_analysis.monitoring_metrics import build_document
from av_analysis.schemas import check_schema_files, schema_documents, validator

FIXTURE_COLUMNS = (
    "exact_correct",
    "response_action",
    "response_time_ms",
    "method_masked",
    "role",
    "scaffold_family",
)
FIXTURE_VALUES = {
    "exact_correct": "true",
    "response_action": "ADD_ONE",
    "response_time_ms": "1234",
    "method_masked": "A3",
    "role": "yoked",
    "scaffold_family": "structured",
}
# String fields of the dashboard document that hold generated text from package constants.
GENERATED_TEXT = {"title", "resolution", "message", "version"}


def _field_present(name: str, text: str) -> bool:
    """``name`` appears as a token (not inside a longer identifier or word)."""
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text, re.I) is not None


def _add_columns(path: Path, columns: dict[str, str], position: int | None = None) -> None:
    """Insert extra columns (same value on every row) into a table file."""
    reader = csv.reader(io.StringIO(path.read_text(encoding="utf-8"), newline=""))
    header, *rows = list(reader)
    at = len(header) if position is None else position
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header[:at] + list(columns) + header[at:])
    for row in rows:
        writer.writerow(row[:at] + list(columns.values()) + row[at:])
    path.write_bytes(out.getvalue().encode("utf-8"))


@pytest.fixture
def demo_root(tmp_path):
    return write_demo_root(
        tmp_path / "root",
        "DEMO-monitoring-test",
        sets=("pilot",),
        progress=0.7,
        inject=[("wrong_hash", None), ("changed_old_atom", None)],
    )


# ---------------------------------------------------------------------------------------
# Allowlist


def test_every_source_column_is_allow_listed_or_a_reviewed_exclusion():
    assert set(ALLOWED_COLUMNS) == set(EXCLUDED_COLUMNS) == set(SOURCE_TABLES)
    assert SOURCE_TABLES == ("visit-status", "discrepancies", "enrollment")
    for name in SOURCE_TABLES:
        allowed, excluded = set(ALLOWED_COLUMNS[name]), set(EXCLUDED_COLUMNS[name])
        assert len(allowed) == len(ALLOWED_COLUMNS[name])
        assert not allowed & excluded, name
        assert allowed | excluded == set(TABLES[name].header), name
        assert "data_kind" in allowed
        assert all(reason for reason in EXCLUDED_COLUMNS[name].values())


def test_allow_listed_columns_pass_masking_and_are_never_free_text():
    for name, columns in allowlist().items():
        for column in columns:
            assert forbidden_reason(column, "masked") is None, (name, column)
            assert column not in free_text_columns(), (name, column)
    for panel in PANELS:
        assert set(panel.sources) <= set(SOURCE_TABLES), panel.id


def test_disallowed_columns_names_a_reason_for_each_refused_column():
    free_text = sorted(free_text_columns())[0]
    found = disallowed_columns(
        "visit-status",
        [*TABLES["visit-status"].header, *FIXTURE_COLUMNS, free_text, "book_id", "detail"],
    )
    assert found == {
        "exact_correct": "outcome",
        "response_action": "outcome",
        "response_time_ms": "outcome",
        "method_masked": "condition",
        "role": "condition",
        "scaffold_family": "condition",
        free_text: "free_text",
        "book_id": "not allow-listed",
        "detail": "not allow-listed",  # excluded only in discrepancies
    }
    assert disallowed_columns("discrepancies", TABLES["discrepancies"].header) == {}
    assert disallowed_columns("enrollment", ["participant_name", "contact_email"]) == {
        "participant_name": "personal",
        "contact_email": "personal",
    }


def test_allowlist_fixture_with_outcome_and_condition_columns_renders_none_of_them(demo_root):
    """Acceptance: a fixture with exact_correct, response_action, response_time_ms,
    method_masked, role and scaffold_family renders 0 of those fields (the build fails
    before anything is written)."""
    clean_html = render(load_monitoring_data(demo_root))
    for name in FIXTURE_COLUMNS:
        assert not _field_present(name, clean_html), name

    _add_columns(demo_root.area("reconciled") / "visit-status.csv", FIXTURE_VALUES)
    with pytest.raises(DisallowedColumnError) as excinfo:
        write_dashboard(demo_root)
    for name in FIXTURE_COLUMNS:
        assert f"visit-status.{name}" in str(excinfo.value)
    assert not demo_root.area("monitoring").exists()
    assert cli.main(["dashboard", "--root", str(demo_root.path)]) == 2
    assert not demo_root.area("monitoring").exists()


def test_fixture_columns_spread_over_all_three_tables_all_fail_the_build(demo_root, capsys):
    reconciled = demo_root.area("reconciled")
    _add_columns(reconciled / "visit-status.csv", {"exact_correct": "true"}, position=3)
    _add_columns(
        reconciled / "discrepancies.csv",
        {"response_action": "ADD_ONE", "role": "yoked"},
        position=1,
    )
    _add_columns(
        reconciled / "enrollment.csv",
        {k: FIXTURE_VALUES[k] for k in ("response_time_ms", "method_masked", "scaffold_family")},
    )
    assert cli.main(["dashboard", "--root", str(demo_root.path)]) == 2
    err = capsys.readouterr().err
    for name in FIXTURE_COLUMNS:
        assert name in err
    assert not demo_root.area("monitoring").exists()


def test_in_memory_rows_with_fixture_fields_never_reach_the_renderer(demo_root):
    data = load_monitoring_data(demo_root)
    for name in FIXTURE_COLUMNS:
        rows = tuple({**r, name: FIXTURE_VALUES[name]} for r in data.tables["visit-status"])
        tainted = MonitoringData(data.data_kind, {**data.tables, "visit-status": rows}, data.inputs)
        with pytest.raises(DisallowedColumnError, match=name):
            render(tainted)


def test_check_monitoring_data_refuses_other_tables_kinds_and_excluded_columns(demo_root):
    data = load_monitoring_data(demo_root)
    check_monitoring_data(data)
    for row in data.tables["discrepancies"]:
        assert set(row) <= set(ALLOWED_COLUMNS["discrepancies"])
    rows = tuple({**r, "detail": "x"} for r in data.tables["discrepancies"])
    with pytest.raises(DisallowedColumnError, match="not allow-listed"):
        check_monitoring_data(
            MonitoringData(data.data_kind, {**data.tables, "discrepancies": rows}, data.inputs)
        )
    with pytest.raises(DisallowedColumnError, match="reads exactly"):
        check_monitoring_data(MonitoringData(data.data_kind, {}, data.inputs))
    with pytest.raises(DisallowedColumnError, match="reads exactly"):
        check_monitoring_data(
            MonitoringData(data.data_kind, {**data.tables, "trials": ()}, data.inputs)
        )
    with pytest.raises(DisallowedColumnError, match="unknown data kind"):
        check_monitoring_data(MonitoringData("DEMO", data.tables, data.inputs))
    with pytest.raises(DisallowedColumnError, match="not REAL"):
        check_monitoring_data(MonitoringData("REAL", data.tables, data.inputs))


_POOL = (
    *FIXTURE_COLUMNS,
    "accuracy",
    "trained_score",
    "rating_ownership",
    "target_referent",
    "presentation",
    "yoked_source_event_id",
    "participant_name",
    "contact_email",
    "book_id",
    "operator",
    "unexpected_n",
)


@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    table=st.sampled_from(SOURCE_TABLES),
    columns=st.lists(st.sampled_from(_POOL), min_size=1, max_size=5, unique=True),
    position=st.integers(min_value=0, max_value=60),
)
def test_any_disallowed_column_anywhere_fails_and_writes_nothing(
    tmp_path_factory, table, columns, position
):
    root = write_demo_root(
        tmp_path_factory.mktemp("prop") / "root", "DEMO-prop", studies=("B",), sets=("pilot",)
    )
    path = root.area("reconciled") / TABLES[table].filename
    header_len = len(TABLES[table].header)
    _add_columns(path, dict.fromkeys(columns, "x"), position=min(position, header_len))
    with pytest.raises(DisallowedColumnError) as excinfo:
        write_dashboard(root)
    for column in columns:
        assert f"{table}.{column}" in str(excinfo.value)
    assert not root.area("monitoring").exists()


# ---------------------------------------------------------------------------------------
# Output schema: no personal name or contact field


def _walk_schema(node, path="$"):
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            yield from _walk_schema(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk_schema(value, f"{path}[{i}]")


def test_dashboard_schema_is_published_and_strict_with_no_personal_or_masked_field():
    """Acceptance: output contains 0 personal name or contact fields (schema test)."""
    assert list(SCHEMAS) == ["dashboard-data.schema.json"]
    doc = schema_documents()["dashboard-data.schema.json"]
    assert check_schema_files() == []
    objects = 0
    for where, node in _walk_schema(doc):
        if node.get("type") == "object" or "properties" in node:
            objects += 1
            assert node.get("additionalProperties") is False, where
            assert set(node.get("required", [])) == set(node["properties"]), where
            for key, sub in node["properties"].items():
                assert forbidden_reason(key, "masked") is None, f"{where}.{key}"
                types = sub.get("type", [])
                types = [types] if isinstance(types, str) else types
                if "string" in types:
                    constrained = {"pattern", "enum", "const"} & set(sub)
                    assert constrained or key in GENERATED_TEXT, f"{where}.{key}"
    assert objects > 20


def test_dashboard_output_validates_and_holds_coded_ids_only(demo_root):
    write_dashboard(demo_root)
    out = demo_root.area("monitoring")
    doc = json.loads((out / "dashboard.json").read_text(encoding="utf-8"))
    assert list(validator("dashboard-data.schema.json").iter_errors(doc)) == []
    assert forbidden_keys(doc, "masked") == {}
    html = (out / "index.html").read_text(encoding="utf-8")
    body = html.split("</style>", 1)[1]  # the stylesheet holds an @media rule
    assert "@" not in body
    assert re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", html) is None  # no e-mail address
    assert re.search(r"\+?\(?[0-9]{3}\)?[ .][0-9]{3}[ .-][0-9]{4}", html) is None  # no phone
    for needle in ("mailto:", "tel:", "http://", "https://", "<script", "<img", "<link"):
        assert needle not in html, needle
    for name in FIXTURE_COLUMNS:
        assert not _field_present(name, html), name
        assert not _field_present(name, json.dumps(doc)), name
    ids = re.findall(r"\b[AB]-[PCS][0-9]{2}(?:-[LM][0-9]{1,2})?(?:-[DVW][0-9])?\b", html)
    assert ids
    for value in ids:
        assert any(re.fullmatch(p, value) for p in (UNIT_RE, PERSON_RE, VISIT_ID_RE)), value


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("visit_id", 7),  # string column
        ("opportunities_n", "3"),  # integer column
        ("fault_n", True),  # booleans are not counts
        ("days_since_anchor", "1"),  # nullable integer column
        ("pair_gap_hours", "1.5"),  # nullable number column
        ("overrun", "yes"),  # nullable boolean column
        ("checks_failed", "C1"),  # list column
    ],
)
def test_metrics_refuse_untyped_cells(demo_root, column, value):
    """The metrics read typed rows (derived.parse_table); a raw cell is a TypeError."""
    data = load_monitoring_data(demo_root)
    held = [r for r in data.tables["visit-status"] if r["visit_state"] == "held"]
    extra = {"pair_gap_ok": False} if column == "pair_gap_hours" else {}  # read when outside
    rows = tuple({**r, **extra, column: value} for r in held)
    tables = {**data.tables, "visit-status": rows}
    with pytest.raises(TypeError, match=column):
        build_document(tables, data.data_kind, {}, version="0")
