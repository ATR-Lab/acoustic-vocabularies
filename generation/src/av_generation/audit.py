"""Generation audit reports (#24). INTERFACE ONLY in the skeleton; the table columns and
the summary schema (`audit-summary.schema.json`) are the contract with the analysis
pipeline (#34), which reads `books.csv` for the per-book fallback flags and effort.

Outputs per run (`rundir.RunLayout`): `audit/unmasked/books.csv`, `audit/unmasked/
summary.json` (+ a Markdown or HTML batch summary) and the same under `audit/masked/`
without `METHOD_COLUMNS`. Reports are built from the logs alone, are byte-identical for
the same logs, and never read learner data. Every number traces back to slot IDs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

from av_generation.outcomes import OUTCOME_CODES

OUTCOME_COLUMNS: Final[tuple[str, ...]] = tuple(f"n_{code}" for code in OUTCOME_CODES)

BOOK_COLUMNS: Final[tuple[str, ...]] = (
    "batch_id",
    "book_id",
    "method",
    "designer_id",
    "profile",
    "slots_total",
    "slots_valid",
    "slots_invalid",
    *OUTCOME_COLUMNS,
    "atoms_committed",
    "atoms_selector",
    "atoms_bank_fallback",
    "atoms_book_fallback",
    "failed_generation",
    "nonfallback",
    "wall_ms",
    "startup_ms",
    "operator_ms",
    "rater_ms",
    "design_active_ms",
    "familiarization_ms",
    "model_runtime_ms",
    "tokens_in",
    "tokens_out",
    "candidate_diversity",
    "committed_diversity",
    "total_ms_450",
    "total_ms_600",
    "total_ms_750",
    "total_ms_900",
    "message_ms_min",
    "message_ms_max",
    "message_duration_violations",
)
"""Columns of `books.csv` (unmasked), one row per book, in this order.

Booleans are `0`/`1`; diversity values are mean pairwise 12-feature distances printed
with 6 decimals; empty cells mean "not applicable" (e.g. tokens for A1/A2)."""

METHOD_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "method",
        "designer_id",
        "design_active_ms",
        "familiarization_ms",
        "model_runtime_ms",
        "tokens_in",
        "tokens_out",
    }
)
"""Columns dropped from the masked `books.csv` (they name or reveal the method)."""

MASKED_BOOK_COLUMNS: Final[tuple[str, ...]] = tuple(
    c for c in BOOK_COLUMNS if c not in METHOD_COLUMNS
)


@dataclass(frozen=True, slots=True)
class AuditResult:
    """Paths and SHA-256 of the files one audit wrote (#24)."""

    files: dict[str, str]


def build_audit(run_dir: str | os.PathLike[str], out_dir: str | os.PathLike[str]) -> AuditResult:
    """Build masked and unmasked reports for one run from its logs (#24)."""
    raise NotImplementedError("#24: audit reports")
