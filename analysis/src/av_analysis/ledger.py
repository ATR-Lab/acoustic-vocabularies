"""Cumulative per-person exposure ledger (#33). Interface; #33 implements.

Folds the exposure ledgers of a person's reconciled visits, in visit order, into one row
per item (``derived.EXPOSURE_CUMULATIVE``): first audible (or uncertain) exposure, play
counts by phase, the scheduled novel visit of held-out messages and the C4 codes raised
for the item. The same fold gives each trial row its ``prior_phrase_exposures``,
``prior_atom_exposures`` and ``novelty`` (``derived.TRIALS``). Uncertain onset counts as
consumed; a retry never renews novelty.
"""

from __future__ import annotations

from collections.abc import Sequence

from .derived import Row
from .paths import DataRoot


def build_ledger(root: DataRoot, visit_ids: Sequence[str]) -> list[Row]:
    """Rows of ``exposure-cumulative`` for the given reconciled visits."""
    raise NotImplementedError("#33: cumulative exposure ledger")
