"""Every generation schema: Draft 2020-12, strict objects, `$id` convention, references resolve."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from av_generation import bank_manifest, rater_protocol
from av_generation._schemas import (
    SCHEMA_ID_PREFIX,
    _registry,
    load_schema,
    schema_errors,
    schema_files,
    schema_validator,
)
from av_generation.config import BatchConfig
from av_generation.dryrun import DryRunPlan
from av_generation.genconfig import GenerationConfig
from av_generation.meanings import MeaningSet
from av_generation.records import DOCUMENT_TYPES, RECORD_TYPES

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "generation" / "schema"

EXPECTED = {
    "audit-summary.schema.json",
    "bank-amendment.schema.json",
    "bank-manifest.schema.json",
    "batch-config.schema.json",
    "commit-record.schema.json",
    "common.schema.json",
    "decision-record.schema.json",
    "dry-run-plan.schema.json",
    "fallback-scan-record.schema.json",
    "freeze-manifest.schema.json",
    "generation-config.schema.json",
    "llm-request.schema.json",
    "meanings.schema.json",
    "play-event.schema.json",
    "rater-message.schema.json",
    "rating-record.schema.json",
    "run-manifest.schema.json",
    "slot-record.schema.json",
    "slot-refusal.schema.json",
    "threshold-session.schema.json",
    "threshold-stimuli.schema.json",
    "threshold-trial.schema.json",
    "timing-event.schema.json",
}


def test_schema_set():
    assert set(schema_files()) == EXPECTED
    documents = (*RECORD_TYPES.values(), *DOCUMENT_TYPES.values(), BatchConfig)
    used = {c.SCHEMA for c in (*documents, GenerationConfig, MeaningSet, DryRunPlan)}
    used |= {rater_protocol.SCHEMA, bank_manifest.SCHEMA, bank_manifest.AMENDMENT_SCHEMA}
    used |= {"common.schema.json", "audit-summary.schema.json", "freeze-manifest.schema.json"}
    assert used == EXPECTED


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_metaschema_and_id(name):
    raw = (SCHEMA_DIR / name).read_bytes()
    assert b"\r" not in raw and raw.endswith(b"}\n")
    schema = json.loads(raw)
    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == SCHEMA_ID_PREFIX + name
    assert schema["title"] and schema["description"]


def _objects(node, path="$"):
    if isinstance(node, dict):
        if "properties" in node:
            yield path, node
        for key, value in node.items():
            yield from _objects(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _objects(value, f"{path}[{i}]")


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_objects_forbid_extra_properties(name):
    schema = load_schema(name)
    for path, node in _objects(schema):
        in_conditional = any(f".{k}" in path for k in ("if", "then", "else", "not"))
        if in_conditional:
            continue
        assert node.get("additionalProperties") is False, f"{name} {path}"
        assert set(node.get("required", [])) <= set(node["properties"]), f"{name} {path}"


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_references_resolve(name):
    schema_validator(name)
    schema = load_schema(name)
    refs = []

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                refs.append(node["$ref"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    resolver = _registry().resolver(base_uri=schema["$id"])
    for ref in refs:
        resolver.lookup(ref)


def test_demo_batch_config_example_validates():
    data = json.loads((ROOT / "generation/examples/demo-batch-config.json").read_text("utf-8"))
    assert schema_errors("batch-config.schema.json", data) == ()
