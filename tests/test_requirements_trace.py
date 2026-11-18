"""Tests for the requirement catalogue and tools/requirements_trace.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import requirements_trace as rt  # noqa: E402


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _catalogue(root: Path, reqs: list[dict]) -> None:
    _write(root, str(rt.CATALOGUE), json.dumps({"version": 1, "requirements": reqs}))


def _req(rid: str, **extra) -> dict:
    base = {"id": rid, "area": rid.split("-")[1], "text": "x", "owner": "app",
            "verification": "auto", "gate": None, "code": [], "tests": [], "issue": None}
    base.update(extra)
    return base


def test_repository_catalogue_is_consistent():
    trace = rt.build(ROOT)
    assert trace.errors == []
    ids = [r["id"] for r in trace.requirements]
    assert len(ids) == len(set(ids)) > 0


def test_index_is_up_to_date():
    assert rt.main(["index", "--check"]) == 0


def test_every_measurement_requirement_has_a_procedure_and_gate():
    trace = rt.build(ROOT)
    measured = rt.measurement_ids(ROOT)
    for req in trace.requirements:
        if req["verification"] == "measure":
            assert req["gate"] in rt.GATES
            assert req["id"] in measured


def test_unity_underscore_category_and_python_docstring_are_discovered(tmp_path):
    _catalogue(tmp_path, [_req("REQ-TEST-04"), _req("REQ-BAT-01", owner="schedules")])
    _write(tmp_path, "unity/Assets/Tests/EditMode/X/XTests.cs",
           'namespace A.B { public sealed class XTests {\n'
           '  [Test, Category("REQ_TEST_04")]\n  public void SlotIsFourteenSeconds() { }\n} }\n')
    _write(tmp_path, "tests/test_x.py", 'def test_battery():\n    """REQ-BAT-01: 36 trials."""\n')
    trace = rt.build(tmp_path)
    assert trace.errors == []
    nunit = trace.tests["REQ-TEST-04"][0]
    assert (nunit.name, nunit.suite, nunit.kind) == ("SlotIsFourteenSeconds", "A.B.XTests", "nunit")
    assert trace.tests["REQ-BAT-01"][0].name == "test_battery"


def test_hyphenated_nunit_category_is_an_error(tmp_path):
    _catalogue(tmp_path, [_req("REQ-TEST-04", issue=1)])
    _write(tmp_path, "unity/Assets/Tests/EditMode/X/XTests.cs",
           'class XTests {\n  [Test, Category("REQ-TEST-04")]\n  public void T() { }\n}\n')
    errors = rt.build(tmp_path).errors
    assert any("contains - , ! or +" in e for e in errors)


def test_untested_automated_requirement_needs_an_issue(tmp_path):
    _catalogue(tmp_path, [_req("REQ-TEST-04")])
    assert any("no test and no tracking issue" in e for e in rt.build(tmp_path).errors)
    _catalogue(tmp_path, [_req("REQ-TEST-04", issue=278)])
    assert rt.build(tmp_path).errors == []


def test_unknown_tag_and_missing_listed_test_are_errors(tmp_path):
    _catalogue(tmp_path, [_req("REQ-TEST-04", tests=[{"path": "tests/test_y.py", "name": "test_gone"}])])
    _write(tmp_path, "tests/test_y.py", 'def test_other():\n    """REQ-ZZZ-99"""\n')
    errors = rt.build(tmp_path).errors
    assert any("REQ-ZZZ-99" in e for e in errors)
    assert any("listed test not found" in e for e in errors)


def test_report_maps_results_to_requirements(tmp_path):
    _catalogue(tmp_path, [_req("REQ-TEST-04"), _req("REQ-BAT-01"), _req("REQ-ACC-01", issue=5)])
    _write(tmp_path, "unity/Assets/Tests/EditMode/X/XTests.cs",
           'namespace A.B { public sealed class XTests {\n'
           '  [Test, Category("REQ_TEST_04")]\n  public void Slot() { }\n} }\n')
    _write(tmp_path, "tests/test_x.py", 'def test_battery():\n    """REQ-BAT-01"""\n')
    nunit = tmp_path / "edit.xml"
    nunit.write_text('<test-run><test-case fullname="A.B.XTests.Slot" methodname="Slot" result="Failed"/></test-run>')
    junit = tmp_path / "py.xml"
    junit.write_text('<testsuites><testsuite><testcase classname="tests.test_x" name="test_battery"/></testsuite></testsuites>')
    rows = {r["id"]: r["outcome"] for r in rt.report(rt.build(tmp_path), [str(nunit)], [str(junit)])}
    assert rows == {"REQ-TEST-04": "fail", "REQ-BAT-01": "pass", "REQ-ACC-01": "missing"}


def test_catalogue_text_is_neutral():
    """Catalogue entries must not carry venue or document identifiers."""
    text = (ROOT / rt.CATALOGUE).read_text(encoding="utf-8").lower()
    for word in ("rss", "robotics: science", "overleaf", "preregist", "osf"):
        assert word not in text
