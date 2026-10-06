"""Published JSON Schemas: committed files equal the generated ones; example documents."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from av_analysis.codes import CHECKS
from av_analysis.derived import TABLES
from av_analysis.glmm import Engine, GlmmLog, LadderAttempt
from av_analysis.masking import forbidden_keys
from av_analysis.schemas import (
    check_schema_files,
    schema_documents,
    validator,
    write_schema_files,
)

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "analysis" / "schema"
SHA = "a" * 64


def test_committed_schemas_equal_the_generated_ones():
    assert check_schema_files() == []
    names = sorted(p.name for p in SCHEMA_DIR.glob("*.schema.json"))
    assert names == sorted(schema_documents())
    assert {t.schema_name for t in TABLES.values()} <= set(names)


def test_schemas_are_strict_draft_2020_12_with_unique_ids():
    ids = set()
    for name, doc in schema_documents().items():
        assert doc["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert doc["$id"].endswith("/analysis/schema/" + name)
        assert doc["additionalProperties"] is False
        Draft202012Validator.check_schema(doc)
        ids.add(doc["$id"])
    assert len(ids) == len(schema_documents())


def test_check_and_write_detect_drift(tmp_path):
    assert len(write_schema_files(tmp_path)) == len(schema_documents())
    assert check_schema_files(tmp_path) == []
    (tmp_path / "trials-row.schema.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "stray.schema.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "glmm-log.schema.json").unlink()
    problems = "\n".join(check_schema_files(tmp_path))
    assert "trials-row.schema.json: differs" in problems
    assert "stray.schema.json: not generated" in problems
    assert "glmm-log.schema.json: missing" in problems


def reconciliation_document():
    return {
        "format": "av-analysis/reconciliation",
        "format_version": 1,
        "data_kind": "SYNTHETIC",
        "analyzer": {"name": "av-analysis", "version": "0.1.0"},
        "study": "B",
        "set": "pilot",
        "unit_id": "B-P01",
        "person_id": "B-P01-M2",
        "visit": "W1",
        "visit_id": "B-P01-M2-W1",
        "session_id": "SYNTHETIC-7",
        "station_id": None,
        "inputs": [{"path": "raw/B-P01-M2-W1/trial-log.csv", "bytes": 10, "sha256": SHA}],
        "raw_unchanged": True,
        "checks": [
            {
                "check": c.id,
                "name": c.name,
                "status": "fail" if c.id in ("C7", "C8") else "pass",
                "discrepancies": 1 if c.id in ("C7", "C8") else 0,
                "unresolved": 1 if c.id in ("C7", "C8") else 0,
            }
            for c in CHECKS
        ],
        "discrepancies": [
            {
                "seq": 1,
                "check": "C7",
                "code": "WINDOW_LATE",
                "rows": ["B-P01-M2-W1"],
                "deviation_id": None,
                "resolved": False,
                "suspension_event": None,
                "detail": "visit on day 10 after V3; window 6-8",
            },
            {
                "seq": 2,
                "check": "C8",
                "code": "DEVIATION_MISSING",
                "rows": ["WINDOW_LATE B-P01-M2-W1"],
                "deviation_id": None,
                "resolved": False,
                "suspension_event": None,
                "detail": "no deviation record",
            },
        ],
        "summary": {"status": "fail", "discrepancies": 1, "unresolved": 1, "suspension_events": []},
    }


def test_reconciliation_example_validates_and_is_masked():
    doc = reconciliation_document()
    validator("reconciliation.schema.json").validate(doc)
    assert forbidden_keys(doc, "masked") == {}
    for bad in (
        {**doc, "accuracy": 0.5},
        {**doc, "checks": doc["checks"][:7]},
        {**doc, "summary": {**doc["summary"], "status": "not_run"}},
    ):
        with pytest.raises(ValidationError):
            validator("reconciliation.schema.json").validate(bad)


def test_exit_and_outputs_manifest_examples_validate():
    exit_manifest = {
        "format": "av-analysis/exit-manifest",
        "format_version": 1,
        "data_kind": "REAL",
        "visit_id": "A-C07-L03-D0",
        "session_id": "s-0001",
        "station_id": "S2",
        "closed": "complete",
        "files": [{"path": "trial-log.csv", "bytes": 1, "sha256": SHA}],
    }
    validator("exit-manifest.schema.json").validate(exit_manifest)
    with pytest.raises(ValidationError):
        validator("exit-manifest.schema.json").validate({**exit_manifest, "visit_id": "x"})
    outputs = {
        "format": "av-analysis/outputs-manifest",
        "format_version": 1,
        "data_kind": "SYNTHETIC",
        "area": "derived",
        "analyzer": {"name": "av-analysis", "version": "0.1.0"},
        "seeds": ["DEMO-x"],
        "inputs": [],
        "files": [{"path": "trials.csv", "bytes": 3, "sha256": SHA}],
    }
    validator("outputs-manifest.schema.json").validate(outputs)
    with pytest.raises(ValidationError):
        validator("outputs-manifest.schema.json").validate({**outputs, "area": "raw"})


def test_glmm_log_document_validates():
    log = GlmmLog(
        data_kind="SYNTHETIC",
        study="B",
        model_id="B-W1",
        engine=Engine("lme4::glmer", "4.6.1", "2.0-6", "bobyqa"),
        data_sha256=SHA,
        attempts=(
            LadderAttempt(
                "full", "y ~ role", "failed", True, True, ("boundary (singular) fit",), "singular"
            ),
            LadderAttempt("no_correlations", "y ~ role", "accepted", True, False),
        ),
    )
    doc = log.document()
    validator("glmm-log.schema.json").validate(doc)
    assert doc["final_rung"] == "no_correlations"
    assert [a["step"] for a in doc["attempts"]] == [1, 2]
    json.dumps(doc)
