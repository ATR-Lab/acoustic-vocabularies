"""Slot outcome codes and the validator-code mapping with its precedence (#17)."""

import itertools

import pytest
from av_sound import Profile, Recipe, Reference, render, validate
from av_sound.synthetic import synthetic_recipes
from av_sound.validate import REASON_CODES

from av_generation.outcomes import (
    B_RELATIONAL_CODES,
    OUTCOME_CODES,
    VALIDATOR_CODE_TO_OUTCOME,
    VALIDATOR_PRECEDENCE,
    LlmStatus,
    OutcomeError,
    SlotOutcome,
    is_technically_valid,
    outcome_from_llm_status,
    outcome_from_validation,
    outcome_from_validator_codes,
)

ISSUE_17_CODES = (
    "valid",
    "invalid_json",
    "schema_violation",
    "out_of_domain",
    "render_fail",
    "event_too_short",
    "clipping",
    "duplicate",
    "reserved_collision",
    "separation_fail",
    "incompatible",
    "overflow_input",
    "overflow_output",
    "timeout",
)


def test_fourteen_codes_in_issue_order():
    assert OUTCOME_CODES == ISSUE_17_CODES
    assert len(set(OUTCOME_CODES)) == 14


def test_precedence_is_the_validator_order_and_every_code_maps():
    assert VALIDATOR_PRECEDENCE == REASON_CODES
    assert set(VALIDATOR_CODE_TO_OUTCOME) == set(REASON_CODES)
    mapped = set(VALIDATOR_CODE_TO_OUTCOME.values())
    assert SlotOutcome.VALID not in mapped
    assert SlotOutcome.INCOMPATIBLE not in mapped


@pytest.mark.parametrize(("code", "outcome"), sorted(VALIDATOR_CODE_TO_OUTCOME.items()))
def test_single_code_mapping(code, outcome):
    assert outcome_from_validator_codes([code]) is outcome


def test_highest_precedence_code_wins_for_every_pair():
    for a, b in itertools.combinations(REASON_CODES, 2):
        first = a if REASON_CODES.index(a) < REASON_CODES.index(b) else b
        expected = VALIDATOR_CODE_TO_OUTCOME[first]
        assert outcome_from_validator_codes([b, a]) is expected
        assert outcome_from_validator_codes([a, b]) is expected


def test_study_b_rules():
    b = {"mode": "B"}
    assert outcome_from_validator_codes([], **b) is SlotOutcome.VALID
    assert outcome_from_validator_codes(["E_SEPARATION"], **b) is SlotOutcome.INCOMPATIBLE
    assert outcome_from_validator_codes(["E_DUPLICATE", "E_SEPARATION"], **b) is (
        SlotOutcome.INCOMPATIBLE
    )
    assert outcome_from_validator_codes(["E_SEPARATION", "E_RESERVED"], **b) is (
        SlotOutcome.RESERVED_COLLISION
    )
    assert outcome_from_validator_codes([], cell_duplicate=True, **b) is SlotOutcome.DUPLICATE
    assert outcome_from_validator_codes(["E_SEPARATION"], cell_duplicate=True, **b) is (
        SlotOutcome.DUPLICATE
    )
    assert outcome_from_validator_codes(["E_CLIP"], cell_duplicate=True, **b) is (
        SlotOutcome.CLIPPING
    )
    assert {"E_DUPLICATE", "E_SEPARATION"} == B_RELATIONAL_CODES


def test_bad_inputs():
    with pytest.raises(OutcomeError):
        outcome_from_validator_codes(["E_NOPE"])
    with pytest.raises(OutcomeError):
        outcome_from_validator_codes([], cell_duplicate=True)
    with pytest.raises(OutcomeError):
        outcome_from_validator_codes([], mode="C")


def test_llm_status_mapping():
    assert outcome_from_llm_status("ok") is None
    assert outcome_from_llm_status(LlmStatus.TIMEOUT) is SlotOutcome.TIMEOUT
    assert outcome_from_llm_status("overflow_output") is SlotOutcome.OVERFLOW_OUTPUT
    assert outcome_from_llm_status("server_error") is SlotOutcome.INVALID_JSON
    with pytest.raises(ValueError):
        outcome_from_llm_status("cancelled")


def _ref(recipe: Recipe, ref_id: str = "K-a1") -> Reference:
    return Reference.from_rendered(ref_id, render(recipe, Profile.P1))


def test_real_validator_results():
    base = synthetic_recipes(Profile.P1)["K-a1"]
    assert outcome_from_validation(validate(base, "P1")) is SlotOutcome.VALID
    assert is_technically_valid(SlotOutcome.VALID)
    assert not is_technically_valid("duplicate")

    dup = validate(base, "P1", [_ref(base)])
    assert set(dup.codes) == {"E_DUPLICATE", "E_SEPARATION"}
    assert outcome_from_validation(dup) is SlotOutcome.DUPLICATE
    assert outcome_from_validation(dup, mode="B") is SlotOutcome.INCOMPATIBLE

    p0 = base.pitches[0]
    near = dict(base.to_dict(), pitches=[p0 + 1 if p0 < 6 else p0 - 1, *base.pitches[1:]])
    close = validate(near, "P1", [_ref(base)])
    assert close.codes == ("E_SEPARATION",)
    assert outcome_from_validation(close) is SlotOutcome.SEPARATION_FAIL

    short = {
        "total_ms": 450,
        "pitches": [0, 0, 0],
        "rhythm_weights": [4, 1, 1],
        "gaps_ms": [60, 60],
        "amplitudes": [1.0, 1.0, 1.0],
    }
    assert outcome_from_validation(validate(short, "P1")) is SlotOutcome.EVENT_TOO_SHORT

    domain = dict(base.to_dict(), pitches=[7, 0, 0])
    assert outcome_from_validation(validate(domain, "P1")) is SlotOutcome.OUT_OF_DOMAIN
    schema = {k: v for k, v in base.to_dict().items() if k != "gaps_ms"}
    assert outcome_from_validation(validate(schema, "P1")) is SlotOutcome.SCHEMA_VIOLATION
    assert outcome_from_validation(validate("not json", "P1")) is SlotOutcome.INVALID_JSON
