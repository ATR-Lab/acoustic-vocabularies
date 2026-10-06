"""Strict output parser (#17 "test_parser"): exactly one JSON object, nothing else."""

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from av_generation.parser import MAX_ERROR_CHARS, ParsedOutput, parse_output

RECIPE = {
    "total_ms": 900,
    "pitches": [-4, -4, 0],
    "rhythm_weights": [1, 3, 2],
    "gaps_ms": [40, 20],
    "amplitudes": [1.0, 0.6, 0.8],
}
TEXT = json.dumps(RECIPE)


@pytest.mark.parametrize(
    "text",
    [
        TEXT,
        json.dumps(RECIPE, indent=2),
        "\n" + TEXT + "\n",
        " \t\r\n" + TEXT + " \r\n\t",
        '{"total_ms": 450}',
        "{}",
        '{"total_ms": 1e3, "x": null}',
    ],
)
def test_accepts_exactly_one_object(text):
    parsed = parse_output(text)
    assert parsed.ok and parsed.error is None
    assert parsed.obj == json.loads(text)


def test_object_passes_unchanged():
    parsed = parse_output('{"total_ms": 500, "extra": [1, 2], "pitches": "x"}')
    assert parsed.obj == {"total_ms": 500, "extra": [1, 2], "pitches": "x"}


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "   \n\t",
        "Here is the recipe: " + TEXT,
        TEXT + " I hope this helps.",
        TEXT + TEXT,
        TEXT + "\n" + TEXT,
        "```json\n" + TEXT + "\n```",
        "[" + TEXT + "]",
        '"' + TEXT.replace('"', '\\"') + '"',
        "450",
        "null",
        "true",
        TEXT[:-1],
        '{"total_ms": 450, "total_ms": 600}',
        '{"total_ms": NaN}',
        '{"total_ms": Infinity}',
        "﻿" + TEXT,
        "{'total_ms': 450}",
        '{"total_ms": 450,}',
        " " + TEXT,
        "\x00" + TEXT,
        "[" * 100_000,
    ],
)
def test_refuses_anything_else(text):
    parsed = parse_output(text)
    assert not parsed.ok and parsed.obj is None
    assert parsed.error and parsed.error.startswith("output is not exactly one JSON object")
    assert len(parsed.error) <= MAX_ERROR_CHARS
    assert all(" " <= c <= "~" for c in parsed.error)


def test_refuses_non_text():
    parsed = parse_output(b"{}")  # type: ignore[arg-type]
    assert parsed == ParsedOutput(
        None, "output is not exactly one JSON object: expected text, got bytes"
    )


def test_huge_integer_is_refused_not_raised():
    assert not parse_output('{"total_ms": ' + "9" * 5000 + "}").ok


_json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=8),
    lambda inner: (
        st.lists(inner, max_size=3) | st.dictionaries(st.text(max_size=5), inner, max_size=3)
    ),
    max_leaves=10,
)


@settings(max_examples=200, deadline=None)
@given(st.dictionaries(st.text(max_size=8), _json_values, max_size=6))
def test_property_any_object_round_trips(obj):
    assert parse_output(json.dumps(obj)).obj == obj
    assert parse_output(json.dumps(obj, indent=1, ensure_ascii=False)).obj == obj


@settings(max_examples=200, deadline=None)
@given(_json_values.filter(lambda v: not isinstance(v, dict)))
def test_property_other_json_values_are_refused(value):
    assert not parse_output(json.dumps(value)).ok


@settings(max_examples=300, deadline=None)
@given(st.text(max_size=60))
def test_property_never_raises_and_agrees_with_json(text):
    parsed = parse_output(text)
    try:
        expected = json.loads(text)
    except (ValueError, RecursionError):
        expected = None
    if parsed.ok:
        assert parsed.obj == expected and isinstance(expected, dict)
    else:
        assert parsed.obj is None and parsed.error


@settings(max_examples=200, deadline=None)
@given(
    st.dictionaries(st.text(max_size=5), st.integers(), max_size=4),
    st.text(min_size=1, max_size=10).filter(lambda s: s.strip(" \t\n\r") != ""),
)
def test_property_junk_around_an_object_is_refused(obj, junk):
    text = json.dumps(obj)
    assert not parse_output(text + junk).ok
    assert not parse_output(junk + text).ok
