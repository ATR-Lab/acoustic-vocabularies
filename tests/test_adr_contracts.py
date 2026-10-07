import json
import re
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[1]
ADR = ROOT / "docs/adr"

# ADR-002 schema copies mirror the shipped contracts; only "$comment" may differ.
MIRRORS = {
    "bridge-state": "isaac/publisher/state-v2.schema.json",
    "control-command": "isaac/commands/command.schema.json",
}


def load(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8-sig"))


@pytest.mark.parametrize("name", ["control-command", "log-event", "bridge-state"])
def test_contract_rejects_answer_field(name):
    schema = load(f"docs/adr/schemas/{name}.schema.json")
    sample = load(f"tests/fixtures/{name}.json")
    Draft202012Validator(schema).validate(sample)
    sample["target"] = "must-not-enter-this-contract"
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(sample)


def test_event_payload_matches_kind():
    schema = load("docs/adr/schemas/log-event.schema.json")
    sample = load("tests/fixtures/log-event.json")
    sample["event_type"] = "state_fault"
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(sample)


@pytest.mark.parametrize("where", ["object", "state"])
def test_public_pose_rejects_nested_answer_metadata(where):
    schema = load("docs/adr/schemas/bridge-state.schema.json")
    sample = load("tests/fixtures/bridge-state.json")
    node = sample["objects"][0] if where == "object" else sample["objects"][0]["state"]
    node["target"] = "must-not-enter-public-state"
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(sample)


@pytest.mark.parametrize("name", sorted(MIRRORS))
def test_adr_schema_mirrors_shipped_contract(name):
    adr = load(f"docs/adr/schemas/{name}.schema.json")
    assert MIRRORS[name] in adr.pop("$comment")
    assert adr == load(MIRRORS[name])


def test_phase1_spike_schema_is_not_the_adr_contract():
    # apparatus/schemas/bridge-state.schema.json is the retained #47 version 1
    # spike envelope; ADR-002 now mirrors the shipped version 2 contract.
    spike = load("apparatus/schemas/bridge-state.schema.json")
    adr = load("docs/adr/schemas/bridge-state.schema.json")
    assert spike["properties"]["version"]["const"] == 1
    assert adr["properties"]["version"]["const"] == 2


ADR_FILES = sorted(ADR.glob("ADR-*.md"))
LINK = re.compile(r"\]\(([^)\s]+)\)")


def test_adrs_remain_proposed():
    assert len(ADR_FILES) == 7
    for path in ADR_FILES:
        status = [line for line in path.read_text(encoding="utf-8").splitlines()
                  if line.startswith("Status:")]
        assert status and status[0].startswith("Status: Proposed"), path.name


@pytest.mark.parametrize("path", ADR_FILES, ids=lambda p: p.name)
def test_adr_links_resolve_on_main(path):
    for target in LINK.findall(path.read_text(encoding="utf-8")):
        repo = "github.com/ATR-Lab/acoustic-vocabularies/"
        if repo in target:
            # Branch blobs and closed-unmerged PRs are not durable evidence.
            assert "/blob/" not in target and "/tree/" not in target, target
            assert not re.search(r"/pull/(94|95|97)\b", target), target
        elif not re.match(r"[a-z]+:", target):
            assert (path.parent / target.split("#")[0]).resolve().is_file(), target
