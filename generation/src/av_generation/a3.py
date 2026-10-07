"""A3 proposer and the Study B slot proposer: prompt -> model -> parser -> validator -> ledger
(#17; Study A protocol §3.3 and §3.6, Study B protocol §4).

One slot, in order (`A3Proposer` runs three a round; `BSlotProposer` one per call):

1. Build the prompt (`prompts.build_a3_prompt` / `build_b_prompt`). A request that breaks
   the context contract raises `prompts.PromptContextError` here, before anything is
   charged.
2. `ledger.reserve(...)`: a 13th slot is refused (and logged) before any model work. The
   ticket's open time starts the slot's proposal time.
3. The slot deadline is `slot_deadline_ms(t_open_ms, window_end_ms)`: 40 s
   (`constants.SLOT_CAP_MS`) after the slot opened, and never later than the end of the
   proposal window (A3: `RoundRequest.window_end_ms`; B has no window). The 40 s cover
   the token count and the model call together. If the deadline has passed when the slot
   starts, the slot closes as `timeout` without a count or a call.
4. `client.count_prompt_tokens(messages)` (the server's `/tokenize`). A failed count
   (`llm.TokenCountError`) consumes the slot as `invalid_json` with
   `llm_status="server_error"`; above `constants.MAX_INPUT_TOKENS` (16,384) the slot is
   `overflow_input`. If the count used up the time to the deadline, the slot is
   `timeout`. None of these makes a model call.
5. Exactly one `client.propose(messages, decoding_schema, seed_key, slot_id=)` with
   `seeds.a3_seed_key(...)` / `seeds.b_seed_key(...)`. The model status decides first
   (`outcomes.outcome_from_llm_status`: `timeout`, `overflow_output`, `server_error` ->
   `invalid_json`); a client that raises or returns an unknown status is treated as
   `server_error` (logged as a warning), so the slot still closes. A result that arrives
   after the slot deadline is `timeout` (the server's status stays in `llm_status`, the
   text in `raw_output`; nothing is parsed or validated). Token counts and latencies that
   are not non-negative integers are logged as `null`.
6. `parser.parse_output`: anything but exactly one JSON object is `invalid_json`
   (validator code `E_JSON` with the parser's message).
7. `av_sound.validate` against the book's committed references (A3) or the other atoms'
   retained options with the same-cell waveform rule (B, `mode="B"`), then
   `outcomes.outcome_from_validation`. The validator renders the recipe; the record keeps
   the recipe, its hash, the PCM and canonical-WAV SHA-256 whenever the waveform is usable.
8. `ledger.consume(record)`: exactly one record per slot, whatever the outcome. There is
   no repair, retry or re-ask: the next slot proceeds.

Time limits: the `LlmClient` contract (#16) has no per-call deadline; the client cancels a
call at its own 40-s cap (result within 40.5 s). So the proposer enforces the slot
deadline around the call: it never starts a call at or after the deadline, and it never
accepts a result that arrives after it. A call that starts late (after a slow count, or
late in the window) can still hold the proposer up to the client's cap after it starts;
cancelling at the slot deadline needs a per-call budget in the client (a #16 contract
extension).

The proposers check at construction that the decoding schema is the schema the prompt
shows (`PromptSet.check_decoding_schema`). The slot record's `schema_sha256` is
`jsonio.schema_sha256(decoding_schema)` (the decoding-schema hash, as in #16's
`LlmRequest`), its `prompt_sha256` the built prompt's, `raw_output` the model text (clipped
to the schema's 65,536 characters), `tokens_in` the server's count. Slots of a round run
one after another, so later slots see earlier same-round proposals (without ratings);
ratings arrive only after the orchestrator closes the round.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

from av_sound.features import ThresholdLike
from av_sound.recipe import Profile
from av_sound.reserved import ReservedEntry, ReservedRegistry
from av_sound.validate import Reference, ValidationResult, validate
from av_sound.wav import file_sha256

from av_generation.clock import Clock
from av_generation.constants import MAX_INPUT_TOKENS, SLOT_CAP_MS, SLOTS_PER_ROUND
from av_generation.ids import Method, Study, bank_slot_id, proposal_slot_id
from av_generation.jsonio import schema_sha256
from av_generation.ledger import SlotLedger, SlotTicket
from av_generation.llm import LlmClient, RawOutcome, TokenCountError
from av_generation.outcomes import (
    LlmStatus,
    SlotOutcome,
    outcome_from_llm_status,
    outcome_from_validation,
)
from av_generation.parser import parse_output
from av_generation.prompts import BuiltPrompt, PromptSet, build_a3_prompt, build_b_prompt
from av_generation.proposers import BCellState, RoundRequest, RoundResult
from av_generation.records import SlotRecord, cap_key
from av_generation.seeds import a3_seed_key, b_seed_key, seed_from_key

RAW_OUTPUT_MAX_CHARS: Final = 65_536
"""`slot-record.schema.json` limit of `raw_output`; longer model text is clipped."""

Reserved = ReservedRegistry | Iterable[ReservedEntry] | None

_log = logging.getLogger(__name__)


def slot_deadline_ms(t_open_ms: int, window_end_ms: int | None = None) -> int:
    """The run-clock time at which a slot opened at `t_open_ms` ends: `SLOT_CAP_MS` later,
    or at `window_end_ms` (the end of the proposal window) if that comes first."""
    cap = t_open_ms + SLOT_CAP_MS
    return cap if window_end_ms is None else min(cap, window_end_ms)


def _count(value: object) -> int | None:
    """A non-negative integer from a client, else `None` (never a record-schema error)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


@dataclass(slots=True)
class _Result:
    """What one slot produced, before the record is written."""

    outcome: SlotOutcome
    llm_status: LlmStatus | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: int | None = None
    raw_output: str | None = None
    recipe: dict[str, Any] | None = None
    recipe_sha256: str | None = None
    pcm_sha256: str | None = None
    file_sha256: str | None = None
    validator_codes: tuple[str, ...] = ()
    validator_messages: tuple[str, ...] = ()


class _SlotCore:
    """The steps shared by A3 and B: count, call, parse, validate."""

    def __init__(
        self,
        client: LlmClient,
        ledger: SlotLedger,
        prompt_set: PromptSet,
        decoding_schema: Mapping[str, Any],
        *,
        clock: Clock,
        reserved: Reserved,
    ) -> None:
        prompt_set.check_decoding_schema(decoding_schema)
        self.client = client
        self.ledger = ledger
        self.decoding_schema = decoding_schema
        self.schema_sha256 = schema_sha256(decoding_schema)
        self.clock = clock
        self.reserved = reserved
        self.validator: Callable[..., ValidationResult] = validate
        """The admissibility check (`av_sound.validate`; replaced only by DEMO fixtures)."""

    def run(
        self,
        prompt: BuiltPrompt,
        seed_key: str,
        slot_id: str,
        *,
        profile: Profile,
        references: tuple[Reference, ...],
        threshold: ThresholdLike | None,
        mode: Literal["A", "B"],
        deadline_ms: int,
        cell_hashes: frozenset[str] = frozenset(),
    ) -> _Result:
        if self.clock.now_ms() >= deadline_ms:
            return _Result(SlotOutcome.TIMEOUT)
        try:
            tokens = _count(self.client.count_prompt_tokens(prompt.messages))
        except Exception as err:
            if not isinstance(err, TokenCountError):
                _log.warning("count_prompt_tokens raised for %s: %r", slot_id, err)
            tokens = None
        if tokens is None:
            return _Result(SlotOutcome.INVALID_JSON, llm_status=LlmStatus.SERVER_ERROR)
        if tokens > MAX_INPUT_TOKENS:
            return _Result(SlotOutcome.OVERFLOW_INPUT, tokens_in=tokens)
        if self.clock.now_ms() >= deadline_ms:  # the count used up the slot's time
            return _Result(SlotOutcome.TIMEOUT, tokens_in=tokens)
        raw: RawOutcome | None
        try:
            raw = self.client.propose(
                prompt.messages, self.decoding_schema, seed_key, slot_id=slot_id
            )
            status = LlmStatus(raw.status)
        except Exception as err:
            _log.warning("propose failed for %s: %r", slot_id, err)
            raw = None
        if raw is None:
            return _Result(
                SlotOutcome.INVALID_JSON, llm_status=LlmStatus.SERVER_ERROR, tokens_in=tokens
            )
        text = raw.text if isinstance(raw.text, str) else None
        served = _count(raw.tokens_in)
        result = _Result(
            SlotOutcome.INVALID_JSON,
            llm_status=status,
            tokens_in=tokens if served is None else served,
            tokens_out=_count(raw.tokens_out),
            latency_ms=_count(raw.latency_ms),
            raw_output=None if text is None else text[:RAW_OUTPUT_MAX_CHARS],
        )
        forced = outcome_from_llm_status(status)
        if forced is not None:
            result.outcome = forced
            return result
        if self.clock.now_ms() > deadline_ms:  # the answer came after the slot ended
            result.outcome = SlotOutcome.TIMEOUT
            return result
        parsed = parse_output(text)
        if parsed.obj is None:
            result.validator_codes = ("E_JSON",)
            result.validator_messages = (parsed.error or "not one JSON object",)
            return result
        checked = self.validator(
            parsed.obj, profile, references, reserved=self.reserved, threshold=threshold
        )
        pcm = checked.pcm_sha256
        result.outcome = outcome_from_validation(
            checked, mode=mode, cell_duplicate=mode == "B" and pcm in cell_hashes
        )
        result.validator_codes = checked.codes
        result.validator_messages = checked.messages
        if checked.recipe is not None:
            result.recipe = checked.recipe.to_dict()
            result.recipe_sha256 = checked.recipe.sha256()
        if pcm is not None and checked.rendered is not None:
            result.pcm_sha256 = pcm
            result.file_sha256 = file_sha256(checked.rendered)
        return result

    def consume(
        self,
        ticket: SlotTicket,
        result: _Result,
        prompt: BuiltPrompt,
        seed_key: str,
        **fields: Any,  # noqa: ANN401 - SlotRecord fields of the study
    ) -> SlotRecord:
        record = SlotRecord(
            run_id=self.ledger.run_id,
            study=ticket.study,
            method=ticket.method,
            slot_id=ticket.slot_id,
            slot_index=ticket.slot_index,
            outcome=result.outcome,
            t_open_ms=ticket.t_open_ms,
            t_ms=max(ticket.t_open_ms, self.clock.now_ms()),
            seed_key=seed_key,
            seed=seed_from_key(seed_key),
            prompt_sha256=prompt.prompt_sha256,
            schema_sha256=self.schema_sha256,
            llm_status=result.llm_status,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            latency_ms=result.latency_ms,
            raw_output=result.raw_output,
            recipe=result.recipe,
            recipe_sha256=result.recipe_sha256,
            validator_codes=result.validator_codes,
            validator_messages=result.validator_messages,
            pcm_sha256=result.pcm_sha256,
            file_sha256=result.file_sha256,
            **fields,
        )
        return self.ledger.consume(record)


class A3Proposer:
    """`proposers.RoundProposer` for A3 (#17); see the module docstring.

    `reserved` is the reserved-signal registry for the validator (`None`: the published
    `sound/reserved/registry.json`). Raises `prompts.PromptSetError` when the prompt set
    does not show `decoding_schema` (`PromptSet.check_decoding_schema`).
    """

    method = Method.A3

    def __init__(
        self,
        client: LlmClient,
        ledger: SlotLedger,
        prompt_set: PromptSet,
        decoding_schema: Mapping[str, Any],
        *,
        clock: Clock,
        reserved: Reserved = None,
    ) -> None:
        self.prompt_set = prompt_set
        self._core = _SlotCore(
            client, ledger, prompt_set, decoding_schema, clock=clock, reserved=reserved
        )

    def propose_round(self, request: RoundRequest) -> RoundResult:
        """Fill the round's three slots in order and return their records."""
        if request.method is not Method.A3:
            raise ValueError(f"A3Proposer got a request for {request.method}")
        if request.semantic_label is None:
            raise ValueError("A3 needs the atom's semantic label")
        if request.run_id != self._core.ledger.run_id:
            raise ValueError(
                f"request run {request.run_id} != ledger run {self._core.ledger.run_id}"
            )
        if request.book.book_id != request.book_id or request.book.profile != request.profile:
            raise ValueError("request.book is not the request's book and profile")
        records: list[SlotRecord] = []
        for slot in range(1, SLOTS_PER_ROUND + 1):
            records.append(self._slot(request, request.semantic_label, slot, tuple(records)))
        return RoundResult(
            Method.A3, request.book_id, request.atom_id, request.round, tuple(records)
        )

    def _slot(
        self, request: RoundRequest, label: str, slot: int, earlier: tuple[SlotRecord, ...]
    ) -> SlotRecord:
        core = self._core
        prompt = build_a3_prompt(
            request.book,
            request.atom_id,
            request.round,
            slot,
            semantic_label=label,
            feedback=request.feedback,
            same_round=(*request.same_round, *earlier),
            prompt_set=self.prompt_set,
        )
        slot_id = proposal_slot_id(request.book_id, request.atom_id, request.round, slot)
        ticket = core.ledger.reserve(
            cap_key(Study.A, request.atom_id, book_id=request.book_id),
            slot_id,
            study=Study.A,
            method=Method.A3,
        )
        seed_key = a3_seed_key(request.seed_namespace, request.atom_id, request.round, slot)
        result = core.run(
            prompt,
            seed_key,
            slot_id,
            profile=request.profile,
            references=request.book.references(),
            threshold=request.book.threshold,
            mode="A",
            deadline_ms=slot_deadline_ms(ticket.t_open_ms, request.window_end_ms),
        )
        return core.consume(
            ticket,
            result,
            prompt,
            seed_key,
            profile=request.profile,
            atom_id=request.atom_id,
            slot=slot,
            batch_id=request.batch_id,
            book_id=request.book_id,
            round=request.round,
        )


class BSlotProposer:
    """The Study B proposer core for the bank builder (#26): one bank slot per call, with
    the frozen A3 steps and the B validation rules (Study B protocol §4).

    `threshold` is the frozen separation threshold (default: the validator config);
    `reserved` the reserved-signal registry (default: the published registry). The
    caller keeps the first four `valid` options per cell, stops a cell at four, fails an
    attempt when a cell has used 12 slots without four, and calls
    `SlotLedger.check_attempt` before each attempt. Each slot has the 40-s cap
    (`slot_deadline_ms`; no window). Raises `prompts.PromptSetError` when the prompt set
    does not show `decoding_schema`.
    """

    method = Method.B

    def __init__(
        self,
        client: LlmClient,
        ledger: SlotLedger,
        prompt_set: PromptSet,
        decoding_schema: Mapping[str, Any],
        *,
        clock: Clock,
        threshold: ThresholdLike | None = None,
        reserved: Reserved = None,
    ) -> None:
        self.prompt_set = prompt_set
        self.threshold = threshold
        self._core = _SlotCore(
            client, ledger, prompt_set, decoding_schema, clock=clock, reserved=reserved
        )

    def propose_slot(self, cell: BCellState, *, seed_namespace: str) -> SlotRecord:
        """Fill bank slot `cell.slot` of the cell and return its record."""
        core = self._core
        prompt = build_b_prompt(cell, prompt_set=self.prompt_set, threshold=self.threshold)
        profile = cell.profile.value
        slot_id = bank_slot_id(cell.bank_id, cell.attempt, profile, cell.atom_id, cell.slot)
        ticket = core.ledger.reserve(
            cap_key(
                Study.B, cell.atom_id, bank_id=cell.bank_id, attempt=cell.attempt, profile=profile
            ),
            slot_id,
            study=Study.B,
            method=Method.B,
        )
        seed_key = b_seed_key(seed_namespace, cell.attempt, profile, cell.atom_id, cell.slot)
        result = core.run(
            prompt,
            seed_key,
            slot_id,
            profile=cell.profile,
            references=cell.other_atom_references(),
            threshold=self.threshold,
            mode="B",
            deadline_ms=slot_deadline_ms(ticket.t_open_ms),
            cell_hashes=cell.cell_hashes(),
        )
        return core.consume(
            ticket,
            result,
            prompt,
            seed_key,
            profile=cell.profile,
            atom_id=cell.atom_id,
            slot=cell.slot,
            bank_id=cell.bank_id,
            attempt=cell.attempt,
        )
