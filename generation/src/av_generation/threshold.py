"""Separation-threshold listening tool (#23): stimuli, session plans, checks, CSV and summary.

The tool gives O6.2.2 data to keep or revise the pilot separation threshold (Study A
protocol §3.2: normalized 12-feature distance `sqrt(sum((x_j - y_j)^2) / 12)`, pilot
default 0.10). It reports data only; it never chooses the threshold.

- Stimuli (`generate_stimuli`): per profile and distance bin (`ThresholdConfig`), pairs
  of atomic motifs whose exact distance lies in `[center - halfwidth, center +
  halfwidth)`, plus identical-pair catch trials spread round-robin over the profiles.
  Each pair has its own seed key, `threshold_seed_key(set, "pair", profile, center, k)`
  or `threshold_seed_key(set, "same", profile, k)`, and its own PCG64 stream. A pair
  starts from a uniformly drawn valid base recipe; a random walk then changes randomly
  chosen coordinates (one to `MAX_STEP` positions each, in a random coordinate order)
  until the exact sum of squares lies in the bin. Every motif passes `av_sound.validate`
  with no committed references (so every rule applies except the separation rule) and
  no motif is used twice in a set. Pairs never come from a study book and are never
  complete messages. The set (`records.ThresholdStimulusSet`) stores both recipes, PCM
  and WAV hashes, the exact sum of squares, the seed key and the config; its
  `sha256()` identifies it. No WAV is stored: the runner renders the recipes and checks
  the stored hashes.
- Sessions (`plan_session`): every pair once, in an order drawn from
  `threshold_seed_key(set, "order", session)`; A/B order balanced within each profile and
  bin (`ab_order_rule="balanced_per_bin"`). The `records.ThresholdSession` document is
  written before the first trial (`threshold_runner`).
- Checks: `check_stimuli` (every pair valid and in its bin, coverage, hashes),
  `check_session` (plan) and `check_plays` (each pair played exactly once per session).
- Results: `export_csv` (`TRIAL_CSV_COLUMNS`, one row per trial), `read_trials_csv`,
  `summarize` (proportion `same` per profile and bin with Wilson 95% intervals),
  `fit_logistic` / `fit_summary` (logistic fit of `P(same)` on distance),
  `build_summary` (the evidence document for O6.2.2 and G4) and `plot_summary`.

The runner (web app and bot listener) is in `threshold_runner`; the command line in
`threshold_cli`; the operator guide in `generation/docs/threshold-tool.md`.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
import os
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Final, Literal

import numpy as np
from av_sound.features import (
    distance_from_sum_sq,
    features,
    parse_threshold,
    sum_squared_diff,
)
from av_sound.recipe import Profile, Recipe, RecipeError
from av_sound.renderer import RENDERER_VERSION
from av_sound.reserved import ReservedRegistry, load_reserved_registry
from av_sound.validate import VALIDATOR_VERSION, ValidationResult, validate
from av_sound.wav import file_sha256 as wav_file_sha256

from av_generation.domain import (
    COORDINATES,
    N_COORDINATES,
    Value,
    differing_coordinates,
    recipe_values,
    values_to_recipe,
)
from av_generation.ids import DEMO_PREFIX, ID_RE
from av_generation.jsonio import (
    CodecError,
    canonical_line,
    decode_dataclass,
    document_text,
    read_json,
)
from av_generation.records import (
    PlayEvent,
    ThresholdConfig,
    ThresholdPair,
    ThresholdPlannedTrial,
    ThresholdSession,
    ThresholdStimulusSet,
    ThresholdTrial,
)
from av_generation.seeds import rng_for, seed_from_key, threshold_seed_key

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
    "run_id",
    "sum_sq",
    "onset_first_ms",
    "onset_second_ms",
    "t_ms",
)
"""One row per trial (contract to O6.2.2); recipes as canonical compact JSON, in the order
played (`first`, `second`); booleans `1`/`0`; empty cells for null values. The columns
after `tryout` complete the trial record, so `read_trials_csv` rebuilds it exactly."""

SUMMARY_CSV_COLUMNS: Final[tuple[str, ...]] = (
    "profile",
    "bin_center",
    "n_trials",
    "n_same",
    "p_same",
    "wilson_low",
    "wilson_high",
    "kind",
    "n_no_response",
    "mean_distance",
)
"""One row per profile (`P1`.., then `all`) and bin; `kind="same"` rows (empty
`bin_center`) are the catch trials. `n_trials` counts answered trials only."""

FIT_COLUMNS: Final[tuple[str, ...]] = (
    "profile",
    "status",
    "n",
    "n_same",
    "intercept",
    "slope",
    "se_intercept",
    "se_slope",
    "d50",
    "p_same_at_default",
    "iterations",
    "log_likelihood",
)
"""Logistic fit of `P(same) = 1 / (1 + exp(-(intercept + slope * distance)))` on the
answered different-pair trials, per profile and pooled (`all`)."""

SUMMARY_FORMAT: Final = "av-generation/threshold-summary"
SUMMARY_VERSION: Final = 1
POOLED: Final = "all"
"""`profile` value of the pooled rows and fit."""
Z_95: Final = 1.959963984540054
"""Two-sided 95% normal quantile used by the Wilson interval."""

MAX_STEP: Final = 2
"""Largest change of one coordinate in one walk step (semitones or value positions)."""
MAX_WALKS_PER_BASE: Final = 400
MAX_BASES: Final = 50
"""A pair search gives up (`E_SEARCH`) after `MAX_BASES x MAX_WALKS_PER_BASE` walks."""
MAX_BASE_DRAWS: Final = 10_000
MAX_DISTANCE: Final = Fraction(1, 2)
"""Upper limit of the configurable bins. The smallest non-zero distance is one semitone
on one note, `sqrt((1/12)^2 / 12) = 0.0241`; bins below it cannot be filled."""

E_CONFIG: Final = "E_CONFIG"
E_SET_ID: Final = "E_SET_ID"
E_SEARCH: Final = "E_SEARCH"
E_STIMULI: Final = "E_STIMULI"
E_SESSION: Final = "E_SESSION"
E_TRIALS: Final = "E_TRIALS"


class ThresholdError(ValueError):
    """A listening-tool input is invalid (`code`: `E_CONFIG`, `E_SET_ID`, `E_SEARCH`, ...)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Configuration


def _decimal(name: str, text: object) -> Fraction:
    if not isinstance(text, str):
        raise ThresholdError(E_CONFIG, f"{name} must be a decimal string, got {text!r}")
    try:
        return parse_threshold(text)
    except (TypeError, ValueError) as err:
        raise ThresholdError(E_CONFIG, f"{name}: {err}") from err


def check_config(config: ThresholdConfig) -> ThresholdConfig:
    """Return `config` if it is usable, else raise `ThresholdError` (`E_CONFIG`).

    Profiles are distinct `P1`..`P3`; bin centers are distinct ascending decimal strings
    within `(0, MAX_DISTANCE]`; `pairs_per_bin >= 1`, `same_pairs >= 0`,
    `0 <= gap_ms <= 5000`; `threshold_default` is a decimal string (it is also the
    threshold of the validator's reserved-signal check).
    """
    if not config.profiles or len(set(config.profiles)) != len(config.profiles):
        raise ThresholdError(E_CONFIG, f"profiles must be distinct and non-empty: {config}")
    for profile in config.profiles:
        if profile not in tuple(p.value for p in Profile):
            raise ThresholdError(E_CONFIG, f"unknown profile {profile!r}")
    half = _decimal("bin_halfwidth", config.bin_halfwidth)
    if half <= 0:
        raise ThresholdError(E_CONFIG, "bin_halfwidth must be > 0")
    centers = [_decimal("bin_centers", c) for c in config.bin_centers]
    if not centers or any(b <= a for a, b in zip(centers, centers[1:], strict=False)):
        raise ThresholdError(E_CONFIG, "bin_centers must be distinct and ascending")
    if centers[0] - half <= 0 or centers[-1] + half > MAX_DISTANCE:
        raise ThresholdError(E_CONFIG, "every bin must lie within (0, 0.5]")
    if isinstance(config.pairs_per_bin, bool) or config.pairs_per_bin < 1:
        raise ThresholdError(E_CONFIG, "pairs_per_bin must be >= 1")
    if isinstance(config.same_pairs, bool) or config.same_pairs < 0:
        raise ThresholdError(E_CONFIG, "same_pairs must be >= 0")
    if isinstance(config.gap_ms, bool) or not 0 <= config.gap_ms <= 5000:
        raise ThresholdError(E_CONFIG, "gap_ms must be within 0..5000")
    _decimal("threshold_default", config.threshold_default)
    return config


def load_config(path: str | os.PathLike[str]) -> ThresholdConfig:
    """Read a `ThresholdConfig` JSON object (the `config` object of the stimulus set)."""
    try:
        config = decode_dataclass(ThresholdConfig, read_json(path))
    except CodecError as err:
        raise ThresholdError(E_CONFIG, f"{path}: {err}") from err
    return check_config(config)


def n_trials(config: ThresholdConfig) -> int:
    """Trials per session: every pair once."""
    return len(config.profiles) * len(config.bin_centers) * config.pairs_per_bin + (
        config.same_pairs
    )


def bin_limits(center: str, halfwidth: str) -> tuple[Fraction, Fraction]:
    """Exact sum-of-squares limits `[12 (c - h)^2, 12 (c + h)^2)` of a distance bin."""
    c, h = parse_threshold(center), parse_threshold(halfwidth)
    return 12 * (c - h) ** 2, 12 * (c + h) ** 2


def in_bin(sum_sq: Fraction, center: str, halfwidth: str) -> bool:
    """True when `sqrt(sum_sq / 12)` lies in `[center - halfwidth, center + halfwidth)`."""
    low, high = bin_limits(center, halfwidth)
    return low <= sum_sq < high


def same_pair_profiles(config: ThresholdConfig) -> tuple[str, ...]:
    """Profile of each catch pair, in set order: round-robin over `config.profiles`."""
    return tuple(config.profiles[j % len(config.profiles)] for j in range(config.same_pairs))


def different_pair_id(profile: str, center: str, k: int) -> str:
    """`P1-0.100-03`."""
    return f"{profile}-{center}-{k:02d}"


def same_pair_id(profile: str, k: int) -> str:
    """`P2-same-07`."""
    return f"{profile}-same-{k:02d}"


def check_set_id(set_id: str) -> str:
    """Set IDs follow the run-ID rule (3-64 letters, digits, inner hyphens); `DEMO-` sets
    are synthetic."""
    if not isinstance(set_id, str) or not ID_RE.fullmatch(set_id):
        raise ThresholdError(E_SET_ID, f"set ID {set_id!r}: 3-64 letters, digits, hyphens")
    return set_id


# ---------------------------------------------------------------------------
# Stimulus generation


@dataclass(frozen=True, slots=True)
class _Motif:
    recipe: Recipe
    result: ValidationResult

    @property
    def pcm(self) -> str:
        assert self.result.pcm_sha256 is not None
        return self.result.pcm_sha256

    @property
    def file_sha256(self) -> str:
        assert self.result.rendered is not None
        return wav_file_sha256(self.result.rendered)


class _Generator:
    """Shared state of one set build: validator inputs and the motifs already used."""

    def __init__(self, config: ThresholdConfig, reserved: ReservedRegistry) -> None:
        self.config = config
        self.reserved = reserved
        self.used: set[tuple[str, str]] = set()

    def check(self, recipe: Recipe, profile: str) -> ValidationResult:
        return validate(
            recipe, profile, (), reserved=self.reserved, threshold=self.config.threshold_default
        )

    def draw_base(self, rng: np.random.Generator, profile: str) -> _Motif:
        for _ in range(MAX_BASE_DRAWS):
            values = [c.values[int(rng.integers(len(c.values)))] for c in COORDINATES]
            recipe = values_to_recipe(values)
            result = self.check(recipe, profile)
            if result.ok and (profile, result.pcm_sha256) not in self.used:
                return _Motif(recipe, result)
        raise ThresholdError(E_SEARCH, f"no valid base recipe for {profile}")  # pragma: no cover

    @staticmethod
    def walk(
        rng: np.random.Generator,
        base: Recipe,
        low: Fraction,
        high: Fraction,
    ) -> tuple[Recipe, Fraction] | None:
        """One random walk from `base`; the first recipe with `low <= S < high`, or None."""
        base_features = features(base)
        values: list[Value] = list(recipe_values(base))
        for index in rng.permutation(N_COORDINATES):
            i = int(index)
            coord = COORDINATES[i]
            step = int(rng.integers(1, MAX_STEP + 1)) * (1 if int(rng.integers(2)) else -1)
            position = coord.position(values[i]) + step
            if not 0 <= position < len(coord.values):
                continue
            trial = list(values)
            trial[i] = coord.values[position]
            recipe = values_to_recipe(trial)
            total = sum_squared_diff(base_features, features(recipe))
            if total < high:
                values = trial
                if total >= low:
                    return recipe, total
        return None

    def different(self, set_id: str, profile: str, center: str, k: int) -> ThresholdPair:
        key = threshold_seed_key(set_id, "pair", profile, center, k)
        rng = rng_for(key)
        low, high = bin_limits(center, self.config.bin_halfwidth)
        steps = 0
        for _ in range(MAX_BASES):
            base = self.draw_base(rng, profile)
            for _ in range(MAX_WALKS_PER_BASE):
                steps += 1
                walked = self.walk(rng, base.recipe, low, high)
                if walked is None:
                    continue
                recipe, total = walked
                result = self.check(recipe, profile)
                if not result.ok or result.pcm_sha256 == base.pcm:
                    continue
                if (profile, result.pcm_sha256) in self.used:
                    continue
                partner = _Motif(recipe, result)
                return self._pair(
                    different_pair_id(profile, center, k),
                    profile,
                    center,
                    base,
                    partner,
                    total,
                    key,
                    steps,
                )
        raise ThresholdError(
            E_SEARCH, f"no pair found for {profile} bin {center} (pair {k}) after {steps} walks"
        )

    def same(self, set_id: str, profile: str, k: int) -> ThresholdPair:
        key = threshold_seed_key(set_id, "same", profile, k)
        motif = self.draw_base(rng_for(key), profile)
        return self._pair(
            same_pair_id(profile, k), profile, None, motif, motif, Fraction(0), key, 0
        )

    def _pair(
        self,
        pair_id: str,
        profile: str,
        center: str | None,
        a: _Motif,
        b: _Motif,
        total: Fraction,
        key: str,
        steps: int,
    ) -> ThresholdPair:
        self.used.add((profile, a.pcm))
        self.used.add((profile, b.pcm))
        return ThresholdPair(
            pair_id=pair_id,
            profile=profile,
            kind="same" if center is None else "different",
            bin_center=center,
            recipe_a=a.recipe.to_dict(),
            recipe_b=b.recipe.to_dict(),
            pcm_sha256_a=a.pcm,
            pcm_sha256_b=b.pcm,
            file_sha256_a=a.file_sha256,
            file_sha256_b=b.file_sha256,
            sum_sq=str(total),
            distance=distance_from_sum_sq(total),
            differing=differing_coordinates(a.recipe, b.recipe),
            seed_key=key,
            search_steps=steps,
        )


def generate_stimuli(
    set_id: str,
    config: ThresholdConfig = DEFAULT_CONFIG,
    *,
    reserved: ReservedRegistry | None = None,
) -> ThresholdStimulusSet:
    """The stimulus set for `set_id` (same ID and config -> identical set and hash) (#23).

    Order: for each profile, each bin (ascending), pairs 1..`pairs_per_bin`; then the
    `same_pairs` catch pairs, round-robin over the profiles. `reserved` defaults to the
    published registry (`sound/reserved/registry.json`). A set ID starting `DEMO-` makes
    a synthetic set (`demo=true`).
    """
    check_set_id(set_id)
    check_config(config)
    gen = _Generator(config, load_reserved_registry() if reserved is None else reserved)
    pairs: list[ThresholdPair] = []
    for profile in config.profiles:
        for center in config.bin_centers:
            for k in range(1, config.pairs_per_bin + 1):
                pairs.append(gen.different(set_id, profile, center, k))
    counts: Counter[str] = Counter()
    for profile in same_pair_profiles(config):
        counts[profile] += 1
        pairs.append(gen.same(set_id, profile, counts[profile]))
    return ThresholdStimulusSet(
        set_id=set_id,
        demo=set_id.startswith(DEMO_PREFIX),
        renderer_version=RENDERER_VERSION,
        validator_version=VALIDATOR_VERSION,
        config=config,
        pairs=tuple(pairs),
    ).check()


def _pair_specs(config: ThresholdConfig) -> list[tuple[str, str, tuple[str | int, ...]]]:
    """`(pair_id, seed purpose, seed parts)` of every pair, in set order."""
    specs: list[tuple[str, str, tuple[str | int, ...]]] = [
        (different_pair_id(profile, center, k), "pair", (profile, center, k))
        for profile in config.profiles
        for center in config.bin_centers
        for k in range(1, config.pairs_per_bin + 1)
    ]
    counts: Counter[str] = Counter()
    for profile in same_pair_profiles(config):
        counts[profile] += 1
        specs.append((same_pair_id(profile, counts[profile]), "same", (profile, counts[profile])))
    return specs


def expected_pair_ids(config: ThresholdConfig) -> tuple[str, ...]:
    """Every pair ID of a set built with `config`, in set order."""
    return tuple(pair_id for pair_id, _, _ in _pair_specs(config))


def expected_seed_keys(set_id: str, config: ThresholdConfig) -> dict[str, str]:
    """Pair ID -> seed key of every pair of set `set_id` built with `config`."""
    return {
        pair_id: threshold_seed_key(set_id, purpose, *parts)
        for pair_id, purpose, parts in _pair_specs(config)
    }


def coverage(stimuli: ThresholdStimulusSet) -> dict[tuple[str, str], int]:
    """Pair count per `(profile, bin_center)`; catch pairs count under `(profile, "same")`."""
    counts: Counter[tuple[str, str]] = Counter(
        (p.profile, p.bin_center if p.bin_center is not None else "same") for p in stimuli.pairs
    )
    return dict(sorted(counts.items()))


def pair_motifs(pair: ThresholdPair) -> tuple[Recipe, Recipe]:
    """The pair's recipes A and B."""
    return Recipe.from_dict(pair.recipe_a), Recipe.from_dict(pair.recipe_b)


def check_stimuli(
    stimuli: ThresholdStimulusSet,
    *,
    reserved: ReservedRegistry | None = None,
    render: bool = True,
) -> tuple[str, ...]:
    """Every problem of a stimulus set (empty when it is sound).

    Checks the schema, the config, the exact pair-ID coverage (every profile x bin with
    `pairs_per_bin` pairs, catch pairs round-robin), the seed keys, the exact sum of
    squares, distance, differing coordinates and bin of each pair, identical catch
    pairs, distinct motifs within a profile, the code versions, and (with `render=True`)
    that both motifs of every pair pass `av_sound.validate` (no committed references:
    every rule except separation) and render to the stored PCM and WAV hashes.
    """
    problems: list[str] = [f"schema: {e}" for e in stimuli.schema_errors()]
    if problems:
        return tuple(problems)
    config = stimuli.config
    try:
        check_config(config)
    except ThresholdError as err:
        return (f"config: {err}",)
    if stimuli.demo != stimuli.set_id.startswith(DEMO_PREFIX):
        problems.append("demo flag does not match the set ID prefix")
    if stimuli.renderer_version != RENDERER_VERSION:
        problems.append(f"built with renderer {stimuli.renderer_version}, not {RENDERER_VERSION}")
    if stimuli.validator_version != VALIDATOR_VERSION:
        problems.append(
            f"built with validator {stimuli.validator_version}, not {VALIDATOR_VERSION}"
        )
    ids = tuple(p.pair_id for p in stimuli.pairs)
    if ids != expected_pair_ids(config):
        problems.append("pair IDs do not match the configured coverage and order")
    registry = load_reserved_registry() if reserved is None else reserved
    keys = expected_seed_keys(stimuli.set_id, config)
    seen: dict[tuple[str, str], str] = {}
    for pair in stimuli.pairs:
        if pair.seed_key != keys.get(pair.pair_id):
            problems.append(f"{pair.pair_id}: seed key {pair.seed_key} is not the pair's key")
        problems += [f"{pair.pair_id}: {p}" for p in _pair_problems(stimuli, pair)]
        for pcm in {pair.pcm_sha256_a, pair.pcm_sha256_b}:
            other = seen.setdefault((pair.profile, pcm), pair.pair_id)
            if other != pair.pair_id:
                problems.append(f"{pair.pair_id}: motif already used by {other}")
        if render:
            problems += [f"{pair.pair_id}: {p}" for p in _render_problems(config, pair, registry)]
    return tuple(problems)


def _pair_problems(stimuli: ThresholdStimulusSet, pair: ThresholdPair) -> list[str]:
    config = stimuli.config
    out: list[str] = []
    try:
        a, b = pair_motifs(pair)
    except RecipeError as err:
        return [f"recipe: {err}"]
    total = sum_squared_diff(a, b)
    if Fraction(pair.sum_sq) != total:
        out.append(f"sum_sq {pair.sum_sq} != {total}")
    if pair.distance != distance_from_sum_sq(total):
        out.append(f"distance {pair.distance} != {distance_from_sum_sq(total)}")
    if tuple(pair.differing) != differing_coordinates(a, b):
        out.append("differing coordinates do not match the recipes")
    if pair.kind == "same":
        if a != b or pair.bin_center is not None or pair.pcm_sha256_a != pair.pcm_sha256_b:
            out.append("catch pair is not two identical motifs without a bin")
        return out
    center = pair.bin_center
    if center is None or center not in config.bin_centers:
        return [*out, f"bin {center!r} is not configured"]
    if not in_bin(total, center, config.bin_halfwidth):
        out.append(f"distance {distance_from_sum_sq(total):.6f} outside bin {center}")
    if pair.pcm_sha256_a == pair.pcm_sha256_b:
        out.append("different pair has identical waveforms")
    return out


def _render_problems(
    config: ThresholdConfig, pair: ThresholdPair, registry: ReservedRegistry
) -> list[str]:
    out: list[str] = []
    for side, data, pcm, wav in (
        ("A", pair.recipe_a, pair.pcm_sha256_a, pair.file_sha256_a),
        ("B", pair.recipe_b, pair.pcm_sha256_b, pair.file_sha256_b),
    ):
        result = validate(
            dict(data), pair.profile, (), reserved=registry, threshold=config.threshold_default
        )
        if not result.ok:
            out.append(f"motif {side} fails validation: {', '.join(result.codes)}")
            continue
        if result.pcm_sha256 != pcm:
            out.append(f"motif {side} PCM hash differs from the set")
        assert result.rendered is not None
        if wav_file_sha256(result.rendered) != wav:
            out.append(f"motif {side} WAV hash differs from the set")
    return out


def write_stimuli(stimuli: ThresholdStimulusSet, path: str | os.PathLike[str]) -> str:
    """Write the set document (refusing a non-DEMO set inside a git work tree); returns
    the file SHA-256. The set hash is `stimuli.sha256()`."""
    from av_sound.fallback import inside_work_tree

    target = Path(path)
    if not stimuli.demo and inside_work_tree(target.parent):
        raise ThresholdError(
            E_STIMULI, f"non-DEMO stimulus set {stimuli.set_id} belongs in restricted storage"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    return stimuli.write(target, exclusive=False)


# ---------------------------------------------------------------------------
# Sessions


def order_seed_key(set_id: str, session_id: str) -> str:
    """`THRESHOLD|<set>|order|<session>`: the session's trial-order and A/B stream."""
    return threshold_seed_key(set_id, "order", session_id)


def trial_id(session_id: str, trial_index: int) -> str:
    """Play-event `trial_id` of a trial: `<session>.t<index:03d>`."""
    return f"{session_id}.t{trial_index:03d}"


def _group(pair: ThresholdPair) -> tuple[str, str, str]:
    return (pair.profile, pair.kind, pair.bin_center or "")


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
    """The session document with its seeded trial order and counterbalanced A/B order (#23).

    One stream, `rng_for(order_seed_key(set, session))`, first assigns A/B orders: within
    each profile x kind x bin group (set order) half the pairs get `AB` and half `BA`,
    shuffled, with one seeded coin flip for an odd group; then it draws a uniform
    permutation of all pairs (each pair exactly once).
    """
    if not isinstance(session_id, str) or not ID_RE.fullmatch(session_id):
        raise ThresholdError(E_SESSION, f"session ID {session_id!r}: 3-64 letters, digits, -")
    key = order_seed_key(stimuli.set_id, session_id)
    rng = rng_for(key)
    groups: dict[tuple[str, str, str], list[str]] = {}
    for pair in stimuli.pairs:
        groups.setdefault(_group(pair), []).append(pair.pair_id)
    orders: dict[str, Literal["AB", "BA"]] = {}
    for members in groups.values():
        labels: list[Literal["AB", "BA"]] = ["AB", "BA"] * (len(members) // 2)
        if len(members) % 2:
            labels.append("AB" if int(rng.integers(2)) == 0 else "BA")
        for member, j in zip(members, rng.permutation(len(labels)), strict=True):
            orders[member] = labels[int(j)]
    sequence = rng.permutation(len(stimuli.pairs))
    plan = tuple(
        ThresholdPlannedTrial(
            i + 1, stimuli.pairs[int(j)].pair_id, orders[stimuli.pairs[int(j)].pair_id]
        )
        for i, j in enumerate(sequence)
    )
    session = ThresholdSession(
        session_id=session_id,
        set_id=stimuli.set_id,
        set_sha256=stimuli.sha256(),
        listener_id=listener_id,
        station=station,
        gain_db=float(gain_db),
        order_seed_key=key,
        order_seed=seed_from_key(key),
        ab_order_rule="balanced_per_bin",
        plan=plan,
        tryout=tryout,
        demo=stimuli.demo,
        created_utc=created_utc,
    )
    errors = session.schema_errors()
    if errors:
        raise ThresholdError(E_SESSION, f"session does not match its schema: {errors[:3]}")
    return session


def check_session(session: ThresholdSession, stimuli: ThresholdStimulusSet) -> tuple[str, ...]:
    """Problems of a session document against its stimulus set (empty when sound): the
    set hash, the seed key and seed, every pair exactly once, trial indices 1..N, A/B
    balance within each group, and the plan equals a fresh `plan_session`."""
    problems: list[str] = [f"schema: {e}" for e in session.schema_errors()]
    if session.set_id != stimuli.set_id or session.set_sha256 != stimuli.sha256():
        problems.append("session does not name this stimulus set (set_id/set_sha256)")
    key = order_seed_key(stimuli.set_id, session.session_id)
    if session.order_seed_key != key or session.order_seed != seed_from_key(key):
        problems.append("order seed key or seed is not the session's")
    planned = Counter(t.pair_id for t in session.plan)
    if planned != Counter(p.pair_id for p in stimuli.pairs):
        problems.append("the plan does not hold every pair exactly once")
    if [t.trial_index for t in session.plan] != list(range(1, len(session.plan) + 1)):
        problems.append("trial indices are not 1..N in order")
    by_id = {p.pair_id: p for p in stimuli.pairs}
    balance: dict[tuple[str, str, str], int] = {}
    for trial in session.plan:
        pair = by_id.get(trial.pair_id)
        if pair is not None:
            g = _group(pair)
            balance[g] = balance.get(g, 0) + (1 if trial.order == "AB" else -1)
    problems += [f"A/B order unbalanced in {g}" for g, v in sorted(balance.items()) if abs(v) > 1]
    if not problems:
        again = plan_session(
            stimuli,
            session.session_id,
            listener_id=session.listener_id,
            station=session.station,
            gain_db=session.gain_db,
            tryout=session.tryout,
            created_utc=session.created_utc,
        )
        if again.plan != session.plan:
            problems.append("the plan is not the seeded plan of this session")
    return tuple(problems)


@dataclass(frozen=True, slots=True)
class Presentation:
    """What one planned trial plays, in order: first motif, gap, second motif."""

    trial_index: int
    pair: ThresholdPair
    order: Literal["AB", "BA"]
    recipe_first: Mapping[str, Any]
    recipe_second: Mapping[str, Any]
    pcm_sha256_first: str
    pcm_sha256_second: str
    file_sha256_first: str
    file_sha256_second: str


def presentation(pair: ThresholdPair, order: Literal["AB", "BA"], trial_index: int) -> Presentation:
    """The motifs of a trial in play order."""
    a = (pair.recipe_a, pair.pcm_sha256_a, pair.file_sha256_a)
    b = (pair.recipe_b, pair.pcm_sha256_b, pair.file_sha256_b)
    first, second = (a, b) if order == "AB" else (b, a)
    return Presentation(
        trial_index, pair, order, first[0], second[0], first[1], second[1], first[2], second[2]
    )


def session_presentations(
    session: ThresholdSession, stimuli: ThresholdStimulusSet
) -> tuple[Presentation, ...]:
    """The presentations of a session's plan, in trial order."""
    by_id = {p.pair_id: p for p in stimuli.pairs}
    try:
        return tuple(presentation(by_id[t.pair_id], t.order, t.trial_index) for t in session.plan)
    except KeyError as err:
        raise ThresholdError(E_SESSION, f"plan names unknown pair {err}") from None


# ---------------------------------------------------------------------------
# Play log check


@dataclass(frozen=True, slots=True)
class PlayCheck:
    """Result of `check_plays` for one session."""

    session_id: str
    n_planned: int
    n_played: int
    """Planned trials with exactly one `threshold_first` and one `threshold_second` play."""
    n_answered: int
    n_refused: int
    """Refused play requests (a reused audio token, a second play report)."""
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems


def check_plays(
    session: ThresholdSession,
    stimuli: ThresholdStimulusSet,
    plays: Iterable[PlayEvent],
    trials: Iterable[ThresholdTrial],
    *,
    complete: bool = True,
) -> PlayCheck:
    """Check that each pair of the session played exactly once (no replay).

    Per planned trial: at most one `played` event per context (`threshold_first`,
    `threshold_second`), with the asset of the presented order, first onset not after the
    second; a trial with a record has both plays (unless it was skipped before playing,
    `response` null and no onsets). With `complete=True` every planned trial must have
    both plays and a trial record. Events of other sessions are ignored.
    """
    shown = {p.trial_index: p for p in session_presentations(session, stimuli)}
    prefix = f"{session.session_id}.t"
    played: dict[tuple[int, str], list[PlayEvent]] = {}
    refused = 0
    problems: list[str] = []
    for event in plays:
        if event.trial_id is None or not event.trial_id.startswith(prefix):
            continue
        if event.result == "refused":
            refused += 1
            continue
        try:
            index = int(event.trial_id[len(prefix) :])
        except ValueError:
            problems.append(f"malformed trial_id {event.trial_id}")
            continue
        if index not in shown:
            problems.append(f"play for unplanned trial {event.trial_id}")
            continue
        played.setdefault((index, event.context), []).append(event)
    answered: dict[int, ThresholdTrial] = {}
    for trial in trials:
        if trial.session_id != session.session_id:
            continue
        if trial.trial_index in answered:
            problems.append(f"trial {trial.trial_index} recorded twice")
        answered[trial.trial_index] = trial
    n_played = 0
    for index, shown_trial in shown.items():
        first = played.get((index, "threshold_first"), [])
        second = played.get((index, "threshold_second"), [])
        if len(first) > 1 or len(second) > 1:
            problems.append(f"trial {index} ({shown_trial.pair.pair_id}) played more than once")
        if len(first) != len(second):
            problems.append(f"trial {index}: first and second play counts differ")
        if first and first[0].asset_id != shown_trial.file_sha256_first:
            problems.append(f"trial {index}: first play is not the planned motif")
        if second and second[0].asset_id != shown_trial.file_sha256_second:
            problems.append(f"trial {index}: second play is not the planned motif")
        if first and second and (first[0].onset_ms or 0) > (second[0].onset_ms or 0):
            problems.append(f"trial {index}: second motif started before the first")
        both = len(first) == 1 and len(second) == 1
        n_played += both
        record = answered.get(index)
        if record is not None:
            if record.pair_id != shown_trial.pair.pair_id or record.order != shown_trial.order:
                problems.append(f"trial {index}: record does not match the plan")
            skipped_unplayed = record.response is None and record.onset_first_ms is None
            if not both and not skipped_unplayed:
                problems.append(f"trial {index}: answered without exactly one play of each motif")
        elif complete:
            problems.append(f"trial {index}: no trial record")
        if complete and not both and not (record is not None and record.response is None):
            problems.append(f"trial {index}: not played")
    extra = sorted(set(answered) - set(shown))
    problems += [f"record for unplanned trial {i}" for i in extra]
    return PlayCheck(
        session_id=session.session_id,
        n_planned=len(shown),
        n_played=n_played,
        n_answered=sum(1 for t in answered.values() if t.response is not None),
        n_refused=refused,
        problems=tuple(problems),
    )


# ---------------------------------------------------------------------------
# CSV export


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _recipe_text(recipe: Mapping[str, Any]) -> str:
    return canonical_line(dict(recipe))[:-1].decode("ascii")


def trial_rows(
    trials: Sequence[ThresholdTrial], stimuli: ThresholdStimulusSet
) -> list[dict[str, str]]:
    """CSV rows (`TRIAL_CSV_COLUMNS`) for trials of this set, sorted by session and trial.

    Raises `ThresholdError` (`E_TRIALS`) when a trial names an unknown pair or disagrees
    with the set (profile, kind, bin, distance).
    """
    by_id = {p.pair_id: p for p in stimuli.pairs}
    rows: list[dict[str, str]] = []
    for trial in sorted(trials, key=lambda t: (t.session_id, t.trial_index)):
        pair = by_id.get(trial.pair_id)
        if pair is None:
            raise ThresholdError(E_TRIALS, f"trial names unknown pair {trial.pair_id}")
        if (trial.profile, trial.kind, trial.bin_center, trial.distance) != (
            pair.profile,
            pair.kind,
            pair.bin_center,
            pair.distance,
        ):
            raise ThresholdError(E_TRIALS, f"trial {trial.trial_index} disagrees with the set")
        shown = presentation(pair, trial.order, trial.trial_index)
        values: dict[str, object] = {
            "session_id": trial.session_id,
            "listener_id": trial.listener_id,
            "trial_index": trial.trial_index,
            "pair_id": trial.pair_id,
            "profile": trial.profile,
            "kind": trial.kind,
            "bin_center": trial.bin_center,
            "distance": trial.distance,
            "order": trial.order,
            "gap_ms": trial.gap_ms,
            "recipe_first": _recipe_text(shown.recipe_first),
            "recipe_second": _recipe_text(shown.recipe_second),
            "pcm_sha256_first": shown.pcm_sha256_first,
            "pcm_sha256_second": shown.pcm_sha256_second,
            "response": trial.response,
            "rt_ms": trial.rt_ms,
            "tryout": trial.tryout,
            "run_id": trial.run_id,
            "sum_sq": pair.sum_sq,
            "onset_first_ms": trial.onset_first_ms,
            "onset_second_ms": trial.onset_second_ms,
            "t_ms": trial.t_ms,
        }
        rows.append({c: _cell(values[c]) for c in TRIAL_CSV_COLUMNS})
    return rows


def _write_csv(
    path: str | os.PathLike[str], columns: Sequence[str], rows: Iterable[Mapping[str, str]]
) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row[c] for c in columns])
    data = buffer.getvalue().encode("utf-8")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return hashlib.sha256(data).hexdigest()


def export_csv(
    trials: Sequence[ThresholdTrial], stimuli: ThresholdStimulusSet, path: str | os.PathLike[str]
) -> str:
    """Write the trial CSV (`TRIAL_CSV_COLUMNS`); returns its SHA-256 (#23)."""
    return _write_csv(path, TRIAL_CSV_COLUMNS, trial_rows(trials, stimuli))


def _opt_int(text: str) -> int | None:
    return None if text == "" else int(text)


def read_trials_csv(path: str | os.PathLike[str]) -> list[ThresholdTrial]:
    """The trial records of an exported CSV (the inverse of `export_csv`), validated."""
    with open(path, encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != TRIAL_CSV_COLUMNS:
            raise ThresholdError(E_TRIALS, f"{path}: columns are not TRIAL_CSV_COLUMNS")
        out: list[ThresholdTrial] = []
        for row in reader:
            try:
                trial = ThresholdTrial(
                    run_id=row["run_id"],
                    session_id=row["session_id"],
                    listener_id=row["listener_id"],
                    trial_index=int(row["trial_index"]),
                    pair_id=row["pair_id"],
                    profile=row["profile"],
                    kind=row["kind"],  # type: ignore[arg-type]
                    bin_center=row["bin_center"] or None,
                    distance=float(row["distance"]),
                    order=row["order"],  # type: ignore[arg-type]
                    gap_ms=int(row["gap_ms"]),
                    tryout=row["tryout"] == "1",
                    t_ms=int(row["t_ms"]),
                    response=row["response"] or None,  # type: ignore[arg-type]
                    rt_ms=_opt_int(row["rt_ms"]),
                    onset_first_ms=_opt_int(row["onset_first_ms"]),
                    onset_second_ms=_opt_int(row["onset_second_ms"]),
                )
            except ValueError as err:
                raise ThresholdError(E_TRIALS, f"{path}:{reader.line_num}: {err}") from err
            errors = trial.schema_errors()
            if errors or row["tryout"] not in ("0", "1"):
                raise ThresholdError(E_TRIALS, f"{path}:{reader.line_num}: {errors[:3]}")
            out.append(trial)
    return out


# ---------------------------------------------------------------------------
# Summary: proportions, Wilson intervals, logistic fit


def wilson_interval(k: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval of `k` successes in `n` trials (95% by default)."""
    if n <= 0 or not 0 <= k <= n:
        raise ValueError(f"wilson_interval needs 0 <= k <= n and n > 0, got k={k}, n={n}")
    p = k / n
    z2 = z * z
    denom = 1 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    low = 0.0 if k == 0 else max(0.0, center - half)
    high = 1.0 if k == n else min(1.0, center + half)
    return low, high


def _require_one_population(trials: Sequence[ThresholdTrial]) -> None:
    if len({t.tryout for t in trials}) > 1:
        raise ThresholdError(
            E_TRIALS, "tryout and listener trials are mixed; summarize them separately"
        )


def _profiles(trials: Sequence[ThresholdTrial]) -> list[str]:
    return sorted({t.profile for t in trials})


def summarize(trials: Sequence[ThresholdTrial]) -> list[dict[str, Any]]:
    """Summary rows (`SUMMARY_CSV_COLUMNS`) with Wilson 95% intervals (#23).

    One row per profile (sorted, then the pooled `all`) and group: the catch trials
    (`kind="same"`, `bin_center=None`) first, then the distance bins in ascending order.
    `n_trials` and `n_same` count answered trials; `p_same`, `wilson_low` and
    `wilson_high` are `None` when nothing was answered. Tryout and listener trials must
    not be mixed (`ThresholdError`).
    """
    _require_one_population(trials)
    rows: list[dict[str, Any]] = []
    for profile in [*_profiles(trials), POOLED]:
        chosen = [t for t in trials if profile in (POOLED, t.profile)]
        groups: dict[str | None, list[ThresholdTrial]] = {}
        for t in chosen:
            groups.setdefault(t.bin_center if t.kind == "different" else None, []).append(t)
        keys = sorted(groups, key=lambda c: Fraction(-1) if c is None else Fraction(c))
        for center in keys:
            group = groups[center]
            answered = [t for t in group if t.response is not None]
            n = len(answered)
            k = sum(1 for t in answered if t.response == "same")
            low, high = wilson_interval(k, n) if n else (None, None)
            rows.append(
                {
                    "profile": profile,
                    "bin_center": center,
                    "n_trials": n,
                    "n_same": k,
                    "p_same": k / n if n else None,
                    "wilson_low": low,
                    "wilson_high": high,
                    "kind": "same" if center is None else "different",
                    "n_no_response": len(group) - n,
                    "mean_distance": math.fsum(t.distance for t in group) / len(group),
                }
            )
    return rows


@dataclass(frozen=True, slots=True)
class LogisticFit:
    """Maximum-likelihood fit of `logit P(same) = intercept + slope * distance`.

    `status`: `ok`; `separated` (the responses are perfectly separable by distance, so no
    finite estimate exists); `degenerate` (fewer than two distinct distances, or every
    answer the same). `d50` is the distance where `P(same) = 0.5` (only when the slope is
    negative); `p_same_at_default` is the fitted `P(same)` at the default threshold.
    """

    profile: str
    status: Literal["ok", "separated", "degenerate"]
    n: int
    n_same: int
    intercept: float | None = None
    slope: float | None = None
    se_intercept: float | None = None
    se_slope: float | None = None
    d50: float | None = None
    p_same_at_default: float | None = None
    iterations: int = 0
    log_likelihood: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {c: getattr(self, c) for c in FIT_COLUMNS}


def _expit(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def _log_likelihood(b0: float, b1: float, xs: Sequence[float], ys: Sequence[int]) -> float:
    terms = []
    for x, y in zip(xs, ys, strict=True):
        eta = b0 + b1 * x
        softplus = eta + math.log1p(math.exp(-eta)) if eta > 0 else math.log1p(math.exp(eta))
        terms.append(y * eta - softplus)
    return math.fsum(terms)


def _separated(xs: Sequence[float], ys: Sequence[int]) -> bool:
    ones = [x for x, y in zip(xs, ys, strict=True) if y]
    zeros = [x for x, y in zip(xs, ys, strict=True) if not y]
    return max(ones) <= min(zeros) or max(zeros) <= min(ones)


def fit_logistic(
    distances: Sequence[float],
    same: Sequence[bool],
    *,
    profile: str = POOLED,
    threshold_default: str = DEFAULT_CONFIG.threshold_default,
    max_iterations: int = 100,
) -> LogisticFit:
    """Fit `P(same)` against distance by Newton-Raphson (pure Python, `math.fsum`, so the
    result does not depend on BLAS). Values are rounded to 9 decimals."""
    xs = [float(x) for x in distances]
    ys = [1 if s else 0 for s in same]
    if len(xs) != len(ys):
        raise ValueError("distances and responses differ in length")
    n, k = len(ys), sum(ys)
    if n < 2 or len(set(xs)) < 2 or k in (0, n):
        return LogisticFit(profile, "degenerate", n, k)
    if _separated(xs, ys):
        return LogisticFit(profile, "separated", n, k)
    b0 = math.log(k / (n - k))
    b1 = 0.0
    ll = _log_likelihood(b0, b1, xs, ys)
    iterations = 0
    h00 = h01 = h11 = 0.0
    for iterations in range(1, max_iterations + 1):  # noqa: B007 - used after the loop
        ps = [_expit(b0 + b1 * x) for x in xs]
        ws = [p * (1 - p) for p in ps]
        g0 = math.fsum(y - p for y, p in zip(ys, ps, strict=True))
        g1 = math.fsum((y - p) * x for x, y, p in zip(xs, ys, ps, strict=True))
        h00 = math.fsum(ws)
        h01 = math.fsum(w * x for w, x in zip(ws, xs, strict=True))
        h11 = math.fsum(w * x * x for w, x in zip(ws, xs, strict=True))
        det = h00 * h11 - h01 * h01
        if det <= 0:  # pragma: no cover - excluded by the degenerate/separated checks
            return LogisticFit(profile, "degenerate", n, k, iterations=iterations)
        d0 = (h11 * g0 - h01 * g1) / det
        d1 = (h00 * g1 - h01 * g0) / det
        scale = 1.0
        while True:
            c0, c1 = b0 + scale * d0, b1 + scale * d1
            new_ll = _log_likelihood(c0, c1, xs, ys)
            if new_ll >= ll - 1e-12 or scale < 1e-6:
                break
            scale /= 2
        b0, b1, ll_old, ll = c0, c1, ll, new_ll
        if abs(scale * d0) < 1e-10 * (1 + abs(b0)) and abs(scale * d1) < 1e-10 * (1 + abs(b1)):
            break
        if abs(ll - ll_old) < 1e-14 and scale < 1e-6:  # pragma: no cover - stalled
            break
    ps = [_expit(b0 + b1 * x) for x in xs]
    ws = [p * (1 - p) for p in ps]
    h00 = math.fsum(ws)
    h01 = math.fsum(w * x for w, x in zip(ws, xs, strict=True))
    h11 = math.fsum(w * x * x for w, x in zip(ws, xs, strict=True))
    det = h00 * h11 - h01 * h01
    se0 = math.sqrt(h11 / det) if det > 0 else None
    se1 = math.sqrt(h00 / det) if det > 0 else None
    default = float(parse_threshold(threshold_default))

    def r(value: float | None) -> float | None:
        return None if value is None else round(value, 9)

    return LogisticFit(
        profile=profile,
        status="ok",
        n=n,
        n_same=k,
        intercept=r(b0),
        slope=r(b1),
        se_intercept=r(se0),
        se_slope=r(se1),
        d50=r(-b0 / b1) if b1 < 0 else None,
        p_same_at_default=r(_expit(b0 + b1 * default)),
        iterations=iterations,
        log_likelihood=r(ll),
    )


def fit_summary(
    trials: Sequence[ThresholdTrial], *, threshold_default: str = DEFAULT_CONFIG.threshold_default
) -> list[LogisticFit]:
    """Logistic fits on answered different-pair trials: per profile, then pooled (`all`)."""
    _require_one_population(trials)
    out: list[LogisticFit] = []
    for profile in [*_profiles(trials), POOLED]:
        chosen = [
            t
            for t in trials
            if t.kind == "different" and t.response is not None and profile in (POOLED, t.profile)
        ]
        out.append(
            fit_logistic(
                [t.distance for t in chosen],
                [t.response == "same" for t in chosen],
                profile=profile,
                threshold_default=threshold_default,
            )
        )
    return out


def _fmt(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.6f}"
    return _cell(value)


def write_summary_csv(rows: Sequence[Mapping[str, Any]], path: str | os.PathLike[str]) -> str:
    """Write `summarize()` rows (`SUMMARY_CSV_COLUMNS`; floats with 6 decimals)."""
    return _write_csv(
        path, SUMMARY_CSV_COLUMNS, ({c: _fmt(r[c]) for c in SUMMARY_CSV_COLUMNS} for r in rows)
    )


def write_fits_csv(fits: Sequence[LogisticFit], path: str | os.PathLike[str]) -> str:
    """Write the logistic fits (`FIT_COLUMNS`; floats with 6 decimals)."""
    return _write_csv(
        path, FIT_COLUMNS, ({c: _fmt(v) for c, v in f.to_dict().items()} for f in fits)
    )


def build_summary(
    trials: Sequence[ThresholdTrial],
    *,
    label: str,
    stimuli: ThresholdStimulusSet | None = None,
    sessions: Sequence[ThresholdSession] = (),
    play_checks: Sequence[PlayCheck] = (),
    trials_csv_sha256: str | None = None,
) -> dict[str, Any]:
    """The summary document (`av-generation/threshold-summary` v1): the evidence file for
    O6.2.2 and the G4 freeze (#25). It reports data only (no threshold decision)."""
    _require_one_population(trials)
    config = stimuli.config if stimuli is not None else None
    default = config.threshold_default if config is not None else DEFAULT_CONFIG.threshold_default
    return {
        "format": SUMMARY_FORMAT,
        "format_version": SUMMARY_VERSION,
        "label": label,
        "tryout": bool(trials) and trials[0].tryout,
        "set_id": stimuli.set_id if stimuli is not None else None,
        "set_sha256": stimuli.sha256() if stimuli is not None else None,
        "demo": stimuli.demo if stimuli is not None else None,
        "config": None if config is None else _config_dict(config),
        "threshold_default": default,
        "n_trials": len(trials),
        "n_answered": sum(1 for t in trials if t.response is not None),
        "listeners": sorted({t.listener_id for t in trials}),
        "sessions": [_session_entry(s, trials, play_checks) for s in sessions]
        or [{"session_id": s} for s in sorted({t.session_id for t in trials})],
        "rows": summarize(trials),
        "fits": [f.to_dict() for f in fit_summary(trials, threshold_default=default)],
        "trials_csv_sha256": trials_csv_sha256,
        "code": {"renderer_version": RENDERER_VERSION, "validator_version": VALIDATOR_VERSION},
        "decision": None,
    }


def _config_dict(config: ThresholdConfig) -> dict[str, Any]:
    return {
        "profiles": list(config.profiles),
        "bin_centers": list(config.bin_centers),
        "bin_halfwidth": config.bin_halfwidth,
        "pairs_per_bin": config.pairs_per_bin,
        "same_pairs": config.same_pairs,
        "gap_ms": config.gap_ms,
        "threshold_default": config.threshold_default,
    }


def _session_entry(
    session: ThresholdSession, trials: Sequence[ThresholdTrial], checks: Sequence[PlayCheck]
) -> dict[str, Any]:
    own = [t for t in trials if t.session_id == session.session_id]
    check = next((c for c in checks if c.session_id == session.session_id), None)
    return {
        "session_id": session.session_id,
        "listener_id": session.listener_id,
        "station": session.station,
        "gain_db": session.gain_db,
        "tryout": session.tryout,
        "created_utc": session.created_utc,
        "n_planned": len(session.plan),
        "n_trials": len(own),
        "n_answered": sum(1 for t in own if t.response is not None),
        "plays_ok": None if check is None else check.ok,
        "n_refused_plays": None if check is None else check.n_refused,
    }


def write_summary(summary: Mapping[str, Any], path: str | os.PathLike[str]) -> str:
    """Write the summary document (pretty canonical JSON); returns its SHA-256."""
    text = document_text(dict(summary))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def plot_summary(
    rows: Sequence[Mapping[str, Any]],
    fits: Sequence[LogisticFit],
    path: str | os.PathLike[str],
    *,
    title: str,
    threshold_default: str = DEFAULT_CONFIG.threshold_default,
) -> None:
    """Plot `P(same)` per bin (Wilson 95% error bars, catch trials at distance 0) with the
    logistic fit, one panel per profile plus the pooled panel, and a dashed line at the
    default threshold. The image is never committed (restricted storage or CI artifact)."""
    from matplotlib.figure import Figure

    profiles = list(dict.fromkeys(str(r["profile"]) for r in rows))
    fig = Figure(figsize=(3.2 * max(1, len(profiles)), 3.4), layout="constrained")
    axes = fig.subplots(1, max(1, len(profiles)), sharey=True, squeeze=False)[0]
    default = float(parse_threshold(threshold_default))
    by_profile = {f.profile: f for f in fits}
    for ax, profile in zip(axes, profiles, strict=False):
        mine = [r for r in rows if r["profile"] == profile and r["p_same"] is not None]
        for kind, marker, face, label in (
            ("different", "o", "#1f4e79", "P(same) per bin, Wilson 95%"),
            ("same", "s", "none", "identical pairs (catch)"),
        ):
            group = [r for r in mine if r["kind"] == kind]
            xs = [0.0 if r["bin_center"] is None else float(r["bin_center"]) for r in group]
            ys = [float(r["p_same"]) for r in group]
            low = [y - float(r["wilson_low"]) for y, r in zip(ys, group, strict=True)]
            high = [float(r["wilson_high"]) - y for y, r in zip(ys, group, strict=True)]
            ax.errorbar(
                xs,
                ys,
                yerr=[low, high],
                fmt=marker,
                capsize=3,
                color="#1f4e79",
                markerfacecolor=face,
                label=label,
            )
        fit = by_profile.get(profile)
        if fit is not None and fit.status == "ok":
            assert fit.intercept is not None and fit.slope is not None
            top = max([*(float(r["bin_center"] or 0) for r in mine), default]) * 1.1
            grid = [top * i / 100 for i in range(101)]
            curve = [_expit(fit.intercept + fit.slope * g) for g in grid]
            ax.plot(grid, curve, color="#c55a11", label="logistic fit")
        ax.axvline(default, linestyle="--", color="#7f7f7f", linewidth=1, label="default threshold")
        ax.set_title(f"{profile} (fit: {fit.status if fit else 'none'})", fontsize=9)
        ax.set_xlabel("12-feature distance")
        ax.set_ylim(-0.02, 1.02)
    axes[0].legend(fontsize=7, loc="lower left")
    axes[0].set_ylabel("P(same)")
    fig.suptitle(title, fontsize=9)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    target = os.fspath(path)
    if target.lower().endswith(".png"):
        fig.savefig(target, metadata={"Software": None})
    else:
        fig.savefig(target)
