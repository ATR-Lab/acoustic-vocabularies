# Generation interface (`av_generation`)

Producer: Study A generation system (#16-#25) and the Study B bank contracts (#26-#28).
Consumers: the generation issues themselves, the package builder (#13: committed books,
dyad banks), the analysis pipeline (#34: audit tables), Unity (#70: bank manifests and
amendments) and the freeze (#25). Architecture, module owners, data flow and policies:
[`generation/docs/architecture.md`](../../generation/docs/architecture.md).

The package is used from this repository as an editable or path dependency
(`av-generation @ {root}/generation`, which depends on `av-sound @ {root}/sound`). Import
submodules directly; there are no package-level re-exports. Nothing needs the network at
runtime.

## Shared contracts (skeleton)

| Module | Public API |
| --- | --- |
| `seeds` | `derive_seed(*parts) -> int` (first 8 bytes of SHA-256 of the `\|`-joined key, unsigned big-endian); `seed_from_key(key)`; `a1_seed_key`, `a2_seed_key`, `a3_seed_key(batch_ns, atom, round, slot)`; `b_seed_key(bank_ns, attempt, profile, atom, slot)`; `panel_seed_key(set_ns, purpose, *parts)`, `bot_seed_key`, `threshold_seed_key`; `parse_seed_key`; `wire_seed(seed)` / `unwire_seed`; `rng_for(key) -> numpy Generator(PCG64)`; `seeds_digest(keys)` |
| `outcomes` | `SlotOutcome` (14 codes, `OUTCOME_CODES`), `LlmStatus`; `outcome_from_validator_codes(codes, *, mode="A"\|"B", cell_duplicate=False)`, `outcome_from_validation(result, ...)`, `outcome_from_llm_status(status)`; `VALIDATOR_PRECEDENCE`, `VALIDATOR_CODE_TO_OUTCOME`, `LLM_ONLY_OUTCOMES` |
| `jsonio` | `canonical_line`, `canonical_sha256`, `document_text`; shared hashes `messages_sha256(messages)` (prompt hash), `schema_sha256(schema)` (decoding-schema hash), `file_set_sha256({path: sha256})` (prompt and other file sets); `repair_torn_tail(path) -> TornTail \| None`; `JsonlAppender` (one lock per file: `path_lock`) |
| `records` | `SlotRecord`, `SlotRefusal`, `LlmRequest`, `RatingRecord`, `DecisionRecord` (`action` in `DECISION_ACTIONS`, `book_substituted`), `CommitRecord`, `FallbackScanRecord`, `PlayEvent`, `TimingEvent`, `ThresholdTrial` (JSONL); `RunManifest`, `ThresholdStimulusSet`, `ThresholdSession` (documents); `.to_dict()`, `.from_dict()`, `.check()`, `.sha256()`; `RecordWriter(path)`, `read_records(path, cls)`, `record_from_dict`, `cap_key` |
| `domain` | `COORDINATES` (12 `Coordinate`s in protocol order: name, field, index, values, kind, feature), `FIELD_VALUES`, `DOMAIN_SIZE`, `recipe_values`, `values_to_recipe`, `replace_values`, `differing_coordinates` |
| `ids` | `Study`, `Method`, `RunKind`; `proposal_slot_id`, `bank_slot_id`, `rating_slot_id` and their parsers; `slot_index`, `round_and_slot`, `check_id`, `check_book`; `check_panel_alias`, `PANEL_ALIAS_RE`; `bank_set(bank_id) -> "pilot"\|"confirmatory"\|"demo"` |
| `config` | `BatchConfig` (`batch-config.schema.json`; `.check_consistency()`, `.method_of()`, `.book_of()`, `.rating_positions(book_id)`, `.rating_slot_ids(book_id, atom_id)`); `PanelAssignment` (`order_index`, `order`, `aliases`, `raters`), `RaterSeat(rater_id, station, kind)` |
| `proposers` | `RoundProposer` protocol (`propose_round(RoundRequest) -> RoundResult`), `BookState` (`.references()`, `.without_labels()`), `CommittedAtom`, `AtomFeedback`, `CandidateFeedback`, `RaterScore`, `BCellState`, `RetainedOption` |
| `meanings` | `MeaningSet` (`meanings.schema.json`; `.text(label)`, `.for_atom(atom, labels)`, `.sha256()`), `load_meanings(dir, *, expected_sha256=None)`; DEMO set `generation/examples/demo-meanings/` |
| `genconfig` | `GenerationConfig` (`generation-config.schema.json`; `.frozen_sha256()`), `build_generation_config(name, *, llm_manifest_sha256, decoding_schema_sha256, prompts, meanings_sha256, separation_threshold, fallback)`, `fallback_pins(FallbackSet)`, `config_differences(config)`, `check_run_config(config, *, kind, freeze_manifest=None)` (`ConfigMismatch.code`), `FREEZE_CONFIG_KEY = "config.frozen_sha256"` |
| `panel_session` | `PanelSessionHost` protocol (`seats`, `wait_events(after_seq, timeout_s)`, `snapshot`, `asset_bytes`, `station_joined`, `station_left`, `asset_ready`, `clock_synced`, `report_play`, `submit_rating -> RatingAck`, `report_withdrawal`); `PanelEvent`, `PanelSlot`, `PanelSnapshot`, `AssetRef`, `PlayReport`, `RatingSubmission`, `RatingAck`, `PanelRefused` |
| `rater_protocol` | `PROTOCOL_VERSION`, `WS_PATH`, `ASSET_PATH`, `STATION_PAGE`, message types, `parse_message`, `message_errors` (`rater-message.schema.json`; `hello` carries `kind`) |
| `rundir` | `create_run_dir`, `run_layout`, `RunLayout` (`.generation_config`, `.dry_run_plan`, `.threshold_session(id)`, `.bank_dir(id)`, ...), `LOG_FILES`, `check_run_id`, `check_run_location` |
| `masking` | `masking_findings(text)`, `METHOD_REVEALING_FIELDS`, `drop_method_fields` |
| `clock` | `Clock` protocol (`now_ms`, `sleep`, `asleep`, `utc_now`), `SystemClock`, `ScaledClock`, `ManualClock`, `utc_text` |
| `constants` | budgets, timings, `FROZEN_DECODING`, `PANEL_ORDERS`, B caps, `MESSAGE_MIN_MS`/`MESSAGE_MAX_MS` |
| `bank_manifest` | `bank_manifest_errors`, `bank_sha256`, `require_bank_set(manifest, set)`, `bank_amendment_errors`, `amendment_chain_errors(manifest, amendments)`, `effective_menu(manifest, amendments)` |
| `netguard`, `webserve`, `llm_fake` | `deny_outbound()` (sockets, asyncio loops incl. the Windows proactor, UDP), `serve_in_thread(app)`, `ScriptedLlmClient` |

Schemas: `generation/schema/*.schema.json` (Draft 2020-12, `additionalProperties: false`,
`$id` `https://github.com/ATR-Lab/acoustic-vocabularies/blob/main/generation/schema/<name>`;
recipes and fallback scans refer to the sound schemas by `$id`).

Config hash: every "config hash" (freeze item, run manifests, bank manifests, bank
registers) is `GenerationConfig.frozen_sha256()` of a `generation-config.json`
(architecture section 11).

## LLM server and client (#16)

Guide: [`generation/docs/llm.md`](../../generation/docs/llm.md).

- **Client** (`av_generation.llm`):
  - `OpenAICompatibleClient(base_url, model, *, run_id, clock, request_log=None,
    timeout_ms=40_000, runtime, model_revision, count_timeout_ms=5_000)` and
    `.from_manifest(base_url, manifest, *, run_id, clock, request_log=None)`.
  - `propose(messages, schema, seed_key, *, slot_id=None, deadline_ms=None) ->
    RawOutcome{status, text, latency_ms, tokens_in, tokens_out, seed, finish_reason}`
    makes one call and never retries, not even a failed connect. `status` is one of:
    - `ok`: `finish_reason` `stop`;
    - `overflow_output`: `length`, 512 tokens;
    - `server_error`: any other finish reason, an HTTP error, a bad body or a refused
      connection;
    - `timeout`: the request was cancelled at 40 s of run-clock time, or at
      `deadline_ms`, and the call returned within 0.5 s of it.
  - A finish reason that the log schema cannot hold is returned and logged as `other`
    (`server_error`).
  - Seed keys are `A3|...` or `B|...` only.
  - One `LlmRequest` per call. It holds the five frozen decoding values, the seed and
    wire seed, `prompt_sha256 = jsonio.messages_sha256`, `schema_sha256 =
    jsonio.schema_sha256`, the status, latency, tokens and `slot_id`.
  - `count_prompt_tokens(messages, *, deadline_ms=None)` sends `POST /tokenize`
    (`add_generation_prompt: true`) and raises `TokenCountError`. It has its own
    run-clock cap of 5 s (`TOKEN_COUNT_TIMEOUT_MS`). At the cap or the deadline it
    raises `TokenCountTimeout`, a subclass of `TokenCountError`.
  - **Slot budget (#17, #26).** Pass `deadline_ms = ticket.t_open_ms + SLOT_CAP_MS`
    (run clock) to both calls of a slot, so count plus call stay within the 40-s cap.
    When the deadline has passed, `propose` makes no call and returns a logged
    `timeout` with latency 0. `deadline_ms` is part of the `LlmClient` protocol, and
    `llm_fake.ScriptedLlmClient` accepts it and records it in `FakeCall.deadline_ms`.
  - The methods are synchronous. Async callers (#20, #21) use
    `await asyncio.to_thread(client.propose, ...)`; calling them on an event loop
    blocks that loop for up to 40 s.
  - `decoding_schema()` returns `sound/schema/recipe.schema.json` unchanged, with the
    §3.2 enums. `decoding_schema_sha256()` returns the freeze item
    `schema.decoding_sha256`.
- **Request body** (`chat_request_body`): `temperature` 0.7, `top_p` 0.9, `top_k` 50,
  `repetition_penalty` 1.0, `max_completion_tokens` 512, `seed` = `wire_seed(seed)`,
  and `response_format` `{"type": "json_schema", "json_schema": {"name":
  "acoustic_motif_recipe", "schema", "strict": true}}`. There is no `guided_json`.
- **LLM manifest** (`av_generation.llm_manifest`, `generation/llm/manifest.json`):
  - It pins the model and tokenizer revision `a09a3545...`, the licence `apache-2.0`,
    the files (LFS SHA-256 for weights, git blob SHA-1 for the others), `weights_sha256`,
    the chat-template SHA-256, the runtime (`vllm 0.30.0`, `bfloat16`, `max_model_len`
    16,896, `xgrammar`, `--generation-config vllm`), the decoding values, the
    decoding-schema hash and the hardware (Pending).
  - `llm_manifest_sha256` = file SHA-256 (`manifest_sha256()`).
  - API: `load_llm_manifest(path=None)`, `verify_model_dir(manifest, dir)` (raises
    `ModelMismatch.code`), `apparatus_values(manifest, *, prompts) -> {model_revision,
    runtime_precision, prompt_hash}`, `freeze_values(manifest, *,
    manifest_file_sha256)` (the `model.*`, `runtime.*`, `decoding.implementation`,
    `schema.decoding_sha256` and `llm.manifest_sha256` items for #25),
    `probe_hardware()`.
- **Server** (`av_generation.llm_server`, `generation/llm/server-config.json`):
  - `prepare_launch(model_dir, ...) -> LaunchPlan` raises `ServerRefused.code`. The
    codes are `E_MANIFEST`, `E_CONFIG`, `E_CONFIG_MANIFEST`, `E_RUNTIME_*`,
    `E_MISSING`, `E_SIZE`, `E_EXTRA_WEIGHTS`, `E_EXTRA_FILE` (any unlisted file, such
    as a stray `chat_template.jinja`), `E_REVISION*`, `E_FILE_BLOB`,
    `E_CHAT_TEMPLATE` and `E_WEIGHTS_SHA256`.
  - The runtime version is the one that the started executable reports
    (`<vllm> --version`, `executable_vllm_version`). The launcher runs from this uv
    project, and `--vllm` names the pinned vLLM in its own environment.
  - `start_server(plan) -> ServerProcess` (`E_STARTUP`).
  - The offline environment is `OFFLINE_ENV`.
  - Startup is logged as `startup_start` / `startup_end` timing events with
    `component="llm"`.
- **Mock server** (`av_generation.mock_llm`): `MockLlmServer().app` serves
  `/v1/chat/completions`, `/tokenize`, `/health`, `/version` and `/v1/models`. Replies
  are scripted with `MockReply`, and the runtime label is `MOCK_RUNTIME`.
- **Benchmark** (`av_generation.llm_bench`):
  - Files: `llm_latency.csv` (`LATENCY_COLUMNS`), `llm_latency_summary.json` and
    `llm_repeatability.csv` (`REPEAT_COLUMNS`).
  - Committed outputs: `generation/bench/`, MOCK only. The LLM-GPU numbers are Pending
    (hardware).

## Prompt builder, parser and slot ledger (#17)

Details: [`generation/docs/prompts-and-ledger.md`](../../generation/docs/prompts-and-ledger.md).

| Module | Public API |
| --- | --- |
| `ledger` | `SlotLedger(path, *, run_id, clock, refusals=None, timing=None, cap=12)`. `.reserve(cap_key, slot_id, *, study, method) -> SlotTicket` runs before any work: it checks the cap first (`SlotCapExceeded`, refusal `slot_cap`), then reuse (`SlotReused`, refusals `slot_reused` / `slot_closed`), and also the slot ID against the cap key and one method per cap key (`LedgerError`). `.consume(SlotRecord)` needs the matching open ticket (`SlotNotReserved`) and validates, then appends one canonical line. Also `.check_attempt(bank_id, attempt)` (`AttemptCapExceeded` for a 5th attempt, refusal `attempt_cap`), `.used(cap_key)`, `.remaining(cap_key)`, `.records(cap_key=None)`, `.open_tickets()` and `.repairs`. Refusals are always logged: to `refusals`, else to `slot-refusals.jsonl` beside the ledger. On reopen, torn tails are cut and logged as `log_repaired`, and the existing records count toward the caps. |
| `prompts` | `load_prompt_set(path, *, meanings, expected=None) -> PromptSet`. Its fields are `a3_instruction`, `b_instruction`, the section templates, `files`, `set_sha256`, `a3_sha256`, `b_sha256` and `context_schema_sha256` (the hash of the recipe schema the context shows; not the decoding-schema hash), plus `.hashes() -> genconfig.PromptHashes` and `.check_decoding_schema(decoding_schema)` (`PromptSetError` unless the decoding schema without its root annotations is the context schema). Also `context_schema(schema)`, `default_prompt_set_dir()` (`generation/prompts/`) and `prompt_set_manifest(path, *, name)`. `build_a3_prompt(book_state, atom_id, round, slot, *, semantic_label, feedback, same_round, prompt_set) -> BuiltPrompt` and `build_b_prompt(cell, *, prompt_set, threshold=None) -> BuiltPrompt`. `BuiltPrompt` carries `messages` (system = instruction, user = canonical context JSON), `prompt_sha256 = jsonio.messages_sha256(messages)` and `context_json`. Errors: `PromptSetError`, `PromptContextError`. |
| `parser` | `parse_output(text) -> ParsedOutput(obj, error)`: exactly one strict JSON object, else `obj=None` (outcome `invalid_json`, logged as validator code `E_JSON`). |
| `a3` | `A3Proposer(client, ledger, prompt_set, decoding_schema, *, clock, reserved=None)`, a `RoundProposer` with three slots a round. `BSlotProposer(client, ledger, prompt_set, decoding_schema, *, clock, threshold=None, reserved=None).propose_slot(cell, *, seed_namespace) -> SlotRecord` handles one Study B slot for #26. Both check the decoding schema at construction (`PromptSetError`). Each slot has a deadline, `slot_deadline_ms(t_open_ms, window_end_ms=None)`: 40 s after `reserve`, or the end of the proposal window if that comes first. The 40 s cover the token count and the call; no call starts at or after the deadline, and an answer after it is `timeout`. `RAW_OUTPUT_MAX_CHARS`. |

Prompt set `prompts-v1` (`generation/prompts/`) has the following hashes. The freeze
items are `prompts.a3_sha256` and `prompts.b_sha256`:

- `a3_sha256` `0e20949a8ff628ba3258a06b70eb5231342466799301d4d5c67a22c9fd681209`
- `b_sha256` `c23dab615f354ccb471c5c980842f7285b1b031ae85723138841df3a8579a8a7`
- set `d3f6c84f8247dd810f520038611bcad45cf82c9ad9ac4ed75754ac2613d0e4ce`
- `context_schema_sha256` `a8ee5754442748826ebf2452a59e2d6cb7925f6db887163c4228a3cc5b957b6c` (the schema the prompt shows; the decoding-schema hash in slot records, `LlmRequest`s and `schema.decoding_sha256` is a different value)

The fixed instruction (Study A §3.6) has SHA-256
`05738403a734776ffa77729d142b26ec36dbc58767a29cf161bd377ca904ebe3`. Study B reuses it byte
for byte. Measured with the pinned tokenizer, the worst-case prompts are 4,923 tokens
(A3) and 10,115 tokens (B), both below 16,384.

Example ledger: `generation/examples/demo-slot-ledger/` (DEMO, all 14 outcome codes and
two refusals).

Call order per method:

- A1 opens a slot with `reserve`.
- A2 runs `reserve`, sample or mutate, validate, `consume`.
- A3 and B run build prompt, `reserve`, `count_prompt_tokens` (`overflow_input` above
  16,384, so 16,384 is sent; a failed count is `invalid_json`/`server_error`; `timeout`
  if the count used up the slot's 40 s), one `propose`, parse, validate, `consume`.

## A2 mutation search (#18)

`av_generation.a2` ([`generation/docs/a2-search.md`](../../generation/docs/a2-search.md)),
Study A protocol §3.5.

- `A2Proposer(ledger, *, clock)` is a `RoundProposer`. `propose_round(request) ->
  RoundResult` fills slots 1..3. Each slot runs `SlotLedger.reserve`, the proposal,
  `validate` against `request.book.references()` at `request.book.threshold`, then
  `consume`. Every outcome consumes the slot, with no resampling. Ledger refusals
  propagate; nothing else can interrupt a round after a reservation.
- Requests: `request.book` must be `BookState.without_labels()` and `semantic_label` must
  be `None`. Feedback must hold `round - 1` closed rounds of this book and atom, and its
  incumbent must follow the selector rule (highest score, then the lowest `slot_index`).
  IDs, profile, `seed_namespace` and `book.threshold` must be well formed. Otherwise
  `A2RequestError.code` is `E_A2_LABEL`, `E_A2_REQUEST`, `E_A2_PARENT` or `E_A2_METHOD`,
  raised before any reservation.
- Proposals: uniform samples without an eligible parent. Otherwise child k (slot k)
  mutates exactly k coordinates of the incumbent. Each proposal is a pure function of
  `a2_seed_key(seed_namespace, atom, round, slot)` and the parent (`plan_slot`).
  Draws come from the raw PCG64 output (`draw_index`; frozen order `A2_ALGORITHM`).
- Helpers: `sample_uniform(rng)`, `mutate(parent, k, rng, *, parent_slot_id=None) ->
  (Recipe, A2Detail)`, `mutate_pitch(value, step, *, coordinate="pitch_1") ->
  A2Mutation`, `mutate_index(coordinate, value, direction)`,
  `choose_coordinates(rng, k)`, `select_parent(feedback)`, `check_request(request)`.
- Slot records: `seed_key`/`seed`, the canonical recipe JSON in `raw_output`,
  `latency_ms` = compute time, and `pcm_sha256`/`file_sha256` when the waveform is
  usable. A2 writes no audio. `a2 = A2Detail(mode, parent_slot_id, mutations)`, and
  every reflection correction is logged (`corrected=true`). A2 never times out.
- Cross-platform fixture: `tests/generation/fixtures/a2-proposals.json`. Credibility check
  (synthetic): `python -m av_generation._a2_credibility --grid`, with the result in
  `generation/runs/DEMO-a2-credibility/grid.json`.

## A1 hand-designer interface (#19)

Implemented in `av_generation.a1` (reference:
[`generation/docs/a1-interface.md`](../../generation/docs/a1-interface.md); designer and
operator guide: [`generation/docs/a1-operating-guide.md`](../../generation/docs/a1-operating-guide.md)).

- `A1SlotService(ledger, plays, timing, *, clock, designer_id, meanings, practice=False,
  refusals=None, station=None, run_id=None, token_factory=None, audio_ttl_ms=60000,
  poll_interval_s=0.01)`: the A1 `RoundProposer`. `propose_round(request) ->
  RoundResult` opens the round's window for the designer's book and blocks until its
  three slots have closed. The slots run back to back (Study A protocol §3.3): slot 1
  opens with the window, each next slot when the previous one closes (submit or 40-s
  server timer), each with up to 40 s (cut only by `window_end_ms`). It raises
  `ValueError` for a request of another method, another book, a practice/study
  mismatch, or feedback naming another book.
- Study mode for the batch runner (#20): `study_service(layout, ledger, config, *,
  clock, meanings, station=None)` builds the service on the run's shared slot ledger and
  the run's `play`, `timing` and `slot_refusal` logs (designer from the A1 book of
  `config`); `serve_a1(service, *, host="127.0.0.1", port=DEFAULT_PORT)` is a context
  manager that serves the app (port 8741) and yields the page URL. The batch runner
  (#20, `batch_runner`) calls them for every batch with real proposers.
- `create_a1_app(service)`: FastAPI app serving `a1.ROUTES` (`GET /a1/`,
  `GET /a1/api/state`, `POST /a1/api/slots/open`,
  `POST /a1/api/slots/{slot_id}/submit` with `{"recipe": ...}`,
  `GET /a1/api/audio/{token}`, `GET /a1/api/feedback`, `GET /a1/api/book`,
  `POST /a1/api/activity`). `POST /a1/api/slots/open` returns the open slot (slots open
  by themselves). Errors: `{"error": {"code", "message"}}` with `E_NO_WINDOW`,
  `E_SLOT_OPEN` (familiarization during a slot), `E_SLOT_CAP`, `E_UNKNOWN_SLOT`, `E_SLOT_CLOSED`,
  `E_UNKNOWN_TOKEN`, `E_TOKEN_USED`, `E_TOKEN_EXPIRED`, `E_BAD_REQUEST`, `E_TOO_LARGE`,
  `E_BAD_ACTIVITY`. The same operations are methods of the service (`state()`,
  `open_slot()`, `submit(slot_id, recipe)`, `audio(token)`, `feedback()`, `book()`,
  `activity(kind)`), which the dry-run bot designer (#22) may call in-process.
- Logs: one `slot` record per slot through `SlotLedger.reserve`/`consume` (#17;
  `designer_id`, `practice`, `latency_ms`, `design_ms`, `raw_output`; no seed);
  `slot_refusal` for edits of closed slots and submits at or after the deadline
  (`slot_closed`) and requests after the atom's 12 slots (`slot_cap`), plus the
  ledger's own refusals when a slot opens; `play` (`a1_preview` / `a1_practice`, `audio_kind atom`,
  `asset_id` = WAV file SHA-256, `pcm_sha256`, `slot_id`, `token_id`; one `played` per
  valid slot at most, then `refused` with `E_TOKEN_USED` / `E_TOKEN_EXPIRED`); `timing`
  (`familiarization_*`, `design_active_*`, component `a1`). Every `played` event joins a
  consumed `valid` slot with the same hashes.
- Practice mode (O1.2.4): `_a1_practice.open_practice_session(runs_root, run_id, *,
  designer_id, meanings, clock, kind="practice"|"demo", ...)` makes a separate practice
  run (`purpose practice`; batch and book IDs with a `PRACTICE` token, refused by study
  services); `python -m av_generation._a1_cli practice ...` serves it. Synthetic
  practice texts: `generation/examples/demo-practice-meanings/`. Kiosk browser policy:
  `generation/kiosk/a1-chrome-policy.json`.

## Separation-threshold listening tool (#23)

Implemented. Guide, formats and decisions:
[`generation/docs/threshold-tool.md`](../../generation/docs/threshold-tool.md). Hand-off to
O6.2.2: the tool, the operator guide, the CSV formats and the summary script; to G4
(#25): the summary document (`summary.json`, `av-generation/threshold-summary` v1) as
the threshold evidence file. The tool reports data and never sets the threshold.

| Module | Public API |
| --- | --- |
| `threshold` | `DEFAULT_CONFIG` (3 profiles x 7 bins 0.050..0.200, half-width 0.0125, 8 pairs per bin, 56 catch pairs, 500-ms gap = 224 trials); `check_config`, `load_config`, `n_trials`, `bin_limits`, `in_bin` (exact, `[c-h, c+h)`); `generate_stimuli(set_id, config=DEFAULT_CONFIG, *, reserved=None) -> ThresholdStimulusSet`; `check_stimuli(stimuli, *, reserved=None, render=True) -> tuple[str, ...]`; `coverage`, `expected_pair_ids`, `expected_seed_keys`, `write_stimuli`; `plan_session(stimuli, session_id, *, listener_id, station, gain_db, tryout, created_utc) -> ThresholdSession`; `check_session`; `session_presentations`, `trial_id`; `check_plays(session, stimuli, plays, trials, *, timing=None, complete=True) -> PlayCheck` (`n_unreported`), `deliveries(timing, session_id)`, `DELIVERY_EVENT`; `export_csv(trials, stimuli, path) -> sha256`, `read_trials_csv(path)`, `check_trials_csv(path, stimuli)`, `TRIAL_CSV_COLUMNS`; `is_demo_data`, `check_output_dir` (`E_POLICY`); `summarize(trials)` (`SUMMARY_CSV_COLUMNS`), `wilson_interval(k, n)`, `fit_logistic`, `fit_summary` (`FIT_COLUMNS`), `build_summary`, `write_summary`, `write_summary_csv`, `write_fits_csv`, `plot_summary`; `ThresholdError(code)` |
| `threshold_runner` | `open_threshold_run(runs_root, run_id, kind, stimuli, *, clock) -> RunLayout`, `load_run`, `read_run_records`, `read_run_timing`; `ThresholdRunner(layout, stimuli, session, *, clock, fsync=True)`; `create_threshold_app(runner)` with `ROUTES` (`/threshold/`, `/threshold/api/state`, `next`, `audio/{token}`, `trials/{i}/played`, `trials/{i}/response`, `trials/{i}/skip`), static page `STATIC_DIR` (`web/threshold/`); `BotListener`, `run_bot_session(client, session, stimuli, listener=None)`; `export_run(layout, out_dir, *, tryout, label=None, plot=True) -> ExportResult`; `RunnerError(code, status)` |
| `threshold_cli` | `python -m av_generation.threshold_cli stimuli \| check \| session \| export \| summary \| demo`; `run_demo(out_dir, *, sessions, small)` |

Records: `ThresholdStimulusSet` (`threshold/stimuli.json` of the run; set hash `sha256()`;
DEMO example `generation/examples/threshold/demo-stimuli.json`, set `DEMO-T1`),
`ThresholdSession` (`threshold/sessions/<session>.json`, written before the first trial;
`ab_order_rule = "balanced_per_bin"`), `ThresholdTrial` (`logs/threshold-trials.jsonl`),
`PlayEvent` (`logs/plays.jsonl`, contexts `threshold_first`/`threshold_second`,
`audio_kind="atom"`, `trial_id = <session>.t<NNN>`, reported after motif B has ended,
`onset_ms` at the speaker and `scheduled_ms` = onset minus the output latency, refusals
`E_TOKEN_USED`, `E_ALREADY_PLAYED` and `E_UNREPORTED`) and `TimingEvent` (`component="threshold"`:
`session_start`, `session_end`, `operator_action` for skips, `asset_ready` for each
audio delivery with detail `<trial_id> <first|second> <token>`, `log_repaired`). Seed keys:
`THRESHOLD|<set>|pair|<profile>|<center>|<k>`, `THRESHOLD|<set>|same|<profile>|<k>`,
`THRESHOLD|<set>|order|<session>`, `THRESHOLD|<set>|bot|<session>` (bot listeners only).
Runs: purpose `threshold`; DEMO sets only in demo/synthetic runs; pilot runs (listeners,
internal tryout) outside any git work tree; their exports too (`E_POLICY`).

## Round orchestrator, selector and panel session host (#20)

Guide and decisions: [`generation/docs/orchestrator.md`](../../generation/docs/orchestrator.md).

| Name | Contract |
| --- | --- |
| `orchestrator.Orchestrator(config, layout, proposers, store, fallback, *, clock, generation_config, meanings, kind, freeze_manifest=None, purpose="batch", preload_lead_ms=1000, slot_event_lead_ms=500)` | Runs one Study A batch. Refuses to start on mismatched pins (`E_CONFIG`), a batch set that does not fit the run kind (`E_KIND`) or `genconfig.check_run_config` (`ConfigMismatch`). Writes or checks `config.json`, `generation-config.json`, `run-manifest.json`; closes the manifest after atom 16 |
| `.run_atom(atom_id)`, `.run_appointment(n)`, `.run_batch()` | Atoms in `atom_order`; 4 rounds each: parallel `propose_round` calls, 9 rating slots in `panel.order` blocks, one `decision` per book; then commit, bank fallback or whole-book substitution |
| `.resume() -> str \| None` | Repairs torn tails, reloads the logs, finishes an interrupted atom; returns the next atom |
| `.panel_host() -> PanelSessionHost` | For `panel.create_panel_app` (#21). Writes every `rating` (one per seat at each lock: rated, `placeholder` or `missing`), `play` and panel `timing` record. Rating refusals: `E_UNKNOWN_RATER`, `E_UNKNOWN_SLOT`, `E_SLOT_CLOSED` (at or after start + 20 s), `E_PLACEHOLDER`, `E_LOCKED` (before unlock), `E_FIRST_ATOM`, `E_PROTOCOL` (missing distinguishability, value outside 1-7), `E_DUPLICATE_RATING` |
| `.console() -> ConsoleView` | Operator console: panel aliases, slot and atom counts, fallback use, station connections; never a book ID or method. Thread-safe (an operator UI may poll it during a batch) |
| `.mark_incomplete(reason)`; withdrawal via the host | `batch_incomplete` timing event, store books voided (`batch_rebuild`), `BatchIncomplete` (`E_BATCH_INCOMPLETE`). The host publishes `end` (`withdrawn` / `aborted`) and halts: no event follows it and `snapshot()` is `ended` with no slot |
| `panel_order_schedule(set_ns, panel_ids, *, profiles=None) -> {panel: 1..6}` | Seed `PANEL\|<set_ns>\|orders`; `profiles` maps each panel to its batch's profile (batch table): one permutation of the 6 orders per profile, differing at every position across profiles (each profile's 6 batches use every order once; 18 panels: each order 3 times; 3 pilot panels: 3 orders). `set_ns` is restricted for pilot and confirmatory sets |
| `panel_aliases(set_ns, panel_id, book_ids) -> {book: PB-XXXX}` | Seed `PANEL\|<set_ns>\|aliases\|<panel>` |
| `write_panel_order_csv(path, set_ns, [(panel_id, batch_id)], *, profiles=None)`, `PANEL_ORDER_COLUMNS` | `panel_id,batch_id,profile,order_index,order,seed_key,seed,aliases_seed_key`; DEMO: `generation/examples/demo-panel-orders/` |
| `read_batch_table(path)`, `check_permutation(definition, permutation)`, `read_book_key(path, unit_id)`, `build_batch_config(...)`, `rebuild_batch_config(config, *, set_ns, panel_id, raters, seed_namespace)` | Batch configs from the schedules files (#29 batch table and `permutation.json`, #31 book key) |
| `nearest_committed(book_state, recipe) -> CommittedAtom \| None` | Nearest reference by 12-feature distance, ties to the lowest commit index, `None` on the first atom |
| `substitute_book_id(book_id)` | `<book>-FB`: store book of the frozen fallback book after a failed scan |
| `check_batch_pins(config, generation_config, fallback, meanings, *, kind)` | The input pins and the set/kind rule (`E_CONFIG`, `E_KIND`), run by `Orchestrator` and the batch runner |
| `batch_runner.load_batch_inputs(*, config, meanings, fallback, generation_config=None, prompts=None, llm_manifest=None, freeze_manifest=None, proposers="real") -> BatchInputs` | Reads a batch's inputs (committed prompt set and LLM manifest by default; a demo batch without a generation config gets `demo_generation_config`) |
| `batch_runner.check_batch_start(inputs, *, kind, run_id, proposers="real")` | The one start check of every batch run, before anything is created: run ID, `check_batch_pins`, prompt-set / meaning / decoding-schema / LLM-manifest hashes against the generation config (`E_INPUTS`), stand-ins only for demo runs (`E_MODE`), `genconfig.check_run_config` (frozen G4 config for confirmatory runs). The G4 freeze guard (#25) goes here |
| `batch_runner.open_batch(inputs, run_dir, *, kind, clock, proposers="real", llm_url=None, a1_station=None, resume=False, purpose="batch", ...) -> StudyBatch` | Start checks, `probe_llm_server` (pinned model and vLLM version; #16's mock for demo runs only; `E_LLM_SERVER`), then the run directory and the real components: one `SlotLedger` shared by `a1.study_service`, `A2Proposer` and `A3Proposer` (with `OpenAICompatibleClient` logging to `llm-requests.jsonl`) |
| `batch_runner.run_session(batch, *, appointment="next", panel="stations", designer="kiosk", a1_host, a1_port, panel_host, panel_port, ...) -> str \| None` | Serves A1 (`a1.serve_a1`) and the panel (`batch_runner.serve_panel` -> `panel.create_panel_app`, port 8765), waits for every seat, finishes an interrupted atom (`resume`) and runs the appointment(s). Synthetic runs only: `panel="bots"`, `designer="bot"`, `proposers="sim"` |
| `python -m av_generation.batch_runner check\|run ...` | Command line (`--run-dir`, `--kind`, inputs, `--llm-url`, `--a1-host`, `--panel-host`, `--appointment next\|all\|1..4`, `--resume`); exit 0 / 1 refused / 3 batch incomplete |
| `selector.score_candidate(slot, ratings, *, first_atom) -> CandidateScore` | Eligible = valid and >= 2 comfort `acceptable` (missing = not acceptable); score = exact mean of (association + distinguishability) / 2 over raters with both (first atom: distinguishability 4); fewer than 3 raters: `flagged_missing` |
| `selector.pick_incumbent(candidates) -> (slot_id, Fraction)` | Best eligible over the atom so far; ties to the lowest `slot_index`; `(None, None)` triggers the bank scan |

Logs per batch: 576 `slot` (proposers), 576 `rating` per rater, 192 `decision`, 48
`commit` in the final store books (+ k-1 superseded commits for a book substituted at
atom k), one `fallback_scan` per atom without an incumbent before a substitution,
`play` and `timing` records. Handoffs: #21 uses `panel_host()`; #22 drives the same
`Orchestrator` (`purpose="dry_run"`); #24 reads the logs; #13 takes the final store books
from the `commit` records (`store_book_id`).

## Rater panel server, stations and bot rater (#21)

Component doc: [`generation/docs/rater-panel.md`](../../generation/docs/rater-panel.md).
Message formats: `av_generation.rater_protocol` (`rater-message.schema.json`); host
contract: `av_generation.panel_session` (implemented by #20).

| Module | Public API |
| --- | --- |
| `panel` | `create_panel_app(host, *, clock, access_secret=None)` (FastAPI: `GET /panel/station`, `GET /panel/static/{station.js,station.css}`, `GET /panel/assets/<sha256>.wav`, WebSocket `/panel/ws`; `app.state.panel` is the `PanelServer`, with `.seat_key(rater_id, station)`); `STATION_STATIC_DIR` (`web/rater/`); `rating_refusal(slot, submission) -> code \| None` (slot rules, in the #20 host's order: `E_SLOT_CLOSED` from `start + 20,000`, `E_PLACEHOLDER`, `E_LOCKED` before `start + unlock_offset_ms`, `E_FIRST_ATOM` for a distinguishability on a first-atom slot, `E_PROTOCOL` for a missing one elsewhere or a value that is not an `int` in 1-7, e.g. `5.0`); `required_plays(slot)` (the roles a station must have reported `played` before rating: the candidate, and the reference when distinguishability is asked); `seat_key(secret, rater_id, station)` (HMAC seat key); `slot_message(slot, *, rejoin=False)`, `preload_message(assets)`, `server_message(event)`, `asset_url(asset_id)`, `station_url(base_url, station, rater_id, *, key=None)`; `ClockSyncEstimator` (chained probe bursts); constants `SYNC_BURST` (8), `SYNC_INTERVAL_MS` (60,000), `SLOT_LEAD_MS` (500, `slot` event lead of the scripted host and #20), `ONSET_TOLERANCE_MS` (50), `MAX_SKEW_MS` (100), `MAX_LATE_START_MS` (1,000), `MAX_FRAME_BYTES` (4,096), `REPLACED_CLOSE_CODE` (4001), `SEAT_KEY_PARAM` (`key`) |
| `rater` | `BotRater(base_url, *, rater_id, station, run_id, policy, clock=None, max_reconnects=20, open_timeout_s=10.0, access_key=None).run() -> BotRunResult`; `BotRater.from_station_url(url, *, run_id, policy, clock=None, max_reconnects=20, open_timeout_s=10.0)` (server root, seat and key from a keyed station URL; `ValueError` for any other URL); `BotRatingPolicy(p_comfort_acceptable=0.9, force_unacceptable_slots=frozenset(), p_missing=0.0, drop_slots=frozenset(), withdraw_at=None, rt_ms_range=(400, 4000))` (rating-slot IDs, never book IDs); `bot_rating(run_id, rater_id, rating_slot_id, *, ask_distinguishability, policy) -> BotRating` (stream `rng_for(bot_seed_key(run_id, rater_id, "rating", rating_slot_id))`) |
| `panel_demo` | `ScriptedPanelHost(session, *, clock, run_dir=None)` (a `PanelSessionHost` for a fixed schedule: `begin`, `begin_when_joined`, `tick`, `start`/`stop`, `wait_ended`, `republish`, `operator`; records in `.ratings`, `.plays`, `.timing` and `run_dir/logs/`); `demo_session(*, run_id, seats, atom_index, rounds, placeholders, positions, ...) -> ScriptedSession` (DEMO batch config, meanings and rendered DEMO atoms); `schedule_document(host)` (`panel-schedule.json`); CLI `python -m av_generation.panel_demo` |
| `batch_runner` (#20 module; the panel's call sites) | `serve_panel(host, *, clock, bind="127.0.0.1", port=8765)` draws a fresh access secret per session (`secrets.token_bytes(32)`, in memory only: never logged, printed or written), serves `create_panel_app(host, clock=clock, access_secret=secret)` and yields `ServedPanel(base_url, station_urls)` (station -> `station_url(..., key=seat_key(secret, rater_id, station))`, seat order); `run_session(..., panel="stations")` logs `Rater station <station> (<rater>): <keyed URL>` per seat, sets `StudyBatch.served_panel` while serving, calls `on_panel(base_url)` and waits for every seat, and after the appointment waits up to `station_end_grace_s` (10 s) for the stations to leave before stopping the server (`wait_for_stations_to_leave`; #22); one appointment per station session (`E_MODE` for `appointment="all"` and for a batch whose panel session already ended: reopen the run); `station_urls(batch) -> dict[str, str]` (the running session's keyed URLs, for bot stations started in `on_panel`; `E_STATIONS` outside a station session) |
| `panel_skew` | `detect_onsets(signal, rate)`, `parse_wav(data)` / `read_capture(path)`, `align(scheduled_ms, detected_ms)`, `capture_onsets(capture, schedule, channels)`, `logged_onsets(plays)`, `onset_table(schedule, onsets)`, `summarize(rows, schedule, ...)`, `read_schedule(path)`, `schedule_from_plays(plays, run_id)`; CLI `python -m av_generation.panel_skew logged\|loopback` (`logged-onsets.csv`, `loopback-skew.csv` and their `-summary.json`) |

Rules the server enforces: a browser handshake from another origin gets HTTP 403; every
frame is schema-checked and integer fields must be JSON integers (ratings only integers
1-7 and a binary comfort; no free text; otherwise `error E_PROTOCOL` and nothing reaches
the host); `hello` first and only for a seat of `host.seats()` (`E_UNKNOWN_RATER`), with
the seat key when the app has an `access_secret` (also needed for assets; the batch
runner always passes a fresh one and hands out `station_url(..., key=seat_key(...))`, so
a wrong or missing key gets `E_UNKNOWN_RATER` or HTTP 403); a new `hello` replaces the
station's old socket, which is closed with `REPLACED_CLOSE_CODE` (stations and bots then stop); `sync_request` is
answered at once; `played` must match the slot's asset and scheduled time; ratings are
pre-checked (`E_UNKNOWN_SLOT`, `rating_refusal`, `E_PROTOCOL` unless the station
reported playing every sound of `required_plays`, then `E_DUPLICATE_RATING`) before
`host.submit_rating`, and `rating_ack` goes to the rating station only. A (re)joining
station gets `welcome`, the pending `preload` and the current `slot` with `rejoin=true`;
stations never play a sound twice, skip a slot joined after its candidate onset and show
a slot whose sound did not play as a neutral screen (rating missing in both cases). A
withdrawal is resent after a reconnect until the server answers it; a broadcast
`end withdrawn` (#20 ends the session when a rater withdraws) shows the normal end screen
on the other stations. The host writes every rating, play and panel timing record
(`panel_session`; host duties in the component doc, section 6).

Handoff to #22 (bot stations in a batch run): `run_session(batch, panel="stations",
on_panel=hook)`; in `hook(base_url)`, one `BotRater.from_station_url(url, run_id=...,
policy=..., clock=batch.clock)` per URL of `batch_runner.station_urls(batch)`, each run in
its own thread (the batch config's seats must be `bot` seats). A full batch is four such
sessions, one per appointment, each on the run reopened with `open_batch(..., resume=True)`
(a second station session on the same `StudyBatch` is refused with `E_MODE`: its panel
session has ended) and each with new keys.

## Synthetic-panel dry run (#22)

*Pending (#22).* Skeleton: `DryRunPlan` (`dry-run-plan.json`,
`dry-run-plan.schema.json`: zero-eligible atoms with the expected fallback, the rating
slots bots rate unacceptable, invalid and timed-out designer slots),
`check_log_completeness(run_dir, *, plan=None) -> CompletenessReport`.

## Generation audit reports (#24)

Module `av_generation.audit`; guide [`generation/docs/audit.md`](../../generation/docs/audit.md).

- `build_audit(run_dir, out_dir, *, machines=None) -> AuditResult(files, ok, problems,
  notes)`:
  reads only the run manifest, `config.json` and the slot, refusal, rating, decision,
  commit, fallback-scan and timing logs (`read_run_logs`), and writes `unmasked/` and
  `masked/` together: `books.csv` (`BOOK_COLUMNS` / `MASKED_BOOK_COLUMNS`, one row per
  book, sorted by book ID), `book-<book_id>-atoms.csv` (`ATOM_COLUMNS`),
  `book-<book_id>-slots.csv` (`SLOT_COLUMNS`, the trace table: every count is a sum over
  slot rows), `summary.json` (`audit-summary.schema.json`: books, timing, `checks` with
  `problems` and `notes`, `machines`, `sources`) and `summary.md`. Same logs, same
  bytes. Masked problem texts are method-neutral; masked texts pass
  `masking.masking_findings` or nothing is written (`AuditError` `E_MASKING`);
  restricted runs' reports are refused inside git work trees (`E_POLICY`).
- Timing contract with #20: an atom, round or appointment open when a new process logs
  `resume` (new run clock; `wall_utc - t_ms` identifies the process) is interrupted, not
  a problem: the part before the restart counts up to the last event logged before it,
  and the part after it when the start is logged again (or the end follows). It is
  listed in `checks.notes`.
- `build_set_audit(run_dirs, out_dir, *, study, set_name, masked) ->
  SetAuditResult(path, sha256, runs, excluded_runs, summary_path, summary_sha256)`:
  `{study}-{set}-audit.csv` (masked; read by #34 at
  `inputs/generation/{study}-{set}-audit.csv`) or `{study}-{set}-audit-unmasked.csv`
  (restricted; proposed `keys/generation/{study}-{set}-audit-unmasked.csv`), one row per
  book sorted by batch then book, plus the cross-batch Markdown summary
  (`SET_SUMMARY_NAME` / `SET_SUMMARY_UNMASKED_NAME`, per method when unmasked) for the
  pilot review (O6.2.1). Complete runs only (closed manifest, no `batch_incomplete`),
  exactly one per batch, else `AuditError` `E_SET`.
- `tally_sheet(run_dir, path) -> bool`: independent raw-JSONL counts next to the audit's
  counts with an empty `hand_count` column (hand-tally check).
- Command line: `python -m av_generation.audit {batch,set,tally}`; every refusal exits 2
  with `error (<code>)` (`AuditError` codes, `E_POLICY`, `E_IO`).
- #34 reads blinded: `failed_generation`, `nonfallback`, `atoms_*`, `slots_valid`,
  `slots_invalid`, `wall_ms`, `rater_ms`, diversity, `total_ms_*` and the message
  durations; after unmasking, the outcome counts (with `n_llm_server_error`) and the
  effort columns (`startup_ms`, `operator_ms`, `design_active_ms`,
  `familiarization_ms`, `model_runtime_ms`, `tokens_in`, `tokens_out`).
- DEMO summary: `generation/runs/DEMO-AUDIT-01/` (synthetic logs from
  `av_generation._audit_synth`, rebuilt and compared in CI).

## G4 freeze (#25)

*Pending (#25).* Skeleton: `freeze-manifest.schema.json`, `freeze.REQUIRED_ITEM_KEYS`
(including `config.frozen_sha256`, `meanings.sha256`, `llm.manifest_sha256`),
`freeze.APPARATUS_FIELDS`, `build_freeze_manifest`, `freeze_differences` (CI guard).
Confirmatory runs check the frozen config hash with `genconfig.check_run_config`.

## Study B bank builder (#26)

*Pending (#26).* Manifest format fixed by the skeleton: `bank-manifest.schema.json`
(`av-banks/bank-manifest` v1: `bank_id` `bank-P<seq>`/`bank-C<seq>`/`DEMO-` tied to
`set`, `seed_namespace`, `generation_config_sha256`, `permutation_sha256`, cells,
attempts), `bank_manifest.bank_sha256(manifest)` (SHA-256 of the canonical compact JSON;
the manifest is immutable), the amendment log `amendments.jsonl`
(`bank-amendment.schema.json`, chained by `prev_sha256`) and `effective_menu(manifest,
amendments)` for #70 and #13. Builder, `verify` and `amend` live in `banks/` (`av_banks`).

## Pilot banks (#27)

*Pending (#27).* Register format and run notes. Bank IDs `bank-P001`..; the register's
config hash is the bank manifest's `generation_config_sha256`.

## Confirmatory banks (#28)

*Pending (#28).* Register format, freeze check (`genconfig.check_run_config` with the G4
manifest) and run notes. Bank IDs `bank-C001`..`bank-C072`.
