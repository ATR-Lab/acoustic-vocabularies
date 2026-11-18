"""Full-size rehearsal of the confirmatory campaign on DEMO inputs (#267, decision D1a).

The generation settings are frozen at G4 and registered before any confirmatory bank is
built, so the confirmatory campaign (#28) cannot run over the December closure. A
rehearsal job runs that campaign anyway, at full size, on synthetic inputs, against the
real model server: every main and spare bank of the chosen size, every attempt the
builder allows, `verify-all` and the register at the end (a DEMO register). Its numbers
(banks, attempts, slots, slots per hour, wall time, model-server waits, rebuilds) size
the real run; nothing it builds can be taken for a confirmatory bank.

`write_rehearsal_job(out, llm_url=...)` (`python -m av_banks.unattended rehearsal-job`)
writes under `out/`:

- `REHEARSAL`: the watermark (`av-banks/rehearsal-marker`); `run` writes it into the job
  root as well;
- `inputs/`: the DEMO meaning set, the committed prompt templates under a DEMO prompt-set
  name (`DEMO-rehearsal-prompts`: real prompt lengths, DEMO label), the recipe schema, a
  DEMO generation config pinned to them, a DEMO draft freeze manifest of that config
  (never frozen, no tag) and the DEMO unit stand-ins `B-C01`..`B-C<dyads>` and
  `B-S01`..`B-S<spares>` (`"demo": true`);
- `job.json`: `purpose: rehearsal`, the size, and one confirmatory stage `demo-campaign`
  (campaign `<job ID>-C`, DEMO pilot namespaces, the register on) under the job root
  `job/`.

Guards (they refuse before any child runs; `check` lists them):

- **IDs.** Job, campaign, bank, run and seed-namespace IDs start with `DEMO-` (the job
  rules in `job`; `campaign_problems` reads them back from `plan.json`). Unit IDs keep
  the dyad-slot names the plan binds banks to; every unit file says `"demo": true`.
- **Inputs** (`input_problems`): a DEMO meaning set, a DEMO prompt set, a DEMO
  generation config, a draft freeze manifest without a tag, and exactly the DEMO units
  of the size.
- **Separation** (`separation_problems`, every job): a rehearsal job root is neither
  inside nor around another job root or a pilot or confirmatory output; every other job
  refuses a root inside or around a rehearsal root (a `REHEARSAL` marker). The
  data-policy check (`job.check_location`: outside git, or ignored; 0700) applies as
  before.
- **Campaign** (`campaign_problems`, every job): `plan.json` names the campaign of the
  job's plan; a job whose ID does not start with `DEMO-` refuses a DEMO campaign; a
  rehearsal plan is a DEMO plan of exactly the rehearsal's size.

Guards that already kept DEMO banks out of confirmatory use, unchanged: the confirmatory
plan refuses a non-empty campaign directory, a freeze manifest that is not frozen and
tagged, a DEMO config and DEMO units for a non-DEMO campaign, and `DEMO-` run IDs for a
confirmatory run; `av_banks.register.open_bank(..., mode="confirmatory")`
(`bank_manifest.require_bank_set`) refuses every `DEMO-` bank.

At the end the supervisor writes `rehearsal-summary.json` and `rehearsal-summary.md` in
the job root (`write_rehearsal_summary`). Runbook: `banks/docs/unattended-run.md`.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from av_generation._paths import examples_path
from av_generation.genconfig import GenerationConfig
from av_generation.ids import is_demo
from av_generation.jsonio import document_text, read_json, write_document
from av_generation.meanings import load_meanings
from av_generation.prompts import MANIFEST_NAME as PROMPT_SET_NAME
from av_generation.prompts import default_prompt_set_dir, load_prompt_set, prompt_set_manifest
from av_generation.rundir import MANIFEST_NAME as RUN_MANIFEST_NAME
from av_sound._paths import schema_path as sound_schema_path

from av_banks.confirmatory import common as confirmatory_common
from av_banks.confirmatory.common import CAMPAIGN_ID_RE, CampaignError, CampaignLayout
from av_banks.confirmatory.plan import CampaignPlan, read_plan, read_rebuilds
from av_banks.confirmatory.rehearsal import DEMO_PILOT, demo_freeze_manifest, demo_unit
from av_banks.confirmatory.runner import campaign_status
from av_banks.metrics import (
    MS_PER_HOUR,
    ThroughputSummary,
    ratio,
    summarize_banks,
    summary_markdown,
)
from av_banks.pilot import PLAN_NAME as PILOT_PLAN_NAME
from av_banks.pilot import REGISTER_NAME as PILOT_REGISTER_NAME

from .demo import demo_config
from .job import (
    E_JOB,
    JOB_FORMAT,
    ConfirmatoryStage,
    Job,
    PilotStage,
    RehearsalSize,
    SupervisorError,
)
from .session import Session, wall_seconds
from .state import CHILD_LOGS, LOCK_NAME, STATE_NAME, atomic_write

PURPOSE: Final = "rehearsal"
STAGE_NAME: Final = "demo-campaign"
"""Not `rehearsal`: on a case-insensitive file system its root would be the marker."""
DEFAULT_JOB_ID: Final = "DEMO-rehearsal-01"
FULL_SIZE: Final = RehearsalSize(64, 8)
"""The default size: 64 main and 8 spare banks (72), the default confirmatory campaign."""
MARKER_NAME: Final = "REHEARSAL"
MARKER_FORMAT: Final = "av-banks/rehearsal-marker"
SUMMARY_FORMAT: Final = "av-banks/rehearsal-summary"
SUMMARY_JSON: Final = "rehearsal-summary.json"
SUMMARY_MD: Final = "rehearsal-summary.md"
PROMPTS_NAME: Final = "DEMO-rehearsal-prompts"
WATERMARK: Final = (
    "REHEARSAL: synthetic DEMO banks built to measure the confirmatory run at full size. "
    "They are not confirmatory banks: never use, publish, register or archive them as such."
)
OUTPUT_MARKERS: Final[dict[str, str]] = {
    STATE_NAME: "an unattended job root",
    LOCK_NAME: "an unattended job root",
    CampaignLayout(Path()).plan.name: "a confirmatory campaign",
    PILOT_PLAN_NAME: "a pilot root",
    PILOT_REGISTER_NAME: "a pilot root",
    RUN_MANIFEST_NAME: "a bank run",
}
"""Files that mark another job's root or a pilot or confirmatory output. Markers are
looked up as files: on a case-insensitive file system `REHEARSAL` also names a directory
`rehearsal`, such as the documented `<restricted>/rehearsal/`."""


# ---------------------------------------------------------------------------
# Size


def size_problem(size: RehearsalSize) -> str | None:
    """Why this checkout cannot plan a campaign of `size` (`None` when it can). The
    confirmatory plan takes its size from the unit set once `common.CampaignSize` exists;
    before that it plans 64 main and 8 spare banks only."""
    sized = getattr(confirmatory_common, "CampaignSize", None)
    if sized is None:
        if size == FULL_SIZE:
            return None
        return (
            f"{size.dyads} + {size.spares} banks: the confirmatory plan of this checkout has "
            f"{FULL_SIZE.dyads} main and {FULL_SIZE.spares} spare banks; other sizes need the "
            "plan sized from the unit set (av_banks.confirmatory.size)"
        )
    try:  # pragma: no cover - the plan sized from the unit set
        sized(size.dyads, size.spares)
    except CampaignError as err:  # pragma: no cover
        return str(err)
    return None  # pragma: no cover


def unit_ids(size: RehearsalSize) -> tuple[str, ...]:
    """The DEMO units of `size`: `B-C01`..`B-C<dyads>`, then `B-S01`..`B-S<spares>`."""
    return (
        *(f"B-C{n:02d}" for n in range(1, size.dyads + 1)),
        *(f"B-S{n:02d}" for n in range(1, size.spares + 1)),
    )


# ---------------------------------------------------------------------------
# The rehearsal job


def marker(job_id: str) -> dict[str, Any]:
    return {
        "format": MARKER_FORMAT,
        "format_version": 1,
        "purpose": PURPOSE,
        "kind": "demo",
        "job_id": job_id,
        "note": WATERMARK,
    }


def write_marker(directory: Path, job_id: str) -> None:
    """Write the `REHEARSAL` marker of `job_id` into `directory` (kept when present)."""
    path = directory / MARKER_NAME
    if not path.exists():
        atomic_write(path, document_text(marker(job_id)))


def marker_job(path: Path) -> str | None:
    """The job ID a `REHEARSAL` marker names (`None` when it is unreadable)."""
    try:
        doc = read_json(path)
    except (OSError, ValueError):
        return None
    job_id = doc.get("job_id") if isinstance(doc, dict) else None
    return job_id if isinstance(job_id, str) else None


def write_rehearsal_job(
    out: str | os.PathLike[str],
    *,
    llm_url: str,
    job_id: str = DEFAULT_JOB_ID,
    size: RehearsalSize = FULL_SIZE,
    parallel_banks: int = 6,
    workers: int = 3,
    only: Sequence[str] | None = None,
) -> Path:
    """Write the rehearsal inputs, the marker and `job.json` under `out` (module
    docstring); returns the job file. `only` (tests only, never on the command line)
    limits the build to those banks and turns the register off: a partial rehearsal."""
    if not is_demo(job_id):
        raise ValueError("a rehearsal job ID starts with 'DEMO-'")
    campaign_id = f"{job_id}-C"
    if not CAMPAIGN_ID_RE.fullmatch(campaign_id):
        raise ValueError(f"job ID {job_id!r}: the campaign ID {campaign_id!r} is too long")
    problem = size_problem(size)
    if problem:
        raise ValueError(problem)
    base = Path(out).resolve()
    if base.exists() and any(base.iterdir()):
        raise ValueError(f"{base} is not empty")
    foreign = _above(base, job_id)
    if foreign:
        raise ValueError(f"{base} lies inside {foreign}; a rehearsal has its own location")
    inputs = base / "inputs"
    inputs.mkdir(parents=True)
    write_marker(base, job_id)
    _write_inputs(inputs, size)
    stage: dict[str, Any] = {
        "name": STAGE_NAME,
        "kind": "confirmatory",
        "plan": {
            "campaign_id": campaign_id,
            "generation_config": "inputs/generation-config.json",
            "freeze_manifest": "inputs/freeze-manifest.json",
            "units": "inputs/units",
            "pilot_namespaces": list(DEMO_PILOT),
            "parallel_banks": parallel_banks,
        },
        "meanings": "inputs/meanings",
        "prompts": "inputs/prompts",
        "decoding_schema": "inputs/decoding-schema.json",
        "parallel_banks": parallel_banks,
        "workers": workers,
        "breaker": 3,
        "monitor_interval_s": 60,
        "verify_jobs": 8,
        "max_rebuilds_per_bank": 2,
        "register": only is None,
    }
    if only is not None:
        stage["only"] = list(only)
    job = {
        "format": JOB_FORMAT,
        "format_version": 1,
        "job_id": job_id,
        "purpose": PURPOSE,
        "rehearsal": {"dyads": size.dyads, "spares": size.spares},
        "root": "job",
        "model": {"url": llm_url},
        "stages": [stage],
    }
    path = base / "job.json"
    write_document(path, job)
    return path


def _write_inputs(inputs: Path, size: RehearsalSize) -> None:
    meanings = inputs / "meanings"
    prompts = inputs / "prompts"
    shutil.copytree(examples_path("demo-meanings"), meanings)
    shutil.copytree(default_prompt_set_dir(), prompts)
    write_document(prompts / PROMPT_SET_NAME, prompt_set_manifest(prompts, name=PROMPTS_NAME))
    schema: dict[str, Any] = json.loads(
        sound_schema_path("recipe.schema.json").read_text(encoding="utf-8")
    )
    write_document(inputs / "decoding-schema.json", schema)
    config = demo_config(meanings, prompts, schema, name="DEMO-rehearsal-config")
    config.write(inputs / "generation-config.json")
    write_document(inputs / "freeze-manifest.json", demo_freeze_manifest(config))
    for unit_id in unit_ids(size):
        path = inputs / "units" / unit_id / "permutation.json"
        path.parent.mkdir(parents=True)
        write_document(path, demo_unit(unit_id))


# ---------------------------------------------------------------------------
# Guards


def rehearsal_stage(job: Job) -> ConfirmatoryStage:
    """The one confirmatory stage of a rehearsal job (the job rules ensure it)."""
    stage = job.stages[0] if job.stages else None
    if job.rehearsal is None or not isinstance(stage, ConfirmatoryStage) or stage.plan is None:
        raise SupervisorError(E_JOB, "not a rehearsal job")
    return stage


def input_problems(job: Job) -> list[str]:
    """What makes the inputs of a rehearsal job unfit (module docstring); empty when fit."""
    stage = rehearsal_stage(job)
    spec = stage.plan
    assert spec is not None and job.rehearsal is not None
    problems: list[str] = []
    try:
        meanings = load_meanings(stage.meanings)
    except (OSError, ValueError) as err:
        problems.append(f"meanings: {err}")
    else:
        if not meanings.demo:
            problems.append(f"the meaning set {meanings.name!r} is not a DEMO set")
        try:
            prompts = load_prompt_set(stage.prompts, meanings=meanings)
        except (OSError, ValueError) as err:
            problems.append(f"prompts: {err}")
        else:
            if not prompts.demo:
                problems.append(f"the prompt set {prompts.name!r} is not a DEMO set")
    try:
        config = GenerationConfig.read(spec.generation_config)
    except (OSError, ValueError) as err:
        problems.append(f"generation config: {err}")
    else:
        if not config.demo:
            problems.append(f"the generation config {config.name!r} is not a DEMO config")
    try:
        freeze = read_json(spec.freeze_manifest)
    except (OSError, ValueError) as err:
        problems.append(f"freeze manifest: {err}")
    else:
        if not isinstance(freeze, dict) or freeze.get("status") != "draft" or freeze.get("tag"):
            problems.append(
                "the freeze manifest is frozen or tagged; a rehearsal runs on a DEMO draft "
                "(never frozen, no tag)"
            )
    return problems + _unit_problems(spec.units, job.rehearsal)


def _unit_problems(units: Path, size: RehearsalSize) -> list[str]:
    expected = unit_ids(size)
    found = sorted(
        p.name for p in (units.iterdir() if units.is_dir() else ()) if p.name[:3] in ("B-C", "B-S")
    )
    if found != sorted(expected):
        return [
            f"the units are not exactly B-C01..B-C{size.dyads:02d} and "
            f"B-S01..B-S{size.spares:02d} ({len(found)} B-C/B-S entries found)"
        ]
    real = []
    for unit_id in expected:
        try:
            doc = read_json(units / unit_id / "permutation.json")
        except (OSError, ValueError):
            doc = None
        if not isinstance(doc, dict) or doc.get("demo") is not True:
            real.append(unit_id)
    return [f"units that are not DEMO stand-ins: {real[:5]}"] if real else []


def _stage_root(stage: PilotStage | ConfirmatoryStage) -> Path:
    return stage.root if isinstance(stage, PilotStage) else stage.campaign_root


def _above(path: Path, job_id: str) -> str | None:
    """A marker of another job or output in a directory above `path` (rehearsal rule)."""
    for directory in path.parents:
        for name, what in OUTPUT_MARKERS.items():
            if (directory / name).is_file():
                return f"{directory} ({what}: {name})"
        if (directory / MARKER_NAME).is_file() and marker_job(directory / MARKER_NAME) != job_id:
            return f"{directory} (another rehearsal: {MARKER_NAME})"
    return None


def _walk_rehearsal(job: Job) -> str | None:
    """A marker of another job or output inside a rehearsal job root (its own state,
    lock, marker and campaign are its own)."""
    own = {_stage_root(s) for s in job.stages} | {job.root / CHILD_LOGS}
    for dirpath, dirnames, filenames in os.walk(job.root):
        directory = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if directory / d not in own)
        names = set(filenames)
        if directory == job.root:
            names -= {STATE_NAME, LOCK_NAME}
            if marker_job(directory / MARKER_NAME) == job.job_id:
                names.discard(MARKER_NAME)
        for name in sorted(names):
            if name in OUTPUT_MARKERS:
                return f"{directory} ({OUTPUT_MARKERS[name]}: {name})"
            if name == MARKER_NAME:
                return f"{directory} (another rehearsal: {name})"
    return None


def _rehearsal_inside(paths: Sequence[Path]) -> str | None:
    """A `REHEARSAL` marker at or above any of `paths`, or below any of them."""
    for path in paths:
        for directory in (path, *path.parents):
            if (directory / MARKER_NAME).is_file():
                return f"{directory} holds {MARKER_NAME}"
    tops = [p for p in paths if not any(other in p.parents for other in paths)]
    for path in dict.fromkeys(tops):
        for dirpath, _dirnames, filenames in os.walk(path):
            if MARKER_NAME in filenames:
                return f"{dirpath} holds {MARKER_NAME}"
    return None


def separation_problems(job: Job) -> list[str]:
    """Roots that mix a rehearsal with anything else (module docstring)."""
    roots = [job.root, *(_stage_root(s) for s in job.stages)]
    if job.purpose == PURPOSE:
        foreign = _above(job.root, job.job_id) or _walk_rehearsal(job)
        if foreign:
            return [
                f"the rehearsal job root {job.root} is inside or around {foreign}; a "
                "rehearsal has its own location (<restricted>/rehearsal/<job ID>)"
            ]
        return []
    found = _rehearsal_inside(roots)
    if found:
        return [
            f"a root of job {job.job_id} is inside or around a rehearsal ({found}); "
            "rehearsal banks are DEMO banks and stay in their own location"
        ]
    return []


def campaign_problems(job: Job, stage: ConfirmatoryStage) -> list[str]:
    """What makes an existing campaign wrong for this job (module docstring)."""
    try:
        plan = read_plan(stage.campaign_root)
    except (OSError, ValueError, CampaignError) as err:
        return [f"{stage.name}: plan.json cannot be read: {err}"]
    problems = []
    if stage.plan is not None and plan.campaign_id != stage.plan.campaign_id:
        problems.append(
            f"{stage.name}: plan.json belongs to campaign {plan.campaign_id!r}, not to "
            f"{stage.plan.campaign_id!r} of the job"
        )
    if plan.demo and not is_demo(job.job_id):
        problems.append(
            f"{stage.name}: {plan.campaign_id} is a DEMO campaign; a job whose ID does not "
            "start with 'DEMO-' never builds DEMO banks"
        )
    if job.purpose == PURPOSE:
        problems += _rehearsal_plan_problems(plan, job.rehearsal)
    return problems


def _rehearsal_plan_problems(plan: CampaignPlan, size: RehearsalSize | None) -> list[str]:
    problems = []
    if not (plan.demo and plan.kind == "demo" and plan.set == "demo"):
        problems.append(f"{plan.campaign_id} is not a DEMO plan")
    ids = {x for b in plan.banks for x in (b.bank_id, b.run_id, b.seed_namespace)}
    real = sorted(x for x in ids | {plan.campaign_id} if not is_demo(x))
    if real:
        problems.append(f"the plan has IDs that do not start with 'DEMO-': {real[:5]}")
    main = sum(1 for b in plan.banks if b.role == "main")
    spares = len(plan.banks) - main
    if size is not None and (main, spares) != (size.dyads, size.spares):
        problems.append(
            f"the plan has {main} main and {spares} spare banks; the rehearsal is "
            f"{size.dyads} + {size.spares}"
        )
    if plan.freeze.status != "draft" or plan.freeze.tag is not None:
        problems.append("the plan names a frozen or tagged freeze manifest")
    return problems


# ---------------------------------------------------------------------------
# The summary


def rehearsal_summary(s: Session) -> tuple[dict[str, Any], ThroughputSummary]:
    """The numbers that size the real run (module docstring), from the campaign files
    and the supervisor's state, and the bank metrics (`metrics.summarize_banks`) of the
    banks kept."""
    stage = rehearsal_stage(s.job)
    size = s.job.rehearsal
    assert size is not None
    state = s.state
    st = state.stages[0]
    layout = CampaignLayout.at(stage.campaign_root)
    status = campaign_status(stage.campaign_root, active=False)
    scope = [b for b in status.banks if stage.only is None or b.bank_id in stage.only]
    final = [b for b in scope if b.state in ("complete", "unavailable")]
    throughput = summarize_banks(
        [layout.bank_dir(b.run_id, b.bank_id) for b in final],
        run_dirs=[layout.run_dir(b.run_id) for b in final],
        project_banks=len(status.banks),
    )
    campaign_s = wall_seconds(st.started_utc, st.ended_utc)
    rebuilds = read_rebuilds(stage.campaign_root)
    register = None
    if layout.register_json.is_file():
        doc = read_json(layout.register_json)
        register = {
            "decision": doc.get("decision"),
            "demo": doc.get("demo"),
            "register_csv_sha256": doc.get("hashes", {}).get("register_csv_sha256"),
        }
    doc = {
        "format": SUMMARY_FORMAT,
        "format_version": 1,
        "purpose": PURPOSE,
        "kind": "demo",
        "watermark": WATERMARK,
        "job_id": state.job_id,
        "campaign_id": status.campaign_id,
        "size": {"dyads": size.dyads, "spares": size.spares, "banks": size.banks},
        "planned_banks": len(status.banks),
        "full_size": stage.only is None,
        "scope": "all" if stage.only is None else list(stage.only),
        "status": state.status,
        "created_utc": state.created_utc,
        "ended_utc": state.ended_utc,
        "wall_s": wall_seconds(state.created_utc, state.ended_utc),
        "campaign_wall_s": campaign_s,
        "banks": {
            "complete": sum(1 for b in scope if b.state == "complete"),
            "unavailable": sum(1 for b in scope if b.state == "unavailable"),
            "crashed_versions": status.crashed_versions,
        },
        "attempts": throughput.attempts,
        "slots": status.slots_total,
        "slots_per_hour": throughput.slots_per_hour,
        "slots_per_hour_run": throughput.slots_per_hour_run,
        "slots_per_hour_wall": (
            ratio(status.slots_total * MS_PER_HOUR, campaign_s * 1000, 1) if campaign_s else None
        ),
        "model_waits": {
            "count": state.waits.count,
            "seconds": state.waits.seconds,
            "longest_s": state.waits.longest_s,
        },
        "rebuilds": {
            "total": len(rebuilds),
            "automatic": sum(st.rebuilds.values()),
            "banks": sorted({r.bank_id for r in rebuilds}),
        },
        "runs": st.runs,
        "sessions": state.sessions,
        "register": register,
        "throughput": throughput.to_dict(),
    }
    return doc, throughput


def _rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.1f}"


def _hours(seconds: float | None) -> str:
    return "n/a" if seconds is None else f"{seconds / 3600:.2f} h ({seconds:,.0f} s)"


def summary_text(doc: dict[str, Any], throughput: ThroughputSummary) -> str:
    """`rehearsal-summary.md`: the watermark, the sizing table, then the bank metrics."""
    size, banks, waits, rebuilds = doc["size"], doc["banks"], doc["model_waits"], doc["rebuilds"]
    register = doc["register"]
    scope = "full size" if doc["full_size"] else f"PARTIAL: {len(doc['scope'])} banks built"
    lines = [
        f"> {WATERMARK}",
        "",
        "| Measure | Value |",
        "| --- | --- |",
        f"| Job, campaign | `{doc['job_id']}`, `{doc['campaign_id']}` (DEMO) |",
        f"| Size | {size['dyads']} main + {size['spares']} spare = {size['banks']} banks "
        f"({doc['planned_banks']} planned; {scope}) |",
        f"| Banks | {banks['complete']} complete, {banks['unavailable']} unavailable; crashed "
        f"versions rebuilt: {banks['crashed_versions']} |",
        f"| Attempts, slots | {doc['attempts']} attempts of the kept banks; {doc['slots']:,} "
        "slots (crashed versions included) |",
        f"| Slots per hour | {_rate(doc['slots_per_hour_wall'])} over the campaign's wall time; "
        f"{_rate(doc['slots_per_hour_run'])} over the bank runs; "
        f"{_rate(doc['slots_per_hour'])} per bank builder |",
        f"| Wall time | campaign {_hours(doc['campaign_wall_s'])}; job {_hours(doc['wall_s'])} |",
        f"| Model-server waits | {waits['count']} ({waits['seconds']:,.0f} s in all, longest "
        f"{waits['longest_s']:,.0f} s) |",
        f"| Rebuilds | {rebuilds['total']} ({rebuilds['automatic']} automatic); banks "
        f"{', '.join(rebuilds['banks']) or 'none'} |",
        f"| Supervisor | {doc['sessions']} sessions, {doc['runs']} campaign runs |",
        "| Register (DEMO) | "
        + (
            f"decision `{register['decision']}`, register.csv `{register['register_csv_sha256']}`"
            if register
            else "none" + ("" if doc["full_size"] else " (a partial rehearsal compiles none)")
        )
        + " |",
    ]
    return summary_markdown(
        throughput,
        title=f"REHEARSAL (DEMO) of the confirmatory campaign: {doc['job_id']}",
        preamble=lines,
    )


def write_rehearsal_summary(s: Session) -> dict[str, Any]:
    """Write `rehearsal-summary.json` and `.md` into the job root; returns the JSON."""
    doc, throughput = rehearsal_summary(s)
    text = summary_text(doc, throughput)
    atomic_write(s.job.root / SUMMARY_JSON, document_text(doc))
    atomic_write(s.job.root / SUMMARY_MD, text)
    return doc
