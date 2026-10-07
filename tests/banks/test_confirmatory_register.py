"""Confirmatory register (#28): verify-all, register, counts and escalation, timing log,
deterministic archive, publishing and the register-commit timestamp check.

The module campaign is a full 72-bank DEMO rehearsal in which 70 banks get unusable model
output (fast: 48 slots each), so 2 banks (one main, one spare) are complete and the
escalation rule applies."""

import csv
import io
import json
import os
import shutil
import subprocess
import tarfile

import pytest
from av_generation.clock import ManualClock
from av_generation.jsonio import file_sha256, read_json, write_document
from hypothesis import given, settings
from hypothesis import strategies as st

from av_banks.confirmatory import rehearsal as R
from av_banks.confirmatory.common import (
    CampaignError,
    CampaignLayout,
    campaign_bank_ids,
    schema_errors,
)
from av_banks.confirmatory.plan import create_plan, read_plan
from av_banks.confirmatory.register import (
    REGISTER_COLUMNS,
    TIMING_COLUMNS,
    archive_members,
    check_register_commit,
    compile_register,
    decide,
    publish_register,
    record_escalation,
    tally,
    verify_all,
    write_archive,
)
from av_banks.confirmatory.seed_check import read_pilot

COMPLETE = ("DEMO-C001", "DEMO-C065")
REF = "https://github.com/ATR-Lab/acoustic-vocabularies/issues/28#issuecomment-1"


@pytest.fixture(scope="module")
def rehearsal(tmp_path_factory, kit):
    out = tmp_path_factory.mktemp("rehearsal")
    failing = [b for b in campaign_bank_ids(demo=True) if b not in COMPLETE]
    return R.rehearse(
        out,
        campaign_id="DEMO-creg-01",
        unavailable=failing,
        parallel_banks=4,
        jobs=2,
        ledger_factory=kit.Ledger,
        clock=ManualClock(),
    )


@pytest.fixture
def copy(rehearsal, tmp_path):
    target = tmp_path / "campaign"
    shutil.copytree(rehearsal.root, target)
    return target


def _rows(path):
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_register_with_too_few_complete_banks(rehearsal):
    root = rehearsal.root
    layout = CampaignLayout.at(root)
    result = rehearsal.register
    assert result is not None and result.decision == "escalation_required"
    assert result.problems == ()
    # register.csv: 72 records in dyad-slot sequence
    text = layout.register_csv.read_text(encoding="utf-8")
    assert text.splitlines()[0] == ",".join(REGISTER_COLUMNS) and "\r" not in text
    rows = _rows(layout.register_csv)
    assert [r["bank_id"] for r in rows] == list(campaign_bank_ids(demo=True))
    assert [int(r["sequence"]) for r in rows] == list(range(1, 73))
    complete = [r for r in rows if r["status"] == "complete"]
    assert [r["bank_id"] for r in complete] == list(COMPLETE)
    for row in rows:
        bank_dir = layout.bank_dir(f"DEMO-creg-01-{row['bank_id']}", row["bank_id"])
        assert row["bank_sha256"] == (bank_dir / "bank-sha256.txt").read_text().strip()
        assert row["generation_config_sha256"] == read_plan(root).freeze.config_frozen_sha256
        assert row["config_matches_freeze"] == "1" and row["verify"] == "ok"
        assert int(row["attempts"]) <= 4 and int(row["max_slots_per_attempt"]) <= 576
        assert row["crashed_versions"] == "0" and row["bank_version"] == "1.0.0"
        if row["status"] == "complete":
            assert row["assignable"] == "1" and row["attempt_used"] == "1"
            assert int(row["slots_attempt_used"]) >= 192
        else:
            assert row["assignable"] == "0" and row["attempt_used"] == ""
            assert (row["attempts"], row["slots_total"], row["max_slots_per_attempt"]) == (
                "4",
                "48",
                "12",
            )
    # register.json
    doc = read_json(layout.register_json)
    assert schema_errors("confirmatory-register.schema.json", doc) == ()
    assert doc["counts"] == {
        "banks": 72,
        "complete": 2,
        "unavailable": 70,
        "main_complete": 1,
        "main_unavailable": 63,
        "spares_complete": 1,
        "spares_unavailable": 7,
    }
    assert all(doc["checks"].values())
    assert doc["escalation"]["required"] and doc["escalation"]["reference"] is None
    assert doc["escalation"]["complete"] == 2 and doc["escalation"]["needed"] == 64
    assert len(doc["unavailable_bank_ids"]) == 70 and "DEMO-C001" not in doc["unavailable_bank_ids"]
    hashes = doc["hashes"]
    assert (
        hashes["register_csv_sha256"]
        == file_sha256(layout.register_csv)
        == result.register_csv_sha256
    )
    assert hashes["plan_sha256"] == file_sha256(layout.plan)
    assert hashes["seed_check_sha256"] == file_sha256(layout.seed_check)
    assert hashes["used_seeds_sha256"] == file_sha256(layout.used_seeds)
    assert hashes["timing_csv_sha256"] == file_sha256(layout.timing)
    assert hashes["verification_sha256"] == file_sha256(layout.verification)
    assert hashes["verification_log_sha256"] == file_sha256(layout.verification_log)
    archive = layout.archive("DEMO-creg-01")
    assert hashes["archive_sha256"] == file_sha256(archive) == doc["archive"]["sha256"]
    assert doc["slots_total"] == sum(int(r["slots_total"]) for r in rows)
    assert file_sha256(layout.register_json) == result.register_json_sha256
    used = read_json(layout.used_seeds)
    assert used["ok"] and used["records"] == used["distinct_seeds"] == doc["slots_total"]
    report = layout.g5b_report.read_text(encoding="utf-8")
    assert "Decision: **escalation_required**" in report and "| All (72) | 2 | 70 |" in report
    # the public register holds hashes and counts, never seeds or paths
    for text in (layout.register_csv.read_text(), layout.register_json.read_text()):
        assert "DEMO-P0" not in text and "B|" not in text
        assert str(root) not in text and "\\" not in text


def test_verification_and_timing_logs(rehearsal):
    layout = CampaignLayout.at(rehearsal.root)
    log = layout.verification_log.read_text(encoding="utf-8").splitlines()
    assert log[0].startswith("# banks verify: campaign DEMO-creg-01")
    assert log[-1] == "# 72 of 72 banks verified; 72 OK; 0 failed"
    first = next(line for line in log if line.startswith("DEMO-C001 "))
    assert "complete OK" in first and "pairs=P1:1920,P2:1920,P3:1920 problems=0" in first
    assert rehearsal.verify.verified == 72 and rehearsal.verify.failed == ()
    timing = _rows(layout.timing)
    assert list(timing[0]) == list(TIMING_COLUMNS) and len(timing) == 73
    total = timing[-1]
    assert total["bank_id"] == "ALL"
    assert int(total["slots"]) == sum(int(r["slots"]) for r in timing[:-1])
    assert int(total["attempts"]) == 2 * 1 + 70 * 4
    c001 = timing[0]
    assert int(c001["latency_ms_p50"]) >= 150 and int(c001["latency_ms_max"]) <= 900
    assert int(c001["wall_ms"]) > 0 and float(c001["slots_per_minute"]) > 0
    assert c001["slots_over_cap"] == "0" and c001["llm_server_errors"] == "0"
    slots = _rows(layout.slot_timing)
    assert len(slots) == int(total["slots"]) and slots[0]["slot_id"].startswith("DEMO-C001.t1.")


def test_the_archive_is_deterministic(rehearsal, tmp_path):
    root = rehearsal.root
    one = write_archive(root, tmp_path / "a" / "x-banks.tar")
    two = write_archive(root, tmp_path / "b" / "x-banks.tar")
    assert one.sha256 == two.sha256 and one.files == two.files
    members = archive_members(root)
    assert "register.csv" in members and "plan.json" in members
    assert not any(
        m.startswith("archive/") or m in ("register.json", "g5b-report.md") for m in members
    )
    with tarfile.open(tmp_path / "a" / "x-banks.tar") as tar:
        infos = tar.getmembers()
        assert [i.name for i in infos] == members
        assert {(i.mtime, i.uid, i.gid, i.mode, i.uname) for i in infos} == {(0, 0, 0, 0o644, "")}
        data = tar.extractfile("register.csv").read()
    assert data == (root / "register.csv").read_bytes()


def test_escalation_is_recorded_then_published(copy, tmp_path):
    with pytest.raises(CampaignError, match="E_INPUT"):
        record_escalation(
            copy, reference="https://example.org/x", date="2027-04-27", clock=ManualClock()
        )
    with pytest.raises(CampaignError, match="YYYY-MM-DD"):
        record_escalation(copy, reference=REF, date="27/04/2027", clock=ManualClock())
    path = record_escalation(copy, reference=REF, date="2027-04-27", clock=ManualClock())
    assert read_json(path)["role"] == "advisor" and "name" not in read_json(path)
    with pytest.raises(FileExistsError):
        record_escalation(copy, reference=REF, date="2027-04-27", clock=ManualClock())
    result = compile_register(copy, clock=ManualClock())
    assert result.decision == "escalated"
    doc = read_json(CampaignLayout.at(copy).register_json)
    assert doc["escalation"]["reference"] == REF and doc["escalation"]["date"] == "2027-04-27"
    assert "escalation.json" in archive_members(copy)
    dest = tmp_path / "repo" / "banks" / "registers" / "DEMO-creg-01"
    paths = publish_register(copy, dest)
    assert [p.name for p in paths] == ["register.csv", "register.json"]
    assert (dest / "register.csv").read_bytes() == (copy / "register.csv").read_bytes()
    (copy / "register.csv").write_text("changed\n", encoding="utf-8")
    with pytest.raises(CampaignError, match="not the file register.json names"):
        publish_register(copy, dest)


def test_a_failed_verify_blocks_the_register(copy):
    layout = CampaignLayout.at(copy)
    report_path = layout.verify_report("DEMO-C001", "1.0.0")
    report = read_json(report_path)
    report.update(ok=False, problems=["P1 K-a1 option 2: waveform hash differs"])
    write_document(report_path, report)
    result = compile_register(copy, clock=ManualClock())
    assert result.decision == "blocked" and "DEMO-C001: banks verify failed" in result.problems
    row = _rows(layout.register_csv)[0]
    assert row["verify"] == "failed" and row["assignable"] == "0"
    assert not read_json(layout.register_json)["checks"]["complete_banks_verified"]
    with pytest.raises(CampaignError, match="blocked"):
        publish_register(copy, copy / "out")


def test_verify_all_finds_a_damaged_bank(copy):
    layout = CampaignLayout.at(copy)
    wav = layout.bank_dir("DEMO-creg-01-DEMO-C065", "DEMO-C065") / "options" / "P1"
    target = sorted(wav.iterdir())[0]
    data = bytearray(target.read_bytes())
    data[-1] ^= 0xFF
    target.write_bytes(bytes(data))
    result = verify_all(copy, jobs=2, only=["DEMO-C065", "DEMO-C001"])
    assert result.failed == ("DEMO-C065",) and result.verified == 72
    log = layout.verification_log.read_text(encoding="utf-8")
    assert "DEMO-C065 v1.0.0 complete FAILED" in log and "  PROBLEM: " in log
    assert log.splitlines()[-1].endswith("1 failed: DEMO-C065")
    assert compile_register(copy, clock=ManualClock()).decision == "blocked"


def test_compile_refusals(copy, tmp_path, rehearsal):
    layout = CampaignLayout.at(copy)
    layout.lock.write_text("x\n")
    with pytest.raises(CampaignError, match="runner.lock"):
        compile_register(copy, clock=ManualClock())
    layout.lock.unlink()
    report = layout.verify_report("DEMO-C003", "1.0.0")
    saved = report.read_bytes()
    doc = read_json(report)
    doc["manifest_sha256"] = "0" * 64
    write_document(report, doc)
    with pytest.raises(CampaignError, match="stale"):
        compile_register(copy, clock=ManualClock())
    doc = json.loads(saved)
    doc["bank_sha256"] = "0" * 64
    write_document(report, doc)
    with pytest.raises(CampaignError, match="another bank hash"):
        compile_register(copy, clock=ManualClock())
    report.unlink()
    with pytest.raises(CampaignError, match="no verify report"):
        compile_register(copy, clock=ManualClock())
    report.write_bytes(saved)
    # escalation refused when not needed
    register = read_json(layout.register_json)
    register["escalation"]["required"] = False
    write_document(layout.register_json, register)
    with pytest.raises(CampaignError, match="no escalation needed"):
        record_escalation(copy, reference=REF, date="2027-04-27", clock=ManualClock())
    # a campaign with pending banks
    fresh = tmp_path / "fresh"
    create_plan(
        fresh,
        campaign_id="DEMO-fresh-01",
        config=R.demo_config(),
        freeze_manifest=rehearsal.root.parent / "inputs" / "freeze-manifest.json",
        units=rehearsal.root.parent / "inputs" / "units",
        pilot=read_pilot(namespaces=R.DEMO_PILOT),
        clock=ManualClock(),
    )
    with pytest.raises(CampaignError, match="72 banks are not finished"):
        compile_register(fresh, clock=ManualClock())
    for call in (
        publish_register,
        lambda r, *a: record_escalation(r, reference=REF, date="2027-01-01", clock=ManualClock()),
    ):
        with pytest.raises(CampaignError, match="compile the register first"):
            call(fresh, tmp_path / "x")


@settings(max_examples=50, deadline=None)
@given(
    statuses=st.lists(st.sampled_from(["complete", "unavailable"]), min_size=72, max_size=72),
    checks_ok=st.booleans(),
    escalated=st.booleans(),
)
def test_property_counts_and_decision(statuses, checks_ok, escalated):
    rows = [{"role": "main" if i < 64 else "spare", "status": s} for i, s in enumerate(statuses)]
    counts = tally(rows)
    assert counts.complete + counts.unavailable == counts.banks == 72
    assert counts.main_complete + counts.spares_complete == counts.complete
    assert counts.main_unavailable + counts.spares_unavailable == counts.unavailable
    decision = decide(counts, checks_ok=checks_ok, escalated=escalated)
    if not checks_ok:
        assert decision == "blocked"
    elif counts.complete >= 64:
        assert decision == "ready"
    else:
        assert decision == ("escalated" if escalated else "escalation_required")
    # spares cover the unavailable main banks exactly when at least 64 banks are complete
    assert (counts.main_unavailable <= counts.spares_complete) == (counts.complete >= 64)


# -- the register commit and its timestamp ---------------------------------------------


def _git(repo, *args, date=None):
    env = dict(os.environ)
    if date is not None:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        check=True,
        capture_output=True,
        env=env,
    )


def test_register_commit_timestamp(copy, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    rel = "banks/registers/DEMO-creg-01"
    publish_register(copy, repo / rel)
    _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", "register", date="2027-04-27T10:00:00+00:00")
    expected = file_sha256(copy / "register.csv")
    ok = check_register_commit(
        repo,
        f"{rel}/register.csv",
        expected_sha256=expected,
        first_screening_utc="2027-05-03T09:00:00Z",
    )
    assert ok.ok and ok.committed_utc == "2027-04-27T10:00:00.000Z" and ok.changed_after == 0
    assert ok.url == f"https://github.com/ATR-Lab/acoustic-vocabularies/commit/{ok.commit}"
    assert ok.sha256 == expected and ok.to_dict()["problems"] == []
    late = check_register_commit(
        repo, f"{rel}/register.csv", first_screening_utc="2027-04-27T09:00:00Z"
    )
    assert not late.ok and "does not precede" in late.problems[0]
    other = check_register_commit(repo, f"{rel}\\register.csv", expected_sha256="0" * 64)
    assert not other.ok and "not the campaign's file" in other.problems[0]
    (repo / rel / "register.csv").write_text("edited\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "edit", date="2027-04-28T10:00:00+00:00")
    edited = check_register_commit(repo, f"{rel}/register.csv", expected_sha256=expected)
    assert edited.commit == ok.commit and edited.changed_after == 1 and not edited.ok
    missing = check_register_commit(repo, "banks/registers/none.csv")
    assert not missing.ok and missing.commit is None
    with pytest.raises(CampaignError, match="time zone"):
        check_register_commit(
            repo, f"{rel}/register.csv", first_screening_utc="2027-05-03T09:00:00"
        )
    with pytest.raises(CampaignError, match="E_COMMIT"):
        check_register_commit(tmp_path / "not-a-repo", "x.csv")
    _git(repo, "rm", "-q", f"{rel}/register.csv")
    _git(repo, "commit", "-q", "-m", "remove", date="2027-04-29T10:00:00+00:00")
    removed = check_register_commit(repo, f"{rel}/register.csv")
    assert removed.commit == ok.commit and removed.changed_after == 2


def test_register_rows_read_like_csv(rehearsal):
    text = (rehearsal.root / "register.csv").read_text(encoding="utf-8")
    rows = list(csv.reader(io.StringIO(text)))
    assert all(len(r) == len(REGISTER_COLUMNS) for r in rows)
    assert (
        json.loads((rehearsal.root / "register.json").read_text())["campaign_id"] == "DEMO-creg-01"
    )


def test_cli_register_escalate_publish_commit_check(copy, tmp_path, capsys):
    from av_banks.confirmatory import cli

    assert cli.main(["register", str(copy)]) == 3  # escalation required
    assert "Decision: **escalation_required**" in capsys.readouterr().out
    assert cli.main(["escalate", str(copy), "--reference", REF, "--date", "2027-04-27"]) == 0
    assert "escalation.json" in capsys.readouterr().out
    assert cli.main(["register", str(copy)]) == 0
    assert "Decision: **escalated**" in capsys.readouterr().out
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    rel = "banks/registers/DEMO-creg-01"
    assert cli.main(["publish", str(copy), "--dest", str(repo / rel)]) == 0
    assert "register.csv" in capsys.readouterr().out
    _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", "register", date="2027-04-27T10:00:00+00:00")
    args = [
        "commit-check",
        "--repo",
        str(repo),
        "--path",
        f"{rel}/register.json",
        "--campaign-root",
        str(copy),
    ]
    assert cli.main([*args, "--first-screening", "2027-05-03T09:00:00Z"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] and result["sha256"] == file_sha256(copy / "register.json")
    assert cli.main([*args, "--first-screening", "2027-04-01T09:00:00Z"]) == 1
    capsys.readouterr()
    # a blocked register exits 1
    report = CampaignLayout.at(copy).verify_report("DEMO-C001", "1.0.0")
    doc = read_json(report)
    doc["ok"] = False
    write_document(report, doc)
    assert cli.main(["register", str(copy)]) == 1
