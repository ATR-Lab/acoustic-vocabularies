"""Clean synthetic logs (#33 acceptance): every visit type reconciles with 0 discrepancies,
raw files are unchanged, reports are deterministic, masked and fast."""

from __future__ import annotations

import json
import os
import shutil
import stat
import time
from pathlib import Path

import pytest

from av_analysis import masking
from av_analysis.fileio import read_bytes, sha256_bytes
from av_analysis.loaders import raw_visit_ids
from av_analysis.paths import DataRoot, WatermarkError
from av_analysis.reconcile import reconcile_many, reconcile_visit, report_bytes, write_report
from av_analysis.schemas import validator
from av_analysis.synthetic_logs import (
    ALL_VISIT_TYPES,
    DEFAULT_SEED,
    StudySet,
    build_synthetic_root,
    example_reports,
    suite_visits,
    synthetic_visit_files,
)

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "analysis" / "examples" / "reconciliation-demo"
SEED = "DEMO-test-33-clean"
# Words that must never appear in a report: outcomes, responses, semantic labels, coded
# participant IDs (role words are checked in discrepancy details: the C6 check is named
# "yoked-ledger" by the skeleton).
ROLE_WORDS = ("active", "yoked")
FORBIDDEN_WORDS = (
    "correct",
    "accuracy",
    "response_time",
    "ADD_ONE",
    "REMOVE_ONE",
    "FLIP_CARD",
    "ALIGN_ARROW",
    "SCAN",
    "QUARANTINE",
    "DEMO-A0",
    "DEMO-B0",
)


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    return build_synthetic_root(
        tmp_path_factory.mktemp("clean") / "root", seed_label=SEED, max_persons=2
    )


@pytest.fixture(scope="module")
def reports(root):
    return {r.visit_id: r for r in reconcile_many(root, raw_visit_ids(root))}


def raw_hashes(root):
    return {
        p.relative_to(root.path).as_posix(): sha256_bytes(read_bytes(p))
        for p in sorted(root.area("raw").rglob("*"))
        if p.is_file()
    }


def test_root_holds_every_visit_type(root):
    visits = suite_visits(root)
    assert set(visits) == set(ALL_VISIT_TYPES)
    ids = raw_visit_ids(root)
    assert len(ids) == 2 * 2 + 2 * 5  # two A learners, one B dyad
    marker = json.loads((root.path / "av-data-root.json").read_text(encoding="utf-8"))
    assert marker["data_kind"] == "SYNTHETIC" and marker["label"] == SEED


@pytest.mark.parametrize("visit_type", ALL_VISIT_TYPES)
def test_clean_logs_reconcile_with_zero_discrepancies(root, reports, visit_type):
    for vid in raw_visit_ids(root):
        if not vid.endswith(visit_type):
            continue
        doc = reports[vid].document()
        assert doc["summary"] == {
            "status": "pass",
            "discrepancies": 0,
            "unresolved": 0,
            "suspension_events": [],
        }, (vid, doc["discrepancies"])
        assert doc["discrepancies"] == []
        assert doc["raw_unchanged"] is True
        statuses = {c["check"]: c["status"] for c in doc["checks"]}
        assert set(statuses.values()) <= {"pass", "not_applicable"}
        applicable = {c for c, s in statuses.items() if s == "pass"}
        assert {"C1", "C2", "C3", "C4", "C8"} <= applicable
        if vid.startswith("B-"):
            assert "C5" in applicable
            assert ("C6" in applicable) == (visit_type in ("V1", "V2", "V3"))
        assert ("C7" in applicable) == (visit_type not in ("D0", "V1"))


def test_reports_are_schema_valid_masked_and_list_their_inputs(root, reports):
    for vid, report in reports.items():
        doc = report.document()
        assert list(validator("reconciliation.schema.json").iter_errors(doc)) == []
        assert masking.forbidden_keys(doc, "masked") == {}
        text = report_bytes(report).decode("utf-8")
        for word in FORBIDDEN_WORDS:
            assert word not in text, (vid, word)
        for d in doc["discrepancies"]:
            assert not any(w in f"{d['detail']} {d['rows']}" for w in ROLE_WORDS)
        paths = [i["path"] for i in doc["inputs"]]
        assert paths == sorted(paths)
        assert f"raw/{vid}/trial-log.csv" in paths and "raw/deviations-log.csv" in paths
        assert any(p.startswith("inputs/schedules/") for p in paths)
        assert not any(p.startswith("keys/") for p in paths)
        for entry in doc["inputs"]:
            data = read_bytes(root.path / entry["path"])
            assert (len(data), sha256_bytes(data)) == (entry["bytes"], entry["sha256"])


def test_c6_partner_files_and_status_are_symmetric(reports):
    for visit in ("V1", "V2", "V3"):
        m1 = reports[f"B-P01-M1-{visit}"].document()
        m2 = reports[f"B-P01-M2-{visit}"].document()
        for doc, other in ((m1, "B-P01-M2"), (m2, "B-P01-M1")):
            assert f"raw/{other}-{visit}/exposure-ledger.csv" in {i["path"] for i in doc["inputs"]}
        c6 = [[c for c in d["checks"] if c["check"] == "C6"] for d in (m1, m2)]
        assert c6[0] == c6[1]


def test_same_inputs_give_identical_bytes(root, reports, tmp_path):
    vid = raw_visit_ids(root)[0]
    assert report_bytes(reconcile_visit(root, vid)) == report_bytes(reports[vid])
    again = build_synthetic_root(tmp_path / "again", seed_label=SEED, max_persons=2)
    assert raw_hashes(again) == raw_hashes(root)
    for area in ("inputs",):
        for p in sorted(root.area(area).rglob("*")):
            if p.is_file():
                rel = p.relative_to(root.path)
                assert read_bytes(again.path / rel) == read_bytes(p), rel


def test_raw_sha256_identical_before_and_after_a_run(root, tmp_path):
    copy = tmp_path / "copy"
    shutil.copytree(root.path, copy)
    data_root = DataRoot.open(copy)
    before = raw_hashes(data_root)
    files = [p for p in data_root.area("raw").rglob("*") if p.is_file()]
    for p in files:  # read-only files: any write attempt would fail
        p.chmod(stat.S_IREAD)
    try:
        for report in reconcile_many(data_root, raw_visit_ids(data_root)):
            assert report.raw_unchanged
            write_report(data_root, report)
    finally:
        for p in files:
            p.chmod(stat.S_IREAD | stat.S_IWRITE)
    assert raw_hashes(data_root) == before


def test_one_visit_reconciles_in_under_30_seconds(root):
    for vid in (suite_visits(root)["D0"], suite_visits(root)["V3"]):
        start = time.perf_counter()
        reconcile_visit(root, vid)
        assert time.perf_counter() - start < 30.0


def test_synthetic_visit_files_regenerate_the_raw_folder(root):
    for vid in (suite_visits(root)["D7"], suite_visits(root)["V2"]):
        files = synthetic_visit_files(root, vid)
        folder = root.raw_visit_dir(vid)
        assert files == {p.name: read_bytes(p) for p in folder.iterdir()}
    with pytest.raises(ValueError, match="not a person slot"):
        synthetic_visit_files(root, "A-P01-L09-D0")


def test_generator_refuses_unsafe_targets(root, tmp_path):
    with pytest.raises(WatermarkError, match="not empty"):
        build_synthetic_root(root.path, seed_label=SEED)
    real = DataRoot.create(tmp_path / "real", "REAL", label="lab")
    with pytest.raises(WatermarkError):
        synthetic_visit_files(real, "A-P01-L01-D0")
    with pytest.raises(ValueError):
        build_synthetic_root(tmp_path / "x", seed_label="not-demo")


def test_study_sets_cover_the_confirmatory_lists(tmp_path):
    gen = StudySet(SEED, "A", "confirmatory")
    persons = gen.persons(units=1, max_persons=1)
    assert [p.person_id[:3] for p in persons] == ["A-C"]
    root = build_synthetic_root(
        tmp_path / "c", seed_label=SEED, study="A", set_name="confirmatory", max_persons=1
    )
    for report in reconcile_many(root, raw_visit_ids(root)):
        assert report.passed and report.counted() == []


def test_committed_examples_are_regenerated_exactly(tmp_path):
    expected = example_reports(tmp_path, seed_label=DEFAULT_SEED)
    committed = {p.name: read_bytes(p) for p in EXAMPLES.glob("*.json")}
    assert committed == expected
    names = sorted(expected)
    assert sum(n.endswith("-clean.json") for n in names) == len(ALL_VISIT_TYPES)
    for name, data in expected.items():
        doc = json.loads(data)
        assert doc["data_kind"] == "SYNTHETIC"
        if name.endswith("-clean.json"):
            assert doc["summary"]["discrepancies"] == 0


def test_examples_readme_lists_every_file():
    text = (EXAMPLES / "README.md").read_text(encoding="utf-8")
    for p in EXAMPLES.iterdir():
        if p.name != "README.md":
            assert p.name in text, p.name
    assert "/Users/" not in text and os.sep + "Users" + os.sep not in text
