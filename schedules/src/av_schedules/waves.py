"""Study A recruitment waves for confirmatory sets other than 18 batches (#266).

The 18-batch factorial keeps its own construction (``assign._a_waves``). For N = 6m
batches dealt by ``design._deal_a_layout`` there are N/3 waves of 3 batches:

1. Profile x designer counts are q or q + 1 (q = (N/3) // 3), so they split into the
   three profile -> designer patterns of one 3 x 3 Latin square, pattern ``P`` used
   ``q`` or ``q + 1`` times (the extra uses are the cells with q + 1 batches; for 30
   batches 4, 3 and 3 waves). Each wave uses one pattern: one batch per profile, three
   different A1 designers.
2. Within a pattern, a seeded 0/1 fill sends the swapped and unswapped batches of each
   profile x designer cell to the pattern's waves so that every wave holds 1 or 2
   swapped batches (as far as N allows: with fewer than 18 batches some patterns have a
   single wave, whose swap levels are fixed by the batch table).
3. Waves are taken in rounds: round ``i`` holds the ``i``-th wave of every pattern used
   more than ``i`` times, in seeded order, so each round of 3 waves holds every profile x
   designer pair once. Batch order inside a wave follows a seeded cyclic Latin square per
   round, so each profile takes each wave position once per complete round.

Draws use the stream ``(A, A-<set code>, alloc:waves:n<N>)``; the 18-batch construction
keeps ``alloc:waves``.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from typing import Final

from .design import DESIGNERS, PROFILES, SET_CODE, ABatch, SetName
from .seeds import MasterSeed, SeedStream, stream

Pattern = tuple[str, ...]  # designer of each profile, in PROFILES order

_PATTERNS: Final[tuple[Pattern, ...]] = tuple(itertools.permutations(DESIGNERS))


def _parity(pattern: Pattern) -> int:
    """0 for an even permutation of DESIGNERS, 1 for an odd one (the two Latin squares)."""
    index = [DESIGNERS.index(d) for d in pattern]
    return sum(1 for a, b in itertools.combinations(index, 2) if a > b) % 2


def _fill(rows: Sequence[int], cols: Sequence[int]) -> list[list[bool]]:
    """A 0/1 matrix with the given row and column sums (first found, largest rows first)."""
    remaining = list(rows)
    grid = [[False] * len(cols) for _ in rows]

    def place(w: int) -> bool:
        if w == len(cols):
            return not any(remaining)
        if any(r > len(cols) - w for r in remaining):
            return False
        ranked = sorted(range(len(rows)), key=lambda j: -remaining[j])
        for chosen in itertools.combinations(ranked, cols[w]):
            if any(remaining[j] == 0 for j in chosen):
                continue
            for j in chosen:
                remaining[j] -= 1
                grid[j][w] = True
            if place(w + 1):
                return True
            for j in chosen:
                remaining[j] += 1
                grid[j][w] = False
        return False

    if not place(0):
        raise ValueError(f"no swap split with row sums {list(rows)} and column sums {list(cols)}")
    return grid


def _column_sums(swapped: int, waves: int) -> list[int]:
    """Swapped batches per wave, as close to 1-2 as ``swapped`` of ``3 * waves`` allow."""
    if swapped < waves:
        return [1] * swapped + [0] * (waves - swapped)
    if swapped > 2 * waves:
        return [3] * (swapped - 2 * waves) + [2] * (3 * waves - swapped)
    return [2] * (swapped - waves) + [1] * (2 * waves - swapped)


def _patterns(
    s: SeedStream, cells: dict[tuple[str, str], list[ABatch]], per_profile: int
) -> dict[Pattern, int]:
    """Uses of each pattern of one Latin square (see module docstring, step 1)."""
    q, r = divmod(per_profile, len(DESIGNERS))
    extra = {cell for cell, members in cells.items() if len(members) == q + 1}
    if any(len(m) not in (q, q + 1) for m in cells.values()) or any(
        sum(1 for d in DESIGNERS if (p, d) in extra) != r for p in PROFILES
    ):
        raise ValueError("Study A designer counts must differ by at most 1 within each profile")
    if r == 0:
        square, extras = s.randbelow(2), set()
    else:
        # r = 1: the cells with q + 1 batches form one pattern; r = 2: the other cells do.
        marked = [
            pt
            for pt in _PATTERNS
            if all(((p, d) in extra) == (r == 1) for p, d in zip(PROFILES, pt, strict=True))
        ]
        if len(marked) != 1:
            raise ValueError("Study A designer counts do not split into one Latin square")
        square = _parity(marked[0])
        same = {pt for pt in _PATTERNS if _parity(pt) == square}
        extras = {marked[0]} if r == 1 else same - {marked[0]}
    return {pt: q + (pt in extras) for pt in _PATTERNS if _parity(pt) == square}


def balanced_waves(
    master: MasterSeed, set_name: SetName, batches: Sequence[ABatch]
) -> tuple[tuple[ABatch, ...], ...]:
    """Recruitment waves of a confirmatory Study A set of 6m batches (module docstring)."""
    n = len(batches)
    per_profile = n // len(PROFILES)
    if n % (2 * len(PROFILES)) or any(
        sum(1 for b in batches if b.profile == p) != per_profile for p in PROFILES
    ):
        raise ValueError("confirmatory batches must split equally over the profiles")
    s = stream(master, "A", f"A-{SET_CODE[set_name]}", f"alloc:waves:n{n}")
    cells = {
        (p, d): [b for b in batches if (b.profile, b.designer) == (p, d)]
        for p in PROFILES
        for d in DESIGNERS
    }
    uses = _patterns(s, cells, per_profile)
    by_pattern: dict[Pattern, list[tuple[ABatch, ...]]] = {}
    for pattern, k in uses.items():
        pairs = list(zip(PROFILES, pattern, strict=True))
        swapped = [list(s.permutation([b for b in cells[c] if b.swap_w1_w4])) for c in pairs]
        plain = [list(s.permutation([b for b in cells[c] if not b.swap_w1_w4])) for c in pairs]
        columns = s.permutation(_column_sums(sum(len(x) for x in swapped), k))
        grid = _fill([len(x) for x in swapped], columns)
        by_pattern[pattern] = [
            tuple((swapped[j] if grid[j][w] else plain[j]).pop() for j in range(len(pairs)))
            for w in range(k)
        ]
    base = s.permutation(range(len(PROFILES)))  # profile positions within a wave
    out: list[tuple[ABatch, ...]] = []
    for i in range(max(uses.values())):
        members = s.permutation([pt for pt, k in uses.items() if k > i])
        shifts = s.permutation(range(len(PROFILES)))
        for t, pattern in enumerate(members):
            wave = by_pattern[pattern][i]
            order = [base[(x + shifts[t]) % len(PROFILES)] for x in range(len(PROFILES))]
            out.append(tuple(wave[j] for j in order))
    return tuple(out)
