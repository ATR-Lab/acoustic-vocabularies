"""Seeded Latin squares of order 4 used for counterbalancing.

Units are laid out on a 4x4 grid per *cycle*: grid row = block of 4 units within the
cycle (4 blocks per cycle), grid column = design cell (Study A: two unswapped and two
swapped cells; Study B: the SQ arm x swap cells). A Latin square assigns one of 4 levels
to every grid cell so that each level occurs once in every row and once in every column.

* :class:`CyclicSquare` gives the semantic-permutation rotation of one label line:
  ``rotation = (col_shift[col] + row_shift[row]) mod 4`` (the rotation-to-column mapping
  shifts by block, with seeded shifts).
* :class:`CycleSquares` holds two orthogonal Latin squares over GF(4) (elements 0..3,
  addition XOR): ``A[r][c] = sym[rho[r] ^ gamma[c]]`` and ``B[r][c] = 2*rho[r] ^ gamma[c]``.
  Together they take each of the 16 level pairs once per cycle. ``B`` is read as two
  bits (``split``), each constant on the cosets of a 2-element subgroup ``{0, h}``. The
  draws are constrained so that (i) for every level of ``A``, the two units in paired
  columns (0,1 | 2,3 and 0,2 | 1,3) get different first bits, and (ii) every half cycle
  (rows 0-1 or rows 2-3) is balanced too: in each column both first-bit values occur,
  in each column pair all four values of ``B`` occur, and every level of ``A`` occurs
  once with each first-bit value. The constraints are ``h1 = 3*(gamma0 ^ gamma3)`` and
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
class CyclicSquare:
    """``value(row, col) = (col_shift[col] + row_shift[row]) mod 4``."""

    row_shift: tuple[int, ...]
    col_shift: tuple[int, ...]

    @classmethod
    def draw(cls, s: SeedStream) -> CyclicSquare:
        rows = s.permutation(range(ORDER))
        cols = s.permutation(range(ORDER))
        return cls(row_shift=rows, col_shift=cols)

    def value(self, row: int, col: int) -> int:
        return (self.col_shift[col] + self.row_shift[row]) % ORDER


@dataclass(frozen=True)
class CycleSquares:
    """Two orthogonal Latin squares for one cycle (see module docstring)."""

    rows: tuple[int, ...]  # rho
    cols: tuple[int, ...]  # gamma
    symbols: tuple[int, ...]  # sym, relabels square A
    h: tuple[int, int]  # subgroup generators of the two bits of square B
    flip: tuple[int, int]  # seeded inversion of each bit

    @classmethod
    def draw(cls, s: SeedStream) -> CycleSquares:
        """Draw order: gamma; rho (redrawn until rho0 ^ rho1 = h1); sym; h2; flips."""
        cols = s.permutation(range(ORDER))
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

    def a(self, row: int, col: int) -> int:
        """Square A: level 0..3."""
        return self.symbols[self.rows[row] ^ self.cols[col]]

    def b(self, row: int, col: int) -> int:
        """Square B before the bit split: level 0..3."""
        return gf4_mul(2, self.rows[row]) ^ self.cols[col]

    def split(self, row: int, col: int) -> tuple[int, int]:
        """Square B as two bits; the pair takes each of its 4 values once per row/column."""
        x = self.b(row, col)
        return (
            int(x not in (0, self.h[0])) ^ self.flip[0],
            int(x not in (0, self.h[1])) ^ self.flip[1],
        )
