"""Strict model-output parser (#17). INTERFACE ONLY in the skeleton.

Accepts exactly one JSON object (strict JSON: `av_sound.recipe.strict_json_loads`) and
nothing else; any other text gives `invalid_json`. It never repairs output. The parsed
object goes to `av_sound.validate` unchanged (which reports `E_SCHEMA` / `E_DOMAIN`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ParsedOutput:
    """`obj` is the decoded JSON object, or `None` when the text was not exactly one."""

    obj: Mapping[str, Any] | None
    error: str | None


def parse_output(text: str | None) -> ParsedOutput:
    """Parse raw model text (#17)."""
    raise NotImplementedError("#17: strict parser")
