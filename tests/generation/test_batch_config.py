"""Study A batch configuration: DEMO example, schema and cross-field rules."""

import dataclasses
import hashlib
import json
from pathlib import Path

import pytest
from av_sound.fallback import fallback_bank_hash

from av_generation.config import BatchConfig
from av_generation.ids import Method
from av_generation.records import RecordError

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "generation/examples/demo-batch-config.json"
FALLBACK = ROOT / "sound/testvectors/fallback/demo-manifest.json"


def demo() -> BatchConfig:
    return BatchConfig.read(EXAMPLE)


def test_demo_example_is_consistent_and_points_at_the_demo_fallback_set():
    config = demo().check_consistency()
    assert config.set == "demo" and config.batch_id.startswith("DEMO-")
    assert config.method_of("DEMO-BK-H9TC") is Method.A3
    assert config.book_of("A1").designer_id == "D1"
    assert config.fallback_manifest_sha256 == hashlib.sha256(FALLBACK.read_bytes()).hexdigest()
    assert config.fallback_bank_hash == fallback_bank_hash(json.loads(FALLBACK.read_text()))
    assert BatchConfig.from_dict(config.to_dict()) == config
    with pytest.raises(KeyError):
        config.method_of("nope")


@pytest.mark.parametrize(
    "change",
    [
        lambda c: dataclasses.replace(c, atom_order=c.atom_order[:-1] + (c.atom_order[0],)),
        lambda c: dataclasses.replace(c, labels={**c.labels, "K-a1": "SCAN"}),
        lambda c: dataclasses.replace(c, labels={**c.labels, "K-a1": c.labels["K-a2"]}),
        lambda c: dataclasses.replace(
            c, books=(c.books[0], c.books[1], dataclasses.replace(c.books[2], method=Method.A2))
        ),
        lambda c: dataclasses.replace(
            c, books=(dataclasses.replace(c.books[0], designer_id=None), *c.books[1:])
        ),
        lambda c: dataclasses.replace(c, panel=dataclasses.replace(c.panel, order_index=1)),
        lambda c: dataclasses.replace(c, set="pilot"),
    ],
)
def test_inconsistent_configs_are_refused(change):
    with pytest.raises(RecordError):
        change(demo()).check_consistency()
