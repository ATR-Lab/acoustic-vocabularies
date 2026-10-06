"""Listening sessions of the separation-threshold tool (#23): run directory, trial runner,
web app, bot listener and export.

Run directory (`rundir`): one run per listening study (purpose `threshold`) holds the
stimulus set (`threshold/stimuli.json`), one session document per listener session
(`threshold/sessions/<session_id>.json`, written before the first trial) and the logs
`threshold-trials.jsonl`, `plays.jsonl` and `timing.jsonl`. DEMO/synthetic runs may live
anywhere; pilot runs (real listeners and the internal tryout) are refused inside a git
work tree (`rundir.create_run_dir`).

Trial flow (no replay, no feedback, self-paced): the listener presses Play; the server
issues the current trial with two single-use audio tokens (`ROUTES["next"]`); the page
fetches both WAVs once (each delivery is logged and synced to disk as a
`threshold.DELIVERY_EVENT` timing event before the bytes leave the server), schedules
motif A, the gap (`config.gap_ms`) and motif B with the Web Audio clock and reports the
onsets at the speaker, output latency included (`ROUTES["played"]`), which logs one
`threshold_first` and one `threshold_second` play event; the Same/Different buttons open
when motif B ends at the speaker and the answer is logged as a `threshold_trial` record
(`rt_ms` from the end of motif B). A second fetch of a token or a second play report is
refused and logged (`result="refused"`). A trial whose audio was delivered but whose
play was never reported (a reload or a server restart in between) is never issued
again: its phase is `skip` and only the operator skip closes it. The page never learns
the pair, its kind, bin or distance, and the answer is never scored. The station plays
at its fixed output gain (recorded in the session document); the page has no volume
control.
"""

from __future__ import annotations

import hashlib
import math
import os
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

import httpx
from av_sound.recipe import Recipe
from av_sound.renderer import RENDERER_VERSION, render
from av_sound.store import validator_code_hash
from av_sound.validate import VALIDATOR_VERSION
from av_sound.version import renderer_hash
from av_sound.wav import HEADER_SIZE, wav_bytes
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from av_generation import __version__
from av_generation import threshold as th
from av_generation.clock import Clock, ManualClock, ScaledClock, utc_text
from av_generation.ids import DEMO_PREFIX, PUBLIC_RUN_KINDS, RunKind, Study
from av_generation.jsonio import repair_torn_tail
from av_generation.records import (
    PlayEvent,
    RecordWriter,
    RunCode,
    RunManifest,
    ThresholdPair,
    ThresholdSession,
    ThresholdStimulusSet,
    ThresholdTrial,
    TimingEvent,
    read_records,
)
from av_generation.rundir import (
    RunLayout,
    check_run_id,
    check_run_location,
    create_run_dir,
    run_layout,
)
from av_generation.seeds import rng_for, threshold_seed_key

STIMULI_NAME: Final = "stimuli.json"
"""The run's stimulus set, under `RunLayout.threshold_dir`."""
STATIC_DIR: Final = Path(__file__).resolve().parent / "web" / "threshold"
"""Static files of the listener page (plain HTML/CSS/JS, no third-party code)."""
COMPONENT: Final = th.TIMING_COMPONENT
SAMPLE_RATE: Final = 48_000

ROUTES: Final[dict[str, str]] = {
    "page": "GET /threshold/",
    "static": "GET /threshold/static/{name}",
    "state": "GET /threshold/api/state",
    "next": "POST /threshold/api/next",
    "audio": "GET /threshold/api/audio/{token}",
    "played": "POST /threshold/api/trials/{trial_index}/played",
    "response": "POST /threshold/api/trials/{trial_index}/response",
    "skip": "POST /threshold/api/trials/{trial_index}/skip",
}
"""HTTP routes of the listener page (bodies in `generation/docs/threshold-tool.md`)."""

_STATIC_TYPES: Final[dict[str, str]] = {
    "threshold.js": "text/javascript; charset=utf-8",
    "threshold.css": "text/css; charset=utf-8",
}

E_RUN: Final = "E_RUN"
E_STATE: Final = "E_STATE"
E_DONE: Final = "E_DONE"
E_TOKEN_UNKNOWN: Final = "E_TOKEN_UNKNOWN"
E_TOKEN_USED: Final = "E_TOKEN_USED"
E_NOT_FETCHED: Final = "E_NOT_FETCHED"
E_ALREADY_PLAYED: Final = "E_ALREADY_PLAYED"
E_ASSET_HASH: Final = "E_ASSET_HASH"
E_ONSETS: Final = "E_ONSETS"
E_UNREPORTED: Final = "E_UNREPORTED"


class RunnerError(Exception):
    """A refused runner request (`code`; HTTP `status`)."""

    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


# ---------------------------------------------------------------------------
# Run directory


def stimuli_path(layout: RunLayout) -> Path:
    """`<run>/threshold/stimuli.json`."""
    return layout.threshold_dir / STIMULI_NAME


def _clock_kind(clock: Clock) -> Literal["real", "scaled", "manual"]:
    if isinstance(clock, ManualClock):
        return "manual"
    return "scaled" if isinstance(clock, ScaledClock) else "real"


def open_threshold_run(
    runs_root: str | os.PathLike[str],
    run_id: str,
    kind: RunKind | str,
    stimuli: ThresholdStimulusSet,
    *,
    clock: Clock,
) -> RunLayout:
    """Create the listening-study run (manifest + stimulus set), or reopen it.

    A new run is created with `rundir.create_run_dir` (DEMO IDs for demo/synthetic runs;
    other kinds are refused inside a git work tree). A reopened run must be a `threshold`
    run of the same kind holding the same stimulus set (hash). DEMO sets run only in
    demo/synthetic runs and real sets only in other runs.
    """
    kind = RunKind(kind)
    if stimuli.demo != (kind in PUBLIC_RUN_KINDS):
        raise RunnerError(E_RUN, f"a {'DEMO' if stimuli.demo else 'real'} set in a {kind} run")
    problems = th.check_stimuli(stimuli, render=False)
    if problems:
        raise RunnerError(E_RUN, f"stimulus set has problems: {problems[:3]}")
    layout = run_layout(runs_root, run_id)
    if not layout.manifest.exists():
        layout = create_run_dir(runs_root, run_id, kind)
        RunManifest(
            run_id=run_id,
            kind=kind,
            study=Study.A,
            purpose="threshold",
            clock=_clock_kind(clock),
            created_utc=utc_text(clock.utc_now()),
            code=RunCode(
                av_generation=__version__,
                renderer_version=RENDERER_VERSION,
                renderer_hash=renderer_hash(),
                validator_version=VALIDATOR_VERSION,
                validator_hash=validator_code_hash(),
            ),
            threshold=stimuli.config.threshold_default,
            seed_namespace=stimuli.set_id,
        ).write(layout.manifest)
        layout.threshold_dir.mkdir(parents=True, exist_ok=True)
        stimuli.write(stimuli_path(layout))
        return layout
    check_run_id(run_id, kind)
    check_run_location(layout.root, kind)
    manifest = RunManifest.read(layout.manifest)
    if manifest.purpose != "threshold" or manifest.kind != kind:
        raise RunnerError(E_RUN, f"{run_id} is a {manifest.kind} {manifest.purpose} run")
    stored = ThresholdStimulusSet.read(stimuli_path(layout))
    if stored.sha256() != stimuli.sha256():
        raise RunnerError(E_RUN, f"{run_id} holds another stimulus set ({stored.set_id})")
    return layout


def load_run(layout: RunLayout) -> tuple[ThresholdStimulusSet, list[ThresholdSession]]:
    """The run's stimulus set and session documents (sorted by session ID)."""
    stimuli = ThresholdStimulusSet.read(stimuli_path(layout))
    folder = layout.threshold_dir / "sessions"
    paths = sorted(folder.glob("*.json")) if folder.is_dir() else []
    return stimuli, sorted((ThresholdSession.read(p) for p in paths), key=lambda s: s.session_id)


def read_run_records(layout: RunLayout) -> tuple[list[ThresholdTrial], list[PlayEvent]]:
    """The run's trial records and play events (empty when a log does not exist yet)."""
    trials_log, plays_log = layout.log("threshold_trial"), layout.log("play")
    trials = read_records(trials_log, ThresholdTrial) if trials_log.exists() else []
    plays = read_records(plays_log, PlayEvent) if plays_log.exists() else []
    return trials, plays


def read_run_timing(layout: RunLayout) -> list[TimingEvent]:
    """The run's timing events (session starts and ends, skips, audio deliveries)."""
    path = layout.log("timing")
    return read_records(path, TimingEvent) if path.exists() else []


# ---------------------------------------------------------------------------
# Session runner


@dataclass(slots=True)
class _Trial:
    shown: th.Presentation
    tokens: dict[str, str] = field(default_factory=dict)
    """`first`/`second` -> token (issued with the trial, or restored from the log)."""
    fetched: set[str] = field(default_factory=set)
    """Sides whose audio was delivered (this server or, from the log, an earlier one)."""
    issued_ms: int | None = None
    """Run-clock time of the `next` reply the page measures its onsets from; None for a
    trial whose audio was delivered before a restart (its play can no longer be logged)."""
    onsets: tuple[int, int] | None = None
    played: bool = False
    record: ThresholdTrial | None = None

    @property
    def phase(self) -> Literal["listen", "respond", "skip"]:
        """`respond` after both plays; `skip` when audio was delivered but no play was
        reported (never issued again); `listen` otherwise."""
        if self.played:
            return "respond"
        return "skip" if self.fetched else "listen"


class ThresholdRunner:
    """Server-side state of one listener session (one station, one listener).

    Opening a runner checks the stimulus set and the session plan, renders every motif
    once and compares it with the stored WAV hash (`E_ASSET_HASH`), writes the session
    document if it does not exist yet (exclusive; an existing one must be identical),
    repairs torn log tails (`log_repaired` timing events) and resumes from the logs: a
    trial with a record is done; a trial with both play events can still be answered
    but never replayed; a trial whose audio was delivered (`threshold.DELIVERY_EVENT`)
    or that has only one play event (a crash between the two appends) but no complete
    play pair is consumed: it is never issued again and only `skip` closes it.
    """

    def __init__(
        self,
        layout: RunLayout,
        stimuli: ThresholdStimulusSet,
        session: ThresholdSession,
        *,
        clock: Clock,
        fsync: bool = True,
    ) -> None:
        self.layout = layout
        self.stimuli = stimuli
        self.session = session
        self.clock = clock
        self._lock = threading.Lock()
        stored = ThresholdStimulusSet.read(stimuli_path(layout))
        if stored.sha256() != stimuli.sha256():
            raise RunnerError(E_RUN, "the run holds another stimulus set")
        problems = th.check_session(session, stimuli)
        if problems:
            raise RunnerError(E_RUN, f"session plan problems: {problems[:3]}")
        self._trials = [_Trial(p) for p in th.session_presentations(session, stimuli)]
        self._assets: dict[str, tuple[Recipe, str]] = {}
        for trial in self._trials:
            pair = trial.shown.pair
            for data, digest in (
                (pair.recipe_a, pair.file_sha256_a),
                (pair.recipe_b, pair.file_sha256_b),
            ):
                self._assets[digest] = (Recipe.from_dict(data), pair.profile)
        for digest in self._assets:
            self._audio(digest)
        path = layout.threshold_session(session.session_id)
        if path.exists():
            if ThresholdSession.read(path).sha256() != session.sha256():
                raise RunnerError(E_RUN, f"{path.name} exists with another plan")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            session.write(path)
        self._timing = RecordWriter(layout.log("timing"), types=(TimingEvent,), fsync=fsync)
        for name in ("threshold_trial", "play", "timing"):
            torn = repair_torn_tail(layout.log(name))
            if torn is not None:
                self._time("log_repaired", detail=f"{torn.name} cut {torn.n_bytes} bytes")
        self._plays = RecordWriter(layout.log("play"), types=(PlayEvent,), fsync=fsync)
        self._records = RecordWriter(
            layout.log("threshold_trial"), types=(ThresholdTrial,), fsync=fsync
        )
        self._generation = self._resume()
        self._started_ms = clock.now_ms()
        self._time("session_start", detail=f"{session.session_id} start {self._generation}")

    # -- helpers -----------------------------------------------------------

    def _time(self, event: str, *, detail: str, duration_ms: int | None = None) -> None:
        self._timing.append(
            TimingEvent(
                run_id=self.layout.run_id,
                event=event,
                t_ms=self.clock.now_ms(),
                wall_utc=utc_text(self.clock.utc_now()),
                station=self.session.station,
                component=COMPONENT,
                actor_id=self.session.listener_id,
                duration_ms=duration_ms,
                detail=detail,
            )
        )

    def _resume(self) -> int:
        """Load this session's records; returns how many times the session started before."""
        trials, plays = read_run_records(self.layout)
        timing = read_run_timing(self.layout)
        by_index = {t.shown.trial_index: t for t in self._trials}
        for record in trials:
            if record.session_id == self.session.session_id and record.trial_index in by_index:
                by_index[record.trial_index].record = record
        for index, sent in th.deliveries(timing, self.session.session_id).items():
            if index in by_index:
                for side, token in sent:
                    by_index[index].tokens[side] = token
                    by_index[index].fetched.add(side)
        prefix = f"{self.session.session_id}.t"
        onsets: dict[int, dict[str, int | None]] = {}
        for event in plays:
            name = event.trial_id or ""
            suffix = name[len(prefix) :]
            if event.result == "played" and name.startswith(prefix) and suffix.isdigit():
                onsets.setdefault(int(suffix), {})[event.context] = event.onset_ms
        for index, seen in onsets.items():
            trial = by_index.get(index)
            if trial is None:
                continue
            # a play report needs both motifs delivered, so a trial with any play event
            # is consumed; only a complete pair can still be answered
            trial.fetched.update(th.SIDES)
            first, second = seen.get("threshold_first"), seen.get("threshold_second")
            if len(seen) == 2:
                trial.played = True
                if first is not None and second is not None:
                    trial.onsets = (first, second)
        return sum(
            1
            for e in timing
            if e.event == "session_start"
            and (e.detail or "").startswith(self.session.session_id + " ")
        )

    def _audio(self, digest: str) -> bytes:
        recipe, profile = self._assets[digest]
        data = wav_bytes(render(recipe, profile))
        if hashlib.sha256(data).hexdigest() != digest:
            raise RunnerError(E_ASSET_HASH, "a motif no longer renders to its stored hash", 500)
        return data

    def _current(self) -> _Trial | None:
        return next((t for t in self._trials if t.record is None), None)

    def _require_current(self, trial_index: int) -> _Trial:
        trial = self._current()
        if trial is None:
            raise RunnerError(E_DONE, "the session is complete")
        if trial.shown.trial_index != trial_index:
            raise RunnerError(E_STATE, f"trial {trial_index} is not the current trial")
        return trial

    def _token(self, trial_index: int, which: str) -> str:
        text = f"{self.session.order_seed_key}|{self._generation}|{trial_index}|{which}"
        return hashlib.sha256(text.encode("ascii")).hexdigest()[:32]

    def _play_event(
        self,
        trial: _Trial,
        which: Literal["first", "second"],
        *,
        result: Literal["played", "refused"],
        onset_ms: int | None = None,
        scheduled_ms: int | None = None,
        reason: str | None = None,
    ) -> PlayEvent:
        shown = trial.shown
        first = which == "first"
        event = PlayEvent(
            run_id=self.layout.run_id,
            context="threshold_first" if first else "threshold_second",
            audio_kind="atom",
            asset_id=shown.file_sha256_first if first else shown.file_sha256_second,
            result=result,
            t_ms=self.clock.now_ms(),
            pcm_sha256=shown.pcm_sha256_first if first else shown.pcm_sha256_second,
            reason=reason,
            station=self.session.station,
            actor_id=self.session.listener_id,
            trial_id=th.trial_id(self.session.session_id, shown.trial_index),
            token_id=trial.tokens.get(which),
            scheduled_ms=scheduled_ms,
            onset_ms=onset_ms,
        )
        self._plays.append(event)
        return event

    def _finish(
        self, trial: _Trial, response: Literal["same", "different"] | None, rt_ms: int | None
    ) -> dict[str, Any]:
        shown = trial.shown
        pair = shown.pair
        record = ThresholdTrial(
            run_id=self.layout.run_id,
            session_id=self.session.session_id,
            listener_id=self.session.listener_id,
            trial_index=shown.trial_index,
            pair_id=pair.pair_id,
            profile=pair.profile,
            kind=pair.kind,
            bin_center=pair.bin_center,
            distance=pair.distance,
            order=shown.order,
            gap_ms=self.stimuli.config.gap_ms,
            tryout=self.session.tryout,
            t_ms=self.clock.now_ms(),
            response=response,
            rt_ms=rt_ms,
            onset_first_ms=None if trial.onsets is None else trial.onsets[0],
            onset_second_ms=None if trial.onsets is None else trial.onsets[1],
        )
        self._records.append(record)
        trial.record = record
        if self._current() is None:
            self._time(
                "session_end",
                detail=f"{self.session.session_id} complete",
                duration_ms=self.clock.now_ms() - self._started_ms,
            )
        return {"ok": True, **self._progress()}

    def _progress(self) -> dict[str, Any]:
        done = sum(1 for t in self._trials if t.record is not None)
        return {
            "n_trials": len(self._trials),
            "n_done": done,
            "status": "done" if done == len(self._trials) else "running",
        }

    # -- API ---------------------------------------------------------------

    @property
    def complete(self) -> bool:
        """True when every planned trial has a record."""
        return self._current() is None

    def state(self) -> dict[str, Any]:
        """Progress and the phase of the current trial (no pair information)."""
        with self._lock:
            trial = self._current()
            phase = None if trial is None else trial.phase
            return {
                **self._progress(),
                "trial_index": None if trial is None else trial.shown.trial_index,
                "phase": phase,
            }

    def next_trial(self) -> dict[str, Any]:
        """Issue the current trial: two single-use audio URLs and the gap; when it has
        already played, only the request for an answer (`respond`); when its audio was
        delivered but no play was reported, nothing but `skip` (never issued again)."""
        with self._lock:
            trial = self._current()
            if trial is None:
                raise RunnerError(E_DONE, "the session is complete")
            index = trial.shown.trial_index
            out: dict[str, Any] = {"trial_index": index, "n_trials": len(self._trials)}
            if trial.phase != "listen":
                return {**out, "phase": trial.phase}
            if not trial.tokens:
                trial.tokens = {w: self._token(index, w) for w in th.SIDES}
            # the page that fetches measures its onsets from the latest reply (a reload
            # before any fetch asks again)
            trial.issued_ms = self.clock.now_ms()
            return {
                **out,
                "phase": "listen",
                "gap_ms": self.stimuli.config.gap_ms,
                "first": f"/threshold/api/audio/{trial.tokens['first']}",
                "second": f"/threshold/api/audio/{trial.tokens['second']}",
            }

    def audio(self, token: str) -> bytes:
        """The WAV of a token, once. The delivery is logged (and synced) before the bytes
        are returned, so it survives a restart. A second request is refused and logged."""
        with self._lock:
            for trial in self._trials:
                for which, issued in trial.tokens.items():
                    if issued != token:
                        continue
                    side: Literal["first", "second"] = "first" if which == "first" else "second"
                    if which in trial.fetched or trial.record is not None:
                        self._play_event(trial, side, result="refused", reason=E_TOKEN_USED)
                        raise RunnerError(E_TOKEN_USED, "audio tokens are single-use", 410)
                    data = self._audio(
                        trial.shown.file_sha256_first
                        if which == "first"
                        else trial.shown.file_sha256_second
                    )
                    name = th.trial_id(self.session.session_id, trial.shown.trial_index)
                    self._time(th.DELIVERY_EVENT, detail=th.delivery_detail(name, which, token))
                    trial.fetched.add(which)
                    return data
        raise RunnerError(E_TOKEN_UNKNOWN, "unknown audio token", 404)

    def report_played(
        self,
        trial_index: int,
        onset_first_ms: int,
        onset_second_ms: int,
        output_latency_ms: int | None = None,
    ) -> dict[str, Any]:
        """Log the two plays of the current trial. Onsets are milliseconds since the page
        received the trial (the latest `next` reply), at the speaker; they are stored on
        the run clock (issue time + offset). With `output_latency_ms` (the audio output
        latency the page added), `scheduled_ms` is the onset minus that latency.

        Refused: a second report (`E_ALREADY_PLAYED`, logged), a report before both
        motifs were fetched (`E_NOT_FETCHED`) and a report for audio delivered before
        the server restarted (`E_UNREPORTED`, logged; only `skip` closes that trial)."""
        with self._lock:
            trial = self._require_current(trial_index)
            if trial.played:
                self._play_event(trial, "first", result="refused", reason=E_ALREADY_PLAYED)
                raise RunnerError(E_ALREADY_PLAYED, "this trial has already played")
            if trial.fetched != set(th.SIDES):
                raise RunnerError(E_NOT_FETCHED, "fetch both motifs before reporting a play")
            if trial.issued_ms is None:
                self._play_event(trial, "first", result="refused", reason=E_UNREPORTED)
                raise RunnerError(
                    E_UNREPORTED,
                    "the audio of this trial was delivered before the server restarted; "
                    "its play cannot be logged now: skip this trial",
                )
            if not 0 <= onset_first_ms <= onset_second_ms:
                raise RunnerError(E_ONSETS, "onsets must satisfy 0 <= first <= second", 422)
            trial.onsets = (trial.issued_ms + onset_first_ms, trial.issued_ms + onset_second_ms)
            for side, onset in zip(th.SIDES, trial.onsets, strict=True):
                scheduled = None
                if output_latency_ms is not None:
                    scheduled = max(trial.issued_ms, onset - output_latency_ms)
                self._play_event(
                    trial,
                    "first" if side == "first" else "second",
                    result="played",
                    onset_ms=onset,
                    scheduled_ms=scheduled,
                )
            trial.played = True
            return {"ok": True}

    def respond(
        self, trial_index: int, response: Literal["same", "different"], rt_ms: int | None
    ) -> dict[str, Any]:
        """Record the answer of the current, played trial (no feedback is returned)."""
        with self._lock:
            trial = self._require_current(trial_index)
            if not trial.played:
                raise RunnerError(E_STATE, "answer only after both motifs have played")
            return self._finish(trial, response, rt_ms)

    def skip(self, trial_index: int) -> dict[str, Any]:
        """Close the current trial without an answer (operator action, e.g. audio failed);
        it is never replayed. Logged as a `threshold_trial` with `response` null and an
        `operator_action` timing event; a skip after the audio was delivered but before a
        play was reported says so in its detail (`check_plays` counts it in
        `n_unreported`: the listener may have heard the pair)."""
        with self._lock:
            trial = self._require_current(trial_index)
            name = th.trial_id(self.session.session_id, trial_index)
            note = " (audio delivered, play not reported)" if trial.phase == "skip" else ""
            self._time("operator_action", detail=f"skip {name}{note}")
            return self._finish(trial, None, None)


# ---------------------------------------------------------------------------
# Web app


class PlayedBody(BaseModel):
    """Body of `ROUTES["played"]`: onsets at the speaker in ms since the page received
    the trial, and the output latency the page added to the scheduled times."""

    model_config = ConfigDict(extra="forbid")
    onset_first_ms: int = Field(ge=0, le=3_600_000)
    onset_second_ms: int = Field(ge=0, le=3_600_000)
    output_latency_ms: int | None = Field(default=None, ge=0, le=10_000)


class ResponseBody(BaseModel):
    """Body of `ROUTES["response"]`: the answer and the time from the end of motif B."""

    model_config = ConfigDict(extra="forbid")
    response: Literal["same", "different"]
    rt_ms: int | None = Field(default=None, ge=0, le=86_400_000)


def create_threshold_app(runner: ThresholdRunner) -> FastAPI:
    """The FastAPI app serving `ROUTES` for one session."""
    app = FastAPI(
        title="Separation-threshold listening tool",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.exception_handler(RunnerError)
    async def _refused(_request: Request, err: Exception) -> JSONResponse:
        assert isinstance(err, RunnerError)
        return JSONResponse({"error": err.code, "detail": str(err)}, status_code=err.status)

    @app.get("/")
    def root() -> RedirectResponse:
        return RedirectResponse("/threshold/")

    @app.get("/threshold/")
    def page() -> HTMLResponse:
        text = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(text, headers={"Cache-Control": "no-store"})

    @app.get("/threshold/static/{name}")
    def static(name: str) -> Response:
        if name not in _STATIC_TYPES:
            return JSONResponse({"error": "E_NOT_FOUND"}, status_code=404)
        data = (STATIC_DIR / name).read_bytes()
        return Response(data, media_type=_STATIC_TYPES[name])

    @app.get("/threshold/api/state")
    def state() -> dict[str, Any]:
        return runner.state()

    @app.post("/threshold/api/next")
    def next_trial() -> dict[str, Any]:
        return runner.next_trial()

    @app.get("/threshold/api/audio/{token}")
    def audio(token: str) -> Response:
        return Response(
            runner.audio(token), media_type="audio/wav", headers={"Cache-Control": "no-store"}
        )

    @app.post("/threshold/api/trials/{trial_index}/played")
    def played(trial_index: int, body: PlayedBody) -> dict[str, Any]:
        return runner.report_played(
            trial_index, body.onset_first_ms, body.onset_second_ms, body.output_latency_ms
        )

    @app.post("/threshold/api/trials/{trial_index}/response")
    def response(trial_index: int, body: ResponseBody) -> dict[str, Any]:
        return runner.respond(trial_index, body.response, body.rt_ms)

    @app.post("/threshold/api/trials/{trial_index}/skip")
    def skip(trial_index: int) -> dict[str, Any]:
        return runner.skip(trial_index)

    return app


# ---------------------------------------------------------------------------
# Bot listener (synthetic sessions, demos, CI)


@dataclass(frozen=True, slots=True)
class BotListener:
    """A simulated listener: `P(same) = 1 / (1 + exp(-(intercept + slope * distance)))` for
    different pairs and `catch_p_same` for identical pairs; answers 300-1500 ms after
    motif B. It reads the pair of each trial from the session plan (the page never
    reveals it), so it only exercises the tool; it is not a model of real listeners."""

    intercept: float = 4.0
    slope: float = -40.0
    catch_p_same: float = 0.95

    def p_same(self, pair: ThresholdPair) -> float:
        if pair.kind == "same":
            return self.catch_p_same
        return 1.0 / (1.0 + math.exp(-(self.intercept + self.slope * pair.distance)))


def run_bot_session(
    client: httpx.Client,
    session: ThresholdSession,
    stimuli: ThresholdStimulusSet,
    listener: BotListener | None = None,
    *,
    max_trials: int | None = None,
) -> int:
    """Drive a session through the HTTP API like the page does; returns trials answered.

    Answers come from `rng_for(threshold_seed_key(set, "bot", session))`. A trial in the
    `skip` phase (audio delivered before an interruption) is skipped, like the operator
    does. `client` has the server as `base_url` (an `httpx.Client` or FastAPI's
    `TestClient`).
    """
    bot = listener or BotListener()
    rng = rng_for(threshold_seed_key(stimuli.set_id, "bot", session.session_id))
    shown = {p.trial_index: p for p in th.session_presentations(session, stimuli)}
    answered = 0
    while max_trials is None or answered < max_trials:
        state = client.get("/threshold/api/state").raise_for_status().json()
        if state["status"] == "done":
            break
        trial = client.post("/threshold/api/next").raise_for_status().json()
        index = int(trial["trial_index"])
        if trial["phase"] == "skip":
            client.post(f"/threshold/api/trials/{index}/skip").raise_for_status()
            continue
        if trial["phase"] == "listen":
            first = client.get(trial["first"]).raise_for_status().content
            client.get(trial["second"]).raise_for_status()
            first_ms = (len(first) - HEADER_SIZE) * 1000 // (2 * SAMPLE_RATE)
            onsets = {"onset_first_ms": 50, "onset_second_ms": 50 + first_ms + trial["gap_ms"]}
            client.post(f"/threshold/api/trials/{index}/played", json=onsets).raise_for_status()
        same = float(rng.random()) < bot.p_same(shown[index].pair)
        body = {"response": "same" if same else "different", "rt_ms": int(rng.integers(300, 1500))}
        client.post(f"/threshold/api/trials/{index}/response", json=body).raise_for_status()
        answered += 1
    return answered


# ---------------------------------------------------------------------------
# Export


@dataclass(frozen=True, slots=True)
class ExportResult:
    """Files written by `export_run` (name -> SHA-256) and the per-session play checks."""

    out_dir: Path
    files: dict[str, str]
    play_checks: tuple[th.PlayCheck, ...]
    summary: dict[str, Any]


TRYOUT_LABEL: Final = "Internal tryout (team members; not listener data)"
SYNTHETIC_LABEL: Final = (
    "SYNTHETIC example (bot listeners in a DEMO run; not tryout or listener data)"
)
LISTENER_LABEL: Final = "Listener sessions"


def default_label(stimuli: ThresholdStimulusSet, *, tryout: bool) -> str:
    """The plot and summary label: synthetic, tryout or listener data."""
    if stimuli.demo:
        return SYNTHETIC_LABEL
    return TRYOUT_LABEL if tryout else LISTENER_LABEL


def export_run(
    layout: RunLayout,
    out_dir: str | os.PathLike[str],
    *,
    tryout: bool,
    label: str | None = None,
    plot: bool = True,
) -> ExportResult:
    """Export one population of a run (tryout sessions or listener sessions).

    Writes `trials.csv` (`TRIAL_CSV_COLUMNS`), `summary.csv` (`SUMMARY_CSV_COLUMNS`),
    `fits.csv` (`FIT_COLUMNS`), `summary.json` (the evidence document) and, with
    `plot=True`, `summary.png`. Play checks run per session with the audio deliveries
    (complete sessions must show every pair played exactly once). A run that is not a
    DEMO run (listener or tryout data) is refused inside a git work tree (`E_POLICY`,
    `threshold.check_output_dir`) before anything is written.
    """
    stimuli, sessions = load_run(layout)
    all_trials, plays = read_run_records(layout)
    chosen = [s for s in sessions if s.tryout == tryout]
    ids = {s.session_id for s in chosen}
    trials = [t for t in all_trials if t.session_id in ids]
    demo = layout.run_id.startswith(DEMO_PREFIX) and th.is_demo_data(all_trials, stimuli)
    th.check_output_dir(out_dir, demo=demo)
    timing = read_run_timing(layout)
    checks: list[th.PlayCheck] = []
    for session in chosen:
        own = [t for t in trials if t.session_id == session.session_id]
        complete = len(own) == len(session.plan)
        checks.append(
            th.check_plays(session, stimuli, plays, own, timing=timing, complete=complete)
        )
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = {"trials.csv": th.export_csv(trials, stimuli, out / "trials.csv")}
    rows = th.summarize(trials)
    default = stimuli.config.threshold_default
    fits = th.fit_summary(trials, threshold_default=default)
    files["summary.csv"] = th.write_summary_csv(rows, out / "summary.csv")
    files["fits.csv"] = th.write_fits_csv(fits, out / "fits.csv")
    text = label or default_label(stimuli, tryout=tryout)
    summary = th.build_summary(
        trials,
        label=text,
        stimuli=stimuli,
        sessions=chosen,
        play_checks=checks,
        trials_csv_sha256=files["trials.csv"],
    )
    summary["tryout"] = tryout
    files["summary.json"] = th.write_summary(summary, out / "summary.json")
    if plot and trials:
        th.plot_summary(rows, fits, out / "summary.png", title=text, threshold_default=default)
    return ExportResult(out, files, tuple(checks), summary)


def bot_session_ids(n: int, prefix: str = "DEMO-S") -> Sequence[str]:
    """`DEMO-S01`, `DEMO-S02`, ... for synthetic sessions."""
    return [f"{prefix}{i:02d}" for i in range(1, n + 1)]
