"""Mock OpenAI-compatible LLM server (#16): tests, the dry run (#22) and the benchmark.

It speaks the subset of the vLLM HTTP API the client uses: `POST /v1/chat/completions`,
`POST /tokenize`, `GET /health`, `GET /version` and `GET /v1/models`. It is NOT a model:

- Outputs are deterministic: a recipe sampled from the request's `response_format`
  schema with a PCG64 stream seeded by the request seed and the prompt hash, so the same
  seed and prompt always give the same bytes (the property the real runtime is tested
  for on the LLM host, Pending hardware).
- Tokens are counted by a stand-in tokenizer: a ChatML-like template (with a mock default
  system message when the first message is not a system message, like the pinned
  template) split into words, punctuation and special markers. Counts are not Qwen
  counts; they only exercise the client and the `overflow_input` rule.
- Requests are checked strictly: unknown fields (for example the removed `guided_json`),
  a wrong model, a bad `response_format`, a seed outside signed 64 bits or a prompt that
  does not leave room for `max_completion_tokens` within `max_model_len` give HTTP 400 or
  404 as vLLM would.
- Behaviour per call is scripted with `MockReply` (`push()` or the default): a valid
  recipe, fixed text, a 512-token overflow (`finish_reason="length"`), an HTTP error, an
  unreadable body, no choice, and a delay (the slowed server). The server notices when a
  client disconnects during a delay (`MockCall.aborted`), as vLLM aborts the request.

Run standalone (also usable as a stand-in `vllm` executable for launcher tests):

    python -m av_generation.mock_llm serve MODEL_DIR --served-model-name NAME \\
        --host 127.0.0.1 --port 8000 [--mock-delay-s S] [other vLLM flags are ignored]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import threading
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from av_generation.constants import MIN_MAX_MODEL_LEN, MODEL_ID
from av_generation.jsonio import messages_sha256
from av_generation.seeds import unwire_seed

MOCK_NAME: Final = "av-generation-mock-llm"
MOCK_VERSION: Final = "1"
MOCK_RUNTIME: Final = f"mock {MOCK_NAME} {MOCK_VERSION}"
"""`runtime` value for `LlmRequest` records of calls to this server."""
MOCK_SYSTEM_MESSAGE: Final = "MOCK default system message."
TOKEN_RE: Final = re.compile(r"<\|[a-z_]+\|>|\w+|[^\w\s]")
VOCAB_SIZE: Final = 151_643
INT64_MIN: Final = -(2**63)
INT64_MAX: Final = 2**63 - 1

CHAT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "model",
        "messages",
        "temperature",
        "top_p",
        "top_k",
        "repetition_penalty",
        "max_completion_tokens",
        "max_tokens",
        "seed",
        "n",
        "stream",
        "response_format",
    }
)
TOKENIZE_FIELDS: Final[frozenset[str]] = frozenset(
    {"model", "messages", "add_generation_prompt", "add_special_tokens"}
)

ReplyKind = Literal["recipe", "text", "overflow", "error", "bad_body", "no_choice"]


@dataclass(frozen=True, slots=True)
class MockReply:
    """How the mock answers one chat call."""

    kind: ReplyKind = "recipe"
    delay_s: float = 0.0
    """Seconds to wait before answering (a slowed server); a disconnect ends the wait."""
    text: str | None = None
    """`kind="text"`: the message content."""
    finish_reason: str = "stop"
    """`kind="text"`: the finish reason."""
    status_code: int = 500
    """`kind="error"`: the HTTP status."""


@dataclass(frozen=True, slots=True)
class MockCall:
    """One chat request as the mock saw it."""

    body: Mapping[str, Any]
    status_code: int
    prompt_tokens: int | None
    reply: MockReply | None
    aborted: bool = False
    """The client disconnected before the answer (a cancelled request)."""


# ---------------------------------------------------------------------------
# Stand-in tokenizer and template


def chat_template_text(messages: Sequence[Mapping[str, Any]], add_generation_prompt: bool) -> str:
    """ChatML-like rendering with a mock default system message."""
    parts = []
    if not messages or messages[0].get("role") != "system":
        parts.append(f"<|im_start|>system\n{MOCK_SYSTEM_MESSAGE}<|im_end|>\n")
    for message in messages:
        parts.append(f"<|im_start|>{message['role']}\n{message['content']}<|im_end|>\n")
    if add_generation_prompt:
        parts.append("<|im_start|>assistant\n")
    return "".join(parts)


def mock_tokens(text: str) -> list[int]:
    """Stand-in token IDs (CRC-32 of each word, punctuation mark or special marker)."""
    return [zlib.crc32(t.encode("utf-8")) % VOCAB_SIZE for t in TOKEN_RE.findall(text)]


def count_tokens(messages: Sequence[Mapping[str, Any]], add_generation_prompt: bool = True) -> int:
    """Stand-in prompt token count of `messages`."""
    return len(mock_tokens(chat_template_text(messages, add_generation_prompt)))


# ---------------------------------------------------------------------------
# Deterministic outputs


def sample_instance(schema: Mapping[str, Any], rng: np.random.Generator) -> Any:  # noqa: ANN401
    """A value of a (simple) JSON Schema: enums, consts, objects, arrays, scalars."""
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        values = list(schema["enum"])
        return values[int(rng.integers(len(values)))]
    kind = schema.get("type")
    if kind == "object":
        props = schema.get("properties", {})
        return {name: sample_instance(sub, rng) for name, sub in props.items()}
    if kind == "array":
        low = int(schema.get("minItems", 0))
        high = int(schema.get("maxItems", low))
        n = low if high <= low else int(rng.integers(low, high + 1))
        return [sample_instance(schema.get("items", {}), rng) for _ in range(n)]
    if kind == "integer":
        low = int(schema.get("minimum", 0))
        high = int(schema.get("maximum", low))
        return low if high <= low else int(rng.integers(low, high + 1))
    if kind == "number":
        return float(schema.get("minimum", 0.0))
    if kind == "boolean":
        return False
    if kind == "string":
        return "mock"
    return None


def mock_output_text(
    schema: Mapping[str, Any] | None, wire_seed: int | None, messages: Sequence[Mapping[str, Any]]
) -> str:
    """The deterministic answer for a seed and a prompt."""
    seed = unwire_seed(wire_seed) if wire_seed is not None else 0
    prompt = int(messages_sha256(messages)[:16], 16)
    rng = np.random.Generator(np.random.PCG64([seed, prompt]))
    value = sample_instance(schema, rng) if schema is not None else {}
    return json.dumps(value, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Request checks


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def messages_errors(messages: object) -> list[str]:
    if not isinstance(messages, list) or not messages:
        return ["messages must be a non-empty array"]
    errors = []
    for i, message in enumerate(messages):
        if (
            not isinstance(message, dict)
            or set(message) != {"role", "content"}
            or message["role"] not in ("system", "user", "assistant")
            or not isinstance(message["content"], str)
        ):
            errors.append(f"messages[{i}] must be {{role, content}}")
    return errors


def chat_body_errors(body: object, model: str) -> tuple[int, list[str]]:
    """HTTP status and errors of a chat request body (`(200, [])` when acceptable)."""
    if not isinstance(body, dict):
        return 400, ["body must be a JSON object"]
    unknown = sorted(set(body) - CHAT_FIELDS)
    if unknown:
        return 400, [f"unsupported fields: {unknown}"]
    if body.get("model") != model:
        return 404, [f"the model {body.get('model')!r} does not exist"]
    errors = messages_errors(body.get("messages"))
    for name in ("temperature", "top_p", "repetition_penalty"):
        if name in body and not _is_number(body[name]):
            errors.append(f"{name} must be a number")
    for name in ("top_k", "max_completion_tokens", "max_tokens", "n"):
        if name in body and not _is_int(body[name]):
            errors.append(f"{name} must be an integer")
    seed = body.get("seed")
    if seed is not None and (not _is_int(seed) or not INT64_MIN <= seed <= INT64_MAX):
        errors.append("seed must be a signed 64-bit integer")
    if body.get("n", 1) != 1 or body.get("stream", False):
        errors.append("the mock answers n=1 without streaming only")
    fmt = body.get("response_format")
    if fmt is not None:
        schema = fmt.get("json_schema") if isinstance(fmt, dict) else None
        if (
            not isinstance(fmt, dict)
            or fmt.get("type") != "json_schema"
            or not isinstance(schema, dict)
            or not isinstance(schema.get("name"), str)
            or not isinstance(schema.get("schema"), dict)
        ):
            errors.append(
                "response_format must be {type: json_schema, json_schema: {name, schema}}"
            )
    return (400 if errors else 200), errors


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"message": message, "type": "BadRequestError", "code": status}},
        status_code=status,
    )


# ---------------------------------------------------------------------------
# Server


@dataclass
class MockLlmServer:
    """The mock server: `app` is the FastAPI application (see the module docstring)."""

    model: str = MODEL_ID
    max_model_len: int = MIN_MAX_MODEL_LEN
    default: MockReply = field(default_factory=MockReply)
    poll_s: float = 0.01
    calls: list[MockCall] = field(default_factory=list)
    tokenize_calls: int = 0
    _script: list[MockReply] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    app: FastAPI = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.app = self._build_app()

    def push(self, *replies: MockReply) -> None:
        """Queue replies for the next calls (then the default applies again)."""
        with self._lock:
            self._script.extend(replies)

    def _next_reply(self) -> MockReply:
        with self._lock:
            return self._script.pop(0) if self._script else self.default

    def _record(self, call: MockCall) -> None:
        with self._lock:
            self.calls.append(call)

    def _build_app(self) -> FastAPI:
        app = FastAPI(title=MOCK_NAME, docs_url=None, redoc_url=None, openapi_url=None)

        @app.get("/health")
        def health() -> Response:
            return Response(status_code=200)

        @app.get("/version")
        def version() -> dict[str, str]:
            return {"version": f"{MOCK_NAME}-{MOCK_VERSION}"}

        @app.get("/v1/models")
        def models() -> dict[str, Any]:
            entry = {"id": self.model, "object": "model", "max_model_len": self.max_model_len}
            return {"object": "list", "data": [entry]}

        @app.post("/tokenize")
        async def tokenize(request: Request) -> Response:
            with self._lock:
                self.tokenize_calls += 1
            try:
                body = json.loads(await request.body())
            except ValueError:
                return _error(400, "body is not JSON")
            if not isinstance(body, dict) or set(body) - TOKENIZE_FIELDS:
                return _error(400, "unsupported tokenize request")
            if body.get("model") != self.model:
                return _error(404, f"the model {body.get('model')!r} does not exist")
            errors = messages_errors(body.get("messages"))
            if errors:
                return _error(400, "; ".join(errors))
            text = chat_template_text(body["messages"], bool(body.get("add_generation_prompt")))
            tokens = mock_tokens(text)
            return JSONResponse(
                {"count": len(tokens), "max_model_len": self.max_model_len, "tokens": tokens}
            )

        @app.post("/v1/chat/completions")
        async def chat(request: Request) -> Response:
            try:
                body = json.loads(await request.body())
            except ValueError:
                self._record(MockCall({}, 400, None, None))
                return _error(400, "body is not JSON")
            status, errors = chat_body_errors(body, self.model)
            if errors:
                self._record(MockCall(body, status, None, None))
                return _error(status, "; ".join(errors))
            prompt_tokens = count_tokens(body["messages"])
            max_out = body.get("max_completion_tokens", body.get("max_tokens")) or 16
            if prompt_tokens + max_out > self.max_model_len:
                self._record(MockCall(body, 400, prompt_tokens, None))
                return _error(
                    400,
                    f"maximum context length is {self.max_model_len} tokens; requested "
                    f"{prompt_tokens} prompt + {max_out} output tokens",
                )
            reply = self._next_reply()
            if reply.delay_s > 0 and await self._wait(request, reply.delay_s):
                self._record(MockCall(body, 499, prompt_tokens, reply, aborted=True))
                return Response(status_code=499)
            response = self._answer(body, reply, prompt_tokens, max_out)
            self._record(MockCall(body, response.status_code, prompt_tokens, reply))
            return response

        return app

    async def _wait(self, request: Request, seconds: float) -> bool:
        """Sleep `seconds`; True if the client disconnected meanwhile."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds
        while (remaining := deadline - loop.time()) > 0:
            if await request.is_disconnected():
                return True
            await asyncio.sleep(min(self.poll_s, remaining))
        return False

    def _answer(
        self, body: Mapping[str, Any], reply: MockReply, prompt_tokens: int, max_out: int
    ) -> Response:
        if reply.kind == "error":
            return _error(reply.status_code, "mock server error")
        if reply.kind == "bad_body":
            return Response(b"{not json", media_type="application/json")
        schema = None
        fmt = body.get("response_format")
        if isinstance(fmt, dict):
            schema = fmt["json_schema"]["schema"]
        finish = "stop"
        if reply.kind == "text":
            text = reply.text if reply.text is not None else ""
            finish = reply.finish_reason
            tokens = mock_tokens(text)
        else:
            text = mock_output_text(schema, body.get("seed"), body["messages"])
            tokens = mock_tokens(text)
            if reply.kind == "overflow" or len(tokens) > max_out:
                cut = max(1, len(text) // 2)
                text = text[:cut]
                tokens = [0] * max_out
                finish = "length"
        choices: list[dict[str, Any]] = [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": finish,
            }
        ]
        if reply.kind == "no_choice":
            choices = []
        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": len(tokens),
            "total_tokens": prompt_tokens + len(tokens),
        }
        return JSONResponse(
            {
                "id": f"chatcmpl-mock-{len(self.calls) + 1}",
                "object": "chat.completion",
                "created": 0,
                "model": self.model,
                "choices": choices,
                "usage": usage,
            }
        )


# ---------------------------------------------------------------------------
# CLI (a stand-in `vllm serve`)


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - runs a server
    import uvicorn

    parser = argparse.ArgumentParser(prog="python -m av_generation.mock_llm")
    parser.add_argument("command", choices=["serve"])
    parser.add_argument("model_dir")
    parser.add_argument("--served-model-name", default=MODEL_ID)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--max-model-len", type=int, default=MIN_MAX_MODEL_LEN)
    parser.add_argument("--mock-delay-s", type=float, default=0.0)
    args, _ignored = parser.parse_known_args(argv)
    server = MockLlmServer(
        model=args.served_model_name,
        max_model_len=args.max_model_len,
        default=MockReply(delay_s=args.mock_delay_s),
    )
    uvicorn.run(server.app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
