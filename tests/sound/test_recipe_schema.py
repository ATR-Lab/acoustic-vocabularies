"""The published recipe schema agrees with the `Recipe` type (issue O4.1.1 acceptance)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from jsonschema import Draft202012Validator

from av_sound import Recipe, RecipeError
from av_sound.recipe import AMPLITUDES, FIELDS, GAPS_MS, PITCHES, RHYTHM_WEIGHTS, TOTAL_MS

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] / "sound" / "schema" / "recipe.schema.json").read_text(
        "utf-8"
    )
)
VALIDATOR = Draft202012Validator(SCHEMA)
BASE = {
    "total_ms": 600,
    "pitches": [0, 0, 0],
    "rhythm_weights": [1, 1, 1],
    "gaps_ms": [20, 20],
    "amplitudes": [0.6, 0.8, 1.0],
}
ALLOWED = {
    "total_ms": TOTAL_MS,
    "pitches": PITCHES,
    "rhythm_weights": RHYTHM_WEIGHTS,
    "gaps_ms": GAPS_MS,
    "amplitudes": AMPLITUDES,
}
PROBES = {
    "total_ms": range(0, 1001, 25),
    "pitches": range(-9, 10),
    "rhythm_weights": range(-1, 7),
    "gaps_ms": range(0, 101, 10),
    "amplitudes": [0.0, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2],
}


def _code_accepts(data: dict) -> bool:
    try:
        Recipe.from_dict(data)
    except RecipeError:
        return False
    return True


def test_schema_is_valid_draft_2020_12():
    Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["additionalProperties"] is False
    assert sorted(SCHEMA["required"]) == sorted(FIELDS)
    assert sorted(SCHEMA["properties"]) == sorted(FIELDS)


def test_schema_enums_equal_code_constants():
    props = SCHEMA["properties"]
    assert tuple(props["total_ms"]["enum"]) == TOTAL_MS
    for field in ("pitches", "rhythm_weights", "gaps_ms", "amplitudes"):
        assert tuple(props[field]["items"]["enum"]) == ALLOWED[field]
    assert (props["pitches"]["minItems"], props["pitches"]["maxItems"]) == (3, 3)
    assert (props["gaps_ms"]["minItems"], props["gaps_ms"]["maxItems"]) == (2, 2)


@pytest.mark.parametrize("field", FIELDS)
def test_schema_accepts_exactly_the_allowed_values(field):
    accepted = []
    for value in PROBES[field]:
        if field == "total_ms":
            ok = VALIDATOR.is_valid({**BASE, field: value})
        else:  # the value must be accepted in every position of the array
            length = len(BASE[field])
            ok = all(
                VALIDATOR.is_valid(
                    {**BASE, field: [value if i == j else BASE[field][i] for i in range(length)]}
                )
                for j in range(length)
            )
        if ok:
            accepted.append(value)
    assert tuple(accepted) == ALLOWED[field]


@pytest.mark.parametrize(
    "data",
    [
        {**BASE, "profile": "P1"},
        {k: v for k, v in BASE.items() if k != "amplitudes"},
        {**BASE, "pitches": [0, 0]},
        {**BASE, "gaps_ms": [20, 20, 20]},
        {**BASE, "total_ms": "600"},
        {**BASE, "amplitudes": [True, 0.6, 0.6]},
    ],
)
def test_schema_and_code_both_reject(data):
    assert not VALIDATOR.is_valid(data)
    assert not _code_accepts(data)


def test_float_for_integer_is_the_only_documented_difference():
    # JSON Schema treats 600.0 as an integer; spec D11 makes the validator reject it.
    data = {**BASE, "total_ms": 600.0}
    assert VALIDATOR.is_valid(data)
    assert not _code_accepts(data)


ints = st.integers(min_value=-10, max_value=1000)
values = st.one_of(ints, st.sampled_from(AMPLITUDES), st.text(max_size=2), st.booleans())


@settings(max_examples=500, deadline=None)
@given(
    total=st.one_of(st.sampled_from(TOTAL_MS), ints),
    pitches=st.lists(st.one_of(st.sampled_from(PITCHES), values), min_size=2, max_size=4),
    weights=st.lists(st.one_of(st.sampled_from(RHYTHM_WEIGHTS), values), min_size=3, max_size=3),
    gaps=st.lists(st.one_of(st.sampled_from(GAPS_MS), values), min_size=2, max_size=2),
    amps=st.lists(st.one_of(st.sampled_from(AMPLITUDES), values), min_size=3, max_size=3),
)
def test_property_schema_and_code_agree(total, pitches, weights, gaps, amps):
    data = {
        "total_ms": total,
        "pitches": pitches,
        "rhythm_weights": weights,
        "gaps_ms": gaps,
        "amplitudes": amps,
    }
    # Documented difference (spec D11): JSON Schema treats 1.0 as an integer, the code
    # rejects floats in integer fields. Everything else must agree exactly.
    float_in_int_field = any(isinstance(v, float) for v in [total, *pitches, *weights, *gaps])
    assert _code_accepts(data) == (VALIDATOR.is_valid(data) and not float_in_int_field)
