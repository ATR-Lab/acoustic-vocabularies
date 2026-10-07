# Synthetic-panel dry run (#22)

Module `av_generation.dryrun`. It runs one Study A batch (16 atoms x 3 methods) through
the real generation system with bots in place of people, then checks that the logs are
complete and records the timing. Requirements: Study A protocol §3.3 (12 slots per atom
and method, 20-s rating slots, at most 120 s of proposals and 180 s of ratings per
round, model and renderer preloaded with startup logged apart, bookings extended if
proposals serialize) and §3.7 (bank fallback, whole-book fallback).

What runs, and what stands in for people or hardware:

| Part | In the dry run |
| --- | --- |
| Orchestrator, selector, panel session host (#20) | Real: `batch_runner.open_batch` / `run_session`, one station session per appointment |
| Slot ledger, A3 proposer and prompts (#17), A2 (#18) | Real |
| LLM server (#16) | #16's mock server (`mock_llm`) in the driver's process, answering after a set latency; the pinned model on the LLM GPU host is **pending (hardware)** (section 9) |
| A1 interface (#19) | Real A1 web app, worked over HTTP by a bot designer (`ThinkingBotDesigner`, a subclass of #20's `_batch_sim.BotDesigner`) |
| Rater panel (#21) | Real panel server; three `rater.BotRater` stations join through the keyed station URLs of each session |

Everything is labelled `synthetic`: run kind `synthetic`, run purpose `dry_run`, `DEMO-`
run, batch and book IDs, and the `DEMO-DRY` namespaces. Nothing enters a pilot or
confirmatory book.

## 1. Commands

From the repository root. `--out` takes the runs root; keep full runs outside git (for
example `generation/out/`, which git ignores). `--evidence` takes the directory for the
small text evidence (section 7).

```bash
# (a) full batch, accelerated clock (about 14 min at 20x)
uv run --project generation python -m av_generation.dryrun run --out generation/out/dry \
    --run-id DEMO-dry-run-accel-01 --clock scaled --speed 20 \
    --evidence generation/runs/DEMO-dry-run-accel-01

# (b) one appointment of 4 atoms in real time against the mock server (about 70 min)
uv run --project generation python -m av_generation.dryrun run --out generation/out/dry \
    --run-id DEMO-dry-run-rt-01 --clock real --appointments 1 \
    --evidence generation/runs/DEMO-dry-run-rt-01

# (c) whole-book fallback, accelerated clock (about 6 min at 50x)
uv run --project generation python -m av_generation.dryrun run --out generation/out/dry \
    --run-id DEMO-dry-run-book-01 --clock scaled --speed 50 --whole-book \
    --evidence generation/runs/DEMO-dry-run-book-01

# checks on an existing run directory
uv run --project generation python -m av_generation.dryrun check <run_dir>
uv run --project generation python -m av_generation.dryrun timing <run_dir> --out <dir>
uv run --project generation python -m av_generation.dryrun tally <run_dir> [--audit <dir>]
uv run --project generation python -m av_generation.dryrun evidence <run_dir> --out <dir>
```

`run` options:

| Option | Default | Meaning |
| --- | --- | --- |
| `--clock real\|scaled`, `--speed` | `scaled`, 20 | Real time, or `clock.ScaledClock(speed)` |
| `--appointments` | `all` | `all`, or a list such as `1` or `1,2` (a reduced run) |
| `--whole-book` | off | The single-recipe bank (section 2); the second zero-eligible atom of a book substitutes it |
| `--zero-eligible METHOD@POSITION` | see section 2 | An atom to force to zero eligible candidates (repeatable), for example `A2@7` |
| `--llm-url` | none: the mock | An external OpenAI-compatible server; it must pass the runner's probe |
| `--mock-latency-ms` | 3000 | Clock time the mock server waits before each answer |
| `--think-ms LOW:HIGH` or `none` | `10000:35000` | Bot designer think time per slot (clock ms, uniform) |
| `--station-timeout-s` | 120 | Real seconds to wait for the bot stations of a session |
| `--resume` | off | Reopen an interrupted dry run with its stored plan and run the unfinished appointments |

Exit codes: 0 when the completeness check, the tallies and both timing limits pass; 1
when one of them fails; 2 when the run or a file is refused (`error: ...`). Python API:
`run_dry_run(out, run_id, *, clock, ...) -> DryRunResult`, `check_log_completeness(run_dir,
*, plan=None) -> CompletenessReport`, `timing_rows`, `write_timing`, `tally_logs`,
`compare_with_audit`, `write_hand_tally`, `write_bundle`, `write_evidence`,
`dry_run_config`, `make_plan`, `single_recipe_fallback`.

## 2. Synthetic batch config and the injected cases

`dry_run_config()` takes the profile (P1), atom order, label permutation and books of
the committed DEMO batch config and gives the batch its own namespaces: batch
`DEMO-DRY-P01`, seed namespace `DEMO-DRY-P01-s1` (the `<batch_ns>` of every A1, A2 and A3
seed key), panel `DEMO-DRY-P01-N1` with its order index and aliases drawn from the
`DEMO-DRY` set namespace (`orchestrator.panel_order_schedule`, `panel_aliases`), and three
`bot` seats (R01/S1, R02/S2, R03/S3).

Before the first slot the driver writes `dry-run-plan.json` (`DryRunPlan`,
`dry-run-plan.schema.json`) into the run directory. The checks compare the logs with it.

| Field | Meaning |
| --- | --- |
| `appointments` | The run's scope (a reduced run covers fewer than four) |
| `zero_eligible` | Atoms forced to zero eligible candidates, each with the fallback it must cause (`bank` or `book`) |
| `force_unacceptable_slots` | The 12 rating-slot IDs of each such atom in its book (`BatchConfig.rating_slot_ids`); every bot rates them comfort `unacceptable` |
| `designer_invalid_slots`, `designer_timeout_slots` | A1 proposal slots where the bot designer submits `"{"` or nothing |
| `designer_think_ms` | The bot designer's think time per slot |
| `p_comfort_acceptable` | 0.9 |
| `mock_llm_latency_ms` | The mock server's latency (`null` with an external server) |
| `fallback_bank` | `demo`, or `single_recipe` for the whole-book run |
| `token_count_cap_ms` | The A3 token-count cap of an accelerated run (`null` in real time) |

- **Bot raters** (`rater.BotRatingPolicy`): comfort `acceptable` with probability 0.9,
  association and distinguishability uniform on 1-7, response 0.4-4 s after the unlock,
  all drawn from `BOT|<run>|<rater>|rating|<rating slot>`. The bots see rating-slot IDs
  only, never a book ID.
- **Zero-eligible atom** (default): the third atom of the second appointment in scope
  (the third atom of a one-appointment run) in the A2 book, which expects a `bank`
  fallback.
- **Bot designer**: per appointment, 2 invalid and 2 skipped slots of the A1 book, drawn
  from `BOT|<run>|driver|inject|<appointment>`. Skipped slots fall in different rounds,
  so no A1 proposal window reaches 120 s. Before each other submit the designer waits
  its think time (`BOT|<run>|<designer>|think|<slot>`).
- **Whole-book run** (`--whole-book`): every bank entry of the profile becomes the
  bank's recipe 0 (`single_recipe_fallback`). The defaults are atoms 1 and 2 of the A3
  book. At atom 1 the scan commits bank recipe 0 (`bank`). At atom 2 the scan finds
  entry 0 used and every other entry a duplicate of it, so it is exhausted and the
  frozen fallback book is substituted (`book`). The batch config pins this set's bank
  hash, and `fallback_manifest_sha256` is the SHA-256 of its compact canonical manifest.
- **Accelerated clocks**: HTTP overhead does not speed up with the clock. #16's 5-s
  token-count cap is 50 ms of real time at 100x, so on a slow computer A3 slots fail as
  `invalid_json` (`server_error`). Accelerated runs therefore set the client's count cap
  to the 40-s slot cap; the slot deadline still bounds it. Real-time runs keep 5 s.

## 3. Startup

Before the run directory exists the driver times two startup intervals on the run
clock and then logs them as `startup_start` / `startup_end` timing events, apart from
the slots:

- `llm`: starting the mock server (no model weights), or waiting for an external server's
  `GET /health`, then one warm-up call (not logged in `llm-requests.jsonl`);
- `renderer`: rendering and validating one recipe (bank recipe 0 of the profile).

With the GPU server the model load itself happens in `llm_server serve`, which logs its
own `startup_*` events to the file given with `--timing-log` (section 9).

## 4. Log-completeness check

`check_log_completeness(run_dir, *, plan=None)` reads the run directory only and lists
every problem. It does not raise for a problem it finds. Counts scale with the plan's
appointments: a full batch has 16 atoms, and a one-appointment run has 4 atoms with 144
slot and rating records. A book substituted by its fallback book always has 16 commits
in its final store book.

| Check | Full batch |
| --- | --- |
| Commits in each book's final store book (`<book>-FB` after a substitution), each verified in the vocabulary store (`verify`, anchored at the last chain head) with the commit's `pcm_sha256` and `file_sha256` equal to the stored waveform | 48 (16 per book) |
| Slot records, per method, each with an outcome code | 576, 192 per method |
| Replenished slots (more than 12 per book and atom, or indexes not 1..12 once each) and repeated slot IDs | 0 |
| The bot designer's injected slots | invalid -> `invalid_json`, skipped -> `timeout` |
| Rating records per rater, placeholders and missing included, over exactly the scheduled rating slots, `rater_kind` `bot` | 576 |
| A placeholder record exactly where the candidate is invalid | every record |
| Decision records | 192 |
| Plays with `audio_kind` `message`, or any non-atomic audio | 0 |
| Fallback scans, `fallback_scan` decisions, `fallback_bank` commits and `book_substituted` events | equal to `zero_eligible`; a bank case's scan selected its commit's `bank_index`; a book case's scan is `exhausted` with 16 `fallback_book` commits |
| Timing events: every atom, round and appointment start and end, `startup_*` of `llm` and `renderer` | present |
| Run manifest closed exactly when all 16 atoms ran; every file it hashes unchanged | yes |

`python -m av_generation.dryrun check <run_dir>` prints one `name value` line per count
and one `problem: ...` line per problem.

## 5. Timing

`write_timing(run_dir, out_dir)` writes `timing.csv` and `timing-summary.json`. The
intervals pair start and end events in log order. A duration is the end event's
`duration_ms` when it has one, or the `t_ms` difference on one run clock. Events of two
processes (for example after `--resume`) are measured on `wall_utc`.

`timing.csv` columns: `level` (`startup`, `appointment`, `atom`, `round`), `component`,
`appointment`, `atom_position`, `atom_id`, `round`, `start_ms`, `end_ms`, `duration_ms`,
`proposal_window_ms`, `rating_window_ms` (rounds), `budget_ms` (300 000 per round,
1 200 000 per atom, 4 800 000 per appointment) and `within_budget` (0/1).
`timing-summary.json` holds the longest round, atom and appointment and the checks
`atom_ok` (at most 20 min), `appointment_ok` (at most 80 min) and
`appointment_within_booking` (at most 90 min).

Times are run-clock milliseconds. In an accelerated run every real overhead is
multiplied by the speed, so only a real-time run measures the timing; accelerated runs
check the logs.

## 6. Audit and independent tally

The evidence step runs the #24 audit on the run (`audit.build_audit(run_dir,
run_dir/"audit")`). It then compares the audit with two recounts:

- `tally_logs` / `compare_with_audit` (`tally-comparison.csv`): a recount from the raw
  JSONL lines with the standard library only (no record classes, no audit code) of every
  count column of the unmasked `books.csv` (slots, the 14 outcome codes, server errors,
  atoms by source, the failed-generation and nonfallback flags, `total_ms_*`), plus the
  batch's fallback scans and slot refusals against `summary.json` `checks`;
- `write_hand_tally` (`hand-tally.csv`): #24's own tally sheet (`audit.tally_sheet`:
  #24's raw-line count and the audit count per book and quantity), with the
  `hand_count` column filled from `tally_logs`. Every row must agree three ways.

## 7. Evidence directory and bundle

`write_evidence(run_dir, evidence_dir, *, bundle_dir=None, result=None)` (CLI `run`, or
`evidence` for an existing run) writes small text files only:

| File | Contents |
| --- | --- |
| `completeness.txt` | The check's counts and problems |
| `timing.csv`, `timing-summary.json` | Section 5 |
| `audit-books.csv`, `audit-summary.md` | The unmasked #24 `books.csv` and summary (DEMO run) |
| `tally-comparison.csv`, `hand-tally.csv` | Section 6 |
| `manifest.json` | `av-generation/dry-run-evidence` v1: run and plan facts, SHA-256 of the run documents, of every log and of every evidence file, the bundle's SHA-256, size and file count, the audit, completeness, timing and tally results, the machine (OS, release, architecture, CPU count, Python; no host name), and the driver's sessions (`<run_id>-driver.json`: per appointment the stations' slots, ratings, missing ratings and reconnects, and the designer's outcomes, late submits, plays and think time) |

The bundle `<run_id>-bundle.tar.gz` is a deterministic tar.gz of the whole run
directory: logs, documents, the vocabulary store with its WAV blobs, and the audit.
Paths are sorted under `<run_id>/`, with mtime 0, owner 0 and mode 0644, and gzip mtime 0.
It goes next to the run directory, outside git. A full batch's bundle is about 3 MB.
In CI, `tests/generation/test_dry_run.py` writes the reduced end-to-end run (one
appointment at 100x, with the bank and whole-book fallbacks injected), its evidence and
its bundle to `generation/out/ci/dry-run/`, which the `generation-ci-<os>` artifact
uploads.

The committed evidence is under `generation/runs/DEMO-dry-run-*/`.

## 8. Evidence of this implementation

Three runs on one computer (Apple arm64, 14 CPUs, macOS, Python 3.11) against #16's mock
server (3 s per call). Their evidence is in `generation/runs/DEMO-dry-run-accel-01/`,
`DEMO-dry-run-rt-01/` and `DEMO-dry-run-book-01/`.

| | (a) `accel-01` | (b) `rt-01` | (c) `book-01` |
| --- | --- | --- | --- |
| Clock, scope | 20x, 4 appointments | real time, appointment 1 | 50x, 4 appointments, single-recipe bank |
| Real time | 13 min 39 s | 66 min 18 s | 5 min 34 s |
| Commits in final store books (each with its SHA-256 in the store) | 48 (16 per book) | 12 (4 per book) | 48 (16 per book), plus 1 superseded |
| Slot records (A1 / A2 / A3), replenished | 576 (192 / 192 / 192), 0 | 144 (48 / 48 / 48), 0 | 576 (192 / 192 / 192), 0 |
| Rating records per rater (R01 / R02 / R03) | 576 / 576 / 576 | 144 / 144 / 144 | 576 / 576 / 576 |
| Placeholder records, missing ratings | 147, 0 | 33, 0 | 195, 0 |
| Decisions | 192 | 48 | 192 |
| Message plays | 0 | 0 | 0 |
| Injected zero-eligible atoms = fallback events | 1 = 1: A2 book, `Q-r1`, bank index 0 | 1 = 1: A2 book, `Q-r2`, bank index 0 | 2 = 2: A3 book, `Q-a2` bank index 0; `K-a4` scan exhausted (64 entries: 1 used, 63 rejected with `E_DUPLICATE` and `E_SEPARATION`), substituted by `DEMO-BK-H9TC-FB` (16 `fallback_book` commits), 14 later atoms `archive` |
| Designer: injected invalid / skipped slots, as logged | 8 / 8 | 2 / 2 | 8 / 8 |
| Longest atom / appointment (run clock) | 17 min 28 s / 68 min 14 s | **16 min 49 s / 66 min 14 s** | 17 min 27 s / 68 min 32 s |
| #24 audit | `ok`, 0 problems | 57 problems, all from the 12 atoms a one-appointment run leaves out | `ok`, 0 problems |
| Independent tally vs audit; #24 sheet | 86 of 86 rows; 75 of 75 rows agree three ways | 86 of 86; 75 of 75 | 86 of 86; 75 of 75 |
| Bundle SHA-256 (bytes, files) | `ebf04248...78f2` (2 889 126, 85) | `8aedd766...f125` (705 547, 48) | `c3fb6e65...be` (2 971 136, 89) |

Real-time timing (b), from `timing.csv`:

| Item | Measured |
| --- | --- |
| Atoms | 16 min 49 s, 16 min 23 s, 16 min 29 s, 16 min 33 s (limit 20 min) |
| Appointment | 66 min 14 s (limit 80 min, booking 90 min) |
| Rounds | 222.5-268.3 s, mean 248.4 s (limit 300 s) |
| Proposal windows | 41.5-87.3 s. The three methods run in parallel: A1 (bot designer) 67.4 s per round on average and 87.3 s at most; A3 9.1 s (three calls of 3.0 s, one after another, as the slots are); A2 0.01 s |
| Rating windows | 181.002-181.015 s: the 1-s preload lead plus 9 slots of 20 s |
| Everything else per round (decisions, feedback, events) | 20 ms on average, 30 ms at most |
| Between atoms | 1 ms |
| Startup | model 3.1 s (mock start and one 3-s warm-up call), renderer 3 ms |

So the round time is the proposal window + 181.0 s. With the bot designer's think time,
the dry run stays 3 min 11 s under the atom limit and 13 min 46 s under the appointment
limit. A round whose proposal window uses all 120 s lasts 301.0 s. An atom of four such
rounds takes 20 min 4 s and such an appointment 80 min 16 s: the 1-s preload lead per
round goes over the 20-min and 80-min figures of Study A §3.3, but stays within the
90-min booking. Proposals do not serialize. The accelerated runs check the logs only:
their clock times include every real overhead multiplied by the speed (outside the
windows 0.9 s of clock time per round at 20x, against 0.02 s in real time).

Problems found, and suggested follow-ups:

1. **Accelerated clocks multiply fixed real overheads.** #16's 5-s token-count cap is
   250 ms of real time at 20x and 25 ms at 200x. In #20's CI it failed every A3 count on
   Windows at 200x. The dry run uses the slot cap in accelerated runs
   (`token_count_cap_ms`). Follow-up: let `batch_runner.open_batch` set the client's
   count cap, or scale it with `ScaledClock.speed`.
2. **The study-mode runner logs no startup.** `batch_runner` writes no `startup_*`
   events, so the #24 audit's `startup_ms` stays 0 for a lab batch. The model load is only
   in `llm_server`'s own timing file on the LLM host. Follow-up: time the LLM probe and
   warm-up, the renderer self-test and the A1 and panel servers in `open_batch` /
   `run_session` for every sitting.
3. **No handle to the served A1 page.** `run_session` exposes the panel
   (`StudyBatch.served_panel`) but not the A1 page, and it takes no designer factory. The
   dry run reads the operator line `A1 designer page: <url>`. Follow-up: add
   `StudyBatch.served_a1` and a designer factory.
4. **The preload lead goes over the round budget when the window is full** (see above).
   This is a note for O6.2.1: book 90 minutes per appointment, not 80, if designers may
   use the whole 120-s window. Follow-up (optional): publish the round's preload while
   the decisions of the previous round are written.
5. **A late A1 submit at 20x.** In run (a), 1 of 184 designer submits arrived after
   its 40-s deadline: think time near 35 s plus 20 times the HTTP and render overhead. It was
   refused (`slot_closed`) and the slot ended as `timeout` (9 A1 timeouts: 8 injected and
   this one). It does not happen in real time.
6. **Load-sensitive tests.** While the full suite ran beside the dry runs on the same
   computer, `test_rater_panel_runner.py::test_keyed_bot_stations_run_an_appointment_through_the_runner`
   (100x station run) and `test_audit.py::test_csv_cells` (a Hypothesis health check)
   failed once. Both pass alone. Follow-up: slow the station test down or loosen it, and
   relax the health check.
7. **CI time.** In the first CI run of this module, `test_dry_run.py` took 4 min 29 s on
   the Windows runner (Ubuntu 3 min 21 s, macOS 2 min 29 s); the Windows test step took
   30 min 29 s of the job's 45 min. Its fixtures now make `os.fsync` a no-op (fsynced log
   lines are slow on Windows, and durability is not under test there), and the
   end-to-end run uses 100x: about 60 s locally, 35 s of it end to end. Follow-up: split
   the generation test job before more end-to-end tests land.

## 9. Pending: real-time batch with the pinned model (hardware)

The real-time batch with `Qwen/Qwen2.5-7B-Instruct` needs the LLM GPU host (O1.3.3)
running the pinned vLLM. On the LLM host, with the verified model directory and the
pinned vLLM environment (#16, `generation/docs/llm.md`):

```bash
python -m av_generation.llm_server serve --model-dir <model dir> --vllm <vllm executable> \
    --host <station-network address> --timing-log <dir>/DEMO-dry-run-gpu-01-llm-startup.jsonl \
    --run-id DEMO-dry-run-gpu-01
```

On the orchestration computer, once the server prints `READY`:

```bash
uv run --project generation python -m av_generation.dryrun run --out generation/out/dry \
    --run-id DEMO-dry-run-gpu-01 --clock real --appointments all \
    --llm-url http://<llm-host>:8000 --evidence generation/runs/DEMO-dry-run-gpu-01
```

The run takes about 4 h 40 min plus startup. After an interruption, add `--resume` to the
same command. The runner's probe accepts only the pinned model and vLLM version. Keep
`DEMO-dry-run-gpu-01-llm-startup.jsonl` with the bundle: it holds the model load time.
The result answers the open question of §3.3: whether proposals serialize or the
transitions run long enough that the panel bookings must be extended.

## 10. Decisions

| Item | Decision | Rationale |
| --- | --- | --- |
| Driver location | `av_generation.dryrun` (the skeleton's #22 module), CLI `python -m av_generation.dryrun` | One module, which the G4 freeze leaves out of `generation.code` (`freeze.GENERATION_CODE_EXCLUDED`): a dry-run tool is not study code, and a new module would change the frozen code hash |
| Synthetic batch config | The DEMO batch's profile, atom order and books, with batch `DEMO-DRY-P01`, seed namespace `DEMO-DRY-P01-s1` and the panel order and aliases drawn from set namespace `DEMO-DRY` by the real schedule functions | The dry run's seeds differ from every other DEMO run, and the panel goes through the same code as a real batch |
| Bot raters (Proposed: comfort acceptable p = 0.9, association and distinguishability uniform on 1-7) | Adopted: `rater.BotRatingPolicy(p_comfort_acceptable=0.9)`, seeded per run, rater and rating slot | The issue's proposal; a candidate is then eligible with probability 0.972, so a zero-eligible atom happens only where it is injected |
| Bot designer think time | Uniform 10-35 s per slot, seeded per slot; none before an invalid submit | With instant submits the A1 window would end after about a second, and the timing would not test the proposal window. 35 s keeps a slot under its 40-s cap, and with at most one skipped slot per round an A1 window stays at or below 110 s |
| Mock model latency | 3 s of clock time per call | A 7B model's JSON recipe on one GPU, until the GPU run measures the real latency |
| Injected cases | Per appointment 2 invalid and 2 skipped A1 slots (seeded; skipped slots in different rounds). One zero-eligible atom: the A2 book, third atom of the second appointment, `bank`. Whole-book run: A3 atoms 1 and 2 | Each case occurs in every appointment. The zero-eligible atom has committed references, so its scan checks the bank against the book. In the whole-book run, atom 1 still exercises the bank path |
| Forcing the whole-book fallback | A bank whose 64 entries are all the profile's recipe 0 (`single_recipe_fallback`), held in memory and pinned by its bank hash | The scan code and the protocol rule (no bank recipe passes the book's checks) are unchanged. The committed DEMO bank never runs out within 16 atoms |
| Token-count cap on accelerated clocks | The 40-s slot cap instead of #16's 5 s (`token_count_cap_ms`, recorded in the plan); real time keeps 5 s | A fixed real overhead is multiplied by the speed: at 200x the Windows runner of #20 lost all 192 A3 token counts |
| Startup | The driver times the model (mock start or external health check, plus one warm-up call) and the renderer (one render and validation) and logs `startup_*` events; with the GPU server the model load is in `llm_server`'s own timing log | Study A §3.3 asks for preload with startup logged apart. The runner of #20 logs no startup |
| Plan schema (owned) | Adds `appointments`, `fallback_bank`, `designer_think_ms`, `mock_llm_latency_ms`, `p_comfort_acceptable`, `token_count_cap_ms`, all required; format version stays 1 | Every key is always present (architecture section 4). No plan document existed before |
| Reduced runs | Counts scale with `appointments`; a substituted book always has 16 commits in its final store book | The CI test and the real-time run cover one appointment |
| Timing | Run-clock milliseconds. An interval split across processes is measured on `wall_utc`, downtime included | A conservative upper bound for the 20-min check. Accelerated runs do not test the limits |
| Hand tally (#24) | Filled by the independent standard-library recount (`hand-tally.csv`); the three counts must agree | #24 asked for a hand count of the dry-run logs. A person can still fill the column from the bundle |
| Committed evidence | Text files only, under `generation/runs/DEMO-dry-run-*/`. The unmasked #24 tables are included because the run is DEMO, as in `DEMO-AUDIT-01`. The bundle stays outside git; its SHA-256 is in `manifest.json` | Architecture section 8; no binaries in git |
| A1 page URL | Read from the runner's operator line `A1 designer page: <url>`; the designer starts in `on_panel` | `run_session` gives no other handle to the served A1 page. The alternative was changing #20's `batch_runner`, which is frozen code |
