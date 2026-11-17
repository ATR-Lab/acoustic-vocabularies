# Unattended bank runs on the GPU host

Issue #267 (WBS O4.3.3), package `av_banks.unattended`. The plan has the GPU host build
the Study B pilot banks (#27) and the confirmatory banks (#28) without a person at the
keyboard over the 24 Dec - 1 Jan closure. The pilot and confirmatory runners need a
person after every interruption: a crashed bank waits for `rebuild`, a model-server
outage halts the campaign and a reboot stops everything. The supervisor does those
steps under the existing rules and stops for a person for everything else.

`python -m av_banks.unattended run JOB.json` runs the stages of a job file in order. Each
step is one of the existing command lines (`python -m av_banks.pilot ...`, `python -m
av_banks.confirmatory ...`) in a child process, so a crash in a child never corrupts the
supervisor's state. The supervisor keeps a checkpoint in the job root. If it is killed,
or the host reboots, at any point, the same command resumes: finished stages are
skipped, and the current step re-reads the stage's own files.

Confirmatory mode still refuses to run without the signed G4 freeze manifest and tag
(#25). Before G4, a DEMO rehearsal campaign exercises the confirmatory path: a small one
on the first day (section 2) and, if decision D1 chooses it, a full-size one over the
closure, right after the pilot banks (section 3).

## 1. What the supervisor does on its own

| Situation | What happens (logged in `supervisor.log`) |
| --- | --- |
| Before a step that calls the model | `GET <url>/v1/models` must answer and list the config's model. Otherwise it waits: probes at 5 s, then doubling up to 120 s, for at most 12 h (`model.*` in the job file). Every wait is logged and counted. |
| Confirmatory campaign halted by the breaker (`CampaignHalted`, model outage) or a crashed runner | Each crashed bank gets `rebuild <root> --bank-id ID --reason "unattended supervisor (automatic rebuild) n of at most N ...: <bank> crashed in run <run> (<cause>); the model server answered again at <time>"` after the server answers, at most `max_rebuilds_per_bank` (default 2) times per bank. Then `run` again. The rebuild is the runner's rule: new bank version, new seed namespace, reason in `rebuilds.jsonl`. |
| `runner.lock` left by a killed runner | `run --break-lock`, only when the lock's owner is provably dead: the supervisor holds the job lock (`supervisor.lock`, `flock`), which every child inherits, so no runner this job started is alive; and the lock's time is after the start of the last `run` child of this job. Otherwise it stops. |
| `complete` or `unavailable` bank | Never rebuilt (asserted; the runner refuses it too). |
| Pilot run crashed (killed, reboot, or halted by `--breaker` during an outage) | Section 2 of [`pilot-banks.md`](pilot-banks.md): `finish`, `check` and `archive` keep the crashed root as the record; the run starts again in a new root, run ID `<run_id>-M<k>`, with `--major k`. At most `max_restarts` (default 2) times. |
| The supervisor was killed while a child kept running | The restarted supervisor waits for that child (it holds the job lock) for up to `lock_wait_s` (default 24 h), then goes on from the files. |
| SIGTERM or SIGINT | The child gets SIGTERM, the state is saved as `interrupted`, exit 4. Banks that were being built are crashed and are handled by the rules above on the next start. |

It stops for a person (exit 3; `state.json` keeps the reason and the next action) on a
refused command, a verify or check failure, a pilot shortfall (unless the job says
`on_shortfall: archive`), a register that needs an advisor escalation or is blocked, an
exhausted cap, a confirmatory run that made no progress, a `runner.lock` it cannot prove
stale, and a model server that stays down past `max_wait_s`. It refuses (exit 2) a bad
job file, a job root inside a git work tree that git does not ignore, a changed job file
(unless `--allow-job-change`) and a second supervisor on the same job.

**Pilot breaker.** `pilot run` has `--breaker N` (default 0, off; the supervisor passes
`breaker`, default 3). With it, the pilot uses the confirmatory runner's breaker: a
failed model call that is the `N`-th in a row of its profile stream, or that falls on a
cell's 12th slot, raises `CampaignHalted` before its slot is recorded. An outage
therefore crashes the pilot run instead of failing cells, and no bank becomes
`unavailable` because of it. The crashed run is replaced under the crash rule above.

## 2. Day one: from a healthy model server to archived banks

Run the commands from the checkout on the GPU host. `<restricted>` is restricted
storage outside any git work tree, `<llm-host>:8000` the model server. Its own
bring-up and service are in [`generation/docs/llm.md`](../../generation/docs/llm.md).

```bash
U="uv run --project banks python -m av_banks.unattended"
uv sync --project banks --locked
git status --porcelain                     # empty: no local changes
curl -s http://<llm-host>:8000/v1/models   # lists the served model
```

**1. DEMO check against the real server** (DEMO IDs only, nothing restricted):

```bash
$U demo-job <restricted>/demo-check --llm-url http://<llm-host>:8000 --dyads 2 --banks 3
$U check <restricted>/demo-check/job.json   # OK, "model server: answers"
$U run <restricted>/demo-check/job.json     # exit 0; prints the summary
```

While it runs, stop and start the model server once (`systemctl restart av-llm`): the
supervisor logs `model_wait_start`, waits, rebuilds the halted DEMO banks and finishes.
`$U status <restricted>/demo-check/job.json` from another shell shows the same.

**2. The job file.** Copy [`../examples/unattended/job.example.json`](../examples/unattended/job.example.json)
to `<restricted>/job.json` and replace every placeholder. Before G4, leave out the
confirmatory stage, or keep a DEMO confirmatory stage (as `demo-job` writes it); a real
confirmatory plan is refused without the signed freeze manifest and tag. If the
confirmatory campaign was planned by hand, leave out `plan`. Field defaults are in
[`../schema/unattended-job.schema.json`](../schema/unattended-job.schema.json) and in
`av_banks.unattended.job`.

**3. Check and confirm.**

```bash
$U check <restricted>/job.json             # job file, data policy, inputs, model server
uv run --project banks python -m av_banks.pilot plan --units <restricted>/schedules-out/B-pilot --run-id P-banks-01
```

`pilot plan` prints the builder identity (package versions, source hashes, git commit).
Confirm it, as section 2 of [`pilot-banks.md`](pilot-banks.md) requires.

**4. Start the service.** Fill in [`../ops/av-banks-unattended.service.example`](../ops/av-banks-unattended.service.example) and install it:

```bash
sudo cp banks/ops/av-banks-unattended.service.example /etc/systemd/system/av-banks-unattended.service
sudoedit /etc/systemd/system/av-banks-unattended.service   # replace the placeholders
sudo systemctl daemon-reload
sudo systemctl enable --now av-banks-unattended
journalctl -u av-banks-unattended -f
$U status <restricted>/job.json
```

After a reboot, the service starts again, waits for the model server and resumes.

**5. The banks are archived when** `status` shows `done`: the pilot root has
`archive-sha256.txt`, the confirmatory campaign has `register.json` and
`archive/<campaign>-banks.tar`, and `summary.json` lists them.

## 3. The closure: pilot banks, then a full-size rehearsal (decision D1)

The generation settings are frozen at G4 and registered before any confirmatory bank is
built, so the confirmatory campaign (#28) cannot run over the closure. Decision D1 is
open, and both options run with this package:

- **(a)** Over the closure, the real pilot banks (#27) and then a **full-size rehearsal**
  of the confirmatory campaign: DEMO inputs, the real model server, every bank of the
  chosen size. It measures what the February run needs at the real scale: throughput,
  model-server outages, rebuilds and days of unattended operation.
- **(b)** The real confirmatory banks shortly after the registration, with the
  confirmatory job file below. It is the existing confirmatory stage; nothing in it
  depends on (a).

**The rehearsal job.** `rehearsal-job` writes its DEMO inputs, its marker and its job
file in a folder of its own:

```bash
$U rehearsal-job <restricted>/rehearsal/DEMO-rehearsal-01 --llm-url http://<llm-host>:8000
#   default size --dyads 64 --spares 8 (72 banks); --parallel-banks 6 --workers 3
$U check <restricted>/rehearsal/DEMO-rehearsal-01/job.json   # OK; "REHEARSAL job: ..."
```

```
<restricted>/rehearsal/DEMO-rehearsal-01/
  REHEARSAL                    the watermark (also in job/)
  inputs/                      DEMO meanings, DEMO prompt set, recipe schema, DEMO config,
                               DEMO draft freeze manifest, DEMO units B-C01.., B-S01..
  job.json                     purpose: rehearsal, size, one stage `demo-campaign`
  job/                         the job root (section 7); job/demo-campaign/ is the campaign
```

The stage is the confirmatory stage at full size: the plan of every main and spare
bank, every attempt the builder allows, automatic rebuilds, `verify-all` and the register
(a DEMO register). Its settings mirror the confirmatory stage of the example job (6 banks
in parallel, 3 workers, breaker 3, 2 automatic rebuilds per bank, `verify-all` with 8
jobs); edit `job.json` before the first `run` to try others. The prompt set is the
committed templates under a DEMO name (`DEMO-rehearsal-prompts`), so prompt lengths, and
with them the model's time per slot, are close to those of the real run. Once the plan takes its
size from the unit set (#299), `--dyads 84 --spares 12` (96 banks) and `--dyads 88
--spares 12` (100 banks) work as well; until then any size but 64 + 8 is refused.

**How it is kept apart from confirmatory banks.**

| What | Rule |
| --- | --- |
| IDs | Job `DEMO-rehearsal-01`, campaign `DEMO-rehearsal-01-C`, banks `DEMO-C001`.., run IDs `DEMO-rehearsal-01-C-DEMO-C001`.., seed namespaces `DEMO-C001`..: no `bank-C` or `bank-P` ID. The units keep the dyad-slot names the plan binds banks to (`B-C01`..), and each says `"demo": true`. |
| Watermark | `REHEARSAL` in the rehearsal folder and the job root; `purpose: rehearsal` in the job file, `state.json`, `summary.json` and `rehearsal-summary.json`; `status` prints `REHEARSAL, DEMO banks only`; `plan.json` and `register.json` say `demo: true`. A job root keeps its purpose: a changed job file cannot turn it into a bank job. |
| Inputs | Refused unless the meaning set, the prompt set, the generation config and every unit are DEMO, the units are exactly those of the size, and the freeze manifest is a draft without a tag. The job file may name no repository, no pilot root and no pilot stage. |
| Location | The job root may not lie inside, or hold, another job root (`state.json`), a confirmatory campaign (`plan.json`), a pilot root or a bank run, and its campaign lies inside it. Every other job refuses a root that lies inside or around a `REHEARSAL`. The data-policy check (outside git or ignored; 0700) applies as before. |
| Confirmatory tools | Already refusing, unchanged: `confirmatory plan` refuses a folder that is not empty, a freeze manifest that is not frozen and tagged, and a DEMO config or DEMO units for a real campaign; loading a bank in confirmatory mode (`av_banks.register.open_bank`) refuses `DEMO-` banks. New in the supervisor: a campaign whose `plan.json` names another campaign than the job, or a DEMO campaign in a job whose ID does not start with `DEMO-`, stops for a person. |

Nothing from a rehearsal is published, committed or archived as a confirmatory output,
and no confirmatory job points at it. When a rehearsal register needs an advisor
escalation (fewer complete DEMO banks than main slots), the stage records it
(`rehearsal_escalation_required` in `supervisor.log`, the decision in the summary)
instead of stopping: DEMO banks have no advisor.

**The numbers.** When the job is done, `rehearsal-summary.json` and
`rehearsal-summary.md` in the job root give the banks (complete, unavailable, crashed
versions), attempts, slots, slots per hour (over the campaign's wall time, over the bank
runs and per bank builder), the wall time, the model-server waits, the rebuilds, the
supervisor sessions, the DEMO register decision and the bank metrics of
`av_banks.metrics` with a projection for the planned bank count. They size the February
run in #28 (the expected hours at `parallel_banks`, the disk, the waits to expect).

**Two job files, two roots, one service.**

```
<restricted>/
  pilot-job/job.json, pilot-job/job/          the real pilot (#27):
                                              ../examples/unattended/pilot-job.example.json
  rehearsal/DEMO-rehearsal-01/                the rehearsal (rehearsal-job, above)
  confirmatory-job/job.json, .../job/         February (b):
                                              ../examples/unattended/confirmatory-job.example.json
```

`run` takes several job files and runs them in order; it starts the next only when one
is done. For (a), check both job files, then give the service both, the pilot first:

```bash
$U check <restricted>/pilot-job/job.json
$U check <restricted>/rehearsal/DEMO-rehearsal-01/job.json
# in the unit: ExecStart=<checkout>/banks/.venv/bin/python -m av_banks.unattended run \
#   <restricted>/pilot-job/job.json <restricted>/rehearsal/DEMO-rehearsal-01/job.json
```

After a reboot the service starts the same command: the finished pilot job exits at once
and the rehearsal resumes. If the pilot stops for a person, the rehearsal does not start
until that person has handled it (`retry`, then `systemctl restart`); the two jobs never
share the model server, so the pilot is not slowed and the rehearsal's rate is that of
the campaign alone. `$U status` takes either job file.

**February: the confirmatory job.** For (b), and in February after (a), the
confirmatory job file has one confirmatory stage: the signed freeze manifest and its tag,
the real units, and `plan.pilot` naming every root of the pilot job (the final root and
every crashed one: `stages[0].root` and `crashed_roots` in the pilot job's
`summary.json`). It is section 2 with this job file; the supervisor refuses it if one of
its roots lies inside or around a rehearsal.

## 4. The first hour

- `status` shows `running`, the stage and step, and a heartbeat less than a minute old.
  The heartbeat line counts the pilot banks and slots, or gives the campaign's
  `status` line (banks per state, slots, slots per minute, the running banks).
- The slot count grows. For the confirmatory stage, compare the rate in
  `progress.jsonl` with the pilot rate in the pilot root's `throughput.md`, and check
  that the slot p95 stays under the 40-s cap (section 4 of
  [`confirmatory-banks.md`](confirmatory-banks.md)).
- `supervisor.log` has no `model_wait_start` you did not cause, and no `stopped`.
- `nvidia-smi` shows the model server's memory steady, and `df -h <restricted>` leaves
  room: a complete DEMO bank takes about 13 MB, so 72 banks and their archive take a few
  GB; keep at least 10 GB free.
- `ls -l <restricted>/job` shows `drwx------` and `-rw-------` (the supervisor sets the
  umask to 077 for itself and every child).

## 5. When it stopped

`$U status <restricted>/job.json` prints `STOPPED for a person`, the reason and `next:`,
the action. Do that action with the existing command lines, then:

```bash
$U retry <restricted>/job.json                       # accept the retry (add --allow-job-change
sudo systemctl restart av-banks-unattended           #   after editing the job file), then start
```

Without the service, `$U run <restricted>/job.json --retry` does both in the foreground.
A retry re-reads the stopped step from the files: a pilot step runs `finish` again, a
confirmatory step looks at the campaign again. A changed job file is refused unless
`--allow-job-change` is given; the change is logged. Examples:

- **Pilot shortfall:** raise it before O6.3.2. To archive the root and go on, set
  `on_shortfall` to `archive` and run with `--retry --allow-job-change`.
- **Escalation required:** escalate to the advisor, record it with
  `python -m av_banks.confirmatory escalate R --reference URL --date YYYY-MM-DD`, then
  `--retry`.
- **Rebuild cap reached:** read the crashed runs' `logs/` and the campaign's
  `events.jsonl`, rebuild by hand (`confirmatory rebuild R --bank-id ID --reason TEXT`)
  or raise `max_rebuilds_per_bank`, then `--retry`.
- **Model server down for 12 h:** repair it, check `curl .../v1/models`, then `--retry`.

## 6. After the run: human steps

The supervisor never publishes or commits anything.

- **Pilot (#27):** record the register hash, the archive hash, the verify log's total
  line and the throughput note in the issue (`summary.json` has the hashes). If there is
  a shortfall, raise it before O6.3.2. Crashed roots are archived as records; name them
  too. The Unity smoke test (#70) stays a human step.
- **Confirmatory (#28):** section 1 of [`confirmatory-banks.md`](confirmatory-banks.md):
  `publish R --dest banks/registers/<campaign>`, then `git add`, `git commit` and
  `git push`, then `commit-check` for `register.csv` and `register.json` before the first
  confirmatory screening. Send `g5b-report.md` to the G5B owner and log the unavailable
  banks with the reveal API before the first reveal. G5B is a human gate.
- Stop and disable the service: `sudo systemctl disable --now av-banks-unattended`.

## 7. Files in the job root

```
<job root>/                     restricted storage, 0700
  state.json                    checkpoint (av-banks/unattended-state v1), atomic writes
  supervisor.log                one JSON line per event (fsynced per line)
  supervisor.lock               job lock (flock; inherited by every child)
  summary.json                  stages, banks per state, slots, wall time, waits, rebuilds
  logs/<n>-<stage>-<step>.out   stdout of child n (.err: its stderr)
  pilot/<run_id>/, pilot/<run_id>-M2/   pilot roots (pilot-banks.md section 3)
  confirmatory/                 the campaign (confirmatory-banks.md section 5)
```

A rehearsal job root (section 3) also holds `REHEARSAL` (the watermark),
`rehearsal-summary.json` and `rehearsal-summary.md` (the numbers, written when the job is
done), and its campaign is `demo-campaign/` (not `rehearsal/`: on a case-insensitive file
system that name is the marker's).

`state.json` is written through a temporary file that is fsynced, renamed over the old
file, and the directory is fsynced. It records the stage and step, the child in flight
(command without credentials, process ID, exit code), the counters and the stop reason.
The banks themselves are always read from the stage files, never from the state.

## 8. Command line

| Command | Exit codes |
| --- | --- |
| `run JOB [JOB ...] [--retry] [--allow-job-change] [--lock-wait S] [--quiet]` | 0 done; 2 refused; 3 stopped for a person; 4 interrupted (run again); 1 unexpected error. Several jobs run in order; the next starts only when one is done, else that job's code |
| `retry JOB [--allow-job-change]` | 0 (the next `run` goes on); 2 refused (a supervisor runs the job, or the job file changed) |
| `status JOB\|JOB_ROOT [--json]` | 0 |
| `check JOB [--no-probe]` | 0 OK; 2 problems (a model server that does not answer is a note) |
| `demo-job OUT --llm-url URL [--job-id DEMO-..] [--dyads 2] [--spares 1] [--banks 3]` | 0 |
| `rehearsal-job OUT --llm-url URL [--job-id DEMO-rehearsal-01] [--dyads 64] [--spares 8] [--parallel-banks 6] [--workers 3]` | 0; 1 refused (a size this checkout cannot plan, a location that is not empty or lies inside another job or rehearsal, an ID that is not DEMO) |

The supervisor needs POSIX (`flock` with inherited descriptors, signals); the GPU host
runs Linux. On Windows it runs, but it cannot prove that a child of an earlier session
has exited, so where a resume depends on that it stops for a person.

## 9. Decisions

| Decision | Rationale |
| --- | --- |
| Drive the existing command lines as child processes; the supervisor only reads the stage files | the runners' rules stay in one place; a crash in a child cannot corrupt the checkpoint |
| Automatic rebuild cap: 2 per confirmatory bank (`max_rebuilds_per_bank`, 0-9) | covers an outage and a reboot hitting the same bank; a third crash points at a cause a person must see |
| Pilot restart cap: 2 (`max_restarts`, 0-9), run IDs `<run_id>-M<k>` | the pilot crash rule rebuilds all 8 banks; three crashed runs need a person |
| Pilot breaker on (`--breaker 3`) under the supervisor; off by default on the command line | without it an outage fails cells within seconds and burns the 2 spares; with it the outage crashes the run, which the crash rule replaces with new seeds |
| Model wait: 5 s doubling to 120 s, at most 12 h, then stop | a vLLM restart or a reboot takes minutes; a server down for half a day needs a person |
| `--break-lock` only with the inherited-lock proof and a lock time after the job's last `run` start | `runner.lock` records only a time; the job lock proves that no runner of this job is alive |
| A restarted supervisor waits up to 24 h for a child a killed supervisor left running | killing that runner would crash its banks; under systemd (`KillMode=mixed`) no child outlives the supervisor |
| Stop for a person on a shortfall (default) or escalation; `on_shortfall: archive` is opt-in | the pilot and confirmatory rules make these human decisions |
| No register for a stage limited by `only` | `register` needs all 72 banks; DEMO stages build a few |
| Rehearsal (D1a) as a job file (`rehearsal-job` writes it, `purpose: rehearsal`), not a `run --rehearsal` flag | one command line and one service for every job; the purpose is hashed with the job file and kept in `state.json`, so a restart after a reboot cannot drop it and a changed job file cannot change it |
| Rehearsal size from its arguments, default 64 + 8; DEMO units `B-C01`.., `B-S01`.. of that size | the plan sized from the unit set (#299) reads the size from the units; before it, sizes other than 64 + 8 are refused and the plan is checked against the size after it is made |
| Rehearsal prompts: the committed templates under a DEMO prompt-set name | realistic prompt lengths and model time per slot; the DEMO meanings fill them; the set is DEMO by name and refused otherwise |
| A rehearsal register that needs an escalation ends the stage, recorded | a DEMO register has no advisor; the decision is a number for February, not a gate |
| `run PILOT_JOB REHEARSAL_JOB`: the rehearsal starts only when the pilot job is done | the pilot is the deliverable; the two jobs never share the model server, so the rehearsal measures the campaign alone |
| Markers are found by file name (`state.json`, `plan.json`, `pilot-plan.json`, `pilot-register.csv`, `run-manifest.json`, `REHEARSAL`) above and below a root | refusing a root that only looks like another job's is safe; a person moves it |

## 10. Tests

`tests/banks/test_unattended.py` runs DEMO jobs on CPU against the mock model server
(`av_generation.mock_llm`, loopback), with the real command lines as children: a clean
pilot-then-confirmatory job (the reference), an outage during the campaign, SIGKILL of
the supervisor alone and of the whole process group (a reboot), a pilot outage that
restarts the pilot with `--major 2`, a pilot shortfall with `--retry`, the git work tree
refusal, the job rules and the register outcomes. The rehearsal (section 3): the
`rehearsal-job` command and its job file; a full-size 64 + 8 plan (72 DEMO banks) made
without building a bank (and 84 + 12 and 88 + 12, 96 and 100 banks, once the plan is
sized from the unit set); a partial rehearsal of two banks against the mock that
survives SIGKILL of its process group and writes its summary; every refusal (real
inputs, a frozen or tagged freeze manifest, the job rules, roots inside or around other
roots, a changed purpose, a campaign that does not suit the job); the register outcome;
and `run` with two job files. The kept banks are compared with the
reference: the same `complete` and `unavailable` sets and the same bank content for
every bank that never crashed. Bank content is the manifest without the attempts'
`wall_ms` and `slots_sha256`, which carry real latencies, so two real-time runs never
share a `bank_sha256`.
