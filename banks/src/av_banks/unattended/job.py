"""The job file of the unattended supervisor and the data-policy checks on its paths.

A job file (`av-banks/unattended-job` v1, `banks/schema/unattended-job.schema.json`)
names the job root (state, logs and, by default, the stage roots), the model server and
the stages in order. Relative paths are resolved against the job file's directory. The
job file lives in restricted storage with the inputs it names; committed examples use
placeholders only (`banks/examples/unattended/`).

Data policy (`docs/data-policy.md`): `check_location` refuses a job root or stage root
inside a git work tree unless `git check-ignore` reports it ignored. The pilot and
confirmatory tools refuse every non-DEMO run inside a work tree on top of this.

`purpose` is `banks` (pilot and confirmatory banks, the default) or `rehearsal`: a
full-size DEMO rehearsal of the confirmatory campaign (`rehearsal` module). A rehearsal
job has a DEMO job ID, its size (`rehearsal.dyads`, `rehearsal.spares`) and exactly one
confirmatory stage that plans a DEMO campaign inside the job root from DEMO inputs only
(`_rehearsal_rules`; the inputs themselves are checked by `rehearsal.input_problems`).
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Final, Literal
from urllib.parse import urlsplit, urlunsplit

from av_generation.ids import RunKind, is_demo
from av_generation.jsonio import CodecError, file_sha256, read_json
from av_generation.rundir import RunPolicyError
from jsonschema import Draft202012Validator

from av_banks._paths import schema_path
from av_banks.pilot import PilotError, PilotPlan

JOB_FORMAT: Final = "av-banks/unattended-job"
JOB_SCHEMA: Final = "unattended-job.schema.json"
JOB_ID_RE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{1,62}[A-Za-z0-9]")

E_JOB: Final = "E_JOB"
E_POLICY: Final = "E_POLICY"
E_PLATFORM: Final = "E_PLATFORM"
E_LOCKED: Final = "E_LOCKED"
E_STATE: Final = "E_STATE"
E_REHEARSAL: Final = "E_REHEARSAL"

Purpose = Literal["banks", "rehearsal"]


class SupervisorError(RuntimeError):
    """The supervisor refused (job file, data policy, platform, lock, state); `.code`
    names the rule."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """The OpenAI-compatible model server every stage calls."""

    url: str
    runtime: str = "vllm"
    served_model: str | None = None
    """The model ID `/v1/models` must list; default: the stage config's `model.model_id`."""
    max_wait_s: float = 43_200.0
    """Longest wait for the server before the supervisor stops for a person (12 h)."""
    poll_initial_s: float = 5.0
    poll_max_s: float = 120.0
    probe_timeout_s: float = 10.0


@dataclass(frozen=True, slots=True)
class PilotStage:
    """Pilot banks (#27): `av_banks.pilot` run, check --verify, archive, check."""

    name: str
    root: Path
    """Parent of the pilot roots: run `k` (bank major version `k`) is `root/<run ID k>`."""
    run_id: str
    demo: bool
    units: Path
    generation_config: Path
    meanings: Path
    prompts: Path
    decoding_schema: Path
    dyads: int = 8
    spares: int = 2
    workers: int = 3
    parallel_banks: int = 1
    breaker: int = 3
    max_restarts: int = 2
    on_shortfall: Literal["stop", "archive"] = "stop"
    project_banks: int | None = None
    kind: Literal["pilot"] = "pilot"

    def run_id_for(self, major: int) -> str:
        """Run ID of the pilot run that builds bank major version `major`: the job's run
        ID for 1, `<run_id>-M<major>` for a run that replaces a crashed one."""
        return self.run_id if major == 1 else f"{self.run_id}-M{major}"

    def root_for(self, major: int) -> Path:
        return self.root / self.run_id_for(major)

    @property
    def run_kind(self) -> RunKind:
        return RunKind.DEMO if self.demo else RunKind.PILOT


@dataclass(frozen=True, slots=True)
class CampaignPlanSpec:
    """Arguments of `confirmatory plan`, run when the campaign has no `plan.json` yet."""

    campaign_id: str
    generation_config: Path
    freeze_manifest: Path
    units: Path
    repo: Path | None = None
    freeze_repo_path: str | None = None
    pilot_stages: tuple[str, ...] = ()
    """Pilot stages of this job whose roots (crashed ones included) are `--pilot`."""
    pilot: tuple[Path, ...] = ()
    pilot_namespaces: tuple[str, ...] = ()
    parallel_banks: int = 1


@dataclass(frozen=True, slots=True)
class ConfirmatoryStage:
    """A confirmatory campaign (#28) or its DEMO rehearsal: `av_banks.confirmatory` run
    (with automatic rebuilds of crashed banks), verify-all and register."""

    name: str
    campaign_root: Path
    meanings: Path
    prompts: Path
    decoding_schema: Path
    plan: CampaignPlanSpec | None = None
    parallel_banks: int = 1
    workers: int = 3
    breaker: int = 3
    monitor_interval_s: float = 60.0
    verify_jobs: int = 1
    only: tuple[str, ...] | None = None
    max_rebuilds_per_bank: int = 2
    register: bool = True
    kind: Literal["confirmatory"] = "confirmatory"


Stage = PilotStage | ConfirmatoryStage


@dataclass(frozen=True, slots=True)
class RehearsalSize:
    """The campaign size a rehearsal job builds: `dyads` main banks and `spares` spares."""

    dyads: int
    spares: int

    @property
    def banks(self) -> int:
        return self.dyads + self.spares


@dataclass(frozen=True, slots=True)
class Job:
    """A loaded job file (module docstring)."""

    path: Path
    sha256: str
    job_id: str
    root: Path
    model: ModelSpec
    stages: tuple[Stage, ...]
    heartbeat_s: float = 60.0
    lock_wait_s: float = 86_400.0
    purpose: Purpose = "banks"
    rehearsal: RehearsalSize | None = None
    """The size of a rehearsal job (`purpose: rehearsal`); `None` otherwise."""

    def stage(self, name: str) -> Stage:
        for stage in self.stages:
            if stage.name == name:
                return stage
        raise KeyError(name)


# ---------------------------------------------------------------------------
# Loading


@cache
def _validator() -> Draft202012Validator:
    schema = read_json(schema_path(JOB_SCHEMA))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def job_errors(doc: object) -> list[str]:
    """Schema errors of a job document (empty when valid)."""
    return [
        f"{'/'.join(map(str, e.absolute_path)) or '$'}: {e.message}"
        for e in sorted(_validator().iter_errors(doc), key=lambda e: list(e.absolute_path))
    ]


def _paths(base: Path, values: Sequence[str]) -> tuple[Path, ...]:
    return tuple(_path(base, v) for v in values)


def _path(base: Path, value: str) -> Path:
    path = Path(os.path.expanduser(value))
    return (path if path.is_absolute() else base / path).resolve()


def _opt(base: Path, value: object) -> Path | None:
    return _path(base, value) if isinstance(value, str) else None


def _pilot(base: Path, root: Path, raw: Mapping[str, Any]) -> PilotStage:
    name = str(raw["name"])
    return PilotStage(
        name=name,
        root=_opt(base, raw.get("root")) or root / name,
        run_id=str(raw["run_id"]),
        demo=bool(raw.get("demo", False)),
        units=_path(base, raw["units"]),
        generation_config=_path(base, raw["generation_config"]),
        meanings=_path(base, raw["meanings"]),
        prompts=_path(base, raw["prompts"]),
        decoding_schema=_path(base, raw["decoding_schema"]),
        dyads=int(raw.get("dyads", 8)),
        spares=int(raw.get("spares", 2)),
        workers=int(raw.get("workers", 3)),
        parallel_banks=int(raw.get("parallel_banks", 1)),
        breaker=int(raw.get("breaker", 3)),
        max_restarts=int(raw.get("max_restarts", 2)),
        on_shortfall=raw.get("on_shortfall", "stop"),
        project_banks=raw.get("project_banks"),
    )


def _confirmatory(base: Path, root: Path, raw: Mapping[str, Any]) -> ConfirmatoryStage:
    name = str(raw["name"])
    plan = None
    if isinstance(raw.get("plan"), dict):
        p = raw["plan"]
        plan = CampaignPlanSpec(
            campaign_id=str(p["campaign_id"]),
            generation_config=_path(base, p["generation_config"]),
            freeze_manifest=_path(base, p["freeze_manifest"]),
            units=_path(base, p["units"]),
            repo=_opt(base, p.get("repo")),
            freeze_repo_path=p.get("freeze_repo_path"),
            pilot_stages=tuple(p.get("pilot_stages", ())),
            pilot=_paths(base, p.get("pilot", ())),
            pilot_namespaces=tuple(p.get("pilot_namespaces", ())),
            parallel_banks=int(p.get("parallel_banks", 1)),
        )
    only = raw.get("only")
    return ConfirmatoryStage(
        name=name,
        campaign_root=_opt(base, raw.get("campaign_root")) or root / name,
        meanings=_path(base, raw["meanings"]),
        prompts=_path(base, raw["prompts"]),
        decoding_schema=_path(base, raw["decoding_schema"]),
        plan=plan,
        parallel_banks=int(raw.get("parallel_banks", 1)),
        workers=int(raw.get("workers", 3)),
        breaker=int(raw.get("breaker", 3)),
        monitor_interval_s=float(raw.get("monitor_interval_s", 60.0)),
        verify_jobs=int(raw.get("verify_jobs", 1)),
        only=tuple(only) if only is not None else None,
        max_rebuilds_per_bank=int(raw.get("max_rebuilds_per_bank", 2)),
        register=bool(raw.get("register", True)),
    )


def load_job(path: str | os.PathLike[str]) -> Job:
    """Read, schema-check and resolve a job file; `SupervisorError(E_JOB)` on any
    problem (the cross-stage rules are in `_job_rules`)."""
    source = Path(path).resolve()
    try:
        doc = read_json(source)
    except (OSError, CodecError) as err:
        raise SupervisorError(E_JOB, f"cannot read the job file: {err}") from None
    errors = job_errors(doc)
    if errors:
        raise SupervisorError(E_JOB, "the job file is not valid: " + "; ".join(errors[:5]))
    base = source.parent
    root = _path(base, doc["root"])
    m = doc["model"]
    model = ModelSpec(
        url=str(m["url"]).rstrip("/"),
        runtime=str(m.get("runtime", "vllm")),
        served_model=m.get("served_model"),
        max_wait_s=float(m.get("max_wait_s", 43_200.0)),
        poll_initial_s=float(m.get("poll_initial_s", 5.0)),
        poll_max_s=float(m.get("poll_max_s", 120.0)),
        probe_timeout_s=float(m.get("probe_timeout_s", 10.0)),
    )
    stages: list[Stage] = [
        _pilot(base, root, s) if s["kind"] == "pilot" else _confirmatory(base, root, s)
        for s in doc["stages"]
    ]
    size = doc.get("rehearsal")
    job = Job(
        path=source,
        sha256=file_sha256(source),
        job_id=str(doc["job_id"]),
        root=root,
        model=model,
        stages=tuple(stages),
        heartbeat_s=float(doc.get("heartbeat_s", 60.0)),
        lock_wait_s=float(doc.get("lock_wait_s", 86_400.0)),
        purpose=doc.get("purpose", "banks"),
        rehearsal=RehearsalSize(int(size["dyads"]), int(size["spares"])) if size else None,
    )
    problems = _job_rules(job)
    if problems:
        raise SupervisorError(E_JOB, "; ".join(problems))
    return job


def _job_rules(job: Job) -> list[str]:
    """Rules the schema cannot state: unique stage names and roots, pilot stages named
    by a plan come earlier, pilot run IDs valid for every restart, a register needs the
    whole campaign."""
    problems = []
    names = [s.name for s in job.stages]
    if len(set(names)) != len(names):
        problems.append(f"stage names repeat: {names}")
    roots = [s.root if isinstance(s, PilotStage) else s.campaign_root for s in job.stages]
    if len(set(roots)) != len(roots):
        problems.append("two stages share a root")
    for i, stage in enumerate(job.stages):
        if isinstance(stage, PilotStage):
            for major in range(1, stage.max_restarts + 2):
                plan = PilotPlan(stage.run_id_for(major), stage.demo, (), stage.spares, major)
                try:
                    plan.check_run_ids()
                except (PilotError, RunPolicyError) as err:
                    problems.append(f"{stage.name}: run ID for major {major}: {err}")
                    break
        else:
            earlier = {s.name for s in job.stages[:i] if isinstance(s, PilotStage)}
            if stage.plan is not None:
                missing = sorted(set(stage.plan.pilot_stages) - earlier)
                if missing:
                    problems.append(
                        f"{stage.name}: plan names pilot stages {missing} that do "
                        "not come before it"
                    )
            if stage.only is not None and stage.register:
                problems.append(
                    f"{stage.name}: 'register' needs the whole campaign; set "
                    "register to false for a stage limited by 'only'"
                )
    if job.purpose == "rehearsal":
        problems += _rehearsal_rules(job)
    return problems


def _rehearsal_rules(job: Job) -> list[str]:
    """A rehearsal job (module docstring): DEMO job and campaign IDs, one confirmatory
    stage that plans its campaign inside the job root, no repository, no real pilot."""
    problems = []
    if not is_demo(job.job_id):
        problems.append(f"a rehearsal job ID starts with 'DEMO-' (found {job.job_id!r})")
    if len(job.stages) != 1 or not isinstance(job.stages[0], ConfirmatoryStage):
        return [*problems, "a rehearsal job has exactly one stage, of kind confirmatory"]
    stage = job.stages[0]
    if stage.campaign_root == job.root or job.root not in stage.campaign_root.parents:
        problems.append(f"{stage.name}: a rehearsal campaign root lies inside the job root")
    spec = stage.plan
    if spec is None:
        return [*problems, f"{stage.name}: a rehearsal plans its own campaign; give 'plan'"]
    if not is_demo(spec.campaign_id):
        problems.append(f"{stage.name}: the rehearsal campaign ID starts with 'DEMO-'")
    if spec.repo is not None or spec.freeze_repo_path is not None:
        problems.append(f"{stage.name}: a rehearsal checks no freeze tag (no repo)")
    if spec.pilot or spec.pilot_stages:
        problems.append(f"{stage.name}: a rehearsal reads no pilot root (DEMO namespaces only)")
    if not all(is_demo(ns) for ns in spec.pilot_namespaces):
        problems.append(f"{stage.name}: rehearsal pilot namespaces start with 'DEMO-'")
    return problems


# ---------------------------------------------------------------------------
# Data policy


def _git_dir_above(path: Path) -> Path | None:
    """The nearest directory at or above `path` that holds a `.git` entry."""
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def check_location(path: str | os.PathLike[str]) -> str | None:
    """Refuse a path inside a git work tree unless git reports it ignored
    (`SupervisorError(E_POLICY)`); returns the note to log, or `None` outside git."""
    target = Path(path).resolve()
    top = _git_dir_above(target)
    if top is None:
        return None
    probe = target.relative_to(top).as_posix() + "/"
    try:
        result = subprocess.run(
            ["git", "-C", os.fspath(top), "check-ignore", "-q", "--", probe],
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as err:
        raise SupervisorError(
            E_POLICY, f"{target} is inside a git work tree and git could not check it: {err}"
        ) from None
    if result.returncode == 0:
        return f"{target} is inside a git work tree but ignored by git"
    raise SupervisorError(
        E_POLICY,
        f"{target} is inside a git work tree and not ignored by git; bank data belongs in "
        "restricted storage (docs/data-policy.md)",
    )


def redact_url(url: str) -> str:
    """The URL without user name or password (logs never carry credentials)."""
    parts = urlsplit(url)
    if parts.username is None and parts.password is None:
        return url
    host = parts.hostname or ""
    netloc = host + (f":{parts.port}" if parts.port is not None else "")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
