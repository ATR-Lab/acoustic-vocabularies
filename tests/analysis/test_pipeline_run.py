"""#34 ``av-analysis run``: end-to-end runs, the section 9 report, refusals.

The module fixture runs the pilot-size scenarios (fast). Set ``AV_FULL_E2E=1`` to run it
on the full-size scenarios (216 learners, 128 people) as ``analysis/ci/34.sh`` does on
every CI runner; the ``needs_r`` test at the end always runs full size with lme4.
"""

from __future__ import annotations

import json
import os
import re
import time
import warnings
from pathlib import Path

import pytest

from av_analysis.cli import main
from av_analysis.derived import ENDPOINTS, TRIALS, parse_table, table_bytes
from av_analysis.fileio import parse_csv, sha256_file
from av_analysis.glmm import ladder_order_ok
from av_analysis.paths import DataRoot, check_watermark, write_output, write_synthetic_input
from av_analysis.pipeline import PipelineError, analyze, run_analysis
from av_analysis.report import REPORT_SECTIONS, build_report, sections_for, write_dataset
from av_analysis.schemas import validator
from av_analysis.simulate import dataset_results, scenarios, simulate_dataset

SEED = "DEMO-o4.5.2-e2e"
LIMIT_S = 30 * 60  # acceptance: a full-size end-to-end run finishes in under 30 min


FULL = os.environ.get("AV_FULL_E2E") == "1"
SIZE = {"A": "central-A", "B": "central-B"} if FULL else {"A": "pilot-A", "B": "pilot-B"}
SET = "confirmatory" if FULL else "pilot"


def full_root(
    path: Path, glmm: str, names: tuple[str, ...] = ("central-A", "central-B")
) -> tuple[DataRoot, dict[str, float]]:
    root = DataRoot.create(path, "SYNTHETIC", label=SEED)
    timings = {}
    for name in names:
        sc = scenarios()[name]
        start = time.perf_counter()
        write_dataset(root, simulate_dataset(sc, SEED))
        run_analysis(root, sc.study, glmm=glmm)  # type: ignore[arg-type]
        timings[sc.study] = time.perf_counter() - start
    return root, timings


@pytest.fixture(scope="module")
def full(tmp_path_factory):
    return full_root(tmp_path_factory.mktemp("e2e") / "root", "skip", (SIZE["A"], SIZE["B"]))


@pytest.mark.skipif(not FULL, reason="set AV_FULL_E2E=1 (CI: analysis/ci/34.sh, needs_r test)")
def test_full_size_runs_finish_within_the_limit(full):
    root, timings = full
    endpoints = parse_table(ENDPOINTS, (root.path / "derived" / "endpoints.csv").read_bytes())
    persons = {s: {r["person_id"] for r in endpoints if r["study"] == s} for s in "AB"}
    assert (len(persons["A"]), len(persons["B"])) == (216, 128)
    assert all(t < LIMIT_S for t in timings.values()), timings


def test_report_follows_section_9_order_and_states_denominators(full):
    root, _ = full
    for study in "AB":
        path = root.path / "estimates" / f"report-{study}.md"
        text = path.read_text(encoding="utf-8")
        check_watermark(path.read_bytes(), ".md", "SYNTHETIC")
        headings = re.findall(r"^## (\d)\. (.+)$", text, flags=re.M)
        assert [int(n) for n, _ in headings] == [s.number for s in sections_for(study)]
        assert [t for _, t in headings] == [s.title for s in sections_for(study)]
        tables = re.findall(r"^\*\*Table (\d+)\. ", text, flags=re.M)
        lines = re.findall(
            r"^_Independent unit: \w+; \d+ of \d+ planned units contribute "
            r"\(\d+ missing\); \d+ trials\.",
            text,
            flags=re.M,
        )
        assert len(tables) == len(lines) >= 20
        header, rows = parse_csv(
            (root.path / "estimates" / "tables" / f"{study}-index.csv").read_bytes()
        )
        assert header[:3] == ("data_kind", "table", "section") and len(rows) == len(tables)
        order = [s.id for s in REPORT_SECTIONS]
        assert [order.index(r[2]) for r in rows] == sorted(order.index(r[2]) for r in rows)
        for r in rows:
            data = (root.path / "estimates" / r[3]).read_bytes()
            check_watermark(data, ".csv", "SYNTHETIC")


def test_primary_estimates_equal_the_simulation_fast_path(full):
    root, _ = full
    _, rows = parse_csv((root.path / "estimates" / "tables" / "A-06-a-primary.csv").read_bytes())
    fast = {r.contrast: r for r in dataset_results(scenarios()[SIZE["A"]], SEED, 0)}
    assert float(rows[0][4]) == pytest.approx(fast["A3-A2"].estimate * 100, abs=1e-9)
    assert float(rows[0][9]) == pytest.approx(fast["A3-A2"].p, abs=1e-12)
    _, rows = parse_csv((root.path / "estimates" / "tables" / "B-06-b-primary.csv").read_bytes())
    fastb = {r.contrast: r for r in dataset_results(scenarios()[SIZE["B"]], SEED, 0)}
    for row in rows:
        k = row[1][0]
        assert float(row[4]) == pytest.approx(fastb[k].estimate * 100, abs=1e-9)
        assert row[17] in ("true", "false")  # Holm decision present


def test_manifest_run_records_and_glmm_logs(full):
    root, _ = full
    est = root.path / "estimates"
    manifest = json.loads((est / "manifest.json").read_bytes())
    validator("outputs-manifest.schema.json").validate(manifest)
    listed = {f["path"]: f["sha256"] for f in manifest["files"]}
    on_disk = sorted(p.relative_to(est).as_posix() for p in est.rglob("*") if p.is_file())
    assert sorted(listed) == [p for p in on_disk if p != "manifest.json"]
    assert all(sha256_file(est / p) == h for p, h in listed.items())
    inputs = {i["path"] for i in manifest["inputs"]}
    assert {
        "derived/trials.csv",
        "derived/endpoints.csv",
        f"keys/A/{SET}-book-key.json",
        f"inputs/schedules/B/{SET}-dyads.json",
    } <= inputs
    assert {"DEMO-A-primary-bootstrap-v1", "DEMO-B-primary-bootstrap-v1-C"} <= set(
        manifest["seeds"]
    )
    for model in ("A-trained", "A-designer", "B-trained"):
        doc = json.loads((est / "glmm" / f"{model}.json").read_bytes())
        validator("glmm-log.schema.json").validate(doc)
        assert doc["final_rung"] == "descriptive" and doc["attempts"][-1]["rung"] == "descriptive"
        data = (est / "glmm" / f"{model}-data.csv").read_bytes()
        check_watermark(data, ".csv", "SYNTHETIC")
        assert doc["data_sha256"] == __import__("hashlib").sha256(data).hexdigest()


def pilot_root(tmp_path: Path, *names: str) -> DataRoot:
    root = DataRoot.create(tmp_path / "pilot", "SYNTHETIC", label="DEMO-pilot")
    for name in names:
        write_dataset(root, simulate_dataset(scenarios()[name], "DEMO-pilot"))
    return root


def test_rerun_gives_identical_bytes(tmp_path):
    root = pilot_root(tmp_path, "pilot-A")
    first = run_analysis(root, "A", glmm="skip", resamples=500)
    snapshot = {p: p.read_bytes() for p in first}
    second = run_analysis(root, "A", glmm="skip", resamples=500)
    assert sorted(first) == sorted(second)
    assert all(p.read_bytes() == snapshot[p] for p in second)


def test_fewer_than_two_complete_batches_is_reported_unavailable(tmp_path):
    root = pilot_root(tmp_path, "pilot-A")
    trials = parse_table(TRIALS, (root.path / "derived" / "trials.csv").read_bytes())
    endpoints = parse_table(ENDPOINTS, (root.path / "derived" / "endpoints.csv").read_bytes())
    from av_analysis.unmask import load_conditions

    cond = load_conditions(root, "A", "pilot")
    units = sorted(cond.planned)
    gone = {
        p
        for p, m in cond.person_condition.items()
        if m == "A3" and cond.person_unit[p] in units[1:]
    }
    trials = [r for r in trials if r["person_id"] not in gone]
    for e in endpoints:
        if e["person_id"] in gone:
            e.update(
                accounted_n=0,
                fault_n=0,
                lost_n=0,
                valid_delivery_n=0,
                retry_n=0,
                status="missing",
                missing_reason="withdrawn",
                planned_endpoint=False,
                reconciliation="not_run",
                visit_date=None,
                days_since_anchor=None,
                timing="unknown" if e["visit"] == "D7" else "not_applicable",
            )
    write_output(
        root, "derived", "trials.csv", table_bytes(TRIALS, trials, "SYNTHETIC"), "SYNTHETIC"
    )
    write_output(
        root,
        "derived",
        "endpoints.csv",
        table_bytes(ENDPOINTS, endpoints, "SYNTHETIC"),
        "SYNTHETIC",
    )
    report = analyze(root, "A", glmm="skip", resamples=200)
    row = report.sections["a_primary"].tables[0].rows[0]
    assert row[2] == 1 and row[8] is None and row[12] == "fewer than 2 complete differences"
    assert any("unavailable" in s for s in report.summary)
    build_report(root, "A")  # the report still renders with the unavailable estimator


def test_refusals(tmp_path, capsys, monkeypatch):
    root = pilot_root(tmp_path, "pilot-B")
    with pytest.raises(PipelineError, match="no Study A"):
        analyze(root, "A")
    with pytest.raises(PipelineError, match="unknown study"):
        analyze(root, "C")
    endpoints = parse_table(ENDPOINTS, (root.path / "derived" / "endpoints.csv").read_bytes())
    held = next(e for e in endpoints if e["accounted_n"])
    held["reconciliation"] = "fail"
    write_output(
        root,
        "derived",
        "endpoints.csv",
        table_bytes(ENDPOINTS, endpoints, "SYNTHETIC"),
        "SYNTHETIC",
    )
    with pytest.raises(PipelineError, match="did not pass reconciliation"):
        analyze(root, "B")
    assert main(["run", "--study", "B", "--data", str(root.path)]) == 2
    assert "refusing" in capsys.readouterr().err
    empty = DataRoot.create(tmp_path / "empty", "SYNTHETIC", label="DEMO-empty")
    with pytest.raises(PipelineError, match="missing derived"):
        analyze(empty, "A")
    # Lists of another allocation: the slot list's book IDs differ from the tables'.
    other = pilot_root(tmp_path / "x", "pilot-A")
    for rel, data in simulate_dataset(scenarios()["pilot-A"], "DEMO-another").files.items():
        area, _, sub = rel.partition("/")
        write_synthetic_input(other, area, sub, data)  # type: ignore[arg-type]
    with pytest.raises(PipelineError, match="book_id differs"):
        analyze(other, "A")
    # A person outside the allocation lists.
    third = pilot_root(tmp_path / "y", "pilot-A")
    endpoints = parse_table(ENDPOINTS, (third.path / "derived" / "endpoints.csv").read_bytes())
    first = endpoints[0]["person_id"]
    for e in endpoints:
        if e["person_id"] == first:
            e["person_id"] = "A-P01-L09"
            e["visit_id"] = f"A-P01-L09-{e['visit']}"
    write_output(
        third,
        "derived",
        "endpoints.csv",
        table_bytes(ENDPOINTS, endpoints, "SYNTHETIC"),
        "SYNTHETIC",
    )
    with pytest.raises(PipelineError, match="not in the A pilot allocation lists"):
        analyze(third, "A")


def test_run_command_and_glmm_modes(tmp_path, capsys):
    root = pilot_root(tmp_path, "pilot-A", "pilot-B")
    assert main(["run", "--study", "B", "--data", str(root.path), "--glmm", "skip"]) == 0
    out = capsys.readouterr().out
    assert "wrote estimates/report-B.md" in out and "wrote estimates/manifest.json" in out
    log = json.loads((root.path / "estimates" / "glmm" / "B-trained.json").read_bytes())
    assert "--glmm skip" in log["attempts"][0]["reason"]

    calls = []

    def runner(script, request, files):
        calls.append(request["formula"])
        from av_analysis.rbridge import r_pins

        pins = r_pins()
        return {
            "versions": {"R": pins.r_version, **pins.packages},
            "optimizer": "bobyqa",
            "error": None,
            "converged": True,
            "singular": False,
            "messages": [],
            "fixed": [{"term": "role_c", "estimate": 0.3, "se": 0.1, "z": 3.0, "p": 0.003}],
            "random": [],
            "n_obs": 10,
        }

    report = analyze(root, "B", glmm="require", runner=runner, resamples=200)
    (fit,) = report.glmm
    assert fit.log.final_rung == "full" and calls and ladder_order_ok(fit.log.attempts)
    glmm_table = next(t for t in report.sections["sensitivities"].tables if t.id == "sens-glmm")
    assert glmm_table.rows[0][3] == "role_c"

    def broken(script, request, files):
        raise RuntimeError("R exploded")

    with pytest.raises(PipelineError, match="R exploded"):
        analyze(root, "B", glmm="require", runner=broken)
    report = analyze(root, "B", glmm="auto", runner=broken, resamples=200)
    assert "R failed" in report.glmm[0].log.attempts[0].reason


def test_real_root_refuses_synthetic_tables(tmp_path, monkeypatch, capsys):
    import av_analysis.paths as paths

    monkeypatch.setattr(paths, "committable", lambda path: False)
    src = pilot_root(tmp_path, "pilot-A")
    real = DataRoot.create(tmp_path / "real", "REAL", label="study-x")
    (real.path / "derived").mkdir()
    for name in ("trials.csv", "endpoints.csv"):
        (real.path / "derived" / name).write_bytes((src.path / "derived" / name).read_bytes())
    assert main(["run", "--study", "A", "--data", str(real.path)]) == 2
    assert "data_kind" in capsys.readouterr().err


@pytest.mark.needs_r
def test_full_size_end_to_end_with_lme4_within_30_minutes(tmp_path):
    root, timings = full_root(tmp_path / "root", "require")
    warnings.warn(
        f"end-to-end with GLMMs: A {timings['A']:.1f} s, B {timings['B']:.1f} s",
        UserWarning,
        stacklevel=1,
    )
    assert all(t < LIMIT_S for t in timings.values()), timings
    ladders = []
    for model in ("A-trained", "A-designer", "B-trained"):
        doc = json.loads((root.path / "estimates" / "glmm" / f"{model}.json").read_bytes())
        validator("glmm-log.schema.json").validate(doc)
        assert doc["engine"] is not None and doc["attempts"][0]["status"] != "skipped"
        steps = ", ".join(
            f"{a['rung']}={a['status']}(converged={a['converged']}, singular={a['singular']}"
            f"{', ' + '; '.join(a['messages'])[:300] if a['status'] == 'failed' else ''})"
            for a in doc["attempts"]
        )
        ladders.append(f"{model}: {steps}; final {doc['final_rung']}")
    # Evidence in the CI log (the r job uploads no artifact).
    warnings.warn("GLMM ladders: " + " | ".join(ladders), UserWarning, stacklevel=1)
