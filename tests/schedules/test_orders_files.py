"""Visit-schedule files (#30): rendered sets, the committed DEMO examples and the CLI."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from av_schedules import (
    build_units,
    check_visit_schedule,
    demo_seed,
    generate_schedules,
    person_ids,
    render_schedules,
    study_visits,
    visit_plan,
)
from av_schedules._paths import schema_path
from av_schedules.cli import main
from av_schedules.output import EXAMPLE_DEMO_SEED, demo_example_files
from av_schedules.schedule_output import (
    EXAMPLE_SCHEDULE_PERSONS,
    SUMMARY_COLUMNS,
    schedule_example_files,
    schedule_path,
    schedules_manifest_name,
    speech_list_name,
    summary_name,
)

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "schedules" / "examples" / "demo"
PRIVATE = "fedcba9876543210" * 4  # synthetic stand-in for a private seed (test only)


def test_rendered_set_files_and_manifest():
    master = demo_seed("DEMO-files")
    for study in ("A", "B"):
        files = generate_schedules(master, study, "pilot")
        units = build_units(master, study, "pilot")
        schedule_files = [p for p in files if "/schedules/" in p]
        assert len(schedule_files) == sum(len(person_ids(u)) for u in units) * len(
            study_visits(study)
        )
        assert schedule_path("A-P01", "A-P01-L01", "D0") == "A-P01/schedules/A-P01-L01/D0.json"
        manifest = json.loads(files[schedules_manifest_name("pilot")])
        assert manifest["format"] == "av-schedules/schedules-manifest"
        assert manifest["demo"] is True and manifest["seed_label"] == "DEMO-files"
        assert set(manifest["files"]) == set(files) - {schedules_manifest_name("pilot")}
        for path, digest in manifest["files"].items():
            assert hashlib.sha256(files[path]).hexdigest() == digest
        assert manifest["persons"] == sum(len(person_ids(u)) for u in units)
        assert all(data.endswith(b"\n") and b"\r" not in data for data in files.values())
        rows = list(csv.DictReader(io.StringIO(files[summary_name("pilot")].decode())))
        assert tuple(rows[0]) == SUMMARY_COLUMNS
        per_person = sum(len(visit_plan(study, v)) for v in study_visits(study))  # type: ignore[arg-type]
        assert len(rows) == manifest["persons"] * per_person
        assert speech_list_name("pilot") in files
    with pytest.raises(ValueError):
        render_schedules([], master)


def test_spare_slots_get_schedules():
    master = demo_seed("DEMO-spares")
    files = generate_schedules(master, "B", "confirmatory", spares=4)
    manifest = json.loads(files[schedules_manifest_name("confirmatory")])
    assert manifest["spare_units"] == ["B-S01", "B-S02", "B-S03", "B-S04"]
    assert "B-S04/schedules/B-S04-M2/W4.json" in files
    assert manifest["persons"] == 2 * (64 + 4)


# ---------------------------------------------------------------------------------------
# Committed DEMO examples


def test_schedule_examples_are_committed_byte_for_byte():
    expected = schedule_example_files(EXAMPLE_DEMO_SEED)
    assert set(expected) <= set(demo_example_files())
    for rel, data in expected.items():
        assert (EXAMPLES / rel).read_bytes() == data, "run: python -m av_schedules demo-examples"
    persons = {rel.split("/")[3] for rel in expected if "/schedules/" in rel}
    assert persons == set(EXAMPLE_SCHEDULE_PERSONS)
    assert sum("/schedules/" in rel for rel in expected) == 2 + 5  # all 7 visit types


def test_schedule_examples_are_valid_demo_and_linked():
    validator = Draft202012Validator(
        json.loads(schema_path("visit-schedule.schema.json").read_text(encoding="utf-8"))
    )
    speech = Draft202012Validator(
        json.loads(schema_path("speech-list.schema.json").read_text(encoding="utf-8"))
    )
    for path in sorted(EXAMPLES.rglob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        rel = path.relative_to(EXAMPLES).as_posix()
        if "/schedules/" in rel:
            assert not list(validator.iter_errors(doc)), rel
            assert check_visit_schedule(doc) == [], rel
            assert doc["demo"] is True and doc["seed_label"] == EXAMPLE_DEMO_SEED
            committed = EXAMPLES / doc["study"] / doc["unit_id"] / "permutation.json"
            assert (
                doc["permutation_json_sha256"] == hashlib.sha256(committed.read_bytes()).hexdigest()
            )
        elif rel.endswith("speech-list.json"):
            assert not list(speech.iter_errors(doc)), rel
            assert doc["demo"] is True


# ---------------------------------------------------------------------------------------
# CLI


def test_cli_schedules_with_demo_seed(tmp_path, capsys):
    out = tmp_path / "out"
    args = ["schedules", "--demo-seed", "DEMO-cli", "--set", "pilot", "--out", str(out)]
    assert main(args) == 0
    assert (out / "A" / "A-P03" / "schedules" / "A-P03-L06" / "D7.json").is_file()
    assert (out / "B" / "B-P08" / "schedules" / "B-P08-M2" / "W4.json").is_file()
    assert (out / "A" / "pilot-speech-list.json").is_file()
    assert (out / "B" / "pilot-schedule-summary.csv").is_file()
    printed = capsys.readouterr().out
    assert "A pilot: 36 visit schedules" in printed and "B pilot: 80 visit schedules" in printed
    assert "DEMO seed" in printed
    before = {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}
    assert main(args) == 0
    assert {p: p.read_bytes() for p in out.rglob("*") if p.is_file()} == before
    other = ["schedules", "--demo-seed", "DEMO-cli2", "--set", "pilot", "--out", str(out)]
    assert main(other) == 2
    assert main([*other, "--force"]) == 0


def test_cli_schedules_master_seed_file(tmp_path):
    seed_file = tmp_path / "master.txt"
    seed_file.write_text(PRIVATE, encoding="utf-8")
    out = tmp_path / "out"
    args = ["schedules", "--master-seed-file", str(seed_file), "--study", "B", "--set", "pilot"]
    assert main([*args, "--out", str(out)]) == 0
    doc = json.loads(
        (out / "B" / "B-P01" / "schedules" / "B-P01-M1" / "V1.json").read_text(encoding="utf-8")
    )
    assert doc["demo"] is False and doc["seed_label"].startswith("sha256:")
    for path in out.rglob("*"):
        if path.is_file():
            assert PRIVATE not in path.read_text(encoding="utf-8"), path
    assert not (out / "A").exists()


def test_cli_schedules_seed_rules(tmp_path, capsys):
    seed_file = tmp_path / "master.txt"
    seed_file.write_text(PRIVATE, encoding="utf-8")
    other_file = tmp_path / "other.txt"
    other_file.write_text(PRIVATE[::-1], encoding="utf-8")
    out = tmp_path / "out"
    base = ["schedules", "--study", "A", "--out", str(out)]
    # One private master seed serves one set only.
    assert main([*base, "--master-seed-file", str(seed_file), "--set", "both"]) == 2
    assert "one set only" in capsys.readouterr().err
    # Schedules must come from the same master seed as the set's curriculum.
    cur = ["curriculum", "--study", "A", "--set", "pilot", "--out", str(out)]
    assert main([*cur, "--master-seed-file", str(other_file)]) == 0
    assert main([*base, "--master-seed-file", str(seed_file), "--set", "pilot"]) == 2
    assert "curriculum" in capsys.readouterr().err
    assert main([*base, "--master-seed-file", str(other_file), "--set", "pilot"]) == 0
    # Pilot and confirmatory sets need different master seeds.
    assert main([*base, "--master-seed-file", str(other_file), "--set", "confirmatory"]) == 2
    assert "different master seeds" in capsys.readouterr().err
    assert main([*base, "--master-seed-file", str(seed_file), "--set", "confirmatory"]) == 0
    summary = (out / "A" / "pilot-schedule-summary.csv").read_text(encoding="utf-8")
    assert summary.splitlines()[0].endswith(",seed_label")
    assert summary.splitlines()[1].endswith(
        ",sha256:" + hashlib.sha256(PRIVATE[::-1].encode()).hexdigest()
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_cli_schedules_refuses_private_output_inside_unignored_work_tree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    inside = repo / "master.txt"
    inside.write_text(PRIVATE, encoding="utf-8")
    outside = tmp_path / "master.txt"
    outside.write_text(PRIVATE, encoding="utf-8")
    base = ["schedules", "--study", "A", "--set", "pilot"]
    assert main([*base, "--master-seed-file", str(inside), "--out", str(tmp_path / "o")]) == 2
    assert main([*base, "--master-seed-file", str(outside), "--out", str(repo / "out")]) == 2
    ok_out = repo / "ignored" / "out"
    assert main([*base, "--master-seed-file", str(outside), "--out", str(ok_out)]) == 0
    assert main([*base, "--demo-seed", "DEMO-x", "--out", str(repo / "demo")]) == 0
