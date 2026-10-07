"""Worst-case prompts and offline token estimates (#17). Synthetic DEMO data only.

The run-time rule is fixed elsewhere: A3 and B count prompt tokens with the model server's
`/tokenize` before every call (`llm.LlmClient.count_prompt_tokens`) and close a slot as
`overflow_input` above 16,384 tokens. This module only answers the design question "can
the largest possible prompt of this prompt set exceed the limit?" for the notes:

- `worst_case_a3_prompt(prompt_set)`: the issue's worst case, slot 12 of the last atom of
  a book: 15 committed atoms with the longest recipes and 200-character meanings (the
  meaning-set maximum), 9 rated candidates of rounds 1-3 (3 ratings each) and 2 earlier
  proposals of round 4 with five validator codes each. `padded=True` also gives every
  rated candidate those five codes (not a reachable state; an upper bound).
- `worst_case_b_prompt(prompt_set)`: slot 12 of the last cell of a profile: 63 retained
  options (15 other atoms x 4, plus 3 of this atom) and 11 earlier slots with five codes.
- `count_tokens_offline(messages, tokenizer_json)`: tokens of the pinned model's chat
  format (Qwen2.5: `<|im_start|>role\\ncontent<|im_end|>\\n` per message, then
  `<|im_start|>assistant\\n` as the generation prompt), with a local `tokenizer.json` of
  the pinned revision (downloaded at development time, never committed).

Command line (development only; the tokenizer file is not part of the repository):
`uv run --project generation python -m av_generation._prompt_budget --tokenizer <tokenizer.json>`
prints the prompt hashes, characters and token counts as JSON.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from fractions import Fraction
from pathlib import Path
from typing import Final

from av_sound.grammar import ATOM_IDS, parse_atom_id
from av_sound.recipe import Profile, Recipe
from av_sound.store import SEMANTIC_LABELS

from av_generation.ids import Method, Study, bank_slot_id, proposal_slot_id, slot_index
from av_generation.llm import ChatMessage
from av_generation.meanings import ALL_LABELS, MeaningSet
from av_generation.outcomes import SlotOutcome
from av_generation.prompts import (
    BuiltPrompt,
    PromptSet,
    build_a3_prompt,
    build_b_prompt,
    default_prompt_set_dir,
    load_prompt_set,
)
from av_generation.proposers import (
    AtomFeedback,
    BCellState,
    BookState,
    CandidateFeedback,
    CommittedAtom,
    RaterScore,
    RetainedOption,
)
from av_generation.records import SlotRecord

MEANING_MAX_CHARS: Final = 200
"""`meanings.schema.json` limit of one meaning text."""
WORST_CASE_PROFILE: Final = Profile.P3
WORST_CASE_ATOM: Final = ATOM_IDS[-1]
WORST_CASE_CODES: Final[tuple[str, ...]] = (
    "E_EVENT_SHORT",
    "E_CLIP",
    "E_DUPLICATE",
    "E_RESERVED",
    "E_SEPARATION",
)
"""The validator codes that can fail together for a parsed recipe (all but E_JSON,
E_SCHEMA, E_DOMAIN and E_NONFINITE)."""
_FILLER: Final = (
    "move the selected item from one marked place to another place and record the step "
    "for the operator while the other containers stay where they are on the table "
)
DEMO_BOOK: Final = "DEMO-BK-H9TC"
DEMO_BATCH: Final = "DEMO-A-P01"
DEMO_BANK: Final = "DEMO-bank-01"


def default_labels() -> dict[str, str]:
    """A fixed DEMO label permutation: atom index k gets the k-th label of its role."""
    out = {}
    for atom_id in ATOM_IDS:
        atom = parse_atom_id(atom_id)
        out[atom_id] = SEMANTIC_LABELS[(atom.family, atom.role)][atom.index - 1]
    return out


def long_meanings() -> MeaningSet:
    """DEMO meaning set whose 16 texts all have the maximum 200 characters."""
    texts = {
        label: (f"DEMO {label}: " + _FILLER * 3)[: MEANING_MAX_CHARS - 1] + "."
        for label in sorted(ALL_LABELS)
    }
    return MeaningSet(name="DEMO-meanings-longest", demo=True, meanings=texts).check_consistency()


def long_recipe(k: int) -> Recipe:
    """A recipe with the longest JSON text (negative pitches, amplitudes 0.6/0.8), varied by
    `k` so recipes differ. Synthetic: not checked for admissibility."""
    return Recipe(
        total_ms=(450, 600, 750, 900)[k % 4],
        pitches=(-6 + k % 4, -5 + (k // 4) % 4, -4 + (k // 16) % 3),
        rhythm_weights=(1 + k % 4, 1 + (k + 1) % 4, 1 + (k + 2) % 4),
        gaps_ms=((20, 40, 60)[k % 3], (20, 40, 60)[(k + 1) % 3]),
        amplitudes=((0.6, 0.8)[k % 2], (0.8, 0.6)[k % 2], 0.6),
    )


def _hash(*parts: object) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()


def synthetic_book_state(
    committed: Sequence[str],
    *,
    labels: dict[str, str] | None = None,
    profile: Profile = WORST_CASE_PROFILE,
    book_id: str = DEMO_BOOK,
    batch_id: str = DEMO_BATCH,
    threshold: str = "0.10",
) -> BookState:
    """A DEMO book state committing `committed` (in order) with `long_recipe`s."""
    names = default_labels() if labels is None else labels
    return BookState(
        batch_id=batch_id,
        book_id=book_id,
        profile=profile,
        threshold=threshold,
        committed=tuple(
            CommittedAtom(atom, names[atom], long_recipe(i), _hash("pcm", atom), i)
            for i, atom in enumerate(committed)
        ),
    )


def worst_case_a3_prompt(prompt_set: PromptSet, *, padded: bool = False) -> BuiltPrompt:
    """The largest A3 prompt of the issue's worst case (see the module docstring)."""
    atom = WORST_CASE_ATOM
    book = synthetic_book_state([a for a in ATOM_IDS if a != atom])
    ratings = (
        RaterScore(association=7, distinguishability=7, comfort="acceptable"),
        RaterScore(association=7, distinguishability=6, comfort="acceptable"),
        RaterScore(association=6, distinguishability=7, comfort="unacceptable"),
    )
    score = Fraction(20, 3)
    candidates = tuple(
        CandidateFeedback(
            slot_id=proposal_slot_id(DEMO_BOOK, atom, rnd, slot),
            round=rnd,
            slot=slot,
            slot_index=slot_index(rnd, slot),
            recipe=long_recipe(40 + slot_index(rnd, slot)),
            outcome=SlotOutcome.VALID,
            validator_codes=WORST_CASE_CODES if padded else (),
            ratings=ratings,
            eligible=True,
            score=score,
        )
        for rnd in (1, 2, 3)
        for slot in (1, 2, 3)
    )
    feedback = AtomFeedback(DEMO_BOOK, atom, 3, candidates, candidates[0].slot_id, score)
    same_round = tuple(
        SlotRecord(
            run_id="DEMO-run-01",
            study=Study.A,
            method=Method.A3,
            slot_id=proposal_slot_id(DEMO_BOOK, atom, 4, slot),
            profile=WORST_CASE_PROFILE,
            atom_id=atom,
            slot=slot,
            slot_index=slot_index(4, slot),
            outcome=SlotOutcome.RESERVED_COLLISION,
            t_open_ms=0,
            t_ms=0,
            batch_id=DEMO_BATCH,
            book_id=DEMO_BOOK,
            round=4,
            recipe=long_recipe(60 + slot).to_dict(),
            validator_codes=WORST_CASE_CODES,
        )
        for slot in (1, 2)
    )
    return build_a3_prompt(
        book,
        atom,
        4,
        3,
        semantic_label=default_labels()[atom],
        feedback=feedback,
        same_round=same_round,
        prompt_set=prompt_set,
    )


def worst_case_b_prompt(prompt_set: PromptSet) -> BuiltPrompt:
    """The largest B prompt: slot 12 of the last cell, 63 retained options."""
    atom = WORST_CASE_ATOM
    profile = WORST_CASE_PROFILE
    retained = tuple(
        RetainedOption(
            profile,
            other,
            rank,
            long_recipe(4 * i + rank),
            _hash("pcm", other, rank),
            bank_slot_id(DEMO_BANK, 4, profile.value, other, rank),
        )
        for i, other in enumerate(ATOM_IDS)
        for rank in (1, 2, 3, 4)
        if other != atom or rank < 4
    )
    history = tuple(
        SlotRecord(
            run_id="DEMO-run-01",
            study=Study.B,
            method=Method.B,
            slot_id=bank_slot_id(DEMO_BANK, 4, profile.value, atom, slot),
            profile=profile,
            atom_id=atom,
            slot=slot,
            slot_index=slot,
            outcome=SlotOutcome.RESERVED_COLLISION,
            t_open_ms=0,
            t_ms=0,
            bank_id=DEMO_BANK,
            attempt=4,
            recipe=long_recipe(80 + slot).to_dict(),
            validator_codes=WORST_CASE_CODES,
        )
        for slot in range(1, 12)
    )
    cell = BCellState(DEMO_BANK, 4, profile, atom, 12, default_labels()[atom], retained, history)
    return build_b_prompt(cell, prompt_set=prompt_set, threshold="0.10")


def chat_text(messages: Sequence[ChatMessage]) -> str:
    """The prompt text the pinned Qwen2.5 chat template renders for a system message and
    user messages (no tools), with the generation prompt. Offline estimates only: the
    run-time count comes from the server, which applies the pinned template itself."""
    if not messages or messages[0]["role"] != "system":
        raise ValueError("expected a system message first (the prompt builders always send one)")
    parts = [f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>\n" for m in messages]
    return "".join(parts) + "<|im_start|>assistant\n"


def count_tokens_offline(messages: Sequence[ChatMessage], tokenizer_json: str | Path) -> int:
    """Prompt tokens of `messages` with a local `tokenizer.json` (`tokenizers` library)."""
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_file(str(tokenizer_json))
    return len(tokenizer.encode(chat_text(messages), add_special_tokens=False).ids)


def report(prompt_set: PromptSet, tokenizer_json: str | Path | None) -> dict[str, object]:
    """Hashes, sizes and (with a tokenizer) token counts of the worst-case prompts."""
    prompts = {
        "a3": worst_case_a3_prompt(prompt_set),
        "a3_padded": worst_case_a3_prompt(prompt_set, padded=True),
        "b": worst_case_b_prompt(prompt_set),
    }
    out: dict[str, object] = {"prompt_set": prompt_set.name, "set_sha256": prompt_set.set_sha256}
    if tokenizer_json is not None:
        data = Path(tokenizer_json).read_bytes()
        out["tokenizer_sha256"] = hashlib.sha256(data).hexdigest()
    for name, prompt in prompts.items():
        entry: dict[str, object] = {
            "prompt_sha256": prompt.prompt_sha256,
            "chars": sum(len(m["content"]) for m in prompt.messages),
        }
        if tokenizer_json is not None:
            entry["tokens"] = count_tokens_offline(prompt.messages, tokenizer_json)
        out[name] = entry
    return out


def main(argv: Sequence[str] | None = None) -> int:
    """Print the worst-case report as JSON (see the module docstring)."""
    parser = argparse.ArgumentParser(prog="python -m av_generation._prompt_budget")
    parser.add_argument("--tokenizer", help="tokenizer.json of the pinned model revision")
    parser.add_argument("--prompts", default=None, help="prompt-set directory")
    args = parser.parse_args(argv)
    prompt_set = load_prompt_set(args.prompts or default_prompt_set_dir(), meanings=long_meanings())
    sys.stdout.write(json.dumps(report(prompt_set, args.tokenizer), indent=2, sort_keys=True))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - command-line entry
    raise SystemExit(main())
