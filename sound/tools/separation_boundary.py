"""Exhaustive separation-boundary analysis and boundary fixtures (issue #9).

For each threshold t it enumerates every achievable value of the exact squared
feature sum S = sum((x_j - y_j)^2) between two domain recipes and reports:

- whether S == 12 * t^2 (distance exactly t) is achievable at all,
- the largest achievable S below it and the smallest above it.

The proportion features depend jointly on the three weights; every other feature
depends on one field. So the achievable set is exactly
{P + R}: P over all 64 x 64 weight pairs, R over the sum of per-coordinate squared
differences of the other nine coordinates. The set includes recipes with short
events, so "not achievable" is a proof for the whole domain.

For every achievable boundary case it then finds a concrete pair of admissible
recipes (no short event, distinct waveforms) and writes them to
`sound/testvectors/validator/boundary.json`, which `tests/sound/` re-verifies.
It prints the boundary table as Markdown.

Usage: uv run --project sound python sound/tools/separation_boundary.py
"""

from __future__ import annotations

import bisect
import itertools
import json
from collections.abc import Iterator
from fractions import Fraction
from pathlib import Path

from av_sound import (
    RENDERER_VERSION,
    VALIDATOR_VERSION,
    Profile,
    Recipe,
    Reference,
    features,
    render,
    sum_squared_diff,
    validate,
)
from av_sound.features import distance_from_sum_sq, parse_threshold
from av_sound.recipe import AMPLITUDES, GAPS_MS, PITCHES, RHYTHM_WEIGHTS, TOTAL_MS

OUT = Path(__file__).resolve().parents[1] / "testvectors" / "validator" / "boundary.json"
THRESHOLDS = ("0.05", "0.10", "0.125", "0.15", "0.20", "0.25")
PROFILE = Profile.P2
SCALE = 138_600  # 5 * lcm(3..12): every feature times SCALE is an integer
BASE = Recipe(900, (0, 0, 0), (1, 1, 1), (20, 20), (0.6, 0.8, 1.0))


def scaled(value: Fraction) -> int:
    v = value * SCALE
    assert v.denominator == 1, value
    return v.numerator


# Per-coordinate value tables, read from features() so the analysis checks the code.
def _coordinate(field: str, values: tuple[object, ...], index: int) -> dict[object, int]:
    out = {}
    for v in values:
        data = BASE.to_dict()
        data[field] = [v] * len(data[field]) if isinstance(data[field], list) else v
        out[v] = scaled(features(Recipe.from_dict(data))[index])
    return out


PITCH = _coordinate("pitches", PITCHES, 0)
TOTAL = _coordinate("total_ms", TOTAL_MS, 3)
GAP = _coordinate("gaps_ms", GAPS_MS, 7)
AMP = _coordinate("amplitudes", AMPLITUDES, 9)
# Nine independent coordinates: 3 pitches, total, 2 gaps, 3 amplitudes.
COORDS: list[dict[object, int]] = [PITCH, PITCH, PITCH, TOTAL, GAP, GAP, AMP, AMP, AMP]
WEIGHTS = list(itertools.product(RHYTHM_WEIGHTS, repeat=3))


def proportion_vector(w: tuple[int, int, int]) -> tuple[int, int, int]:
    f = features(Recipe(900, (0, 0, 0), w, (20, 20), (0.6, 0.8, 1.0)))
    return (scaled(f[4]), scaled(f[5]), scaled(f[6]))


PROPS = {w: proportion_vector(w) for w in WEIGHTS}


def proportion_sums() -> dict[int, list[tuple[tuple[int, ...], tuple[int, ...]]]]:
    out: dict[int, list[tuple[tuple[int, ...], tuple[int, ...]]]] = {}
    for a in WEIGHTS:
        for b in WEIGHTS:
            s = sum((x - y) ** 2 for x, y in zip(PROPS[a], PROPS[b], strict=True))
            out.setdefault(s, []).append((a, b))
    return out


def coord_squares(table: dict[object, int]) -> dict[int, list[tuple[object, object]]]:
    out: dict[int, list[tuple[object, object]]] = {}
    for a, b in itertools.product(sorted(table), repeat=2):
        out.setdefault((table[a] - table[b]) ** 2, []).append((a, b))
    return out


SQUARES = [coord_squares(c) for c in COORDS]


def suffix_sumsets() -> list[set[int]]:
    sets: list[set[int]] = [{0}]
    for sq in reversed(SQUARES):
        sets.append({s + d for s in sets[-1] for d in sq})
    sets.reverse()
    return sets  # sets[k] = achievable sums over coordinates k..8


SUFFIX = suffix_sumsets()


def other_decompositions(r: int, k: int = 0) -> Iterator[tuple[int, ...]]:
    """All ways to write r as one squared difference per coordinate k..8."""
    if k == len(SQUARES):
        if r == 0:
            yield ()
        return
    for d in sorted(SQUARES[k]):
        if r - d in SUFFIX[k + 1]:
            for rest in other_decompositions(r - d, k + 1):
                yield (d, *rest)


def concrete_pairs(p: int, r: int, pairs_p: dict[int, list]) -> Iterator[tuple[Recipe, Recipe]]:
    for wa, wb in pairs_p[p]:
        for dec in other_decompositions(r):
            options = [SQUARES[k][d] for k, d in enumerate(dec)]
            # Prefer long totals first (all structures admissible at 750 and 900 ms).
            options[3] = sorted(options[3], key=lambda ab: (-min(ab), ab))
            for combo in itertools.product(*options):
                (pa, pb), (qa, qb), (ra, rb), (ta, tb), *rest = combo
                (ga, gb), (ha, hb), (aa, ab), (ba, bb), (ca, cb) = rest
                yield (
                    Recipe(ta, (pa, qa, ra), wa, (ga, ha), (aa, ba, ca)),
                    Recipe(tb, (pb, qb, rb), wb, (gb, hb), (ab, bb, cb)),
                )


def admissible_pair(p: int, r: int, pairs_p: dict[int, list]) -> tuple[Recipe, Recipe]:
    for a, b in concrete_pairs(p, r, pairs_p):
        ra, rb = render(a, PROFILE), render(b, PROFILE)
        if not (ra.short_event or rb.short_event) and ra.pcm_sha256 != rb.pcm_sha256:
            return a, b
    raise LookupError(f"no admissible pair for P={p}, R={r}")


def analyse() -> dict[str, object]:
    pairs_p = proportion_sums()
    p_values = sorted(pairs_p)
    r_values = sorted(SUFFIX[0])
    analysis = []
    fixtures = []
    for text in THRESHOLDS:
        t = parse_threshold(text)
        target = 12 * t * t * SCALE * SCALE  # exact; integer when it can be achieved
        exact: tuple[int, int] | None = None
        below: tuple[int, int, int] | None = None
        above: tuple[int, int, int] | None = None
        for p in p_values:
            i = bisect.bisect_left(r_values, target - p)
            if i < len(r_values) and p + r_values[i] == target and exact is None:
                exact = (p, r_values[i])
            j = i - 1
            if (
                j >= 0
                and p + r_values[j] < target
                and (below is None or p + r_values[j] > below[0])
            ):
                below = (p + r_values[j], p, r_values[j])
            k = i + 1 if i < len(r_values) and p + r_values[i] == target else i
            if k < len(r_values) and (above is None or p + r_values[k] < above[0]):
                above = (p + r_values[k], p, r_values[k])
        assert below is not None and above is not None
        norm = SCALE * SCALE
        analysis.append(
            {
                "threshold": text,
                "limit_sum_sq": str(Fraction(12) * t * t),
                "exact_achievable": exact is not None,
                "below_sum_sq": str(Fraction(below[0], norm)),
                "above_sum_sq": str(Fraction(above[0], norm)),
            }
        )
        cases = [("below", below[1], below[2]), ("above", above[1], above[2])]
        if exact is not None:
            cases.insert(1, ("exact", exact[0], exact[1]))
        for relation, p, r in cases:
            ref, cand = admissible_pair(p, r, pairs_p)
            s = sum_squared_diff(cand, ref)
            assert s * norm == p + r
            reference = Reference.from_rendered("REF", render(ref, PROFILE))
            result = validate(cand, PROFILE, [reference], reserved=(), threshold=text)
            fixtures.append(
                {
                    "name": f"t{text}-{relation}",
                    "threshold": text,
                    "relation": relation,
                    "profile": PROFILE.value,
                    "reference": ref.to_dict(),
                    "candidate": cand.to_dict(),
                    "sum_sq": str(s),
                    "distance": distance_from_sum_sq(s),
                    "separated": relation != "below",
                    "codes": list(result.codes),
                }
            )
    return {
        "synthetic": True,
        "renderer_version": RENDERER_VERSION,
        "validator_version": VALIDATOR_VERSION,
        "analysis": analysis,
        "pairs": fixtures,
    }


def _fmt_features(recipe: Recipe) -> str:
    return "(" + ", ".join(str(f) for f in features(recipe)) + ")"


def _fmt_recipe(d: dict[str, object]) -> str:
    return Recipe.from_dict(d).canonical_json().replace('"', "")


def markdown(doc: dict[str, object]) -> str:
    lines = [
        "| threshold | 12 t^2 | exact achievable | largest S below | smallest S above |",
        "| --- | --- | --- | --- | --- |",
    ]
    for a in doc["analysis"]:
        below = Fraction(a["below_sum_sq"])
        above = Fraction(a["above_sum_sq"])
        lines.append(
            f"| {a['threshold']} | {a['limit_sum_sq']} | {'yes' if a['exact_achievable'] else 'no'}"
            f" | {below} (d = {distance_from_sum_sq(below):.7f})"
            f" | {above} (d = {distance_from_sum_sq(above):.7f}) |"
        )
    lines += [
        "",
        "| fixture | reference / candidate recipe | features (exact) | S | distance | codes |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for f in doc["pairs"]:
        ref, cand = Recipe.from_dict(f["reference"]), Recipe.from_dict(f["candidate"])
        lines.append(
            f"| {f['name']} | ref `{_fmt_recipe(f['reference'])}`<br>cand "
            f"`{_fmt_recipe(f['candidate'])}` | ref {_fmt_features(ref)}<br>cand "
            f"{_fmt_features(cand)} | {f['sum_sq']} | {f['distance']:.7f} | "
            f"{', '.join(f['codes']) or 'ok'} |"
        )
    return "\n".join(lines)


def main() -> None:
    doc = analyse()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    print(markdown(doc))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
