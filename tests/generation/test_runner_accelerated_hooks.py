"""Batch-runner hooks for accelerated runs and scripted sessions (#305, #306).

#305: on an accelerated clock, #16's 5-s token-count cap is run-clock time (25 ms of real
time at 200x), so `open_batch` gives the LLM client a cap that keeps the count's real-time
budget (`default_token_count_cap_ms`) and records it. A slow fake count finishes within
the new cap at 20x and 200x and misses the old one. The count's time is run-clock time on
a clock that only the fake tokenizer moves (`TokenizerClock`), so the result does not
depend on how fast the test machine is. Real-time runs keep 5 s and the same records.

#306: `StudyBatch.served_a1` is the A1 page while a session serves it, and
`run_session(designer_factory=...)` builds the session's bot designer from that page
(the dry run's thinking designer uses it). The operator lines stay the same.
"""

import asyncio
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import Request

from av_generation import _batch_sim as sim
from av_generation import batch_runner as br
from av_generation.clock import ManualClock, ScaledClock, SystemClock
from av_generation.constants import SLOT_CAP_MS
from av_generation.ids import Method, RunKind, proposal_slot_id
from av_generation.llm import TOKEN_COUNT_TIMEOUT_MS, OpenAICompatibleClient, TokenCountTimeout
from av_generation.mock_llm import MockLlmServer
from av_generation.records import SlotRecord, TimingEvent, read_records
from av_generation.webserve import serve_in_thread

ROOT = Path(__file__).resolve().parents[2]
MESSAGES = [{"role": "user", "content": "How many prompt tokens is this?"}]


def demo_inputs():
    return br.load_batch_inputs(
        config=ROOT / "generation/examples/demo-batch-config.json",
        meanings=ROOT / "generation/examples/demo-meanings",
        fallback=ROOT / "sound" / sim.DEMO_FALLBACK_MANIFEST,
        proposers="real",
    )


@pytest.fixture(scope="module")
def mock_llm_url():
    """#16's mock OpenAI-compatible server on loopback, answering at once."""
    with serve_in_thread(MockLlmServer().app) as url:
        yield url


def _open(tmp_path, run_id, url, clock, **kwargs):
    return br.open_batch(
        demo_inputs(),
        tmp_path / run_id,
        kind=RunKind.SYNTHETIC,
        clock=clock,
        llm_url=url,
        **kwargs,
    )


def _llm_startup(batch):
    timing = read_records(batch.layout.log("timing"), TimingEvent)
    return [e for e in timing if e.event.startswith("startup_") and e.component == "llm"]


# ---------------------------------------------------------------------------
# #305: the token-count cap on accelerated clocks


@pytest.mark.parametrize(
    ("clock", "cap_ms"),
    [
        (SystemClock(), TOKEN_COUNT_TIMEOUT_MS),
        (ManualClock(), TOKEN_COUNT_TIMEOUT_MS),
        (ScaledClock(0.5), TOKEN_COUNT_TIMEOUT_MS),
        (ScaledClock(1), TOKEN_COUNT_TIMEOUT_MS),
        (ScaledClock(2), 10_000),
        (ScaledClock(4), 20_000),
        (ScaledClock(8), SLOT_CAP_MS),
        (ScaledClock(20), SLOT_CAP_MS),
        (ScaledClock(200), SLOT_CAP_MS),
    ],
)
def test_default_cap_keeps_five_real_seconds_within_the_slot_cap(clock, cap_ms):
    assert br.default_token_count_cap_ms(clock) == cap_ms
    speed = getattr(clock, "speed", 1.0)
    if speed > 1:  # real-time budget: 5 s, or the whole slot when the slot is shorter
        real_ms = cap_ms / speed
        assert real_ms == pytest.approx(min(TOKEN_COUNT_TIMEOUT_MS, SLOT_CAP_MS / speed))


COUNT_MS = 8_000
"""Run-clock time of the fake count: over #16's 5-s cap, under the 40-s slot cap."""
GUARD_S = 30.0
"""Real-time guard of the fake tokenizer's waits; reached only when the client is broken."""


class TokenizerClock(ScaledClock):
    """A run clock of speed k on which no wall time passes: only the fake tokenizer moves it.

    The runner reads `speed` (`default_token_count_cap_ms`), so its client gets the cap of a
    real k-times clock. Each `asleep` records when it falls due, so the tokenizer sees the
    client's pending cap. A slow CI runner therefore cannot move the run clock."""

    def __init__(self, speed: float) -> None:
        super().__init__(speed)
        self._manual = ManualClock()
        self._lock = threading.Lock()
        self._due: list[int] = []

    def now_ms(self) -> int:
        return self._manual.now_ms()

    def set_ms(self, ms: int) -> int:
        return self._manual.set_ms(ms)

    def sleep(self, seconds: float) -> None:
        self._manual.sleep(seconds)

    async def asleep(self, seconds: float) -> None:
        due = self.now_ms() + round(seconds * 1000)
        with self._lock:
            self._due.append(due)
        try:
            while self.now_ms() < due:
                await asyncio.sleep(0.001)
        finally:
            with self._lock:
                self._due.remove(due)

    def first_due(self) -> int | None:
        with self._lock:
            return min(self._due, default=None)

    def utc_now(self) -> datetime:
        return self._manual.utc_now()


@dataclass
class RunClockTokenizer(MockLlmServer):
    """#16's mock server whose `/tokenize` takes `tokenize_delay_s` of run-clock time on
    `clock`. It moves the clock itself: to the client's cap when the cap falls due first
    (then it waits for the client to cancel, as a busy tokenizer would), otherwise to the
    end of the count (then it answers). Nothing races the wall clock."""

    clock: TokenizerClock | None = None

    async def _wait(self, request: Request, seconds: float) -> bool:
        clock = self.clock
        assert clock is not None
        end = clock.now_ms() + round(seconds * 1000)
        loop = asyncio.get_running_loop()
        guard = loop.time() + GUARD_S
        while (cap := clock.first_due()) is None and loop.time() < guard:
            await asyncio.sleep(0.001)  # the client starts its cap as it posts
        if cap is None or cap >= end:
            clock.set_ms(end)
            return False
        clock.set_ms(cap)
        while loop.time() < guard:
            if await request.is_disconnected():
                return True
            await asyncio.sleep(0.001)
        return False


@pytest.mark.parametrize("speed", [20, 200])
def test_an_accelerated_runner_keeps_the_count_within_its_real_time_budget(tmp_path, speed):
    """The fake count takes 8 s of run-clock time: longer than #16's 5 s (250 ms real at
    20x, 25 ms at 200x) and well under the new 40-s cap (2 s and 200 ms real). The
    runner's client counts in 8 s; a client with #16's default cap on the same clock is
    cancelled at exactly 5 s. Only the fake tokenizer moves the clock, so how fast the
    machine is does not matter."""
    clock = TokenizerClock(speed)
    server = RunClockTokenizer(tokenize_delay_s=COUNT_MS / 1000, clock=clock)
    with serve_in_thread(server.app) as url:
        batch = _open(tmp_path, f"DEMO-count-cap-{speed}", url, clock)
        assert batch.llm is not None
        assert batch.token_count_cap_ms == batch.llm.count_timeout_ms == SLOT_CAP_MS
        start = clock.now_ms()
        assert batch.llm.count_prompt_tokens(MESSAGES) > 0
        assert clock.now_ms() - start == COUNT_MS
        old = OpenAICompatibleClient(url, batch.llm.model, run_id="DEMO-x", clock=batch.clock)
        assert old.count_timeout_ms == TOKEN_COUNT_TIMEOUT_MS
        with pytest.raises(TokenCountTimeout, match=f"after {TOKEN_COUNT_TIMEOUT_MS} ms"):
            old.count_prompt_tokens(MESSAGES)
    assert (server.tokenize_calls, server.tokenize_aborted) == (2, 1)
    # the run records the cap it used, in the sitting's `llm` startup events
    startup = _llm_startup(batch)
    assert [e.event for e in startup] == ["startup_start", "startup_end"]
    assert {e.detail for e in startup} == {
        f"{br.STARTUP_LLM_DETAIL}; token-count cap {SLOT_CAP_MS} ms (run clock)"
    }


@pytest.mark.parametrize("clock", [SystemClock(), ManualClock()], ids=["real", "manual"])
def test_real_time_runs_keep_the_five_second_cap_and_their_records(tmp_path, mock_llm_url, clock):
    batch = _open(tmp_path, "DEMO-count-cap-rt", mock_llm_url, clock)
    assert batch.llm is not None
    assert batch.token_count_cap_ms == batch.llm.count_timeout_ms == TOKEN_COUNT_TIMEOUT_MS
    assert {e.detail for e in _llm_startup(batch)} == {br.STARTUP_LLM_DETAIL}


def test_an_explicit_cap_is_used_and_recorded(tmp_path, mock_llm_url):
    batch = _open(
        tmp_path, "DEMO-count-cap-set", mock_llm_url, SystemClock(), token_count_cap_ms=12_000
    )
    assert batch.llm is not None
    assert batch.token_count_cap_ms == batch.llm.count_timeout_ms == 12_000
    assert {e.detail for e in _llm_startup(batch)} == {
        f"{br.STARTUP_LLM_DETAIL}; token-count cap 12000 ms (run clock)"
    }
    for bad in (0, -1, 1.5, True):
        with pytest.raises(ValueError, match="token_count_cap_ms"):
            _open(
                tmp_path, "DEMO-count-cap-bad", mock_llm_url, SystemClock(), token_count_cap_ms=bad
            )
    assert not (tmp_path / "DEMO-count-cap-bad").exists()


def test_simulated_proposers_have_no_count_cap(tmp_path):
    batch = br.open_batch(
        br.load_batch_inputs(
            config=ROOT / "generation/examples/demo-batch-config.json",
            meanings=ROOT / "generation/examples/demo-meanings",
            fallback=ROOT / "sound" / sim.DEMO_FALLBACK_MANIFEST,
            proposers="sim",
        ),
        tmp_path / "DEMO-count-cap-sim",
        kind=RunKind.SYNTHETIC,
        clock=ScaledClock(200),
        proposers="sim",
    )
    assert batch.llm is None and batch.token_count_cap_ms is None and batch.served_a1 is None


# ---------------------------------------------------------------------------
# #306: the served A1 page and a designer factory


def test_a_designer_factory_drives_the_bot_designer_on_the_served_a1_page(tmp_path, mock_llm_url):
    """Virtual time (`ManualClock`, driven by the bot panel): one appointment whose bot
    designer comes from the caller's factory, built from the served A1 page."""
    batch = _open(tmp_path, "DEMO-designer-factory", mock_llm_url, ManualClock())
    assert batch.a1 is not None and batch.served_a1 is None
    config = batch.orchestrator.config
    invalid = proposal_slot_id(config.book_of(Method.A1).book_id, config.atom_order[0], 1, 2)
    built = []

    def factory(page):
        built.append((page, batch.served_a1))
        return sim.BotDesigner(
            page,
            run_id=batch.layout.run_id,
            designer_id=batch.a1.designer_id,
            invalid_slots=frozenset({invalid}),
        )

    lines = []
    br.run_session(
        batch,
        appointment=1,
        panel="bots",
        designer="bot",
        designer_factory=factory,
        a1_port=0,
        log=lines.append,
    )
    [(page, served)] = built
    assert served == page and page.startswith("http://127.0.0.1:")
    assert lines[0] == f"A1 designer page: {page}"  # the operator line is unchanged
    assert lines[-1].startswith("Atoms finished: 4/16")
    assert batch.served_a1 is None  # nothing is served after the session
    # the factory's designer worked every A1 slot of the appointment
    designer = batch.bot_designer
    assert isinstance(designer, sim.BotDesigner) and designer.errors == []
    a1 = {
        s.slot_id: s.outcome.value
        for s in read_records(batch.layout.log("slot"), SlotRecord)
        if s.method is Method.A1
    }
    assert len(a1) == 48 and a1[invalid] == "invalid_json"
    assert set(designer.submitted) == set(a1)


def test_served_a1_is_set_only_while_the_session_serves(tmp_path, mock_llm_url):
    """A station session that fails in `on_panel`: the A1 page was served before the
    panel, and both handles are cleared when the session ends."""
    batch = _open(tmp_path, "DEMO-served-a1", mock_llm_url, ScaledClock(100))
    seen = []

    class Stop(Exception):
        pass

    def on_panel(base_url):
        seen.append((batch.served_a1, batch.served_panel))
        raise Stop

    lines = []
    with pytest.raises(Stop):
        br.run_session(
            batch, panel="stations", a1_port=0, panel_port=0, on_panel=on_panel, log=lines.append
        )
    [(page, panel)] = seen
    assert lines[0] == f"A1 designer page: {page}" and panel is not None
    assert batch.served_a1 is None and batch.served_panel is None


def test_a_designer_factory_needs_the_bot_designer_mode(tmp_path, mock_llm_url):
    batch = _open(tmp_path, "DEMO-designer-mode", mock_llm_url, ScaledClock(100))

    def factory(page):  # pragma: no cover - never called
        raise AssertionError(page)

    lines = []
    for extra in (
        {"designer": "kiosk"},
        {"designer": "bot", "designer_invalid_slots": frozenset({"x"})},
        {"designer": "bot", "designer_timeout_slots": frozenset({"x"})},
    ):
        with pytest.raises(br.RunnerError) as err:
            br.run_session(
                batch, panel="bots", a1_port=0, designer_factory=factory, log=lines.append, **extra
            )
        assert err.value.code == br.E_MODE
    assert lines == [] and batch.served_a1 is None
