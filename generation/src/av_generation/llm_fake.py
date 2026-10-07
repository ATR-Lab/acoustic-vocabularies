"""In-process scripted `LlmClient` for tests of #17, #22 and #26 (no server, no network).

`ScriptedLlmClient(script)` returns the scripted outcomes in order. A script entry is a
`RawOutcome` (its `seed` is replaced by the key's seed), a string (an `ok` outcome with
that text), or a callable `(messages, schema, seed_key) -> RawOutcome | str`. It counts
calls, keeps every request (with its `slot_id` and `deadline_ms`) and counts tokens as
whitespace-separated words unless a `token_counter` is given (which may raise
`llm.TokenCountError` to script a failed count). It accepts `deadline_ms` like the real
client and records it, but has no clock: a script entry decides the outcome. It is not
the #16 mock server (which tests the HTTP client).
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from av_generation.llm import ChatMessage, RawOutcome
from av_generation.outcomes import LlmStatus
from av_generation.seeds import seed_from_key

ScriptEntry = RawOutcome | str | Callable[[Sequence[ChatMessage], Mapping[str, Any], str], Any]


@dataclass(frozen=True, slots=True)
class FakeCall:
    """One recorded `propose` call."""

    messages: tuple[ChatMessage, ...]
    schema: Mapping[str, Any]
    seed_key: str
    seed: int
    slot_id: str | None = None
    deadline_ms: int | None = None


class ScriptExhausted(RuntimeError):
    """`propose` was called more often than the script allows."""


class ScriptedLlmClient:
    """A deterministic fake model client (see the module docstring)."""

    def __init__(
        self,
        script: Sequence[ScriptEntry],
        *,
        token_counter: Callable[[Sequence[ChatMessage]], int] | None = None,
        latency_ms: int = 5,
    ) -> None:
        self._script = list(script)
        self._token_counter = token_counter
        self._latency_ms = latency_ms
        self._lock = threading.Lock()
        self.calls: list[FakeCall] = []
        self.token_counts = 0

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def propose(
        self,
        messages: Sequence[ChatMessage],
        schema: Mapping[str, Any],
        seed_key: str,
        *,
        slot_id: str | None = None,
        deadline_ms: int | None = None,
    ) -> RawOutcome:
        seed = seed_from_key(seed_key)
        with self._lock:
            index = len(self.calls)
            if index >= len(self._script):
                raise ScriptExhausted(f"script has {len(self._script)} entries")
            self.calls.append(
                FakeCall(tuple(messages), schema, seed_key, seed, slot_id, deadline_ms)
            )
            entry = self._script[index]
        result = entry(messages, schema, seed_key) if callable(entry) else entry
        if isinstance(result, str):
            return RawOutcome(
                status=LlmStatus.OK,
                text=result,
                latency_ms=self._latency_ms,
                tokens_in=self.count_prompt_tokens(messages),
                tokens_out=len(result.split()),
                seed=seed,
                finish_reason="stop",
            )
        if not isinstance(result, RawOutcome):
            raise TypeError("a script entry must give a RawOutcome or a string")
        return replace(result, seed=seed)

    def count_prompt_tokens(
        self, messages: Sequence[ChatMessage], *, deadline_ms: int | None = None
    ) -> int:
        with self._lock:
            self.token_counts += 1
        if self._token_counter is not None:
            return self._token_counter(messages)
        return sum(len(m["content"].split()) for m in messages)


def timeout_outcome(latency_ms: int = 40_000) -> RawOutcome:
    """A scripted `timeout` (seed filled in by the client)."""
    return RawOutcome(LlmStatus.TIMEOUT, None, latency_ms, None, None, 0, None)


def overflow_outcome(text: str = "{", tokens_out: int = 512) -> RawOutcome:
    """A scripted `overflow_output`."""
    return RawOutcome(LlmStatus.OVERFLOW_OUTPUT, text, 100, None, tokens_out, 0, "length")


def server_error_outcome() -> RawOutcome:
    """A scripted `server_error`."""
    return RawOutcome(LlmStatus.SERVER_ERROR, None, 10, None, None, 0, None)
