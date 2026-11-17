"""`run`, `status` and `check` of the unattended supervisor (the `cli` calls these).

`run_job` takes the job lock, reads or creates `state.json`, then drives every stage that
is not done, in order. Killing it (or the machine) at any point and starting the same
command again resumes: finished stages are skipped and the current step re-reads the
stage's files (`stages`). Exit codes (`EXIT_*`): 0 the job is done; 2 refused (job file,
data policy, lock); 3 stopped for a person (`state.json` keeps the reason and the next
action; `run --retry` after it is handled); 4 interrupted by a signal (run again); 1 an
unexpected error (the service restarts it).

A rehearsal job (`purpose: rehearsal`, `rehearsal` module) runs the same way; before it
starts, its roots and inputs are checked (`rehearsal.separation_problems`,
`rehearsal.input_problems`; every job gets the separation check), the `REHEARSAL` marker
is written into its job root, and at the end `rehearsal-summary.json` and `.md` are
written next to `summary.json`.
"""

from __future__ import annotations

import os
import signal
import sys
from collections.abc import Callable
from pathlib import Path
from types import FrameType
from typing import Any, Final

from av_generation.jsonio import document_text

from .job import (
    E_JOB,
    E_LOCKED,
    E_REHEARSAL,
    E_STATE,
    Job,
    PilotStage,
    SupervisorError,
    check_location,
    load_job,
)
from .rehearsal import PURPOSE as REHEARSAL
from .rehearsal import (
    input_problems,
    separation_problems,
    write_marker,
    write_rehearsal_summary,
)
from .runtime import INHERITED_LOCK, JobLock, lock_free, pid_alive, probe_model
from .session import Interrupted, Session, StopForPerson, wall_seconds
from .stages import drive_confirmatory, drive_pilot, stage_root
from .state import (
    CHILD_LOGS,
    LOCK_NAME,
    LOG_NAME,
    STATE_NAME,
    SUMMARY_NAME,
    JobState,
    Journal,
    StopRecord,
    atomic_write,
    now_utc,
    read_state,
)

EXIT_DONE: Final = 0
EXIT_ERROR: Final = 1
EXIT_REFUSED: Final = 2
EXIT_STOPPED: Final = 3
EXIT_INTERRUPTED: Final = 4


def prepare_root(job: Job) -> list[str]:
    """Apply the data policy (module docstring of `job`) and the rehearsal checks
    (`SupervisorError(E_REHEARSAL)`), create the job root 0700 and, for a rehearsal,
    write its marker. Returns the notes to log."""
    notes = [n for n in (check_location(job.root),) if n]
    for stage in job.stages:
        note = check_location(stage_root(stage))
        if note:
            notes.append(note)
    problems = rehearsal_problems(job)
    if problems:
        raise SupervisorError(E_REHEARSAL, "; ".join(problems))
    job.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(job.root, 0o700)
    (job.root / CHILD_LOGS).mkdir(exist_ok=True, mode=0o700)
    if job.purpose == REHEARSAL:
        write_marker(job.root, job.job_id)
    return notes


def rehearsal_problems(job: Job) -> list[str]:
    """Separation of rehearsal roots from everything else (every job) and, for a
    rehearsal job, its inputs (`rehearsal` module docstring)."""
    problems = separation_problems(job)
    if job.purpose == REHEARSAL:
        problems += input_problems(job)
    return problems


def _load_state(job: Job, *, allow_job_change: bool, journal: Journal) -> JobState:
    state = read_state(job.root / STATE_NAME)
    stages = [(s.name, s.kind) for s in job.stages]
    if state is None:
        return JobState.new(job.job_id, job.sha256, stages, purpose=job.purpose)
    if state.job_id != job.job_id:
        raise SupervisorError(E_STATE, f"the job root belongs to job {state.job_id!r}")
    if state.purpose != job.purpose:
        raise SupervisorError(
            E_STATE,
            f"the job root belongs to a {state.purpose} job; the job file says {job.purpose} "
            "(a purpose never changes)",
        )
    if [(s.name, s.kind) for s in state.stages] != stages:
        raise SupervisorError(E_JOB, "the job file's stages differ from the job root's state")
    if state.job_sha256 != job.sha256:
        if not allow_job_change:
            raise SupervisorError(
                E_JOB,
                "the job file changed since the job started; restore it, or run with "
                "--allow-job-change to accept the change (it is logged)",
            )
        journal.event("job_changed", old_sha256=state.job_sha256, new_sha256=job.sha256)
        state.job_sha256 = job.sha256
    return state


def write_summary(s: Session) -> dict[str, Any]:
    state = s.state
    summary: dict[str, Any] = {
        "job_id": state.job_id,
        "purpose": state.purpose,
        "status": state.status,
        "created_utc": state.created_utc,
        "ended_utc": state.ended_utc,
        "wall_s": wall_seconds(state.created_utc, state.ended_utc or now_utc()),
        "sessions": state.sessions,
        "children": state.children,
        "model_waits": {
            "count": state.waits.count,
            "seconds": state.waits.seconds,
            "longest_s": state.waits.longest_s,
        },
        "stages": [
            {
                "name": st.name,
                "kind": st.kind,
                "status": st.status,
                "wall_s": wall_seconds(st.started_utc, st.ended_utc),
                **st.summary,
            }
            for st in state.stages
        ],
    }
    state.summary = summary
    atomic_write(s.job.root / SUMMARY_NAME, document_text(summary))
    return summary


def run_job(
    job_path: str | os.PathLike[str],
    *,
    retry: bool = False,
    allow_job_change: bool = False,
    echo: bool = True,
    lock_wait_s: float | None = None,
) -> int:
    """`python -m av_banks.unattended run` (module docstring for the exit codes)."""
    job = load_job(job_path)
    umask = os.umask(0o077)  # files 0600, directories 0700, in every child too
    try:
        return _run_job(job, retry, allow_job_change, echo, lock_wait_s)
    finally:
        os.umask(umask)


def _run_job(
    job: Job, retry: bool, allow_job_change: bool, echo: bool, lock_wait_s: float | None
) -> int:
    notes = prepare_root(job)
    journal = Journal(job.root / LOG_NAME, echo=echo)
    for note in notes:
        journal.event("data_policy", note=note)
    lock = JobLock(job.root / LOCK_NAME)
    previous = read_state(job.root / STATE_NAME)
    holder = previous.supervisor_pid if previous is not None else None

    def lock_wait(waited: float) -> None:
        journal.event("lock_wait", waited_s=round(waited, 1), recorded_supervisor_pid=holder)

    lock.acquire(
        holder_alive=lambda: holder != os.getpid() and pid_alive(holder),
        wait_s=job.lock_wait_s if lock_wait_s is None else lock_wait_s,
        on_wait=lock_wait,
        sleep=lambda seconds: _sleep(seconds),
    )
    try:
        state = _load_state(job, allow_job_change=allow_job_change, journal=journal)
        session = Session(job, state, journal, lock_fd=lock.fd)
        return _run(session, retry=retry)
    finally:
        lock.release()


def _sleep(seconds: float) -> bool:
    import time

    time.sleep(seconds)
    return False


def _accept_retry(s: Session) -> None:
    """Clear the stop; the stopped step is evaluated again from the files."""
    state = s.state
    stop = state.stop
    if stop is None:
        return
    s.log("retry", reason=stop.reason, stage=stop.stage, step=stop.step)
    if state.child is not None and state.child.stage == stop.stage:
        state.child = None
    state.stop = None
    state.status = "ready"


def retry_job(job_path: str | os.PathLike[str], *, allow_job_change: bool = False) -> int:
    """`retry`: accept the retry of a stopped job without running it, so the service's
    next start goes on (`systemctl restart`). Refused while a supervisor runs the job."""
    job = load_job(job_path)
    if not (job.root / STATE_NAME).is_file():
        raise SupervisorError(E_STATE, "the job has not started")
    journal = Journal(job.root / LOG_NAME, echo=False)
    lock = JobLock(job.root / LOCK_NAME)
    if not lock.try_acquire():
        raise SupervisorError(E_LOCKED, "a supervisor (or its child) runs this job")
    try:
        state = _load_state(job, allow_job_change=allow_job_change, journal=journal)
        session = Session(job, state, journal, lock_fd=lock.fd)
        if state.status != "stopped":
            print(f"job {state.job_id} is {state.status}; nothing to retry")
        else:
            _accept_retry(session)
            print(f"job {state.job_id}: retry accepted; start the supervisor again")
        session.save()
    finally:
        lock.release()
    return EXIT_DONE


def _run(s: Session, *, retry: bool) -> int:
    state = s.state
    if state.status == "done":
        s.log("job_already_done", job_id=state.job_id)
        print(status_text(s.job), end="")
        return EXIT_DONE
    if state.status == "stopped" and state.stop is not None:
        if not retry:
            print(
                f"stopped for a person: {state.stop.reason}\nnext: {state.stop.action}",
                file=sys.stderr,
            )
            return EXIT_STOPPED
        _accept_retry(s)
    state.sessions += 1
    state.supervisor_pid = os.getpid()
    state.status = "running"
    unseen = state.child if state.child and state.child.exit_code is None else None
    s.unseen = unseen
    s.save()
    s.log("session_start", job_id=state.job_id, session=state.sessions, pid=os.getpid(),
          lock_inherited=INHERITED_LOCK)  # fmt: skip
    if unseen is not None:
        s.log("child_unseen", seq=unseen.seq, stage=unseen.stage, step=unseen.step,
              pid=unseen.pid, note="a previous session ended while this child ran")  # fmt: skip
    handlers = _install_signals(s)
    stage_state = None
    try:
        for index, stage in enumerate(s.job.stages):
            stage_state = state.stages[index]
            if stage_state.status == "done":
                continue
            state.stage_index = index
            stage_state.status = "running"
            stage_state.started_utc = stage_state.started_utc or now_utc()
            s.save()
            s.log("stage_start", stage=stage.name, kind=stage.kind, step=stage_state.step)
            if isinstance(stage, PilotStage):
                drive_pilot(s, stage, stage_state)
            else:
                drive_confirmatory(s, stage, stage_state)
            stage_state.status = "done"
            stage_state.ended_utc = now_utc()
            s.save()
            s.log("stage_done", stage=stage.name, summary=stage_state.summary)
        state.status = "done"
        state.ended_utc = now_utc()
        state.heartbeat = None
        write_summary(s)
        if s.job.purpose == REHEARSAL:
            try:
                write_rehearsal_summary(s)
            except Exception as err:  # noqa: BLE001 - a summary never fails a finished job
                s.log("rehearsal_summary_error", error=f"{type(err).__name__}: {err}"[:400])
        s.save()
        s.log("job_done", job_id=state.job_id)
        print(status_text(s.job), end="")
        return EXIT_DONE
    except StopForPerson as stop:
        state.status = "stopped"
        state.stop = StopRecord(
            stop.reason,
            stop.action,
            stage_state.name if stage_state else None,
            stage_state.step if stage_state else None,
            now_utc(),
        )
        if stage_state is not None:
            stage_state.status = "stopped"
        write_summary(s)
        s.save()
        s.log("stopped", reason=stop.reason, action=stop.action)
        print(f"stopped for a person: {stop.reason}\nnext: {stop.action}", file=sys.stderr)
        return EXIT_STOPPED
    except Interrupted as why:
        state.status = "interrupted"
        if stage_state is not None and stage_state.status == "running":
            stage_state.status = "pending"
        s.save()
        s.log("interrupted", signal=str(why))
        return EXIT_INTERRUPTED
    except Exception as err:  # an unexpected error: logged, state kept, the service restarts
        text = f"{type(err).__name__}: {err}"[:400]
        state.status = "interrupted"
        if stage_state is not None and stage_state.status == "running":
            stage_state.status = "pending"
        s.save()
        s.log("error", error=text)
        print(f"unattended run: unexpected error: {text}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        _restore_signals(handlers)


def _install_signals(s: Session) -> dict[int, Any]:
    def handler(signum: int, _frame: FrameType | None) -> None:
        s.interrupt(signal.Signals(signum).name)

    old: dict[int, Any] = {}
    for sig in (signal.SIGTERM, signal.SIGINT):
        old[sig] = signal.signal(sig, handler)
    return old


def _restore_signals(old: dict[int, Any]) -> None:
    for sig, handler in old.items():
        signal.signal(sig, handler)


# ---------------------------------------------------------------------------
# status and check


def _status_root(target: str | os.PathLike[str]) -> tuple[Path, Job | None]:
    path = Path(target)
    if path.is_dir():
        return path.resolve(), None
    job = load_job(path)
    return job.root, job


def status_text(target: Job | str | os.PathLike[str]) -> str:
    """What `status` prints: the job, each stage, waits, rebuilds, the child in flight,
    the heartbeat and, when stopped, the reason and the next action."""
    root = target.root if isinstance(target, Job) else _status_root(target)[0]
    state = read_state(root / STATE_NAME)
    if state is None:
        return f"job root {root}: not started\n"
    alive = pid_alive(state.supervisor_pid)
    free = lock_free(root / LOCK_NAME)
    watermark = "REHEARSAL, DEMO banks only; " if state.purpose == REHEARSAL else ""
    lines = [
        f"job {state.job_id}: {state.status} ({watermark}session {state.sessions}; supervisor pid "
        f"{state.supervisor_pid} {'alive' if alive else 'not running'}; job lock "
        f"{'free' if free else 'held'})",
    ]
    if state.status in ("running", "waiting") and not alive:
        lines.append("the supervisor is not running: start it again to resume")
    if state.heartbeat:
        beat = state.heartbeat
        age = wall_seconds(beat.get("at_utc"), now_utc())
        lines.append(f"heartbeat {beat.get('at_utc')} ({age:.0f} s ago): {beat.get('line')}")
    for i, st in enumerate(state.stages, start=1):
        extra = ""
        if st.kind == "pilot":
            extra = f"; major {st.major}, restarts {st.restarts}"
        elif st.rebuilds:
            extra = f"; automatic rebuilds {dict(st.rebuilds)}"
        lines.append(f"[{i}/{len(state.stages)}] {st.name} ({st.kind}): {st.status}, "
                     f"step {st.step}{extra}")  # fmt: skip
        for key in ("counts", "usable_slots", "shortfall", "slots_total", "slots",
                    "register_sha256", "archive_sha256", "decision"):  # fmt: skip
            if key in st.summary:
                lines.append(f"  {key}: {st.summary[key]}")
    waits = state.waits
    lines.append(f"model waits: {waits.count} ({waits.seconds:.0f} s in all, longest "
                 f"{waits.longest_s:.0f} s)")  # fmt: skip
    lines.append(f"children started: {state.children}")
    child = state.child
    if child is not None and child.exit_code is None and state.status != "done":
        lines.append(f"child {child.seq}: {' '.join(child.command[:2])} (pid {child.pid}, "
                     f"since {child.started_utc}; output {child.stdout})")  # fmt: skip
    if state.stop is not None:
        lines.append(f"STOPPED for a person: {state.stop.reason}")
        lines.append(f"next: {state.stop.action}")
    if state.summary is not None and state.status == "done":
        lines.append(f"done {state.ended_utc}; wall {state.summary.get('wall_s')} s; "
                     f"summary in {SUMMARY_NAME}")  # fmt: skip
    return "\n".join(lines) + "\n"


def check_job(
    job_path: str | os.PathLike[str], *, probe: bool = True
) -> tuple[list[str], list[str]]:
    """`check`: the job file, the data policy and the inputs, without writing anything.
    Returns (problems, notes); the model server being down is a note."""
    from av_generation.genconfig import GenerationConfig

    job = load_job(job_path)
    problems: list[str] = rehearsal_problems(job)
    notes: list[str] = []
    if job.rehearsal is not None:
        notes.append(
            f"REHEARSAL job: DEMO inputs, {job.rehearsal.dyads} + {job.rehearsal.spares} "
            "banks; the banks are DEMO banks, never confirmatory banks"
        )
    for path in (job.root, *(stage_root(s) for s in job.stages)):
        try:
            note = check_location(path)
        except SupervisorError as err:
            problems.append(str(err))
        else:
            if note:
                notes.append(note)
    expected: str | None = job.model.served_model
    for stage in job.stages:
        inputs: dict[str, Path] = {
            "meanings": stage.meanings,
            "prompts": stage.prompts,
            "decoding_schema": stage.decoding_schema,
        }
        configs: list[Path] = []
        if isinstance(stage, PilotStage):
            inputs.update(units=stage.units, generation_config=stage.generation_config)
            configs.append(stage.generation_config)
        elif stage.plan is not None:
            inputs.update(
                plan_units=stage.plan.units,
                plan_generation_config=stage.plan.generation_config,
                plan_freeze_manifest=stage.plan.freeze_manifest,
            )
            configs.append(stage.plan.generation_config)
            for path in stage.plan.pilot:
                inputs[f"plan_pilot {path.name}"] = path
        elif not (stage.campaign_root / "plan.json").is_file():
            problems.append(f"{stage.name}: the campaign has no plan.json and the job gives "
                            "no plan arguments")  # fmt: skip
        else:
            configs.append(stage.campaign_root / "generation-config.json")
        for name, path in inputs.items():
            if not path.exists():
                problems.append(f"{stage.name}: {name} {path} does not exist")
        for path in configs:
            if not path.is_file():
                continue
            try:
                model = GenerationConfig.read(path).model.model_id
            except (OSError, ValueError) as err:
                problems.append(f"{stage.name}: {path.name} is not a generation config: {err}")
                continue
            expected = expected or model
    if probe:
        why = probe_model(job.model.url, expected, timeout_s=job.model.probe_timeout_s)
        notes.append("model server: " + ("answers" if why is None else f"not ready ({why})"))
    return problems, notes


def main_check(job_path: str, *, probe: bool, out: Callable[[str], None]) -> int:
    try:
        problems, notes = check_job(job_path, probe=probe)
    except SupervisorError as err:
        out(f"REFUSED: {err}")
        return EXIT_REFUSED
    for note in notes:
        out(f"note: {note}")
    for problem in problems:
        out(f"PROBLEM: {problem}")
    out("OK" if not problems else f"FAILED ({len(problems)} problems)")
    return EXIT_DONE if not problems else EXIT_REFUSED
