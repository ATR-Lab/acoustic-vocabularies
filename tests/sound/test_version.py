"""Renderer version and renderer hash (renderer spec D10)."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import av_sound.renderer as renderer_mod
from av_sound import RENDERER_VERSION, renderer_hash, renderer_manifest, renderer_recipe_schema_hash
from av_sound.version import code_digest, recipe_schema_sha256

HEX64 = re.compile(r"^[0-9a-f]{64}$")
SOUND = Path(__file__).resolve().parents[2] / "sound"
SCHEMA = SOUND / "schema" / "recipe.schema.json"
VECTORS = SOUND / "testvectors" / "renderer" / "vectors.json"


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
    assert set(m["code"]) == {"renderer.py", "tables.py", "wav.py"}
    assert m["spec_version"] == "0.1.0"


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


def test_hashes_are_pinned():
    """A failure here means renderer code or the recipe schema changed.

    If rendered bytes changed, bump RENDERER_VERSION; either way regenerate the vectors
    (`sound/tools/make_testvectors.py`) and explain the change in the pull request.
    """
    pins = json.loads(VECTORS.read_text("utf-8"))
    assert renderer_hash() == pins["renderer_hash"]
    assert renderer_recipe_schema_hash() == pins["renderer_recipe_schema_hash"]


def test_code_digest_ignores_docs_and_formatting_but_not_code(tmp_path):
    base = tmp_path / "m.py"
    base.write_text('"""Doc."""\n\nX = 1  # comment\n\n\ndef f():\n    """Doc."""\n    return X\n')
    reformatted = tmp_path / "n.py"
    reformatted.write_text('"""Other doc."""\nX = 1\ndef f():\n    return X  # other\n')
    changed = tmp_path / "o.py"
    changed.write_text('"""Doc."""\nX = 2\ndef f():\n    return X\n')
    assert code_digest(base) == code_digest(reformatted)
    assert code_digest(base) != code_digest(changed)
