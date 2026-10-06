"""The mock OpenAI-compatible server (#16) refuses what vLLM would refuse, so the client
tests catch request mistakes (removed `guided_json`, bad seeds, bad response formats)."""

import httpx
import numpy as np
import pytest

from av_generation.constants import MIN_MAX_MODEL_LEN, MODEL_ID
from av_generation.llm import decoding_schema, response_format
from av_generation.mock_llm import (
    MOCK_SYSTEM_MESSAGE,
    MockLlmServer,
    chat_template_text,
    count_tokens,
    mock_output_text,
    sample_instance,
)

MESSAGES = [{"role": "user", "content": "DEMO atom"}]


@pytest.fixture
def base(serve_app):
    return serve_app(MockLlmServer().app)


def body(**changes):
    data = {
        "model": MODEL_ID,
        "messages": MESSAGES,
        "temperature": 0.7,
        "top_p": 0.9,
        "top_k": 50,
        "repetition_penalty": 1.0,
        "max_completion_tokens": 512,
        "seed": -5,
        "n": 1,
        "stream": False,
        "response_format": response_format(decoding_schema()),
    }
    data.update(changes)
    return {k: v for k, v in data.items() if v is not ...}


@pytest.mark.parametrize(
    ("changes", "status"),
    [
        ({"guided_json": {"type": "object"}}, 400),
        ({"model": "other/model"}, 404),
        ({"seed": 2**63}, 400),
        ({"seed": "7"}, 400),
        ({"top_k": 50.5}, 400),
        ({"temperature": "0.7"}, 400),
        ({"n": 2}, 400),
        ({"stream": True}, 400),
        ({"messages": []}, 400),
        ({"messages": [{"role": "tool", "content": "x"}]}, 400),
        ({"response_format": {"type": "json_object"}}, 400),
        ({"response_format": {"type": "json_schema", "json_schema": {"name": "x"}}}, 400),
        ({"messages": [{"role": "user", "content": "w " * MIN_MAX_MODEL_LEN}]}, 400),
        ({"response_format": ...}, 200),
        ({"max_completion_tokens": ..., "max_tokens": 512}, 200),
    ],
)
def test_chat_requests_are_checked(base, changes, status):
    response = httpx.post(base + "/v1/chat/completions", json=body(**changes), timeout=10)
    assert response.status_code == status
    if status != 200:
        assert "error" in response.json()


def test_not_json_and_tokenize_checks(base):
    assert httpx.post(base + "/v1/chat/completions", content=b"{x", timeout=10).status_code == 400
    tokenize = base + "/tokenize"
    assert httpx.post(tokenize, content=b"{x", timeout=10).status_code == 400
    ok = {"model": MODEL_ID, "messages": MESSAGES, "add_generation_prompt": True}
    reply = httpx.post(tokenize, json=ok, timeout=10).json()
    assert reply["count"] == len(reply["tokens"]) == count_tokens(MESSAGES)
    assert reply["max_model_len"] == MIN_MAX_MODEL_LEN
    assert httpx.post(tokenize, json={**ok, "extra": 1}, timeout=10).status_code == 400
    assert httpx.post(tokenize, json={**ok, "model": "x/y"}, timeout=10).status_code == 404
    bad = {**ok, "messages": [{"role": "user"}]}
    assert httpx.post(tokenize, json=bad, timeout=10).status_code == 400


def test_info_endpoints(base):
    assert httpx.get(base + "/health", timeout=10).status_code == 200
    assert httpx.get(base + "/version", timeout=10).json()["version"].startswith("av-generation")
    assert httpx.get(base + "/v1/models", timeout=10).json()["data"][0]["id"] == MODEL_ID


def test_template_inserts_a_default_system_message():
    text = chat_template_text(MESSAGES, True)
    assert MOCK_SYSTEM_MESSAGE in text and text.endswith("<|im_start|>assistant\n")
    own = [{"role": "system", "content": "DEMO system"}, *MESSAGES]
    assert MOCK_SYSTEM_MESSAGE not in chat_template_text(own, False)


def test_outputs_depend_on_seed_and_prompt_only():
    schema = decoding_schema()
    one = mock_output_text(schema, 5, MESSAGES)
    assert one == mock_output_text(schema, 5, MESSAGES)
    assert one != mock_output_text(schema, 6, MESSAGES)
    assert one != mock_output_text(schema, 5, [{"role": "user", "content": "other"}])
    assert mock_output_text(None, None, MESSAGES) == "{}"


def test_sample_instance_covers_simple_schemas():
    rng = np.random.Generator(np.random.PCG64(1))
    schema = {
        "type": "object",
        "properties": {
            "c": {"const": 3},
            "i": {"type": "integer", "minimum": 1, "maximum": 4},
            "n": {"type": "number", "minimum": 0.5},
            "b": {"type": "boolean"},
            "s": {"type": "string"},
            "a": {"type": "array", "minItems": 1, "maxItems": 3, "items": {"enum": ["x"]}},
            "z": {},
        },
    }
    value = sample_instance(schema, rng)
    assert value["c"] == 3 and 1 <= value["i"] <= 4 and value["n"] == 0.5
    assert value["b"] is False and value["s"] == "mock" and value["z"] is None
    assert 1 <= len(value["a"]) <= 3 and set(value["a"]) == {"x"}
