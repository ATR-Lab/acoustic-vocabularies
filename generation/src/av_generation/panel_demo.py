"""Scripted panel sessions (#21): a `PanelSessionHost` that plays a fixed schedule.

The real host is the orchestrator (#20). `ScriptedPanelHost` implements the same
contract (`panel_session`) for a fixed list of slots, so the panel server, the station
page and the bot rater can be tested, demonstrated and timed without proposers:

- tests and the browser tests of #21 (`tests/generation/test_rater_client.py`);
- the three-station onset-skew session with loopback capture (36 consecutive slots,
  `generation/docs/rater-panel.md`), analysed by `av_generation.panel_skew`;
- the screen recording of one round on three stations.

It follows the host rules of `panel_session`: events in `seq` order, `slot` events
`panel.SLOT_LEAD_MS` before the slot start, ratings checked with
`panel.rating_refusal`, and at each slot's lock exactly one `RatingRecord` per seat
(submitted, placeholder or missing; `reconnected` when the station rejoined during the
slot; distinguishability 4 by rule on first-atom slots). It also logs `play` records and
the panel `timing` events. Everything it uses is synthetic: the DEMO batch config and
meaning set from `generation/examples/` and DEMO recipes rendered by `av_sound.render`.

Command line (DEMO runs only; logs go to `--out-dir`, never into git):

    uv run --project generation python -m av_generation.panel_demo --rounds 4 --atom-index 5 \
        --host 0.0.0.0 --port 8765 --out-dir generation/out/panel

prints the three station URLs and starts the schedule `--start-delay-s` after all
three seats have joined (`--bots` seats bot raters instead of browsers).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import sys
import threading
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

from av_sound.recipe import Profile, Recipe
from av_sound.renderer import render
from av_sound.wav import file_sha256, pcm_sha256, wav_bytes

from av_generation._paths import examples_path
from av_generation.clock import Clock, ScaledClock, SystemClock, utc_text
from av_generation.config import BatchConfig, RaterSeat
from av_generation.constants import (
    FIRST_ATOM_DISTINGUISHABILITY,
    RATING_SLOT_MS,
    RATING_SLOTS_PER_ROUND,
    REFERENCE_ONSET_MS,
    SLOTS_PER_ROUND,
)
from av_generation.ids import parse_rating_slot_id, proposal_slot_id, rating_slot_id
from av_generation.meanings import load_meanings
from av_generation.panel import SLOT_LEAD_MS, create_panel_app, rating_refusal, station_url
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
from av_generation.rater import BotRater, BotRatingPolicy
from av_generation.records import PlayEvent, RatingRecord, RecordWriter, TimingEvent
from av_generation.rundir import LOG_FILES, check_run_id
from av_generation.seeds import bot_seed_key, rng_for
from av_generation.webserve import serve_in_thread

SCHEDULE_FORMAT: Final = "av-generation/panel-schedule"
SCHEDULE_FILE: Final = "panel-schedule.json"
PRELOAD_LEAD_MS: Final = 10_000
DEMO_RUN_ID: Final = "DEMO-panel-01"
DEMO_SEATS: Final[tuple[RaterSeat, ...]] = (
    RaterSeat("R01", "S1", "human"),
    RaterSeat("R02", "S2", "human"),
    RaterSeat("R03", "S3", "human"),
)

StepKind = Literal["preload", "slot", "pause", "resume", "end"]


@dataclass(frozen=True, slots=True)
class ScriptedSlot:
    """One slot of a scripted session. `panel.start_ms` is relative to the anchor."""

    panel: PanelSlot
    book_id: str
    slot_id: str
    """The rated proposal slot (restricted in real runs; DEMO here)."""


@dataclass(frozen=True, slots=True)
class ScriptStep:
    """One host event of the script, issued at anchor + `at_ms`."""

    at_ms: int
    kind: StepKind
    slot: int | None = None
    """Index into `ScriptedSession.slots` (`slot` steps)."""
    assets: tuple[AssetRef, ...] = ()
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ScriptedSession:
    """A fixed panel schedule with its assets (relative times)."""

    run_id: str
    seats: tuple[RaterSeat, ...]
    slots: tuple[ScriptedSlot, ...]
    steps: tuple[ScriptStep, ...]
    assets: Mapping[str, bytes]
    """asset_id (file SHA-256) -> canonical WAV bytes."""


def asset_ref(data: bytes) -> AssetRef:
    """The `AssetRef` of canonical WAV bytes."""
    pcm = data[44:]
    return AssetRef(file_sha256(pcm), pcm_sha256(pcm), len(pcm) // 2, len(data))


def demo_recipe(key: str) -> Recipe:
    """A DEMO recipe drawn from `rng_for(key)` (no 60-ms short event by construction)."""
    rng = rng_for(key)
    total = int(rng.choice([600, 750, 900]))
    p = [int(x) for x in rng.integers(-6, 7, size=3)]
    w = [int(x) for x in rng.integers(1, 4, size=3)]
    g = [int(x) for x in rng.choice([20, 40, 60], size=2)]
    a = [float(x) for x in rng.choice([0.6, 0.8, 1.0], size=3)]
    return Recipe(
        total_ms=total,
        pitches=(p[0], p[1], p[2]),
        rhythm_weights=(w[0], w[1], w[2]),
        gaps_ms=(g[0], g[1]),
        amplitudes=(a[0], a[1], a[2]),
    )


def demo_wav(run_ns: str, *parts: str) -> bytes:
    """Canonical WAV bytes of a DEMO atom for profile P1 (`bot_seed_key(run_ns, "DEMO", ...)`)."""
    rendered = render(demo_recipe(bot_seed_key(run_ns, "DEMO", "recipe", *parts)), Profile.P1)
    return wav_bytes(rendered)


def demo_session(
    *,
    run_id: str = DEMO_RUN_ID,
    seats: Sequence[RaterSeat] = DEMO_SEATS,
    atom_index: int = 5,
    rounds: int = 1,
    placeholders: Iterable[tuple[int, int]] = (),
    positions: Iterable[int] | None = None,
    first_atom: bool | None = None,
    round_gap_ms: int = 0,
    lead_in_ms: int = 5_000,
    slot_lead_ms: int = SLOT_LEAD_MS,
    preload_lead_ms: int = PRELOAD_LEAD_MS,
    end_reason: str = "appointment_complete",
) -> ScriptedSession:
    """A DEMO session of `rounds` rounds of 9 slots of one atom of the DEMO batch.

    Slots follow the DEMO panel order (`BatchConfig.panel.order`, blocks of three);
    `placeholders` are (round, position) pairs shown as invalid candidates; `positions`
    keeps only some play positions of each round (short test sessions; the kept slots
    follow each other without gaps). Atom
    `atom_index` of the DEMO atom order is the first atom (no reference) when the index is
    0, unless `first_atom` says otherwise. Each round's assets are preloaded
    `preload_lead_ms` before its first slot; the first slot starts at `lead_in_ms`."""
    check_run_id(run_id, "demo")
    config = BatchConfig.read(examples_path("demo-batch-config.json")).check_consistency()
    meanings = load_meanings(examples_path("demo-meanings"))
    atom = config.atom_order[atom_index]
    first = atom_index == 0 if first_atom is None else first_atom
    invalid = set(placeholders)
    assets: dict[str, bytes] = {}
    slots: list[ScriptedSlot] = []
    steps: list[ScriptStep] = []
    kept = sorted(set(positions)) if positions is not None else []
    kept = kept or list(range(1, RATING_SLOTS_PER_ROUND + 1))
    if not all(1 <= p <= RATING_SLOTS_PER_ROUND for p in kept):
        raise ValueError(f"positions must be in 1..{RATING_SLOTS_PER_ROUND}")
    round_ms = len(kept) * RATING_SLOT_MS + round_gap_ms

    def add(data: bytes) -> AssetRef:
        ref = asset_ref(data)
        assets[ref.asset_id] = data
        return ref

    for round_ in range(1, rounds + 1):
        round_start = lead_in_ms + (round_ - 1) * round_ms
        round_assets: list[AssetRef] = []
        for order, position in enumerate(kept):
            book = config.panel.order[(position - 1) // SLOTS_PER_ROUND]
            slot_no = (position - 1) % SLOTS_PER_ROUND + 1
            start = round_start + order * RATING_SLOT_MS
            placeholder = (round_, position) in invalid
            candidate = reference = None
            reference_meaning = None
            unlock = REFERENCE_ONSET_MS
            if not placeholder:
                candidate = add(demo_wav(run_id, book, atom, str(round_), str(slot_no)))
                round_assets.append(candidate)
                if not first:
                    earlier = config.atom_order[(round_ + position) % max(atom_index, 1)]
                    reference = add(demo_wav(run_id, book, earlier))
                    round_assets.append(reference)
                    reference_meaning = meanings.for_atom(earlier, config.labels)
                    unlock = REFERENCE_ONSET_MS + math.ceil(reference.n_samples / 48)
            panel = PanelSlot(
                rating_slot_id=rating_slot_id(config.batch_id, atom, round_, position),
                position=position,
                start_ms=start,
                placeholder=placeholder,
                first_atom=first,
                meaning=None if placeholder else meanings.for_atom(atom, config.labels),
                candidate=candidate,
                reference=reference,
                reference_meaning=reference_meaning,
                unlock_offset_ms=unlock,
            )
            slots.append(ScriptedSlot(panel, book, proposal_slot_id(book, atom, round_, slot_no)))
            steps.append(ScriptStep(max(0, start - slot_lead_ms), "slot", slot=len(slots) - 1))
        steps.append(
            ScriptStep(max(0, round_start - preload_lead_ms), "preload", assets=tuple(round_assets))
        )
    last_lock = slots[-1].panel.start_ms + RATING_SLOT_MS
    steps.append(ScriptStep(last_lock + 500, "end", reason=end_reason))
    return ScriptedSession(
        run_id=run_id,
        seats=tuple(seats),
        slots=tuple(slots),
        steps=tuple(sorted(steps, key=lambda s: (s.at_ms, s.kind != "preload"))),
        assets=dict(assets),
    )


@dataclass
class _Live:
    """An issued slot (absolute times) and what the stations did in it."""

    scripted: ScriptedSlot
    panel: PanelSlot
    ratings: dict[str, RatingSubmission] = field(default_factory=dict)
    plays: dict[tuple[str, str], PlayReport] = field(default_factory=dict)
    final: bool = False

    @property
    def lock_ms(self) -> int:
        return self.panel.start_ms + RATING_SLOT_MS


class ScriptedPanelHost:
    """A `PanelSessionHost` that issues a `ScriptedSession` on a clock (tests, demos).

    Call `begin()` (or `begin_when_joined(delay_ms)`), then drive it with `tick()` (tests
    with a `ManualClock`) or `start()` (a polling thread for real or scaled clocks).
    Records go to `ratings`, `plays` and `timing`, and to `run_dir/logs/*.jsonl` when
    `run_dir` is given (plus `panel-schedule.json` at `begin()`)."""

    def __init__(
        self, session: ScriptedSession, *, clock: Clock, run_dir: Path | None = None
    ) -> None:
        self.session = session
        self.run_id = session.run_id
        self.clock = clock
        self.run_dir = run_dir
        self.ratings: list[RatingRecord] = []
        self.plays: list[PlayEvent] = []
        self.timing: list[TimingEvent] = []
        self.withdrawals: list[tuple[str, str, str]] = []
        self._cond = threading.Condition()
        self._events: list[PanelEvent] = []
        self._pending: list[tuple[int, ScriptStep]] = []
        self._live: dict[str, _Live] = {}
        self._order: list[_Live] = []
        self._state: Literal["waiting", "slot", "paused", "between_atoms", "ended"] = "waiting"
        self._preload: tuple[AssetRef, ...] = ()
        self._seats = {(s.rater_id, s.station): s for s in session.seats}
        self._joined: set[str] = set()
        self._connected: set[str] = set()
        self._reconnects: dict[str, list[int]] = {}
        self._withdrawn: set[tuple[str, str]] = set()
        self._anchor: int | None = None
        self._begin_delay: int | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._writers: dict[str, RecordWriter] = {}
        if run_dir is not None:
            for record in ("rating", "play", "timing"):
                path = Path(run_dir) / LOG_FILES[record]
                path.parent.mkdir(parents=True, exist_ok=True)
                self._writers[record] = RecordWriter(path, fsync=False)

    # -- driving ------------------------------------------------------------

    @property
    def anchor_ms(self) -> int | None:
        return self._anchor

    @property
    def ended(self) -> bool:
        return self._state == "ended"

    def begin(self, at_ms: int | None = None) -> None:
        """Anchor the script at `at_ms` (default now) and write the schedule file."""
        with self._cond:
            if self._anchor is not None:
                raise RuntimeError("the session already began")
            self._anchor = self.clock.now_ms() if at_ms is None else at_ms
            self._pending = sorted(
                ((self._anchor + s.at_ms, s) for s in self.session.steps),
                key=lambda p: (p[0], p[1].kind != "preload"),
            )
        if self.run_dir is not None:
            write_schedule(Path(self.run_dir) / SCHEDULE_FILE, self)
        self.tick()

    def begin_when_joined(self, delay_ms: int) -> None:
        """Begin `delay_ms` after every seat has joined (operator sessions)."""
        self._begin_delay = delay_ms

    def absolute_slots(self) -> tuple[PanelSlot, ...]:
        """The slots with absolute start times (after `begin`)."""
        if self._anchor is None:
            raise RuntimeError("the session has not begun")
        anchor = self._anchor
        return tuple(
            dataclasses.replace(s.panel, start_ms=anchor + s.panel.start_ms)
            for s in self.session.slots
        )

    def tick(self) -> None:
        """Issue every due event and finalize every slot whose lock has passed."""
        now = self.clock.now_ms()
        with self._cond:
            while self._pending and self._pending[0][0] <= now:
                _, step = self._pending.pop(0)
                self._issue(step, now)
            for live in self._order:
                if not live.final and live.lock_ms <= now:
                    self._finalize(live)
            if self._state == "slot" and all(live.final for live in self._order):
                self._state = "waiting"

    def start(self) -> None:
        """Tick in a background thread until `stop()` or the end of the session."""
        if self._thread is not None:
            return
        self._stop.clear()

        def loop() -> None:
            while not self._stop.is_set():
                self.tick()
                if self.ended and all(live.final for live in self._order):
                    break
                time.sleep(0.002)

        self._thread = threading.Thread(target=loop, name="scripted-panel-host", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def wait_ended(self, timeout_s: float) -> bool:
        """Block until the `end` event has been issued and every slot finalized."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with self._cond:
                if self.ended and all(live.final for live in self._order):
                    return True
            time.sleep(0.01)
        return False

    def republish(self, rating_slot_id: str) -> None:
        """Issue an already issued slot again (test of the no-replay rule)."""
        with self._cond:
            live = self._live[rating_slot_id]
            self._append("slot", slot=live.panel)

    def operator(self, kind: Literal["pause", "resume", "end"], reason: str | None = None) -> None:
        """An operator event now (`pause` with a `PAUSE_REASONS` reason, `resume`, `end`)."""
        with self._cond:
            self._issue(ScriptStep(0, kind, reason=reason), self.clock.now_ms())

    # -- internals ------------------------------------------------------------

    def _append(
        self,
        kind: StepKind,
        *,
        slot: PanelSlot | None = None,
        assets: tuple[AssetRef, ...] = (),
        reason: str | None = None,
    ) -> None:
        event = PanelEvent(
            len(self._events) + 1,
            kind,
            self.clock.now_ms(),
            slot=slot,
            assets=assets,
            reason=reason,
        )
        self._events.append(event)
        self._cond.notify_all()

    def _issue(self, step: ScriptStep, now: int) -> None:
        if step.kind == "slot":
            assert step.slot is not None and self._anchor is not None
            scripted = self.session.slots[step.slot]
            panel = dataclasses.replace(
                scripted.panel, start_ms=self._anchor + scripted.panel.start_ms
            )
            live = _Live(scripted, panel)
            self._live[panel.rating_slot_id] = live
            self._order.append(live)
            self._state = "slot"
            self._append("slot", slot=panel)
        elif step.kind == "preload":
            self._preload = step.assets
            self._append("preload", assets=step.assets)
        elif step.kind == "pause":
            self._state = "between_atoms" if step.reason == "between_atoms" else "paused"
            self._append("pause", reason=step.reason)
        elif step.kind == "resume":
            self._state = "waiting"
            self._append("resume")
        else:
            self._state = "ended"
            self._pending.clear()
            self._append("end", reason=step.reason)

    def _current(self) -> _Live | None:
        now = self.clock.now_ms()
        for live in reversed(self._order):
            if not live.final and now < live.lock_ms:
                return live
        return None

    def _timing(
        self, event: str, *, station: str | None, actor: str | None, detail: str | None
    ) -> None:
        record = TimingEvent(
            run_id=self.run_id,
            event=event,
            t_ms=self.clock.now_ms(),
            wall_utc=utc_text(self.clock.utc_now()),
            station=station,
            component="panel",
            actor_id=actor,
            detail=detail,
        ).check()
        self.timing.append(record)
        if "timing" in self._writers:
            self._writers["timing"].append(record)

    def _finalize(self, live: _Live) -> None:
        live.final = True
        slot = live.panel
        parsed = parse_rating_slot_id(slot.rating_slot_id)
        for seat in self.session.seats:
            submitted = live.ratings.get(seat.station)
            rated = submitted is not None and not slot.placeholder
            reconnected = any(
                slot.start_ms <= t < live.lock_ms for t in self._reconnects.get(seat.station, [])
            )
            plays = {
                role: live.plays.get((seat.station, role)) for role in ("candidate", "reference")
            }
            onsets = {
                role: None if p is None else max(0, p.onset_ms - slot.start_ms)
                for role, p in plays.items()
            }
            by_rule = rated and slot.first_atom
            record = RatingRecord(
                run_id=self.run_id,
                batch_id=parsed.batch_id,
                book_id=live.scripted.book_id,
                atom_id=parsed.atom_id,
                round=parsed.round,
                position=parsed.position,
                rating_slot_id=slot.rating_slot_id,
                slot_id=live.scripted.slot_id,
                rater_id=seat.rater_id,
                station=seat.station,
                rater_kind=seat.kind,
                placeholder=slot.placeholder,
                first_atom=slot.first_atom,
                association=submitted.association if rated and submitted else None,
                distinguishability=(
                    FIRST_ATOM_DISTINGUISHABILITY
                    if by_rule
                    else (submitted.distinguishability if rated and submitted else None)
                ),
                distinguishability_by_rule=by_rule,
                comfort=submitted.comfort if rated and submitted else None,
                missing=not slot.placeholder and not rated,
                reconnected=reconnected,
                slot_start_ms=slot.start_ms,
                t_ms=live.lock_ms,
                candidate_onset_ms=onsets["candidate"],
                reference_onset_ms=onsets["reference"],
                unlock_ms=None if slot.placeholder else slot.unlock_offset_ms,
                rt_ms=submitted.rt_ms if rated and submitted else None,
            ).check()
            self.ratings.append(record)
            if "rating" in self._writers:
                self._writers["rating"].append(record)

    # -- PanelSessionHost ---------------------------------------------------------

    def seats(self) -> Sequence[RaterSeat]:
        return self.session.seats

    def wait_events(self, after_seq: int, timeout_s: float) -> tuple[PanelEvent, ...]:
        with self._cond:
            self._cond.wait_for(lambda: len(self._events) > after_seq, timeout=timeout_s)
            return tuple(self._events[after_seq:])

    def snapshot(self) -> PanelSnapshot:
        with self._cond:
            live = self._current()
            state = self._state
            if state == "slot" and live is None:
                state = "waiting"
            return PanelSnapshot(
                seq=len(self._events),
                state=state,
                slot=live.panel if live is not None and state == "slot" else None,
                preload=self._preload,
            )

    def asset_bytes(self, asset_id: str) -> bytes:
        return self.session.assets[asset_id]

    def station_joined(self, rater_id: str, station: str, kind: str) -> None:
        seat = self._seats.get((rater_id, station))
        if seat is None or seat.kind != kind or (rater_id, station) in self._withdrawn:
            raise PanelRefused("E_UNKNOWN_RATER", f"no seat for {rater_id} at {station}")
        with self._cond:
            again = station in self._joined
            self._joined.add(station)
            self._connected.add(station)
            if again:
                self._reconnects.setdefault(station, []).append(self.clock.now_ms())
            all_joined = len(self._joined) == len(self.session.seats)
        self._timing(
            "station_reconnect" if again else "station_connect",
            station=station,
            actor=rater_id,
            detail=f"kind={kind}",
        )
        if all_joined and self._begin_delay is not None and self._anchor is None:
            self.begin(self.clock.now_ms() + self._begin_delay)

    def station_left(self, rater_id: str, station: str) -> None:
        with self._cond:
            self._connected.discard(station)
        self._timing("station_disconnect", station=station, actor=rater_id, detail=None)

    def asset_ready(self, rater_id: str, station: str, asset_id: str, ok: bool) -> None:
        self._timing(
            "asset_ready",
            station=station,
            actor=rater_id,
            detail=f"{'ok' if ok else 'failed'} {asset_id}",
        )

    def clock_synced(self, rater_id: str, station: str, offset_ms: float, rtt_ms: float) -> None:
        self._timing(
            "clock_sync",
            station=station,
            actor=rater_id,
            detail=f"offset_ms={offset_ms:.1f} rtt_ms={rtt_ms:.1f}",
        )

    def report_play(self, report: PlayReport) -> None:
        with self._cond:
            live = self._live.get(report.rating_slot_id)
            if live is None:
                return
            live.plays.setdefault((report.station, report.role), report)
            asset = live.panel.candidate if report.role == "candidate" else live.panel.reference
        record = PlayEvent(
            run_id=self.run_id,
            context="rating_candidate" if report.role == "candidate" else "rating_reference",
            audio_kind="atom",
            asset_id=report.asset_id,
            result="played",
            t_ms=report.received_ms,
            pcm_sha256=None if asset is None else asset.pcm_sha256,
            station=report.station,
            actor_id=report.rater_id,
            slot_id=live.scripted.slot_id if report.role == "candidate" else None,
            rating_slot_id=report.rating_slot_id,
            scheduled_ms=report.scheduled_ms,
            onset_ms=report.onset_ms,
        ).check()
        with self._cond:
            self.plays.append(record)
        if "play" in self._writers:
            self._writers["play"].append(record)

    def submit_rating(self, submission: RatingSubmission) -> RatingAck:
        with self._cond:
            live = self._live.get(submission.rating_slot_id)
            seat = (submission.rater_id, submission.station)
            if seat not in self._seats or seat in self._withdrawn:
                return RatingAck(False, "E_UNKNOWN_RATER")
            if live is None:
                return RatingAck(False, "E_UNKNOWN_SLOT")
            if live.final:
                return RatingAck(False, "E_SLOT_CLOSED")
            if submission.station in live.ratings:
                return RatingAck(False, "E_DUPLICATE_RATING")
            code = rating_refusal(live.panel, submission)
            if code is not None:
                return RatingAck(False, code)
            live.ratings[submission.station] = submission
            return RatingAck(True)

    def report_withdrawal(self, rater_id: str, station: str, reason: str) -> None:
        with self._cond:
            self._withdrawn.add((rater_id, station))
            self.withdrawals.append((rater_id, station, reason))
        self._timing("rater_withdrawal", station=station, actor=rater_id, detail=f"reason={reason}")


def schedule_document(host: ScriptedPanelHost) -> dict[str, object]:
    """The session schedule with absolute times (input of `panel_skew`)."""
    return {
        "format": SCHEDULE_FORMAT,
        "format_version": 1,
        "run_id": host.run_id,
        "stations": [s.station for s in host.session.seats],
        "slots": [
            {
                "rating_slot_id": s.rating_slot_id,
                "position": s.position,
                "start_ms": s.start_ms,
                "placeholder": s.placeholder,
                "first_atom": s.first_atom,
                "candidate": None if s.candidate is None else s.candidate.asset_id,
                "reference": None if s.reference is None else s.reference.asset_id,
                "unlock_offset_ms": s.unlock_offset_ms,
            }
            for s in host.absolute_slots()
        ],
    }


def write_schedule(path: Path, host: ScriptedPanelHost) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(schedule_document(host), indent=2, sort_keys=True, ensure_ascii=False)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text + "\n")


# ---------------------------------------------------------------------------
# Command line


def _parse_placeholder(text: str) -> tuple[int, int]:
    round_, _, position = text.partition(":")
    return int(round_), int(position)


def main(argv: Sequence[str] | None = None) -> int:
    """Serve a DEMO panel session for three stations (screen recording, skew check)."""
    parser = argparse.ArgumentParser(
        prog="python -m av_generation.panel_demo", description=main.__doc__
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="bind address (0.0.0.0 for the lab LAN)"
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--public-url", help="base URL the stations use (e.g. http://192.168.1.20:8765)"
    )
    parser.add_argument("--run-id", default=DEMO_RUN_ID)
    parser.add_argument("--out-dir", type=Path, default=Path("generation/out/panel"))
    parser.add_argument("--rounds", type=int, default=1, help="rounds of 9 slots (4 = 36 slots)")
    parser.add_argument("--atom-index", type=int, default=5, help="0 = first atom (no reference)")
    parser.add_argument("--placeholder", action="append", default=[], metavar="ROUND:POSITION")
    parser.add_argument("--stations", default="S1,S2,S3")
    parser.add_argument("--raters", default="R01,R02,R03")
    parser.add_argument("--start-delay-s", type=float, default=20.0)
    parser.add_argument("--bots", action="store_true", help="seat bot raters instead of browsers")
    parser.add_argument("--speed", type=float, default=1.0, help="clock speed (bots only)")
    args = parser.parse_args(argv)

    kind: Literal["human", "bot"] = "bot" if args.bots else "human"
    stations, raters = args.stations.split(","), args.raters.split(",")
    if len(stations) != len(raters):
        parser.error("--stations and --raters need the same number of entries")
    if args.speed != 1.0 and not args.bots:
        parser.error("--speed needs --bots (browsers run in real time)")
    seats = tuple(RaterSeat(r, s, kind) for r, s in zip(raters, stations, strict=True))
    session = demo_session(
        run_id=args.run_id,
        seats=seats,
        atom_index=args.atom_index,
        rounds=args.rounds,
        placeholders=[_parse_placeholder(p) for p in args.placeholder],
    )
    clock: Clock = ScaledClock(args.speed) if args.speed != 1.0 else SystemClock()
    run_dir = args.out_dir / args.run_id
    host = ScriptedPanelHost(session, clock=clock, run_dir=run_dir)
    host.begin_when_joined(int(args.start_delay_s * 1000))
    host.start()
    app = create_panel_app(host, clock=clock)
    with serve_in_thread(app, host=args.host, port=args.port) as base:
        print(f"panel session {args.run_id}: {len(session.slots)} slots, logs in {run_dir}")
        for seat in seats:
            url = station_url(args.public_url or base, seat.station, seat.rater_id)
            print(f"  {seat.station} ({seat.rater_id}): {url}")
        bots = [
            threading.Thread(
                target=BotRater(
                    base,
                    rater_id=s.rater_id,
                    station=s.station,
                    run_id=args.run_id,
                    policy=BotRatingPolicy(),
                    clock=clock,
                ).run,
                daemon=True,
            )
            for s in (seats if args.bots else ())
        ]
        for bot in bots:
            bot.start()
        total_s = (len(session.slots) * RATING_SLOT_MS) / 1000 / args.speed
        print(
            f"waiting for {len(seats)} stations; the schedule starts {args.start_delay_s:.0f} s "
            f"after the last one joins and lasts {total_s:.0f} s"
        )
        sys.stdout.flush()
        try:
            while not host.wait_ended(1.0):
                pass
        except KeyboardInterrupt:
            host.operator("end", "aborted")
            host.wait_ended(5.0)
        for bot in bots:
            bot.join(timeout=10)
    host.stop()
    print(f"done: {len(host.ratings)} rating records, {len(host.plays)} play records")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
