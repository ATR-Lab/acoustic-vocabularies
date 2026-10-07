"""Synthetic (DEMO) books for tests, test vectors and examples. Not study material.

One book per profile, 16 atoms each, from a fixed systematic rule: no random
numbers, no generation method, no rating. The recipes are in domain, distinct,
free of short events, and the action and referent lengths of each family cover all
4 x 4 `total_ms` combinations, so the 32 messages span 1.1 to 2.0 s. They have not
been through the validator (#9) and are not meant to be learnable vocabularies.
"""

from __future__ import annotations

from typing import Final

from av_sound.composer import AtomAudio
from av_sound.grammar import ATOM_IDS, parse_atom_id
from av_sound.recipe import TOTAL_MS, Profile, Recipe
from av_sound.renderer import MIN_EVENT_SAMPLES, event_samples, render

SYNTHETIC_PREFIX: Final = "DEMO"

_PITCH_RULE: Final[tuple[tuple[int, int], ...]] = ((5, 0), (3, 6), (7, 11))
_RHYTHMS: Final[tuple[tuple[int, int, int], ...]] = (
    (1, 2, 3),
    (3, 2, 1),
    (2, 3, 1),
    (1, 3, 2),
    (2, 1, 3),
    (3, 1, 2),
    (1, 1, 2),
    (2, 1, 1),
    (1, 2, 1),
    (4, 3, 2),
    (2, 3, 4),
    (3, 4, 2),
)
_GAPS: Final[tuple[tuple[int, int], ...]] = (
    (20, 40),
    (40, 60),
    (60, 20),
    (40, 20),
    (60, 40),
    (20, 60),
    (20, 20),
    (40, 40),
    (60, 60),
)
_AMPLITUDES: Final[tuple[tuple[float, float, float], ...]] = (
    (1.0, 0.8, 0.6),
    (0.6, 0.8, 1.0),
    (0.8, 1.0, 0.6),
    (1.0, 0.6, 0.8),
    (0.6, 1.0, 0.8),
    (0.8, 0.6, 1.0),
)
_PROFILE_ORDER: Final[tuple[Profile, ...]] = (Profile.P1, Profile.P2, Profile.P3)


def synthetic_book_id(profile: Profile | str) -> str:
    """For example `DEMO-P1`."""
    return f"{SYNTHETIC_PREFIX}-{Profile(profile).value}"


def _rhythm(k: int, total_ms: int, gaps: tuple[int, int]) -> tuple[int, int, int]:
    """First rhythm, from position k in the list, whose three events all reach 60 ms."""
    for step in range(len(_RHYTHMS)):
        weights = _RHYTHMS[(k + step) % len(_RHYTHMS)]
        if min(event_samples(total_ms, weights, gaps)) >= MIN_EVENT_SAMPLES:
            return weights
    raise AssertionError(  # pragma: no cover
        "unreachable: (1, 1, 2) is admissible for every total_ms and gaps"
    )


def _recipe(profile_index: int, position: int) -> Recipe:
    """Atom `position` (0..15, canonical atom order) of the book for one profile."""
    k = 16 * profile_index + position
    matrix_index = parse_atom_id(ATOM_IDS[position]).index
    total_ms = TOTAL_MS[(matrix_index - 1 + profile_index) % len(TOTAL_MS)]
    p1, p2, p3 = (((k * m + o) % 13) - 6 for m, o in _PITCH_RULE)
    gaps = _GAPS[k % len(_GAPS)]
    rhythm = _rhythm(k, total_ms, gaps)
    return Recipe(total_ms, (p1, p2, p3), rhythm, gaps, _AMPLITUDES[k % len(_AMPLITUDES)])


def synthetic_recipes(profile: Profile | str) -> dict[str, Recipe]:
    """Atom ID -> recipe for the synthetic book of `profile`, in canonical atom order."""
    index = _PROFILE_ORDER.index(Profile(profile))
    return {atom: _recipe(index, position) for position, atom in enumerate(ATOM_IDS)}


def synthetic_book(profile: Profile | str) -> dict[str, AtomAudio]:
    """Atom ID -> rendered atom (with `book_id`) for the synthetic book of `profile`."""
    profile = Profile(profile)
    book_id = synthetic_book_id(profile)
    return {
        atom: AtomAudio.from_rendered(atom, render(recipe, profile), book_id=book_id)
        for atom, recipe in synthetic_recipes(profile).items()
    }
