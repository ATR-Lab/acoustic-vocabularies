"""Seeds, the portable random stream, written outputs, DEMO examples and the CLI (#29)."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from av_schedules import (
    MasterSeed,
    SeedStream,
    cli,
    demo_seed,
    derive_seed,
    generate,
    load_master_seed,
    private_seed,
    write_files,
)
from av_schedules._paths import default_out_dir, examples_dir
from av_schedules.cli import main
from av_schedules.latin import ORDER, CycleSquares
from av_schedules.output import EXAMPLE_DEMO_SEED, demo_example_files, table_name
from av_schedules.planning import synthetic_planning_files

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "schedules" / "examples" / "demo"
PRIVATE = "0123456789abcdef" * 4  # synthetic stand-in for a private seed (test only)
PRIVATE_2 = "fedcba9876543210" * 4


# ---------------------------------------------------------------------------------------
# Seeds


def test_derive_seed_formula():
    master = demo_seed("DEMO-x")
    expected = hashlib.sha256(b"DEMO-x|A|A-C01|unit").hexdigest()
    assert derive_seed(master, "A", "A-C01", "unit") == expected
    longer = hashlib.sha256(b"DEMO-x|B|B-C01|p1|V2|trained").hexdigest()
    assert derive_seed(master, "B", "B-C01", "p1", "V2", "trained") == longer
    with pytest.raises(ValueError):
        derive_seed(master, "A", "A|C01", "unit")
    with pytest.raises(ValueError):
        derive_seed(master)


@pytest.mark.parametrize("value", ["demo-x", "DEMO-", "DEMO-a b", "X-DEMO-1", "DEMO-" + "a" * 65])
def test_demo_seed_must_start_with_demo(value):
    with pytest.raises(ValueError):
        demo_seed(value)


def test_private_seed_rules(tmp_path):
    with pytest.raises(ValueError, match="DEMO-"):
        private_seed("DEMO-" + "a" * 40)
    for weak in ("short", "a" * 63, PRIVATE.upper(), "g" * 64, "a" * 257):
        with pytest.raises(ValueError):
            private_seed(weak)
    assert private_seed("a" * 64).value == "a" * 64
    path = tmp_path / "seed.txt"
    path.write_text(PRIVATE + "\n", encoding="utf-8")
    master = load_master_seed(path)
    assert master == MasterSeed(PRIVATE, demo=False)
    assert PRIVATE not in repr(master)
    assert master.label == "sha256:" + hashlib.sha256(PRIVATE.encode()).hexdigest()
    assert demo_seed("DEMO-x").label == "DEMO-x"


def test_seed_stream_reference_values():
    # Frozen reference outputs: any change to the stream definition breaks reproducibility.
    s = SeedStream("00" * 32)
    draws = [s.randbelow(1000) for _ in range(5)]
    perm = SeedStream("00" * 32).permutation(range(8))
    digest = hashlib.sha256(b"00" * 32 + b"#0").digest()
    assert int.from_bytes(digest[:8], "big") % 1000 == draws[0]
    assert draws == REFERENCE_DRAWS
    assert perm == REFERENCE_PERMUTATION


REFERENCE_DRAWS = [167, 114, 60, 989, 497]
REFERENCE_PERMUTATION = (3, 2, 0, 6, 5, 4, 1, 7)


@settings(max_examples=200, deadline=None)
@given(st.text(min_size=1, max_size=16), st.integers(min_value=1, max_value=50))
def test_seed_stream_permutation_property(seed, n):
    s = SeedStream(seed)
    assert sorted(s.permutation(range(n))) == list(range(n))
    assert 0 <= s.randbelow(n) < n


def test_seed_stream_is_roughly_uniform():
    s = SeedStream("uniformity")
    counts = Counter(s.randbelow(6) for _ in range(6000))
    assert all(900 < counts[k] < 1100 for k in range(6))


@settings(max_examples=100, deadline=None)
@given(st.text(min_size=1, max_size=16))
def test_latin_squares_property(seed):
    sq = CycleSquares.draw(SeedStream(seed))
    grid = [(r, c) for r in range(ORDER) for c in range(ORDER)]

    def latin(k):
        return lambda r, c: sq.latin(k, r, c)

    squares = (sq.a, sq.b, latin(1), latin(2), latin(3))
    for square in squares:
        for r in range(ORDER):
            assert sorted(square(r, c) for c in range(ORDER)) == [0, 1, 2, 3]
        for c in range(ORDER):
            assert sorted(square(r, c) for r in range(ORDER)) == [0, 1, 2, 3]
    for j, k in ((1, 2), (1, 3), (2, 3)):  # mutually orthogonal
        assert len({(sq.latin(j, r, c), sq.latin(k, r, c)) for r, c in grid}) == 16
        for r in range(ORDER):  # L_j ^ L_k depends on the row only
            assert len({sq.latin(j, r, c) ^ sq.latin(k, r, c) for c in range(ORDER)}) == 1
    assert len({(sq.a(r, c), sq.b(r, c)) for r, c in grid}) == 16
    assert len({(sq.a(r, c), sq.split(r, c)) for r, c in grid}) == 16
    for pair in ((0, 1), (2, 3), (0, 2), (1, 3)):  # arm and swap column pairs
        cols = [(r, c) for r in range(ORDER) for c in pair]
        assert len({(sq.a(r, c), sq.split(r, c)[0]) for r, c in cols}) == 8
    for rows in ((0, 1), (2, 3)):  # half cycles
        for c in range(ORDER):
            assert {sq.split(r, c)[0] for r in rows} == {0, 1}
        half = [(r, c) for r in rows for c in range(ORDER)]
        assert len({(sq.a(r, c), sq.split(r, c)[0]) for r, c in half}) == 8


# ---------------------------------------------------------------------------------------
# Rendered sets


@pytest.mark.parametrize(("study", "set_name"), [("A", "confirmatory"), ("B", "pilot")])
def test_rendered_set_manifest_hashes_every_file(study, set_name):
    files = generate(demo_seed("DEMO-manifest"), study, set_name)
    manifest = json.loads(files[f"{set_name}-manifest.json"])
    assert manifest["demo"] is True and manifest["seed_label"] == "DEMO-manifest"
    assert set(manifest["files"]) == set(files) - {f"{set_name}-manifest.json"}
    for path, digest in manifest["files"].items():
        assert hashlib.sha256(files[path]).hexdigest() == digest
    assert table_name(study, set_name) in files
    assert all(data.endswith(b"\n") and b"\r" not in data for data in files.values())


def test_balance_report_rows():
    files = generate(demo_seed("DEMO-balance"), "B", "confirmatory")
    lines = files["confirmatory-balance.csv"].decode().splitlines()
    assert lines[0] == (
        "study,set,metric,scope,family,role,item,position,count,expected,deviation,seed_label"
    )
    assert all(line.endswith(",DEMO-balance") for line in lines[1:])
    label_rows = [line for line in lines if ",label_index,all," in line]
    assert len(label_rows) == 64
    assert all(line.endswith(",16,16,0,DEMO-balance") for line in label_rows)
    cell_rows = [line for line in lines if ",message_cell,all," in line]
    assert len(cell_rows) == 2 * 16 * 16
    assert all(",4,4,0," in line for line in cell_rows)  # 4 cycles: each message 4x per cell
    held = [line for line in lines if ",message_heldout,all," in line]
    assert len(held) == 32 and all(",28,28,0," in line for line in held)
    assert sum(1 for line in lines if ",message_novel,all," in line) == 32 * 5
    assert sum(1 for line in lines if ",wave1_label_position,all," in line) == 16 * 8
    a_lines = generate(demo_seed("DEMO-balance"), "A", "confirmatory")["confirmatory-balance.csv"]
    a_text = a_lines.decode()
    assert a_text.count(",label_position,all,") == 16 * 16
    assert a_text.count(",atom_position,all,") == 16 * 16


def test_write_files_removes_stale_units(tmp_path):
    master = demo_seed("DEMO-stale")
    write_files(tmp_path, generate(master, "B", "confirmatory", spares=8))
    assert (tmp_path / "B-S08" / "permutation.json").is_file()
    files = generate(master, "B", "confirmatory", spares=4)
    write_files(tmp_path, files)
    assert not (tmp_path / "B-S05").exists() and not (tmp_path / "B-S08").exists()
    written = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()}
    assert written == set(files)


def test_write_files_round_trip(tmp_path):
    files = generate(demo_seed("DEMO-write"), "A", "pilot")
    write_files(tmp_path, files)
    for rel, data in files.items():
        assert (tmp_path / rel).read_bytes() == data


# ---------------------------------------------------------------------------------------
# Committed DEMO examples


def test_demo_examples_are_current():
    expected = demo_example_files()
    committed = {
        p.relative_to(EXAMPLES).as_posix(): p.read_bytes()
        for p in sorted(EXAMPLES.rglob("*"))
        if p.is_file() and p.name != "README.md"
    }
    assert sorted(committed) == sorted(expected), "run: python -m av_schedules demo-examples"
    for rel, data in expected.items():
        assert committed[rel] == data, rel


def test_demo_examples_are_labelled_demo():
    for rel, data in demo_example_files().items():
        if rel.endswith(".json"):
            doc = json.loads(data)
            assert doc["demo"] is True and doc["seed_label"] == EXAMPLE_DEMO_SEED, rel
    assert (EXAMPLES / "README.md").is_file()


# ---------------------------------------------------------------------------------------
# CLI


def test_default_out_dir_is_git_ignored():
    assert default_out_dir() == ROOT / "schedules" / "out"
    assert examples_dir() == EXAMPLES
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    probe = "schedules/out/A/A-C01/curriculum.csv"
    ignored = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", probe], check=False)
    assert ignored.returncode == 0


def test_cli_curriculum_with_demo_seed(tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["curriculum", "--demo-seed", "DEMO-cli", "--out", str(out)]) == 0
    assert (out / "A" / "A-C18" / "curriculum.csv").is_file()
    assert (out / "A" / "A-P03" / "permutation.json").is_file()
    assert (out / "B" / "B-C64" / "permutation.json").is_file()
    assert (out / "B" / "B-S08" / "permutation.json").is_file()
    assert (out / "B" / "B-P08" / "curriculum.csv").is_file()
    assert (out / "B" / "confirmatory-design-table.csv").is_file()
    assert (out / "A" / "pilot-batch-table.csv").is_file()
    assert "DEMO seed" in capsys.readouterr().out
    # Same seed again: identical bytes.
    before = {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}
    assert main(["curriculum", "--demo-seed", "DEMO-cli", "--out", str(out)]) == 0
    assert {p: p.read_bytes() for p in out.rglob("*") if p.is_file()} == before
    # Another seed into the same folder needs --force.
    assert main(["curriculum", "--demo-seed", "DEMO-cli2", "--out", str(out)]) == 2
    args = ["curriculum", "--demo-seed", "DEMO-cli2", "--out", str(out), "--force"]
    assert main(args) == 0


def test_cli_rejects_non_demo_demo_seed(tmp_path):
    assert main(["curriculum", "--demo-seed", "seed-1", "--out", str(tmp_path)]) == 2


def test_cli_master_seed_file(tmp_path):
    seed_file = tmp_path / "master.txt"
    seed_file.write_text(PRIVATE, encoding="utf-8")
    out = tmp_path / "out"
    args = ["curriculum", "--master-seed-file", str(seed_file), "--study", "B", "--set", "pilot"]
    assert main([*args, "--out", str(out)]) == 0
    doc = json.loads((out / "B" / "B-P01" / "permutation.json").read_text(encoding="utf-8"))
    assert doc["demo"] is False and doc["seed_label"].startswith("sha256:")
    assert PRIVATE not in (out / "B" / "pilot-manifest.json").read_text(encoding="utf-8")
    assert not (out / "A").exists()
    curriculum = (out / "B" / "B-P01" / "curriculum.csv").read_text(encoding="utf-8")
    assert curriculum.splitlines()[1].endswith(doc["seed_label"])


def test_cli_private_seed_serves_one_set(tmp_path):
    seed_file = tmp_path / "master.txt"
    seed_file.write_text(PRIVATE, encoding="utf-8")
    other_file = tmp_path / "master2.txt"
    other_file.write_text(PRIVATE_2, encoding="utf-8")
    out = tmp_path / "out"
    base = ["curriculum", "--study", "A", "--out", str(out)]
    # --set both with a private seed is refused before anything is written.
    assert main([*base, "--master-seed-file", str(seed_file), "--set", "both"]) == 2
    assert not out.exists()
    assert main([*base, "--master-seed-file", str(seed_file), "--set", "pilot"]) == 0
    # The confirmatory set may not reuse the pilot's master seed.
    assert main([*base, "--master-seed-file", str(seed_file), "--set", "confirmatory"]) == 2
    assert not (out / "A" / "confirmatory-manifest.json").exists()
    assert main([*base, "--master-seed-file", str(other_file), "--set", "confirmatory"]) == 0


def test_cli_checks_every_set_before_writing(tmp_path):
    out = tmp_path / "out"
    assert main(["curriculum", "--demo-seed", "DEMO-one", "--study", "B", "--out", str(out)]) == 0
    before = (out / "B" / "pilot-manifest.json").read_bytes()
    # Study A would be fine, Study B conflicts: nothing is written for A either.
    assert main(["curriculum", "--demo-seed", "DEMO-two", "--out", str(out)]) == 2
    assert not (out / "A").exists()
    assert (out / "B" / "pilot-manifest.json").read_bytes() == before


def test_cli_fails_closed_without_git(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_GIT", "git-not-installed-av-schedules")
    seed_file = tmp_path / "master.txt"
    seed_file.write_text(PRIVATE, encoding="utf-8")
    base = ["curriculum", "--master-seed-file", str(seed_file), "--study", "A", "--set", "pilot"]
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    assert main([*base, "--out", str(repo / "out")]) == 2  # repository present, git missing
    assert main([*base, "--out", str(tmp_path / "plain")]) == 0  # no repository at all


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_cli_refuses_private_seed_or_output_inside_unignored_work_tree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    inside = repo / "master.txt"
    inside.write_text(PRIVATE, encoding="utf-8")
    outside = tmp_path / "master.txt"
    outside.write_text(PRIVATE, encoding="utf-8")
    base = ["curriculum", "--study", "A", "--set", "pilot"]
    assert main([*base, "--master-seed-file", str(inside), "--out", str(tmp_path / "o")]) == 2
    assert main([*base, "--master-seed-file", str(outside), "--out", str(repo / "out")]) == 2
    ok_out = repo / "ignored" / "out"
    assert main([*base, "--master-seed-file", str(outside), "--out", str(ok_out)]) == 0
    # DEMO outputs may be written anywhere.
    assert main([*base, "--demo-seed", "DEMO-x", "--out", str(repo / "demo")]) == 0


def test_cli_check_planning(tmp_path, capsys):
    write_files(tmp_path, synthetic_planning_files())
    assert main(["check-planning", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "PASS" in out and "DRIFT" in out
    (tmp_path / "ontology.csv").unlink()
    assert main(["check-planning", str(tmp_path)]) == 1


def test_cli_demo_examples(tmp_path):
    assert main(["demo-examples", "--out", str(tmp_path)]) == 0
    for rel, data in demo_example_files().items():
        assert (tmp_path / rel).read_bytes() == data
