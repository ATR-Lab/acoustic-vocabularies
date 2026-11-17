"""The supervisor's checkpoint (`state.json`) and journal (`supervisor.log`).

`state.json` (`av-banks/unattended-state` v1) is rewritten atomically after every change:
a temporary file in the job root is written, flushed and fsynced, renamed over the old
file, and the directory is fsynced (`write_state`). A kill or a power loss at any point
leaves either the old or the new state, never a mix. The state names the stage and step
being worked on, the child process in flight, the counters (waits, automatic rebuilds,
pilot restarts) and, once the job ends, the summary. What the banks are is never taken
from the state: each step re-reads the stage's own files when it resumes.

`supervisor.log` is the journal: one JSON line per event (`jsonio.JsonlAppender`, fsynced
per line); the same events go to stderr as text for the service journal.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal

from av_generation.clock import utc_text
from av_generation.jsonio import (
    CodecError,
    JsonlAppender,
    decode_dataclass,
    document_text,
    read_json,
    repair_torn_tail,
    to_json_value,
)

from .job import E_STATE, SupervisorError

STATE_FORMAT: Final = "av-banks/unattended-state"
STATE_NAME: Final = "state.json"
LOG_NAME: Final = "supervisor.log"
LOCK_NAME: Final = "supervisor.lock"
SUMMARY_NAME: Final = "summary.json"
CHILD_LOGS: Final = "logs"

JobStatus = Literal["running", "waiting", "stopped", "ready", "done", "interrupted"]
"""`ready`: a person accepted a retry (`retry`); the next `run` goes on."""
StageStatus = Literal["pending", "running", "done", "stopped"]


def now_utc() -> str:
    return utc_text(datetime.now(UTC))


@dataclass
class ChildRecord:
    """One child process (an existing CLI) the supervisor started."""

    seq: int
    stage: str
    step: str
    command: tuple[str, ...]
    """`<module> <subcommand> ...` (URLs without credentials)."""
    stdout: str
    """Path of the child's stdout file, relative to the job root (stderr: `.err`)."""
    started_utc: str
    pid: int | None = None
    exit_code: int | None = None
    """Negative: killed by that signal. `None` while running or when it ended unseen."""
    ended_utc: str | None = None


@dataclass
class StageState:
    name: str
    kind: str
    status: StageStatus = "pending"
    step: str = "start"
    started_utc: str | None = None
    ended_utc: str | None = None
    major: int = 1
    """Pilot: the bank major version (and run) being built."""
    roots: tuple[str, ...] = ()
    """Pilot: every pilot root of the stage, in order (crashed ones first)."""
    crashed_roots: tuple[str, ...] = ()
    restarts: int = 0
    rebuilds: dict[str, int] = field(default_factory=dict)
    """Confirmatory: automatic rebuilds per bank."""
    runs: int = 0
    """Confirmatory: `run` children started."""
    run_started_utc: str | None = None
    """Confirmatory: start of the last `run` child (proves who left `runner.lock`)."""
    summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class WaitStats:
    count: int = 0
    seconds: float = 0.0
    longest_s: float = 0.0
    last_utc: str | None = None


@dataclass
class StopRecord:
    """Why the supervisor stopped for a person, and what that person does next."""

    reason: str
    action: str
    stage: str | None
    step: str | None
    at_utc: str


@dataclass
class JobState:
    format: str
    format_version: int
    job_id: str
    job_sha256: str
    created_utc: str
    updated_utc: str
    status: JobStatus
    sessions: int
    supervisor_pid: int | None
    stage_index: int
    stages: tuple[StageState, ...]
    child: ChildRecord | None = None
    children: int = 0
    waits: WaitStats = field(default_factory=WaitStats)
    heartbeat: dict[str, Any] | None = None
    stop: StopRecord | None = None
    ended_utc: str | None = None
    summary: dict[str, Any] | None = None
    purpose: str = "banks"
    """The job's purpose (`banks` or `rehearsal`), fixed when the job starts."""

    @classmethod
    def new(
        cls,
        job_id: str,
        job_sha256: str,
        stages: Sequence[tuple[str, str]],
        *,
        purpose: str = "banks",
    ) -> JobState:
        at = now_utc()
        return cls(
            format=STATE_FORMAT,
            format_version=1,
            job_id=job_id,
            job_sha256=job_sha256,
            created_utc=at,
            updated_utc=at,
            status="running",
            sessions=0,
            supervisor_pid=None,
            stage_index=0,
            stages=tuple(StageState(name, kind) for name, kind in stages),
            purpose=purpose,
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = to_json_value(self)
        return data


def read_state(path: str | os.PathLike[str]) -> JobState | None:
    """The state file, or `None` when the job has not started."""
    source = Path(path)
    if not source.exists():
        return None
    try:
        state = decode_dataclass(JobState, read_json(source))
    except (OSError, CodecError) as err:
        raise SupervisorError(E_STATE, f"{source.name} is unreadable: {err}") from None
    if state.format != STATE_FORMAT or state.format_version != 1:
        raise SupervisorError(E_STATE, f"{source.name} is not an {STATE_FORMAT} v1 document")
    return state


def _fsync_dir(path: Path) -> None:
    if sys.platform == "win32":  # pragma: no cover - directories cannot be opened there
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _replace(source: Path, target: Path) -> None:
    """`os.replace`, retried briefly on Windows, where a reader that has the target open
    (another shell's `status`) makes the rename fail for a moment."""
    for _ in range(40):
        try:
            os.replace(source, target)
            return
        except PermissionError:  # pragma: no cover - Windows only
            if sys.platform != "win32":
                raise
            time.sleep(0.05)
    os.replace(source, target)  # pragma: no cover - Windows only


def atomic_write(path: Path, text: str) -> None:
    """Write `text` to `path` through a fsynced temporary file and a rename."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _fsync_dir(path.parent)


def write_state(path: Path, state: JobState) -> None:
    state.updated_utc = now_utc()
    atomic_write(path, document_text(state.to_dict()))


class Journal:
    """`supervisor.log` (JSON lines) and the matching text lines on stderr."""

    def __init__(self, path: Path, *, echo: bool = True) -> None:
        torn = repair_torn_tail(path)
        self.log = JsonlAppender(path)
        self.echo = echo
        if torn is not None:
            self.event("log_repaired", file=torn.name, n_bytes=torn.n_bytes)

    def event(self, event: str, **fields: Any) -> None:  # noqa: ANN401
        at = now_utc()
        self.log.append_obj({"at_utc": at, "event": event, **to_json_value(fields)})
        if self.echo:
            text = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
            print(f"[unattended {at}] {event}: {text}", file=sys.stderr, flush=True)
