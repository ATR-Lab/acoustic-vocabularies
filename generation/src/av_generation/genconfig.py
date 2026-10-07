"""The generation configuration and its frozen hash (one value for #20, #25, #26-#28).

`GenerationConfig` (`generation-config.schema.json`, format `av-generation/generation-config`)
is the one document whose hash every "config hash" means: the G4 freeze manifest item
`config.frozen_sha256` (#25), `RunManifest.generation_config_sha256` of every batch and
bank run, and `generation_config_sha256` of every bank manifest (#26) and bank register
row (#27, #28). It holds everything that decides what a method can propose and what is
admissible, and nothing per batch or per bank:

- model ID and revision, the LLM-manifest hash (#16: runtime, precision, weights, chat
  template), the decoding values and the decoding-schema hash;
- the A3 and B prompt-set hashes and the meaning-set hash (#17, `meanings`);
- renderer and validator versions and hashes (from the running code);
- the separation threshold, the seed function and namespaces;
- the Study A and Study B budgets, the A2 rules, the selector rules;
- the fallback bank hash and fallback-book hashes (Study A).

Hash: `GenerationConfig.frozen_sha256()` = `canonical_sha256` of the document
(`jsonio`), so whitespace never matters. Runs store the document as
`generation-config.json` in the run directory (`rundir.RunLayout.generation_config`) and
the bank builder stores it beside each bank manifest, so `banks verify` can read the
threshold and code versions a bank was built with.

Checks before a run starts (`check_run_config`): the running code and constants must
equal the config (`config_differences`); a confirmatory batch or bank run also needs a
`frozen` freeze manifest whose `config.frozen_sha256` equals the config's hash (#25:
"confirmatory runs refuse to start if their config hash differs"). The orchestrator (#20)
and the bank builder (#26) call it; pilot runs record their (unfrozen) config hash.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from av_sound.fallback import FallbackSet
from av_sound.renderer import RENDERER_VERSION
from av_sound.store import validator_code_hash
from av_sound.validate import VALIDATOR_VERSION
from av_sound.version import renderer_hash, renderer_recipe_schema_hash

from av_generation import constants as C
from av_generation import rundir
from av_generation.ids import RunKind, is_demo
from av_generation.records import Document
from av_generation.seeds import SeedNamespace

GENERATION_CONFIG_NAME: Final = rundir.GENERATION_CONFIG_NAME
SEED_FUNCTION: Final = "sha256-first8-be-unsigned-v1"
"""`seeds.derive_seed`: first 8 bytes of SHA-256 of the `|`-joined key, unsigned big-endian."""
FREEZE_CONFIG_KEY: Final = "config.frozen_sha256"
"""The freeze-manifest item that holds the frozen config hash (#25)."""

E_CONFIG_CODE: Final = "E_CONFIG_CODE"
E_CONFIG_KIND: Final = "E_CONFIG_KIND"
E_FREEZE_MISSING: Final = "E_FREEZE_MISSING"
E_FREEZE_STATUS: Final = "E_FREEZE_STATUS"
E_FREEZE_MISMATCH: Final = "E_FREEZE_MISMATCH"


class ConfigMismatch(RuntimeError):
    """A run may not start with this configuration; `.code` names the rule."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True, slots=True)
class ModelPin:
    model_id: str
    revision: str


@dataclass(frozen=True, slots=True)
class DecodingConfig:
    temperature: float
    top_p: float
    top_k: int
    repetition_penalty: float
    max_tokens: int
    max_input_tokens: int


@dataclass(frozen=True, slots=True)
class PromptHashes:
    """Prompt-set hashes (`jsonio.file_set_sha256` of each set's files; #17)."""

    a3_sha256: str
    b_sha256: str


@dataclass(frozen=True, slots=True)
class CodePins:
    renderer_version: str
    renderer_hash: str
    renderer_recipe_schema_hash: str
    validator_version: str
    validator_hash: str
    """`av_sound.store.validator_code_hash()`."""


@dataclass(frozen=True, slots=True)
class SeedRules:
    function: str
    namespaces: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StudyABudget:
    rounds_per_atom: int
    slots_per_round: int
    slot_cap_ms: int
    rating_slot_ms: int
    reference_onset_ms: int
    raters_per_panel: int
    atoms_per_appointment: int


@dataclass(frozen=True, slots=True)
class StudyBBudget:
    slots_per_cell: int
    options_per_cell: int
    shown_options: int
    max_attempts: int
    slot_cap_ms: int


@dataclass(frozen=True, slots=True)
class A2Rules:
    pitch_steps: tuple[int, ...]
    child_mutations: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class SelectorRules:
    min_acceptable_comfort: int
    first_atom_distinguishability: int
    missing_comfort: str
    """`not_acceptable` (#20 proposed rule)."""


@dataclass(frozen=True, slots=True)
class FallbackPins:
    bank_hash: str
    """`av_sound.fallback.fallback_bank_hash` of the fallback manifest."""
    books_sha256: Mapping[str, str]
    """Profile -> `FallbackBook.book_sha256`."""


@dataclass(frozen=True, slots=True)
class GenerationConfig(Document):
    """The configuration a batch or bank run is generated under (see the module docstring)."""

    TAG = "av-generation/generation-config"
    VERSION = 1
    SCHEMA = "generation-config.schema.json"

    name: str
    """`DEMO-...` for demo configs; e.g. `pilot-1` or `frozen-1.0` otherwise."""
    demo: bool
    model: ModelPin
    llm_manifest_sha256: str | None
    """File SHA-256 of the LLM manifest (#16); `None` only in demo configs."""
    decoding: DecodingConfig
    decoding_schema_sha256: str
    prompts: PromptHashes
    meanings_sha256: str
    code: CodePins
    separation_threshold: str
    seeds: SeedRules
    budget_a: StudyABudget
    budget_b: StudyBBudget
    a2: A2Rules
    selector: SelectorRules
    fallback: FallbackPins

    def frozen_sha256(self) -> str:
        """The config hash (canonical JSON of the document)."""
        return self.sha256()


def current_code() -> CodePins:
    """Renderer and validator pins of the running code."""
    return CodePins(
        renderer_version=RENDERER_VERSION,
        renderer_hash=renderer_hash(),
        renderer_recipe_schema_hash=renderer_recipe_schema_hash(),
        validator_version=VALIDATOR_VERSION,
        validator_hash=validator_code_hash(),
    )


def _rules() -> dict[str, Any]:
    d = C.FROZEN_DECODING
    return {
        "model": ModelPin(C.MODEL_ID, C.MODEL_REVISION),
        "decoding": DecodingConfig(
            d.temperature, d.top_p, d.top_k, d.repetition_penalty, d.max_tokens, C.MAX_INPUT_TOKENS
        ),
        "seeds": SeedRules(SEED_FUNCTION, tuple(ns.value for ns in SeedNamespace)),
        "budget_a": StudyABudget(
            C.ROUNDS_PER_ATOM,
            C.SLOTS_PER_ROUND,
            C.SLOT_CAP_MS,
            C.RATING_SLOT_MS,
            C.REFERENCE_ONSET_MS,
            C.RATERS_PER_PANEL,
            C.ATOMS_PER_APPOINTMENT,
        ),
        "budget_b": StudyBBudget(
            C.B_SLOTS_PER_CELL,
            C.B_OPTIONS_PER_CELL,
            C.B_SHOWN_OPTIONS,
            C.B_MAX_ATTEMPTS,
            C.SLOT_CAP_MS,
        ),
        "a2": A2Rules(C.A2_PITCH_STEPS, C.A2_CHILD_MUTATIONS),
        "selector": SelectorRules(
            C.MIN_ACCEPTABLE_COMFORT, C.FIRST_ATOM_DISTINGUISHABILITY, "not_acceptable"
        ),
    }


def fallback_pins(fallback: FallbackSet) -> FallbackPins:
    """Fallback hashes of a loaded fallback set."""
    return FallbackPins(
        fallback.fallback_bank_hash,
        {book.profile.value: book.book_sha256 for book in fallback.books},
    )


def build_generation_config(
    name: str,
    *,
    llm_manifest_sha256: str | None,
    decoding_schema_sha256: str,
    prompts: PromptHashes,
    meanings_sha256: str,
    separation_threshold: str,
    fallback: FallbackPins,
) -> GenerationConfig:
    """A config for the running code: code pins, decoding, seeds, budgets and rules come
    from the code (`constants`, `av_sound`); the arguments are the file-based inputs."""
    return GenerationConfig(
        name=name,
        demo=is_demo(name),
        llm_manifest_sha256=llm_manifest_sha256,
        decoding_schema_sha256=decoding_schema_sha256,
        prompts=prompts,
        meanings_sha256=meanings_sha256,
        code=current_code(),
        separation_threshold=separation_threshold,
        fallback=fallback,
        **_rules(),
    ).check()


def config_differences(config: GenerationConfig) -> tuple[str, ...]:
    """Fields whose value differs from the running code and constants (empty = same)."""
    expected: dict[str, Any] = {"code": current_code(), **_rules()}
    found = []
    for name, value in expected.items():
        if getattr(config, name) != value:
            found.append(name)
    if config.demo != is_demo(config.name):
        found.append("demo")
    if not config.demo and config.llm_manifest_sha256 is None:
        found.append("llm_manifest_sha256")
    return tuple(found)


def freeze_item(freeze_manifest: Mapping[str, Any], key: str) -> Any:  # noqa: ANN401
    """The `value` of a freeze-manifest item (`KeyError` if the manifest lacks it)."""
    for item in freeze_manifest.get("items", ()):
        if isinstance(item, Mapping) and item.get("key") == key:
            return item.get("value")
    raise KeyError(key)


def check_run_config(
    config: GenerationConfig,
    *,
    kind: RunKind | str,
    freeze_manifest: Mapping[str, Any] | None = None,
) -> None:
    """Refuse to start a batch or bank run under the wrong configuration.

    - Every run: the code and constants equal the config (`E_CONFIG_CODE`), and demo
      configs go with demo/synthetic runs only (`E_CONFIG_KIND`).
    - Confirmatory runs: a freeze manifest is given (`E_FREEZE_MISSING`), its status is
      `frozen` (`E_FREEZE_STATUS`) and its `config.frozen_sha256` equals
      `config.frozen_sha256()` (`E_FREEZE_MISMATCH`).
    - Other kinds: a given freeze manifest is checked the same way except for the status.
    """
    kind = RunKind(kind)
    differences = config_differences(config)
    if differences:
        raise ConfigMismatch(
            E_CONFIG_CODE, f"config {config.name!r} differs from the running code: {differences}"
        )
    public = kind in (RunKind.DEMO, RunKind.SYNTHETIC)
    if config.demo != public:
        raise ConfigMismatch(
            E_CONFIG_KIND, f"a {'demo' if config.demo else 'real'} config cannot run a {kind} run"
        )
    if freeze_manifest is None:
        if kind is RunKind.CONFIRMATORY:
            raise ConfigMismatch(E_FREEZE_MISSING, "confirmatory runs need the G4 freeze manifest")
        return
    if kind is RunKind.CONFIRMATORY and freeze_manifest.get("status") != "frozen":
        raise ConfigMismatch(E_FREEZE_STATUS, "the freeze manifest is not frozen")
    try:
        frozen = freeze_item(freeze_manifest, FREEZE_CONFIG_KEY)
    except KeyError:
        raise ConfigMismatch(
            E_FREEZE_MISMATCH, f"the freeze manifest has no {FREEZE_CONFIG_KEY} item"
        ) from None
    if frozen != config.frozen_sha256():
        raise ConfigMismatch(
            E_FREEZE_MISMATCH,
            f"config hash {config.frozen_sha256()} differs from the frozen value {frozen}",
        )
