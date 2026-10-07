"""Run-sheet generator (#32): template header, pre-filled columns, hashes, files, CLI."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
import subprocess
from functools import cache
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from av_schedules import build_units, demo_seed, person_ids, study_visits
from av_schedules._paths import run_sheet_examples_dir, schema_path
from av_schedules.assign import build_a_allocation
from av_schedules.checks import SetRun, build_set
from av_schedules.cli import main
from av_schedules.findings import Finding
from av_schedules.masking import find_method_strings
from av_schedules.orders import build_visit_schedule
from av_schedules.output import write_files
from av_schedules.planning import (
    RUN_SHEET_COLUMNS,
    RUN_SHEET_TEMPLATE,
    TEMPLATE_SHA256,
    check_planning,
    synthetic_planning_files,
    synthetic_template_files,
)
from av_schedules.run_sheet_output import (
    EXAMPLE_RUN_SHEET_PERSONS,
    EXAMPLE_RUN_SHEET_SEED,
    RunSheetCheckError,
    generate_run_sheets,
    package_hashes_name,
    run_sheet_example_files,
    run_sheets_manifest_name,
)
from av_schedules.run_sheets import (
    OPERATOR_COLUMNS,
    hash_cells,
    package_hashes_document,
    package_keys,
    parse_package_hashes,
    placeholder_package_hashes,
    read_run_sheet,
    run_sheet_csv,
    run_sheet_findings,
    run_sheet_path,
)
from av_schedules.schedule_output import render_schedules, schedules_manifest_name
from av_schedules.seeds import private_seed

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "schedules" / "examples" / "demo-run-sheets"
PRIVATE = "0123456789abcdef" * 4  # synthetic stand-in for a private seed (test only)
MASTER = demo_seed("DEMO-run-sheets")
HEADER = ",".join(RUN_SHEET_COLUMNS)


def validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads(schema_path(name).read_text(encoding="utf-8")))


@cache
def generated(study: str, set_name: str) -> SetRun:
    return build_set(MASTER, study, set_name)  # type: ignore[arg-type]


def fake_hashes(study: str, set_name: str, *, demo: bool = True, drop: str = "") -> bytes:
    units = build_units(MASTER, study, set_name)  # type: ignore[arg-type]
    keys = sorted(set(package_keys(MASTER, units).values()) - {drop})
    packages = {k: hashlib.sha256(k.encode()).hexdigest() for k in keys}
    doc = package_hashes_document(study, set_name, packages, demo=demo)  # type: ignore[arg-type]
    return json.dumps(doc).encode()


# ---------------------------------------------------------------------------------------
# Acceptance: generated run sheets match the template header exactly


@pytest.mark.parametrize(
    ("study", "set_name"),
    [("A", "pilot"), ("B", "pilot"), ("A", "confirmatory"), ("B", "confirmatory")],
)
def test_every_run_sheet_has_the_template_header_and_one_row_per_block(study, set_name):
    run = generated(study, set_name)
    rows_schema = validator("run-sheet-row.schema.json")
    for person, by_visit in run.docs.items():
        assert set(run.run_sheets[person]) == set(study_visits(study))  # type: ignore[arg-type]
        for visit, doc in by_visit.items():
            data = run.run_sheets[person][visit]
            assert data.split(b"\n", 1)[0].decode() == HEADER
            assert b"\r" not in data and data.endswith(b"\n")
            header, rows = read_run_sheet(data)
            assert header == RUN_SHEET_COLUMNS
            assert [r["block"] for r in rows] == [b["block"] for b in doc["blocks"]]
            for row, block in zip(rows, doc["blocks"], strict=True):
                assert row["participant_id"] == person and row["visit"] == visit
                assert row["expected_count"] == str(len(block["items"]))
                assert all(row[c] == "" for c in (*OPERATOR_COLUMNS, "hash_check"))
                assert not list(rows_schema.iter_errors(row))
            assert run_sheet_findings(data, doc) == []


def test_run_sheets_hold_no_answers_or_method_labels():
    for study in ("A", "B"):
        run = generated(study, "confirmatory")
        for by_visit in run.run_sheets.values():
            for data in by_visit.values():
                text = data.decode()
                assert "-a1-" not in text and "-r1" not in text  # no message or atom IDs
                assert "ADD_ONE" not in text and "SCAN" not in text  # no semantic labels
                assert "active" not in text and "yoked" not in text and "SQ-" not in text
                if study == "A":
                    assert find_method_strings(text) == []


def test_run_sheet_rows_follow_schedule_blocks():
    unit = build_units(MASTER, "B", "pilot")[0]
    doc = build_visit_schedule(MASTER, unit, "B-P01-M1", "V1")
    text = run_sheet_csv(doc, "sha256:" + "ab" * 32).decode()
    assert text.splitlines() == [
        HEADER,
        *(
            f"B-P01-M1,V1,{block},{count},,,,,,sha256:{'ab' * 32},,"
            for block, count in [
                ("profile_menu", 1),
                ("atom_menus", 8),
                ("atomic_lessons", 8),
                ("message_lessons", 8),
                ("trained", 4),
                ("novel", 2),
                ("atomic", 8),
            ]
        ),
    ]
    assert run_sheet_path("B-P01", "B-P01-M1", "V1") == "B-P01/run-sheets/B-P01-M1/V1.csv"


def test_unreadable_run_sheet_is_a_finding():
    doc = generated("A", "pilot").docs["A-P01-L01"]["D0"]
    findings = run_sheet_findings(b"\xff\xfe", doc)
    assert findings and findings[0] == Finding(
        "A-P01", "A-P01-L01", "D0", "run-sheet", findings[0].detail
    )
    assert "unreadable" in findings[0].detail


# ---------------------------------------------------------------------------------------
# hash_check: package-hash mappings


def test_study_a_hash_cells_follow_the_learner_facing_book_ids():
    units = build_units(MASTER, "A", "pilot")
    ph = parse_package_hashes(fake_hashes("A", "pilot"))
    cells = hash_cells(MASTER, units, ph)
    alloc = build_a_allocation(MASTER, "pilot")
    for slot in alloc.slots:
        assert cells[slot.slot_id] == "sha256:" + hashlib.sha256(slot.book_id.encode()).hexdigest()
    # Learners of the same book share the cell; 9 books for 18 learners.
    assert len(set(cells.values())) == 9 and len(cells) == 18
    run = build_set(MASTER, "A", "pilot", package_hashes=ph)
    row = read_run_sheet(run.run_sheets["A-P01-L01"]["D7"])[1][0]
    assert row["hash_check"] == cells["A-P01-L01"]


def test_study_b_members_share_the_dyad_package_and_spares_may_be_missing():
    units = build_units(MASTER, "B", "confirmatory")
    spare = next(u for u in units if u.kind == "spare").unit_id
    ph = parse_package_hashes(fake_hashes("B", "confirmatory", drop=spare))
    cells = hash_cells(MASTER, units, ph)
    assert cells["B-C01-M1"] == cells["B-C01-M2"] != cells["B-C02-M1"]
    assert cells[f"{spare}-M1"] == cells[f"{spare}-M2"] == ""
    main = parse_package_hashes(fake_hashes("B", "confirmatory", drop="B-C07"))
    with pytest.raises(ValueError, match="lacks packages"):
        hash_cells(MASTER, units, main)


def test_hash_mapping_must_match_the_set():
    a_units = build_units(MASTER, "A", "pilot")
    with pytest.raises(ValueError, match="is for B pilot"):
        hash_cells(MASTER, a_units, parse_package_hashes(fake_hashes("B", "pilot")))
    with pytest.raises(ValueError, match="DEMO status"):
        hash_cells(MASTER, a_units, parse_package_hashes(fake_hashes("A", "pilot", demo=False)))
    doc = json.loads(fake_hashes("A", "pilot"))
    doc["packages"]["BK-P-XXXXXX"] = "0" * 64
    with pytest.raises(ValueError, match="outside the set"):
        hash_cells(MASTER, a_units, parse_package_hashes(json.dumps(doc).encode()))
    assert hash_cells(MASTER, [], parse_package_hashes(fake_hashes("A", "pilot"))) == {}
    assert set(hash_cells(MASTER, a_units, None).values()) == {""}


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda d: d.update(format="x"), "not a av-schedules/package-hashes"),
        (lambda d: d.update(extra=1), "fields"),
        (lambda d: d.update(format_version=2), "format_version"),
        (lambda d: d.update(study="C"), "study/set"),
        (lambda d: d.update(demo="yes"), "booleans"),
        (lambda d: d.update(demo=False, placeholder=True), "only in DEMO"),
        (lambda d: d.update(packages={}), "non-empty"),
        (lambda d: d["packages"].update({"B-P01": "ABC"}), "64 lowercase hex"),
    ],
)
def test_malformed_mappings_are_refused(edit, message):
    doc = json.loads(fake_hashes("B", "pilot"))
    edit(doc)
    with pytest.raises(ValueError, match=message):
        parse_package_hashes(json.dumps(doc).encode())


def test_mapping_bytes_must_be_json():
    with pytest.raises(ValueError, match="not UTF-8 JSON"):
        parse_package_hashes(b"\xff")


def test_placeholder_mapping_is_labelled_and_demo_only():
    units = build_units(MASTER, "B", "pilot")
    data = placeholder_package_hashes(MASTER, units)
    doc = json.loads(data)
    assert doc["demo"] is True and doc["placeholder"] is True
    assert not list(validator("package-hashes.schema.json").iter_errors(doc))
    ph = parse_package_hashes(data)
    assert ph.cell("B-P01") == "DEMO-placeholder:" + doc["packages"]["B-P01"]
    assert ph.cell("B-P99") is None and ph.sha256 == hashlib.sha256(data).hexdigest()
    with pytest.raises(ValueError, match="only for DEMO"):
        placeholder_package_hashes(private_seed(PRIVATE), units)
    with pytest.raises(ValueError, match="no units"):
        placeholder_package_hashes(MASTER, [])
    schema = validator("package-hashes.schema.json")
    doc["demo"] = False
    assert list(schema.iter_errors(doc))  # placeholder requires demo


# ---------------------------------------------------------------------------------------
# Files and manifest


def test_generated_files_and_manifest():
    files = generate_run_sheets(MASTER, "B", "pilot", demo_placeholder_hashes=True)
    manifest = json.loads(files[run_sheets_manifest_name("pilot")])
    assert not list(validator("run-sheets-manifest.schema.json").iter_errors(manifest))
    assert manifest["template"]["columns"] == list(RUN_SHEET_COLUMNS)
    assert manifest["template"]["sha256"] == TEMPLATE_SHA256[RUN_SHEET_TEMPLATE]
    assert manifest["template"]["prefilled"] == [
        "participant_id",
        "visit",
        "block",
        "expected_count",
        "hash_check",
    ]
    assert manifest["package_hashes"]["placeholder"] is True
    assert (
        manifest["package_hashes"]["sha256"]
        == hashlib.sha256(files[package_hashes_name("pilot")]).hexdigest()
    )
    schedules = render_schedules(build_units(MASTER, "B", "pilot"), MASTER)
    assert (
        manifest["schedules_manifest_sha256"]
        == hashlib.sha256(schedules[schedules_manifest_name("pilot")]).hexdigest()
    )
    assert set(manifest["files"]) == set(files) - {run_sheets_manifest_name("pilot")}
    for path, digest in manifest["files"].items():
        assert hashlib.sha256(files[path]).hexdigest() == digest
    assert sum("/run-sheets/" in p for p in files) == 16 * 5 == manifest["checks"]["schedules"]
    assert manifest["screening_booked_minutes"] == 20
    booked = sum(v["booked_minutes"] for v in manifest["visits"].values())
    assert manifest["screening_booked_minutes"] + booked == 260
    for v in manifest["visits"].values():
        assert v["scheduled_seconds"] <= 60 * v["booked_minutes"]
    a = json.loads(generate_run_sheets(MASTER, "A", "pilot")[run_sheets_manifest_name("pilot")])
    assert a["package_hashes"] is None and a["screening_booked_minutes"] is None
    assert "hash_check" not in a["template"]["prefilled"]
    assert {v: x["booked_minutes"] for v, x in a["visits"].items()} == {"D0": 75, "D7": 30}


def test_run_sheets_are_deterministic():
    one = generate_run_sheets(demo_seed("DEMO-det"), "A", "pilot")
    two = generate_run_sheets(demo_seed("DEMO-det"), "A", "pilot")
    assert one == two
    other = generate_run_sheets(demo_seed("DEMO-det2"), "A", "pilot")
    assert one[run_sheets_manifest_name("pilot")] != other[run_sheets_manifest_name("pilot")]


def test_failed_checks_write_nothing(monkeypatch, tmp_path, capsys):
    fault = Finding("A-P01", "A-P01-L01", "D0", "blocks", "injected")
    monkeypatch.setattr("av_schedules.run_sheet_output.check_set", lambda run: [fault])
    with pytest.raises(RunSheetCheckError) as err:
        generate_run_sheets(MASTER, "A", "pilot")
    assert err.value.findings == [fault] and "| A-P01 | A-P01-L01 | D0 | blocks |" in str(err.value)
    with pytest.raises(ValueError, match="not both"):
        generate_run_sheets(
            MASTER,
            "A",
            "pilot",
            package_hashes=parse_package_hashes(fake_hashes("A", "pilot")),
            demo_placeholder_hashes=True,
        )
    out = tmp_path / "out"
    args = ["run-sheets", "--demo-seed", "DEMO-x", "--set", "pilot", "--out", str(out)]
    assert main(args) == 1
    assert not out.exists()
    assert "checks failed, nothing written" in capsys.readouterr().err


# ---------------------------------------------------------------------------------------
# Committed DEMO examples


def test_run_sheet_examples_are_committed_byte_for_byte():
    expected = run_sheet_example_files()
    assert run_sheet_examples_dir() == EXAMPLES
    committed = {
        p.relative_to(EXAMPLES).as_posix(): p.read_bytes()
        for p in EXAMPLES.rglob("*")
        if p.is_file() and p.name != "README.md"
    }
    assert sorted(committed) == sorted(expected), "run: python -m av_schedules run-sheet-examples"
    for rel, data in expected.items():
        assert committed[rel] == data, rel
    persons = {rel.split("/")[3] for rel in expected if "/run-sheets/" in rel}
    assert persons == set(EXAMPLE_RUN_SHEET_PERSONS)
    assert sum("/run-sheets/" in rel for rel in expected) == 2 + 2 * 5


def test_run_sheet_examples_are_valid_demo_and_masked():
    rows_schema = validator("run-sheet-row.schema.json")
    for path in sorted(EXAMPLES.rglob("*.csv")):
        header, rows = read_run_sheet(path.read_bytes())
        assert header == RUN_SHEET_COLUMNS
        for row in rows:
            assert not list(rows_schema.iter_errors(row))
            assert row["hash_check"].startswith("DEMO-placeholder:")
        if path.relative_to(EXAMPLES).parts[0] == "A":
            assert find_method_strings(path.read_text(encoding="utf-8")) == []
    for name in ("package-hashes", "run-sheets-manifest"):
        for path in EXAMPLES.rglob(f"pilot-{name}.json"):
            doc = json.loads(path.read_text(encoding="utf-8"))
            assert not list(validator(f"{name}.schema.json").iter_errors(doc)), path
            assert doc["demo"] is True
    manifest = json.loads((EXAMPLES / "A" / "pilot-run-sheets-manifest.json").read_text())
    assert manifest["seed_label"] == EXAMPLE_RUN_SHEET_SEED
    # Book IDs of the placeholder mapping are those of the committed DEMO pilot slot list.
    slots = json.loads(
        (ROOT / "schedules" / "examples" / "demo-allocation" / "A" / "pilot-slots.json").read_text()
    )
    books = json.loads((EXAMPLES / "A" / "pilot-package-hashes.json").read_text())["packages"]
    assert set(books) == {s["book_id"] for s in slots["slots"]}


# ---------------------------------------------------------------------------------------
# CLI


def test_cli_run_sheets_with_demo_seed(tmp_path, capsys):
    out = tmp_path / "out"
    args = ["run-sheets", "--demo-seed", "DEMO-cli", "--set", "pilot", "--out", str(out)]
    assert main([*args, "--demo-placeholder-hashes"]) == 0
    assert (out / "A" / "A-P03" / "run-sheets" / "A-P03-L06" / "D7.csv").is_file()
    assert (out / "B" / "B-P08" / "run-sheets" / "B-P08-M2" / "W4.csv").is_file()
    assert (out / "B" / package_hashes_name("pilot")).is_file()
    printed = capsys.readouterr().out
    assert "A pilot: 36 run sheets (hash_check pre-filled)" in printed
    assert "B pilot: 80 run sheets" in printed and "DEMO seed" in printed
    before = {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}
    assert main([*args, "--demo-placeholder-hashes"]) == 0
    assert {p: p.read_bytes() for p in out.rglob("*") if p.is_file()} == before
    other = ["run-sheets", "--demo-seed", "DEMO-cli2", "--set", "pilot", "--out", str(out)]
    assert main(other) == 2
    assert main([*other, "--force"]) == 0
    assert "hash_check empty" in capsys.readouterr().out


def test_cli_run_sheets_with_package_hash_files(tmp_path, capsys):
    a = tmp_path / "a.json"
    a.write_bytes(fake_hashes("A", "pilot"))
    out = tmp_path / "out"
    base = ["run-sheets", "--demo-seed", MASTER.value, "--set", "pilot", "--out", str(out)]
    assert main([*base, "--study", "A", "--package-hashes", str(a)]) == 0
    sheet = (out / "A" / "A-P01" / "run-sheets" / "A-P01-L01" / "D0.csv").read_text()
    assert ",sha256:" in sheet
    assert main([*base, "--study", "B", "--package-hashes", str(a)]) == 2
    assert "was not requested" in capsys.readouterr().err
    assert main([*base, "--package-hashes", str(a), "--package-hashes", str(a)]) == 2
    assert main([*base, "--package-hashes", str(a), "--demo-placeholder-hashes"]) == 2


def test_cli_run_sheets_private_seed_rules(tmp_path, capsys):
    seed_file = tmp_path / "master.txt"
    seed_file.write_text(PRIVATE, encoding="utf-8")
    out = tmp_path / "out"
    base = ["run-sheets", "--study", "A", "--out", str(out), "--master-seed-file", str(seed_file)]
    assert main([*base, "--set", "both"]) == 2
    assert "one set only" in capsys.readouterr().err
    assert main([*base, "--set", "pilot", "--demo-placeholder-hashes"]) == 2
    assert "needs a --demo-seed" in capsys.readouterr().err
    # Run sheets must come from the set's curriculum seed.
    other = tmp_path / "other.txt"
    other.write_text(PRIVATE[::-1], encoding="utf-8")
    cur = ["curriculum", "--study", "A", "--set", "pilot", "--out", str(out)]
    assert main([*cur, "--master-seed-file", str(other)]) == 0
    assert main([*base, "--set", "pilot"]) == 2
    assert "curriculum" in capsys.readouterr().err
    assert main([*cur, "--master-seed-file", str(seed_file), "--force"]) == 0
    assert main([*base, "--set", "pilot"]) == 0
    manifest = json.loads((out / "A" / run_sheets_manifest_name("pilot")).read_text())
    assert manifest["demo"] is False and manifest["seed_label"].startswith("sha256:")
    for path in out.rglob("*"):
        if path.is_file():
            assert PRIVATE not in path.read_text(encoding="utf-8"), path


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_cli_run_sheets_refuses_private_output_inside_unignored_work_tree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    inside = repo / "master.txt"
    inside.write_text(PRIVATE, encoding="utf-8")
    outside = tmp_path / "master.txt"
    outside.write_text(PRIVATE, encoding="utf-8")
    base = ["run-sheets", "--study", "A", "--set", "pilot"]
    assert main([*base, "--master-seed-file", str(inside), "--out", str(tmp_path / "o")]) == 2
    assert main([*base, "--master-seed-file", str(outside), "--out", str(repo / "out")]) == 2
    ok_out = repo / "ignored" / "out"
    assert main([*base, "--master-seed-file", str(outside), "--out", str(ok_out)]) == 0
    assert main([*base, "--demo-seed", "DEMO-x", "--out", str(repo / "demo")]) == 0


def test_cli_check_report(capsys, monkeypatch):
    args = ["check", "--demo-seed", "DEMO-cli", "--set", "pilot"]
    assert main([*args, "--demo-placeholder-hashes"]) == 0
    out = capsys.readouterr().out
    assert "## Schedule checks (#32)" in out and "No findings" in out
    assert "| `full_primary_trials` | 36 | 36 | yes |" in out
    fault = Finding("B-P01", "B-P01-M1", "V1", "order", "injected")
    monkeypatch.setattr(
        "av_schedules.cli.check_set", lambda run: [fault] if run.study == "B" else []
    )
    assert main([*args, "--study", "B"]) == 1
    assert "| B-P01 | B-P01-M1 | V1 | order | injected |" in capsys.readouterr().out


def test_cli_run_sheet_examples(tmp_path):
    assert main(["run-sheet-examples", "--out", str(tmp_path)]) == 0
    for rel, data in run_sheet_example_files().items():
        assert (tmp_path / rel).read_bytes() == data


def test_cli_check_planning_with_templates(tmp_path, capsys):
    planning = tmp_path / "planning-materials"
    write_files(planning, synthetic_planning_files())
    templates = tmp_path / "templates"
    write_files(templates, synthetic_template_files())
    assert main(["check-planning", str(planning)]) == 0  # ../templates found by default
    out = capsys.readouterr().out
    assert f"checked {RUN_SHEET_TEMPLATE}" in out
    assert check_planning(planning).drift and RUN_SHEET_TEMPLATE in check_planning(planning).checked
    # The synthetic template is the header only; it equals the reviewed bytes.
    assert all(RUN_SHEET_TEMPLATE not in d for d in check_planning(planning).drift)
    bad = tmp_path / "bad"
    (bad / RUN_SHEET_TEMPLATE).parent.mkdir()
    (bad / RUN_SHEET_TEMPLATE).write_text("participant_id,visit\r\nx,y\r\n", encoding="utf-8")
    assert main(["check-planning", str(planning), "--templates", str(bad)]) == 1
    printed = capsys.readouterr().out
    assert "MISMATCH visit-run-sheet-template.csv header" in printed
    assert "data rows" in printed
    assert not check_planning(planning, templates=tmp_path / "missing").ok


def test_check_planning_reports_unencoded_or_wrong_design_check_fields(tmp_path):
    write_files(tmp_path, synthetic_planning_files())
    path = tmp_path / "design-checks.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["new_field"] = 3
    doc["B_total_main_booked_minutes_per_person"] = 250
    del doc["A_assigned_learners"]
    path.write_text(json.dumps(doc), encoding="utf-8")
    problems = check_planning(tmp_path).problems
    assert any("new_field=3 is not encoded" in p for p in problems)
    assert any("B_total_main_booked_minutes_per_person=250" in p for p in problems)
    assert any("A_assigned_learners=None" in p for p in problems)
    schedule = tmp_path / "assessment-schedule.csv"
    schedule.write_text(schedule.read_text().replace("704", "700", 1), encoding="utf-8")
    assert any("assessment-schedule.csv rows" in p for p in check_planning(tmp_path).problems)
    schedule.write_text("study,visit\n", encoding="utf-8")
    assert any("assessment-schedule.csv header" in p for p in check_planning(tmp_path).problems)


def test_person_slots_and_paths_avoid_guarded_names():
    for study, set_name in (("A", "pilot"), ("B", "confirmatory")):
        for unit in build_units(MASTER, study, set_name):  # type: ignore[arg-type]
            for person in person_ids(unit):
                path = run_sheet_path(unit.unit_id, person, "D0")
                assert "participant" not in path and "private" not in path


def test_rows_parse_with_csv_module():
    data = generated("B", "pilot").run_sheets["B-P02-M2"]["W4"]
    rows = list(csv.reader(io.StringIO(data.decode())))
    assert rows[0] == list(RUN_SHEET_COLUMNS) and len(rows) == 1 + 4
