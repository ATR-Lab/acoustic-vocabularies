"""The shared meaning set: one source of meaning texts for stations, A1 and prompts."""

import dataclasses
from pathlib import Path

import pytest

from av_generation.config import BatchConfig
from av_generation.meanings import ALL_LABELS, MeaningSet, load_meanings
from av_generation.records import RecordError

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "generation/examples/demo-meanings"


def test_demo_set_loads_and_hashes():
    meanings = load_meanings(DEMO)
    assert meanings.demo and meanings.name.startswith("DEMO-")
    assert set(meanings.meanings) == ALL_LABELS
    assert load_meanings(DEMO / "meanings.json", expected_sha256=meanings.sha256()) == meanings
    with pytest.raises(RecordError):
        load_meanings(DEMO, expected_sha256="0" * 64)
    config = BatchConfig.read(ROOT / "generation/examples/demo-batch-config.json")
    assert config.labels["K-a1"] in meanings.for_atom("K-a1", config.labels)
    assert all(len(t) <= 200 and t.isascii() for t in meanings.meanings.values())
    with pytest.raises(KeyError):
        meanings.text("NOPE")


@pytest.mark.parametrize(
    "change",
    [
        lambda m: dataclasses.replace(m, meanings={**m.meanings, "SCAN": "the LLM scans"}),
        lambda m: dataclasses.replace(
            m, meanings={k: v for k, v in m.meanings.items() if k != "H"}
        ),
        lambda m: dataclasses.replace(m, name="real-set-1"),
    ],
)
def test_inconsistent_sets_are_refused(change):
    with pytest.raises(RecordError):
        change(load_meanings(DEMO)).check_consistency()


def test_texts_fit_the_station_message():
    meanings = load_meanings(DEMO)
    too_long = dataclasses.replace(meanings, meanings={**meanings.meanings, "TAG": "x" * 201})
    assert too_long.schema_errors()
    assert MeaningSet.SCHEMA == "meanings.schema.json"
