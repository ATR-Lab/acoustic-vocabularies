"""Run clocks."""

import asyncio
import threading
import time
from datetime import UTC, datetime

import pytest

from av_generation.clock import Clock, ManualClock, ScaledClock, SystemClock, utc_text


def test_protocol_and_utc_text():
    for clock in (SystemClock(), ScaledClock(10), ManualClock()):
        assert isinstance(clock, Clock)
        assert clock.now_ms() >= 0
        assert utc_text(clock.utc_now()).endswith("Z")
    assert utc_text(datetime(2026, 11, 5, 9, 30, 0, 123456, tzinfo=UTC)) == (
        "2026-11-05T09:30:00.123Z"
    )
    with pytest.raises(ValueError):
        utc_text(datetime(2026, 1, 1))  # noqa: DTZ001


def test_manual_clock_sleep_waits_for_advance():
    clock = ManualClock(start_ms=1000)
    done = threading.Event()

    def sleeper():
        clock.sleep(40.0)
        done.set()

    thread = threading.Thread(target=sleeper)
    thread.start()
    time.sleep(0.02)
    assert not done.is_set()
    clock.advance(39_999)
    time.sleep(0.02)
    assert not done.is_set()
    clock.set_ms(41_000)
    thread.join(timeout=2)
    assert done.is_set() and clock.now_ms() == 41_000
    with pytest.raises(ValueError):
        clock.advance(-1)
    with pytest.raises(TimeoutError):
        clock.sleep(1.0, timeout=0.01)


def test_manual_clock_async_and_scaled_clock():
    clock = ManualClock()

    async def main():
        task = asyncio.create_task(clock.asleep(2.0))
        await asyncio.sleep(0.01)
        assert not task.done()
        clock.advance(2000)
        await asyncio.wait_for(task, 1)

    asyncio.run(main())
    fast = ScaledClock(1000)
    start = time.monotonic()
    fast.sleep(40.0)
    asyncio.run(fast.asleep(1.0))
    assert time.monotonic() - start < 1.0
    assert fast.now_ms() >= 30_000  # 0.04 s real at speed 1000 (timer slack allowed)
    with pytest.raises(ValueError):
        ScaledClock(0)
    SystemClock().sleep(0)
    asyncio.run(SystemClock().asleep(0))
