"""The scripted in-process LLM client used by #17, #22 and #26 tests."""

import pytest

from av_generation.llm import LlmClient, RawOutcome, TokenCountError
from av_generation.llm_fake import (
    ScriptedLlmClient,
    ScriptExhausted,
    overflow_outcome,
    server_error_outcome,
    timeout_outcome,
)
from av_generation.outcomes import LlmStatus
from av_generation.seeds import a3_seed_key, seed_from_key

MESSAGES = [{"role": "system", "content": "one two"}, {"role": "user", "content": "three"}]


def test_script_order_seeds_and_counts():
    key = a3_seed_key("DEMO-A-P01", "K-a1", 1, 1)
    client = ScriptedLlmClient(
        [
            '{"x": 1}',
            timeout_outcome(),
            overflow_outcome(),
            server_error_outcome(),
            lambda m, s, k: RawOutcome(LlmStatus.OK, k, 1, 1, 1, 0),
        ]
    )
    assert isinstance(client, LlmClient)
    results = [client.propose(MESSAGES, {}, key) for _ in range(5)]
    assert [r.status for r in results] == [
        LlmStatus.OK,
        LlmStatus.TIMEOUT,
        LlmStatus.OVERFLOW_OUTPUT,
        LlmStatus.SERVER_ERROR,
        LlmStatus.OK,
    ]
    assert {r.seed for r in results} == {seed_from_key(key)}
    assert results[0].tokens_in == 3 and results[4].text == key
    assert client.call_count == 5 and client.calls[0].seed_key == key
    with pytest.raises(ScriptExhausted):
        client.propose(MESSAGES, {}, key)
    assert ScriptedLlmClient([], token_counter=lambda m: 99).count_prompt_tokens(MESSAGES) == 99
    with pytest.raises(TypeError):
        ScriptedLlmClient([lambda m, s, k: 5]).propose(MESSAGES, {}, key)


def test_slot_id_and_failed_token_counts_are_scriptable():
    key = a3_seed_key("DEMO-A-P01", "K-a1", 1, 1)
    client = ScriptedLlmClient(['{"x": 1}'])
    client.propose(MESSAGES, {}, key, slot_id="DEMO-BK-H9TC.K-a1.r1s1")
    assert client.calls[0].slot_id == "DEMO-BK-H9TC.K-a1.r1s1"

    def refuse(messages):
        raise TokenCountError("server down")

    with pytest.raises(TokenCountError):
        ScriptedLlmClient([], token_counter=refuse).count_prompt_tokens(MESSAGES)
