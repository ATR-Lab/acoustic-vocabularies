"""Study B pilot banks (#27): plan, build, verify, register, summarize and archive.

The pilot has 8 dyad slots (Study B protocol §2; schedules pilot set `B-P01`..`B-P08`).
Each gets its own bank, `bank-P001`..`bank-P008` (the dyad-slot sequence of the
schedules allocation, #31; `DEMO-bank-P001`.. for rehearsals), built from the slot's
package-safe `permutation.json` under the generation config current at the time (pilot
banks are built before G4: unfrozen config, 0.10 pilot-default threshold).

**Seed namespace.** A first build uses the bank ID (`bank-P001`), a spare
`<bank_id>-v1.<n>.0`. Every pilot namespace starts with `bank-P` and every confirmatory
one with `bank-C` (`builder.seed_namespace_error` makes a namespace name its bank), so
pilot and confirmatory seed keys (`B|<ns>|...`) never meet. A crashed run is not
resumed (#26): a new root with `major=2` builds every bank as `2.0.0` (spares `2.<n>.0`)
under new namespaces.

**Spares.** Up to `spares` (default 2) extra banks. A spare is a new version of the bank
of a dyad slot whose bank ended `unavailable` or failed `banks verify`: same bank ID
and permutation, version `1.<n>.0`, its own seed namespace and its own 4-attempt budget.
It is built right after the main banks, before any pilot session, so the slot's entry in
the pilot allocation list (which names `bank-P00k`) stays valid. Slots are served in
order; when the spares run out the remaining slots are the shortfall, which the summary
states (raise it before O6.3.2). A pair that fails compatibility screening is logged
before any reveal and uses no bank.

**Builder identity.** The plan records the code that builds the banks
(`builder_identity`): the `av-banks` and `av-generation` versions, the SHA-256 of each
package's Python source files (`source_sha256`) and the git commit of the checkout with
a dirty flag (`git_identity`). `plan` prints it so the operator can confirm it first.

**Root** (restricted storage; refused inside a git work tree unless DEMO):

```
<root>/
  pilot-plan.json            the plan: banks, seed namespaces, spare budget, config hash,
                             builder identity (av-banks/pilot-plan v1)
  runs/<run_id>/             main run: the 8 banks (av_banks.run layout)
  runs/<run_id>-S<k>/        spare run k (one bank)
  verify/<bank>-v<ver>.json  banks verify report of every bank
  verify/verify-log.txt      banks verify output of every bank
  pilot-register.csv         the register (av_banks.register)
  throughput.json, throughput.md   throughput and failure metrics (av_banks.metrics)
  archive-manifest.json, archive-sha256.txt   written by `archive` (av_banks.archive)
```

**Check and archive.** `check_pilot` compares the register with the banks on disk and
with the plan (`_register_plan_problems`), checks each bank's amendment log, and checks the
archive once there is one. The archive freezes every file except the banks' amendment
logs (`AMENDMENT_LOGS`): a pilot session may still amend a bank under the reserve rule
(Study B protocol §4; `amend.amend_bank`). The archive pins the lines a log held when it
was archived; the amendment chain (`bank_manifest.amendment_chain_errors`) checks every
line against the archived bank hash.

Command line: `python -m av_banks.pilot plan|run|finish|check|archive|load` (`main`).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import av_generation
from av_generation.bank_manifest import AMENDMENTS_NAME, BankSetError, amendment_chain_errors
from av_generation.clock import Clock, SystemClock, utc_text
from av_generation.genconfig import GenerationConfig, check_run_config
from av_generation.ids import RunKind
from av_generation.jsonio import (
    document_text,
    file_set_sha256,
    file_sha256,
    read_json,
    write_document,
)
from av_generation.records import RunManifest
from av_generation.rundir import MANIFEST_NAME as RUN_MANIFEST_NAME
from av_generation.rundir import (
    RunLayout,
    RunPolicyError,
    check_run_id,
    check_run_location,
)

import av_banks
from av_banks.archive import (
    ARCHIVE_HASH_NAME,
    ARCHIVE_MANIFEST_NAME,
    ArchiveResult,
    archive_problems,
    archive_tree,
)
from av_banks.builder import BankSpec, LedgerFactory, bank_spec, slot_ledger
from av_banks.cli import make_proposer
from av_banks.layout import BankLayout
from av_banks.manifest import read_amendments, read_manifest
from av_banks.metrics import (
    CONFIRMATORY_BANKS,
    ThroughputSummary,
    summarize_banks,
    summary_markdown,
)
from av_banks.permutation import load_permutation
from av_banks.proposer import SlotProposer
from av_banks.register import (
    RegisterError,
    RegisterRow,
    SetName,
    open_bank,
    read_register,
    register_problems,
    register_row,
    relative_path,
    write_register,
)
from av_banks.run import RunResult, build_banks
from av_banks.verify import VerifyReport, verify_bank

PILOT_DYAD_SLOTS: Final = 8
"""Study B pilot: 8 dyads / 16 people (Study B protocol §2)."""
PILOT_SPARES: Final = 2
"""Spare banks of the pilot (#27 decision)."""
PLAN_FORMAT: Final = "av-banks/pilot-plan"
SUMMARY_FORMAT: Final = "av-banks/pilot-summary"
PLAN_NAME: Final = "pilot-plan.json"
REGISTER_NAME: Final = "pilot-register.csv"
RUNS_DIR: Final = "runs"
VERIFY_DIR: Final = "verify"
VERIFY_LOG_NAME: Final = "verify-log.txt"
SUMMARY_JSON_NAME: Final = "throughput.json"
SUMMARY_MD_NAME: Final = "throughput.md"
DEMO_BANK_PREFIX: Final = "DEMO-bank-P"
AMENDMENT_LOGS: Final = (f"{RUNS_DIR}/*/banks/*/{AMENDMENTS_NAME}",)
"""Append-only files of an archived pilot root: the banks' reserve-rule amendment logs."""
GIT_COMMIT_RE: Final = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")

E_PLAN: Final = "E_PLAN"
E_EXISTS: Final = "E_EXISTS"
E_ARCHIVED: Final = "E_ARCHIVED"
E_ROOT: Final = "E_ROOT"


class PilotError(RuntimeError):
    """The pilot tooling refused; `.code` names the rule."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


# ---------------------------------------------------------------------------
# IDs and the plan


def pilot_bank_id(slot: int, *, demo: bool = False) -> str:
    """Bank ID of pilot dyad slot `slot` (1-based): `bank-P001`, or `DEMO-bank-P001`."""
    if isinstance(slot, bool) or not isinstance(slot, int) or not 1 <= slot <= 99:
        raise PilotError(E_PLAN, f"pilot dyad slot must be 1..99, got {slot!r}")
    return f"{DEMO_BANK_PREFIX if demo else 'bank-P'}{slot:03d}"


def pilot_unit_id(slot: int) -> str:
    """Unit (dyad-slot) ID of pilot slot `slot`: `B-P01` (schedules pilot set)."""
    pilot_bank_id(slot)
    return f"B-P{slot:02d}"


def _check_major(major: int) -> int:
    if isinstance(major, bool) or not isinstance(major, int) or not 1 <= major <= 99:
        raise PilotError(E_PLAN, f"major version must be 1..99, got {major!r}")
    return major


def main_version(major: int = 1) -> str:
    """Bank version of the main banks of a pilot run: `1.0.0`; `2.0.0` for a run that
    replaces a crashed one (a crashed build is not resumed, #26)."""
    return f"{_check_major(major)}.0.0"


def spare_version(n: int, major: int = 1) -> str:
    """Bank version of the `n`-th spare of a dyad slot: `1.1.0`, `1.2.0`, ..."""
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 9:
        raise PilotError(E_PLAN, f"spare number must be 1..9, got {n!r}")
    return f"{_check_major(major)}.{n}.0"


# ---------------------------------------------------------------------------
# Builder identity


def source_sha256(package_dir: str | os.PathLike[str]) -> str:
    """`jsonio.file_set_sha256` of every `.py` file under a package directory (paths
    relative to it, `__pycache__` skipped). `.gitattributes` checks Python files out with
    `\\n` line ends, so a checkout of one commit gives the same hash on every OS."""
    base = Path(package_dir)
    files = {
        path.relative_to(base).as_posix(): file_sha256(path)
        for path in base.rglob("*.py")
        if path.is_file() and "__pycache__" not in path.relative_to(base).parts
    }
    if not files:
        raise PilotError(E_PLAN, f"{base}: no Python source files")
    return file_set_sha256(files)


def git_identity(path: str | os.PathLike[str]) -> dict[str, Any] | None:
    """`{"commit", "dirty"}` of the git checkout that holds `path` (`dirty`: a tracked
    file differs from the commit), or `None` outside a checkout or without git."""

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", os.fspath(path), *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout

    try:
        commit = git("rev-parse", "HEAD").strip()
        status = git("status", "--porcelain", "--untracked-files=no")
    except (OSError, subprocess.SubprocessError):
        return None
    if not GIT_COMMIT_RE.fullmatch(commit):
        return None
    return {"commit": commit, "dirty": bool(status.strip())}


def builder_identity() -> dict[str, Any]:
    """The code that builds the banks: package versions, source hashes and git commit
    (`pilot-plan.json` `builder`). The versions alone do not change with the code."""
    banks_dir = Path(av_banks.__file__).resolve().parent
    generation_dir = Path(av_generation.__file__).resolve().parent
    return {
        "av_banks": av_banks.__version__,
        "av_generation": av_generation.__version__,
        "source_sha256": {
            "av_banks": source_sha256(banks_dir),
            "av_generation": source_sha256(generation_dir),
        },
        "git": git_identity(banks_dir),
    }


def builder_text(builder: Mapping[str, Any]) -> str:
    """One line naming the builder of a plan (the throughput note)."""
    sources = builder.get("source_sha256") or {}
    git = builder.get("git")
    if isinstance(git, Mapping):
        dirty = " (dirty: tracked files differ from the commit)" if git.get("dirty") else ""
        commit = f"`{git.get('commit')}`{dirty}"
    else:
        commit = "not recorded"
    return (
        f"av-banks {builder.get('av_banks')} (source SHA-256 "
        f"`{sources.get('av_banks', 'not recorded')}`), av-generation "
        f"{builder.get('av_generation')} (source SHA-256 "
        f"`{sources.get('av_generation', 'not recorded')}`); git commit {commit}"
    )


@dataclass(frozen=True, slots=True)
class PilotPlan:
    """The main banks of the pilot, in dyad-slot order, and the spare budget."""

    run_id: str
    demo: bool
    banks: tuple[BankSpec, ...]
    spares: int
    major: int = 1

    @property
    def set_name(self) -> SetName:
        return "demo" if self.demo else "pilot"

    @property
    def kind(self) -> RunKind:
        return RunKind.DEMO if self.demo else RunKind.PILOT

    def spare_spec(self, main: BankSpec, n: int) -> BankSpec:
        """The `n`-th spare of a main bank: same bank ID and permutation, version
        `<major>.<n>.0`, seed namespace `<bank_id>-v<major>.<n>.0`."""
        return bank_spec(main.bank_id, main.permutation, bank_version=spare_version(n, self.major))

    def spare_run_id(self, k: int) -> str:
        """Run ID of the `k`-th spare run: `<run_id>-S<k>`."""
        return f"{self.run_id}-S{k}"

    def check_run_ids(self) -> None:
        """Refuse a run ID that is invalid for the run kind (`RunPolicyError`), or too long
        to name every spare run (`PilotError` `E_PLAN`): checked before any bank is
        built, not when the first spare is needed."""
        check_run_id(self.run_id, self.kind)
        for k in range(1, self.spares + 1):
            try:
                check_run_id(self.spare_run_id(k), self.kind)
            except RunPolicyError as err:
                raise PilotError(
                    E_PLAN, f"spare run {k} of run {self.run_id!r}: {err}; use a shorter run ID"
                ) from None

    def document(
        self,
        config: GenerationConfig,
        created_utc: str,
        builder: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """`pilot-plan.json` (`av-banks/pilot-plan` v1); `builder` defaults to
        `builder_identity()`."""
        return {
            "format": PLAN_FORMAT,
            "format_version": 1,
            "run_id": self.run_id,
            "demo": self.demo,
            "set": self.set_name,
            "dyad_slots": len(self.banks),
            "spares": self.spares,
            "major": self.major,
            "created_utc": created_utc,
            "builder": dict(builder) if builder is not None else builder_identity(),
            "generation_config": {
                "name": config.name,
                "sha256": config.frozen_sha256(),
                "demo": config.demo,
                "separation_threshold": config.separation_threshold,
                "model_id": config.model.model_id,
                "model_revision": config.model.revision,
                "prompts_b_sha256": config.prompts.b_sha256,
                "renderer_version": config.code.renderer_version,
                "validator_version": config.code.validator_version,
            },
            "banks": [
                {
                    "bank_id": spec.bank_id,
                    "bank_version": spec.bank_version,
                    "dyad_slot": spec.permutation.unit_id,
                    "seed_namespace": spec.seed_namespace,
                    "permutation_sha256": spec.permutation.sha256,
                }
                for spec in self.banks
            ],
        }


def pilot_plan(
    units: str | os.PathLike[str],
    *,
    run_id: str,
    demo: bool = False,
    dyads: int = PILOT_DYAD_SLOTS,
    spares: int = PILOT_SPARES,
    major: int = 1,
) -> PilotPlan:
    """Bind pilot slots 1..`dyads` to their banks: `<units>/B-P<nn>/permutation.json`
    must be the pilot unit `B-P<nn>` (DEMO units for a DEMO plan, real units otherwise;
    `bank_spec` applies #26's bank-to-unit rule to real banks). Main banks get version
    `<major>.0.0` (`major` 2 or more only to replace a crashed run: new seeds). The run
    ID must also leave room for the spare run IDs (`PilotPlan.check_run_ids`)."""
    kind = RunKind.DEMO if demo else RunKind.PILOT
    check_run_id(run_id, kind)
    if not 1 <= dyads <= 99:
        raise PilotError(E_PLAN, f"dyads must be 1..99, got {dyads}")
    if not 0 <= spares <= 9:
        raise PilotError(E_PLAN, f"spares must be 0..9, got {spares}")
    PilotPlan(run_id, demo, (), spares, major).check_run_ids()
    specs = []
    for slot in range(1, dyads + 1):
        unit = pilot_unit_id(slot)
        permutation = load_permutation(Path(units) / unit)
        if permutation.unit_id != unit or permutation.set != "pilot":
            raise PilotError(
                E_PLAN,
                f"{unit}: the permutation is unit {permutation.unit_id} of the "
                f"{permutation.set} set, not pilot unit {unit}",
            )
        if permutation.demo != demo:
            raise PilotError(
                E_PLAN,
                f"{unit}: a {'DEMO' if demo else 'real'} plan needs "
                f"{'DEMO' if demo else 'real'} units",
            )
        specs.append(
            bank_spec(pilot_bank_id(slot, demo=demo), permutation, bank_version=main_version(major))
        )
    return PilotPlan(run_id, demo, tuple(specs), spares, major)


# ---------------------------------------------------------------------------
# Results


@dataclass(frozen=True, slots=True)
class PilotFinish:
    """Register, verification and summary of a pilot root (`finish_pilot`)."""

    rows: tuple[RegisterRow, ...]
    register_sha256: str
    reports: tuple[VerifyReport, ...]
    summary: ThroughputSummary
    shortfall: tuple[str, ...]
    """Dyad slots without a usable bank (raise before O6.3.2)."""
    problems: tuple[str, ...]
    """Plan mismatches: a planned bank missing, too many spares, a bank of another set."""

    @property
    def verified(self) -> bool:
        return all(report.ok for report in self.reports)

    @property
    def exit_code(self) -> int:
        """0 complete, 3 shortfall, 1 a verify or plan problem."""
        if self.problems or not self.verified:
            return 1
        return 3 if self.shortfall else 0


@dataclass(frozen=True, slots=True)
class PilotResult:
    """What `run_pilot` built and wrote."""

    root: Path
    runs: tuple[RunResult, ...]
    finish: PilotFinish


# ---------------------------------------------------------------------------
# Run


def _usable(bank_dir: Path, status: str, reports: dict[str, VerifyReport], root: Path) -> bool:
    report = verify_bank(bank_dir)
    reports[relative_path(bank_dir, root)] = report
    return status == "complete" and report.ok


def run_pilot(
    plan: PilotPlan,
    *,
    root: str | os.PathLike[str],
    config: GenerationConfig,
    proposer: SlotProposer | Callable[[RunLayout], SlotProposer],
    clock: Clock,
    workers: int = 3,
    parallel_banks: int = 1,
    ledger_factory: LedgerFactory = slot_ledger,
    llm_runtime: str | None = None,
    project_banks: int = CONFIRMATORY_BANKS,
    fsync: bool = True,
) -> PilotResult:
    """Build the plan's banks and the spares they need, then `finish_pilot` (module
    docstring). The root must be new or empty; real pilots must be outside git."""
    base = Path(root)
    plan.check_run_ids()
    check_run_location(base, plan.kind)
    if base.exists() and any(base.iterdir()):
        raise PilotError(E_EXISTS, f"pilot root {base} already exists")
    check_run_config(config, kind=plan.kind)  # before anything is written
    if isinstance(proposer, SlotProposer):
        proposer.check_config(config)
    base.mkdir(parents=True, exist_ok=True)
    write_document(
        base / PLAN_NAME, plan.document(config, utc_text(clock.utc_now())), exclusive=True
    )
    common: dict[str, Any] = {
        "runs_root": base / RUNS_DIR,
        "config": config,
        "proposer": proposer,
        "clock": clock,
        "kind": plan.kind,
        "llm_runtime": llm_runtime,
        "workers": workers,
        "ledger_factory": ledger_factory,
        "fsync": fsync,
    }
    main = build_banks(plan.banks, run_id=plan.run_id, parallel_banks=parallel_banks, **common)
    runs = [main]
    reports: dict[str, VerifyReport] = {}
    remaining = plan.spares
    for spec, built in zip(plan.banks, main.banks, strict=True):
        usable = _usable(built.bank_dir, built.status, reports, base)
        number = 0
        while not usable and remaining > 0:
            number += 1
            remaining -= 1
            spare = build_banks(
                [plan.spare_spec(spec, number)], run_id=plan.spare_run_id(len(runs)), **common
            )
            runs.append(spare)
            usable = _usable(spare.banks[0].bank_dir, spare.banks[0].status, reports, base)
    finish = finish_pilot(base, project_banks=project_banks, reports=reports)
    return PilotResult(base, tuple(runs), finish)


# ---------------------------------------------------------------------------
# Finish: verify, register, summary


def read_plan(root: str | os.PathLike[str]) -> dict[str, Any]:
    """The `pilot-plan.json` of a pilot root."""
    try:
        doc = read_json(Path(root) / PLAN_NAME)
    except OSError as err:
        raise PilotError(E_ROOT, f"{root}: no {PLAN_NAME} ({err})") from None
    if not isinstance(doc, dict) or doc.get("format") != PLAN_FORMAT:
        raise PilotError(E_ROOT, f"{PLAN_NAME}: not an {PLAN_FORMAT} document")
    return doc


def bank_dirs(root: str | os.PathLike[str]) -> tuple[Path, ...]:
    """Every bank directory under `<root>/runs/*/banks/`, in sorted path order."""
    runs = Path(root) / RUNS_DIR
    if not runs.is_dir():
        return ()
    found = []
    for run in sorted(p for p in runs.iterdir() if p.is_dir()):
        banks = run / "banks"
        if banks.is_dir():
            found.extend(sorted(p for p in banks.iterdir() if p.is_dir()))
    return tuple(found)


def run_dirs(root: str | os.PathLike[str]) -> tuple[Path, ...]:
    runs = Path(root) / RUNS_DIR
    return tuple(sorted(p for p in runs.iterdir() if p.is_dir())) if runs.is_dir() else ()


def _version_key(version: str) -> tuple[int, ...]:
    """`1.10.0` after `1.2.0`; a part that is not a number sorts first (a register value
    is not trusted before it is checked)."""
    return tuple(int(part) if part.isdigit() else -1 for part in version.split("."))


def _planned(plan: Mapping[str, Any]) -> dict[tuple[str, str], str]:
    """(bank ID, version) -> dyad slot of every main bank of a plan document."""
    return {(b["bank_id"], b["bank_version"]): b["dyad_slot"] for b in plan["banks"]}


@dataclass(frozen=True, slots=True)
class _Built:
    """What the plan checks need of one built bank (from its manifest or register row)."""

    bank_id: str
    bank_version: str
    dyad_slot: str
    config_sha256: str


def _plan_problems(
    plan: Mapping[str, Any], built: Sequence[_Built], *, missing: str | None
) -> list[str]:
    """The banks against the plan: each planned bank present (`missing` names the
    problem; `None` skips the rule), at most `spares` other banks and each one a version
    of a planned bank, every bank on its planned dyad slot and built under the plan's
    generation config."""
    planned = _planned(plan)
    slot_of = {bank: slot for (bank, _), slot in planned.items()}
    config = plan["generation_config"]["sha256"]
    present = {(b.bank_id, b.bank_version) for b in built}
    problems: list[str] = []
    if missing is not None:
        problems.extend(
            f"{bank} v{version}: planned but {missing}"
            for (bank, version) in sorted(planned)
            if (bank, version) not in present
        )
    spares = [b for b in built if (b.bank_id, b.bank_version) not in planned]
    if len(spares) > int(plan["spares"]):
        problems.append(f"{len(spares)} spare banks built, the plan allows {plan['spares']}")
    problems.extend(
        f"{b.bank_id} v{b.bank_version}: not a bank of the plan"
        for b in spares
        if b.bank_id not in slot_of
    )
    for b in built:
        where = f"{b.bank_id} v{b.bank_version}"
        if b.bank_id in slot_of and b.dyad_slot != slot_of[b.bank_id]:
            problems.append(
                f"{where}: dyad slot {b.dyad_slot}, the plan binds {b.bank_id} to "
                f"{slot_of[b.bank_id]}"
            )
        if b.config_sha256 != config:
            problems.append(
                f"{where}: generation config {b.config_sha256}, the plan records {config}"
            )
    return problems


def _used_keys(banks: Iterable[tuple[str, str, str, bool]]) -> set[tuple[str, str]]:
    """(bank ID, version) of the bank each dyad slot uses, from `(bank ID, version, dyad
    slot, complete and verified)`: per slot, the first such bank by version (`finish`
    writes `use` with this rule and `check` recomputes it)."""
    best: dict[str, tuple[tuple[tuple[int, ...], str], tuple[str, str]]] = {}
    for bank_id, version, slot, usable in banks:
        order = (_version_key(version), bank_id)
        if usable and (slot not in best or order < best[slot][0]):
            best[slot] = (order, (bank_id, version))
    return {key for _, key in best.values()}


def verify_lines(report: VerifyReport, bank_path: str, version: str) -> list[str]:
    """`banks verify` output of one bank, headed by its path (the verify log)."""
    lines = [
        f"== {bank_path} ({report.bank_id} v{version})",
        f"bank {report.bank_id}: {report.status}; bank hash {report.bank_sha256}",
        f"attempts {report.attempts_checked}, slots {report.slots_checked}, options "
        f"{report.options_checked}, pairs {dict(report.pairs_checked)}, failed pairs "
        f"{dict(report.pairs_failed)}, amendments {report.amendments_checked}",
    ]
    lines.extend(f"PROBLEM: {problem}" for problem in report.problems)
    lines.append("OK" if report.ok else f"FAILED ({len(report.problems)} problems)")
    return lines


def _ensure_open(root: Path) -> None:
    if any((root / name).exists() for name in (ARCHIVE_MANIFEST_NAME, ARCHIVE_HASH_NAME)):
        raise PilotError(E_ARCHIVED, f"{root} is archived; its files are read-only")


def finish_pilot(
    root: str | os.PathLike[str],
    *,
    project_banks: int = CONFIRMATORY_BANKS,
    reports: Mapping[str, VerifyReport] | None = None,
) -> PilotFinish:
    """Verify every bank under the root, write the verify log and reports, the register
    and the throughput summary (re-runnable until the root is archived). `reports` are
    verify reports already made, by bank path relative to the root."""
    base = Path(root)
    _ensure_open(base)
    plan = read_plan(base)
    expected_set: SetName = "demo" if plan.get("demo") else "pilot"
    known = dict(reports or {})
    found = []
    problems: list[str] = []
    for bank_dir in bank_dirs(base):
        path = relative_path(bank_dir, base)
        try:
            manifest = read_manifest(BankLayout(bank_dir).manifest)
        except (OSError, ValueError) as err:
            problems.append(f"{path}: no bank manifest (unfinished or crashed build): {err}")
            continue
        report = known.get(path) or verify_bank(bank_dir)
        found.append((manifest, bank_dir, path, report))
    found.sort(key=lambda item: (str(item[0].dyad_slot), _version_key(item[0].bank_version)))
    planned = _planned(plan)
    problems.extend(
        _plan_problems(
            plan,
            [
                _Built(m.bank_id, m.bank_version, str(m.dyad_slot), m.generation_config_sha256)
                for m, *_ in found
            ],
            missing="not built",
        )
    )
    problems.extend(
        f"{m.bank_id}: a {m.set} bank in a {expected_set} pilot"
        for m, *_ in found
        if m.set != expected_set
    )
    used = _used_keys(
        (m.bank_id, m.bank_version, str(m.dyad_slot), m.status == "complete" and report.ok)
        for m, _, _, report in found
    )
    used_slots = {str(m.dyad_slot) for m, *_ in found if (m.bank_id, m.bank_version) in used}
    rows = []
    log: list[str] = []
    verify_dir = base / VERIFY_DIR
    if verify_dir.exists():
        shutil.rmtree(verify_dir)
    verify_dir.mkdir()
    for manifest, bank_dir, path, report in found:
        key = (manifest.bank_id, manifest.bank_version)
        rows.append(
            register_row(
                bank_dir,
                root=base,
                role="main" if key in planned else "spare",
                use=key in used,
                verify_ok=report.ok,
            )
        )
        write_document(
            verify_dir / f"{manifest.bank_id}-v{manifest.bank_version}.json", report.to_dict()
        )
        log.extend(verify_lines(report, path, manifest.bank_version))
        log.append("")
    n_ok = sum(1 for *_, report in found if report.ok)
    log.append(f"verified {len(found)} banks: {n_ok} OK, {len(found) - n_ok} FAILED")
    with open(verify_dir / VERIFY_LOG_NAME, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(log) + "\n")
    register_sha256 = write_register(rows, base / REGISTER_NAME)
    shortfall = tuple(sorted(set(planned.values()) - used_slots))
    summary = summarize_banks(
        [bank_dir for _, bank_dir, _, _ in found],
        run_dirs=run_dirs(base),
        project_banks=project_banks,
    )
    write_summary(base, plan, summary, rows, register_sha256, shortfall)
    return PilotFinish(
        rows=tuple(rows),
        register_sha256=register_sha256,
        reports=tuple(report for *_, report in found),
        summary=summary,
        shortfall=shortfall,
        problems=tuple(problems),
    )


def write_summary(
    root: Path,
    plan: Mapping[str, Any],
    summary: ThroughputSummary,
    rows: Sequence[RegisterRow],
    register_sha256: str,
    shortfall: Sequence[str],
) -> None:
    """`throughput.json` (`av-banks/pilot-summary` v1) and the note `throughput.md`."""
    config = plan["generation_config"]
    spares_used = sum(1 for row in rows if row.role == "spare")
    doc = {
        "format": SUMMARY_FORMAT,
        "format_version": 1,
        "run_id": plan["run_id"],
        "demo": plan["demo"],
        "dyad_slots": plan["dyad_slots"],
        "usable_slots": sum(1 for row in rows if row.use),
        "shortfall": list(shortfall),
        "spares": plan["spares"],
        "spares_used": spares_used,
        "generation_config_sha256": config["sha256"],
        "separation_threshold": config["separation_threshold"],
        "register_sha256": register_sha256,
        "builder": plan.get("builder"),
        "throughput": summary.to_dict(),
    }
    write_document(root / SUMMARY_JSON_NAME, doc)
    demo = bool(plan["demo"])
    unavailable = [f"{r.bank_id} v{r.bank_version}" for r in rows if r.status == "unavailable"]
    preamble = [
        *(
            ["**DEMO rehearsal: fake model, synthetic latencies; not pilot data.**", ""]
            if demo
            else []
        ),
        f"- Plan `{plan['run_id']}`: {plan['dyad_slots']} dyad slots, spare budget "
        f"{plan['spares']} ({spares_used} used).",
        f"- Builder: {builder_text(plan.get('builder') or {})}.",
        f"- Generation config `{config['name']}`, hash `{config['sha256']}`, separation "
        f"threshold {config['separation_threshold']}.",
        f"- Register `{REGISTER_NAME}` SHA-256 `{register_sha256}`.",
        f"- Unavailable banks: {', '.join(unavailable) if unavailable else 'none'}.",
        (
            f"- **Shortfall: {len(shortfall)} dyad slot(s) without a usable bank "
            f"({', '.join(shortfall)}); raise it before O6.3.2.**"
            if shortfall
            else f"- Every dyad slot has a usable bank ({doc['usable_slots']} of "
            f"{plan['dyad_slots']})."
        ),
    ]
    title = "Pilot bank throughput" + (" (DEMO)" if demo else "")
    text = summary_markdown(summary, title=title, preamble=preamble)
    with open(root / SUMMARY_MD_NAME, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


# ---------------------------------------------------------------------------
# Check and archive


def _run_crashed(root: Path, run_id: str) -> bool:
    """True when run `run_id` exists but its run manifest is missing or was never closed
    (`build_banks` closes it after its last bank): the build crashed."""
    run = root / RUNS_DIR / run_id
    if not run.is_dir():
        return False
    try:
        return RunManifest.read(run / RUN_MANIFEST_NAME).closed_utc is None
    except (OSError, ValueError):
        return True


def _register_plan_problems(
    root: Path, plan: Mapping[str, Any], rows: Sequence[RegisterRow]
) -> list[str]:
    """The register against the banks on disk, the plan and the summary (`check_pilot`).

    - one row for every bank directory that has a manifest (a register cannot hide a
      bank that was built);
    - one row for every planned bank, unless the main run crashed (a crashed root is
      archived as the record of the crash; `finish` reports the banks it never built);
    - the plan rules of `finish` (spares, dyad slots, the plan's config hash), and
      `role` = `main` exactly for a planned bank;
    - `use` recomputed with `finish`'s rule from `status` and `verify`, and `verify`
      equal to the stored verify report (`--verify` re-runs it);
    - `throughput.json` names this register's hash and the shortfall the rows give.
    """
    problems: list[str] = []
    listed = {row.bank_path for row in rows}
    for bank_dir in bank_dirs(root):
        path = relative_path(bank_dir, root)
        if BankLayout(bank_dir).manifest.is_file() and path not in listed:
            problems.append(f"{path}: a built bank with no register row")
    crashed = _run_crashed(root, str(plan["run_id"]))
    problems.extend(
        _plan_problems(
            plan,
            [
                _Built(r.bank_id, r.bank_version, r.dyad_slot, r.generation_config_sha256)
                for r in rows
            ],
            missing=None if crashed else "not in the register",
        )
    )
    planned = _planned(plan)
    used = _used_keys(
        (r.bank_id, r.bank_version, r.dyad_slot, r.status == "complete" and r.verify == "pass")
        for r in rows
    )
    for row in rows:
        where = f"{row.bank_id} v{row.bank_version}"
        key = (row.bank_id, row.bank_version)
        role = "main" if key in planned else "spare"
        if row.role != role:
            problems.append(f"{where}: role {row.role}, the plan makes it a {role} bank")
        if row.use != (key in used):
            problems.append(
                f"{where}: use {int(row.use)}, expected {int(key in used)} (a dyad slot uses "
                "its first complete bank that verifies)"
            )
        name = f"{VERIFY_DIR}/{row.bank_id}-v{row.bank_version}.json"
        try:
            report = read_json(root / VERIFY_DIR / f"{row.bank_id}-v{row.bank_version}.json")
        except (OSError, ValueError):
            problems.append(f"{where}: no verify report {name}")
            continue
        ok = report.get("ok") if isinstance(report, dict) else None
        if ok is not (row.verify == "pass"):
            problems.append(f"{where}: verify {row.verify}, but {name} has ok {ok}")
    shortfall = sorted(
        set(planned.values()) - {r.dyad_slot for r in rows if (r.bank_id, r.bank_version) in used}
    )
    try:
        summary = read_json(root / SUMMARY_JSON_NAME)
        if not isinstance(summary, dict):
            raise ValueError("not a JSON object")
    except (OSError, ValueError) as err:
        return [*problems, f"{SUMMARY_JSON_NAME}: {err}"]
    if summary.get("register_sha256") != file_sha256(root / REGISTER_NAME):
        problems.append(f"{SUMMARY_JSON_NAME}: register_sha256 is not the hash of {REGISTER_NAME}")
    if summary.get("shortfall") != shortfall:
        problems.append(
            f"{SUMMARY_JSON_NAME}: shortfall {summary.get('shortfall')}, the register gives "
            f"{shortfall}"
        )
    return problems


def amendment_problems(root: str | os.PathLike[str]) -> tuple[str, ...]:
    """Every bank's `amendments.jsonl` against its manifest: a sound chain from the bank
    hash, one reserve per cell, the replaced and replacing options of the cell
    (`bank_manifest.amendment_chain_errors`). Empty when no bank is amended."""
    base = Path(root)
    problems: list[str] = []
    for bank_dir in bank_dirs(base):
        layout = BankLayout(bank_dir)
        if not layout.amendments.exists():
            continue
        where = f"{relative_path(bank_dir, base)}/{AMENDMENTS_NAME}"
        try:
            manifest = read_manifest(layout.manifest)
            amendments = read_amendments(layout.amendments)
        except (OSError, ValueError) as err:
            problems.append(f"{where}: {err}")
            continue
        problems.extend(
            f"{where}: {e}" for e in amendment_chain_errors(manifest.to_dict(), amendments)
        )
    return tuple(problems)


def check_pilot(root: str | os.PathLike[str], *, rerun_verify: bool = False) -> tuple[str, ...]:
    """Every problem of a pilot root (empty when sound): the register against the stored
    banks (hashes recomputed from the files; `register.register_problems`), against the
    bank directories, the plan and the summary (`_register_plan_problems`), the amendment
    logs (`amendment_problems`), and the archive when the root is archived."""
    base = Path(root)
    plan = read_plan(base)
    expected: SetName = "demo" if plan.get("demo") else "pilot"
    register = base / REGISTER_NAME
    problems = list(
        register_problems(register, base, expected_set=expected, rerun_verify=rerun_verify)
    )
    try:
        rows = read_register(register)
    except (OSError, RegisterError):
        pass  # register_problems reported it
    else:
        problems.extend(_register_plan_problems(base, plan, rows))
    problems.extend(amendment_problems(base))
    if (base / ARCHIVE_MANIFEST_NAME).exists() or (base / ARCHIVE_HASH_NAME).exists():
        problems.extend(archive_problems(base, append_only=AMENDMENT_LOGS))
    return tuple(problems)


def archive_pilot(root: str | os.PathLike[str], *, clock: Clock) -> ArchiveResult:
    """Archive a finished pilot root (refused while `check_pilot` reports a problem).
    Every file becomes read-only except the banks' amendment logs (`AMENDMENT_LOGS`),
    which the reserve rule may still extend during the pilot sessions."""
    base = Path(root)
    _ensure_open(base)
    plan = read_plan(base)
    problems = check_pilot(base)
    if problems:
        raise PilotError(E_ROOT, f"the register does not match the banks: {problems[:3]}")
    label = f"{'DEMO ' if plan.get('demo') else ''}pilot banks {plan['run_id']}"
    return archive_tree(
        base, label=label, created_utc=utc_text(clock.utc_now()), append_only=AMENDMENT_LOGS
    )


# ---------------------------------------------------------------------------
# Command line


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m av_banks.pilot", description="Study B pilot banks (#27)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def plan_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--units", required=True, type=Path, help="holds B-P01/permutation.json..")
        p.add_argument("--run-id", required=True)
        p.add_argument("--demo", action="store_true", help="DEMO bank IDs and DEMO units")
        p.add_argument("--dyads", type=int, default=PILOT_DYAD_SLOTS)
        p.add_argument("--spares", type=int, default=PILOT_SPARES)
        p.add_argument("--major", type=int, default=1, help="2.. only to replace a crashed run")

    plan = sub.add_parser("plan", help="print the bank IDs, units and seed namespaces")
    plan_args(plan)

    run = sub.add_parser("run", help="build, verify, register and summarize the pilot banks")
    plan_args(run)
    run.add_argument("--root", required=True, type=Path)
    run.add_argument("--generation-config", required=True, type=Path)
    run.add_argument("--meanings", required=True, type=Path)
    run.add_argument("--prompts", required=True, type=Path)
    run.add_argument("--decoding-schema", required=True, type=Path)
    run.add_argument("--llm-url", required=True)
    run.add_argument("--llm-runtime", default="vllm")
    run.add_argument("--workers", type=int, default=3)
    run.add_argument("--parallel-banks", type=int, default=1)
    run.add_argument("--project-banks", type=int, default=CONFIRMATORY_BANKS)

    finish = sub.add_parser("finish", help="re-verify, rewrite the register and the summary")
    finish.add_argument("--root", required=True, type=Path)
    finish.add_argument("--project-banks", type=int, default=CONFIRMATORY_BANKS)

    check = sub.add_parser("check", help="register hashes against the banks, and the archive")
    check.add_argument("--root", required=True, type=Path)
    check.add_argument("--verify", action="store_true", help="also re-run banks verify")

    archive = sub.add_parser("archive", help="hash every file and make it read-only")
    archive.add_argument("--root", required=True, type=Path)

    load = sub.add_parser("load", help="load a bank for a set (refuses other sets)")
    load.add_argument("bank_dir", type=Path)
    load.add_argument("--mode", required=True, choices=("pilot", "confirmatory", "demo"))
    return parser


def _plan(args: argparse.Namespace) -> PilotPlan:
    return pilot_plan(
        args.units,
        run_id=args.run_id,
        demo=args.demo,
        dyads=args.dyads,
        spares=args.spares,
        major=args.major,
    )


def _print_plan(args: argparse.Namespace) -> int:
    plan = _plan(args)
    doc = {
        "run_id": plan.run_id,
        "set": plan.set_name,
        "spares": plan.spares,
        "spare_run_ids": [plan.spare_run_id(k) for k in range(1, plan.spares + 1)],
        "builder": builder_identity(),
        "banks": [
            {
                "bank_id": s.bank_id,
                "bank_version": s.bank_version,
                "dyad_slot": s.permutation.unit_id,
                "seed_namespace": s.seed_namespace,
                "permutation_sha256": s.permutation.sha256,
            }
            for s in plan.banks
        ],
    }
    print(document_text(doc), end="")
    return 0


def _finish_report(finish: PilotFinish, root: Path) -> None:
    doc = {
        "root": str(root),
        "register_sha256": finish.register_sha256,
        "usable_slots": sum(1 for row in finish.rows if row.use),
        "shortfall": list(finish.shortfall),
        "verified": finish.verified,
        "problems": list(finish.problems),
        "banks": [
            {
                "bank_id": r.bank_id,
                "bank_version": r.bank_version,
                "role": r.role,
                "status": r.status,
                "use": r.use,
                "verify": r.verify,
                "bank_sha256": r.bank_sha256,
            }
            for r in finish.rows
        ],
        "slots_per_hour": finish.summary.slots_per_hour,
        "attempts_per_bank": finish.summary.attempts_per_bank.mean,
    }
    print(json.dumps(doc, indent=2, sort_keys=True))


def _run(args: argparse.Namespace) -> int:
    plan = _plan(args)
    config = GenerationConfig.read(args.generation_config)
    result = run_pilot(
        plan,
        root=args.root,
        config=config,
        proposer=make_proposer(args, config),
        clock=SystemClock(),
        workers=args.workers,
        parallel_banks=args.parallel_banks,
        llm_runtime=args.llm_runtime,
        project_banks=args.project_banks,
    )
    _finish_report(result.finish, result.root)
    return result.finish.exit_code


def _finish(args: argparse.Namespace) -> int:
    finish = finish_pilot(args.root, project_banks=args.project_banks)
    _finish_report(finish, args.root)
    return finish.exit_code


def _check(args: argparse.Namespace) -> int:
    problems = check_pilot(args.root, rerun_verify=args.verify)
    for problem in problems:
        print(f"PROBLEM: {problem}")
    print("OK" if not problems else f"FAILED ({len(problems)} problems)")
    return 0 if not problems else 1


def _archive(args: argparse.Namespace) -> int:
    result = archive_pilot(args.root, clock=SystemClock())
    print(
        json.dumps(
            {
                "archive_sha256": result.archive_sha256,
                "manifest_sha256": result.manifest_sha256,
                "n_files": result.n_files,
                "bytes": result.bytes,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _load(args: argparse.Namespace) -> int:
    manifest = open_bank(args.bank_dir, mode=args.mode)
    print(
        f"{manifest.bank_id} v{manifest.bank_version} ({manifest.set}): {manifest.status}; "
        f"bank hash {manifest.bank_sha256()}"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m av_banks.pilot ...`: exit 0 ok, 1 verify/register problems, 2 refused
    or failed, 3 shortfall (fewer usable banks than dyad slots)."""
    args = _parser().parse_args(argv)
    commands: dict[str, Callable[[argparse.Namespace], int]] = {
        "plan": _print_plan,
        "run": _run,
        "finish": _finish,
        "check": _check,
        "archive": _archive,
        "load": _load,
    }
    try:
        return commands[args.command](args)
    except BankSetError as err:
        print(f"pilot {args.command}: refused: {err}", file=sys.stderr)
        return 2
    except (OSError, ValueError, RuntimeError) as err:
        print(f"pilot {args.command}: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
