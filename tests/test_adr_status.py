"""Keep ADR status lines, the ADR index and the station contracts consistent."""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADR_DIR = ROOT / "docs/adr"
ADRS = sorted(ADR_DIR.glob("ADR-[0-9][0-9][0-9]-*.md"))


def status_word(path):
    match = re.search(r"^Status: (\w+)", path.read_text(encoding="utf-8"), re.MULTILINE)
    assert match, f"{path.name} has no Status line"
    return match.group(1)


def section(path, title):
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else None


def enum(schema_path, *keys):
    node = json.loads((ROOT / schema_path).read_text(encoding="utf-8"))
    for key in keys:
        node = node[key]
    return node["enum"]


def test_seven_adrs_exist():
    assert [p.name[:7] for p in ADRS] == [f"ADR-00{i}" for i in range(1, 8)]


@pytest.mark.parametrize("path", ADRS, ids=lambda p: p.name[:7])
def test_status_is_proposed_or_accepted(path):
    assert status_word(path) in {"Proposed", "Accepted"}


@pytest.mark.parametrize("path", ADRS, ids=lambda p: p.name[:7])
def test_accepted_adr_lists_conditions_and_revisit_trigger(path):
    if status_word(path) != "Accepted":
        pytest.skip("Proposed")
    assert section(path, "Acceptance conditions"), "Accepted ADR needs measurable conditions"
    assert section(path, "Revisit trigger")
    decision = section(path, "Decision")
    for undecided in ("No winning topology is selected", "Do not select an input method yet",
                      "Keep route, buffer and"):
        assert undecided not in decision


def test_index_matches_status_lines():
    index = (ADR_DIR / "README.md").read_text(encoding="utf-8")
    rows = dict(re.findall(r"^\| \[(ADR-\d{3})\]\([^)]*\) \| [^|]+ \| (\w+) \|$", index, re.MULTILINE))
    assert rows == {p.name[:7]: status_word(p) for p in ADRS}


def test_accepted_topology_is_a_station_option():
    adr = ADR_DIR / "ADR-001-headset-topology.md"
    if status_word(adr) != "Accepted":
        pytest.skip("Proposed")
    chosen = re.search(r"^Status: Accepted.*?`(\w+)`", adr.read_text(encoding="utf-8"), re.MULTILINE).group(1)
    assert chosen in enum("apparatus/schemas/station.schema.json", "properties", "topology")


def test_accepted_input_is_a_panel_and_station_option():
    adr = ADR_DIR / "ADR-004-input.md"
    if status_word(adr) != "Accepted":
        pytest.skip("Proposed")
    chosen = re.search(r"^Status: Accepted.*?`(\w+)`", adr.read_text(encoding="utf-8"), re.MULTILINE).group(1)
    assert chosen in enum("apparatus/response-panel/response-panel.schema.json", "properties", "input_method")
    station_value = {"controller_ray": "controllers", "hand_poke": "hands"}[chosen]
    assert station_value in enum("apparatus/schemas/station.schema.json", "properties", "input_method")
    example = json.loads((ROOT / "apparatus/response-panel/response-panel.example.json").read_text(encoding="utf-8"))
    assert example["input_method"] == chosen


def test_accepted_audio_route_keeps_scheduled_nonspatial_48k():
    adr = ADR_DIR / "ADR-005-audio.md"
    if status_word(adr) != "Accepted":
        pytest.skip("Proposed")
    decision = section(adr, "Decision")
    for required in ("48 kHz", "nonspatial", "PlayScheduled", "Bluetooth are not used", "<=20 ms"):
        assert required in decision
