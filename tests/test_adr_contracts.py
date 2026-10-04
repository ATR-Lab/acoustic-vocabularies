import json
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["control-command", "log-event", "bridge-state"])
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


def test_public_pose_rejects_nested_answer_metadata():
    schema = json.loads((ROOT / "docs/adr/schemas/bridge-state.schema.json").read_text())
    sample = json.loads((ROOT / "tests/fixtures/bridge-state.json").read_text())
    sample["objects"][0]["target"] = "must-not-enter-public-state"
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(sample)


def test_bridge_schema_copies_agree_when_spike_is_integrated():
    spike = ROOT / "apparatus/schemas/bridge-state.schema.json"
    if spike.exists():
        assert json.loads(spike.read_text()) == json.loads(
            (ROOT / "docs/adr/schemas/bridge-state.schema.json").read_text())
