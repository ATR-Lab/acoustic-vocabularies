# Pilot-day runbook: Study A pilot panels and the threshold check

For the research assistants and the generation operator who run the Study A pilot
panels (3 batches, 12 sittings) and the separation-threshold check (about 8 listeners).
Each step says what to type and what you should see. You never change code on the day.

Both studies run only after ethics approval. Do not start a session without it.

How to read the commands. Run every command from the repository root of a clean
checkout. Replace each `<...>` with your value (for example `<restricted>` is the path of
the restricted storage folder). Commands for real sessions use `<restricted>` paths.
Commands for rehearsals use `DEMO-` names and write to `generation/out/` (git ignores
it). `tests/generation/test_pilot_runbook.py` checks that every command in this file
still parses.

## 0. One-page checklist

The day before the pilot panels:

- [ ] Inputs are in restricted storage (section 3.1).
- [ ] Batch configs and the generation config are built, and `check` says `ok` for
      all three batches (section 3.2).
- [ ] The model server on the GPU host prints `READY`. The health check from the
      orchestration computer answers (section 3.3).
- [ ] Stations: headphones, fixed gain, kiosk browser, system sounds off (section 3.4).
- [ ] DEMO rehearsal with people done: one round, a restart, a withdrawal (section 3.5).

Each sitting (one appointment, at most 90 minutes):

- [ ] Note: date, batch, sitting 1-4, coded IDs of the designer and the 3 raters.
- [ ] The model server answers. Start the runner (sitting 1 without `--resume`,
      sittings 2-4 with `--resume`).
- [ ] Open the A1 page on the kiosk and each station URL on its own station.
- [ ] The session starts when all 3 stations have joined. Do not touch anything.
- [ ] At the end: `Appointment k: done`, exit status 0. Back up the run folder.

If something goes wrong (section 5):

- [ ] Never replay a sound. Never edit or delete anything in a run folder.
- [ ] Exit 1: fix the cause. Start again, with `--resume` if the run folder exists.
- [ ] Exit 3 (a rater stopped): the batch is incomplete. Stop. Rebuild it later.
- [ ] Exit 130 or a crash: start again with `--resume`. Write a deviation note.

After the last sitting of a batch: `audit batch` and `audit tally` (section 6).

Threshold check, each listener (section 7):

- [ ] Coded listener ID; the listener is in no learner cohort.
- [ ] Fixed station gain, the same value in `--gain-db`.
- [ ] `session`, kiosk browser, read the instructions, no feedback, no replay.
- [ ] `export`: every session shows `"ok": true`. Give the `summary.json` SHA-256 to
      the owner.

## 1. Roles and stations

| Role | Who | Does |
| --- | --- | --- |
| Generation operator | one trained team member | runs the commands on the orchestration computer; may see which book is which method; keeps the unmasked files |
| Session assistant | research assistant | greets people, seats them, reads the instructions, writes the session notes; never says which method made a sound |
| Hand designer | D1, D2 or D3 (one per batch) | designs at the A1 kiosk |
| Raters | 3 per batch, 9 in total (coded IDs such as R01) | rate at stations S1, S2, S3 |
| GPU-host operator | team member with access to the GPU host | starts and checks the model server |
| Listener (threshold check) | about 8 people, coded IDs | listens at one rater station |

| Computer | Runs | Network |
| --- | --- | --- |
| Orchestration computer | the batch runner: A1 page (port 8741), rater panel (port 8765), A2 search inside the runner | lab network address `<lab-ip>` |
| A1 kiosk (station S9) | kiosk browser with the A1 page | lab network |
| Rater stations S1, S2, S3 | kiosk browser with the station page; closed-back headphones | lab network |
| GPU host | the model server for A3 (port 8000) | lab network address `<llm-host>` |

A2 search and the selector run inside the batch runner. You start nothing for them.

## 2. Where files go

Real files go to restricted storage only. That is an approved, encrypted folder outside
every git work tree. The tools refuse real outputs inside a git work tree (`E_POLICY`).
Nothing from a real session goes into the repository, a chat, an e-mail or an
unapproved cloud folder.

Suggested layout of `<restricted>`:

```text
<restricted>/
  schedules/A/        pilot-batch-table.csv, A-P01/permutation.json, ..., pilot-book-key.json
  fallback/           fallback-manifest.json (restricted fallback build)
  meanings/           the study meaning set
  pilot-set-ns.txt    the restricted set namespace (one line)
  pilot-inputs/       panel-orders.csv, A-P01/config.json, ..., generation-config.json
  runs/               one folder per batch run, for example A-P01-r1
  audit/              set tables (masked and unmasked)
  threshold/          stimuli.json, runs/, export/
  notes/              session notes and deviation notes
```

Backups. After every sitting, copy the whole run folder to `<backup>` (restricted and
encrypted too). Copy it only after the runner has exited. Never copy station URLs: each
one holds a seat key.

Session notes. One note per sitting in `<restricted>/notes/`: date, run ID, sitting
number, coded IDs present, start and end time, exit status, every fault and what you
did. Use coded IDs only, never names.

## 3. The day before the pilot panels

### 3.1 Inputs in restricted storage

| File | From |
| --- | --- |
| `schedules/A/pilot-batch-table.csv`, `schedules/A/A-P0x/permutation.json` | schedules owner (curriculum, pilot master seed) |
| `schedules/A/pilot-book-key.json` | schedules owner (allocation, pilot seed) |
| `fallback/fallback-manifest.json` | restricted fallback build (`sound/docs/fallback.md`, section 7) |
| `meanings/` | the study meaning set |
| `pilot-set-ns.txt` | the generation operator, once: `A-P-` and 16 random hex characters, for example from `python3 -c "import secrets; print('A-P-' + secrets.token_hex(8))"` |

The batch table names, for each pilot batch, its profile and its designer (D1, D2 or
D3). Each batch needs 3 raters of its own: 9 raters in total.

### 3.2 Build and check the batch inputs (once)

Write the seats first: which coded rater sits at which station in which batch.

```bash
uv run --project generation python -m av_generation.batch_setup configs \
    --batch-table <restricted>/schedules/A/pilot-batch-table.csv \
    --units <restricted>/schedules/A \
    --book-key <restricted>/schedules/A/pilot-book-key.json \
    --fallback <restricted>/fallback/fallback-manifest.json \
    --set-ns-file <restricted>/pilot-set-ns.txt \
    --seats A-P01=R01@S1,R02@S2,R03@S3 \
    --seats A-P02=R04@S1,R05@S2,R06@S3 \
    --seats A-P03=R07@S1,R08@S2,R09@S3 \
    --out <restricted>/pilot-inputs
uv run --project generation python -m av_generation.batch_setup generation-config \
    --name pilot-1 --meanings <restricted>/meanings \
    --fallback <restricted>/fallback/fallback-manifest.json \
    --out <restricted>/pilot-inputs/generation-config.json
```

You should see JSON with one entry per batch (`batch_id`, `profile`, `designer`,
`panel_id`, `order_index`, `seats`, `config_sha256`) and the generation config's
`frozen_sha256`. The three order indexes differ. Write the hashes in the notes.

Then check each batch (repeat for `A-P02` and `A-P03`):

```bash
uv run --project generation python -m av_generation.batch_runner check \
    --run-dir <restricted>/runs/A-P01-r1 --kind pilot \
    --config <restricted>/pilot-inputs/A-P01/config.json \
    --generation-config <restricted>/pilot-inputs/generation-config.json \
    --meanings <restricted>/meanings --fallback <restricted>/fallback/fallback-manifest.json
```

```text
ok: run A-P01-r1 (pilot), batch A-P01, generation config <64 hex>
```

`check` creates nothing. Exit status 1 means a refused input; the message names it.

### 3.3 Model server on the GPU host

On the GPU host (`generation/docs/llm.md`, section 2), check, then serve:

```bash
uv run --project generation python -m av_generation.llm_server check \
    --model-dir <model-dir> --vllm <vllm-env>/bin/vllm
uv run --project generation python -m av_generation.llm_server serve \
    --model-dir <model-dir> --vllm <vllm-env>/bin/vllm --host <llm-host> \
    --timing-log <gpu-restricted>/llm-startup.jsonl --run-id A-P01-r1
```

`check` prints `OK verify_ms=...`. `serve` prints `READY http://<llm-host>:8000
startup_ms=...` and keeps running. A refusal prints `REFUSED <code>`: do not work
around it, call the engineer. Keep the timing log; the model load time is in it.

Health check from the orchestration computer:

```bash
curl -s http://<llm-host>:8000/health
curl -s http://<llm-host>:8000/version
curl -s http://<llm-host>:8000/v1/models
```

`/health` answers with status 200. `/v1/models` lists `Qwen/Qwen2.5-7B-Instruct`.
`/version` names the pinned vLLM version. The runner checks the same at every start and
refuses a wrong model or runtime (`E_LLM_SERVER`, exit 1, nothing created).

### 3.4 Stations

- Rater stations: the same browser, identical closed-back headphones, the same output
  device and the same fixed gain on S1, S2 and S3; system sounds and notifications off;
  browser full screen or kiosk (`generation/docs/rater-panel.md`, section 9).
- A1 kiosk: the kiosk browser with the managed policy
  (`generation/docs/a1-operating-guide.md`, "Kiosk checks before every session").
- Designers have had their practice sessions (`a1-operating-guide.md`, "Practice
  session"). Their coded IDs match the batch table.

### 3.5 DEMO rehearsal with people

Rehearse on the real computers with DEMO material: a mock model, DEMO schedules and DEMO
configs whose seats are `human`, so the real station pages can join. Nothing here is
study material.

Mock model server (its own terminal on the orchestration computer; stop it with Ctrl-C
at the end):

```bash
uv run --project generation python -m av_generation.mock_llm serve \
    generation/out/rehearsal/mock-model --host 127.0.0.1 --port 8000
```

DEMO inputs (the same steps as section 3.2, with DEMO files):

```bash
uv run --project schedules python -m av_schedules curriculum \
    --demo-seed DEMO-pilot-rehearsal --study A --set pilot --out generation/out/rehearsal/schedules
uv run --project schedules python -m av_schedules allocate \
    --pilot-demo-seed DEMO-pilot-rehearsal --study A --out generation/out/rehearsal/schedules
uv run --project generation python -m av_generation.batch_setup configs \
    --batch-table generation/out/rehearsal/schedules/A/pilot-batch-table.csv \
    --units generation/out/rehearsal/schedules/A \
    --book-key generation/out/rehearsal/schedules/A/pilot-book-key.json \
    --fallback sound/testvectors/fallback/demo-manifest.json \
    --demo-set-ns DEMO-pilot-rehearsal \
    --seats A-P01=R01@S1,R02@S2,R03@S3 \
    --seats A-P02=R04@S1,R05@S2,R06@S3 \
    --seats A-P03=R07@S1,R08@S2,R09@S3 \
    --out generation/out/rehearsal/inputs
uv run --project generation python -m av_generation.batch_setup generation-config \
    --name DEMO-pilot-rehearsal-1 --meanings generation/examples/demo-meanings \
    --fallback sound/testvectors/fallback/demo-manifest.json \
    --out generation/out/rehearsal/inputs/generation-config.json
uv run --project generation python -m av_generation.batch_runner check \
    --run-dir generation/out/rehearsal/runs/DEMO-rehearsal-A-P01 --kind demo \
    --config generation/out/rehearsal/inputs/DEMO-A-P01/config.json \
    --generation-config generation/out/rehearsal/inputs/generation-config.json \
    --meanings generation/examples/demo-meanings \
    --fallback sound/testvectors/fallback/demo-manifest.json
```

Expected: three DEMO configs (`DEMO-A-P01` .. `DEMO-A-P03`, three different
`order_index` values), a DEMO generation config, then
`ok: run DEMO-rehearsal-A-P01 (demo), batch DEMO-A-P01, generation config <64 hex>`.

The rehearsal sitting, with a team member at the kiosk and three at the stations:

```bash
uv run --project generation python -m av_generation.batch_runner run \
    --run-dir generation/out/rehearsal/runs/DEMO-rehearsal-A-P01 --kind demo \
    --config generation/out/rehearsal/inputs/DEMO-A-P01/config.json \
    --generation-config generation/out/rehearsal/inputs/generation-config.json \
    --meanings generation/examples/demo-meanings \
    --fallback sound/testvectors/fallback/demo-manifest.json \
    --llm-url http://127.0.0.1:8000 --a1-host <lab-ip> --a1-station S9 \
    --panel-host <lab-ip> --appointment next
```

Rehearse in this order:

1. Open the A1 page and the three station URLs, exactly as in section 4. Let round 1
   run (about 5 minutes).
2. Press Ctrl-C in the runner's terminal. You see
   `interrupted; the logs are kept: reopen the run with --resume` (exit status 130).
3. Run the same command again with `--resume` added. Open the new URLs (the old keys no
   longer work). The interrupted atom is finished first.
4. After one more round, one rater presses **Stop taking part**. The runner ends with
   `batch incomplete: ...` (exit status 3). Rehearse the rebuild (section 5.3) with
   `--demo-set-ns DEMO-pilot-rehearsal` instead of `--set-ns-file`.
5. Delete nothing. The rehearsal folder stays in `generation/out/`.

### 3.6 Rehearsals without people (bots)

These need no people and finish in minutes. They exercise the same code with a bot
designer and bot raters. Run them after any software update. The mock model server of
section 3.5 must run.

A whole batch, 4 sittings in one go, bot designer and bot panel, accelerated clock:

```bash
uv run --project generation python -m av_generation.batch_runner run \
    --run-dir generation/out/rehearsal/runs/DEMO-rehearsal-batch-01 --kind demo \
    --llm-url http://127.0.0.1:8000 --designer bot --panel bots \
    --clock scaled --speed 200 --a1-port 8742 --appointment all
uv run --project generation python -m av_generation.audit batch \
    generation/out/rehearsal/runs/DEMO-rehearsal-batch-01 --strict
uv run --project generation python -m av_generation.audit tally \
    generation/out/rehearsal/runs/DEMO-rehearsal-batch-01 \
    --out generation/out/rehearsal/runs/DEMO-rehearsal-batch-01-tally.csv
```

```text
Run DEMO-rehearsal-batch-01 (demo): batch DEMO-A-P01
A1 designer page: http://127.0.0.1:8742/a1/
Appointment 1: started
Appointment 1: done
...
Appointment 4: done
Atoms finished: 16/16; next: None
```

The audit prints its files with their SHA-256 and `"ok": true`; the tally prints
`"all_match": true`.

One sitting through the real panel server, with three bot stations that join with
their keyed URLs (the dry run of #22):

```bash
uv run --project generation python -m av_generation.dryrun run \
    --out generation/out/rehearsal/dry --run-id DEMO-rehearsal-stations-01 \
    --clock scaled --speed 100 --appointments 1 \
    --evidence generation/out/rehearsal/dry/DEMO-rehearsal-stations-01-evidence
```

Expected last line: `dry run DEMO-rehearsal-stations-01: completeness OK, ...`, exit
status 0. Its audit says `ok=False`: a one-sitting run lacks 12 atoms by design.

Interrupt and resume. Start a bot batch with `--appointment next`, press Ctrl-C during
a sitting, then repeat the command with `--resume` until the run is closed:

```bash
uv run --project generation python -m av_generation.batch_runner run \
    --run-dir generation/out/rehearsal/runs/DEMO-rehearsal-resume-01 --kind demo \
    --llm-url http://127.0.0.1:8000 --designer bot --panel bots \
    --clock scaled --speed 100 --a1-port 8742 --appointment next
uv run --project generation python -m av_generation.batch_runner run \
    --run-dir generation/out/rehearsal/runs/DEMO-rehearsal-resume-01 --kind demo \
    --llm-url http://127.0.0.1:8000 --designer bot --panel bots \
    --clock scaled --speed 100 --a1-port 8742 --appointment next --resume
uv run --project generation python -m av_generation.audit batch \
    generation/out/rehearsal/runs/DEMO-rehearsal-resume-01 --strict
```

A closed run answers `Atoms finished: 16/16; the run is closed` and writes nothing.
The audit lists each interrupted interval as a note, not a problem.

The station server alone (36 slots, scripted schedule, three bot stations), and the
onsets the stations logged:

```bash
uv run --project generation python -m av_generation.panel_demo \
    --host 127.0.0.1 --port 8768 --rounds 4 --atom-index 5 \
    --run-id DEMO-panel-rehearsal-01 --out-dir generation/out/rehearsal/panel \
    --bots --speed 10 --start-delay-s 1
uv run --project generation python -m av_generation.panel_skew logged \
    --plays generation/out/rehearsal/panel/DEMO-panel-rehearsal-01/logs/plays.jsonl \
    --schedule generation/out/rehearsal/panel/DEMO-panel-rehearsal-01/panel-schedule.json \
    --out generation/out/rehearsal/panel/DEMO-panel-rehearsal-01/skew
```

Without `--bots` the same `panel_demo` waits for three real station browsers: use it to
check the stations and the headphones (`rater-panel.md`, section 9). Bot onsets at
`--speed 10` lag by a few real milliseconds times 10; they test the protocol, not the
timing.

A whole DEMO set: three bot batches from bot-seat configs, then the set audit:

```bash
uv run --project generation python -m av_generation.batch_setup configs \
    --batch-table generation/out/rehearsal/schedules/A/pilot-batch-table.csv \
    --units generation/out/rehearsal/schedules/A \
    --book-key generation/out/rehearsal/schedules/A/pilot-book-key.json \
    --fallback sound/testvectors/fallback/demo-manifest.json \
    --demo-set-ns DEMO-pilot-rehearsal --seat-kind bot \
    --seats A-P01=B01@S1,B02@S2,B03@S3 \
    --seats A-P02=B04@S1,B05@S2,B06@S3 \
    --seats A-P03=B07@S1,B08@S2,B09@S3 \
    --out generation/out/rehearsal/inputs-bots
uv run --project generation python -m av_generation.batch_runner run \
    --run-dir generation/out/rehearsal/runs/DEMO-rehearsal-set-A-P01 --kind demo \
    --config generation/out/rehearsal/inputs-bots/DEMO-A-P01/config.json \
    --generation-config generation/out/rehearsal/inputs/generation-config.json \
    --meanings generation/examples/demo-meanings \
    --fallback sound/testvectors/fallback/demo-manifest.json \
    --llm-url http://127.0.0.1:8000 --designer bot --panel bots \
    --clock scaled --speed 200 --a1-port 8801 --appointment all
uv run --project generation python -m av_generation.batch_runner run \
    --run-dir generation/out/rehearsal/runs/DEMO-rehearsal-set-A-P02 --kind demo \
    --config generation/out/rehearsal/inputs-bots/DEMO-A-P02/config.json \
    --generation-config generation/out/rehearsal/inputs/generation-config.json \
    --meanings generation/examples/demo-meanings \
    --fallback sound/testvectors/fallback/demo-manifest.json \
    --llm-url http://127.0.0.1:8000 --designer bot --panel bots \
    --clock scaled --speed 200 --a1-port 8802 --appointment all
uv run --project generation python -m av_generation.batch_runner run \
    --run-dir generation/out/rehearsal/runs/DEMO-rehearsal-set-A-P03 --kind demo \
    --config generation/out/rehearsal/inputs-bots/DEMO-A-P03/config.json \
    --generation-config generation/out/rehearsal/inputs/generation-config.json \
    --meanings generation/examples/demo-meanings \
    --fallback sound/testvectors/fallback/demo-manifest.json \
    --llm-url http://127.0.0.1:8000 --designer bot --panel bots \
    --clock scaled --speed 200 --a1-port 8803 --appointment all
uv run --project generation python -m av_generation.audit set --study A --set demo \
    --masked-out generation/out/rehearsal/audit-set/masked \
    --unmasked-out generation/out/rehearsal/audit-set/unmasked \
    generation/out/rehearsal/runs/DEMO-rehearsal-set-A-P01 \
    generation/out/rehearsal/runs/DEMO-rehearsal-set-A-P02 \
    generation/out/rehearsal/runs/DEMO-rehearsal-set-A-P03
```

The three batches may run at the same time (each has its own `--a1-port`). Each ends
with `Atoms finished: 16/16; next: None`. On a busy computer a few bot ratings may be
`missing` at 200x (a bot woke after the lock); the record counts still hold. The set audit writes `A-demo-audit.csv`
(masked) and `A-demo-audit-unmasked.csv` and lists any incomplete run under
`excluded_runs`.

## 4. A sitting, step by step

One sitting is one appointment: 4 atoms, each of 4 rounds. A round has a proposal window
of at most 2 minutes, then 9 rating slots of 20 seconds. A sitting takes at most about
80 minutes; book 90. A batch has 4 sittings, so the pilot has 12. Run the 4 sittings of
a batch in order. Batches may be interleaved.

### 4.1 Before

1. The model server answers (section 3.3).
2. The designer of this batch is at the kiosk (S9). The three raters of this batch's
   panel sit at their own stations (S1, S2, S3), as written in the seats of
   section 3.2. A wrong person on a seat is a deviation: do not start.
3. Start the session note.

### 4.2 Start the runner

Sitting 1 of a batch (the run folder does not exist yet):

```bash
uv run --project generation python -m av_generation.batch_runner run \
    --run-dir <restricted>/runs/A-P01-r1 --kind pilot \
    --config <restricted>/pilot-inputs/A-P01/config.json \
    --generation-config <restricted>/pilot-inputs/generation-config.json \
    --meanings <restricted>/meanings --fallback <restricted>/fallback/fallback-manifest.json \
    --llm-url http://<llm-host>:8000 --a1-host <lab-ip> --a1-station S9 \
    --panel-host <lab-ip> --appointment next
```

Run it in the foreground of its own terminal window, never with `&` or `nohup`: Ctrl-C
must reach it. Sittings 2, 3 and 4, and every restart: the same command with
`--resume`:

```bash
uv run --project generation python -m av_generation.batch_runner run \
    --run-dir <restricted>/runs/A-P01-r1 --kind pilot \
    --config <restricted>/pilot-inputs/A-P01/config.json \
    --generation-config <restricted>/pilot-inputs/generation-config.json \
    --meanings <restricted>/meanings --fallback <restricted>/fallback/fallback-manifest.json \
    --llm-url http://<llm-host>:8000 --a1-host <lab-ip> --a1-station S9 \
    --panel-host <lab-ip> --appointment next --resume
```

You should see:

```text
Run A-P01-r1 (pilot): batch A-P01
A1 designer page: http://<lab-ip>:8741/a1/
Rater station S1 (R01): http://<lab-ip>:8765/panel/station?station=S1&rater=R01&key=<seat key>
Rater station S2 (R02): http://<lab-ip>:8765/panel/station?station=S2&rater=R02&key=<seat key>
Rater station S3 (R03): http://<lab-ip>:8765/panel/station?station=S3&rater=R03&key=<seat key>
Waiting for stations: S1, S2, S3
```

### 4.3 Open the pages

1. Kiosk: open the A1 page. No PRACTICE banner. The designer present is this batch's
   designer (the `designer` value that `batch_setup configs` printed for the batch).
2. Each station: open its own URL (S1's URL on S1, and so on), full screen. The rater
   presses **Start** once; this allows audio.
3. The session starts when all three stations have joined. The runner waits at most
   10 minutes, then stops with `E_STATIONS` (section 5).
4. Each URL works only for this run of the command. Do not write the URLs down.

### 4.4 During the sitting

- The terminal shows `Appointment k: started`. It shows nothing else until the end.
- The kiosk shows the round, the slots used and the time left. The stations show the
  meaning of each sound and the rating controls. (With #303 merged, the station page
  also shows "Candidate k of 9".)
- The session assistant watches the people, not the screens. Answer questions about
  the controls only. Never say which method made a sound.
- Between atoms the stations show a pause screen. Short breaks happen there.
- Do not press keys in the runner's terminal. Do not close the stations' browsers.

### 4.5 End of the sitting

```text
Appointment 1: done
Atoms finished: 4/16; next: <atom ID>
```

The stations show the end screen. The command exits with status 0 (sitting 4:
`Atoms finished: 16/16; next: None`). Then:

1. Write the end time and the exit status in the note.
2. Back up the run folder (section 2).
3. Tell the coordinator if anything unusual happened.

## 5. Faults and interruptions

### 5.1 What to do

| What you see | What it means | What to do |
| --- | --- | --- |
| `error: E_STATIONS: stations not connected: S2` (exit 1) | a station did not join within 10 minutes | Fix the station (network, browser, URL). Start again with `--resume`. New URLs are printed. |
| `error: run directory ... already exists` (exit 1) | the run folder exists, and `--resume` is missing | Add `--resume`. |
| `error: E_RUN_DIR: ... is not a started run` (exit 1) | `--resume` was given, but the run folder does not exist yet | Remove `--resume` (sitting 1). |
| `address already in use` (exit 1) | another program holds port 8741 or 8765 | Stop the old runner if one still runs. Otherwise add `--a1-port` or `--panel-port` with a free port and start again with `--resume`. |
| `error: E_LLM_SERVER: ...` (exit 1) | the model server is down, or it serves another model or runtime | Ask the GPU-host operator (section 3.3). Nothing was created; start again when the health check answers. |
| `error: E_INPUTS`, `E_CONFIG`, `E_KIND`, `E_RUN_ID` (exit 1) | an input file or name is wrong | Do not edit files. Check the command against section 4.2; call the generation operator. |
| The model server stops during a sitting | A3 slots fail; they are logged as failed slots | Do not stop the sitting. Ask the GPU-host operator to restart the server. Note the time. |
| A station shows "reconnecting" | its network dropped | Wait. It joins again by itself. A slot it missed has no rating. |
| A station page was reloaded or the browser restarted | the page lost its state | Open the same URL again. The current slot is skipped; nothing is played twice. |
| A station shows that a sound could not be played | a sound did not start | Nothing. The rating of that slot is missing. Note the time. Never replay. |
| A valid A1 recipe did not play on the kiosk | the sound was not fetched | Note the slot. Do not replay (`a1-operating-guide.md`, "Troubleshooting"). |
| A rater presses **Stop taking part** | a withdrawal | The runner stops at the end of the slot: `batch incomplete: ...` (exit 3). Thank the rater. Stop the sitting. See 5.3. |
| The runner exits with status 130, the computer crashes, or the power fails | the sitting was interrupted | Start again with `--resume` (section 5.2). |
| `error: E_RESUME_PARTIAL_WINDOW` | a crash inside a proposal window that cannot be resumed | Stop. Write a deviation note. The batch is rebuilt (5.3). |

### 5.2 Resume after an interruption

1. Check that the model server answers and that all people are still there.
2. Run the sitting's command with `--resume`. Open the new URLs.
3. The runner first finishes the atom that was interrupted, then the rest of the
   sitting.
4. Rating slots that had no records when the crash came are run again, so raters may
   hear those candidates again. Write a deviation note with the time.
5. If the sitting cannot go on today, stop. The next sitting with `--resume` finishes
   the interrupted atom first.

### 5.3 A rater withdraws: rebuild the batch

A batch with a withdrawal is incomplete. The run keeps every record and refuses to
continue. The batch is run again in a new run folder with a new, independent panel (3
raters who have not rated this batch), new aliases and new seeds:

```bash
uv run --project generation python -m av_generation.batch_setup rebuild \
    --config <restricted>/pilot-inputs/A-P01/config.json \
    --set-ns-file <restricted>/pilot-set-ns.txt \
    --seats R10@S1,R11@S2,R12@S3 \
    --out <restricted>/pilot-inputs/A-P01-rb1/config.json
uv run --project generation python -m av_generation.batch_runner check \
    --run-dir <restricted>/runs/A-P01-rb1-r1 --kind pilot \
    --config <restricted>/pilot-inputs/A-P01-rb1/config.json \
    --generation-config <restricted>/pilot-inputs/generation-config.json \
    --meanings <restricted>/meanings --fallback <restricted>/fallback/fallback-manifest.json
```

The rebuilt batch starts at sitting 1 (section 4.2 with `--run-dir
<restricted>/runs/A-P01-rb1-r1` and the new config). Its panel is `A-P01-N2`. If a rater
says between sittings that they will not come back, do not start the next sitting; write
a deviation note and rebuild in the same way.

DEMO version (section 3.5 rehearsal):

```bash
uv run --project generation python -m av_generation.batch_setup rebuild \
    --config generation/out/rehearsal/inputs/DEMO-A-P01/config.json \
    --demo-set-ns DEMO-pilot-rehearsal --seats R10@S1,R11@S2,R12@S3 \
    --out generation/out/rehearsal/inputs/DEMO-A-P01-rb1/config.json
uv run --project generation python -m av_generation.batch_runner check \
    --run-dir generation/out/rehearsal/runs/DEMO-rehearsal-A-P01-rb1 --kind demo \
    --config generation/out/rehearsal/inputs/DEMO-A-P01-rb1/config.json \
    --generation-config generation/out/rehearsal/inputs/generation-config.json \
    --meanings generation/examples/demo-meanings \
    --fallback sound/testvectors/fallback/demo-manifest.json
```

### 5.4 Never

- Never replay a sound for a rater, a designer or a listener.
- Never edit, move or delete files inside a run folder. Never run two runners on one run
  folder.
- Never tell a rater or a listener which method made a sound, or what answer is
  expected.
- Never change the station gain during a session.
- Never write names, station URLs or seat keys into notes.
- Never copy real files into the repository or `generation/out/`.

## 6. End of a batch and end of the pilot

After sitting 4 the run is closed (`Atoms finished: 16/16; next: None`). Then:

```bash
uv run --project generation python -m av_generation.audit batch \
    <restricted>/runs/A-P01-r1 --strict
uv run --project generation python -m av_generation.audit tally \
    <restricted>/runs/A-P01-r1 --out <restricted>/audit/A-P01-r1-tally.csv
```

`batch` writes `audit/masked/` and `audit/unmasked/` inside the run folder and prints
`"ok": true`. `--strict` exits 1 when a check found a problem: give the message to the
generation operator. `tally` must print `"all_match": true`. Only the generation operator
keeps `unmasked/`; analysts get `masked/` only.

After the three pilot batches, list the run folder of every pilot batch, rebuilt runs
included. Incomplete runs are left out by themselves (`excluded_runs`):

```bash
uv run --project generation python -m av_generation.audit set --study A --set pilot \
    --masked-out <restricted>/audit/masked --unmasked-out <restricted>/audit/unmasked \
    <restricted>/runs/A-P01-r1 <restricted>/runs/A-P02-r1 <restricted>/runs/A-P03-r1
```

Give the masked table and the SHA-256 values it prints to the owner. The owner uses them
for the pilot timing review and the G4 items `pilot.timing_review` and
`pilot.audit_sha256` (`generation/docs/freeze.md`, section 6).

End of each day: back up every run folder used today; stop the runner terminals that are
still open (Ctrl-C only when no sitting runs); tell the GPU-host operator whether the
model server can stop; close the station browsers.

## 7. Threshold-check day

About 8 listeners. Each listener has one session of 224 pairs, about 15 to 20 minutes,
at one rater station. A listener is never in a learner cohort and never joins one.
Coded listener IDs only. Tool reference: `generation/docs/threshold-tool.md`.

### 7.1 Once, before the first listener

```bash
uv run --project generation python -m av_generation.threshold_cli stimuli \
    --set-id <set-id> --out <restricted>/threshold/stimuli.json
uv run --project generation python -m av_generation.threshold_cli check \
    --stimuli <restricted>/threshold/stimuli.json
```

`check` prints `"problems": []` and the set hash (`set_sha256`). Write the set hash in
the notes. `<set-id>` must not start with `DEMO-`.

### 7.2 Each listener

On the station computer (it needs this checkout and the restricted folder). Use the same
station for every listener if you can. The station's output is at its fixed gain; type
the same value as `--gain-db`. Use a new session ID per listener (for example
`TH01-L03`):

```bash
uv run --project generation python -m av_generation.threshold_cli session \
    --runs-root <restricted>/threshold/runs --run-id <threshold-run-id> --kind pilot \
    --stimuli <restricted>/threshold/stimuli.json --session-id <session-id> \
    --listener <listener-id> --station S1 --gain-db <gain-db> \
    --host 127.0.0.1 --port 8023
```

It prints JSON with `"n_trials": 224` and `"url": "http://127.0.0.1:8023/threshold/"`,
then serves the page until Ctrl-C.

1. Open the URL in the kiosk browser of this station. One tab only.
2. Read the instructions on the page to the listener. Do not explain the distances or
   which answers are expected.
3. During the session: no feedback, no replay. If the audio fails, or the page says the
   pair was interrupted, press **Skip this trial (operator)** and write a note.
4. When the page says "The session is complete", press Ctrl-C in the terminal.

Interrupted session: run the same `session` command again with the same values. A
different listener, station, gain or set is refused (exit status 2). A pair whose audio
already left the server is never played again; it can only be skipped.

### 7.3 After the listeners

```bash
uv run --project generation python -m av_generation.threshold_cli export \
    --run-dir <restricted>/threshold/runs/<threshold-run-id> \
    --out-dir <restricted>/threshold/export
```

Every session must show `"ok": true` in `play_checks`. Write each `n_unreported` value in
the notes. `files` lists `summary.json` with its SHA-256: give that hash and the export
folder to the owner. The tool never decides the threshold (`"decision": null`).

The owner records the hash in the G4 draft as `separation.evidence_sha256` together with
the threshold decision (`freeze.md`, section 6, step 3):

```bash
uv run --project generation python -m av_generation.freeze record \
    separation.evidence_sha256 --text <summary-sha256> \
    --source <source-note> --manifest <restricted>/freeze-draft.json
```

Internal tryout (two team members, not counted as listeners): add `--tryout` to
`session` and to `export`. Tryout and listener data are never mixed.

### 7.4 DEMO rehearsal of the threshold day

```bash
uv run --project generation python -m av_generation.threshold_cli stimuli \
    --set-id DEMO-T-REHEARSAL --out generation/out/rehearsal/threshold/stimuli.json
uv run --project generation python -m av_generation.threshold_cli check \
    --stimuli generation/out/rehearsal/threshold/stimuli.json
uv run --project generation python -m av_generation.threshold_cli session \
    --runs-root generation/out/rehearsal/threshold/runs --run-id DEMO-threshold-rehearsal \
    --kind demo --stimuli generation/out/rehearsal/threshold/stimuli.json \
    --session-id TH00-L00 --listener L00 --station S1 --gain-db -12 --plan-only
uv run --project generation python -m av_generation.threshold_cli demo \
    --out-dir generation/out/rehearsal/threshold-demo --sessions 8
uv run --project generation python -m av_generation.threshold_cli export \
    --run-dir generation/out/rehearsal/threshold-demo/runs/DEMO-T1-run \
    --out-dir generation/out/rehearsal/threshold-export
uv run --project generation python -m av_generation.threshold_cli summary \
    --trials generation/out/rehearsal/threshold-export/trials.csv \
    --out-dir generation/out/rehearsal/threshold-summary --label "SYNTHETIC rehearsal" \
    --stimuli generation/out/rehearsal/threshold-demo/runs/DEMO-T1-run/threshold/stimuli.json
cp generation/FREEZE-v1.0.draft.json generation/out/rehearsal/freeze-draft.json
uv run --project generation python -m av_generation.freeze record \
    separation.evidence_sha256 --text <summary-sha256> \
    --source "SYNTHETIC rehearsal" --manifest generation/out/rehearsal/freeze-draft.json
```

Expected: 224 pairs and `"problems": []`; a session plan of 224 trials (remove
`--plan-only` to serve the page and try it in a browser); 8 bot-listener sessions,
each `"ok": true` with 224 played and answered; the export repeats the same
`summary.json` SHA-256 (use it as `<summary-sha256>`); `recorded
separation.evidence_sha256 in freeze-draft.json`. The `cp` line makes a copy, so the
committed draft is never touched.

## 8. Open changes that affect what you see

This runbook is written against `main`. These open pull requests change details:

- #262: the runner logs the startup of every sitting (model probe and renderer self-test)
  for the audit. A start can then also be refused with `E_RENDERER` (exit 1, nothing
  created): call the engineer.
- #302: panel orders for any number of batches. The 3-batch pilot keeps its orders.
- #303: the station page shows "Candidate k of 9" on every slot screen.
- #301: a bring-up script and service template for the GPU host; section 3.3 stays valid.

## 9. Known gaps

- The terminal shows no progress during a sitting (only start and end). Progress is
  visible on the kiosk and the stations.
- There is no command to mark a batch incomplete when a rater withdraws between
  sittings. Do not start the next sitting; the open run is left out of the set audit;
  write a deviation note and rebuild (section 5.3).
- Until #262 is merged, the runner logs no startup time, so the audit's `startup_ms` is 0
  for pilot sittings. Write the model server's `READY ... startup_ms=...` line in the
  notes.
- A withdrawal cannot be rehearsed with bots from the command line; rehearse it with
  people (section 3.5, step 4).
- Each station URL carries a 32-digit seat key, and there is no short code. Type it on
  the station, or use a way that keeps no copy (no chat, no e-mail, no notes).
