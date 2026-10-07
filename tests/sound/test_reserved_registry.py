"""Reserved-signal registry format and loader (issue #9; #14 fills the entries)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from av_sound import (
    RENDERER_VERSION,
    Profile,
    Recipe,
    ReservedEntry,
    ReservedRegistry,
    file_sha256,
    load_reserved_registry,
    render,
    validate,
)
from av_sound._schemas import SCHEMA_FILES, load_schema

SOUND = Path(__file__).resolve().parents[2] / "sound"
REGISTRY = SOUND / "reserved" / "registry.json"
DEMO = Recipe(750, (2, -1, 4), (1, 2, 1), (40, 40), (1.0, 0.8, 0.6))  # synthetic


def demo_entry(**changes):
    r = render(DEMO, Profile.P3)
    data = {
        "id": "ready-cue-demo",
        "kind": "ready_cue",
        "profile": "P3",
        "n_samples": r.n_samples,
        "pcm_sha256": r.pcm_sha256,
        "file_sha256": file_sha256(r),
        "recipe": DEMO.to_dict(),
        "description": "synthetic test entry",
    }
    data.update(changes)
    return data


def registry_doc(*entries, **changes):
    doc = {"registry_version": 1, "renderer_version": RENDERER_VERSION, "entries": list(entries)}
    doc.update(changes)
    return doc


def test_committed_registry_is_canonical_and_current():
    text = REGISTRY.read_text(encoding="utf-8")
    doc = json.loads(text)
    assert text == json.dumps(doc, indent=2, sort_keys=True) + "\n"
    registry = load_reserved_registry()
    assert registry == load_reserved_registry(REGISTRY)
    assert registry.registry_version == 1
    assert registry.renderer_version == RENDERER_VERSION  # rebuild with the renderer (#14)
    assert len(registry.entries) == 7  # nonlexical assets; contents: test_nonlexical.py
    assert registry.to_dict() == doc


def test_published_schemas_are_valid_and_closed():
    for name in SCHEMA_FILES:
        schema = load_schema(name)
        Draft202012Validator.check_schema(schema)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["additionalProperties"] is False
        assert schema["$id"].endswith("/sound/schema/" + name)


def test_entry_round_trip_and_features():
    data = demo_entry()
    entry = ReservedEntry.from_dict(data)
    assert entry.to_dict() == data
    assert entry.profile is Profile.P3 and entry.recipe == DEMO
    assert entry.features is not None and len(entry.features) == 12
    no_recipe = ReservedEntry.from_dict(demo_entry(recipe=None, profile=None, kind="click"))
    assert no_recipe.features is None and no_recipe.profile is None


def test_asset_spec_version_is_optional_and_round_trips():
    without = ReservedRegistry.from_dict(registry_doc(demo_entry()))
    assert without.asset_spec_version is None
    assert without.to_dict() == registry_doc(demo_entry())
    doc = registry_doc(demo_entry(), asset_spec_version="0.1.0")
    with_version = ReservedRegistry.from_dict(doc)
    assert with_version.asset_spec_version == "0.1.0"
    assert with_version.to_dict() == doc
    assert validate(DEMO, Profile.P3, reserved=with_version).codes == ("E_RESERVED",)


def test_load_registry_file_and_cache_invalidation(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry_doc(demo_entry())), encoding="utf-8", newline="\n")
    first = load_reserved_registry(path)
    assert [e.id for e in first.entries] == ["ready-cue-demo"]
    path.write_text(
        json.dumps(registry_doc(demo_entry(), demo_entry(id="click-demo", kind="click"))),
        encoding="utf-8",
        newline="\n",
    )
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert [e.id for e in load_reserved_registry(path).entries] == ["ready-cue-demo", "click-demo"]


@pytest.mark.parametrize(
    "doc",
    [
        registry_doc(registry_version=2),
        registry_doc(renderer_version="pilot"),
        registry_doc(asset_spec_version="pilot"),
        registry_doc(asset_spec_version=1),
        registry_doc(extra=True),
        {"registry_version": 1, "renderer_version": RENDERER_VERSION},
        registry_doc(demo_entry(kind="music")),
        registry_doc(demo_entry(profile="P4")),
        registry_doc(demo_entry(pcm_sha256="ABC")),
        registry_doc(demo_entry(file_sha256="0" * 63)),
        registry_doc(demo_entry(n_samples=0)),
        registry_doc(demo_entry(n_samples=1.5)),
        registry_doc(demo_entry(id="")),
        registry_doc(demo_entry(id="bad id")),
        registry_doc(demo_entry(recipe={"total_ms": 450})),
        registry_doc(demo_entry(recipe={**DEMO.to_dict(), "total_ms": 500})),
        registry_doc({k: v for k, v in demo_entry().items() if k != "description"}),
        registry_doc({**demo_entry(), "notes": "x"}),
        registry_doc(demo_entry(), demo_entry()),  # duplicate id
        [],
    ],
)
def test_invalid_registries_are_refused(doc, tmp_path):
    with pytest.raises(ValueError):
        ReservedRegistry.from_dict(doc)
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError):
        load_reserved_registry(path)


def test_registry_file_must_be_strict_json(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text('{"registry_version": 1, "registry_version": 1}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_reserved_registry(path)


def test_entry_constructor_checks():
    good = ReservedEntry.from_dict(demo_entry())
    fields = {k: getattr(good, k) for k in demo_entry()}
    for change in (
        {"kind": "x"},
        {"n_samples": True},
        {"n_samples": -1},
        {"pcm_sha256": "x"},
        {"id": ""},
    ):
        with pytest.raises(ValueError):
            ReservedEntry(**{**fields, **change})
    with pytest.raises(TypeError):
        ReservedEntry(**{**fields, "recipe": DEMO.to_dict()})
    assert ReservedEntry(**{**fields, "profile": "P1"}).profile is Profile.P1


def test_exact_match_returns_e_reserved():
    registry = ReservedRegistry.from_dict(registry_doc(demo_entry(recipe=None, profile=None)))
    assert validate(DEMO, Profile.P3, reserved=registry).codes == ("E_RESERVED",)
    assert validate(DEMO, Profile.P1, reserved=registry).ok  # other waveform, no recipe check
    with_recipe = ReservedRegistry.from_dict(registry_doc(demo_entry()))
    assert validate(DEMO, Profile.P3, reserved=with_recipe).codes == ("E_RESERVED",)
    assert validate(DEMO, Profile.P2, reserved=with_recipe).ok  # P3-only entry
