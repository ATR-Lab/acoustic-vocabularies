# Generation interface (`av_generation`)

Producer: Study A generation system (#16-#25) and the Study B bank contracts (#26-#28).
Consumers: the generation issues themselves, the package builder (#13: committed books),
the analysis pipeline (#34: audit tables), Unity (#70: bank manifests) and the freeze
(#25). Architecture, module owners, data flow and policies:
[`generation/docs/architecture.md`](../../generation/docs/architecture.md).

The package is used from this repository as an editable or path dependency
(`av-generation @ {root}/generation`, which depends on `av-sound @ {root}/sound`). Import
submodules directly; there are no package-level re-exports. Nothing needs the network at
runtime.

## Shared contracts (skeleton)

| Module | Public API |
| --- | --- |
| `seeds` | `derive_seed(*parts) -> int` (first 8 bytes of SHA-256 of the `\|`-joined key, unsigned big-endian); `seed_from_key(key)`; `a1_seed_key`, `a2_seed_key`, `a3_seed_key(batch_ns, atom, round, slot)`; `b_seed_key(bank, attempt, profile, atom, slot)`; `panel_seed_key`, `bot_seed_key`, `threshold_seed_key`; `parse_seed_key`; `wire_seed(seed)` / `unwire_seed`; `rng_for(key) -> numpy Generator(PCG64)`; `seeds_digest(keys)` |
| `outcomes` | `SlotOutcome` (14 codes, `OUTCOME_CODES`), `LlmStatus`; `outcome_from_validator_codes(codes, *, mode="A"\|"B", cell_duplicate=False)`, `outcome_from_validation(result, ...)`, `outcome_from_llm_status(status)`; `VALIDATOR_PRECEDENCE`, `VALIDATOR_CODE_TO_OUTCOME` |
| `records` | `SlotRecord`, `SlotRefusal`, `LlmRequest`, `RatingRecord`, `DecisionRecord`, `CommitRecord`, `FallbackScanRecord`, `PlayEvent`, `TimingEvent`, `ThresholdTrial` (JSONL); `RunManifest`, `ThresholdStimulusSet` (documents); `.to_dict()`, `.from_dict()`, `.check()`, `.sha256()`; `RecordWriter(path)`, `read_records(path, cls)`, `record_from_dict`, `cap_key` |
| `domain` | `COORDINATES` (12 `Coordinate`s in protocol order: name, field, index, values, kind, feature), `FIELD_VALUES`, `DOMAIN_SIZE`, `recipe_values`, `values_to_recipe`, `replace_values`, `differing_coordinates` |
| `ids` | `Study`, `Method`, `RunKind`; `proposal_slot_id`, `bank_slot_id`, `rating_slot_id` and their parsers; `slot_index`, `round_and_slot`, `check_id`, `check_book` |
| `config` | `BatchConfig` (`batch-config.schema.json`; `.check_consistency()`, `.method_of()`, `.book_of()`) |
| `proposers` | `RoundProposer` protocol (`propose_round(RoundRequest) -> RoundResult`), `BookState`, `CommittedAtom`, `AtomFeedback`, `CandidateFeedback`, `RaterScore`, `BCellState`, `RetainedOption` |
| `rater_protocol` | `PROTOCOL_VERSION`, `WS_PATH`, `ASSET_PATH`, `STATION_PAGE`, message types, `parse_message`, `message_errors` (`rater-message.schema.json`) |
| `rundir` | `create_run_dir`, `run_layout`, `RunLayout`, `LOG_FILES`, `check_run_id`, `check_run_location` |
| `masking` | `masking_findings(text)`, `METHOD_REVEALING_FIELDS`, `drop_method_fields` |
| `clock` | `Clock` protocol (`now_ms`, `sleep`, `asleep`, `utc_now`), `SystemClock`, `ScaledClock`, `ManualClock`, `utc_text` |
| `constants` | budgets, timings, `FROZEN_DECODING`, `PANEL_ORDERS`, B caps, `MESSAGE_MIN_MS`/`MESSAGE_MAX_MS` |
| `netguard`, `webserve`, `llm_fake` | `deny_outbound()`, `serve_in_thread(app)`, `ScriptedLlmClient` |

Schemas: `generation/schema/*.schema.json` (Draft 2020-12, `additionalProperties: false`,
`$id` `https://github.com/ATR-Lab/acoustic-vocabularies/blob/main/generation/schema/<name>`;
recipes and fallback scans refer to the sound schemas by `$id`).

## LLM server and client (#16)

*Pending (#16).* Contract fixed by the skeleton (`av_generation.llm`):
`LlmClient.propose(messages, schema, seed_key) -> RawOutcome{status, text, latency_ms,
tokens_in, tokens_out, seed, finish_reason}` with `status` in `ok`, `timeout`,
`overflow_output`, `server_error`; at most one call, no retry; one `LlmRequest` record per
call; `count_prompt_tokens(messages)`. To fill: server launcher and pinned config, LLM
manifest (`generation/llm/manifest.json`), mock server, benchmark CSV format.

## Prompt builder, parser and slot ledger (#17)

*Pending (#17).* Contract fixed by the skeleton: `SlotLedger(path, *, run_id, clock,
refusals, cap=12).consume(SlotRecord)`, `.used(cap_key)`, `.remaining(cap_key)`,
`.records(cap_key=None)`; `SlotCapExceeded`, `SlotReused`, `AttemptCapExceeded`;
`build_a3_prompt(book_state, atom_id, round, slot, *, semantic_label, feedback, same_round,
prompt_set) -> BuiltPrompt`; `build_b_prompt(cell, *, prompt_set)`; `parse_output(text)`;
`A3Proposer`. To fill: prompt-set format and hash file, B instruction decision.

## A2 mutation search (#18)

*Pending (#18).* Skeleton: `A2Proposer(ledger, *, clock).propose_round(request)`,
`sample_uniform(rng)`, `mutate_pitch(value, step) -> A2Mutation`, `mutate_index(coordinate,
value, direction)`, `mutate(parent, k, rng)`; slot records carry `A2Detail`.

## A1 hand-designer interface (#19)

*Pending (#19).* Skeleton: `A1SlotService(ledger, plays, timing, *, clock, designer_id,
practice=False)` (a `RoundProposer`), `create_a1_app(service)`, route table `a1.ROUTES`. To
fill: request/response bodies, practice-mode storage, kiosk setup.

## Separation-threshold listening tool (#23)

*Pending (#23).* Skeleton: `generate_stimuli(set_id, config=DEFAULT_CONFIG) ->
ThresholdStimulusSet`, `summarize(trials)`, `export_csv(trials, stimuli, path)`,
`TRIAL_CSV_COLUMNS`, `SUMMARY_CSV_COLUMNS`; records `ThresholdStimulusSet`,
`ThresholdTrial`, `PlayEvent` (`threshold_first`/`threshold_second`). To fill: runner
routes, operator guide link.

## Round orchestrator, selector and panel session (#20)

*Pending (#20).* Skeleton: `Orchestrator(config, layout, proposers, store, fallback, *,
clock)` with `run_atom`, `run_appointment`, `resume`, `panel_host()`; `PanelSessionHost`
protocol; `RatingSlotPlan`, `AssetRef`; `create_panel_app(host)`;
`panel_order_schedule(set_ns, panel_ids)`; `selector.score_candidate`,
`selector.pick_incumbent`. Logs: `decision`, `commit`, `fallback_scan`, `timing`.

## Rater panel client (#21)

*Pending (#21).* Protocol fixed by the skeleton (`av_generation.rater_protocol`,
`rater-message.schema.json`): station messages `hello`, `sync_request`, `asset_ready`,
`played`, `rating`, `withdraw`; server messages `welcome`, `sync_reply`, `preload`, `slot`,
`rating_ack`, `pause`, `resume`, `end`, `error`. Rating record contract to #20:
`RatingRecord{slot_id, rating_slot_id, rater_id, station, association, distinguishability,
comfort, rt_ms, ...}`. Skeleton: `BotRater`, `BotRatingPolicy`.

## Synthetic-panel dry run (#22)

*Pending (#22).* Skeleton: `check_log_completeness(run_dir) -> CompletenessReport`.

## Generation audit reports (#24)

*Pending (#24).* Contract with #34 fixed by the skeleton: `audit/unmasked/books.csv`
with `audit.BOOK_COLUMNS` (one row per book; booleans `0`/`1`), the masked copy with
`audit.MASKED_BOOK_COLUMNS`, and `summary.json` (`audit-summary.schema.json`). #34 reads
`failed_generation`, `nonfallback`, `atoms_bank_fallback`, `atoms_book_fallback`, the
outcome counts and the effort columns.

## G4 freeze (#25)

*Pending (#25).* Skeleton: `freeze-manifest.schema.json`, `freeze.REQUIRED_ITEM_KEYS`,
`freeze.APPARATUS_FIELDS`, `build_freeze_manifest`, `freeze_differences` (CI guard).

## Study B bank builder (#26)

*Pending (#26).* Manifest format fixed by the skeleton: `bank-manifest.schema.json`
(`av-banks/bank-manifest` v1), `bank_manifest.bank_sha256(manifest)` (SHA-256 of the
canonical compact JSON). Builder, `verify` and `amend` live in `banks/` (`av_banks`).

## Pilot banks (#27)

*Pending (#27).* Register format and run notes.

## Confirmatory banks (#28)

*Pending (#28).* Register format, freeze check and run notes.
