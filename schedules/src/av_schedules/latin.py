"""Seeded Latin squares of order 4 used for counterbalancing.

Units are laid out on a 4x4 grid per *cycle*: grid row = block of 4 units within the
cycle (4 blocks per cycle), grid column = design cell (Study A: profile column; Study B:
the SQ arm x swap cell). A Latin square assigns one of 4 levels to every grid cell so
that each level occurs once in every row and once in every column.

:class:`CycleSquares` holds the complete set of three mutually orthogonal Latin squares
over GF(4) (elements 0..3, addition XOR), with seeded row order ``rho`` and column order
``gamma``::

    L_k[r][c] = k*rho[r] ^ gamma[c]        for k = 1, 2, 3

Any two of them take each of the 16 level pairs once per cycle, and ``L_j ^ L_k``
(j != k) depends on the row only. ``L_1`` and ``L_3`` shift the semantic labels (and the
Study A atom-order tracks); ``L_2`` is read as two bits (``split``), each constant on the
cosets of a 2-element subgroup ``{0, h}``, for family-first and role-first. Square ``A``
(``sym[L_1]``) gives the Study B wave-1 order bits.

The draws are constrained so that (i) for every level of ``A``, the two units in paired
columns (0,1 | 2,3 and 0,2 | 1,3) get different first bits, and (ii) every half cycle
(rows 0-1 or rows 2-3) is balanced too: in each column both first-bit values occur, in
each column pair all four values of ``L_2`` occur, and every level of ``A`` occurs once
with each first-bit value; (iii) columns 1 and 3 form a coset of ``{0, 3}``. The
constraints are ``gamma1 ^ gamma3 = 3``, ``h1 = 3*(gamma0 ^ gamma3)`` and
``rho0 ^ rho1 = h1`` (GF(4) arithmetic); see ``schedules/docs/curriculum.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .seeds import SeedStream

ORDER: Final = 4

# GF(4) multiplication with elements 0, 1, x=2, x+1=3 (modulus x^2 + x + 1).
_GF4_MUL: Final = ((0, 0, 0, 0), (0, 1, 2, 3), (0, 2, 3, 1), (0, 3, 1, 2))


def gf4_mul(a: int, b: int) -> int:
    return _GF4_MUL[a][b]


@dataclass(frozen=True)
class CycleSquares:
    """Three mutually orthogonal Latin squares for one cycle (see module docstring)."""

    rows: tuple[int, ...]  # rho
    cols: tuple[int, ...]  # gamma
    symbols: tuple[int, ...]  # sym, relabels square A = sym[L_1]
    h: tuple[int, int]  # subgroup generators of the two bits of L_2
    flip: tuple[int, int]  # seeded inversion of each bit

    @classmethod
    def draw(cls, s: SeedStream) -> CycleSquares:
        """Draw order: gamma (redrawn until gamma1 ^ gamma3 = 3); rho (redrawn until
        rho0 ^ rho1 = h1); sym; h2; flips."""
        while True:
            # Columns 1 and 3 (the swapped Study B cells) form a coset of {0, 3}, so the
            # two units holding a semantic message in the two H-W1 (or H-W4) cells of a
            # cycle always differ in swap.
            cols = s.permutation(range(ORDER))
            if cols[1] ^ cols[3] == 3:
                break
        # The first bit is constant on cosets of {0, h1}; choosing h1 = 3*(gamma0 ^ gamma3)
        # makes the column pairs (0,1)/(2,3) and (0,2)/(1,3) split every A level.
        h1 = gf4_mul(3, cols[0] ^ cols[3])
        while True:
            rows = s.permutation(range(ORDER))
            if rows[0] ^ rows[1] == h1:
                break
        symbols = s.permutation(range(ORDER))
        others = [g for g in (1, 2, 3) if g != h1]
        h2 = others[s.randbelow(2)]
        flip = (s.randbelow(2), s.randbelow(2))
        return cls(rows=rows, cols=cols, symbols=symbols, h=(h1, h2), flip=flip)

    def latin(self, k: int, row: int, col: int) -> int:
        """``L_k[row][col] = k*rho[row] ^ gamma[col]`` for k in 1..3."""
        if k not in (1, 2, 3):
            raise ValueError("k must be 1, 2 or 3")
        return gf4_mul(k, self.rows[row]) ^ self.cols[col]

    def a(self, row: int, col: int) -> int:
        """Square A = ``sym[L_1]``: level 0..3."""
        return self.symbols[self.latin(1, row, col)]

    def b(self, row: int, col: int) -> int:
        """Square ``L_2`` before the bit split: level 0..3."""
        return self.latin(2, row, col)

    def split(self, row: int, col: int) -> tuple[int, int]:
        """``L_2`` as two bits; the pair takes each of its 4 values once per row/column."""
        x = self.b(row, col)
        return (
            int(x not in (0, self.h[0])) ^ self.flip[0],
            int(x not in (0, self.h[1])) ^ self.flip[1],
        )
