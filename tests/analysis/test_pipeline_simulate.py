"""#34 simulation: scenarios, calibration, null operating characteristics, datasets, CLI."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest
from scipy.special import expit

from av_analysis.cli import main
from av_analysis.derived import ENDPOINTS, TRIALS, parse_table, table_bytes
from av_analysis.fileio import parse_csv
from av_analysis.paths import DataRoot, check_watermark
from av_analysis.pipeline import analyze
from av_analysis.report import write_dataset
from av_analysis.schemas import validator
from av_analysis.scoring import battery_scores
from av_analysis.seeds import rng
from av_analysis.simulate import (
    OC_COLUMNS,
    Scenario,
    a_differences,
    b_differences,
    dataset_results,
    draw_a,
    intercept,
    operating_characteristics,
    scenarios,
    simulate_dataset,
)

NULL_SEED = "DEMO-o4.5.2-null"  # fixed before the run; recorded in the evidence


def test_scenarios():
    sc = scenarios()
    assert {"null-A", "central-A", "pessimistic-A", "null-B", "central-B", "pessimistic-B"} <= set(
        sc
    )
    assert sc["null-A"].effect_pp == 0 and sc["central-A"].effect_pp == 10
    assert (sc["null-B"].effect_pp, sc["null-B"].scaffold_pp) == (0, 0)
    assert sc["null-co-B"].effect_pp == 0 and sc["null-co-B"].scaffold_pp == 10
    assert sc["central-A"].variances["person"] == pytest.approx(0.6**2)
    assert sc["pessimistic-B"].variances["pair_co_slope"] == pytest.approx(0.65**2)
    assert sc["pilot-A"].set_name == "pilot"


@pytest.mark.parametrize(("target", "variance"), [(0.65, 0.8925), (0.75, 1.9225), (0.55, 0.3)])
def test_intercept_calibrates_the_marginal_accuracy(target, variance):
    b = intercept(target, variance)
    # Independent check: trapezoid integration of expit(b + z) over N(0, variance).
    sd = math.sqrt(variance)
    z = np.linspace(-12 * sd, 12 * sd, 200_001)
    dens = np.exp(-0.5 * (z / sd) ** 2) / (sd * math.sqrt(2 * math.pi))
    values = expit(b + z) * dens
    mean = float(np.sum((values[1:] + values[:-1]) / 2.0 * np.diff(z)))
    assert abs(mean - target) < 1e-8
    with pytest.raises(ValueError):
        intercept(1.0, 1.0)


def test_null_scenario_over_2000_synthetic_a_datasets():
    oc, results = operating_characteristics(scenarios()["null-A"], NULL_SEED, 2000)
    primary = next(o for o in oc if o.contrast == "A3-A2")
    assert primary.datasets == 2000 and len(results) == 3 * 2000
    assert 0.040 <= primary.rate <= 0.060, primary.rate
    assert primary.mc_low < 0.05 < primary.mc_high
    assert abs(primary.mean_estimate) < 0.01 and 17.9 < primary.mean_units <= 18.0
    assert primary.mcse == pytest.approx(math.sqrt(primary.rate * (1 - primary.rate) / 2000))


def test_null_b_family_wise_error_and_power():
    oc, _ = operating_characteristics(scenarios()["null-B"], NULL_SEED, 1000)
    any_r = next(o for o in oc if o.contrast == "any")
    assert 0.03 <= any_r.rate <= 0.07
    oc, _ = operating_characteristics(scenarios()["central-B"], NULL_SEED, 300)
    assert next(o for o in oc if o.contrast == "C").rate > 0.8


def test_dataset_results_are_deterministic():
    sc = scenarios()["central-A"]
    assert dataset_results(sc, "DEMO-x", 3) == dataset_results(sc, "DEMO-x", 3)
    assert dataset_results(sc, "DEMO-x", 3) != dataset_results(sc, "DEMO-x", 4)
    d = draw_a(sc, rng("DEMO-x"), 18, 4)
    assert d.y.shape == (18, 3, 4, 18, 2) and d.missing.shape == (18, 3, 4)
    assert not np.any(d.partial & ~d.missing)
    diffs = a_differences(d)
    assert set(diffs) == {"A3-A2", "A3-A1", "A2-A1"} and diffs["A3-A2"].shape == (18,)


def test_scenario_validation():
    sc = scenarios()["central-A"]
    bad = Scenario("bad", "A", 0, 0.6, {"nope": 1.0}, 0.0, 0.0)
    with pytest.raises(ValueError):
        draw_a(bad, rng("DEMO-x"), 2, 2)
    bad_rate = Scenario("bad", "A", 0, 0.6, dict(sc.variances), 1.5, 0.0)
    with pytest.raises(ValueError):
        draw_a(bad_rate, rng("DEMO-x"), 2, 2)
    with pytest.raises(ValueError):
        operating_characteristics(sc, "DEMO-x", 0)


@pytest.mark.parametrize("name", ["pilot-A", "pilot-B"])
def test_synthetic_dataset_follows_the_derived_contract(name):
    sc = scenarios()[name]
    ds = simulate_dataset(sc, "DEMO-contract")
    assert ds.scenario == name and ds.study == sc.study and ds.set_name == "pilot"
    trials = parse_table(TRIALS, table_bytes(TRIALS, ds.tables["trials"], "SYNTHETIC"))
    endpoints = parse_table(ENDPOINTS, table_bytes(ENDPOINTS, ds.tables["endpoints"], "SYNTHETIC"))
    scores = battery_scores(trials, endpoints)
    assert scores and all(r["data_kind"] == "SYNTHETIC" for r in trials)
    assert {r["row_source"] for r in trials} <= {"logged", "deviation"}
    for rel, data in ds.files.items():
        assert rel.startswith(("keys/A/", "inputs/schedules/"))
        assert json.loads(data)["demo"] is True
    assert ds.seeds == (f"DEMO-contract:{name}:dataset:0",)
    again = simulate_dataset(sc, "DEMO-contract")
    assert again.tables == ds.tables and again.files == ds.files


@pytest.mark.parametrize("name", ["pilot-A", "pilot-B"])
def test_fast_path_equals_the_table_and_pipeline_path(tmp_path, name):
    """The operating characteristics and the full pipeline see the same primary data."""
    sc = scenarios()[name]
    seed = "DEMO-paths"
    root = DataRoot.create(tmp_path / "root", "SYNTHETIC", label=seed)
    write_dataset(root, simulate_dataset(sc, seed))
    report = analyze(root, sc.study, glmm="skip", resamples=200)
    fast = {r.contrast: r for r in dataset_results(sc, seed, 0)}
    if sc.study == "A":
        rows = report.sections["a_primary"].tables[0].rows
        estimate, p = rows[0][3], rows[0][8]
        assert estimate == pytest.approx(fast["A3-A2"].estimate * 100, abs=1e-9)
        assert p == pytest.approx(fast["A3-A2"].p, abs=1e-12)
    else:
        rows = {r[0][0]: r for r in report.sections["b_primary"].tables[0].rows}
        for k in ("C", "S"):
            assert rows[k][3] == pytest.approx(fast[k].estimate * 100, abs=1e-9)
            assert rows[k][8] == pytest.approx(fast[k].p, abs=1e-12)
            assert rows[k][2] == fast[k].units


def test_b_differences_need_both_members():
    sc = scenarios()["central-B"]
    from av_analysis.simulate import _b_structured, draw_b

    d = draw_b(sc, rng("DEMO-y"), _b_structured("confirmatory", "DEMO-y"))
    diffs = b_differences(d)
    available = ~d.missing & ~d.late
    both = available.all(axis=1)
    assert np.array_equal(np.isfinite(diffs["C"]), both)
    assert np.array_equal(np.isfinite(diffs["S"]), both)


def test_simulate_command_writes_operating_characteristics(tmp_path, capsys):
    out = tmp_path / "sim"
    code = main(
        [
            "simulate",
            "--scenario",
            "null-A,null-B",
            "--datasets",
            "50",
            "--seed",
            "DEMO-cli",
            "--out",
            str(out),
        ]
    )
    assert code == 0
    root = DataRoot.open(out)
    path = out / "estimates" / "simulation" / "operating-characteristics.csv"
    data = path.read_bytes()
    check_watermark(data, ".csv", "SYNTHETIC")
    header, rows = parse_csv(data)
    assert header == OC_COLUMNS and len(rows) == 3 + 4
    schema = validator("operating-characteristics-row.schema.json")
    ints = {"datasets", "rejections", "unavailable"}
    nums = {
        "true_effect_pp",
        "rate",
        "mcse",
        "mc95_low",
        "mc95_high",
        "mean_estimate",
        "mean_unit_sd",
        "mean_units",
    }
    for row in rows:
        doc = {}
        for k, v in zip(header, row, strict=True):
            doc[k] = None if v == "" else int(v) if k in ints else float(v) if k in nums else v
        schema.validate(doc)
    manifest = json.loads((out / "estimates" / "manifest.json").read_bytes())
    validator("outputs-manifest.schema.json").validate(manifest)
    assert "DEMO-cli:null-A:dataset:0-49" in manifest["seeds"]
    assert root.data_kind == "SYNTHETIC"
    # A second call adds its scenario to the combined table (same root, other scenario).
    assert (
        main(
            [
                "simulate",
                "--scenario",
                "central-A",
                "--datasets",
                "20",
                "--seed",
                "DEMO-cli",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    _, rows = parse_csv(path.read_bytes())
    assert {r[1] for r in rows} == {"null-A", "null-B", "central-A"}
    capsys.readouterr()


def test_simulate_command_refusals(tmp_path, capsys):
    out = str(tmp_path / "sim")
    base = ["simulate", "--datasets", "5", "--out", out]
    assert main([*base, "--scenario", "nope", "--seed", "DEMO-x"]) == 2
    assert main([*base, "--scenario", "null-A", "--seed", "real-seed"]) == 2
    assert (
        main([*base, "--scenario", "null-A,central-A", "--seed", "DEMO-x", "--write-dataset"]) == 2
    )
    real = tmp_path / "real"
    real.mkdir()
    (real / "av-data-root.json").write_text(
        json.dumps(
            {
                "format": "av-analysis/data-root",
                "format_version": 1,
                "data_kind": "REAL",
                "study": "both",
                "set": "both",
                "label": "x",
            }
        ),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "simulate",
                "--scenario",
                "null-A",
                "--datasets",
                "5",
                "--seed",
                "DEMO-x",
                "--out",
                str(real),
            ]
        )
        == 2
    )
    assert "refusing" in capsys.readouterr().err


def test_simulate_all_scenarios_and_write_dataset(tmp_path, capsys):
    out = tmp_path / "sim"
    assert (
        main(
            [
                "simulate",
                "--scenario",
                "pilot-A,pilot-B",
                "--datasets",
                "3",
                "--seed",
                "DEMO-w",
                "--out",
                str(out),
                "--write-dataset",
            ]
        )
        == 0
    )
    for rel in (
        "derived/trials.csv",
        "derived/endpoints.csv",
        "derived/manifest.json",
        "keys/A/pilot-book-key.json",
        "inputs/schedules/A/pilot-slots.json",
        "inputs/schedules/B/pilot-dyads.json",
    ):
        assert (out / rel).is_file(), rel
    trials = parse_table(TRIALS, (out / "derived" / "trials.csv").read_bytes())
    assert {r["study"] for r in trials} == {"A", "B"}  # merged, not overwritten
    validator("outputs-manifest.schema.json").validate(
        json.loads((out / "derived" / "manifest.json").read_bytes())
    )
    assert (
        main(
            [
                "simulate",
                "--scenario",
                "all",
                "--datasets",
                "2",
                "--seed",
                "DEMO-w",
                "--out",
                str(tmp_path / "all"),
            ]
        )
        == 0
    )
    capsys.readouterr()
