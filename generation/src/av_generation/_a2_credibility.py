"""A2 credibility check on synthetic scores (#18; Study A protocol §3.5 development check).

The protocol asks, during development, whether the A2 search is a credible baseline. This
module compares A2 with plain uniform sampling (the same 12-slot budget, every slot an
independent uniform sample) on synthetic books scored by a synthetic three-rater panel.
Everything is DEMO data: no study material, no real ratings.

Both methods use the real proposal code (`a2.plan_slot`, `a2.select_parent`), the real
validator against the book's committed references, and the protocol's selector rule
(eligible = valid and at least two acceptable comfort ratings; score = mean over raters
of (association + distinguishability) / 2; the highest score so far wins, ties go to the
lowest submission slot). Common random numbers keep the comparison paired: the uniform
baseline draws from A2's own per-slot seed keys (so round 1 of an atom is identical when
the committed books agree), and the synthetic raters use one stream per book, atom, round
and slot, shared by both methods.

Synthetic panel (an assumption for this check, not a perceptual model):

- each atom has a hidden target point, the features of a uniform recipe; association
  falls linearly from 7 at the target to 1 at feature distance `association_range`;
- distinguishability rises linearly from 1 at distance 0 to 7 at
  `distinguishability_range` from the nearest committed reference of the book; it is
  fixed at 4 while the book has no committed reference (the protocol's first-atom rule);
- each rater adds independent normal noise (`noise_sd`) and reports an integer 1..7
  (`integer_ratings=False` keeps the clamped real value: a diagnostic, not the protocol);
- comfort is unacceptable with probability `p_unacceptable` plus `p_unacceptable_high`
  times the fraction of events pitched at +3 or higher.

An atom without an eligible candidate after round 4 counts as a fallback and commits
nothing (the real fallback bank is out of scope here); later atoms are checked against the
atoms committed so far. The outcome measure is the noise-free score of each committed atom
("true" score), compared per book (A2 minus uniform, paired t interval).

Run from the repository root. Each scenario's `summary.json` and `books.csv` go to the
ignored `generation/out/` directory and are never committed. Only the grid summary
(`grid.json`, DEMO data) is committed, as `generation/runs/DEMO-a2-credibility/grid.json`:
copy it there after a deliberate change, and update the table in
`generation/docs/a2-search.md`.

    uv run --project generation python -m av_generation._a2_credibility --grid \\
        --out generation/out/ci/a2-credibility
    cp generation/out/ci/a2-credibility/grid.json \\
        generation/runs/DEMO-a2-credibility/grid.json

The whole grid takes minutes, so CI recomputes only part of the committed file exactly:
the `ci_check` entry (`ci_check_config`), two first-atom scenarios and the centrality
drift. It also checks the documented table against the file.
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from fractions import Fraction
from pathlib import Path
from typing import Any, Final, Literal, NamedTuple

from av_sound.features import Features, distance, features
from av_sound.grammar import ATOM_IDS
from av_sound.recipe import Profile, Recipe
from av_sound.validate import Reference, validate
from scipy import stats

from av_generation.a2 import A2_ALGORITHM, mutate, plan_slot, sample_uniform, select_parent
from av_generation.constants import (
    MIN_ACCEPTABLE_COMFORT,
    RATERS_PER_PANEL,
    ROUNDS_PER_ATOM,
    SLOTS_PER_ROUND,
)
from av_generation.ids import proposal_slot_id, slot_index
from av_generation.jsonio import canonical_sha256, document_text
from av_generation.outcomes import SlotOutcome, outcome_from_validation
from av_generation.proposers import AtomFeedback, CandidateFeedback
from av_generation.seeds import a2_seed_key, bot_seed_key, rng_for

Method = Literal["A2", "uniform"]
METHODS: Final[tuple[Method, ...]] = ("A2", "uniform")
DEFAULT_OUT: Final = Path("generation") / "out" / "ci" / "a2-credibility"
BOOK_COLUMNS: Final[tuple[str, ...]] = (
    "book_id",
    "profile",
    "method",
    "atoms",
    "fallbacks",
    "slots_valid",
    "slots_eligible",
    "mean_true_score",
    "mean_panel_score",
)


@dataclass(frozen=True, slots=True)
class SyntheticPanel:
    """Parameters of the synthetic raters (see the module docstring)."""

    run_id: str = "DEMO-a2-credibility"
    noise_sd: float = 1.0
    integer_ratings: bool = True
    association_range: float = 0.5
    distinguishability_range: float = 0.4
    p_unacceptable: float = 0.1
    p_unacceptable_high: float = 0.3


@dataclass(frozen=True, slots=True)
class CredibilityConfig:
    """One scenario: books per profile, atoms per book, the panel and the threshold."""

    books_per_profile: int = 40
    atoms: int = 16
    namespace: str = "DEMO-CRED"
    threshold: str = "0.10"
    panel: SyntheticPanel = field(default_factory=SyntheticPanel)

    def book_ids(self) -> tuple[tuple[Profile, str], ...]:
        """`(profile, book_id)` pairs; the book ID is also the A2 seed namespace."""
        return tuple(
            (profile, f"{self.namespace}-{profile.value}-{i:03d}")
            for profile in Profile
            for i in range(self.books_per_profile)
        )

    def describe(self) -> dict[str, Any]:
        p = self.panel
        return {
            "books_per_profile": self.books_per_profile,
            "atoms": self.atoms,
            "namespace": self.namespace,
            "threshold": self.threshold,
            "panel": {
                "run_id": p.run_id,
                "noise_sd": p.noise_sd,
                "integer_ratings": p.integer_ratings,
                "association_range": p.association_range,
                "distinguishability_range": p.distinguishability_range,
                "p_unacceptable": p.p_unacceptable,
                "p_unacceptable_high": p.p_unacceptable_high,
            },
        }


class SyntheticRating(NamedTuple):
    association: float
    distinguishability: float
    acceptable: bool


@dataclass(frozen=True, slots=True)
class AtomOutcome:
    """One atom of one synthetic book under one method."""

    atom_id: str
    fallback: bool
    true_score: float | None
    """Noise-free score of the committed candidate; `None` for a fallback."""
    true_association: float | None
    true_distinguishability: float | None
    panel_score: Fraction | None
    slots_valid: int
    slots_eligible: int
    true_by_round: tuple[float | None, ...]
    """Noise-free score of the incumbent after rounds 1..4 (`None`: no incumbent yet)."""


@dataclass(frozen=True, slots=True)
class BookOutcome:
    book_id: str
    profile: Profile
    method: Method
    atoms: tuple[AtomOutcome, ...]

    @property
    def fallbacks(self) -> int:
        return sum(a.fallback for a in self.atoms)

    @property
    def mean_true_score(self) -> float | None:
        return _mean([a.true_score for a in self.atoms if a.true_score is not None])

    @property
    def mean_panel_score(self) -> float | None:
        return _mean([float(a.panel_score) for a in self.atoms if a.panel_score is not None])

    def row(self) -> dict[str, Any]:
        return {
            "book_id": self.book_id,
            "profile": self.profile.value,
            "method": self.method,
            "atoms": len(self.atoms),
            "fallbacks": self.fallbacks,
            "slots_valid": sum(a.slots_valid for a in self.atoms),
            "slots_eligible": sum(a.slots_eligible for a in self.atoms),
            "mean_true_score": _round(self.mean_true_score),
            "mean_panel_score": _round(self.mean_panel_score),
        }


def _mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


# ---------------------------------------------------------------------------
# Synthetic panel


def true_scores(
    candidate: Features,
    target: Features,
    references: Sequence[Features],
    panel: SyntheticPanel,
) -> tuple[float, float]:
    """Noise-free `(association, distinguishability)` of a candidate."""
    closeness = 1.0 - distance(candidate, target) / panel.association_range
    association = 1.0 + 6.0 * max(0.0, closeness)
    if not references:
        return association, 4.0
    nearest = min(distance(candidate, ref) for ref in references)
    return association, 1.0 + 6.0 * min(1.0, nearest / panel.distinguishability_range)


def rate(
    recipe: Recipe,
    association: float,
    distinguishability: float,
    first_atom: bool,
    seed_key: str,
    panel: SyntheticPanel,
) -> tuple[SyntheticRating, ...]:
    """Three synthetic raters for one valid candidate (one stream per slot)."""
    rng = rng_for(seed_key)
    high = sum(p >= 3 for p in recipe.pitches) / len(recipe.pitches)
    p_bad = panel.p_unacceptable + panel.p_unacceptable_high * high

    def report(value: float) -> float:
        clamped = min(7.0, max(1.0, value))
        return float(min(7, max(1, round(value)))) if panel.integer_ratings else clamped

    out = []
    for _ in range(RATERS_PER_PANEL):
        a = report(association + panel.noise_sd * float(rng.normal()))
        d_noisy = report(distinguishability + panel.noise_sd * float(rng.normal()))
        out.append(SyntheticRating(a, 4.0 if first_atom else d_noisy, float(rng.random()) >= p_bad))
    return tuple(out)


def panel_score(ratings: Sequence[SyntheticRating]) -> tuple[bool, Fraction]:
    """`(eligible by comfort, score)` under the protocol's selector rule (exact)."""
    acceptable = sum(r.acceptable for r in ratings)
    total = sum(
        (Fraction(r.association) + Fraction(r.distinguishability) for r in ratings), Fraction(0)
    )
    return acceptable >= MIN_ACCEPTABLE_COMFORT, total / (2 * len(ratings))


# ---------------------------------------------------------------------------
# Simulation


def _incumbent(candidates: Sequence[CandidateFeedback]) -> CandidateFeedback | None:
    eligible = [c for c in candidates if c.eligible is True and c.score is not None]
    if not eligible:
        return None
    return min(eligible, key=lambda c: (-(c.score or Fraction(0)), c.slot_index))


def simulate_book(
    profile: Profile, book_id: str, method: Method, config: CredibilityConfig
) -> BookOutcome:
    """Build one synthetic book with `method` (A2 or uniform sampling)."""
    panel = config.panel
    references: list[Reference] = []
    atoms: list[AtomOutcome] = []
    for atom_id in ATOM_IDS[: config.atoms]:
        target_key = bot_seed_key(panel.run_id, "panel", "target", book_id, atom_id)
        target = features(sample_uniform(rng_for(target_key)))
        ref_features = [r.features for r in references]
        candidates: list[CandidateFeedback] = []
        truth: dict[str, tuple[float, float]] = {}
        pcm: dict[str, str] = {}
        by_round: list[float | None] = []
        valid = eligible_count = 0
        for round_ in range(1, ROUNDS_PER_ATOM + 1):
            incumbent = _incumbent(candidates)
            feedback = AtomFeedback(
                book_id,
                atom_id,
                round_ - 1,
                tuple(candidates),
                None if incumbent is None else incumbent.slot_id,
                None if incumbent is None else incumbent.score,
            )
            parent = select_parent(feedback) if method == "A2" else None
            for slot in range(1, SLOTS_PER_ROUND + 1):
                recipe, _ = plan_slot(a2_seed_key(book_id, atom_id, round_, slot), slot, parent)
                result = validate(recipe, profile, references, threshold=config.threshold)
                outcome = outcome_from_validation(result)
                slot_id = proposal_slot_id(book_id, atom_id, round_, slot)
                eligible = False
                score: Fraction | None = None
                if outcome is SlotOutcome.VALID:
                    valid += 1
                    assoc, dist = true_scores(features(recipe), target, ref_features, panel)
                    truth[slot_id] = (assoc, dist)
                    assert result.pcm_sha256 is not None
                    pcm[slot_id] = result.pcm_sha256
                    rating_key = bot_seed_key(
                        panel.run_id, "panel", "rating", book_id, atom_id, round_, slot
                    )
                    ratings = rate(recipe, assoc, dist, not references, rating_key, panel)
                    eligible, score = panel_score(ratings)
                    eligible_count += eligible
                candidates.append(
                    CandidateFeedback(
                        slot_id,
                        round_,
                        slot,
                        slot_index(round_, slot),
                        recipe,
                        outcome,
                        result.codes,
                        (),
                        eligible,
                        score,
                    )
                )
            best = _incumbent(candidates)
            by_round.append(None if best is None else sum(truth[best.slot_id]) / 2)
        best = _incumbent(candidates)
        if best is not None and best.recipe is not None:
            references.append(Reference(atom_id, best.recipe, pcm[best.slot_id], profile))
        components = None if best is None else truth[best.slot_id]
        atoms.append(
            AtomOutcome(
                atom_id,
                best is None,
                None if components is None else sum(components) / 2,
                None if components is None else components[0],
                None if components is None else components[1],
                None if best is None else best.score,
                valid,
                eligible_count,
                tuple(by_round),
            )
        )
    return BookOutcome(book_id, profile, method, tuple(atoms))


def _paired(diffs: Sequence[float]) -> dict[str, Any]:
    n = len(diffs)
    if n < 2:
        return {"books": n, "mean": _round(_mean(diffs)), "sd": None, "ci95": None}
    mean, sd = statistics.fmean(diffs), statistics.stdev(diffs)
    half = float(stats.t.ppf(0.975, n - 1)) * sd / math.sqrt(n)
    return {
        "books": n,
        "mean": _round(mean),
        "sd": _round(sd),
        "ci95": [_round(mean - half), _round(mean + half)],
        "a2_better_books": sum(d > 0 for d in diffs),
        "a2_worse_books": sum(d < 0 for d in diffs),
    }


def summarize(books: Sequence[BookOutcome], config: CredibilityConfig) -> dict[str, Any]:
    """Per-method means and the paired per-book comparison (A2 minus uniform)."""
    methods: dict[str, Any] = {}
    for method in METHODS:
        atoms = [a for b in books if b.method == method for a in b.atoms]
        n_slots = len(atoms) * ROUNDS_PER_ATOM * SLOTS_PER_ROUND
        committed = [a for a in atoms if not a.fallback]
        methods[method] = {
            "atoms": len(atoms),
            "fallback_rate": _round(sum(a.fallback for a in atoms) / len(atoms)),
            "valid_slot_rate": _round(sum(a.slots_valid for a in atoms) / n_slots),
            "eligible_slot_rate": _round(sum(a.slots_eligible for a in atoms) / n_slots),
            "mean_true_score": _round(_mean([a.true_score or 0.0 for a in committed])),
            "mean_true_association": _round(_mean([a.true_association or 0.0 for a in committed])),
            "mean_true_distinguishability": _round(
                _mean([a.true_distinguishability or 0.0 for a in committed])
            ),
            "mean_panel_score": _round(_mean([float(a.panel_score or 0) for a in committed])),
            "mean_true_score_by_round": [
                _round(_mean([s for a in atoms if (s := a.true_by_round[r]) is not None]))
                for r in range(ROUNDS_PER_ATOM)
            ],
        }
    pairs = {(b.book_id, b.method): b for b in books}
    diffs: list[float] = []
    fallback_diffs: list[int] = []
    for _profile, book_id in config.book_ids():
        a2, uni = pairs[(book_id, "A2")], pairs[(book_id, "uniform")]
        fallback_diffs.append(a2.fallbacks - uni.fallbacks)
        if a2.mean_true_score is not None and uni.mean_true_score is not None:
            diffs.append(a2.mean_true_score - uni.mean_true_score)
    return {
        "check": "A2 vs uniform sampling on synthetic scores (DEMO)",
        "a2_algorithm": A2_ALGORITHM,
        "config": config.describe(),
        "methods": methods,
        "paired_true_score_difference": _paired(diffs),
        "mean_fallback_difference_per_book": _round(_mean(fallback_diffs)),
    }


def run(config: CredibilityConfig) -> tuple[dict[str, Any], tuple[BookOutcome, ...]]:
    """Simulate every book under both methods; returns the summary and the books."""
    books = tuple(
        simulate_book(profile, book_id, method, config)
        for profile, book_id in config.book_ids()
        for method in METHODS
    )
    summary = summarize(books, config)
    summary["books_sha256"] = canonical_sha256([b.row() for b in books])
    return summary, books


GRID: Final[tuple[tuple[str, int, float, bool], ...]] = (
    ("book-int-noise1", 16, 1.0, True),
    ("book-int-noise0", 16, 0.0, True),
    ("book-real-noise1", 16, 1.0, False),
    ("book-real-noise0", 16, 0.0, False),
    ("atom1-int-noise1", 1, 1.0, True),
    ("atom1-int-noise0", 1, 0.0, True),
    ("atom1-real-noise1", 1, 1.0, False),
    ("atom1-real-noise0", 1, 0.0, False),
)
"""The recorded scenarios: whole books (16 atoms) or the first atom only; integer (protocol)
or real-valued ratings; rater noise SD 1 or 0. The first scenario is the main one."""


def _centrality(recipe: Recipe) -> float:
    return statistics.fmean(abs(float(x) - 0.5) for x in features(recipe))


def centrality_drift(
    samples: int = 2000, generations: Sequence[int] = (1, 3, 9), run_id: str = "DEMO-a2-drift"
) -> dict[str, float | None]:
    """Mean |feature - 0.5| of uniform recipes, and of the same recipes after `g` unselected
    A2 mutations (child sizes cycling 1, 2, 3). Reflection at the ends of each value list
    favours interior values, so the value falls as mutations accumulate (a diagnostic)."""
    start = [
        sample_uniform(rng_for(bot_seed_key(run_id, "drift", "start", i))) for i in range(samples)
    ]
    out: dict[str, float | None] = {"uniform": _round(_mean([_centrality(r) for r in start]))}
    for g in generations:
        values = []
        for i, recipe in enumerate(start):
            rng = rng_for(bot_seed_key(run_id, "drift", "walk", i))
            for step in range(g):
                recipe, _ = mutate(recipe, 1 + step % 3, rng)
            values.append(_centrality(recipe))
        out[f"after_{g}_mutations"] = _round(_mean(values))
    return out


def grid_configs(books_per_profile: int) -> tuple[tuple[str, CredibilityConfig], ...]:
    base = CredibilityConfig(books_per_profile=books_per_profile)
    return tuple(
        (
            name,
            replace(
                base,
                atoms=atoms,
                panel=replace(base.panel, noise_sd=noise, integer_ratings=integer),
            ),
        )
        for name, atoms, noise, integer in GRID
    )


CI_CHECK_BOOKS_PER_PROFILE: Final = 2


def ci_check_config() -> CredibilityConfig:
    """The main scenario (first `GRID` row) on its first two books per profile.

    Book IDs do not depend on the number of books, and books are simulated independently,
    so these are the first books of the full main run. `--grid` records this run as
    `ci_check` in `grid.json`; CI recomputes it in seconds, which covers the book-level
    path (committed references, separation threshold) that first-atom scenarios skip.
    """
    return grid_configs(CI_CHECK_BOOKS_PER_PROFILE)[0][1]


def write_outputs(out_dir: Path, summary: dict[str, Any], books: Sequence[BookOutcome]) -> None:
    """`summary.json` and `books.csv` (UTF-8, `\\n` line ends)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(document_text(summary), encoding="utf-8", newline="\n")
    with (out_dir / "books.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(BOOK_COLUMNS)
        for book in books:
            row = book.row()
            writer.writerow(["" if row[c] is None else row[c] for c in BOOK_COLUMNS])


def markdown_row(name: str, summary: dict[str, Any]) -> str:
    """One table row: scenario, final scores, rounds 1 and 4, paired difference."""
    a2, uni = summary["methods"]["A2"], summary["methods"]["uniform"]
    paired = summary["paired_true_score_difference"]
    ci = paired["ci95"] or [None, None]
    return (
        f"| {name} | {a2['mean_true_score']} | {uni['mean_true_score']} "
        f"| {a2['mean_true_score_by_round'][0]} -> {a2['mean_true_score_by_round'][-1]} "
        f"| {uni['mean_true_score_by_round'][0]} -> {uni['mean_true_score_by_round'][-1]} "
        f"| {paired['mean']} [{ci[0]}, {ci[1]}] "
        f"| {paired.get('a2_better_books')}/{paired.get('a2_worse_books')} "
        f"| {a2['fallback_rate']} / {uni['fallback_rate']} |"
    )


MARKDOWN_HEADER: Final = (
    "| Scenario | A2 score | Uniform score | A2 rounds 1->4 | Uniform rounds 1->4 "
    "| A2 - uniform [95% CI] | Books A2 better/worse | Fallback rate A2 / uniform |\n"
    "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m av_generation._a2_credibility",
        description="A2 vs uniform sampling on synthetic scores (DEMO).",
    )
    parser.add_argument("--books-per-profile", type=int, default=40)
    parser.add_argument("--grid", action="store_true", help="run the recorded scenarios")
    parser.add_argument("--atoms", type=int, default=16, choices=range(1, len(ATOM_IDS) + 1))
    parser.add_argument("--noise-sd", type=float, default=1.0)
    parser.add_argument("--real-ratings", action="store_true", help="diagnostic, not protocol")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    if args.books_per_profile < 2:
        parser.error("--books-per-profile must be at least 2")
    if args.grid:
        scenarios = grid_configs(args.books_per_profile)
    else:
        base = CredibilityConfig(books_per_profile=args.books_per_profile, atoms=args.atoms)
        panel = replace(base.panel, noise_sd=args.noise_sd, integer_ratings=not args.real_ratings)
        scenarios = (("single", replace(base, panel=panel)),)
    rows = []
    index: dict[str, Any] = {"scenarios": {}}
    for name, config in scenarios:
        summary, books = run(config)
        write_outputs(args.out / name, summary, books)
        index["scenarios"][name] = summary
        rows.append(markdown_row(name, summary))
    text = MARKDOWN_HEADER + "\n".join(rows) + "\n"
    if args.grid:
        index["ci_check"] = run(ci_check_config())[0]
        index["centrality_drift"] = drift = centrality_drift()
        text += f"\nMean |feature - 0.5| (uniform, then after unselected mutations): {drift}\n"
    (args.out / "grid.json").write_text(document_text(index), encoding="utf-8", newline="\n")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":  # pragma: no cover - command-line entry
    raise SystemExit(main())
