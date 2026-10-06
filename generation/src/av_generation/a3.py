"""A3 proposer: prompt -> model -> parser -> validator -> ledger, three slots a round (#17).

INTERFACE ONLY in the skeleton. Per slot: build the prompt; if
`client.count_prompt_tokens` exceeds `constants.MAX_INPUT_TOKENS`, consume the slot as
`overflow_input` without a call; else make exactly one `client.propose` call with
`seeds.a3_seed_key(...)`, map the status (`outcomes.outcome_from_llm_status`), parse,
validate against the book's committed references and consume the slot with the outcome
(`outcomes.outcome_from_validation`). Slots of a round run one after another, so later
slots may see earlier same-round proposals; ratings arrive only after the round closes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from av_generation.clock import Clock
from av_generation.ids import Method
from av_generation.ledger import SlotLedger
from av_generation.llm import LlmClient
from av_generation.prompts import PromptSet
from av_generation.proposers import RoundRequest, RoundResult


class A3Proposer:
    """`proposers.RoundProposer` for A3 (#17)."""

    method = Method.A3

    def __init__(
        self,
        client: LlmClient,
        ledger: SlotLedger,
        prompt_set: PromptSet,
        decoding_schema: Mapping[str, Any],
        *,
        clock: Clock,
    ) -> None:
        raise NotImplementedError("#17: A3 proposer")

    def propose_round(self, request: RoundRequest) -> RoundResult:
        raise NotImplementedError("#17: A3 proposer")
