"""Commands of #33: ``synth-logs``, ``reconcile``, ``derive`` and the operator ``refresh``."""

from __future__ import annotations

import json
import shutil

import pytest

from av_analysis import synthetic_logs
from av_analysis.cli import main
from av_analysis.fileio import read_bytes, sha256_bytes
from av_analysis.synthetic_logs import FaultCase

SEED = "DEMO-test-33-cli"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("cli") / "root"
    assert main(["synth-logs", "--demo-seed", SEED, "--out", str(out), "--max-persons", "1"]) == 0
    return out


@pytest.fixture
def root(built, tmp_path):
    target = tmp_path / "root"
    shutil.copytree(built, target)
    return target


def raw_hashes(path):
    return {
        p.relative_to(path).as_posix(): sha256_bytes(read_bytes(p))
        for p in sorted((path / "raw").rglob("*"))
        if p.is_file()
    }


def test_synth_logs_builds_a_synthetic_root(built, capsys):
    marker = json.loads((built / "av-data-root.json").read_text(encoding="utf-8"))
    assert marker["data_kind"] == "SYNTHETIC" and marker["label"] == SEED
    assert len([p for p in (built / "raw").iterdir() if p.is_dir()]) == 2 + 10
    assert main(["synth-logs", "--demo-seed", SEED, "--out", str(built)]) == 2
    assert "not empty" in capsys.readouterr().err


@pytest.mark.parametrize(
    "extra",
    [["--demo-seed", "plain"], ["--demo-seed", SEED, "--fault", "extra_play"]],
)
def test_synth_logs_refuses_bad_arguments(tmp_path, extra, capsys):
    args = ["synth-logs", "--out", str(tmp_path / "x"), *extra]
    assert main(args) == 2
    assert "refusing" in capsys.readouterr().err


def test_reconcile_derive_and_refresh_on_a_clean_root(root, capsys):
    before = raw_hashes(root)
    assert main(["reconcile", "--root", str(root), "--all"]) == 0
    out = capsys.readouterr().out
    assert out.count(": pass (0 discrepancies, 0 unresolved)") == 12
    assert main(["derive", "--root", str(root)]) == 0
    assert "derive: wrote trials" in capsys.readouterr().out
    assert main(["refresh", "--root", str(root)]) == 0
    assert "refresh: reconcile exit 0" in capsys.readouterr().out
    assert raw_hashes(root) == before
    assert (root / "reconciled" / "visit-status.csv").is_file()
    assert (root / "derived" / "manifest.json").is_file()


def test_reconcile_reports_findings_and_refusals(root, tmp_path, capsys):
    vid = synthetic_logs.suite_visits(synthetic_logs.DataRoot.open(root))["D0"]
    args = [
        "synth-logs",
        "--demo-seed",
        SEED,
        "--out",
        str(root),
        "--fault",
        "wrong_hash",
        "--visit",
        vid,
    ]
    assert main(args) == 0
    assert "injected wrong_hash" in capsys.readouterr().out
    assert main(["reconcile", "--root", str(root), vid]) == 1
    assert "failed C3 C8" in capsys.readouterr().out
    assert main(["reconcile", "--root", str(tmp_path)]) == 2
    assert main(["reconcile", "--root", str(root), "not-a-visit"]) == 2
    assert main(["reconcile", "--root", str(root), "A-P03-L06-D0"]) == 2
    assert main(["reconcile", "--root", str(root)]) == 2  # no visit given
    empty = tmp_path / "empty"
    assert main(["init-root", str(empty), "--kind", "SYNTHETIC", "--label", "DEMO-e"]) == 0
    assert main(["reconcile", "--root", str(empty), "--all"]) == 0
    assert main(["derive", "--root", str(tmp_path / "nothing")]) == 2
    capsys.readouterr()


def test_derive_refuses_a_stale_report(root, capsys):
    assert main(["reconcile", "--root", str(root), "--all"]) == 0
    vid = synthetic_logs.suite_visits(synthetic_logs.DataRoot.open(root))["D7"]
    assert (
        main(
            [
                "synth-logs",
                "--demo-seed",
                SEED,
                "--out",
                str(root),
                "--fault",
                "extra_play",
                "--visit",
                vid,
                "--documented",
            ]
        )
        == 0
    )
    assert main(["derive", "--root", str(root)]) == 2
    assert "rerun reconcile" in capsys.readouterr().err


def test_fault_suite_and_examples_commands(tmp_path, monkeypatch, capsys):
    good = FaultCase(
        "extra_play",
        "D0",
        "A-P01-L01-D0",
        False,
        "COUNT_EXTRA_PLAY",
        True,
        False,
        True,
        "fail",
        ("COUNT_EXTRA_PLAY", "DEVIATION_MISSING"),
    )
    bad = FaultCase(
        "late_visit", "D7", "A-P01-L01-D7", True, "WINDOW_LATE", False, False, False, "pass", ()
    )
    monkeypatch.setattr(synthetic_logs, "fault_suite", lambda out, seed_label: [good])
    out = tmp_path / "suite"
    assert main(["synth-logs", "--demo-seed", SEED, "--out", str(out), "--fault-suite"]) == 0
    assert (out / "fault-suite.csv").read_bytes().startswith(b"fault,visit_type")
    assert "1/1 cases as expected" in capsys.readouterr().out
    monkeypatch.setattr(synthetic_logs, "fault_suite", lambda out, seed_label: [good, bad])
    assert (
        main(["synth-logs", "--demo-seed", SEED, "--out", str(tmp_path / "s2"), "--fault-suite"])
        == 1
    )
    assert "NOT OK: late_visit" in capsys.readouterr().err
    assert main(["synth-logs", "--demo-seed", SEED, "--out", str(out), "--fault-suite"]) == 2
    monkeypatch.setattr(synthetic_logs, "write_examples", lambda out, seed_label: ["a.json"])
    assert (
        main(["synth-logs", "--demo-seed", SEED, "--out", str(tmp_path / "ex"), "--examples"]) == 0
    )
    assert "wrote a.json" in capsys.readouterr().out
