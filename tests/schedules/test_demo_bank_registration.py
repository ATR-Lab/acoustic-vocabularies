"""DEMO registration preserves concealed allocation authority and real-ID limits."""

import json
from dataclasses import replace

import pytest
from jsonschema import Draft202012Validator

from av_schedules import build_b_allocation, check_b_allocation, demo_seed, register_demo_bank
from av_schedules._paths import schema_path
from av_schedules.assign_output import b_dyads_document, canonical_sha256


def _validator(name):
    return Draft202012Validator(json.loads(schema_path(name).read_text()))


def test_registered_demo_bank_preserves_design_and_reseals_list():
    original = build_b_allocation(demo_seed("DEMO-registration"), "confirmatory")
    registered = register_demo_bank(original, "B-C01", "DEMO-QBANK-01")
    assert check_b_allocation(registered) == []
    changed = next(d for d in registered.dyads if d.unit_id == "B-C01")
    before = next(d for d in original.dyads if d.unit_id == "B-C01")
    assert replace(changed, bank_id=before.bank_id) == before
    assert original.dyads != registered.dyads
    assert register_demo_bank(registered, "B-C01", "DEMO-QBANK-01") == registered
    doc = b_dyads_document(registered)
    _validator("b-dyads.schema.json").validate(doc)
    assert doc["list_sha256"] == canonical_sha256(doc)
    assert doc["list_sha256"] != b_dyads_document(original)["list_sha256"]
    doc["demo"] = False
    assert not _validator("b-dyads.schema.json").is_valid(doc)


def test_demo_bank_registration_refuses_wrong_scope_rebinding_and_duplicates():
    allocation = build_b_allocation(demo_seed("DEMO-registration"), "confirmatory")
    for alloc, unit, bank in (
        (replace(allocation, demo=False), "B-C01", "DEMO-QBANK-01"),
        (allocation, "B-C01", "bank-C001"),
        (allocation, "B-C99", "DEMO-QBANK-01"),
        (allocation, "B-C01", "DEMO-bad/id"),
    ):
        with pytest.raises(ValueError):
            register_demo_bank(alloc, unit, bank)
    registered = register_demo_bank(allocation, "B-C01", "DEMO-QBANK-01")
    with pytest.raises(ValueError, match="replaced"):
        register_demo_bank(registered, "B-C01", "DEMO-QBANK-02")
    with pytest.raises(ValueError, match="duplicate"):
        register_demo_bank(registered, "B-C02", "DEMO-QBANK-01")
