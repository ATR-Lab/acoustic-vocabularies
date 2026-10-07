"""LLM client against the mock OpenAI-compatible server (#16).

Acceptance covered here: every call logs temperature 0.7, top_p 0.9, top_k 50,
repetition_penalty 1.0, max_tokens 512 and the derived seed; a call exceeding the 40-s
cap returns `timeout` within 40.5 s (`test_slow_*`: real time, about 40 s; deselect with
`-k "not slow"`); stop reasons map to statuses; no retries; no outbound network.
"""

import asyncio
import json
import logging
import os
import platform
import socket
import threading
import time
from dataclasses import replace
from pathlib import Path

import httpcore
import httpx
import pytest
from av_sound.recipe import Recipe
from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from hypothesis import given, settings
from hypothesis import strategies as st

from av_generation.clock import ManualClock, ScaledClock, SystemClock
from av_generation.constants import (
    FROZEN_DECODING,
    MAX_INPUT_TOKENS,
    MODEL_ID,
    MODEL_REVISION,
    SLOT_CAP_MS,
    DecodingParams,
)
from av_generation.jsonio import messages_sha256, schema_sha256
from av_generation.llm import (
    CHAT_PATH,
    FINISH_OTHER,
    TOKEN_COUNT_TIMEOUT_MS,
    LlmClient,
    OpenAICompatibleClient,
    TokenCountError,
    TokenCountTimeout,
    chat_request_body,
    decoding_schema,
    decoding_schema_sha256,
    loggable_finish_reason,
    parse_chat_response,
    response_format,
)
from av_generation.llm_fake import ScriptedLlmClient
from av_generation.llm_manifest import load_llm_manifest
from av_generation.mock_llm import MOCK_RUNTIME, MockLlmServer, MockReply, count_tokens
from av_generation.outcomes import LlmStatus
from av_generation.records import LlmRequest, RecordError, RecordWriter, read_records
from av_generation.seeds import (
    SeedKeyError,
    a3_seed_key,
    b_seed_key,
    panel_seed_key,
    seed_from_key,
    unwire_seed,
    wire_seed,
)

MESSAGES = [
    {"role": "system", "content": "DEMO instruction (synthetic)."},
    {"role": "user", "content": "DEMO atom K-a1: synthetic meaning text."},
]
KEY = a3_seed_key("DEMO-A-P01", "K-a1", 2, 3)
SLOT = "BK-C-7QX4MN.K-a1.r2s3"


@pytest.fixture
def mock():
    return MockLlmServer()


@pytest.fixture
def base(serve_app, mock):
    return serve_app(mock.app)


def make_client(url, tmp_path, *, clock=None, timeout_ms=SLOT_CAP_MS, log=True, **extra):
    writer = (
        RecordWriter(tmp_path / "llm-requests.jsonl", types=(LlmRequest,), fsync=False)
        if log
        else None
    )
    client = OpenAICompatibleClient(
        url,
        MODEL_ID,
        run_id="DEMO-llm-test",
        clock=clock if clock is not None else SystemClock(),
        request_log=writer,
        runtime=MOCK_RUNTIME,
        model_revision=MODEL_REVISION,
        timeout_ms=timeout_ms,
        **extra,
    )
    return client, writer


def logged(tmp_path):
    return read_records(tmp_path / "llm-requests.jsonl", LlmRequest)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(predicate, timeout_s=5.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# ---------------------------------------------------------------------------
# Request body, decoding schema and log line


def test_decoding_schema_is_the_recipe_schema_with_the_domain():
    schema = decoding_schema()
    assert schema["additionalProperties"] is False
    assert sorted(schema["required"]) == sorted(schema["properties"])
    assert schema["properties"]["total_ms"]["enum"] == [450, 600, 750, 900]
    for name in ("pitches", "rhythm_weights", "gaps_ms", "amplitudes"):
        assert "enum" in schema["properties"][name]["items"]
    schema["mutated"] = True
    assert "mutated" not in decoding_schema()
    assert decoding_schema_sha256() == schema_sha256(decoding_schema())
    assert response_format(decoding_schema()) == {
        "type": "json_schema",
        "json_schema": {
            "name": "acoustic_motif_recipe",
            "schema": decoding_schema(),
            "strict": True,
        },
    }


def test_client_is_an_llm_client(base, tmp_path):
    client, _ = make_client(base, tmp_path)
    assert isinstance(client, LlmClient)


def test_request_carries_frozen_values_seed_and_schema(base, mock, tmp_path):
    client, _ = make_client(base, tmp_path)
    client.propose(MESSAGES, decoding_schema(), KEY, slot_id=SLOT)
    assert len(mock.calls) == 1
    body = mock.calls[0].body
    assert body["temperature"] == 0.7 and body["top_p"] == 0.9 and body["top_k"] == 50
    assert body["repetition_penalty"] == 1.0 and body["max_completion_tokens"] == 512
    assert body["seed"] == wire_seed(seed_from_key(KEY))
    assert unwire_seed(body["seed"]) == seed_from_key(KEY)
    assert body["response_format"] == response_format(decoding_schema())
    assert body["messages"] == MESSAGES and body["model"] == MODEL_ID
    assert body["n"] == 1 and body["stream"] is False
    assert "guided_json" not in body and "max_tokens" not in body


def test_ok_outcome_and_one_log_line(base, mock, tmp_path):
    client, writer = make_client(base, tmp_path)
    outcome = client.propose(MESSAGES, decoding_schema(), KEY, slot_id=SLOT)
    assert outcome.status is LlmStatus.OK and outcome.finish_reason == "stop"
    assert outcome.seed == seed_from_key(KEY)
    assert outcome.tokens_in == count_tokens(MESSAGES) and outcome.tokens_out > 0
    Recipe.from_json(outcome.text)  # the mock samples the decoding schema
    assert writer.count == 1
    (record,) = logged(tmp_path)
    assert record.seed_key == KEY and record.seed == outcome.seed
    assert record.wire_seed == wire_seed(outcome.seed)
    assert (record.temperature, record.top_p, record.top_k) == (0.7, 0.9, 50)
    assert (record.repetition_penalty, record.max_tokens) == (1.0, 512)
    assert record.prompt_sha256 == messages_sha256(MESSAGES)
    assert record.schema_sha256 == decoding_schema_sha256()
    assert record.slot_id == SLOT and record.status is LlmStatus.OK
    assert record.runtime == MOCK_RUNTIME and record.model_revision == MODEL_REVISION
    assert record.latency_ms == outcome.latency_ms and record.tokens_in == outcome.tokens_in
    line = (tmp_path / "llm-requests.jsonl").read_text(encoding="ascii")
    for text in (
        '"temperature":0.7',
        '"top_p":0.9',
        '"top_k":50',
        '"repetition_penalty":1.0',
        '"max_tokens":512',
        f'"seed":{seed_from_key(KEY)}',
        '"response_format":"json_schema"',
    ):
        assert text in line


def test_log_message_shows_the_decoding_values(base, tmp_path, caplog):
    client, _ = make_client(base, tmp_path, log=False)
    with caplog.at_level(logging.INFO, logger="av_generation.llm"):
        client.propose(MESSAGES, decoding_schema(), KEY)
    message = caplog.records[-1].getMessage()
    for text in (
        "temperature=0.7",
        "top_p=0.9",
        "top_k=50",
        "repetition_penalty=1.0",
        "max_tokens=512",
        f"seed={seed_from_key(KEY)}",
    ):
        assert text in message


def test_same_key_same_output_and_b_keys(base, tmp_path):
    client, _ = make_client(base, tmp_path, log=False)
    first = client.propose(MESSAGES, decoding_schema(), KEY)
    again = client.propose(MESSAGES, decoding_schema(), KEY)
    other = client.propose(MESSAGES, decoding_schema(), a3_seed_key("DEMO-A-P01", "K-a1", 2, 2))
    assert first.text == again.text and first.seed == again.seed
    assert other.seed != first.seed
    b_key = b_seed_key("DEMO-bank-01", 1, "P2", "Q-r4", 7)
    assert client.propose(MESSAGES, decoding_schema(), b_key).seed == seed_from_key(b_key)


def test_seed_keys_and_decoding_are_checked_before_any_call(base, mock, tmp_path):
    client, _ = make_client(base, tmp_path)
    with pytest.raises(ValueError, match="A3 or B"):
        client.propose(MESSAGES, decoding_schema(), panel_seed_key("DEMO-A", "orders"))
    with pytest.raises(SeedKeyError):
        client.propose(MESSAGES, decoding_schema(), "A3|DEMO-A-P01|K-a1|02|3")
    with pytest.raises(ValueError, match="bad chat message"):
        client.propose([{"role": "tool", "content": "x"}], decoding_schema(), KEY)
    with pytest.raises(RecordError, match="slot_id"):
        client.propose(MESSAGES, decoding_schema(), KEY, slot_id="not a slot")
    assert mock.calls == []
    other = DecodingParams(0.8, 0.9, 50, 1.0, 512)
    with pytest.raises(ValueError, match="frozen"):
        OpenAICompatibleClient(base, MODEL_ID, run_id="DEMO-x", clock=SystemClock(), decoding=other)
    with pytest.raises(ValueError, match="http"):
        OpenAICompatibleClient("llm:8000", MODEL_ID, run_id="DEMO-x", clock=SystemClock())
    with pytest.raises(ValueError, match="timeout_ms"):
        OpenAICompatibleClient(base, MODEL_ID, run_id="DEMO-x", clock=SystemClock(), timeout_ms=0)
    with pytest.raises(ValueError, match="count_timeout_ms"):
        OpenAICompatibleClient(
            base, MODEL_ID, run_id="DEMO-x", clock=SystemClock(), count_timeout_ms=True
        )
    with pytest.raises(ValueError, match="deadline_ms"):
        client.propose(MESSAGES, decoding_schema(), KEY, deadline_ms=1.5)
    with pytest.raises(ValueError, match="deadline_ms"):
        client.count_prompt_tokens(MESSAGES, deadline_ms=True)
    assert mock.calls == [] and mock.tokenize_calls == 0


def test_client_from_manifest(base, tmp_path):
    manifest = load_llm_manifest()
    client = OpenAICompatibleClient.from_manifest(
        base + "/", manifest, run_id="DEMO-llm-test", clock=SystemClock()
    )
    assert client.base_url == base and client.model == manifest.model.id
    assert client.runtime == f"vllm {manifest.runtime.version}"
    assert client.model_revision == manifest.model.revision
    assert client.propose(MESSAGES, decoding_schema(), KEY).status is LlmStatus.OK


# ---------------------------------------------------------------------------
# Stop reasons -> statuses, no retries


@pytest.mark.parametrize(
    ("reply", "status", "finish"),
    [
        (MockReply("overflow"), LlmStatus.OVERFLOW_OUTPUT, "length"),
        (
            MockReply("text", text='{"total_ms": 600', finish_reason="length"),
            "overflow_output",
            "length",
        ),
        (MockReply("text", text="not json at all"), LlmStatus.OK, "stop"),
        (MockReply("text", text="{}", finish_reason="abort"), LlmStatus.SERVER_ERROR, "abort"),
        (MockReply("error", status_code=500), LlmStatus.SERVER_ERROR, None),
        (MockReply("error", status_code=503), LlmStatus.SERVER_ERROR, None),
        (MockReply("bad_body"), LlmStatus.SERVER_ERROR, None),
        (MockReply("no_choice"), LlmStatus.SERVER_ERROR, None),
    ],
)
def test_stop_reasons_map_to_statuses_without_retry(base, mock, tmp_path, reply, status, finish):
    client, _ = make_client(base, tmp_path)
    mock.push(reply)
    outcome = client.propose(MESSAGES, decoding_schema(), KEY)
    assert outcome.status == status and outcome.finish_reason == finish
    assert len(mock.calls) == 1, "the client never retries"
    (record,) = logged(tmp_path)
    assert record.status == status
    if reply.kind == "overflow":
        assert outcome.text and outcome.tokens_out == 512
    # the next call is a fresh, single call
    assert client.propose(MESSAGES, decoding_schema(), KEY).status is LlmStatus.OK
    assert len(mock.calls) == 2


@pytest.mark.parametrize("finish", ["content filter", "x" * 40, "stop ", "\u00e9t\u00e9"])
def test_an_unloggable_finish_reason_is_a_logged_server_error(base, mock, tmp_path, finish):
    """A finish reason the log schema cannot hold never makes `propose` raise."""
    client, writer = make_client(base, tmp_path)
    mock.push(MockReply("text", text="{}", finish_reason=finish))
    outcome = client.propose(MESSAGES, decoding_schema(), KEY, slot_id=SLOT)
    assert outcome.status is LlmStatus.SERVER_ERROR and outcome.finish_reason == FINISH_OTHER
    assert writer.count == 1
    (record,) = logged(tmp_path)
    assert record.status is LlmStatus.SERVER_ERROR and record.finish_reason == FINISH_OTHER
    assert record.slot_id == SLOT and len(mock.calls) == 1


def test_server_refusals_are_server_errors(base, mock, tmp_path):
    client, _ = make_client(base, tmp_path)
    too_long = [{"role": "user", "content": "word " * (MAX_INPUT_TOKENS + 10)}]
    assert client.count_prompt_tokens(too_long) > MAX_INPUT_TOKENS
    assert client.propose(too_long, decoding_schema(), KEY).status is LlmStatus.SERVER_ERROR
    assert mock.calls[-1].status_code == 400
    wrong_model = OpenAICompatibleClient(
        base, "other/model", run_id="DEMO-llm-test", clock=SystemClock()
    )
    assert wrong_model.propose(MESSAGES, decoding_schema(), KEY).status is LlmStatus.SERVER_ERROR
    assert mock.calls[-1].status_code == 404
    assert len(mock.calls) == 2


def test_unreachable_server_is_a_server_error(tmp_path):
    client, _ = make_client(f"http://127.0.0.1:{free_port()}", tmp_path, timeout_ms=5_000)
    started = time.perf_counter()
    outcome = client.propose(MESSAGES, decoding_schema(), KEY)
    assert outcome.status is LlmStatus.SERVER_ERROR and outcome.text is None
    assert time.perf_counter() - started < 5
    (record,) = logged(tmp_path)
    assert record.status is LlmStatus.SERVER_ERROR and record.tokens_in is None


class CountingBackend(httpcore.AsyncNetworkBackend):
    """A network backend whose every connect fails; counts the attempts (no sockets)."""

    def __init__(self):
        self.attempts = 0
        self.slept = 0.0

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.attempts += 1
        raise httpcore.ConnectError(f"refused by the test backend ({host}:{port})")

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise NotImplementedError

    async def sleep(self, seconds):
        self.slept += seconds  # a retrying transport would back off here


def test_a_failed_connect_is_tried_exactly_once(tmp_path, monkeypatch):
    """No transport-level retries: one connection attempt per call, also for a count."""
    backend = CountingBackend()
    original = httpcore.AsyncConnectionPool.__init__

    def with_backend(self, *args, **kwargs):
        kwargs["network_backend"] = backend
        original(self, *args, **kwargs)

    monkeypatch.setattr(httpcore.AsyncConnectionPool, "__init__", with_backend)
    client, _ = make_client("http://127.0.0.1:9", tmp_path)
    outcome = client.propose(MESSAGES, decoding_schema(), KEY)
    assert outcome.status is LlmStatus.SERVER_ERROR
    assert backend.attempts == 1 and backend.slept == 0
    with pytest.raises(TokenCountError, match="ConnectError"):
        client.count_prompt_tokens(MESSAGES)
    assert backend.attempts == 2 and backend.slept == 0
    (record,) = logged(tmp_path)
    assert record.status is LlmStatus.SERVER_ERROR


# ---------------------------------------------------------------------------
# The 40-s cap


def test_cap_cancels_the_request_and_returns_timeout(base, mock, tmp_path):
    client, _ = make_client(base, tmp_path, timeout_ms=300)
    mock.push(MockReply(delay_s=30))
    started = time.perf_counter()
    outcome = client.propose(MESSAGES, decoding_schema(), KEY, slot_id=SLOT)
    elapsed = time.perf_counter() - started
    assert outcome.status is LlmStatus.TIMEOUT and outcome.text is None
    assert outcome.tokens_in is None and outcome.tokens_out is None
    assert 300 <= outcome.latency_ms < 300 + 500 and elapsed < 0.3 + 0.5
    assert wait_for(lambda: mock.calls and mock.calls[-1].aborted), "server saw no cancel"
    (record,) = logged(tmp_path)
    assert record.status is LlmStatus.TIMEOUT and record.finish_reason is None
    assert record.seed == seed_from_key(KEY) and record.temperature == 0.7


def test_cap_runs_on_the_scaled_run_clock(base, mock, tmp_path):
    client, _ = make_client(base, tmp_path, clock=ScaledClock(100))
    mock.push(MockReply(delay_s=20))
    started = time.perf_counter()
    outcome = client.propose(MESSAGES, decoding_schema(), KEY)
    assert outcome.status is LlmStatus.TIMEOUT
    assert SLOT_CAP_MS <= outcome.latency_ms < SLOT_CAP_MS + 30_000
    assert time.perf_counter() - started < 3


def test_cap_on_a_manual_clock_is_exactly_40_s(base, mock, tmp_path):
    clock = ManualClock()
    client, _ = make_client(base, tmp_path, clock=clock)
    mock.push(MockReply(delay_s=20))
    timer = threading.Timer(0.3, lambda: clock.advance(SLOT_CAP_MS))
    timer.start()
    try:
        outcome = client.propose(MESSAGES, decoding_schema(), KEY)
    finally:
        timer.cancel()
    assert outcome.status is LlmStatus.TIMEOUT and outcome.latency_ms == SLOT_CAP_MS
    (record,) = logged(tmp_path)
    assert record.latency_ms == SLOT_CAP_MS and record.t_ms == 0


def test_answer_before_the_cap_is_kept(base, mock, tmp_path):
    client, _ = make_client(base, tmp_path, timeout_ms=3_000)
    mock.push(MockReply(delay_s=0.2))
    outcome = client.propose(MESSAGES, decoding_schema(), KEY)
    assert outcome.status is LlmStatus.OK and 200 <= outcome.latency_ms < 3_000


@pytest.mark.timeout(120)
def test_slow_real_time_cap_returns_timeout_within_40_5_s(base, mock, tmp_path):
    """Acceptance: a call exceeding 40 s returns `timeout` within 40.5 s (real clock)."""
    client, _ = make_client(base, tmp_path)
    mock.push(MockReply(delay_s=60))
    started = time.perf_counter()
    outcome = client.propose(MESSAGES, decoding_schema(), KEY)
    elapsed = time.perf_counter() - started
    assert outcome.status is LlmStatus.TIMEOUT
    assert SLOT_CAP_MS <= outcome.latency_ms <= 40_500
    assert 40.0 <= elapsed <= 40.5, elapsed
    assert wait_for(lambda: mock.calls and mock.calls[-1].aborted)
    if os.environ.get("CI"):  # evidence for the PR: the measured bound on each OS
        out = Path(__file__).resolve().parents[2] / "generation" / "out" / "ci"
        out.mkdir(parents=True, exist_ok=True)
        note = {
            "os": platform.system(),
            "elapsed_s": round(elapsed, 3),
            "latency_ms": outcome.latency_ms,
            "status": outcome.status.value,
            "server_saw_cancel": mock.calls[-1].aborted,
        }
        (out / f"llm-timeout-{platform.system().lower()}.json").write_text(
            json.dumps(note, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"timeout bound: {note}")


# ---------------------------------------------------------------------------
# Token counting


def test_count_prompt_tokens_through_the_server(base, mock, tmp_path):
    client, _ = make_client(base, tmp_path)
    assert client.count_prompt_tokens(MESSAGES) == count_tokens(MESSAGES)
    no_system = [MESSAGES[1]]
    assert client.count_prompt_tokens(no_system) > count_tokens(no_system, False)
    assert mock.tokenize_calls == 2 and mock.calls == []


def _tokenize_app(response):
    app = FastAPI()

    @app.post("/tokenize")
    def tokenize():
        return response

    return app


@pytest.mark.parametrize(
    "response",
    [
        Response(b"{broken", media_type="application/json"),
        JSONResponse({"count": -1}),
        JSONResponse({"tokens": [1, 2]}),
        JSONResponse({"error": "busy"}, status_code=503),
    ],
)
def test_count_failures_raise_token_count_error(serve_app, tmp_path, response):
    client, _ = make_client(serve_app(_tokenize_app(response)), tmp_path)
    with pytest.raises(TokenCountError):
        client.count_prompt_tokens(MESSAGES)


def test_a_hung_tokenize_ends_at_the_count_cap_on_the_run_clock(base, mock, tmp_path):
    """A hung `/tokenize` cannot hold a slot: the count has its own 5-s run-clock cap."""
    assert TOKEN_COUNT_TIMEOUT_MS == 5_000 and TOKEN_COUNT_TIMEOUT_MS < SLOT_CAP_MS // 4
    assert issubclass(TokenCountTimeout, TokenCountError)
    client, _ = make_client(base, tmp_path, clock=ScaledClock(10))  # propose cap 40 s
    assert client.count_timeout_ms == TOKEN_COUNT_TIMEOUT_MS
    mock.tokenize_delay_s = 30
    started = time.perf_counter()
    with pytest.raises(TokenCountTimeout, match="cancelled after"):
        client.count_prompt_tokens(MESSAGES)
    # 5 s of run time at speed 10 is 0.5 s; the 40-s propose cap would be 4 s
    assert 0.5 <= time.perf_counter() - started < 2.5
    assert wait_for(lambda: mock.tokenize_aborted == 1), "server saw no cancel"


def test_a_hung_tokenize_on_the_real_clock(base, mock, tmp_path):
    client, _ = make_client(base, tmp_path, count_timeout_ms=300)
    mock.tokenize_delay_s = 30
    started = time.perf_counter()
    with pytest.raises(TokenCountTimeout):
        client.count_prompt_tokens(MESSAGES)
    assert 0.3 <= time.perf_counter() - started < 0.3 + 0.5
    assert wait_for(lambda: mock.tokenize_aborted == 1)
    mock.tokenize_delay_s = 0
    assert client.count_prompt_tokens(MESSAGES) == count_tokens(MESSAGES)


def test_count_and_call_share_one_slot_deadline(base, mock, tmp_path):
    """With `deadline_ms` (slot open + 40 s), count plus call never pass the slot cap."""
    clock = ManualClock()
    client, _ = make_client(base, tmp_path, clock=clock)
    deadline = clock.now_ms() + SLOT_CAP_MS  # e.g. ticket.t_open_ms + SLOT_CAP_MS
    assert client.count_prompt_tokens(MESSAGES, deadline_ms=deadline) > 0
    clock.advance(30_000)  # prompt building and counting used 30 s of the slot
    mock.push(MockReply(delay_s=20))
    timer = threading.Timer(0.3, lambda: clock.advance(10_000))
    timer.start()
    try:
        outcome = client.propose(MESSAGES, decoding_schema(), KEY, deadline_ms=deadline)
    finally:
        timer.cancel()
    assert outcome.status is LlmStatus.TIMEOUT and outcome.latency_ms == 10_000
    (record,) = logged(tmp_path)
    assert record.t_ms + record.latency_ms == deadline
    assert wait_for(lambda: mock.calls and mock.calls[-1].aborted)


def test_a_later_cap_than_the_deadline_never_extends_the_call(base, mock, tmp_path):
    clock = ManualClock()
    client, _ = make_client(base, tmp_path, clock=clock)
    mock.push(MockReply(delay_s=20))
    timer = threading.Timer(0.3, lambda: clock.advance(SLOT_CAP_MS))
    timer.start()
    try:
        far = clock.now_ms() + 10 * SLOT_CAP_MS
        outcome = client.propose(MESSAGES, decoding_schema(), KEY, deadline_ms=far)
    finally:
        timer.cancel()
    assert outcome.status is LlmStatus.TIMEOUT and outcome.latency_ms == SLOT_CAP_MS


def test_a_passed_deadline_makes_no_call(base, mock, tmp_path):
    clock = ManualClock(start_ms=50_000)
    client, _ = make_client(base, tmp_path, clock=clock)
    with pytest.raises(TokenCountTimeout, match="deadline"):
        client.count_prompt_tokens(MESSAGES, deadline_ms=50_000)
    outcome = client.propose(MESSAGES, decoding_schema(), KEY, slot_id=SLOT, deadline_ms=40_000)
    assert outcome.status is LlmStatus.TIMEOUT and outcome.latency_ms == 0
    assert outcome.text is None and outcome.seed == seed_from_key(KEY)
    assert mock.calls == [] and mock.tokenize_calls == 0
    (record,) = logged(tmp_path)
    assert record.status is LlmStatus.TIMEOUT and record.slot_id == SLOT
    assert record.latency_ms == 0 and record.t_ms == 50_000


def test_the_fake_client_takes_the_deadline_too():
    """`llm_fake.ScriptedLlmClient` follows the `LlmClient` signature (#17's tests)."""
    fake = ScriptedLlmClient(['{"x": 1}'])
    assert isinstance(fake, LlmClient)
    assert fake.count_prompt_tokens(MESSAGES, deadline_ms=40_000) > 0
    assert fake.propose(MESSAGES, {}, KEY, slot_id=SLOT, deadline_ms=40_000).status == "ok"
    assert fake.calls[0].deadline_ms == 40_000 and fake.calls[0].slot_id == SLOT


def test_count_on_a_dead_server_or_wrong_model(base, tmp_path):
    dead, _ = make_client(f"http://127.0.0.1:{free_port()}", tmp_path)
    with pytest.raises(TokenCountError):
        dead.count_prompt_tokens(MESSAGES)
    wrong = OpenAICompatibleClient(base, "other/model", run_id="DEMO-x", clock=SystemClock())
    with pytest.raises(TokenCountError, match="HTTP 404"):
        wrong.count_prompt_tokens(MESSAGES)


# ---------------------------------------------------------------------------
# Callers: event loops, threads


def test_works_inside_a_running_event_loop(base, mock, tmp_path):
    client, _ = make_client(base, tmp_path, log=False, count_timeout_ms=200)

    async def main():
        count = client.count_prompt_tokens(MESSAGES)
        mock.tokenize_delay_s = 30
        with pytest.raises(TokenCountTimeout):
            client.count_prompt_tokens(MESSAGES)
        return count, client.propose(MESSAGES, decoding_schema(), KEY)

    count, outcome = asyncio.run(main())
    assert count == count_tokens(MESSAGES) and outcome.status is LlmStatus.OK


def test_async_callers_use_a_worker_thread_and_keep_their_loop_free(base, mock, tmp_path):
    """The documented async pattern: `await asyncio.to_thread(client.propose, ...)`."""
    client, _ = make_client(base, tmp_path, log=False)
    mock.push(MockReply(delay_s=0.5))

    async def main():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        task = asyncio.create_task(ticker())
        outcome = await asyncio.to_thread(client.propose, MESSAGES, decoding_schema(), KEY)
        task.cancel()
        return outcome, ticks

    outcome, ticks = asyncio.run(main())
    assert outcome.status is LlmStatus.OK and ticks >= 10, "the caller's loop kept running"


def test_parallel_threads_each_make_one_call(base, mock, tmp_path):
    client, writer = make_client(base, tmp_path)
    keys = [a3_seed_key("DEMO-A-P01", "K-a1", 1, slot) for slot in (1, 2, 3)]
    results = {}
    counts = {}

    def run(key):  # one proposer thread: count, then call, as an A3 slot does
        counts[key] = client.count_prompt_tokens(MESSAGES, deadline_ms=10**9)
        results[key] = client.propose(MESSAGES, decoding_schema(), key, deadline_ms=10**9)

    threads = [threading.Thread(target=run, args=(k,)) for k in keys]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert {r.status for r in results.values()} == {LlmStatus.OK}
    assert set(counts.values()) == {count_tokens(MESSAGES)} and mock.tokenize_calls == 3
    assert len(mock.calls) == 3 and writer.count == 3
    assert {r.seed_key for r in logged(tmp_path)} == set(keys)


# ---------------------------------------------------------------------------
# No outbound network


def test_a_session_makes_no_outbound_connection(base, mock, tmp_path, _no_outbound_network):
    client, _ = make_client(base, tmp_path, timeout_ms=500)
    mock.push(MockReply("overflow"), MockReply(delay_s=5), MockReply("error"))
    client.count_prompt_tokens(MESSAGES)
    statuses = [client.propose(MESSAGES, decoding_schema(), KEY).status for _ in range(4)]
    assert statuses == ["overflow_output", "timeout", "server_error", "ok"]
    assert _no_outbound_network == []


def test_proxy_variables_are_ignored(base, tmp_path, monkeypatch, _no_outbound_network):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "all_proxy"):
        monkeypatch.setenv(name, "http://10.255.255.1:3128")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    client, _ = make_client(base, tmp_path)
    assert client.propose(MESSAGES, decoding_schema(), KEY).status is LlmStatus.OK
    assert client.count_prompt_tokens(MESSAGES) > 0
    assert _no_outbound_network == []


def test_a_server_off_the_station_network_is_refused(tmp_path, _no_outbound_network):
    client, _ = make_client("http://10.255.255.1:8000", tmp_path, timeout_ms=5_000)
    started = time.perf_counter()
    assert client.propose(MESSAGES, decoding_schema(), KEY).status is LlmStatus.SERVER_ERROR
    with pytest.raises(TokenCountError):
        client.count_prompt_tokens(MESSAGES)
    assert time.perf_counter() - started < 5
    assert sum("10.255.255.1" in r for r in _no_outbound_network) >= 2
    _no_outbound_network.clear()  # the guard refused them, as required


# ---------------------------------------------------------------------------
# Properties


@settings(max_examples=200, deadline=None)
@given(
    batch=st.from_regex(r"DEMO-[A-Za-z0-9]{1,12}", fullmatch=True),
    atom=st.sampled_from(["K-a1", "K-r4", "Q-a2", "Q-r3"]),
    round_=st.integers(1, 4),
    slot=st.integers(1, 3),
)
def test_body_seed_is_the_wire_form_of_the_derived_seed(batch, atom, round_, slot):
    key = a3_seed_key(batch, atom, round_, slot)
    seed = seed_from_key(key)
    body = chat_request_body(MODEL_ID, MESSAGES, decoding_schema(), seed)
    assert -(2**63) <= body["seed"] < 2**63 and unwire_seed(body["seed"]) == seed
    d = FROZEN_DECODING
    assert (body["temperature"], body["top_p"], body["top_k"]) == (d.temperature, d.top_p, d.top_k)
    assert (body["repetition_penalty"], body["max_completion_tokens"]) == (1.0, 512)
    json.dumps(body, allow_nan=False)


FINISH = st.one_of(st.none(), st.text(max_size=12))


@settings(max_examples=300, deadline=None)
@given(
    status_code=st.integers(100, 599),
    finish=FINISH,
    content=st.one_of(st.none(), st.text(max_size=40)),
)
def test_status_mapping(status_code, finish, content):
    body = json.dumps(
        {
            "choices": [{"index": 0, "message": {"content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4},
        }
    ).encode()
    result = parse_chat_response(status_code, body)
    if status_code != 200 or content is None:
        assert result.status is LlmStatus.SERVER_ERROR
    elif finish == "stop":
        assert result.status is LlmStatus.OK and result.text == content
    elif finish == "length":
        assert result.status is LlmStatus.OVERFLOW_OUTPUT and result.text == content
    else:
        assert result.status is LlmStatus.SERVER_ERROR
    if status_code == 200:
        assert (result.tokens_in, result.tokens_out) == (3, 4)


LOG_BASE = LlmRequest(
    run_id="DEMO-llm-test",
    seed_key=KEY,
    seed=seed_from_key(KEY),
    wire_seed=wire_seed(seed_from_key(KEY)),
    model=MODEL_ID,
    runtime=MOCK_RUNTIME,
    temperature=0.7,
    top_p=0.9,
    top_k=50,
    repetition_penalty=1.0,
    max_tokens=512,
    prompt_sha256="a" * 64,
    schema_sha256="b" * 64,
    status=LlmStatus.SERVER_ERROR,
    latency_ms=0,
    t_ms=0,
)


@settings(max_examples=300, deadline=None)
@given(
    finish=st.one_of(st.none(), st.text(max_size=48), st.integers(), st.booleans()),
    content=st.one_of(st.none(), st.text(max_size=8)),
    usage=st.one_of(st.none(), st.integers(-5, 2**70), st.text(max_size=3)),
)
def test_every_parsed_response_can_be_logged(finish, content, usage):
    body = json.dumps(
        {
            "choices": [{"index": 0, "message": {"content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": usage, "completion_tokens": usage},
        }
    ).encode()
    result = parse_chat_response(200, body)
    record = replace(
        LOG_BASE,
        status=result.status,
        finish_reason=result.finish_reason,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
    )
    record.check()
    assert result.finish_reason == loggable_finish_reason(finish)
    if result.finish_reason == FINISH_OTHER and finish != FINISH_OTHER:
        assert result.status is LlmStatus.SERVER_ERROR


def test_chat_path_is_the_openai_endpoint():
    assert CHAT_PATH == "/v1/chat/completions"
    assert httpx.URL("http://h:8000" + CHAT_PATH).path == "/v1/chat/completions"
