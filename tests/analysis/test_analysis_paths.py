"""Data roots and the SYNTHETIC/REAL watermark rule."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from av_analysis import paths
from av_analysis.derived import ENDPOINTS, table_bytes
from av_analysis.fileio import json_bytes
from av_analysis.paths import (
    AREA_OWNER,
    INPUT_PATHS,
    MARKER,
    OUTPUT_AREAS,
    READ_ONLY_AREAS,
    DataRoot,
    WatermarkError,
    check_watermark,
    committable,
    parse_visit_id,
    visit_id,
    write_output,
    write_synthetic_input,
)
from av_analysis.schemas import validator

HAS_GIT = shutil.which("git") is not None


def synthetic(tmp_path, name="syn"):
    return DataRoot.create(tmp_path / name, "SYNTHETIC", label="DEMO-test")


def test_visit_ids():
    assert visit_id("A-C07-L03", "D0") == "A-C07-L03-D0"
    assert parse_visit_id("B-S02-M2-W4") == ("B-S02-M2", "W4")
    for bad in ("A-C07-L03-V1", "B-C01-M3-V1", "A-C07-L03", "P-0412-D0"):
        with pytest.raises(ValueError):
            parse_visit_id(bad)


def test_layout_constants():
    assert set(OUTPUT_AREAS) == set(AREA_OWNER)
    assert set(READ_ONLY_AREAS) == {"raw", "inputs", "keys"}
    assert INPUT_PATHS["book_key"].startswith("keys/")
    assert all(p.startswith("inputs/") for k, p in INPUT_PATHS.items() if k != "book_key")


def test_create_and_open_synthetic_root(tmp_path):
    root = synthetic(tmp_path)
    assert root.synthetic and root.label == "DEMO-test" and root.study == "both"
    marker = json.loads((root.path / MARKER).read_text(encoding="utf-8"))
    validator("data-root.schema.json").validate(marker)
    assert DataRoot.open(root.path) == root
    assert DataRoot.create(root.path, "SYNTHETIC", label="DEMO-test") == root
    with pytest.raises(WatermarkError, match="other settings"):
        DataRoot.create(root.path, "SYNTHETIC", label="DEMO-other")
    assert root.raw_visit_dir("A-C01-L01-D0") == root.path / "raw" / "A-C01-L01-D0"
    with pytest.raises(ValueError):
        root.area("tmp")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("kind", "label", "kwargs"),
    [
        ("SYNTHETIC", "plain", {}),
        ("REAL", "DEMO-x", {}),
        ("FAKE", "DEMO-x", {}),
        ("SYNTHETIC", "DEMO-x", {"study": "C"}),
        ("SYNTHETIC", "DEMO x", {}),
    ],
)
def test_create_refuses_bad_settings(tmp_path, kind, label, kwargs):
    with pytest.raises(ValueError):
        DataRoot.create(tmp_path / "r", kind, label=label, **kwargs)


def test_open_refuses_missing_or_invalid_markers(tmp_path):
    with pytest.raises(WatermarkError, match="not a data root"):
        DataRoot.open(tmp_path)
    (tmp_path / MARKER).write_text('{"format": "x"}', encoding="utf-8")
    with pytest.raises(WatermarkError, match="invalid data-root marker"):
        DataRoot.open(tmp_path)


def test_roots_of_different_kinds_never_nest(tmp_path):
    outer = synthetic(tmp_path)
    with pytest.raises(WatermarkError, match="inside a SYNTHETIC data root"):
        DataRoot.create(outer.path / "inner", "REAL", label="real-1")
    real = DataRoot.create(tmp_path / "real", "REAL", label="real-1")
    assert not real.synthetic
    with pytest.raises(WatermarkError, match="inside a REAL data root"):
        DataRoot.create(real.path / "demo", "SYNTHETIC", label="DEMO-x")
    (real.path / "sub").mkdir()
    (real.path / "sub" / MARKER).write_text("{}", encoding="utf-8")
    with pytest.raises(WatermarkError, match="invalid data_kind"):
        DataRoot.open(real.path)
    other = tmp_path / "parent"
    DataRoot.create(other / "child", "SYNTHETIC", label="DEMO-c")
    with pytest.raises(WatermarkError, match="contains a SYNTHETIC data root"):
        DataRoot.create(other, "REAL", label="real-2")


@pytest.mark.skipif(not HAS_GIT, reason="git not installed")
def test_real_root_refused_inside_a_git_work_tree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    assert committable(repo / "data")
    assert not committable(repo / "ignored" / "data")
    assert not committable(tmp_path / "outside")
    with pytest.raises(WatermarkError, match="git work tree"):
        DataRoot.create(repo / "data", "REAL", label="real-1")
    assert DataRoot.create(repo / "ignored" / "data", "REAL", label="real-1").data_kind == "REAL"
    assert DataRoot.create(repo / "demo", "SYNTHETIC", label="DEMO-x").synthetic


def test_committable_fails_closed_without_git(tmp_path, monkeypatch):
    with_git = tmp_path / "a"
    (with_git / ".git").mkdir(parents=True)
    (tmp_path / "b").mkdir()
    monkeypatch.setattr(paths, "_GIT", "av-no-such-git-binary")
    assert committable(with_git / "data")
    assert not committable(tmp_path / "b" / "data")


def test_output_paths_stay_in_output_areas(tmp_path):
    root = synthetic(tmp_path)
    assert root.output_path("derived", "a/b.csv") == root.path / "derived" / "a" / "b.csv"
    for area in READ_ONLY_AREAS:
        with pytest.raises(WatermarkError, match="read-only"):
            root.output_path(area, "x.json")
    for rel in ("../x.json", "/abs.json", "a/./b.json", "", "C:/x.json", "a\\b.json"):
        with pytest.raises(WatermarkError, match="invalid output path"):
            root.output_path("derived", rel)


def test_write_output_checks_kind_and_watermark(tmp_path, sample_row):
    root = synthetic(tmp_path)
    doc = json_bytes({"data_kind": "SYNTHETIC", "x": 1})
    path = write_output(root, "reconciled", "A-C01-L01-D0/reconciliation.json", doc, "SYNTHETIC")
    assert path.read_bytes() == doc
    with pytest.raises(WatermarkError, match="holds SYNTHETIC data"):
        write_output(root, "reconciled", "x.json", json_bytes({"data_kind": "REAL"}), "REAL")
    with pytest.raises(WatermarkError, match="lacks top-level data_kind"):
        write_output(root, "reconciled", "x.json", json_bytes({"x": 1}), "SYNTHETIC")
    csv_data = table_bytes(ENDPOINTS, [sample_row(ENDPOINTS)], "SYNTHETIC")
    write_output(root, "derived", "endpoints.csv", csv_data, "SYNTHETIC")
    real = DataRoot.create(tmp_path / "real", "REAL", label="real-1")
    with pytest.raises(WatermarkError, match="holds REAL data"):
        write_output(real, "derived", "endpoints.csv", csv_data, "SYNTHETIC")
    with pytest.raises(WatermarkError, match="CSV output"):
        write_output(real, "derived", "endpoints.csv", csv_data, "REAL")
    assert not (real.path / "derived").exists()


def test_check_watermark_rules():
    check_watermark(b'{"data_kind": "REAL"}', ".json", "REAL")
    for data in (b"[1]", b"{", b'{"data_kind": "SYNTHETIC"}'):
        with pytest.raises(WatermarkError):
            check_watermark(data, ".json", "REAL")
    check_watermark(b"data_kind,x\nREAL,1\n", ".csv", "REAL")
    with pytest.raises(WatermarkError):
        check_watermark(b"x,data_kind\n1,REAL\n", ".csv", "REAL")
    html = '<meta name="av-data-kind" content="SYNTHETIC"><p>SYNTHETIC data</p>'
    check_watermark(html.encode(), ".html", "SYNTHETIC")
    with pytest.raises(WatermarkError, match="banner"):
        check_watermark(b'<meta name="av-data-kind" content="SYNTHETIC">', ".html", "SYNTHETIC")
    with pytest.raises(WatermarkError, match="marker"):
        check_watermark(html.encode(), ".html", "REAL")
    check_watermark(b'<meta name="av-data-kind" content="REAL">', ".html", "REAL")
    md = "# Report\n\nav-data-kind: SYNTHETIC\n\n**SYNTHETIC** data\n"
    check_watermark(md.encode(), ".md", "SYNTHETIC")
    check_watermark(b"# Report\n\nav-data-kind: REAL\n", ".md", "REAL")
    with pytest.raises(WatermarkError, match="marker"):
        check_watermark(md.encode(), ".md", "REAL")
    with pytest.raises(WatermarkError, match="marker"):
        check_watermark(b"# Report\n", ".md", "SYNTHETIC")
    with pytest.raises(WatermarkError, match="not strict CSV"):
        check_watermark(b"data_kind,data_kind\n", ".csv", "REAL")
    with pytest.raises(WatermarkError, match="not UTF-8"):
        check_watermark(b"\xff", ".md", "REAL")
    with pytest.raises(WatermarkError, match="no watermark rule"):
        check_watermark(b"x", ".png", "SYNTHETIC")


def test_input_paths_stay_in_input_areas(tmp_path):
    root = synthetic(tmp_path)
    assert root.input_path("keys", "A/pilot-book-key.json") == (
        root.path / "keys" / "A" / "pilot-book-key.json"
    )
    with pytest.raises(WatermarkError, match="not an input area"):
        root.input_path("derived", "x.csv")
    with pytest.raises(WatermarkError, match="invalid input path"):
        root.input_path("inputs", "../x.json")


def test_write_synthetic_input_is_the_only_writer_of_read_only_areas(tmp_path):
    root = synthetic(tmp_path)
    manifest = json_bytes({"data_kind": "SYNTHETIC", "files": []})
    path = write_synthetic_input(root, "raw", "A-C01-L01-D0/exit-manifest.json", manifest)
    assert path.read_bytes() == manifest
    write_synthetic_input(root, "raw", "A-C01-L01-D0/trial-log.csv", b"study\nA\n")
    write_synthetic_input(root, "raw", "deviations-log.csv", b"deviation_id\n")
    key = json_bytes({"demo": True, "format": "x"})
    write_synthetic_input(root, "keys", "A/pilot-book-key.json", key)
    write_synthetic_input(root, "inputs", "reveal/A-pilot.jsonl", b'{"line": 1}\n')
    # Replacing a file is allowed (fault injection rewrites raw files).
    write_synthetic_input(root, "raw", "A-C01-L01-D0/trial-log.csv", b"study\nB\n")
    refusals = [
        ("derived", "trials.csv", b"data_kind\nSYNTHETIC\n", "not an input area"),
        ("raw", "../x.csv", b"", "invalid input path"),
        ("raw", "notes.csv", b"a\n", "invalid raw path"),
        ("raw", "A-C01-L01-D0", b"a\n", "invalid raw path"),
        ("raw", "A-C01-L01-D0/sub/x.csv", b"a\n", "invalid raw path"),
        ("raw", "A-C01-L01-D0/exit-manifest.json", json_bytes({"files": []}), "exit manifest"),
        ("raw", "A-C01-L01-D0/exit-manifest.json", b"[1]", "exit manifest"),
        ("inputs", "x.json", json_bytes({"data_kind": "REAL"}), "not SYNTHETIC"),
        ("inputs", "x.json", json_bytes({"demo": False}), "demo is not true"),
        ("inputs", "x.json", b"{", "not valid JSON"),
        ("inputs", "x.csv", b"data_kind,a\nREAL,1\n", "data_kind column"),
        ("inputs", "x.csv", b"a,a\n", "not strict CSV"),
    ]
    for area, rel, data, message in refusals:
        with pytest.raises(WatermarkError, match=message):
            write_synthetic_input(root, area, rel, data)
    real = DataRoot.create(tmp_path / "real", "REAL", label="real-1")
    with pytest.raises(WatermarkError, match="holds REAL data"):
        write_synthetic_input(real, "inputs", "x.json", json_bytes({"demo": True}))
    assert not (real.path / "inputs").exists()
