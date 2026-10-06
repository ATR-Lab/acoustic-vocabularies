"""A3 and Study B prompt builders (#17). INTERFACE ONLY in the skeleton.

Prompts are built from a `PromptSet` loaded from a directory: the fixed instruction, the
context templates and a hash file. Whether the protocol-quoted texts (the fixed A3
instruction, the operational meanings) may be committed is decided in #17's public-data
review; the code takes the directory as an argument so the real set can live in
restricted storage with only its SHA-256 committed. The repository always carries a
clearly labelled DEMO set for tests.

Determinism: the same state gives a byte-identical prompt and `prompt_sha256`. Context is
serialized as canonical JSON (sorted keys; recipes via `Recipe.to_dict()`; fractions as
`p/q` strings). The context never holds participant IDs, learner data, other methods'
scores or current-round ratings (A3), and never any rating field (B).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from av_generation.llm import ChatMessage
from av_generation.proposers import AtomFeedback, BCellState, BookState
from av_generation.records import SlotRecord


@dataclass(frozen=True, slots=True)
class PromptSet:
    """The instruction and templates of one prompt set, with their hashes (#17)."""

    name: str
    demo: bool
    a3_instruction: str
    b_instruction: str
    meanings: dict[str, str]
    """Semantic label -> operational meaning text."""
    sha256: dict[str, str]
    """File name -> SHA-256 of the files the set was loaded from."""


@dataclass(frozen=True, slots=True)
class BuiltPrompt:
    """A prompt ready for `LlmClient.propose`."""

    messages: tuple[ChatMessage, ...]
    prompt_sha256: str
    """SHA-256 of the canonical JSON of `messages`."""
    context_json: str
    """The canonical context block embedded in the user message."""


def load_prompt_set(path: str | os.PathLike[str]) -> PromptSet:
    """Load and hash-check a prompt-set directory (#17)."""
    raise NotImplementedError("#17: prompt sets")


def build_a3_prompt(
    book_state: BookState,
    atom_id: str,
    round_: int,
    slot: int,
    *,
    semantic_label: str,
    feedback: AtomFeedback,
    same_round: tuple[SlotRecord, ...],
    prompt_set: PromptSet,
) -> BuiltPrompt:
    """The A3 prompt for one slot (#17 -> #20 contract `build_a3_prompt(book_state, atom,
    round, slot)`)."""
    raise NotImplementedError("#17: A3 prompt builder")


def build_b_prompt(cell: BCellState, *, prompt_set: PromptSet) -> BuiltPrompt:
    """The Study B prompt for one bank slot: meanings, schema/profile constraints,
    validation history and the full retained-prefix constraints; no rating fields."""
    raise NotImplementedError("#17: B prompt builder")
