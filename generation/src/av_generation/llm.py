"""LLM client contract for A3 and Study B (#16; Study A protocol §3.6).

INTERFACE ONLY in the skeleton: `RawOutcome`, `ChatMessage` and the `LlmClient` protocol
are the frozen contract; `OpenAICompatibleClient` is filled by #16 (with the pinned-server
launcher, the LLM manifest and the mock server). Consumers: #17 (A3 slots, B prompts),
#22 (dry run), #26 (bank builder). Tests that need a model use `av_generation.llm_fake`.

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
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypedDict, runtime_checkable

from av_generation.clock import Clock
from av_generation.constants import FROZEN_DECODING, SLOT_CAP_MS, DecodingParams
from av_generation.outcomes import LlmStatus
from av_generation.records import RecordWriter


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


class OpenAICompatibleClient:
    """HTTP client for the pinned vLLM server (#16 implements every method)."""

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
        raise NotImplementedError("#16: OpenAI-compatible client")

    def propose(
        self,
        messages: Sequence[ChatMessage],
        schema: Mapping[str, Any],
        seed_key: str,
        *,
        slot_id: str | None = None,
    ) -> RawOutcome:
        raise NotImplementedError("#16: OpenAI-compatible client")

    def count_prompt_tokens(self, messages: Sequence[ChatMessage]) -> int:
        raise NotImplementedError("#16: OpenAI-compatible client")
