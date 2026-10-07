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

Producer: `banks/` (`av-banks`, import `av_banks`; design and usage in
[`banks/docs/bank-builder.md`](../../banks/docs/bank-builder.md)). Consumers: Unity
menus and yoked replay (#70: manifest, WAVs, amendments), dyad package builder (#13),
pilot and confirmatory bank runs (#27, #28: CLI, `verify`, bank hash).

**Inputs.** A bank ID from the dyad-slot sequence (#31; `bank-P001`.., `bank-C001`..`bank-C064`
main, `bank-C065`..`bank-C072` spares, or `DEMO-`), bound to its unit by a fixed rule
(`av_banks.permutation.expected_unit_id`: `bank-C012` -> `B-C12`, `bank-C066` -> `B-S02`)
and read through the unit's package-safe `permutation.json` (`labels`, `atom_order` =
traversal order; never `<set>-dyads.json`); the generation config (threshold, pins, budgets;
confirmatory banks also the `frozen` G4 manifest); the B prompt set, meaning set and
decoding schema (hashes equal to the config's); the #16 client.

**Build** (`banks build`, `av_banks.run.build_banks`, `builder.BankBuilder`). Attempts
1..4; per profile P1..P3 and atom in the stored order, slots 1..12 of the cell: ledger
`reserve` (cap key `B|<bank>|<attempt>|<profile>|<atom>`; a 13th slot and a 577th slot
raise `ledger.SlotCapExceeded` before any work), one proposal from a `BCellState`
(label, all options retained so far under the profile, the cell's slot history; no
rating, participant or test field) with seed key `seeds.b_seed_key(seed_namespace,
attempt, profile, atom, slot)`, `av_sound.validate` against the other atoms' retained
options at the config threshold, outcome with `mode="B"`, one `SlotRecord`. The first 4
`valid` slots are the options (ranks 1-3 shown, 4 reserve). A cell with 12 slots and
fewer than 4 options fails the attempt (kept on disk); the first complete attempt is the
bank; 4 failed attempts give `status: unavailable` (no cells; never assigned); a 5th
attempt raises `ledger.AttemptCapExceeded` (logged). `workers` 1-3 runs profiles as
parallel streams, `parallel_banks` several banks per run. Single atoms only: the builder
never composes a message.

**Files** (`rundir.RunLayout.bank_dir(bank_id)` = `<run>/banks/<bank_id>/`): `manifest.json`
(`bank-manifest.schema.json`; derived from the stored files with
`av_banks.manifest.manifest_from_files`), `bank-sha256.txt`, `generation-config.json`,
`permutation.json` (byte copy; `permutation_sha256` = SHA-256 of the bytes),
`timing.jsonl`, `attempts/<n>/slots.jsonl` (`slot-record`), `attempts/<n>/slot-refusals.jsonl`,
`attempts/<n>/attempt.json` ([`banks/schema/bank-attempt.schema.json`](../../banks/schema/bank-attempt.schema.json):
status, reason, failed cell, timing, throughput), `options/P<k>/<atom>-<rank>.wav`
(attempt used only), `amendments.jsonl`. The run directory holds `run-manifest.json`
(`purpose: "bank"`, study B, `bank_ids`, config and meanings hashes, `files`) and
`logs/llm-requests.jsonl`. Option IDs: `<bank_id>.<profile>.<atom>.<rank>`; manifest cells
in the order P1, P2, P3 x stored atom order.

**Bank hash.** `bank_manifest.bank_sha256(manifest)` (SHA-256 of the canonical compact
JSON); recomputing the manifest from the stored files gives the same hash (`banks verify`,
`banks hash`). Amendments never change it.

**Verify** (`banks verify`, `av_banks.verify.verify_bank -> VerifyReport`): schema, manifest
= stored files, bank hash, config pins, a seed namespace that names the bank and version
(`<bank_id>` for 1.0.0, else `<bank_id>-v<version>[-<suffix>]`), every attempt log (hash,
counts, slot IDs, seed keys, caps, traversal order, retention), provenance of every option
(a `valid` record of the attempt used), re-render and WAV hashes, technical validity,
distinct waveforms per cell, all 1,920 different-atom pairs per profile, amendment chain.

**Amend** (`banks amend ... --unheard-confirmed`, `av_banks.amend.amend_bank`): the reserve
replaces a shown option (rank 1-3) after the operator confirms it is unheard and
uncommitted and the wave's menu has not been heard; verifies the bank, rechecks the reserve
(re-render, hashes, validity, 60 pairs against the other atoms' options) and appends a
chained `bank_amendment` line; one per cell. Consumers use `effective_menu(manifest,
amendments)` after `amendment_chain_errors(...) == ()`.

**Package builder (#13).** `av_banks.manifest.to_dyad_bank(read_manifest(bank_dir))` ->
`av_sound.dyad_bank.DyadBank` (option `source` = slot ID); amendments are not applied by
the conversion.

**Pending.** Example bank with the real model on the LLM host (hardware); manifest review
by the #70 owner (human).

## Pilot banks (#27)

*Pending (#27).* Register format and run notes. Bank IDs `bank-P001`..; the register's
config hash is the bank manifest's `generation_config_sha256`.

## Confirmatory banks (#28)

Producer: `banks/` subpackage `av_banks.confirmatory` (design and runbook in
[`banks/docs/confirmatory-banks.md`](../../banks/docs/confirmatory-banks.md)); command
line `python -m av_banks.confirmatory`. Consumers: G5B owner (register, report), the
allocation reveal (#31: unavailable bank IDs), #70 and #13 (bank directories, unchanged
#26 format). The real run is **pending** (G4 freeze #25, LLM host).

**Banks.** 72 bank IDs in dyad-slot sequence: `bank-C001`..`bank-C064` (main, units
`B-C01`..`B-C64`), `bank-C065`..`bank-C072` (spares, `B-S01`..`B-S08`); each built by
the #26 builder as its own run `<campaign>-<bank>` under the campaign directory
(restricted storage). Seed namespace = bank ID (`<bank>-v<version>` for a rebuild after a
crash). `DEMO-C001`..`DEMO-C072` in rehearsals.

**Freeze check.** `plan` and `run` refuse unless the freeze manifest matches
`freeze-manifest.schema.json`, `genconfig.check_run_config(config, kind="confirmatory",
freeze_manifest=...)` passes (frozen status, `config.frozen_sha256` = config hash, code
pins) and, with `--repo`, the manifest's `tag` points at its `repo_commit`.

**Seeds.** `seed_check.check_seeds`: namespaces name their bank and version, are unique,
and the 165,888 keys of the budget (72 x 4 x 3 x 16 x 12) give distinct seeds, none in
the key space of the pilot namespaces (read from pilot bank, run or campaign directories,
or a CSV with a `seed_namespace` column). `check_used_seeds` rechecks every slot record
after the run.

**Register** (public; commit before the allocation list is unsealed):
`register.csv` with `register.REGISTER_COLUMNS` (`sequence, bank_id, role, dyad_slot,
bank_version, status, assignable, generation_config_sha256, config_matches_freeze,
attempts, attempt_used, slots_attempt_used, max_slots_per_attempt, slots_total,
crashed_versions, crashed_slots, verify, verify_report_sha256, bank_sha256`) and
`register.json` ([`banks/schema/confirmatory-register.schema.json`](../../banks/schema/confirmatory-register.schema.json):
counts, checks, `decision` in `ready`, `escalation_required`, `escalated`, `blocked`,
escalation link and date, `unavailable_bank_ids`, hashes of the plan, seed checks,
verification and timing logs, the register CSV and the deterministic archive). No seeds,
recipes, WAVs or allocation information.

**Rules.** At least 64 complete banks, else stop and escalate to the advisor before G5B
(`escalate --reference <issue/PR link> --date`). Unavailable banks are never assignable;
the coordinator logs each with `RevealLog.log_bank_unavailable` before the first reveal,
and the reveal replaces an unavailable main slot by the first unused spare with the same
SQ arm and swap flag. `commit-check` confirms the committed register equals the campaign's,
was never changed, and its commit time precedes the first confirmatory screening.

**Restricted files** (campaign directory): `plan.json`
([`confirmatory-plan.schema.json`](../../banks/schema/confirmatory-plan.schema.json)),
freeze/config/unit copies, `seed-check.json`, `used-seeds.json`, `runs/`, `verify/`,
`verification-log.txt`, `timing.csv` (per bank: wall time, slots/min, model latency and
slot time p50/p95/max, slots over the 40-s cap, failed model calls), `slot-timing.csv`,
`events.jsonl`, `progress.jsonl`, `rebuilds.jsonl`, `g5b-report.md`,
`archive/<campaign>-banks.tar`.
