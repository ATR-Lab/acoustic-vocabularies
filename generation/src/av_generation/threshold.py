"""Separation-threshold listening tool (#23). INTERFACE ONLY in the skeleton.

Pairs of valid atomic motifs per profile at controlled 12-feature distances (bins of
`DEFAULT_CONFIG`), plus identical-pair catch trials; a same/different trial runner on the
rater-station web stack; CSV export and a summary (proportion `same` per bin and profile
with Wilson 95% intervals and a logistic fit). Records: `records.ThresholdStimulusSet`
(the stimulus set), `records.ThresholdSession` (one per listener session, written before
the first trial under `RunLayout.threshold_dir / "sessions"`: set hash, listener,
station, fixed gain, order seed, A/B rule, planned trial order),
`records.ThresholdTrial`, `records.PlayEvent` (`threshold_first`/`threshold_second`).
Seeds: `seeds.threshold_seed_key(set_id, purpose, ...)`; the trial order uses
`threshold_seed_key(set_id, "order", session_id)`. Pairs never come from a study book
and are never complete messages.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any, Final

from av_generation.records import (
    ThresholdConfig,
    ThresholdSession,
    ThresholdStimulusSet,
    ThresholdTrial,
)

DEFAULT_CONFIG: Final = ThresholdConfig(
    profiles=("P1", "P2", "P3"),
    bin_centers=("0.050", "0.075", "0.100", "0.125", "0.150", "0.175", "0.200"),
    bin_halfwidth="0.0125",
    pairs_per_bin=8,
    same_pairs=56,
    gap_ms=500,
    threshold_default="0.10",
)
"""Proposed default session: 3 profiles x 7 bins x 8 pairs + 56 same pairs = 224 trials."""

TRIAL_CSV_COLUMNS: Final[tuple[str, ...]] = (
    "session_id",
    "listener_id",
    "trial_index",
    "pair_id",
    "profile",
    "kind",
    "bin_center",
    "distance",
    "order",
    "gap_ms",
    "recipe_first",
    "recipe_second",
    "pcm_sha256_first",
    "pcm_sha256_second",
    "response",
    "rt_ms",
    "tryout",
)
"""One row per trial (contract to O6.2.2); recipes as canonical compact JSON."""

SUMMARY_CSV_COLUMNS: Final[tuple[str, ...]] = (
    "profile",
    "bin_center",
    "n_trials",
    "n_same",
    "p_same",
    "wilson_low",
    "wilson_high",
)


def generate_stimuli(set_id: str, config: ThresholdConfig = DEFAULT_CONFIG) -> ThresholdStimulusSet:
    """The stimulus set for `set_id` (same ID and config -> identical set and hash) (#23)."""
    raise NotImplementedError("#23: stimulus generation")


def plan_session(
    stimuli: ThresholdStimulusSet,
    session_id: str,
    *,
    listener_id: str,
    station: str,
    gain_db: float,
    tryout: bool,
    created_utc: str,
) -> ThresholdSession:
    """The session document with its seeded trial order and counterbalanced A/B order (#23)."""
    raise NotImplementedError("#23: session plan")


def summarize(trials: Sequence[ThresholdTrial]) -> list[dict[str, Any]]:
    """Summary rows (`SUMMARY_CSV_COLUMNS`) with Wilson 95% intervals (#23)."""
    raise NotImplementedError("#23: summary")


def export_csv(
    trials: Sequence[ThresholdTrial], stimuli: ThresholdStimulusSet, path: str | os.PathLike[str]
) -> str:
    """Write the trial CSV (`TRIAL_CSV_COLUMNS`); returns its SHA-256 (#23)."""
    raise NotImplementedError("#23: CSV export")
