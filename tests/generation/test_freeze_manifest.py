"""G4 freeze manifest format (#25 owns this module and `freeze-manifest.schema.json`).

Skeleton tests: the required item keys and the schema. #25 extends this module with the
manifest builder and the CI freeze-guard tests.
"""

from av_generation import freeze
from av_generation._schemas import load_schema, schema_errors
from av_generation.genconfig import FREEZE_CONFIG_KEY


def _draft() -> dict:
    items = [
        {"key": k, "category": k.split(".")[0], "value": None, "sha256": None, "source": "pending"}
        for k in freeze.REQUIRED_ITEM_KEYS
    ]
    return {
        "format": freeze.FREEZE_FORMAT,
        "format_version": 1,
        "freeze_version": "1.0",
        "status": "draft",
        "protocol_version": "0.1",
        "repo_commit": None,
        "tag": None,
        "items": items,
        "apparatus": {f: None for f in freeze.APPARATUS_FIELDS},
        "signoff": [],
    }


def test_freeze_manifest_schema():
    doc = _draft()
    assert schema_errors("freeze-manifest.schema.json", doc) == ()
    assert schema_errors("freeze-manifest.schema.json", dict(doc, status="frozen"))
    assert len(set(freeze.REQUIRED_ITEM_KEYS)) == len(freeze.REQUIRED_ITEM_KEYS)


def test_required_keys_hold_the_frozen_config_hash_and_fit_the_categories():
    assert FREEZE_CONFIG_KEY in freeze.REQUIRED_ITEM_KEYS
    assert {"meanings.sha256", "llm.manifest_sha256"} <= set(freeze.REQUIRED_ITEM_KEYS)
    schema = load_schema("freeze-manifest.schema.json")
    categories = set(schema["properties"]["items"]["items"]["properties"]["category"]["enum"])
    assert {k.split(".")[0] for k in freeze.REQUIRED_ITEM_KEYS} <= categories
