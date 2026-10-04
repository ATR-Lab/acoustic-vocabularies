"""Recipe and Profile types (renderer spec section 4)."""

from __future__ import annotations

import json

import pytest

from av_sound import Profile, Recipe, RecipeError

BASE = {
    "total_ms": 600,
    "pitches": [-3, 0, 4],
    "rhythm_weights": [2, 1, 3],
    "gaps_ms": [40, 20],
    "amplitudes": [1.0, 0.6, 0.8],
}


def test_profiles_are_frozen():
    assert [p.value for p in Profile] == ["P1", "P2", "P3"]
    assert [p.f0_hz for p in Profile] == [300, 450, 675]
    with pytest.raises(ValueError):
        Profile("P4")


def test_canonical_json_and_round_trip():
    r = Recipe.from_dict(BASE)
    assert r.canonical_json() == (
        '{"amplitudes":[1.0,0.6,0.8],"gaps_ms":[40,20],"pitches":[-3,0,4],'
        '"rhythm_weights":[2,1,3],"total_ms":600}'
    )
    assert Recipe.from_json(r.canonical_json()) == r
    assert Recipe.from_json(json.dumps(BASE).encode()) == r
    assert len(r.sha256()) == 64


def test_integer_amplitude_one_is_canonicalized():
    r = Recipe.from_dict({**BASE, "amplitudes": [1, 0.6, 0.8]})
    assert r.amplitudes == (1.0, 0.6, 0.8)
    assert '"amplitudes":[1.0,0.6,0.8]' in r.canonical_json()


@pytest.mark.parametrize(
    "patch",
    [
        {"total_ms": 500},
        {"pitches": [7, 0, 0]},
        {"pitches": [0, -7, 0]},
        {"rhythm_weights": [0, 1, 1]},
        {"rhythm_weights": [1, 1, 5]},
        {"gaps_ms": [10, 20]},
        {"gaps_ms": [20, 80]},
        {"amplitudes": [0.7, 0.6, 0.6]},
    ],
)
def test_out_of_domain_values_raise_e_domain(patch):
    with pytest.raises(RecipeError) as err:
        Recipe.from_dict({**BASE, **patch})
    assert err.value.code == "E_DOMAIN"


@pytest.mark.parametrize(
    "data",
    [
        {**BASE, "total_ms": 600.0},
        {**BASE, "total_ms": "600"},
        {**BASE, "total_ms": True},
        {**BASE, "pitches": [0, 0]},
        {**BASE, "pitches": "000"},
        {**BASE, "gaps_ms": [20, 20, 20]},
        {**BASE, "amplitudes": [True, 0.6, 0.6]},
        {**BASE, "amplitudes": ["1.0", 0.6, 0.6]},
        {**BASE, "amplitudes": [1.0, 0.6]},
        {**BASE, "profile": "P1"},
        {k: v for k, v in BASE.items() if k != "gaps_ms"},
    ],
)
def test_malformed_recipes_raise_e_schema(data):
    with pytest.raises(RecipeError) as err:
        Recipe.from_dict(data)
    assert err.value.code == "E_SCHEMA"


def test_non_object_and_invalid_json():
    with pytest.raises(RecipeError):
        Recipe.from_dict([1, 2, 3])  # type: ignore[arg-type]
    with pytest.raises(json.JSONDecodeError):
        Recipe.from_json("{not json")


def test_recipe_is_immutable_and_hashable():
    r = Recipe.from_dict(BASE)
    with pytest.raises(AttributeError):
        r.total_ms = 450  # type: ignore[misc]
    assert len({r, Recipe.from_dict(BASE)}) == 1
