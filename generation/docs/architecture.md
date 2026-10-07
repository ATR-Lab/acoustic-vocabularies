# Generation architecture

Package `av-generation` (import `av_generation`), version 0.1.0. Scope: the Study A
generation system (#16-#25) and the contracts the Study B bank builder (#26-#28, project
`banks/`) reuses. It builds on the sound engine (`av_sound`,
[`docs/interfaces/sound-engine.md`](../../docs/interfaces/sound-engine.md)): every sound is
made by `render()`, every admissibility decision by `validate()`, every committed atom
goes to the `VocabularyStore`, and the fallback bank and books come from
`av_sound.fallback`.

The skeleton implements the shared contracts below and leaves one interface module per
issue with `NotImplementedError` bodies. Implementers fill their own modules and add new
files; they do not edit shared files (section 14).

## 1. Stack

- Python 3.11, uv project in `generation/` (src layout), `av-sound` as an editable path
  dependency. Every third-party dependency of #16-#28 is declared in the skeleton
  (section 13), so no implementer edits `pyproject.toml` or `uv.lock`.
- Web UIs (#19 A1, #21 rater stations, #23 listening tool): FastAPI apps serving plain
  HTML/CSS/JS from `src/av_generation/web/<app>/`. No npm build, no CDN, no third-party
  JavaScript. Rater stations use one WebSocket each.
- Model server (#16): pinned vLLM OpenAI-compatible endpoint on a dedicated GPU host,
  reached only on the station network. Prompt tokens are counted by the same server
  (`POST /tokenize`), so the client needs no tokenizer files or template engine. Here: a
  mock server and `llm_fake`.
- No network at runtime, no wall clock in decisions, no randomness except PCG64 streams
  seeded from seed keys (section 6).

## 2. Module map

Status: **C** = shared contract, implemented and tested in the skeleton (change only by a
contract update, section 14); **I** = interface, filled by the owner issue.

| Module | Contents | Status | Owner |
| --- | --- | --- | --- |
| `constants` | Budgets, timings, decoding values, panel orders, B caps, message bounds | C | skeleton (frozen at G4, #25) |
| `ids` | `Study`, `Method`, `RunKind`; batch/book/bank/slot/rating-slot IDs; panel aliases; bank sets | C | skeleton |
| `seeds` | Seed keys, `derive_seed`, `wire_seed`, `rng_for` | C | skeleton (#16 contract) |
| `outcomes` | 14 slot outcome codes, `LlmStatus`, validator-code mapping and precedence | C | skeleton (#17 contract) |
| `domain` | The 12 recipe coordinates in protocol order with allowed values | C | skeleton |
| `jsonio` | Canonical JSON/JSONL, strict reading, dataclass codec, shared hash definitions, torn-tail repair, per-file locks | C | skeleton |
| `records` | Every log record and run document (dataclasses + schemas), `RecordWriter` | C | skeleton |
| `config` | `BatchConfig` (Study A batch: books, panel seats, order, aliases), rating-slot helpers | C | skeleton (#20 fills real configs) |
| `proposers` | `RoundRequest`, `RoundResult`, `RoundProposer`, feedback and book state, `BCellState` | C | skeleton |
| `meanings` | `MeaningSet`, `load_meanings`: the one source of meaning texts | C | skeleton |
| `genconfig` | `GenerationConfig`, `frozen_sha256()`, `check_run_config` (section 11) | C | skeleton |
| `panel_session` | `PanelSessionHost` protocol and its data types (#20 host <-> #21 server) | C | skeleton |
| `rater_protocol` | Rater panel message protocol (schema, routes, parser) | C | skeleton |
| `rundir` | Run-directory layout, log file names, public/restricted policy | C | skeleton |
| `masking` | Masking rules and `masking_findings()` string test | C | skeleton |
| `clock` | `SystemClock`, `ScaledClock` (accelerated), `ManualClock` (tests) | C | skeleton |
| `netguard` | `deny_outbound()`: loopback-only sockets, asyncio loops and UDP | C | skeleton |
| `webserve` | `serve_in_thread(app)`: uvicorn in a thread | C | skeleton |
| `llm_fake` | `ScriptedLlmClient` for tests without a server | C | skeleton |
| `bank_manifest` | Bank manifest validation, bank hash, amendment chain, effective menu | C | skeleton (schemas owned by #26) |
| `llm` | `LlmClient`, `RawOutcome`, `ChatMessage`, `TokenCountError`; `OpenAICompatibleClient` | I | #16 (+ server launcher, LLM manifest, mock server, benchmark) |
| `ledger` | `SlotLedger` (`reserve`, `consume`), `SlotTicket`, cap errors | I | #17 |
| `prompts` | `PromptSet`, `build_a3_prompt`, `build_b_prompt` | I | #17 |
| `parser` | `parse_output` (exactly one JSON object) | I | #17 |
| `a3` | `A3Proposer` (prompt -> model -> parser -> validator -> ledger) | I | #17 |
| `a2` | `A2Proposer`, uniform sampling, mutations | I | #18 |
| `a1` | `A1SlotService`, `create_a1_app`, `ROUTES` | I | #19 |
| `orchestrator` | `Orchestrator` (implements `PanelSessionHost`), `RatingSlotPlan`, panel order schedule, panel aliases | I | #20 |
| `selector` | `score_candidate`, `pick_incumbent` | I | #20 |
| `panel` | `create_panel_app(host, clock=)`, station page (`web/rater/`) | I | #21 |
| `rater` | `BotRater`, `BotRatingPolicy` | I | #21 |
| `dryrun` | `DryRunPlan`, dry-run driver, `check_log_completeness` | I | #22 |
| `threshold` | Listening tool: stimuli, session plan, runner, CSV, summary | I | #23 |
| `audit` | `build_audit`, `build_set_audit`; `books.csv` columns (contract with #34) | I | #24 |
| `freeze` | Freeze manifest builder and CI freeze guard | I | #25 |

Owners may add private modules (`_<name>.py`) and subpackages next to their module, e.g.
`web/a1/`, `llm_server.py`, `mock_llm.py`. #26 creates the `banks/` project; #27 and #28
are runs of the #26 builder and add no `av_generation` module.

## 3. Data flow

### 3.1 One Study A round (per atom, rounds 1..4)

1. The orchestrator writes `timing` `round_start` and, per book, builds a `RoundRequest`
   from the book's own `BookState` and `AtomFeedback` (ratings of closed rounds only).
   A2 gets `book.without_labels()` and `semantic_label=None`: no label or meaning
   reaches it.
2. It runs the three `RoundProposer`s in parallel threads (`proposal_window_start/end`,
   at most 3 x 40 s each). Each proposer fills its three slots in order, and each slot's
   proposal work starts with `SlotLedger.reserve` (the cap is checked before any model
   call or design work):
   - A3 (#17): build prompt (a contract error raises before anything is charged);
     reserve (the slot's 40 s start); `count_prompt_tokens` (server `/tokenize`); above
     16,384 the slot is `overflow_input` without a call; no call at or after the slot
     deadline (`a3.slot_deadline_ms`: 40 s after reserve, or the window end if earlier;
     the 40 s cover count and call); else one `LlmClient.propose(..., slot_id=)` (#16
     logs an `llm_request`); an answer after the deadline is `timeout`; map the status;
     parse; `validate()` against the book's committed references; `consume`.
   - A2 (#18): reserve; sample or mutate with `rng_for(a2_seed_key(...))`; validate;
     consume (the record carries `A2Detail`).
   - A1 (#19): slots open back to back (slot 1 when the round starts, each next one
     when the previous closes); opening a slot reserves it (a 13th request is refused
     before anything is designed or heard) and starts a 40-s server timer; submit validates, renders and
     consumes; a valid recipe gets one single-use audio token (`play` events; a second
     play is `refused`). No submission: `timeout`.
3. The panel plays 9 rating slots of 20 s in the order of `BatchConfig.panel.order`
   (three blocks of three, slot order within a block; `BatchConfig.rating_positions`).
   Invalid candidates are placeholder slots. Per slot: candidate at 0 s, nearest
   committed reference of the same book at 2 s (`av_sound.nearest_reference`; none on
   the first atom), meaning texts from the shared `MeaningSet`. The orchestrator is the
   `panel_session.PanelSessionHost`: it publishes the schedule as events and writes the
   `rating`, `play` and panel `timing` records; the panel server (#21) only relays.
   At each slot's lock time the host writes one `rating` record per seat (submitted,
   placeholder or missing), so every rater has 9 records per round.
4. The selector (#20) scores the round's candidates, updates the incumbent and writes one
   `decision` per book; each method then gets its own book's feedback (`feedback_sent`).

### 3.2 After round 4

- Incumbent exists: `VocabularyStore.commit(book_id, atom_id, label, recipe,
  source=slot_id, pcm_sha256=...)`, `decision` `commit`, `commit` record with
  `source="selector"` and the returned chain head.
- No eligible candidate: `decision` `fallback_scan`; `scan_fallback(bank,
  book_entries, used=...)` -> `fallback_scan` record; a selected entry is committed with
  `source="fallback_bank"`.
- Scan exhausted at atom k (Study A protocol §3.7, whole-book substitution): the
  book's store book is voided (`cause="failed_generation"`), the profile's frozen
  fallback book is committed to a new store book (16 `commit` records,
  `source="fallback_book"`, `failed_generation=true`, the new `store_book_id`) and a
  `book_substituted` timing event is logged. The method label stays.
- After a substitution the method keeps generating for atoms k+1..16 with the same
  budget and the same panel schedule, and its candidates keep being rated ("all
  original candidates and ratings remain archived"). Its `BookState` is the continued
  book: the atoms it committed before atom k plus the incumbents archived since.
  Round-4 decisions of these atoms carry `book_substituted=true` and the action
  `archive` (the incumbent becomes part of the continued book; nothing is committed) or
  `archive_none` (no eligible candidate; no scan). So the matched budget, the panel
  order and every count in section 4 hold for every batch.
- State is persisted after every slot; a batch pauses between atoms and resumes from the
  logs (`Orchestrator.resume`, which first repairs torn log tails).

### 3.3 One Study B bank slot (#26)

For each attempt (at most 4), profile and atom in the stored order (`permutation.json`
`atom_order`), slot 1..12: `BCellState` (retained options of all atoms so far, this
cell's history, no ratings) -> `build_b_prompt` -> `SlotLedger.reserve` ->
`count_prompt_tokens` -> `LlmClient.propose` with `b_seed_key(bank_ns, attempt, profile,
atom, slot)` -> parse -> `validate(candidate, profile, cell.other_atom_references())` ->
outcome with `mode="B"` and `cell_duplicate` -> `consume` (cap key per attempt and
cell). Keep the first 4 valid options; a cell that uses 12 slots without 4 fails the
attempt. Bank directory, manifest, amendment log and hashes: `bank_manifest` (module
docstring) and `generation/schema/bank-manifest.schema.json`.

## 4. Log contracts

All logs are append-only JSONL under `<run>/logs/` (`rundir.LOG_FILES`), one canonical
line per record (compact JSON, sorted keys, ASCII, `\n`; `jsonio.canonical_line`), and
validated against their schema before they are written (`records.RecordWriter`). Every
key is always present (`null` when it does not apply). Corrections are new records, never
edits. All appenders of one file in a process share one lock (`jsonio.path_lock`); one
process writes a run. A torn last line after a crash is cut with
`jsonio.repair_torn_tail` and logged as a `log_repaired` timing event.

| Record (`record`) | File | Schema | Written by | Read by |
| --- | --- | --- | --- | --- |
| `slot` | `slots.jsonl` | `slot-record` | ledger for A1, A2, A3 (#17-#19), B (#26) | #20, #22, #24, #26 |
| `slot_refusal` | `slot-refusals.jsonl` | `slot-refusal` | ledger (A1, A2, A3, B) | #24 |
| `llm_request` | `llm-requests.jsonl` | `llm-request` | LLM client (#16) | #16 bench, #24, #25 |
| `rating` | `ratings.jsonl` | `rating-record` | panel session host (#20) | selector (#20), #22, #24 |
| `decision` | `decisions.jsonl` | `decision-record` | selector (#20) | #24 |
| `commit` | `commits.jsonl` | `commit-record` | orchestrator (#20) | #22, #24, package handoff (#13) |
| `fallback_scan` | `fallback-scans.jsonl` | `fallback-scan-record` | orchestrator (#20) | #22, #24 |
| `play` | `plays.jsonl` | `play-event` | A1 (#19), panel session host (#20), listening tool (#23) | #22 (0 message plays), #24 |
| `timing` | `timing.jsonl` | `timing-event` | every component | #22, #24 |
| `threshold_trial` | `threshold-trials.jsonl` | `threshold-trial` | listening tool (#23) | #23 summary |

Documents (pretty JSON, `indent=2`, sorted keys, trailing newline): `run-manifest.json`
(`RunManifest`), `config.json` (`BatchConfig`), `generation-config.json`
(`GenerationConfig`), `dry-run-plan.json` (`DryRunPlan`, #22), meaning sets
(`MeaningSet`), the threshold stimulus set (`ThresholdStimulusSet`) and sessions
(`ThresholdSession`), the audit summary (`audit-summary`), the freeze manifest
(`freeze-manifest`), bank manifests (`bank-manifest`) and their amendment logs
(`bank-amendment`, JSONL).

Counting rules used by #20, #22 and #24 (they hold with and without substitutions):

- 576 `slot` records per batch (192 per book; 0 refusals in a clean run);
- 576 `rating` records per rater (9 per round, placeholders and missing included);
- 192 `decision` records (4 per atom per book);
- one `commit` per atom in each book's final store book: 48 per batch. A book
  substituted at atom k also has the k-1 commits of its voided store book, so the log
  holds 48 + sum(k-1) `commit` records;
- one `fallback_scan` per atom that ended without an incumbent before any
  substitution of its book;
- 0 `play` records with `audio_kind="message"`.

## 5. Identifiers

| ID | Format | Source |
| --- | --- | --- |
| batch | schedules unit ID `A-P01` / `A-C01`, or `DEMO-...` | schedules batch table (#29) |
| book | anonymous book ID `BK-C-7QX4MN` (store rules) | schedules book key (#31) |
| panel alias | `PB-` + 4 of `BCFGHJKMNPQRTVWXY456789`, per panel | `orchestrator.panel_aliases`, stored in `BatchConfig.panel.aliases` |
| bank | `bank-P001` (pilot), `bank-C001` (confirmatory, spares continue), `DEMO-...` | dyad-slot sequence (schedules allocation, #31) |
| proposal slot (A) | `<book>.<atom>.r<round>s<slot>` | `ids.proposal_slot_id` |
| proposal slot (B) | `<bank>.t<attempt>.<profile>.<atom>.s<slot:02>` | `ids.bank_slot_id` |
| rating slot | `<batch>.<atom>.r<round>p<position>` | `ids.rating_slot_id` |
| asset | SHA-256 of the canonical WAV file | `rater_protocol`, `play` |
| run | 3-64 letters/digits/hyphens; `DEMO-` exactly for demo/synthetic runs | `rundir.check_run_id` |

`slot` is 1..3 within a round (A) or 1..12 within a cell (B); `slot_index` is the
submission-slot number 1..12 used by the tie rule. Cap keys: `A|<book>|<atom>` and
`B|<bank>|<attempt>|<profile>|<atom>` (`records.cap_key`). Slot and rating-slot IDs are
unique within a run; a rebuilt batch reuses them in its new run, so the global key is
`(run_id, slot_id)`.

Panel aliases: the learner-facing book IDs are not the panel's names for the books
(Study A protocol §3.1: book IDs rotate between panels). Each panel gets new aliases;
the operator console shows aliases only, and no book name reaches a station.

## 6. Seeds

- `derive_seed(*parts)` = first 8 bytes of SHA-256 of the `|`-joined key, unsigned
  big-endian (0 .. 2^64-1). The stored waveform hash, not the seed, is the
  reproducibility record.
- Slot keys: `A1|A2|A3` + `|<batch_ns>|<atom>|<round>|<slot>`;
  `B|<bank_ns>|<attempt>|<profile>|<atom>|<slot>`. Other namespaces: `PANEL` (#20 panel
  orders, `PANEL|<set_ns>|orders`, and aliases, `PANEL|<set_ns>|aliases|<panel>`), `BOT`
  (#22 bot raters and designer), `THRESHOLD` (#23). Integers are decimal without leading
  zeros; parts match `[A-Za-z0-9._-]+`.
- `<batch_ns>` is `BatchConfig.seed_namespace`: the batch ID, or a new namespace when a
  batch is rebuilt after a rater withdrawal. `<bank_ns>` is the bank manifest's
  `seed_namespace`: the bank ID on a first build, a new namespace for every rebuild under
  a new `bank_version` (so a rebuild never repeats seeds). Pilot and confirmatory keys
  differ through their IDs (`A-P..`/`A-C..`, `bank-P..`/`bank-C..`), so their seeds are
  disjoint.
- Each method has its own stream: A2 draws from `rng_for(a2_seed_key(...))`
  (`numpy.random.Generator(PCG64(seed))`), one stream per slot. A3 sends the seed with
  the model call as `wire_seed(seed)`, the signed 64-bit two's-complement reading,
  because the OpenAI-compatible endpoint of vLLM accepts only signed 64-bit seeds; logs
  keep both.
- Test: 11,520 sample keys across the A2, A3 and B spaces give 11,520 distinct seeds and a
  pinned digest (`tests/generation/test_seed.py`). The CI matrix runs it on Linux, macOS
  and Windows and prints sample seeds in the job summary.

## 7. Slot outcomes

Every slot ends with one of 14 codes, each of which consumes the slot:
`valid`, `invalid_json`, `schema_violation`, `out_of_domain`, `render_fail`,
`event_too_short`, `clipping`, `duplicate`, `reserved_collision`, `separation_fail`,
`incompatible` (B only), `overflow_input`, `overflow_output`, `timeout`.

| Source | Outcome |
| --- | --- |
| prompt above 16,384 tokens (no call) | `overflow_input` |
| token count failed (`TokenCountError`; no call) | `invalid_json` (`llm_status` `server_error`) |
| model `timeout` / A1 no submission in 40 s | `timeout` |
| model `overflow_output` (stopped at 512 tokens) | `overflow_output` |
| model `server_error` | `invalid_json` (`llm_status` keeps `server_error`) |
| parser: not exactly one JSON object | `invalid_json` |
| `E_JSON`, `E_SCHEMA`, `E_DOMAIN` | `invalid_json`, `schema_violation`, `out_of_domain` |
| `E_EVENT_SHORT`, `E_NONFINITE`, `E_CLIP` | `event_too_short`, `render_fail`, `clipping` |
| `E_DUPLICATE`, `E_RESERVED`, `E_SEPARATION` | `duplicate`, `reserved_collision`, `separation_fail` |

Precedence when several validator codes fail: the validator's fixed order
(`av_sound.validate.REASON_CODES`, the same as `ValidationResult.primary_code`); all codes
stay in `validator_codes`. Study B (`mode="B"`, validated against the other atoms'
retained options): technical codes first, then a same-cell waveform match (`duplicate`),
then `E_DUPLICATE`/`E_SEPARATION` against other atoms (`incompatible`). Infrastructure
failures stay visible: the audit counts `llm_status="server_error"` separately
(`n_llm_server_error`).

## 8. Run directories: public and restricted

Layout (`rundir`): `run-manifest.json`, `config.json`, `generation-config.json`,
`dry-run-plan.json` (#22), `logs/*.jsonl`, `store/` (vocabulary store),
`audio/<file_sha256>.wav`, `audit/{unmasked,masked}/`, `threshold/` (stimulus set,
`sessions/<session_id>.json`, exports), `banks/<bank_id>/` (`bank_manifest`).

| Run kind | IDs | Where | In git |
| --- | --- | --- | --- |
| `demo`, `synthetic` (CI, dry runs, examples) | `DEMO-...` | anywhere | small summaries and hashes only, under `generation/runs/<run_id>/`; logs, stores and audio go to CI artifacts (`generation/out/ci/`) |
| `practice` (A1 training) | no `DEMO-` | restricted storage | never |
| `pilot`, `confirmatory` | no `DEMO-` | restricted storage | never; only hashes (chain heads, config, bank and register hashes) in PR text or registers |

`rundir.create_run_dir` refuses a non-DEMO run inside any git work tree (`E_POLICY`), the
same rule as store books and fallback sets. Real batch configs, book keys, meaning sets,
ledgers, ratings, banks and seeds of real runs never enter git. The one exception is the
committed prompt set `generation/prompts/` (#17): it holds the fixed instruction of Study
A §3.6 byte for byte, so its hash test runs in CI. Meaning texts stay in restricted
storage (DEMO set in git).

## 9. Masking

- Who may see method labels: the generation operator's restricted files (run manifest,
  batch config, logs, unmasked audit). Raters, session experimenters and blinded analysts
  never do.
- Rater stations: messages carry rating-slot IDs, asset hashes and meanings only; no
  method, book ID, proposal-slot ID or seed (`rater_protocol`; tested with
  `masking_findings`). The panel server (#21) cannot leak more: the
  `panel_session` types it receives hold no book ID, alias, proposal-slot ID, method or
  seed. Bot raters are told which slots to rate unacceptable by rating-slot ID
  (`BatchConfig.rating_slot_ids`). The operator console shows panel aliases only.
- Feedback: a proposer receives only its own book (`RoundRequest` holds one book's state
  and history). A2 never receives meanings or labels (`BookState.without_labels()`).
  A3/B prompts never carry participant IDs, learner data, other books' scores or
  current-round ratings; B prompts carry no rating field at all.
- Masked outputs (#24 masked audit, anything for #34's blinded tables): anonymous book IDs
  only, no `audit.METHOD_COLUMNS` and no `masking.METHOD_REVEALING_FIELDS`; check with
  `masking_findings(text) == ()`. `METHOD_COLUMNS` holds every column that names a method
  or reveals it by construction: labels, designer, per-method effort, every outcome count
  (the overflow codes exist only for A3; A2 never produces `invalid_json`), startup and
  operator time. Outcome distributions in other columns can still hint at a method;
  masking removes labels, not every inference.

## 10. Timing budget

| Item | Value | Constant |
| --- | --- | --- |
| Proposal slot cap | 40 s from `reserve` (A1 server timer; A3/B: token count and call together, no call at or after the slot deadline, a later answer is `timeout`; the client cancels a call at its own 40-s cap, result within 40.5 s) | `SLOT_CAP_MS` |
| Proposal window | 3 slots x 40 s = 120 s, three methods in parallel | `PROPOSAL_WINDOW_MS` |
| Rating slot | 20 s; candidate at 0 s, reference at 2 s | `RATING_SLOT_MS`, `REFERENCE_ONSET_MS` |
| Rating window | 9 x 20 s = 180 s | `RATING_WINDOW_MS` |
| Round / atom | 300 s / 20 min | `ROUND_BUDGET_MS`, `ATOM_BUDGET_MS` |
| Appointment | 4 atoms = 80 min within a 90-min booking | `APPOINTMENT_BUDGET_MS`, `APPOINTMENT_BOOKING_MS` |
| Batch | 576 proposal slots, 576 rating slots per rater | `SLOTS_PER_BATCH`, `RATING_SLOTS_PER_RATER` |
| Study B | 12 slots per cell, 576 per attempt, 4 attempts | `B_*` |

Startup (model load, renderer self-test), A1 active design time, familiarization, model
runtime and operator actions are logged as separate `timing` events. Time comes from a
`clock.Clock`: real time for real runs, `ScaledClock(speed)` for accelerated runs of the
real components (#20, #22), `ManualClock` in unit tests. Real clocks read
`time.perf_counter_ns()` (high resolution on Windows too).

## 11. Frozen configuration

One document, `genconfig.GenerationConfig` (`generation-config.schema.json`), holds
everything that decides what a method can propose and what is admissible: model ID and
revision, the LLM-manifest hash (#16), decoding values and the decoding-schema hash, the
A3 and B prompt-set hashes and the meaning-set hash (#17), renderer and validator
versions and hashes, the separation threshold, the seed function and namespaces, the
Study A and B budgets, the A2 and selector rules, and the fallback hashes. Its hash,
`frozen_sha256()` (canonical JSON), is the only "config hash":

- freeze item `config.frozen_sha256` (#25);
- `RunManifest.generation_config_sha256` of every batch and bank run (required for pilot
  and confirmatory runs), with the document stored as `generation-config.json`;
- `generation_config_sha256` of every bank manifest (#26), with the document stored
  beside it (so `banks verify` knows the threshold and code versions), and the bank
  registers (#27, #28).

`check_run_config(config, kind=, freeze_manifest=)` runs before the first slot of every
batch (#20) and bank (#26): the running code and constants must equal the config, demo
configs run only demo/synthetic runs, and a confirmatory run needs a `frozen` freeze
manifest whose `config.frozen_sha256` equals the config hash. Pilot runs record their
current (unfrozen) config hash. Hash definitions used inside the config are shared
(`jsonio.messages_sha256`, `schema_sha256`, `file_set_sha256`; `MeaningSet.sha256()`).

## 12. Hardware- and human-pending policy

There is no GPU in development. Everything is built and tested against
`llm_fake.ScriptedLlmClient` and #16's mock OpenAI-compatible server. Evidence that needs
the LLM GPU host (300-call latency CSV, same-seed repeatability, p95 at the worst-case
prompt, the real-time dry run, a real-model example bank) or people (listening checks,
panels, screen recordings, sign-offs) is marked **Pending (human/hardware)** in the PR
and the issue checklist item stays open. Model weights are never downloaded here;
Hugging Face metadata (revision SHA, file SHA-256, licence) is fine at development time.
Tests never touch the network: every generation test runs under `netguard`.

## 13. Dependencies (declared once, in the skeleton)

| Package | Used for |
| --- | --- |
| `av-sound` (path, editable) | renderer, validator, store, fallback, composer guard |
| `numpy` | PCG64 streams (#18, #22, #23) |
| `scipy` | chi-square uniformity check (#18), logistic fit (#23) |
| `jsonschema` | every schema |
| `httpx` | LLM client (#16), test clients |
| `fastapi`, `pydantic`, `python-multipart`, `uvicorn`, `websockets` | A1 app, panel server and stations, listening tool, mock LLM server |
| `tokenizers` | optional offline token estimates for notes (#17 worst-case prompt); never decides `overflow_input`, which uses the server's `/tokenize` |
| `matplotlib` | listening-tool summary plot (#23; written to restricted storage or CI artifacts, never committed as an image) |
| dev: `pytest`, `pytest-cov`, `pytest-timeout`, `hypothesis`, `ruff`, `mypy`, `types-jsonschema`, `playwright` | tests, lint, types, browser tests |

## 14. Rules for implementers

- Do not edit shared files: `pyproject.toml`, `uv.lock`, the C modules above,
  `generation/schema/*` (except the schemas you own), `tests/generation/conftest.py`,
  the shared test modules, `.github/workflows/generation.yml`, this file. If a contract
  must change, say so in your PR (what and why) and keep the change minimal and
  backwards compatible; the orchestrator merges contract changes.
- Owned schemas and their test modules (the owner edits both; nobody else does):
  `audit-summary` -> `test_audit.py` (#24); `freeze-manifest` ->
  `test_freeze_manifest.py` (#25); `bank-manifest`, `bank-amendment` ->
  `test_bank_manifest.py` (#26); `dry-run-plan` -> `test_dry_run.py` (#22).
- Fill your interface module; keep its public names (`tests/generation/test_generation_modules.py`
  checks them). Do not add re-exports to `av_generation/__init__.py`.
- Docs: your topic in `generation/docs/<topic>.md`; your section of
  `docs/interfaces/generation.md` (replace its "Pending" line).
- Tests: `tests/generation/test_<unique>.py` (basenames are unique across `tests/`). The
  issues name `generation/tests/test_*.py`; use `tests/generation/` and these names:
  `test_llm_client.py`, `test_seed.py` (exists), `test_prompt_builder.py`,
  `test_output_parser.py` (#17 "test_parser"), `test_slot_ledger.py`, `test_a2_search.py`,
  `test_a1_api.py`, `test_selector.py`, `test_orchestrator.py`, `test_round_fallback.py`
  (#20 "test_fallback", which `tests/sound/` already uses), `test_rater_client.py`,
  `test_dry_run.py` (exists), `test_threshold_tool.py`, `test_audit.py` (exists),
  `test_freeze_manifest.py` (exists), `test_bank_manifest.py` (exists). Use the shared
  fixtures (`serve_app`, `browser_page`, the network guard) and mark browser tests
  `@pytest.mark.browser`.
- CI: 3-OS matrix (ruff, format, mypy strict, pytest with an 85% coverage gate and a
  300-s per-test timeout) and an Ubuntu Chromium job for browser tests; jobs stop after
  30 minutes. Bulky synthetic outputs (dry-run bundle, summary plot, mock benchmark) go
  to `generation/out/ci/`, which both jobs upload as an artifact.
- Synthetic data only, labelled `DEMO-`. No WAV or other binary file in git.

## 15. Decisions taken in the skeleton

| Decision | Rationale |
| --- | --- |
| `server_error` maps to `invalid_json` (status kept in `llm_status`) | #17's list has no server-error code; no JSON object came back, which is the parser's `invalid_json` rule; the slot is consumed either way; the audit counts it apart (`n_llm_server_error`) |
| Validator precedence = `REASON_CODES` order | one order everywhere (`primary_code`); all codes are logged |
| `wire_seed` for the server | vLLM rejects seeds outside the signed 64-bit range; the unsigned derivation of #16 is kept |
| B seed keys name a bank namespace (`B\|<bank_ns>\|...`) | #16 proposed `B\|bank\|...`; a rebuilt bank (new `bank_version`) needs new seeds for independently seeded attempts (Study B §4) |
| Prompt tokens counted by the server (`/tokenize`) | the pinned chat template inserts a default system message; counting where the template runs avoids a second template implementation and unpinned dependencies |
| Ledger reserves before work (`reserve` -> `consume`) | no model call or design work beyond the cap (Study B §4; #19 13th-slot refusal). A3/B build the prompt before `reserve` (#17): it is not proposal work, and a contract error then charges no slot |
| Slot and rating-slot IDs name the anonymous book or nothing | masking by construction; parseable and filename-safe on Windows |
| Per-panel book aliases | Study A §3.1 book-ID rotation; the schedules book IDs are learner-facing |
| Whole-book substitution keeps the method generating (section 3.2) | matched budget and unchanged panel schedule; counts hold; candidates and ratings stay archived |
| The panel server is #21's module over a #20 host protocol | one owner each; the host writes every panel record, the server only relays |
| Bank IDs from the dyad-slot sequence (`bank-P001`, `bank-C001`) | file contract with #31 (no allocation information); #27/#28 proposed other IDs before it existed |
| Bank manifests are immutable; amendments go to a chained log | a register hash committed before allocation (#28) stays valid; #70 and #13 apply the log with `effective_menu` |
| One generation config and one config hash | #25, #26, #28 and confirmatory batches compare the same value |
| Meaning texts come from one shared set | stations, A1 and prompts show the same frozen texts (hash in the config and run manifest) |
| Prompt and meaning sets are loaded from directories | protocol-quoted text can stay in restricted storage with only its hash committed. #17 decided: the fixed instruction is committed (`generation/prompts/`; owner sign-off pending), meaning texts stay restricted |
| Records carry every key (nullable) and are validated on write | unique canonical lines, byte-identical reports (#24), early failure |
| Masked audit drops every method-revealing-by-construction column | the masked table goes to the blinded analysis inputs (#34) |
