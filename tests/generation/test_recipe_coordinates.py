"""The 12 recipe coordinates (protocol order) and their allowed values."""

import pytest
from av_sound import FEATURE_NAMES, Recipe, RecipeError
from av_sound import recipe as sound_recipe
from av_sound.synthetic import synthetic_recipes
from hypothesis import given
from hypothesis import strategies as st

from av_generation import domain
from av_generation.domain import (
    COORDINATE_NAMES,
    COORDINATES,
    DOMAIN_SIZE,
    coordinate,
    differing_coordinates,
    recipe_values,
    replace_values,
    values_to_dict,
    values_to_recipe,
)


def test_twelve_coordinates_in_feature_order():
    assert len(COORDINATES) == 12
    assert COORDINATE_NAMES == (
        "pitch_1",
        "pitch_2",
        "pitch_3",
        "total_ms",
        "rhythm_weight_1",
        "rhythm_weight_2",
        "rhythm_weight_3",
        "gap_1",
        "gap_2",
        "amplitude_1",
        "amplitude_2",
        "amplitude_3",
    )
    assert tuple(c.feature for c in COORDINATES) == FEATURE_NAMES
    assert [c.kind for c in COORDINATES].count("pitch") == 3


def test_values_come_from_av_sound():
    assert domain.FIELD_VALUES["total_ms"] is sound_recipe.TOTAL_MS
    assert coordinate("pitch_2").values == tuple(range(-6, 7))
    assert coordinate("total_ms").values == (450, 600, 750, 900)
    assert coordinate("rhythm_weight_3").values == (1, 2, 3, 4)
    assert coordinate("gap_1").values == (20, 40, 60)
    assert coordinate("amplitude_1").values == (0.6, 0.8, 1.0)
    for c in COORDINATES:
        assert list(c.values) == sorted(c.values)
    assert DOMAIN_SIZE == 4 * 13**3 * 4**3 * 3**2 * 3**3 == 136_670_976


@given(st.tuples(*(st.sampled_from(c.values) for c in COORDINATES)))
def test_values_round_trip(values):
    recipe = values_to_recipe(values)
    assert recipe_values(recipe) == values
    assert Recipe.from_dict(values_to_dict(values)) == recipe


def test_replace_and_differences():
    base = synthetic_recipes("P1")["K-a1"]
    changed = replace_values(base, {"gap_2": 60 if base.gaps_ms[1] != 60 else 20})
    assert differing_coordinates(base, changed) == ("gap_2",)
    assert differing_coordinates(base, base) == ()
    with pytest.raises(KeyError):
        replace_values(base, {"pitch_4": 0})
    with pytest.raises(RecipeError):
        replace_values(base, {"pitch_1": 7})
    with pytest.raises(ValueError):
        values_to_dict([0] * 11)


def test_position():
    assert coordinate("amplitude_2").position(0.8) == 1
    assert coordinate("pitch_1").position(-6) == 0
    with pytest.raises(ValueError):
        coordinate("gap_1").position(30)
    with pytest.raises(ValueError):
        coordinate("rhythm_weight_1").position(True)
