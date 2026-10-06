"""A1 hand-designer slot service and web interface (#19).

The orchestrator (#20) calls `A1SlotService.propose_round(request)`, which opens the
round's proposal window for the designer's book and blocks until its three slots have
closed. The designer works in a kiosk browser on the app from `create_a1_app(service)`.

Slot rules (Study A protocol §3.3, §3.4):

- Opening a slot calls `SlotLedger.reserve` (#17) before anything is designed or heard,
  so a 13th slot request for an atom is refused and logged (`SlotRefusal`). The slot
  gets a server-side 40-s timer (`SLOT_CAP_MS`), cut short only by the end of the
  round's window (`RoundRequest.window_end_ms`). Slots never reopen.
- Submit validates and renders the recipe (`av_sound.validate`) and consumes the slot
  with exactly one `SlotRecord` (`SlotLedger.consume`), whatever the outcome. A valid
  recipe gets one single-use audio token: the first request for it serves the WAV and
  logs a `play` event (`a1_preview`, or `a1_practice` in practice mode) with the
  waveform hashes; any later request is refused and logged (`E_TOKEN_USED`). A preview
  is therefore a submitted slot, and no sound is served outside a logged slot.
- An open slot without a submission closes as `timeout` at its deadline (the record's
  `t_ms` is the deadline). Slots never opened close as `timeout` when the window ends.
- A second submission for a closed slot (an edit) is refused and logged
  (`SlotRefusal`, reason `slot_closed`).
- The designer sees only their own book: the atom's meaning (`meanings`), the round's
  feedback for this book (`RoundRequest.feedback`: recipes, technical status, ratings,
  incumbent) and the book's committed recipes as parameter tables, never as audio.
  The service never reads other books' records, and refuses a request whose feedback
  or book state names another book.
- Active design time per slot (`SlotRecord.design_ms`) and interface familiarization
  time are logged as `timing` events (`design_active_*`, `familiarization_*`).

Practice mode (`practice=True`, see `_a1_practice`) uses non-study meanings, writes
`practice=True` records to a separate practice run and never reaches a book.

Web stack: FastAPI + plain HTML/CSS/JS from `av_generation/web/a1/` (no npm build, no
CDN, no third-party JS). Request and response bodies: `generation/docs/a1-interface.md`.

Route contract (paths are fixed here so the dry-run bot designer (#22) can drive them):
"""

from __future__ import annotations

import json
import re
import secrets
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Final, Literal

from av_sound.recipe import Recipe
from av_sound.validate import validate
from av_sound.wav import file_sha256, wav_bytes

from av_generation.clock import Clock, utc_text
from av_generation.constants import SLOT_CAP_MS, SLOTS_PER_ATOM, SLOTS_PER_ROUND
from av_generation.domain import FIELD_VALUES
from av_generation.ids import (
    Method,
    Study,
    check_book,
    parse_proposal_slot_id,
    proposal_slot_id,
)
from av_generation.ledger import SlotCapExceeded, SlotLedger, SlotReused, SlotTicket
from av_generation.meanings import MeaningSet
from av_generation.outcomes import SlotOutcome, outcome_from_validation
from av_generation.proposers import CandidateFeedback, RoundRequest, RoundResult
from av_generation.records import (
    PlayEvent,
    RecordWriter,
    SlotRecord,
    SlotRefusal,
    TimingEvent,
    cap_key,
)
from av_generation.rundir import LOG_FILES

ROUTES: Final[dict[str, str]] = {
    "page": "GET /a1/",
    "state": "GET /a1/api/state",
    "open_slot": "POST /a1/api/slots/open",
    "submit": "POST /a1/api/slots/{slot_id}/submit",
    "audio": "GET /a1/api/audio/{token}",
    "feedback": "GET /a1/api/feedback",
    "book": "GET /a1/api/book",
    "activity": "POST /a1/api/activity",
}
"""A1 HTTP routes (bodies: `generation/docs/a1-interface.md`, docs/interfaces/generation.md)."""

API_VERSION: Final = 1
"""Version of the JSON bodies served by the A1 app."""
AUDIO_TTL_MS: Final = 60_000
"""A valid slot's audio token expires this long (run clock) after the submit."""
POLL_INTERVAL_S: Final = 0.01
"""Real seconds between timer checks while a window is open (timeouts are exact anyway:
a submit after the deadline is refused and the record's `t_ms` is the deadline)."""
MAX_RAW_OUTPUT: Final = 65_536
"""`SlotRecord.raw_output` limit (schema); the app refuses larger bodies first."""
PRACTICE_TOKEN: Final = "PRACTICE"
"""A hyphen-separated token of every practice batch ID (`PRACTICE`, `DEMO-PRACTICE-01`)."""
ACTIVITY_KINDS: Final[tuple[str, ...]] = (
    "active",
    "idle",
    "familiarization_start",
    "familiarization_end",
)
"""`POST /a1/api/activity` kinds."""
TOKEN_RE: Final = re.compile(r"[A-Za-z0-9_-]{8,128}")
"""Audio token format (also the `play` record's `token_id` pattern)."""
COMPONENT: Final = "a1"
"""`TimingEvent.component` of every A1 event."""

SlotState = Literal["unopened", "open", "closed", "refused"]


class A1Error(Exception):
    """A refused A1 request; `.code` (`E_*`) and `.status` (HTTP) go to the client."""

    def __init__(self, code: str, status: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message

    def to_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message}}


def is_practice_batch(batch_id: str) -> bool:
    """True when `batch_id` names a practice batch (a `PRACTICE` token)."""
    return PRACTICE_TOKEN in batch_id.upper().split("-")


def recipe_text(value: object) -> str:
    """What a submission stores as `raw_output`: a string as given, anything else as
    compact JSON with sorted keys (ASCII)."""
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def fraction_text(value: Fraction | None) -> str | None:
    """`p/q` (or an integer) for an exact score."""
    return None if value is None else str(value)


@dataclass(slots=True)
class _Slot:
    window: _Window
    slot: int
    slot_id: str
    state: SlotState = "unopened"
    ticket: SlotTicket | None = None
    deadline_ms: int | None = None
    record: SlotRecord | None = None
    active_ms: int = 0
    active_since: int | None = None
    token: str | None = None


@dataclass(slots=True)
class _Window:
    request: RoundRequest
    cap_key: str
    meaning: str
    slots: list[_Slot] = field(default_factory=list)
    closed_ms: int | None = None

    @property
    def done(self) -> bool:
        return all(s.state in ("closed", "refused") for s in self.slots)

    @property
    def is_open(self) -> bool:
        return self.closed_ms is None


@dataclass(slots=True)
class _Token:
    token: str
    slot_id: str
    file_sha256: str
    pcm_sha256: str
    expires_ms: int
    wav: bytes | None
    used: bool = False


class A1SlotService:
    """`proposers.RoundProposer` for A1 plus the state behind the web app (#19).

    One service serves one designer and one book (study mode) or one practice run
    (`practice=True`). The API methods (`state`, `open_slot`, `submit`, `audio`,
    `feedback`, `book`, `activity`) raise `A1Error` for refused requests; the app maps
    them to HTTP. Thread-safe: `propose_round` blocks in the orchestrator's thread while
    the web server calls the API methods from its worker threads.

    Optional keywords (all backwards compatible with the skeleton signature):
    `refusals` (default: `slot-refusals.jsonl` next to the `plays` log), `station`
    (the kiosk's station ID for `play` records), `run_id` (default: the ledger's
    `run_id` attribute, else the first request's), `token_factory` (default
    `secrets.token_urlsafe(24)`), `audio_ttl_ms`, `poll_interval_s`.
    """

    method = Method.A1

    def __init__(
        self,
        ledger: SlotLedger,
        plays: RecordWriter,
        timing: RecordWriter,
        *,
        clock: Clock,
        designer_id: str,
        meanings: MeaningSet,
        practice: bool = False,
        refusals: RecordWriter | None = None,
        station: str | None = None,
        run_id: str | None = None,
        token_factory: Callable[[], str] | None = None,
        audio_ttl_ms: int = AUDIO_TTL_MS,
        poll_interval_s: float = POLL_INTERVAL_S,
    ) -> None:
        self._ledger = ledger
        self._plays = plays
        self._timing = timing
        self._clock = clock
        self._designer_id = designer_id
        self._meanings = meanings
        self._practice = bool(practice)
        if refusals is None:
            refusals = RecordWriter(Path(plays.path).parent / Path(LOG_FILES["slot_refusal"]).name)
        self._refusals = refusals
        self._station = station
        ledger_run = getattr(ledger, "run_id", None)
        self._run_id: str | None = run_id or (ledger_run if isinstance(ledger_run, str) else None)
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(24))
        self._audio_ttl_ms = int(audio_ttl_ms)
        self._poll_s = float(poll_interval_s)
        self._cond = threading.Condition(threading.RLock())
        self._window: _Window | None = None
        self._book_id: str | None = None
        self._slots: dict[str, _Slot] = {}
        self._tokens: dict[str, _Token] = {}
        self._active = True
        self._fam_since: int | None = None
        self._fam_total_ms = 0
        self._pending_timing: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ properties

    @property
    def practice(self) -> bool:
        return self._practice

    @property
    def designer_id(self) -> str:
        return self._designer_id

    @property
    def run_id(self) -> str | None:
        return self._run_id

    @property
    def familiarization_ms(self) -> int:
        """Familiarization time logged so far (a running interval counts up to now)."""
        with self._cond:
            running = 0 if self._fam_since is None else self._clock.now_ms() - self._fam_since
            return self._fam_total_ms + running

    # ------------------------------------------------------------------ proposer

    def propose_round(self, request: RoundRequest) -> RoundResult:
        """Open the round's window, block until its slots have closed, return the records.

        Raises `ValueError` for a request this designer may not see (another method, a
        practice/study mismatch, another book, or feedback naming another book) and
        `RuntimeError` if a window is already open. A slot the ledger refuses (a resumed
        atom already at its cap) has no record, so the result can hold fewer than three.
        """
        with self._cond:
            meaning = self._check_request(request)
            if self._window is not None and self._window.is_open:
                raise RuntimeError("an A1 proposal window is already open")
            if self._run_id is None:
                self._run_id = request.run_id
            elif self._run_id != request.run_id:
                raise ValueError(f"request run {request.run_id!r} is not {self._run_id!r}")
            self._book_id = request.book_id
            window = _Window(
                request=request,
                cap_key=cap_key(Study.A, request.atom_id, book_id=request.book_id),
                meaning=meaning,
            )
            window.slots = [
                _Slot(
                    window, k, proposal_slot_id(request.book_id, request.atom_id, request.round, k)
                )
                for k in range(1, SLOTS_PER_ROUND + 1)
            ]
            self._window = window
            self._flush_pending_timing()
            self._cond.notify_all()
            while True:
                self._expire_locked(self._clock.now_ms())
                if window.done:
                    break
                self._cond.wait(self._poll_s)
            records = tuple(s.record for s in window.slots if s.record is not None)
        return RoundResult(Method.A1, request.book_id, request.atom_id, request.round, records)

    def tick(self) -> None:
        """Close overdue slots now (also done before every API call and by the window loop)."""
        with self._cond:
            self._expire_locked(self._clock.now_ms())

    # ------------------------------------------------------------------ API

    def state(self) -> dict[str, Any]:
        """`GET /a1/api/state`: mode, timers, the current window and its slots."""
        with self._cond:
            now = self._clock.now_ms()
            self._expire_locked(now)
            w = self._window
            window: dict[str, Any] | None = None
            if w is not None:
                req = w.request
                window = {
                    "open": w.is_open,
                    "atom_id": req.atom_id,
                    "round": req.round,
                    "label": None if self._practice else req.semantic_label,
                    "meaning": w.meaning,
                    "profile": req.profile.value,
                    "f0_hz": req.profile.f0_hz,
                    "window_end_ms": req.window_end_ms,
                    "window_remaining_ms": max(0, req.window_end_ms - now) if w.is_open else 0,
                    "atom_slots_used": self._ledger.used(w.cap_key),
                    "atom_slots_cap": SLOTS_PER_ATOM,
                    "slots": [self._slot_view(s, now) for s in w.slots],
                }
            return {
                "api_version": API_VERSION,
                "mode": "practice" if self._practice else "study",
                "server_ms": now,
                "slot_cap_ms": SLOT_CAP_MS,
                "slots_per_round": SLOTS_PER_ROUND,
                "domain": {name: list(values) for name, values in FIELD_VALUES.items()},
                "active": self._active,
                "familiarization": self._fam_view(now),
                "window": window,
            }

    def open_slot(self) -> dict[str, Any]:
        """`POST /a1/api/slots/open`: reserve the next slot and start its 40-s timer."""
        with self._cond:
            now = self._clock.now_ms()
            self._expire_locked(now)
            w = self._window
            if w is None or not w.is_open:
                self._refuse_closed_atom(w, now)
                raise A1Error("E_NO_WINDOW", 409, "no proposal window is open; wait for the round")
            if any(s.state == "open" for s in w.slots):
                raise A1Error("E_SLOT_OPEN", 409, "a slot is already open; submit it first")
            nxt = next((s for s in w.slots if s.state == "unopened"), None)
            if nxt is None:  # pragma: no cover - a full window closes in the same step
                raise A1Error("E_NO_WINDOW", 409, "this round's slots are used")
            ticket = self._reserve(nxt)
            self._end_familiarization(ticket.t_open_ms, "slot opened")
            nxt.state = "open"
            nxt.ticket = ticket
            nxt.deadline_ms = min(ticket.t_open_ms + SLOT_CAP_MS, w.request.window_end_ms)
            self._active = True
            self._start_active(nxt, ticket.t_open_ms)
            self._slots[nxt.slot_id] = nxt
            self._cond.notify_all()
            return self._slot_view(nxt, now)

    def submit(self, slot_id: str, recipe: object) -> dict[str, Any]:
        """`POST /a1/api/slots/{slot_id}/submit`: validate, render, consume, hand out audio.

        `recipe` is the submitted JSON value (an object, or JSON text). Any value consumes
        the slot; a refused edit of a closed slot raises `E_SLOT_CLOSED` (logged).
        """
        with self._cond:
            now = self._clock.now_ms()
            self._expire_locked(now)
            slot = self._slots.get(slot_id)
            if slot is None:
                raise A1Error("E_UNKNOWN_SLOT", 404, f"slot {slot_id!r} was never opened")
            w = slot.window
            if slot.state != "open":
                outcome = None if slot.record is None else slot.record.outcome.value
                self._refusals.append(
                    SlotRefusal(
                        run_id=w.request.run_id,
                        study=Study.A,
                        method=Method.A1,
                        cap_key=w.cap_key,
                        reason="slot_closed",
                        requested=slot_id,
                        used=self._ledger.used(w.cap_key),
                        t_ms=now,
                        detail=f"slot already closed ({outcome}); a submitted recipe "
                        "cannot be edited",
                    ).check()
                )
                raise A1Error(
                    "E_SLOT_CLOSED", 409, f"slot {slot_id} is closed ({outcome}); it never reopens"
                )
            req = w.request
            assert slot.ticket is not None
            raw = recipe_text(recipe)[:MAX_RAW_OUTPUT]
            result = validate(
                recipe if isinstance(recipe, str | Mapping) else raw,
                req.profile,
                req.book.references(),
                threshold=req.book.threshold,
            )
            outcome_code = outcome_from_validation(result)
            rendered = result.rendered
            pcm = result.pcm_sha256
            wav = wav_bytes(rendered) if rendered is not None and pcm is not None else None
            fsha = None if wav is None else file_sha256(wav)
            record = SlotRecord(
                run_id=req.run_id,
                study=Study.A,
                method=Method.A1,
                slot_id=slot.slot_id,
                profile=req.profile,
                atom_id=req.atom_id,
                slot=slot.slot,
                slot_index=slot.ticket.slot_index,
                outcome=outcome_code,
                t_open_ms=slot.ticket.t_open_ms,
                t_ms=now,
                batch_id=req.batch_id,
                book_id=req.book_id,
                round=req.round,
                practice=self._practice,
                designer_id=self._designer_id,
                latency_ms=now - slot.ticket.t_open_ms,
                design_ms=self._stop_active(slot, now),
                raw_output=raw,
                recipe=None if result.recipe is None else result.recipe.to_dict(),
                recipe_sha256=None if result.recipe is None else result.recipe.sha256(),
                validator_codes=result.codes,
                validator_messages=tuple(m[:400] for m in result.messages),
                pcm_sha256=pcm,
                file_sha256=fsha,
            )
            self._close(slot, record)
            audio: dict[str, Any] | None = None
            if outcome_code is SlotOutcome.VALID and wav is not None:
                assert pcm is not None and fsha is not None
                token = self._new_token()
                self._tokens[token] = _Token(
                    token, slot.slot_id, fsha, pcm, now + self._audio_ttl_ms, wav
                )
                slot.token = token
                audio = self._audio_view(self._tokens[token])
            return {
                "slot_id": slot.slot_id,
                "slot": slot.slot,
                "round": req.round,
                "outcome": outcome_code.value,
                "valid": outcome_code is SlotOutcome.VALID,
                "validator_codes": list(record.validator_codes),
                "validator_messages": list(record.validator_messages),
                "recipe": record.recipe,
                "t_open_ms": record.t_open_ms,
                "t_ms": record.t_ms,
                "audio": audio,
            }

    def audio(self, token: str) -> bytes:
        """`GET /a1/api/audio/{token}`: the WAV of a valid slot, once; logs every request
        for a known token (`played` or `refused`)."""
        with self._cond:
            now = self._clock.now_ms()
            self._expire_locked(now)
            entry = self._tokens.get(token) if TOKEN_RE.fullmatch(token) else None
            if entry is None:
                raise A1Error("E_UNKNOWN_TOKEN", 404, "unknown audio token")
            if entry.used or now >= entry.expires_ms or entry.wav is None:
                code = "E_TOKEN_USED" if entry.used else "E_TOKEN_EXPIRED"
                entry.wav = None
                self._log_play(entry, now, "refused", code)
                raise A1Error(
                    code,
                    410,
                    "this slot's audio was already played"
                    if entry.used
                    else "this slot's audio token has expired",
                )
            entry.used = True
            data, entry.wav = entry.wav, None
            self._log_play(entry, now, "played", None)
            return data

    def feedback(self) -> dict[str, Any]:
        """`GET /a1/api/feedback`: this book's candidates, technical status, ratings and
        incumbent for the current atom (closed rounds), plus this round's own slots."""
        with self._cond:
            now = self._clock.now_ms()
            self._expire_locked(now)
            w = self._window
            if w is None:
                return {"available": False, "practice": self._practice}
            fb = w.request.feedback
            return {
                "available": True,
                "practice": self._practice,
                "ratings_shown": not self._practice,
                "atom_id": fb.atom_id,
                "round": w.request.round,
                "rounds_closed": fb.rounds_closed,
                "candidates": [
                    self._candidate_view(c, fb.incumbent_slot_id) for c in fb.candidates
                ],
                "incumbent_slot_id": fb.incumbent_slot_id,
                "incumbent_score": fraction_text(fb.incumbent_score),
                "current_round": [self._own_slot_view(s) for s in w.slots if s.record is not None],
            }

    def book(self) -> dict[str, Any]:
        """`GET /a1/api/book`: the book's committed recipes as parameter tables (no audio)."""
        with self._cond:
            w = self._window
            if w is None:
                return {"available": False, "practice": self._practice, "committed": []}
            state = w.request.book
            committed = []
            for atom in state.committed:
                label = atom.semantic_label
                committed.append(
                    {
                        "atom_id": atom.atom_id,
                        "commit_index": atom.commit_index,
                        "label": None if self._practice else label,
                        "meaning": None if label is None else self._meanings.text(label),
                        "recipe": atom.recipe.to_dict(),
                    }
                )
            return {
                "available": True,
                "practice": self._practice,
                "profile": state.profile.value,
                "f0_hz": state.profile.f0_hz,
                "threshold": state.threshold,
                "playback": False,
                "committed": committed,
            }

    def activity(self, kind: str) -> dict[str, Any]:
        """`POST /a1/api/activity`: `active` / `idle` (design time) and
        `familiarization_start` / `familiarization_end`."""
        if kind not in ACTIVITY_KINDS:
            raise A1Error("E_BAD_ACTIVITY", 400, f"kind must be one of {list(ACTIVITY_KINDS)}")
        with self._cond:
            now = self._clock.now_ms()
            self._expire_locked(now)
            open_slot = self._open_slot()
            if kind == "active" and not self._active:
                self._active = True
                if open_slot is not None:
                    self._start_active(open_slot, now)
            elif kind == "idle" and self._active:
                self._active = False
                if open_slot is not None:
                    self._stop_active(open_slot, now)
            elif kind == "familiarization_start" and self._fam_since is None:
                if open_slot is not None:
                    raise A1Error("E_SLOT_OPEN", 409, "familiarization cannot start in a slot")
                self._fam_since = now
                self._log_timing("familiarization_start", now)
            elif kind == "familiarization_end":
                self._end_familiarization(now, None)
            return {"active": self._active, "familiarization": self._fam_view(now)}

    # ------------------------------------------------------------------ internals

    def _check_request(self, request: RoundRequest) -> str:
        if request.method is not Method.A1:
            raise ValueError(f"A1 service got a request for {request.method}")
        if is_practice_batch(request.batch_id) != self._practice:
            raise ValueError(
                "practice services take practice batches only, and study services never do "
                f"(batch {request.batch_id!r})"
            )
        check_book(request.book_id)
        if self._book_id is not None and request.book_id != self._book_id:
            raise ValueError(f"this designer's book is {self._book_id}, not {request.book_id}")
        if request.book.book_id != request.book_id:
            raise ValueError("the book state belongs to another book")
        if request.book.profile is not request.profile:
            raise ValueError("the book state has another profile")
        fb = request.feedback
        if fb.book_id != request.book_id or fb.atom_id != request.atom_id:
            raise ValueError("the feedback belongs to another book or atom")
        for cand in fb.candidates:
            parsed = parse_proposal_slot_id(cand.slot_id)
            if parsed.book_id != request.book_id or parsed.atom_id != request.atom_id:
                raise ValueError(f"the feedback holds a foreign candidate {cand.slot_id!r}")
        if request.semantic_label is None:
            raise ValueError("A1 requests carry the atom's semantic label")
        return self._meanings.text(request.semantic_label)

    def _expire_locked(self, now: int) -> None:
        w = self._window
        if w is not None and w.is_open:
            for slot in w.slots:
                if (
                    slot.state == "open"
                    and slot.deadline_ms is not None
                    and now >= slot.deadline_ms
                ):
                    self._timeout(slot, slot.deadline_ms)
            if now >= w.request.window_end_ms:
                for slot in w.slots:
                    if slot.state == "unopened":
                        try:
                            ticket = self._reserve(slot)
                        except A1Error:
                            continue
                        slot.ticket = ticket
                        slot.state = "open"
                        self._slots[slot.slot_id] = slot
                        self._timeout(slot, ticket.t_open_ms)
        for entry in self._tokens.values():
            if entry.wav is not None and now >= entry.expires_ms:
                entry.wav = None

    def _reserve(self, slot: _Slot) -> SlotTicket:
        w = slot.window
        try:
            return self._ledger.reserve(w.cap_key, slot.slot_id, study=Study.A, method=Method.A1)
        except SlotCapExceeded as err:
            slot.state = "refused"
            self._close_window_if_done(w, self._clock.now_ms())
            raise A1Error("E_SLOT_CAP", 409, f"the atom's {SLOTS_PER_ATOM} slots are used") from err
        except SlotReused as err:
            slot.state = "refused"
            self._close_window_if_done(w, self._clock.now_ms())
            raise A1Error("E_SLOT_REUSED", 409, f"slot {slot.slot_id} was already used") from err

    def _refuse_closed_atom(self, w: _Window | None, now: int) -> None:
        """A request after the atom's 12 slots: refused and logged as a 13th slot."""
        if w is None:
            return
        used = self._ledger.used(w.cap_key)
        if used < SLOTS_PER_ATOM:
            return
        req = w.request
        self._refusals.append(
            SlotRefusal(
                run_id=req.run_id,
                study=Study.A,
                method=Method.A1,
                cap_key=w.cap_key,
                reason="slot_cap",
                requested=f"{req.book_id}.{req.atom_id}.slot{used + 1}",
                used=used,
                t_ms=now,
                detail=f"slot request {used + 1} for one atom (cap {SLOTS_PER_ATOM})",
            ).check()
        )
        raise A1Error("E_SLOT_CAP", 409, f"the atom's {SLOTS_PER_ATOM} slots are used")

    def _timeout(self, slot: _Slot, t_close: int) -> None:
        w = slot.window
        req = w.request
        assert slot.ticket is not None
        record = SlotRecord(
            run_id=req.run_id,
            study=Study.A,
            method=Method.A1,
            slot_id=slot.slot_id,
            profile=req.profile,
            atom_id=req.atom_id,
            slot=slot.slot,
            slot_index=slot.ticket.slot_index,
            outcome=SlotOutcome.TIMEOUT,
            t_open_ms=slot.ticket.t_open_ms,
            t_ms=max(t_close, slot.ticket.t_open_ms),
            batch_id=req.batch_id,
            book_id=req.book_id,
            round=req.round,
            practice=self._practice,
            designer_id=self._designer_id,
            design_ms=self._stop_active(slot, max(t_close, slot.ticket.t_open_ms)),
        )
        self._close(slot, record)

    def _close(self, slot: _Slot, record: SlotRecord) -> None:
        self._ledger.consume(record)
        slot.record = record
        slot.state = "closed"
        self._close_window_if_done(slot.window, record.t_ms)
        self._cond.notify_all()

    def _close_window_if_done(self, w: _Window, t_ms: int) -> None:
        if w.is_open and w.done:
            w.closed_ms = t_ms
            self._cond.notify_all()

    def _open_slot(self) -> _Slot | None:
        w = self._window
        if w is None or not w.is_open:
            return None
        return next((s for s in w.slots if s.state == "open"), None)

    def _start_active(self, slot: _Slot, now: int) -> None:
        if self._active and slot.active_since is None:
            slot.active_since = now
            self._log_timing("design_active_start", now, slot=slot)

    def _stop_active(self, slot: _Slot, now: int) -> int:
        if slot.active_since is not None:
            span = max(0, now - slot.active_since)
            slot.active_ms += span
            slot.active_since = None
            self._log_timing("design_active_end", now, slot=slot, duration_ms=span)
        return slot.active_ms

    def _end_familiarization(self, now: int, why: str | None) -> None:
        if self._fam_since is None:
            return
        span = max(0, now - self._fam_since)
        self._fam_total_ms += span
        self._fam_since = None
        self._log_timing("familiarization_end", now, duration_ms=span, detail=why)

    def _fam_view(self, now: int) -> dict[str, Any]:
        running = self._fam_since is not None
        current = 0 if self._fam_since is None else now - self._fam_since
        return {"running": running, "total_ms": self._fam_total_ms + current}

    def _new_token(self) -> str:
        for _ in range(8):
            token = self._token_factory()
            if TOKEN_RE.fullmatch(token) and token not in self._tokens:
                return token
        raise RuntimeError("token factory keeps returning unusable tokens")

    def _audio_view(self, entry: _Token) -> dict[str, Any]:
        return {
            "token": entry.token,
            "url": f"/a1/api/audio/{entry.token}",
            "expires_ms": entry.expires_ms,
        }

    def _slot_view(self, slot: _Slot, now: int) -> dict[str, Any]:
        record = slot.record
        entry = None if slot.token is None else self._tokens.get(slot.token)
        playable = entry is not None and not entry.used and entry.wav is not None
        return {
            "slot": slot.slot,
            "slot_id": slot.slot_id,
            "state": slot.state,
            "t_open_ms": None if slot.ticket is None else slot.ticket.t_open_ms,
            "deadline_ms": slot.deadline_ms,
            "remaining_ms": max(0, slot.deadline_ms - now)
            if slot.state == "open" and slot.deadline_ms is not None
            else None,
            "t_close_ms": None if record is None else record.t_ms,
            "outcome": None if record is None else record.outcome.value,
            "audio": self._audio_view(entry) if playable and entry is not None else None,
        }

    def _own_slot_view(self, slot: _Slot) -> dict[str, Any]:
        record = slot.record
        assert record is not None
        return {
            "slot_id": slot.slot_id,
            "round": slot.window.request.round,
            "slot": slot.slot,
            "slot_index": record.slot_index,
            "recipe": record.recipe,
            "outcome": record.outcome.value,
            "validator_codes": list(record.validator_codes),
        }

    @staticmethod
    def _candidate_view(cand: CandidateFeedback, incumbent: str | None) -> dict[str, Any]:
        recipe: Recipe | None = cand.recipe
        return {
            "slot_id": cand.slot_id,
            "round": cand.round,
            "slot": cand.slot,
            "slot_index": cand.slot_index,
            "recipe": None if recipe is None else recipe.to_dict(),
            "outcome": cand.outcome.value,
            "validator_codes": list(cand.validator_codes),
            "ratings": [
                {
                    "association": r.association,
                    "distinguishability": r.distinguishability,
                    "comfort": r.comfort,
                }
                for r in cand.ratings
            ],
            "eligible": cand.eligible,
            "score": fraction_text(cand.score),
            "score_value": None if cand.score is None else float(cand.score),
            "incumbent": cand.slot_id == incumbent,
        }

    def _log_play(
        self, entry: _Token, now: int, result: Literal["played", "refused"], reason: str | None
    ) -> None:
        slot = self._slots[entry.slot_id]
        self._plays.append(
            PlayEvent(
                run_id=slot.window.request.run_id,
                context="a1_practice" if self._practice else "a1_preview",
                audio_kind="atom",
                asset_id=entry.file_sha256,
                result=result,
                t_ms=now,
                pcm_sha256=entry.pcm_sha256,
                reason=reason,
                station=self._station,
                actor_id=self._designer_id,
                slot_id=entry.slot_id,
                token_id=entry.token,
            ).check()
        )

    def _log_timing(
        self,
        event: str,
        now: int,
        *,
        slot: _Slot | None = None,
        duration_ms: int | None = None,
        detail: str | None = None,
    ) -> None:
        fields: dict[str, Any] = {
            "event": event,
            "t_ms": now,
            "wall_utc": utc_text(self._clock.utc_now()),
            "component": COMPONENT,
            "actor_id": self._designer_id,
            "station": self._station,
            "duration_ms": duration_ms,
            "detail": detail,
        }
        if slot is not None:
            req = slot.window.request
            fields.update(
                batch_id=req.batch_id,
                book_id=req.book_id,
                atom_id=req.atom_id,
                round=req.round,
                profile=req.profile.value,
                detail=f"slot {slot.slot_id}",
            )
        if self._run_id is None:
            self._pending_timing.append(fields)
            return
        self._timing.append(TimingEvent(run_id=self._run_id, **fields).check())

    def _flush_pending_timing(self) -> None:
        if self._run_id is None:  # pragma: no cover - called after the run ID is known
            return
        pending, self._pending_timing = self._pending_timing, []
        for fields in pending:
            self._timing.append(TimingEvent(run_id=self._run_id, **fields).check())


def create_a1_app(service: A1SlotService) -> Any:  # noqa: ANN401 - FastAPI app
    """The FastAPI app serving `ROUTES` (and `/a1/static/*`) for one service (#19)."""
    from av_generation._a1_app import build_app

    return build_app(service)
