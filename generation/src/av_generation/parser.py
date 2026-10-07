"""Strict model-output parser (#17; Study A protocol §3.6: "one JSON object", no repair).

Accepts exactly one JSON object and nothing else; any other text gives `invalid_json`:

- the text is strict JSON (`av_sound.recipe.strict_json_loads`: unique keys, no `NaN` or
  `Infinity`, no byte-order mark) whose top-level value is an object;
- JSON whitespace (space, tab, line feed, carriage return) may surround it, as in any
  JSON text; anything else (a second value, prose, a Markdown code fence, an array, a
  string, nothing at all) is refused.

It never repairs, trims or re-asks. The decoded object goes to `av_sound.validate`
unchanged, which reports `E_SCHEMA` / `E_DOMAIN` and the waveform checks. A refused text is
logged in the slot record as validator code `E_JSON` with the parser's message, so that
`outcomes.outcome_from_validator_codes(record.validator_codes) == record.outcome` holds for
every parsed slot.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from av_sound.recipe import StrictJsonError, strict_json_loads

MAX_ERROR_CHARS: Final = 400
"""Parser messages are clipped to the slot record's message limit."""


@dataclass(frozen=True, slots=True)
class ParsedOutput:
    """`obj` is the decoded JSON object, or `None` when the text was not exactly one."""

    obj: Mapping[str, Any] | None
    error: str | None

    @property
    def ok(self) -> bool:
        return self.obj is not None


def _refused(message: str) -> ParsedOutput:
    text = "output is not exactly one JSON object: " + message
    printable = "".join(c if " " <= c <= "~" else "?" for c in text)
    return ParsedOutput(None, printable[:MAX_ERROR_CHARS])


def parse_output(text: str | None) -> ParsedOutput:
    """Parse raw model text (#17); see the module docstring."""
    if text is None:
        return _refused("no text")
    if not isinstance(text, str):
        return _refused(f"expected text, got {type(text).__name__}")
    if not text.strip(" \t\n\r"):
        return _refused("empty text")
    try:
        value = strict_json_loads(text)
    except StrictJsonError as err:
        return _refused(str(err))
    if not isinstance(value, dict):
        return _refused(f"the JSON value is {type(value).__name__}, not an object")
    return ParsedOutput(value, None)
