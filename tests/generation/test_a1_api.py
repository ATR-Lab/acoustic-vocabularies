"""A1 hand-designer interface (#19): API, slot timer, ledger, audio tokens, practice mode,
masking sentinel, a 48-slot session and browser tests (`@pytest.mark.browser`).

The slot ledger is #17's (`av_generation.ledger.SlotLedger`). Until it lands, these
tests use `StandInLedger`, a stand-in with the same documented contract (reserve before
work, consume with an open ticket, refusals logged); `make_ledger` switches to the real
ledger automatically once it is implemented.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from av_sound.recipe import Profile, Recipe
from av_sound.wav import pcm_from_wav
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from av_generation import _a1_cli
from av_generation._a1_app import SECURITY_HEADERS
from av_generation._a1_practice import (
    PRACTICE_LABELS,
    demo_practice_meanings,
    open_practice_session,
    practice_atoms,
)
from av_generation._paths import examples_path
from av_generation.a1 import (
    ROUTES,
    A1Error,
    A1SlotService,
    create_a1_app,
    is_practice_batch,
    recipe_text,
)
from av_generation.clock import ManualClock, ScaledClock
from av_generation.config import BatchConfig
from av_generation.constants import SLOT_CAP_MS
from av_generation.ids import Method, RunKind, Study, proposal_slot_id
from av_generation.ledger import (
    SlotCapExceeded,
    SlotLedger,
    SlotNotReserved,
    SlotReused,
    SlotTicket,
)
from av_generation.masking import METHOD_REVEALING_FIELDS
from av_generation.meanings import load_meanings
from av_generation.outcomes import SlotOutcome
from av_generation.proposers import (
    AtomFeedback,
    BookState,
    CandidateFeedback,
    CommittedAtom,
    RaterScore,
    RoundRequest,
    RoundResult,
)
from av_generation.records import (
    A2Detail,
    PlayEvent,
    RecordWriter,
    RunManifest,
    SlotRecord,
    SlotRefusal,
    TimingEvent,
    read_records,
)
from av_generation.rundir import RunPolicyError, create_run_dir

REPO = Path(__file__).resolve().parents[2]
CONFIG = BatchConfig.read(examples_path("demo-batch-config.json"))
MEANINGS = load_meanings(examples_path("demo-meanings"))
BOOK = CONFIG.book_of(Method.A1).book_id  # DEMO-BK-7QX4
OTHER_BOOKS = tuple(b.book_id for b in CONFIG.books if b.method is not Method.A1)
PROFILE = CONFIG.profile
RUN = "DEMO-A1-RUN-01"
ATOMS = CONFIG.atom_order[:4]

VALID = (
    {"total_ms": 600, "pitches": [0, 2, 4], "rhythm_weights": [2, 2, 2], "gaps_ms": [40, 40],
     "amplitudes": [0.8, 0.8, 0.8]},
    {"total_ms": 900, "pitches": [-6, 6, -6], "rhythm_weights": [1, 4, 1], "gaps_ms": [20, 60],
     "amplitudes": [1.0, 0.6, 1.0]},
    {"total_ms": 750, "pitches": [-3, -3, 3], "rhythm_weights": [3, 3, 1], "gaps_ms": [60, 60],
     "amplitudes": [1.0, 1.0, 0.6]},
    {"total_ms": 450, "pitches": [6, -6, 6], "rhythm_weights": [2, 2, 2], "gaps_ms": [20, 20],
     "amplitudes": [0.6, 1.0, 0.6]},
)  # fmt: skip
"""One admissible recipe per atom; pairwise above the 0.10 separation threshold."""
SHORT_EVENT = {"total_ms": 450, "pitches": [0, 0, 0], "rhythm_weights": [4, 1, 1],
               "gaps_ms": [60, 60], "amplitudes": [0.8, 0.8, 0.8]}  # fmt: skip
OUT_OF_DOMAIN = {**VALID[0], "total_ms": 500}


# ---------------------------------------------------------------------------
# Ledger: #17's ledger when implemented, else a stand-in with the same contract


class StandInLedger:
    """Stand-in for `SlotLedger` (#17) following its documented contract."""

    def __init__(self, path, *, run_id, clock, refusals=None, timing=None, cap=12):
        self.path = Path(path)
        self.run_id = run_id
        self._clock = clock
        self._refusals = refusals
        self._cap = cap
        self._writer = RecordWriter(self.path)
        self._records: list[SlotRecord] = []
        self._open: dict[str, SlotTicket] = {}
        self._used: Counter[str] = Counter()
        self._lock = threading.Lock()
        if self.path.exists():
            for rec in read_records(self.path, SlotRecord):
                self._records.append(rec)
                self._used[rec.cap_key] += 1

    def _refuse(self, cap_key, slot_id, reason, study, method):
        if self._refusals is not None:
            self._refusals.append(
                SlotRefusal(
                    run_id=self.run_id,
                    study=study,
                    method=method,
                    cap_key=cap_key,
                    reason=reason,
                    requested=slot_id,
                    used=self._used[cap_key],
                    t_ms=self._clock.now_ms(),
                )
            )

    def reserve(self, cap_key, slot_id, *, study, method):
        with self._lock:
            if self._used[cap_key] >= self._cap:
                self._refuse(cap_key, slot_id, "slot_cap", study, method)
                raise SlotCapExceeded(f"{cap_key}: cap {self._cap}")
            if slot_id in self._open or any(r.slot_id == slot_id for r in self._records):
                self._refuse(cap_key, slot_id, "slot_reused", study, method)
                raise SlotReused(slot_id)
            self._used[cap_key] += 1
            ticket = SlotTicket(
                cap_key, slot_id, self._used[cap_key], Study(study), Method(method),
                self._clock.now_ms(),
            )  # fmt: skip
            self._open[slot_id] = ticket
            return ticket

    def consume(self, record):
        with self._lock:
            ticket = self._open.get(record.slot_id)
            if (
                ticket is None
                or ticket.cap_key != record.cap_key
                or ticket.slot_index != record.slot_index
            ):
                raise SlotNotReserved(record.slot_id)
            del self._open[record.slot_id]
            self._writer.append(record)
            self._records.append(record)
            return record

    def used(self, cap_key):
        with self._lock:
            return self._used[cap_key]

    def remaining(self, cap_key):
        return self._cap - self.used(cap_key)

    def records(self, cap_key=None):
        with self._lock:
            return tuple(r for r in self._records if cap_key is None or r.cap_key == cap_key)


def make_ledger(path, **kwargs):
    try:
        return SlotLedger(path, **kwargs)
    except NotImplementedError:
        return StandInLedger(path, **kwargs)


class TimedLedger:
    """Delegating ledger that notes the clock time of every `consume` (timer tolerance)."""

    def __init__(self, inner, clock):
        self.inner = inner
        self.run_id = getattr(inner, "run_id", None)
        self._clock = clock
        self.consumed_at: dict[str, int] = {}

    def reserve(self, *args, **kwargs):
        return self.inner.reserve(*args, **kwargs)

    def consume(self, record):
        now = self._clock.now_ms()
        result = self.inner.consume(record)
        self.consumed_at[record.slot_id] = now
        return result

    def used(self, cap_key):
        return self.inner.used(cap_key)

    def remaining(self, cap_key):
        return self.inner.remaining(cap_key)

    def records(self, cap_key=None):
        return self.inner.records(cap_key)


# ---------------------------------------------------------------------------
# Rig: run directory, writers, service, app client, round driver


@dataclass
class Rig:
    root: Path
    clock: Any
    ledger: Any
    service: A1SlotService
    client: TestClient
    logs: Path
    run_id: str = RUN
    rounds: list[RoundResult] = field(default_factory=list)
    committed: list[CommittedAtom] = field(default_factory=list)
    thread: threading.Thread | None = None
    error: list[BaseException] = field(default_factory=list)

    def slots(self) -> list[SlotRecord]:
        return read_records(self.logs / "slots.jsonl", SlotRecord)

    def plays(self) -> list[PlayEvent]:
        path = self.logs / "plays.jsonl"
        return read_records(path, PlayEvent) if path.exists() else []

    def refusals(self) -> list[SlotRefusal]:
        path = self.logs / "slot-refusals.jsonl"
        return read_records(path, SlotRefusal) if path.exists() else []

    def timing(self) -> list[TimingEvent]:
        path = self.logs / "timing.jsonl"
        return read_records(path, TimingEvent) if path.exists() else []


def make_rig(
    root: Path, *, clock=None, timed=False, hide_run_id=False, run_id=RUN, **kwargs
) -> Rig:
    clock = clock or ManualClock()
    logs = root / run_id / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    timing = RecordWriter(logs / "timing.jsonl")
    refusals = RecordWriter(logs / "slot-refusals.jsonl")
    ledger = make_ledger(
        logs / "slots.jsonl", run_id=run_id, clock=clock, refusals=refusals, timing=timing
    )
    if timed or hide_run_id:
        ledger = TimedLedger(ledger, clock)
        if hide_run_id:  # the service then learns the run ID from the first request
            ledger.run_id = None
    service = A1SlotService(
        ledger,
        RecordWriter(logs / "plays.jsonl"),
        timing,
        clock=clock,
        designer_id="D1",
        meanings=MEANINGS,
        **kwargs,
    )
    return Rig(root, clock, ledger, service, TestClient(create_a1_app(service)), logs, run_id)


def ratings_for(rec: SlotRecord) -> tuple[RaterScore, ...]:
    if rec.outcome is not SlotOutcome.VALID:
        return ()
    return tuple(RaterScore(5, 4, "acceptable") for _ in range(3))


def build_request(
    rig: Rig,
    atom: str,
    round_: int,
    history: Sequence[SlotRecord] = (),
    *,
    window_ms: int = 120_000,
    batch_id: str = CONFIG.batch_id,
    book_id: str = BOOK,
    run_id: str | None = None,
) -> RoundRequest:
    run_id = run_id or rig.run_id
    cands = tuple(
        CandidateFeedback(
            rec.slot_id,
            rec.round or 1,
            rec.slot,
            rec.slot_index,
            None if rec.recipe is None else Recipe.from_dict(rec.recipe),
            rec.outcome,
            rec.validator_codes,
            ratings_for(rec),
            rec.outcome is SlotOutcome.VALID,
            Fraction(9, 2) if rec.outcome is SlotOutcome.VALID else None,
        )  # fmt: skip
        for rec in history
    )
    incumbent = next((c for c in cands if c.eligible), None)
    return RoundRequest(
        run_id=run_id,
        batch_id=batch_id,
        book_id=book_id,
        method=Method.A1,
        atom_id=atom,
        round=round_,
        profile=PROFILE,
        seed_namespace=batch_id,
        book=BookState(batch_id, book_id, PROFILE, CONFIG.threshold, tuple(rig.committed)),
        feedback=AtomFeedback(
            book_id,
            atom,
            round_ - 1,
            cands,
            None if incumbent is None else incumbent.slot_id,
            None if incumbent is None else incumbent.score,
        ),  # fmt: skip
        window_end_ms=rig.clock.now_ms() + window_ms,
        semantic_label=CONFIG.labels[atom],
    )


def start_rounds(rig: Rig, atoms: Sequence[str], rounds: int = 4, **request_kwargs) -> None:
    """Run the orchestrator side in a thread: rounds of each atom, commit the incumbent."""

    def drive() -> None:
        try:
            for atom in atoms:
                history: list[SlotRecord] = []
                for round_ in range(1, rounds + 1):
                    result = rig.service.propose_round(
                        build_request(rig, atom, round_, history, **request_kwargs)
                    )
                    rig.rounds.append(result)
                    history.extend(result.records)
                best = next((r for r in history if r.outcome is SlotOutcome.VALID), None)
                if best is not None:
                    rig.committed.append(
                        CommittedAtom(
                            atom,
                            CONFIG.labels[atom],
                            Recipe.from_dict(best.recipe),
                            best.pcm_sha256,
                            len(rig.committed),
                        )  # fmt: skip
                    )
        except BaseException as err:  # surfaced by the test
            rig.error.append(err)

    rig.thread = threading.Thread(target=drive, daemon=True)
    rig.thread.start()


def wait_for(predicate: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        time.sleep(0.005)


def wait_window(rig: Rig, atom: str, round_: int) -> dict[str, Any]:
    def ready() -> bool:
        assert not rig.error, rig.error
        w = rig.service.state()["window"]
        return bool(w and w["open"] and w["atom_id"] == atom and w["round"] == round_)

    wait_for(ready)
    return rig.service.state()["window"]


def finish(rig: Rig) -> None:
    assert rig.thread is not None
    rig.thread.join(timeout=10)
    assert not rig.thread.is_alive()
    assert not rig.error, rig.error


def api(rig: Rig, method: str, path: str, body: Any = None, expect: int = 200) -> Any:
    response = rig.client.request(method, path, json=body)
    assert response.status_code == expect, (response.status_code, response.text)
    ctype = response.headers.get("content-type", "")
    return response.json() if ctype.startswith("application/json") else response


def open_slot(rig: Rig) -> dict[str, Any]:
    return api(rig, "POST", "/a1/api/slots/open")


def submit(rig: Rig, slot_id: str, recipe: Any, expect: int = 200) -> dict[str, Any]:
    return api(rig, "POST", f"/a1/api/slots/{slot_id}/submit", {"recipe": recipe}, expect)


def advance_to_close(rig: Rig, slot_id: str, ms: int) -> None:
    rig.clock.advance(ms)
    wait_for(lambda: any(r.slot_id == slot_id for r in rig.ledger.records()))


def walk_keys(obj: Any) -> Iterator[str]:
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield key
            yield from walk_keys(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from walk_keys(value)


# ---------------------------------------------------------------------------
# Routes and basic flow


def test_routes_match_the_contract(tmp_path):
    rig = make_rig(tmp_path)
    app = create_a1_app(rig.service)
    served = {
        f"{sorted(r.methods - {'HEAD'})[0]} {r.path}"
        for r in app.routes
        if getattr(r, "methods", None)
    }
    assert set(ROUTES.values()) <= served
    page = rig.client.get("/a1/")
    assert page.status_code == 200 and "A1 designer interface" in page.text
    for name in ("a1.js", "a1.css"):
        assert rig.client.get(f"/a1/static/{name}").status_code == 200
    assert rig.client.get("/a1/static/../a1.py").status_code == 404
    assert rig.client.get("/a1/static/index.html").status_code == 404
    assert rig.client.get("/", follow_redirects=False).headers["location"] == "/a1/"
    assert rig.client.get("/a1", follow_redirects=False).headers["location"] == "/a1/"
    for name, value in SECURITY_HEADERS.items():
        assert page.headers[name] == value
    state = api(rig, "GET", "/a1/api/state")
    assert state["window"] is None and state["mode"] == "study"
    assert state["slot_cap_ms"] == SLOT_CAP_MS == 40_000
    assert state["domain"] == {
        "total_ms": [450, 600, 750, 900],
        "pitches": list(range(-6, 7)),
        "rhythm_weights": [1, 2, 3, 4],
        "gaps_ms": [20, 40, 60],
        "amplitudes": [0.6, 0.8, 1.0],
    }
    assert api(rig, "GET", "/a1/api/feedback") == {"available": False, "practice": False}
    assert api(rig, "GET", "/a1/api/book")["available"] is False
    assert api(rig, "POST", "/a1/api/slots/open", expect=409)["error"]["code"] == "E_NO_WINDOW"


def test_one_round_open_submit_play(tmp_path):
    rig = make_rig(tmp_path)
    atom = ATOMS[0]
    start_rounds(rig, [atom], rounds=1)
    window = wait_window(rig, atom, 1)
    assert window["meaning"] == MEANINGS.text(CONFIG.labels[atom])
    assert [s["state"] for s in window["slots"]] == ["unopened"] * 3
    opened = open_slot(rig)
    assert opened["slot_id"] == proposal_slot_id(BOOK, atom, 1, 1)
    assert opened["deadline_ms"] - opened["t_open_ms"] == SLOT_CAP_MS
    assert api(rig, "POST", "/a1/api/slots/open", expect=409)["error"]["code"] == "E_SLOT_OPEN"
    rig.clock.advance(7_000)
    result = submit(rig, opened["slot_id"], VALID[0])
    assert result["valid"] and result["outcome"] == "valid" and result["audio"]
    wav = api(rig, "GET", result["audio"]["url"])
    assert wav.headers["content-type"] == "audio/wav"
    assert wav.headers["cache-control"] == "no-store"
    rec = rig.slots()[0]
    assert rec.outcome is SlotOutcome.VALID and rec.latency_ms == 7_000
    assert rec.design_ms == 7_000 and rec.designer_id == "D1" and not rec.practice
    assert rec.raw_output == recipe_text(VALID[0])
    assert rec.recipe == Recipe.from_dict(VALID[0]).to_dict()
    from av_sound.wav import pcm_sha256

    assert pcm_sha256(pcm_from_wav(wav.content)) == rec.pcm_sha256
    (play,) = rig.plays()
    assert (play.result, play.context, play.audio_kind) == ("played", "a1_preview", "atom")
    assert play.slot_id == rec.slot_id and play.asset_id == rec.file_sha256
    assert play.pcm_sha256 == rec.pcm_sha256 and play.actor_id == "D1"
    # Slots 2 and 3: an invalid recipe (no audio) and a timeout.
    second = open_slot(rig)
    bad = submit(rig, second["slot_id"], SHORT_EVENT)
    assert bad["outcome"] == "event_too_short" and bad["audio"] is None
    assert bad["validator_codes"] == ["E_EVENT_SHORT"] and bad["validator_messages"]
    third = open_slot(rig)
    advance_to_close(rig, third["slot_id"], SLOT_CAP_MS)
    finish(rig)
    (result_round,) = rig.rounds
    assert [r.outcome for r in result_round.records] == [
        SlotOutcome.VALID,
        SlotOutcome.EVENT_TOO_SHORT,
        SlotOutcome.TIMEOUT,
    ]
    assert len(rig.plays()) == 1
    feedback = api(rig, "GET", "/a1/api/feedback")
    assert [c["outcome"] for c in feedback["current_round"]] == [
        "valid",
        "event_too_short",
        "timeout",
    ]


# ---------------------------------------------------------------------------
# Acceptance criteria


def test_slot_times_out_at_40_s_server_time(tmp_path):
    rig = make_rig(tmp_path, timed=True)
    atom = ATOMS[0]
    start_rounds(rig, [atom], rounds=1)
    wait_window(rig, atom, 1)
    opened = open_slot(rig)
    rig.clock.advance(SLOT_CAP_MS - 1)
    rig.service.tick()
    assert rig.service.state()["window"]["slots"][0]["state"] == "open"
    assert rig.service.state()["window"]["slots"][0]["remaining_ms"] == 1
    rig.clock.advance(1)
    wait_for(lambda: opened["slot_id"] in rig.ledger.consumed_at)
    rec = rig.slots()[0]
    assert rec.outcome is SlotOutcome.TIMEOUT
    assert rec.t_ms - rec.t_open_ms == SLOT_CAP_MS
    assert rec.latency_ms is None and rec.raw_output is None and rec.recipe is None
    # A submission after the deadline is an edit of a closed slot.
    late = submit(rig, opened["slot_id"], VALID[0], expect=409)
    assert late["error"]["code"] == "E_SLOT_CLOSED"
    rig.clock.advance(120_000)
    finish(rig)


def test_slot_auto_closes_in_real_time_within_tolerance(tmp_path):
    # The real component on an accelerated clock: 40 s of server time take 10 s, so the
    # 0.5-s tolerance is 125 ms of real time (timer checks run every 10 ms).
    clock = ScaledClock(4.0)
    rig = make_rig(tmp_path, clock=clock, timed=True)
    atom = ATOMS[0]
    start_rounds(rig, [atom], rounds=1)
    wait_window(rig, atom, 1)
    opened = open_slot(rig)
    wait_for(lambda: opened["slot_id"] in rig.ledger.consumed_at, timeout=30)
    closed_at = rig.ledger.consumed_at[opened["slot_id"]]
    rec = rig.slots()[0]
    assert rec.outcome is SlotOutcome.TIMEOUT
    assert rec.t_ms - rec.t_open_ms == SLOT_CAP_MS
    assert SLOT_CAP_MS <= closed_at - rec.t_open_ms <= SLOT_CAP_MS + 500
    # Close the other slots quickly: the window ends 120 s (30 real s) after it opened.
    for _ in range(2):
        nxt = open_slot(rig)
        submit(rig, nxt["slot_id"], SHORT_EVENT)
    finish(rig)


def test_submitted_recipe_cannot_be_edited(tmp_path):
    rig = make_rig(tmp_path)
    atom = ATOMS[0]
    start_rounds(rig, [atom], rounds=1)
    wait_window(rig, atom, 1)
    opened = open_slot(rig)
    first = submit(rig, opened["slot_id"], VALID[0])
    assert first["valid"]
    edit = submit(rig, opened["slot_id"], VALID[1], expect=409)
    assert edit["error"]["code"] == "E_SLOT_CLOSED"
    for method in ("PUT", "PATCH", "DELETE"):
        response = rig.client.request(
            method, f"/a1/api/slots/{opened['slot_id']}/submit", json={"recipe": VALID[1]}
        )
        assert response.status_code == 405
    (rec,) = rig.slots()
    assert rec.recipe == Recipe.from_dict(VALID[0]).to_dict()
    (refusal,) = rig.refusals()
    assert refusal.reason == "slot_closed" and refusal.requested == opened["slot_id"]
    assert refusal.cap_key == f"A|{BOOK}|{atom}" and refusal.method is Method.A1
    unknown = submit(rig, proposal_slot_id(BOOK, atom, 1, 3), VALID[0], expect=404)
    assert unknown["error"]["code"] == "E_UNKNOWN_SLOT"
    rig.clock.advance(120_000)
    finish(rig)
    assert len(rig.slots()) == 3


def test_second_audio_request_is_refused_and_logged(tmp_path):
    rig = make_rig(tmp_path)
    atom = ATOMS[0]
    start_rounds(rig, [atom], rounds=1)
    wait_window(rig, atom, 1)
    opened = open_slot(rig)
    url = submit(rig, opened["slot_id"], VALID[0])["audio"]["url"]
    assert rig.client.head(url).status_code == 405  # no way to spend a token without a body
    assert api(rig, "GET", url).content[:4] == b"RIFF"
    again = api(rig, "GET", url, expect=410)
    assert again["error"]["code"] == "E_TOKEN_USED"
    assert api(rig, "GET", url, expect=410)["error"]["code"] == "E_TOKEN_USED"
    assert api(rig, "GET", "/a1/api/audio/not-a-token-123", expect=404)
    assert api(rig, "GET", "/a1/api/audio/x", expect=404)["error"]["code"] == "E_UNKNOWN_TOKEN"
    played, *refused = rig.plays()
    assert played.result == "played" and played.onset_ms is None
    assert [(p.result, p.reason) for p in refused] == [("refused", "E_TOKEN_USED")] * 2
    assert {p.slot_id for p in rig.plays()} == {opened["slot_id"]}
    assert {p.token_id for p in rig.plays()} == {url.rsplit("/", 1)[1]}
    rig.clock.advance(120_000)
    finish(rig)


def test_audio_token_expires(tmp_path):
    rig = make_rig(tmp_path, audio_ttl_ms=5_000)
    atom = ATOMS[0]
    start_rounds(rig, [atom], rounds=1)
    wait_window(rig, atom, 1)
    opened = open_slot(rig)
    url = submit(rig, opened["slot_id"], VALID[0])["audio"]["url"]
    state_slot = rig.service.state()["window"]["slots"][0]
    assert state_slot["audio"]["url"] == url  # recoverable after a page reload
    rig.clock.advance(5_000)
    assert api(rig, "GET", url, expect=410)["error"]["code"] == "E_TOKEN_EXPIRED"
    assert rig.service.state()["window"]["slots"][0]["audio"] is None
    (refused,) = rig.plays()
    assert (refused.result, refused.reason) == ("refused", "E_TOKEN_EXPIRED")
    rig.clock.advance(120_000)
    finish(rig)


def test_13th_slot_request_for_an_atom_is_refused(tmp_path):
    rig = make_rig(tmp_path)
    atom = ATOMS[0]
    start_rounds(rig, [atom], rounds=4)
    for round_ in range(1, 5):
        wait_window(rig, atom, round_)
        for _ in range(3):
            opened = open_slot(rig)
            submit(rig, opened["slot_id"], SHORT_EVENT)
    finish(rig)
    cap = f"A|{BOOK}|{atom}"
    assert rig.ledger.used(cap) == 12 and len(rig.slots()) == 12
    refused = api(rig, "POST", "/a1/api/slots/open", expect=409)
    assert refused["error"]["code"] == "E_SLOT_CAP"
    (refusal,) = rig.refusals()
    assert (refusal.reason, refusal.used, refusal.cap_key) == ("slot_cap", 12, cap)
    assert len(rig.slots()) == 12


def test_resumed_atom_at_its_cap_refuses_every_slot(tmp_path):
    rig = make_rig(tmp_path)
    atom = ATOMS[0]
    start_rounds(rig, [atom], rounds=4)
    for round_ in range(1, 5):
        wait_window(rig, atom, round_)
        for _ in range(3):
            submit(rig, open_slot(rig)["slot_id"], SHORT_EVENT)
    finish(rig)
    # A resumed batch (new service, ledger reopened from the log) replays round 4.
    resumed = make_rig(tmp_path)
    assert resumed.ledger.used(f"A|{BOOK}|{atom}") == 12
    holder: list[RoundResult] = []
    request = build_request(resumed, atom, 4)
    thread = threading.Thread(target=lambda: holder.append(resumed.service.propose_round(request)))
    thread.start()
    wait_for(lambda: (resumed.service.state()["window"] or {}).get("round") == 4)
    error = api(resumed, "POST", "/a1/api/slots/open", expect=409)["error"]["code"]
    assert error in ("E_SLOT_CAP", "E_SLOT_REUSED")
    resumed.clock.advance(120_000)
    thread.join(timeout=10)
    assert holder and holder[0].records == ()
    assert len(resumed.slots()) == 12
    assert {r.reason for r in resumed.refusals()} <= {"slot_cap", "slot_reused"}
    assert len(resumed.refusals()) == 3


def test_feedback_payload_holds_no_other_method_fields(tmp_path):
    rig = make_rig(tmp_path)
    sentinel_book = OTHER_BOOKS[0]
    atom = ATOMS[0]
    # Other methods' slots share the run's ledger; their recipes carry a sentinel pitch set.
    sentinel = {"total_ms": 900, "pitches": [-5, 5, -5], "rhythm_weights": [4, 1, 4],
                "gaps_ms": [60, 20], "amplitudes": [0.6, 0.6, 0.6]}  # fmt: skip
    for method, book in zip((Method.A2, Method.A3), OTHER_BOOKS, strict=True):
        cap = f"A|{book}|{atom}"
        ticket = rig.ledger.reserve(
            cap, proposal_slot_id(book, atom, 1, 1), study=Study.A, method=method
        )
        rig.ledger.consume(
            SlotRecord(
                run_id=RUN,
                study=Study.A,
                method=method,
                slot_id=ticket.slot_id,
                profile=PROFILE,
                atom_id=atom,
                slot=1,
                slot_index=ticket.slot_index,
                outcome=SlotOutcome.VALID,
                t_open_ms=0,
                t_ms=1,
                batch_id=CONFIG.batch_id,
                book_id=book,
                round=1,
                seed_key=f"{method.value}|{CONFIG.batch_id}|{atom}|1|1",
                seed=7,
                prompt_sha256="0" * 64 if method is Method.A3 else None,
                raw_output="SENTINEL-OTHER-METHOD",
                recipe=Recipe.from_dict(sentinel).to_dict(),
                recipe_sha256=Recipe.from_dict(sentinel).sha256(),
                pcm_sha256="5" * 64,
                a2=A2Detail("uniform", None) if method is Method.A2 else None,
            )  # fmt: skip
        )
    start_rounds(rig, [atom], rounds=2)
    wait_window(rig, atom, 1)
    for recipe in (VALID[0], SHORT_EVENT, OUT_OF_DOMAIN):
        submit(rig, open_slot(rig)["slot_id"], recipe)
    wait_window(rig, atom, 2)
    payloads = {
        name: rig.client.get(path).json()
        for name, path in (
            ("feedback", "/a1/api/feedback"),
            ("book", "/a1/api/book"),
            ("state", "/a1/api/state"),
        )
    }
    feedback = payloads["feedback"]
    assert [c["outcome"] for c in feedback["candidates"]] == [
        "valid",
        "event_too_short",
        "out_of_domain",
    ]
    assert (
        feedback["candidates"][0]["ratings"]
        == [{"association": 5, "distinguishability": 4, "comfort": "acceptable"}] * 3
    )
    assert feedback["incumbent_slot_id"] == proposal_slot_id(BOOK, atom, 1, 1)
    assert feedback["incumbent_score"] == "9/2"
    for name, payload in payloads.items():
        text = json.dumps(payload)
        keys = set(walk_keys(payload))
        assert not keys & METHOD_REVEALING_FIELDS, (name, keys & METHOD_REVEALING_FIELDS)
        for marker in ("SENTINEL", "[-5, 5, -5]", *OTHER_BOOKS, "A2", "A3", "seed"):
            assert marker not in text, (name, marker)
    assert sentinel_book not in json.dumps(payloads)
    rig.clock.advance(200_000)
    finish(rig)


def test_requests_naming_another_book_are_refused(tmp_path):
    rig = make_rig(tmp_path)
    atom = ATOMS[0]
    good = build_request(rig, atom, 1)
    foreign = CandidateFeedback(
        proposal_slot_id(OTHER_BOOKS[0], atom, 1, 1), 1, 1, 1, Recipe.from_dict(VALID[1]),
        SlotOutcome.VALID, (), (RaterScore(7, 7, "acceptable"),), True, Fraction(7),
    )  # fmt: skip
    from dataclasses import replace

    bad_requests = [
        replace(good, feedback=replace(good.feedback, candidates=(foreign,))),
        replace(good, feedback=replace(good.feedback, book_id=OTHER_BOOKS[0])),
        replace(good, book=replace(good.book, book_id=OTHER_BOOKS[0])),
        replace(good, book=replace(good.book, profile=Profile.P3)),
        replace(good, method=Method.A2),
        replace(good, semantic_label=None),
        replace(good, batch_id="DEMO-PRACTICE"),
    ]
    for request in bad_requests:
        with pytest.raises(ValueError):
            rig.service.propose_round(request)
    assert rig.service.state()["window"] is None
    assert not (rig.logs / "slots.jsonl").exists() or rig.slots() == []


def test_out_of_domain_and_malformed_submissions(tmp_path):
    rig = make_rig(tmp_path)
    atom = ATOMS[0]
    start_rounds(rig, [atom], rounds=2)
    wait_window(rig, atom, 1)
    first = open_slot(rig)
    # Transport errors consume nothing: the slot stays open.
    path = f"/a1/api/slots/{first['slot_id']}/submit"
    for body, code in (
        (b"{not json", "E_BAD_REQUEST"),
        (b"[1, 2]", "E_BAD_REQUEST"),
        (b'{"recipe": NaN}', "E_BAD_REQUEST"),
        (b'{"other": 1}', "E_BAD_REQUEST"),
        (b'{"recipe": "' + b"x" * 70_000 + b'"}', "E_TOO_LARGE"),
    ):
        response = rig.client.post(path, content=body)
        assert response.json()["error"]["code"] == code
    assert rig.service.state()["window"]["slots"][0]["state"] == "open"
    outcomes = [
        submit(rig, first["slot_id"], OUT_OF_DOMAIN)["outcome"],
        submit(rig, open_slot(rig)["slot_id"], {**VALID[0], "extra": 1})["outcome"],
        submit(rig, open_slot(rig)["slot_id"], '{"total_ms": 600,')["outcome"],
    ]
    assert outcomes == ["out_of_domain", "schema_violation", "invalid_json"]
    wait_window(rig, atom, 2)
    outcomes = [
        submit(rig, open_slot(rig)["slot_id"], json.dumps(VALID[0]))["outcome"],
        submit(rig, open_slot(rig)["slot_id"], [1, 2, 3])["outcome"],
        submit(rig, open_slot(rig)["slot_id"], {**VALID[0], "amplitudes": [0.7, 0.8, 0.8]})[
            "outcome"
        ],
    ]
    assert outcomes == ["valid", "schema_violation", "out_of_domain"]
    finish(rig)
    records = rig.slots()
    assert len(records) == 6
    assert records[2].raw_output == '{"total_ms": 600,'
    assert [len(p.slot_id) > 0 for p in rig.plays()] == []  # nothing was played
    assert all(r.file_sha256 is None for r in records if r.recipe is None)


def test_window_end_closes_unopened_slots_and_truncates_an_open_one(tmp_path):
    rig = make_rig(tmp_path, timed=True)
    atom = ATOMS[0]
    start_rounds(rig, [atom], rounds=1, window_ms=60_000)
    wait_window(rig, atom, 1)
    rig.clock.advance(30_000)
    opened = open_slot(rig)
    assert opened["deadline_ms"] - opened["t_open_ms"] == 30_000  # the window ends first
    rig.clock.advance(30_000)
    finish(rig)
    records = rig.rounds[0].records
    assert [r.outcome for r in records] == [SlotOutcome.TIMEOUT] * 3
    assert records[0].t_ms - records[0].t_open_ms == 30_000
    assert all(r.design_ms == 0 for r in records[1:])
    assert [r.slot_index for r in records] == [1, 2, 3]


def test_design_time_and_familiarization_are_logged(tmp_path):
    rig = make_rig(tmp_path, hide_run_id=True)
    assert rig.service.run_id is None
    atom = ATOMS[0]
    # Familiarization before the first round (no run ID known yet: buffered).
    fam = api(rig, "POST", "/a1/api/activity", {"kind": "familiarization_start"})
    assert fam["familiarization"]["running"]
    rig.clock.advance(90_000)
    assert api(rig, "POST", "/a1/api/activity", {"kind": "familiarization_end"})[
        "familiarization"
    ] == {"running": False, "total_ms": 90_000}
    api(rig, "POST", "/a1/api/activity", {"kind": "familiarization_start"})
    rig.clock.advance(10_000)
    assert rig.service.familiarization_ms == 100_000
    start_rounds(rig, [atom], rounds=1)
    wait_window(rig, atom, 1)
    opened = open_slot(rig)  # ends the running familiarization
    rig.clock.advance(4_000)
    api(rig, "POST", "/a1/api/activity", {"kind": "idle"})
    rig.clock.advance(10_000)
    api(rig, "POST", "/a1/api/activity", {"kind": "active"})
    rig.clock.advance(3_000)
    submit(rig, opened["slot_id"], VALID[0])
    with pytest.raises(A1Error):
        rig.service.activity("dance")
    assert api(rig, "POST", "/a1/api/activity", {"kind": 3}, expect=400)
    second = open_slot(rig)
    assert (
        api(rig, "POST", "/a1/api/activity", {"kind": "familiarization_start"}, expect=409)[
            "error"
        ]["code"]
        == "E_SLOT_OPEN"
    )
    rig.clock.advance(40_000)
    wait_for(lambda: len(rig.ledger.records()) == 2)
    rig.clock.advance(120_000)
    finish(rig)
    first_rec, second_rec, third_rec = rig.slots()
    assert first_rec.latency_ms == 17_000 and first_rec.design_ms == 7_000
    assert second_rec.slot_id == second["slot_id"] and second_rec.design_ms == 40_000
    assert third_rec.design_ms == 0
    events = rig.timing()
    assert all(e.run_id == RUN and e.component == "a1" and e.actor_id == "D1" for e in events)
    fam_ends = [e for e in events if e.event == "familiarization_end"]
    assert [e.duration_ms for e in fam_ends] == [90_000, 10_000]
    assert fam_ends[1].detail == "slot opened"
    active_ends = [e for e in events if e.event == "design_active_end"]
    assert [e.duration_ms for e in active_ends] == [4_000, 3_000, 40_000]
    assert all(e.book_id == BOOK and e.detail.startswith("slot ") for e in active_ends)
    assert rig.service.familiarization_ms == 100_000
    assert rig.service.run_id == RUN and not rig.service.practice


def test_a_used_slot_id_is_never_reopened(tmp_path):
    rig = make_rig(tmp_path)
    atom = ATOMS[0]
    first_id = proposal_slot_id(BOOK, atom, 1, 1)
    # A slot consumed before a restart (same run) is in the ledger already.
    ticket = rig.ledger.reserve(f"A|{BOOK}|{atom}", first_id, study=Study.A, method=Method.A1)
    rig.ledger.consume(
        SlotRecord(
            run_id=RUN,
            study=Study.A,
            method=Method.A1,
            slot_id=first_id,
            profile=PROFILE,
            atom_id=atom,
            slot=1,
            slot_index=ticket.slot_index,
            outcome=SlotOutcome.TIMEOUT,
            t_open_ms=0,
            t_ms=40_000,
            batch_id=CONFIG.batch_id,
            book_id=BOOK,
            round=1,
            designer_id="D1",
        )  # fmt: skip
    )
    start_rounds(rig, [atom], rounds=1)
    wait_window(rig, atom, 1)
    refused = api(rig, "POST", "/a1/api/slots/open", expect=409)["error"]["code"]
    assert refused in ("E_SLOT_REUSED", "E_SLOT_CAP")
    assert rig.service.state()["window"]["slots"][0]["state"] == "refused"
    second = open_slot(rig)
    assert second["slot"] == 2
    submit(rig, second["slot_id"], VALID[0])
    submit(rig, open_slot(rig)["slot_id"], VALID[0])
    finish(rig)
    assert [r.slot for r in rig.rounds[0].records] == [2, 3]
    assert [r.reason for r in rig.refusals()] == ["slot_reused"]


def test_practice_mode_is_labelled_and_stored_apart(tmp_path):
    meanings = demo_practice_meanings()
    assert meanings.demo and all(t.startswith("Practice") for t in meanings.meanings.values())
    clock = ManualClock()
    session = open_practice_session(
        tmp_path / "runs", "DEMO-PRACTICE-D1-01", designer_id="D1", meanings=meanings,
        clock=clock, kind=RunKind.DEMO, station="S9", ledger_factory=make_ledger,
    )  # fmt: skip
    manifest = RunManifest.read(session.layout.manifest)
    assert (manifest.purpose, manifest.kind) == ("practice", RunKind.DEMO)
    assert manifest.meanings_sha256 == meanings.sha256()
    client = TestClient(create_a1_app(session.service))
    results: list[Any] = []
    thread = threading.Thread(
        target=lambda: results.append(session.run(("K-a1", "K-r1"), rounds=2, between_rounds_s=0.0))
    )
    thread.start()
    rig_like = Rig(
        tmp_path, clock, session.ledger, session.service, client, session.layout.logs_dir
    )
    window = wait_window(rig_like, "K-a1", 1)
    assert window["label"] is None
    assert window["meaning"] == meanings.text(PRACTICE_LABELS["K-a1"])
    state = client.get("/a1/api/state").json()
    assert state["mode"] == "practice"
    opened = client.post("/a1/api/slots/open").json()
    res = client.post(f"/a1/api/slots/{opened['slot_id']}/submit", json={"recipe": VALID[0]}).json()
    assert client.get(res["audio"]["url"]).status_code == 200
    clock.advance(120_000)
    wait_window(rig_like, "K-a1", 2)
    fb = client.get("/a1/api/feedback").json()
    assert fb["practice"] and not fb["ratings_shown"]
    assert fb["candidates"][0]["ratings"] == [] and fb["candidates"][0]["eligible"] is None
    clock.advance(120_000)
    wait_window(rig_like, "K-r1", 1)
    book = client.get("/a1/api/book").json()
    assert [a["atom_id"] for a in book["committed"]] == ["K-a1"]  # practice reference
    assert book["committed"][0]["label"] is None
    clock.advance(120_000)
    wait_window(rig_like, "K-r1", 2)
    clock.advance(120_000)
    thread.join(timeout=10)
    assert results and len(results[0]) == 4
    slots = read_records(session.layout.log("slot"), SlotRecord)
    assert len(slots) == 12 and all(r.practice for r in slots)
    assert {r.batch_id for r in slots} == {"DEMO-PRACTICE"}
    assert {r.book_id for r in slots} == {"DEMO-BK-PRACTICE"}
    (play,) = read_records(session.layout.log("play"), PlayEvent)
    assert play.context == "a1_practice" and play.station == "S9"
    events = [e.event for e in read_records(session.layout.log("timing"), TimingEvent)]
    assert events[0] == "session_start" and events[-1] == "session_end"
    assert not (session.layout.root / "store").exists()


def test_practice_and_study_never_mix(tmp_path):
    assert is_practice_batch("PRACTICE") and is_practice_batch("DEMO-PRACTICE-01")
    assert not is_practice_batch(CONFIG.batch_id) and not is_practice_batch("A-P01")
    practice = make_rig(tmp_path / "p", practice=True)
    with pytest.raises(ValueError, match="practice"):
        practice.service.propose_round(build_request(practice, ATOMS[0], 1))
    study = make_rig(tmp_path / "s")
    with pytest.raises(ValueError, match="practice"):
        study.service.propose_round(
            build_request(study, ATOMS[0], 1, batch_id="DEMO-PRACTICE", book_id="DEMO-BK-PRACTICE")
        )
    # Real practice runs are restricted: refused inside a git work tree.
    with pytest.raises(RunPolicyError):
        create_run_dir(REPO / "generation" / "out", "PRACTICE-D1-01", RunKind.PRACTICE)
    with pytest.raises(ValueError):
        open_practice_session(
            tmp_path / "x", "DEMO-X-01", designer_id="D1", meanings=demo_practice_meanings(),
            clock=ManualClock(), kind=RunKind.PILOT,
        )  # fmt: skip
    assert practice_atoms("K-a1, Q-r2") == ("K-a1", "Q-r2")
    for bad in ("", "K-a1,K-a1", "K-a9"):
        with pytest.raises(ValueError):
            practice_atoms(bad)


def test_practice_cli_serves_the_practice_session(tmp_path):
    seen: dict[str, Any] = {}

    def fake_serve(app, host, port, session):
        client = TestClient(app)
        seen["state"] = client.get("/a1/api/state").json()
        seen["host"], seen["port"] = host, port
        seen["run"] = session.layout.run_id
        seen["manifest"] = RunManifest.read(session.layout.manifest)

    code = _a1_cli.main(
        [
            "practice", "--runs-root", str(tmp_path), "--run-id", "DEMO-PRACTICE-CLI",
            "--designer", "D2", "--atoms", "K-a1", "--rounds", "1", "--demo",
            "--meanings", str(examples_path("demo-practice-meanings")),
            "--port", "9999", "--station", "S8",
        ],
        serve=fake_serve,
        ledger_factory=make_ledger,
    )  # fmt: skip
    assert code == 0
    assert seen["state"]["mode"] == "practice" and seen["port"] == 9999
    assert seen["manifest"].books[0].designer_id == "D2"
    assert (
        _a1_cli.build_parser()
        .parse_args(["practice", "--runs-root", "r", "--run-id", "x", "--designer", "D1"])
        .atoms
        == "K-a1,K-r1"
    )


def test_service_guards(tmp_path):
    rig = make_rig(tmp_path, token_factory=lambda: "short")
    atom = ATOMS[0]
    start_rounds(rig, [atom], rounds=1)
    wait_window(rig, atom, 1)
    with pytest.raises(RuntimeError):
        rig.service.propose_round(build_request(rig, atom, 1))
    with pytest.raises(RuntimeError, match="token"):
        rig.service.submit(rig.service.open_slot()["slot_id"], VALID[0])
    rig.clock.advance(120_000)
    finish(rig)
    other_run = build_request(rig, atom, 1, run_id="DEMO-OTHER-RUN")
    with pytest.raises(ValueError, match="run"):
        rig.service.propose_round(other_run)
    with pytest.raises(ValueError, match="book"):
        rig.service.propose_round(build_request(rig, atom, 1, book_id=OTHER_BOOKS[0]))


# ---------------------------------------------------------------------------
# 48-slot session: plays joined to the ledger


SLOT_SCRIPT = (
    "valid",
    "valid_replay",
    "invalid",
    "timeout",
    "valid_no_play",
    "edit",
    "out_of_domain",
    "valid",
    "skip",
)


def run_scripted_session(rig: Rig, atoms: Sequence[str], script: Sequence[str]) -> Counter[str]:
    """Drive atoms x 4 rounds x 3 slots through the HTTP API with a scripted designer."""
    actions = iter(script * 100)
    counts: Counter[str] = Counter()
    start_rounds(rig, atoms, rounds=4)
    for atom_no, atom in enumerate(atoms):
        recipe = VALID[atom_no % len(VALID)]
        for round_ in range(1, 5):
            wait_window(rig, atom, round_)
            for _slot in range(3):
                action = next(actions)
                counts[action] += 1
                if action == "skip":
                    rig.clock.advance(120_000)  # window ends: the rest time out unopened
                    break
                opened = open_slot(rig)
                rig.clock.advance(2_000)
                if action == "timeout":
                    advance_to_close(rig, opened["slot_id"], SLOT_CAP_MS)
                    continue
                body = {"invalid": SHORT_EVENT, "out_of_domain": OUT_OF_DOMAIN}.get(action, recipe)
                result = submit(rig, opened["slot_id"], body)
                if result["audio"] and action != "valid_no_play":
                    api(rig, "GET", result["audio"]["url"])
                    if action == "valid_replay":
                        api(rig, "GET", result["audio"]["url"], expect=410)
                if action == "edit":
                    submit(rig, opened["slot_id"], VALID[(atom_no + 1) % 4], expect=409)
    finish(rig)
    return counts


def join_plays(slots: Sequence[SlotRecord], plays: Sequence[PlayEvent]) -> dict[str, Any]:
    by_slot = {r.slot_id: r for r in slots}
    played = [p for p in plays if p.result == "played"]
    orphans = [p for p in played if p.slot_id not in by_slot]
    mismatched = [
        p
        for p in played
        if p.slot_id in by_slot
        and (
            by_slot[p.slot_id].outcome is not SlotOutcome.VALID
            or by_slot[p.slot_id].pcm_sha256 != p.pcm_sha256
            or by_slot[p.slot_id].file_sha256 != p.asset_id
        )
    ]
    per_slot = Counter(p.slot_id for p in played)
    return {
        "slots": len(slots),
        "plays_played": len(played),
        "plays_refused": sum(p.result == "refused" for p in plays),
        "plays_without_consumed_slot": len(orphans),
        "plays_not_matching_slot": len(mismatched),
        "slots_played_more_than_once": sum(n > 1 for n in per_slot.values()),
        "audio_kind_message": sum(p.audio_kind == "message" for p in plays),
        "outcomes": dict(sorted(Counter(r.outcome.value for r in slots).items())),
    }


def test_48_slot_session_has_no_play_without_a_consumed_slot(tmp_path):
    rig = make_rig(tmp_path, run_id="DEMO-A1-SESSION-48", station="S9")
    counts = run_scripted_session(rig, ATOMS, SLOT_SCRIPT)
    slots, plays = rig.slots(), rig.plays()
    summary = join_plays(slots, plays)
    assert summary["slots"] == 48 == len({r.slot_id for r in slots})
    assert summary["plays_without_consumed_slot"] == 0
    assert summary["plays_not_matching_slot"] == 0
    assert summary["slots_played_more_than_once"] == 0
    assert summary["audio_kind_message"] == 0
    assert summary["plays_played"] > 0 and summary["plays_refused"] > 0
    for atom in ATOMS:
        cap = f"A|{BOOK}|{atom}"
        assert rig.ledger.used(cap) == 12
        assert sorted(r.slot_index for r in slots if r.atom_id == atom) == list(range(1, 13))
    assert all(r.run_id == "DEMO-A1-SESSION-48" and r.method is Method.A1 for r in slots)
    assert {r.reason for r in rig.refusals()} == {"slot_closed"}
    assert len(rig.refusals()) == counts["edit"]
    summary["designer_actions"] = dict(sorted(counts.items()))
    summary["refusals"] = len(rig.refusals())
    out = os.environ.get("AV_GENERATION_CI_OUT") or (
        str(REPO / "generation" / "out" / "ci") if os.environ.get("CI") else None
    )
    if out:
        target = Path(out) / "a1-session" / "DEMO-A1-SESSION-48"
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(rig.logs, target / "logs")
        (target / "join-summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )


ACTIONS = st.sampled_from(
    (
        "valid",
        "valid_replay",
        "invalid",
        "timeout",
        "valid_no_play",
        "edit",
        "out_of_domain",
        "skip",
    )
)


@settings(
    max_examples=12,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(script=st.lists(ACTIONS, min_size=1, max_size=14))
def test_every_play_joins_one_consumed_valid_slot(script):
    with tempfile.TemporaryDirectory() as tmp:
        rig = make_rig(Path(tmp))
        run_scripted_session(rig, ATOMS[:1], script)
        slots, plays = rig.slots(), rig.plays()
        summary = join_plays(slots, plays)
        assert summary["slots"] == 12
        assert summary["plays_without_consumed_slot"] == 0
        assert summary["plays_not_matching_slot"] == 0
        assert summary["slots_played_more_than_once"] == 0
        # Every refused play names a slot that was played or whose token expired.
        assert all(p.slot_id in {r.slot_id for r in slots} for p in plays)
        for rec in slots:
            assert rec.t_ms - rec.t_open_ms <= SLOT_CAP_MS


# ---------------------------------------------------------------------------
# Kiosk configuration and docs


def test_kiosk_policy_locks_the_browser():
    policy = json.loads((REPO / "generation" / "kiosk" / "a1-chrome-policy.json").read_text())
    assert policy["DeveloperToolsAvailability"] == 2
    assert policy["URLBlocklist"] == ["*"]
    assert all(u.startswith("http://") for u in policy["URLAllowlist"])
    assert policy["DownloadRestrictions"] == 3
    assert policy["IncognitoModeAvailability"] == 1
    for doc in ("a1-interface.md", "a1-operating-guide.md"):
        text = (REPO / "generation" / "docs" / doc).read_text(encoding="utf-8")
        assert "kiosk" in text.lower()


# ---------------------------------------------------------------------------
# Browser tests (CI job "browser"; skipped locally without Chromium)


def wait_js(page, function: str, timeout: float = 10.0) -> None:
    """Poll a JS predicate (the page's CSP forbids `wait_for_function`'s string eval)."""
    wait_for(lambda: bool(page.evaluate(function)), timeout)


FIELDSETS_DISABLED = (
    "() => [...document.querySelectorAll('#recipe-form fieldset')].every((f) => f.disabled)"
)


def _serve_round(serve_app, rig: Rig, atom: str, rounds: int = 1) -> str:
    base = serve_app(create_a1_app(rig.service))
    start_rounds(rig, [atom], rounds=rounds)
    wait_window(rig, atom, 1)
    return base


@pytest.mark.browser
def test_browser_full_round(serve_app, chromium, tmp_path):
    """One full round in the kiosk page: open, set controls, submit and play, timeout."""
    rig = make_rig(tmp_path, clock=ManualClock())
    atom = ATOMS[0]
    base = _serve_round(serve_app, rig, atom, rounds=2)
    video_dir = None
    if os.environ.get("CI"):
        video_dir = REPO / "generation" / "out" / "ci" / "a1-browser"
        video_dir.mkdir(parents=True, exist_ok=True)
    try:
        context = chromium.new_context(
            viewport={"width": 1366, "height": 900},
            record_video_dir=str(video_dir) if video_dir else None,
        )
    except Exception:  # pragma: no cover - video needs Playwright's ffmpeg
        context = chromium.new_context(viewport={"width": 1366, "height": 900})
    page = context.new_page()
    try:
        page.goto(base + "/a1/")
        page.wait_for_selector("#recipe-form fieldset")
        assert page.text_content("#meaning") == MEANINGS.text(CONFIG.labels[atom])
        assert page.is_disabled("#submit") and page.is_disabled("input[name='total_ms-0']")
        assert page.evaluate(FIELDSETS_DISABLED)
        assert page.locator("audio, video").count() == 0
        # Slot 1: set every control to VALID[1], submit and play once.
        page.click("#open-slot")
        page.wait_for_selector("#recipe-form fieldset:not([disabled])")
        target = VALID[1]
        page.check(f"input[name='total_ms-0'][value='{target['total_ms']}']")
        for i, p in enumerate(target["pitches"]):
            page.fill(f"input[name='pitches-{i}']", str(p))
        for field_name in ("rhythm_weights", "gaps_ms", "amplitudes"):
            for i, v in enumerate(target[field_name]):
                page.check(f"input[name='{field_name}-{i}'][value='{v}']")
        assert "Schematic" in page.text_content("#preview-caption")
        page.click("#submit")
        wait_js(page, "() => window.a1Debug.plays === 1")
        page.wait_for_selector(".slot[data-slot='1'][data-state='closed']")
        rec = rig.slots()[0]
        assert rec.outcome is SlotOutcome.VALID
        assert rec.recipe == Recipe.from_dict(target).to_dict()
        (play,) = rig.plays()
        assert play.result == "played" and play.slot_id == rec.slot_id
        # Slot 2: an inadmissible recipe; its reason is shown, nothing plays.
        page.click("#open-slot")
        page.wait_for_selector("#recipe-form fieldset:not([disabled])")
        for field_name in ("total_ms", "gaps_ms"):
            for i, v in enumerate(SHORT_EVENT[field_name] if field_name != "total_ms" else [450]):
                page.check(f"input[name='{field_name}-{i}'][value='{v}']")
        for i, v in enumerate(SHORT_EVENT["rhythm_weights"]):
            page.check(f"input[name='rhythm_weights-{i}'][value='{v}']")
        page.click("#submit")
        page.wait_for_selector(".slot[data-slot='2'][data-state='closed']")
        assert "event_too_short" in page.text_content("#status")
        # Slot 3: opened, then the server timer closes it.
        page.click("#open-slot")
        page.wait_for_selector("#recipe-form fieldset:not([disabled])")
        wait_js(page, "() => !!document.querySelector('#countdown-value').textContent.match(/s$/)")
        rig.clock.advance(SLOT_CAP_MS)
        wait_for(lambda: len(rig.slots()) == 3)
        assert rig.slots()[2].outcome is SlotOutcome.TIMEOUT
        # Round 2: the feedback view shows this book's candidates and ratings only.
        wait_window(rig, atom, 2)
        wait_js(page, "() => document.querySelector('#round').textContent.startsWith('2')")
        page.wait_for_selector("#feedback-table")
        feedback_text = page.text_content("#feedback")
        assert "incumbent" in feedback_text and "5/4/a" in feedback_text
        assert "timeout" in feedback_text and "event_too_short" in feedback_text
        for other in OTHER_BOOKS:
            assert other not in page.content()
        assert page.evaluate("window.a1Debug.plays") == 1
        assert len(rig.plays()) == 1
        if video_dir:
            page.screenshot(path=str(video_dir / "a1-round-feedback.png"), full_page=True)
    finally:
        context.close()
    rig.clock.advance(120_000)
    finish(rig)


@pytest.mark.browser
def test_browser_controls_only_allow_domain_values(serve_app, browser_page, tmp_path):
    rig = make_rig(tmp_path)
    atom = ATOMS[0]
    base = _serve_round(serve_app, rig, atom)
    page = browser_page
    page.goto(base + "/a1/")
    page.wait_for_selector("#recipe-form fieldset")
    page.click("#open-slot")
    page.wait_for_selector("#recipe-form fieldset:not([disabled])")
    radios = page.evaluate(
        """() => {
            const out = {};
            for (const el of document.querySelectorAll('#recipe-form input[type=radio]')) {
                (out[el.name] = out[el.name] || []).push(Number(el.value));
            }
            return out;
        }"""
    )
    assert radios["total_ms-0"] == [450, 600, 750, 900]
    assert all(radios[f"rhythm_weights-{i}"] == [1, 2, 3, 4] for i in range(3))
    assert all(radios[f"gaps_ms-{i}"] == [20, 40, 60] for i in range(2))
    assert all(radios[f"amplitudes-{i}"] == [0.6, 0.8, 1.0] for i in range(3))
    assert page.locator("#recipe-form input:not([type=radio]):not([type=range])").count() == 0
    # Range inputs clamp anything scripted or keyed to -6..+6 in integer steps.
    for raw, expected in (("99", "6"), ("-40", "-6"), ("2.6", "3"), ("abc", "0")):
        page.eval_on_selector(
            "input[name='pitches-0']",
            "(el, v) => { el.value = v; el.dispatchEvent(new Event('input')); }",
            raw,
        )
        assert page.input_value("input[name='pitches-0']") == expected
    page.focus("input[name='pitches-1']")
    for _ in range(20):
        page.keyboard.press("ArrowRight")
    assert page.input_value("input[name='pitches-1']") == "6"
    page.eval_on_selector(
        "input[name='pitches-0']",
        "(el) => { el.value = '3'; el.dispatchEvent(new Event('input')); }",
    )
    page.click("#submit")
    page.wait_for_selector(".slot[data-slot='1'][data-state='closed']")
    rec = rig.slots()[0]
    assert rec.recipe is not None and rec.recipe["pitches"][:2] == [3, 6]
    assert rec.outcome is not SlotOutcome.OUT_OF_DOMAIN
    rig.clock.advance(120_000)
    finish(rig)


@pytest.mark.browser
def test_browser_practice_banner(serve_app, browser_page, tmp_path):
    clock = ManualClock()
    session = open_practice_session(
        tmp_path / "runs", "DEMO-PRACTICE-UI-01", designer_id="D3",
        meanings=demo_practice_meanings(), clock=clock, kind=RunKind.DEMO,
        ledger_factory=make_ledger,
    )  # fmt: skip
    base = serve_app(create_a1_app(session.service))
    thread = threading.Thread(
        target=session.run, args=(("Q-a1",),), kwargs={"rounds": 1}, daemon=True
    )
    thread.start()
    page = browser_page
    page.goto(base + "/a1/")
    page.wait_for_selector("#practice-banner:not([hidden])")
    assert "PRACTICE MODE" in page.text_content("#practice-banner")
    wait_js(page, "() => document.querySelector('#meaning').textContent.startsWith('Practice')")
    clock.advance(120_000)
    thread.join(timeout=10)
