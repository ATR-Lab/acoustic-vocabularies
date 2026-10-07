"""#34 GLMMs: model specifications, model data, the fallback ladder, the R bridge.

Tests without R drive the ladder with fake runners (and a fake ``Rscript`` on POSIX);
tests marked ``needs_r`` fit real models with the pinned lme4 (CI job ``r``).
"""

from __future__ import annotations

import json
import stat
import sys
import textwrap
import warnings
from pathlib import Path

import pytest

from av_analysis.fileio import csv_bytes
from av_analysis.glmm import (
    LADDER,
    fit_ladder,
    fit_with_ladder,
    ladder_order_ok,
    model_specs,
    not_run_log,
)
from av_analysis.rbridge import r_pins, run_r, version_problems
from av_analysis.schemas import validator

PINS = {"R": r_pins().r_version, **r_pins().packages}


def spec(model_id):
    study = model_id[0]
    return next(s for s in model_specs(study) if s.model_id == model_id)


def result(converged=True, singular=False, error=None, fixed=(), messages=()):
    return {
        "versions": dict(PINS),
        "optimizer": "bobyqa",
        "error": error,
        "converged": converged,
        "singular": singular,
        "messages": list(messages),
        "fixed": [dict(term=t, estimate=e, se=0.1, z=e / 0.1, p=0.5) for t, e in fixed],
        "random": [{"group": "person_id", "term1": "(Intercept)", "term2": None, "sdcor": 0.6}],
        "n_obs": 1234,
    }


class Recorder:
    """A fake runner: answers by formula and records every request."""

    def __init__(self, decide):
        self.decide = decide
        self.calls = []

    def __call__(self, script, request, files):
        assert script == "glmm.R" and set(files) == {"data.csv"}
        self.calls.append(request)
        return self.decide(request["formula"])


DATA = csv_bytes(("data_kind", "y"), [["SYNTHETIC", "1"], ["SYNTHETIC", "0"]])


def test_model_specs_follow_section_5():
    a = {s.model_id: s for s in model_specs("A")}
    assert set(a) == {"A-trained", "A-designer"}
    assert a["A-trained"].formula("full") == (
        "y ~ method + profile + rep + family + endpoint + (1 | unit_id) + (1 | book_id) "
        "+ (1 | person_id) + (1 | item_id)"
    )
    assert list(a["A-trained"].random) == ["full"]
    assert a["A-trained"].factors["method"][0] == "A2"  # A3 and A1 against A2
    assert "A1-D3" in a["A-designer"].factors["method_designer"]
    (b,) = model_specs("B")
    assert list(b.random) == list(LADDER[:4])
    assert b.formula("full").endswith(
        "(1 + role_c | unit_id) + (1 + format_c | person_id) + (1 | item_id)"
    )
    assert "(1 + role_c || unit_id)" in b.formula("no_correlations")
    assert "role_c" not in b.formula("no_dyad_role_slope").split("+ (", 1)[1]
    assert b.formula("no_participant_teaching_slope").endswith(
        "(1 | unit_id) + (1 | person_id) + (1 | item_id)"
    )
    assert b.columns[:4] == ("y", "unit_id", "person_id", "item_id")
    with pytest.raises(ValueError):
        model_specs("C")


def model_rows(tmp_path, scenario, late=False):
    """Load a pilot-size synthetic study (one held W1 visit made late when ``late``) and
    return (study data, the visit made late, {model_id: (header, rows)})."""
    from av_analysis.derived import ENDPOINTS, TRIALS, parse_table, table_bytes
    from av_analysis.fileio import parse_csv
    from av_analysis.glmm import model_data
    from av_analysis.paths import DataRoot, write_output
    from av_analysis.pipeline import load_study
    from av_analysis.report import write_dataset
    from av_analysis.simulate import scenarios, simulate_dataset

    root = DataRoot.create(tmp_path / "root", "SYNTHETIC", label="DEMO-glmm-data")
    sc = scenarios()[scenario]
    write_dataset(root, simulate_dataset(sc, "DEMO-glmm-data"))
    late_visit = None
    if late:
        trials = parse_table(TRIALS, (root.path / "derived" / "trials.csv").read_bytes())
        endpoints = parse_table(ENDPOINTS, (root.path / "derived" / "endpoints.csv").read_bytes())
        late_visit = next(
            e["visit_id"]
            for e in endpoints
            if (e["visit"], e["battery"]) == ("W1", "trained") and e["planned_endpoint"]
        )
        for row in (*endpoints, *trials):
            if row["visit_id"] == late_visit:
                row["timing"] = "late"
                if "planned_endpoint" in row:
                    row["planned_endpoint"] = False
        for name, table_spec, rows in (
            ("trials.csv", TRIALS, trials),
            ("endpoints.csv", ENDPOINTS, endpoints),
        ):
            data = table_bytes(table_spec, rows, "SYNTHETIC")
            write_output(root, "derived", name, data, "SYNTHETIC")
    data = load_study(root, sc.study)
    out = {
        s.model_id: parse_csv(
            model_data(s, data.opportunities, data.conditions, data_kind="SYNTHETIC")
        )
        for s in model_specs(sc.study)
    }
    return data, late_visit, out


def test_b_model_data_codes_role_format_and_timing(tmp_path):
    """B-trained data: role_c +0.5 exactly for the active member, format_c +0.5 exactly on
    the dyad's structured family, columns in spec order, late visits left out."""
    data, late_visit, out = model_rows(tmp_path, "pilot-B", late=True)
    header, rows = out["B-trained"]
    assert header == (
        "data_kind",
        "y",
        "unit_id",
        "person_id",
        "item_id",
        "family",
        "rep",
        "visit",
        "role_c",
        "format_c",
    )
    expected = [
        o
        for o in data.opportunities
        if o.battery == "trained"
        and o.row["visit"] in ("W1", "W4")
        and o.row["timing"] in ("in_window", "not_applicable")
    ]
    assert len(rows) == len(expected) > 0
    assert any(o.visit_id == late_visit for o in data.opportunities)
    cond = data.conditions
    for row, o in zip(rows, expected, strict=True):
        rec = dict(zip(header, row, strict=True))
        d = cond.dyads[rec["unit_id"]]
        assert rec["person_id"] == o.person_id and o.visit_id != late_visit
        assert (rec["y"], rec["item_id"], rec["family"]) == (
            str(o.score.y_operational),
            o.row["item_id"],
            o.family,
        )
        assert rec["role_c"] == ("0.5" if o.person_id == d.active_person else "-0.5")
        assert o.person_id in (d.active_person, d.yoked_person)
        assert rec["format_c"] == ("0.5" if o.family == d.structured_family else "-0.5")
        assert rec["visit"] == o.row["visit"] and rec["rep"] == str(o.row["pass"])
    codes = {(r[8], r[9]) for r in rows}
    assert codes == {("0.5", "0.5"), ("0.5", "-0.5"), ("-0.5", "0.5"), ("-0.5", "-0.5")}
    late_person = late_visit.rsplit("-", 1)[0]
    assert not any(r[3] == late_person and r[7] == "W1" for r in rows)


def test_a_model_data_codes_method_designer_and_endpoint(tmp_path):
    data, _, out = model_rows(tmp_path, "pilot-A")
    cond = data.conditions
    for model_id in ("A-trained", "A-designer"):
        header, rows = out[model_id]
        assert header == ("data_kind", "y", "unit_id", "book_id", "person_id", "item_id") + tuple(
            next(s for s in model_specs("A") if s.model_id == model_id).factors
        )
        assert rows
        for row in rows:
            rec = dict(zip(header, row, strict=True))
            book = cond.books[cond.person_book[rec["person_id"]]]
            assert (rec["book_id"], rec["unit_id"]) == (book.book_id, book.unit_id)
            assert rec["endpoint"] in ("D0", "D7") and rec["profile"] == book.profile
            if model_id == "A-trained":
                assert rec["method"] == book.method
            else:
                assert rec["method_designer"] == (
                    f"A1-{book.designer}" if book.method == "A1" else book.method
                )
        assert {r[header.index("endpoint")] for r in rows} == {"D0", "D7"}


def test_ladder_steps_in_order_with_singular_fits():
    """Singular full model: correlations, then the dyad role slope, then the participant
    teaching slope are dropped, each step logged, until the intercept model is stable."""

    def decide(formula):
        if "role_c |" in formula or "role_c ||" in formula or "format_c ||" in formula:
            return result(
                singular=True, messages=["message: boundary (singular) fit: see help('isSingular')"]
            )
        return result(fixed=[("(Intercept)", 0.8), ("role_c", 0.4)])

    runner = Recorder(decide)
    fit = fit_ladder(spec("B-trained"), DATA, data_kind="SYNTHETIC", runner=runner)
    log = fit.log
    assert [a.rung for a in log.attempts] == list(LADDER[:4])
    assert [a.status for a in log.attempts] == ["failed", "failed", "failed", "accepted"]
    assert all(a.singular for a in log.attempts[:3]) and all(a.converged for a in log.attempts)
    assert "singular" in log.attempts[0].reason and log.attempts[0].messages
    assert [c["formula"] for c in runner.calls] == [a.formula for a in log.attempts]
    assert ladder_order_ok(log.attempts) and log.final_rung == "no_participant_teaching_slope"
    assert [f.term for f in fit.fixed] == ["(Intercept)", "role_c"] and fit.n_obs == 1234
    assert fit.random[0].term2 is None
    doc = log.document()
    validator("glmm-log.schema.json").validate(doc)
    assert doc["engine"]["lme4_version"] == PINS["lme4"]
    assert [a["step"] for a in doc["attempts"]] == [1, 2, 3, 4]


def test_ladder_ends_descriptive_when_nothing_is_stable():
    fit = fit_ladder(
        spec("B-trained"),
        DATA,
        data_kind="SYNTHETIC",
        runner=Recorder(lambda f: result(converged=False)),
    )
    rungs = [(a.rung, a.status) for a in fit.log.attempts]
    assert rungs == [(r, "failed") for r in LADDER[:4]] + [("descriptive", "accepted")]
    assert fit.log.final_rung == "descriptive" and fit.fixed == ()
    assert fit.log.attempts[0].reason == "did not converge"
    validator("glmm-log.schema.json").validate(fit.log.document())


def test_study_a_has_one_fitted_rung_and_skips_the_slopes():
    ok = fit_ladder(
        spec("A-trained"),
        DATA,
        data_kind="SYNTHETIC",
        runner=Recorder(lambda f: result(fixed=[("methodA3", 0.5)])),
    )
    assert [(a.rung, a.status) for a in ok.log.attempts] == [("full", "accepted")]
    bad = fit_ladder(
        spec("A-trained"),
        DATA,
        data_kind="SYNTHETIC",
        runner=Recorder(lambda f: result(error="boom")),
    )
    statuses = [(a.rung, a.status) for a in bad.log.attempts]
    assert statuses == [
        ("full", "failed"),
        ("no_correlations", "skipped"),
        ("no_dyad_role_slope", "skipped"),
        ("no_participant_teaching_slope", "skipped"),
        ("descriptive", "accepted"),
    ]
    assert bad.log.attempts[0].reason == "error: boom" and "Study A" in bad.log.attempts[1].reason
    assert ladder_order_ok(bad.log.attempts)


def test_runner_errors_propagate_and_not_run_logs_are_valid():
    def broken(script, request, files):
        raise RuntimeError("Rscript not found")

    with pytest.raises(RuntimeError):
        fit_ladder(spec("A-trained"), DATA, data_kind="SYNTHETIC", runner=broken)
    log = not_run_log(spec("B-trained"), DATA, data_kind="SYNTHETIC", reason="R not available")
    assert [a.status for a in log.attempts] == ["skipped"] * 4 + ["accepted"]
    assert log.engine is None and log.final_rung == "descriptive" and ladder_order_ok(log.attempts)
    validator("glmm-log.schema.json").validate(log.document())


def test_version_problems():
    assert version_problems(PINS) == []
    dashed = {**PINS, "lme4": PINS["lme4"].replace("-", ".")}
    assert version_problems(dashed) == []  # 2.0-6 == 2.0.6
    problems = version_problems({**PINS, "lme4": "1.1-35"})
    assert len(problems) == 1 and "lme4" in problems[0]
    assert version_problems({}) != []


FAKE_R = textwrap.dedent(
    """
    import json, sys, time
    from pathlib import Path
    assert sys.argv[1] == "--vanilla" and sys.argv[2].endswith(".R")
    req_path = Path(sys.argv[3])
    req = json.loads(req_path.read_text())
    pins = json.loads(PINS_JSON)
    mode = req.get("mode", "glmm" if "formula" in req else "ok")
    out = req_path.parent / "result.json"
    if mode == "exit":
        sys.stderr.write("R failed on purpose")
        sys.exit(3)
    if mode == "sleep":
        time.sleep(30)
    if mode == "no_result":
        sys.exit(0)
    if mode == "not_json":
        out.write_text("nope")
        sys.exit(0)
    if mode == "bad_versions":
        pins["lme4"] = "0.9-1"
    names = sorted(p.name for p in req_path.parent.iterdir())
    body = {"versions": pins, "echo": req, "files": names}
    if mode == "no_versions":
        body = {"echo": req}
    if mode == "glmm":
        body.update(optimizer="bobyqa", error=None, converged=True, singular=False, messages=[],
                    fixed=[{"term": "methodA3", "estimate": 0.4, "se": 0.1, "z": 4.0, "p": 0.0001}],
                    random=[], n_obs=2)
    out.write_text(json.dumps(body))
    """
)


@pytest.fixture
def fake_rscript(tmp_path, monkeypatch):
    if sys.platform == "win32":
        pytest.skip("the fake Rscript is a POSIX shell wrapper")
    script = tmp_path / "fake_r.py"
    script.write_text(f"PINS_JSON = {json.dumps(json.dumps(PINS))}\n" + FAKE_R, encoding="utf-8")
    wrapper = tmp_path / "Rscript"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("AV_RSCRIPT", str(wrapper))
    return wrapper


def test_run_r_contract_with_a_fake_rscript(fake_rscript):
    out = run_r("glmm.R", {"mode": "ok", "x": 1}, {"data.csv": b"a\n1\n"})
    assert out["echo"] == {"mode": "ok", "x": 1}
    assert out["files"] == ["data.csv", "request.json"]
    for mode, match in (
        ("exit", "exited 3"),
        ("no_result", "wrote no"),
        ("not_json", "not JSON"),
        ("bad_versions", "pins.dcf"),
        ("no_versions", "versions"),
    ):
        with pytest.raises(RuntimeError, match=match):
            run_r("glmm.R", {"mode": mode}, {})
    with pytest.raises(RuntimeError, match="timed out"):
        run_r("glmm.R", {"mode": "sleep"}, {}, timeout_s=0.5)
    with pytest.raises(RuntimeError, match="unknown R script"):
        run_r("missing.R", {}, {})
    for bad in ("request.json", "a/b.csv", ""):
        with pytest.raises(ValueError):
            run_r("glmm.R", {}, {bad: b""})
    log = fit_with_ladder(spec("A-trained"), DATA, data_kind="SYNTHETIC")
    assert log.final_rung == "full" and log.engine is not None and log.engine.r_version == PINS["R"]


def test_run_r_without_r(monkeypatch, tmp_path):
    monkeypatch.setenv("AV_RSCRIPT", str(tmp_path / "no-such-Rscript"))
    with pytest.raises(RuntimeError, match="Rscript not found"):
        run_r("glmm.R", {}, {})


# ---------------------------------------------------------------------------------------
# Real lme4 fits (CI job "r")


def balanced_b_data() -> bytes:
    """Study B trials where every person, dyad, item, role and format has exactly half
    correct answers: every random-effect variance is estimated at 0 (singular)."""
    rows = []
    for d in range(1, 9):
        for m, role in ((1, 0.5), (2, -0.5)):
            for i in range(18):
                family = "K" if i < 9 else "Q"
                structured = (family == "K") == (d % 2 == 0)
                for rep in (1, 2):
                    for v, visit in enumerate(("W1", "W4")):
                        y = (i + rep + v) % 2
                        rows.append(
                            [
                                "SYNTHETIC",
                                str(y),
                                f"B-C{d:02d}",
                                f"B-C{d:02d}-M{m}",
                                f"{family}-a{i % 9 // 3 + 1}-r{i % 3 + 1}",
                                family,
                                str(rep),
                                visit,
                                repr(role),
                                "0.5" if structured else "-0.5",
                            ]
                        )
    b = spec("B-trained")
    header = ("data_kind", *b.columns)
    order = {
        name: i
        for i, name in enumerate(
            (
                "data_kind",
                "y",
                "unit_id",
                "person_id",
                "item_id",
                "family",
                "rep",
                "visit",
                "role_c",
                "format_c",
            )
        )
    }
    return csv_bytes(header, ([r[order[c]] for c in header] for r in rows))


@pytest.mark.needs_r
def test_singular_fit_fixture_walks_the_ladder_with_lme4():
    fit = fit_ladder(spec("B-trained"), balanced_b_data(), data_kind="SYNTHETIC")
    log = fit.log
    rungs = [(a.rung, a.status) for a in log.attempts]
    assert rungs == [(r, "failed") for r in LADDER[:4]] + [("descriptive", "accepted")], rungs
    assert all(a.singular for a in log.attempts[:4]), [a.messages for a in log.attempts]
    assert log.engine is not None and log.engine.lme4_version == PINS["lme4"]
    assert log.engine.r_version == PINS["R"]
    assert ladder_order_ok(log.attempts)
    validator("glmm-log.schema.json").validate(log.document())


@pytest.mark.needs_r
def test_lme4_fits_a_simulated_study_a_model(tmp_path):
    from av_analysis.glmm import model_data
    from av_analysis.paths import DataRoot
    from av_analysis.pipeline import load_study
    from av_analysis.report import write_dataset
    from av_analysis.simulate import scenarios, simulate_dataset

    root = DataRoot.create(tmp_path / "root", "SYNTHETIC", label="DEMO-glmm")
    write_dataset(root, simulate_dataset(scenarios()["central-A"], "DEMO-glmm"))
    data = load_study(root, "A")
    s = spec("A-trained")
    csv = model_data(s, data.opportunities, data.conditions, data_kind="SYNTHETIC")
    fit = fit_ladder(s, csv, data_kind="SYNTHETIC")
    assert ladder_order_ok(fit.log.attempts)
    if fit.log.final_rung == "full":
        terms = {f.term: f for f in fit.fixed}
        assert {"(Intercept)", "methodA1", "methodA3"} <= set(terms)
        assert fit.n_obs == csv.count(b"\n") - 1
        a3 = terms["methodA3"]
        detail = f"methodA3 {a3.estimate:.3f} (SE {a3.se:.3f}, p {a3.p:.2g}), n {fit.n_obs}"
    else:
        detail = "; ".join(f"{a.rung} {a.status}: {a.reason}" for a in fit.log.attempts)
    validator("glmm-log.schema.json").validate(fit.log.document())
    warnings.warn(
        f"A-trained on central-A: final rung {fit.log.final_rung}; {detail}",
        UserWarning,
        stacklevel=1,
    )


def test_glmm_r_script_is_published():
    path = Path(__file__).resolve().parents[2] / "analysis" / "r" / "glmm.R"
    data = path.read_bytes()
    assert b"isSingular" in data and b"glmer" in data and b"result.json" in data
    assert b"\r\n" not in data
