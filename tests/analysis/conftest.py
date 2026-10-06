"""Analysis tests need the `av_analysis` package and its dev group.

Run them with `uv run --project analysis pytest --import-mode=importlib -p no:cacheprovider
tests/analysis`. A bare `python -m pytest` from the repository root (without the package
installed) skips this directory instead of failing on imports.

Tests marked `needs_r` need `Rscript` with the pinned packages (`analysis/r/pins.dcf`):
they are skipped when R is missing (set `AV_RSCRIPT` to point at a specific Rscript) and
run in CI by the `r` job of `.github/workflows/analysis.yml`.
"""

import importlib.util
import os
import shutil

import pytest

_REQUIRED = ("av_analysis", "av_schedules", "numpy", "scipy", "jsonschema", "hypothesis")

if any(importlib.util.find_spec(name) is None for name in _REQUIRED):
    collect_ignore_glob = ["*"]


def _rscript_available() -> bool:
    configured = os.environ.get("AV_RSCRIPT")
    if configured:
        return os.path.isfile(configured)
    return shutil.which("Rscript") is not None


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "needs_r: needs Rscript with the pinned R packages (analysis/r/pins.dcf); "
        "skipped when R is missing",
    )


def pytest_collection_modifyitems(config, items):
    if _rscript_available():
        return
    skip = pytest.mark.skip(
        reason="Rscript not found (install the pinned R or set AV_RSCRIPT; CI job 'r' runs it)"
    )
    for item in items:
        if "needs_r" in item.keywords:
            item.add_marker(skip)


# Valid sample values for the string columns of the derived and reconciled tables.
SAMPLE_STR = {
    "unit_id": "A-C01",
    "book_id": "BK-C-7QX4MN",
    "person_id": "A-C01-L01",
    "visit_id": "A-C01-L01-D0",
    "session_id": "SYNTHETIC-1",
    "trial_id": "A-C01-L01-D0-TR-01",
    "retry_of": "A-C01-L01-D0-TR-02",
    "item_id": "K-a1-r1",
    "station_id": "S1",
    "report_sha256": "0" * 64,
    "detail": "row missing",
    "first_audible_event_id": "E-1",
    "deviation_id": "DV-1",
}


def _sample_value(column):
    if column.type == "enum":
        return column.values[0]
    if column.type == "str":
        return SAMPLE_STR[column.name]
    if column.type == "int":
        return 1 if column.minimum is None else max(column.minimum, 1)
    if column.type == "float":
        return 1.5
    if column.type == "bool":
        return True
    if column.type == "date":
        return "2027-03-01"
    if column.type == "list":
        return (column.values[0],) if column.values is not None else ("A-C01-L01-D0-TR-01",)
    raise AssertionError(column.type)


@pytest.fixture
def sample_row():
    """``sample_row(spec, **overrides)``: a valid SYNTHETIC row of a table spec."""

    def make(spec, **overrides):
        row = {c.name: _sample_value(c) for c in spec.columns}
        row["data_kind"] = "SYNTHETIC"
        row.update(overrides)
        return row

    return make
