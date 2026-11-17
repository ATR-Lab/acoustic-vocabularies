"""The unattended supervisor (#267) end to end on CPU: DEMO jobs against the mock model
server (`av_generation.mock_llm.MockLlmServer` on loopback), with the existing pilot and
confirmatory command lines as child processes.

- a clean DEMO job (pilot stage, then a DEMO confirmatory stage) is the reference;
- a model-server outage in the middle of the campaign: the runner halts, the supervisor
  waits, the server comes back, the crashed banks are rebuilt and the job finishes with no
  bank made unavailable by the outage;
- SIGKILL of the supervisor alone (its runner keeps going) and of the whole process group
  (a reboot: a stale `runner.lock`, crashed banks), each followed by a restart;
- a pilot outage: the pilot run halts (`--breaker`), the crashed root is archived as the
  record and the run starts again with `--major 2`;
- a pilot shortfall stops for a person, and `--retry` goes on once the job allows it;
- the job root is refused inside a git work tree unless git ignores it;
- the full-size rehearsal (`purpose: rehearsal`, last section): a 64 + 8 rehearsal is
  planned (72 DEMO banks) without building them, and once the plan is sized from the unit
  set (#299) so are 84 + 12 and 88 + 12 (96 and 100 banks); a partial rehearsal builds two
  banks, survives SIGKILL of its process group and writes its summary; every refusal.

The kept banks are compared with the reference: the same `complete` and `unavailable`
sets, and the same bank content for every bank that never crashed. Bank content is the
manifest without the attempts' wall times and slot-log hashes: those carry real latencies,
so two real-time runs never share a bank hash (`bank_sha256` covers them).
`DEMO-C002` gets unusable model output (every seed of its namespaces), so it ends
`unavailable` in every run.
"""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import ExitStack
from pathlib import Path

import pytest
from av_generation.constants import PROFILES
from av_generation.jsonio import canonical_sha256, read_json
from av_generation.mock_llm import MockLlmServer, MockReply
from av_generation.seeds import b_seed_key, seed_from_key, wire_seed
from av_generation.webserve import serve_in_thread
from av_sound.grammar import ATOM_IDS

from av_banks.confirmatory.common import CampaignLayout
from av_banks.confirmatory.plan import read_rebuilds
from av_banks.unattended import cli, runtime, stages
from av_banks.unattended import job as jobmod
from av_banks.unattended import rehearsal as rehearsalmod
from av_banks.unattended import state as statemod
from av_banks.unattended.demo import write_demo_job
from av_banks.unattended.supervisor import (
    EXIT_DONE,
    EXIT_REFUSED,
    EXIT_STOPPED,
    check_job,
    run_job,
    status_text,
)

ROOT = Path(__file__).resolve().parents[2]
SCOPE = ["DEMO-C001", "DEMO-C002", "DEMO-C003", "DEMO-C004"]
UNAVAILABLE = "DEMO-C002"
POSIX = pytest.mark.skipif(sys.platform == "win32", reason="SIGKILL and flock (POSIX)")


def _bad_seeds(bank_id, versions=("1.0.0", "1.0.1", "1.0.2", "1.0.3")):
    namespaces = [bank_id] + [f"{bank_id}-v{v}" for v in versions[1:]]
    return {
        wire_seed(seed_from_key(b_seed_key(ns, attempt, profile, atom, slot)))
        for ns in namespaces
        for attempt in range(1, 5)
        for profile in PROFILES
        for atom in ATOM_IDS
        for slot in range(1, 13)
    }


class DemoModel(MockLlmServer):
    """The mock server; requests seeded from `bad` namespaces get unusable text."""

    def __init__(self, bad=frozenset()):
        super().__init__()
        self.bad = bad

    def _answer(self, body, reply, prompt_tokens, max_out):
        if body.get("seed") in self.bad:
            reply = MockReply(kind="text", text="DEMO: unusable output")
        return super()._answer(body, reply, prompt_tokens, max_out)


class ModelServer:
    """The mock on a fixed loopback port that a test can stop and start again."""

    def __init__(self, bad=frozenset()):
        self.mock = DemoModel(bad)
        self.port = 0
        self.url = None
        self._stack = None

    def start(self):
        self._stack = ExitStack()
        self.url = self._stack.enter_context(serve_in_thread(self.mock.app, port=self.port))
        self.port = int(self.url.rsplit(":", 1)[1])
        return self.url

    def stop(self):
        self._stack.close()
        self._stack = None

    def chat_calls(self):
        return len(self.mock.calls)


@pytest.fixture(scope="module")
def bad_seeds():
    return frozenset(_bad_seeds(UNAVAILABLE) | _bad_seeds("DEMO-bank-P002", ("2.0.0",)))


@pytest.fixture
def model(bad_seeds):
    server = ModelServer(bad_seeds)
    server.start()
    yield server
    if server._stack is not None:
        server.stop()


def make_job(out, url, *, pilot=True, confirmatory=True, dyads=2, spares=1, **overrides):
    """A DEMO job (`demo.write_demo_job`) tuned for tests: fast polls and heartbeats,
    workers=1 (deterministic banks), the 4-bank confirmatory scope."""
    path = write_demo_job(out, llm_url=url, dyads=dyads, spares=spares, banks=4)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["heartbeat_s"] = 0.5
    doc["lock_wait_s"] = 120
    doc["model"].update(max_wait_s=120, poll_initial_s=0.2, poll_max_s=1.0, probe_timeout_s=2)
    pilot_stage, conf_stage = doc["stages"]
    pilot_stage.update(workers=1, parallel_banks=2)
    conf_stage.update(workers=1, parallel_banks=4, monitor_interval_s=1, verify_jobs=2)
    conf_stage["only"] = SCOPE
    if not pilot:
        conf_stage["plan"].pop("pilot_stages")
        conf_stage["plan"]["pilot_namespaces"] = [f"DEMO-P{n:03d}" for n in range(1, 9)]
    doc["stages"] = [s for s, keep in ((pilot_stage, pilot), (conf_stage, confirmatory)) if keep]
    for key, value in overrides.items():
        doc[key] = value
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


def job_root(path):
    return path.parent / "job"


def state_of(path):
    return statemod.read_state(job_root(path) / statemod.STATE_NAME)


def events(path, name=None):
    log = job_root(path) / statemod.LOG_NAME
    lines = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    return [e for e in lines if name is None or e["event"] == name]


def bank_content(manifest_path):
    """The bank manifest without real-time fields (module docstring)."""
    doc = read_json(manifest_path)
    for attempt in doc["attempts"]:
        attempt.pop("wall_ms")
        attempt.pop("slots_sha256")
    return canonical_sha256(doc)


def campaign_banks(path):
    """bank ID -> (status, version, content) of the kept (latest) version of each bank."""
    root = job_root(path) / "confirmatory"
    layout = CampaignLayout.at(root)
    kept = {}
    for bank_dir in sorted((root / "runs").glob("*/banks/*")):
        manifest = bank_dir / "manifest.json"
        if not manifest.is_file():
            continue
        doc = read_json(manifest)
        key = doc["bank_id"]
        if key not in kept or doc["bank_version"] > kept[key][1]:
            kept[key] = (doc["status"], doc["bank_version"], bank_content(manifest))
    assert layout.plan.is_file()
    return kept


def crashed_ids(path):
    return {r.bank_id for r in read_rebuilds(job_root(path) / "confirmatory")}


def compare_with_reference(found, reference):
    assert set(found) == set(reference) == set(SCOPE)
    assert {b for b, v in found.items() if v[0] == "unavailable"} == {UNAVAILABLE}
    assert {b for b, v in found.items() if v[0] == "complete"} == set(SCOPE) - {UNAVAILABLE}


@pytest.fixture(autouse=True)
def _keep_umask():
    old = os.umask(0o022)
    os.umask(old)
    yield
    os.umask(old)


@pytest.fixture(scope="module")
def reference(tmp_path_factory, bad_seeds):
    """The clean DEMO job: pilot then confirmatory, no interruption (in-process)."""
    server = ModelServer(bad_seeds)
    server.start()
    try:
        path = make_job(tmp_path_factory.mktemp("reference"), server.url)
        start = time.monotonic()
        code = run_job(path, echo=False)
        wall = time.monotonic() - start
    finally:
        server.stop()
    return {"path": path, "code": code, "wall": wall, "banks": campaign_banks(path)}


# -- the clean job --------------------------------------------------------------


def test_clean_demo_job_finishes_and_reports(reference):
    path = reference["path"]
    assert reference["code"] == EXIT_DONE
    state = state_of(path)
    assert state.status == "done" and state.stop is None
    pilot, conf = state.stages
    assert pilot.status == conf.status == "done"
    assert pilot.summary["usable_slots"] == 2 and pilot.summary["shortfall"] == []
    assert pilot.summary["archive_sha256"] and pilot.restarts == 0
    assert conf.summary["counts"]["complete"] == 3
    assert conf.summary["counts"]["unavailable"] == 1
    assert conf.summary["automatic_rebuilds"] == {}
    summary = read_json(job_root(path) / statemod.SUMMARY_NAME)
    assert summary["status"] == "done" and summary["model_waits"]["count"] == 0
    assert [s["name"] for s in summary["stages"]] == ["pilot", "confirmatory"]
    # the plan named the pilot stage's root
    plan_child = [e for e in events(path, "child_start") if e["step"] == "plan"]
    assert len(plan_child) == 1
    plan_out = job_root(path) / "logs" / "0005-confirmatory-plan.out"
    # DEMO-bank-P001, DEMO-bank-P002 (unavailable) and its spare DEMO-bank-P002-v1.1.0
    assert json.loads(plan_out.read_text(encoding="utf-8"))["pilot_namespaces"] == 3
    text = status_text(path)
    assert "job DEMO-unattended-01: done" in text and "summary.json" in text
    # a finished job is not run again
    assert run_job(path, echo=False) == EXIT_DONE
    assert state_of(path).sessions == 1


@POSIX
def test_files_are_private_to_the_user(reference):
    root = job_root(reference["path"])
    assert (root.stat().st_mode & 0o777) == 0o700
    assert (root / "logs").stat().st_mode & 0o077 == 0
    for name in (statemod.STATE_NAME, statemod.LOG_NAME, statemod.SUMMARY_NAME):
        assert (root / name).stat().st_mode & 0o077 == 0
    campaign = root / "confirmatory" / "plan.json"
    assert campaign.stat().st_mode & 0o077 == 0  # written by a child under umask 077


def test_status_and_cli(reference, capsys):
    path = reference["path"]
    assert cli.main(["status", str(path)]) == 0
    assert "[2/2] confirmatory (confirmatory): done" in capsys.readouterr().out
    assert cli.main(["status", str(job_root(path)), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "done"
    assert cli.main(["check", str(path), "--no-probe"]) == 0
    assert capsys.readouterr().out.strip().endswith("OK")


# -- model-server outage --------------------------------------------------------


def _wait_for(predicate, timeout=120, what="condition"):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.05)


def _outage(model, path, *, after_calls, step):
    """Stop the server after `after_calls` chat calls during `step`, start it again once
    the supervisor waits for it."""

    def run():
        def in_step():
            state = state_of(path)
            child = state.child if state else None
            return child is not None and child.step == step and model.chat_calls() >= after_calls

        _wait_for(in_step, what=f"{after_calls} calls in {step}")
        model.stop()
        _wait_for(lambda: state_of(path).status == "waiting", what="the model wait")
        time.sleep(0.5)
        model.start()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def test_outage_halts_waits_resumes_and_makes_no_bank_unavailable(tmp_path, model, reference):
    path = make_job(tmp_path, model.url, pilot=False)
    base = model.chat_calls()
    thread = _outage(model, path, after_calls=base + 150, step="build")
    assert run_job(path, echo=False) == EXIT_DONE
    thread.join(5)
    state = state_of(path)
    assert state.waits.count >= 1 and state.waits.seconds > 0
    assert events(path, "model_wait_start") and events(path, "model_wait_end")
    layout = CampaignLayout.at(job_root(path) / "confirmatory")
    runner_events = [json.loads(x) for x in layout.events.read_text().splitlines()]
    assert any(e["event"] == "runner_end" and e["halted"] for e in runner_events)
    rebuilds = read_rebuilds(layout.root)
    assert rebuilds, "the halted banks were rebuilt"
    assert all(r.reason.startswith(stages.REBUILD_MARK) for r in rebuilds)
    assert all("answered again" in r.reason for r in rebuilds)
    found = campaign_banks(path)
    compare_with_reference(found, reference["banks"])
    for bank in set(SCOPE) - crashed_ids(path):
        assert found[bank] == reference["banks"][bank], bank
    for bank in crashed_ids(path):
        assert found[bank][1] != "1.0.0"
    assert state.stages[0].summary["automatic_rebuilds"] == {
        b: sum(1 for r in rebuilds if r.bank_id == b) for b in crashed_ids(path)
    }


def test_pilot_outage_archives_the_crashed_run_and_restarts(tmp_path, model):
    path = make_job(tmp_path, model.url, confirmatory=False, spares=0)
    thread = _outage(model, path, after_calls=model.chat_calls() + 120, step="run")
    assert run_job(path, echo=False) == EXIT_DONE
    thread.join(5)
    st = state_of(path).stages[0]
    assert st.restarts == 1 and st.major == 2
    assert st.crashed_roots == ("pilot/DEMO-unattended-01-P",)
    crashed = job_root(path) / "pilot" / "DEMO-unattended-01-P"
    assert (crashed / "archive-sha256.txt").is_file()
    final = job_root(path) / "pilot" / "DEMO-unattended-01-P-M2"
    rows = (final / "pilot-register.csv").read_text(encoding="utf-8").splitlines()[1:]
    assert len(rows) == 2 and all(",2.0.0," in r for r in rows)
    assert st.summary["root"] == "pilot/DEMO-unattended-01-P-M2"
    assert st.summary["banks_unavailable"] == 0
    assert any(e["event"] == "pilot_crashed" for e in events(path))


# -- kill and resume ------------------------------------------------------------


def _start(path, *args):
    return subprocess.Popen(
        [sys.executable, "-m", "av_banks.unattended", "run", str(path), "--quiet", *args],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def _kill_mid_build(model, path, proc, *, group):
    base = model.chat_calls()

    def mid_build():
        state = state_of(path)
        child = state.child if state else None
        return child is not None and child.step == "build" and model.chat_calls() >= base + 120

    _wait_for(mid_build, what="the campaign run")
    if group:
        os.killpg(proc.pid, signal.SIGKILL)
    else:
        os.kill(proc.pid, signal.SIGKILL)
    proc.wait(30)
    return state_of(path).child


@POSIX
def test_sigkill_of_the_supervisor_resumes_with_the_same_banks(tmp_path, model, reference):
    path = make_job(tmp_path, model.url, pilot=False)
    child = _kill_mid_build(model, path, _start(path), group=False)
    assert child.exit_code is None and runtime.pid_alive(child.pid)  # the runner goes on
    again = _start(path)
    _, err = again.communicate(timeout=300)
    assert again.returncode == EXIT_DONE, err.decode()
    state = state_of(path)
    assert state.sessions == 2
    assert any(e["event"] == "child_unseen" for e in events(path))
    assert events(path, "lock_wait")  # the restart waited for the orphaned runner
    assert crashed_ids(path) == set()
    assert campaign_banks(path) == reference["banks"]


@POSIX
def test_reboot_breaks_the_stale_lock_rebuilds_and_finishes(tmp_path, model, reference):
    path = make_job(tmp_path, model.url, pilot=False)
    _kill_mid_build(model, path, _start(path), group=True)
    lock = job_root(path) / "confirmatory" / "runner.lock"
    assert lock.exists()  # left by the killed runner
    again = _start(path)
    _, err = again.communicate(timeout=300)
    assert again.returncode == EXIT_DONE, err.decode()
    assert not lock.exists()
    assert events(path, "runner_lock_stale")
    found = campaign_banks(path)
    compare_with_reference(found, reference["banks"])
    crashed = crashed_ids(path)
    assert crashed, "the killed banks were rebuilt"
    for bank in set(SCOPE) - crashed:
        assert found[bank] == reference["banks"][bank], bank


# -- stops for a person ---------------------------------------------------------


def test_pilot_shortfall_stops_for_a_person_and_retry_goes_on(tmp_path, model):
    path = make_job(tmp_path, model.url, confirmatory=False, spares=0)
    assert run_job(path, echo=False) == EXIT_STOPPED
    state = state_of(path)
    assert state.status == "stopped" and "shortfall" in state.stop.reason
    assert state.stop.stage == "pilot" and "on_shortfall" in state.stop.action
    assert "STOPPED for a person" in status_text(path)
    assert run_job(path, echo=False) == EXIT_STOPPED  # stays stopped without --retry
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["stages"][0]["on_shortfall"] = "archive"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(jobmod.SupervisorError, match="job file changed"):
        run_job(path, echo=False, retry=True)
    assert cli.main(["retry", str(path)]) == EXIT_REFUSED  # the job file changed
    assert cli.main(["retry", str(path), "--allow-job-change"]) == 0
    assert state_of(path).status == "ready" and state_of(path).stop is None
    assert cli.main(["retry", str(path)]) == 0  # nothing to retry any more
    assert run_job(path, echo=False) == EXIT_DONE
    st = state_of(path).stages[0]
    assert st.summary["shortfall"] == ["B-P02"] and st.restarts == 0
    names = [e["event"] for e in events(path)]
    assert "retry" in names and "job_changed" in names and "pilot_shortfall_archived" in names


def test_an_unplanned_campaign_and_a_down_server_stop_for_a_person(tmp_path, model):
    path = make_job(tmp_path, model.url, pilot=False)
    doc = json.loads(path.read_text(encoding="utf-8"))
    plan = doc["stages"][0].pop("plan")
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert run_job(path, echo=False) == EXIT_STOPPED
    assert "no plan.json" in state_of(path).stop.reason
    doc["stages"][0]["plan"] = plan
    doc["model"]["max_wait_s"] = 0.5
    path.write_text(json.dumps(doc), encoding="utf-8")
    model.stop()
    code = run_job(path, echo=False, retry=True, allow_job_change=True)
    assert code == EXIT_STOPPED
    state = state_of(path)
    assert "did not answer" in state.stop.reason and state.waits.count == 1
    assert state.stages[0].step == "build" and state.stages[0].runs == 0


# -- data policy and the job file -----------------------------------------------


def test_a_job_root_inside_a_git_work_tree_is_refused_unless_ignored(tmp_path, model):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    path = make_job(repo / "work", model.url, pilot=False)
    with pytest.raises(jobmod.SupervisorError, match="E_POLICY"):
        run_job(path, echo=False)
    assert not job_root(path).exists()
    assert cli.main(["run", str(path), "--quiet"]) == EXIT_REFUSED
    problems, _ = check_job(path, probe=False)
    assert any("not ignored by git" in p for p in problems)
    (repo / ".gitignore").write_text("work/job/\n", encoding="utf-8")
    problems, notes = check_job(path, probe=True)
    assert problems == [] and any("ignored by git" in n for n in notes)
    assert "model server: answers" in notes


def test_job_file_rules(tmp_path):
    path = make_job(tmp_path, "http://127.0.0.1:9")
    doc = json.loads(path.read_text(encoding="utf-8"))
    bad = dict(doc, stages=[dict(doc["stages"][1], register=True)])
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(jobmod.SupervisorError, match="register"):
        jobmod.load_job(path)
    bad = dict(doc, stages=[doc["stages"][1], doc["stages"][0]])
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(jobmod.SupervisorError, match="do not come before"):
        jobmod.load_job(path)
    bad = dict(doc, stages=[dict(doc["stages"][0], run_id="x" * 57)])
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(jobmod.SupervisorError, match="run ID"):
        jobmod.load_job(path)
    bad = dict(doc, model={"url": "ftp://nowhere"})
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(jobmod.SupervisorError, match="not valid"):
        jobmod.load_job(path)
    path.write_text("{", encoding="utf-8")
    with pytest.raises(jobmod.SupervisorError, match="cannot read"):
        jobmod.load_job(path)
    assert cli.main(["check", str(path)]) == EXIT_REFUSED


def test_the_committed_example_jobs_are_valid_and_hold_placeholders_only():
    examples = sorted((ROOT / "banks" / "examples" / "unattended").glob("*.example.json"))
    names = [p.name for p in examples]
    assert names == ["confirmatory-job.example.json", "job.example.json", "pilot-job.example.json"]
    for example in examples:
        doc = read_json(example)
        assert jobmod.job_errors(doc) == [], example.name
        text = example.read_text(encoding="utf-8")
        assert "<restricted>" in text and "<llm-host>" in text
        assert "/Users/" not in text and "/home/" not in text
        assert doc.get("purpose", "banks") == "banks"


def test_model_probe_and_url_redaction(model):
    assert runtime.probe_model(model.url, None, timeout_s=2) is None
    assert runtime.probe_model(model.url, model.mock.model, timeout_s=2) is None
    assert "does not list" in runtime.probe_model(model.url, "other/model", timeout_s=2)
    assert runtime.probe_model("http://127.0.0.1:9", None, timeout_s=1) is not None
    assert jobmod.redact_url("http://user:secret@host:8000/v1") == "http://host:8000/v1"
    assert jobmod.redact_url("http://host:8000") == "http://host:8000"
    step = runtime.backoff(1, 5)
    assert [step() for _ in range(5)] == [1, 2, 4, 5, 5]


def test_final_banks_are_never_rebuilt():
    from av_banks.confirmatory.runner import BankProgress

    for state in ("complete", "unavailable", "pending"):
        bank = BankProgress("DEMO-C001", "1.0.0", "r", state, 1, 1, 1, None)
        with pytest.raises(AssertionError):
            stages.assert_rebuildable(bank)
    stages.assert_rebuildable(BankProgress("DEMO-C001", "1.0.0", "r", "crashed", 1, 1, 1, None))


def test_state_file_is_written_atomically(tmp_path):
    path = tmp_path / statemod.STATE_NAME
    assert statemod.read_state(path) is None
    state = statemod.JobState.new("DEMO-x1", "0" * 64, [("pilot", "pilot")])
    statemod.write_state(path, state)
    assert statemod.read_state(path) == state
    assert [p.name for p in tmp_path.iterdir()] == [statemod.STATE_NAME]
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(jobmod.SupervisorError, match="unreadable"):
        statemod.read_state(path)
    (tmp_path / "empty").mkdir()
    assert status_text(tmp_path / "empty").endswith("not started\n")


@POSIX
def test_a_second_supervisor_is_refused(tmp_path):
    lock = runtime.JobLock(tmp_path / statemod.LOCK_NAME)
    assert lock.try_acquire()
    other = runtime.JobLock(tmp_path / statemod.LOCK_NAME)
    with pytest.raises(jobmod.SupervisorError, match="another supervisor"):
        other.acquire(holder_alive=lambda: True, wait_s=10, on_wait=print, sleep=lambda s: False)
    waits = []
    with pytest.raises(jobmod.SupervisorError, match="still held"):
        other.acquire(holder_alive=lambda: False, wait_s=0.2, on_wait=waits.append,
                      sleep=lambda s: False)  # fmt: skip
    assert waits and runtime.lock_free(tmp_path / statemod.LOCK_NAME) is False
    lock.release()
    assert runtime.lock_free(tmp_path / statemod.LOCK_NAME) is True
    assert not runtime.pid_alive(None) and runtime.pid_alive(os.getpid())


def test_demo_job_command(tmp_path, capsys):
    out = tmp_path / "demo"
    assert cli.main(["demo-job", str(out), "--llm-url", "http://127.0.0.1:9"]) == 0
    assert Path(capsys.readouterr().out.strip()) == out.resolve() / "job.json"
    job = jobmod.load_job(out / "job.json")
    assert [s.name for s in job.stages] == ["pilot", "confirmatory"]
    with pytest.raises(ValueError, match="already exists"):
        write_demo_job(out, llm_url="http://127.0.0.1:9")
    with pytest.raises(ValueError, match="DEMO-"):
        write_demo_job(tmp_path / "x", llm_url="http://127.0.0.1:9", job_id="real-01")


def _session(tmp_path, job, codes):
    """A real session on a copy of `job` rooted in `tmp_path`, whose children are faked:
    each step returns the next exit code of `codes[step]`."""
    import dataclasses

    from av_banks.unattended.session import Session

    copy = dataclasses.replace(job, root=tmp_path)
    state = statemod.JobState.new(job.job_id, job.sha256, [(s.name, s.kind) for s in job.stages])
    session = Session(copy, state, statemod.Journal(tmp_path / "log.jsonl", echo=False),
                      lock_fd=None)  # fmt: skip
    calls = []

    def run_child(st, step, module, args, *, progress=None):
        calls.append((step, args[0]))
        out = tmp_path / f"{len(calls)}.out"
        out.write_text("{}", encoding="utf-8")
        return runtime.ChildResult(codes[step].pop(0), out, out.with_suffix(".err"))

    session.run_child = run_child
    return session, calls


def test_verify_and_register_outcomes_of_a_whole_campaign(tmp_path, reference):
    import dataclasses

    from av_banks.unattended.session import StopForPerson

    job = jobmod.load_job(reference["path"])
    stage = dataclasses.replace(job.stage("confirmatory"), only=None, register=True)
    session, calls = _session(tmp_path, job, {"verify": [1, 0], "register": [3, 1, 0]})
    st = session.state.stages[1]
    st.step = "verify"
    with pytest.raises(StopForPerson, match="do not verify"):
        stages.drive_confirmatory(session, stage, st)
    with pytest.raises(StopForPerson, match="escalation") as stop:
        stages.drive_confirmatory(session, stage, st)
    assert "escalate" in stop.value.action
    with pytest.raises(StopForPerson, match="blocked"):
        stages.drive_confirmatory(session, stage, st)
    stages.drive_confirmatory(session, stage, st)
    assert st.step == "done" and st.summary["scope"] == "all"
    assert st.summary["counts"]["pending"] == 68
    assert (
        calls
        == [("verify", "verify-all"), ("verify", "verify-all")] + [("register", "register")] * 3
    )


def test_runner_lock_is_broken_only_when_this_job_left_it(tmp_path, reference):
    from av_banks.unattended.session import StopForPerson

    job = jobmod.load_job(reference["path"])
    session, _ = _session(tmp_path, job, {})
    st = session.state.stages[1]
    layout = CampaignLayout.at(tmp_path / "campaign")
    layout.root.mkdir()
    assert stages._runner_lock_is_ours(session, st, layout) is False  # no lock
    layout.lock.write_text("2026-12-24T10:00:05.000Z\n", encoding="utf-8")
    with pytest.raises(StopForPerson, match="not left by a runner"):
        stages._runner_lock_is_ours(session, st, layout)  # this job never started a run
    st.run_started_utc = "2026-12-24T10:00:04.500Z"
    assert stages._runner_lock_is_ours(session, st, layout) is True
    st.run_started_utc = "2026-12-24T11:00:00.000Z"  # the lock is older than our runner
    with pytest.raises(StopForPerson):
        stages._runner_lock_is_ours(session, st, layout)
    layout.lock.write_text("garbage", encoding="utf-8")
    with pytest.raises(StopForPerson):
        stages._runner_lock_is_ours(session, st, layout)
    st.run_started_utc = "2026-12-24T10:00:04.500Z"
    layout.lock.write_text("2026-12-24T10:00:05.000Z\n", encoding="utf-8")
    session.unseen = statemod.ChildRecord(1, "confirmatory", "build", ("run",), "x", "t")
    expected = runtime.INHERITED_LOCK and session.lock_fd is not None
    assert session.children_proven_dead() is expected
    with pytest.raises(StopForPerson):  # an orphan may still hold it: never broken
        stages._runner_lock_is_ours(session, st, layout)


def test_pilot_breaker_wraps_the_model_proposer():
    from av_banks import pilot
    from av_banks.confirmatory.runner import GuardedProposer

    inner = object()
    guarded = pilot._guarded(lambda layout: inner, 3)(None)
    assert isinstance(guarded, GuardedProposer)
    assert guarded.inner is inner and guarded.breaker.threshold == 3


def test_an_unexpected_error_keeps_the_state_and_exits_1(tmp_path, model, monkeypatch):
    from av_banks.unattended import supervisor

    path = make_job(tmp_path, model.url, pilot=False)

    def broken(*args, **kwargs):
        raise RuntimeError("disk vanished")

    monkeypatch.setattr(supervisor, "drive_confirmatory", broken)
    assert run_job(path, echo=False) == 1
    state = state_of(path)
    assert state.status == "interrupted" and state.stages[0].status == "pending"
    assert events(path, "error")[0]["error"] == "RuntimeError: disk vanished"
    assert "start it again to resume" not in status_text(path)  # not running, not waiting


# -- full-size rehearsal (purpose: rehearsal) -------------------------------------

FULL = jobmod.RehearsalSize(64, 8)
PAIR = ("DEMO-C001", UNAVAILABLE)
DOWN = "http://127.0.0.1:9"


def _supported(size):
    """Whether this checkout plans `size` (64 + 8 only, until the plan is sized from the
    unit set: #299)."""
    return rehearsalmod.size_problem(size) is None


def make_rehearsal(out, url, *, size=FULL, only=None, **model):
    """A rehearsal job (`rehearsal.write_rehearsal_job`) tuned for tests: fast polls and
    heartbeats, workers=1 (deterministic banks); `only` builds part of the campaign."""
    path = rehearsalmod.write_rehearsal_job(out, llm_url=url, size=size, only=only)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["heartbeat_s"] = 0.5
    doc["lock_wait_s"] = 120
    fast = {"max_wait_s": 120, "poll_initial_s": 0.2, "poll_max_s": 1.0, "probe_timeout_s": 2}
    doc["model"].update({**fast, **model})
    doc["stages"][0].update(workers=1, parallel_banks=2, monitor_interval_s=1, verify_jobs=2)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


def campaign_of(path):
    return job_root(path) / rehearsalmod.STAGE_NAME


def plan_only(out, size=FULL):
    """A rehearsal with no model server: the plan child runs, then the supervisor stops
    for a person because the server stays down. Nothing is built."""
    down = {"max_wait_s": 0.2, "poll_initial_s": 0.05, "poll_max_s": 0.1, "probe_timeout_s": 0.5}
    path = make_rehearsal(out, DOWN, size=size, **down)
    assert run_job(path, echo=False) == EXIT_STOPPED
    return path


def test_rehearsal_job_command_writes_a_watermarked_full_size_job(tmp_path, capsys):
    out = tmp_path / "rehearsal" / "DEMO-rehearsal-01"
    assert cli.main(["rehearsal-job", str(out), "--llm-url", DOWN]) == 0
    path = Path(capsys.readouterr().out.strip())
    assert path == out.resolve() / "job.json"
    job = jobmod.load_job(path)
    assert job.purpose == "rehearsal" and job.rehearsal == FULL
    (stage,) = job.stages
    assert stage.name == "demo-campaign" and stage.only is None and stage.register
    assert stage.plan.campaign_id == "DEMO-rehearsal-01-C" and stage.plan.repo is None
    assert stage.parallel_banks == 6 and stage.workers == 3
    assert rehearsalmod.marker_job(out / rehearsalmod.MARKER_NAME) == job.job_id
    units = [p.name for p in sorted((out / "inputs" / "units").iterdir())]
    assert units == list(rehearsalmod.unit_ids(FULL)) and len(units) == 72
    assert read_json(out / "inputs" / "prompts" / "prompt-set.json")["demo"] is True
    assert rehearsalmod.input_problems(job) == []
    assert cli.main(["check", str(path), "--no-probe"]) == 0
    text = capsys.readouterr().out
    assert "REHEARSAL job" in text and text.strip().endswith("OK")
    with pytest.raises(ValueError, match="not empty"):
        rehearsalmod.write_rehearsal_job(out, llm_url=DOWN)
    with pytest.raises(ValueError, match="DEMO-"):
        rehearsalmod.write_rehearsal_job(tmp_path / "a", llm_url=DOWN, job_id="rehearsal-01")
    with pytest.raises(ValueError, match="too long"):
        rehearsalmod.write_rehearsal_job(tmp_path / "b", llm_url=DOWN, job_id="DEMO-" + "x" * 40)
    with pytest.raises(ValueError, match="lies inside"):  # inside another rehearsal
        rehearsalmod.write_rehearsal_job(out / "x", llm_url=DOWN, job_id="DEMO-rehearsal-02")
    big = jobmod.RehearsalSize(84, 12)
    if not _supported(big):
        with pytest.raises(ValueError, match="sized from the unit set"):
            rehearsalmod.write_rehearsal_job(tmp_path / "c", llm_url=DOWN, size=big)
        assert cli.main(["rehearsal-job", str(tmp_path / "c"), "--llm-url", DOWN,
                         "--dyads", "84", "--spares", "12"]) == 1  # fmt: skip


@pytest.mark.parametrize(("dyads", "spares", "banks"), [(64, 8, 72), (84, 12, 96), (88, 12, 100)])
def test_a_full_size_rehearsal_is_planned_without_building_it(tmp_path, dyads, spares, banks):
    size = jobmod.RehearsalSize(dyads, spares)
    if not _supported(size):
        pytest.skip("this checkout plans 64 + 8 banks (sizes from the unit set: #299)")
    path = plan_only(tmp_path, size)
    state = state_of(path)
    assert state.purpose == "rehearsal" and "did not answer" in state.stop.reason
    plan = read_json(campaign_of(path) / "plan.json")
    ids = [b["bank_id"] for b in plan["banks"]]
    assert len(ids) == banks and ids[0] == "DEMO-C001" and ids[-1] == f"DEMO-C{banks:03d}"
    assert sum(b["role"] == "main" for b in plan["banks"]) == dyads
    assert plan["demo"] is True and plan["kind"] == "demo" and plan["freeze"]["status"] == "draft"
    assert all(
        value.startswith("DEMO-")
        for b in plan["banks"]
        for value in (b["bank_id"], b["run_id"], b["seed_namespace"])
    )
    job = jobmod.load_job(path)
    assert rehearsalmod.campaign_problems(job, job.stages[0]) == []  # the plan checks
    assert not list(campaign_of(path).glob("runs/*/banks/*"))  # nothing built
    root = job_root(path)
    assert rehearsalmod.marker_job(root / rehearsalmod.MARKER_NAME) == job.job_id
    assert read_json(root / statemod.SUMMARY_NAME)["purpose"] == "rehearsal"
    assert "REHEARSAL, DEMO banks only" in status_text(path)
    assert check_job(path, probe=False)[0] == []  # its own state and campaign are its own


def test_a_partial_rehearsal_builds_survives_a_reboot_and_reports(tmp_path, model):
    path = make_rehearsal(tmp_path, model.url, only=PAIR)
    if sys.platform == "win32":
        assert run_job(path, echo=False) == EXIT_DONE
    else:  # SIGKILL of the supervisor's process group (a reboot), then the same command
        _kill_mid_build(model, path, _start(path), group=True)
        again = _start(path)
        _, err = again.communicate(timeout=300)
        assert again.returncode == EXIT_DONE, err.decode()
    root = job_root(path)
    doc = read_json(root / rehearsalmod.SUMMARY_JSON)
    assert doc["purpose"] == "rehearsal" and doc["kind"] == "demo"
    assert doc["watermark"] == rehearsalmod.WATERMARK
    assert doc["full_size"] is False and doc["scope"] == list(PAIR)
    assert doc["planned_banks"] == 72 and doc["size"]["banks"] == 72
    assert doc["banks"]["complete"] == 1 and doc["banks"]["unavailable"] == 1
    assert doc["attempts"] >= 5 and doc["slots"] > 0 and doc["slots_per_hour"] > 0
    assert doc["campaign_wall_s"] > 0 and doc["slots_per_hour_wall"] > 0
    assert doc["register"] is None and doc["model_waits"]["count"] == 0
    rebuilt = sorted({r.bank_id for r in read_rebuilds(campaign_of(path))})
    assert doc["rebuilds"]["banks"] == rebuilt
    if sys.platform != "win32":
        assert doc["sessions"] == 2 and rebuilt and doc["rebuilds"]["automatic"] >= 1
    text = (root / rehearsalmod.SUMMARY_MD).read_text(encoding="utf-8")
    assert text.startswith("# REHEARSAL (DEMO)") and rehearsalmod.WATERMARK in text
    assert "PARTIAL" in text and "## Throughput" in text
    assert read_json(root / statemod.SUMMARY_NAME)["purpose"] == "rehearsal"
    assert events(path, "register_skipped")
    # the summary is a function of the files and the state: made again here, it is the same
    from av_generation.jsonio import document_text

    from av_banks.unattended.session import Session

    journal = statemod.Journal(tmp_path / "again.jsonl", echo=False)
    session = Session(jobmod.load_job(path), state_of(path), journal, lock_fd=None)
    assert json.loads(document_text(rehearsalmod.write_rehearsal_summary(session))) == doc
    assert read_json(root / rehearsalmod.SUMMARY_JSON) == doc
    assert (root / rehearsalmod.SUMMARY_MD).read_text(encoding="utf-8") == text


def test_a_rehearsal_refuses_inputs_that_are_not_demo(tmp_path):
    import dataclasses
    import shutil

    from av_generation.genconfig import GenerationConfig
    from av_generation.jsonio import write_document
    from av_generation.prompts import prompt_set_manifest

    path = make_rehearsal(tmp_path, DOWN)
    inputs = tmp_path / "inputs"

    def refused(match):
        with pytest.raises(jobmod.SupervisorError, match="E_REHEARSAL") as err:
            run_job(path, echo=False)
        assert match in str(err.value)
        assert not job_root(path).exists()
        assert any(match in p for p in check_job(path, probe=False)[0])

    def edited(rel, change):
        target = inputs / rel
        before = target.read_bytes()
        doc = json.loads(before)
        change(doc)
        target.write_text(json.dumps(doc), encoding="utf-8")
        return lambda: target.write_bytes(before)

    for rel, change, match in (
        ("freeze-manifest.json", lambda d: d.update(status="frozen"), "frozen or tagged"),
        ("freeze-manifest.json", lambda d: d.update(tag="FREEZE-v1.0"), "frozen or tagged"),
        ("meanings/meanings.json", lambda d: d.update(name="meanings-v1", demo=False),
         "meaning set 'meanings-v1' is not a DEMO set"),
        ("units/B-C07/permutation.json", lambda d: d.update(demo=False), "['B-C07']"),
    ):  # fmt: skip
        restore = edited(rel, change)
        refused(match)
        restore()
    prompts = inputs / "prompts" / "prompt-set.json"
    before = prompts.read_bytes()
    write_document(prompts, prompt_set_manifest(prompts.parent, name="prompts-v1"))
    refused("prompt set 'prompts-v1' is not a DEMO set")
    prompts.write_bytes(before)
    config_path = inputs / "generation-config.json"
    before = config_path.read_bytes()
    config = GenerationConfig.read(config_path)
    real = dataclasses.replace(config, name="real-config", demo=False, llm_manifest_sha256="0" * 64)
    real.write(config_path, exclusive=False)
    refused("generation config 'real-config' is not a DEMO config")
    config_path.write_bytes(before)
    shutil.copytree(inputs / "units" / "B-C64", inputs / "units" / "B-C65")
    refused("not exactly B-C01..B-C64 and B-S01..B-S08")
    shutil.rmtree(inputs / "units" / "B-C65")
    assert check_job(path, probe=False)[0] == []


def test_rehearsal_job_rules(tmp_path):
    path = make_rehearsal(tmp_path, DOWN)
    doc = json.loads(path.read_text(encoding="utf-8"))
    stage = doc["stages"][0]
    plan = stage["plan"]
    pilot = {
        "name": "pilot", "kind": "pilot", "run_id": "DEMO-P", "demo": True,
        "units": "inputs/units", "generation_config": "inputs/generation-config.json",
        "meanings": "inputs/meanings", "prompts": "inputs/prompts",
        "decoding_schema": "inputs/decoding-schema.json",
    }  # fmt: skip
    no_plan = {k: v for k, v in stage.items() if k != "plan"}
    no_size = {k: v for k, v in doc.items() if k != "rehearsal"}
    for bad, match in (
        (dict(doc, job_id="rehearsal-01"), "job ID starts with 'DEMO-'"),
        (dict(doc, stages=[pilot, stage]), "exactly one stage"),
        (dict(doc, stages=[dict(stage, campaign_root="elsewhere")]), "inside the job root"),
        (dict(doc, stages=[dict(stage, plan=dict(plan, campaign_id="C-banks-v1"))]),
         "campaign ID starts"),
        (dict(doc, stages=[dict(stage, plan=dict(plan, repo="."))]), "no repo"),
        (dict(doc, stages=[dict(stage, plan=dict(plan, pilot=["p"]))]), "no pilot root"),
        (dict(doc, stages=[dict(stage, plan=dict(plan, pilot_namespaces=["bank-P001"]))]),
         "namespaces start"),
        (dict(doc, stages=[no_plan]), "give 'plan'"),
        (no_size, "not valid"),  # a rehearsal gives its size
        (dict(doc, purpose="banks"), "not valid"),  # and only a rehearsal does
    ):  # fmt: skip
        path.write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(jobmod.SupervisorError, match=match):
            jobmod.load_job(path)


def test_rehearsal_roots_stay_apart_from_every_other_root(tmp_path):
    path = make_rehearsal(tmp_path / "r", DOWN)
    doc = json.loads(path.read_text(encoding="utf-8"))
    other = tmp_path / "pilot-job" / "job"
    other.mkdir(parents=True)
    (other / statemod.STATE_NAME).write_text("{}", encoding="utf-8")
    path.write_text(json.dumps(dict(doc, root=str(other / "rehearsal"))), encoding="utf-8")
    with pytest.raises(jobmod.SupervisorError, match="an unattended job root"):
        run_job(path, echo=False)
    path.write_text(json.dumps(doc), encoding="utf-8")
    stray = job_root(path) / "old" / "pilot-register.csv"
    stray.parent.mkdir(parents=True)
    stray.write_text("", encoding="utf-8")
    with pytest.raises(jobmod.SupervisorError, match="a pilot root"):
        run_job(path, echo=False)
    stray.unlink()
    stray.parent.rmdir()
    assert rehearsalmod.separation_problems(jobmod.load_job(path)) == []
    # any other job: refused inside or around a rehearsal
    inside = write_demo_job(tmp_path / "r" / "demo", llm_url=DOWN)
    with pytest.raises(jobmod.SupervisorError, match="inside or around a rehearsal"):
        run_job(inside, echo=False)
    assert any("rehearsal" in p for p in check_job(inside, probe=False)[0])
    around = write_demo_job(tmp_path / "d", llm_url=DOWN)
    d = json.loads(around.read_text(encoding="utf-8"))
    around.write_text(json.dumps(dict(d, root=str(tmp_path))), encoding="utf-8")
    with pytest.raises(jobmod.SupervisorError, match="inside or around a rehearsal"):
        run_job(around, echo=False)
    # a job root keeps its purpose
    job = jobmod.load_job(path)
    state = statemod.JobState.new(job.job_id, job.sha256, [(s.name, s.kind) for s in job.stages])
    statemod.write_state(job_root(path) / statemod.STATE_NAME, state)
    with pytest.raises(jobmod.SupervisorError, match="a purpose never changes"):
        run_job(path, echo=False)


def test_campaign_checks_and_the_rehearsal_register(tmp_path):
    import dataclasses

    from av_banks.unattended.session import StopForPerson

    path = plan_only(tmp_path / "planned")
    job = jobmod.load_job(path)
    stage = job.stages[0]
    assert rehearsalmod.campaign_problems(job, stage) == []
    real = dataclasses.replace(job, job_id="closure-banks-01", purpose="banks", rehearsal=None)
    assert "never builds DEMO banks" in rehearsalmod.campaign_problems(real, stage)[0]
    other = dataclasses.replace(stage, plan=dataclasses.replace(stage.plan, campaign_id="C-x1"))
    assert "belongs to campaign" in rehearsalmod.campaign_problems(job, other)[0]
    bigger = dataclasses.replace(job, rehearsal=jobmod.RehearsalSize(84, 12))
    assert "64 main and 8 spare" in rehearsalmod.campaign_problems(bigger, stage)[0]
    missing = dataclasses.replace(stage, campaign_root=tmp_path / "nowhere")
    assert "cannot be read" in rehearsalmod.campaign_problems(job, missing)[0]
    from av_banks.confirmatory.plan import read_plan

    plan = read_plan(campaign_of(path))
    frozen = dataclasses.replace(plan.freeze, status="frozen", tag="FREEZE-v1.0")
    real_plan = dataclasses.replace(plan, demo=False, campaign_id="C-x1", freeze=frozen)
    problems = rehearsalmod._rehearsal_plan_problems(real_plan, FULL)
    assert len(problems) == 3 and "not a DEMO plan" in problems[0] and "C-x1" in problems[1]
    (tmp_path / "s").mkdir()
    session, calls = _session(tmp_path / "s", bigger, {})
    with pytest.raises(StopForPerson, match="does not suit"):
        stages.drive_confirmatory(session, stage, session.state.stages[0])
    assert calls == []
    # an escalation ends a rehearsal's register step: DEMO banks have no advisor
    (tmp_path / "t").mkdir()
    session, calls = _session(tmp_path / "t", job, {"register": [3]})
    st = session.state.stages[0]
    st.step = "register"
    stages.drive_confirmatory(session, stage, st)
    assert st.step == "done" and calls == [("register", "register")]
    log = (tmp_path / "t" / "log.jsonl").read_text(encoding="utf-8")
    assert "rehearsal_escalation_required" in log


def test_run_takes_several_jobs_in_order(tmp_path):
    first = make_job(tmp_path / "a", DOWN, pilot=False)
    second = make_job(tmp_path / "b", DOWN, pilot=False)
    for path in (first, second):
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["stages"][0].pop("plan")  # each stops for a person at once
        path.write_text(json.dumps(doc), encoding="utf-8")
    assert cli.main(["run", str(first), str(second), "--quiet"]) == EXIT_STOPPED
    assert state_of(first).status == "stopped" and not job_root(second).exists()
    state = state_of(first)
    state.status, state.stop = "done", None
    statemod.write_state(job_root(first) / statemod.STATE_NAME, state)
    assert cli.main(["run", str(first), str(second), "--quiet"]) == EXIT_STOPPED
    assert "no plan.json" in state_of(second).stop.reason


def test_the_summary_text_of_a_full_size_rehearsal():
    from av_banks.metrics import summarize_banks

    doc = {
        "job_id": "DEMO-rehearsal-01",
        "campaign_id": "DEMO-rehearsal-01-C",
        "size": {"dyads": 64, "spares": 8, "banks": 72},
        "planned_banks": 72,
        "full_size": True,
        "scope": "all",
        "banks": {"complete": 69, "unavailable": 3, "crashed_versions": 2},
        "attempts": 80,
        "slots": 12_345,
        "slots_per_hour": None,
        "slots_per_hour_run": 1234.5,
        "slots_per_hour_wall": 1000.0,
        "campaign_wall_s": 36_000.0,
        "wall_s": None,
        "model_waits": {"count": 1, "seconds": 300.0, "longest_s": 300.0},
        "rebuilds": {"total": 2, "automatic": 2, "banks": ["DEMO-C004", "DEMO-C010"]},
        "sessions": 3,
        "runs": 4,
        "register": {"decision": "escalation_required", "register_csv_sha256": "ab" * 32},
    }
    text = rehearsalmod.summary_text(doc, summarize_banks([], project_banks=72))
    assert "(72 planned; full size)" in text and "decision `escalation_required`" in text
    assert "1,000.0 over the campaign's wall time; 1,234.5 over the bank runs; n/a" in text
    assert "campaign 10.00 h (36,000 s); job n/a" in text
    assert "## Projection for 72 banks" in text
    assert "none" in rehearsalmod.summary_text(dict(doc, register=None), summarize_banks([]))


def test_a_failing_rehearsal_summary_never_fails_a_finished_job(tmp_path, monkeypatch):
    from av_banks.unattended import supervisor

    path = make_rehearsal(tmp_path, DOWN)
    monkeypatch.setattr(supervisor, "drive_confirmatory", lambda s, stage, st: None)

    def broken(session):
        raise OSError("disk full")

    monkeypatch.setattr(supervisor, "write_rehearsal_summary", broken)
    assert run_job(path, echo=False) == EXIT_DONE
    assert state_of(path).status == "done"
    assert events(path, "rehearsal_summary_error")[0]["error"] == "OSError: disk full"


def test_rehearsal_guards_name_unreadable_inputs_and_nested_rehearsals(tmp_path):
    import shutil

    path = make_rehearsal(tmp_path, DOWN)
    job = jobmod.load_job(path)
    inputs = tmp_path / "inputs"
    (inputs / "meanings" / "meanings.json").unlink()
    (inputs / "generation-config.json").write_text("{", encoding="utf-8")
    (inputs / "freeze-manifest.json").unlink()
    (inputs / "units" / "B-S08" / "permutation.json").unlink()
    problems = rehearsalmod.input_problems(job)
    assert [p.split(":")[0] for p in problems] == [
        "meanings",
        "generation config",
        "freeze manifest",
        "units that are not DEMO stand-ins",
    ]
    assert "B-S08" in problems[-1]
    shutil.copytree(
        ROOT / "generation" / "examples" / "demo-meanings", inputs / "meanings", dirs_exist_ok=True
    )
    (inputs / "prompts" / "prompt-set.json").write_text("{", encoding="utf-8")
    assert rehearsalmod.input_problems(job)[0].startswith("prompts:")
    demo = jobmod.load_job(write_demo_job(tmp_path / "demo", llm_url=DOWN))
    with pytest.raises(jobmod.SupervisorError, match="not a rehearsal job"):
        rehearsalmod.input_problems(demo)
    # a rehearsal marker of another job inside the job root
    nested = job_root(path) / "old"
    nested.mkdir(parents=True)
    rehearsalmod.write_marker(nested, "DEMO-rehearsal-02")
    rehearsalmod.write_marker(nested, "DEMO-rehearsal-03")  # an existing marker is kept
    assert rehearsalmod.marker_job(nested / rehearsalmod.MARKER_NAME) == "DEMO-rehearsal-02"
    assert "another rehearsal" in rehearsalmod.separation_problems(job)[0]
