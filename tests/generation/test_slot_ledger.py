"""Shared slot ledger (#17): one record per slot, hard caps, refusals, reopening, and one
fixture per outcome code (each consumes exactly one slot, at most one model call)."""

import dataclasses
import random
import shutil
import tempfile
import threading
from collections import Counter
from pathlib import Path

import pytest
from av_sound.recipe import Profile
from hypothesis import given, settings
from hypothesis import strategies as st

from av_generation import _demo_ledger as demo
from av_generation.clock import ManualClock
from av_generation.ids import (
    IdError,
    Method,
    Study,
    bank_slot_id,
    parse_bank_slot_id,
    parse_proposal_slot_id,
    proposal_slot_id,
)
from av_generation.jsonio import canonical_line, iter_jsonl
from av_generation.ledger import (
    AttemptCapExceeded,
    LedgerError,
    SlotCapExceeded,
    SlotLedger,
    SlotNotReserved,
    SlotReused,
    SlotTicket,
)
from av_generation.llm import TokenCountError
from av_generation.llm_fake import ScriptedLlmClient
from av_generation.outcomes import (
    OUTCOME_CODES,
    LlmStatus,
    SlotOutcome,
    outcome_from_validator_codes,
)
from av_generation.records import (
    A2Detail,
    RecordError,
    RecordWriter,
    SlotRecord,
    SlotRefusal,
    TimingEvent,
    cap_key,
    read_records,
)

ROOT = Path(__file__).resolve().parents[2]
RUN = "DEMO-run-01"
BOOK = "DEMO-BK-H9TC"
OTHER_BOOK = "DEMO-BK-7QX4"
BANK = "DEMO-bank-01"
ATOM = "K-a1"
A_KEY = cap_key(Study.A, ATOM, book_id=BOOK)
SLOTS_A = [proposal_slot_id(BOOK, ATOM, r, s) for r in (1, 2, 3, 4) for s in (1, 2, 3)]


def b_key(attempt=1, profile=Profile.P1, atom=ATOM):
    return cap_key(Study.B, atom, bank_id=BANK, attempt=attempt, profile=profile)


def b_slot(slot, attempt=1, profile=Profile.P1, atom=ATOM):
    return bank_slot_id(BANK, attempt, profile, atom, slot)


def make_ledger(path, clock=None, **kwargs):
    return SlotLedger(path / "slots.jsonl", run_id=RUN, clock=clock or ManualClock(0), **kwargs)


def record_for(ticket: SlotTicket, *, outcome=SlotOutcome.TIMEOUT, t_ms=None, **over):
    """A minimal valid record for an open ticket (timeout: nothing else needed)."""
    fields = dict(
        run_id=RUN,
        study=ticket.study,
        method=ticket.method,
        slot_id=ticket.slot_id,
        slot_index=ticket.slot_index,
        outcome=outcome,
        t_open_ms=ticket.t_open_ms,
        t_ms=ticket.t_open_ms if t_ms is None else t_ms,
    )
    if ticket.study is Study.A:
        p = parse_proposal_slot_id(ticket.slot_id)
        fields.update(
            profile=Profile.P1,
            atom_id=p.atom_id,
            slot=p.slot,
            batch_id="DEMO-A-P01",
            book_id=p.book_id,
            round=p.round,
        )
    else:
        b = parse_bank_slot_id(ticket.slot_id)
        fields.update(
            profile=Profile(b.profile),
            atom_id=b.atom_id,
            slot=b.slot,
            bank_id=b.bank_id,
            attempt=b.attempt,
        )
    if ticket.method is Method.A2:
        fields["a2"] = A2Detail(mode="uniform", parent_slot_id=None)
    fields.update(over)
    return SlotRecord(**fields)


def ledger_bytes(path):
    target = path / "slots.jsonl"
    return target.read_bytes() if target.exists() else b""


def take(ledger, key, slot_id, *, study=Study.A, method=Method.A3, **over):
    ticket = ledger.reserve(key, slot_id, study=study, method=method)
    return ledger.consume(record_for(ticket, **over))


def refusals(path):
    target = path / "slot-refusals.jsonl"
    return read_records(target, SlotRefusal) if target.exists() else []


# ---------------------------------------------------------------------------
# Reserve and consume


def test_reserve_then_consume(tmp_path):
    clock = ManualClock(1_000)
    ledger = make_ledger(tmp_path, clock)
    ticket = ledger.reserve(A_KEY, SLOTS_A[7], study=Study.A, method=Method.A3)
    assert ticket == SlotTicket(A_KEY, SLOTS_A[7], 8, Study.A, Method.A3, 1_000)
    assert ledger.used(A_KEY) == 1 and ledger.remaining(A_KEY) == 11
    assert ledger.open_tickets() == (ticket,)
    clock.advance(40_000)
    record = ledger.consume(record_for(ticket, t_ms=clock.now_ms()))
    assert ledger.records() == (record,) and ledger.records(A_KEY) == (record,)
    assert ledger.records(b_key()) == ()
    assert ledger.open_tickets() == () and ledger.used(A_KEY) == 1
    raw = (tmp_path / "slots.jsonl").read_bytes()
    assert raw == canonical_line(record.to_dict())
    assert read_records(tmp_path / "slots.jsonl", SlotRecord) == [record]


def test_study_b_slot_index_is_the_cell_slot(tmp_path):
    ledger = make_ledger(tmp_path)
    ticket = ledger.reserve(b_key(), b_slot(7), study=Study.B, method=Method.B)
    assert ticket.slot_index == 7
    ledger.consume(record_for(ticket))
    assert ledger.used(b_key()) == 1


@pytest.mark.parametrize("study", [Study.A, Study.B])
def test_thirteenth_slot_raises_and_is_logged(tmp_path, study):
    ledger = make_ledger(tmp_path)
    if study is Study.A:
        key, slots, method = A_KEY, SLOTS_A, Method.A3
    else:
        key, slots, method = b_key(), [b_slot(s) for s in range(1, 13)], Method.B
    for slot_id in slots:
        take(ledger, key, slot_id, study=study, method=method)
    assert ledger.used(key) == 12 and ledger.remaining(key) == 0
    before = (tmp_path / "slots.jsonl").read_bytes()
    with pytest.raises(SlotCapExceeded) as err:
        ledger.reserve(key, slots[-1], study=study, method=method)
    assert err.value.code == "E_SLOT_CAP"
    assert (tmp_path / "slots.jsonl").read_bytes() == before
    [refusal] = refusals(tmp_path)
    assert (refusal.reason, refusal.cap_key, refusal.requested, refusal.used) == (
        "slot_cap",
        key,
        slots[-1],
        12,
    )
    assert refusal.method is method and refusal.study is study
    assert ledger.used(key) == 12


def test_open_tickets_count_toward_the_cap(tmp_path):
    ledger = make_ledger(tmp_path, cap=2)
    ledger.reserve(A_KEY, SLOTS_A[0], study=Study.A, method=Method.A3)
    ledger.reserve(A_KEY, SLOTS_A[1], study=Study.A, method=Method.A3)
    with pytest.raises(SlotCapExceeded):
        ledger.reserve(A_KEY, SLOTS_A[2], study=Study.A, method=Method.A3)
    assert [r.reason for r in refusals(tmp_path)] == ["slot_cap"]


def test_reusing_a_slot_is_refused_and_logged(tmp_path):
    ledger = make_ledger(tmp_path)
    ticket = ledger.reserve(A_KEY, SLOTS_A[0], study=Study.A, method=Method.A3)
    with pytest.raises(SlotReused):
        ledger.reserve(A_KEY, SLOTS_A[0], study=Study.A, method=Method.A3)
    ledger.consume(record_for(ticket))
    with pytest.raises(SlotReused):
        ledger.reserve(A_KEY, SLOTS_A[0], study=Study.A, method=Method.A3)
    assert [(r.reason, r.used) for r in refusals(tmp_path)] == [
        ("slot_reused", 1),
        ("slot_closed", 1),
    ]
    assert ledger.used(A_KEY) == 1


def test_consume_needs_an_open_ticket(tmp_path):
    ledger = make_ledger(tmp_path)
    ticket = SlotTicket(A_KEY, SLOTS_A[0], 1, Study.A, Method.A3, 0)
    with pytest.raises(SlotNotReserved):
        ledger.consume(record_for(ticket))
    assert refusals(tmp_path) == [] and ledger_bytes(tmp_path) == b""
    real = ledger.reserve(A_KEY, SLOTS_A[0], study=Study.A, method=Method.A3)
    ledger.consume(record_for(real))
    with pytest.raises(SlotNotReserved):  # a second record for a consumed slot
        ledger.consume(record_for(real, outcome=SlotOutcome.INVALID_JSON))
    assert [r.reason for r in refusals(tmp_path)] == ["slot_closed"]
    assert len(ledger.records()) == 1
    with pytest.raises(TypeError):
        ledger.consume({"slot_id": SLOTS_A[0]})


@pytest.mark.parametrize(
    "change",
    [
        {"slot_index": 2},
        {"t_open_ms": 5},
        {"run_id": "DEMO-run-02"},
        {"method": Method.A2},
        {"round": 2},
        {"slot": 2},
        {"atom_id": "K-a2"},
        {"t_ms": 9},
    ],
)
def test_record_must_match_its_ticket(tmp_path, change):
    clock = ManualClock(10)
    ledger = make_ledger(tmp_path, clock)
    ticket = ledger.reserve(A_KEY, SLOTS_A[0], study=Study.A, method=Method.A3)
    with pytest.raises(SlotNotReserved, match="does not match its ticket"):
        ledger.consume(record_for(ticket, **change))
    assert ledger.open_tickets() == (ticket,) and ledger.records() == ()
    ledger.consume(record_for(ticket))
    assert len(ledger.records()) == 1


def test_study_b_record_must_match_its_slot_id(tmp_path):
    ledger = make_ledger(tmp_path)
    ticket = ledger.reserve(b_key(), b_slot(3), study=Study.B, method=Method.B)
    for change in ({"profile": Profile.P2}, {"attempt": 2}, {"bank_id": "DEMO-bank-02"}):
        with pytest.raises(SlotNotReserved):
            ledger.consume(record_for(ticket, **change))
    with pytest.raises(SlotNotReserved):  # a Study A record cannot close a B ticket
        ledger.consume(record_for(ticket, bank_id=None, attempt=None))
    ledger.consume(record_for(ticket))


def test_schema_failure_leaves_the_ticket_open(tmp_path):
    ledger = make_ledger(tmp_path)
    ticket = ledger.reserve(A_KEY, SLOTS_A[0], study=Study.A, method=Method.A3)
    with pytest.raises(RecordError):  # `valid` needs a recipe and hashes
        ledger.consume(record_for(ticket, outcome=SlotOutcome.VALID))
    assert ledger.open_tickets() == (ticket,)
    assert ledger_bytes(tmp_path) == b""
    ledger.consume(record_for(ticket))


def test_slot_id_must_belong_to_the_cap_key(tmp_path):
    ledger = make_ledger(tmp_path)
    cases = [
        (A_KEY, proposal_slot_id(OTHER_BOOK, ATOM, 1, 1), Study.A, Method.A3),
        (A_KEY, proposal_slot_id(BOOK, "K-a2", 1, 1), Study.A, Method.A3),
        (A_KEY, "not-a-slot", Study.A, Method.A3),
        (A_KEY, SLOTS_A[0], Study.A, Method.B),
        (b_key(), b_slot(1), Study.B, Method.A3),
        (b_key(), b_slot(1, profile="P2"), Study.B, Method.B),
        (A_KEY, b_slot(1), Study.A, Method.A3),
    ]
    for key, slot_id, study, method in cases:
        with pytest.raises(LedgerError) as err:
            ledger.reserve(key, slot_id, study=study, method=method)
        assert type(err.value) is LedgerError and err.value.code == "E_LEDGER"
    assert refusals(tmp_path) == [] and ledger.used(A_KEY) == 0


def test_a_cap_key_never_mixes_methods(tmp_path):
    ledger = make_ledger(tmp_path)
    take(ledger, A_KEY, SLOTS_A[0], method=Method.A3)
    with pytest.raises(LedgerError, match="belongs to A3"):
        ledger.reserve(A_KEY, SLOTS_A[1], study=Study.A, method=Method.A1)


def test_constructor_checks(tmp_path):
    with pytest.raises(ValueError):
        make_ledger(tmp_path, cap=0)
    with pytest.raises(IdError):
        SlotLedger(tmp_path / "slots.jsonl", run_id="x", clock=ManualClock())


# ---------------------------------------------------------------------------
# Attempts (Study B)


def test_fifth_attempt_is_refused_and_logged(tmp_path):
    ledger = make_ledger(tmp_path)
    for attempt in (1, 2, 3, 4):
        ledger.check_attempt(BANK, attempt)
    with pytest.raises(AttemptCapExceeded) as err:
        ledger.check_attempt(BANK, 5)
    assert err.value.code == "E_ATTEMPT_CAP"
    [refusal] = refusals(tmp_path)
    assert (refusal.reason, refusal.cap_key, refusal.requested, refusal.used) == (
        "attempt_cap",
        f"B|{BANK}",
        "attempt-5",
        4,
    )
    for bad in (0, -1, True, "2"):
        with pytest.raises(ValueError):
            ledger.check_attempt(BANK, bad)
    with pytest.raises(IdError):
        ledger.check_attempt("x", 1)


# ---------------------------------------------------------------------------
# Reopening and crash recovery


def test_reopen_counts_existing_records(tmp_path):
    first = make_ledger(tmp_path)
    for slot_id in SLOTS_A[:5]:
        take(first, A_KEY, slot_id)
    take(first, b_key(), b_slot(1), study=Study.B, method=Method.B)
    second = make_ledger(tmp_path)
    assert second.used(A_KEY) == 5 and second.used(b_key()) == 1
    assert second.records() == first.records()
    with pytest.raises(SlotReused):
        second.reserve(A_KEY, SLOTS_A[4], study=Study.A, method=Method.A3)
    with pytest.raises(LedgerError):  # the cap key stays with its method
        second.reserve(A_KEY, SLOTS_A[5], study=Study.A, method=Method.A2)
    take(second, A_KEY, SLOTS_A[5])
    assert len(list(iter_jsonl(tmp_path / "slots.jsonl"))) == 7


def test_reopen_repairs_torn_tails_and_logs_them(tmp_path):
    ledger = make_ledger(tmp_path)
    take(ledger, A_KEY, SLOTS_A[0])
    with pytest.raises(SlotReused):
        ledger.reserve(A_KEY, SLOTS_A[0], study=Study.A, method=Method.A3)
    with open(tmp_path / "slots.jsonl", "ab") as handle:
        handle.write(b'{"record":"slot","run_id":"DEMO-ru')
    with open(tmp_path / "slot-refusals.jsonl", "ab") as handle:
        handle.write(b'{"rec')
    clock = ManualClock(77)
    reopened = make_ledger(tmp_path, clock)
    assert [t.name for t in reopened.repairs] == ["slots.jsonl", "slot-refusals.jsonl"]
    events = read_records(tmp_path / "timing.jsonl", TimingEvent)
    assert [(e.event, e.component, e.t_ms) for e in events] == [("log_repaired", "ledger", 77)] * 2
    assert "cut 34 bytes" in events[0].detail and reopened.repairs[0].sha256 in events[0].detail
    assert reopened.used(A_KEY) == 1 and len(refusals(tmp_path)) == 1
    take(reopened, A_KEY, SLOTS_A[1])
    assert len(read_records(tmp_path / "slots.jsonl", SlotRecord)) == 2


def test_repair_goes_to_the_given_timing_writer(tmp_path):
    take(make_ledger(tmp_path), A_KEY, SLOTS_A[0])
    with open(tmp_path / "slots.jsonl", "ab") as handle:
        handle.write(b"{")
    timing = RecordWriter(tmp_path / "elsewhere.jsonl", types=(TimingEvent,))
    make_ledger(tmp_path, timing=timing)
    assert timing.count == 1 and not (tmp_path / "timing.jsonl").exists()


def _rewrite(path, records):
    path.write_bytes(b"".join(canonical_line(r.to_dict()) for r in records))


def test_reopen_refuses_inconsistent_logs(tmp_path):
    ledger = make_ledger(tmp_path)
    for slot_id in SLOTS_A[:3]:
        take(ledger, A_KEY, slot_id)
    records = list(ledger.records())
    target = tmp_path / "slots.jsonl"
    _rewrite(target, [*records, records[0]])
    with pytest.raises(LedgerError, match="appears twice"):
        make_ledger(tmp_path)
    _rewrite(target, [*records, dataclasses.replace(records[0], run_id="DEMO-run-02")])
    with pytest.raises(LedgerError, match="record of run"):
        make_ledger(tmp_path)
    _rewrite(target, records)
    with pytest.raises(LedgerError, match="cap 2"):
        make_ledger(tmp_path, cap=2)
    a1 = dataclasses.replace(records[2], method=Method.A1)
    _rewrite(target, [*records[:2], a1])
    with pytest.raises(LedgerError, match="mixes methods"):
        make_ledger(tmp_path)


def test_refusals_default_to_the_file_beside_the_ledger(tmp_path):
    custom = RecordWriter(tmp_path / "other" / "refusals.jsonl", types=(SlotRefusal,))
    ledger = make_ledger(tmp_path, refusals=custom)
    ticket = ledger.reserve(A_KEY, SLOTS_A[0], study=Study.A, method=Method.A3)
    with pytest.raises(SlotReused):
        ledger.reserve(A_KEY, ticket.slot_id, study=Study.A, method=Method.A3)
    assert custom.count == 1 and not (tmp_path / "slot-refusals.jsonl").exists()


# ---------------------------------------------------------------------------
# Concurrency


def test_three_methods_in_parallel(tmp_path):
    ledger = make_ledger(tmp_path)
    books = {Method.A1: "DEMO-BK-7QX4", Method.A2: "DEMO-BK-M2RW", Method.A3: BOOK}
    errors = []

    def run(method, book):
        try:
            key = cap_key(Study.A, ATOM, book_id=book)
            for r in (1, 2, 3, 4):
                for s in (1, 2, 3):
                    take(ledger, key, proposal_slot_id(book, ATOM, r, s), method=method)
        except Exception as err:  # pragma: no cover - reported below
            errors.append(err)

    threads = [threading.Thread(target=run, args=item) for item in books.items()]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    records = read_records(tmp_path / "slots.jsonl", SlotRecord)
    assert Counter(r.method for r in records) == {Method.A1: 12, Method.A2: 12, Method.A3: 12}
    assert all(ledger.used(cap_key(Study.A, ATOM, book_id=b)) == 12 for b in books.values())


def test_racing_threads_never_charge_a_slot_twice(tmp_path):
    ledger = make_ledger(tmp_path)
    key = b_key()
    slot_ids = [b_slot(s) for s in range(1, 13)]
    outcomes = Counter()
    lock = threading.Lock()

    def run(seed):
        order = slot_ids * 2
        random.Random(seed).shuffle(order)
        for slot_id in order:
            try:
                ticket = ledger.reserve(key, slot_id, study=Study.B, method=Method.B)
            except (SlotReused, SlotCapExceeded) as err:
                with lock:
                    outcomes[type(err).__name__] += 1
                continue
            ledger.consume(record_for(ticket))
            with lock:
                outcomes["consumed"] += 1

    threads = [threading.Thread(target=run, args=(seed,)) for seed in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes["consumed"] == 12
    assert outcomes["SlotReused"] + outcomes["SlotCapExceeded"] == 6 * 24 - 12
    assert len(read_records(tmp_path / "slots.jsonl", SlotRecord)) == 12
    assert len(refusals(tmp_path)) == 6 * 24 - 12


@settings(max_examples=60, deadline=None)
@given(
    st.lists(
        st.tuples(st.sampled_from(["reserve", "consume"]), st.integers(0, 1), st.integers(1, 6)),
        max_size=30,
    )
)
def test_property_ledger_matches_a_model(ops):
    """Against a plain model: used <= cap, one line per consumed slot, one refusal per
    refused request, and a reopened ledger agrees."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        ledger = make_ledger(path, cap=4)
        keys = [b_key(atom="K-a1"), b_key(atom="K-a2")]
        open_ids: dict[str, SlotTicket] = {}
        closed: set[str] = set()
        refused = 0
        for op, k, slot in ops:
            slot_id = b_slot(slot, atom=["K-a1", "K-a2"][k])
            if op == "reserve":
                used = sum(
                    1 for s in [*open_ids, *closed] if s.split(".")[3] == ["K-a1", "K-a2"][k]
                )
                expect_ok = used < 4 and slot_id not in open_ids and slot_id not in closed
                try:
                    open_ids[slot_id] = ledger.reserve(
                        keys[k], slot_id, study=Study.B, method=Method.B
                    )
                    assert expect_ok
                except (SlotCapExceeded, SlotReused):
                    assert not expect_ok
                    refused += 1
            elif open_ids:
                ticket = open_ids.pop(sorted(open_ids)[0])
                ledger.consume(record_for(ticket))
                closed.add(ticket.slot_id)
            for key in keys:
                assert ledger.used(key) <= 4
        assert len(ledger.records()) == len(closed)
        assert len(refusals(path)) == refused
        reopened = make_ledger(path, cap=4)
        for key in keys:
            assert reopened.used(key) == ledger.used(key) - sum(
                1 for t in open_ids.values() if t.cap_key == key
            )


# ---------------------------------------------------------------------------
# One fixture per outcome code: exactly one slot, at most one model call

A3_CODES = [SlotOutcome(c) for c in OUTCOME_CODES if c != SlotOutcome.INCOMPATIBLE]


@pytest.mark.parametrize("code", A3_CODES, ids=str)
def test_each_a3_outcome_consumes_one_slot_and_at_most_one_call(tmp_path, code):
    clock = ManualClock(0)
    ledger = demo.demo_ledger(tmp_path, clock, run_id=RUN)
    [result], client = demo.run_a3_rounds(
        [(demo.A_ATOM, 1, (code, SlotOutcome.VALID, SlotOutcome.VALID))], ledger, clock
    )
    first, *rest = result.records
    assert first.outcome is code
    assert [r.outcome for r in rest] == [SlotOutcome.VALID, SlotOutcome.VALID]
    assert ledger.records() == result.records and ledger.used(first.cap_key) == 3
    calls = Counter(c.slot_id for c in client.calls)
    assert calls[first.slot_id] == (0 if code is SlotOutcome.OVERFLOW_INPUT else 1)
    assert all(calls[r.slot_id] == 1 for r in rest)
    assert refusals(tmp_path) == []


@pytest.mark.parametrize(
    "code",
    [SlotOutcome.VALID, SlotOutcome.DUPLICATE, SlotOutcome.INCOMPATIBLE, SlotOutcome.INVALID_JSON],
    ids=str,
)
def test_each_b_outcome_consumes_one_slot_and_at_most_one_call(tmp_path, code):
    clock = ManualClock(0)
    ledger = demo.demo_ledger(tmp_path, clock, run_id=RUN)
    [record], client = demo.run_b_slots([code], ledger, clock)
    assert record.outcome is code and record.study is Study.B and record.method is Method.B
    assert ledger.records() == (record,) and ledger.used(record.cap_key) == 1
    assert [c.slot_id for c in client.calls] == [record.slot_id]


def test_failed_token_count_consumes_the_slot_without_a_call(tmp_path):
    clock = ManualClock(0)
    ledger = demo.demo_ledger(tmp_path, clock, run_id=RUN)

    def broken(messages):
        raise TokenCountError("tokenize failed")

    client = ScriptedLlmClient([], token_counter=broken)
    proposer = demo.a3_proposer(client, ledger, clock)
    result = proposer.propose_round(demo.demo_request(1, clock=clock, run_id=RUN))
    assert [r.outcome for r in result.records] == [SlotOutcome.INVALID_JSON] * 3
    assert {r.llm_status for r in result.records} == {LlmStatus.SERVER_ERROR}
    assert client.call_count == 0 and len(ledger.records()) == 3


def test_demo_example_ledger_is_reproducible(tmp_path):
    summary = demo.write_demo_ledger(tmp_path)
    assert summary["outcomes"] == sorted(OUTCOME_CODES)
    assert summary["refusals"] == ["E_SLOT_CAP", "E_ATTEMPT_CAP"]
    assert summary["max_calls_per_slot"] == 1
    assert summary["records"] == 19 and summary["calls"] == 18
    committed = ROOT / "generation" / "examples" / demo.EXAMPLE_DIR
    for name in ("slots.jsonl", "slot-refusals.jsonl"):
        assert (tmp_path / name).read_bytes() == (committed / name).read_bytes(), name
    out = ROOT / "generation" / "out" / "ci" / "slot-ledger-demo"
    shutil.rmtree(out, ignore_errors=True)
    shutil.copytree(tmp_path, out)


def test_logged_outcome_follows_the_validator_codes(tmp_path):
    demo.write_demo_ledger(tmp_path)
    for record in read_records(tmp_path / "slots.jsonl", SlotRecord):
        if record.llm_status is not LlmStatus.OK:
            assert record.validator_codes == ()
            continue
        if record.study is Study.A:
            assert outcome_from_validator_codes(record.validator_codes) is record.outcome
            valid = record.recipe_sha256 is not None and not record.validator_codes
            assert (record.outcome is SlotOutcome.VALID) == valid


def test_second_record_without_a_cap_key_raises_without_refusal(tmp_path):
    ledger = make_ledger(tmp_path)
    ticket = ledger.reserve(A_KEY, SLOTS_A[0], study=Study.A, method=Method.A3)
    ledger.consume(record_for(ticket))
    with pytest.raises(SlotNotReserved):
        ledger.consume(record_for(ticket, book_id=None))
    assert refusals(tmp_path) == []
