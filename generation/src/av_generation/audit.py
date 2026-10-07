"""Generation audit reports (#24). INTERFACE ONLY in the skeleton; the table columns, the
set-table file contract and the summary schema (`audit-summary.schema.json`) are the
contract with the analysis pipeline (#34).

Per run (`rundir.RunLayout`): `audit/unmasked/books.csv` (`BOOK_COLUMNS`),
`audit/unmasked/summary.json` (+ a Markdown or HTML batch summary) and the same under
`audit/masked/` with `MASKED_BOOK_COLUMNS`. Reports are built from the logs alone, are
byte-identical for the same logs, and never read learner data. Every number traces back
to slot IDs.

Per set (`build_set_audit`): one CSV over every complete batch of a study set, one row
per book, sorted by `batch_id` then `book_id`:

- masked: `{study}-{set}-audit.csv` with `MASKED_BOOK_COLUMNS`; the analysis pipeline
  reads it at `inputs/generation/{study}-{set}-audit.csv` of its data root (blinded area);
- unmasked: `{study}-{set}-audit-unmasked.csv` with `BOOK_COLUMNS`; restricted, for the
  generation operator and, after unmasking, #34's method-level effort and outcome
  endpoints (proposed analysis path `keys/generation/{study}-{set}-audit-unmasked.csv`).

Run selection: a run is complete when its manifest is closed (`closed_utc`) and its
timing log has no `batch_incomplete` event. Incomplete runs (a rater withdrew; the batch
was rebuilt with a new panel and seed namespace) are left out and listed in
`SetAuditResult.excluded_runs`; their books are void in the store. Each batch must have
exactly one complete run, else `build_set_audit` raises.

Masking: the masked table has no column that names a method or reveals it by
construction. Besides the labels and per-method effort, that drops every outcome count
(`n_<code>`: overflow codes exist only for A3, A2 never produces `invalid_json`, ...),
`startup_ms` (model load) and `operator_ms`; the masked table keeps only the
method-neutral totals (`slots_valid`, `slots_invalid`). Outcome counts stay in the
unmasked table, with `n_llm_server_error` separating infrastructure failures (model
`server_error`, counted inside `n_invalid_json`) from model output.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
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
    "n_llm_server_error",
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
with 6 decimals; empty cells mean "not applicable" (e.g. tokens for A1/A2). Counts of a
substituted book cover all its 192 slots; `atoms_*` count the atoms of its final store
book (16 `atoms_book_fallback`)."""

METHOD_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "method",
        "designer_id",
        *OUTCOME_COLUMNS,
        "n_llm_server_error",
        "startup_ms",
        "operator_ms",
        "design_active_ms",
        "familiarization_ms",
        "model_runtime_ms",
        "tokens_in",
        "tokens_out",
    }
)
"""Columns dropped from the masked tables: they name the method or reveal it by
construction (see the module docstring)."""

MASKED_BOOK_COLUMNS: Final[tuple[str, ...]] = tuple(
    c for c in BOOK_COLUMNS if c not in METHOD_COLUMNS
)

SET_AUDIT_NAME: Final = "{study}-{set}-audit.csv"
SET_AUDIT_UNMASKED_NAME: Final = "{study}-{set}-audit-unmasked.csv"


@dataclass(frozen=True, slots=True)
class AuditResult:
    """Paths and SHA-256 of the files one audit wrote (#24)."""

    files: dict[str, str]


@dataclass(frozen=True, slots=True)
class SetAuditResult:
    """The set table written by `build_set_audit` (#24)."""

    path: str
    sha256: str
    runs: tuple[str, ...]
    """Run IDs included (one complete run per batch)."""
    excluded_runs: tuple[str, ...]
    """Incomplete runs left out."""


def build_audit(run_dir: str | os.PathLike[str], out_dir: str | os.PathLike[str]) -> AuditResult:
    """Build masked and unmasked reports for one run from its logs (#24)."""
    raise NotImplementedError("#24: audit reports")


def build_set_audit(
    run_dirs: Sequence[str | os.PathLike[str]],
    out_dir: str | os.PathLike[str],
    *,
    study: str,
    set_name: str,
    masked: bool,
) -> SetAuditResult:
    """Write the set table (`SET_AUDIT_NAME` or `SET_AUDIT_UNMASKED_NAME`) for every
    complete batch among `run_dirs` (#24)."""
    raise NotImplementedError("#24: set audit table")
