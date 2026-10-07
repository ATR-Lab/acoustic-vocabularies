"""Recipe and profile types (renderer spec section 4).

A `Recipe` always holds an in-domain value: the constructor rejects wrong types
(`E_SCHEMA`) and out-of-domain values (`E_DOMAIN`), and `Recipe.from_json` rejects
text that is not strict JSON (`E_JSON`). It never repairs a value.
"""

from __future__ import annotations

import hashlib
import json
import numbers
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

TOTAL_MS: tuple[int, ...] = (450, 600, 750, 900)
PITCHES: tuple[int, ...] = tuple(range(-6, 7))
RHYTHM_WEIGHTS: tuple[int, ...] = (1, 2, 3, 4)
GAPS_MS: tuple[int, ...] = (20, 40, 60)
AMPLITUDES: tuple[float, ...] = (0.6, 0.8, 1.0)
FIELDS: tuple[str, ...] = ("total_ms", "pitches", "rhythm_weights", "gaps_ms", "amplitudes")

E_JSON = "E_JSON"
E_SCHEMA = "E_SCHEMA"
E_DOMAIN = "E_DOMAIN"


class Profile(StrEnum):
    """Frozen render profiles. No other base frequency is accepted."""

    P1 = "P1"
    P2 = "P2"
    P3 = "P3"

    @property
    def f0_hz(self) -> int:
        """Base frequency in Hz (300, 450 or 675)."""
        return _F0_HZ[self]


_F0_HZ: dict[Profile, int] = {Profile.P1: 300, Profile.P2: 450, Profile.P3: 675}


class RecipeError(ValueError):
    """A recipe is not strict JSON (`code == "E_JSON"`), malformed (`"E_SCHEMA"`) or out of
    domain (`"E_DOMAIN"`)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _is_int(value: object) -> bool:
    return isinstance(value, numbers.Integral) and not isinstance(value, bool)


def _int_field(name: str, value: object, allowed: tuple[int, ...]) -> int:
    if not _is_int(value):
        raise RecipeError(E_SCHEMA, f"{name}: expected an integer, got {value!r}")
    assert isinstance(value, numbers.Integral)
    number = int(value)
    if number not in allowed:
        raise RecipeError(E_DOMAIN, f"{name}: {number} is not one of {list(allowed)}")
    return number


def _int_list(name: str, value: object, length: int, allowed: tuple[int, ...]) -> tuple[int, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise RecipeError(E_SCHEMA, f"{name}: expected a list of {length} integers")
    if len(value) != length:
        raise RecipeError(E_SCHEMA, f"{name}: expected {length} values, got {len(value)}")
    return tuple(_int_field(f"{name}[{i}]", v, allowed) for i, v in enumerate(value))


def _amplitude(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise RecipeError(E_SCHEMA, f"{name}: expected a number, got {value!r}")
    for allowed in AMPLITUDES:
        if value == allowed:
            return allowed
    raise RecipeError(E_DOMAIN, f"{name}: {value} is not one of {list(AMPLITUDES)}")


@dataclass(frozen=True, slots=True)
class Recipe:
    """One 3-event motif recipe. The profile is not part of the recipe."""

    total_ms: int
    pitches: tuple[int, int, int]
    rhythm_weights: tuple[int, int, int]
    gaps_ms: tuple[int, int]
    amplitudes: tuple[float, float, float]

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        set_(self, "total_ms", _int_field("total_ms", self.total_ms, TOTAL_MS))
        set_(self, "pitches", _int_list("pitches", self.pitches, 3, PITCHES))
        set_(
            self,
            "rhythm_weights",
            _int_list("rhythm_weights", self.rhythm_weights, 3, RHYTHM_WEIGHTS),
        )
        set_(self, "gaps_ms", _int_list("gaps_ms", self.gaps_ms, 2, GAPS_MS))
        amps = self.amplitudes
        if not isinstance(amps, Sequence) or isinstance(amps, str | bytes) or len(amps) != 3:
            raise RecipeError(E_SCHEMA, "amplitudes: expected a list of 3 numbers")
        set_(
            self, "amplitudes", tuple(_amplitude(f"amplitudes[{i}]", a) for i, a in enumerate(amps))
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Recipe:
        """Build a recipe from a decoded JSON object with exactly the five fields."""
        if not isinstance(data, Mapping):
            raise RecipeError(E_SCHEMA, "recipe: expected a JSON object")
        missing = [f for f in FIELDS if f not in data]
        extra = sorted(str(k) for k in data if k not in FIELDS)
        if missing or extra:
            raise RecipeError(E_SCHEMA, f"recipe: missing fields {missing}, extra fields {extra}")
        return cls(**{f: data[f] for f in FIELDS})

    @classmethod
    def from_json(cls, text: str | bytes | bytearray) -> Recipe:
        """Parse a strict JSON recipe (e.g. model output); see `strict_json_loads`.

        Raises `RecipeError` with `E_JSON` when the text is not strict JSON, then as
        `from_dict`.
        """
        try:
            data = strict_json_loads(text)
        except StrictJsonError as err:
            raise RecipeError(E_JSON, f"recipe: not strict JSON ({err})") from err
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON-compatible dict (lists, not tuples)."""
        return {
            "total_ms": self.total_ms,
            "pitches": list(self.pitches),
            "rhythm_weights": list(self.rhythm_weights),
            "gaps_ms": list(self.gaps_ms),
            "amplitudes": list(self.amplitudes),
        }

    def canonical_json(self) -> str:
        """Compact JSON with sorted keys; amplitudes as 0.6, 0.8 or 1.0 (spec section 4)."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    def sha256(self) -> str:
        """SHA-256 of the canonical JSON (UTF-8), lowercase hex."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


class StrictJsonError(ValueError):
    """Text is not strict JSON (`E_JSON`)."""


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise StrictJsonError(f"duplicate key {key!r}")
        out[key] = value
    return out


def _no_constant(name: str) -> Any:  # noqa: ANN401 - json hook signature
    raise StrictJsonError(f"non-standard constant {name}")


def strict_json_loads(text: str | bytes | bytearray) -> Any:  # noqa: ANN401 - any JSON value
    """Decode strict JSON: bytes must be UTF-8 without a byte-order mark, keys unique,
    and `NaN`/`Infinity` are rejected. Every failure, including nesting depth and
    integer-size limits, raises `StrictJsonError`.
    """
    try:
        if isinstance(text, bytes | bytearray):
            text = bytes(text).decode("utf-8")
        return json.loads(text, object_pairs_hook=_unique_keys, parse_constant=_no_constant)
    except StrictJsonError:
        raise
    except (ValueError, RecursionError) as err:  # JSONDecodeError, UnicodeDecodeError, limits
        raise StrictJsonError(str(err)) from err
