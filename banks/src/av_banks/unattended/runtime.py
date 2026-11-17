"""Processes and the model server: the job lock, child processes and the health probe.

**Job lock.** `supervisor.lock` in the job root is held with `flock` for the whole life of
`run`, and every child process inherits the locked file descriptor. The kernel releases
the lock only when the supervisor and every child it started have exited, also after
`SIGKILL` or a reboot. So a supervisor that holds the lock knows that no runner it or an
earlier supervisor of this job started is still alive: that is the proof the
confirmatory step needs before it passes `--break-lock` (`runner.lock` records only the
time it was taken). A restarted supervisor that finds the lock held while the recorded
supervisor process is dead waits for the orphaned child to finish (`lock_wait_s`).
This needs POSIX (`flock`, inherited descriptors); the GPU host runs Linux. On Windows
the lock covers the supervisor alone (`INHERITED_LOCK`), so a resume that would need the
proof stops for a person instead.

**Children.** Each step runs an existing command line (`sys.executable -m
av_banks.pilot ...`, `-m av_banks.confirmatory ...`) with its stdout and stderr in files
under `logs/` (never pipes: a child that outlives a killed supervisor must not die of a
broken pipe), stdin closed and the umask the supervisor set (077).

**Model server.** `probe_model` asks the OpenAI-compatible endpoint `GET /v1/models`
directly (no proxy) and checks that the served model is listed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .job import E_LOCKED, SupervisorError

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

MODELS_PATH: Final = "/v1/models"
INHERITED_LOCK: Final = sys.platform != "win32"
"""Whether children inherit the job lock (POSIX `flock`). On Windows the lock covers the
supervisor only, so the supervisor never proves a child dead there and stops for a
person instead (`Session.children_proven_dead`)."""


# ---------------------------------------------------------------------------
# Job lock


class JobLock:
    """The job root's `supervisor.lock` (module docstring)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd: int | None = None

    def try_acquire(self) -> bool:
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        if not _try_lock(fd):
            os.close(fd)
            return False
        self.fd = fd
        return True

    def acquire(
        self,
        *,
        holder_alive: Callable[[], bool],
        wait_s: float,
        on_wait: Callable[[float], None],
        sleep: Callable[[float], bool],
    ) -> None:
        """Take the lock. A live recorded supervisor (`holder_alive`; on Windows any
        holder) refuses at once; otherwise wait up to `wait_s` for a child a killed
        supervisor left running.
        `sleep(s)` returns True when the wait must end (a signal)."""
        waited = 0.0
        while not self.try_acquire():
            if not INHERITED_LOCK or holder_alive():
                raise SupervisorError(E_LOCKED, "another supervisor runs this job")
            if waited >= wait_s:
                raise SupervisorError(
                    E_LOCKED,
                    f"the job lock is still held after {waited:.0f} s: a child process of "
                    "a killed supervisor is still running; wait for it or stop it",
                )
            on_wait(waited)
            step = min(5.0, max(wait_s - waited, 0.1))
            if sleep(step):
                raise SupervisorError(E_LOCKED, "interrupted while waiting for the job lock")
            waited += step

    def release(self) -> None:
        if self.fd is not None:
            _unlock(self.fd)
            self.fd = None


def _try_lock(fd: int) -> bool:
    """Lock `fd` exclusively without blocking (POSIX `flock`, Windows `msvcrt.locking`
    of its first byte); False when another process holds it."""
    try:
        if sys.platform == "win32":
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(fd: int) -> None:
    if sys.platform == "win32":  # pragma: no cover - Windows releases a lock only on request
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    os.close(fd)


def lock_free(path: Path) -> bool:
    """Whether no supervisor (and, on POSIX, no child of one) holds the job lock."""
    if not path.exists():
        return True
    fd = os.open(path, os.O_RDWR)
    if not _try_lock(fd):
        os.close(fd)
        return False
    _unlock(fd)
    return True


def pid_alive(pid: int | None) -> bool:
    """Whether a process with this ID exists (POSIX; another user's process counts)."""
    if pid is None or pid <= 0 or sys.platform == "win32":
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# ---------------------------------------------------------------------------
# Children


@dataclass(frozen=True, slots=True)
class ChildResult:
    exit_code: int
    stdout: Path
    stderr: Path

    def stdout_json(self) -> object:
        """The child's stdout as JSON (`None` when it is not JSON)."""
        try:
            return json.loads(self.stdout.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def stderr_tail(self, n: int = 600) -> str:
        try:
            text = self.stderr.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return text.strip()[-n:]


def start_child(
    module: str, args: Sequence[str], *, stdout: Path, cwd: Path, keep_fd: int | None
) -> tuple[subprocess.Popen[bytes], Path]:
    """Start `sys.executable -m module args` with its output in `stdout` and `.err`."""
    stderr = stdout.with_suffix(".err")
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    with open(stdout, "wb") as out, open(stderr, "wb") as err:
        proc = subprocess.Popen(
            [sys.executable, "-m", module, *args],
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            cwd=cwd,
            env=env,
            pass_fds=(keep_fd,) if keep_fd is not None else (),
        )
    return proc, stderr


# ---------------------------------------------------------------------------
# Model server


def probe_model(url: str, expected: str | None, *, timeout_s: float) -> str | None:
    """`None` when `GET <url>/v1/models` answers and lists `expected` (when given);
    otherwise why not."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url + MODELS_PATH, timeout=timeout_s) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        return f"HTTP {err.code}"
    except (OSError, ValueError) as err:
        return f"{type(err).__name__}: {str(err)[:160]}"
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        return "the answer has no model list"
    ids = [m.get("id") for m in data if isinstance(m, dict)]
    if expected is not None and expected not in ids:
        return f"the server does not list model {expected!r} (it lists {ids[:4]})"
    return None


def backoff(initial_s: float, max_s: float) -> Callable[[], float]:
    """Wait lengths for the probes: `initial_s`, doubling, at most `max_s`."""
    current = [initial_s]

    def step() -> float:
        value = current[0]
        current[0] = min(value * 2, max_s)
        return value

    return step


def monotonic() -> float:
    return time.monotonic()
