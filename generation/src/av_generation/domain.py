"""The 12 recipe coordinates in protocol order, with their ordered allowed values.

Study A protocol §3.5 lists the coordinates A2 mutates: 3 pitches, `total_ms`, 3 rhythm
weights, 2 gaps and 3 amplitudes. This is also the order of the 12-feature vector of §3.2
(`av_sound.features.FEATURE_NAMES`). The allowed values come from `av_sound.recipe`, the
single definition shared with the renderer and the validator; nothing is redefined here.

A2 (#18) mutates pitches by a semitone step (`kind == "pitch"`) and every other coordinate
by one position in its ordered value list (`kind == "index"`). The threshold tool (#23)
uses the same coordinates to vary features of a pair.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import prod
from typing import Any, Final, Literal

from av_sound.features import FEATURE_NAMES
from av_sound.recipe import AMPLITUDES, FIELDS, GAPS_MS, PITCHES, RHYTHM_WEIGHTS, TOTAL_MS, Recipe

Value = int | float
"""A coordinate value: an integer, or an amplitude (0.6, 0.8 or 1.0)."""

FIELD_VALUES: Final[Mapping[str, tuple[Value, ...]]] = {
    "total_ms": TOTAL_MS,
    "pitches": PITCHES,
    "rhythm_weights": RHYTHM_WEIGHTS,
    "gaps_ms": GAPS_MS,
    "amplitudes": AMPLITUDES,
}
"""Recipe field -> ordered allowed values (ascending), from `av_sound.recipe`."""


@dataclass(frozen=True, slots=True)
class Coordinate:
    """One of the 12 recipe coordinates."""

    name: str
    """Stable name, e.g. `pitch_2`, `total_ms`, `rhythm_weight_1`, `gap_2`, `amplitude_3`."""
    field: str
    """Recipe field: `pitches`, `total_ms`, `rhythm_weights`, `gaps_ms` or `amplitudes`."""
    index: int | None
    """Position in the field's list, or `None` for the scalar `total_ms`."""
    values: tuple[Value, ...]
    """Ordered allowed values."""
    kind: Literal["pitch", "index"]
    """A2 mutation rule: semitone step with reflection, or one index up/down."""
    feature: str
    """The matching `av_sound.features.FEATURE_NAMES` entry."""

    def get(self, recipe: Recipe) -> Value:
        """The coordinate's value in `recipe`."""
        value: Value | tuple[Value, ...] = getattr(recipe, self.field)
        if isinstance(value, tuple):
            assert self.index is not None
            return value[self.index]
        assert self.index is None
        return value

    def position(self, value: Value) -> int:
        """Index of `value` in `values`; `ValueError` if not allowed."""
        for i, allowed in enumerate(self.values):
            if not isinstance(value, bool) and value == allowed:
                return i
        raise ValueError(f"{self.name}: {value!r} is not one of {list(self.values)}")


def _coords() -> tuple[Coordinate, ...]:
    out: list[Coordinate] = []
    feature = iter(FEATURE_NAMES)
    out += [
        Coordinate(f"pitch_{i + 1}", "pitches", i, PITCHES, "pitch", next(feature))
        for i in range(3)
    ]
    out.append(Coordinate("total_ms", "total_ms", None, TOTAL_MS, "index", next(feature)))
    out += [
        Coordinate(
            f"rhythm_weight_{i + 1}", "rhythm_weights", i, RHYTHM_WEIGHTS, "index", next(feature)
        )
        for i in range(3)
    ]
    out += [
        Coordinate(f"gap_{i + 1}", "gaps_ms", i, GAPS_MS, "index", next(feature)) for i in range(2)
    ]
    out += [
        Coordinate(f"amplitude_{i + 1}", "amplitudes", i, AMPLITUDES, "index", next(feature))
        for i in range(3)
    ]
    return tuple(out)


COORDINATES: Final[tuple[Coordinate, ...]] = _coords()
"""The 12 coordinates in protocol order (= feature order)."""
COORDINATE_NAMES: Final[tuple[str, ...]] = tuple(c.name for c in COORDINATES)
N_COORDINATES: Final = len(COORDINATES)
DOMAIN_SIZE: Final = prod(len(c.values) for c in COORDINATES)
"""Number of distinct recipes: 4 x 13^3 x 4^3 x 3^2 x 3^3 = 136,670,976."""

_BY_NAME: Final[Mapping[str, Coordinate]] = {c.name: c for c in COORDINATES}


def coordinate(name: str) -> Coordinate:
    """The coordinate called `name`; `KeyError` if unknown."""
    return _BY_NAME[name]


def recipe_values(recipe: Recipe) -> tuple[Value, ...]:
    """The 12 coordinate values of `recipe`, in protocol order."""
    return tuple(c.get(recipe) for c in COORDINATES)


def values_to_dict(values: Sequence[Value]) -> dict[str, Any]:
    """The recipe JSON object for 12 coordinate values (no domain check; see `Recipe`)."""
    if len(values) != N_COORDINATES:
        raise ValueError(f"expected {N_COORDINATES} values, got {len(values)}")
    lists: dict[str, list[Value]] = {f: [] for f in FIELDS if f != "total_ms"}
    total: Value | None = None
    for coord, value in zip(COORDINATES, values, strict=True):
        if coord.index is None:
            total = value
        else:
            lists[coord.field].append(value)
    return {"total_ms": total, **lists}


def values_to_recipe(values: Sequence[Value]) -> Recipe:
    """`Recipe.from_dict(values_to_dict(values))`; raises `RecipeError` out of domain."""
    return Recipe.from_dict(values_to_dict(values))


def replace_values(recipe: Recipe, changes: Mapping[str, Value]) -> Recipe:
    """`recipe` with the named coordinates set to new values (raises out of domain)."""
    unknown = sorted(set(changes) - set(_BY_NAME))
    if unknown:
        raise KeyError(f"unknown coordinates {unknown}")
    values = [changes.get(c.name, c.get(recipe)) for c in COORDINATES]
    return values_to_recipe(values)


def differing_coordinates(a: Recipe, b: Recipe) -> tuple[str, ...]:
    """Names of the coordinates whose values differ between `a` and `b`, in protocol order."""
    return tuple(c.name for c in COORDINATES if c.get(a) != c.get(b))
