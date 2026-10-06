"""The Study B proposer: one model proposal per bank slot (Study B protocol §4).

The bank builder (`av_banks.builder`) owns the slot: it reserves it in the ledger,
validates the candidate against the retained options and consumes it. The proposer only
turns one `BCellState` into one `Proposal`:

1. `build_b_prompt(cell, prompt_set=)` (#17 B mode): meanings, schema/profile
   constraints, validation history and the full retained-prefix constraints; never a
   rating, participant or test field (`BCellState` carries none);
2. `client.count_prompt_tokens(messages)` (#16, the server's `/tokenize`); above
   `MAX_INPUT_TOKENS` the slot is `overflow_input` without a call, and a failed count is
   `invalid_json` with `llm_status="server_error"` and no call;
3. exactly one `client.propose(messages, decoding_schema, seed_key, slot_id=)` with the
   B seed key (`seeds.b_seed_key`); no retry, no corrective call;
4. the model status (`outcomes.outcome_from_llm_status`): `timeout` (the A3 per-slot
   cap of 40 s, enforced by the client), `overflow_output`, `server_error` ->
   `invalid_json`;
5. the strict parser (#17 `parse_output`): anything but exactly one JSON object is
   `invalid_json`.

A `Proposal` with `forced` set has its outcome decided; otherwise its `candidate` goes to
the validator. Prompt builder and parser are injectable so the builder can be tested
with scripted fakes while #17 is built in parallel; the defaults are #17's functions.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from av_generation.constants import MAX_INPUT_TOKENS
from av_generation.genconfig import GenerationConfig
from av_generation.jsonio import schema_sha256
from av_generation.llm import LlmClient, TokenCountError
from av_generation.outcomes import LlmStatus, SlotOutcome, outcome_from_llm_status
from av_generation.parser import ParsedOutput, parse_output
from av_generation.prompts import BuiltPrompt, PromptSet, build_b_prompt
from av_generation.proposers import BCellState
from av_generation.seeds import seed_from_key

RAW_OUTPUT_LIMIT = 65_536
"""`SlotRecord.raw_output` holds at most this many characters (schema limit)."""


class ProposerConfigError(ValueError):
    """The proposer's inputs differ from the generation config (prompt set, meanings,
    decoding schema)."""


@dataclass(frozen=True, slots=True)
class Proposal:
    """What one bank slot's proposal produced, before validation."""

    forced: SlotOutcome | None
    """An outcome decided before validation (`overflow_input`, `timeout`,
    `overflow_output`, `invalid_json`), or `None` when `candidate` must be validated."""
    candidate: Mapping[str, Any] | None
    """The parsed JSON object (exactly one), passed to `av_sound.validate` unchanged."""
    seed: int
    raw_output: str | None = None
    prompt_sha256: str | None = None
    schema_sha256: str | None = None
    llm_status: LlmStatus | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: int | None = None
    detail: str | None = None
    """Why the slot was forced (parser error, token-count failure); not logged as a code."""


@runtime_checkable
class SlotProposer(Protocol):
    """What the bank builder calls once per slot."""

    def check_config(self, config: GenerationConfig) -> None:
        """Raise if the proposer's inputs differ from `config` (called before slot 1)."""
        ...

    def propose(self, cell: BCellState, *, seed_key: str, slot_id: str) -> Proposal:
        """One proposal for `cell`; never raises for a model or output failure."""
        ...


PromptBuilder = Callable[..., BuiltPrompt]
"""`build_b_prompt(cell, *, prompt_set) -> BuiltPrompt` (#17)."""
Parser = Callable[[str | None], ParsedOutput]
"""`parse_output(text) -> ParsedOutput` (#17)."""


def check_prompt_inputs(
    prompt_set: PromptSet, decoding_schema: Mapping[str, Any], config: GenerationConfig
) -> None:
    """Refuse a B prompt set, meaning set or decoding schema other than the config's."""
    problems = []
    if prompt_set.set_sha256 != config.prompts.b_sha256:
        problems.append(f"B prompt set {prompt_set.set_sha256} != config {config.prompts.b_sha256}")
    if prompt_set.meanings.sha256() != config.meanings_sha256:
        problems.append("the prompt set's meaning set differs from the config's")
    found = schema_sha256(dict(decoding_schema))
    if found != config.decoding_schema_sha256:
        problems.append(f"decoding schema {found} != config {config.decoding_schema_sha256}")
    if problems:
        raise ProposerConfigError("; ".join(problems))


def _clip(text: str | None) -> str | None:
    return None if text is None else text[:RAW_OUTPUT_LIMIT]


class LlmSlotProposer:
    """`SlotProposer` over an `LlmClient` (#16) and the B-mode prompt builder (#17)."""

    def __init__(
        self,
        client: LlmClient,
        prompt_set: PromptSet,
        decoding_schema: Mapping[str, Any],
        *,
        prompt_builder: PromptBuilder = build_b_prompt,
        parser: Parser = parse_output,
        max_input_tokens: int = MAX_INPUT_TOKENS,
    ) -> None:
        self.client = client
        self.prompt_set = prompt_set
        self.decoding_schema = dict(decoding_schema)
        self.schema_sha256 = schema_sha256(self.decoding_schema)
        self._build = prompt_builder
        self._parse = parser
        self._max_input_tokens = max_input_tokens

    def check_config(self, config: GenerationConfig) -> None:
        check_prompt_inputs(self.prompt_set, self.decoding_schema, config)

    def propose(self, cell: BCellState, *, seed_key: str, slot_id: str) -> Proposal:
        seed = seed_from_key(seed_key)
        prompt = self._build(cell, prompt_set=self.prompt_set)
        prompt_sha = prompt.prompt_sha256
        try:
            tokens_in = self.client.count_prompt_tokens(prompt.messages)
        except TokenCountError as err:
            return Proposal(
                SlotOutcome.INVALID_JSON,
                None,
                seed,
                llm_status=LlmStatus.SERVER_ERROR,
                detail=f"token count failed: {err}",
                prompt_sha256=prompt_sha,
                schema_sha256=self.schema_sha256,
            )
        if tokens_in > self._max_input_tokens:
            return Proposal(
                SlotOutcome.OVERFLOW_INPUT,
                None,
                seed,
                tokens_in=tokens_in,
                detail=f"prompt has {tokens_in} tokens (limit {self._max_input_tokens})",
                prompt_sha256=prompt_sha,
                schema_sha256=self.schema_sha256,
            )
        raw = self.client.propose(prompt.messages, self.decoding_schema, seed_key, slot_id=slot_id)
        common: dict[str, Any] = {
            "raw_output": _clip(raw.text),
            "llm_status": LlmStatus(raw.status),
            "tokens_in": raw.tokens_in if raw.tokens_in is not None else tokens_in,
            "tokens_out": raw.tokens_out,
            "latency_ms": raw.latency_ms,
            "prompt_sha256": prompt_sha,
            "schema_sha256": self.schema_sha256,
        }
        forced = outcome_from_llm_status(raw.status)
        if forced is not None:
            return Proposal(forced, None, seed, detail=f"model status {raw.status}", **common)
        parsed = self._parse(raw.text)
        if parsed.obj is None:
            return Proposal(SlotOutcome.INVALID_JSON, None, seed, detail=parsed.error, **common)
        return Proposal(None, parsed.obj, seed, **common)
