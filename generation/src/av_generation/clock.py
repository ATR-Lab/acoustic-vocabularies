"""Run clocks: real time, accelerated time and manually driven time.

Every component that times something (slot caps, rating slots, timing events, the
accelerated-clock batch run of #20 and #22) takes a `Clock` instead of reading the
system clock. Times in records are integer milliseconds since the clock started
(`now_ms()`, monotonic). Wall-clock UTC appears only in run manifests and timing events
(`utc_now()`), never in a decision.

- `SystemClock`: real monotonic time.
- `ScaledClock(speed)`: virtual time runs `speed` times faster than real time; a 40-s
  slot cap takes 0.4 s at `speed=100`. Use it for accelerated runs of real components.
- `ManualClock`: time moves only when a test calls `advance()`; `sleep()` blocks until
  the clock reaches the target.
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Monotonic run clock in integer milliseconds."""

    def now_ms(self) -> int:
        """Milliseconds since the clock started (monotonic, never decreases)."""
        ...

    def sleep(self, seconds: float) -> None:
        """Block the calling thread for `seconds` of clock time."""
        ...

    async def asleep(self, seconds: float) -> None:
        """Suspend the calling task for `seconds` of clock time."""
        ...

    def utc_now(self) -> datetime:
        """Wall-clock UTC for manifests and timing events (consistent with `now_ms`)."""
        ...


def utc_text(moment: datetime) -> str:
    """ISO 8601 UTC with milliseconds and `Z`, e.g. `2026-11-05T09:30:00.000Z`."""
    if moment.tzinfo is None:
        raise ValueError("utc_text needs an aware datetime")
    moment = moment.astimezone(UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


class SystemClock:
    """Real time. `now_ms()` counts from construction."""

    def __init__(self) -> None:
        self._start_ns = time.monotonic_ns()
        self._start_utc = datetime.now(UTC)

    def now_ms(self) -> int:
        return (time.monotonic_ns() - self._start_ns) // 1_000_000

    def sleep(self, seconds: float) -> None:
        time.sleep(max(0.0, seconds))

    async def asleep(self, seconds: float) -> None:
        await asyncio.sleep(max(0.0, seconds))

    def utc_now(self) -> datetime:
        return self._start_utc + timedelta(milliseconds=self.now_ms())


class ScaledClock:
    """Accelerated real time: one real second is `speed` clock seconds."""

    def __init__(self, speed: float, *, start_utc: datetime | None = None) -> None:
        if not speed > 0:
            raise ValueError("speed must be positive")
        self.speed = float(speed)
        self._start_ns = time.monotonic_ns()
        self._start_utc = start_utc if start_utc is not None else datetime.now(UTC)

    def now_ms(self) -> int:
        return int((time.monotonic_ns() - self._start_ns) * self.speed) // 1_000_000

    def sleep(self, seconds: float) -> None:
        time.sleep(max(0.0, seconds) / self.speed)

    async def asleep(self, seconds: float) -> None:
        await asyncio.sleep(max(0.0, seconds) / self.speed)

    def utc_now(self) -> datetime:
        return self._start_utc + timedelta(milliseconds=self.now_ms())


class ManualClock:
    """Test clock: time stands still until `advance()` or `set_ms()` moves it."""

    def __init__(self, start_ms: int = 0, *, start_utc: datetime | None = None) -> None:
        self._now = int(start_ms)
        self._cond = threading.Condition()
        self._start_utc = (
            start_utc if start_utc is not None else datetime(2026, 1, 1, tzinfo=UTC)
        ) - timedelta(milliseconds=self._now)

    def now_ms(self) -> int:
        with self._cond:
            return self._now

    def advance(self, ms: int) -> int:
        """Move the clock forward by `ms` and wake sleepers; returns the new time."""
        if ms < 0:
            raise ValueError("a monotonic clock cannot go back")
        with self._cond:
            self._now += int(ms)
            self._cond.notify_all()
            return self._now

    def set_ms(self, ms: int) -> int:
        """Move the clock forward to `ms` (no-op if already there)."""
        with self._cond:
            return self.advance(max(0, int(ms) - self._now))

    def sleep(self, seconds: float, *, timeout: float | None = 30.0) -> None:
        """Block until the clock has advanced by `seconds` (real `timeout` guards tests)."""
        with self._cond:
            target = self._now + int(round(seconds * 1000))
            if not self._cond.wait_for(lambda: self._now >= target, timeout=timeout):
                raise TimeoutError("ManualClock.sleep: nobody advanced the clock")

    async def asleep(self, seconds: float) -> None:
        target = self.now_ms() + int(round(seconds * 1000))
        while self.now_ms() < target:
            await asyncio.sleep(0.001)

    def utc_now(self) -> datetime:
        return self._start_utc + timedelta(milliseconds=self.now_ms())
