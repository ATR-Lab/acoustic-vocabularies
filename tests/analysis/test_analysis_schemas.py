"""Published JSON Schemas: committed files equal the generated ones; example documents."""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from av_analysis import schemas as schemas_module
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
    assert "stray.schema.json: not generated (no core schema or module SCHEMAS entry)" in problems
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
    # Projection of the #72 ExportBundle manifest (docs/data/synthetic-visit on main).
    source = {
        "format": "data-export-provisional-1",
        "export_id": "312f03e7f2ed4a74bbaead05993c2aaf",
        "manifest_sha256": SHA,
        "protocol_version": "engineering-test",
        "build_sha256": SHA,
        "headers_qualified": False,
        "unacknowledged_torn_tail": False,
        "record_count": 614,
        "trial_rows": 36,
        "exposure_rows": 36,
    }
    exit_manifest = {
        "format": "av-analysis/exit-manifest",
        "format_version": 1,
        "data_kind": "REAL",
        "visit_id": "A-C07-L03-D0",
        "session_id": "11111111111111111111111111111111",
        "station_id": "synthetic-station",
        "closed": "complete",
        "source": source,
        "files": [
            {"path": "trial-log.csv", "bytes": 1, "sha256": SHA},
            {"path": "export/raw/events-0000.local.jsonl", "bytes": 2, "sha256": SHA},
        ],
    }
    check = validator("exit-manifest.schema.json")
    check.validate(exit_manifest)
    check.validate({**exit_manifest, "data_kind": "SYNTHETIC", "source": None})
    for bad in (
        {**exit_manifest, "visit_id": "x"},
        {k: v for k, v in exit_manifest.items() if k != "source"},
        {**exit_manifest, "source": {**source, "extra": 1}},
        {**exit_manifest, "source": {k: v for k, v in source.items() if k != "export_id"}},
    ):
        with pytest.raises(ValidationError):
            check.validate(bad)
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


def _extra_schema(name):
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://github.com/ATR-Lab/acoustic-vocabularies/analysis/schema/" + name,
        "type": "object",
        "additionalProperties": False,
    }


def test_modules_publish_schemas_through_their_schemas_mapping(monkeypatch):
    import av_analysis.simulate as simulate

    monkeypatch.setattr(
        simulate,
        "SCHEMAS",
        {"oc-row.schema.json": lambda: _extra_schema("oc-row.schema.json")},
        raising=False,
    )
    docs = schema_documents()
    assert "oc-row.schema.json" in docs
    assert list(docs) == sorted(docs)
    assert "oc-row.schema.json" in schemas_module.module_schemas()
    assert "oc-row.schema.json" not in schemas_module.core_schemas()


def test_module_schemas_refuse_duplicates_and_bad_names(monkeypatch):
    import av_analysis.monitoring as monitoring
    import av_analysis.simulate as simulate

    one = {"x.schema.json": lambda: _extra_schema("x.schema.json")}
    monkeypatch.setattr(simulate, "SCHEMAS", one, raising=False)
    monkeypatch.setattr(monitoring, "SCHEMAS", one, raising=False)
    with pytest.raises(ValueError, match="published twice"):
        schema_documents()
    monkeypatch.setattr(monitoring, "SCHEMAS", {}, raising=False)
    core = {"glmm-log.schema.json": lambda: _extra_schema("glmm-log.schema.json")}
    monkeypatch.setattr(simulate, "SCHEMAS", core, raising=False)
    with pytest.raises(ValueError, match="already a core schema"):
        schema_documents()
    wrong_id = {"y.schema.json": lambda: _extra_schema("z.schema.json")}
    monkeypatch.setattr(simulate, "SCHEMAS", wrong_id, raising=False)
    with pytest.raises(ValueError, match="must end .schema.json and match"):
        schema_documents()
    assert isinstance(simulate, types.ModuleType)
