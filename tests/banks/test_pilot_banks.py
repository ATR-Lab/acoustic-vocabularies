"""Pilot banks (#27): IDs and seed namespaces, the plan, the run driver with spares, the
register, verification, archive, the throughput summary and the command line.

The end-to-end rehearsal builds the 8 pilot banks on DEMO IDs (`DEMO-bank-P001`..) with a
fake model (`DemoModel`: every output is a pure function of the whole B seed key, so a
spare's new seed namespace gets new outputs) and the kit's #17 stand-ins. The script makes
`DEMO-bank-P003` fail its first attempt and `DEMO-bank-P006` unavailable (a cell whose
12 slots are all malformed), so the run uses one spare (`DEMO-bank-P006` v1.1.0).

Set `AV_BANKS_DEMO_PILOT_OUT=<dir>` to write the small evidence files of the rehearsal
(`banks/examples/demo-pilot/` holds the committed copy).
"""

import csv
import dataclasses
import hashlib
import json
import os
import random
import shutil
import subprocess
from pathlib import Path

import av_generation
import pytest
from av_generation.bank_manifest import BankSetError, require_bank_set
from av_generation.clock import ManualClock
from av_generation.genconfig import ConfigMismatch
from av_generation.jsonio import file_set_sha256, file_sha256, read_json
from av_generation.llm import RawOutcome
from av_generation.llm_fake import (
    ScriptedLlmClient,
    overflow_outcome,
    server_error_outcome,
    timeout_outcome,
)
from av_generation.outcomes import LlmStatus
from av_generation.records import SlotRecord, read_records
from av_generation.rundir import RunPolicyError
from av_generation.seeds import b_seed_key
from hypothesis import given, settings
from hypothesis import strategies as st

import av_banks
import av_banks.pilot as pilot
from av_banks.amend import amend_bank
from av_banks.archive import (
    ARCHIVE_HASH_NAME,
    ARCHIVE_MANIFEST_NAME,
    archive_problems,
    is_read_only,
    make_writable,
)
from av_banks.builder import seed_namespace_error
from av_banks.layout import BankLayout
from av_banks.manifest import AttemptSummary, read_manifest
from av_banks.proposer import LlmSlotProposer, ProposerConfigError
from av_banks.register import (
    REGISTER_COLUMNS,
    check_bank_id_set,
    open_bank,
    read_register,
    register_problems,
    write_register,
)

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "banks" / "examples" / "demo-pilot"
RUN_ID = "DEMO-pilot-banks"
SMALL_RUN = "DEMO-small-pilot"
UNAVAILABLE = "DEMO-bank-P006"
RETRIED = "DEMO-bank-P003"
OTHER_KINDS = ("invalid_json", "schema", "domain", "timeout", "overflow_output", "server_error")


# -- DEMO units and the fake model ---------------------------------------------


def write_units(kit, root, *, demo=True, slots=8, set_name="pilot"):
    """Pilot units `B-P01`.. derived from the committed DEMO unit (synthetic)."""
    doc = read_json(kit.DEMO_UNIT)
    for slot in range(1, slots + 1):
        unit = f"B-P{slot:02d}"
        doc.update(unit_id=unit, set=set_name, demo=demo)
        if not demo:
            doc["seed_label"] = "sha256:" + "6" * 64
        path = Path(root) / unit / "permutation.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        # `\n` on every OS: the permutation hash feeds the bank, register and archive hashes
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(doc, indent=2) + "\n")
    return Path(root)


def _rng(*parts):
    digest = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


class DemoModel:
    """A fake model: output kind, text and latency are pure functions of the B seed key
    (namespace, attempt, profile, atom, slot). Each call advances the clock by its
    simulated latency (synthetic throughput figures)."""

    def __init__(self, kit, clock, kind):
        self.kit = kit
        self.clock = clock
        self.kind = kind

    def text(self, kind, ns, attempt, profile, atom, slot):
        if kind == "repeat" and slot > 1:
            return self.text(self.kind(ns, attempt, profile, atom, slot - 1), ns, attempt,
                             profile, atom, slot - 1)  # fmt: skip
        if kind == "invalid_json":
            return "not json {"
        if kind == "schema":
            return '{"total_ms": 600}'
        recipe = self.kit.fresh_recipe(ns, attempt, profile, atom, slot)
        if kind == "domain":
            recipe["total_ms"] = 500
        return json.dumps(recipe)

    def respond(self, messages, schema, seed_key):
        _, ns, attempt, profile, atom, slot = seed_key.split("|")
        key = (ns, int(attempt), profile, atom, int(slot))
        kind = self.kind(*key)
        if kind == "timeout":
            self.clock.advance(40_000)
            return timeout_outcome()
        latency = 900 + _rng("latency", *key).randrange(2_600)
        self.clock.advance(latency)
        if kind == "overflow_output":
            return overflow_outcome()
        if kind == "server_error":
            return server_error_outcome()
        text = self.text(kind, *key)
        return RawOutcome(LlmStatus.OK, text, latency, 100, len(text.split()), 0, "stop")

    def proposer(self, calls=4 * 576 * 3):
        client = ScriptedLlmClient([self.respond] * calls, token_counter=lambda messages: 100)
        kit = self.kit
        return LlmSlotProposer(
            client,
            kit.prompt_set,
            kit.decoding_schema,
            prompt_builder=kit.dump_prompt,
            parser=kit.strict_parser,
            threshold=kit.config.separation_threshold,
        )

    def factory(self, layout):
        return self.proposer(calls=4 * 576 * 10)


def demo_kinds(order, p_valid=0.75):
    def kind(ns, attempt, profile, atom, slot):
        if ns == UNAVAILABLE and profile == "P2" and atom == order[5]:
            return "invalid_json"
        if ns == RETRIED and attempt == 1 and profile == "P1" and atom == order[9]:
            return "schema"
        rng = _rng("kind", ns, attempt, profile, atom, slot)
        if rng.random() < p_valid:
            return "valid"
        return rng.choice((*OTHER_KINDS, "repeat"))

    return kind


def failing_first_cell(order, banks):
    """`banks` (seed namespaces) fail every attempt at their first cell; others valid."""

    def kind(ns, attempt, profile, atom, slot):
        return "invalid_json" if ns in banks and (profile, atom) == ("P1", order[0]) else "valid"

    return kind


def run_demo(kit, root, units, kind, *, dyads=8, spares=2, run_id=RUN_ID, clock=None):
    clock = clock or ManualClock()
    plan = pilot.pilot_plan(units, run_id=run_id, demo=True, dyads=dyads, spares=spares)
    model = DemoModel(kit, clock, kind)
    return pilot.run_pilot(
        plan,
        root=root,
        config=kit.config,
        proposer=model.factory,
        clock=clock,
        workers=1,
        ledger_factory=kit.Ledger,
        fsync=False,
    )


@pytest.fixture(scope="module")
def demo_units(kit, tmp_path_factory):
    return write_units(kit, tmp_path_factory.mktemp("units"))


@pytest.fixture(scope="module")
def demo_pilot(kit, demo_units, tmp_path_factory):
    """The full DEMO rehearsal: 8 main banks, one spare (built once per module)."""
    root = tmp_path_factory.mktemp("pilot") / "root"
    return run_demo(kit, root, demo_units, demo_kinds(kit.permutation.atom_order))


@pytest.fixture(scope="module")
def small_pilot(kit, demo_units, tmp_path_factory):
    """2 dyad slots, 1 spare: `DEMO-bank-P002` is unavailable, its spare complete."""
    root = tmp_path_factory.mktemp("small") / "root"
    kind = failing_first_cell(kit.permutation.atom_order, {"DEMO-bank-P002"})
    return run_demo(kit, root, demo_units, kind, dyads=2, spares=1, run_id=SMALL_RUN)


@pytest.fixture(scope="module")
def small_reports(small_pilot):
    """The small pilot's verify reports by bank path, for `finish_pilot(reports=)` on an
    unchanged copy (saves re-running banks verify)."""
    return {
        row.bank_path: report
        for row, report in zip(small_pilot.finish.rows, small_pilot.finish.reports, strict=True)
    }


@pytest.fixture
def pilot_copy(small_pilot, tmp_path):
    """A writable copy of the small pilot's root (archiving makes files read-only)."""
    target = tmp_path / "copy"
    shutil.copytree(small_pilot.root, target)
    yield target
    make_writable(target)


# -- IDs and seed namespaces ----------------------------------------------------


def test_pilot_ids():
    assert [pilot.pilot_bank_id(k) for k in (1, 8, 10)] == ["bank-P001", "bank-P008", "bank-P010"]
    assert pilot.pilot_bank_id(2, demo=True) == "DEMO-bank-P002"
    assert pilot.pilot_unit_id(7) == "B-P07"
    assert [pilot.spare_version(n) for n in (1, 2)] == ["1.1.0", "1.2.0"]
    for bad in (0, 100, True, "1"):
        with pytest.raises(pilot.PilotError, match="E_PLAN"):
            pilot.pilot_bank_id(bad)
    for bad in (0, 10):
        with pytest.raises(pilot.PilotError, match="E_PLAN"):
            pilot.spare_version(bad)
    assert pilot.main_version() == "1.0.0" and pilot.spare_version(2, major=3) == "3.2.0"
    with pytest.raises(pilot.PilotError, match="major"):
        pilot.main_version(0)


def test_a_replacement_run_uses_a_new_major_version(kit, demo_units):
    plan = pilot.pilot_plan(demo_units, run_id="DEMO-pilot-02", demo=True, dyads=2, major=2)
    assert [(s.bank_version, s.seed_namespace) for s in plan.banks] == [
        ("2.0.0", "DEMO-bank-P001-v2.0.0"),
        ("2.0.0", "DEMO-bank-P002-v2.0.0"),
    ]
    spare = plan.spare_spec(plan.banks[1], 1)
    assert (spare.bank_version, spare.seed_namespace) == ("2.1.0", "DEMO-bank-P002-v2.1.0")
    assert plan.document(kit.config, "x")["major"] == 2


@settings(max_examples=200, deadline=None)
@given(
    pilot_seq=st.integers(1, 999),
    conf_seq=st.integers(1, 999),
    minor=st.integers(0, 9),
    conf_version=st.sampled_from(["1.0.0", "1.1.0", "1.2.0", "2.0.0"]),
    suffix=st.sampled_from([None, "a", "rerun-2"]),
)
def test_pilot_and_confirmatory_seed_namespaces_never_meet(
    pilot_seq, conf_seq, minor, conf_version, suffix
):
    """Every namespace a pilot bank may use is refused for every confirmatory bank, and
    their B seed keys differ (Study B protocol §4: independently seeded banks)."""
    bank = f"bank-P{pilot_seq:03d}"
    version = f"1.{minor}.0"
    namespace = bank if minor == 0 else f"{bank}-v{version}"
    if suffix is not None:
        namespace = f"{bank}-v{version}-{suffix}"
    assert seed_namespace_error(bank, version, namespace) is None
    confirmatory = f"bank-C{conf_seq:03d}"
    assert seed_namespace_error(confirmatory, conf_version, namespace) is not None
    conf_ns = confirmatory if conf_version == "1.0.0" else f"{confirmatory}-v{conf_version}"
    assert b_seed_key(namespace, 1, "P1", "K-a1", 1) != b_seed_key(conf_ns, 1, "P1", "K-a1", 1)


# -- the plan -------------------------------------------------------------------


def test_plan_binds_slots_to_units(kit, demo_units):
    plan = pilot.pilot_plan(demo_units, run_id=RUN_ID, demo=True)
    assert [s.bank_id for s in plan.banks] == [f"DEMO-bank-P{k:03d}" for k in range(1, 9)]
    assert [s.permutation.unit_id for s in plan.banks] == [f"B-P{k:02d}" for k in range(1, 9)]
    assert [s.seed_namespace for s in plan.banks] == [s.bank_id for s in plan.banks]
    assert plan.spares == 2 and plan.set_name == "demo"
    spare = plan.spare_spec(plan.banks[5], 1)
    assert (spare.bank_id, spare.bank_version, spare.seed_namespace) == (
        "DEMO-bank-P006",
        "1.1.0",
        "DEMO-bank-P006-v1.1.0",
    )
    assert spare.permutation == plan.banks[5].permutation
    doc = plan.document(kit.config, "2026-12-14T00:00:00Z")
    assert doc["generation_config"]["sha256"] == kit.config.frozen_sha256()
    assert doc["generation_config"]["separation_threshold"] == "0.10"
    assert doc["builder"]["av_banks"] and doc["dyad_slots"] == 8


def test_the_plan_records_the_builder_code(demo_pilot):
    doc = read_json(demo_pilot.root / pilot.PLAN_NAME)
    builder = doc["builder"]
    assert builder["av_banks"] == av_banks.__version__
    assert builder["av_generation"] == av_generation.__version__
    assert builder["source_sha256"] == {
        "av_banks": pilot.source_sha256(Path(av_banks.__file__).resolve().parent),
        "av_generation": pilot.source_sha256(Path(av_generation.__file__).resolve().parent),
    }
    git = builder["git"]  # None only outside a git checkout
    assert git is None or (
        pilot.GIT_COMMIT_RE.fullmatch(git["commit"]) and isinstance(git["dirty"], bool)
    )
    summary = read_json(demo_pilot.root / pilot.SUMMARY_JSON_NAME)
    assert summary["builder"] == builder
    note = (demo_pilot.root / pilot.SUMMARY_MD_NAME).read_text(encoding="utf-8")
    assert f"source SHA-256 `{builder['source_sha256']['av_banks']}`" in note
    assert f"source SHA-256 `{builder['source_sha256']['av_generation']}`" in note


def test_source_hash_covers_the_python_files_of_a_package(tmp_path):
    package = tmp_path / "package"
    (package / "sub" / "__pycache__").mkdir(parents=True)
    (package / "a.py").write_bytes(b"x = 1\n")
    (package / "sub" / "b.py").write_bytes(b"y = 2\n")
    first = pilot.source_sha256(package)
    assert first == file_set_sha256(
        {
            "a.py": hashlib.sha256(b"x = 1\n").hexdigest(),
            "sub/b.py": hashlib.sha256(b"y = 2\n").hexdigest(),
        }
    )
    (package / "sub" / "__pycache__" / "c.py").write_bytes(b"cache")
    (package / "data.json").write_bytes(b"{}")
    assert pilot.source_sha256(package) == first
    (package / "a.py").write_bytes(b"x = 2\n")
    assert pilot.source_sha256(package) != first
    (tmp_path / "empty").mkdir()
    with pytest.raises(pilot.PilotError, match="no Python source files"):
        pilot.source_sha256(tmp_path / "empty")


def test_git_identity(monkeypatch, tmp_path):
    here = pilot.git_identity(ROOT)
    assert here is None or pilot.GIT_COMMIT_RE.fullmatch(here["commit"])
    answers = {"rev-parse": "a" * 40 + "\n", "status": " M banks/x.py\n"}

    def fake_run(args, **kwargs):
        assert args[:3] == ["git", "-C", os.fspath(tmp_path)] and kwargs["check"]
        return subprocess.CompletedProcess(args, 0, answers[args[3]], "")

    monkeypatch.setattr(pilot.subprocess, "run", fake_run)
    assert pilot.git_identity(tmp_path) == {"commit": "a" * 40, "dirty": True}
    answers["status"] = ""
    assert pilot.git_identity(tmp_path) == {"commit": "a" * 40, "dirty": False}
    answers["rev-parse"] = "HEAD\n"
    assert pilot.git_identity(tmp_path) is None

    def failing(error):
        def run(args, **kwargs):
            raise error

        return run

    for error in (FileNotFoundError("git"), subprocess.CalledProcessError(128, ["git"])):
        monkeypatch.setattr(pilot.subprocess, "run", failing(error))
        assert pilot.git_identity(tmp_path) is None
    builder = {"av_banks": "0.1.0", "av_generation": "0.1.0", "git": None}
    assert "git commit not recorded" in pilot.builder_text(builder)
    assert "source SHA-256 `not recorded`" in pilot.builder_text(builder)


def test_real_plan_uses_pilot_ids(kit, tmp_path):
    units = write_units(kit, tmp_path / "units", demo=False, slots=2)
    plan = pilot.pilot_plan(units, run_id="P-banks-01", dyads=2)
    assert [s.bank_id for s in plan.banks] == ["bank-P001", "bank-P002"]
    assert plan.set_name == "pilot" and plan.kind.value == "pilot"


def test_plan_refusals(kit, tmp_path, demo_units):
    with pytest.raises(RunPolicyError):
        pilot.pilot_plan(demo_units, run_id="P-banks-01", demo=True)
    with pytest.raises(RunPolicyError):
        pilot.pilot_plan(demo_units, run_id="DEMO-x-01")  # real plan, DEMO run ID
    with pytest.raises(pilot.PilotError, match="real units"):
        pilot.pilot_plan(demo_units, run_id="P-banks-01")
    with pytest.raises(pilot.PilotError, match="dyads"):
        pilot.pilot_plan(demo_units, run_id=RUN_ID, demo=True, dyads=0)
    with pytest.raises(pilot.PilotError, match="spares"):
        pilot.pilot_plan(demo_units, run_id=RUN_ID, demo=True, spares=10)
    confirmatory = write_units(kit, tmp_path / "conf", slots=1, set_name="confirmatory")
    with pytest.raises(pilot.PilotError, match="not pilot unit B-P01"):
        pilot.pilot_plan(confirmatory, run_id=RUN_ID, demo=True, dyads=1)
    with pytest.raises(FileNotFoundError):
        pilot.pilot_plan(demo_units, run_id=RUN_ID, demo=True, dyads=9)


def test_a_run_id_too_long_for_its_spare_runs_is_refused_before_any_build(
    kit, demo_units, tmp_path
):
    run_id = "DEMO-" + "x" * 59  # 64 characters: a valid main run ID
    with pytest.raises(pilot.PilotError, match="E_PLAN: spare run 1 of run .*shorter run ID"):
        pilot.pilot_plan(demo_units, run_id=run_id, demo=True, dyads=1, spares=2)
    assert pilot.pilot_plan(demo_units, run_id=run_id, demo=True, dyads=1, spares=0).spares == 0
    short = pilot.pilot_plan(demo_units, run_id=run_id[:-3], demo=True, dyads=1, spares=2)
    assert short.spare_run_id(2) == run_id[:-3] + "-S2" and len(short.spare_run_id(2)) == 64
    # a plan made by hand is checked before the root is written
    unchecked = dataclasses.replace(short, run_id=run_id)
    with pytest.raises(pilot.PilotError, match="E_PLAN: spare run 1"):
        pilot.run_pilot(
            unchecked, root=tmp_path / "x", config=kit.config, proposer=None, clock=ManualClock()
        )
    assert not (tmp_path / "x").exists()


def test_real_pilot_root_is_refused_inside_git(kit, tmp_path):
    units = write_units(kit, tmp_path / "units", demo=False, slots=1)
    plan = pilot.pilot_plan(units, run_id="P-banks-01", dyads=1)
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    real = kit.make_config("pilot-1", prompt_set=kit.prompt_set, llm="b" * 64)
    with pytest.raises(RunPolicyError, match="git work tree"):
        pilot.run_pilot(
            plan, root=tmp_path / "repo" / "pilot", config=real, proposer=None, clock=ManualClock()
        )
    assert not (tmp_path / "repo" / "pilot").exists()


def test_a_wrong_config_is_refused_before_the_root_is_written(kit, demo_units, tmp_path):
    plan = pilot.pilot_plan(demo_units, run_id=RUN_ID, demo=True, dyads=1)
    real = kit.make_config("pilot-1", prompt_set=kit.prompt_set, llm="b" * 64)
    model = DemoModel(kit, ManualClock(), lambda *key: "valid")
    with pytest.raises(ConfigMismatch, match="E_CONFIG_KIND"):
        pilot.run_pilot(plan, root=tmp_path / "a", config=real, proposer=model.factory,
                        clock=ManualClock())  # fmt: skip
    other = kit.make_config(prompt_set=kit.make_prompt_set(kit.meanings, instruction="other"))
    with pytest.raises(ProposerConfigError, match="B prompt set"):
        pilot.run_pilot(plan, root=tmp_path / "b", config=other, proposer=model.proposer(),
                        clock=ManualClock())  # fmt: skip
    assert not (tmp_path / "a").exists() and not (tmp_path / "b").exists()


# -- the DEMO rehearsal ----------------------------------------------------------


def test_rehearsal_builds_8_banks_and_one_spare(demo_pilot):
    finish = demo_pilot.finish
    rows = {(r.bank_id, r.bank_version): r for r in finish.rows}
    assert len(rows) == 9
    assert [r.role for r in finish.rows].count("spare") == 1
    assert rows[(UNAVAILABLE, "1.0.0")].status == "unavailable"
    assert not rows[(UNAVAILABLE, "1.0.0")].use
    assert rows[(UNAVAILABLE, "1.0.0")].attempts == 4
    assert "unavailable: 4 attempts failed" in rows[(UNAVAILABLE, "1.0.0")].reason
    spare = rows[(UNAVAILABLE, "1.1.0")]
    assert (spare.role, spare.status, spare.use, spare.dyad_slot) == (
        "spare",
        "complete",
        True,
        "B-P06",
    )
    assert spare.seed_namespace == "DEMO-bank-P006-v1.1.0"
    assert spare.run_id == "DEMO-pilot-banks-S1"
    assert rows[(RETRIED, "1.0.0")].attempt_used == 2
    assert "failed cells: t1 P1" in rows[(RETRIED, "1.0.0")].reason
    used = sorted(r.dyad_slot for r in finish.rows if r.use)
    assert used == [f"B-P{k:02d}" for k in range(1, 9)]
    assert finish.shortfall == () and finish.problems == () and finish.verified
    assert finish.exit_code == 0
    assert [r.run_id for r in demo_pilot.runs] == ["DEMO-pilot-banks", "DEMO-pilot-banks-S1"]
    for row in finish.rows:
        assert (
            row.generation_config_sha256
            == demo_pilot.runs[0].banks[0].manifest.generation_config_sha256
        )
        assert row.separation_threshold == "0.10" and row.set == "demo"


def test_rehearsal_text_files_have_lf_line_ends(demo_pilot):
    """The rehearsal's hashes are the same on every OS: no text file holds a CR."""
    texts = [
        path
        for path in demo_pilot.root.rglob("*")
        if path.suffix in {".json", ".jsonl", ".csv", ".txt", ".md"}
    ]
    assert len(texts) > 80
    assert [path.name for path in texts if b"\r" in path.read_bytes()] == []


def test_every_slot_of_the_spare_uses_its_own_namespace(demo_pilot):
    layout = BankLayout(demo_pilot.root / "runs" / "DEMO-pilot-banks-S1" / "banks" / UNAVAILABLE)
    keys = {r.seed_key.split("|")[1] for r in read_records(layout.slots(1), SlotRecord)}
    assert keys == {"DEMO-bank-P006-v1.1.0"}
    main = BankLayout(demo_pilot.root / "runs" / RUN_ID / "banks" / UNAVAILABLE)
    for attempt in range(1, 5):
        keys = {r.seed_key.split("|")[1] for r in read_records(main.slots(attempt), SlotRecord)}
        assert keys == {UNAVAILABLE}
        assert AttemptSummary.read(main.attempt_summary(attempt)).status == "failed"


def test_register_hashes_equal_recomputed_hashes(demo_pilot):
    root = demo_pilot.root
    path = root / pilot.REGISTER_NAME
    with open(path, encoding="utf-8", newline="") as handle:
        header = next(csv.reader(handle))
    assert tuple(header) == REGISTER_COLUMNS
    assert b"\r" not in path.read_bytes()
    assert file_sha256(path) == demo_pilot.finish.register_sha256
    assert read_register(path) == demo_pilot.finish.rows
    assert pilot.check_pilot(root) == ()  # register_problems(..., expected_set="demo")
    for row in read_register(path):
        bank_dir = root.joinpath(*row.bank_path.split("/"))
        assert read_manifest(bank_dir).bank_sha256() == row.bank_sha256
        assert BankLayout(bank_dir).bank_hash.read_text(encoding="utf-8").strip() == row.bank_sha256


def test_verify_log_and_reports(demo_pilot):
    text = (demo_pilot.root / "verify" / "verify-log.txt").read_text(encoding="utf-8")
    assert text.endswith("verified 9 banks: 9 OK, 0 FAILED\n")
    assert text.count("\nOK\n") == 9 and "PROBLEM" not in text
    assert "== runs/DEMO-pilot-banks/banks/DEMO-bank-P001 (DEMO-bank-P001 v1.0.0)" in text
    reports = sorted(p.name for p in (demo_pilot.root / "verify").glob("*.json"))
    assert len(reports) == 9 and "DEMO-bank-P006-v1.1.0.json" in reports
    for report in demo_pilot.finish.reports:
        if report.status == "complete":
            assert report.pairs_checked == {"P1": 1920, "P2": 1920, "P3": 1920}


def test_throughput_summary_records_slots_per_hour_and_attempts(demo_pilot):
    root = demo_pilot.root
    doc = read_json(root / pilot.SUMMARY_JSON_NAME)
    tp = doc["throughput"]
    rows = demo_pilot.finish.rows
    attempts = sum(r.attempts for r in rows)
    slots = sum(r.slots_used for r in rows)
    assert tp["attempts"] == attempts == 6 * 1 + 2 + 4 + 1
    assert tp["attempts_per_bank"]["mean"] == round(attempts / 9, 3)
    assert tp["attempts_histogram"] == {"1": 7, "2": 1, "3": 0, "4": 1}
    assert tp["slots"] == slots
    wall = 0
    for row in rows:
        layout = BankLayout(root.joinpath(*row.bank_path.split("/")))
        wall += sum(
            AttemptSummary.read(layout.attempt_summary(n)).wall_ms
            for n in range(1, row.attempts + 1)
        )
    assert tp["slots_per_hour"] == round(slots * 3_600_000 / wall, 1) > 0
    assert tp["slots_per_hour_run"] is not None and tp["slots_per_hour_run"] > 0
    assert tp["attempts_failed"] == 4 + 1
    assert tp["cells_failed"] == 4 + 1
    assert tp["projection"]["banks"] == 72
    assert doc["shortfall"] == [] and doc["usable_slots"] == 8 and doc["spares_used"] == 1
    assert doc["register_sha256"] == demo_pilot.finish.register_sha256
    note = (root / pilot.SUMMARY_MD_NAME).read_text(encoding="utf-8")
    assert "DEMO rehearsal" in note and "Slots per hour (bank builder)" in note
    assert "Attempts per bank" in note and "Every dyad slot has a usable bank (8 of 8)" in note


def test_archive_is_read_only_and_hashed(pilot_copy):
    with pytest.raises(pilot.PilotError, match="E_ROOT"):  # nothing is archived unchecked
        broken = pilot_copy / pilot.REGISTER_NAME
        original = broken.read_bytes()  # bytes: the summary names the register's hash
        broken.write_bytes(original.replace(b"pass", b"fail", 1))
        pilot.archive_pilot(pilot_copy, clock=ManualClock())
    broken.write_bytes(original)
    result = pilot.archive_pilot(pilot_copy, clock=ManualClock())
    manifest = read_json(pilot_copy / ARCHIVE_MANIFEST_NAME)
    assert manifest["archive_sha256"] == result.archive_sha256 == file_set_sha256(manifest["files"])
    assert (pilot_copy / ARCHIVE_HASH_NAME).read_text(
        encoding="utf-8"
    ) == result.archive_sha256 + "\n"
    files = manifest["files"]
    assert files[pilot.REGISTER_NAME] == file_sha256(pilot_copy / pilot.REGISTER_NAME)
    bank = f"runs/{SMALL_RUN}/banks/DEMO-bank-P002"
    failed_attempt = f"{bank}/attempts/4/slots.jsonl"
    assert failed_attempt in files and f"{bank}/timing.jsonl" in files
    assert f"runs/{SMALL_RUN}-S1/banks/DEMO-bank-P002/manifest.json" in files
    assert all(is_read_only(pilot_copy.joinpath(*name.split("/"))) for name in files)
    assert is_read_only(pilot_copy / ARCHIVE_MANIFEST_NAME)
    assert pilot.check_pilot(pilot_copy) == ()
    with pytest.raises(pilot.PilotError, match="E_ARCHIVED"):
        pilot.finish_pilot(pilot_copy)
    with pytest.raises(pilot.PilotError, match="E_ARCHIVED"):
        pilot.archive_pilot(pilot_copy, clock=ManualClock())
    # a change after archiving is reported
    target = pilot_copy.joinpath(*failed_attempt.split("/"))
    os.chmod(target, 0o644)
    target.write_bytes(target.read_bytes() + b"\n")
    problems = " | ".join(pilot.check_pilot(pilot_copy))
    assert "changed since archiving" in problems and "writable" in problems
    assert archive_problems(pilot_copy)


def _amend(bank_dir, cell, rank, date):
    return amend_bank(
        bank_dir,
        profile=cell.profile,
        atom_id=cell.atom_id,
        rank=rank,
        reason="DEMO: cached asset unusable before the wave menu",
        unheard_confirmed=True,
        date=date,
    )


def test_an_archived_bank_can_still_be_amended(pilot_copy):
    """Study B protocol §4 reserve rule: a pilot session may amend an archived bank. The
    archive keeps every other file frozen and pins the amendment lines it archived."""
    main = pilot_copy / "runs" / SMALL_RUN / "banks" / "DEMO-bank-P001"
    spare = pilot_copy / "runs" / f"{SMALL_RUN}-S1" / "banks" / "DEMO-bank-P002"
    cells = read_manifest(main).cells
    _amend(main, cells[2], 1, "2026-12-15")  # before the archive
    assert pilot.check_pilot(pilot_copy) == ()
    pilot.archive_pilot(pilot_copy, clock=ManualClock())
    log = BankLayout(main).amendments
    name = f"runs/{SMALL_RUN}/banks/DEMO-bank-P001/amendments.jsonl"
    archived = read_json(pilot_copy / ARCHIVE_MANIFEST_NAME)
    assert archived["append_only"] == {
        "patterns": list(pilot.AMENDMENT_LOGS),
        "bytes": {name: log.stat().st_size},
    }
    assert archived["files"][name] == file_sha256(log)
    assert not is_read_only(log) and is_read_only(main / "manifest.json")
    # the sessions amend after the archive: append to a log, and start a new one
    _amend(main, cells[20], 2, "2026-12-16")
    _amend(spare, read_manifest(spare).cells[5], 3, "2026-12-16")
    assert pilot.check_pilot(pilot_copy) == ()
    assert pilot.main(["check", "--root", str(pilot_copy)]) == 0
    # the archived lines cannot change, and every line chains to the archived bank hash
    lines = log.read_bytes().splitlines(keepends=True)
    log.write_bytes(lines[1])
    problems = pilot.check_pilot(pilot_copy)
    assert f"{name}: append-only log changed before its archived end" in problems
    assert f"{name}: amendment 1: prev_sha256 breaks the chain" in problems
    log.write_bytes(b"".join(lines))
    spare_log = BankLayout(spare).amendments
    with open(spare_log, "ab") as handle:
        handle.write(b'{"record": "bank_amendment"}\n')
    problems = " | ".join(pilot.check_pilot(pilot_copy))
    assert f"runs/{SMALL_RUN}-S1/banks/DEMO-bank-P002/amendments.jsonl: amendment 2:" in problems
    spare_log.write_bytes(spare_log.read_bytes().splitlines(keepends=True)[0])
    assert pilot.check_pilot(pilot_copy) == ()
    # any other new file is still reported, amendment-named or not
    (pilot_copy / "runs" / SMALL_RUN / "amendments.jsonl").write_bytes(b"")
    (main / "notes.txt").write_bytes(b"x")
    problems = pilot.check_pilot(pilot_copy)
    assert f"runs/{SMALL_RUN}/amendments.jsonl: not in the archive" in problems
    assert f"runs/{SMALL_RUN}/banks/DEMO-bank-P001/notes.txt: not in the archive" in problems
    os.remove(log)
    assert f"{name}: missing" in pilot.check_pilot(pilot_copy)
    log.write_bytes(b"[1]\n")
    assert any(
        p.startswith(f"{name}: ") and "every line is a JSON object" in p
        for p in pilot.check_pilot(pilot_copy)
    )


def test_a_changed_bank_breaks_the_register(pilot_copy, small_reports):
    row = read_register(pilot_copy / pilot.REGISTER_NAME)[0]
    layout = BankLayout(pilot_copy.joinpath(*row.bank_path.split("/")))
    manifest = read_manifest(layout.manifest)
    wav = layout.option(manifest.cells[3].options[1].wav)
    data = bytearray(wav.read_bytes())
    data[-2] ^= 0x01
    wav.write_bytes(bytes(data))
    problems = " | ".join(pilot.check_pilot(pilot_copy, rerun_verify=True))
    assert f"{row.bank_id} v1.0.0: register hash {row.bank_sha256} != recomputed" in problems
    assert "verify now fails" in problems
    others = {path: r for path, r in small_reports.items() if path != row.bank_path}
    finish = pilot.finish_pilot(pilot_copy, reports=others)
    assert finish.exit_code == 1 and not finish.verified
    assert finish.shortfall == ("B-P01",)  # a bank that fails verify is not used


def test_finish_reports_plan_mismatches(pilot_copy, small_reports):
    shutil.rmtree(pilot_copy / "runs" / SMALL_RUN / "banks" / "DEMO-bank-P001")
    plan = read_json(pilot_copy / pilot.PLAN_NAME)
    plan["spares"] = 0
    (pilot_copy / pilot.PLAN_NAME).write_text(json.dumps(plan), encoding="utf-8")
    crashed = pilot_copy / "runs" / f"{SMALL_RUN}-S1" / "banks" / "DEMO-bank-P002"
    os.remove(crashed / "manifest.json")
    finish = pilot.finish_pilot(pilot_copy, reports=small_reports)
    assert "DEMO-bank-P001 v1.0.0: planned but not built" in finish.problems
    assert any("no bank manifest (unfinished or crashed build)" in p for p in finish.problems)
    assert finish.shortfall == ("B-P01", "B-P02") and finish.exit_code == 1
    assert [(r.bank_id, r.bank_version) for r in finish.rows] == [("DEMO-bank-P002", "1.0.0")]


def test_finish_reports_banks_outside_the_plan(pilot_copy, small_reports):
    plan = read_json(pilot_copy / pilot.PLAN_NAME)
    plan.update(spares=0, demo=False)
    plan["banks"][1]["bank_id"] = "DEMO-bank-P009"
    (pilot_copy / pilot.PLAN_NAME).write_text(json.dumps(plan), encoding="utf-8")
    finish = pilot.finish_pilot(pilot_copy, reports=small_reports)
    assert "DEMO-bank-P009 v1.0.0: planned but not built" in finish.problems
    assert "2 spare banks built, the plan allows 0" in finish.problems
    assert "DEMO-bank-P002 v1.1.0: not a bank of the plan" in finish.problems
    assert "DEMO-bank-P001: a demo bank in a pilot pilot" in finish.problems
    assert finish.exit_code == 1


def _rewrite_register(root, change):
    path = root / pilot.REGISTER_NAME
    rows = [r for r in (change(row) for row in read_register(path)) if r is not None]
    write_register(rows, path)


def test_check_refuses_a_register_that_drops_rows(pilot_copy):
    _rewrite_register(pilot_copy, lambda r: None if r.bank_id == "DEMO-bank-P002" else r)
    problems = pilot.check_pilot(pilot_copy)
    for run in (SMALL_RUN, f"{SMALL_RUN}-S1"):
        assert f"runs/{run}/banks/DEMO-bank-P002: a built bank with no register row" in problems
    assert "DEMO-bank-P002 v1.0.0: planned but not in the register" in problems
    assert "throughput.json: shortfall [], the register gives ['B-P02']" in problems
    assert "throughput.json: register_sha256 is not the hash of pilot-register.csv" in problems
    with pytest.raises(pilot.PilotError, match="E_ROOT"):
        pilot.archive_pilot(pilot_copy, clock=ManualClock())
    assert not (pilot_copy / ARCHIVE_HASH_NAME).exists()


def test_check_recomputes_which_bank_each_slot_uses(pilot_copy):
    _rewrite_register(pilot_copy, lambda r: dataclasses.replace(r, use=False))
    problems = " | ".join(pilot.check_pilot(pilot_copy))
    for bank in ("DEMO-bank-P001 v1.0.0", "DEMO-bank-P002 v1.1.0"):
        assert f"{bank}: use 0, expected 1 (a dyad slot uses its first complete bank" in problems
    # hiding a bank behind a false `verify` is caught by the stored verify report
    _rewrite_register(
        pilot_copy,
        lambda r: (
            dataclasses.replace(r, verify="fail", use=False)
            if r.bank_id == "DEMO-bank-P001"
            else dataclasses.replace(r, use=r.status == "complete")
        ),
    )
    problems = " | ".join(pilot.check_pilot(pilot_copy))
    assert "use 0, expected 1" not in problems
    assert (
        "DEMO-bank-P001 v1.0.0: verify fail, but verify/DEMO-bank-P001-v1.0.0.json has ok True"
        in problems
    )
    os.remove(pilot_copy / "verify" / "DEMO-bank-P001-v1.0.0.json")
    assert "DEMO-bank-P001 v1.0.0: no verify report" in " | ".join(pilot.check_pilot(pilot_copy))
    with pytest.raises(pilot.PilotError, match="E_ROOT"):
        pilot.archive_pilot(pilot_copy, clock=ManualClock())


def test_check_compares_the_register_with_the_plan(pilot_copy, small_reports):
    config = read_register(pilot_copy / pilot.REGISTER_NAME)[0].generation_config_sha256
    plan_path = pilot_copy / pilot.PLAN_NAME
    plan = read_json(plan_path)
    plan["generation_config"]["sha256"] = "0" * 64
    plan["banks"][1]["dyad_slot"] = "B-P09"
    with open(plan_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(plan))
    _rewrite_register(
        pilot_copy, lambda r: dataclasses.replace(r, role="main") if r.role == "spare" else r
    )
    problems = " | ".join(pilot.check_pilot(pilot_copy))
    assert "DEMO-bank-P002 v1.1.0: role main, the plan makes it a spare bank" in problems
    for version in ("1.0.0", "1.1.0"):
        assert (
            f"DEMO-bank-P002 v{version}: dyad slot B-P02, the plan binds DEMO-bank-P002 to B-P09"
            in problems
        )
    zeros = "0" * 64
    assert (
        f"DEMO-bank-P001 v1.0.0: generation config {config}, the plan records {zeros}" in problems
    )
    assert problems.count(f", the plan records {zeros}") == 3
    assert any(
        f"the plan records {zeros}" in p
        for p in pilot.finish_pilot(pilot_copy, reports=small_reports).problems
    )
    os.remove(pilot_copy / pilot.SUMMARY_JSON_NAME)
    assert any(p.startswith("throughput.json: ") for p in pilot.check_pilot(pilot_copy))
    (pilot_copy / pilot.SUMMARY_JSON_NAME).write_bytes(b"[]\n")
    assert "throughput.json: not a JSON object" in pilot.check_pilot(pilot_copy)


def test_a_crashed_main_run_is_archived_as_its_record(pilot_copy, small_reports):
    bank = pilot_copy / "runs" / SMALL_RUN / "banks" / "DEMO-bank-P001"
    for name in ("manifest.json", "bank-sha256.txt"):  # the build never finished
        os.remove(bank / name)
    finish = pilot.finish_pilot(pilot_copy, reports=small_reports)
    assert "DEMO-bank-P001 v1.0.0: planned but not built" in finish.problems
    assert finish.shortfall == ("B-P01",)
    # while the main run is closed, a planned bank missing from the register is a problem
    assert "DEMO-bank-P001 v1.0.0: planned but not in the register" in pilot.check_pilot(pilot_copy)
    run_manifest = pilot_copy / "runs" / SMALL_RUN / "run-manifest.json"
    doc = read_json(run_manifest)
    doc.update(closed_utc=None, files=None)  # as a crash leaves it: never closed
    with open(run_manifest, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(doc, indent=2) + "\n")
    assert pilot.check_pilot(pilot_copy) == ()
    pilot.archive_pilot(pilot_copy, clock=ManualClock())
    assert pilot.check_pilot(pilot_copy) == ()
    # a run that does not exist did not crash; one whose manifest cannot be read did
    assert pilot._run_crashed(pilot_copy, "DEMO-no-such-run") is False
    (pilot_copy / "runs" / "DEMO-torn").mkdir()
    (pilot_copy / "runs" / "DEMO-torn" / "run-manifest.json").write_bytes(b"{")
    assert pilot._run_crashed(pilot_copy, "DEMO-torn") is True


def test_shortfall_is_reported_when_spares_run_out(kit, demo_units, tmp_path):
    order = kit.permutation.atom_order
    kind = failing_first_cell(order, {"DEMO-bank-P002", "DEMO-bank-P002-v1.1.0"})
    result = run_demo(
        kit, tmp_path / "short", demo_units, kind, dyads=2, spares=1, run_id="DEMO-short"
    )
    finish = result.finish
    assert [(r.bank_id, r.bank_version, r.status, r.use) for r in finish.rows] == [
        ("DEMO-bank-P001", "1.0.0", "complete", True),
        ("DEMO-bank-P002", "1.0.0", "unavailable", False),
        ("DEMO-bank-P002", "1.1.0", "unavailable", False),
    ]
    assert finish.shortfall == ("B-P02",) and finish.exit_code == 3
    note = (result.root / pilot.SUMMARY_MD_NAME).read_text(encoding="utf-8")
    assert "Shortfall: 1 dyad slot(s) without a usable bank (B-P02); raise it before O6.3.2" in note
    assert read_json(result.root / pilot.SUMMARY_JSON_NAME)["shortfall"] == ["B-P02"]
    with pytest.raises(pilot.PilotError, match="E_EXISTS"):
        run_demo(kit, tmp_path / "short", demo_units, kind, dyads=2, spares=1, run_id="DEMO-short")


def test_spares_are_not_used_without_need(kit, demo_units, tmp_path):
    result = run_demo(
        kit, tmp_path / "ok", demo_units, failing_first_cell(kit.permutation.atom_order, set()),
        dyads=2, spares=2, run_id="DEMO-ok",
    )  # fmt: skip
    assert len(result.runs) == 1 and [r.role for r in result.finish.rows] == ["main", "main"]
    assert result.finish.exit_code == 0


def fail_verify_after_build(monkeypatch, targets):
    """Banks `targets` (bank ID, version) fail `banks verify` right after their build: one
    option WAV of a complete bank gets a flipped bit, so its file hash no longer matches."""
    real = pilot.build_banks

    def build(specs, **kwargs):
        result = real(specs, **kwargs)
        for spec, bank in zip(specs, result.banks, strict=True):
            if (spec.bank_id, spec.bank_version) in targets and bank.status == "complete":
                wav = BankLayout(bank.bank_dir).option(bank.manifest.cells[0].options[0].wav)
                data = bytearray(wav.read_bytes())
                data[-2] ^= 0x01
                wav.write_bytes(bytes(data))
        return result

    monkeypatch.setattr(pilot, "build_banks", build)


def assert_only_the_broken_bank(root):
    """`check` finds the changed WAV of the main bank (its files no longer give its bank
    hash) and nothing else: the register, plan and summary agree."""
    problems = pilot.check_pilot(root)
    assert len(problems) == 2
    assert problems[0].startswith("DEMO-bank-P001 v1.0.0: register hash ")
    assert problems[1] == (
        "DEMO-bank-P001 v1.0.0: stored manifest or bank-sha256.txt differs from the files"
    )


def _rows(finish):
    return [(r.bank_id, r.bank_version, r.role, r.status, r.verify, r.use) for r in finish.rows]


def test_spares_replace_a_bank_that_fails_verify_and_an_unavailable_one(
    kit, demo_units, tmp_path, monkeypatch
):
    """Slot B-P01: the main bank completes but fails banks verify, its first spare is
    unavailable, its second spare (v1.2.0) is used. Slot B-P02: the main bank is
    unavailable and its spare is used. Spares are served in slot order."""
    fail_verify_after_build(monkeypatch, {("DEMO-bank-P001", "1.0.0")})
    kind = failing_first_cell(
        kit.permutation.atom_order, {"DEMO-bank-P001-v1.1.0", "DEMO-bank-P002"}
    )
    result = run_demo(
        kit, tmp_path / "spares", demo_units, kind, dyads=2, spares=3, run_id="DEMO-spares"
    )
    assert _rows(result.finish) == [
        ("DEMO-bank-P001", "1.0.0", "main", "complete", "fail", False),
        ("DEMO-bank-P001", "1.1.0", "spare", "unavailable", "pass", False),
        ("DEMO-bank-P001", "1.2.0", "spare", "complete", "pass", True),
        ("DEMO-bank-P002", "1.0.0", "main", "unavailable", "pass", False),
        ("DEMO-bank-P002", "1.1.0", "spare", "complete", "pass", True),
    ]
    assert [r.run_id for r in result.runs] == [
        "DEMO-spares",
        "DEMO-spares-S1",
        "DEMO-spares-S2",
        "DEMO-spares-S3",
    ]
    used = [(r.seed_namespace, r.run_id) for r in result.finish.rows if r.use]
    assert used == [
        ("DEMO-bank-P001-v1.2.0", "DEMO-spares-S2"),
        ("DEMO-bank-P002-v1.1.0", "DEMO-spares-S3"),
    ]
    assert result.finish.shortfall == ()
    assert result.finish.exit_code == 1  # a bank failed banks verify: look before archiving
    assert_only_the_broken_bank(result.root)


# -- confirmatory-mode refusal ---------------------------------------------------


@pytest.fixture(scope="module")
def real_pilot_bank(kit, tmp_path_factory):
    """One real-format pilot bank (`bank-P001`, non-DEMO config), outside any git tree."""
    base = tmp_path_factory.mktemp("real")
    units = write_units(kit, base / "units", demo=False, slots=1)
    plan = pilot.pilot_plan(units, run_id="P-banks-01", dyads=1, spares=0)
    real = kit.make_config("pilot-1", prompt_set=kit.prompt_set, llm="b" * 64)
    clock = ManualClock()
    model = DemoModel(kit, clock, lambda *key: "valid")
    result = pilot.run_pilot(
        plan, root=base / "pilot", config=real, proposer=model.factory, clock=clock,
        workers=1, ledger_factory=kit.Ledger, fsync=False,
    )  # fmt: skip
    assert result.finish.exit_code == 0
    return result


def test_a_confirmatory_mode_load_of_a_pilot_bank_is_refused(real_pilot_bank):
    bank_dir = real_pilot_bank.runs[0].banks[0].bank_dir
    manifest = open_bank(bank_dir, mode="pilot")
    assert manifest.bank_id == "bank-P001" and manifest.set == "pilot"
    with pytest.raises(
        BankSetError, match="'bank-P001' is a pilot bank; confirmatory mode refuses it"
    ):
        open_bank(bank_dir, mode="confirmatory")
    with pytest.raises(BankSetError, match="confirmatory mode refuses it"):
        check_bank_id_set("bank-P001", "confirmatory")
    # the issue's proposed form `PILOT-...` is no bank ID at all, so it is refused too
    with pytest.raises(BankSetError, match="not a bank ID: 'PILOT-B-01'"):
        check_bank_id_set("PILOT-B-01", "confirmatory")
    doc = manifest.to_dict()
    for bank_id in ("PILOT-B-01", "bank-P001"):
        with pytest.raises(BankSetError):
            require_bank_set(dict(doc, bank_id=bank_id), "confirmatory")
    check_bank_id_set("bank-C001", "confirmatory")
    row = read_register(real_pilot_bank.root / pilot.REGISTER_NAME)[0]
    assert (row.bank_id, row.set, row.seed_namespace) == ("bank-P001", "pilot", "bank-P001")
    problems = register_problems(
        real_pilot_bank.root / pilot.REGISTER_NAME,
        real_pilot_bank.root,
        expected_set="confirmatory",
    )
    assert any("confirmatory mode refuses it" in p for p in problems)


def test_cli_load_refuses_a_pilot_bank_in_confirmatory_mode(real_pilot_bank, capsys):
    bank_dir = str(real_pilot_bank.runs[0].banks[0].bank_dir)
    assert pilot.main(["load", bank_dir, "--mode", "confirmatory"]) == 2
    err = capsys.readouterr().err
    assert "refused" in err and "pilot bank; confirmatory mode refuses it" in err
    assert pilot.main(["load", bank_dir, "--mode", "pilot"]) == 0
    assert "bank-P001 v1.0.0 (pilot): complete" in capsys.readouterr().out


# -- command line ----------------------------------------------------------------


def test_cli_plan(demo_units, capsys):
    args = ["plan", "--units", str(demo_units), "--run-id", RUN_ID, "--demo"]
    assert pilot.main(args) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["set"] == "demo" and len(doc["banks"]) == 8
    assert doc["banks"][0]["seed_namespace"] == "DEMO-bank-P001"
    assert doc["spare_run_ids"] == [f"{RUN_ID}-S1", f"{RUN_ID}-S2"]
    assert doc["builder"]["source_sha256"]["av_banks"] == pilot.source_sha256(
        Path(av_banks.__file__).resolve().parent
    )
    assert pilot.main(["plan", "--units", str(demo_units), "--run-id", "P-banks-01"]) == 2
    assert "real units" in capsys.readouterr().err


def test_cli_run_check_finish_archive(kit, demo_units, tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "generation-config.json"
    kit.config.write(config_path)
    schema_path = tmp_path / "decoding-schema.json"
    schema_path.write_text(json.dumps(kit.decoding_schema), encoding="utf-8")
    order = kit.permutation.atom_order
    model = DemoModel(kit, ManualClock(), failing_first_cell(order, {"DEMO-bank-P002"}))
    monkeypatch.setattr(pilot, "make_proposer", lambda args, config: model.factory)
    real_run = pilot.run_pilot
    monkeypatch.setattr(
        pilot,
        "run_pilot",
        lambda plan, **kw: real_run(plan, ledger_factory=kit.Ledger, fsync=False, **kw),
    )
    root = tmp_path / "cli-root"
    args = [
        "run", "--units", str(demo_units), "--run-id", "DEMO-cli-pilot", "--demo",
        "--dyads", "2", "--spares", "0", "--root", str(root),
        "--generation-config", str(config_path),
        "--meanings", str(kit.root / "generation/examples/demo-meanings"),
        "--prompts", str(tmp_path / "prompts"), "--decoding-schema", str(schema_path),
        "--llm-url", "http://127.0.0.1:9", "--workers", "1",
    ]  # fmt: skip
    assert pilot.main(args) == 3  # DEMO-bank-P002 is unavailable and there is no spare
    out = json.loads(capsys.readouterr().out)
    assert out["shortfall"] == ["B-P02"] and out["usable_slots"] == 1 and out["verified"]
    assert out["attempts_per_bank"] == 2.5 and out["slots_per_hour"] > 0
    assert pilot.main(["check", "--root", str(root)]) == 0
    assert capsys.readouterr().out.strip() == "OK"
    assert pilot.main(["finish", "--root", str(root)]) == 3
    assert json.loads(capsys.readouterr().out)["register_sha256"] == out["register_sha256"]
    try:
        assert pilot.main(["archive", "--root", str(root)]) == 0
        archived = json.loads(capsys.readouterr().out)
        assert (root / ARCHIVE_HASH_NAME).read_text(encoding="utf-8").strip() == archived[
            "archive_sha256"
        ]
        assert pilot.main(["check", "--root", str(root), "--verify"]) == 0
        capsys.readouterr()
        assert pilot.main(["finish", "--root", str(root)]) == 2
        assert "E_ARCHIVED" in capsys.readouterr().err
        os.chmod(root / pilot.REGISTER_NAME, 0o644)
        with open(root / pilot.REGISTER_NAME, "a", encoding="utf-8", newline="") as handle:
            handle.write("x\n")
        assert pilot.main(["check", "--root", str(root)]) == 1
        assert "PROBLEM: register:" in capsys.readouterr().out
    finally:
        make_writable(root)
    assert pilot.main(["finish", "--root", str(tmp_path / "nowhere")]) == 2
    assert "pilot-plan.json" in capsys.readouterr().err


# -- evidence ---------------------------------------------------------------------


def _register_counts(path):
    keep = ("bank_id", "bank_version", "role", "dyad_slot", "set", "status", "use",
            "attempt_used", "attempts", "slots_used", "verify", "reason", "seed_namespace",
            "separation_threshold", "permutation_sha256", "run_id", "bank_path")  # fmt: skip
    with open(path, encoding="utf-8", newline="") as handle:
        return [{k: row[k] for k in keep} for row in csv.DictReader(handle)]


def test_demo_evidence(demo_pilot, tmp_path):
    """Writes the evidence files when `AV_BANKS_DEMO_PILOT_OUT` is set, and checks the
    committed copy against this rehearsal: every register column but the hashes that
    change with a shared format or the code pins (the permutation hashes depend only on
    the DEMO units, so they are compared too, on every OS)."""
    root = tmp_path / "evidence-root"
    shutil.copytree(demo_pilot.root, root)
    try:
        archived = pilot.archive_pilot(root, clock=ManualClock(start_ms=10_000_000))
        out = os.environ.get("AV_BANKS_DEMO_PILOT_OUT")
        # the evidence run re-runs banks verify on the archive; CI checks the hashes only
        assert pilot.check_pilot(root, rerun_verify=bool(out)) == ()
        if out:
            target = Path(out)
            target.mkdir(parents=True, exist_ok=True)
            for name in (pilot.PLAN_NAME, pilot.REGISTER_NAME, pilot.SUMMARY_JSON_NAME,
                         pilot.SUMMARY_MD_NAME, ARCHIVE_HASH_NAME):  # fmt: skip
                shutil.copyfile(root / name, target / name)
                os.chmod(target / name, 0o644)
            shutil.copyfile(root / "verify" / "verify-log.txt", target / "verify-log.txt")
            os.chmod(target / "verify-log.txt", 0o644)
            summary = {
                "archive_sha256": archived.archive_sha256,
                "archive_manifest_sha256": archived.manifest_sha256,
                "n_files": archived.n_files,
                "bytes": archived.bytes,
                "register_sha256": demo_pilot.finish.register_sha256,
                "check": "OK (register hashes equal the hashes recomputed from the files; "
                "archive intact and read-only; banks verify re-run)",
            }
            with open(target / "archive-summary.json", "w", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    finally:
        make_writable(root)
    assert _register_counts(EVIDENCE / pilot.REGISTER_NAME) == _register_counts(
        demo_pilot.root / pilot.REGISTER_NAME
    )
    committed = read_json(EVIDENCE / pilot.SUMMARY_JSON_NAME)
    assert committed["register_sha256"] == file_sha256(EVIDENCE / pilot.REGISTER_NAME)
    fresh = read_json(demo_pilot.root / pilot.SUMMARY_JSON_NAME)
    for key in ("attempts", "attempts_per_bank", "slots", "slots_per_hour", "cells_failed",
                "outcomes", "llm_status", "projection"):  # fmt: skip
        assert committed["throughput"][key] == fresh["throughput"][key], key
    log = (EVIDENCE / "verify-log.txt").read_text(encoding="utf-8")
    assert log.endswith("verified 9 banks: 9 OK, 0 FAILED\n")
    evidence = read_json(EVIDENCE / "archive-summary.json")
    assert (EVIDENCE / ARCHIVE_HASH_NAME).read_text(encoding="utf-8").strip() == evidence[
        "archive_sha256"
    ]
    for path in EVIDENCE.iterdir():
        text = path.read_text(encoding="utf-8")
        assert "/Users/" not in text and "\\Users\\" not in text and "/tmp/" not in text
