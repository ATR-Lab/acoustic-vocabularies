"""The renderer spec document states the values the code implements."""

from __future__ import annotations

import re
from pathlib import Path

from av_sound import RENDERER_VERSION, RMS_TARGET, Profile, Recipe, file_sha256, render
from av_sound.tables import table_digests
from av_sound.version import SPEC_VERSION

SPEC = (Path(__file__).resolve().parents[2] / "sound" / "docs" / "renderer-spec.md").read_text(
    "utf-8"
)


def _row_digest(table: str) -> str:
    match = re.search(rf"^\| `{table}` \(.*?\| `([0-9a-f]{{64}})` \|$", SPEC, re.MULTILINE)
    assert match, f"no digest row for {table}"
    return match.group(1)


def test_table_digests_match_spec():
    digests = table_digests()
    assert _row_digest("S") == digests["sine_int32le"]
    assert _row_digest("EA") == digests["attack_int32le"]
    assert _row_digest("ER") == digests["release_int32le"]
    assert _row_digest("INC") == digests["increment_uint32le"]


def test_versions_and_target_match_spec():
    assert f"Spec version: **{SPEC_VERSION}**" in SPEC
    assert f'`RENDERER_VERSION = "{RENDERER_VERSION}"`' in SPEC
    assert f"`RMS_TARGET = {RMS_TARGET}` LSB" in SPEC


def test_worked_example_matches_spec():
    r = render(Recipe(600, (-3, 0, 4), (2, 1, 3), (40, 20), (1.0, 0.6, 0.8)), Profile.P2)
    assert f"| `pcm_sha256` | `{r.pcm_sha256}` |" in SPEC
    assert f"| `file_sha256` | `{file_sha256(r)}` |" in SPEC
    assert "| Output peak, RMS | 13,865; 7,336.0 LSB |" in SPEC
    assert r.peak == 13865
    for n in r.event_samples:
        assert f"{n:,} samples" in SPEC
