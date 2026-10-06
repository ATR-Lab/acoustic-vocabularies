"""Separation-threshold listening tool (#23): stimuli, session plans, checks, CSV, summary.

Acceptance checks of #23 covered here:

- every generated pair passes the validator (all rules except separation) and lies in its
  bin: `test_every_pair_is_valid_and_in_its_bin` (all 224 pairs of the committed DEMO set)
  and `test_generated_sets_pass_every_check` (property test over set IDs and configs);
- the set covers 7 bins x 3 profiles with the configured counts: `test_coverage_*`;
- the same seed regenerates an identical set (hash match): `test_demo_set_regenerates_*`;
- the summary reproduces hand-computed proportions on a fixture:
  `test_summary_reproduces_hand_computed_fixture`.

Play-once logging is in `test_threshold_runner.py`.
"""

import dataclasses
import hashlib
import json
import math
import os
import shutil
from fractions import Fraction
from pathlib import Path

import pytest
from av_sound.features import distance_from_sum_sq, sum_squared_diff
from av_sound.recipe import Recipe
from av_sound.validate import validate
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from av_generation import threshold as th
from av_generation.jsonio import document_text
from av_generation.records import (
    ThresholdConfig,
    ThresholdPlannedTrial,
    ThresholdStimulusSet,
)
from av_generation.seeds import seed_from_key

ROOT = Path(__file__).resolve().parents[2]
DEMO_SET = ROOT / "generation" / "examples" / "threshold" / "demo-stimuli.json"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "threshold"
CI_OUT = ROOT / "generation" / "out" / "ci" / "threshold"

SMALL = ThresholdConfig(
    profiles=("P1", "P3"),
    bin_centers=("0.050", "0.150"),
    bin_halfwidth="0.0125",
    pairs_per_bin=2,
    same_pairs=3,
    gap_ms=500,
    threshold_default="0.10",
)


@pytest.fixture(scope="module")
def demo_set() -> ThresholdStimulusSet:
    return ThresholdStimulusSet.read(DEMO_SET)


@pytest.fixture(scope="module")
def small_set() -> ThresholdStimulusSet:
    return th.generate_stimuli("DEMO-T-small", SMALL)


def _ci_copy(path: Path, name: str) -> None:
    """Keep a synthetic output as a CI artifact (generation/out/ci/, uploaded by CI)."""
    if os.environ.get("CI"):
        CI_OUT.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, CI_OUT / name)


# ---------------------------------------------------------------------------
# Configuration


def test_default_config_is_the_proposed_session():
    config = th.DEFAULT_CONFIG
    assert th.check_config(config) is config
    assert th.n_trials(config) == 3 * 7 * 8 + 56 == 224
    assert config.bin_centers == ("0.050", "0.075", "0.100", "0.125", "0.150", "0.175", "0.200")
    assert (config.bin_halfwidth, config.gap_ms, config.threshold_default) == (
        "0.0125",
        500,
        "0.10",
    )
    assert th.same_pair_profiles(config).count("P1") == 19
    assert th.same_pair_profiles(config).count("P3") == 18
    assert len(th.TRIAL_CSV_COLUMNS) == len(set(th.TRIAL_CSV_COLUMNS))
    assert th.TRIAL_CSV_COLUMNS[:17] == (
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


@pytest.mark.parametrize(
    "change, message",
    [
        ({"profiles": ()}, "profiles"),
        ({"profiles": ("P1", "P1")}, "profiles"),
        ({"profiles": ("P4",)}, "unknown profile"),
        ({"bin_centers": ("0.100", "0.050")}, "ascending"),
        ({"bin_centers": ()}, "ascending"),
        ({"bin_centers": ("0.010",)}, "within"),
        ({"bin_centers": ("0.500",)}, "within"),
        ({"bin_centers": (0.1,)}, "decimal string"),
        ({"bin_halfwidth": "0"}, "> 0"),
        ({"bin_halfwidth": "x"}, "bin_halfwidth"),
        ({"pairs_per_bin": 0}, "pairs_per_bin"),
        ({"same_pairs": -1}, "same_pairs"),
        ({"gap_ms": 6000}, "gap_ms"),
        ({"threshold_default": "-1"}, "threshold_default"),
    ],
)
def test_bad_configs_are_refused(change, message):
    config = dataclasses.replace(th.DEFAULT_CONFIG, **change)
    with pytest.raises(th.ThresholdError, match=message) as err:
        th.check_config(config)
    assert err.value.code == th.E_CONFIG


def test_impossible_bins_fail_the_search(monkeypatch):
    monkeypatch.setattr(th, "MAX_BASES", 2)
    monkeypatch.setattr(th, "MAX_WALKS_PER_BASE", 20)
    config = dataclasses.replace(SMALL, bin_centers=("0.020",), bin_halfwidth="0.0025")
    with pytest.raises(th.ThresholdError, match="no pair found") as err:
        th.generate_stimuli("DEMO-T-none", config)
    assert err.value.code == th.E_SEARCH


def test_load_config(tmp_path):
    path = tmp_path / "config.json"
    data = dataclasses.asdict(SMALL)
    path.write_text(json.dumps(data), encoding="utf-8")
    assert th.load_config(path) == SMALL
    path.write_text(json.dumps({**data, "extra": 1}), encoding="utf-8")
    with pytest.raises(th.ThresholdError, match="unexpected"):
        th.load_config(path)


def test_bins_are_exact_half_open_intervals():
    low, high = th.bin_limits("0.100", "0.0125")
    assert (low, high) == (12 * Fraction(35, 400) ** 2, 12 * Fraction(45, 400) ** 2)
    assert th.in_bin(low, "0.100", "0.0125")
    assert not th.in_bin(high, "0.100", "0.0125")
    assert th.in_bin(high, "0.125", "0.0125")  # a boundary belongs to the upper bin
    assert not th.in_bin(low - Fraction(1, 10**12), "0.100", "0.0125")


def test_set_ids_and_pair_ids():
    assert th.check_set_id("DEMO-T1") == "DEMO-T1"
    for bad in ("x", "DEMO_T1", "-DEMO", 7):
        with pytest.raises(th.ThresholdError):
            th.check_set_id(bad)
    ids = th.expected_pair_ids(SMALL)
    assert ids[:2] == ("P1-0.050-01", "P1-0.050-02")
    assert ids[-3:] == ("P1-same-01", "P3-same-01", "P1-same-02")
    keys = th.expected_seed_keys("DEMO-T9", SMALL)
    assert keys["P1-0.050-01"] == "THRESHOLD|DEMO-T9|pair|P1|0.050|1"
    assert keys["P3-same-01"] == "THRESHOLD|DEMO-T9|same|P3|1"


# ---------------------------------------------------------------------------
# Stimulus set: validity, bins, coverage, reproducibility


def test_demo_set_regenerates_byte_identical(demo_set):
    """Same set ID (seed) and config -> identical set and hash on every OS (CI matrix)."""
    again = th.generate_stimuli("DEMO-T1")
    assert again == demo_set
    assert again.sha256() == demo_set.sha256()
    assert document_text(again.to_dict()).encode("utf-8") == DEMO_SET.read_bytes()
    print(f"DEMO-T1 set sha256 {again.sha256()}")


def test_other_set_ids_give_other_sets(small_set):
    other = th.generate_stimuli("DEMO-T-small-2", SMALL)
    assert other.sha256() != small_set.sha256()
    assert {p.pcm_sha256_a for p in other.pairs}.isdisjoint(
        {p.pcm_sha256_a for p in small_set.pairs}
    )
    assert th.generate_stimuli("DEMO-T-small", SMALL).sha256() == small_set.sha256()


def test_every_pair_is_valid_and_in_its_bin(demo_set):
    """Automated check over every pair: validator OK (every rule but separation, which a
    pair breaks on purpose) and the exact distance inside the bin."""
    config = demo_set.config
    for pair in demo_set.pairs:
        a, b = Recipe.from_dict(pair.recipe_a), Recipe.from_dict(pair.recipe_b)
        for recipe, pcm in ((a, pair.pcm_sha256_a), (b, pair.pcm_sha256_b)):
            result = validate(recipe, pair.profile, (), threshold=config.threshold_default)
            assert result.ok, (pair.pair_id, result.codes)
            assert result.pcm_sha256 == pcm
        total = sum_squared_diff(a, b)
        assert Fraction(pair.sum_sq) == total
        assert pair.distance == distance_from_sum_sq(total)
        if pair.kind == "same":
            assert a == b and total == 0 and pair.bin_center is None
        else:
            assert th.in_bin(total, pair.bin_center, config.bin_halfwidth), pair.pair_id
            center = float(pair.bin_center)
            assert center - 0.0125 <= pair.distance < center + 0.0125
            assert pair.differing
    assert th.check_stimuli(demo_set) == ()


def test_coverage_of_the_default_set(demo_set):
    cover = th.coverage(demo_set)
    for profile in ("P1", "P2", "P3"):
        for center in th.DEFAULT_CONFIG.bin_centers:
            assert cover[(profile, center)] == 8
    assert [cover[(p, "same")] for p in ("P1", "P2", "P3")] == [19, 19, 18]
    assert sum(cover.values()) == len(demo_set.pairs) == 224
    assert tuple(p.pair_id for p in demo_set.pairs) == th.expected_pair_ids(demo_set.config)


def test_pairs_vary_which_features_differ(demo_set):
    differing = {c for p in demo_set.pairs for c in p.differing}
    assert len(differing) == 12
    counts = {len(p.differing) for p in demo_set.pairs if p.kind == "different"}
    assert min(counts) == 1 and max(counts) >= 4


def test_motifs_are_atomic_and_unique(demo_set):
    seen = set()
    for pair in demo_set.pairs:
        assert set(pair.recipe_a) == {
            "total_ms",
            "pitches",
            "rhythm_weights",
            "gaps_ms",
            "amplitudes",
        }
        for pcm in {pair.pcm_sha256_a, pair.pcm_sha256_b}:
            assert (pair.profile, pcm) not in seen
            seen.add((pair.profile, pcm))
    assert demo_set.demo and demo_set.set_id.startswith("DEMO-")


def test_seed_keys_are_stored_per_pair(demo_set):
    keys = th.expected_seed_keys(demo_set.set_id, demo_set.config)
    assert {p.pair_id: p.seed_key for p in demo_set.pairs} == keys
    assert all(p.search_steps >= 1 for p in demo_set.pairs if p.kind == "different")
    assert all(p.search_steps == 0 for p in demo_set.pairs if p.kind == "same")


@settings(
    max_examples=12,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(
    suffix=st.text("ABCDEFGHJKMNPQRSTVWXYZ0123456789", min_size=1, max_size=8),
    profiles=st.lists(st.sampled_from(["P1", "P2", "P3"]), min_size=1, max_size=3, unique=True),
    centers=st.lists(
        st.sampled_from(["0.030", "0.050", "0.075", "0.100", "0.150", "0.200", "0.250"]),
        min_size=1,
        max_size=3,
        unique=True,
    ),
    halfwidth=st.sampled_from(["0.005", "0.0125", "0.02"]),
    pairs=st.integers(1, 2),
    same=st.integers(0, 4),
)
def test_generated_sets_pass_every_check(suffix, profiles, centers, halfwidth, pairs, same):
    config = ThresholdConfig(
        profiles=tuple(profiles),
        bin_centers=tuple(sorted(centers, key=Fraction)),
        bin_halfwidth=halfwidth,
        pairs_per_bin=pairs,
        same_pairs=same,
        gap_ms=500,
        threshold_default="0.10",
    )
    stimuli = th.generate_stimuli(f"DEMO-{suffix}", config)
    assert th.check_stimuli(stimuli) == ()
    assert len(stimuli.pairs) == th.n_trials(config)
    assert th.generate_stimuli(f"DEMO-{suffix}", config).sha256() == stimuli.sha256()


def _tamper(stimuli, index, **change):
    pairs = list(stimuli.pairs)
    pairs[index] = dataclasses.replace(pairs[index], **change)
    return dataclasses.replace(stimuli, pairs=tuple(pairs))


def test_check_stimuli_finds_tampering(small_set):
    first = small_set.pairs[0]
    other_bin = next(p for p in small_set.pairs if p.bin_center == "0.150")
    same = next(p for p in small_set.pairs if p.kind == "same")
    cases = {
        "sum_sq": _tamper(small_set, 0, sum_sq="1/2"),
        "distance": _tamper(small_set, 0, distance=0.5),
        "bin": _tamper(
            small_set, 0, recipe_b=other_bin.recipe_b, pcm_sha256_b=other_bin.pcm_sha256_b
        ),
        "seed key": _tamper(small_set, 0, seed_key="THRESHOLD|DEMO-x|pair|P1|0.050|9"),
        "differing": _tamper(small_set, 0, differing=()),
        "WAV hash": _tamper(small_set, 0, file_sha256_a="0" * 64),
        "PCM hash": _tamper(small_set, 0, pcm_sha256_a="1" * 64),
        "identical": _tamper(small_set, small_set.pairs.index(same), recipe_b=first.recipe_a),
        "motif already used": _tamper(
            small_set, 1, recipe_a=first.recipe_a, pcm_sha256_a=first.pcm_sha256_a
        ),
        "renderer": dataclasses.replace(small_set, renderer_version="9.9.9"),
        "validator": dataclasses.replace(small_set, validator_version="9.9.9"),
        "demo flag": dataclasses.replace(small_set, demo=False),
        "pair IDs": dataclasses.replace(small_set, pairs=small_set.pairs[1:]),
        "fails validation": _tamper(
            small_set,
            0,
            recipe_b={
                **first.recipe_b,
                "total_ms": 450,
                "gaps_ms": [60, 60],
                "rhythm_weights": [1, 4, 4],
            },
        ),
        "config": dataclasses.replace(
            small_set, config=dataclasses.replace(SMALL, bin_centers=("0.150", "0.050"))
        ),
    }
    for expected, stimuli in cases.items():
        problems = th.check_stimuli(stimuli)
        assert problems, expected
        text = " ".join(problems).lower()
        key = {"sum_sq": "sum_sq", "bin": "outside bin", "pcm hash": "pcm hash"}.get(
            expected, expected.lower()
        )
        assert key.lower() in text, (expected, problems)
    schema_broken = _tamper(small_set, 0, pair_id="bad id!")
    assert th.check_stimuli(schema_broken)[0].startswith("schema:")


def test_write_stimuli_refuses_real_sets_in_the_repository(tmp_path, small_set):
    real = th.generate_stimuli("TH-2026-01", dataclasses.replace(SMALL, same_pairs=0))
    assert not real.demo
    with pytest.raises(th.ThresholdError) as err:
        th.write_stimuli(real, ROOT / "generation" / "out" / "never.json")
    assert err.value.code == th.E_STIMULI
    sha = th.write_stimuli(real, tmp_path / "real" / "stimuli.json")
    assert sha == hashlib.sha256((tmp_path / "real" / "stimuli.json").read_bytes()).hexdigest()
    assert ThresholdStimulusSet.read(tmp_path / "real" / "stimuli.json") == real
    th.write_stimuli(small_set, tmp_path / "demo.json")


def test_real_listener_data_is_refused_in_the_repository(tmp_path, demo_set):
    trials = th.read_trials_csv(FIXTURES / "hand-trials.csv")
    assert th.is_demo_data(trials, demo_set) and th.is_demo_data(trials)
    real_trials = [dataclasses.replace(t, run_id="TH-run-1") for t in trials]
    assert not th.is_demo_data(real_trials)
    assert not th.is_demo_data([], dataclasses.replace(demo_set, set_id="TH-2026-01", demo=False))
    target = ROOT / "generation" / "out" / "never-threshold-trials"
    with pytest.raises(th.ThresholdError) as err:
        th.export_csv(real_trials, demo_set, target / "trials.csv")
    assert err.value.code == th.E_POLICY and not target.exists()
    with pytest.raises(th.ThresholdError, match="restricted storage"):
        th.check_output_dir(ROOT, demo=False)
    th.check_output_dir(ROOT, demo=True)
    th.check_output_dir(tmp_path, demo=False)
    th.export_csv(real_trials, demo_set, tmp_path / "trials.csv")


# ---------------------------------------------------------------------------
# Session plans


def _plan(stimuli, session_id="DEMO-S01", **kw):
    args = {
        "listener_id": "L01",
        "station": "S1",
        "gain_db": -12.0,
        "tryout": False,
        "created_utc": "2026-11-23T09:00:00.000Z",
    }
    return th.plan_session(stimuli, session_id, **{**args, **kw})


DEMO_S01_PLAN_SHA256 = "1018b96ff346fcc2e95df1ca4d4e5ce61b59159b9395c3a9ffea3a6acb8efd8d"
"""SHA-256 of the DEMO-S01 plan of set DEMO-T1, one `<trial_index> <pair_id> <order>\\n`
line per trial. Pinned so that a change in how `plan_session` draws from its seeded
stream (or a numpy upgrade that changes the stream) fails on every OS: stored session
documents could no longer be checked or resumed."""

DEMO_S01_PLAN_HEAD = [
    ("P1-0.125-01", "BA"),
    ("P2-0.125-03", "AB"),
    ("P3-0.100-05", "AB"),
    ("P1-0.075-04", "AB"),
    ("P3-0.125-03", "AB"),
    ("P2-same-06", "BA"),
    ("P3-0.200-07", "BA"),
    ("P1-0.125-05", "AB"),
    ("P1-same-11", "BA"),
    ("P2-0.100-03", "BA"),
]


def test_session_plan_is_seeded_complete_and_balanced(demo_set):
    session = _plan(demo_set)
    assert session.order_seed_key == "THRESHOLD|DEMO-T1|order|DEMO-S01"
    assert session.order_seed == seed_from_key(session.order_seed_key) == 7307076573463306863
    # the seeded order and A/B labels are pinned (not only compared with a fresh plan)
    assert [(t.pair_id, t.order) for t in session.plan[:10]] == DEMO_S01_PLAN_HEAD
    text = "".join(f"{t.trial_index} {t.pair_id} {t.order}\n" for t in session.plan)
    assert hashlib.sha256(text.encode("ascii")).hexdigest() == DEMO_S01_PLAN_SHA256
    assert session.set_sha256 == demo_set.sha256()
    assert session.ab_order_rule == "balanced_per_bin"
    assert [t.trial_index for t in session.plan] == list(range(1, 225))
    assert sorted(t.pair_id for t in session.plan) == sorted(p.pair_id for p in demo_set.pairs)
    assert _plan(demo_set) == session
    assert _plan(demo_set, "DEMO-S02").plan != session.plan
    by_id = {p.pair_id: p for p in demo_set.pairs}
    balance = {}
    for trial in session.plan:
        pair = by_id[trial.pair_id]
        key = (pair.profile, pair.kind, pair.bin_center)
        balance[key] = balance.get(key, 0) + (1 if trial.order == "AB" else -1)
    assert all(abs(v) <= 1 for v in balance.values())
    assert sum(1 for v in balance.values() if v == 0) >= 21  # every even group exactly half
    assert th.check_session(session, demo_set) == ()
    # the order is not the set order
    assert [t.pair_id for t in session.plan] != [p.pair_id for p in demo_set.pairs]


def test_check_session_finds_problems(small_set):
    session = _plan(small_set)
    swapped = dataclasses.replace(
        session,
        plan=(
            dataclasses.replace(session.plan[0], pair_id=session.plan[1].pair_id),
            *session.plan[1:],
        ),
    )
    assert any("exactly once" in p for p in th.check_session(swapped, small_set))
    flipped = dataclasses.replace(
        session,
        plan=tuple(dataclasses.replace(t, order="AB") for t in session.plan),
    )
    assert any("unbalanced" in p for p in th.check_session(flipped, small_set))
    reordered = dataclasses.replace(
        session,
        plan=tuple(
            ThresholdPlannedTrial(i + 1, t.pair_id, t.order)
            for i, t in enumerate(reversed(session.plan))
        ),
    )
    assert th.check_session(reordered, small_set) == (
        "the plan is not the seeded plan of this session",
    )
    other = th.generate_stimuli("DEMO-T-other", SMALL)
    assert any("stimulus set" in p for p in th.check_session(session, other))
    wrong_seed = dataclasses.replace(session, order_seed=1)
    assert any("seed" in p for p in th.check_session(wrong_seed, small_set))
    reindexed = dataclasses.replace(
        session,
        plan=tuple(dataclasses.replace(t, trial_index=t.trial_index + 1) for t in session.plan),
    )
    assert any("1..N" in p for p in th.check_session(reindexed, small_set))


def test_plan_session_refuses_bad_input(small_set):
    with pytest.raises(th.ThresholdError):
        _plan(small_set, "x")
    with pytest.raises(th.ThresholdError, match="schema"):
        _plan(small_set, station="station-1")
    with pytest.raises(th.ThresholdError, match="schema"):
        _plan(small_set, listener_id="Jane Doe")


def test_presentations_follow_the_order(small_set):
    session = _plan(small_set)
    shown = th.session_presentations(session, small_set)
    for planned, item in zip(session.plan, shown, strict=True):
        pair = item.pair
        if planned.order == "AB":
            assert (item.recipe_first, item.file_sha256_second) == (
                pair.recipe_a,
                pair.file_sha256_b,
            )
        else:
            assert (item.recipe_first, item.file_sha256_second) == (
                pair.recipe_b,
                pair.file_sha256_a,
            )
    broken = dataclasses.replace(
        session, plan=(dataclasses.replace(session.plan[0], pair_id="P9-x-01"), *session.plan[1:])
    )
    with pytest.raises(th.ThresholdError, match="unknown pair"):
        th.session_presentations(broken, small_set)
    assert th.trial_id("DEMO-S01", 7) == "DEMO-S01.t007"


# ---------------------------------------------------------------------------
# Summary: hand-computed fixture, Wilson intervals, logistic fit


def test_summary_reproduces_hand_computed_fixture(tmp_path):
    """`hand-summary.csv` was computed by hand (Decimal arithmetic, independent code) from
    `hand-trials.csv`: 15 DEMO trials, one unanswered."""
    trials = th.read_trials_csv(FIXTURES / "hand-trials.csv")
    rows = th.summarize(trials)
    th.write_summary_csv(rows, tmp_path / "summary.csv")
    assert (tmp_path / "summary.csv").read_bytes() == (FIXTURES / "hand-summary.csv").read_bytes()
    table = {(r["profile"], r["bin_center"]): r for r in rows}
    hand = {
        ("P1", None): (3, 2),
        ("P1", "0.050"): (4, 3),
        ("P1", "0.100"): (4, 1),
        ("P2", None): (1, 1),
        ("P2", "0.200"): (2, 0),
        ("all", None): (4, 3),
        ("all", "0.050"): (4, 3),
        ("all", "0.100"): (4, 1),
        ("all", "0.200"): (2, 0),
    }
    assert set(table) == set(hand)
    for key, (n, k) in hand.items():
        row = table[key]
        assert (row["n_trials"], row["n_same"]) == (n, k)
        assert row["p_same"] == k / n
    assert table[("P1", "0.100")]["n_no_response"] == 1
    assert table[("P1", "0.050")]["wilson_low"] == pytest.approx(0.300642, abs=5e-7)
    assert table[("P1", "0.050")]["wilson_high"] == pytest.approx(0.954413, abs=5e-7)


def test_summary_script_on_the_fixture(tmp_path):
    from av_generation.threshold_cli import main

    out = tmp_path / "out"
    status = main(
        [
            "summary",
            "--trials",
            str(FIXTURES / "hand-trials.csv"),
            "--out-dir",
            str(out),
            "--label",
            "SYNTHETIC hand fixture",
            "--stimuli",
            str(DEMO_SET),
        ]
    )
    assert status == 0
    assert (out / "summary.csv").read_bytes() == (FIXTURES / "hand-summary.csv").read_bytes()
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["format"] == th.SUMMARY_FORMAT and summary["decision"] is None
    assert summary["set_sha256"] == ThresholdStimulusSet.read(DEMO_SET).sha256()
    assert summary["listeners"] == ["L01", "L02"]
    assert summary["n_trials"] == 15 and summary["n_answered"] == 14
    assert (out / "summary.png").stat().st_size > 1000
    _ci_copy(out / "summary.png", "hand-fixture-summary.png")


def test_summary_script_refuses_trials_of_another_set(tmp_path, capsys, demo_set):
    """Pair IDs repeat across sets, so `summary --stimuli` checks every row against the
    set before it writes the set's ID and hash into the evidence file."""
    from av_generation.threshold_cli import main

    hand = FIXTURES / "hand-trials.csv"
    assert th.check_trials_csv(hand, demo_set) == ()
    config = ThresholdConfig(
        profiles=("P1", "P2"),
        bin_centers=("0.050", "0.100", "0.200"),
        bin_halfwidth="0.0125",
        pairs_per_bin=8,
        same_pairs=6,
        gap_ms=500,
        threshold_default="0.10",
    )
    other = th.generate_stimuli("DEMO-T-other", config)
    assert {t.pair_id for t in th.read_trials_csv(hand)} <= {p.pair_id for p in other.pairs}
    th.write_stimuli(other, tmp_path / "other.json")
    renamed = dataclasses.replace(demo_set, set_id="DEMO-T-renamed")
    th.write_stimuli(renamed, tmp_path / "renamed.json")
    for stimuli, needle in ((other, "disagrees with the set"), (renamed, "set_sha256")):
        problems = th.check_trials_csv(hand, stimuli)
        assert problems and needle in problems[0], problems
    capsys.readouterr()
    for name in ("other.json", "renamed.json"):
        out = tmp_path / f"out-{name}"
        args = ["summary", "--trials", str(hand), "--out-dir", str(out), "--label", "x"]
        assert main([*args, "--stimuli", str(tmp_path / name), "--no-plot"]) == 2
        assert "does not belong to stimulus set" in capsys.readouterr().err
        assert not out.exists()
    # a row whose recipes or hashes were edited no longer matches its set
    text = hand.read_text(encoding="utf-8")
    first = th.trial_rows(th.read_trials_csv(hand)[:1], demo_set)[0]["pcm_sha256_first"]
    edited = tmp_path / "edited.csv"
    edited.write_text(text.replace(first, "0" * 64, 1), encoding="utf-8")
    assert th.check_trials_csv(edited, demo_set) == (
        "DEMO-HAND-1 trial 1: pcm_sha256_first differ from set DEMO-T1",
    )
    recipe = text.replace("[-3,2,-4]", "[-3,2,-5]", 1)
    assert recipe != text
    edited.write_text(recipe, encoding="utf-8")
    assert "recipe_first" in th.check_trials_csv(edited, demo_set)[0]
    garbled = text.replace('"{""amplitudes"":[1.0,0.6,0.8]', '"{""amplitudes"":[1.0,0.6,0.8', 1)
    assert garbled != text
    edited.write_text(garbled, encoding="utf-8")
    assert "recipe_first" in th.check_trials_csv(edited, demo_set)[0]
    edited.write_text(text.replace(demo_set.sha256(), "x" * 64, 1), encoding="utf-8")
    with pytest.raises(th.ThresholdError, match="set_sha256"):
        th.read_trials_csv(edited)


def test_wilson_interval_known_values():
    assert th.wilson_interval(3, 4) == pytest.approx((0.3006418, 0.9544127), abs=1e-6)
    assert th.wilson_interval(0, 10) == (0.0, pytest.approx(0.2775328, abs=1e-6))
    assert th.wilson_interval(10, 10) == (pytest.approx(0.7224672, abs=1e-6), 1.0)
    low, high = th.wilson_interval(50, 100)
    assert low == pytest.approx(1 - high) and low == pytest.approx(0.4038315, abs=1e-6)
    for bad in ((1, 0), (-1, 3), (4, 3)):
        with pytest.raises(ValueError):
            th.wilson_interval(*bad)


def _neg_log_likelihood(beta, xs, ys):
    b0, b1 = beta
    total = 0.0
    for x, y in zip(xs, ys, strict=True):
        eta = b0 + b1 * x
        total += math.log1p(math.exp(-abs(eta))) + max(eta, 0) - y * eta
    return total


def test_logistic_fit_matches_scipy():
    from scipy.optimize import minimize

    xs = [0.04, 0.05, 0.06, 0.08, 0.09, 0.10, 0.11, 0.13, 0.15, 0.17, 0.19, 0.21] * 3
    ys = [1, 1, 1, 1, 0, 1, 0, 1, 0, 0, 0, 0, 1, 1, 0, 1, 1, 0, 0, 0, 1, 0, 0, 0]
    ys = ys + [1, 1, 1, 0, 1, 1, 0, 0, 0, 1, 0, 0]
    fit = th.fit_logistic(xs, [bool(y) for y in ys], profile="P1")
    ref = minimize(_neg_log_likelihood, [0.0, 0.0], args=(xs, ys), method="BFGS", tol=1e-12)
    assert fit.status == "ok"
    assert fit.intercept == pytest.approx(ref.x[0], rel=1e-4)
    assert fit.slope == pytest.approx(ref.x[1], rel=1e-4)
    assert fit.log_likelihood == pytest.approx(-ref.fun, rel=1e-8)
    assert fit.d50 == pytest.approx(-ref.x[0] / ref.x[1], rel=1e-4)
    assert fit.slope < 0 and 0 < fit.p_same_at_default < 1
    assert fit.iterations >= 1
    assert set(fit.to_dict()) == set(th.FIT_COLUMNS)
    # standard errors: square roots of the diagonal of the inverse observed information,
    # here a central-difference Hessian of the scipy objective at the scipy optimum
    b = list(ref.x)
    steps = (1e-3, 1e-2)

    def f(d0, d1):
        return _neg_log_likelihood((b[0] + d0, b[1] + d1), xs, ys)

    def second(i, j):
        def shifted(si, sj):
            d = [0.0, 0.0]
            d[i] += si * steps[i]
            d[j] += sj * steps[j]
            return f(*d)

        total = shifted(1, 1) - shifted(1, -1) - shifted(-1, 1) + shifted(-1, -1)
        return total / (4 * steps[i] * steps[j])

    h00, h01, h11 = second(0, 0), second(0, 1), second(1, 1)
    det = h00 * h11 - h01 * h01
    assert fit.se_intercept == pytest.approx(math.sqrt(h11 / det), rel=1e-4)
    assert fit.se_slope == pytest.approx(math.sqrt(h00 / det), rel=1e-4)
    assert fit.se_intercept != pytest.approx(fit.se_slope, rel=0.1)


def test_logistic_fit_degenerate_and_separated():
    assert th.fit_logistic([0.1], [True]).status == "degenerate"
    assert th.fit_logistic([0.1, 0.1, 0.1], [True, False, True]).status == "degenerate"
    assert th.fit_logistic([0.05, 0.2], [False, False]).status == "degenerate"
    separated = th.fit_logistic([0.05, 0.06, 0.15, 0.2], [True, True, False, False])
    assert separated.status == "separated" and separated.slope is None
    quasi = th.fit_logistic([0.05, 0.1, 0.1, 0.2], [False, True, False, True])
    assert quasi.status == "separated"  # quasi-complete: a tie at the boundary only
    rising = th.fit_logistic([0.05, 0.1, 0.15, 0.2], [False, True, False, True])
    assert rising.status == "ok" and rising.slope > 0 and rising.d50 is None
    with pytest.raises(ValueError):
        th.fit_logistic([0.1, 0.2], [True])


def test_summary_refuses_mixed_tryout_and_listener_trials():
    trials = th.read_trials_csv(FIXTURES / "hand-trials.csv")
    mixed = [trials[0], dataclasses.replace(trials[1], tryout=True)]
    for fn in (th.summarize, th.fit_summary):
        with pytest.raises(th.ThresholdError, match="tryout"):
            fn(mixed)
    assert th.summarize([]) == []


# ---------------------------------------------------------------------------
# CSV export


def test_csv_export_round_trips(tmp_path, demo_set):
    trials = th.read_trials_csv(FIXTURES / "hand-trials.csv")
    sha = th.export_csv(trials, demo_set, tmp_path / "trials.csv")
    data = (tmp_path / "trials.csv").read_bytes()
    assert sha == hashlib.sha256(data).hexdigest()
    assert data == (FIXTURES / "hand-trials.csv").read_bytes()
    assert b"\r" not in data
    assert th.read_trials_csv(tmp_path / "trials.csv") == trials
    header = data.split(b"\n", 1)[0].decode()
    assert tuple(header.split(",")) == th.TRIAL_CSV_COLUMNS
    row = th.trial_rows(trials[:1], demo_set)[0]
    pair = next(p for p in demo_set.pairs if p.pair_id == trials[0].pair_id)
    first = pair.recipe_a if trials[0].order == "AB" else pair.recipe_b
    assert json.loads(row["recipe_first"]) == first
    assert row["tryout"] == "0" and row["sum_sq"] == pair.sum_sq


def test_csv_export_refuses_inconsistent_trials(tmp_path, demo_set):
    trials = th.read_trials_csv(FIXTURES / "hand-trials.csv")
    with pytest.raises(th.ThresholdError, match="unknown pair"):
        th.export_csv(
            [dataclasses.replace(trials[0], pair_id="P1-0.050-99")], demo_set, tmp_path / "x"
        )
    with pytest.raises(th.ThresholdError, match="disagrees"):
        th.export_csv([dataclasses.replace(trials[0], distance=0.3)], demo_set, tmp_path / "x")
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(th.ThresholdError, match="columns"):
        th.read_trials_csv(bad)
    text = (FIXTURES / "hand-trials.csv").read_text(encoding="utf-8").replace(",L01,", ",L 01,", 1)
    bad.write_text(text, encoding="utf-8")
    with pytest.raises(th.ThresholdError):
        th.read_trials_csv(bad)
    text = (
        (FIXTURES / "hand-trials.csv").read_text(encoding="utf-8").replace(",L01,1,", ",L01,x,", 1)
    )
    bad.write_text(text, encoding="utf-8")
    with pytest.raises(th.ThresholdError):
        th.read_trials_csv(bad)


def test_plot_and_summary_documents(tmp_path, demo_set):
    trials = th.read_trials_csv(FIXTURES / "hand-trials.csv")
    rows = th.summarize(trials)
    fits = th.fit_summary(trials)
    assert [f.profile for f in fits] == ["P1", "P2", "all"]
    assert fits[1].status == "degenerate"
    th.plot_summary(rows, fits, tmp_path / "plot.svg", title="SYNTHETIC test plot")
    assert (tmp_path / "plot.svg").read_text(encoding="utf-8").lstrip().startswith("<?xml")
    sha = th.write_fits_csv(fits, tmp_path / "fits.csv")
    assert len(sha) == 64
    lines = (tmp_path / "fits.csv").read_text(encoding="utf-8").splitlines()
    assert lines[0].split(",") == list(th.FIT_COLUMNS)
    assert lines[2].startswith("P2,degenerate,2,0,,")
    summary = th.build_summary(trials, label="SYNTHETIC", stimuli=demo_set)
    assert summary["config"]["pairs_per_bin"] == 8
    assert [s["session_id"] for s in summary["sessions"]] == ["DEMO-HAND-1", "DEMO-HAND-2"]
    assert summary["tryout"] is False
