"""Generation audit reports (#24): the column contract with #34, the summary schema, and
the audit built from synthetic batch logs (`av_generation._audit_synth`).

The synthetic batch (`SynthSpec()` defaults) has a bank fallback, a whole-book
substitution with continued generation, an `archive_none` atom, a resume that restarts
the run clock, and per-method outcome mixes. The DEMO example under
`generation/examples/demo-audit/` is rebuilt here and compared byte for byte.
"""

import builtins
import csv
import hashlib
import io
import itertools
import json
import os
import pathlib
import shutil
from collections import Counter
from pathlib import Path

import pytest
from av_sound.composer import message_length
from av_sound.grammar import ATOM_IDS
from av_sound.recipe import TOTAL_MS, Recipe
from av_sound.tables import SAMPLES_PER_MS
from hypothesis import given, settings
from hypothesis import strategies as st

from av_generation import audit
from av_generation._audit_synth import (
    EXAMPLE_FILES,
    SynthSpec,
    synth_spec,
    write_example,
    write_synthetic_batch,
)
from av_generation._schemas import schema_errors
from av_generation.constants import MESSAGE_MAX_MS, MESSAGE_MIN_MS
from av_generation.ids import Method
from av_generation.jsonio import document_text, read_json
from av_generation.masking import masking_findings
from av_generation.outcomes import LLM_ONLY_OUTCOMES, OUTCOME_CODES
from av_generation.records import TimingEvent
from av_generation.rundir import RunPolicyError

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "generation" / "runs" / "DEMO-AUDIT-01"
BOOKS = {"A1": "DEMO-BK-7QX4", "A2": "DEMO-BK-M2RW", "A3": "DEMO-BK-H9TC"}


# ---------------------------------------------------------------------------
# Column contract and summary schema (skeleton tests, kept)


def test_audit_columns():
    assert tuple(f"n_{c}" for c in OUTCOME_CODES) == audit.OUTCOME_COLUMNS
    assert len(set(audit.BOOK_COLUMNS)) == len(audit.BOOK_COLUMNS)
    assert set(audit.BOOK_COLUMNS) >= audit.METHOD_COLUMNS
    assert not set(audit.MASKED_BOOK_COLUMNS) & audit.METHOD_COLUMNS
    assert audit.SET_AUDIT_NAME.format(study="A", set="confirmatory") == (
        "A-confirmatory-audit.csv"
    )
    for columns, method_columns, masked in (
        (audit.ATOM_COLUMNS, audit.METHOD_ATOM_COLUMNS, audit.MASKED_ATOM_COLUMNS),
        (audit.SLOT_COLUMNS, audit.METHOD_SLOT_COLUMNS, audit.MASKED_SLOT_COLUMNS),
    ):
        assert len(set(columns)) == len(columns)
        assert set(columns) >= method_columns
        assert masked == tuple(c for c in columns if c not in method_columns)


def test_masked_columns_reveal_no_method_by_construction():
    masked = set(audit.MASKED_BOOK_COLUMNS)
    assert not {f"n_{o.value}" for o in LLM_ONLY_OUTCOMES} & masked
    assert not {c for c in masked if c.startswith("n_")}
    per_method_effort = {
        "startup_ms",
        "operator_ms",
        "design_active_ms",
        "familiarization_ms",
        "model_runtime_ms",
        "tokens_in",
        "tokens_out",
        "n_llm_server_error",
    }
    assert not per_method_effort & masked
    for columns in (
        audit.MASKED_BOOK_COLUMNS,
        audit.MASKED_ATOM_COLUMNS,
        audit.MASKED_SLOT_COLUMNS,
    ):
        assert masking_findings(",".join(columns)) == ()
        assert not {c for c in columns if c.startswith("n_") and c != "n_ratings"} - {"n_eligible"}
    assert {"slots_valid", "slots_invalid", "failed_generation", "nonfallback"} <= masked


def _book_row(masked: bool) -> dict:
    row = {c: 0 for c in audit.BOOK_COLUMNS}
    row.update(
        batch_id="DEMO-A-P01",
        book_id="DEMO-BK-H9TC",
        profile="P1",
        failed_generation=False,
        nonfallback=True,
        method="A3",
        designer_id=None,
        candidate_diversity=0.31,
        committed_diversity=None,
        wall_ms=None,
        startup_ms=None,
    )
    if masked:
        row = {k: v for k, v in row.items() if k not in audit.METHOD_COLUMNS}
    return row


def _summary(masked: bool, row: dict) -> dict:
    return {
        "format": "av-generation/audit-summary",
        "format_version": 1,
        "masked": masked,
        "run_id": "DEMO-run-01",
        "batch_id": "DEMO-A-P01",
        "sources": {"logs/slots.jsonl": "a" * 64},
        "books": [row],
        "timing": {
            "batch_wall_ms": None,
            "appointments": [],
            "atoms": [],
            "max_atom_ms": None,
            "max_appointment_ms": None,
            "startup_ms": 0,
            "operator_ms": 0,
        },
        "checks": {
            "complete": True,
            "ok": True,
            "problems": [],
            "slot_refusals": 0,
            "fallback_scans": 0,
        },
        "machines": None,
    }


def test_audit_summary_masking_rule():
    assert schema_errors("audit-summary.schema.json", _summary(False, _book_row(False))) == ()
    assert schema_errors("audit-summary.schema.json", _summary(True, _book_row(True))) == ()
    assert schema_errors("audit-summary.schema.json", _summary(True, _book_row(False)))
    assert schema_errors("audit-summary.schema.json", _summary(False, _book_row(True)))
    leaky = dict(_book_row(True), n_overflow_input=0)
    assert schema_errors("audit-summary.schema.json", _summary(True, leaky))
    with_machines = dict(_summary(True, _book_row(True)), machines={"llm_host": {"gpu": "x"}})
    assert schema_errors("audit-summary.schema.json", with_machines)
    assert tuple(_book_row(False)) == audit.BOOK_COLUMNS
    assert tuple(_book_row(True)) == audit.MASKED_BOOK_COLUMNS


# ---------------------------------------------------------------------------
# Fixtures: one validated synthetic batch and its audit per session


@pytest.fixture(scope="session")
def demo_run(tmp_path_factory) -> Path:
    return write_synthetic_batch(tmp_path_factory.mktemp("runs")).root


@pytest.fixture(scope="session")
def demo_audit(demo_run, tmp_path_factory) -> tuple[audit.AuditResult, Path]:
    out = tmp_path_factory.mktemp("audit")
    result = audit.build_audit(demo_run, out)
    if os.environ.get("CI"):  # bulky synthetic outputs go to the CI artifact
        ci = ROOT / "generation" / "out" / "ci" / "audit"
        shutil.rmtree(ci, ignore_errors=True)
        shutil.copytree(demo_run, ci / "run")
        shutil.copytree(out, ci / "report")
    return result, out


def _copy_run(demo_run: Path, tmp_path: Path) -> Path:
    target = tmp_path / demo_run.name
    shutil.copytree(demo_run, target)
    return target


def _csv(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


def _books(out: Path, masked: bool = False) -> dict[str, dict[str, str]]:
    rows = _csv(out / ("masked" if masked else "unmasked") / "books.csv")
    return {r["book_id"]: r for r in rows}


# ---------------------------------------------------------------------------
# Acceptance criteria


def test_slot_outcome_counts_per_book_sum_to_192(demo_audit):
    result, out = demo_audit
    assert result.ok, result.problems
    books = _books(out)
    assert sorted(books) == sorted(BOOKS.values())
    for row in books.values():
        assert int(row["slots_total"]) == 192
        assert sum(int(row[c]) for c in audit.OUTCOME_COLUMNS) == 192
        assert int(row["slots_valid"]) + int(row["slots_invalid"]) == 192
        assert row["slots_valid"] == row["n_valid"]
    summary = read_json(out / "unmasked" / "summary.json")
    assert summary["checks"] == {
        "complete": True,
        "fallback_scans": 2,
        "ok": True,
        "problems": [],
        "slot_refusals": 0,
    }


def _raw_tally(run: Path) -> dict[str, Counter]:
    """A tally written for this test only: raw JSON lines, no audit code."""
    tally: dict[str, Counter] = {}
    for slot in _jsonl(run / "logs" / "slots.jsonl"):
        c = tally.setdefault(slot["book_id"], Counter())
        c["slots_total"] += 1
        c[f"n_{slot['outcome']}"] += 1
        c["n_llm_server_error"] += slot["llm_status"] == "server_error"
        c["design_active_ms"] += slot["design_ms"] or 0
    commits = _jsonl(run / "logs" / "commits.jsonl")
    for book in {c["book_id"] for c in commits}:
        mine = [c for c in commits if c["book_id"] == book]
        final = [c for c in mine if c["store_book_id"] == mine[-1]["store_book_id"]]
        assert len({c["atom_id"] for c in final}) == len(final)
        for commit in final:
            tally[book][f"source_{commit['source']}"] += 1
    return tally


def test_counts_match_an_independent_tally(demo_run, demo_audit, tmp_path):
    _, out = demo_audit
    books = _books(out)
    tally = _raw_tally(demo_run)
    sources = {"selector": "atoms_selector", "fallback_bank": "atoms_bank_fallback"}
    for book, row in books.items():
        expected = tally[book]
        for column in ("slots_total", *audit.OUTCOME_COLUMNS, "n_llm_server_error"):
            assert int(row[column]) == expected[column], (book, column)
        for source, column in (*sources.items(), ("fallback_book", "atoms_book_fallback")):
            assert int(row[column]) == expected[f"source_{source}"], (book, column)
    a1 = books[BOOKS["A1"]]
    assert int(a1["design_active_ms"]) == tally[BOOKS["A1"]]["design_active_ms"]
    # The module's own independent counter and the tally sheet agree too.
    sheet = tmp_path / "tally.csv"
    assert audit.tally_sheet(demo_run, sheet)
    rows = _csv(sheet)
    assert len(rows) == 3 * len(audit.TALLY_QUANTITIES)
    assert all(r["match"] == "1" and r["hand_count"] == "" for r in rows)


def test_every_committed_atom_appears_once_with_its_source(demo_run, demo_audit):
    _, out = demo_audit
    books = _books(out)
    slots_log = {s["slot_id"]: s for s in _jsonl(demo_run / "logs" / "slots.jsonl")}
    for book_id, row in books.items():
        atoms = _csv(out / "unmasked" / f"book-{book_id}-atoms.csv")
        slots = {r["slot_id"]: r for r in _csv(out / "unmasked" / f"book-{book_id}-slots.csv")}
        assert len(atoms) == 16 and len({a["atom_id"] for a in atoms}) == 16
        assert all(a["source"] in ("selector", "fallback_bank", "fallback_book") for a in atoms)
        counts = Counter(a["source"] for a in atoms)
        assert counts["selector"] == int(row["atoms_selector"])
        assert counts["fallback_bank"] == int(row["atoms_bank_fallback"])
        assert counts["fallback_book"] == int(row["atoms_book_fallback"])
        for a in atoms:
            if a["source"] == "selector":
                origin = slots_log[a["source_slot_id"]]
                assert origin["outcome"] == "valid" and origin["atom_id"] == a["atom_id"]
                assert slots[a["source_slot_id"]]["committed"] == "1"
                assert a["source_slot_id"] == a["selected_slot_id"]
            elif a["source"] == "fallback_bank":
                assert a["bank_index"] != "" and a["fallback_scan"] == "1"
        assert sum(r["committed"] == "1" for r in slots.values()) == counts["selector"]
    # The injected cases: bank fallback (A2 atom 3), whole-book substitution (A1 atom 7).
    a2 = _csv(out / "unmasked" / f"book-{BOOKS['A2']}-atoms.csv")
    assert [a["source"] for a in a2].count("fallback_bank") == 1
    assert a2[2]["source"] == "fallback_bank" and a2[2]["round4_action"] == "fallback_scan"
    a1 = _csv(out / "unmasked" / f"book-{BOOKS['A1']}-atoms.csv")
    assert {a["source"] for a in a1} == {"fallback_book"}
    assert [a["voided_source"] for a in a1[:6]] == ["selector"] * 6
    assert all(a["voided_source"] == "" for a in a1[6:])
    assert a1[6]["round4_action"] == "fallback_scan" and a1[6]["fallback_scan"] == "1"
    assert {a["round4_action"] for a in a1[7:]} == {"archive", "archive_none"}
    assert a1[11]["round4_action"] == "archive_none"
    a1_row = books[BOOKS["A1"]]
    assert (a1_row["failed_generation"], a1_row["nonfallback"]) == ("1", "0")
    assert (books[BOOKS["A2"]]["nonfallback"], books[BOOKS["A3"]]["nonfallback"]) == ("0", "1")


def test_message_durations_stay_within_bounds(demo_audit):
    _, out = demo_audit
    for row in _books(out).values():
        assert MESSAGE_MIN_MS <= int(row["message_ms_min"]) <= int(row["message_ms_max"])
        assert int(row["message_ms_max"]) <= MESSAGE_MAX_MS
        assert row["message_duration_violations"] == "0"
        totals = [int(row[f"total_ms_{t}"]) for t in TOTAL_MS]
        assert sum(totals) == 16


def test_message_duration_violations_are_flagged(demo_run, tmp_path, monkeypatch):
    def tight(durations, *, min_ms=MESSAGE_MIN_MS, max_ms=MESSAGE_MAX_MS):
        return tuple(sorted(m for m, ms in durations.items() if not 1_100 <= ms <= 1_900))

    monkeypatch.setattr(audit, "duration_violations", tight)
    result = audit.build_audit(demo_run, tmp_path / "out")
    assert not result.ok
    flagged = [p for p in result.problems if "outside 1100-2000 ms" in p]
    books = _books(tmp_path / "out")
    assert len(flagged) == sum(int(r["message_duration_violations"]) for r in books.values()) > 0
    assert any(p.endswith("lasts 2000 ms, outside 1100-2000 ms") for p in flagged)


def test_message_durations_match_the_composer():
    for action, referent in itertools.product(TOTAL_MS, TOTAL_MS):
        durations = audit.message_durations_ms({"K-a1": action, "K-r2": referent})
        recipe = {"pitches": [0, 0, 0], "rhythm_weights": [1, 1, 1], "gaps_ms": [20, 20]}
        expected = message_length(
            Recipe.from_dict({**recipe, "amplitudes": [1.0] * 3, "total_ms": action}),
            Recipe.from_dict({**recipe, "amplitudes": [1.0] * 3, "total_ms": referent}),
        )
        assert durations == {"K-a1-r2": expected // SAMPLES_PER_MS}
    assert audit.duration_violations({"K-a1-r1": 1_099, "K-a1-r2": 1_100, "Q-a1-r1": 2_001}) == (
        "K-a1-r1",
        "Q-a1-r1",
    )


@given(st.lists(st.sampled_from(TOTAL_MS), min_size=16, max_size=16))
def test_any_committed_durations_compose_within_bounds(totals):
    durations = audit.message_durations_ms(dict(zip(ATOM_IDS, totals, strict=True)))
    assert len(durations) == 32
    assert audit.duration_violations(durations) == ()


def test_masked_reports_contain_no_method_labels(demo_audit):
    result, out = demo_audit
    masked = sorted(p for p in result.files if p.startswith("masked/"))
    assert len(masked) == len([p for p in result.files if p.startswith("unmasked/")]) == 9
    for name in masked:
        text = (out / name).read_text("utf-8")
        assert masking_findings(text) == (), name
        for label in ("A1", "A2", "A3", "D1", '"method"', "designer"):
            assert label not in text, (name, label)
    for name, columns in (
        ("masked/books.csv", audit.MASKED_BOOK_COLUMNS),
        (f"masked/book-{BOOKS['A1']}-atoms.csv", audit.MASKED_ATOM_COLUMNS),
        (f"masked/book-{BOOKS['A1']}-slots.csv", audit.MASKED_SLOT_COLUMNS),
    ):
        assert tuple(_csv(out / name)[0]) == columns
    # The string test is not vacuous: the unmasked report names every method.
    unmasked = (out / "unmasked" / "summary.md").read_text("utf-8")
    assert {"A1", "A2", "A3"} <= {f.split("'")[1] for f in masking_findings(unmasked)}
    # Recipe columns of invalid slots are blank in the masked slot tables.
    for row in _csv(out / "masked" / f"book-{BOOKS['A3']}-slots.csv"):
        if row["valid"] == "0":
            assert row["recipe_sha256"] == row["total_ms"] == ""
    summary = read_json(out / "masked" / "summary.json")
    assert summary["masked"] is True and summary["machines"] is None
    assert [set(b) for b in summary["books"]] == [set(audit.MASKED_BOOK_COLUMNS)] * 3


def test_a_leaky_masked_report_is_never_written(tmp_path):
    run = write_synthetic_batch(
        tmp_path / "runs", synth_spec(run_id="DEMO-A3-LEAK"), validate=False
    )
    with pytest.raises(audit.AuditError) as err:
        audit.build_audit(run.root, tmp_path / "out")
    assert err.value.code == audit.E_MASKING
    assert not (tmp_path / "out").exists()


def test_the_same_logs_give_byte_identical_reports(demo_run, demo_audit, tmp_path):
    first, out = demo_audit
    again = audit.build_audit(demo_run, tmp_path / "again")
    assert again.files == first.files
    for name in first.files:
        assert (out / name).read_bytes() == (tmp_path / "again" / name).read_bytes()
        assert b"\r" not in (out / name).read_bytes()
    # The synthetic logs themselves are reproducible, so the committed example is too.
    rebuilt = write_example(tmp_path / "example", tmp_path / "work")
    assert sorted(rebuilt) == sorted(
        ["hashes.json", "audit/tally.csv", *(f"audit/{n}" for n in EXAMPLE_FILES)]
    )
    committed = sorted(p.relative_to(EXAMPLE).as_posix() for p in EXAMPLE.rglob("*") if p.is_file())
    assert committed == sorted(rebuilt)
    for name in rebuilt:
        assert (tmp_path / "example" / name).read_bytes() == (EXAMPLE / name).read_bytes(), name
    hashes = read_json(EXAMPLE / "hashes.json")
    assert hashes["audit"] == {**first.files, "tally.csv": rebuilt["audit/tally.csv"]}
    summary = read_json(EXAMPLE / "audit" / "unmasked" / "summary.json")
    assert summary["sources"] == hashes["run"]


def test_every_number_traces_back_to_slot_ids(demo_run, demo_audit):
    _, out = demo_audit
    log_ids = {s["slot_id"] for s in _jsonl(demo_run / "logs" / "slots.jsonl")}
    seen: set[str] = set()
    for book_id, row in _books(out).items():
        slots = _csv(out / "unmasked" / f"book-{book_id}-slots.csv")
        atoms = _csv(out / "unmasked" / f"book-{book_id}-atoms.csv")
        ids = [s["slot_id"] for s in slots]
        assert len(ids) == len(set(ids)) == 192 and set(ids) <= log_ids
        assert all(i.startswith(f"{book_id}.") for i in ids)
        seen |= set(ids)
        outcomes = Counter(s["outcome"] for s in slots)
        for code in OUTCOME_CODES:
            assert int(row[f"n_{code}"]) == outcomes[code]
            assert sum(int(a[f"n_{code}"]) for a in atoms) == outcomes[code]
        assert int(row["slots_valid"]) == sum(s["valid"] == "1" for s in slots)
        assert int(row["n_llm_server_error"]) == sum(
            s["llm_status"] == "server_error" for s in slots
        )
        assert int(row["rater_ms"]) == sum(int(s["rater_ms"]) for s in slots)
        assert int(row["rater_ms"]) == sum(int(a["rater_ms"]) for a in atoms)
        for column in ("design_active_ms", "model_runtime_ms", "tokens_in", "tokens_out"):
            if row[column]:
                assert int(row[column]) == sum(int(a[column]) for a in atoms), column
        valid_by_atom = Counter(s["atom_id"] for s in slots if s["valid"] == "1")
        for a in atoms:
            assert int(a["slots_valid"]) == valid_by_atom[a["atom_id"]]
            assert sum(int(a[f"valid_r{r}"]) for r in range(1, 5)) == int(a["slots_valid"])
        masked = _csv(out / "masked" / f"book-{book_id}-slots.csv")
        assert [m["slot_id"] for m in masked] == ids
    assert seen == log_ids


def test_effort_columns(demo_audit):
    _, out = demo_audit
    books = _books(out)
    a1, a2, a3 = (books[BOOKS[m]] for m in ("A1", "A2", "A3"))
    assert a1["familiarization_ms"] == "600000" and int(a1["design_active_ms"]) > 0
    assert a1["model_runtime_ms"] == a1["tokens_in"] == ""
    assert a2["design_active_ms"] == a2["familiarization_ms"] == a2["tokens_out"] == ""
    assert int(a3["model_runtime_ms"]) > 0 and int(a3["tokens_in"]) > int(a3["tokens_out"]) > 0
    # Startup: the model (95 s + 90 s after the resume) for A3, the A1 app (2 x 6 s) for A1.
    assert (a3["startup_ms"], a1["startup_ms"], a2["startup_ms"]) == ("185000", "12000", "0")
    assert (a3["operator_ms"], a1["operator_ms"], a2["operator_ms"]) == ("45000", "30000", "0")
    assert {r["rater_ms"] for r in books.values()} == {str(192 * 3 * 20_000)}
    assert len({r["wall_ms"] for r in books.values()}) == 1
    summary = read_json(out / "unmasked" / "summary.json")
    timing = summary["timing"]
    assert timing["startup_ms"] == 2 * (3_000 + 6_000) + 95_000 + 90_000
    assert timing["operator_ms"] == 120_000 + 45_000 + 30_000
    assert timing["batch_wall_ms"] == sum(a["wall_ms"] for a in timing["atoms"])
    assert int(a1["wall_ms"]) == timing["batch_wall_ms"]
    assert [a["appointment"] for a in timing["appointments"]] == [1, 2, 3, 4]
    assert all(a["wall_ms"] for a in timing["appointments"])  # paired across the resume
    assert timing["max_atom_ms"] == max(a["wall_ms"] for a in timing["atoms"])
    for atom in timing["atoms"]:
        assert atom["max_round_ms"] == max(atom["rounds_ms"]) <= atom["wall_ms"]
    diversity = [float(r["candidate_diversity"]) for r in books.values()]
    assert all(0 < d < 1 for d in diversity)


def test_only_the_run_files_are_read(demo_run, tmp_path, monkeypatch):
    run = _copy_run(demo_run, tmp_path)
    (run / "decoy-outcomes.csv").write_text("never read\n", encoding="utf-8")
    (run / "logs" / "plays.jsonl").write_text("not json\n", encoding="utf-8")
    opened: list[Path] = []
    real_read_bytes, real_open = pathlib.Path.read_bytes, builtins.open

    def spy_read_bytes(self):
        opened.append(Path(self).resolve())
        return real_read_bytes(self)

    def spy_open(file, mode="r", *args, **kwargs):
        if isinstance(file, str | os.PathLike) and "r" in mode:
            opened.append(Path(file).resolve())
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_bytes", spy_read_bytes)
    monkeypatch.setattr(builtins, "open", spy_open)
    result = audit.build_audit(run, tmp_path / "out")
    monkeypatch.undo()
    assert result.ok
    inside = sorted(
        {p.relative_to(run.resolve()).as_posix() for p in opened if run.resolve() in p.parents}
    )
    expected = sorted(read_json(tmp_path / "out" / "unmasked" / "summary.json")["sources"])
    assert inside == expected
    assert "decoy-outcomes.csv" not in inside and "logs/plays.jsonl" not in inside
    schema_roots = (ROOT / "generation" / "schema", ROOT / "sound" / "schema")
    outside = {p for p in opened if run.resolve() not in p.parents}
    assert all(any(root.resolve() in p.parents for root in schema_roots) for p in outside), outside


# ---------------------------------------------------------------------------
# Checks on broken logs


def _drop_line(path: Path, predicate) -> dict:
    lines = path.read_bytes().splitlines(keepends=True)
    keep = [line for line in lines if not predicate(json.loads(line))]
    dropped = [json.loads(line) for line in lines if predicate(json.loads(line))]
    path.write_bytes(b"".join(keep))
    return dropped[0]


def test_missing_and_duplicate_records_are_reported(demo_run, tmp_path):
    run = _copy_run(demo_run, tmp_path)
    slot = _drop_line(run / "logs" / "slots.jsonl", lambda s: s["slot_id"].endswith("K-a1.r2s2"))
    commits = (run / "logs" / "commits.jsonl").read_bytes().splitlines(keepends=True)
    with open(run / "logs" / "commits.jsonl", "ab") as handle:
        handle.write(commits[0])
    result = audit.build_audit(run, tmp_path / "out")
    assert not result.ok
    book = slot["book_id"]
    problems = "\n".join(result.problems)
    assert f"book {book}: 191 slot records (expected 192)" in problems
    assert f"book {book}: atom K-a1 has 11 slot records (expected 12)" in problems
    assert "committed more than once" in problems
    assert "unknown slot " + slot["slot_id"] in problems
    row = _books(tmp_path / "out")[book]
    assert row["slots_total"] == "191"
    summary = read_json(tmp_path / "out" / "masked" / "summary.json")
    assert summary["checks"]["ok"] is False and summary["checks"]["problems"]
    assert audit.main(["batch", str(run), "--out", str(tmp_path / "cli"), "--strict"]) == 1


def test_timing_gaps_and_clock_restarts_are_reported(demo_run, tmp_path):
    run = _copy_run(demo_run, tmp_path)
    _drop_line(run / "logs" / "timing.jsonl", lambda e: e["event"] == "atom_end")
    result = audit.build_audit(run, tmp_path / "out")
    assert any(p.endswith("atom_start without atom_end") for p in result.problems)
    summary = read_json(tmp_path / "out" / "unmasked" / "summary.json")
    assert summary["timing"]["batch_wall_ms"] is None
    assert _books(tmp_path / "out")[BOOKS["A1"]]["wall_ms"] == ""


def _event(event, t_ms, **fields):
    return TimingEvent(run_id="DEMO-r", event=event, t_ms=t_ms, **fields)


def test_pair_intervals():
    events = [
        _event("atom_start", 100, atom_id="K-a1"),
        _event("atom_end", 400, atom_id="K-a1"),
        _event("atom_start", 900, atom_id="K-a2"),
        _event("atom_end", 50, atom_id="K-a2"),  # clock restarted: not counted
        _event("atom_start", 10, atom_id="K-a3"),
        _event("atom_end", 5, atom_id="K-a3", duration_ms=70),  # writer's own duration wins
        _event("atom_end", 60, atom_id="K-a4"),
        _event("atom_start", 70, atom_id="K-r1"),
    ]
    iv = audit.pair_intervals(
        events, "atom_start", "atom_end", lambda e: e.atom_id, lambda k: f"atom {k}"
    )
    assert iv.durations == {"K-a1": [300], "K-a3": [70]}
    assert iv.single("K-a2") is None and iv.total() == 370
    assert iv.problems == (
        "atom K-a2: atom_end is earlier than atom_start on the run clock",
        "atom K-a4: atom_end without atom_start",
        "atom K-r1: atom_start without atom_end",
    )


def test_bad_inputs_are_refused(demo_run, tmp_path):
    with pytest.raises(audit.AuditError) as err:
        audit.read_run_logs(tmp_path)
    assert err.value.code == audit.E_INPUT
    run = _copy_run(demo_run, tmp_path)
    with open(run / "logs" / "decisions.jsonl", "ab") as handle:
        handle.write(b'{"record": "decision"')  # torn last line
    with pytest.raises(audit.AuditError, match="repair_torn_tail"):
        audit.read_run_logs(run)
    (run / "logs" / "decisions.jsonl").write_bytes(b'{"record": "decision"}\n')
    with pytest.raises(audit.AuditError, match="decisions.jsonl:1"):
        audit.read_run_logs(run)
    (run / "logs" / "decisions.jsonl").unlink()
    logs = audit.read_run_logs(run)
    assert logs.missing == ("logs/decisions.jsonl",)
    assert "logs/decisions.jsonl is missing" in audit.compute_audit(logs).problems
    manifest = read_json(run / "run-manifest.json")
    manifest["study"] = "B"
    (run / "run-manifest.json").write_text(document_text(manifest), encoding="utf-8")
    with pytest.raises(audit.AuditError) as err:
        audit.read_run_logs(run)
    assert err.value.code == audit.E_STUDY


def test_restricted_reports_stay_out_of_git_work_trees(demo_run, tmp_path):
    run = _copy_run(demo_run, tmp_path)
    manifest = read_json(run / "run-manifest.json")
    manifest.update(kind="pilot", run_id="AUDIT-P01-RUN")  # restricted run (not DEMO-)
    (run / "run-manifest.json").write_text(document_text(manifest), encoding="utf-8")
    target = ROOT / "generation" / "out" / "never-written"
    with pytest.raises(RunPolicyError) as err:
        audit.build_audit(run, target)
    assert err.value.code == "E_POLICY"
    assert not target.exists()
    assert audit.build_audit(run, tmp_path / "restricted").files  # outside any work tree


def test_machine_specifications_go_to_the_unmasked_summary_only(demo_run, tmp_path):
    specs = tmp_path / "machines.json"
    specs.write_text(
        json.dumps({"llm_host": {"gpu": "synthetic GPU", "driver": "0.0"}, "a1_station": {}}),
        encoding="utf-8",
    )
    result = audit.build_audit(demo_run, tmp_path / "out", machines=specs)
    unmasked = read_json(tmp_path / "out" / "unmasked" / "summary.json")
    masked = read_json(tmp_path / "out" / "masked" / "summary.json")
    assert unmasked["machines"] == {
        "a1_station": {},
        "llm_host": {"driver": "0.0", "gpu": "synthetic GPU"},
    }
    assert masked["machines"] is None
    assert unmasked["sources"]["machines.json"] == hashlib.sha256(specs.read_bytes()).hexdigest()
    assert "synthetic GPU" in (tmp_path / "out" / "unmasked" / "summary.md").read_text("utf-8")
    assert "synthetic GPU" not in (tmp_path / "out" / "masked" / "summary.md").read_text("utf-8")
    assert result.ok
    specs.write_text(json.dumps({"LLM Host": {}}), encoding="utf-8")
    with pytest.raises(audit.AuditError):
        audit.build_audit(demo_run, tmp_path / "bad", machines=specs)


# ---------------------------------------------------------------------------
# Set tables and the cross-batch summary


@pytest.fixture(scope="module")
def set_runs(tmp_path_factory, demo_run) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("set-runs")
    runs = {"p01": demo_run}
    runs["p02"] = write_synthetic_batch(
        root, synth_spec(run_id="DEMO-AUDIT-02", batch=2, book_fallback=None, archive_none=None)
    ).root
    runs["p02-withdrawn"] = write_synthetic_batch(
        root, synth_spec(run_id="DEMO-AUDIT-02X", batch=2, incomplete=True), validate=False
    ).root
    runs["p03-open"] = write_synthetic_batch(
        root, synth_spec(run_id="DEMO-AUDIT-03", batch=3, closed=False), validate=False
    ).root
    return runs


def test_set_audit_tables(set_runs, tmp_path):
    inputs = [set_runs["p02-withdrawn"], set_runs["p02"], set_runs["p01"]]
    masked = audit.build_set_audit(inputs, tmp_path / "m", study="A", set_name="demo", masked=True)
    unmasked = audit.build_set_audit(
        inputs, tmp_path / "u", study="A", set_name="demo", masked=False
    )
    assert Path(masked.path).name == "A-demo-audit.csv"
    assert Path(unmasked.path).name == "A-demo-audit-unmasked.csv"
    assert masked.runs == unmasked.runs == ("DEMO-AUDIT-01", "DEMO-AUDIT-02")
    assert masked.excluded_runs == ("DEMO-AUDIT-02X",)
    rows = _csv(Path(masked.path))
    assert tuple(rows[0]) == audit.MASKED_BOOK_COLUMNS
    keys = [(r["batch_id"], r["book_id"]) for r in rows]
    assert keys == sorted(keys) and len(keys) == 6
    assert hashlib.sha256(Path(masked.path).read_bytes()).hexdigest() == masked.sha256
    for path in (masked.path, masked.summary_path):
        assert masking_findings(Path(path).read_text("utf-8")) == ()
    unmasked_rows = _csv(Path(unmasked.path))
    assert tuple(unmasked_rows[0]) == audit.BOOK_COLUMNS
    assert [(r["batch_id"], r["book_id"]) for r in unmasked_rows] == keys
    per_batch = {r["batch_id"]: r for r in unmasked_rows if r["method"] == "A1"}
    assert per_batch["DEMO-A-P02"]["failed_generation"] == "0"
    assert per_batch["DEMO-A-P01"]["failed_generation"] == "1"
    text = Path(unmasked.summary_path).read_text("utf-8")
    assert "## Per method" in text and "| A3 | 2 | 384 |" in text
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == unmasked.summary_sha256
    again = audit.build_set_audit(
        list(reversed(inputs)), tmp_path / "m2", study="A", set_name="demo", masked=True
    )
    assert again.sha256 == masked.sha256 and again.summary_sha256 == masked.summary_sha256


def test_set_audit_rules(set_runs, demo_run, tmp_path):
    def build(runs, **kwargs):
        args = {"study": "A", "set_name": "demo", "masked": True, **kwargs}
        return audit.build_set_audit(runs, tmp_path / "out", **args)

    with pytest.raises(audit.AuditError, match="more than one complete run"):
        build([demo_run, set_runs["p01"]])
    with pytest.raises(audit.AuditError, match="without a complete run: DEMO-A-P03"):
        build([demo_run, set_runs["p03-open"]])
    with pytest.raises(audit.AuditError, match="belongs to set 'demo'"):
        build([demo_run], set_name="pilot")
    with pytest.raises(audit.AuditError, match="Study A"):
        build([demo_run], study="B")
    with pytest.raises(audit.AuditError, match="unknown set"):
        build([demo_run], set_name="main")


# ---------------------------------------------------------------------------
# Command line


def test_command_line(set_runs, tmp_path, capsys):
    run = set_runs["p01"]
    assert audit.main(["batch", str(run), "--out", str(tmp_path / "batch")]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["ok"] is True and len(printed["files"]) == 18
    assert audit.main(["tally", str(run), "--out", str(tmp_path / "t" / "tally.csv")]) == 0
    assert json.loads(capsys.readouterr().out)["all_match"] is True
    args = ["set", str(run), str(set_runs["p02"]), "--set", "demo"]
    assert audit.main([*args, "--masked-out", str(tmp_path / "m")]) == 0
    assert set(json.loads(capsys.readouterr().out)) == {"masked"}
    assert (
        audit.main(
            [*args, "--masked-out", str(tmp_path / "m"), "--unmasked-out", str(tmp_path / "u")]
        )
        == 0
    )
    assert set(json.loads(capsys.readouterr().out)) == {"masked", "unmasked"}
    assert audit.main(args) == 2
    assert "E_SET" in capsys.readouterr().err
    assert audit.main(["batch", str(tmp_path / "nothing")]) == 2


# ---------------------------------------------------------------------------
# Metric properties


_RECIPES = st.builds(
    lambda total, pitches, weights, gaps, amps: {
        "total_ms": total,
        "pitches": pitches,
        "rhythm_weights": weights,
        "gaps_ms": gaps,
        "amplitudes": amps,
    },
    st.sampled_from(TOTAL_MS),
    st.lists(st.integers(-6, 6), min_size=3, max_size=3),
    st.lists(st.integers(1, 4), min_size=3, max_size=3),
    st.lists(st.sampled_from((20, 40, 60)), min_size=2, max_size=2),
    st.lists(st.sampled_from((0.6, 0.8, 1.0)), min_size=3, max_size=3),
)


@settings(max_examples=60, deadline=None)
@given(st.lists(_RECIPES, max_size=6), st.randoms(use_true_random=False))
def test_mean_pairwise_distance(recipes, rnd):
    value = audit.mean_pairwise_distance(recipes)
    if len(recipes) < 2:
        assert value is None
        return
    assert 0.0 <= value <= 1.0
    shuffled = list(recipes)
    rnd.shuffle(shuffled)
    assert audit.mean_pairwise_distance(shuffled) == pytest.approx(value, abs=1e-12)
    assert audit.mean_pairwise_distance([recipes[0], recipes[0]]) == 0.0


@given(
    st.lists(
        st.fixed_dictionaries(
            {
                "a": st.one_of(st.none(), st.booleans(), st.integers(-5, 10**12)),
                "b": st.one_of(st.none(), st.floats(0, 1, allow_nan=False), st.text(max_size=8)),
            }
        ),
        max_size=5,
    )
)
def test_csv_cells(rows):
    text = audit.csv_text(("a", "b"), rows)
    parsed = list(csv.reader(io.StringIO(text, newline="")))
    assert parsed[0] == ["a", "b"] and len(parsed) == len(rows) + 1
    for row, cells in zip(rows, parsed[1:], strict=True):
        a, b = row["a"], row["b"]
        assert cells[0] == (
            "" if a is None else ("1" if a is True else "0" if a is False else str(a))
        )
        if isinstance(b, float):
            assert cells[1] == f"{b:.6f}"
        elif b is None:
            assert cells[1] == ""


def test_synthetic_spec_defaults_are_the_example():
    assert SynthSpec().run_id == "DEMO-AUDIT-01"
    assert SynthSpec().book_fallback == (Method.A1, 7)
