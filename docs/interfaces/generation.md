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
  manager that serves the app (port 8741) and yields the page URL. Pending: #20 calls
  them for a study batch.
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

*Pending (#23).* Skeleton: `generate_stimuli(set_id, config=DEFAULT_CONFIG) ->
ThresholdStimulusSet`, `plan_session(stimuli, session_id, *, listener_id, station,
gain_db, tryout, created_utc) -> ThresholdSession`, `summarize(trials)`,
`export_csv(trials, stimuli, path)`, `TRIAL_CSV_COLUMNS`, `SUMMARY_CSV_COLUMNS`; records
`ThresholdStimulusSet`, `ThresholdSession` (`threshold-session.schema.json`),
`ThresholdTrial`, `PlayEvent` (`threshold_first`/`threshold_second`). To fill: runner
routes, operator guide link.

## Round orchestrator, selector and panel session host (#20)

*Pending (#20).* Skeleton: `Orchestrator(config, layout, proposers, store, fallback, *,
clock, generation_config, meanings, kind, freeze_manifest=None)` with `run_atom`,
`run_appointment`, `resume`, `panel_host() -> panel_session.PanelSessionHost`;
`RatingSlotPlan`; `panel_order_schedule(set_ns, panel_ids)`; `panel_aliases(set_ns,
panel_id, book_ids)`; `selector.score_candidate`, `selector.pick_incumbent`. Logs:
`decision`, `commit`, `fallback_scan`, `timing`, and as panel host `rating`, `play` and
the panel timing events. Whole-book substitution: architecture section 3.2.

## Rater panel server, stations and bot rater (#21)

*Pending (#21).* Skeleton: `panel.create_panel_app(host, *, clock)` over
`panel_session.PanelSessionHost`, station page in `panel.STATION_STATIC_DIR`;
`rater.BotRater(base_url, *, rater_id, station, run_id, policy)`,
`rater.BotRatingPolicy(p_comfort_acceptable, force_unacceptable_slots, p_missing)`
(rating-slot IDs, never book IDs). Protocol fixed by the skeleton
(`av_generation.rater_protocol`, `rater-message.schema.json`): station messages
`hello` (with `kind`), `sync_request`, `asset_ready`, `played`, `rating`, `withdraw`;
server messages `welcome`, `sync_reply`, `preload`, `slot`, `rating_ack`, `pause`,
`resume`, `end`, `error`. Rating records are written by the host (#20) at lock time.

## Synthetic-panel dry run (#22)

*Pending (#22).* Skeleton: `DryRunPlan` (`dry-run-plan.json`,
`dry-run-plan.schema.json`: zero-eligible atoms with the expected fallback, the rating
slots bots rate unacceptable, invalid and timed-out designer slots),
`check_log_completeness(run_dir, *, plan=None) -> CompletenessReport`.

## Generation audit reports (#24)

*Pending (#24).* Contract with #34 fixed by the skeleton: per run
`audit/unmasked/books.csv` with `audit.BOOK_COLUMNS` (one row per book; booleans
`0`/`1`), the masked copy with `audit.MASKED_BOOK_COLUMNS`, and `summary.json`
(`audit-summary.schema.json`); per set `build_set_audit(run_dirs, out_dir, *, study,
set_name, masked)` writing `{study}-{set}-audit.csv` (masked; the analysis pipeline
reads it at `inputs/generation/{study}-{set}-audit.csv`) or
`{study}-{set}-audit-unmasked.csv` (restricted; proposed analysis path
`keys/generation/{study}-{set}-audit-unmasked.csv`), one complete run per batch,
incomplete runs excluded and listed. The masked tables hold no outcome counts, no
startup or operator time and no per-method effort (`audit.METHOD_COLUMNS`); #34 reads
`failed_generation`, `nonfallback`, `atoms_bank_fallback`, `atoms_book_fallback`,
`slots_valid` and `slots_invalid` blinded, and the outcome counts (with
`n_llm_server_error`) and effort columns from the unmasked table after unmasking.

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
