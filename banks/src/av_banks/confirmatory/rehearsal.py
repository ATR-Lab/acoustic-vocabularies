"""Rehearse the confirmatory campaign on `DEMO-` IDs with a scripted proposer (no model).

`rehearse(out)` runs every step of the real procedure on synthetic inputs, so the
tooling, the files and the counts can be checked without the G4 freeze or the LLM host:

- 72 DEMO unit permutations (`demo_units`: stand-ins with the fields the builder reads,
  `"demo": true`, labels and atom order drawn from a DEMO seed label; not schedules
  output);
- a DEMO generation config from the repository's DEMO inputs (`demo_config`: DEMO
  meaning set, the recipe schema as decoding schema, the DEMO fallback set, synthetic
  prompt-set hashes) and a DEMO freeze manifest holding its hash
  (`demo_freeze_manifest`, tag `DEMO-g4-freeze`);
- DEMO pilot namespaces `DEMO-P001`..`DEMO-P008`;
- the plan, the parallel run with `DemoSlotProposer` (random recipes drawn from each
  slot's seed key, simulated model latency on a per-bank `ManualClock`; banks listed in
  `unavailable` get unusable output and end unavailable after 4 x 12 slots),
  `verify_all` and `compile_register`.

With `workers=1` every bank, and so `register.csv`, is the same on every machine. The
runner's events, the timing log's `ALL` row and the archive carry the real run time.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from av_generation._paths import examples_path
from av_generation.clock import Clock, ManualClock, SystemClock
from av_generation.freeze import REQUIRED_ITEM_KEYS
from av_generation.genconfig import (
    FREEZE_CONFIG_KEY,
    GenerationConfig,
    PromptHashes,
    build_generation_config,
    fallback_pins,
)
from av_generation.jsonio import file_set_sha256, messages_sha256, schema_sha256, write_document
from av_generation.meanings import load_meanings
from av_generation.outcomes import LlmStatus, SlotOutcome
from av_generation.proposers import BCellState
from av_generation.seeds import rng_for, seed_from_key
from av_sound._paths import data_root as sound_root
from av_sound._paths import schema_path as sound_schema_path
from av_sound.fallback import load_fallback
from av_sound.grammar import ATOM_IDS, parse_atom_id
from av_sound.recipe import AMPLITUDES, GAPS_MS, PITCHES, RHYTHM_WEIGHTS, TOTAL_MS
from av_sound.renderer import MIN_EVENT_SAMPLES, event_samples
from av_sound.store import SEMANTIC_LABELS

from av_banks import builder
from av_banks.builder import LedgerFactory
from av_banks.proposer import Proposal, ProposerConfigError

from .common import CampaignLayout, campaign_bank_ids, dyad_slot
from .plan import CampaignPlan, PlannedBank, create_plan
from .register import RegisterResult, VerifyAll, compile_register, verify_all
from .runner import CampaignRun, CampaignStatus, run_campaign
from .seed_check import read_pilot

DEMO_THRESHOLD: Final = "0.10"
DEMO_SEED_LABEL: Final = "DEMO-o8-1-1-units"
DEFAULT_UNAVAILABLE: Final[tuple[str, ...]] = ("DEMO-C007", "DEMO-C030", "DEMO-C070")
"""Two main banks and one spare end unavailable: 69 complete, decision `ready`."""
DEMO_PILOT: Final[tuple[str, ...]] = tuple(f"DEMO-P{n:03d}" for n in range(1, 9))
_A3_TEXT: Final = "DEMO A3 instruction (synthetic rehearsal text, not a study prompt)"
_B_TEXT: Final = "DEMO B instruction (synthetic rehearsal text, not a study prompt)"
Mode = Literal["valid", "invalid", "outage"]


# ---------------------------------------------------------------------------
# Inputs


def _rng(*parts: str) -> random.Random:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def demo_unit(unit_id: str, *, seed_label: str = DEMO_SEED_LABEL) -> dict[str, Any]:
    """A DEMO stand-in for a Study B unit's `permutation.json` (the fields `av_banks.
    permutation` reads): labels and atom order drawn from `seed_label` and the unit ID."""
    rng = _rng(seed_label, unit_id)
    labels: dict[str, str] = {}
    for (family, role), allowed in sorted(SEMANTIC_LABELS.items()):
        shuffled = list(allowed)
        rng.shuffle(shuffled)
        atoms = sorted(
            a
            for a in ATOM_IDS
            if parse_atom_id(a).family == family and parse_atom_id(a).role == role
        )
        labels.update(zip(atoms, shuffled, strict=True))
    order = list(ATOM_IDS)
    rng.shuffle(order)
    return {
        "format": "av-schedules/permutation",
        "format_version": 2,
        "study": "B",
        "unit_id": unit_id,
        "set": "confirmatory",
        "demo": True,
        "atoms": [{"atom_id": a, "semantic_label": labels[a]} for a in ATOM_IDS],
        "atom_order": order,
        "generator": {"name": "av-banks rehearsal", "note": "DEMO stand-in, not schedules output"},
        "seed_label": seed_label,
    }


def demo_units(out: str | os.PathLike[str], *, seed_label: str = DEMO_SEED_LABEL) -> Path:
    """Write the 72 DEMO units (`<out>/<unit_id>/permutation.json`); returns `out`."""
    root = Path(out)
    for bank_id in campaign_bank_ids(demo=True):
        unit_id = dyad_slot(bank_id)
        path = root / unit_id / "permutation.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_document(path, demo_unit(unit_id, seed_label=seed_label))
    return root


def _decoding_schema() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        sound_schema_path("recipe.schema.json").read_text(encoding="utf-8")
    )
    return data


def demo_config(name: str = "DEMO-o8-1-1-config") -> GenerationConfig:
    """The DEMO generation config of the rehearsal (module docstring)."""
    meanings = load_meanings(examples_path("demo-meanings"))
    fallback = load_fallback(sound_root() / "testvectors" / "fallback" / "demo-manifest.json")
    prompts = PromptHashes(
        a3_sha256=file_set_sha256(
            {"a3/instruction.txt": hashlib.sha256(_A3_TEXT.encode()).hexdigest()}
        ),
        b_sha256=file_set_sha256(
            {"b/instruction.txt": hashlib.sha256(_B_TEXT.encode()).hexdigest()}
        ),
    )
    return build_generation_config(
        name,
        llm_manifest_sha256=None,
        decoding_schema_sha256=schema_sha256(_decoding_schema()),
        prompts=prompts,
        meanings_sha256=meanings.sha256(),
        separation_threshold=DEMO_THRESHOLD,
        fallback=fallback_pins(fallback),
    )


def demo_freeze_manifest(config: GenerationConfig, *, status: str = "frozen") -> dict[str, Any]:
    """A DEMO freeze manifest (`freeze-manifest.schema.json`) whose
    `config.frozen_sha256` is `config`'s hash. Only `config.frozen_sha256` and a few
    pins carry real values; the other items say `DEMO`."""
    known: dict[str, Any] = {
        FREEZE_CONFIG_KEY: config.frozen_sha256(),
        "renderer.version": config.code.renderer_version,
        "renderer.hash": config.code.renderer_hash,
        "validator.version": config.code.validator_version,
        "validator.hash": config.code.validator_hash,
        "separation.threshold": config.separation_threshold,
        "model.id": config.model.model_id,
        "model.revision": config.model.revision,
        "prompts.b_sha256": config.prompts.b_sha256,
        "meanings.sha256": config.meanings_sha256,
    }
    items = [
        {
            "key": key,
            "category": key.split(".", 1)[0],
            "value": known.get(key, "DEMO"),
            "sha256": config.frozen_sha256() if key == FREEZE_CONFIG_KEY else None,
            "source": "DEMO rehearsal (av_banks.confirmatory.rehearsal); not a G4 value",
        }
        for key in REQUIRED_ITEM_KEYS
    ]
    reference = "https://github.com/ATR-Lab/acoustic-vocabularies/issues/28"
    return {
        "format": "av-generation/freeze-manifest",
        "format_version": 1,
        "freeze_version": "0.1",
        "status": status,
        "protocol_version": "DEMO",
        "repo_commit": "0" * 40,
        "tag": "DEMO-g4-freeze",
        "items": items,
        "apparatus": {
            "renderer_recipe_schema_hash": config.code.renderer_recipe_schema_hash,
            "model_revision": config.model.revision,
            "runtime_precision": None,
            "prompt_hash": config.prompts.b_sha256,
            "fallback_bank_hash": None,
        },
        "signoff": [
            {"role": "owner", "date": "2027-04-22", "reference": reference},
            {"role": "advisor", "date": "2027-04-22", "reference": reference},
        ],
    }


# ---------------------------------------------------------------------------
# The scripted proposer


def demo_recipe(rng: Any) -> dict[str, Any]:  # noqa: ANN401 - a numpy Generator
    """A random recipe whose three events are long enough to render (drawn from `rng`)."""

    def pick(values: Sequence[Any]) -> Any:  # noqa: ANN401
        return values[int(rng.integers(len(values)))]

    while True:
        recipe = {
            "total_ms": pick(TOTAL_MS),
            "pitches": [pick(PITCHES) for _ in range(3)],
            "rhythm_weights": [pick(RHYTHM_WEIGHTS) for _ in range(3)],
            "gaps_ms": [pick(GAPS_MS) for _ in range(2)],
            "amplitudes": [pick(AMPLITUDES) for _ in range(3)],
        }
        lengths = event_samples(recipe["total_ms"], recipe["rhythm_weights"], recipe["gaps_ms"])
        if min(lengths) >= MIN_EVENT_SAMPLES:
            return recipe


class DemoSlotProposer:
    """A `SlotProposer` for DEMO configs only: per slot, a random recipe from the slot's
    seed key (`seeds.rng_for`), as JSON text, with a simulated model latency (advanced on
    `clock` when it is a `ManualClock`). `mode="invalid"` returns unusable text (the bank
    ends unavailable); `mode="outage"` reports a failed model call (`server_error`)."""

    def __init__(
        self,
        *,
        mode: Mode = "valid",
        clock: ManualClock | None = None,
        latency_ms: tuple[int, int] = (150, 900),
    ) -> None:
        self.mode = mode
        self.clock = clock
        self.latency_ms = latency_ms
        self.schema_sha256: str | None = None

    def check_config(self, config: GenerationConfig) -> None:
        if not config.demo:
            raise ProposerConfigError("the DEMO proposer runs DEMO configs only")
        self.schema_sha256 = config.decoding_schema_sha256

    def propose(self, cell: BCellState, *, seed_key: str, slot_id: str) -> Proposal:
        rng = rng_for(seed_key)
        latency = int(rng.integers(self.latency_ms[0], self.latency_ms[1] + 1))
        if self.clock is not None:
            self.clock.advance(latency)
        messages = (
            {"role": "system", "content": _B_TEXT},
            {"role": "user", "content": f"{cell.bank_id} {slot_id} {cell.semantic_label}"},
        )
        common: dict[str, Any] = {
            "prompt_sha256": messages_sha256(messages),
            "schema_sha256": self.schema_sha256,
            "tokens_in": 100 + 30 * len(cell.retained),
            "latency_ms": latency,
        }
        seed = seed_from_key(seed_key)
        if self.mode == "outage":
            return Proposal(
                SlotOutcome.INVALID_JSON,
                None,
                seed,
                llm_status=LlmStatus.SERVER_ERROR,
                detail="DEMO outage",
                **common,
            )
        if self.mode == "invalid":
            text = "DEMO: unusable output"
            return Proposal(
                SlotOutcome.INVALID_JSON,
                None,
                seed,
                raw_output=text,
                llm_status=LlmStatus.OK,
                tokens_out=5,
                **common,
            )
        recipe = demo_recipe(rng)
        text = json.dumps(recipe, sort_keys=True)
        return Proposal(
            None,
            recipe,
            seed,
            raw_output=text,
            llm_status=LlmStatus.OK,
            tokens_out=len(text) // 3,
            **common,
        )


# ---------------------------------------------------------------------------
# The rehearsal


@dataclass(frozen=True, slots=True)
class Rehearsal:
    root: Path
    """The campaign directory."""
    plan: CampaignPlan
    run: CampaignRun
    verify: VerifyAll | None
    register: RegisterResult | None


def rehearse(
    out: str | os.PathLike[str],
    *,
    campaign_id: str = "DEMO-cbanks-01",
    unavailable: Sequence[str] = DEFAULT_UNAVAILABLE,
    outage: Sequence[str] = (),
    parallel_banks: int = 4,
    workers: int = 1,
    jobs: int = 1,
    only: Sequence[str] | None = None,
    ledger_factory: LedgerFactory | None = None,
    on_progress: Callable[[CampaignStatus, str], None] | None = None,
    monitor_interval_s: float = 30.0,
    clock: Clock | None = None,
    fsync: bool = False,
) -> Rehearsal:
    """Run the whole procedure on DEMO IDs under `out` (`inputs/`, `campaign/`). Banks in
    `only` (default: all) are built; the register is compiled when all 72 are done.
    `ledger_factory` defaults to #17's ledger (`builder.slot_ledger`). The runner runs on
    `clock` (default: the system clock); each bank's model time is simulated on its own
    `ManualClock`, so its slot records, manifest and bank hash are the same in every run."""
    base = Path(out)
    units = demo_units(base / "inputs" / "units")
    config = demo_config()
    freeze_path = base / "inputs" / "freeze-manifest.json"
    write_document(freeze_path, demo_freeze_manifest(config))
    runner = clock if clock is not None else SystemClock()
    root = base / "campaign"
    plan = create_plan(
        root,
        campaign_id=campaign_id,
        config=config,
        freeze_manifest=freeze_path,
        units=units,
        pilot=read_pilot(namespaces=DEMO_PILOT),
        clock=runner,
        parallel_banks=parallel_banks,
    )
    clocks: dict[str, ManualClock] = {}
    failing = set(unavailable)
    down = set(outage)

    def bank_clock(bank: PlannedBank) -> ManualClock:
        # simulated model time from 0 ms, starting at the runner's time
        return clocks.setdefault(bank.bank_id, ManualClock(start_utc=runner.utc_now()))

    def factory(_run: Any, bank: PlannedBank) -> DemoSlotProposer:  # noqa: ANN401
        mode: Mode = (
            "outage" if bank.bank_id in down else "invalid" if bank.bank_id in failing else "valid"
        )
        return DemoSlotProposer(mode=mode, clock=bank_clock(bank))

    run = run_campaign(
        root,
        proposer_factory=factory,
        clock=runner,
        bank_clock=bank_clock,
        parallel_banks=parallel_banks,
        workers=workers,
        only=only,
        ledger_factory=ledger_factory or builder.slot_ledger,
        fsync=fsync,
        monitor_interval_s=monitor_interval_s,
        on_progress=on_progress,
    )
    verify = verify_all(root, jobs=jobs) if run.halted is None else None
    done = run.status.counts.get("complete", 0) + run.status.counts.get("unavailable", 0)
    register = (
        compile_register(root, clock=runner)
        if verify is not None and done == len(plan.banks)
        else None
    )
    return Rehearsal(CampaignLayout.at(root).root, plan, run, verify, register)
