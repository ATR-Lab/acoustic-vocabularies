"""Actual temporary files/processes; all lists, screening IDs and evidence are synthetic."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from av_schedules.admission import DurableRevealLog, _receipt, inspect_orientation, read_orientation
from av_schedules.admission_cli import main
from av_schedules.admission_io import WriterLock, canonical, sha
from av_schedules.reveal import A_CHECKS, B_CHECKS, GENESIS, RevealError

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def private(tmp_path):
    root = tmp_path / ".local"
    root.mkdir()
    return root


def make_list(private, study="A"):
    name = "pilot-slots.json" if study == "A" else "pilot-dyads.json"
    data = (ROOT / "schedules/examples/demo-allocation" / study / name).read_bytes()
    path = private / (study + "-list.json")
    path.write_bytes(data)
    return path, sha(data)


def orientation(private, person="DEMO-person", second=False, draft=False, fail=False):
    second = second or fail
    receipt = {
        "schema_version": 1,
        "receipt_type": "orientation-outcome",
        "screening_id": person,
        "station_id": "DEMO-station",
        "protocol_version": "DEMO-protocol",
        "orientation_id": "DEMO-" + person,
        "plan_sha256": "1" * 64,
        "demo_index_sha256": "2" * 64,
        "outcome": "fail" if fail else "pass_second" if second else "pass_first",
        "engineering_draft": draft,
        "eligible": not draft and not fail,
    }
    retry = [True] * 7 + [False] if fail else [True] * 8
    header = {
        "event": "orientation_header",
        "preallocation": True,
        "study_audio_loaded": False,
        **{
            k: receipt[k]
            for k in (
                "screening_id",
                "orientation_id",
                "protocol_version",
                "station_id",
                "plan_sha256",
                "demo_index_sha256",
            )
        },
    }
    first = [False] + [True] * 7 if second else [True] * 8
    answers = [(1, first)] + ([(2, retry)] if second else [])
    rows = [header] + [
        {"event": "practice_response", "attempt": a, "ordinal": i + 1, "correct": answer}
        for a, values in answers
        for i, answer in enumerate(values)
    ]
    rows.append(
        {
            "event": "eligibility_outcome",
            "outcome": receipt["outcome"],
            "engineering_draft": draft,
            "preallocation": True,
            "learning_result": False,
            "first_correct": first,
            "second_correct": retry if second else [],
            "reexplanations": 1 if second else 0,
        }
    )
    journal = private / (person + ".jsonl")
    journal.write_bytes(b"".join(canonical(row) + b"\n" for row in rows))
    receipt.update(journal_sha256=sha(journal.read_bytes()), journal_bytes=journal.stat().st_size)
    receipt = _receipt(receipt)
    path = private / (person + ".json")
    path.write_bytes(canonical(receipt))
    return path, sha(path.read_bytes()), journal


def open_log(private, study="A", head=GENESIS, recover=False):
    path, pin = make_list(private, study)
    return DurableRevealLog(
        path, pin, private / (study + ".admission.jsonl"), expected_head=head, recover_tail=recover
    )


def eligible(log, private, people=("DEMO-person",)):
    checks = A_CHECKS if log.doc["study"] == "A" else B_CHECKS
    return log.log_eligibility(
        people,
        staff="DEMO-operator",
        checks=dict.fromkeys(checks, True),
        orientation_files=[orientation(private, p) for p in people],
    )


def reveal(log, receipt):
    return log.reveal_next(
        receipt["eligibility_id"],
        eligibility_receipt_sha256=receipt["receipt_sha256"],
        staff="DEMO-operator",
    )


@pytest.mark.parametrize("study,people", [("A", ("DEMO-a",)), ("B", ("DEMO-b1", "DEMO-b2"))])
def test_real_lists_pinned_receipts_exact_entry_and_reopen(private, study, people):
    log = open_log(private, study)
    e = eligible(log, private, people)
    r = reveal(log, e)
    assert r["screening_ids"] == list(people)
    assert r["eligibility_receipt_sha256"] == e["receipt_sha256"]
    assert r["entry_sha256"] == sha(canonical(r["entry"]))
    assert r == _receipt({k: v for k, v in r.items() if k != "receipt_sha256"})
    reopened = open_log(private, study, log.head)
    assert reveal(reopened, e) == r
    assert reopened.head == log.head and len(reopened.rows) == 2
    assert reopened.eligibility_receipt(e["eligibility_id"]) == e
    r["entry"]["unit_id"] = "mutated-copy"
    assert reveal(reopened, e)["entry"]["unit_id"] != "mutated-copy"


def test_duplicate_eligibility_returns_same_receipt_and_rejects_new_evidence(private):
    log = open_log(private)
    e = eligible(log, private)
    assert eligible(log, private) == e and len(log.rows) == 1
    files = orientation(private, second=True)
    with pytest.raises(RevealError, match="RETRY_MISMATCH"):
        log.log_eligibility(
            ["DEMO-person"],
            staff="DEMO-operator",
            checks=dict.fromkeys(A_CHECKS, True),
            orientation_files=[files],
        )


@pytest.mark.parametrize("second", [False, True])
def test_orientation_actual_terminal_counts(private, second):
    files = orientation(private, second=second)
    assert read_orientation(*files)["outcome"] == ("pass_second" if second else "pass_first")


@pytest.mark.parametrize(
    "mutation",
    [
        "draft",
        "wrong_pin",
        "journal_changed",
        "false_pass",
        "different_screening",
        "unknown_key",
        "duplicate_key",
    ],
)
def test_orientation_integrity_and_admission_refusal(private, mutation):
    log = open_log(private)
    path, pin, journal = orientation(private, draft=mutation == "draft")
    if mutation == "wrong_pin":
        pin = "3" * 64
    elif mutation == "journal_changed":
        journal.write_bytes(journal.read_bytes() + b" ")
    elif mutation in {"false_pass", "different_screening", "unknown_key"}:
        value = json.loads(path.read_bytes())
        if mutation == "false_pass":
            value["eligible"] = False
        elif mutation == "different_screening":
            value["screening_id"] = "OTHER"
        else:
            value["unknown"] = 1
        value.pop("receipt_sha256")
        path.write_bytes(canonical(_receipt(value)))
        pin = sha(path.read_bytes())
    elif mutation == "duplicate_key":
        path.write_bytes(
            path.read_bytes().replace(b'"eligible":true', b'"eligible":false,"eligible":true')
        )
        pin = sha(path.read_bytes())
    with pytest.raises(RevealError):
        log.log_eligibility(
            ["DEMO-person"],
            staff="DEMO-operator",
            checks=dict.fromkeys(A_CHECKS, True),
            orientation_files=[(path, pin, journal)],
        )
    assert not log.journal_path.exists() and log.head == GENESIS


@pytest.mark.parametrize("fail,draft", [(True, False), (False, True), (True, True)])
def test_display_inspection_verifies_recorded_fail_or_draft_but_never_admits(private, fail, draft):
    files = orientation(private, fail=fail, draft=draft)
    receipt = inspect_orientation(*files)
    assert receipt["outcome"] == ("fail" if fail else "pass_first")
    assert receipt["eligible"] is False and receipt["engineering_draft"] is draft
    with pytest.raises(RevealError, match="ORIENTATION_NOT_ELIGIBLE"):
        read_orientation(*files)


@pytest.mark.parametrize(
    "mutation", ["fail_flagged_eligible", "fail_with_full_retry", "draft_unflagged"]
)
def test_display_inspection_refuses_inconsistent_outcomes(private, mutation):
    draft = mutation == "draft_unflagged"
    path, _, journal = orientation(private, fail=not draft, draft=draft)
    value = json.loads(path.read_bytes())
    value.pop("receipt_sha256")
    if mutation == "fail_flagged_eligible":
        value["eligible"] = True
        code = "ORIENTATION_OUTCOME_INVALID"
    elif mutation == "fail_with_full_retry":
        retry = b'"second_correct":[' + b",".join([b"true"] * 7)
        journal.write_bytes(journal.read_bytes().replace(retry + b",false]", retry + b",true]"))
        value.update(journal_sha256=sha(journal.read_bytes()), journal_bytes=journal.stat().st_size)
        code = "ORIENTATION_CHECK_COUNTS"
    else:
        value.update(engineering_draft=False, eligible=True)
        code = "ORIENTATION_JOURNAL_BINDING"
    path.write_bytes(canonical(_receipt(value)))
    with pytest.raises(RevealError, match=code):
        inspect_orientation(path, sha(path.read_bytes()), journal)


def test_receipt_cannot_claim_missing_practice_response(private):
    path, _, journal = orientation(private)
    rows = journal.read_bytes().splitlines(keepends=True)
    del rows[3]
    journal.write_bytes(b"".join(rows))
    value = json.loads(path.read_bytes())
    value.pop("receipt_sha256")
    value.update(journal_sha256=sha(journal.read_bytes()), journal_bytes=journal.stat().st_size)
    path.write_bytes(canonical(_receipt(value)))
    with pytest.raises(RevealError, match="ORIENTATION_RESPONSE_EVIDENCE"):
        read_orientation(path, sha(path.read_bytes()), journal)


def test_recorded_checks_are_exact_and_b_member_order_bound(private):
    log = open_log(private, "B")
    files = [orientation(private, "DEMO-b2"), orientation(private, "DEMO-b1")]
    with pytest.raises(RevealError, match="SCREENING_RECEIPT_MISMATCH"):
        log.log_eligibility(
            ["DEMO-b1", "DEMO-b2"],
            staff="DEMO-operator",
            checks=dict.fromkeys(B_CHECKS, True),
            orientation_files=files,
        )
    with pytest.raises(RevealError, match="ELIGIBILITY_BINDING_INVALID"):
        log.log_eligibility(
            ["DEMO-b2", "DEMO-b1"],
            staff="DEMO-operator",
            checks={**dict.fromkeys(B_CHECKS, True), "extra": True},
            orientation_files=files,
        )


def test_missing_or_wrong_eligibility_pin_does_not_allocate(private):
    log = open_log(private)
    e = eligible(log, private)
    with pytest.raises(RevealError, match="ELIGIBILITY_RECEIPT_MISMATCH"):
        log.reveal_next(
            e["eligibility_id"], eligibility_receipt_sha256="4" * 64, staff="DEMO-operator"
        )
    assert len(log.rows) == 1


def test_crash_after_durable_journal_before_checkpoint_requires_explicit_recovery(
    private, monkeypatch
):
    log = open_log(private)
    e = eligible(log, private)
    old = log.head

    def crash(_):
        raise OSError("injected checkpoint loss")

    monkeypatch.setattr(log, "_write_checkpoint", crash)
    with pytest.raises(OSError):
        reveal(log, e)
    with pytest.raises(RevealError, match="REOPEN_REQUIRED"):
        reveal(log, e)
    with pytest.raises(RevealError, match="RECOVERY_REQUIRED"):
        open_log(private, head=old)
    recovered = open_log(private, head=old, recover=True)
    r = reveal(recovered, e)
    assert r["journal_line"] == 2 and len(recovered.rows) == 2
    assert reveal(recovered, e) == r


def test_failed_journal_sync_never_returns_receipt(private, monkeypatch):
    log = open_log(private)
    e = eligible(log, private)
    old = log.head
    original = os.fsync

    def bad(fd):
        raise OSError("injected fsync failure")

    monkeypatch.setattr(os, "fsync", bad)
    with pytest.raises(OSError):
        reveal(log, e)
    assert log.faulted
    monkeypatch.setattr(os, "fsync", original)
    recovered = open_log(private, head=old, recover=True)
    assert reveal(recovered, e)["journal_line"] == 2


@pytest.mark.parametrize(
    "mutation",
    [
        "partial",
        "truncate",
        "joint_rollback",
        "checkpoint_missing",
        "checkpoint_corrupt",
        "changed_list",
    ],
)
def test_tamper_rollback_and_partial_journal_fail_closed(private, mutation):
    log = open_log(private)
    e = eligible(log, private)
    previous_journal = log.journal_path.read_bytes()
    previous_checkpoint = log.checkpoint_path.read_bytes()
    reveal(log, e)
    latest = log.head
    if mutation == "partial":
        log.journal_path.write_bytes(log.journal_path.read_bytes()[:-1])
    elif mutation == "truncate":
        log.journal_path.write_bytes(previous_journal)
    elif mutation == "joint_rollback":
        log.journal_path.write_bytes(previous_journal)
        log.checkpoint_path.write_bytes(previous_checkpoint)
    elif mutation == "checkpoint_missing":
        log.checkpoint_path.unlink()
    elif mutation == "checkpoint_corrupt":
        log.checkpoint_path.write_bytes(b"{}")
    elif mutation == "changed_list":
        log.list_path.write_bytes(log.list_path.read_bytes() + b" ")
    with pytest.raises(RevealError):
        DurableRevealLog(
            log.list_path, log.list_file_sha256, log.journal_path, expected_head=latest
        )


def test_stale_in_memory_owner_reloads_and_allocates_distinct_next_slot(private):
    first = open_log(private)
    second = open_log(private)
    a = eligible(first, private, ("DEMO-a",))
    ra = reveal(first, a)
    b = eligible(second, private, ("DEMO-b",))
    rb = reveal(second, b)
    assert ra["entry"]["slot_id"] != rb["entry"]["slot_id"]
    assert rb["journal_line"] == 4


def test_os_lock_blocks_a_different_process_without_partial_write(private):
    log = open_log(private)
    script = (
        "from pathlib import Path;from av_schedules.admission_io import WriterLock;\n"
        "with WriterLock(Path(__import__('sys').argv[1])): print('acquired')"
    )
    with WriterLock(log.lock_path):
        result = subprocess.run(
            [sys.executable, "-c", script, str(log.lock_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    assert result.returncode != 0 and "WRITER_BUSY" in result.stderr
    assert not log.journal_path.exists()
    with WriterLock(log.lock_path):
        pass


def test_spare_bank_mapping_retains_same_sq_swap_and_cannot_change_after_reveal(private):
    path = private / "B-list.json"
    path.write_bytes(
        (ROOT / "schedules/examples/demo-allocation/B/confirmatory-dyads.json").read_bytes()
    )
    log = DurableRevealLog(
        path, sha(path.read_bytes()), private / "B.admission.jsonl", expected_head=GENESIS
    )
    bank = log.policy._entries[0]["bank_id"]
    log.log_bank_unavailable(bank, staff="DEMO-operator")
    e = eligible(log, private, ("DEMO-b1", "DEMO-b2"))
    r = reveal(log, e)
    assert r["entry"]["bank_id"] != bank and r["entry"]["replaces"]
    with pytest.raises(RevealError, match="before the first reveal"):
        log.log_bank_unavailable(bank, staff="DEMO-operator")


def test_local_links_and_public_journal_refused(private):
    path, pin = make_list(private)
    with pytest.raises(RevealError, match="PRIVATE_JOURNAL_REQUIRED"):
        DurableRevealLog(path, pin, private.parent / "public.jsonl", expected_head=GENESIS)
    other = private / "link.json"
    os.link(path, other)
    with pytest.raises(RevealError, match="REGULAR_UNLINKED_FILE_REQUIRED"):
        DurableRevealLog(path, pin, private / "private.jsonl", expected_head=GENESIS)


def test_process_death_after_append_recovers_without_second_slot(private):
    log = open_log(private)
    e = eligible(log, private)
    old = log.head
    code = """
import os,sys
from pathlib import Path
from av_schedules.admission import DurableRevealLog
log=DurableRevealLog(Path(sys.argv[1]),sys.argv[2],Path(sys.argv[3]),expected_head=sys.argv[4])
log._write_checkpoint=lambda value:os._exit(73)
log.reveal_next(sys.argv[5],eligibility_receipt_sha256=sys.argv[6],staff='DEMO-operator')
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(log.list_path),
            log.list_file_sha256,
            str(log.journal_path),
            old,
            e["eligibility_id"],
            e["receipt_sha256"],
        ],
        timeout=10,
    )
    assert result.returncode == 73
    with pytest.raises(RevealError, match="RECOVERY_REQUIRED"):
        open_log(private, head=old)
    recovered = open_log(private, head=old, recover=True)
    assert reveal(recovered, e)["journal_line"] == 2
    assert len(recovered.rows) == 2


def test_cli_private_eligibility_reveal_receipt_and_collision(private, capsys):
    list_path, list_pin = make_list(private)
    receipt, pin, journal = orientation(private)
    request = {
        "schema_version": 1,
        "operation": "eligibility",
        "screening_ids": ["DEMO-person"],
        "staff": "DEMO-operator",
        "checks": dict.fromkeys(A_CHECKS, True),
        "orientation_files": [
            {"receipt_path": str(receipt), "receipt_file_sha256": pin, "journal_path": str(journal)}
        ],
    }
    request_path = private / "request.json"
    request_path.write_bytes(canonical(request))
    output = private / "eligibility.json"
    args = [
        "--allocation-list",
        str(list_path),
        "--list-file-sha256",
        list_pin,
        "--journal",
        str(private / "cli.jsonl"),
        "--expected-head",
        GENESIS,
        "--request",
        str(request_path),
        "--request-sha256",
        sha(request_path.read_bytes()),
        "--output",
        str(output),
    ]
    assert main(args) == 0
    report = json.loads(capsys.readouterr().out)
    e = json.loads(output.read_bytes())
    assert report["receipt_file_sha256"] == sha(output.read_bytes())
    before = (private / "cli.jsonl").read_bytes()
    assert main(args) == 2
    assert (private / "cli.jsonl").read_bytes() == before
    request = {
        "schema_version": 1,
        "operation": "reveal",
        "eligibility_id": e["eligibility_id"],
        "eligibility_receipt_sha256": e["receipt_sha256"],
        "staff": "DEMO-operator",
    }
    request_path.write_bytes(canonical(request))
    args[args.index("--expected-head") + 1] = report["current_journal_head_sha256"]
    args[args.index("--request-sha256") + 1] = sha(request_path.read_bytes())
    args[-1] = str(private / "reveal.json")
    assert main(args) == 0
    result = json.loads((private / "reveal.json").read_bytes())
    assert result["entry"]["participant_id"] == "DEMO-person"
    args[args.index("--request-sha256") + 1] = "0" * 64
    args[-1] = str(private / "bad.json")
    assert main(args) == 2 and not (private / "bad.json").exists()


@pytest.mark.parametrize("study,people", [("A", ("DEMO-a",)), ("B", ("DEMO-b1", "DEMO-b2"))])
def test_closed_receipt_schema_matches_actual_apis(private, study, people):
    from jsonschema import Draft202012Validator

    validator = Draft202012Validator(
        json.loads((ROOT / "schedules/schema/admission-receipt.schema.json").read_text())
    )
    log = open_log(private, study)
    e = eligible(log, private, people)
    r = reveal(log, e)
    for value in [e, r, *[json.loads(orientation(private, p)[0].read_bytes()) for p in people]]:
        validator.validate(value)
        changed = dict(value, extra="refused")
        assert list(validator.iter_errors(changed))


def test_two_processes_cannot_issue_two_entries_for_one_eligibility(private):
    log = open_log(private)
    e = eligible(log, private)
    old = log.head
    code = """
import json,sys
from pathlib import Path
from av_schedules.admission import DurableRevealLog
try:
 log=DurableRevealLog(Path(sys.argv[1]),sys.argv[2],Path(sys.argv[3]),expected_head=sys.argv[4])
 receipt=log.reveal_next(
     sys.argv[5],eligibility_receipt_sha256=sys.argv[6],staff='DEMO-operator')
 print(json.dumps(receipt))
except Exception as error:
 print(type(error).__name__);sys.exit(2)
"""
    args = [
        sys.executable,
        "-c",
        code,
        str(log.list_path),
        log.list_file_sha256,
        str(log.journal_path),
        old,
        e["eligibility_id"],
        e["receipt_sha256"],
    ]
    children = [
        subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(2)
    ]
    results = [child.communicate(timeout=10) for child in children]
    successes = [
        json.loads(out)
        for child, (out, _) in zip(children, results, strict=True)
        if child.returncode == 0
    ]
    assert successes
    assert all(value == successes[0] for value in successes)
    reopened = open_log(private, head=old)
    assert len(reopened.rows) == 2 and reveal(reopened, e) == successes[0]
