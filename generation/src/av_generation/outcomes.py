"""Slot outcome codes and their mapping from model statuses and validator reason codes (#17).

Every slot ends with exactly one of the 14 outcome codes below, and every code consumes
the slot (Study A protocol §3.3; Study B protocol §4). The ledger stores the outcome and,
when the validator ran, all its reason codes (`validator_codes`), so no information is
lost by choosing one outcome.

Order of decision for one slot:

1. Before any call: `overflow_input` (prompt above 16,384 tokens; no model call).
2. Model status (A3 and B): `timeout`, `overflow_output`; `server_error` gives
   `invalid_json` because no JSON object came back (the parser rule: anything other than
   exactly one JSON object is `invalid_json`; `llm_status` keeps `server_error`).
   A1: no submission before the 40-s cap gives `timeout`.
3. Parser (#17): not exactly one JSON object gives `invalid_json`.
4. Validator (`av_sound.validate`): the failing code that comes first in
   `VALIDATOR_PRECEDENCE` decides. The precedence is the validator's own fixed order
   (`av_sound.validate.REASON_CODES`, i.e. `ValidationResult.primary_code`): structure
   (`E_JSON`, `E_SCHEMA`, `E_DOMAIN`), then the waveform (`E_EVENT_SHORT`, `E_NONFINITE`,
   `E_CLIP`), then relations to other sounds (`E_DUPLICATE`, `E_RESERVED`,
   `E_SEPARATION`).
5. Study B only (`mode="B"`): the validator runs against the retained options of the
   *other* atoms of the profile, so their `E_DUPLICATE` and `E_SEPARATION` mean
   `incompatible`. A waveform equal to a retained option of the *same* cell is
   `duplicate`. Precedence in B: technical codes (all codes except `E_DUPLICATE` and
   `E_SEPARATION`, in validator order), then same-cell `duplicate`, then `incompatible`.
6. Otherwise `valid`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Literal

from av_sound.recipe import E_DOMAIN, E_JSON, E_SCHEMA
from av_sound.validate import (
    E_CLIP,
    E_DUPLICATE,
    E_EVENT_SHORT,
    E_NONFINITE,
    E_RESERVED,
    E_SEPARATION,
    REASON_CODES,
    ValidationResult,
)


class SlotOutcome(StrEnum):
    """The 14 slot outcome codes (#17). Every one consumes the slot."""

    VALID = "valid"
    INVALID_JSON = "invalid_json"
    SCHEMA_VIOLATION = "schema_violation"
    OUT_OF_DOMAIN = "out_of_domain"
    RENDER_FAIL = "render_fail"
    EVENT_TOO_SHORT = "event_too_short"
    CLIPPING = "clipping"
    DUPLICATE = "duplicate"
    RESERVED_COLLISION = "reserved_collision"
    SEPARATION_FAIL = "separation_fail"
    INCOMPATIBLE = "incompatible"
    OVERFLOW_INPUT = "overflow_input"
    OVERFLOW_OUTPUT = "overflow_output"
    TIMEOUT = "timeout"


OUTCOME_CODES: Final[tuple[str, ...]] = tuple(o.value for o in SlotOutcome)
"""All outcome codes in the order of #17 (the column order of audit tables)."""

B_ONLY_OUTCOMES: Final[frozenset[SlotOutcome]] = frozenset({SlotOutcome.INCOMPATIBLE})
LLM_ONLY_OUTCOMES: Final[frozenset[SlotOutcome]] = frozenset(
    {SlotOutcome.OVERFLOW_INPUT, SlotOutcome.OVERFLOW_OUTPUT}
)


class LlmStatus(StrEnum):
    """Status of one model call (#16 `RawOutcome.status`)."""

    OK = "ok"
    TIMEOUT = "timeout"
    OVERFLOW_OUTPUT = "overflow_output"
    SERVER_ERROR = "server_error"


VALIDATOR_PRECEDENCE: Final[tuple[str, ...]] = REASON_CODES
"""Validator reason codes from highest to lowest precedence (the validator's own order)."""

VALIDATOR_CODE_TO_OUTCOME: Final[Mapping[str, SlotOutcome]] = MappingProxyType(
    {
        E_JSON: SlotOutcome.INVALID_JSON,
        E_SCHEMA: SlotOutcome.SCHEMA_VIOLATION,
        E_DOMAIN: SlotOutcome.OUT_OF_DOMAIN,
        E_EVENT_SHORT: SlotOutcome.EVENT_TOO_SHORT,
        E_NONFINITE: SlotOutcome.RENDER_FAIL,
        E_CLIP: SlotOutcome.CLIPPING,
        E_DUPLICATE: SlotOutcome.DUPLICATE,
        E_RESERVED: SlotOutcome.RESERVED_COLLISION,
        E_SEPARATION: SlotOutcome.SEPARATION_FAIL,
    }
)
"""Validator reason code -> outcome in Study A (and for B technical codes)."""

B_RELATIONAL_CODES: Final[frozenset[str]] = frozenset({E_DUPLICATE, E_SEPARATION})
"""In Study B these codes, raised against other atoms' retained options, mean `incompatible`."""

LLM_STATUS_TO_OUTCOME: Final[Mapping[LlmStatus, SlotOutcome | None]] = MappingProxyType(
    {
        LlmStatus.OK: None,
        LlmStatus.TIMEOUT: SlotOutcome.TIMEOUT,
        LlmStatus.OVERFLOW_OUTPUT: SlotOutcome.OVERFLOW_OUTPUT,
        LlmStatus.SERVER_ERROR: SlotOutcome.INVALID_JSON,
    }
)
"""Model status -> outcome; `None` means: continue with the parser and the validator."""


class OutcomeError(ValueError):
    """Unknown validator code or inconsistent outcome inputs."""


def _ordered(codes: Iterable[str]) -> list[str]:
    seen = set(codes)
    unknown = sorted(seen - set(VALIDATOR_PRECEDENCE))
    if unknown:
        raise OutcomeError(f"unknown validator reason codes {unknown}")
    return [c for c in VALIDATOR_PRECEDENCE if c in seen]


def outcome_from_validator_codes(
    codes: Iterable[str],
    *,
    mode: Literal["A", "B"] = "A",
    cell_duplicate: bool = False,
) -> SlotOutcome:
    """The outcome for a validated candidate with failing reason `codes` (any order).

    `mode="B"`: the codes come from validating against the other atoms' retained options of
    the profile; `cell_duplicate` says the waveform equals a retained option of the same
    cell. `cell_duplicate` is only meaningful in B.
    """
    ordered = _ordered(codes)
    if mode == "A":
        if cell_duplicate:
            raise OutcomeError("cell_duplicate is a Study B rule")
        return VALIDATOR_CODE_TO_OUTCOME[ordered[0]] if ordered else SlotOutcome.VALID
    if mode != "B":
        raise OutcomeError(f"mode must be 'A' or 'B', got {mode!r}")
    technical = [c for c in ordered if c not in B_RELATIONAL_CODES]
    if technical:
        return VALIDATOR_CODE_TO_OUTCOME[technical[0]]
    if cell_duplicate:
        return SlotOutcome.DUPLICATE
    if ordered:
        return SlotOutcome.INCOMPATIBLE
    return SlotOutcome.VALID


def outcome_from_validation(
    result: ValidationResult,
    *,
    mode: Literal["A", "B"] = "A",
    cell_duplicate: bool = False,
) -> SlotOutcome:
    """`outcome_from_validator_codes(result.codes, ...)`."""
    return outcome_from_validator_codes(result.codes, mode=mode, cell_duplicate=cell_duplicate)


def outcome_from_llm_status(status: LlmStatus | str) -> SlotOutcome | None:
    """The outcome a model status forces, or `None` when the text must be parsed (`ok`)."""
    return LLM_STATUS_TO_OUTCOME[LlmStatus(status)]


def is_technically_valid(outcome: SlotOutcome | str) -> bool:
    """True only for `valid`: the candidate may be rated (A) or retained (B)."""
    return SlotOutcome(outcome) is SlotOutcome.VALID
