# Round orchestrator, common selector and panel session host (#20)

Modules: `av_generation.orchestrator` (public API), `av_generation.selector`,
`av_generation.batch_runner` (the study-mode batch runner and its command line, section
9), and the private `_orch_host` (panel session host), `_orch_index` (log index) and
`_batch_sim` (stand-ins for tests, evidence and dry runs). Protocol references: Study A protocol §3.1
(matching, counterbalancing, book-ID rotation), §3.3 (budget, rating slots, selector,
withdrawal), §3.7 (fallback). Architecture: [`architecture.md`](architecture.md) §3-§5.

## 1. Public API

| Name | Signature | Purpose |
| --- | --- | --- |
| `Orchestrator` | `(config, layout, proposers, store, fallback, *, clock, generation_config, meanings, kind, freeze_manifest=None, purpose="batch", preload_lead_ms=1000, slot_event_lead_ms=500)` | Runs one Study A batch |
| `.run_atom(atom_id)` | `-> None` | The next atom of the stored order: 4 rounds, then commit or fallback |
| `.run_appointment(n)` | `-> None` | Atoms `4(n-1)+1 .. 4n` (finished atoms are skipped) |
| `.run_batch()` | `-> None` | Appointments 1..4 |
| `.resume()` | `-> str \| None` | Repair torn log tails, reload the logs, finish an interrupted atom; returns the next atom |
| `.panel_host()` | `-> PanelSessionHost` | The session API for the panel server (#21) |
| `.console()` | `-> ConsoleView` | Operator console data: panel aliases only; safe to poll from another thread |
| `.mark_incomplete(reason)` | `-> None` | Operator stop (e.g. a withdrawal between appointments) |
| `.next_atom()`, `.book_state(book_id, atom_id)`, `.incomplete`, `.config`, `.layout` | | State queries (restricted) |
| `panel_order_schedule` | `(set_ns, panel_ids, *, profiles=None) -> Mapping[str, int]` | Panel -> order index 1..6 (`profiles`: panel -> batch profile) |
| `panel_aliases` | `(set_ns, panel_id, book_ids) -> Mapping[str, str]` | Book -> `PB-XXXX` alias for one panel |
| `panel_order_rows`, `write_panel_order_csv` | `(set_ns, [(panel_id, batch_id), ...], *, profiles=None)` | The schedule CSV (`PANEL_ORDER_COLUMNS`) |
| `read_batch_table` | `(path) -> tuple[BatchDefinition, ...]` | Schedules batch table (#29) |
| `check_permutation` | `(definition, permutation_json) -> None` | Batch table row == the unit's `permutation.json` |
| `read_book_key` | `(path_or_mapping, unit_id) -> tuple[BookAssignment, ...]` | Schedules book key (#31) |
| `build_batch_config` | `(definition, books, *, set_ns, panel_id, order_index, raters, threshold, fallback, fallback_manifest_sha256, batch_id=None, seed_namespace=None, sources=None) -> BatchConfig` | One batch config |
| `rebuild_batch_config` | `(config, *, set_ns, panel_id, raters, seed_namespace) -> BatchConfig` | Config of a rebuilt batch |
| `check_batch_pins` | `(config, generation_config, fallback, meanings, *, kind) -> None` | Input pins and set/kind rule (`E_CONFIG`, `E_KIND`); run by `Orchestrator` and the batch runner |
| `nearest_committed` | `(book: BookState, recipe) -> CommittedAtom \| None` | Nearest-reference lookup |
| `substitute_book_id` | `(book_id) -> str` | Store book of a substituted book (`<book>-FB`) |
| `RatingSlotPlan`, `ConsoleView`, `BookConsole`, `BatchDefinition` | dataclasses | |
| `OrchestratorError(code, message)`, `BatchIncomplete` | exceptions | Codes below |
| `selector.score_candidate` | `(slot, ratings, *, first_atom) -> CandidateScore` | Eligibility and exact score |
| `selector.pick_incumbent` | `(candidates) -> (slot_id \| None, Fraction \| None)` | Best eligible, ties to the lowest slot |
| `selector.format_score`, `selector.parse_score` | | Exact score text (`9/2`) |

Error codes (`OrchestratorError.code`): `E_CONFIG` (threshold, fallback or meaning pins
differ), `E_KIND` (batch set vs run kind), `E_PROPOSERS`, `E_PROPOSER_RESULT` (a round
result breaks the `RoundProposer` contract), `E_PROPOSER_FAILED`, `E_ATOM_ORDER`,
`E_ATOM_DONE`, `E_APPOINTMENT`, `E_RESUME_PARTIAL_WINDOW`, `E_RUN_MISMATCH` (reopening a
run with another config), `E_BATCH_INCOMPLETE`, `E_STORE`, `E_SCHEDULE`,
`E_BATCH_TABLE`, `E_REBUILD`. `genconfig.ConfigMismatch` comes from
`check_run_config` (frozen-config rules).

## 2. Batch configs

A `BatchConfig` (`config.py`, restricted unless DEMO) is built from the schedules files,
which are the single source of the batch definitions:

1. `read_batch_table(<set>-batch-table.csv)`: profile, designer, stored atom order and
   the label permutation (`K_action` etc. list the labels of matrix indices 1..4).
2. `check_permutation(definition, permutation.json)`: refuses a disagreement.
3. `read_book_key(<set>-book-key.json, unit_id)`: the three anonymous books and methods.
4. `panel_order_schedule(set_ns, panel_ids, profiles={panel_id: definition.profile})`
   gives the batch's order index;
   `build_batch_config(...)` orders the books by `PANEL_ORDERS[order_index - 1]`, draws
   the panel aliases and checks the result (`check_consistency`).

Panel order schedule. Pass every panel's batch profile (the batch table's `profile`
column; the table interleaves profiles, e.g. 4 x P1, 4 x P2, 4 x P3, 2 x P2, 2 x P1,
2 x P3). One seeded stream (`rng_for(panel_seed_key(set_ns, "orders"))`) draws one
permutation of the six orders per profile (per six panels of that profile), in sorted
profile order, each uniformly among the permutations that differ at every position
from the profiles drawn before it. So each profile's six batches use every order once,
18 panels use each order exactly 3 times, and panels at the same position within their
profiles get different orders: the three pilot panels (one per profile) use three
different orders. The CSV has a `profile` column. Without `profiles` the panels form one
group (each six consecutive panels use every order once, no balance within a profile).

Aliases (book-ID rotation). Per panel, `rng_for(panel_seed_key(set_ns, "aliases",
panel_id))` draws one alias per book in sorted book-ID order (`PB-` + 4 characters of
`ids.PANEL_ALIAS_ALPHABET`, redrawn on a repeat). The operator console shows aliases
only; stations see neither books nor aliases.

`set_ns` is a stored seed. For pilot and confirmatory sets it must be a restricted
namespace (for example `A-C-` + 16 random hex characters), kept with the restricted
batch configs: rating-slot IDs show the batch ID to the stations, so a schedule computable
from public code and a public namespace would let a rater map rating positions to
methods. DEMO sets use `DEMO-A-P` and `DEMO-A-C`; their schedules are committed in
`generation/examples/demo-panel-orders/` (regenerate with
`python -m av_generation._batch_sim --panel-orders --out generation/examples/demo-panel-orders`).

## 3. One batch

Opening (`Orchestrator(...)`): the batch config passes `check_consistency`; the run ID
fits the run kind (`rundir.check_run_id`); the generation config, batch config, fallback
set and meaning set pin the same threshold, fallback hashes and meaning hash (`E_CONFIG`);
the batch set fits the run kind (demo -> demo/synthetic, pilot -> pilot, confirmatory
-> confirmatory); `genconfig.check_run_config` passes (a confirmatory run needs the
frozen G4 manifest). The run writes `config.json`, `generation-config.json` and
`run-manifest.json` (or checks them when reopened), repairs torn log tails (one
`log_repaired` event each), loads its logs, creates the three store books (kind
`synthetic` for `DEMO-` books, else `study`, threshold of the batch) and logs `run_start`.

Per atom (in `atom_order`; `atom_start`, then for rounds 1..4):

1. **Proposals.** For rounds 2-4, `feedback_sent` per book. Each book gets a
   `RoundRequest` with its own `BookState` and `AtomFeedback` only (A2: the label-free
   state and no label). The three `propose_round` calls run in parallel threads
   (`proposal_window_start/end`). Each proposer caps its own slots at 40 s (A1 server
   timer, A3 client timeout: `RoundProposer` contract); the orchestrator checks the
   three records of every result (`E_PROPOSER_RESULT`) and notes `window_overrun_ms`
   and `slots_over_cap` in `proposal_window_end.detail` when the window or a slot ran
   long.
2. **Ratings.** Positions 1..9 follow `panel.order` (block b holds the book's slots 1-3 in
   slot order). An invalid candidate is a placeholder slot (no audio, no meaning, no
   controls). A valid candidate plays at 0 s with its meaning; the nearest committed atom
   of the same book (12-feature distance, ties to the lowest commit index;
   `nearest_committed`) plays at 2 s with its meaning; nothing plays on the first atom.
   Controls unlock when the reference ends (2 s without one). Timeline: `preload` event,
   slot 1 starts `preload_lead_ms` (1 s) later, slots every 20 s; each later `slot`
   event is published `slot_event_lead_ms` (0.5 s) before its start. At each lock
   (start + 20 s) the host writes one `rating` record per seat.
3. **Decisions.** Per book one `decision` record: this round's candidates
   (`score_candidate`), the incumbent over every candidate of the atom so far
   (`pick_incumbent`), `incumbent_changed`, and the action.

After round 4 (`_finish_atom`), per book:

| Final decision | What happens | Records |
| --- | --- | --- |
| `commit` | the incumbent is committed (`source` = its slot ID) | `commit` (`selector`) |
| `fallback_scan` | `scan_fallback(bank, store entries, used=...)`; the first unused passing recipe is committed | `fallback_scan`, `commit` (`fallback_bank`, `bank_index`) |
| `fallback_scan`, scan exhausted | the store book is voided (`failed_generation`), the profile's fallback book is committed to `<book>-FB` in stored order | `fallback_scan`, 16 `commit` (`fallback_book`, `failed_generation=true`), timing `book_substituted` |
| `archive` / `archive_none` (book substituted earlier) | nothing is committed; the incumbent joins the continued book state | none |

Between atoms of an appointment the panel gets `pause` (`between_atoms`); an appointment
ends with `end` (`appointment_complete`, or `batch_complete` after atom 16); the next
atom publishes `resume`. After the last atom the run logs `run_end` and closes its
manifest (`closed_utc` and the SHA-256 of every file).

Counts per batch (they hold with substitutions): 576 `slot` records, 576 `rating`
records per rater, 192 `decision` records, 48 commits in the final store books
(+ k-1 superseded commits for a book substituted at atom k), 0 message plays.

Budget. The protocol's round is 120 s of proposals + 180 s of ratings. The preload lead
adds 1 s, so a round that uses the whole proposal window takes 301 s (an atom 20 min 4 s,
an appointment 80 min 16 s, within the 90-min booking). The proposal window usually ends
early (A2 and A3 take seconds); the round then starts rating at once.

## 4. Panel session host

`Orchestrator.panel_host()` implements `panel_session.PanelSessionHost` for the panel
server (#21); see that module for the division of work. Events: `preload` (assets of the
round), `slot`, `pause`, `resume`, `end`, with `seq` 1, 2, ... `snapshot()` returns the
slot in progress (or the next one), the state (`waiting`, `slot`, `paused`,
`between_atoms`, `ended`) and the round's assets. `asset_bytes(asset_id)` serves the
round's canonical WAVs (`KeyError` otherwise). An `end` for a withdrawal or an operator
stop (`withdrawn`, `aborted`) halts the session: no event follows it, and `snapshot()`
returns `ended` with no slot and no assets, also while the slot in progress runs to its
lock, so a station that rejoins shows the end screen.

Ratings (`submit_rating`) are checked in this order:

| Situation | `RatingAck.code` |
| --- | --- |
| rater/station pair is not a seat | `E_UNKNOWN_RATER` |
| rating-slot ID never scheduled in this run | `E_UNKNOWN_SLOT` |
| slot locked (received at or after start + 20 s, or already closed) | `E_SLOT_CLOSED` |
| placeholder slot | `E_PLACEHOLDER` |
| received before the controls unlock | `E_LOCKED` |
| distinguishability sent on a first-atom slot | `E_FIRST_ATOM` |
| distinguishability missing on another slot, or a value outside 1-7 | `E_PROTOCOL` |
| second rating of the rater for the slot | `E_DUPLICATE_RATING` |

`station_joined` refuses a rater, station or kind that is not a seat
(`PanelRefused("E_UNKNOWN_RATER")`) and logs `station_connect`, later
`station_reconnect` (a rejoin during a slot marks that seat's record `reconnected`).
`station_left` logs `station_disconnect`, `asset_ready` an `asset_ready` event (detail
`<asset_id> ok|failed`), `clock_synced` a `clock_sync` event. `report_play` writes a
`play` record (`rating_candidate` / `rating_reference`, `audio_kind="atom"`) and the
measured onset that goes into the rating record; a play of another asset or slot is
refused (`E_PROTOCOL`, `E_UNKNOWN_SLOT`). Free text in details is reduced to printable
ASCII.

Rating records at lock: the accepted rating (first atom: distinguishability 4,
`distinguishability_by_rule=true`), a `placeholder` record, or a `missing` record.
`rater_kind` comes from the seat.

## 5. Persistence, resume and crashes

All state is in the logs; `_orch_index.LogIndex` rebuilds it when a run is opened. A batch
pauses between atoms and resumes at the next appointment in a new process:

```python
orch = Orchestrator(config, layout, proposers, store, fallback, clock=SystemClock(), ...)
orch.resume()            # finishes an interrupted atom, returns the next atom
orch.run_appointment(2)
```

Within an atom, the orchestrator skips what the logs already hold: closed rounds, a
round's proposals when all three books have their three slot records, rating positions
whose three seat records exist, decisions and round-4 steps already logged (a store
commit repeated after a crash is a `recommit_noop`). A rating position with records for
only some seats is completed with `missing` records; positions without records are run
again (a crash mid-slot means the raters hear those candidates again: record a
deviation). A proposal window that left one or two slot records of a book cannot be
resumed (`E_RESUME_PARTIAL_WINDOW`: the proposer contract fills exactly three slots);
record a deviation and rebuild the batch. A proposer that failed before its first slot is
asked again (only that book).

A new process starts a new run clock (`t_ms` restarts at 0); `wall_utc` orders events
across restarts.

## 6. Rater withdrawal and rebuild

`report_withdrawal` (or `mark_incomplete(reason)` between appointments) logs
`rater_withdrawal` and `batch_incomplete`, publishes `end` (`withdrawn` / `aborted`),
and the run stops at the next slot lock with `BatchIncomplete` (the slot in progress
keeps its records; the next slot is never announced). Every record is kept;
the batch's store books (and a substituted `-FB` book) are voided with
`cause="batch_rebuild"`. Reopening the run raises `BatchIncomplete` again. Rebuild in a
new run directory and store:

```python
config2 = rebuild_batch_config(
    config, set_ns=SET_NS, panel_id="A-C07-N2", raters=new_seats, seed_namespace="A-C07-rb1"
)
```

The rebuilt batch keeps the books, profile, atom order, permutation and presentation
order, with an independent panel, new aliases and a new seed namespace. The audit (#24)
leaves runs with a `batch_incomplete` event out.

## 7. Decisions (#20)

| Item | Decision | Rationale |
| --- | --- | --- |
| Order within a block (Proposed) | Slot order 1, 2, 3 | Issue proposal; deterministic, already in `BatchConfig.rating_positions` |
| Missing ratings (Decision/Proposed) | A missing comfort counts as not acceptable; the score uses raters with both judgments and is flagged (`flagged_missing`); a candidate with no score is not eligible | Issue proposal; keeps the 2-of-3 rule strict and the score exact |
| Batch definitions (Proposed: generated here) | Read from the schedules batch table and checked against `permutation.json` | One source of truth: #29 already generates them with a stored seed; generating them twice could disagree |
| Order schedule over 18 panels | One seeded permutation of the 6 orders per profile (from the batch table's `profile` column), the profiles' permutations differing at every position | Each order exactly 3 times over 18 panels (§3.1), every profile's 6 batches get each order once although the batch table interleaves profiles, and the 3 pilot panels get 3 different orders |
| Rebuilt batches | Same order index and books, new panel, aliases and seed namespace | Counterbalancing is per batch; §3.3 asks for a new independent panel |
| Substitute store book | `<book>-FB`, commits in the fallback book's stored order | Anonymous, valid store ID; the store snapshot equals the frozen `book_sha256` |
| First atom | `first_atom` = the book's state has no committed atom | Equals "atom 1" in every normal run; after a substitution with nothing archived it keeps distinguishability fixed at 4 when no reference exists (§3.3) |
| Rating codes | `E_LOCKED` before unlock, `E_SLOT_CLOSED` after lock | The skeleton lists both codes without timing rules |
| `config_sha256` in the run manifest | `BatchConfig.sha256()` (canonical JSON) | Same hash definition as the generation config |
| `freeze_manifest_sha256` | SHA-256 of `jsonio.document_text(manifest)` | Equals the file hash of a manifest written with `write_document` |
| Study-mode runner (section 9) | A separate module, `batch_runner`, builds the batch from #16-#19 and #21 and keeps `Orchestrator` free of transport | The orchestrator stays testable with any `RoundProposer` and any panel; one place wires the real components |
| One start-check function | `batch_runner.check_batch_start`, run before the run directory exists | A refused start leaves nothing behind; #25 adds the G4 freeze guard in one place |
| LLM server check at start | `GET /v1/models` lists the pinned model and `GET /version` reports the pinned vLLM version; #16's mock is accepted for demo/synthetic runs only | The request log's `runtime` is then true, and a study batch cannot run against the mock |
| Stand-ins in the runner | Simulated proposers, the HTTP bot designer and bot raters only for demo/synthetic runs (`E_MODE`) | Accelerated tests and the dry run (#22) use the same runner; a study batch never has a bot in it |
| `--appointment next` | The appointment of the next unfinished atom, after `resume` finished an interrupted atom | One process per appointment, as booked (§3.1); an interrupted atom is finished first |

## 8. Synthetic runs (evidence)

`_batch_sim` runs the real orchestrator with simulated proposers (an A1 bot designer and
A2/A3 stand-ins drawing seeded uniform recipes, about 5 % scripted failures; from round 2
the A2 stand-in's slot 1 changes one coordinate of the incumbent it receives as feedback,
so the pinned digests depend on the feedback channel) and a
synthetic panel of three bot seats (`bot_seed_key` ratings, comfort acceptable with
p = 0.9):

```sh
# virtual clock driven by the panel (deterministic; pinned summary)
uv run --project generation python -m av_generation._batch_sim --out /tmp/runs \
  --run-id DEMO-A-virtual-01 --clock manual --summary summary.json
# accelerated real time (ScaledClock, 250x as in the CI test)
uv run --project generation python -m av_generation._batch_sim --out /tmp/runs \
  --run-id DEMO-A-accel-01 --clock scaled --speed 250
```

`generation/runs/DEMO-A-virtual-01/summary.json` holds the counts and the digests of
every log (SHA-256 over sorted lines); `tests/generation/test_orchestrator.py` re-runs
the batch on every CI OS and compares. CI writes the accelerated run's logs and summary
to `generation/out/ci/orchestrator/` (artifact `generation-ci-<os>`). In accelerated real
time a 20-s slot lasts 80 ms, so a bot whose thread a busy runner wakes late misses the
lock and its record is `missing`; the counts hold whatever the timing.

The real components run the same way in `tests/generation/test_batch_runner.py`
(section 9): ledger, A2, A3 against #16's mock server, A1 over HTTP with a bot designer and
bot raters, at 200x real time. CI writes that run's logs and summary to
`generation/out/ci/batch-runner/`.

## 9. Study-mode batch runner (`batch_runner`)

`Orchestrator` takes its proposers and panel from the caller. `batch_runner` builds them
from the real components and serves what people use during a batch:

| Component | Built by the runner |
| --- | --- |
| Slot ledger (#17) | One `SlotLedger` on the run's `logs/slots.jsonl` (refusals and timing to the run's logs), shared by A1, A2 and A3 |
| A3 (#17, #16) | `A3Proposer` with the prompt set and `OpenAICompatibleClient` on `--llm-url`; every call is logged to `logs/llm-requests.jsonl` |
| A2 (#18) | `A2Proposer` on the shared ledger |
| A1 (#19) | `a1.study_service` on the shared ledger and the run's play, timing and refusal logs, served with `a1.serve_a1` on `--a1-host` (port 8741); slots open back to back |
| Panel (#21) | `serve_panel`: `panel.create_panel_app` over `Orchestrator.panel_host()` on `--panel-host` (port 8765); the session starts when every seat's station has joined |

API:

| Name | Signature | Purpose |
| --- | --- | --- |
| `load_batch_inputs` | `(*, config, meanings, fallback, generation_config=None, prompts=None, llm_manifest=None, freeze_manifest=None, proposers="real") -> BatchInputs` | Read the input files; the committed prompt set and LLM manifest by default; a demo batch without a generation config gets `demo_generation_config` |
| `check_batch_start` | `(inputs, *, kind, run_id, proposers="real") -> None` | The start checks of every batch run (below) |
| `probe_llm_server` | `(url, manifest, *, kind, timeout_s=5.0) -> str` | The LLM server serves the pinned model and runtime (`E_LLM_SERVER`); returns the request log's runtime label |
| `open_batch` | `(inputs, run_dir, *, kind, clock, proposers="real", llm_url=None, a1_station=None, resume=False, purpose="batch", sim_p_failure=0.05, sim_propose=None) -> StudyBatch` | Start checks, then create (or reopen) the run directory (its name is the run ID) and build the components and the `Orchestrator` |
| `run_session` | `(batch, *, appointment="next", panel="stations", designer="kiosk", a1_host, a1_port, panel_host, panel_port, station_timeout_s=600, rating_policy=None, designer_invalid_slots, designer_timeout_slots, log) -> str \| None` | Serve A1 and the panel, finish an interrupted atom of a reopened run, run the appointment(s); returns the next atom |
| `serve_panel` | `(host, *, clock, bind="127.0.0.1", port=8765)` | Context manager yielding the panel server's base URL |
| `wait_for_stations` | `(orchestrator, *, timeout_s=600, poll_s=0.1)` | Block until every seat has joined (`E_STATIONS`) |
| `demo_generation_config` | `(config, meanings, fallback, *, prompt_set=None, llm_manifest_sha256=None)` | The DEMO generation config of a demo/synthetic run |
| `BatchInputs`, `StudyBatch`, `RunnerError(code, message)`, `main(argv)` | | Codes `E_INPUTS`, `E_MODE`, `E_LLM_SERVER`, `E_RUN_DIR`, `E_STATIONS` |

Start checks (`check_batch_start`), before anything is created: the run ID fits the kind;
`check_batch_pins`; with real proposers the prompt set and its meaning set, the decoding
schema and the LLM manifest hash to the generation config's values (`E_INPUTS`); then
`genconfig.check_run_config` (running code = config; demo configs only for demo runs; a
confirmatory run needs the `frozen` G4 manifest with the config's hash). The G4 freeze
guard (#25) is added in this function.

Command line (from the repository root; inputs of real runs live in restricted storage):

```sh
# start checks only (prints the generation-config hash)
uv run --project generation python -m av_generation.batch_runner check \
  --run-dir <runs>/<run_id> --kind confirmatory --config <config.json> \
  --generation-config <generation-config.json> --meanings <meanings dir> \
  --fallback <fallback manifest> --freeze-manifest <freeze manifest>
# appointment of a batch: same inputs, plus the servers
uv run --project generation python -m av_generation.batch_runner run <same inputs> \
  --llm-url http://<llm-host>:8000 --a1-host <lab interface> --a1-station <kiosk station> \
  --panel-host <lab interface> --appointment next
# next appointment (a new process): reopen the run
uv run --project generation python -m av_generation.batch_runner run <same inputs> \
  --llm-url ... --a1-host ... --panel-host ... --resume
```

The runner prints the A1 page URL (for the kiosk policy) and the station page URL, waits
for the three stations, runs the appointment and prints the atoms finished. Exit codes: 0
done, 1 refused (`error: <code>: ...`), 3 batch incomplete (rebuild it, section 6), 130
interrupted (the logs are kept; reopen with `--resume`). Other options: `--prompts`,
`--llm-manifest` (defaults: the committed set and manifest), `--a1-port`, `--panel-port`,
`--station-timeout-s`, `--appointment 1..4|all`, `--purpose dry_run`.

Synthetic runs (`--kind demo|synthetic`, `DEMO-` run IDs; the DEMO batch config, meaning
set and fallback set are the defaults): `--designer bot` works the A1 app over HTTP
(`_batch_sim.BotDesigner`), `--panel bots` seats in-process bot raters on the panel
session contract (`_batch_sim.SyntheticPanel`), `--proposers sim` uses the simulated
proposers, `--clock scaled --speed N` runs in accelerated real time and `--clock manual`
in virtual time driven by the bot panel (bots only). For example, with #16's mock server
on `127.0.0.1:8000` (`python -m av_generation.mock_llm serve <dir>`):

```sh
uv run --project generation python -m av_generation.batch_runner run \
  --run-dir /tmp/runs/DEMO-A-real-01 --kind synthetic --llm-url http://127.0.0.1:8000 \
  --designer bot --panel bots --clock scaled --speed 200 --a1-port 0 --appointment all
```

Pending (#21): the panel server. Until #21 lands, `serve_panel` (and so `--panel
stations`) raises `NotImplementedError` from the skeleton `panel.create_panel_app`; the
tests exercise the serving path with a stand-in app.
