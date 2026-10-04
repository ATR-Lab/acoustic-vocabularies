import json
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["control-command", "log-event"])
def test_contract_rejects_answer_field(name):
    schema = json.loads((ROOT / f"docs/adr/schemas/{name}.schema.json").read_text())
    sample = json.loads((ROOT / f"tests/fixtures/{name}.json").read_text())
    sample["target"] = "must-not-enter-this-contract"
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(sample)


def test_event_payload_matches_kind():
    schema = json.loads((ROOT / "docs/adr/schemas/log-event.schema.json").read_text())
    sample = json.loads((ROOT / "tests/fixtures/log-event.json").read_text())
    sample["event_type"] = "state_fault"
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(sample)
