"""A3 and Study B prompt builders and the prompt set they use (#17; Study A protocol §3.6,
Study B protocol §4).

Prompt set (`generation/prompts/`, loaded with `load_prompt_set`):

```
prompt-set.json               hash file: name, demo flag, file hashes, set hashes
a3/instruction.txt            the fixed instruction (Study A §3.6), exact bytes, no newline
a3/context-template.json      static context sections of the A3 prompt
b/instruction.txt             the Study B instruction (the A3 text, byte for byte)
b/context-template.json       static context sections of the Study B prompt
```

Every model call sends two messages: `system` = the mode's instruction, exactly as stored,
and `user` = the context block, a canonical JSON object (sorted keys, compact separators,
ASCII). The context holds the template's static sections (`checks`, `codes`,
`feedback_fields`, `features`, `grammar`, `schema`) and the dynamic sections built here:

- A3: `task` (atom, family, role, semantic label, meaning, round, slot, slot index,
  separation threshold), `profile`, `committed` (the book's committed atoms in commit
  order: label, meaning, recipe, 12 features) and `feedback` (closed rounds' candidates
  with outcome, validator codes, recipe, ratings, eligibility and score; the incumbent's
  slot index; the current round's earlier proposals without ratings).
- B: `task` (atom, label, meaning, slot, options retained), `profile`, `retained` (every
  option retained so far in this attempt and profile, in order, with recipe and features)
  and `feedback` (this cell's earlier slots: outcome, validator codes, recipe). No rating
  field exists in B mode.

Meaning texts are not part of a prompt set: they come from the shared meaning set
(`av_generation.meanings`), the same texts the A1 screen and the rater stations show.

Hashes (shared definitions, `av_generation.jsonio`):

- `PromptSet.a3_sha256` / `b_sha256` = `jsonio.file_set_sha256` over the files of `a3/` /
  `b/` (relative POSIX paths; freeze items `prompts.a3_sha256` / `prompts.b_sha256`;
  `GenerationConfig.prompts`, see `PromptSet.hashes()`); `set_sha256` over all four files;
- `BuiltPrompt.prompt_sha256` = `jsonio.messages_sha256(messages)`, the per-call hash in
  the slot record and the `LlmRequest` (#16 computes it the same way).

Determinism: the same state gives a byte-identical prompt and `prompt_sha256`. Numbers in
the context are exact integers, recipe amplitudes (0.6, 0.8, 1.0), or exact fractions
rounded half-even to 4 decimals (features, scores); ratings are sorted, so the order of
the panel seats never shows.

Masking and leaks: the context never holds run, batch, book, bank, slot or rater IDs,
seeds, participant data, learner data, raw model text or other books' scores. A3 keeps only
candidates of this book and atom from rounds that have closed (`AtomFeedback.rounds_closed`
and earlier than the current round): current-round ratings never reach the prompt, and
current-round proposals appear only as `same_round` entries without ratings. B prompts
carry no rating field at all.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Final

from av_sound.features import ThresholdLike, features, format_fraction, parse_threshold
from av_sound.grammar import GrammarError, parse_atom_id
from av_sound.recipe import Profile, Recipe, RecipeError
from av_sound.validate import load_separation_threshold

from av_generation._paths import data_root
from av_generation.constants import B_OPTIONS_PER_CELL, B_SLOTS_PER_CELL, SLOTS_PER_ATOM
from av_generation.genconfig import PromptHashes
from av_generation.ids import (
    IdError,
    Study,
    is_demo,
    parse_bank_slot_id,
    parse_proposal_slot_id,
    slot_index,
)
from av_generation.jsonio import (
    CodecError,
    file_set_sha256,
    file_sha256,
    messages_sha256,
    read_json,
    schema_sha256,
)
from av_generation.llm import ChatMessage
from av_generation.meanings import MeaningSet
from av_generation.proposers import (
    AtomFeedback,
    BCellState,
    BookState,
    CandidateFeedback,
    RaterScore,
)
from av_generation.records import SlotRecord

MANIFEST_NAME: Final = "prompt-set.json"
PROMPT_SET_FORMAT: Final = "av-generation/prompt-set"
PROMPT_SET_VERSION: Final = 1
TEMPLATE_FORMAT: Final = "av-generation/context-template"
TEMPLATE_VERSION: Final = 1
INSTRUCTION_NAME: Final = "instruction.txt"
TEMPLATE_NAME: Final = "context-template.json"
MODES: Final[tuple[str, ...]] = ("a3", "b")
SET_FILES: Final[tuple[str, ...]] = tuple(
    sorted(f"{mode}/{name}" for mode in MODES for name in (INSTRUCTION_NAME, TEMPLATE_NAME))
)
"""The four files of a prompt set (relative POSIX paths), besides the hash file."""
STATIC_SECTIONS: Final[frozenset[str]] = frozenset(
    {"checks", "codes", "feedback_fields", "features", "grammar", "schema"}
)
"""Sections every context template defines (copied into each context unchanged)."""
DYNAMIC_SECTIONS: Final[dict[str, frozenset[str]]] = {
    "a3": frozenset({"task", "profile", "committed", "feedback"}),
    "b": frozenset({"task", "profile", "retained", "feedback"}),
}
"""Sections the builders fill per slot."""
FEATURE_PLACES: Final = 4
"""Decimal places of features and scores in the context (exact half-even rounding)."""
_MANIFEST_KEYS: Final[frozenset[str]] = frozenset(
    {
        "format",
        "format_version",
        "name",
        "demo",
        "files",
        "a3_sha256",
        "b_sha256",
        "set_sha256",
        "schema_sha256",
    }
)


class PromptSetError(ValueError):
    """A prompt-set directory is malformed or does not match its hash file."""


class PromptContextError(ValueError):
    """A builder input breaks the context contract (another book's feedback, an unknown
    label, a slot out of range): a caller error, raised before anything is charged."""


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
    """Relative POSIX path -> SHA-256 of every file the set was loaded from (not the hash
    file itself)."""
    set_sha256: str
    """`jsonio.file_set_sha256(files)`."""
    a3_sections: Mapping[str, Any]
    """Static context sections of A3 prompts (`a3/context-template.json`)."""
    b_sections: Mapping[str, Any]
    """Static context sections of B prompts (`b/context-template.json`)."""
    a3_sha256: str
    """`file_set_sha256` of the `a3/` files: freeze item `prompts.a3_sha256`."""
    b_sha256: str
    """`file_set_sha256` of the `b/` files: freeze item `prompts.b_sha256`."""
    schema_sha256: str
    """`jsonio.schema_sha256` of the recipe schema shown in the context (both modes)."""

    def hashes(self) -> PromptHashes:
        """The `GenerationConfig.prompts` value of this set."""
        return PromptHashes(a3_sha256=self.a3_sha256, b_sha256=self.b_sha256)


@dataclass(frozen=True, slots=True)
class BuiltPrompt:
    """A prompt ready for `LlmClient.propose`."""

    messages: tuple[ChatMessage, ...]
    prompt_sha256: str
    """`jsonio.messages_sha256(messages)`."""
    context_json: str
    """The canonical context block embedded in the user message."""


# ---------------------------------------------------------------------------
# Prompt sets


def default_prompt_set_dir() -> Path:
    """`generation/prompts/`: the committed prompt set (frozen at G4)."""
    return data_root() / "prompts"


def _set_files(root: Path) -> dict[str, str]:
    found = sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and p.name != MANIFEST_NAME
    )
    if tuple(found) != SET_FILES:
        raise PromptSetError(
            f"{root}: a prompt set holds exactly {list(SET_FILES)} besides {MANIFEST_NAME}; "
            f"found {found}"
        )
    return {name: file_sha256(root / name) for name in found}


def _subset(files: Mapping[str, str], mode: str) -> dict[str, str]:
    return {name: digest for name, digest in files.items() if name.startswith(f"{mode}/")}


def _read_instruction(path: Path) -> str:
    raw = path.read_bytes()
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        raise PromptSetError(f"{path}: the instruction must be ASCII") from None
    if not text or not all(" " <= c <= "~" for c in text) or text != text.strip():
        raise PromptSetError(
            f"{path}: the instruction must be one line of printable ASCII without leading or "
            "trailing whitespace (no final newline)"
        )
    return text


def _read_sections(path: Path, mode: str) -> dict[str, Any]:
    try:
        doc = read_json(path)
    except CodecError as err:
        raise PromptSetError(str(err)) from err
    if not isinstance(doc, dict) or set(doc) != {"format", "format_version", "mode", "sections"}:
        raise PromptSetError(f"{path}: expected keys format, format_version, mode, sections")
    if doc["format"] != TEMPLATE_FORMAT or doc["format_version"] != TEMPLATE_VERSION:
        raise PromptSetError(f"{path}: expected {TEMPLATE_FORMAT} version {TEMPLATE_VERSION}")
    if doc["mode"] != mode.upper():
        raise PromptSetError(f"{path}: mode must be {mode.upper()!r}")
    sections = doc["sections"]
    if not isinstance(sections, dict) or set(sections) != STATIC_SECTIONS:
        raise PromptSetError(f"{path}: sections must be exactly {sorted(STATIC_SECTIONS)}")
    if not isinstance(sections["schema"], dict):
        raise PromptSetError(f"{path}: sections.schema must be a JSON Schema object")
    return sections


def prompt_set_manifest(path: str | os.PathLike[str], *, name: str) -> dict[str, Any]:
    """The hash file (`prompt-set.json`) for the files in `path`, as a JSON object.

    Regenerate it after an intended template change (and record the new hashes at G4):
    `jsonio.write_document(<dir>/prompt-set.json, prompt_set_manifest(<dir>, name=...))`.
    """
    root = Path(path)
    files = _set_files(root)
    schemas = {
        schema_sha256(_read_sections(root / mode / TEMPLATE_NAME, mode)["schema"]) for mode in MODES
    }
    if len(schemas) != 1:
        raise PromptSetError(f"{root}: the A3 and B templates must show the same recipe schema")
    return {
        "format": PROMPT_SET_FORMAT,
        "format_version": PROMPT_SET_VERSION,
        "name": name,
        "demo": is_demo(name),
        "files": files,
        "a3_sha256": file_set_sha256(_subset(files, "a3")),
        "b_sha256": file_set_sha256(_subset(files, "b")),
        "set_sha256": file_set_sha256(files),
        "schema_sha256": schemas.pop(),
    }


def load_prompt_set(
    path: str | os.PathLike[str],
    *,
    meanings: MeaningSet,
    expected: PromptHashes | None = None,
) -> PromptSet:
    """Load and hash-check a prompt-set directory (#17).

    The files must be exactly `SET_FILES` and match the hash file `prompt-set.json` (file
    hashes, set hashes, schema hash, name and demo flag). `expected` (the generation
    config's `prompts`) must equal the set's hashes. Raises `PromptSetError`.
    """
    root = Path(path)
    try:
        manifest = read_json(root / MANIFEST_NAME)
    except (CodecError, OSError) as err:
        raise PromptSetError(f"{root}: cannot read {MANIFEST_NAME}: {err}") from err
    if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_KEYS:
        raise PromptSetError(f"{root}/{MANIFEST_NAME}: keys must be {sorted(_MANIFEST_KEYS)}")
    name = manifest["name"]
    if not isinstance(name, str) or not name:
        raise PromptSetError(f"{root}/{MANIFEST_NAME}: name must be a non-empty string")
    computed = prompt_set_manifest(root, name=name)
    differences = sorted(k for k in _MANIFEST_KEYS if manifest[k] != computed[k])
    if differences:
        raise PromptSetError(
            f"{root}: the files do not match {MANIFEST_NAME} ({', '.join(differences)}); "
            "an edited prompt set needs a new hash file and a new freeze"
        )
    prompt_set = PromptSet(
        name=name,
        demo=computed["demo"],
        a3_instruction=_read_instruction(root / "a3" / INSTRUCTION_NAME),
        b_instruction=_read_instruction(root / "b" / INSTRUCTION_NAME),
        meanings=meanings,
        files=dict(computed["files"]),
        set_sha256=computed["set_sha256"],
        a3_sections=_read_sections(root / "a3" / TEMPLATE_NAME, "a3"),
        b_sections=_read_sections(root / "b" / TEMPLATE_NAME, "b"),
        a3_sha256=computed["a3_sha256"],
        b_sha256=computed["b_sha256"],
        schema_sha256=computed["schema_sha256"],
    )
    if expected is not None and prompt_set.hashes() != expected:
        raise PromptSetError(
            f"prompt set {name!r} has hashes {prompt_set.hashes()}, expected {expected}"
        )
    return prompt_set


# ---------------------------------------------------------------------------
# Context values


def _decimal(value: Fraction) -> float:
    """An exact fraction rounded half-even to `FEATURE_PLACES` decimals, as the nearest float
    (its shortest repr is the decimal itself, on every platform)."""
    scale = 10**FEATURE_PLACES
    return round(value * scale) / scale


def _recipe(recipe: Recipe | Mapping[str, Any] | None) -> dict[str, Any] | None:
    if recipe is None:
        return None
    if isinstance(recipe, Recipe):
        return recipe.to_dict()
    try:
        return Recipe.from_dict(recipe).to_dict()
    except RecipeError:
        return None


def _features(recipe: Recipe) -> list[float]:
    return [_decimal(f) for f in features(recipe)]


def _rating(score: RaterScore) -> dict[str, Any]:
    return {
        "association": score.association,
        "comfort": score.comfort,
        "distinguishability": score.distinguishability,
    }


def _rating_key(score: RaterScore) -> tuple[int, int, str]:
    return (
        -1 if score.association is None else score.association,
        -1 if score.distinguishability is None else score.distinguishability,
        "" if score.comfort is None else score.comfort,
    )


def _threshold_text(threshold: ThresholdLike) -> str:
    return format_fraction(parse_threshold(threshold))


def _meaning(meanings: MeaningSet, label: str) -> str:
    try:
        return meanings.text(label)
    except KeyError as err:
        raise PromptContextError(str(err)) from None


def _atom_parts(atom_id: str) -> tuple[str, str]:
    try:
        atom = parse_atom_id(atom_id)
    except GrammarError as err:
        raise PromptContextError(str(err)) from None
    return atom.family, atom.role


def _owned_by(slot_id: str, book_id: str, atom_id: str, round_: int | None, slot: int) -> bool:
    """True when `slot_id` names this book and atom and the claimed round and slot."""
    try:
        parsed = parse_proposal_slot_id(slot_id)
    except IdError:
        return False
    return parsed == (book_id, atom_id, round_, slot)


def _unique(items: Iterable[Any], key: str) -> list[Any]:
    seen: set[str] = set()
    out = []
    for item in items:
        value = getattr(item, key)
        if value not in seen:
            seen.add(value)
            out.append(item)
    return out


def _messages(instruction: str, context: Mapping[str, Any]) -> BuiltPrompt:
    context_json = json.dumps(
        context, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    messages = (
        ChatMessage(role="system", content=instruction),
        ChatMessage(role="user", content=context_json),
    )
    return BuiltPrompt(messages, messages_sha256(messages), context_json)


def _profile(profile: Profile) -> dict[str, Any]:
    return {"f0_hz": profile.f0_hz, "id": profile.value}


# ---------------------------------------------------------------------------
# A3


def _candidate(cand: CandidateFeedback) -> dict[str, Any]:
    return {
        "eligible": cand.eligible,
        "outcome": cand.outcome.value,
        "ratings": [_rating(r) for r in sorted(cand.ratings, key=_rating_key)],
        "recipe": _recipe(cand.recipe),
        "round": cand.round,
        "score": None if cand.score is None else _decimal(cand.score),
        "slot": cand.slot,
        "slot_index": cand.slot_index,
        "validator_codes": list(cand.validator_codes),
    }


def _proposal(record: SlotRecord) -> dict[str, Any]:
    return {
        "outcome": record.outcome.value,
        "recipe": _recipe(record.recipe),
        "round": record.round,
        "slot": record.slot,
        "slot_index": record.slot_index,
        "validator_codes": list(record.validator_codes),
    }


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
    round, slot)`).

    `feedback` must be this book's history of `atom_id`; only candidates of rounds that
    have closed (at most `round_ - 1`) are shown, with their ratings. `same_round` may hold
    any records: only this book's earlier slots of `round_` for `atom_id` are shown
    (without ratings). Raises `PromptContextError` for a contract violation.
    """
    try:
        index = slot_index(round_, slot)
    except IdError as err:
        raise PromptContextError(str(err)) from None
    family, role = _atom_parts(atom_id)
    book_id = book_state.book_id
    if feedback.book_id != book_id or feedback.atom_id != atom_id:
        raise PromptContextError(
            f"feedback for {feedback.book_id}/{feedback.atom_id} given for {book_id}/{atom_id}"
        )
    if any(a.atom_id == atom_id for a in book_state.committed):
        raise PromptContextError(f"{atom_id} is already committed in {book_id}")
    meanings = prompt_set.meanings
    committed = [
        {
            "atom_id": atom.atom_id,
            "features": _features(atom.recipe),
            "meaning": None
            if atom.semantic_label is None
            else _meaning(meanings, atom.semantic_label),
            "recipe": atom.recipe.to_dict(),
            "semantic_label": atom.semantic_label,
        }
        for atom in book_state.committed
    ]
    closed = min(feedback.rounds_closed, round_ - 1)
    shown = sorted(
        _unique(
            (
                c
                for c in feedback.candidates
                if c.round <= closed and _owned_by(c.slot_id, book_id, atom_id, c.round, c.slot)
            ),
            "slot_id",
        ),
        key=lambda c: (c.slot_index, c.slot_id),
    )
    incumbent = next((c for c in shown if c.slot_id == feedback.incumbent_slot_id), None)
    earlier = sorted(
        _unique(
            (
                r
                for r in same_round
                if r.study is Study.A
                and r.book_id == book_id
                and r.atom_id == atom_id
                and r.round == round_
                and r.slot < slot
                and _owned_by(r.slot_id, book_id, atom_id, r.round, r.slot)
            ),
            "slot_id",
        ),
        key=lambda r: (r.slot, r.slot_id),
    )
    context: dict[str, Any] = dict(prompt_set.a3_sections)
    context.update(
        {
            "committed": committed,
            "feedback": {
                "candidates": [_candidate(c) for c in shown],
                "incumbent_slot_index": None if incumbent is None else incumbent.slot_index,
                "rounds_closed": closed,
                "same_round": [_proposal(r) for r in earlier],
            },
            "profile": _profile(book_state.profile),
            "task": {
                "atom_id": atom_id,
                "family": family,
                "meaning": _meaning(meanings, semantic_label),
                "role": role,
                "round": round_,
                "semantic_label": semantic_label,
                "separation_threshold": _threshold_text(book_state.threshold),
                "slot": slot,
                "slot_index": index,
                "slots_per_atom": SLOTS_PER_ATOM,
            },
        }
    )
    return _messages(prompt_set.a3_instruction, context)


# ---------------------------------------------------------------------------
# Study B


def _in_cell(record: SlotRecord, cell: BCellState) -> bool:
    if record.study is not Study.B:
        return False
    try:
        parsed = parse_bank_slot_id(record.slot_id)
    except IdError:
        return False
    return (
        parsed.bank_id == cell.bank_id
        and parsed.attempt == cell.attempt
        and parsed.profile == cell.profile.value
        and parsed.atom_id == cell.atom_id
        and parsed.slot == record.slot
        and record.slot < cell.slot
    )


def build_b_prompt(
    cell: BCellState,
    *,
    prompt_set: PromptSet,
    threshold: ThresholdLike | None = None,
) -> BuiltPrompt:
    """The Study B prompt for one bank slot: meanings, schema/profile constraints,
    validation history and the full retained-prefix constraints; no rating fields.

    `threshold` is the frozen separation threshold (default: the validator config,
    `av_sound.validate.load_separation_threshold()`). Only this cell's earlier slots of
    `cell.history` are shown. Raises `PromptContextError` for a contract violation.
    """
    if isinstance(cell.slot, bool) or not 1 <= cell.slot <= B_SLOTS_PER_CELL:
        raise PromptContextError(f"slot must be 1..{B_SLOTS_PER_CELL}, got {cell.slot!r}")
    family, role = _atom_parts(cell.atom_id)
    mismatched = [o.slot_id for o in cell.retained if o.profile != cell.profile]
    if mismatched:
        raise PromptContextError(f"retained options of another profile: {mismatched}")
    sep = load_separation_threshold() if threshold is None else threshold
    retained = [
        {
            "atom_id": option.atom_id,
            "features": _features(option.recipe),
            "rank": option.rank,
            "recipe": option.recipe.to_dict(),
            "same_atom": option.atom_id == cell.atom_id,
        }
        for option in cell.retained
    ]
    history = sorted(
        _unique((r for r in cell.history if _in_cell(r, cell)), "slot_id"),
        key=lambda r: (r.slot, r.slot_id),
    )
    context: dict[str, Any] = dict(prompt_set.b_sections)
    context.update(
        {
            "feedback": {
                "candidates": [
                    {
                        "outcome": r.outcome.value,
                        "recipe": _recipe(r.recipe),
                        "slot": r.slot,
                        "validator_codes": list(r.validator_codes),
                    }
                    for r in history
                ]
            },
            "profile": _profile(cell.profile),
            "retained": retained,
            "task": {
                "atom_id": cell.atom_id,
                "family": family,
                "meaning": _meaning(prompt_set.meanings, cell.semantic_label),
                "options_per_cell": B_OPTIONS_PER_CELL,
                "options_retained": sum(1 for o in cell.retained if o.atom_id == cell.atom_id),
                "role": role,
                "semantic_label": cell.semantic_label,
                "separation_threshold": _threshold_text(sep),
                "slot": cell.slot,
                "slots_per_cell": B_SLOTS_PER_CELL,
            },
        }
    )
    return _messages(prompt_set.b_instruction, context)
