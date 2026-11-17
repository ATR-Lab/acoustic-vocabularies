"""The stage drivers: pilot banks (#27) and a confirmatory campaign (#28) or its rehearsal.

Each driver re-reads the stage's own files at every step, so a step resumes under the
runner's own rules whatever the supervisor knew when it stopped. Every write is done by
an existing command line in a child process; the supervisor itself only reads.

**Pilot** (`banks/docs/pilot-banks.md`): `run` (with `--breaker`, so a model-server
outage crashes the run instead of failing cells), `check --verify`, `archive`, `check`.
Exit codes of `run`: 0 next step; 3 shortfall (stop, or archive with
`on_shortfall: archive`); 1 a verify or plan problem (stop); anything else with files in
the root is a crash. A crashed run is not resumed (#26, section 2): the crashed root is
kept as the record (`finish`, `check`, `archive`), then the run starts again in a new
root with a new run ID (`<run_id>-M<k>`) and `--major k`, up to `max_restarts` times. A
run whose end the supervisor did not see is a crash unless it wrote its register and
summary and closed every run manifest; then `finish` gives its exit code again.

**Confirmatory** (`banks/docs/confirmatory-banks.md`): `plan` when the campaign has no
`plan.json` and the job gives the plan arguments; then, until every bank in scope is
`complete` or `unavailable`: a `runner.lock` left by a runner of this job is broken with
`run --break-lock` (proof: `runtime` module docstring); each crashed bank is rebuilt
with `rebuild --reason` after the model server answers again, at most
`max_rebuilds_per_bank` times (counted in `rebuilds.jsonl`); pending banks are built by
`run`. Then `verify-all` and `register`. `rebuild` is never called for a `complete` or
`unavailable` bank (`assert_rebuildable`; the runner refuses it as well).
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

from av_generation.clock import utc_text
from av_generation.genconfig import GenerationConfig
from av_generation.jsonio import CodecError, iter_jsonl, read_json
from av_generation.records import RunManifest
from av_generation.rundir import MANIFEST_NAME as RUN_MANIFEST_NAME

from av_banks.archive import ARCHIVE_HASH_NAME, ARCHIVE_MANIFEST_NAME
from av_banks.confirmatory.common import CampaignLayout
from av_banks.confirmatory.plan import read_rebuilds
from av_banks.confirmatory.runner import BankProgress, CampaignStatus, campaign_status
from av_banks.layout import BankLayout
from av_banks.pilot import REGISTER_NAME, RUNS_DIR, SUMMARY_JSON_NAME, bank_dirs

from .job import ConfirmatoryStage, PilotStage
from .session import Session, StopForPerson
from .state import StageState

PILOT: Final = "av_banks.pilot"
CONFIRMATORY: Final = "av_banks.confirmatory"
REBUILD_MARK: Final = "unattended supervisor (automatic rebuild)"
"""Start of the reason of every rebuild the supervisor records (it counts them)."""
LOCK_SLACK: Final = timedelta(seconds=2)
FINAL: Final = frozenset({"complete", "unavailable"})


def _iso(text: str) -> datetime:
    return datetime.fromisoformat(text.strip().replace("Z", "+00:00"))


def _see(s: Session, result_path: Path) -> str:
    return f"see {s.rel(result_path)} and its .err file"


# ---------------------------------------------------------------------------
# Pilot


def _archived(root: Path) -> bool:
    return (root / ARCHIVE_MANIFEST_NAME).exists() and (root / ARCHIVE_HASH_NAME).exists()


def _nonempty(root: Path) -> bool:
    return root.is_dir() and any(root.iterdir())


def pilot_finished(root: Path) -> bool:
    """`pilot run` wrote its register and summary and closed every run manifest."""
    if not ((root / REGISTER_NAME).is_file() and (root / SUMMARY_JSON_NAME).is_file()):
        return False
    runs = root / RUNS_DIR
    if not runs.is_dir():
        return False
    for run in runs.iterdir():
        try:
            if RunManifest.read(run / RUN_MANIFEST_NAME).closed_utc is None:
                return False
        except (OSError, ValueError):
            return False
    return True


def pilot_progress(root: Path) -> str:
    dirs = bank_dirs(root)
    built = sum(1 for d in dirs if BankLayout(d).manifest.is_file())
    slots = 0
    for d in dirs:
        layout = BankLayout(d)
        for attempt in layout.attempts():
            with contextlib.suppress(OSError):
                slots += layout.slots(attempt).read_bytes().count(b"\n")
    return f"pilot root {root.name}: {built} of {len(dirs)} started banks built; {slots} slots"


def _model_of(path: Path) -> str | None:
    try:
        return GenerationConfig.read(path).model.model_id
    except (OSError, ValueError):
        return None


def _pilot_run_args(stage: PilotStage, st: StageState, root: Path, url: str) -> list[str]:
    args = [
        "run",
        "--units", str(stage.units),
        "--run-id", stage.run_id_for(st.major),
        "--root", str(root),
        "--generation-config", str(stage.generation_config),
        "--meanings", str(stage.meanings),
        "--prompts", str(stage.prompts),
        "--decoding-schema", str(stage.decoding_schema),
        "--llm-url", url,
        "--workers", str(stage.workers),
        "--parallel-banks", str(stage.parallel_banks),
        "--dyads", str(stage.dyads),
        "--spares", str(stage.spares),
        "--major", str(st.major),
        "--breaker", str(stage.breaker),
    ]  # fmt: skip
    if stage.demo:
        args.append("--demo")
    if stage.project_banks is not None:
        args += ["--project-banks", str(stage.project_banks)]
    return args


def drive_pilot(s: Session, stage: PilotStage, st: StageState) -> None:
    """Run the pilot stage to its end (module docstring); `StopForPerson` otherwise."""
    model = s.job.model.served_model or _model_of(stage.generation_config)
    while True:
        s.check_interrupt()
        root = stage.root_for(st.major)
        if s.rel(root) not in st.roots:
            st.roots = (*st.roots, s.rel(root))
            s.save()
        step = st.step
        if step in ("start", "run"):
            st.step = _pilot_run(s, stage, st, root, model)
        elif step == "crashed-finish":
            if not _archived(root):
                result = s.run_child(st, step, PILOT, ["finish", "--root", str(root)])
                if result.exit_code not in (0, 1, 3):
                    raise StopForPerson(
                        f"pilot finish on the crashed root {s.rel(root)} failed "
                        f"(exit {result.exit_code}): {result.stderr_tail(300)}",
                        f"{_see(s, result.stdout)}; repair the root by hand, then run --retry",
                    )
            st.step = "crashed-check"
        elif step == "crashed-check":
            _pilot_check(s, st, root, verify=False, crashed=True)
            st.step = "crashed-archive"
        elif step == "crashed-archive":
            if not _archived(root):
                _pilot_archive(s, st, root)
            st.crashed_roots = (*st.crashed_roots, s.rel(root))
            st.restarts += 1
            st.major += 1
            st.step = "run"
            s.log("pilot_restart", stage=st.name, crashed_root=s.rel(root), major=st.major,
                  restarts=st.restarts)  # fmt: skip
        elif step == "verify":
            _pilot_check(s, st, root, verify=True, crashed=False)
            st.step = "archive"
        elif step == "archive":
            if not _archived(root):
                _pilot_archive(s, st, root)
            st.step = "final-check"
        elif step == "final-check":
            _pilot_check(s, st, root, verify=False, crashed=False)
            st.summary = pilot_summary(s, stage, st)
            st.step = "done"
            s.save()
            return
        else:
            raise StopForPerson(f"unknown pilot step {step!r} in state.json", "restore the state")
        s.save()


def _pilot_run(s: Session, stage: PilotStage, st: StageState, root: Path, model: str | None) -> str:
    """The `run` step: returns the next step, or raises `StopForPerson`."""
    if _archived(root):
        return "final-check"
    code: int | None = None
    record = s.state.child
    seen = (
        record is not None
        and record.stage == st.name
        and record.step == "run"
        and record.exit_code is not None
        and any(a == str(root) for a in record.command)
    )
    if seen and record is not None:
        code = record.exit_code
    elif _nonempty(root):
        s.require_children_dead(st, f"the pilot root {s.rel(root)} holds files")
        if pilot_finished(root):
            result = s.run_child(st, "run-finish", PILOT, ["finish", "--root", str(root)])
            code = result.exit_code
        else:
            code = None  # interrupted: a crash
    else:
        if st.restarts > stage.max_restarts:
            raise StopForPerson(
                f"the pilot run crashed {st.restarts} times (cap {stage.max_restarts}); "
                f"crashed roots, archived as records: {list(st.crashed_roots)}",
                "find the cause in the crashed roots' logs; raise max_restarts in the job "
                "file if a new run is right, then run --retry --allow-job-change",
            )
        s.wait_for_model(st, model)
        result = s.run_child(
            st, "run", PILOT, _pilot_run_args(stage, st, root, s.job.model.url),
            progress=lambda: pilot_progress(root),
        )  # fmt: skip
        code = result.exit_code
        if code == 2 and not _nonempty(root):
            raise StopForPerson(
                f"pilot run {stage.run_id_for(st.major)} was refused: {result.stderr_tail(400)}",
                f"{_see(s, result.stdout)}; correct the inputs, then run --retry",
            )
    run_id = stage.run_id_for(st.major)
    if code == 0:
        return "verify"
    if code == 3:
        if stage.on_shortfall == "archive":
            s.log("pilot_shortfall_archived", stage=st.name, run_id=run_id)
            return "verify"
        raise StopForPerson(
            f"pilot run {run_id} ended with a shortfall (dyad slots without a usable bank; "
            f"see {s.rel(root)}/throughput.md)",
            "raise the shortfall before O6.3.2; to archive this root and go on, set "
            "on_shortfall to archive and run --retry --allow-job-change",
        )
    if code == 1:
        raise StopForPerson(
            f"pilot run {run_id}: a verify or plan problem (exit 1)",
            f"read {s.rel(root)}/verify/verify-log.txt and run `python -m av_banks.pilot "
            f"check --root {root} --verify`; run --retry after the cause is fixed",
        )
    if not _nonempty(root):
        s.log("pilot_run_restart_empty", stage=st.name, run_id=run_id, exit_code=code)
        return "run"
    s.log("pilot_crashed", stage=st.name, run_id=run_id, root=s.rel(root), exit_code=code)
    return "crashed-finish"


def _pilot_check(s: Session, st: StageState, root: Path, *, verify: bool, crashed: bool) -> None:
    args = ["check", "--root", str(root)] + (["--verify"] if verify else [])
    result = s.run_child(st, st.step, PILOT, args)
    if result.exit_code != 0:
        what = "crashed root" if crashed else "root"
        raise StopForPerson(
            f"pilot check of the {what} {s.rel(root)} found problems (exit {result.exit_code})",
            f"{_see(s, result.stdout)}; resolve them by hand, then run --retry",
        )


def _pilot_archive(s: Session, st: StageState, root: Path) -> None:
    result = s.run_child(st, st.step, PILOT, ["archive", "--root", str(root)])
    if result.exit_code != 0:
        raise StopForPerson(
            f"pilot archive of {s.rel(root)} was refused (exit {result.exit_code}): "
            f"{result.stderr_tail(300)}",
            f"{_see(s, result.stdout)}; resolve it by hand, then run --retry",
        )


def pilot_summary(s: Session, stage: PilotStage, st: StageState) -> dict[str, Any]:
    root = stage.root_for(st.major)
    summary: dict[str, Any] = {
        "root": s.rel(root),
        "run_id": stage.run_id_for(st.major),
        "major": st.major,
        "restarts": st.restarts,
        "crashed_roots": list(st.crashed_roots),
    }
    try:
        doc = read_json(root / SUMMARY_JSON_NAME)
        thr = doc["throughput"]
        summary.update(
            register_sha256=doc["register_sha256"],
            usable_slots=doc["usable_slots"],
            dyad_slots=doc["dyad_slots"],
            shortfall=doc["shortfall"],
            spares_used=doc["spares_used"],
            banks_complete=thr.get("banks_complete"),
            banks_unavailable=thr.get("banks_unavailable"),
            slots=thr.get("slots"),
        )
        summary["archive_sha256"] = (root / ARCHIVE_HASH_NAME).read_text("utf-8").split()[0]
    except (OSError, ValueError, KeyError, TypeError, IndexError) as err:
        summary["error"] = f"summary unreadable: {type(err).__name__}"
    return summary


# ---------------------------------------------------------------------------
# Confirmatory


def assert_rebuildable(bank: BankProgress) -> None:
    """A `complete` or `unavailable` bank is final and is never rebuilt (Study B §4)."""
    if bank.state in FINAL:
        raise AssertionError(f"{bank.bank_id} is {bank.state}; it is never rebuilt")
    if bank.state != "crashed":
        raise AssertionError(f"{bank.bank_id} is {bank.state}; only a crashed bank is rebuilt")


def automatic_rebuilds(root: Path) -> dict[str, int]:
    """Rebuilds this supervisor recorded, per bank (from `rebuilds.jsonl`)."""
    counts: dict[str, int] = {}
    for entry in read_rebuilds(root):
        if entry.reason.startswith(REBUILD_MARK):
            counts[entry.bank_id] = counts.get(entry.bank_id, 0) + 1
    return counts


def crash_cause(layout: CampaignLayout, bank: BankProgress) -> str:
    """Why a bank crashed, from the campaign's `events.jsonl`."""
    cause = "the runner stopped during the build (killed, reboot or power loss)"
    try:
        for event in iter_jsonl(layout.events):
            if (
                isinstance(event, dict)
                and event.get("event") == "bank_error"
                and event.get("bank_id") == bank.bank_id
            ):
                cause = str(event.get("error", ""))[:200]
    except (OSError, CodecError):
        pass
    return cause


def _in_scope(stage: ConfirmatoryStage, status: CampaignStatus) -> list[BankProgress]:
    return [b for b in status.banks if stage.only is None or b.bank_id in stage.only]


def _only_args(stage: ConfirmatoryStage) -> list[str]:
    return [a for bank in stage.only or () for a in ("--only", bank)]


def campaign_progress(root: Path) -> str:
    return campaign_status(root, active=True).line()


def drive_confirmatory(s: Session, stage: ConfirmatoryStage, st: StageState) -> None:
    """Run the confirmatory stage to its end (module docstring)."""
    root = stage.campaign_root
    layout = CampaignLayout.at(root)
    if not layout.plan.is_file():
        st.step = "plan"
        _plan(s, stage, st, layout)
    if st.step in ("start", "plan", "build"):
        st.step = "build"
        s.save()
        _build(s, stage, st, layout)
        st.step = "verify"
        s.save()
    if st.step == "verify":
        args = ["verify-all", str(root), "--jobs", str(stage.verify_jobs), *_only_args(stage)]
        result = s.run_child(st, "verify", CONFIRMATORY, args)
        if result.exit_code != 0:
            raise StopForPerson(
                f"verify-all found banks that do not verify (exit {result.exit_code})",
                f"read {s.rel(layout.verification_log)}; a person decides; run --retry to "
                "verify again",
            )
        st.step = "register"
        s.save()
    if st.step == "register":
        if stage.register:
            result = s.run_child(st, "register", CONFIRMATORY, ["register", str(root)])
            if result.exit_code == 3:
                raise StopForPerson(
                    "the register needs an advisor escalation (fewer complete banks than "
                    "the minimum)",
                    "escalate to the advisor, record it with `python -m av_banks.confirmatory "
                    f"escalate {root} --reference URL --date YYYY-MM-DD`, then run --retry",
                )
            if result.exit_code != 0:
                raise StopForPerson(
                    f"register refused or blocked (exit {result.exit_code}): "
                    f"{result.stderr_tail(300)}",
                    f"read {s.rel(layout.g5b_report)} and {_see(s, result.stdout)}; then "
                    "run --retry",
                )
        else:
            s.log("register_skipped", stage=st.name, why="the stage covers part of the campaign")
        st.summary = campaign_summary(s, stage, st)
        st.step = "done"
        s.save()


def _plan(s: Session, stage: ConfirmatoryStage, st: StageState, layout: CampaignLayout) -> None:
    spec = stage.plan
    if spec is None:
        raise StopForPerson(
            f"the campaign {s.rel(layout.root)} has no plan.json",
            "plan it by hand (`python -m av_banks.confirmatory plan ...`, which checks the "
            "G4 freeze), or give the plan arguments in the job file; then run --retry",
        )
    args = [
        "plan",
        "--campaign-root", str(layout.root),
        "--campaign-id", spec.campaign_id,
        "--generation-config", str(spec.generation_config),
        "--freeze-manifest", str(spec.freeze_manifest),
        "--units", str(spec.units),
        "--parallel-banks", str(spec.parallel_banks),
    ]  # fmt: skip
    if spec.repo is not None:
        args += ["--repo", str(spec.repo)]
    if spec.freeze_repo_path is not None:
        args += ["--freeze-repo-path", spec.freeze_repo_path]
    pilots: list[Path] = list(spec.pilot)
    for name in spec.pilot_stages:
        index = [x.name for x in s.state.stages].index(name)
        pilots += [_job_path(s, r) for r in s.state.stages[index].roots]
    for path in pilots:
        args += ["--pilot", str(path)]
    for namespace in spec.pilot_namespaces:
        args += ["--pilot-namespace", namespace]
    result = s.run_child(st, "plan", CONFIRMATORY, args)
    if result.exit_code != 0:
        raise StopForPerson(
            f"confirmatory plan was refused (exit {result.exit_code}): {result.stderr_tail(400)}",
            f"{_see(s, result.stdout)}; a confirmatory plan needs the signed G4 freeze "
            "manifest and tag (#25); correct the inputs, then run --retry",
        )


def _job_path(s: Session, rel: str) -> Path:
    path = Path(rel)
    return path if path.is_absolute() else s.job.root / path


def _runner_lock_is_ours(s: Session, st: StageState, layout: CampaignLayout) -> bool:
    """True when `runner.lock` exists and a runner of this job left it (module
    docstring); `StopForPerson` when it exists and that cannot be shown."""
    if not layout.lock.exists():
        return False
    taken: datetime | None
    try:
        taken = _iso(layout.lock.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        taken = None
    started = _iso(st.run_started_utc) if st.run_started_utc else None
    if (
        s.children_proven_dead()
        and taken is not None
        and started is not None
        and taken >= started - LOCK_SLACK
    ):
        s.log("runner_lock_stale", stage=st.name, taken_utc=utc_text(taken),
              run_started_utc=st.run_started_utc,
              proof="the job lock is free of children: every runner this job started "
              "has exited, and the lock was taken after the last one started")  # fmt: skip
        return True
    raise StopForPerson(
        f"{s.rel(layout.lock)} exists and was not left by a runner this supervisor started "
        "(or that runner cannot be shown to have exited)",
        "check that no `confirmatory run` is working on this campaign (ps), then run "
        f"`python -m av_banks.confirmatory run {layout.root} ... --break-lock` by hand or "
        "remove the lock; then run --retry",
    )


def _build(s: Session, stage: ConfirmatoryStage, st: StageState, layout: CampaignLayout) -> None:
    root = layout.root
    model: str | None = s.job.model.served_model
    while True:
        s.check_interrupt()
        break_lock = _runner_lock_is_ours(s, st, layout)
        status = campaign_status(root, active=False)
        banks = _in_scope(stage, status)
        crashed = [b for b in banks if b.state == "crashed"]
        pending = [b for b in banks if b.state == "pending"]
        if not (break_lock or crashed or pending):
            return
        model = model or _model_of(layout.generation_config)
        if crashed and not break_lock:
            _rebuild(s, stage, st, layout, crashed, model)
            continue
        s.wait_for_model(st, model)
        before = {b.bank_id: (b.state, b.bank_version) for b in banks}
        args = [
            "run", str(root),
            "--meanings", str(stage.meanings),
            "--prompts", str(stage.prompts),
            "--decoding-schema", str(stage.decoding_schema),
            "--llm-url", s.job.model.url,
            "--llm-runtime", s.job.model.runtime,
            "--parallel-banks", str(stage.parallel_banks),
            "--workers", str(stage.workers),
            "--breaker", str(stage.breaker),
            "--monitor-interval", str(stage.monitor_interval_s),
            *_only_args(stage),
        ]  # fmt: skip
        if break_lock:
            args.append("--break-lock")
        st.runs += 1
        st.run_started_utc = s.now()
        s.save()
        result = s.run_child(st, "build", CONFIRMATORY, args,
                             progress=lambda: campaign_progress(root))  # fmt: skip
        if result.exit_code == 2:
            raise StopForPerson(
                f"confirmatory run was refused (exit 2): {result.stderr_tail(400)}",
                f"{_see(s, result.stdout)}; correct the cause, then run --retry",
            )
        after = {
            b.bank_id: (b.state, b.bank_version)
            for b in _in_scope(stage, campaign_status(root, active=False))
        }
        out = result.stdout_json()
        halted = out.get("halted") if isinstance(out, dict) else None
        stopped = out.get("stopped") if isinstance(out, dict) else None
        s.log("campaign_run_end", stage=st.name, exit_code=result.exit_code, halted=halted,
              stopped=stopped)  # fmt: skip
        if after == before and not break_lock:
            raise StopForPerson(
                f"confirmatory run made no progress (exit {result.exit_code}; "
                f"{stopped or halted or 'no bank changed state'})",
                f"{_see(s, result.stdout)}; correct the cause, then run --retry",
            )


def _rebuild(
    s: Session,
    stage: ConfirmatoryStage,
    st: StageState,
    layout: CampaignLayout,
    crashed: Sequence[BankProgress],
    model: str | None,
) -> None:
    counts = automatic_rebuilds(layout.root)
    over = [b.bank_id for b in crashed if counts.get(b.bank_id, 0) >= stage.max_rebuilds_per_bank]
    if over:
        raise StopForPerson(
            f"crashed banks reached the automatic rebuild cap ({stage.max_rebuilds_per_bank}): "
            f"{over}",
            "find the cause in the crashed runs' logs and events.jsonl; rebuild by hand "
            f"(`python -m av_banks.confirmatory rebuild {layout.root} --bank-id ID --reason "
            "TEXT`) or raise max_rebuilds_per_bank; then run --retry",
        )
    s.wait_for_model(st, model)
    answered = s.now()
    for bank in crashed:
        assert_rebuildable(bank)
        n = counts.get(bank.bank_id, 0) + 1
        reason = (
            f"{REBUILD_MARK} {n} of at most {stage.max_rebuilds_per_bank}, job "
            f"{s.job.job_id}: {bank.bank_id} v{bank.bank_version} crashed in run "
            f"{bank.run_id} ({crash_cause(layout, bank)}); the model server answered again "
            f"at {answered}"
        )
        args = ["rebuild", str(layout.root), "--bank-id", bank.bank_id, "--reason", reason[:400]]
        result = s.run_child(st, "rebuild", CONFIRMATORY, args)
        if result.exit_code != 0:
            raise StopForPerson(
                f"rebuild of {bank.bank_id} was refused (exit {result.exit_code}): "
                f"{result.stderr_tail(300)}",
                f"{_see(s, result.stdout)}; then run --retry",
            )
        st.rebuilds[bank.bank_id] = n
        s.save()
        s.log("bank_rebuilt", stage=st.name, bank_id=bank.bank_id, n=n, reason=reason[:400])


def campaign_summary(s: Session, stage: ConfirmatoryStage, st: StageState) -> dict[str, Any]:
    root = stage.campaign_root
    layout = CampaignLayout.at(root)
    status = campaign_status(root, active=False)
    scope = _in_scope(stage, status)
    summary: dict[str, Any] = {
        "campaign_root": s.rel(root),
        "campaign_id": status.campaign_id,
        "scope": "all" if stage.only is None else list(stage.only),
        "counts": {k: sum(1 for b in scope if b.state == k) for k in sorted(status.counts)},
        "slots_total": status.slots_total,
        "crashed_versions": status.crashed_versions,
        "automatic_rebuilds": automatic_rebuilds(root),
        "runs": st.runs,
        "banks": {
            b.bank_id: {"state": b.state, "version": b.bank_version, "bank_sha256": b.bank_sha256}
            for b in scope
        },
    }
    if stage.register and layout.register_json.is_file():
        try:
            doc = read_json(layout.register_json)
            summary.update(
                decision=doc["decision"],
                register_csv_sha256=doc["hashes"]["register_csv_sha256"],
                archive_sha256=doc["hashes"]["archive_sha256"],
            )
        except (OSError, ValueError, KeyError, TypeError) as err:
            summary["error"] = f"register.json unreadable: {type(err).__name__}"
    return summary


def stage_root(stage: PilotStage | ConfirmatoryStage) -> Path:
    """The directory a stage writes in (pilot: the parent of its roots)."""
    return stage.root if isinstance(stage, PilotStage) else stage.campaign_root
