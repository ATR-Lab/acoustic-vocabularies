"""The panel session host of a batch (#20; private to `orchestrator`).

`PanelHost` implements `panel_session.PanelSessionHost` for the panel server (#21). The
orchestrator schedules the rating window on the run clock and calls `publish_*` and
`close_slot`; the panel server calls the station callbacks from its own threads. The
host is the only writer of the panel logs: `rating` records (one per seat at each slot's
lock), `play` records and the panel `timing` events. Everything a station can learn goes
through `PanelSlot`, `PanelEvent` and `PanelSnapshot`, which hold no book ID, alias,
proposal-slot ID, method or seed (masking by construction).

Rating rules (codes of `rater_protocol.RATING_ERROR_CODES`):

| Situation | Code |
| --- | --- |
| rater/station pair is not a seat of the panel | `E_UNKNOWN_RATER` |
| rating-slot ID never scheduled in this run | `E_UNKNOWN_SLOT` |
| slot already locked (received at or after start + 20 s) | `E_SLOT_CLOSED` |
| placeholder slot (invalid candidate) | `E_PLACEHOLDER` |
| received before the controls unlock | `E_LOCKED` |
| distinguishability given on a first-atom slot | `E_FIRST_ATOM` |
| distinguishability missing on another slot, or a value outside 1-7 | `E_PROTOCOL` |
| second rating of the same rater for the slot | `E_DUPLICATE_RATING` |
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

from av_generation.clock import Clock
from av_generation.config import BatchConfig, RaterSeat
from av_generation.constants import (
    COMFORT_VALUES,
    FIRST_ATOM_DISTINGUISHABILITY,
    RATING_MAX,
    RATING_MIN,
    RATING_SLOT_MS,
)
from av_generation.panel_session import (
    AssetRef,
    PanelEvent,
    PanelRefused,
    PanelSlot,
    PanelSnapshot,
    PlayReport,
    RatingAck,
    RatingSubmission,
)
from av_generation.records import PlayEvent, RatingRecord, RecordWriter

TimingSink = Callable[..., None]
"""`sink(event, **fields)`: writes one `TimingEvent` (the orchestrator's writer)."""


@dataclass(frozen=True, slots=True)
class RatingSlotPlan:
    """One scheduled rating slot, server side (#20 internal; never sent to a station).

    `panel` is the part the panel server sees; the rest identifies the candidate."""

    panel: PanelSlot
    batch_id: str
    book_id: str
    alias: str
    """The book's panel alias (operator console)."""
    slot_id: str
    atom_id: str
    round: int

    @property
    def rating_slot_id(self) -> str:
        return self.panel.rating_slot_id

    @property
    def lock_ms(self) -> int:
        """Run-clock time the slot locks and its records are written (start + 20 s)."""
        return self.panel.start_ms + RATING_SLOT_MS


_NOT_PRINTABLE: Final = re.compile(r"[^ -~]")


def printable(text: str) -> str:
    """Free text for a log `detail`: printable ASCII (others become `?`), at most 400."""
    return _NOT_PRINTABLE.sub("?", text)[:400]


def _in_scale(value: int | None) -> bool:
    return (
        isinstance(value, int) and not isinstance(value, bool) and RATING_MIN <= value <= RATING_MAX
    )


class PanelHost:
    """`panel_session.PanelSessionHost` of one batch run (see the module docstring)."""

    def __init__(
        self,
        *,
        run_id: str,
        config: BatchConfig,
        clock: Clock,
        ratings: RecordWriter,
        plays: RecordWriter,
        timing: TimingSink,
        on_withdrawal: Callable[[str, str, str], None],
    ) -> None:
        self.run_id = run_id
        self._config = config
        self._clock = clock
        self._ratings = ratings
        self._plays = plays
        self._timing = timing
        self._on_withdrawal = on_withdrawal
        self._seats: tuple[RaterSeat, ...] = tuple(config.panel.raters)
        self._cond = threading.Condition()
        self._events: list[PanelEvent] = []
        self._state: Literal["waiting", "slot", "paused", "between_atoms", "ended"] = "waiting"
        self._preload: tuple[AssetRef, ...] = ()
        self._assets: dict[str, bytes] = {}
        self._plans: dict[str, RatingSlotPlan] = {}
        self._published: list[str] = []
        self._closed: set[str] = set()
        self._accepted: dict[str, dict[str, RatingSubmission]] = {}
        self._onsets: dict[tuple[str, str, str], int] = {}
        self._reconnected: dict[str, set[str]] = {}
        self._joined: set[str] = set()
        self._connected: set[str] = set()

    # ------------------------------------------------------------ helpers

    def _seat(self, rater_id: str, station: str) -> RaterSeat | None:
        for seat in self._seats:
            if seat.rater_id == rater_id and seat.station == station:
                return seat
        return None

    def _require_seat(self, rater_id: str, station: str) -> RaterSeat:
        seat = self._seat(rater_id, station)
        if seat is None:
            raise PanelRefused("E_UNKNOWN_RATER", f"{rater_id} at {station} is not a seat")
        return seat

    def _emit(self, kind: Any, **fields: Any) -> PanelEvent:  # noqa: ANN401 - event kind literal
        with self._cond:
            event = PanelEvent(len(self._events) + 1, kind, self._clock.now_ms(), **fields)
            self._events.append(event)
            self._cond.notify_all()
            return event

    def _panel_timing(self, event: str, seat: RaterSeat, detail: str | None = None) -> None:
        self._timing(
            event,
            component="panel",
            station=seat.station,
            actor_id=seat.rater_id,
            detail=None if detail is None else printable(detail),
        )

    # ------------------------------------------------------------ orchestrator side

    def schedule(self, plans: Sequence[RatingSlotPlan]) -> None:
        """Register a round's slots (submissions and plays are checked against them)."""
        with self._cond:
            for plan in plans:
                self._plans[plan.rating_slot_id] = plan

    def publish_preload(self, assets: Mapping[str, bytes], refs: Sequence[AssetRef]) -> None:
        """Make the next round's WAVs available and announce them."""
        with self._cond:
            self._assets = dict(assets)
            self._preload = tuple(refs)
        self._emit("preload", assets=tuple(refs))

    def publish_slot(self, plan: RatingSlotPlan) -> PanelEvent:
        with self._cond:
            self._plans[plan.rating_slot_id] = plan
            self._published.append(plan.rating_slot_id)
            self._state = "slot"
        return self._emit("slot", slot=plan.panel)

    def publish(self, kind: Literal["pause", "resume", "end"], reason: str | None = None) -> None:
        with self._cond:
            if kind == "pause":
                self._state = "between_atoms" if reason == "between_atoms" else "paused"
            elif kind == "resume":
                self._state = "waiting"
            else:
                self._state = "ended"
        self._emit(kind, reason=reason)

    @property
    def state(self) -> str:
        with self._cond:
            return self._state

    def connected(self) -> tuple[tuple[str, bool], ...]:
        """(station, connected) in seat order (operator console)."""
        with self._cond:
            return tuple((s.station, s.rater_id in self._connected) for s in self._seats)

    def close_slot(
        self, plan: RatingSlotPlan, existing: Mapping[str, RatingRecord] | None = None
    ) -> list[RatingRecord]:
        """Lock the slot and write one rating record per seat (skipping seats that already
        have one in `existing`, e.g. after a resume)."""
        rsid = plan.rating_slot_id
        with self._cond:
            self._closed.add(rsid)
            submissions = self._accepted.pop(rsid, {})
            reconnected = self._reconnected.pop(rsid, set())
            onsets = {
                (rater, role): ms
                for (slot, rater, role), ms in self._onsets.items()
                if slot == rsid
            }
            for key in [k for k in self._onsets if k[0] == rsid]:
                del self._onsets[key]
            if self._published and self._published[-1] == rsid:
                self._state = "waiting"
        now = self._clock.now_ms()
        slot = plan.panel
        written: list[RatingRecord] = []
        for seat in self._seats:
            if existing and seat.rater_id in existing:
                continue
            sub = submissions.get(seat.rater_id)
            base: dict[str, Any] = {
                "run_id": self.run_id,
                "batch_id": plan.batch_id,
                "book_id": plan.book_id,
                "atom_id": plan.atom_id,
                "round": plan.round,
                "position": slot.position,
                "rating_slot_id": rsid,
                "slot_id": plan.slot_id,
                "rater_id": seat.rater_id,
                "station": seat.station,
                "rater_kind": seat.kind,
                "placeholder": slot.placeholder,
                "first_atom": slot.first_atom,
                "reconnected": seat.rater_id in reconnected,
                "slot_start_ms": slot.start_ms,
                "t_ms": now,
                "candidate_onset_ms": onsets.get((seat.rater_id, "candidate")),
                "reference_onset_ms": onsets.get((seat.rater_id, "reference")),
            }
            if slot.placeholder:
                record = RatingRecord(
                    **base,
                    association=None,
                    distinguishability=None,
                    distinguishability_by_rule=False,
                    comfort=None,
                    missing=False,
                )
            elif sub is None:
                record = RatingRecord(
                    **base,
                    association=None,
                    distinguishability=None,
                    distinguishability_by_rule=False,
                    comfort=None,
                    missing=True,
                    unlock_ms=slot.unlock_offset_ms,
                )
            else:
                record = RatingRecord(
                    **base,
                    association=sub.association,
                    distinguishability=(
                        FIRST_ATOM_DISTINGUISHABILITY if slot.first_atom else sub.distinguishability
                    ),
                    distinguishability_by_rule=slot.first_atom,
                    comfort=sub.comfort,
                    missing=False,
                    unlock_ms=slot.unlock_offset_ms,
                    rt_ms=max(0, sub.rt_ms),
                )
            self._ratings.append(record)
            written.append(record)
        return written

    # ------------------------------------------------------------ PanelSessionHost

    def seats(self) -> Sequence[RaterSeat]:
        return self._seats

    def wait_events(self, after_seq: int, timeout_s: float) -> tuple[PanelEvent, ...]:
        with self._cond:
            self._cond.wait_for(lambda: len(self._events) > after_seq, timeout=timeout_s)
            return tuple(self._events[max(0, after_seq) :])

    def snapshot(self) -> PanelSnapshot:
        now = self._clock.now_ms()
        with self._cond:
            open_slots = [self._plans[r] for r in self._published if r not in self._closed]
            current = None
            for plan in open_slots:
                if plan.panel.start_ms <= now < plan.lock_ms:
                    current = plan
            if current is None and open_slots:
                current = open_slots[-1]
            state = "slot" if current is not None else self._state
            if state == "slot" and current is None:
                state = "waiting"
            return PanelSnapshot(
                len(self._events),
                state,
                None if current is None else current.panel,
                self._preload,
            )

    def asset_bytes(self, asset_id: str) -> bytes:
        with self._cond:
            return self._assets[asset_id]

    def station_joined(self, rater_id: str, station: str, kind: str) -> None:
        seat = self._require_seat(rater_id, station)
        if seat.kind != kind:
            raise PanelRefused("E_UNKNOWN_RATER", f"{rater_id} is a {seat.kind} seat")
        now = self._clock.now_ms()
        with self._cond:
            again = rater_id in self._joined
            self._joined.add(rater_id)
            self._connected.add(rater_id)
            if again:
                for rsid in self._published:
                    plan = self._plans[rsid]
                    if rsid not in self._closed and plan.panel.start_ms <= now < plan.lock_ms:
                        self._reconnected.setdefault(rsid, set()).add(rater_id)
        self._panel_timing("station_reconnect" if again else "station_connect", seat)

    def station_left(self, rater_id: str, station: str) -> None:
        seat = self._require_seat(rater_id, station)
        with self._cond:
            self._connected.discard(rater_id)
        self._panel_timing("station_disconnect", seat)

    def asset_ready(self, rater_id: str, station: str, asset_id: str, ok: bool) -> None:
        seat = self._require_seat(rater_id, station)
        self._panel_timing("asset_ready", seat, f"{asset_id} {'ok' if ok else 'failed'}")

    def clock_synced(self, rater_id: str, station: str, offset_ms: float, rtt_ms: float) -> None:
        seat = self._require_seat(rater_id, station)
        self._panel_timing("clock_sync", seat, f"offset_ms={offset_ms:.1f} rtt_ms={rtt_ms:.1f}")

    def report_play(self, report: PlayReport) -> None:
        seat = self._require_seat(report.rater_id, report.station)
        with self._cond:
            plan = self._plans.get(report.rating_slot_id)
            if plan is None:
                raise PanelRefused("E_UNKNOWN_SLOT", report.rating_slot_id)
            asset = plan.panel.candidate if report.role == "candidate" else plan.panel.reference
            if asset is None or asset.asset_id != report.asset_id:
                raise PanelRefused("E_PROTOCOL", f"{report.asset_id} is not this slot's asset")
            self._onsets[(report.rating_slot_id, seat.rater_id, report.role)] = max(
                0, report.onset_ms - plan.panel.start_ms
            )
        self._plays.append(
            PlayEvent(
                run_id=self.run_id,
                context="rating_candidate" if report.role == "candidate" else "rating_reference",
                audio_kind="atom",
                asset_id=asset.asset_id,
                result="played",
                t_ms=self._clock.now_ms(),
                pcm_sha256=asset.pcm_sha256,
                station=seat.station,
                actor_id=seat.rater_id,
                slot_id=plan.slot_id if report.role == "candidate" else None,
                rating_slot_id=report.rating_slot_id,
                scheduled_ms=report.scheduled_ms,
                onset_ms=report.onset_ms,
            )
        )

    def submit_rating(self, submission: RatingSubmission) -> RatingAck:
        sub = submission
        if self._seat(sub.rater_id, sub.station) is None:
            return RatingAck(False, "E_UNKNOWN_RATER")
        with self._cond:
            plan = self._plans.get(sub.rating_slot_id)
            if plan is None:
                return RatingAck(False, "E_UNKNOWN_SLOT")
            slot = plan.panel
            if sub.rating_slot_id in self._closed or sub.received_ms >= plan.lock_ms:
                return RatingAck(False, "E_SLOT_CLOSED")
            if slot.placeholder:
                return RatingAck(False, "E_PLACEHOLDER")
            if sub.received_ms < slot.start_ms + slot.unlock_offset_ms:
                return RatingAck(False, "E_LOCKED")
            if slot.first_atom and sub.distinguishability is not None:
                return RatingAck(False, "E_FIRST_ATOM")
            if (
                not _in_scale(sub.association)
                or sub.comfort not in COMFORT_VALUES
                or (not slot.first_atom and not _in_scale(sub.distinguishability))
            ):
                return RatingAck(False, "E_PROTOCOL")
            done = self._accepted.setdefault(sub.rating_slot_id, {})
            if sub.rater_id in done:
                return RatingAck(False, "E_DUPLICATE_RATING")
            done[sub.rater_id] = sub
        return RatingAck(True)

    def report_withdrawal(self, rater_id: str, station: str, reason: str) -> None:
        seat = self._require_seat(rater_id, station)
        self._panel_timing("rater_withdrawal", seat, reason or None)
        self._on_withdrawal(rater_id, station, reason)
        self.publish("end", "withdrawn")
