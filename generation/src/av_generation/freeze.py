"""G4 freeze manifest: builder, CI freeze guard and fallback re-render check (#25).

The freeze manifest (`generation/schema/freeze-manifest.schema.json`, format
`av-generation/freeze-manifest`; G4 file `generation/FREEZE-v1.0.json`) lists every value
gate G4 freezes, one item per key of `REQUIRED_ITEM_KEYS`, each with its value, its
SHA-256 (the value itself for hash items, the canonical SHA-256 for objects, else `null`),
its source and its `guard`, so that it can be read without the code
(`generation/docs/freeze.md`):

- `code`: recomputed from the running code at every guard run (renderer and validator
  versions and hashes, model pin, decoding values, seeds, budgets, A2 and selector rules);
- `file`: recomputed from the committed file at `path` (golden manifest, renderer spec,
  separation threshold, reserved signals);
- `config`: copied from the frozen generation config, which is itself an item
  (`config.document`, hashed as `config.frozen_sha256`) and must equal the running code
  (`genconfig.config_differences`);
- `recorded`: recorded at the freeze from the GPU host, restricted storage or a human
  decision (runtime, weights, chat template, decoding implementation, threshold evidence,
  per-profile fallback bank digests, pilot timing review and audit hashes).

A `draft` may leave any non-code item pending (`value: null`, source `PENDING ...`); a
`frozen` manifest may not, and needs the repository commit, the tag and owner and advisor
sign-off (roles and links only, never names). The apparatus-manifest fields
(`APPARATUS_FIELDS`) are derived from the items (`apparatus_values`).

The CI freeze guard (`freeze_differences(active_manifest_path(), current_values())`, run
by `tests/generation/test_freeze_manifest.py` on every OS) fails when any frozen value
differs from the repository. Confirmatory runs (#28, O7.1.1) refuse to start when their
config hash differs: `genconfig.check_run_config(config, kind=..., freeze_manifest=...)`
compares `config.frozen_sha256` (shared, so #20 and #26 need nothing from this module
beyond `load_freeze_manifest`). `verify_fallback_hashes` re-renders the fallback set with
the running renderer and compares its hashes with the manifest.

Command line: `python -m av_generation.freeze {check,refresh,record,build,verify,config,
weights,table}` (`main`).
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final, Literal

from av_sound import renderer as _renderer
from av_sound import version as _version
from av_sound.fallback import FallbackError, FallbackSet, load_fallback, reserved_digest
from av_sound.fallback import verify_fallback as _verify_fallback
from av_sound.features import parse_threshold
from av_sound.grammar import ATOM_IDS
from av_sound.recipe import StrictJsonError, strict_json_loads
from av_sound.reserved import load_reserved_registry
from av_sound.store import snapshot_digest, validator_code_hash
from av_sound.validate import VALIDATOR_VERSION

from av_generation import constants as C
from av_generation import genconfig, seeds
from av_generation._paths import data_root
from av_generation._schemas import schema_errors
from av_generation.genconfig import GenerationConfig
from av_generation.ids import CONFIRMATORY_BANK_RE, PILOT_BANK_RE
from av_generation.jsonio import (
    CodecError,
    canonical_line,
    canonical_sha256,
    document_text,
    file_set_sha256,
    file_sha256,
    read_json,
    write_document,
)
from av_generation.records import RecordError

FREEZE_FORMAT: Final = "av-generation/freeze-manifest"
FREEZE_FORMAT_VERSION: Final = 1
SCHEMA: Final = "freeze-manifest.schema.json"
FREEZE_VERSION: Final = "1.0"
PROTOCOL_VERSION: Final = "0.1"
"""Protocol version of the methodology documents the draft follows (Study A/B protocol v0.1)."""

FROZEN_NAME_RE: Final = re.compile(r"FREEZE-v([0-9]+)\.([0-9]+)\.json")
"""Frozen manifests in `generation/`: `FREEZE-v1.0.json`, later `FREEZE-v1.1.json`, ..."""
DRAFT_NAME: Final = "FREEZE-v1.0.draft.json"
"""The committed draft (`status: draft`); deleted when `FREEZE-v1.0.json` is committed."""
TAG_PREFIX: Final = "generation-freeze-v"
"""Proposed signed tag of the freeze commit: `generation-freeze-v1.0`."""

GOLDEN_MANIFEST_PATH: Final = "tests/golden/manifest.json"
RENDERER_SPEC_PATH: Final = "sound/docs/renderer-spec.md"
VALIDATOR_CONFIG_PATH: Final = "sound/config/validator.json"
RESERVED_REGISTRY_PATH: Final = "sound/reserved/registry.json"
LLM_MANIFEST_PATH: Final = "generation/llm/manifest.json"
"""The LLM manifest (#16); when the repository carries it, its hash must equal the config's."""
WEIGHTS_SUFFIX: Final = ".safetensors"

REFERENCE_BATCH_NS: Final = "A-C01"
REFERENCE_BANK_NS: Final = "bank-C001"

E_INPUT: Final = "E_INPUT"
E_ITEM: Final = "E_ITEM"
E_MANIFEST: Final = "E_MANIFEST"
E_STATUS: Final = "E_STATUS"
E_CONFIG: Final = "E_CONFIG"
E_FALLBACK: Final = "E_FALLBACK"
E_PENDING: Final = "E_PENDING"

Guard = Literal["code", "file", "config", "recorded"]
Kind = Literal["sha256", "sha256_map", "revision", "text", "decimal", "integer", "number", "object"]

_SHA256_RE: Final = re.compile(r"[0-9a-f]{64}")
_REVISION_RE: Final = re.compile(r"[0-9a-f]{40}")
_MAP_KEY_RE: Final = re.compile(r"[!-~]{1,64}")
_TEXT_RE: Final = re.compile(r"[ -~]{1,400}")


class FreezeError(ValueError):
    """The freeze manifest cannot be built, read or used; `.code` names the rule and
    `.problems` lists the individual findings."""

    def __init__(self, code: str, message: str, problems: Sequence[str] = ()) -> None:
        detail = "".join(f"\n  - {p}" for p in problems)
        super().__init__(f"{code}: {message}{detail}")
        self.code = code
        self.problems = tuple(problems)


@dataclass(frozen=True, slots=True)
class FreezeValue:
    """One collected value and where it comes from (`value=None`: pending, drafts only)."""

    value: Any
    source: str


@dataclass(frozen=True, slots=True)
class ItemSpec:
    """How one freeze item is collected, checked and described."""

    key: str
    guard: Guard
    kind: Kind
    pending: str = ""
    """Source text of the item while it is pending in a draft (how to fill it)."""
    path: str | None = None
    """Repository-relative POSIX path of the committed file (`file` items)."""
    draft_pending: bool = False
    """A `code` item left pending in drafts (filled by `build`; see `GENERATION_CODE_MODULES`)."""

    @property
    def category(self) -> str:
        return self.key.split(".", 1)[0]


def _cfg(what: str) -> str:
    return (
        f"PENDING (G4): {what}; copied from the frozen generation-config.json by "
        "`python -m av_generation.freeze build` (generation/docs/freeze.md section 6)"
    )


def _host(what: str) -> str:
    return (
        f"PENDING (GPU host): {what}; then `python -m av_generation.freeze record <key> "
        "--value <json> --source <text>` (generation/docs/freeze.md section 6)"
    )


def _human(what: str) -> str:
    return (
        f"PENDING (human): {what}; then `python -m av_generation.freeze record <key> "
        "--value <json> --source <text>`"
    )


ITEM_SPECS: Final[tuple[ItemSpec, ...]] = (
    ItemSpec(
        "config.frozen_sha256",
        "config",
        "sha256",
        _cfg("GenerationConfig.frozen_sha256() of the frozen config (config.document)"),
    ),
    ItemSpec(
        "config.document",
        "config",
        "object",
        _cfg("the frozen generation config document (after the O6.2.2 threshold decision)"),
    ),
    ItemSpec("renderer.version", "code", "text"),
    ItemSpec("renderer.hash", "code", "sha256"),
    ItemSpec("renderer.recipe_schema_hash", "code", "sha256"),
    ItemSpec("renderer.implementation", "code", "object"),
    ItemSpec("renderer.spec_sha256", "file", "sha256", path=RENDERER_SPEC_PATH),
    ItemSpec("renderer.golden_manifest_sha256", "file", "sha256", path=GOLDEN_MANIFEST_PATH),
    ItemSpec("validator.version", "code", "text"),
    ItemSpec("validator.hash", "code", "sha256"),
    ItemSpec("validator.reserved_sha256", "file", "sha256", path=RESERVED_REGISTRY_PATH),
    ItemSpec("separation.threshold", "file", "decimal", path=VALIDATOR_CONFIG_PATH),
    ItemSpec(
        "separation.evidence_sha256",
        "recorded",
        "sha256",
        _human(
            "O6.2.2: SHA-256 of the separation-threshold summary of the listening tool (#23), "
            "kept in restricted storage, with the decision (0.10 kept or revised) on #25"
        ),
    ),
    ItemSpec("model.id", "code", "text"),
    ItemSpec("model.revision", "code", "revision"),
    ItemSpec(
        "model.tokenizer_revision",
        "recorded",
        "revision",
        _host("tokenizer revision SHA from the LLM manifest (#16)"),
    ),
    ItemSpec(
        "model.weights_sha256",
        "recorded",
        "sha256",
        _host(
            "`python -m av_generation.freeze weights <model dir>` on the GPU host (file-set "
            "hash of the *.safetensors files)"
        ),
    ),
    ItemSpec("model.license", "recorded", "text", _host("model licence from the LLM manifest")),
    ItemSpec(
        "runtime.vllm_version",
        "recorded",
        "text",
        _host('`python -c "import vllm; print(vllm.__version__)"` in the server environment'),
    ),
    ItemSpec(
        "runtime.cuda_version",
        "recorded",
        "text",
        _host('`python -c "import torch; print(torch.version.cuda)"` in the server environment'),
    ),
    ItemSpec(
        "runtime.driver_version",
        "recorded",
        "text",
        _host("`nvidia-smi --query-gpu=driver_version --format=csv,noheader`"),
    ),
    ItemSpec(
        "runtime.gpu",
        "recorded",
        "text",
        _host("`nvidia-smi --query-gpu=name,memory.total --format=csv,noheader`"),
    ),
    ItemSpec(
        "runtime.precision",
        "recorded",
        "text",
        _host("dtype the server runs (planned bfloat16), from the LLM manifest and server log"),
    ),
    ItemSpec(
        "runtime.max_model_len",
        "recorded",
        "integer",
        _host("vLLM --max-model-len from the LLM manifest (at least 16896 = 16384 in + 512 out)"),
    ),
    ItemSpec(
        "runtime.chat_template_sha256",
        "recorded",
        "sha256",
        _host("SHA-256 of the chat template the server applies, from the LLM manifest (#16)"),
    ),
    ItemSpec("decoding.temperature", "code", "number"),
    ItemSpec("decoding.top_p", "code", "number"),
    ItemSpec("decoding.top_k", "code", "integer"),
    ItemSpec("decoding.repetition_penalty", "code", "number"),
    ItemSpec("decoding.max_tokens", "code", "integer"),
    ItemSpec("decoding.max_input_tokens", "code", "integer"),
    ItemSpec(
        "decoding.implementation",
        "recorded",
        "text",
        _host(
            "structured-output path of the frozen runtime (response_format json_schema and its "
            "guided-decoding backend), as verified in development (#16)"
        ),
    ),
    ItemSpec(
        "schema.decoding_sha256",
        "config",
        "sha256",
        _cfg("jsonio.schema_sha256 of the decoding schema (#16, #17)"),
    ),
    ItemSpec(
        "prompts.a3_sha256",
        "config",
        "sha256",
        _cfg("PromptSet.set_sha256 of the frozen A3 prompt set (#17)"),
    ),
    ItemSpec(
        "prompts.b_sha256",
        "config",
        "sha256",
        _cfg("PromptSet.set_sha256 of the frozen Study B prompt set (#17)"),
    ),
    ItemSpec(
        "meanings.sha256",
        "config",
        "sha256",
        _cfg("MeaningSet.sha256() of the frozen meaning set (restricted storage)"),
    ),
    ItemSpec(
        "llm.manifest_sha256",
        "config",
        "sha256",
        _cfg("file SHA-256 of the LLM manifest (#16)"),
    ),
    ItemSpec("seeds.function", "code", "text"),
    ItemSpec("seeds.namespaces", "code", "object"),
    ItemSpec("seeds.reference_digest", "code", "sha256"),
    ItemSpec("budget.study_a", "code", "object"),
    ItemSpec("budget.study_b", "code", "object"),
    ItemSpec("a2.rules", "code", "object"),
    ItemSpec("selector.rules", "code", "object"),
    ItemSpec(
        "generation.code",
        "code",
        "object",
        "PENDING (G4): code digests of the generation modules, computed by `python -m "
        "av_generation.freeze build`; left pending in the draft while #16-#20 implement them",
        draft_pending=True,
    ),
    ItemSpec(
        "fallback.bank_hash",
        "config",
        "sha256",
        _cfg("fallback_bank_hash of the restricted fallback build at the frozen threshold"),
    ),
    ItemSpec(
        "fallback.books_sha256",
        "config",
        "sha256_map",
        _cfg("book_sha256 of the P1, P2 and P3 fallback books of that build"),
    ),
    ItemSpec(
        "fallback.banks_sha256",
        "recorded",
        "sha256_map",
        "PENDING (G4): bank_sha256 of the P1, P2 and P3 fallback banks (64 recipes each), "
        "filled by `python -m av_generation.freeze build --fallback <restricted manifest>` "
        "after re-rendering every recipe",
    ),
    ItemSpec(
        "pilot.timing_review",
        "recorded",
        "object",
        _human(
            "O6.2.1 panel timing review: panel_sessions, pilot_books, max_atom_minutes, "
            "within_budget (20 min per atom) or bookings_extended, reference (issue link)"
        ),
    ),
    ItemSpec(
        "pilot.audit_sha256",
        "recorded",
        "sha256_map",
        _human("O6.2.1: {set: SHA-256} of the #24 audit tables of the pilot books"),
    ),
)
"""Every freeze item in manifest order."""

GENERATION_CODE_MODULES: Final[tuple[str, ...]] = (
    "a1.py",
    "a2.py",
    "a3.py",
    "constants.py",
    "domain.py",
    "genconfig.py",
    "ledger.py",
    "llm.py",
    "meanings.py",
    "orchestrator.py",
    "outcomes.py",
    "parser.py",
    "prompts.py",
    "proposers.py",
    "seeds.py",
    "selector.py",
)
"""`av_generation` modules whose code decides what a method proposes and what is
admitted, selected and committed (item `generation.code`). Reports, the panel and the
tools (audit, dry run, threshold tool) may still be fixed after G4."""

SPECS: Final[Mapping[str, ItemSpec]] = {spec.key: spec for spec in ITEM_SPECS}

REQUIRED_ITEM_KEYS: Final[tuple[str, ...]] = tuple(spec.key for spec in ITEM_SPECS)
"""Item keys every freeze manifest must contain, in manifest order (#25 may add keys,
never drop one; the skeleton's 41 keys are all here)."""

APPARATUS_FIELDS: Final[tuple[str, ...]] = (
    "renderer_recipe_schema_hash",
    "model_revision",
    "runtime_precision",
    "prompt_hash",
    "fallback_bank_hash",
)
"""Apparatus-manifest fields the freeze manifest fills."""

DRAFT_DESCRIPTION: Final = (
    "DRAFT - NOT FROZEN. Example of the G4 generation freeze manifest (#25): code and "
    "repository values are computed from this commit and checked by the CI freeze guard; "
    "GPU-host, restricted-storage and human values are PENDING (null) with the command that "
    "fills them. Not signed off and not tagged: never use it for confirmatory runs. Format "
    "and procedure: generation/docs/freeze.md."
)
FROZEN_DESCRIPTION: Final = (
    "G4 generation freeze (#25): every frozen generation value with its hash and source. "
    "The CI freeze guard fails on any difference; confirmatory batch and bank runs must use "
    "config.document (config.frozen_sha256). Format and procedure: generation/docs/freeze.md."
)

A2_RULES_TEXT: Final[Mapping[str, str]] = {
    "round_1": "uniform: every coordinate drawn uniformly from its allowed values; invalid "
    "samples consume their slots (no rejection sampling)",
    "parent": "highest-scoring eligible candidate seen so far; with none, the round is "
    "sampled as round 1",
    "children": "child k of rounds 2-4 mutates k distinct coordinates drawn uniformly "
    "without replacement",
    "index_step": "duration, rhythm weight, gap and amplitude move one index up or down with "
    "equal probability",
    "reflection": "reflect at the domain ends; a pitch reflected back to its original value "
    "takes the nearest legal inward one-semitone step (logged as corrected)",
    "restarts": "none",
    "stream": "one PCG64 stream per slot from A2|<batch_ns>|<atom>|<round>|<slot>",
}
"""A2 rules besides the step sets (Study A protocol section 3.5; implemented by `a2`)."""

SELECTOR_RULES_TEXT: Final[Mapping[str, str]] = {
    "eligible": "technical checks pass and at least min_acceptable_comfort raters mark "
    "comfort acceptable",
    "score": "mean over raters of (association + distinguishability) / 2",
    "first_atom": "distinguishability fixed to first_atom_distinguishability (no reference)",
    "incumbent": "highest score among the eligible candidates seen so far; committed after round 4",
    "tie": "lowest submission-slot number (slot_index 1..12)",
    "nearest_reference_tie": "lowest committed atom index",
    "fallback": "no eligible candidate: first unused bank recipe that passes the book's "
    "checks; none passes: the whole fallback book replaces the book (failed generation, "
    "method label kept)",
}
"""Selector, first-atom, tie and fallback rules (Study A protocol sections 3.3, 3.7;
implemented by `selector` and `orchestrator`)."""


# ---------------------------------------------------------------------------
# Paths


def repo_root() -> Path:
    """The repository root (the parent of `generation/`)."""
    return data_root().parent


def active_manifest_path(root: str | os.PathLike[str] | None = None) -> Path:
    """The manifest the CI guard checks: the highest frozen `generation/FREEZE-vX.Y.json`
    if one exists, else the committed draft `generation/FREEZE-v1.0.draft.json`."""
    generation = (Path(root) if root is not None else repo_root()) / "generation"
    frozen = []
    for path in generation.glob("FREEZE-v*.json"):
        match = FROZEN_NAME_RE.fullmatch(path.name)
        if match:
            frozen.append(((int(match[1]), int(match[2])), path))
    if frozen:
        return max(frozen)[1]
    return generation / DRAFT_NAME


def _repo_file(base: Path, rel: str) -> Path:
    path = base.joinpath(*rel.split("/"))
    if not path.is_file():
        raise FreezeError(E_INPUT, f"{rel} not found under {base.name or base}")
    return path


# ---------------------------------------------------------------------------
# Values


def _json_value(value: object) -> Any:  # noqa: ANN401 - any JSON value
    """`value` as plain JSON (tuples to lists, mappings to sorted dicts)."""
    if isinstance(value, Mapping):
        return {str(k): _json_value(v) for k, v in sorted(value.items())}
    if isinstance(value, tuple | list):
        return [_json_value(v) for v in value]
    return value


def _same(a: object, b: object) -> bool:
    """JSON equality with types (1 differs from 1.0; key order never matters)."""
    try:
        return canonical_line(_json_value(a)) == canonical_line(_json_value(b))
    except (TypeError, ValueError):
        return False


def _show(value: object, limit: int = 80) -> str:
    try:
        text = canonical_line(_json_value(value))[:-1].decode("ascii")
    except (TypeError, ValueError):
        text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def item_sha256(kind: Kind, value: object) -> str | None:
    """The `sha256` of an item: the value for hash items, the canonical SHA-256 of an
    object or array, `None` for other values and for pending items."""
    if value is None:
        return None
    if kind == "sha256" and isinstance(value, str):
        return value
    if isinstance(value, Mapping | list | tuple):
        return canonical_sha256(_json_value(value))
    return None


def _kind_problem(kind: Kind, value: object) -> str | None:
    """Why `value` does not fit `kind` (None when it does)."""
    if kind == "sha256":
        ok = isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None
        return None if ok else "expected a lowercase SHA-256 hex digest"
    if kind == "sha256_map":
        ok = (
            isinstance(value, Mapping)
            and len(value) > 0
            and all(
                isinstance(k, str)
                and _MAP_KEY_RE.fullmatch(k)
                and isinstance(v, str)
                and _SHA256_RE.fullmatch(v)
                for k, v in value.items()
            )
        )
        return None if ok else "expected a non-empty object of SHA-256 hex digests"
    if kind == "revision":
        ok = isinstance(value, str) and _REVISION_RE.fullmatch(value) is not None
        return None if ok else "expected a 40-hex revision SHA"
    if kind == "text":
        ok = isinstance(value, str) and _TEXT_RE.fullmatch(value) is not None
        return None if ok else "expected 1-400 printable ASCII characters"
    if kind == "decimal":
        if not isinstance(value, str):
            return "expected decimal text like 0.10"
        try:
            parse_threshold(value)
        except ValueError:
            return "expected decimal text like 0.10"
        return None
    if kind == "integer":
        ok = isinstance(value, int) and not isinstance(value, bool)
        return None if ok else "expected an integer"
    if kind == "number":
        ok = (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        )
        return None if ok else "expected a finite number"
    return None if isinstance(value, Mapping) else "expected an object"


def reference_seed_keys() -> list[str]:
    """The 2,880 keys of `seeds.reference_digest`: A1, A2 and A3 for every atom, round and
    slot of batch namespace `A-C01`, and B for every attempt, profile, atom and slot of
    bank namespace `bank-C001`."""
    keys = []
    for builder in (seeds.a1_seed_key, seeds.a2_seed_key, seeds.a3_seed_key):
        for atom in ATOM_IDS:
            for round_ in range(1, 5):
                for slot in range(1, 4):
                    keys.append(builder(REFERENCE_BATCH_NS, atom, round_, slot))
    for attempt in range(1, 5):
        for profile in ("P1", "P2", "P3"):
            for atom in ATOM_IDS:
                for slot in range(1, 13):
                    keys.append(seeds.b_seed_key(REFERENCE_BANK_NS, attempt, profile, atom, slot))
    return keys


def _seed_namespaces() -> dict[str, Any]:
    return {
        "key_namespaces": sorted(ns.value for ns in seeds.SeedNamespace),
        "study_a_key": "<A1|A2|A3>|<batch_ns>|<atom>|<round>|<slot>",
        "study_a_batch_ns": "BatchConfig.seed_namespace: the batch ID (A-P01.. pilot, "
        "A-C01.. confirmatory) or a new namespace for a rebuilt batch",
        "study_b_key": "B|<bank_ns>|<attempt>|<profile>|<atom>|<slot>",
        "study_b_bank_ns": "bank manifest seed_namespace: the bank ID or a new namespace for "
        "every rebuild under a new bank_version",
        "pilot_bank_ids": PILOT_BANK_RE.pattern,
        "confirmatory_bank_ids": CONFIRMATORY_BANK_RE.pattern,
        "other_keys": "PANEL|<set_ns>|..., BOT|<run>|..., THRESHOLD|<set>|...",
    }


def _budget_a() -> dict[str, Any]:
    return {
        "rounds_per_atom": C.ROUNDS_PER_ATOM,
        "slots_per_round": C.SLOTS_PER_ROUND,
        "slots_per_atom": C.SLOTS_PER_ATOM,
        "slot_cap_ms": C.SLOT_CAP_MS,
        "proposal_window_ms": C.PROPOSAL_WINDOW_MS,
        "raters_per_panel": C.RATERS_PER_PANEL,
        "rating_slot_ms": C.RATING_SLOT_MS,
        "reference_onset_ms": C.REFERENCE_ONSET_MS,
        "rating_slots_per_round": C.RATING_SLOTS_PER_ROUND,
        "rating_window_ms": C.RATING_WINDOW_MS,
        "round_budget_ms": C.ROUND_BUDGET_MS,
        "atom_budget_ms": C.ATOM_BUDGET_MS,
        "atoms_per_appointment": C.ATOMS_PER_APPOINTMENT,
        "appointment_budget_ms": C.APPOINTMENT_BUDGET_MS,
        "appointment_booking_ms": C.APPOINTMENT_BOOKING_MS,
        "slots_per_batch": C.SLOTS_PER_BATCH,
        "rating_slots_per_rater": C.RATING_SLOTS_PER_RATER,
    }


def _budget_b() -> dict[str, Any]:
    return {
        "slots_per_cell": C.B_SLOTS_PER_CELL,
        "options_per_cell": C.B_OPTIONS_PER_CELL,
        "shown_options": C.B_SHOWN_OPTIONS,
        "max_attempts": C.B_MAX_ATTEMPTS,
        "slot_cap_ms": C.SLOT_CAP_MS,
        "cells": C.B_CELLS,
        "slots_per_attempt": C.B_SLOTS_PER_ATTEMPT,
        "max_slots": C.B_MAX_SLOTS,
    }


def _a2_rules() -> dict[str, Any]:
    return {
        "pitch_steps": list(C.A2_PITCH_STEPS),
        "child_mutations": list(C.A2_CHILD_MUTATIONS),
        **A2_RULES_TEXT,
    }


def _selector_rules() -> dict[str, Any]:
    return {
        "min_acceptable_comfort": C.MIN_ACCEPTABLE_COMFORT,
        "raters": C.RATERS_PER_PANEL,
        "first_atom_distinguishability": C.FIRST_ATOM_DISTINGUISHABILITY,
        "missing_comfort": "not_acceptable",
        **SELECTOR_RULES_TEXT,
    }


def generation_code_digests() -> dict[str, str]:
    """`{module file: code digest}` of `GENERATION_CODE_MODULES` (running code)."""
    package = Path(__file__).resolve().parent
    return {name: _version.code_digest(package / name) for name in GENERATION_CODE_MODULES}


def current_values(root: str | os.PathLike[str] | None = None) -> dict[str, FreezeValue]:
    """The `code` and `file` items as the repository and the running code give them now
    (what the CI guard compares a manifest with). `root` is the repository root."""
    base = Path(root) if root is not None else repo_root()
    decoding = C.FROZEN_DECODING
    threshold_doc = read_json(_repo_file(base, VALIDATOR_CONFIG_PATH))
    threshold = (
        threshold_doc.get("separation_threshold") if isinstance(threshold_doc, dict) else None
    )
    if not isinstance(threshold, str):
        raise FreezeError(E_INPUT, f"{VALIDATOR_CONFIG_PATH} has no separation_threshold text")
    registry = load_reserved_registry(_repo_file(base, RESERVED_REGISTRY_PATH))
    dec = "av_generation.constants.FROZEN_DECODING.{} (Study A protocol section 3.6)"
    return {
        "renderer.version": FreezeValue(
            _renderer.RENDERER_VERSION,
            "av_sound.renderer.RENDERER_VERSION (renderer spec D10; 1.0.0 from G4 on)",
        ),
        "renderer.hash": FreezeValue(
            _version.renderer_hash(),
            "av_sound.version.renderer_hash(): SHA-256 of the canonical "
            "renderer.implementation object (renderer spec D10)",
        ),
        "renderer.recipe_schema_hash": FreezeValue(
            _version.renderer_recipe_schema_hash(),
            "av_sound.version.renderer_recipe_schema_hash(): SHA-256 of {recipe_schema_sha256, "
            "renderer_hash} (renderer spec D10; apparatus field renderer_recipe_schema_hash)",
        ),
        "renderer.implementation": FreezeValue(
            _version.renderer_manifest(),
            "av_sound.version.renderer_manifest(): versions, byte-path constants (480/1440-"
            "sample attack/release envelope, RMS target 7336 LSB, full scale 32767, no limiter, "
            "60-ms minimum event), table and code digests",
        ),
        "renderer.spec_sha256": FreezeValue(
            file_sha256(_repo_file(base, RENDERER_SPEC_PATH)),
            "SHA-256 of sound/docs/renderer-spec.md, the implementation manifest of Study A "
            "protocol section 3.2: D1 sample rounding, D2 envelope, D6 normalization target, "
            "D7 limiter policy (none: overflow is rejected as E_CLIP)",
        ),
        "renderer.golden_manifest_sha256": FreezeValue(
            file_sha256(_repo_file(base, GOLDEN_MANIFEST_PATH)),
            "SHA-256 of tests/golden/manifest.json: cross-machine golden hashes of renderer, "
            "composer, nonlexical assets and store (#12)",
        ),
        "validator.version": FreezeValue(VALIDATOR_VERSION, "av_sound.validate.VALIDATOR_VERSION"),
        "validator.hash": FreezeValue(
            validator_code_hash(),
            "av_sound.store.validator_code_hash(): validator version, code digests of the "
            "admissibility modules and the recipe schema digest",
        ),
        "validator.reserved_sha256": FreezeValue(
            reserved_digest(registry.entries),
            "av_sound.fallback.reserved_digest of sound/reserved/registry.json (E_RESERVED; "
            "equals the store's and the fallback manifest's reserved_sha256)",
        ),
        "separation.threshold": FreezeValue(
            threshold,
            "separation_threshold of sound/config/validator.json: one threshold for every "
            "method and Study B bank (Study A protocol section 3.2; kept or revised by O6.2.2)",
        ),
        "model.id": FreezeValue(
            C.MODEL_ID, "av_generation.constants.MODEL_ID (Study A protocol section 3.6)"
        ),
        "model.revision": FreezeValue(
            C.MODEL_REVISION,
            "av_generation.constants.MODEL_REVISION: pinned Hugging Face revision; the LLM "
            "manifest (#16) must match it",
        ),
        "decoding.temperature": FreezeValue(decoding.temperature, dec.format("temperature")),
        "decoding.top_p": FreezeValue(decoding.top_p, dec.format("top_p")),
        "decoding.top_k": FreezeValue(decoding.top_k, dec.format("top_k")),
        "decoding.repetition_penalty": FreezeValue(
            decoding.repetition_penalty, dec.format("repetition_penalty")
        ),
        "decoding.max_tokens": FreezeValue(decoding.max_tokens, dec.format("max_tokens")),
        "decoding.max_input_tokens": FreezeValue(
            C.MAX_INPUT_TOKENS,
            "av_generation.constants.MAX_INPUT_TOKENS: longer prompts end as overflow_input "
            "without a model call (Study A protocol section 3.6)",
        ),
        "seeds.function": FreezeValue(
            genconfig.SEED_FUNCTION,
            "av_generation.genconfig.SEED_FUNCTION: seeds.derive_seed = first 8 bytes of "
            "SHA-256 of the |-joined key, unsigned big-endian",
        ),
        "seeds.namespaces": FreezeValue(
            _seed_namespaces(),
            "av_generation.seeds.SeedNamespace and the key formats of seeds.a*_seed_key and "
            "b_seed_key (generation/docs/architecture.md section 6)",
        ),
        "seeds.reference_digest": FreezeValue(
            seeds.seeds_digest(reference_seed_keys()),
            "av_generation.seeds.seeds_digest over freeze.reference_seed_keys() (2,880 keys "
            "under A-C01 and bank-C001): changes when the derivation changes",
        ),
        "budget.study_a": FreezeValue(
            _budget_a(),
            "av_generation.constants (Study A protocol section 3.3): 4 rounds x 3 slots of "
            "40 s, 20-s rating slots, 20 min per atom, 4 atoms per 90-min booking",
        ),
        "budget.study_b": FreezeValue(
            _budget_b(),
            "av_generation.constants (Study B protocol section 4): 12 slots per cell, 4 "
            "retained (3 shown + 1 reserve), 576 slots per attempt, 4 attempts",
        ),
        "a2.rules": FreezeValue(
            _a2_rules(),
            "av_generation.constants A2_PITCH_STEPS, A2_CHILD_MUTATIONS and freeze.A2_RULES_TEXT, "
            "implemented by av_generation.a2 (Study A protocol section 3.5)",
        ),
        "generation.code": FreezeValue(
            generation_code_digests(),
            "av_sound.version.code_digest (Python 3.11 AST without docstrings, so comments "
            "and formatting do not count) of freeze.GENERATION_CODE_MODULES: the proposers, "
            "prompts, parser, ledger, LLM client, selector and orchestrator",
        ),
        "selector.rules": FreezeValue(
            _selector_rules(),
            "av_generation.constants MIN_ACCEPTABLE_COMFORT, FIRST_ATOM_DISTINGUISHABILITY and "
            "freeze.SELECTOR_RULES_TEXT, implemented by av_generation.selector and orchestrator "
            "(Study A protocol sections 3.3, 3.7)",
        ),
    }


def config_values(config: GenerationConfig) -> dict[str, FreezeValue]:
    """The `config` items of a frozen generation config."""
    doc = config.to_dict()
    origin = f"config.document ({config.name})"
    return {
        "config.frozen_sha256": FreezeValue(
            config.frozen_sha256(),
            "GenerationConfig.frozen_sha256() of config.document: the config hash every "
            "confirmatory batch and bank run must match (genconfig.check_run_config)",
        ),
        "config.document": FreezeValue(
            doc,
            f"the frozen generation config {config.name!r}; runs and bank builds store it as "
            "generation-config.json (`python -m av_generation.freeze config` extracts it)",
        ),
        "schema.decoding_sha256": FreezeValue(
            config.decoding_schema_sha256,
            f"{origin} decoding_schema_sha256: jsonio.schema_sha256 of the decoding schema "
            "sent in response_format (#16, #17)",
        ),
        "prompts.a3_sha256": FreezeValue(
            config.prompts.a3_sha256,
            f"{origin} prompts.a3_sha256: PromptSet.set_sha256 (jsonio.file_set_sha256) of the "
            "frozen A3 prompt set (#17)",
        ),
        "prompts.b_sha256": FreezeValue(
            config.prompts.b_sha256,
            f"{origin} prompts.b_sha256: PromptSet.set_sha256 of the frozen Study B prompt "
            "set (#17)",
        ),
        "meanings.sha256": FreezeValue(
            config.meanings_sha256,
            f"{origin} meanings_sha256: MeaningSet.sha256() of the frozen meaning set",
        ),
        "llm.manifest_sha256": FreezeValue(
            config.llm_manifest_sha256,
            f"{origin} llm_manifest_sha256: file SHA-256 of the LLM manifest (#16: runtime, "
            "precision, weights, chat template)",
        ),
        "fallback.bank_hash": FreezeValue(
            config.fallback.bank_hash,
            f"{origin} fallback.bank_hash: fallback_bank_hash of the frozen fallback manifest "
            "(sound/docs/fallback.md section 4; restricted, hash only)",
        ),
        "fallback.books_sha256": FreezeValue(
            dict(config.fallback.books_sha256),
            f"{origin} fallback.books_sha256: book_sha256 per profile (snapshot digest of the "
            "frozen 16-atom fallback book)",
        ),
    }


def fallback_values(
    fallback: FallbackSet, config: GenerationConfig | None = None
) -> dict[str, FreezeValue]:
    """The `fallback.banks_sha256` item of a fallback set; with `config`, also check that
    the set is the one the config pins and was built at the config's threshold."""
    if config is not None:
        if genconfig.fallback_pins(fallback) != config.fallback:
            raise FreezeError(E_FALLBACK, "the fallback set is not the one the config pins")
        if parse_threshold(config.separation_threshold) != fallback.threshold:
            raise FreezeError(E_FALLBACK, "the fallback set was built at another threshold")
    return {
        "fallback.banks_sha256": FreezeValue(
            {bank.profile.value: bank.bank_sha256 for bank in fallback.banks},
            "bank_sha256 per profile of the frozen fallback manifest (64 recipes each), "
            "re-rendered by `python -m av_generation.freeze build --fallback` at the freeze",
        )
    }


def pending_value(key: str) -> FreezeValue:
    """A pending (`None`) value of a `config`, `recorded` or draft-pending item, with its
    fill-in source."""
    spec = SPECS[key]
    if spec.guard in ("code", "file") and not spec.draft_pending:
        raise FreezeError(E_ITEM, f"{key} is computed from the repository; it is never pending")
    return FreezeValue(None, spec.pending)


def draft_values(
    recorded: Mapping[str, FreezeValue] | None = None,
    *,
    root: str | os.PathLike[str] | None = None,
) -> dict[str, FreezeValue]:
    """Values of a draft: current code and file items (draft-pending ones pending), the
    given `config`/`recorded` values, everything else pending."""
    values = current_values(root)
    for spec in ITEM_SPECS:
        if spec.guard in ("config", "recorded") or spec.draft_pending:
            values[spec.key] = pending_value(spec.key)
    for key, value in (recorded or {}).items():
        if key not in SPECS or SPECS[key].guard in ("code", "file"):
            raise FreezeError(E_ITEM, f"{key} cannot be recorded (unknown or computed)")
        values[key] = value
    return values


def apparatus_values(values: Mapping[str, Any]) -> dict[str, Any]:
    """Apparatus-manifest fields from item values (key -> value; `None` when pending).

    `prompt_hash` is the canonical SHA-256 of `{"a3_sha256": ..., "b_sha256": ...}`, i.e. of
    the frozen config's `prompts` object: one value for both prompt sets.
    """
    a3, b = values.get("prompts.a3_sha256"), values.get("prompts.b_sha256")
    prompt_hash = (
        canonical_sha256({"a3_sha256": a3, "b_sha256": b})
        if a3 is not None and b is not None
        else None
    )
    return {
        "renderer_recipe_schema_hash": values.get("renderer.recipe_schema_hash"),
        "model_revision": values.get("model.revision"),
        "runtime_precision": values.get("runtime.precision"),
        "prompt_hash": prompt_hash,
        "fallback_bank_hash": values.get("fallback.bank_hash"),
    }


# ---------------------------------------------------------------------------
# Building and checking manifests


def _as_freeze_value(key: str, raw: object) -> FreezeValue:
    if isinstance(raw, FreezeValue):
        return raw
    if isinstance(raw, Mapping) and "source" in raw:
        return FreezeValue(raw.get("value"), str(raw["source"]))
    raise FreezeError(E_ITEM, f"{key}: expected a FreezeValue or a {{value, source}} object")


def _item(spec: ItemSpec, value: FreezeValue) -> dict[str, Any]:
    data = _json_value(value.value)
    return {
        "key": spec.key,
        "category": spec.category,
        "guard": spec.guard,
        "value": data,
        "sha256": item_sha256(spec.kind, data),
        "path": spec.path,
        "source": value.source,
    }


def build_freeze_manifest(
    values: Mapping[str, Any],
    *,
    status: Literal["draft", "frozen"] = "draft",
    freeze_version: str = FREEZE_VERSION,
    protocol_version: str = PROTOCOL_VERSION,
    repo_commit: str | None = None,
    tag: str | None = None,
    signoff: Sequence[Mapping[str, str]] = (),
    description: str | None = None,
) -> dict[str, Any]:
    """Assemble the manifest document from collected values (key -> `FreezeValue` or
    `{value, source}`), one item per `REQUIRED_ITEM_KEYS` entry in that order.

    Raises `FreezeError` (`E_ITEM` for missing or unknown keys, `E_MANIFEST` for any
    problem of `manifest_problems`, e.g. a pending item in a frozen manifest).
    """
    unknown = sorted(set(values) - set(SPECS))
    missing = [key for key in REQUIRED_ITEM_KEYS if key not in values]
    if unknown or missing:
        raise FreezeError(
            E_ITEM,
            "the values do not match REQUIRED_ITEM_KEYS",
            [f"{k}: unknown item" for k in unknown] + [f"{k}: missing" for k in missing],
        )
    items = [_item(spec, _as_freeze_value(spec.key, values[spec.key])) for spec in ITEM_SPECS]
    by_value = {item["key"]: item["value"] for item in items}
    if description is None:
        description = FROZEN_DESCRIPTION if status == "frozen" else DRAFT_DESCRIPTION
    manifest: dict[str, Any] = {
        "format": FREEZE_FORMAT,
        "format_version": FREEZE_FORMAT_VERSION,
        "freeze_version": freeze_version,
        "status": status,
        "description": description,
        "protocol_version": protocol_version,
        "repo_commit": repo_commit,
        "tag": tag,
        "items": items,
        "apparatus": apparatus_values(by_value),
        "signoff": [dict(entry) for entry in signoff],
    }
    problems = manifest_problems(manifest)
    if problems:
        raise FreezeError(E_MANIFEST, "the freeze manifest is not consistent", problems)
    return manifest


def _items_by_key(manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    items = manifest.get("items")
    for item in items if isinstance(items, list) else ():
        if isinstance(item, Mapping) and isinstance(item.get("key"), str):
            out.setdefault(item["key"], item)
    return out


def item_values(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Key -> value of every item (`None` for pending items)."""
    return {key: item.get("value") for key, item in _items_by_key(manifest).items()}


def _item_problems(spec: ItemSpec, item: Mapping[str, Any]) -> list[str]:
    key = spec.key
    out = []
    if item.get("category") != spec.category:
        out.append(f"{key}: category {item.get('category')!r}, expected {spec.category!r}")
    if item.get("guard") != spec.guard:
        out.append(f"{key}: guard {item.get('guard')!r}, expected {spec.guard!r}")
    if item.get("path") != spec.path:
        out.append(f"{key}: path {item.get('path')!r}, expected {spec.path!r}")
    value = item.get("value")
    if value is not None:
        problem = _kind_problem(spec.kind, value)
        if problem:
            out.append(f"{key}: {problem}, got {_show(value)}")
    if item.get("sha256") != item_sha256(spec.kind, value):
        out.append(f"{key}: sha256 {item.get('sha256')!r} does not match the value")
    return out


def _decode_config(doc: object) -> tuple[GenerationConfig | None, list[str]]:
    if not isinstance(doc, Mapping):
        return None, [f"config.document: expected an object, got {_show(doc)}"]
    try:
        return GenerationConfig.from_dict(doc), []
    except (RecordError, CodecError, KeyError, TypeError, ValueError) as err:
        return None, [f"config.document: not a valid generation config: {err}"]


def _config_problems(values: Mapping[str, Any]) -> list[str]:
    """`config` items equal `config.document`, and the document describes the same code,
    threshold, decoding, seeds, budgets and rules as the other items."""
    doc = values.get("config.document")
    if doc is None:
        return [
            f"{s.key}: set without config.document"
            for s in ITEM_SPECS
            if s.guard == "config" and s.key != "config.document" and values.get(s.key) is not None
        ]
    config, problems = _decode_config(doc)
    if config is None:
        return problems
    for key, expected in config_values(config).items():
        if not _same(values.get(key), expected.value):
            problems.append(
                f"{key}: {_show(values.get(key))}, but config.document gives "
                f"{_show(expected.value)}"
            )
    pairs: dict[str, Any] = {
        "model.id": config.model.model_id,
        "model.revision": config.model.revision,
        "renderer.version": config.code.renderer_version,
        "renderer.hash": config.code.renderer_hash,
        "renderer.recipe_schema_hash": config.code.renderer_recipe_schema_hash,
        "validator.version": config.code.validator_version,
        "validator.hash": config.code.validator_hash,
        "separation.threshold": config.separation_threshold,
        "seeds.function": config.seeds.function,
        **{f"decoding.{k}": v for k, v in asdict(config.decoding).items()},
    }
    for key, expected_value in pairs.items():
        found = values.get(key)
        if found is not None and not _same(found, expected_value):
            problems.append(
                f"{key}: {_show(found)}, but config.document has {_show(expected_value)}"
            )
    groups: list[tuple[str, Mapping[str, Any]]] = [
        ("budget.study_a", asdict(config.budget_a)),
        ("budget.study_b", asdict(config.budget_b)),
        ("a2.rules", asdict(config.a2)),
        ("selector.rules", asdict(config.selector)),
    ]
    for key, fields in groups:
        found = values.get(key)
        if not isinstance(found, Mapping):
            continue
        for name, expected_value in fields.items():
            if not _same(found.get(name), expected_value):
                problems.append(
                    f"{key}.{name}: {_show(found.get(name))}, but config.document has "
                    f"{_show(expected_value)}"
                )
    namespaces = values.get("seeds.namespaces")
    if isinstance(namespaces, Mapping) and not _same(
        sorted(config.seeds.namespaces), namespaces.get("key_namespaces")
    ):
        problems.append("seeds.namespaces: key_namespaces differ from config.document")
    return problems


def _frozen_problems(manifest: Mapping[str, Any], values: Mapping[str, Any]) -> list[str]:
    out = [f"{key}: pending in a frozen manifest" for key, v in values.items() if v is None]
    roles = {
        entry.get("role") for entry in manifest.get("signoff") or () if isinstance(entry, Mapping)
    }
    for role in ("owner", "advisor"):
        if role not in roles:
            out.append(f"signoff: no {role} sign-off")
    doc = values.get("config.document")
    if isinstance(doc, Mapping) and doc.get("demo") is not False:
        out.append("config.document: a DEMO config cannot be frozen")
    return out


def manifest_problems(manifest: Mapping[str, Any]) -> list[str]:
    """Everything wrong with a manifest by itself (no repository access): schema, item
    keys, categories, guards, paths, value kinds, `sha256` fields, apparatus fields, the
    config document against the other items, and the extra rules of a frozen manifest.
    An empty list means the manifest is consistent."""
    problems = [f"schema: {err}" for err in schema_errors(SCHEMA, dict(manifest))]
    seen: set[str] = set()
    items = manifest.get("items")
    for item in items if isinstance(items, list) else ():
        if not isinstance(item, Mapping) or not isinstance(item.get("key"), str):
            continue
        key = item["key"]
        if key in seen:
            problems.append(f"{key}: listed twice")
            continue
        seen.add(key)
        spec = SPECS.get(key)
        if spec is None:
            problems.append(f"{key}: not a freeze item")
            continue
        problems.extend(_item_problems(spec, item))
    problems.extend(f"{key}: missing" for key in REQUIRED_ITEM_KEYS if key not in seen)
    values = item_values(manifest)
    apparatus = manifest.get("apparatus")
    expected = apparatus_values(values)
    for field, value in expected.items():
        found = apparatus.get(field) if isinstance(apparatus, Mapping) else None
        if found != value:
            problems.append(f"apparatus.{field}: {_show(found)}, expected {_show(value)}")
    problems.extend(_config_problems(values))
    if manifest.get("status") == "frozen":
        problems.extend(_frozen_problems(manifest, values))
    return problems


def freeze_differences(
    manifest_path: str | os.PathLike[str], current: Mapping[str, Any]
) -> list[str]:
    """Items whose current value or hash differs from the manifest (the CI freeze guard).

    `current` maps item keys to current values (`FreezeValue`s or plain values), normally
    `current_values()`. Reported, in this order: every `manifest_problems` finding; every
    current item missing from the manifest or different from its value (pending items of
    a draft are skipped; a frozen manifest has none); every field of the frozen
    generation config that differs from the running code (`genconfig.config_differences`).
    An empty list means nothing frozen has changed.
    """
    path = Path(manifest_path)
    try:
        manifest = read_json(path)
    except (OSError, CodecError) as err:
        return [f"{path.name}: cannot read the manifest: {err}"]
    if not isinstance(manifest, dict):
        return [f"{path.name}: the manifest is not a JSON object"]
    out = manifest_problems(manifest)
    frozen = manifest.get("status") == "frozen"
    items = _items_by_key(manifest)
    for key, raw in current.items():
        now = raw.value if isinstance(raw, FreezeValue) else raw
        item = items.get(key)
        if item is None:
            out.append(f"{key}: not in the manifest")
            continue
        recorded = item.get("value")
        if now is None or (recorded is None and not frozen):
            continue
        if not _same(recorded, now):
            out.append(f"{key}: frozen {_show(recorded)}, current {_show(now)}")
    doc = item_values(manifest).get("config.document")
    if doc is not None:
        config, _ = _decode_config(doc)
        if config is not None:
            out.extend(
                f"config.document: {name} differs from the running code"
                for name in genconfig.config_differences(config)
            )
    return out


# ---------------------------------------------------------------------------
# Drafts


def _recorded_from(manifest: Mapping[str, Any]) -> dict[str, FreezeValue]:
    """The `config` and `recorded` items of a manifest as values (pending ones included)."""
    out = {}
    for key, item in _items_by_key(manifest).items():
        spec = SPECS.get(key)
        if spec is not None and spec.guard in ("config", "recorded"):
            out[key] = FreezeValue(item.get("value"), str(item.get("source", spec.pending)))
    return out


def _require_draft(manifest: Mapping[str, Any]) -> None:
    if manifest.get("status") != "draft":
        raise FreezeError(
            E_STATUS,
            "only a draft can be refreshed or edited; a frozen "
            "manifest changes only through a new freeze version",
        )


def draft_manifest(
    recorded: Mapping[str, FreezeValue] | None = None,
    *,
    root: str | os.PathLike[str] | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """A new draft from the current repository (`draft_values`)."""
    return build_freeze_manifest(draft_values(recorded, root=root), description=description)


def refresh_draft(
    manifest: Mapping[str, Any], *, root: str | os.PathLike[str] | None = None
) -> dict[str, Any]:
    """Recompute the `code` and `file` items (and the apparatus fields) of a draft and keep
    everything else: the regenerated committed draft must equal the file."""
    _require_draft(manifest)
    values: dict[str, Any] = draft_values(root=root)
    values.update(_recorded_from(manifest))
    return build_freeze_manifest(
        values,
        status="draft",
        freeze_version=str(manifest.get("freeze_version", FREEZE_VERSION)),
        protocol_version=str(manifest.get("protocol_version", PROTOCOL_VERSION)),
        description=str(manifest.get("description", DRAFT_DESCRIPTION)),
    )


def record_value(
    manifest: Mapping[str, Any],
    key: str,
    value: object,
    source: str,
    *,
    root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """A copy of draft `manifest` with `recorded` item `key` set (`value=None` makes it
    pending again, with its fill-in source when `source` is empty)."""
    _require_draft(manifest)
    spec = SPECS.get(key)
    if spec is None or spec.guard != "recorded":
        guard = "unknown" if spec is None else spec.guard
        raise FreezeError(E_ITEM, f"{key} is not a recorded item ({guard}); see REQUIRED_ITEM_KEYS")
    if value is not None:
        problem = _kind_problem(spec.kind, value)
        if problem:
            raise FreezeError(E_ITEM, f"{key}: {problem}, got {_show(value)}")
        if not source.strip():
            raise FreezeError(E_ITEM, f"{key}: a recorded value needs its source")
    values = _recorded_from(manifest)
    values[key] = FreezeValue(value, source) if value is not None else pending_value(key)
    full: dict[str, Any] = draft_values(root=root)
    full.update(values)
    return build_freeze_manifest(
        full,
        status="draft",
        freeze_version=str(manifest.get("freeze_version", FREEZE_VERSION)),
        protocol_version=str(manifest.get("protocol_version", PROTOCOL_VERSION)),
        description=str(manifest.get("description", DRAFT_DESCRIPTION)),
    )


def freeze_values(
    recorded: Mapping[str, FreezeValue],
    config: GenerationConfig,
    fallback: FallbackSet,
    *,
    root: str | os.PathLike[str] | None = None,
) -> dict[str, FreezeValue]:
    """Every item for a G4 build: recorded items (from a filled draft), the config items,
    the fallback bank digests and the current code and file items. Checks that the
    fallback set is the one the config pins and, when the repository carries the LLM
    manifest, that its hash is the config's."""
    base = Path(root) if root is not None else repo_root()
    llm_manifest = base.joinpath(*LLM_MANIFEST_PATH.split("/"))
    if llm_manifest.is_file() and file_sha256(llm_manifest) != config.llm_manifest_sha256:
        raise FreezeError(E_CONFIG, f"{LLM_MANIFEST_PATH} does not hash to the config's value")
    values: dict[str, FreezeValue] = {
        key: value
        for key, value in recorded.items()
        if key in SPECS and SPECS[key].guard == "recorded"
    }
    for spec in ITEM_SPECS:
        if spec.guard == "recorded" and spec.key not in values:
            values[spec.key] = pending_value(spec.key)
    values.update(config_values(config))
    values.update(fallback_values(fallback, config))
    values.update(current_values(base))
    return values


# ---------------------------------------------------------------------------
# Reading manifests (confirmatory runs, #28 / O7.1.1)


@dataclass(frozen=True, slots=True)
class FreezeManifestFile:
    """A checked freeze manifest and its file SHA-256 (`RunManifest.freeze_manifest_sha256`)."""

    path: Path
    manifest: dict[str, Any]
    sha256: str


def load_freeze_manifest(
    path: str | os.PathLike[str], *, require_frozen: bool = False
) -> FreezeManifestFile:
    """Read and check a freeze manifest (`manifest_problems` must be empty); pass
    `.manifest` to `genconfig.check_run_config(..., freeze_manifest=)`."""
    try:
        manifest = read_json(path)
    except (OSError, CodecError) as err:
        raise FreezeError(E_INPUT, f"cannot read {Path(path).name}: {err}") from err
    if not isinstance(manifest, dict):
        raise FreezeError(E_MANIFEST, f"{Path(path).name} is not a JSON object")
    problems = manifest_problems(manifest)
    if problems:
        raise FreezeError(E_MANIFEST, f"{Path(path).name} is not a valid freeze manifest", problems)
    if require_frozen and manifest["status"] != "frozen":
        raise FreezeError(E_STATUS, f"{Path(path).name} is a draft, not a frozen manifest")
    return FreezeManifestFile(Path(path), manifest, file_sha256(path))


def frozen_config(manifest: Mapping[str, Any]) -> GenerationConfig:
    """The frozen generation config (`config.document`) of a manifest."""
    doc = item_values(manifest).get("config.document")
    if doc is None:
        raise FreezeError(E_PENDING, "config.document is pending in this manifest")
    config, problems = _decode_config(doc)
    if config is None:
        raise FreezeError(E_CONFIG, "config.document is not a generation config", problems)
    return config


# ---------------------------------------------------------------------------
# Fallback re-render check and model weights


def verify_fallback_hashes(
    manifest: Mapping[str, Any],
    fallback: FallbackSet | Mapping[str, Any] | str | os.PathLike[str],
) -> list[str]:
    """Re-render a fallback set with the running renderer and compare it with the manifest.

    Checks (`av_sound.fallback.verify_fallback`) that every bank and book recipe renders to
    its recorded waveform and is admissible in acceptance order, recomputes every fallback
    book's digest from freshly rendered samples, and compares the set's `fallback_bank_hash`,
    book and bank digests, renderer and validator versions, reserved-signal digest and
    threshold with the manifest items. Returns the problems (empty: the frozen hashes
    reproduce).
    """
    try:
        fset = fallback if isinstance(fallback, FallbackSet) else load_fallback(fallback)
    except (FallbackError, OSError, ValueError) as err:
        return [f"fallback: cannot load the fallback manifest: {err}"]
    values = item_values(manifest)
    out = [f"fallback: {problem}" for problem in _verify_fallback(fset)]

    def compare(key: str, actual: object) -> None:
        frozen = values.get(key)
        if frozen is None:
            out.append(f"{key}: pending in the manifest (nothing to compare)")
        elif not _same(frozen, actual):
            out.append(f"{key}: the fallback set gives {_show(actual)}, frozen {_show(frozen)}")

    rendered_books = {}
    for book in fset.books:
        pcm = {
            atom.atom_id: _renderer.render(atom.recipe, book.profile).pcm_sha256 for atom in book
        }
        rendered_books[book.profile.value] = snapshot_digest(pcm)
    compare("fallback.books_sha256", rendered_books)
    compare("fallback.banks_sha256", {b.profile.value: b.bank_sha256 for b in fset.banks})
    compare("fallback.bank_hash", fset.fallback_bank_hash)
    compare("renderer.version", fset.renderer_version)
    compare("validator.version", fset.validator_version)
    compare("validator.reserved_sha256", fset.reserved_sha256)
    threshold = values.get("separation.threshold")
    if isinstance(threshold, str) and _kind_problem("decimal", threshold) is None:
        if parse_threshold(threshold) != fset.threshold:
            out.append("separation.threshold: the fallback set was built at another threshold")
    else:
        out.append("separation.threshold: missing or not decimal text")
    return out


def weights_sha256(model_dir: str | os.PathLike[str]) -> str:
    """`model.weights_sha256` of a local model directory: `jsonio.file_set_sha256` of
    `{file name: SHA-256}` over its `*.safetensors` files (run on the GPU host; the same
    value as from the per-file LFS SHA-256s of the model repository)."""
    directory = Path(model_dir)
    files = sorted(
        p for p in directory.iterdir() if p.name.endswith(WEIGHTS_SUFFIX) and p.is_file()
    )
    if not files:
        raise FreezeError(E_INPUT, f"no {WEIGHTS_SUFFIX} files in {directory}")
    return file_set_sha256({p.name: file_sha256(p) for p in files})


# ---------------------------------------------------------------------------
# Reading aid


def manifest_table(manifest: Mapping[str, Any]) -> str:
    """A Markdown table of the items (key, value, SHA-256, guard, source) for review."""

    def cell(text: str) -> str:
        return text.replace("|", "\\|")

    lines = [
        f"Freeze manifest v{manifest.get('freeze_version')} ({manifest.get('status')}), "
        f"protocol {manifest.get('protocol_version')}, commit {manifest.get('repo_commit')}, "
        f"tag {manifest.get('tag')}",
        "",
        "| Key | Value | SHA-256 | Guard | Source |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in _items_by_key(manifest).values():
        value = item.get("value")
        shown = "PENDING" if value is None else _show(value, 60)
        if isinstance(value, Mapping) and len(_show(value, 10_000)) > 60:
            shown = f"object ({len(value)} fields)"
        digest = item.get("sha256")
        lines.append(
            "| "
            + " | ".join(
                cell(part)
                for part in (
                    f"`{item.get('key')}`",
                    f"`{shown}`" if value is not None else shown,
                    f"`{digest[:16]}...`" if isinstance(digest, str) else "",
                    str(item.get("guard")),
                    str(item.get("source")),
                )
            )
            + " |"
        )
    apparatus = manifest.get("apparatus")
    if isinstance(apparatus, Mapping):
        lines += ["", "| Apparatus field | Value |", "| --- | --- |"]
        lines += [f"| `{k}` | `{_show(v)}` |" for k, v in apparatus.items()]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Command line


def _write(path: Path, manifest: Mapping[str, Any], *, exclusive: bool) -> str:
    try:
        return write_document(path, manifest, exclusive=exclusive)
    except FileExistsError as err:
        raise FreezeError(
            E_INPUT, f"{path.name} exists; a freeze manifest is never overwritten"
        ) from err


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        data = read_json(path)
    except (OSError, CodecError) as err:
        raise FreezeError(E_INPUT, f"cannot read {path.name}: {err}") from err
    if not isinstance(data, dict):
        raise FreezeError(E_INPUT, f"{path.name} is not a JSON object")
    return data


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m av_generation.freeze",
        description="G4 freeze manifest: guard, draft upkeep, build, verification "
        "(generation/docs/freeze.md).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="CI freeze guard: compare a manifest with the repository")
    check.add_argument("manifest", nargs="?", type=Path)
    refresh = sub.add_parser("refresh", help="recompute the code and file items of a draft")
    refresh.add_argument("manifest", nargs="?", type=Path)
    refresh.add_argument("--check", action="store_true", help="exit 1 if the file would change")
    record = sub.add_parser("record", help="set a recorded item of a draft")
    record.add_argument("key")
    given = record.add_mutually_exclusive_group(required=True)
    given.add_argument("--value", help="the value as JSON (e.g. '\"0.6.3\"' or 16896)")
    given.add_argument("--text", help="the value as plain text")
    given.add_argument("--pending", action="store_true", help="make the item pending again")
    record.add_argument("--source", default="", help="where the value comes from")
    record.add_argument("--manifest", type=Path)
    build = sub.add_parser("build", help="build a manifest from a filled draft (G4)")
    build.add_argument("--draft", type=Path, required=True)
    build.add_argument("--config", type=Path, required=True, help="frozen generation-config.json")
    build.add_argument("--fallback", type=Path, required=True, help="fallback-manifest.json")
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--status", choices=("draft", "frozen"), default="draft")
    build.add_argument("--protocol-version", default=PROTOCOL_VERSION)
    build.add_argument("--freeze-version", default=FREEZE_VERSION)
    build.add_argument("--repo-commit")
    build.add_argument("--tag")
    build.add_argument(
        "--signoff",
        nargs=3,
        action="append",
        default=[],
        metavar=("ROLE", "DATE", "URL"),
        help="owner|advisor, YYYY-MM-DD, link to the sign-off comment",
    )
    verify = sub.add_parser("verify", help="re-render a fallback set and compare its hashes")
    verify.add_argument("manifest", type=Path)
    verify.add_argument("--fallback", type=Path, required=True)
    config = sub.add_parser("config", help="write the frozen generation-config.json")
    config.add_argument("manifest", type=Path)
    config.add_argument("--out", type=Path, required=True)
    weights = sub.add_parser("weights", help="model.weights_sha256 of a local model directory")
    weights.add_argument("model_dir", type=Path)
    table = sub.add_parser("table", help="print the items as a Markdown table")
    table.add_argument("manifest", nargs="?", type=Path)
    return parser


def _cmd_check(args: argparse.Namespace) -> int:
    path = args.manifest or active_manifest_path()
    differences = freeze_differences(path, current_values())
    status = _read_manifest(path).get("status") if path.is_file() else None
    if differences:
        print(f"FREEZE GUARD FAILED: {path.name} ({status}) differs from the repository:")
        for line in differences:
            print(f"  - {line}")
        return 1
    print(f"freeze guard OK: {path.name} ({status}); every checked value equals the repository")
    return 0


def _cmd_refresh(args: argparse.Namespace) -> int:
    path = args.manifest or active_manifest_path()
    manifest = _read_manifest(path)
    text = document_text(refresh_draft(manifest))
    current = path.read_text(encoding="utf-8")
    if args.check:
        if text != current:
            print(f"{path.name} is out of date: run `python -m av_generation.freeze refresh`")
            return 1
        print(f"{path.name} is up to date")
        return 0
    if text != current:
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        print(f"refreshed {path.name}")
    else:
        print(f"{path.name} is up to date")
    return 0


def _cmd_record(args: argparse.Namespace) -> int:
    path = args.manifest or (repo_root() / "generation" / DRAFT_NAME)
    if args.pending:
        value: object = None
    elif args.text is not None:
        value = args.text
    else:
        try:
            value = read_json_text(args.value)
        except CodecError as err:
            raise FreezeError(E_INPUT, f"--value is not JSON: {err}") from err
    updated = record_value(_read_manifest(path), args.key, value, args.source)
    _write(path, updated, exclusive=False)
    print(f"recorded {args.key} in {path.name}")
    return 0


def read_json_text(text: str) -> Any:  # noqa: ANN401 - any JSON value
    """Strictly decode JSON text (the `--value` of `record`)."""
    try:
        return strict_json_loads(text.encode("utf-8"))
    except StrictJsonError as err:
        raise CodecError(str(err)) from err


def _cmd_build(args: argparse.Namespace) -> int:
    draft = _read_manifest(args.draft)
    try:
        config = GenerationConfig.read(args.config)
    except (OSError, RecordError, CodecError) as err:
        raise FreezeError(E_CONFIG, f"cannot read {args.config.name}: {err}") from err
    try:
        fset = load_fallback(args.fallback)
    except (FallbackError, OSError, ValueError) as err:
        raise FreezeError(E_FALLBACK, f"cannot load {args.fallback.name}: {err}") from err
    if args.status == "frozen" and fset.demo_seed is not None:
        raise FreezeError(E_STATUS, "a DEMO fallback set cannot be frozen")
    values = freeze_values(_recorded_from(draft), config, fset)
    signoff = [{"role": r, "date": d, "reference": u} for r, d, u in args.signoff]
    manifest = build_freeze_manifest(
        values,
        status=args.status,
        freeze_version=args.freeze_version,
        protocol_version=args.protocol_version,
        repo_commit=args.repo_commit,
        tag=args.tag,
        signoff=signoff,
    )
    problems = verify_fallback_hashes(manifest, fset)
    if problems:
        raise FreezeError(E_FALLBACK, "the fallback set does not reproduce", problems)
    digest = _write(args.out, manifest, exclusive=True)
    pending = sorted(k for k, v in item_values(manifest).items() if v is None)
    print(f"wrote {args.out.name} ({args.status}), file SHA-256 {digest}")
    print(f"config.frozen_sha256 {config.frozen_sha256()}")
    print(f"fallback re-rendered: {len(fset.banks)} banks, {len(fset.books)} books reproduce")
    if pending:
        print(f"pending ({len(pending)}): {', '.join(pending)}")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    manifest = _read_manifest(args.manifest)
    problems = verify_fallback_hashes(manifest, args.fallback)
    if problems:
        print(f"FALLBACK CHECK FAILED for {args.manifest.name}:")
        for line in problems:
            print(f"  - {line}")
        return 1
    values = item_values(manifest)
    print(
        f"fallback reproduces {args.manifest.name}: renderer {values['renderer.version']}, "
        f"fallback_bank_hash {values['fallback.bank_hash']}"
    )
    for profile, digest in sorted((values.get("fallback.books_sha256") or {}).items()):
        print(f"  book {profile} {digest} (re-rendered, 16 atoms)")
    for profile, digest in sorted((values.get("fallback.banks_sha256") or {}).items()):
        print(f"  bank {profile} {digest} (re-rendered, 64 recipes)")
    return 0


def _cmd_config(args: argparse.Namespace) -> int:
    config = frozen_config(_read_manifest(args.manifest))
    try:
        digest = config.write(args.out)
    except FileExistsError as err:
        raise FreezeError(E_INPUT, f"{args.out.name} exists; not overwritten") from err
    print(f"wrote {args.out.name}: config hash {config.frozen_sha256()}, file SHA-256 {digest}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point (`python -m av_generation.freeze --help`)."""
    args = _parser().parse_args(argv)
    try:
        if args.command == "check":
            return _cmd_check(args)
        if args.command == "refresh":
            return _cmd_refresh(args)
        if args.command == "record":
            return _cmd_record(args)
        if args.command == "build":
            return _cmd_build(args)
        if args.command == "verify":
            return _cmd_verify(args)
        if args.command == "config":
            return _cmd_config(args)
        if args.command == "weights":
            print(weights_sha256(args.model_dir))
            return 0
        path = args.manifest or active_manifest_path()
        sys.stdout.write(manifest_table(_read_manifest(path)))
        return 0
    except FreezeError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
