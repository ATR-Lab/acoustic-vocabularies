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
(#25). Before G4, a DEMO rehearsal campaign exercises the confirmatory path (section 2).

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

**1. DEMO rehearsal against the real server** (DEMO IDs only, nothing restricted):

```bash
$U demo-job <restricted>/rehearsal --llm-url http://<llm-host>:8000 --dyads 2 --banks 3
$U check <restricted>/rehearsal/job.json   # OK, "model server: answers"
$U run <restricted>/rehearsal/job.json     # exit 0; prints the summary
```

While it runs, stop and start the model server once (`systemctl restart av-llm`): the
supervisor logs `model_wait_start`, waits, rebuilds the halted DEMO banks and finishes.
`$U status <restricted>/rehearsal/job.json` from another shell shows the same.

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

## 3. The first hour

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

## 4. When it stopped

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

## 5. After the run: human steps

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

## 6. Files in the job root

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

`state.json` is written through a temporary file that is fsynced, renamed over the old
file, and the directory is fsynced. It records the stage and step, the child in flight
(command without credentials, process ID, exit code), the counters and the stop reason.
The banks themselves are always read from the stage files, never from the state.

## 7. Command line

| Command | Exit codes |
| --- | --- |
| `run JOB [--retry] [--allow-job-change] [--lock-wait S] [--quiet]` | 0 done; 2 refused; 3 stopped for a person; 4 interrupted (run again); 1 unexpected error |
| `retry JOB [--allow-job-change]` | 0 (the next `run` goes on); 2 refused (a supervisor runs the job, or the job file changed) |
| `status JOB\|JOB_ROOT [--json]` | 0 |
| `check JOB [--no-probe]` | 0 OK; 2 problems (a model server that does not answer is a note) |
| `demo-job OUT --llm-url URL [--job-id DEMO-..] [--dyads 2] [--spares 1] [--banks 3]` | 0 |

The supervisor needs POSIX (`flock` with inherited descriptors, signals); the GPU host
runs Linux. On Windows it runs, but it cannot prove that a child of an earlier session
has exited, so where a resume depends on that it stops for a person.

## 8. Decisions

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

## 9. Tests

`tests/banks/test_unattended.py` runs DEMO jobs on CPU against the mock model server
(`av_generation.mock_llm`, loopback), with the real command lines as children: a clean
pilot-then-confirmatory job (the reference), an outage during the campaign, SIGKILL of
the supervisor alone and of the whole process group (a reboot), a pilot outage that
restarts the pilot with `--major 2`, a pilot shortfall with `--retry`, the git work tree
refusal, the job rules and the register outcomes. The kept banks are compared with the
reference: the same `complete` and `unavailable` sets and the same bank content for
every bank that never crashed. Bank content is the manifest without the attempts'
`wall_ms` and `slots_sha256`, which carry real latencies, so two real-time runs never
share a `bank_sha256`.
