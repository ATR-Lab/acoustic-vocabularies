"""The campaign plan: G4 freeze check, 72 bank IDs, dyad slots, seed namespaces (#28).

`create_plan` runs before any slot, in restricted storage (a confirmatory campaign is
refused inside a git work tree, like every confirmatory run):

1. **Freeze.** The G4 freeze manifest (#25) is read through #25's freeze guard
   (`freeze_check.load_freeze`: `freeze.load_freeze_manifest(path, require_frozen=True)`
   for a confirmatory campaign, which also compares the running code and the committed
   files with the manifest), and `genconfig.check_run_config(config,
   kind="confirmatory", freeze_manifest=...)` passes: the running code equals the
   config, the manifest is `frozen` and its `config.frozen_sha256` equals the config
   hash. A confirmatory plan also checks the freeze tag in the repository (`repo=`,
   required): the tagged commit holds this manifest byte for byte and `repo_commit` is
   that commit or an ancestor of it (`freeze_check.check_tag`).
2. **Bank IDs.** `bank-C001`..`bank-C064` (main dyad slots `B-C01`..`B-C64`) and
   `bank-C065`..`bank-C072` (spares `B-S01`..`B-S08`): the #26 dyad-slot sequence, no
   random draw, no allocation information. Each is bound to its unit's package-safe
   `permutation.json` (`builder.bank_spec`).
3. **Seed namespaces.** The #26 default: the bank ID for version `1.0.0` (a rebuild gets
   `<bank_id>-v<version>`). `seed_check.check_seeds` checks them (unique, distinct seeds
   over the whole budget, disjoint from the pilot); a confirmatory plan needs the pilot
   namespaces.
4. **Sizing** (optional): expected and worst-case wall time from the pilot throughput.

The plan, a byte copy of the freeze manifest, the config and the 72 unit permutations
and the seed check are written to the campaign directory (`common.CampaignLayout`).
The plan is immutable; rebuilds of crashed banks are appended to `rebuilds.jsonl`
(`runner.rebuild_bank`) and applied by `effective_banks`.
"""

from __future__ import annotations

import hashlib
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from av_generation.clock import Clock, utc_text
from av_generation.constants import B_MAX_SLOTS
from av_generation.genconfig import (
    FREEZE_CONFIG_KEY,
    ConfigMismatch,
    GenerationConfig,
    check_run_config,
    freeze_item,
)
from av_generation.ids import RunKind
from av_generation.jsonio import (
    CodecError,
    JsonlAppender,
    decode_dataclass,
    iter_jsonl,
    read_json,
    to_json_value,
    write_document,
)
from av_generation.rundir import check_run_id, check_run_location

from av_banks.builder import FIRST_VERSION, BankBuildError, bank_spec, default_seed_namespace
from av_banks.permutation import PermutationError, load_permutation

from .common import (
    CAMPAIGN_ID_RE,
    E_EXISTS,
    E_FREEZE,
    E_INPUT,
    E_SEEDS,
    E_UNITS,
    N_BANKS,
    CampaignError,
    CampaignLayout,
    Role,
    bank_role,
    bank_sequence,
    campaign_bank_ids,
    dyad_slot,
    is_demo_campaign,
    require_schema,
    run_id_for,
)
from .freeze_check import check_tag, load_freeze
from .seed_check import PilotSeeds, SeedCheck, SeedEntry, SourceRef, check_seeds

PLAN_FORMAT: Final = "av-banks/confirmatory-plan"
PLAN_VERSION: Final = 1
PLAN_SCHEMA: Final = "confirmatory-plan.schema.json"


@dataclass(frozen=True, slots=True)
class FreezeRef:
    """The G4 freeze manifest the campaign runs under."""

    manifest_sha256: str
    """SHA-256 of the freeze-manifest file (the bytes copied into the campaign)."""
    status: str
    freeze_version: str
    tag: str | None
    repo_commit: str | None
    config_frozen_sha256: str
    tag_checked: bool
    """`True` when the tag was checked in the git repository (`freeze_check.check_tag`);
    always for a confirmatory plan."""
    tag_commit: str | None
    """The commit the tag points at (the commit that adds the manifest), when checked."""
    guard_checked: bool
    """`True` when #25's freeze guard compared the running code and committed files with
    the manifest (`freeze_check.load_freeze`); always for a confirmatory plan."""


@dataclass(frozen=True, slots=True)
class PlannedBank:
    """One of the 72 banks (one version of it)."""

    sequence: int
    bank_id: str
    role: Role
    dyad_slot: str
    bank_version: str
    seed_namespace: str
    run_id: str
    permutation_sha256: str


@dataclass(frozen=True, slots=True)
class SeedSummary:
    keys: int
    distinct_seeds: int
    seeds_digest: str
    seed_check_sha256: str


@dataclass(frozen=True, slots=True)
class PilotRef:
    namespaces: tuple[str, ...]
    sources: tuple[SourceRef, ...]
    records_checked: int


@dataclass(frozen=True, slots=True)
class Sizing:
    """Run size from the pilot throughput (#27): an estimate, not a budget."""

    pilot_banks: int
    pilot_slots_mean: float
    pilot_slots_max: int
    slots_per_minute: float
    """Pooled pilot rate of one bank (all its profile streams)."""
    slot_ms_p95_max: int | None
    parallel_banks: int
    expected_slots: int
    worst_slots: int
    expected_hours: float
    worst_hours: float


@dataclass(frozen=True, slots=True)
class CampaignPlan:
    """`plan.json` (`banks/schema/confirmatory-plan.schema.json`)."""

    campaign_id: str
    set: Literal["confirmatory", "demo"]
    demo: bool
    kind: Literal["confirmatory", "demo"]
    created_utc: str
    bank_version: str
    generation_config_sha256: str
    freeze: FreezeRef
    seeds: SeedSummary
    pilot: PilotRef
    sizing: Sizing | None
    banks: tuple[PlannedBank, ...]

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = to_json_value(self)
        data["format"] = PLAN_FORMAT
        data["format_version"] = PLAN_VERSION
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CampaignPlan:
        require_schema(PLAN_SCHEMA, data)
        body = {k: v for k, v in data.items() if k not in ("format", "format_version")}
        try:
            return decode_dataclass(cls, body)
        except CodecError as err:
            raise CampaignError(E_INPUT, f"plan: {err}") from err

    def bank(self, bank_id: str) -> PlannedBank:
        for bank in self.banks:
            if bank.bank_id == bank_id:
                return bank
        raise CampaignError(E_INPUT, f"{bank_id} is not a bank of campaign {self.campaign_id}")


def read_plan(root: str | os.PathLike[str]) -> CampaignPlan:
    """The plan of a campaign directory (schema-checked)."""
    path = CampaignLayout.at(root).plan
    if not path.is_file():
        raise CampaignError(E_INPUT, f"{Path(root).name}: no plan.json (run `plan` first)")
    return CampaignPlan.from_dict(read_json(path))


# ---------------------------------------------------------------------------
# Freeze


def check_freeze(
    freeze_manifest: str | os.PathLike[str],
    config: GenerationConfig,
    *,
    kind: RunKind | str,
    repo: str | os.PathLike[str] | None = None,
    manifest_path: str | None = None,
) -> FreezeRef:
    """Refuse a campaign whose config hash differs from the G4 freeze manifest at path
    `freeze_manifest` (module docstring, step 1); raises `CampaignError(E_FREEZE)`. A
    confirmatory campaign needs `repo`, the git repository the freeze tag is checked in
    (`manifest_path`: the manifest's path there, default `generation/FREEZE-v<freeze
    version>.json`)."""
    kind = RunKind(kind)
    if kind is RunKind.CONFIRMATORY and repo is None:
        raise CampaignError(
            E_FREEZE,
            "a confirmatory plan checks the freeze tag in the repository: give repo (--repo)",
        )
    loaded = load_freeze(freeze_manifest, kind=kind)
    doc = loaded.manifest
    try:
        check_run_config(config, kind=kind, freeze_manifest=doc)
    except ConfigMismatch as err:
        raise CampaignError(
            E_FREEZE, f"generation config refused by the freeze check: {err}"
        ) from err
    tag_commit = (
        check_tag(repo, doc, loaded.data, manifest_path=manifest_path) if repo is not None else None
    )
    tag = doc.get("tag")
    commit = doc.get("repo_commit")
    return FreezeRef(
        manifest_sha256=loaded.sha256,
        status=str(doc["status"]),
        freeze_version=str(doc["freeze_version"]),
        tag=tag if isinstance(tag, str) else None,
        repo_commit=commit if isinstance(commit, str) else None,
        config_frozen_sha256=str(freeze_item(doc, FREEZE_CONFIG_KEY)),
        tag_checked=tag_commit is not None,
        tag_commit=tag_commit,
        guard_checked=loaded.guard_checked,
    )


# ---------------------------------------------------------------------------
# Sizing


def size_run(pilot: PilotSeeds, *, parallel_banks: int, n_banks: int = N_BANKS) -> Sizing | None:
    """Expected and worst-case wall time of the campaign from the pilot throughput:
    `expected_slots = n_banks x mean pilot slots per bank`, `worst_slots = n_banks x
    2,304`, each divided by `parallel_banks x` the pooled pilot rate of one bank. Assumes
    the per-bank rate holds with `parallel_banks` banks at once (check the first hour)."""
    timings = [t for t in pilot.timings if t.slots > 0 and t.wall_ms > 0]
    if not timings or parallel_banks < 1:
        return None
    slots = sum(t.slots for t in timings)
    wall_min = sum(t.wall_ms for t in timings) / 60_000
    rate = slots / wall_min
    mean = slots / len(timings)
    expected = math.ceil(mean * n_banks)
    worst = n_banks * B_MAX_SLOTS
    p95 = [t.slot_ms_p95 for t in timings if t.slot_ms_p95 is not None]
    return Sizing(
        pilot_banks=len(timings),
        pilot_slots_mean=round(mean, 1),
        pilot_slots_max=max(t.slots for t in timings),
        slots_per_minute=round(rate, 3),
        slot_ms_p95_max=max(p95) if p95 else None,
        parallel_banks=parallel_banks,
        expected_slots=expected,
        worst_slots=worst,
        expected_hours=round(expected / (rate * parallel_banks) / 60, 2),
        worst_hours=round(worst / (rate * parallel_banks) / 60, 2),
    )


# ---------------------------------------------------------------------------
# The plan


def _planned(
    bank_id: str, units: Path, *, demo: bool, bank_version: str, campaign_id: str
) -> PlannedBank:
    slot = dyad_slot(bank_id)
    path = units / slot / "permutation.json"
    if not path.is_file():
        raise CampaignError(E_UNITS, f"{bank_id}: unit {slot} has no permutation.json under units")
    try:
        permutation = load_permutation(path)
        if demo and (not permutation.demo or permutation.unit_id != slot):
            raise PermutationError(f"{bank_id}: expected the DEMO unit {slot}")
        spec = bank_spec(bank_id, permutation, bank_version=bank_version)
    except (PermutationError, BankBuildError, ValueError) as err:
        raise CampaignError(E_UNITS, str(err)) from err
    return PlannedBank(
        sequence=bank_sequence(bank_id),
        bank_id=bank_id,
        role=bank_role(bank_id),
        dyad_slot=slot,
        bank_version=bank_version,
        seed_namespace=spec.seed_namespace,
        run_id=run_id_for(campaign_id, bank_id, bank_version),
        permutation_sha256=permutation.sha256,
    )


def create_plan(
    root: str | os.PathLike[str],
    *,
    campaign_id: str,
    config: GenerationConfig,
    freeze_manifest: str | os.PathLike[str],
    units: str | os.PathLike[str],
    pilot: PilotSeeds,
    clock: Clock,
    bank_version: str = FIRST_VERSION,
    repo: str | os.PathLike[str] | None = None,
    freeze_repo_path: str | None = None,
    parallel_banks: int = 1,
) -> CampaignPlan:
    """Check the freeze, bind the 72 banks, check the seeds and write the campaign
    directory (module docstring). `campaign_id` starts with `DEMO-` for a rehearsal; a
    confirmatory campaign needs `repo` (and `freeze_repo_path` when the manifest is not
    committed at `generation/FREEZE-v<freeze version>.json`)."""
    demo = is_demo_campaign(campaign_id)
    kind = RunKind.DEMO if demo else RunKind.CONFIRMATORY
    if not CAMPAIGN_ID_RE.fullmatch(campaign_id):
        raise CampaignError(E_INPUT, f"campaign ID {campaign_id!r}: 3-40 letters, digits, hyphens")
    check_run_id(campaign_id, kind)
    layout = CampaignLayout.at(root)
    check_run_location(layout.root, kind)
    if layout.root.exists() and any(layout.root.iterdir()):
        raise CampaignError(E_EXISTS, f"campaign directory {layout.root.name} is not empty")
    freeze_path = Path(freeze_manifest)
    freeze = check_freeze(freeze_path, config, kind=kind, repo=repo, manifest_path=freeze_repo_path)
    freeze_bytes = freeze_path.read_bytes()
    if hashlib.sha256(freeze_bytes).hexdigest() != freeze.manifest_sha256:
        raise CampaignError(E_FREEZE, "the freeze manifest changed while the plan was made")
    units_root = Path(units)
    banks = tuple(
        _planned(b, units_root, demo=demo, bank_version=bank_version, campaign_id=campaign_id)
        for b in campaign_bank_ids(demo=demo)
    )
    if not demo and not pilot.namespaces:
        raise CampaignError(
            E_SEEDS, "a confirmatory plan needs the pilot seed namespaces (--pilot)"
        )
    check = check_seeds(
        [SeedEntry(b.bank_id, b.bank_version, b.seed_namespace) for b in banks],
        expected_set="demo" if demo else "confirmatory",
        pilot=pilot,
    )
    if not check.ok:
        raise CampaignError(E_SEEDS, f"seed check failed: {_seed_problems(check)}")
    layout.root.mkdir(parents=True, exist_ok=True)
    with open(layout.freeze_manifest, "xb") as handle:
        handle.write(freeze_bytes)
    config.write(layout.generation_config, exclusive=True)
    for bank in banks:
        target = layout.unit(bank.dyad_slot)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "xb") as handle:
            handle.write((units_root / bank.dyad_slot / "permutation.json").read_bytes())
    seed_sha = write_document(layout.seed_check, check.to_dict(), exclusive=True)
    plan = CampaignPlan(
        campaign_id=campaign_id,
        set="demo" if demo else "confirmatory",
        demo=demo,
        kind="demo" if demo else "confirmatory",
        created_utc=utc_text(clock.utc_now()),
        bank_version=bank_version,
        generation_config_sha256=config.frozen_sha256(),
        freeze=freeze,
        seeds=SeedSummary(check.keys, check.distinct_seeds, check.seeds_digest, seed_sha),
        pilot=PilotRef(tuple(sorted(pilot.namespaces)), pilot.sources, pilot.records_checked),
        sizing=size_run(pilot, parallel_banks=parallel_banks),
        banks=banks,
    )
    require_schema(PLAN_SCHEMA, plan.to_dict())
    write_document(layout.plan, plan.to_dict(), exclusive=True)
    append_event(layout, {"event": "plan_created", "at_utc": plan.created_utc, "banks": len(banks)})
    return plan


def _seed_problems(check: SeedCheck) -> list[str]:
    return [
        *check.namespace_problems,
        *(f"namespace {n} repeats" for n in check.duplicate_namespaces),
        *check.seed_collisions,
        *(f"pilot namespace {n} reused" for n in check.pilot_namespace_overlap),
        *check.pilot_seed_overlap,
        *check.pilot_problems,
    ][:5]


def append_event(layout: CampaignLayout, event: Mapping[str, Any], *, fsync: bool = True) -> None:
    """Append one canonical line to `events.jsonl` (`jsonio.JsonlAppender`: the runner's
    bank threads share one lock per file, so lines never interleave, also on Windows)."""
    JsonlAppender(layout.events, fsync=fsync).append_obj(dict(event))


# ---------------------------------------------------------------------------
# Rebuilds and the effective plan


@dataclass(frozen=True, slots=True)
class Rebuild:
    """One line of `rebuilds.jsonl`: a crashed bank rebuilt under a new bank version."""

    bank_id: str
    from_version: str
    to_version: str
    seed_namespace: str
    run_id: str
    reason: str
    at_utc: str


def read_rebuilds(root: str | os.PathLike[str]) -> tuple[Rebuild, ...]:
    path = CampaignLayout.at(root).rebuilds
    if not path.is_file():
        return ()
    out = []
    for line in iter_jsonl(path):
        try:
            out.append(decode_dataclass(Rebuild, line))
        except CodecError as err:
            raise CampaignError(E_INPUT, f"rebuilds.jsonl: {err}") from err
    return tuple(out)


def next_version(bank_version: str) -> str:
    """`1.0.0` -> `1.0.1`: a rebuild after a crash keeps the major and minor version."""
    major, minor, patch = (int(p) for p in bank_version.split("."))
    return f"{major}.{minor}.{patch + 1}"


def bank_history(
    plan: CampaignPlan, rebuilds: Sequence[Rebuild] = ()
) -> dict[str, tuple[PlannedBank, ...]]:
    """Every version of every bank, oldest first (the plan's, then each rebuild)."""
    history: dict[str, list[PlannedBank]] = {b.bank_id: [b] for b in plan.banks}
    for rebuild in rebuilds:
        versions = history.get(rebuild.bank_id)
        if versions is None:
            raise CampaignError(E_INPUT, f"rebuilds.jsonl names {rebuild.bank_id}, not in the plan")
        current = versions[-1]
        expected_ns = default_seed_namespace(rebuild.bank_id, rebuild.to_version)
        if (
            rebuild.from_version != current.bank_version
            or rebuild.to_version != next_version(current.bank_version)
            or rebuild.seed_namespace != expected_ns
            or rebuild.run_id != run_id_for(plan.campaign_id, rebuild.bank_id, rebuild.to_version)
        ):
            raise CampaignError(
                E_INPUT, f"rebuilds.jsonl: inconsistent rebuild of {rebuild.bank_id}"
            )
        versions.append(
            PlannedBank(
                sequence=current.sequence,
                bank_id=current.bank_id,
                role=current.role,
                dyad_slot=current.dyad_slot,
                bank_version=rebuild.to_version,
                seed_namespace=rebuild.seed_namespace,
                run_id=rebuild.run_id,
                permutation_sha256=current.permutation_sha256,
            )
        )
    return {k: tuple(v) for k, v in history.items()}


def effective_banks(root: str | os.PathLike[str]) -> tuple[PlannedBank, ...]:
    """The current version of each of the 72 banks, in sequence order."""
    plan = read_plan(root)
    history = bank_history(plan, read_rebuilds(root))
    return tuple(history[b.bank_id][-1] for b in plan.banks)
