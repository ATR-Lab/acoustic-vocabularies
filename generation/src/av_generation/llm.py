"""LLM client for A3 and Study B (#16; Study A protocol §3.6, Study B protocol §4).

`RawOutcome`, `ChatMessage` and the `LlmClient` protocol are the frozen contract;
`OpenAICompatibleClient` talks to the pinned vLLM server (`llm_server`, manifest
`generation/llm/manifest.json`) or to the mock server (`mock_llm`). Consumers: #17 (A3
slots, B prompts), #22 (dry run), #26 (bank builder). Tests without a server use
`av_generation.llm_fake`. Component guide: `generation/docs/llm.md`.

Contract (#16 -> #17):

- `propose(messages, schema, seed_key, *, slot_id=None) -> RawOutcome` makes at most one
  model call with the frozen decoding values (`constants.FROZEN_DECODING`),
  `response_format` `{"type": "json_schema", ...}` carrying `schema`, and
  `seed = seeds.seed_from_key(seed_key)` (sent as `seeds.wire_seed(seed)`). It never
  retries and never raises for a model or network failure: the outcome is `timeout`
  (request cancelled at the 40-s cap, returned within 40.5 s), `overflow_output`
  (stopped at 512 tokens), `server_error` or `ok` with the raw text. It appends one
  `records.LlmRequest` per call when given a writer, with `slot_id`,
  `prompt_sha256 = jsonio.messages_sha256(messages)` and
  `schema_sha256 = jsonio.schema_sha256(schema)` (the same definitions #17 uses for the
  slot record, so slot and request join on `slot_id` and on the hashes).
- `count_prompt_tokens(messages)` returns the prompt tokens the pinned server will see:
  `POST /tokenize` on the same server with `{"model", "messages",
  "add_generation_prompt": true}` (vLLM's tokenizer and chat template, including any
  default system message the template inserts). No tokenizer files or template engine
  are needed in the client; the LLM manifest (#16) pins the template's SHA-256 and the
  mock server implements `/tokenize`. If counting fails it raises `TokenCountError`; A3
  and B then consume the slot as `invalid_json` with `llm_status="server_error"` and no
  generation call. #17 calls it after `SlotLedger.reserve` and compares with
  `constants.MAX_INPUT_TOKENS` (`overflow_input` above it, no call).

Status mapping of one call (`parse_chat_response`):

- HTTP 200, one choice, `finish_reason` `stop`: `ok` (raw text, token counts);
- HTTP 200, one choice, `finish_reason` `length` (stopped at `max_tokens`):
  `overflow_output` (partial text kept);
- any other `finish_reason`, no message text, a non-200 status, an unreadable body, a
  refused or reset connection: `server_error`;
- no complete response when the run clock reaches the cap: `timeout` (request cancelled).

The cap is measured on the run clock (`clock.Clock`), so accelerated runs (`ScaledClock`)
cancel at 40 s of run time. Cancelling closes the HTTP connection; vLLM aborts a request
whose client disconnected. HTTP clients ignore proxy environment variables
(`trust_env=False`) and never retry.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import threading
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import cache
from typing import (
    TYPE_CHECKING,
    Any,
    Final,
    Literal,
    Protocol,
    TypedDict,
    TypeVar,
    runtime_checkable,
)

import httpx
from av_sound._paths import schema_path as sound_schema_path
from av_sound.recipe import StrictJsonError, strict_json_loads

from av_generation.clock import Clock
from av_generation.constants import FROZEN_DECODING, SLOT_CAP_MS, DecodingParams
from av_generation.jsonio import messages_sha256, schema_sha256
from av_generation.outcomes import LlmStatus
from av_generation.records import LlmRequest, RecordWriter
from av_generation.seeds import SeedNamespace, parse_seed_key, seed_from_key, wire_seed

if TYPE_CHECKING:
    from av_generation.llm_manifest import LlmManifest

CHAT_PATH: Final = "/v1/chat/completions"
"""OpenAI-compatible chat endpoint (relative to the server root)."""
TOKENIZE_PATH: Final = "/tokenize"
"""vLLM tokenizer endpoint (server root, not under `/v1`)."""
RESPONSE_SCHEMA_NAME: Final = "acoustic_motif_recipe"
"""`response_format.json_schema.name` sent with every call."""
DECODING_SCHEMA_SOURCE: Final = "sound/schema/recipe.schema.json"
"""The decoding schema is the published recipe schema, unchanged (every field an enum)."""
PROPOSAL_NAMESPACES: Final[frozenset[SeedNamespace]] = frozenset(
    {SeedNamespace.A3, SeedNamespace.B}
)
"""Seed-key namespaces of model calls (A3 slots, Study B bank slots)."""
CANCEL_GRACE_S: Final = 0.25
"""Real seconds allowed for the cancelled request to close before `propose` returns."""
HTTP_SAFETY_MARGIN_S: Final = 5.0
"""httpx timeout beyond the cap (a safety net; the run-clock cap decides)."""

FINISH_OK: Final = "stop"
FINISH_LENGTH: Final = "length"

_log = logging.getLogger(__name__)
_T = TypeVar("_T")


class ChatMessage(TypedDict):
    """One chat message as sent to the OpenAI-compatible endpoint."""

    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class RawOutcome:
    """Result of one `propose` call (#16 contract)."""

    status: LlmStatus
    text: str | None
    """Raw model text (`None` when nothing came back)."""
    latency_ms: int
    tokens_in: int | None
    tokens_out: int | None
    seed: int
    """The derived unsigned seed (`seeds.seed_from_key(seed_key)`)."""
    finish_reason: str | None = None


class TokenCountError(RuntimeError):
    """`count_prompt_tokens` could not get a count from the server."""


@runtime_checkable
class LlmClient(Protocol):
    """What #17 and #26 call. Implementations: `OpenAICompatibleClient` (#16), fakes."""

    def propose(
        self,
        messages: Sequence[ChatMessage],
        schema: Mapping[str, Any],
        seed_key: str,
        *,
        slot_id: str | None = None,
    ) -> RawOutcome:
        """One schema-constrained proposal; see the module docstring."""
        ...

    def count_prompt_tokens(self, messages: Sequence[ChatMessage]) -> int:
        """Prompt tokens of `messages` under the server's tokenizer and chat template
        (raises `TokenCountError`)."""
        ...


# ---------------------------------------------------------------------------
# Decoding schema and request body


@cache
def _decoding_schema() -> dict[str, Any]:
    data = strict_json_loads(sound_schema_path("recipe.schema.json").read_bytes())
    if not isinstance(data, dict):
        raise ValueError("recipe.schema.json must be a JSON object")
    return data


def decoding_schema() -> dict[str, Any]:
    """The decoding schema sent with every A3/B call: the recipe schema of
    `sound/schema/recipe.schema.json` unchanged (structure plus the Study A §3.2 enums;
    `additionalProperties: false`). Returns a fresh copy."""
    return copy.deepcopy(_decoding_schema())


def decoding_schema_sha256() -> str:
    """`jsonio.schema_sha256(decoding_schema())` (freeze item `schema.decoding_sha256`)."""
    return schema_sha256(_decoding_schema())


def response_format(schema: Mapping[str, Any]) -> dict[str, Any]:
    """The `response_format` object of a call (vLLM structured outputs, strict JSON Schema)."""
    return {
        "type": "json_schema",
        "json_schema": {"name": RESPONSE_SCHEMA_NAME, "schema": dict(schema), "strict": True},
    }


def plain_messages(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """`{"role", "content"}` copies of `messages` (the exact objects sent and hashed)."""
    out = []
    for message in messages:
        role, content = message["role"], message["content"]
        if role not in ("system", "user", "assistant") or not isinstance(content, str):
            raise ValueError(f"bad chat message: role={role!r}")
        out.append({"role": role, "content": content})
    return out


def chat_request_body(
    model: str,
    messages: Sequence[Mapping[str, Any]],
    schema: Mapping[str, Any],
    seed: int,
    decoding: DecodingParams = FROZEN_DECODING,
) -> dict[str, Any]:
    """The JSON body of one chat call: the frozen decoding values, the wire seed and the
    schema. `max_tokens` is sent as `max_completion_tokens` (vLLM deprecates the old
    name); the logged value is the same 512. No `guided_json` (removed in vLLM v0.12)."""
    return {
        "model": model,
        "messages": plain_messages(messages),
        "temperature": decoding.temperature,
        "top_p": decoding.top_p,
        "top_k": decoding.top_k,
        "repetition_penalty": decoding.repetition_penalty,
        "max_completion_tokens": decoding.max_tokens,
        "seed": wire_seed(seed),
        "n": 1,
        "stream": False,
        "response_format": response_format(schema),
    }


# ---------------------------------------------------------------------------
# Response parsing


@dataclass(frozen=True, slots=True)
class ChatResult:
    """What one chat call returned, before the latency is attached."""

    status: LlmStatus
    text: str | None = None
    finish_reason: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    error: str | None = None
    """Short reason for a `server_error` (logged, never sent to the model)."""


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def parse_chat_response(status_code: int, body: bytes) -> ChatResult:
    """Map one HTTP response of `/v1/chat/completions` to a status (module docstring)."""
    if status_code != 200:
        return ChatResult(LlmStatus.SERVER_ERROR, error=f"HTTP {status_code}")
    try:
        payload = strict_json_loads(body)
    except StrictJsonError as err:
        return ChatResult(LlmStatus.SERVER_ERROR, error=f"unreadable body: {err}")
    if not isinstance(payload, dict):
        return ChatResult(LlmStatus.SERVER_ERROR, error="body is not an object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        return ChatResult(LlmStatus.SERVER_ERROR, error="expected exactly one choice")
    choice = choices[0]
    message = choice.get("message")
    text = message.get("content") if isinstance(message, dict) else None
    finish = choice.get("finish_reason")
    finish_reason = finish if isinstance(finish, str) and finish else None
    usage = payload.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    if not isinstance(text, str):
        status, error = LlmStatus.SERVER_ERROR, "no message content"
    elif finish_reason == FINISH_OK:
        status, error = LlmStatus.OK, None
    elif finish_reason == FINISH_LENGTH:
        status, error = LlmStatus.OVERFLOW_OUTPUT, None
    else:
        status, error = LlmStatus.SERVER_ERROR, f"finish_reason {finish_reason!r}"
    return ChatResult(
        status,
        text=text if isinstance(text, str) else None,
        finish_reason=finish_reason,
        tokens_in=_count(usage.get("prompt_tokens")),
        tokens_out=_count(usage.get("completion_tokens")),
        error=error,
    )


# ---------------------------------------------------------------------------
# Running a coroutine from synchronous code


def run_coroutine(factory: Callable[[], Coroutine[Any, Any, _T]]) -> _T:
    """Run `factory()` to completion on a fresh event loop. Inside a running loop (e.g.
    an async web handler) it runs on a helper thread, so callers never block a loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = asyncio.run(factory())
        except BaseException as err:  # pragma: no cover - re-raised below
            box["error"] = err

    thread = threading.Thread(target=target, name="llm-call", daemon=True)
    thread.start()
    thread.join()
    if "error" in box:  # pragma: no cover - only when the coroutine itself fails
        raise box["error"]
    value: _T = box["value"]
    return value


# ---------------------------------------------------------------------------
# Client


class OpenAICompatibleClient:
    """HTTP client for the pinned vLLM server (or the mock server).

    `base_url` is the server root (`http://<llm-host>:8000`, no `/v1`). `run_id` and
    `request_log` (a `RecordWriter` on `logs/llm-requests.jsonl`) make every call
    traceable; `runtime` is logged as given (`vllm 0.30.0` from the manifest, or
    `mock ...`). The decoding values are frozen: other values are refused here because
    the request log could not record them.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        run_id: str,
        clock: Clock,
        request_log: RecordWriter | None = None,
        decoding: DecodingParams = FROZEN_DECODING,
        timeout_ms: int = SLOT_CAP_MS,
        runtime: str = "vllm",
        model_revision: str | None = None,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError(f"base_url must be an http(s) URL, got {base_url!r}")
        if decoding != FROZEN_DECODING:
            raise ValueError(f"decoding values are frozen ({FROZEN_DECODING}), got {decoding}")
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms <= 0:
            raise ValueError(f"timeout_ms must be a positive integer, got {timeout_ms!r}")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.run_id = run_id
        self.runtime = runtime
        self.model_revision = model_revision
        self.decoding = decoding
        self.timeout_ms = timeout_ms
        self._clock = clock
        self._log = request_log
        self._http_timeout = httpx.Timeout(timeout_ms / 1000 + HTTP_SAFETY_MARGIN_S)

    @classmethod
    def from_manifest(
        cls,
        base_url: str,
        manifest: LlmManifest,
        *,
        run_id: str,
        clock: Clock,
        request_log: RecordWriter | None = None,
        timeout_ms: int = SLOT_CAP_MS,
    ) -> OpenAICompatibleClient:
        """A client for the server the LLM manifest pins (model ID, revision, runtime)."""
        return cls(
            base_url,
            manifest.model.id,
            run_id=run_id,
            clock=clock,
            request_log=request_log,
            timeout_ms=timeout_ms,
            runtime=manifest.runtime_label(),
            model_revision=manifest.model.revision,
        )

    # -- generation ---------------------------------------------------------

    def propose(
        self,
        messages: Sequence[ChatMessage],
        schema: Mapping[str, Any],
        seed_key: str,
        *,
        slot_id: str | None = None,
    ) -> RawOutcome:
        key = parse_seed_key(seed_key)
        if key.namespace not in PROPOSAL_NAMESPACES:
            raise ValueError(f"model calls use A3 or B seed keys, got {seed_key!r}")
        seed = seed_from_key(seed_key)
        body = chat_request_body(self.model, messages, schema, seed, self.decoding)
        draft = LlmRequest(
            run_id=self.run_id,
            seed_key=seed_key,
            seed=seed,
            wire_seed=wire_seed(seed),
            model=self.model,
            runtime=self.runtime,
            temperature=self.decoding.temperature,
            top_p=self.decoding.top_p,
            top_k=self.decoding.top_k,
            repetition_penalty=self.decoding.repetition_penalty,
            max_tokens=self.decoding.max_tokens,
            prompt_sha256=messages_sha256(body["messages"]),
            schema_sha256=schema_sha256(schema),
            status=LlmStatus.TIMEOUT,
            latency_ms=0,
            t_ms=self._clock.now_ms(),
            model_revision=self.model_revision,
            slot_id=slot_id,
        ).check()  # a bad run ID, slot ID, model or runtime label fails before any call
        result, latency_ms = run_coroutine(lambda: self._call(body))
        outcome = RawOutcome(
            status=result.status,
            text=result.text,
            latency_ms=latency_ms,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            seed=seed,
            finish_reason=result.finish_reason,
        )
        record = replace(
            draft,
            status=outcome.status,
            latency_ms=latency_ms,
            finish_reason=outcome.finish_reason,
            tokens_in=outcome.tokens_in,
            tokens_out=outcome.tokens_out,
        )
        if self._log is not None:
            self._log.append(record)
        _log.info(
            "llm_request run=%s slot=%s seed_key=%s seed=%d wire_seed=%d temperature=%s "
            "top_p=%s top_k=%d repetition_penalty=%s max_tokens=%d status=%s latency_ms=%d%s",
            self.run_id,
            slot_id,
            seed_key,
            seed,
            wire_seed(seed),
            self.decoding.temperature,
            self.decoding.top_p,
            self.decoding.top_k,
            self.decoding.repetition_penalty,
            self.decoding.max_tokens,
            outcome.status.value,
            latency_ms,
            f" error={result.error}" if result.error else "",
        )
        return outcome

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self._http_timeout,
            trust_env=False,
            transport=httpx.AsyncHTTPTransport(retries=0),
        )

    async def _cap(self, start_ms: int) -> None:
        """Return once `timeout_ms` of run-clock time has passed since `start_ms` (never
        early, even where the event loop wakes timers ahead of a coarse clock)."""
        while (remaining := self.timeout_ms - (self._clock.now_ms() - start_ms)) > 0:
            await self._clock.asleep(remaining / 1000)

    async def _call(self, body: Mapping[str, Any]) -> tuple[ChatResult, int]:
        """One request raced against the run-clock cap; returns the result and latency."""
        content = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        start = self._clock.now_ms()
        async with self._client() as client:
            request = asyncio.ensure_future(
                client.post(CHAT_PATH, content=content, headers=headers)
            )
            cap = asyncio.ensure_future(self._cap(start))
            try:
                await asyncio.wait({request, cap}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                latency_ms = max(0, self._clock.now_ms() - start)
                cap.cancel()
            if not request.done():
                request.cancel()
                await asyncio.wait({request}, timeout=CANCEL_GRACE_S)
                return ChatResult(LlmStatus.TIMEOUT, error="slot cap reached"), latency_ms
            try:
                response = request.result()
            except httpx.TimeoutException as err:
                return ChatResult(LlmStatus.TIMEOUT, error=type(err).__name__), latency_ms
            except (httpx.HTTPError, OSError) as err:
                return ChatResult(LlmStatus.SERVER_ERROR, error=type(err).__name__), latency_ms
            return parse_chat_response(response.status_code, response.content), latency_ms

    # -- token counting -----------------------------------------------------

    def count_prompt_tokens(self, messages: Sequence[ChatMessage]) -> int:
        body = {
            "model": self.model,
            "messages": plain_messages(messages),
            "add_generation_prompt": True,
        }
        try:
            with httpx.Client(
                base_url=self.base_url,
                timeout=self._http_timeout,
                trust_env=False,
                transport=httpx.HTTPTransport(retries=0),
            ) as client:
                response = client.post(TOKENIZE_PATH, json=body)
        except (httpx.HTTPError, OSError) as err:
            raise TokenCountError(f"token count failed: {type(err).__name__}: {err}") from err
        if response.status_code != 200:
            raise TokenCountError(f"token count failed: HTTP {response.status_code}")
        try:
            payload = strict_json_loads(response.content)
        except StrictJsonError as err:
            raise TokenCountError(f"token count failed: unreadable body: {err}") from err
        count = payload.get("count") if isinstance(payload, dict) else None
        if _count(count) is None:
            raise TokenCountError("token count failed: no non-negative integer 'count'")
        assert isinstance(count, int)
        return count
