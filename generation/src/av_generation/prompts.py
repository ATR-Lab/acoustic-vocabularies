"""A3 and Study B prompt builders (#17). INTERFACE ONLY in the skeleton.

Prompts are built from a `PromptSet` loaded from a directory: the fixed instruction, the
context templates and a hash file. Whether the protocol-quoted texts (the fixed A3
instruction) may be committed is decided in #17's public-data review; the code takes the
directory as an argument so the real set can live in restricted storage with only its
SHA-256 committed. The repository always carries a clearly labelled DEMO set for tests.

Meaning texts are not part of a prompt set: they come from the shared meaning set
(`av_generation.meanings`), the same texts the A1 screen and the rater stations show.

Hashes (shared definitions, `av_generation.jsonio`):

- `PromptSet.set_sha256` = `jsonio.file_set_sha256(PromptSet.files)` over the set's
  files (freeze items `prompts.a3_sha256` / `prompts.b_sha256`; `GenerationConfig.prompts`);
- `BuiltPrompt.prompt_sha256` = `jsonio.messages_sha256(messages)`, the per-call hash in
  the slot record and the `LlmRequest` (#16 computes it the same way).

Determinism: the same state gives a byte-identical prompt and `prompt_sha256`. Context is
serialized as canonical JSON (sorted keys; recipes via `Recipe.to_dict()`; fractions as
`p/q` strings). The context never holds participant IDs, learner data, other methods'
scores or current-round ratings (A3), and never any rating field (B).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from av_generation.llm import ChatMessage
from av_generation.meanings import MeaningSet
from av_generation.proposers import AtomFeedback, BCellState, BookState
from av_generation.records import SlotRecord


@dataclass(frozen=True, slots=True)
class PromptSet:
    """The instruction and templates of one prompt set, with their hashes (#17)."""

    name: str
    demo: bool
    a3_instruction: str
    b_instruction: str
    meanings: MeaningSet
    """The shared meaning set the prompts quote (checked against the generation config)."""
    files: dict[str, str]
    """Relative POSIX path -> SHA-256 of every file the set was loaded from."""
    set_sha256: str
    """`jsonio.file_set_sha256(files)`."""


@dataclass(frozen=True, slots=True)
class BuiltPrompt:
    """A prompt ready for `LlmClient.propose`."""

    messages: tuple[ChatMessage, ...]
    prompt_sha256: str
    """`jsonio.messages_sha256(messages)`."""
    context_json: str
    """The canonical context block embedded in the user message."""


def load_prompt_set(path: str | os.PathLike[str], *, meanings: MeaningSet) -> PromptSet:
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
