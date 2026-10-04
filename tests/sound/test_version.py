"""Renderer version and renderer hash (renderer spec D10)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import av_sound.renderer as renderer_mod
from av_sound import RENDERER_VERSION, renderer_hash, renderer_manifest, renderer_recipe_schema_hash
from av_sound.version import recipe_schema_sha256

HEX64 = re.compile(r"^[0-9a-f]{64}$")
SCHEMA = Path(__file__).resolve().parents[2] / "sound" / "schema" / "recipe.schema.json"


def test_version_string_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", RENDERER_VERSION)


def test_manifest_contents():
    m = renderer_manifest()
    assert m["renderer_version"] == RENDERER_VERSION
    assert m["constants"]["rms_target"] == renderer_mod.RMS_TARGET
    assert set(m["tables"]) == {
        "sine_int32le",
        "attack_int32le",
        "release_int32le",
        "increment_uint32le",
    }
    assert set(m["sources"]) == {"recipe.py", "renderer.py", "tables.py", "wav.py"}


def test_hashes_are_stable_hex():
    assert HEX64.match(renderer_hash())
    assert renderer_hash() == renderer_hash()
    assert HEX64.match(renderer_recipe_schema_hash())


def test_hash_changes_with_a_constant(monkeypatch):
    before = renderer_hash()
    monkeypatch.setattr(renderer_mod, "RMS_TARGET", renderer_mod.RMS_TARGET + 1)
    assert renderer_hash() != before


def test_schema_digest_is_lf_normalized():
    raw = SCHEMA.read_bytes().replace(b"\r\n", b"\n")
    assert recipe_schema_sha256() == hashlib.sha256(raw).hexdigest()
