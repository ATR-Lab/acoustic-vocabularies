# Generation architecture

Package `av-generation` (import `av_generation`), version 0.1.0. Scope: the Study A
generation system (#16-#25) and the contracts the Study B bank builder (#26-#28, project
`banks/`) reuses. It builds on the sound engine (`av_sound`,
[`docs/interfaces/sound-engine.md`](../../docs/interfaces/sound-engine.md)): every sound is
made by `render()`, every admissibility decision by `validate()`, every committed atom
goes to the `VocabularyStore`, and the fallback bank and books come from
`av_sound.fallback`.

The skeleton commit implements the shared contracts below and leaves one interface module
per issue with `NotImplementedError` bodies. Implementers fill their own modules and add
new files; they do not edit shared files (section 13).

## 1. Stack

- Python 3.11, uv project in `generation/` (src layout), `av-sound` as an editable path
  dependency. Every third-party dependency of #16-#28 is declared in the skeleton
  (section 12), so no implementer edits `pyproject.toml` or `uv.lock`.
- Web UIs (#19 A1, #21 rater stations, #23 listening tool): FastAPI apps serving plain
  HTML/CSS/JS from `src/av_generation/web/<app>/`. No npm build, no CDN, no third-party
  JavaScript. Rater stations use one WebSocket each.
- Model server (#16): pinned vLLM OpenAI-compatible endpoint on a dedicated GPU host,
  reached only on the station network. Here: a mock server and `llm_fake`.
- No network at runtime, no wall clock in decisions, no randomness except PCG64 streams
  seeded from seed keys (section 6).

## 2. Module map

Status: **C** = shared contract, implemented and tested in the skeleton (change only by a
contract update, section 13); **I** = interface, filled by the owner issue.

| Module | Contents | Status | Owner |
| --- | --- | --- | --- |
| `constants` | Budgets, timings, decoding values, panel orders, B caps, message bounds | C | skeleton (frozen at G4, #25) |
| `ids` | `Study`, `Method`, `RunKind`; batch/book/bank/slot/rating-slot IDs | C | skeleton |
| `seeds` | Seed keys, `derive_seed`, `wire_seed`, `rng_for` | C | skeleton (#16 contract) |
| `outcomes` | 14 slot outcome codes, `LlmStatus`, validator-code mapping and precedence | C | skeleton (#17 contract) |
| `domain` | The 12 recipe coordinates in protocol order with allowed values | C | skeleton |
| `jsonio` | Canonical JSON/JSONL, strict reading, dataclass codec | C | skeleton |
| `records` | Every log record and run document (dataclasses + schemas), `RecordWriter` | C | skeleton |
| `config` | `BatchConfig` (Study A batch configuration) | C | skeleton (#20 fills real configs) |
| `proposers` | `RoundRequest`, `RoundResult`, `RoundProposer`, feedback and book state, `BCellState` | C | skeleton |
| `rater_protocol` | Rater panel message protocol (schema, routes, parser) | C | skeleton (#20/#21 contract) |
| `rundir` | Run-directory layout, log file names, public/restricted policy | C | skeleton |
| `masking` | Masking rules and `masking_findings()` string test | C | skeleton |
| `clock` | `SystemClock`, `ScaledClock` (accelerated), `ManualClock` (tests) | C | skeleton |
| `netguard` | `deny_outbound()`: loopback-only sockets | C | skeleton |
| `webserve` | `serve_in_thread(app)`: uvicorn in a thread | C | skeleton |
| `llm_fake` | `ScriptedLlmClient` for tests without a server | C | skeleton |
| `llm` | `LlmClient`, `RawOutcome`, `ChatMessage`; `OpenAICompatibleClient` | I | #16 (+ server launcher, LLM manifest, mock server, benchmark) |
| `ledger` | `SlotLedger`, cap errors | I | #17 |
| `prompts` | `PromptSet`, `build_a3_prompt`, `build_b_prompt` | I | #17 |
| `parser` | `parse_output` (exactly one JSON object) | I | #17 |
| `a3` | `A3Proposer` (prompt -> model -> parser -> validator -> ledger) | I | #17 |
| `a2` | `A2Proposer`, uniform sampling, mutations | I | #18 |
| `a1` | `A1SlotService`, `create_a1_app`, `ROUTES` | I | #19 |
| `orchestrator` | `Orchestrator`, `PanelSessionHost`, panel app, panel order schedule | I | #20 (panel app shared with #21) |
| `selector` | `score_candidate`, `pick_incumbent` | I | #20 |
| `rater` | Station client (web), `BotRater` | I | #21 |
| `dryrun` | Dry-run driver, `check_log_completeness` | I | #22 |
| `threshold` | Listening tool: stimuli, runner, CSV, summary | I | #23 |
| `audit` | `build_audit`; `books.csv` columns (contract with #34) | I | #24 |
| `freeze` | Freeze manifest builder and CI freeze guard | I | #25 |
| `bank_manifest` | Bank manifest schema validation and bank hash | C (schema) / I (types) | #26 (builder in `banks/`) |

Owners may add private modules (`_<name>.py`) and subpackages next to their module, e.g.
`web/a1/`, `llm_server.py`, `mock_llm.py`. #27 and #28 are runs of the #26 builder and add
no `av_generation` module.

## 3. Data flow

### 3.1 One Study A round (per atom, rounds 1..4)

1. The orchestrator writes `timing` `round_start` and, per book, builds a `RoundRequest`
   from the book's own `BookState` and `AtomFeedback` (ratings of closed rounds only).
   A2 gets `semantic_label=None`.
2. It runs the three `RoundProposer`s in parallel threads (`proposal_window_start/end`,
   at most 3 x 40 s each). Each proposer fills its three slots in order:
   - A3 (#17): build prompt; if `count_prompt_tokens` > 16,384 the slot is
     `overflow_input` without a call; else one `LlmClient.propose` (#16 logs an
     `llm_request`); map the status; parse; `validate()` against the book's committed
     references; `SlotLedger.consume(SlotRecord)`.
   - A2 (#18): sample or mutate with `rng_for(a2_seed_key(...))`; validate; consume
     (the record carries `A2Detail`).
   - A1 (#19): the designer's slot opens with a 40-s server timer; submit validates,
     renders and consumes; a valid recipe gets one single-use audio token (`play`
     events; a second play is `refused`). No submission: `timeout`.
3. The panel plays 9 rating slots of 20 s in the order of `BatchConfig.panel.order`
   (three blocks of three, slot order within a block). Invalid candidates are placeholder
   slots. Per slot: candidate at 0 s, nearest committed reference of the same book at 2 s
   (`av_sound.nearest_reference`; none on the first atom). Stations send `played` and
   `rating` messages; the server writes `play` and `rating` records (placeholders and
   missing ratings included: 9 rating records per rater per round).
4. The selector (#20) scores the round's candidates, updates the incumbent and writes one
   `decision` per book; each method then gets its own book's feedback (`feedback_sent`).

### 3.2 After round 4

- Incumbent exists: `VocabularyStore.commit(book_id, atom_id, label, recipe,
  source=slot_id, pcm_sha256=...)`, `commit` record with `source="selector"` and the
  returned chain head.
- No eligible candidate: `scan_fallback(bank, book_entries, used=...)` -> `fallback_scan`
  record; a selected entry is committed with `source="fallback_bank"`.
- Scan exhausted: the frozen fallback book is committed to a new store book, the failed
  book is voided (`cause="failed_generation"`), and 16 `commit` records with
  `source="fallback_book"`, `failed_generation=true` and the new `store_book_id` follow.
  The method label stays.
- State is persisted after every slot; a batch pauses between atoms and resumes from the
  logs (`Orchestrator.resume`).

### 3.3 One Study B bank slot (#26)

For each attempt (at most 4), profile and atom in the stored order, slot 1..12:
`BCellState` (retained options of all atoms so far, this cell's history, no ratings) ->
`build_b_prompt` -> `LlmClient.propose` with `b_seed_key(bank, attempt, profile, atom,
slot)` -> parse -> `validate(candidate, profile, cell.other_atom_references())` ->
outcome with `mode="B"` and `cell_duplicate` -> ledger (cap key per attempt and cell).
Keep the first 4 valid options; a cell that uses 12 slots without 4 fails the attempt.
The manifest format is `generation/schema/bank-manifest.schema.json`.

## 4. Log contracts

All logs are append-only JSONL under `<run>/logs/` (`rundir.LOG_FILES`), one canonical
line per record (compact JSON, sorted keys, ASCII, `\n`; `jsonio.canonical_line`), and
validated against their schema before they are written (`records.RecordWriter`). Every
key is always present (`null` when it does not apply). Corrections are new records, never
edits.

| Record (`record`) | File | Schema | Written by | Read by |
| --- | --- | --- | --- | --- |
| `slot` | `slots.jsonl` | `slot-record` | ledger for A1, A2, A3 (#17-#19), B (#26) | #20, #22, #24, #26 |
| `slot_refusal` | `slot-refusals.jsonl` | `slot-refusal` | ledger, A1 service, bank builder | #24 |
| `llm_request` | `llm-requests.jsonl` | `llm-request` | LLM client (#16) | #16 bench, #24, #25 |
| `rating` | `ratings.jsonl` | `rating-record` | panel session (#20/#21) | selector (#20), #22, #24 |
| `decision` | `decisions.jsonl` | `decision-record` | selector (#20) | #24 |
| `commit` | `commits.jsonl` | `commit-record` | orchestrator (#20) | #22, #24, package handoff (#13) |
| `fallback_scan` | `fallback-scans.jsonl` | `fallback-scan-record` | orchestrator (#20) | #22, #24 |
| `play` | `plays.jsonl` | `play-event` | A1 (#19), panel (#21), listening tool (#23) | #22 (0 message plays), #24 |
| `timing` | `timing.jsonl` | `timing-event` | every component | #22, #24 |
| `threshold_trial` | `threshold-trials.jsonl` | `threshold-trial` | listening tool (#23) | #23 summary |

Documents (pretty JSON, `indent=2`, sorted keys, trailing newline): `run-manifest.json`
(`RunManifest`), `config.json` (`BatchConfig`), the threshold stimulus set
(`ThresholdStimulusSet`), the audit summary (`audit-summary`), the freeze manifest
(`freeze-manifest`) and bank manifests (`bank-manifest`).

Counting rules used by #22 and #24: a batch has 576 `slot` records (192 per book; 0
refusals in a clean run), 576 `rating` records per rater, 48 `commit` records (16 per
book, plus 16 per substituted book), and 0 `play` records with `audio_kind="message"`.

## 5. Identifiers

| ID | Format | Source |
| --- | --- | --- |
| batch | schedules unit ID `A-P01` / `A-C01`, or `DEMO-...` | schedules batch table (#29) |
| book | anonymous book ID `BK-C-7QX4MN` (store rules) | schedules book key (#31) |
| bank | `bank-P001` (pilot), `bank-C001` (confirmatory, spares continue), `DEMO-...` | schedules dyad list (#31) |
| proposal slot (A) | `<book>.<atom>.r<round>s<slot>` | `ids.proposal_slot_id` |
| proposal slot (B) | `<bank>.t<attempt>.<profile>.<atom>.s<slot:02>` | `ids.bank_slot_id` |
| rating slot | `<batch>.<atom>.r<round>p<position>` | `ids.rating_slot_id` |
| asset | SHA-256 of the canonical WAV file | `rater_protocol`, `play` |
| run | 3-64 letters/digits/hyphens; `DEMO-` exactly for demo/synthetic runs | `rundir.check_run_id` |

`slot` is 1..3 within a round (A) or 1..12 within a cell (B); `slot_index` is the
submission-slot number 1..12 used by the tie rule. Cap keys: `A|<book>|<atom>` and
`B|<bank>|<attempt>|<profile>|<atom>` (`records.cap_key`).

## 6. Seeds

- `derive_seed(*parts)` = first 8 bytes of SHA-256 of the `|`-joined key, unsigned
  big-endian (0 .. 2^64-1). The stored waveform hash, not the seed, is the
  reproducibility record.
- Slot keys: `A1|A2|A3` + `|<batch_ns>|<atom>|<round>|<slot>`;
  `B|<bank>|<attempt>|<profile>|<atom>|<slot>`. Other namespaces: `PANEL` (#20 panel
  orders and book-ID rotation), `BOT` (#22 bot raters and designer), `THRESHOLD` (#23).
  Integers are decimal without leading zeros; parts match `[A-Za-z0-9._-]+`.
- `<batch_ns>` is `BatchConfig.seed_namespace`: the batch ID, or a new namespace when a
  batch is rebuilt after a rater withdrawal. Pilot and confirmatory keys differ through
  their IDs (`A-P..`/`A-C..`, `bank-P..`/`bank-C..`), so their seeds are disjoint.
- Each method has its own stream: A2 draws from `rng_for(a2_seed_key(...))`
  (`numpy.random.Generator(PCG64(seed))`), one stream per slot. A3 sends the seed with
  the model call as `wire_seed(seed)`, the signed 64-bit two's-complement reading,
  because the OpenAI-compatible endpoint of vLLM accepts only signed 64-bit seeds; logs
  keep both.
- Test: 11,520 sample keys across the A2, A3 and B spaces give 11,520 distinct seeds and a
  pinned digest on Linux, macOS and Windows (`tests/generation/test_seed.py`).

## 7. Slot outcomes

Every slot ends with one of 14 codes, each of which consumes the slot:
`valid`, `invalid_json`, `schema_violation`, `out_of_domain`, `render_fail`,
`event_too_short`, `clipping`, `duplicate`, `reserved_collision`, `separation_fail`,
`incompatible` (B only), `overflow_input`, `overflow_output`, `timeout`.

| Source | Outcome |
| --- | --- |
| prompt above 16,384 tokens (no call) | `overflow_input` |
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
then `E_DUPLICATE`/`E_SEPARATION` against other atoms (`incompatible`).

## 8. Run directories: public and restricted

Layout (`rundir`): `run-manifest.json`, `config.json`, `logs/*.jsonl`, `store/`
(vocabulary store), `audio/<file_sha256>.wav`, `audit/{unmasked,masked}/`, `threshold/`,
`banks/<bank_id>/`.

| Run kind | IDs | Where | In git |
| --- | --- | --- | --- |
| `demo`, `synthetic` (CI, dry runs, examples) | `DEMO-...` | anywhere | small summaries and hashes only, under `generation/runs/<run_id>/`; logs, stores and audio go to CI artifacts |
| `practice` (A1 training) | no `DEMO-` | restricted storage | never |
| `pilot`, `confirmatory` | no `DEMO-` | restricted storage | never; only hashes (chain heads, bank and register hashes) in PR text or registers |

`rundir.create_run_dir` refuses a non-DEMO run inside any git work tree (`E_POLICY`), the
same rule as store books and fallback sets. Real batch configs, book keys, prompt sets
quoting the protocol, ledgers, ratings, banks and seeds of real runs never enter git.

## 9. Masking

- Who may see method labels: the generation operator's restricted files (run manifest,
  batch config, logs, unmasked audit). Raters, session experimenters and blinded analysts
  never do.
- Rater stations: messages carry rating-slot IDs, asset hashes and meanings only; no
  method, book ID, proposal-slot ID or seed (`rater_protocol`; tested with
  `masking_findings`). The operator console shows anonymous book IDs only.
- Feedback: a proposer receives only its own book (`RoundRequest` holds one book's state
  and history). A2 never receives meanings. A3/B prompts never carry participant IDs,
  learner data, other books' scores or current-round ratings; B prompts carry no rating
  field at all.
- Masked outputs (#24 masked audit, anything for #34's blinded tables): anonymous book IDs
  only, no `audit.METHOD_COLUMNS` and no `masking.METHOD_REVEALING_FIELDS`; check with
  `masking_findings(text) == ()`. Outcome distributions can still hint at a method;
  masking removes labels, not every inference.

## 10. Timing budget

| Item | Value | Constant |
| --- | --- | --- |
| Proposal slot cap | 40 s (A1 server timer; A3/B request cancelled, result within 40.5 s) | `SLOT_CAP_MS` |
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
real components (#20, #22), `ManualClock` in unit tests.

## 11. Hardware- and human-pending policy

There is no GPU in development. Everything is built and tested against
`llm_fake.ScriptedLlmClient` and #16's mock OpenAI-compatible server. Evidence that needs
the LLM GPU host (300-call latency CSV, same-seed repeatability, p95 at the worst-case
prompt, the real-time dry run, a real-model example bank) or people (listening checks,
panels, screen recordings, sign-offs) is marked **Pending (human/hardware)** in the PR
and the issue checklist item stays open. Model weights are never downloaded here;
Hugging Face metadata (revision SHA, file SHA-256, licence) is fine at development time.
Tests never touch the network: every generation test runs under `netguard`.

## 12. Dependencies (declared once, in the skeleton)

| Package | Used for |
| --- | --- |
| `av-sound` (path, editable) | renderer, validator, store, fallback, composer guard |
| `numpy` | PCG64 streams (#18, #22, #23) |
| `scipy` | chi-square uniformity check (#18), logistic fit (#23) |
| `jsonschema` | every schema |
| `httpx` | LLM client (#16), test clients |
| `fastapi`, `pydantic`, `python-multipart`, `uvicorn`, `websockets` | A1 app, panel server and stations, listening tool, mock LLM server |
| `tokenizers` | optional offline token counting with the pinned tokenizer file (#16/#17) |
| `matplotlib` | listening-tool summary plot (#23; written to restricted storage or CI artifacts, never committed as an image) |
| dev: `pytest`, `pytest-cov`, `pytest-timeout`, `hypothesis`, `ruff`, `mypy`, `types-jsonschema`, `playwright` | tests, lint, types, browser tests |

## 13. Rules for implementers

- Do not edit shared files: `pyproject.toml`, `uv.lock`, the C modules above,
  `generation/schema/*` (except the schema you own: `audit-summary` #24,
  `freeze-manifest` #25, `bank-manifest` #26), `tests/generation/conftest.py`,
  `.github/workflows/generation.yml`, this file. If a contract must change, say so in
  your PR (what and why) and keep the change minimal and backwards compatible; the
  orchestrator merges contract changes.
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
  `test_threshold_tool.py`, `test_audit.py`. Use the shared fixtures
  (`serve_app`, `browser_page`, the network guard) and mark browser tests
  `@pytest.mark.browser`. CI: 3-OS matrix (ruff, format, mypy strict, pytest with an 85%
  coverage gate) and an Ubuntu Chromium job for browser tests.
- Synthetic data only, labelled `DEMO-`. No WAV or other binary file in git.

## 14. Decisions taken in the skeleton

| Decision | Rationale |
| --- | --- |
| `server_error` maps to `invalid_json` (status kept in `llm_status`) | #17's list has no server-error code; no JSON object came back, which is the parser's `invalid_json` rule; the slot is consumed either way |
| Validator precedence = `REASON_CODES` order | one order everywhere (`primary_code`); all codes are logged |
| `wire_seed` for the server | vLLM rejects seeds outside the signed 64-bit range; the unsigned derivation of #16 is kept |
| Slot and rating-slot IDs name the anonymous book or nothing | masking by construction; parseable and filename-safe on Windows |
| Bank IDs from the schedules dyad list (`bank-P001`, `bank-C001`) | file contract with #31; #27/#28 proposed other IDs before it existed |
| Prompt sets are loaded from a directory | protocol-quoted text can stay in restricted storage with only its hash committed (#17 decides in its public-data review) |
| Records carry every key (nullable) and are validated on write | unique canonical lines, byte-identical reports (#24), early failure |
