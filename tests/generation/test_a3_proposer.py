"""A3 proposer and Study B slot proposer (#17): one call per slot, no repair or retry,
same-round visibility, and an integration run against a mock OpenAI-compatible server
that returns bad JSON, long output and slow responses."""

import asyncio
import json
import logging
import shutil
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from av_generation import _demo_ledger as demo
from av_generation.a3 import RAW_OUTPUT_MAX_CHARS, A3Proposer, BSlotProposer, slot_deadline_ms
from av_generation.clock import ManualClock, SystemClock
from av_generation.constants import SLOT_CAP_MS
from av_generation.ids import Method, Study
from av_generation.jsonio import messages_sha256, schema_sha256
from av_generation.ledger import SlotCapExceeded
from av_generation.llm import LlmClient, RawOutcome, TokenCountError
from av_generation.llm_fake import ScriptedLlmClient
from av_generation.outcomes import LlmStatus, SlotOutcome
from av_generation.prompts import PromptSetError
from av_generation.proposers import RoundProposer
from av_generation.records import SlotRecord, SlotRefusal, read_records
from av_generation.seeds import a3_seed_key, b_seed_key, seed_from_key, wire_seed

ROOT = Path(__file__).resolve().parents[2]
VALID = json.dumps(demo.VALID.to_dict())


def scripted(entries, **kwargs):
    return ScriptedLlmClient(entries, **kwargs)


def proposer_for(client, tmp_path, clock=None):
    clock = clock or ManualClock(0)
    ledger = demo.demo_ledger(tmp_path, clock)
    return demo.a3_proposer(client, ledger, clock), ledger, clock


def test_a3_is_a_round_proposer(tmp_path):
    proposer, _, _ = proposer_for(scripted([]), tmp_path)
    assert isinstance(proposer, RoundProposer) and proposer.method is Method.A3


def test_one_round_logs_seeds_hashes_and_calls(tmp_path):
    client = scripted([VALID, "not json", VALID])
    proposer, ledger, clock = proposer_for(client, tmp_path)
    result = proposer.propose_round(demo.demo_request(1, clock=clock))
    assert (result.method, result.book_id, result.atom_id, result.round) == (
        Method.A3,
        demo.BOOK,
        demo.A_ATOM,
        1,
    )
    assert [r.outcome for r in result.records] == [
        SlotOutcome.VALID,
        SlotOutcome.INVALID_JSON,
        SlotOutcome.VALID,
    ]
    assert ledger.records() == result.records
    schema_hash = schema_sha256(demo.demo_decoding_schema())
    for slot, (record, call) in enumerate(zip(result.records, client.calls, strict=True), 1):
        key = a3_seed_key(demo.BATCH, demo.A_ATOM, 1, slot)
        assert record.seed_key == call.seed_key == key
        assert record.seed == call.seed == seed_from_key(key)
        assert record.slot_id == call.slot_id
        assert record.prompt_sha256 == messages_sha256(call.messages)
        assert record.schema_sha256 == schema_hash
        assert record.llm_status is LlmStatus.OK and record.latency_ms == 5
    valid = result.records[0]
    assert valid.recipe == demo.VALID.to_dict() and valid.recipe_sha256 == demo.VALID.sha256()
    assert valid.pcm_sha256 and valid.file_sha256 and valid.raw_output == VALID
    bad = result.records[1]
    assert bad.validator_codes == ("E_JSON",) and bad.raw_output == "not json"
    assert bad.validator_messages[0].startswith("output is not exactly one JSON object")
    assert bad.recipe is None and bad.pcm_sha256 is None


def test_later_slots_see_earlier_same_round_proposals(tmp_path):
    client = scripted([VALID, "{}", VALID])
    proposer, _, clock = proposer_for(client, tmp_path)
    proposer.propose_round(demo.demo_request(1, clock=clock))
    same = [json.loads(c.messages[1]["content"])["feedback"]["same_round"] for c in client.calls]
    assert [len(s) for s in same] == [0, 1, 2]
    assert same[2][0]["recipe"] == demo.VALID.to_dict()
    assert same[2][1]["outcome"] == "schema_violation"
    assert all("ratings" not in entry for entries in same for entry in entries)


def test_feedback_reaches_the_next_round_only_after_closing(tmp_path):
    client = scripted([VALID] * 6)
    proposer, ledger, clock = proposer_for(client, tmp_path)
    proposer.propose_round(demo.demo_request(1, clock=clock))
    feedback = demo.demo_feedback(ledger.records(), demo.A_ATOM, 1)
    proposer.propose_round(demo.demo_request(2, clock=clock, feedback=feedback))
    contexts = [json.loads(c.messages[1]["content"])["feedback"] for c in client.calls]
    assert [len(c["candidates"]) for c in contexts] == [0, 0, 0, 3, 3, 3]
    assert all(len(c["ratings"]) == 3 for c in contexts[3]["candidates"])
    assert contexts[3]["incumbent_slot_index"] == 1


def test_a_closed_slot_is_never_retried(tmp_path):
    client = scripted(["{bad", "```json\n" + VALID + "\n```", VALID + " done"])
    proposer, ledger, clock = proposer_for(client, tmp_path)
    result = proposer.propose_round(demo.demo_request(1, clock=clock))
    assert [r.outcome for r in result.records] == [SlotOutcome.INVALID_JSON] * 3
    assert client.call_count == 3 and len(ledger.records()) == 3


def task_slot(messages):
    """The slot (A3: round slot, B: cell slot) a prompt is for."""
    return json.loads(messages[-1]["content"])["task"]["slot"]


def answer_after(clock, ms, text=VALID):
    """A scripted `ok` call that takes `ms` of run-clock time (and counts no tokens)."""

    def entry(messages, schema, seed_key):
        clock.advance(ms)
        return RawOutcome(LlmStatus.OK, text, ms, None, 20, 0, "stop")

    return entry


def test_slot_deadline_is_the_cap_or_the_window_end():
    assert slot_deadline_ms(1_000) == 1_000 + SLOT_CAP_MS == 41_000
    assert slot_deadline_ms(1_000, 120_000) == 41_000
    assert slot_deadline_ms(100_000, 120_000) == 120_000


def test_window_end_closes_remaining_slots_without_calls(tmp_path):
    clock = ManualClock(0)
    client = scripted([answer_after(clock, 39_000), answer_after(clock, 12_000)])
    proposer, ledger, _ = proposer_for(client, tmp_path, clock)
    request = replace(demo.demo_request(1, clock=clock), window_end_ms=50_000)
    first, second, third = proposer.propose_round(request).records
    assert first.outcome is SlotOutcome.VALID and first.t_ms == 39_000
    # slot 2 opened at 39 s; its answer came at 51 s, after the window ended (50 s)
    assert (second.outcome, second.llm_status, second.t_ms) == (
        SlotOutcome.TIMEOUT,
        LlmStatus.OK,
        51_000,
    )
    assert second.raw_output == VALID and second.recipe is None and second.validator_codes == ()
    assert second.latency_ms == 12_000 and second.tokens_in is not None
    # slot 3 opens after the window: no count, no call
    assert (third.outcome, third.llm_status, third.tokens_in) == (SlotOutcome.TIMEOUT, None, None)
    assert client.call_count == 2 and client.token_counts == 2
    assert len(ledger.records()) == 3


def test_the_40_s_cap_covers_the_token_count_and_the_call(tmp_path):
    """Slot 1: the count takes 40 s, so no call. Slot 2: count 25 s + call 15 s, the answer
    comes exactly at the cap and counts. Slot 3: count 25 s + call 15.001 s, too late."""
    clock = ManualClock(0)
    count_ms = {1: SLOT_CAP_MS, 2: 25_000, 3: 25_000}

    def slow_count(messages):
        clock.advance(count_ms[task_slot(messages)])
        return 1_000

    client = scripted(
        [answer_after(clock, 15_000), answer_after(clock, 15_001)], token_counter=slow_count
    )
    proposer, ledger, _ = proposer_for(client, tmp_path, clock)
    first, second, third = proposer.propose_round(demo.demo_request(1, clock=clock)).records
    assert [(r.t_open_ms, r.t_ms) for r in (first, second, third)] == [
        (0, 40_000),
        (40_000, 80_000),
        (80_000, 120_001),
    ]
    assert (first.outcome, first.llm_status, first.tokens_in) == (
        SlotOutcome.TIMEOUT,
        None,
        1_000,
    )
    assert second.outcome is SlotOutcome.VALID and second.llm_status is LlmStatus.OK
    assert (third.outcome, third.llm_status, third.raw_output) == (
        SlotOutcome.TIMEOUT,
        LlmStatus.OK,
        VALID,
    )
    assert third.recipe is None and third.pcm_sha256 is None
    assert [c.slot_id for c in client.calls] == [second.slot_id, third.slot_id]
    assert client.token_counts == 3 and len(ledger.records()) == 3


def test_overflow_and_failed_counts_win_over_a_slow_count(tmp_path):
    """Once the count is known, `overflow_input` (a property of the prompt) and a failed
    count are logged as such even if the count also used up the slot's time."""
    clock = ManualClock(0)

    def slow_count(messages):
        clock.advance(SLOT_CAP_MS)
        if task_slot(messages) == 2:
            raise TokenCountError("tokenize timed out")
        return 16_385 if task_slot(messages) == 1 else 10

    client = scripted([], token_counter=slow_count)
    proposer, _, _ = proposer_for(client, tmp_path, clock)
    records = proposer.propose_round(demo.demo_request(1, clock=clock)).records
    assert [(r.outcome, r.llm_status) for r in records] == [
        (SlotOutcome.OVERFLOW_INPUT, None),
        (SlotOutcome.INVALID_JSON, LlmStatus.SERVER_ERROR),
        (SlotOutcome.TIMEOUT, None),
    ]
    assert client.call_count == 0


def test_b_slots_have_the_40_s_cap(tmp_path):
    clock = ManualClock(0)
    ledger = demo.demo_ledger(tmp_path, clock)

    def slow_count(messages):
        if task_slot(messages) == 1:
            clock.advance(SLOT_CAP_MS)
        return 10

    client = scripted(
        [answer_after(clock, SLOT_CAP_MS + 1), answer_after(clock, SLOT_CAP_MS)],
        token_counter=slow_count,
    )
    proposer = BSlotProposer(
        client, ledger, demo.demo_prompt_set(), demo.demo_decoding_schema(), clock=clock
    )
    records = [
        proposer.propose_slot(demo.demo_cell(slot, ledger.records()), seed_namespace=demo.BANK)
        for slot in (1, 2, 3)
    ]
    assert [(r.outcome, r.llm_status) for r in records] == [
        (SlotOutcome.TIMEOUT, None),
        (SlotOutcome.TIMEOUT, LlmStatus.OK),
        (SlotOutcome.VALID, LlmStatus.OK),
    ]
    assert client.call_count == 2


@pytest.mark.parametrize("study", ["A", "B"])
def test_a_decoding_schema_the_prompt_does_not_show_is_refused(tmp_path, study):
    clock = ManualClock(0)
    ledger = demo.demo_ledger(tmp_path, clock)
    schema = demo.demo_decoding_schema()
    schema["properties"]["total_ms"]["enum"] = [450, 600]
    cls = A3Proposer if study == "A" else BSlotProposer
    with pytest.raises(PromptSetError, match="must agree"):
        cls(scripted([]), ledger, demo.demo_prompt_set(), schema, clock=clock)
    assert ledger.records() == () and ledger.open_tickets() == ()


def test_a_raising_client_closes_the_slot_as_server_error(tmp_path, caplog):
    def boom(messages, schema, seed_key):
        raise RuntimeError("socket closed")

    class Weird(ScriptedLlmClient):
        def count_prompt_tokens(self, messages):
            if self.call_count == 2:
                raise OSError("unexpected")
            return super().count_prompt_tokens(messages)

    client = Weird([boom, RawOutcome(LlmStatus.OK, VALID, 5, 10, 10, 0, "stop")])
    proposer, _, clock = proposer_for(client, tmp_path)
    with caplog.at_level(logging.WARNING, logger="av_generation.a3"):
        result = proposer.propose_round(demo.demo_request(1, clock=clock))
    assert [(r.outcome, r.llm_status) for r in result.records] == [
        (SlotOutcome.INVALID_JSON, LlmStatus.SERVER_ERROR),
        (SlotOutcome.VALID, LlmStatus.OK),
        (SlotOutcome.INVALID_JSON, LlmStatus.SERVER_ERROR),
    ]
    assert "propose failed" in caplog.text and "count_prompt_tokens raised" in caplog.text


def test_malformed_client_values_never_break_the_record(tmp_path):
    counts = iter([-1, "many", 10])
    entries = [RawOutcome("weird", VALID, -5, -1, None, 0, None)]  # type: ignore[arg-type]
    client = scripted(entries, token_counter=lambda m: next(counts))
    proposer, ledger, clock = proposer_for(client, tmp_path)
    records = proposer.propose_round(demo.demo_request(1, clock=clock)).records
    assert [(r.outcome, r.llm_status) for r in records] == [
        (SlotOutcome.INVALID_JSON, LlmStatus.SERVER_ERROR)
    ] * 3
    assert client.call_count == 1 and len(ledger.records()) == 3
    assert records[2].tokens_in == 10 and records[2].latency_ms is None


def test_long_raw_output_is_clipped_and_server_tokens_win(tmp_path):
    long_text = "x" * (RAW_OUTPUT_MAX_CHARS + 10)
    entries = [
        RawOutcome(LlmStatus.OK, long_text, 7, 1234, 512, 0, "stop"),
        RawOutcome(LlmStatus.OK, VALID, 7, None, 20, 0, "stop"),
        RawOutcome(LlmStatus.SERVER_ERROR, None, 3, None, None, 0, None),
    ]
    client = scripted(entries, token_counter=lambda m: 999)
    proposer, _, clock = proposer_for(client, tmp_path)
    first, second, third = proposer.propose_round(demo.demo_request(1, clock=clock)).records
    assert len(first.raw_output) == RAW_OUTPUT_MAX_CHARS and first.tokens_in == 1234
    assert first.outcome is SlotOutcome.INVALID_JSON
    assert second.tokens_in == 999 and second.outcome is SlotOutcome.VALID
    assert third.outcome is SlotOutcome.INVALID_JSON and third.llm_status is LlmStatus.SERVER_ERROR


def test_a_thirteenth_slot_is_refused_before_any_call(tmp_path):
    client = scripted([VALID] * 12)
    proposer, ledger, clock = proposer_for(client, tmp_path)
    for rnd in (1, 2, 3, 4):
        feedback = demo.demo_feedback(ledger.records(), demo.A_ATOM, rnd - 1)
        proposer.propose_round(demo.demo_request(rnd, clock=clock, feedback=feedback))
    feedback = demo.demo_feedback(ledger.records(), demo.A_ATOM, 4)
    with pytest.raises(SlotCapExceeded):
        proposer.propose_round(demo.demo_request(4, clock=clock, feedback=feedback))
    assert client.call_count == 12 and len(ledger.records()) == 12
    [refusal] = read_records(tmp_path / "slot-refusals.jsonl", SlotRefusal)
    assert refusal.reason == "slot_cap" and refusal.method is Method.A3


@pytest.mark.parametrize("problem", ["method", "label", "run", "book"])
def test_bad_requests_raise_before_anything_is_charged(tmp_path, problem):
    client = scripted([])
    proposer, ledger, clock = proposer_for(client, tmp_path)
    request = demo.demo_request(1, clock=clock)
    if problem == "method":
        request = replace(request, method=Method.A2)
    elif problem == "label":
        request = replace(request, semantic_label=None)
    elif problem == "run":
        request = replace(request, run_id="DEMO-run-99")
    else:
        request = replace(request, book_id="DEMO-BK-7QX4")
    with pytest.raises(ValueError):
        proposer.propose_round(request)
    assert ledger.records() == () and ledger.open_tickets() == ()


def test_b_slot_records_and_seeds(tmp_path):
    clock = ManualClock(0)
    ledger = demo.demo_ledger(tmp_path, clock)
    records, client = demo.run_b_slots(
        [SlotOutcome.VALID, SlotOutcome.DUPLICATE, SlotOutcome.INCOMPATIBLE], ledger, clock
    )
    for slot, (record, call) in enumerate(zip(records, client.calls, strict=True), 1):
        key = b_seed_key(demo.BANK, 1, "P1", demo.A_ATOM, slot)
        assert record.seed_key == call.seed_key == key and record.slot_index == slot
        assert (record.study, record.method, record.bank_id, record.attempt) == (
            Study.B,
            Method.B,
            demo.BANK,
            1,
        )
        assert record.batch_id is None and record.round is None
    duplicate, incompatible = records[1], records[2]
    assert duplicate.validator_codes == () and duplicate.pcm_sha256 is not None
    assert set(incompatible.validator_codes) == {"E_DUPLICATE", "E_SEPARATION"}


def test_b_proposer_uses_the_validator_config_threshold(tmp_path):
    clock = ManualClock(0)
    ledger = demo.demo_ledger(tmp_path, clock)
    proposer = BSlotProposer(
        scripted([VALID]), ledger, demo.demo_prompt_set(), demo.demo_decoding_schema(), clock=clock
    )
    record = proposer.propose_slot(demo.demo_cell(1), seed_namespace=demo.BANK)
    assert record.outcome is SlotOutcome.VALID


# ---------------------------------------------------------------------------
# Integration against a mock OpenAI-compatible server (HTTP, loopback only)

MODEL = "Qwen/Qwen2.5-7B-Instruct"
COMMITTED_TEXT = json.dumps(demo.demo_book_state().committed[0].recipe.to_dict())
BEHAVIOUR = {
    1: "ok",
    2: "bad_json",
    3: "long_output",
    4: "slow",
    5: "http_500",
    6: "code_fence",
    7: "huge_prompt",
    8: "tokenize_error",
    9: "committed_copy",
    10: "out_of_domain",
    11: "two_objects",
    12: "ok",
}
EXPECTED = {
    1: (SlotOutcome.VALID, LlmStatus.OK),
    2: (SlotOutcome.INVALID_JSON, LlmStatus.OK),
    3: (SlotOutcome.OVERFLOW_OUTPUT, LlmStatus.OVERFLOW_OUTPUT),
    4: (SlotOutcome.TIMEOUT, LlmStatus.TIMEOUT),
    5: (SlotOutcome.INVALID_JSON, LlmStatus.SERVER_ERROR),
    6: (SlotOutcome.INVALID_JSON, LlmStatus.OK),
    7: (SlotOutcome.OVERFLOW_INPUT, None),
    8: (SlotOutcome.INVALID_JSON, LlmStatus.SERVER_ERROR),
    9: (SlotOutcome.DUPLICATE, LlmStatus.OK),
    10: (SlotOutcome.OUT_OF_DOMAIN, LlmStatus.OK),
    11: (SlotOutcome.INVALID_JSON, LlmStatus.OK),
    12: (SlotOutcome.VALID, LlmStatus.OK),
}
SLOW_S = 4.0
CLIENT_TIMEOUT_S = 2.0


def mock_llm_app(requests: Counter) -> FastAPI:
    """A mock vLLM server: `/tokenize` and `/v1/chat/completions`, behaviour chosen by the
    slot index the prompt states."""
    app = FastAPI()

    def behaviour(body) -> tuple[int, str]:
        task = json.loads(body["messages"][-1]["content"])["task"]
        return task["slot_index"], BEHAVIOUR[task["slot_index"]]

    @app.post("/tokenize")
    async def tokenize(request: Request):
        body = await request.json()
        index, kind = behaviour(body)
        requests[("tokenize", index)] += 1
        assert body["add_generation_prompt"] is True
        if kind == "tokenize_error":
            return JSONResponse({"error": "tokenizer unavailable"}, status_code=500)
        words = sum(len(m["content"].split()) for m in body["messages"])
        count = 20_000 if kind == "huge_prompt" else words
        return {"count": count, "max_model_len": 16_896, "tokens": []}

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()
        index, kind = behaviour(body)
        requests[("chat", index)] += 1
        assert body["response_format"]["type"] == "json_schema"
        assert body["temperature"] == 0.7 and body["max_tokens"] == 512
        if kind == "http_500":
            return JSONResponse({"error": "engine crashed"}, status_code=500)
        if kind == "slow":
            await asyncio.sleep(SLOW_S)
        text, finish = {
            "ok": (VALID, "stop"),
            "slow": (VALID, "stop"),
            "bad_json": ("The recipe is: " + VALID, "stop"),
            "long_output": ('{"total_ms": 900, "pitches": [' + "0, " * 600, "length"),
            "code_fence": ("```json\n" + VALID + "\n```", "stop"),
            "committed_copy": (COMMITTED_TEXT, "stop"),
            "out_of_domain": (VALID.replace('"total_ms": 900', '"total_ms": 901'), "stop"),
            "two_objects": (VALID + "\n" + VALID, "stop"),
        }[kind]
        completion_tokens = 512 if finish == "length" else len(text.split())
        return {
            "choices": [
                {"message": {"role": "assistant", "content": text}, "finish_reason": finish}
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": completion_tokens},
        }

    return app


class HttpTestClient:
    """A minimal OpenAI-compatible `LlmClient` for this test (the real client is #16's
    `OpenAICompatibleClient`): one request per call, client-side timeout, no retry."""

    def __init__(self, base_url: str, *, timeout_s: float) -> None:
        self.base_url = base_url
        self.timeout_s = timeout_s

    def count_prompt_tokens(self, messages):
        try:
            response = httpx.post(
                f"{self.base_url}/tokenize",
                json={"model": MODEL, "messages": list(messages), "add_generation_prompt": True},
                timeout=5.0,
            )
            response.raise_for_status()
            return int(response.json()["count"])
        except (httpx.HTTPError, KeyError, ValueError) as err:
            raise TokenCountError(str(err)) from err

    def propose(self, messages, schema, seed_key, *, slot_id=None):
        seed = seed_from_key(seed_key)
        started = time.perf_counter()
        body = {
            "model": MODEL,
            "messages": list(messages),
            "temperature": 0.7,
            "top_p": 0.9,
            "top_k": 50,
            "repetition_penalty": 1.0,
            "max_tokens": 512,
            "seed": wire_seed(seed),
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "recipe", "schema": schema},
            },
        }

        def elapsed():
            return int((time.perf_counter() - started) * 1000)

        try:
            response = httpx.post(
                f"{self.base_url}/v1/chat/completions", json=body, timeout=self.timeout_s
            )
        except httpx.TimeoutException:
            return RawOutcome(LlmStatus.TIMEOUT, None, elapsed(), None, None, seed)
        if response.status_code != 200:
            return RawOutcome(LlmStatus.SERVER_ERROR, None, elapsed(), None, None, seed)
        data = response.json()
        choice = data["choices"][0]
        status = LlmStatus.OVERFLOW_OUTPUT if choice["finish_reason"] == "length" else LlmStatus.OK
        usage = data["usage"]
        return RawOutcome(
            status,
            choice["message"]["content"],
            elapsed(),
            usage["prompt_tokens"],
            usage["completion_tokens"],
            seed,
            choice["finish_reason"],
        )


def test_integration_with_a_mock_server(tmp_path, serve_app):
    requests = Counter()
    base_url = serve_app(mock_llm_app(requests))
    client = HttpTestClient(base_url, timeout_s=CLIENT_TIMEOUT_S)
    assert isinstance(client, LlmClient)
    clock = SystemClock()
    ledger = demo.demo_ledger(tmp_path, clock)
    proposer = A3Proposer(
        client,
        ledger,
        demo.demo_prompt_set(),
        demo.demo_decoding_schema(),
        clock=clock,
        reserved=demo.demo_reserved(),
    )
    for rnd in (1, 2, 3, 4):
        feedback = demo.demo_feedback(ledger.records(), demo.A_ATOM, rnd - 1)
        proposer.propose_round(demo.demo_request(rnd, clock=clock, feedback=feedback))
    records = read_records(tmp_path / "slots.jsonl", SlotRecord)
    assert [r.slot_index for r in records] == list(range(1, 13))
    assert {r.slot_index: (r.outcome, r.llm_status) for r in records} == EXPECTED
    for index in range(1, 13):
        assert requests[("tokenize", index)] == 1
        no_call = BEHAVIOUR[index] in ("huge_prompt", "tokenize_error")
        assert requests[("chat", index)] == (0 if no_call else 1), index
    slow = records[3]
    assert CLIENT_TIMEOUT_S * 1000 <= slow.latency_ms < SLOW_S * 1000
    assert records[2].raw_output.startswith('{"total_ms": 900') and records[2].tokens_out == 512
    assert records[6].tokens_in == 20_000
    assert ledger.used(records[0].cap_key) == 12
    out = ROOT / "generation" / "out" / "ci" / "a3-mock-server"
    shutil.rmtree(out, ignore_errors=True)
    shutil.copytree(tmp_path, out)
