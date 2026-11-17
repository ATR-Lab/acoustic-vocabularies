"""One supervisor session: the state, the journal, child processes and model waits.

The stage drivers (`stages.py`) work through a `Session`. Every change is saved to
`state.json` before the next action, so a session killed at any point is resumed by the
next one. Two exceptions end a session early:

- `StopForPerson`: a rule says a person decides (a refusal, a verify failure, a
  shortfall or an escalation, an exhausted cap, a model server that stays down). The
  state keeps the reason and the next action; `run` exits 3 until `run --retry`.
- `Interrupted`: SIGTERM or SIGINT. The running child gets SIGTERM, the state is saved
  as `interrupted`, and the next `run` resumes (exit 4).
"""

from __future__ import annotations

import signal
import subprocess
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

from .job import Job, redact_url
from .runtime import (
    INHERITED_LOCK,
    ChildResult,
    backoff,
    monotonic,
    probe_model,
    start_child,
)
from .state import CHILD_LOGS, STATE_NAME, ChildRecord, JobState, Journal, StageState, write_state
from .state import now_utc as _now

TERM_GRACE_S: Final = 20.0


class StopForPerson(Exception):  # noqa: N818 - the name says what happens
    """A rule leaves the decision to a person (module docstring)."""

    def __init__(self, reason: str, action: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.action = action


class Interrupted(Exception):  # noqa: N818
    """SIGTERM or SIGINT reached the supervisor."""


class Session:
    """State, journal and child processes of one `run` (module docstring)."""

    def __init__(
        self,
        job: Job,
        state: JobState,
        journal: Journal,
        *,
        lock_fd: int | None,
        sleep: Callable[[float], bool] | None = None,
    ) -> None:
        self.job = job
        self.state = state
        self.journal = journal
        self.lock_fd = lock_fd
        self.stopping = threading.Event()
        self.signal_name: str | None = None
        self.proc: subprocess.Popen[bytes] | None = None
        self.unseen: ChildRecord | None = None
        """The child an earlier session left running (its end was not seen), if any."""
        self._sleep = sleep

    # -- state and journal --------------------------------------------------

    @property
    def state_path(self) -> Path:
        return self.job.root / STATE_NAME

    def save(self) -> None:
        write_state(self.state_path, self.state)

    def log(self, event: str, **fields: object) -> None:
        self.journal.event(event, **fields)

    def now(self) -> str:
        return _now()

    def children_proven_dead(self) -> bool:
        """Whether every child of this job has exited: this session waited for each of
        its own, and either no earlier session left one running (`unseen`) or the job
        lock proves it (POSIX: children inherit the lock; `runtime.INHERITED_LOCK`)."""
        return self.unseen is None or (self.lock_fd is not None and INHERITED_LOCK)

    def require_children_dead(self, stage: StageState, why: str) -> None:
        """Stop for a person when a resume depends on a child's end that this platform
        cannot prove."""
        if not self.children_proven_dead():
            raise StopForPerson(
                f"{why}, and this platform cannot prove that the child which wrote them "
                f"has exited (stage {stage.name}, step {stage.step})",
                "check that no av_banks command of this job is still running, then run --retry",
            )

    def rel(self, path: Path) -> str:
        """`path` relative to the job root when it is under it (else absolute)."""
        try:
            return path.resolve().relative_to(self.job.root).as_posix()
        except ValueError:
            return path.resolve().as_posix()

    def heartbeat(self, stage: StageState | None, line: str | None) -> None:
        self.state.heartbeat = {
            "at_utc": _now(),
            "stage": stage.name if stage is not None else None,
            "step": stage.step if stage is not None else None,
            "child_pid": self.proc.pid if self.proc is not None else None,
            "line": line,
        }
        self.save()
        self.log("heartbeat", stage=stage.name if stage else None, line=line)

    # -- signals ------------------------------------------------------------

    def interrupt(self, name: str) -> None:
        """Signal handler body: stop waiting, pass SIGTERM to the running child."""
        self.signal_name = name
        self.stopping.set()
        proc = self.proc
        if proc is not None and proc.poll() is None:
            proc.send_signal(signal.SIGTERM)

    def check_interrupt(self) -> None:
        if self.stopping.is_set():
            raise Interrupted(self.signal_name or "interrupted")

    def sleep(self, seconds: float) -> bool:
        """Wait `seconds`; True when a signal ended the wait."""
        if self._sleep is not None:
            return self._sleep(seconds)
        return self.stopping.wait(seconds)

    # -- children -----------------------------------------------------------

    def run_child(
        self,
        stage: StageState,
        step: str,
        module: str,
        args: Sequence[str],
        *,
        progress: Callable[[], str | None] | None = None,
    ) -> ChildResult:
        """Run one existing command line to its end (module docstring of `runtime`),
        writing a heartbeat every `heartbeat_s` while it runs."""
        self.check_interrupt()
        seq = self.state.children + 1
        logs = self.job.root / CHILD_LOGS
        logs.mkdir(mode=0o700, exist_ok=True)
        stdout = logs / f"{seq:04d}-{stage.name}-{step}.out"
        shown = tuple(redact_url(a) for a in (module.rsplit(".", 1)[-1], *args))
        record = ChildRecord(seq, stage.name, step, shown, self.rel(stdout), _now())
        self.state.children = seq
        self.state.child = record
        stage.step = step
        self.save()
        proc, stderr = start_child(
            module,
            args,
            stdout=stdout,
            cwd=self.job.root,
            keep_fd=self.lock_fd if INHERITED_LOCK else None,
        )
        self.proc = proc
        record.pid = proc.pid
        self.save()
        self.log("child_start", seq=seq, stage=stage.name, step=step, pid=proc.pid,
                 command=" ".join(shown[:2]))  # fmt: skip
        try:
            code = self._wait_child(proc, stage, progress)
        finally:
            self.proc = None
        record.exit_code = code
        record.ended_utc = _now()
        self.save()
        self.log("child_end", seq=seq, stage=stage.name, step=step, exit_code=code)
        self.check_interrupt()
        return ChildResult(code, stdout, stderr)

    def _wait_child(
        self,
        proc: subprocess.Popen[bytes],
        stage: StageState,
        progress: Callable[[], str | None] | None,
    ) -> int:
        next_beat = monotonic() + self.job.heartbeat_s
        while True:
            try:
                return proc.wait(timeout=min(1.0, self.job.heartbeat_s))
            except subprocess.TimeoutExpired:
                pass
            if self.stopping.is_set():
                try:
                    return proc.wait(timeout=TERM_GRACE_S)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    return proc.wait()
            if monotonic() >= next_beat:
                line = None
                if progress is not None:
                    try:
                        line = progress()
                    except Exception as err:  # noqa: BLE001 - a heartbeat never fails a run
                        line = f"progress unavailable: {type(err).__name__}"
                self.heartbeat(stage, line)
                next_beat = monotonic() + self.job.heartbeat_s

    # -- model server -------------------------------------------------------

    def wait_for_model(self, stage: StageState, expected: str | None) -> None:
        """Return once the model server answers and lists `expected`; wait with backoff
        and log every wait; `StopForPerson` after `model.max_wait_s`."""
        spec = self.job.model
        url = redact_url(spec.url)
        problem = probe_model(spec.url, expected, timeout_s=spec.probe_timeout_s)
        if problem is None:
            return
        start = monotonic()
        waited = 0.0
        delay = backoff(spec.poll_initial_s, spec.poll_max_s)
        self.state.status = "waiting"
        self.state.waits.count += 1
        self.save()
        self.log("model_wait_start", stage=stage.name, step=stage.step, url=url, why=problem)
        while problem is not None:
            if waited >= spec.max_wait_s:
                self._end_wait(start, ok=False)
                raise StopForPerson(
                    f"the model server at {url} did not answer for {waited:.0f} s "
                    f"(last: {problem})",
                    "start or repair the model server (llm_server serve), check it with "
                    "`curl <url>/v1/models`, then `python -m av_banks.unattended run JOB "
                    "--retry`",
                )
            pause = min(delay(), max(spec.max_wait_s - waited, 0.0))
            self.heartbeat(stage, f"waiting for the model server ({problem}); next probe "
                           f"in {pause:.0f} s")  # fmt: skip
            if self.sleep(pause):
                self._end_wait(start, ok=False)
                self.check_interrupt()
            waited = monotonic() - start
            problem = probe_model(spec.url, expected, timeout_s=spec.probe_timeout_s)
            if problem is not None:
                self.log("model_wait", stage=stage.name, waited_s=round(waited, 1), why=problem)
        self._end_wait(start, ok=True)

    def _end_wait(self, start: float, *, ok: bool) -> None:
        seconds = monotonic() - start
        waits = self.state.waits
        waits.seconds = round(waits.seconds + seconds, 3)
        waits.longest_s = round(max(waits.longest_s, seconds), 3)
        waits.last_utc = _now()
        self.state.status = "running"
        self.save()
        self.log("model_wait_end", ok=ok, waited_s=round(seconds, 1))


def wall_seconds(start_utc: str | None, end_utc: str | None) -> float | None:
    """Seconds between two `utc_text` values (`None` when one is missing)."""
    if start_utc is None or end_utc is None:
        return None
    from datetime import datetime

    a = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
    b = datetime.fromisoformat(end_utc.replace("Z", "+00:00"))
    return round((b - a).total_seconds(), 3)
