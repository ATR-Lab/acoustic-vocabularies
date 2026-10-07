"""Pre-allocation orientation outcome in the operator console (#66 / #73).

All receipts, journals and screening IDs are synthetic temporary files.
"""
import json
import threading
import urllib.error
import urllib.request
from dataclasses import replace

import pytest

from av_schedules.admission import _receipt
from av_schedules.admission_io import canonical, sha
from av_schedules.masking import find_method_strings
from ops.console.core import Audit, Console, ConsoleFault, encoded, orientation_check
from ops.console.server import config_catalog, demo_catalog, make_server
from ops.console.transport import DemoEngine

VIEW_KEYS = {"status", "outcome", "engineering_draft", "receipt_sha256", "reason", "allocation"}
SCREENING = "demo-01"  # The demo fixture's coded participant.


def receipt_files(root, name, outcome="pass_first", draft=False, screening=SCREENING):
    first = [True] * 8 if outcome == "pass_first" else [False] + [True] * 7
    second = [] if outcome == "pass_first" else ([True] * 8 if outcome == "pass_second" else [True] * 7 + [False])
    receipt = {
        "schema_version": 1, "receipt_type": "orientation-outcome", "screening_id": screening,
        "station_id": "DEMO-station", "protocol_version": "DEMO-protocol", "orientation_id": "DEMO-orientation-" + name,
        "plan_sha256": "1" * 64, "demo_index_sha256": "2" * 64, "outcome": outcome,
        "engineering_draft": draft, "eligible": outcome != "fail" and not draft,
    }
    rows = [{"event": "orientation_header", "preallocation": True, "study_audio_loaded": False,
             **{k: receipt[k] for k in ("screening_id", "orientation_id", "protocol_version", "station_id",
                                        "plan_sha256", "demo_index_sha256")}}]
    rows += [{"event": "practice_response", "attempt": attempt, "ordinal": i + 1, "correct": value}
             for attempt, values in ((1, first), (2, second)) for i, value in enumerate(values)]
    rows.append({"event": "eligibility_outcome", "outcome": outcome, "engineering_draft": draft,
                 "preallocation": True, "learning_result": False, "first_correct": first,
                 "second_correct": second, "reexplanations": 0 if outcome == "pass_first" else 1})
    journal = root / (name + ".journal.jsonl")
    journal.write_bytes(b"".join(canonical(row) + b"\n" for row in rows))
    receipt.update(journal_sha256=sha(journal.read_bytes()), journal_bytes=journal.stat().st_size)
    path = root / (name + ".receipt.json")
    path.write_bytes(canonical(_receipt(receipt)))
    return dict(screening_id=screening, receipt_path=str(path), receipt_file_sha256=sha(path.read_bytes()),
                journal_path=str(journal))


@pytest.fixture
def private(tmp_path):
    root = tmp_path / ".local"
    root.mkdir()
    return root


def leaks(view, entry):
    text = encoded(view).decode()
    secrets = [entry["screening_id"], entry["receipt_path"], entry["journal_path"], "DEMO-orientation",
               "first_correct", "second_correct", "practice_response", "correct\""]
    return [s for s in secrets if s in text] + find_method_strings(text)


@pytest.mark.parametrize("outcome", ["pass_first", "pass_second"])
def test_verified_pass_is_eligible_for_handoff(private, outcome):
    entry = receipt_files(private, outcome, outcome)
    view = orientation_check(entry)
    assert set(view) == VIEW_KEYS
    assert view["status"] == "verified" and view["outcome"] == outcome and view["engineering_draft"] is False
    assert view["allocation"] == "eligible_for_handoff" and view["reason"] is None
    receipt = json.loads(open(entry["receipt_path"], "rb").read())
    assert view["receipt_sha256"] == receipt["receipt_sha256"]
    assert not leaks(view, entry)


@pytest.mark.parametrize("outcome,draft,reason", [
    ("fail", False, "orientation_failed"),
    ("pass_first", True, "orientation_engineering_draft"),
    ("fail", True, "orientation_engineering_draft"),
])
def test_recorded_fail_or_draft_is_visible_and_blocked(private, outcome, draft, reason):
    entry = receipt_files(private, "r", outcome, draft)
    view = orientation_check(entry)
    assert view["status"] == "verified" and view["outcome"] == outcome and view["engineering_draft"] is draft
    assert view["allocation"] == "blocked" and view["reason"] == reason
    assert not leaks(view, entry)


def test_missing_receipt_is_incomplete_and_blocked(private):
    entry = receipt_files(private, "r")
    (private / "r.receipt.json").unlink()
    view = orientation_check(entry)
    assert view == dict(status="incomplete", outcome=None, engineering_draft=None, receipt_sha256=None,
                        reason="orientation_receipt_missing", allocation="blocked")


@pytest.mark.parametrize("mutation,reason", [
    ("wrong_pin", "orientation_file_hash"),
    ("journal_changed", "orientation_journal_hash"),
    ("journal_missing", "file_missing"),
    ("receipt_hash", "orientation_receipt_hash"),
    ("false_pass", "orientation_outcome_invalid"),
    ("other_screening", "screening_receipt_mismatch"),
    ("fail_claims_pass", "orientation_journal_binding"),
])
def test_tampered_receipt_is_rejected_without_outcome(private, mutation, reason):
    entry = receipt_files(private, "r", "fail" if mutation == "fail_claims_pass" else "pass_second")
    receipt_path, journal = private / "r.receipt.json", private / "r.journal.jsonl"
    value = json.loads(receipt_path.read_bytes())
    if mutation == "wrong_pin":
        entry["receipt_file_sha256"] = "3" * 64
    elif mutation == "journal_changed":
        journal.write_bytes(journal.read_bytes() + b"{}\n")
    elif mutation == "journal_missing":
        journal.unlink()
    elif mutation == "other_screening":
        entry["screening_id"] = "demo-02"
    else:
        if mutation == "receipt_hash":
            value["outcome"] = "pass_first"  # Edited without resealing.
        else:
            value.pop("receipt_sha256")
            if mutation == "false_pass":
                value["eligible"] = False
            else:  # A resealed receipt claiming a pass over a recorded fail journal.
                value.update(outcome="pass_second", eligible=True)
            value = _receipt(value)
        receipt_path.write_bytes(canonical(value))
        entry["receipt_file_sha256"] = sha(receipt_path.read_bytes())
    view = orientation_check(entry)
    assert view == dict(status="rejected", outcome=None, engineering_draft=None, receipt_sha256=None,
                        reason=reason, allocation="blocked")


def test_config_entry_shape_is_closed(private):
    entry = receipt_files(private, "r")
    with pytest.raises(ConsoleFault, match="orientation_config_invalid"):
        orientation_check(dict(entry, extra="x"))
    with pytest.raises(ConsoleFault, match="hash_invalid"):
        orientation_check(dict(entry, receipt_file_sha256="X"))


@pytest.fixture
def screened(private, tmp_path):
    entries = {"scr-pass": receipt_files(private, "pass", "pass_second"),
               "scr-fail": receipt_files(private, "fail", "fail"),
               "scr-missing": dict(receipt_files(private, "gone"))}
    (private / "gone.receipt.json").unlink()
    base = demo_catalog()["demo-a"]()
    catalog = {"visit-pass": lambda: replace(base, screening="scr-pass"),
               "visit-fail": lambda: replace(base, screening="scr-fail"),
               "visit-unscreened": lambda: replace(base, screening="scr-unknown"),
               "visit-real-unbound": lambda: replace(base, demo=False)}
    audit = Audit(tmp_path / "audit.local.jsonl", "protocol-test")
    return Console(catalog, DemoEngine(), audit, screenings=entries), entries


def test_outcome_visible_and_logged_before_any_allocation(screened):
    console, entries = screened
    view = console.snapshot()
    assert not view["loaded"]
    assert [(r["screening"], r["status"], r["allocation"]) for r in view["orientation"]] == [
        ("scr-fail", "not_verified", "blocked"), ("scr-missing", "not_verified", "blocked"),
        ("scr-pass", "not_verified", "blocked")]
    for alias in entries:
        view = console.command("orientation", "ops-01", {"screening": alias})
    rows = {r["screening"]: r for r in view["orientation"]}
    assert rows["scr-pass"]["outcome"] == "pass_second" and rows["scr-pass"]["allocation"] == "eligible_for_handoff"
    assert rows["scr-fail"]["outcome"] == "fail" and rows["scr-fail"]["allocation"] == "blocked"
    assert rows["scr-missing"]["status"] == "incomplete"
    for entry in entries.values():
        assert not leaks(view, entry)
    logged = [r for r in console.audit.rows if r["event"] == "orientation_verified"]
    assert [(r["details"]["screening"], r["details"]["status"], r["details"]["outcome"]) for r in logged] == [
        ("scr-pass", "verified", "pass_second"), ("scr-fail", "verified", "fail"), ("scr-missing", "incomplete", None)]
    assert logged[0]["details"]["receipt_sha256"] == rows["scr-pass"]["receipt_sha256"]
    for entry in entries.values():
        assert not leaks(console.audit.rows, entry)
    with pytest.raises(ConsoleFault, match="screening_unknown"):
        console.command("orientation", "ops-01", {"screening": "scr-other"})
    with pytest.raises(ConsoleFault, match="request_invalid"):
        console.command("orientation", "ops-01", {"screening": "scr-pass", "answer": "x"})


def test_failed_missing_or_unbound_orientation_never_binds_allocation(screened):
    console, _ = screened
    for alias, fault in (("visit-fail", "orientation_blocked"), ("visit-unscreened", "orientation_binding_missing"),
                         ("visit-real-unbound", "orientation_binding_missing")):
        with pytest.raises(ConsoleFault, match=fault):
            console.load(alias, "ops-01")
        assert console.visit is None and console.engine.visit is None
    assert not [r for r in console.audit.rows if r["event"] == "load_requested"]
    rows = {r["screening"]: r for r in console.snapshot()["orientation"]}
    assert rows["scr-fail"]["status"] == "verified" and rows["scr-fail"]["allocation"] == "blocked"


def test_participant_must_match_screening(screened):
    console, entries = screened
    console.screenings["scr-pass"] = dict(entries["scr-pass"], screening_id="demo-02")
    with pytest.raises(ConsoleFault, match="orientation_participant_mismatch"):
        console.load("visit-pass", "ops-01")


def test_receipt_changed_after_load_blocks_start(screened, private):
    console, _ = screened
    console.load("visit-pass", "ops-01")
    console.command("checks", "ops-01", dict(comfort=True, phone=True))
    assert console.snapshot()["can_start"]
    journal = private / "pass.journal.jsonl"
    journal.write_bytes(journal.read_bytes().replace(b'"reexplanations":1', b'"reexplanations":2'))
    with pytest.raises(ConsoleFault, match="start_blocked"):
        console.command("start", "ops-01", {})
    view = console.snapshot()
    assert "orientation_unverified" in view["faults"] and not view["can_start"]
    assert console.engine.state == "awaiting_operator"
    row = [r for r in view["orientation"] if r["screening"] == "scr-pass"][0]
    assert row["status"] == "rejected" and row["reason"] == "orientation_journal_hash"
    console.command("stop", "ops-01", {})  # Stop remains available.


def test_audit_failure_never_leaves_a_displayed_pass(screened, monkeypatch):
    console, _ = screened
    console.command("orientation", "ops-01", {"screening": "scr-pass"})
    monkeypatch.setattr("ops.console.core.os.fsync", lambda _: (_ for _ in ()).throw(OSError("private")))
    with pytest.raises(ConsoleFault, match="audit_failed"):
        console.command("orientation", "ops-01", {"screening": "scr-pass"})
    row = [r for r in console.snapshot()["orientation"] if r["screening"] == "scr-pass"][0]
    assert row["status"] == "not_verified" and row["allocation"] == "blocked"


def test_private_config_requires_orientation_binding(private):
    entry = receipt_files(private, "r")
    visit = dict(allocation_list="x", reveal_log="y", screening="scr-01")
    catalog, screenings = config_catalog(dict(mailbox="m", visits={"v-01": visit}, screenings={"scr-01": entry}))
    assert set(catalog) == {"v-01"} and screenings == {"scr-01": entry}
    for config, fault in [
        (dict(mailbox="m", visits={"v-01": visit}), "orientation_binding_missing"),
        (dict(mailbox="m", visits={"v-01": dict(visit, screening="scr-02")}, screenings={"scr-01": entry}),
         "orientation_binding_missing"),
        (dict(mailbox="m", visits={}, screenings={"scr-01": dict(entry, extra=1)}), "orientation_config_invalid"),
        (dict(mailbox="m", visits={}, screenings={}, other={}), "config_invalid"),
    ]:
        with pytest.raises(ConsoleFault, match=fault):
            config_catalog(config)


def test_http_preallocation_view_is_sanitized_and_csrf_bound(screened):
    console, entries = screened
    server = make_server(console)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    origin = f"http://127.0.0.1:{server.server_port}"

    def request(path, body=None, token=None, source=None):
        headers = {"Content-Type": "application/json"} if body is not None else {}
        if source:
            headers["Origin"] = source
        if token:
            headers["X-Console-Token"] = token
        data = encoded(body) if body is not None else None
        return urllib.request.urlopen(urllib.request.Request(origin + path, data=data, headers=headers))
    try:
        with request("/") as response:
            page = response.read().decode()
        assert 'id="orientation"' in page and 'id="verify"' in page and "BEFORE ALLOCATION" in page
        with request("/app.js") as response:
            assert "renderOrientation" in response.read().decode()
        with request("/api/state") as response:
            state = json.load(response)
        body = dict(action="orientation", staff="ops-01", payload=dict(screening="scr-fail"))
        with pytest.raises(urllib.error.HTTPError) as error:
            request("/api/command", body, "wrong", origin)
        assert error.value.code == 409
        assert not [r for r in console.audit.rows if r["event"] == "orientation_verified"]
        with request("/api/command", body, state["token"], origin) as response:
            state = json.load(response)
        row = [r for r in state["orientation"] if r["screening"] == "scr-fail"][0]
        assert row["status"] == "verified" and row["outcome"] == "fail" and row["allocation"] == "blocked"
        load = dict(action="load", staff="ops-01", payload=dict(visit="visit-fail"))
        with pytest.raises(urllib.error.HTTPError) as error:
            request("/api/command", load, state["token"], origin)
        assert json.load(error.value) == {"error": "orientation_blocked"}
        with request("/api/state") as response:
            raw = response.read()
        for entry in entries.values():
            assert not leaks(json.loads(raw), entry)
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
