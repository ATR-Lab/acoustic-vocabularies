"""Integrity dashboard (#35): command, refresh, outputs, watermark, determinism and timing."""

from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
import time

import pytest

from av_analysis import cli
from av_analysis.derived import TABLES, table_bytes
from av_analysis.fileio import sha256_bytes
from av_analysis.monitoring import OUTPUT_FILES, write_dashboard
from av_analysis.monitoring_demo import demo_tables, write_demo_root
from av_analysis.monitoring_demo import main as demo_main
from av_analysis.paths import DataRoot, check_watermark, write_output
from av_analysis.schemas import validator

SOURCES = ("visit-status", "discrepancies", "enrollment")


def _outputs(root) -> dict[str, bytes]:
    return {name: (root.area("monitoring") / name).read_bytes() for name in OUTPUT_FILES}


def test_dashboard_command_writes_three_watermarked_outputs(tmp_path, capsys):
    root = write_demo_root(tmp_path / "r", "DEMO-cli", sets=("pilot",), progress=0.5)
    assert cli.main(["dashboard", "--root", str(root.path)]) == 0
    assert "SYNTHETIC; data as of 2027-" in capsys.readouterr().out
    files = _outputs(root)
    check_watermark(files["index.html"], ".html", "SYNTHETIC")
    check_watermark(files["dashboard.json"], ".json", "SYNTHETIC")
    manifest = json.loads(files["manifest.json"])
    assert list(validator("outputs-manifest.schema.json").iter_errors(manifest)) == []
    assert manifest["area"] == "monitoring" and manifest["seeds"] == []
    assert [f["path"] for f in manifest["inputs"]] == [
        "reconciled/discrepancies.csv",
        "reconciled/enrollment.csv",
        "reconciled/visit-status.csv",
    ]
    for entry in manifest["inputs"]:
        data = (root.path / entry["path"]).read_bytes()
        assert (entry["bytes"], entry["sha256"]) == (len(data), sha256_bytes(data))
    for entry in manifest["files"]:
        assert entry["sha256"] == sha256_bytes(files[entry["path"]])
    html = files["index.html"].decode("utf-8")
    assert html.count("SYNTHETIC demonstration data (DEMO)") == 2  # top and bottom banner
    doc = json.loads(files["dashboard.json"])
    for entry in doc["inputs"]:
        assert entry["sha256"] in html  # the page shows the input hashes


def test_regeneration_is_deterministic_and_independent_of_the_root_path(tmp_path):
    a = write_demo_root(tmp_path / "a", "DEMO-same", sets=("pilot",), progress=0.4)
    b = write_demo_root(tmp_path / "elsewhere" / "b", "DEMO-same", sets=("pilot",), progress=0.4)
    write_dashboard(a)
    first = _outputs(a)
    write_dashboard(a)
    write_dashboard(b)
    assert _outputs(a) == first == _outputs(b)
    assert str(tmp_path) not in first["index.html"].decode("utf-8")


def test_last_update_is_the_latest_visit_or_reveal_date(tmp_path):
    root = write_demo_root(tmp_path / "r", "DEMO-asof", studies=("B",), sets=("pilot",))
    write_dashboard(root)
    doc = json.loads((root.area("monitoring") / "dashboard.json").read_text(encoding="utf-8"))
    tables = demo_tables("DEMO-asof", studies=("B",), sets=("pilot",))
    latest_visit = max(r["visit_date"] for r in tables["visit-status"] if r["visit_date"])
    latest_reveal = max(r["last_event_date"] for r in tables["enrollment"])
    assert doc["as_of"] == {
        "data_date": max(latest_visit, latest_reveal),
        "latest_visit_date": latest_visit,
        "latest_reveal_date": latest_reveal,
    }


def test_last_update_is_the_reveal_date_when_it_is_later_than_every_visit(tmp_path):
    root = write_demo_root(tmp_path / "r", "DEMO-asof-reveal", studies=("A",), sets=("pilot",))
    path = root.area("reconciled") / "enrollment.csv"
    header, *rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8"), newline="")))
    for row in rows:
        row[header.index("last_event_date")] = "2030-01-31"
    buf = io.StringIO(newline="")
    csv.writer(buf, lineterminator="\n").writerows([header, *rows])
    path.write_bytes(buf.getvalue().encode("utf-8"))
    write_dashboard(root)
    doc = json.loads((root.area("monitoring") / "dashboard.json").read_text(encoding="utf-8"))
    as_of = doc["as_of"]
    assert as_of["latest_visit_date"] < "2030-01-31"
    assert as_of["data_date"] == as_of["latest_reveal_date"] == "2030-01-31"
    html = (root.area("monitoring") / "index.html").read_text(encoding="utf-8")
    assert "<dt>Data as of</dt><dd>2030-01-31</dd>" in html


def test_empty_tables_give_an_empty_dashboard(tmp_path):
    root = DataRoot.create(tmp_path / "r", "SYNTHETIC", label="DEMO-empty")
    for name in SOURCES:
        spec = TABLES[name]
        write_output(
            root, "reconciled", spec.filename, table_bytes(spec, [], "SYNTHETIC"), "SYNTHETIC"
        )
    assert cli.main(["dashboard", "--root", str(root.path)]) == 0
    html = (root.area("monitoring") / "index.html").read_text(encoding="utf-8")
    assert "no data yet" in html and "No revealed units yet." in html


def test_refused_inputs_exit_2_and_write_nothing(tmp_path, capsys):
    assert cli.main(["dashboard", "--root", str(tmp_path / "missing")]) == 2
    assert "not a data root" in capsys.readouterr().err
    root = DataRoot.create(tmp_path / "r", "SYNTHETIC", label="DEMO-refused")
    assert cli.main(["dashboard", "--root", str(root.path)]) == 2
    assert "visit-status.csv is missing" in capsys.readouterr().err
    root = write_demo_root(tmp_path / "r2", "DEMO-refused", sets=("pilot",))
    path = root.area("reconciled") / "enrollment.csv"
    header, rest = path.read_text(encoding="utf-8").split("\n", 1)
    columns = header.split(",")
    path.write_text(",".join([columns[1], columns[0], *columns[2:]]) + "\n" + rest, "utf-8")
    assert cli.main(["dashboard", "--root", str(root.path)]) == 2
    assert "header differs" in capsys.readouterr().err
    path.write_bytes(b"\xff\xfe")
    assert cli.main(["dashboard", "--root", str(root.path)]) == 2
    assert "not UTF-8" in capsys.readouterr().err
    assert not root.area("monitoring").exists()


def test_rows_of_the_other_data_kind_are_refused(tmp_path, capsys):
    root = write_demo_root(tmp_path / "r", "DEMO-kind", sets=("pilot",))
    path = root.area("reconciled") / "enrollment.csv"
    path.write_text(path.read_text(encoding="utf-8").replace("SYNTHETIC", "REAL"), "utf-8")
    assert cli.main(["dashboard", "--root", str(root.path)]) == 2
    assert "data_kind is not SYNTHETIC" in capsys.readouterr().err


def test_a_real_root_gets_the_real_watermark_and_no_banner(tmp_path):
    root = DataRoot.create(tmp_path / "real", "REAL", label="test-real-root")
    for name, rows in demo_tables("DEMO-real", studies=("A",), sets=("pilot",)).items():
        spec = TABLES[name]
        real = [{**r, "data_kind": "REAL"} for r in rows]
        write_output(root, "reconciled", spec.filename, table_bytes(spec, real, "REAL"), "REAL")
    write_dashboard(root)
    files = _outputs(root)
    check_watermark(files["index.html"], ".html", "REAL")
    check_watermark(files["dashboard.json"], ".json", "REAL")
    assert b"SYNTHETIC" not in files["index.html"]


def test_refresh_regenerates_the_dashboard_after_reconcile_and_derive(
    tmp_path, monkeypatch, capsys
):
    """``av-analysis refresh`` runs the real dashboard step after (faked) #33 steps."""
    root = write_demo_root(
        tmp_path / "r", "DEMO-refresh", sets=("pilot",), inject=[("wrong_hash", None)]
    )
    real_build = cli.build_parser

    def build():
        parser, handlers = real_build()
        handlers["reconcile"] = lambda args: 1  # findings: refresh continues
        handlers["derive"] = lambda args: 0
        return parser, handlers

    monkeypatch.setattr(cli, "build_parser", build)
    assert cli.main(["refresh", "--root", str(root.path)]) == 1
    out = capsys.readouterr().out
    assert "refresh: dashboard exit 1" in out and "RED ALERT WRONG_FILE_MAPPING" in out
    assert (root.area("monitoring") / "index.html").is_file()


def test_demo_command(tmp_path, capsys):
    out = tmp_path / "demo"
    assert (
        demo_main(
            [
                "--out",
                str(out),
                "--seed",
                "DEMO-cmd",
                "--study",
                "B",
                "--set",
                "both",
                "--progress",
                "0.5",
                "--inject",
                "changed_old_atom=B-C01-M1-V2",
            ]
        )
        == 0
    )
    assert "visit-status rows" in capsys.readouterr().out
    root = DataRoot.open(out)
    assert (root.study, root.set_name, root.label) == ("B", "both", "DEMO-cmd")
    assert cli.main(["dashboard", "--root", str(out)]) == 1
    assert "B-C01-M1-V2" in capsys.readouterr().out
    for bad in (
        ["--seed", "plain-seed"],
        ["--progress", "0"],
        ["--inject", "nothing"],
    ):
        assert demo_main(["--out", str(tmp_path / "bad"), *bad]) == 2
        assert "refusing" in capsys.readouterr().err
    with pytest.raises(ValueError, match="studies must be"):
        demo_tables("DEMO-x", studies=("C",))


def test_demo_module_entry_point():
    result = subprocess.run(
        [sys.executable, "-m", "av_analysis.monitoring_demo", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0 and "--inject" in result.stdout


def test_regeneration_on_full_size_data_takes_well_under_60_seconds(tmp_path):
    """Proposed acceptance: regeneration < 60 s on full-size synthetic data (both studies,
    pilot and confirmatory sets: 1,188 expected visits)."""
    root = write_demo_root(
        tmp_path / "full",
        "DEMO-full",
        sets=("pilot", "confirmatory"),
        inject=[("wrong_hash", None), ("changed_old_atom", None), ("answer_leak", None)],
    )
    rows = (root.area("reconciled") / "visit-status.csv").read_text(encoding="utf-8")
    assert rows.count("\n") - 1 == (216 + 18) * 2 + (128 + 16) * 5
    start = time.perf_counter()
    write_dashboard(root)
    elapsed = time.perf_counter() - start
    print(f"dashboard regeneration on full-size synthetic data: {elapsed:.3f} s")
    assert elapsed < 60
