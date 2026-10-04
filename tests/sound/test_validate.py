"""Validator tests (issue #9; Study A protocol §3.2, renderer spec D11).

Every reason code has failing and passing fixtures. `E_NONFINITE` and `E_CLIP`
cannot occur inside the recipe domain (integer renderer, 4.19 dB headroom), so their
failing fixtures inject the condition: a patched `render` that sets `nonfinite`, and
a raised `RMS_TARGET` that makes the renderer report overflow.
"""

from __future__ import annotations

import copy
import dataclasses
import importlib
import itertools
import json
import statistics
import time
from fractions import Fraction
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import av_sound.renderer as renderer_mod
from av_sound import (
    MIN_EVENT_SAMPLES,
    REASON_CODES,
    RENDERER_VERSION,
    VALIDATOR_VERSION,
    Profile,
    Recipe,
    RecipeError,
    Reference,
    ReservedEntry,
    ReservedRegistry,
    distance,
    features,
    file_sha256,
    load_separation_threshold,
    nearest_reference,
    render,
    sum_squared_diff,
    validate,
)
from av_sound._schemas import schema_validator
from av_sound.recipe import AMPLITUDES, GAPS_MS, PITCHES, RHYTHM_WEIGHTS, TOTAL_MS

# `av_sound.validate` (the package attribute) is the function; this is the module.
validate_mod = importlib.import_module("av_sound.validate")
REPO = Path(__file__).resolve().parents[2]
P = Profile.P2

# Synthetic recipes only (no study motifs).
GOOD = Recipe(600, (-3, 0, 4), (2, 1, 3), (40, 20), (1.0, 0.6, 0.8))  # renderer spec worked example
GOOD_DICT = GOOD.to_dict()
GOOD_JSON = GOOD.canonical_json()
FAR = Recipe(900, (6, 6, 6), (4, 1, 1), (60, 60), (0.6, 1.0, 0.6))
NEAR = Recipe(600, (-3, 0, 3), (2, 1, 3), (40, 20), (1.0, 0.6, 0.8))  # one semitone from GOOD
SHORT = Recipe(450, (0, 2, 4), (1, 4, 4), (60, 60), (1.0, 0.8, 0.6))  # event 1: 1,760 samples
NONE: tuple[ReservedEntry, ...] = ()


def ref(ref_id: str, recipe: Recipe, profile: Profile = P) -> Reference:
    return Reference.from_rendered(ref_id, render(recipe, profile))


def entry(entry_id: str, recipe: Recipe | None, profile: Profile | None, pcm_of: Recipe):
    r = render(pcm_of, profile or P)
    return ReservedEntry(
        id=entry_id,
        kind="other",
        profile=profile,
        n_samples=r.n_samples,
        pcm_sha256=r.pcm_sha256,
        file_sha256=file_sha256(r),
        recipe=recipe,
        description="synthetic test entry",
    )


def check(candidate, committed=(), **kwargs):
    kwargs.setdefault("reserved", NONE)
    return validate(candidate, P, committed, **kwargs)


# --- reason codes and result shape ---------------------------------------------------


def test_reason_codes_and_fixed_order():
    assert REASON_CODES == (
        "E_JSON",
        "E_SCHEMA",
        "E_DOMAIN",
        "E_EVENT_SHORT",
        "E_NONFINITE",
        "E_CLIP",
        "E_DUPLICATE",
        "E_RESERVED",
        "E_SEPARATION",
    )


def test_valid_candidate_result_fields():
    committed = [
        ref("K-a1", FAR),
        ref("K-r1", Recipe(750, (1, 2, 3), (3, 2, 1), (20, 60), (1.0, 1.0, 0.6))),
    ]
    result = check(GOOD_JSON, committed)
    rendered = render(GOOD, P)
    assert result.ok and result.codes == () and result.messages == ()
    assert result.primary_code is None
    assert result.recipe == GOOD
    assert result.features == features(GOOD)
    assert result.event_samples == (8640, 4320, 12960)
    assert result.pcm_sha256 == rendered.pcm_sha256
    assert result.rendered is not None and result.rendered.pcm == rendered.pcm
    assert result.threshold == Fraction(1, 10)
    assert result.validator_version == VALIDATOR_VERSION
    assert result.renderer_version == RENDERER_VERSION
    nearest = nearest_reference(GOOD, committed)
    assert nearest is not None
    assert (result.nearest_id, result.nearest_index) == (nearest.ref_id, nearest.index)
    assert result.nearest_distance == nearest.distance


# --- failing and passing fixtures per reason code ----------------------------------------

E_JSON_FAIL = [
    "",
    "{",
    '{"total_ms": 600,}',
    GOOD_JSON[:-1],
    GOOD_JSON + " x",
    GOOD_JSON.replace('"total_ms":600', '"total_ms":600,"total_ms":600'),  # duplicate key
    GOOD_JSON.replace("1.0,0.6,0.8", "NaN,0.6,0.8"),
    GOOD_JSON.replace("1.0,0.6,0.8", "Infinity,0.6,0.8"),
    b"\xff\xfe{}",
    "﻿" + GOOD_JSON,  # byte-order mark
]


@pytest.mark.parametrize("text", E_JSON_FAIL)
def test_e_json_fails(text):
    result = check(text)
    assert result.codes == ("E_JSON",)
    assert result.recipe is None and result.features is None and result.pcm_sha256 is None
    assert result.messages[0].startswith("invalid JSON")


@pytest.mark.parametrize("text", [GOOD_JSON, GOOD_JSON.encode(), json.dumps(GOOD_DICT, indent=2)])
def test_e_json_passes(text):
    assert check(text).ok


E_SCHEMA_FAIL = [
    {k: v for k, v in GOOD_DICT.items() if k != "gaps_ms"},
    {**GOOD_DICT, "profile": "P1"},
    {**GOOD_DICT, "total_ms": "600"},
    {**GOOD_DICT, "total_ms": 600.0},
    {**GOOD_DICT, "total_ms": True},
    {**GOOD_DICT, "pitches": [-3, 0]},
    {**GOOD_DICT, "pitches": [-3, 0, 4, 0]},
    {**GOOD_DICT, "pitches": [-3.0, 0, 4]},
    {**GOOD_DICT, "rhythm_weights": "213"},
    {**GOOD_DICT, "gaps_ms": [40, None]},
    {**GOOD_DICT, "amplitudes": [1.0, 0.6, "0.8"]},
    {**GOOD_DICT, "amplitudes": [True, 0.6, 0.8]},
]


@pytest.mark.parametrize("data", E_SCHEMA_FAIL)
def test_e_schema_fails(data):
    for candidate in (data, json.dumps(data)):
        assert check(candidate).codes == ("E_SCHEMA",)


@pytest.mark.parametrize("candidate", ["[]", "600", "null", '"recipe"', [], None, 600])
def test_non_object_json_is_e_schema(candidate):
    assert check(candidate).codes == ("E_SCHEMA",)


def test_e_schema_passes_for_equivalent_forms():
    tuples = {**GOOD_DICT, "pitches": (-3, 0, 4), "amplitudes": (1, 0.6, 0.8)}
    for candidate in (GOOD, GOOD_DICT, tuples, json.dumps(GOOD_DICT, sort_keys=False)):
        result = check(candidate)
        assert result.ok and result.recipe == GOOD


def test_integer_valued_floats_are_rejected_like_recipe_error():
    """Spec D11: 450.0 is not an integer field value, although JSON Schema allows it."""
    text = GOOD_JSON.replace('"total_ms":600', '"total_ms":600.0')
    assert schema_validator("recipe.schema.json").is_valid(json.loads(text))
    result = check(text)
    assert result.codes == ("E_SCHEMA",)
    assert "600.0" in result.messages[0]
    with pytest.raises(RecipeError) as err:
        Recipe.from_json(text)
    assert err.value.code == "E_SCHEMA"


E_DOMAIN_FAIL = [
    {"total_ms": 500},
    {"total_ms": 1200},
    {"pitches": [7, 0, 0]},
    {"pitches": [0, -7, 0]},
    {"rhythm_weights": [0, 1, 1]},
    {"rhythm_weights": [1, 1, 5]},
    {"gaps_ms": [10, 20]},
    {"gaps_ms": [20, 80]},
    {"amplitudes": [0.7, 0.6, 0.6]},
    {"amplitudes": [1.2, 0.6, 0.6]},
]


@pytest.mark.parametrize("patch", E_DOMAIN_FAIL)
def test_e_domain_fails(patch):
    data = {**GOOD_DICT, **patch}
    assert check(data).codes == ("E_DOMAIN",)
    assert check(json.dumps(data)).codes == ("E_DOMAIN",)


@pytest.mark.parametrize("total_ms", TOTAL_MS)
def test_e_domain_passes_every_allowed_total(total_ms):
    result = check({**GOOD_DICT, "total_ms": total_ms})
    assert "E_DOMAIN" not in result.codes and result.recipe is not None


def test_schema_and_domain_reported_together_and_nothing_else():
    data = {**SHORT.to_dict(), "pitches": [0, 2, 9], "extra": 1}
    result = check(data, [ref("K-a1", SHORT)])
    assert result.codes == ("E_SCHEMA", "E_DOMAIN")
    assert len(result.messages) == 2
    assert result.recipe is None and result.pcm_sha256 is None and result.nearest_id is None


def test_wrong_type_is_not_also_reported_as_domain():
    assert check({**GOOD_DICT, "pitches": ["a", 0, 4]}).codes == ("E_SCHEMA",)
    assert check({**GOOD_DICT, "pitches": [9.5, 0, 4]}).codes == ("E_SCHEMA",)
    assert check({**GOOD_DICT, "pitches": [9.0, 0, 4]}).codes == ("E_SCHEMA",)


SINGLE_FAULTS = [
    {"total_ms": 500},
    {"total_ms": 600.0},
    {"total_ms": "600"},
    {"pitches": [7, 0, 0]},
    {"pitches": [0.5, 0, 0]},
    {"pitches": [0, 0]},
    {"rhythm_weights": [1, 1, 5]},
    {"rhythm_weights": [1, 1, 2.0]},
    {"gaps_ms": [10, 20]},
    {"gaps_ms": [False, 20]},
    {"amplitudes": [0.7, 0.6, 0.6]},
    {"amplitudes": [None, 0.6, 0.6]},
    {"amplitudes": [0.6, 0.6]},
]


@pytest.mark.parametrize("patch", SINGLE_FAULTS)
def test_single_fault_codes_match_recipe_error(patch):
    data = {**GOOD_DICT, **patch}
    with pytest.raises(RecipeError) as err:
        Recipe.from_dict(data)
    assert check(data).codes == (err.value.code,)


def test_missing_and_extra_fields_match_recipe_error():
    for data in ({**GOOD_DICT, "x": 1}, {k: v for k, v in GOOD_DICT.items() if k != "pitches"}):
        with pytest.raises(RecipeError) as err:
            Recipe.from_dict(data)
        assert check(data).codes == (err.value.code,) == ("E_SCHEMA",)


def test_e_event_short_acceptance_case():
    """T=450, gaps 60/60, weights (1,4,4): first event 1,760 samples (36.7 ms) -> rejected."""
    data = {**SHORT.to_dict(), "pitches": [0, 0, 0]}
    result = check(json.dumps(data))
    assert result.codes == ("E_EVENT_SHORT",)
    assert result.event_samples == (1760, 7040, 7040)
    assert "event 1 is 1760 samples (36.7 ms)" in result.messages[0]
    assert result.recipe == Recipe.from_dict(data)  # schema-valid, rejected anyway
    assert result.pcm_sha256 == render(result.recipe, P).pcm_sha256


@pytest.mark.parametrize(
    ("recipe", "short"),
    [
        (Recipe(450, (0, 0, 0), (3, 3, 1), (20, 20), (1.0, 0.8, 0.6)), True),  # 2,812 samples
        (Recipe(600, (0, 0, 0), (1, 4, 4), (20, 40), (1.0, 0.8, 0.6)), False),  # 2,880 samples
        (GOOD, False),
    ],
)
def test_e_event_short_boundary(recipe, short):
    result = check(recipe)
    assert ("E_EVENT_SHORT" in result.codes) is short
    assert (min(result.event_samples) < MIN_EVENT_SAMPLES) is short


def test_short_event_still_gets_duplicate_and_separation():
    result = check(SHORT, [ref("K-a1", SHORT)])
    assert result.codes == ("E_EVENT_SHORT", "E_DUPLICATE", "E_SEPARATION")
    assert result.nearest_id == "K-a1" and result.nearest_distance == 0.0


def test_e_nonfinite_fails_when_renderer_reports_it(monkeypatch):
    real = validate_mod.render

    def nonfinite_render(recipe, profile):
        return dataclasses.replace(real(recipe, profile), nonfinite=True)

    monkeypatch.setattr(validate_mod, "render", nonfinite_render)
    result = check(GOOD, [ref("K-a1", GOOD)])
    assert result.codes == ("E_NONFINITE", "E_SEPARATION")  # no hash, so no duplicate check
    assert result.pcm_sha256 is None


def test_e_nonfinite_passes_for_domain_recipe():
    result = check(GOOD)
    assert "E_NONFINITE" not in result.codes and not result.rendered.nonfinite


def test_e_clip_fails_on_overflow(monkeypatch):
    monkeypatch.setattr(renderer_mod, "RMS_TARGET", 30000)
    result = check(GOOD_JSON)
    assert result.codes == ("E_CLIP",)
    assert result.pcm_sha256 is None
    assert result.rendered.overflow and result.rendered.peak > 32767
    assert result.recipe == GOOD  # never limited or repaired


def test_e_clip_passes_at_spec_target():
    result = check(Recipe(450, (-2, -2, -2), (1, 1, 3), (60, 60), (0.6, 1.0, 0.6)))
    assert result.ok and not result.rendered.overflow  # worst crest factor (spec D6)


def test_e_duplicate_uniform_amplitudes_render_identically():
    """Spec D5: different recipes, same waveform. Distance 0.25 passes separation."""
    medium = Recipe(600, (-3, 0, 4), (2, 1, 3), (40, 20), (0.8, 0.8, 0.8))
    quiet = Recipe(600, (-3, 0, 4), (2, 1, 3), (40, 20), (0.6, 0.6, 0.6))
    assert medium != quiet and distance(medium, quiet) == 0.25
    result = check(quiet.canonical_json(), [ref("K-a1", FAR), ref("Q-r2", medium)])
    assert result.codes == ("E_DUPLICATE",)
    assert "Q-r2" in result.messages[0] and "K-a1" not in result.messages[0]
    assert result.pcm_sha256 == render(medium, P).pcm_sha256


def test_e_duplicate_passes_for_distinct_waveform():
    medium = Recipe(600, (-3, 0, 4), (2, 1, 3), (40, 20), (0.8, 0.8, 1.0))
    assert check(
        medium, [ref("Q-r2", Recipe(600, (-3, 0, 4), (2, 1, 3), (40, 20), (1.0, 1.0, 1.0)))]
    ).ok


def test_e_reserved_exact_waveform_match():
    reserved = [entry("ready-cue-demo", None, None, pcm_of=GOOD)]
    result = check(GOOD_JSON, reserved=reserved)
    assert result.codes == ("E_RESERVED",)
    assert "ready-cue-demo" in result.messages[0]
    registry = ReservedRegistry(1, RENDERER_VERSION, tuple(reserved))
    assert validate(GOOD_JSON, P, reserved=registry).codes == ("E_RESERVED",)


def test_e_reserved_feature_collision_respects_profile():
    close = entry("click-demo", NEAR, Profile.P2, pcm_of=NEAR)
    anywhere = dataclasses.replace(close, profile=None)
    other_profile = entry("click-demo-p1", NEAR, Profile.P1, pcm_of=NEAR)
    assert check(GOOD, reserved=[close]).codes == ("E_RESERVED",)
    assert check(GOOD, reserved=[anywhere]).codes == ("E_RESERVED",)
    assert check(GOOD, reserved=[other_profile]).ok


def test_e_reserved_passes():
    far = entry("calibration-demo", FAR, None, pcm_of=FAR)
    no_recipe = entry("click-demo", None, Profile.P2, pcm_of=FAR)
    assert check(GOOD, reserved=[far, no_recipe]).ok
    assert validate(GOOD, P).ok  # the committed registry is empty until #14 fills it


def test_reserved_registry_built_for_another_renderer_is_refused():
    with pytest.raises(ValueError, match="renderer"):
        validate(GOOD, P, reserved=ReservedRegistry(1, "9.9.9", ()))


def test_e_separation_fails_and_passes():
    committed = [ref("K-a1", FAR), ref("K-a2", NEAR)]
    result = check(GOOD, committed)
    assert result.codes == ("E_SEPARATION",)
    assert "K-a2" in result.messages[0] and "K-a1" not in result.messages[0]
    assert result.nearest_id == "K-a2" and result.nearest_index == 1
    assert result.nearest_distance == pytest.approx((1 / 144 / 12) ** 0.5)
    assert check(GOOD, [ref("K-a1", FAR)]).ok


def test_all_post_render_codes_together_in_order():
    reserved = [entry("other-demo", None, None, pcm_of=SHORT)]
    result = check(SHORT, [ref("K-a1", SHORT)], reserved=reserved)
    assert result.codes == ("E_EVENT_SHORT", "E_DUPLICATE", "E_RESERVED", "E_SEPARATION")
    assert result.primary_code == "E_EVENT_SHORT"
    assert len(result.messages) == len(result.codes)


def test_separation_compares_against_every_reference():
    committed = [ref(f"K-a{i}", FAR) for i in range(1, 4)] + [ref("Q-r4", NEAR)]
    assert check(GOOD, committed).codes == ("E_SEPARATION",)
    assert check(GOOD, committed[:3]).ok


# --- caller errors -----------------------------------------------------------------------


def test_reference_profile_mismatch_raises():
    with pytest.raises(ValueError, match="profile"):
        check(GOOD, [ref("K-a1", FAR, Profile.P1)])


def test_bad_arguments_raise():
    with pytest.raises(ValueError):
        validate(GOOD, "P4")
    with pytest.raises(TypeError):
        check(GOOD, [GOOD])
    with pytest.raises(TypeError):
        check(GOOD, reserved=[GOOD])
    with pytest.raises(TypeError):
        check(GOOD, threshold=0.1)
    with pytest.raises(ValueError):
        check(GOOD, threshold="-0.1")


def test_reference_checks_its_fields():
    r = render(GOOD, P)
    assert Reference("K-a1", GOOD, r.pcm_sha256, "P2").profile is Profile.P2
    with pytest.raises(ValueError):
        Reference("", GOOD, r.pcm_sha256, P)
    with pytest.raises(ValueError):
        Reference("K-a1", GOOD, r.pcm_sha256.upper(), P)
    with pytest.raises(TypeError):
        Reference("K-a1", GOOD_DICT, r.pcm_sha256, P)


def test_reference_from_overflowed_render_raises(monkeypatch):
    monkeypatch.setattr(renderer_mod, "RMS_TARGET", 30000)
    with pytest.raises(OverflowError):
        Reference.from_rendered("K-a1", render(GOOD, P))


# --- never edits, never repairs ----------------------------------------------------------


def test_validator_never_edits_its_inputs():
    data = {**GOOD_DICT, "amplitudes": [1, 0.6, 0.8]}
    before = copy.deepcopy(data)
    committed = [ref("K-a1", NEAR)]
    result = check(data, committed)
    assert data == before
    assert result.recipe == Recipe.from_dict(before)
    short = check(SHORT)
    assert short.recipe == SHORT and not short.ok  # rejected, not shortened or stretched


# --- threshold configuration -------------------------------------------------------------


def test_threshold_comes_from_config():
    config = json.loads((REPO / "sound" / "config" / "validator.json").read_text("utf-8"))
    assert config["separation_threshold"] == "0.10"
    assert "G4" in config["status"]
    assert load_separation_threshold() == Fraction(1, 10)
    assert check(GOOD).threshold == Fraction(1, 10)
    assert check(GOOD).to_dict()["threshold"] == "0.1"


def test_threshold_override_and_custom_config(tmp_path):
    committed = [ref("K-a1", NEAR)]  # distance 0.0241
    assert check(GOOD, committed, threshold="0.02").ok
    assert check(GOOD, committed, threshold=Fraction(1, 40)).codes == ("E_SEPARATION",)
    assert check(GOOD, committed, threshold=0).ok
    path = tmp_path / "validator.json"
    path.write_text(
        json.dumps(
            {
                "config_version": 1,
                "description": "test",
                "separation_threshold": "0.25",
                "status": "test",
            }
        ),
        encoding="utf-8",
    )
    assert load_separation_threshold(path) == Fraction(1, 4)
    path.write_text('{"config_version": 1, "separation_threshold": 0.1}', encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        load_separation_threshold(path)


# --- result serialization ----------------------------------------------------------------


def _results():
    committed = [ref("K-a1", SHORT), ref("K-a2", FAR)]
    yield check(GOOD_JSON, committed)
    yield check(GOOD_JSON)
    yield check("{")
    yield check({**GOOD_DICT, "pitches": [0, 0, 9], "x": 1})
    yield check(SHORT, committed)
    yield check(GOOD_JSON, committed, threshold=Fraction(1, 3))


def test_to_dict_matches_published_schema():
    validator = schema_validator("validation-result.schema.json")
    for result in _results():
        doc = json.loads(json.dumps(result.to_dict(), allow_nan=False))
        errors = list(validator.iter_errors(doc))
        assert not errors, errors
        assert doc["ok"] == (doc["codes"] == [])
        if doc["features"] is not None:
            assert [Fraction(f) for f in doc["features"]] == list(result.features)
    assert next(iter(_results())).to_dict()["threshold"] == "0.1"
    assert check(GOOD, threshold=Fraction(1, 3)).to_dict()["threshold"] == "1/3"


# --- nearest reference -------------------------------------------------------------------


def test_nearest_reference_none_for_first_atom():
    assert nearest_reference(GOOD, []) is None
    result = check(GOOD)
    assert result.nearest_id is None and result.nearest_index is None
    assert result.nearest_distance is None


def test_nearest_reference_ties_go_to_lowest_index():
    a = Recipe(600, (-3, 0, 5), (2, 1, 3), (40, 20), (1.0, 0.6, 0.8))  # +1 semitone
    b = Recipe(600, (-3, 0, 3), (2, 1, 3), (40, 20), (1.0, 0.6, 0.8))  # -1 semitone
    assert sum_squared_diff(GOOD, a) == sum_squared_diff(GOOD, b)
    committed = [ref("K-a1", FAR), ref("Q-a3", b), ref("K-r2", a)]
    nearest = nearest_reference(GOOD, committed)
    assert (nearest.ref_id, nearest.index) == ("Q-a3", 1)
    swapped = [committed[0], committed[2], committed[1]]
    assert nearest_reference(GOOD, swapped).ref_id == "K-r2"
    assert check(GOOD, committed, threshold=0).nearest_id == "Q-a3"


def test_nearest_reference_profiles():
    mixed = [ref("K-a1", FAR, Profile.P1), ref("K-a2", NEAR, Profile.P2)]
    with pytest.raises(ValueError):
        nearest_reference(GOOD, mixed)
    with pytest.raises(ValueError):
        nearest_reference(GOOD, mixed[:1], profile="P2")
    assert nearest_reference(GOOD, mixed[:1], profile="P1").ref_id == "K-a1"
    assert nearest_reference(features(GOOD), mixed[1:]).ref_id == "K-a2"


# --- performance -------------------------------------------------------------------------


def test_validation_against_15_references_is_fast():
    committed = []
    for i, (t, p) in enumerate(itertools.product((600, 750, 900), (-6, -3, 0, 3, 6))):
        committed.append(ref(f"R{i}", Recipe(t, (p, 0, -p), (1, 2, 3), (20, 40), (1.0, 0.8, 0.6))))
    assert len(committed) == 15
    candidate = Recipe(900, (3, -2, 5), (2, 3, 1), (40, 60), (0.8, 1.0, 0.6)).canonical_json()
    check(candidate, committed)  # warm caches
    times = []
    for _ in range(5):
        start = time.perf_counter()
        check(candidate, committed)
        times.append(time.perf_counter() - start)
    assert statistics.median(times) < 1.0  # target < 0.1 s; see sound/tools/bench_validate.py


# --- properties --------------------------------------------------------------------------

recipes = st.builds(
    Recipe,
    total_ms=st.sampled_from(TOTAL_MS),
    pitches=st.tuples(*[st.sampled_from(PITCHES)] * 3),
    rhythm_weights=st.tuples(*[st.sampled_from(RHYTHM_WEIGHTS)] * 3),
    gaps_ms=st.tuples(*[st.sampled_from(GAPS_MS)] * 2),
    amplitudes=st.tuples(*[st.sampled_from(AMPLITUDES)] * 3),
)
json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats() | st.text(max_size=5),
    lambda inner: (
        st.lists(inner, max_size=4) | st.dictionaries(st.text(max_size=8), inner, max_size=4)
    ),
    max_leaves=12,
)
field_values = st.one_of(
    json_values,
    st.lists(
        st.one_of(st.integers(-8, 8), st.sampled_from([0.6, 0.8, 1.0, 0.7, 450.0])), max_size=4
    ),
)
mutated = st.builds(
    lambda base, key, value, drop: {
        **{k: v for k, v in base.to_dict().items() if k != drop},
        key: value,
    },
    recipes,
    st.sampled_from([*GOOD_DICT, "extra"]),
    field_values,
    st.sampled_from([None, *GOOD_DICT]),
)


def _check_result_invariants(result):
    assert set(result.codes) <= set(REASON_CODES)
    assert list(result.codes) == sorted(result.codes, key=REASON_CODES.index)
    assert len(result.codes) == len(set(result.codes)) == len(result.messages)
    assert result.ok == (not result.codes)
    if result.features is not None:
        assert len(result.features) == 12 and all(0 <= f <= 1 for f in result.features)


@settings(max_examples=150, deadline=None)
@given(
    candidate=recipes,
    committed=st.lists(recipes, max_size=4),
    profile=st.sampled_from(list(Profile)),
)
def test_property_domain_recipes(candidate, committed, profile):
    refs = [Reference.from_rendered(f"R{i}", render(r, profile)) for i, r in enumerate(committed)]
    result = validate(candidate.canonical_json(), profile, refs, reserved=NONE)
    _check_result_invariants(result)
    assert set(result.codes) <= {"E_EVENT_SHORT", "E_DUPLICATE", "E_SEPARATION"}
    assert result.recipe == candidate
    assert ("E_EVENT_SHORT" in result.codes) == render(candidate, profile).short_event
    if refs:
        nearest = nearest_reference(candidate, refs)
        assert result.nearest_id == nearest.ref_id and result.nearest_index == nearest.index
        best = min(sum_squared_diff(candidate, r) for r in committed)
        assert nearest.index == min(
            i for i, r in enumerate(committed) if sum_squared_diff(candidate, r) == best
        )
    else:
        assert result.nearest_id is None


@settings(max_examples=300, deadline=None)
@given(candidate=st.one_of(mutated, json_values, st.text(max_size=40), st.binary(max_size=40)))
def test_property_validate_never_raises_on_bad_candidates(candidate):
    result = check(candidate)
    _check_result_invariants(result)
    if isinstance(candidate, dict):
        result_text = check(json.dumps(candidate))
        _check_result_invariants(result_text)
        if result.recipe is None:
            assert set(result.codes) <= {"E_SCHEMA", "E_DOMAIN"}


@settings(max_examples=200, deadline=None)
@given(candidate=mutated)
def test_property_mutated_recipes_agree_with_recipe_type(candidate):
    result = check(candidate)
    try:
        recipe = Recipe.from_dict(candidate)
    except RecipeError as err:
        assert not result.ok and err.code in result.codes
        assert set(result.codes) <= {"E_SCHEMA", "E_DOMAIN"}
    else:
        assert result.recipe == recipe
