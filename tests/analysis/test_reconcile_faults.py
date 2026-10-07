"""Fault-injection suite (#33 acceptance): every listed fault is detected with its code on
every visit type it applies to; documented faults are resolved and leave C8 clean."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from av_analysis.codes import CODE_BY_ID, FAULT_INJECTIONS
from av_analysis.fileio import read_bytes
from av_analysis.paths import DataRoot, WatermarkError
from av_analysis.reconcile import reconcile_visit, report_bytes
from av_analysis.synthetic_logs import (
    DEFAULT_SEED,
    FAULT_VISITS,
    build_synthetic_root,
    fault_suite,
    fault_suite_csv,
    inject_fault,
    suite_visits,
)

ROOT = Path(__file__).resolve().parents[2]
SUITE_CSV = ROOT / "analysis" / "examples" / "reconciliation-demo" / "fault-suite.csv"
SEED = "DEMO-test-33-faults"
CASES = [
    (fault, visit, documented)
    for fault, visits in FAULT_VISITS.items()
    for visit in visits
    for documented in (False, True)
    if not (documented and fault == "unlinked_discrepancy")
]


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    return build_synthetic_root(
        tmp_path_factory.mktemp("faults") / "base", seed_label=SEED, max_persons=2
    )


def injected(base, tmp_path, fault, visit_type, documented):
    target = tmp_path / "root"
    shutil.copytree(base.path, target)
    root = DataRoot.open(target)
    vid = suite_visits(root)[visit_type]
    inject_fault(root, vid, fault, documented=documented)
    return root, vid


def test_the_suite_covers_every_listed_fault():
    assert set(FAULT_VISITS) == set(FAULT_INJECTIONS)
    assert {c for c in FAULT_INJECTIONS.values()} <= set(CODE_BY_ID)
    for fault in ("missing_trial", "extra_play", "wrong_hash", "broken_retry_of"):
        assert len(FAULT_VISITS[fault]) == 7


@pytest.mark.parametrize(("fault", "visit_type", "documented"), CASES)
def test_injected_fault_is_detected_with_its_code(base, tmp_path, fault, visit_type, documented):
    root, vid = injected(base, tmp_path, fault, visit_type, documented)
    report = reconcile_visit(root, vid)
    expected = FAULT_INJECTIONS[fault]
    found = [d for c in report.checks for d in c.discrepancies]
    hits = [d for d in found if d.code == expected]
    assert hits, (fault, vid, [d.code for d in found])
    assert all(d.check == CODE_BY_ID[expected].check for d in hits)
    assert report.raw_unchanged
    missing = [d for d in found if d.code == "DEVIATION_MISSING"]
    if documented:
        assert all(d.resolved for d in hits) and not missing, [d for d in found]
        assert report.passed
        assert {c.status for c in report.checks if c.check == hits[0].check} == {"explained"}
    else:
        assert missing and not report.passed
        assert any(c.status == "fail" for c in report.checks if c.check == hits[0].check)


def test_yoked_mismatch_is_written_identically_into_both_reports(base, tmp_path):
    root, vid = injected(base, tmp_path, "yoked_mismatch", "V2", False)
    docs = [reconcile_visit(root, f"B-P01-M{m}-V2").document() for m in (1, 2)]
    c6 = [[d for d in doc["discrepancies"] if d["check"] == "C6"] for doc in docs]
    assert c6[0] and c6[0] == c6[1]
    for d in c6[0]:
        assert d["rows"] == sorted(d["rows"])
        assert "yoked" not in d["detail"] and "active" not in d["detail"]


def test_suspension_events_are_raised(base, tmp_path):
    root, vid = injected(base, tmp_path, "wrong_hash", "D0", True)
    doc = reconcile_visit(root, vid).document()
    assert doc["summary"]["suspension_events"] == ["WRONG_FILE_MAPPING"]
    assert doc["summary"]["status"] == "pass"  # documented: explained, but still an alert
    root2, vid2 = injected(base, tmp_path / "2", "changed_old_atom", "W4", False)
    doc2 = reconcile_visit(root2, vid2).document()
    assert doc2["summary"]["suspension_events"] == ["OLD_WAVEFORM_CHANGED"]


def test_inject_fault_refuses_bad_requests(base, tmp_path):
    root, vid = injected(base, tmp_path, "extra_play", "D0", False)
    with pytest.raises(ValueError, match="unknown fault"):
        inject_fault(root, vid, "no_such_fault")
    with pytest.raises(ValueError, match="does not apply"):
        inject_fault(root, vid, "yoked_mismatch")
    with pytest.raises(ValueError, match="no raw folder"):
        inject_fault(root, "A-P03-L06-D0", "extra_play")
    real = DataRoot.create(tmp_path / "real", "REAL", label="lab")
    with pytest.raises(WatermarkError):
        inject_fault(real, vid, "extra_play")


def test_fault_suite_summary_matches_the_committed_csv(tmp_path):
    cases = fault_suite(tmp_path / "suite", seed_label=DEFAULT_SEED)
    assert len(cases) == len(CASES) and all(c.ok for c in cases)
    assert fault_suite_csv(cases) == read_bytes(SUITE_CSV)
    reports = sorted((tmp_path / "suite" / "reports").glob("*.json"))
    assert len(reports) == len(cases)
    assert not (tmp_path / "suite" / "cases").exists()


def test_report_bytes_of_a_failing_visit_are_deterministic(base, tmp_path):
    root, vid = injected(base, tmp_path, "holdout_in_lesson", "V2", False)
    assert report_bytes(reconcile_visit(root, vid)) == report_bytes(reconcile_visit(root, vid))
