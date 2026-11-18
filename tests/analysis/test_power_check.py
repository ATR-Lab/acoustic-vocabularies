"""#309 power check: configuration, both engines, Holm rule, loss, balanced families,
verdict table and the command (small replicate counts; the full reproduction of the
earlier planning simulation values is ``test_power_check_reproduction.py``)."""

from __future__ import annotations

import copy
import dataclasses
import json
import math
import subprocess
import sys

import numpy as np
import pytest
from scipy import stats

from av_analysis.estimators import wilson
from av_analysis.fileio import json_bytes, parse_csv
from av_analysis.power_check import (
    CONFIG_SCHEMA,
    POWER_COLUMNS,
    ConfigError,
    PowerRow,
    balanced_structured,
    batch_drop_probability,
    cells,
    config_schema,
    decide,
    is_planning_example,
    latent_scenario,
    load_config,
    parse_config,
    planning_example_path,
    replicate_function,
    run_cell,
    run_config,
    verdict,
    write_outputs,
)
from av_analysis.power_check.__main__ import main
from av_analysis.power_check.run import cell_rng, summarise
from av_analysis.schemas import schema_dir
from av_analysis.seeds import rng
from av_analysis.simulate import (
    a_differences,
    b_differences,
    draw_a,
    draw_b,
    scenarios,
)


def planning_doc():
    return json.loads(planning_example_path().read_text(encoding="utf-8"))


def small_doc(replicates=20):
    """A small two-study configuration (fast)."""
    return {
        "format": "av-analysis/power-check-config",
        "format_version": 1,
        "basis": "planning",
        "description": "small synthetic test grid",
        "seed": "DEMO-power-test",
        "replicates": replicates,
        "alpha": 0.05,
        "effect_pp": 10,
        "criterion": 0.8,
        "studies": {
            "A": {
                "baseline": 0.65,
                "learners_per_book": 4,
                "sizes": [12, 6],
                "loss": [0.05],
                "heterogeneity": [
                    {"name": "central", "engine": "logistic-normal", "latent": "central"},
                    {"name": "unit", "engine": "unit-normal", "unit_sd": {"A3-A2": 0.13}},
                ],
                "scenarios": [
                    {"name": "effect", "effect_pp": {"A3-A2": 10}},
                    {"name": "null", "effect_pp": {"A3-A2": 0}},
                ],
            },
            "B": {
                "baseline": 0.65,
                "sizes": [8, 16],
                "loss": [0.15],
                "heterogeneity": [
                    {"name": "central", "engine": "logistic-normal", "latent": "central"},
                    {
                        "name": "unit",
                        "engine": "unit-normal",
                        "unit_sd": {"C": 0.18, "S": 0.12},
                        "correlation": 0.3,
                    },
                ],
                "scenarios": [
                    {"name": "present", "effect_pp": {"C": 10, "S": 10}},
                    {"name": "absent", "effect_pp": {"C": 10, "S": 0}},
                ],
            },
        },
    }


def grid_of(config, study):
    return next(g for g in config.studies if g.study == study)


def het_of(grid, name):
    return next(h for h in grid.heterogeneity if h.name == name)


def scenario_of(grid, name):
    return next(s for s in grid.scenarios if s.name == name)


# ---------------------------------------------------------------------------------------
# Configuration


def test_planning_example_holds_the_planning_values():
    cfg = load_config(planning_example_path())
    assert (cfg.basis, cfg.replicates, cfg.alpha, cfg.effect_pp, cfg.criterion) == (
        "planning",
        2000,
        0.05,
        10.0,
        0.8,
    )
    assert cfg.seed.startswith("DEMO-") and len(cfg.sha256) == 64
    a, b = grid_of(cfg, "A"), grid_of(cfg, "B")
    assert a.sizes == (18, 24, 30, 36) and a.loss == (0.05,) and a.learners_per_book == 4
    assert b.sizes == (64, 72, 80, 84, 88, 96) and b.loss == (0.15, 0.25)
    assert a.baseline == b.baseline == 0.65
    assert het_of(a, "unit-central").unit_sd == {"A3-A2": 0.13}
    assert het_of(a, "unit-pessimistic").unit_sd == {"A3-A2": 0.18}
    assert het_of(b, "unit-central").unit_sd == {"C": 0.18, "S": 0.12}
    assert het_of(b, "unit-pessimistic").unit_sd == {"C": 0.22, "S": 0.15}
    assert het_of(b, "unit-pessimistic").correlation == 0.0
    assert {s.name: dict(s.effect_pp) for s in b.scenarios} == {
        "teaching-present": {"C": 10, "S": 10},
        "teaching-absent": {"C": 10, "S": 0},
        "global-null": {"C": 0, "S": 0},
    }
    assert is_planning_example(planning_doc())
    assert not is_planning_example(small_doc())


def test_config_schema_is_the_committed_file():
    assert (schema_dir() / CONFIG_SCHEMA).read_bytes() == json_bytes(config_schema())


def _mutate(path, value):
    doc = small_doc()
    target = doc
    for key in path[:-1]:
        target = target[key]
    if value is KeyError:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return doc


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("seed",), "power-1", "seed"),
        (("replicates",), 5, "replicates"),
        (("basis",), "real", "basis"),
        (("studies", "A", "sizes"), [20], "sizes"),
        (("studies", "B", "sizes"), [65], "sizes"),
        (("studies", "B", "loss"), [1.0], "loss"),
        (("studies", "A", "learners_per_book"), KeyError, "learners_per_book"),
        (("studies", "A", "extra"), 1, "extra"),
        (
            ("studies", "A", "heterogeneity", 0),
            {"name": "x", "engine": "logistic-normal", "unit_sd": {"A3-A2": 0.1}},
            "heterogeneity",
        ),
        (
            ("studies", "A", "heterogeneity", 1),
            {"name": "x", "engine": "unit-normal", "unit_sd": {"C": 0.1}},
            "heterogeneity",
        ),
        (("studies",), {}, "studies"),
    ],
)
def test_schema_violations_are_refused(path, value, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(_mutate(path, value))


def test_semantic_checks():
    doc = small_doc()
    doc["studies"]["A"]["heterogeneity"][1]["name"] = "central"
    with pytest.raises(ConfigError, match="SD source names must be unique"):
        parse_config(doc)
    doc = small_doc()
    doc["studies"]["B"]["scenarios"] = [{"name": "absent", "effect_pp": {"C": 10, "S": 0}}]
    with pytest.raises(ConfigError, match="no scenario has S = effect_pp"):
        parse_config(doc)
    doc = small_doc()
    doc["studies"]["B"]["baseline"] = 0.85
    with pytest.raises(ConfigError, match="cell target leaves"):
        parse_config(doc)
    doc = small_doc()
    del doc["replicates"]
    cfg = parse_config(doc)
    assert cfg.replicates == 2000
    assert grid_of(cfg, "A").sizes == (6, 12)
    assert cfg.with_replicates(30).replicates == 30
    with pytest.raises(ConfigError):
        cfg.with_replicates(9)


def test_unreadable_configuration(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(bad)


# ---------------------------------------------------------------------------------------
# Engines


def test_balanced_families():
    s = balanced_structured(84)
    assert s.shape == (84, 18) and s.dtype == bool
    assert np.all(s.sum(axis=1) == 9)
    assert np.all(s[:42, :9]) and not np.any(s[:42, 9:])
    assert np.all(s[42:, 9:]) and not np.any(s[42:, :9])
    assert np.all(s.sum(axis=0) == 42)
    with pytest.raises(ValueError):
        balanced_structured(65)
    with pytest.raises(ValueError):
        balanced_structured(0)


def test_logistic_engine_is_the_planning_draws_without_faults():
    cfg = parse_config(planning_doc())
    a = grid_of(cfg, "A")
    sc = latent_scenario(a, het_of(a, "central"), scenario_of(a, "effect"), 0.05)
    assert dict(sc.variances) == dict(scenarios()["central-A"].variances)
    assert (sc.effect_pp, sc.baseline, sc.attrition, sc.fault_rate, sc.late_rate) == (
        10,
        0.65,
        0.05,
        0.0,
        0.0,
    )
    fn = replicate_function(a, het_of(a, "central"), scenario_of(a, "effect"), 0.05, 12)
    got = fn(rng("DEMO-same"))["A3-A2"]
    want = a_differences(draw_a(sc, rng("DEMO-same"), 12, 4))["A3-A2"]
    np.testing.assert_array_equal(got, want)

    b = grid_of(cfg, "B")
    het, absent = het_of(b, "pessimistic"), scenario_of(b, "teaching-absent")
    sc = latent_scenario(b, het, absent, 0.25)
    assert dict(sc.variances) == dict(scenarios()["pessimistic-B"].variances)
    assert (sc.effect_pp, sc.scaffold_pp, sc.attrition) == (10, 0, 0.25)
    got_b = replicate_function(b, het, absent, 0.25, 64)(rng("DEMO-same"))
    want_b = b_differences(draw_b(sc, rng("DEMO-same"), balanced_structured(64)))
    for k in ("C", "S"):
        np.testing.assert_array_equal(got_b[k], want_b[k])
    with pytest.raises(ValueError):
        latent_scenario(b, het_of(b, "unit-central"), absent, 0.15)


def test_multiplier_scales_every_latent_sd():
    doc = small_doc()
    doc["studies"]["A"]["heterogeneity"][0]["multiplier"] = 1.5
    cfg = parse_config(doc)
    a = grid_of(cfg, "A")
    sc = latent_scenario(a, a.heterogeneity[0], a.scenarios[0], 0.05)
    base = scenarios()["central-A"].variances
    for term, v in base.items():
        assert sc.variances[term] == pytest.approx(2.25 * v, rel=1e-8)
    assert a.heterogeneity[0].describe() == "latent central x1.5"


def test_loss_semantics_of_the_logistic_engine():
    cfg = parse_config(small_doc())
    a = grid_of(cfg, "A")
    one = dataclasses.replace(a, learners_per_book=1)
    sc = latent_scenario(one, a.heterogeneity[0], a.scenarios[0], 0.5)
    d = draw_a(sc, rng("DEMO-loss"), 300, 1)
    diff = a_differences(d)["A3-A2"]
    # A batch drops exactly when its A2 or A3 book has no learner left (A1 is irrelevant).
    kept = ~d.missing[:, 1, 0] & ~d.missing[:, 2, 0]
    np.testing.assert_array_equal(np.isfinite(diff), kept)
    assert np.any(kept & d.missing[:, 0, 0])

    b = grid_of(cfg, "B")
    sc = latent_scenario(b, b.heterogeneity[0], b.scenarios[0], 0.3)
    db = draw_b(sc, rng("DEMO-loss"), balanced_structured(200))
    diffs = b_differences(db)
    both = ~db.missing[:, 0] & ~db.missing[:, 1]
    np.testing.assert_array_equal(np.isfinite(diffs["C"]), both)
    np.testing.assert_array_equal(np.isfinite(diffs["S"]), both)
    assert not db.fault.any() and not db.late.any()


def test_batch_drop_probability():
    assert batch_drop_probability(0.0, 4) == 0.0
    assert batch_drop_probability(0.5, 1) == pytest.approx(0.75)
    assert batch_drop_probability(0.05, 4) == pytest.approx(1 - (1 - 0.05**4) ** 2)


def test_unit_normal_engine_draws_the_configured_distribution():
    cfg = parse_config(small_doc())
    a = grid_of(cfg, "A")
    fn = replicate_function(a, het_of(a, "unit"), scenario_of(a, "effect"), 0.0, 20000)
    x = fn(rng("DEMO-unit"))["A3-A2"]
    assert np.all(np.isfinite(x))
    assert x.mean() == pytest.approx(0.10, abs=0.005) and x.std() == pytest.approx(0.13, rel=0.03)
    heavy = replicate_function(a, het_of(a, "unit"), scenario_of(a, "effect"), 0.5, 20000)
    dropped = np.mean(~np.isfinite(heavy(rng("DEMO-unit"))["A3-A2"]))
    assert dropped == pytest.approx(batch_drop_probability(0.5, 4), abs=0.01)

    b = grid_of(cfg, "B")
    fn = replicate_function(b, het_of(b, "unit"), scenario_of(b, "absent"), 0.25, 20000)
    d = fn(rng("DEMO-unit"))
    complete = np.isfinite(d["C"])
    np.testing.assert_array_equal(complete, np.isfinite(d["S"]))
    assert complete.mean() == pytest.approx(0.75**2, abs=0.01)
    c, s = d["C"][complete], d["S"][complete]
    assert c.mean() == pytest.approx(0.10, abs=0.006) and s.mean() == pytest.approx(0.0, abs=0.004)
    assert c.std() == pytest.approx(0.18, rel=0.03) and s.std() == pytest.approx(0.12, rel=0.03)
    assert np.corrcoef(c, s)[0, 1] == pytest.approx(0.3, abs=0.03)
    with pytest.raises(ValueError):
        from av_analysis.power_check.engines import unit_normal

        unit_normal(b, het_of(b, "central"), scenario_of(b, "absent"), 0.25, 8)


def _values_with_p(p, n=20):
    """Differences whose two-sided one-sample t test has p value ``p``."""
    z = np.linspace(-1.0, 1.0, n)
    z = (z - z.mean()) / z.std(ddof=1)
    t = stats.t.isf(p / 2.0, n - 1)
    return t / math.sqrt(n) + z


@pytest.mark.parametrize(
    ("pc", "ps", "expected"),
    [
        (0.02, 0.04, (True, True)),  # smaller <= .025, larger <= .05
        (0.03, 0.04, (False, False)),  # smaller > .025: stop
        (0.001, 0.2, (True, False)),
        (0.2, 0.001, (False, True)),
        (0.024, 0.051, (True, False)),
    ],
)
def test_holm_rule_over_c_and_s(pc, ps, expected):
    d = decide("B", {"C": _values_with_p(pc), "S": _values_with_p(ps)}, 0.05)
    assert d.tests["C"].p == pytest.approx(pc, rel=1e-9)
    assert d.tests["S"].p == pytest.approx(ps, rel=1e-9)
    assert (d.reject["C"], d.reject["S"]) == expected
    assert d.reject["any"] == any(expected) and d.reject["both"] == all(expected)


def test_decisions_with_an_unavailable_test_and_study_a():
    d = decide("B", {"C": _values_with_p(1e-6), "S": np.array([0.1, np.nan, np.nan])}, 0.05)
    assert d.tests["S"].p is None and not d.reject["C"] and not d.reject["any"]
    assert decide("A", {"A3-A2": _values_with_p(0.049)}, 0.05).reject["A3-A2"]
    assert not decide("A", {"A3-A2": _values_with_p(0.051)}, 0.05).reject["A3-A2"]
    assert not decide("A", {"A3-A2": np.array([np.nan, 0.1])}, 0.05).reject["A3-A2"]


# ---------------------------------------------------------------------------------------
# Runs, rows and verdict


def test_run_rows_are_complete_and_deterministic():
    cfg = parse_config(small_doc())
    rows = run_config(cfg)
    assert rows == run_config(cfg)
    expected = sum(
        len({"A": ("A3-A2",), "B": ("C", "S", "any", "both")}[c.grid.study]) for c in cells(cfg)
    )
    assert len(rows) == expected == 2 * 2 * 2 * 1 + 2 * 2 * 2 * 4
    for r in rows:
        assert r.replicates == 20 and 0 <= r.rejections <= 20
        assert r.mcse == pytest.approx(math.sqrt(r.rate * (1 - r.rate) / 20))
        assert (r.mc95_low, r.mc95_high) == wilson(r.rejections, 20)
        if r.contrast in ("any", "both"):
            assert r.true_effect_pp is None and r.implied_unit_sd is None
        else:
            assert r.implied_unit_sd is not None and r.implied_unit_sd_mcse is not None
    no_loss = [r for r in rows if r.study == "A" and r.engine == "unit-normal"]
    assert all(r.mean_complete_units == pytest.approx(r.size, abs=0.01) for r in no_loss)
    # Independent streams per cell and replicate.
    c0, c1 = cells(cfg)[:2]
    assert cell_rng(cfg.seed, c0, 0).random() != cell_rng(cfg.seed, c1, 0).random()
    assert cell_rng(cfg.seed, c0, 0).random() == cell_rng(cfg.seed, c0, 0).random()
    with pytest.raises(ValueError):
        summarise(c0, [], cfg.seed)


def test_run_cell_without_loss_keeps_every_unit():
    doc = small_doc()
    doc["studies"]["A"]["loss"] = [0.0]
    doc["studies"]["B"]["loss"] = [0.0]
    cfg = parse_config(doc)
    for cell in cells(cfg):
        for r in run_cell(cfg, cell):
            assert r.mean_complete_units == r.size and r.unavailable == 0


def _row(source, scenario, size, rejections, effect=10.0, n=1000, contrast="A3-A2"):
    rate = rejections / n
    low, high = wilson(rejections, n)
    return PowerRow(
        "A", "unit-normal", source, "unit SD", scenario, 0.05, size, contrast, effect, n,
        rejections, rate, math.sqrt(rate * (1 - rate) / n), low, high, size, 0.13, 0.001,
        0.1, 0, "DEMO-v",
    )  # fmt: skip


def test_verdict_table():
    doc = small_doc()
    doc["studies"]["A"]["sizes"] = [6, 12, 18]
    del doc["studies"]["B"]
    cfg = parse_config(doc)
    rows = [
        _row("central", "effect", 6, 700),
        _row("central", "effect", 12, 805),  # point estimate holds, MC lower limit does not
        _row("central", "effect", 18, 900),
        _row("unit", "effect", 6, 810),
        _row("unit", "effect", 12, 790),  # non-monotone: reported as is
        _row("unit", "effect", 18, 950),
        _row("central", "null", 6, 50, effect=0.0),  # not a verdict case
    ]
    doc_v = verdict(cfg, rows)
    assert doc_v["data_kind"] == "SYNTHETIC" and doc_v["decision_grade"] is False
    assert "point estimate" in doc_v["verdict_rule"]
    (c,) = doc_v["contrasts"]
    assert (c["study"], c["contrast"]) == ("A", "A3-A2")
    by = {case["sd_source"]: case for case in c["cases"]}
    assert set(by) == {"central", "unit"}
    assert [e["holds"] for e in by["central"]["sizes"]] == [False, True, True]
    assert [e["holds_mc95_low"] for e in by["central"]["sizes"]] == [False, False, True]
    assert by["central"]["smallest_size"] == 12 and by["central"]["smallest_size_mc95_low"] == 18
    assert by["unit"]["smallest_size"] == 6 and by["unit"]["smallest_size_mc95_low"] == 18
    assert [s["holds"] for s in c["all_cases"]["sizes"]] == [False, False, True]
    assert c["all_cases"]["smallest_size"] == 18
    rows[2] = _row("central", "effect", 18, 790)
    c = verdict(cfg, rows)["contrasts"][0]
    assert c["all_cases"]["smallest_size"] is None
    assert c["all_cases"]["smallest_size_mc95_low"] is None


def test_outputs(tmp_path):
    cfg = parse_config(small_doc())
    rows = run_config(cfg)
    paths = write_outputs(tmp_path, cfg, rows)
    assert [p.name for p in paths] == ["power.csv", "power.md", "verdict.json"]
    header, body = parse_csv((tmp_path / "power.csv").read_bytes())
    assert header == POWER_COLUMNS and len(body) == len(rows)
    assert {r[0] for r in body} == {"SYNTHETIC"}
    md = (tmp_path / "power.md").read_text(encoding="utf-8")
    assert md.startswith("<!-- av-data-kind: SYNTHETIC -->")
    assert "not for the freeze decision" in md and "## Study B: S" in md
    assert "## Implied unit SD" in md and "**all cases**" in md
    doc = json.loads((tmp_path / "verdict.json").read_text(encoding="utf-8"))
    assert [c["contrast"] for c in doc["contrasts"]] == ["A3-A2", "C", "S"]
    assert doc["config_sha256"] == cfg.sha256 and doc["replicates"] == 20
    before = {p.name: p.read_bytes() for p in paths}
    write_outputs(tmp_path, cfg, run_config(cfg))
    assert {p.name: p.read_bytes() for p in paths} == before


# ---------------------------------------------------------------------------------------
# Command


def _write(path, doc):
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_command_writes_the_outputs(tmp_path, capsys):
    cfg = _write(tmp_path / "small.json", small_doc(replicates=500))
    out = tmp_path / "out"
    assert main(["run", "--config", str(cfg), "--out", str(out), "--replicates", "10"]) == 0
    assert sorted(p.name for p in out.iterdir()) == ["power.csv", "power.md", "verdict.json"]
    doc = json.loads((out / "verdict.json").read_text(encoding="utf-8"))
    assert doc["replicates"] == 10 and doc["decision_grade"] is False
    captured = capsys.readouterr()
    assert "Study A A3-A2:" in captured.out and "[1/16]" in captured.err


@pytest.mark.parametrize(
    ("content", "extra", "message"),
    [
        ("{oops", [], "cannot read"),
        (json.dumps({"format": "x"}), [], "does not match"),
        (None, ["--replicates", "3"], "replicates must be at least"),
    ],
)
def test_command_refuses_bad_input(tmp_path, capsys, content, extra, message):
    cfg = tmp_path / "c.json"
    if content is None:
        _write(cfg, small_doc())
    else:
        cfg.write_text(content, encoding="utf-8")
    out = tmp_path / "out"
    assert main(["run", "--config", str(cfg), "--out", str(out), "--quiet", *extra]) == 2
    assert message in capsys.readouterr().err and not out.exists()


def _git_repo(path):
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    return path


def test_command_refuses_a_git_work_tree_unless_planning_example(tmp_path, capsys):
    repo = _git_repo(tmp_path / "repo")
    cfg = _write(tmp_path / "small.json", small_doc())
    argv = ["run", "--config", str(cfg), "--quiet", "--replicates", "10", "--out"]
    assert main([*argv, str(repo / "power")]) == 2
    assert "only the committed planning example" in capsys.readouterr().err
    assert not (repo / "power").exists()
    # A git-ignored folder is not committable (paths.committable), so it is allowed.
    assert main([*argv, str(repo / "ignored" / "power")]) == 0
    # The committed planning example may write inside the work tree.
    copy_of_planning = _write(tmp_path / "planning.json", copy.deepcopy(planning_doc()))
    assert (
        main(
            [
                "run",
                "--config",
                str(copy_of_planning),
                "--out",
                str(repo / "planning"),
                "--replicates",
                "10",
                "--quiet",
            ]
        )
        == 0
    )
    assert (repo / "planning" / "verdict.json").is_file()


def test_module_entry_point(tmp_path):
    cfg = _write(tmp_path / "small.json", small_doc(replicates=10))
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "av_analysis.power_check",
            "run",
            "--config",
            str(cfg),
            "--out",
            str(tmp_path / "out"),
            "--quiet",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "out" / "power.md").is_file()
