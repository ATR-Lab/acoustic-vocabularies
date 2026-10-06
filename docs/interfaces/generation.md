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

*Pending (#16).* Contract fixed by the skeleton (`av_generation.llm`):
`LlmClient.propose(messages, schema, seed_key, *, slot_id=None) -> RawOutcome{status,
text, latency_ms, tokens_in, tokens_out, seed, finish_reason}` with `status` in `ok`,
`timeout`, `overflow_output`, `server_error`; at most one call, no retry; one
`LlmRequest` record per call with `slot_id`, `prompt_sha256 = jsonio.messages_sha256`
and `schema_sha256 = jsonio.schema_sha256`; `count_prompt_tokens(messages)` through the
server's `POST /tokenize` (`messages`, `add_generation_prompt: true`), raising
`TokenCountError` on failure. To fill: server launcher and pinned config, LLM manifest
(`generation/llm/manifest.json`, including the chat-template SHA-256), mock server with
`/v1/chat/completions` and `/tokenize`, benchmark CSV format.

## Prompt builder, parser and slot ledger (#17)

*Pending (#17).* Contract fixed by the skeleton: `SlotLedger(path, *, run_id, clock,
refusals, timing, cap=12)` with `.reserve(cap_key, slot_id, *, study, method) ->
SlotTicket` (cap and reuse checked before any work), `.consume(SlotRecord)` (needs an
open ticket), `.used(cap_key)`, `.remaining(cap_key)`, `.records(cap_key=None)`;
`SlotCapExceeded`, `SlotReused`, `SlotNotReserved`, `AttemptCapExceeded`;
`load_prompt_set(path, *, meanings)`; `build_a3_prompt(book_state, atom_id, round, slot,
*, semantic_label, feedback, same_round, prompt_set) -> BuiltPrompt`;
`build_b_prompt(cell, *, prompt_set)`; `parse_output(text)`; `A3Proposer`. To fill:
prompt-set format and hash file, B instruction decision.

## A2 mutation search (#18)

*Pending (#18).* Skeleton: `A2Proposer(ledger, *, clock).propose_round(request)` (the
request's book is label-free), `sample_uniform(rng)`, `mutate_pitch(value, step) ->
A2Mutation`, `mutate_index(coordinate, value, direction)`, `mutate(parent, k, rng)`;
slot records carry `A2Detail`.

## A1 hand-designer interface (#19)

*Pending (#19).* Skeleton: `A1SlotService(ledger, plays, timing, *, clock, designer_id,
meanings, practice=False)` (a `RoundProposer`; opening a slot reserves it),
`create_a1_app(service)`, route table `a1.ROUTES`. To fill: request/response bodies,
practice-mode storage, kiosk setup.

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

Component doc: [`generation/docs/rater-panel.md`](../../generation/docs/rater-panel.md).
Message formats: `av_generation.rater_protocol` (`rater-message.schema.json`); host
contract: `av_generation.panel_session` (implemented by #20).

| Module | Public API |
| --- | --- |
| `panel` | `create_panel_app(host, *, clock)` (FastAPI: `GET /panel/station`, `GET /panel/static/{station.js,station.css}`, `GET /panel/assets/<sha256>.wav`, WebSocket `/panel/ws`; `app.state.panel` is the `PanelServer`); `STATION_STATIC_DIR` (`web/rater/`); `rating_refusal(slot, submission) -> code \| None` (slot rules, in the #20 host's order: `E_SLOT_CLOSED` from `start + 20,000`, `E_PLACEHOLDER`, `E_LOCKED` before `start + unlock_offset_ms`, `E_FIRST_ATOM` for a distinguishability on a first-atom slot, `E_PROTOCOL` for a missing one elsewhere or a value outside 1-7); `slot_message(slot, *, rejoin=False)`, `preload_message(assets)`, `server_message(event)`, `asset_url(asset_id)`, `station_url(base_url, station, rater_id)`; `ClockSyncEstimator` (chained probe bursts); constants `SYNC_BURST` (8), `SYNC_INTERVAL_MS` (60,000), `SLOT_LEAD_MS` (500, `slot` event lead of the scripted host and #20), `ONSET_TOLERANCE_MS` (50), `MAX_SKEW_MS` (100), `MAX_LATE_START_MS` (1,000), `MAX_FRAME_BYTES` (4,096) |
| `rater` | `BotRater(base_url, *, rater_id, station, run_id, policy, clock=None, max_reconnects=20, open_timeout_s=10.0).run() -> BotRunResult`; `BotRatingPolicy(p_comfort_acceptable=0.9, force_unacceptable_slots=frozenset(), p_missing=0.0, drop_slots=frozenset(), withdraw_at=None, rt_ms_range=(400, 4000))` (rating-slot IDs, never book IDs); `bot_rating(run_id, rater_id, rating_slot_id, *, ask_distinguishability, policy) -> BotRating` (stream `rng_for(bot_seed_key(run_id, rater_id, "rating", rating_slot_id))`) |
| `panel_demo` | `ScriptedPanelHost(session, *, clock, run_dir=None)` (a `PanelSessionHost` for a fixed schedule: `begin`, `begin_when_joined`, `tick`, `start`/`stop`, `wait_ended`, `republish`, `operator`; records in `.ratings`, `.plays`, `.timing` and `run_dir/logs/`); `demo_session(*, run_id, seats, atom_index, rounds, placeholders, positions, ...) -> ScriptedSession` (DEMO batch config, meanings and rendered DEMO atoms); `schedule_document(host)` (`panel-schedule.json`); CLI `python -m av_generation.panel_demo` |
| `panel_skew` | `detect_onsets(signal, rate)`, `parse_wav(data)` / `read_capture(path)`, `align(scheduled_ms, detected_ms)`, `capture_onsets(capture, schedule, channels)`, `logged_onsets(plays)`, `onset_table(schedule, onsets)`, `summarize(rows, schedule, ...)`, `read_schedule(path)`, `schedule_from_plays(plays, run_id)`; CLI `python -m av_generation.panel_skew logged\|loopback` (`logged-onsets.csv`, `loopback-skew.csv` and their `-summary.json`) |

Rules the server enforces: every frame is schema-checked (ratings only integers 1-7 and a
binary comfort; no free text; otherwise `error E_PROTOCOL` and nothing reaches the host);
`hello` first and only for a seat of `host.seats()` (`E_UNKNOWN_RATER`); a new `hello`
replaces the station's old socket; `sync_request` is answered at once; `played` must
match the slot's asset and scheduled time; ratings are pre-checked (`E_UNKNOWN_SLOT`,
`rating_refusal`, then `E_DUPLICATE_RATING`) before `host.submit_rating`, and `rating_ack`
goes to the rating station only. A (re)joining station gets `welcome`, the pending
`preload` and the current `slot` with `rejoin=true`; stations never play a sound twice
and skip a slot joined after its candidate onset (its rating is missing). The host writes
every rating, play and panel timing record (`panel_session`; host duties in the component
doc, section 6).

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
