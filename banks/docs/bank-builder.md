# Study B bank builder

Package `av-banks` (import `av_banks`), issue #26 (WBS O4.3.1). It builds, verifies and
amends the candidate bank of one Study B dyad: 16 atoms x 3 profiles, each cell with 4
retained options (3 shown, 1 reserve), following Study B protocol section 4. It is the only
source of the candidates Study B participants choose from (#70 menus, #13 dyad packages).
Pilot (#27) and confirmatory (#28) banks are runs of this builder.

Shared contracts it uses (generation stack, [`generation.md`](../../docs/interfaces/generation.md)):
the bank manifest and amendment formats (`av_generation.bank_manifest`,
`generation/schema/bank-manifest.schema.json`, `bank-amendment.schema.json`), the slot
ledger (#17), the B-mode prompt builder and parser (#17), the LLM client (#16), seeds,
slot outcomes, records and the generation config. The sound engine
([`sound-engine.md`](../../docs/interfaces/sound-engine.md)) renders and validates
every candidate.

## 1. Inputs

| Input | Source | Used for |
| --- | --- | --- |
| Bank ID | dyad-slot sequence (schedules allocation, #31): `bank-P001`.., `bank-C001`..`bank-C064` (main), `bank-C065`..`bank-C072` (spares), or `DEMO-...` | names, slot IDs, cap keys, the default seed namespace |
| `permutation.json` of the unit | schedules (#29), package-safe (`av-schedules/permutation` v2) | `labels` (atom -> semantic label), `atom_order` (the traversal order), `unit_id` (the manifest's `dyad_slot`) |
| `generation-config.json` | generation config (`genconfig`) | separation threshold, code pins, budgets; its hash is the bank's `generation_config_sha256` |
| Meaning set, B prompt set, decoding schema | #17, `meanings` | the prompt; their hashes must equal the config's |
| LLM endpoint | #16 (pinned vLLM server on the LLM host) | one call per slot |
| Freeze manifest | #25 (G4) | confirmatory banks only: its `config.frozen_sha256` must equal the config hash |

The builder never reads `<set>-dyads.json` or any other allocation list. The bank ID is
bound to its unit by the fixed sequence rule (`permutation.expected_unit_id`): `bank-P007`
is unit `B-P07`, `bank-C012` is `B-C12`, `bank-C066` is the spare `B-S02`. A pilot or
confirmatory bank refuses a DEMO unit, a unit of the other set or another dyad slot's
unit; a DEMO bank refuses a non-DEMO unit.

## 2. What one build does

Before slot 1: `genconfig.check_run_config` (running code equals the config; demo
configs only for DEMO banks; confirmatory banks need the `frozen` G4 manifest with the
same config hash), the proposer's input hashes (B prompt set, meaning set, decoding
schema) against the config, and the bank directory must not exist.

Attempts 1..4, each independently seeded. Within an attempt, for each profile P1, P2, P3
and each atom in `atom_order`, slots 1..12 of the cell (`builder.AttemptRun.run_slot`):

1. **Reserve** the slot in the attempt's ledger (cap key `B|<bank>|<attempt>|<profile>|<atom>`,
   slot ID `<bank>.t<attempt>.<profile>.<atom>.s<NN>`). The builder itself refuses a 13th
   slot of a cell and a 577th slot of an attempt (`SlotCapExceeded`, logged as a
   `slot_refusal`) before any work, whatever the ledger does.
2. **Propose** (`proposer.LlmSlotProposer`): build the B prompt from a `BCellState`; count
   prompt tokens on the server (`overflow_input` above 16,384 tokens and `invalid_json`
   with `llm_status="server_error"` on a failed count, both without a call); one model
   call with seed key `B|<seed_namespace>|<attempt>|<profile>|<atom>|<slot>`; map the
   model status (`timeout` at the 40-s slot cap, `overflow_output`, `server_error` ->
   `invalid_json`); strict parse (anything but one JSON object is `invalid_json`).
3. **Validate** with `av_sound.validate` against the options already retained for the
   *other* atoms under the profile, at the config threshold: technical codes
   (schema, domain, short event, render, clipping, reserved signal) first; then a
   waveform equal to an option already retained in the same cell (`duplicate`); then an
   identical waveform or a distance below the threshold to another atom's option
   (`incompatible`). `outcomes.outcome_from_validation(..., mode="B")` decides.
4. **Consume** the slot with one `SlotRecord` (seed, prompt and schema hashes, tokens,
   latency, raw output, recipe, validator codes, waveform and WAV hashes).

A `valid` slot becomes the cell's next option (rank 1, 2, 3, then 4 = reserve); the cell
stops at 4. Every other outcome just uses the slot: no repair, no retry, no extra call.
A cell that ends 12 slots with fewer than 4 options fails the attempt. The attempt's
files stay on disk and the next attempt starts empty (its slot IDs, seeds and options
are its own). The first complete attempt is the bank; after 4 failed attempts the bank
is `unavailable` (its manifest has no cells and it must not be assigned). A 5th attempt
raises `AttemptCapExceeded` and is logged. Budget: 12 slots per cell, 576 per attempt,
2,304 per bank.

The builder renders single atoms only (inside `validate`); it never composes or renders a
message, and it never reads ratings, participant data or test data.

### What the proposer sees

`BCellState`: bank ID, attempt, profile, atom, slot, the atom's semantic label (the
prompt builder adds its meaning text from the shared meaning set), every option retained
so far under the profile in this attempt (all atoms, with recipes, waveform hashes and
slot IDs: the full retained-prefix constraints) and this cell's earlier slot records (the
validation history). Nothing else: the type has no rating, participant or test field, and
the sentinel test (`tests/banks/test_bank_inputs.py`) checks that values planted next
to the unit and in the environment never reach a prompt, a cell state or a bank file.

### Parallel execution and throughput

Profiles are independent (compatibility is checked within a profile), so `workers=3`
runs the three profile streams in threads; `build_banks(..., parallel_banks=N)` builds N
banks at once in one run (or run several processes, one run each). A complete attempt is
the same whatever the interleaving. When a profile fails, the other streams stop before
their next slot (`stopped` in `attempt.json`), so the slot count of a *failed* attempt can
depend on timing when `workers > 1`; with `workers=1` the whole build is deterministic.
Each `attempt.json` records slots per minute, model latency and open-to-close slot time
(p50, p95, max) and the number of slots over the 40-s cap; `timing.jsonl` holds
`bank_start/end`, `attempt_start/end` (with the throughput line) and `cell_start/end`.
Parallel streams are fine as long as the slot time stays under the cap.

## 3. Files

A build is a run directory (`av_banks.run.build_banks`, `rundir.create_run_dir`:
pilot and confirmatory runs are refused inside a git work tree) with one directory per
bank (`rundir.RunLayout.bank_dir`):

```
<runs_root>/<run_id>/
  run-manifest.json            purpose "bank", study B, bank IDs, config and meanings hashes, files
  generation-config.json
  logs/timing.jsonl            run_start, run_end
  logs/llm-requests.jsonl      every model call (#16 client)
  banks/<bank_id>/
    manifest.json              bank-manifest.schema.json (immutable)
    bank-sha256.txt            the bank hash
    generation-config.json     the config the bank was built under
    permutation.json           byte copy of the unit's permutation.json
    timing.jsonl               bank, attempt and cell timing
    slot-refusals.jsonl        bank-level refusals (5th attempt), when any
    amendments.jsonl           reserve-rule amendments, when any
    attempts/<n>/slots.jsonl          the attempt's ledger: one record per slot
    attempts/<n>/slot-refusals.jsonl  refused slots, when any
    attempts/<n>/attempt.json         status, reason, failed cell(s), timing, throughput
    options/P<k>/<atom>-<rank>.wav    the 192 options of the attempt used
```

`attempt.json` follows [`banks/schema/bank-attempt.schema.json`](../schema/bank-attempt.schema.json)
(`av-banks/bank-attempt` v1).

### Manifest and bank hash

The manifest is derived from the stored files (`manifest.manifest_from_files`), at build
time and again in `verify`:

- `bank_id`, `bank_version`, `seed_namespace` from the attempt summaries; `set` and
  `demo` from the bank ID; `dyad_slot`, `labels`, `atom_order`, `permutation_sha256` (SHA-256
  of the file bytes) from `permutation.json`; `generation_config_sha256` from
  `generation-config.json`;
- `attempts[]`: status, failed cell and wall time from `attempt.json`, `slots_used` and
  `slots_sha256` (file hash) from `slots.jsonl`;
- `cells[]` (complete banks): profiles P1, P2, P3, atoms in the stored order; per cell the
  first 4 `valid` records of the attempt used, as options with `option_id`
  `<bank>.<profile>.<atom>.<rank>`, recipe, `recipe_sha256`, `pcm_sha256`, the WAV path and
  the WAV file's `file_sha256`, and the `slot_id` (provenance).

The bank hash is `bank_manifest.bank_sha256(manifest)`: SHA-256 of the canonical compact
JSON of the whole manifest. Recomputing the manifest from the files gives the same hash.
A build is not reproducible byte for byte (wall times, run-clock times), but the
manifest of stored files is: `verify` recomputes it.

`bank_version` defaults to `1.0.0` and the seed namespace to the bank ID; a rebuild
under a new version gets the namespace `<bank_id>-v<version>` (or an explicit
`--seed-namespace`), so its seeds never repeat an earlier build's. A crashed build is not
resumed: build again under a new version.

## 4. `verify`

`verify_bank(bank_dir)` (CLI `banks verify`) reports every problem it finds:

- schema; manifest equal to the manifest derived from the files; bank hash equal to the
  recomputed hash and to `bank-sha256.txt`;
- the config is the one the manifest names and the running renderer and validator are
  the ones it pins;
- every attempt's log: file hash and count, slot IDs and seed keys, at most 12 slots per
  cell and 576 per attempt, cells in the stored order, retention stopped at the 4th
  option, a failed attempt's cell used all 12 slots;
- provenance: every option comes from a `valid` record of the attempt used, with the same
  recipe and hashes;
- every option re-renders to its waveform hash, its canonical WAV equals the stored file,
  and it is technically valid;
- options of a cell have distinct waveforms;
- under each profile, all 1,920 pairs of options of different atoms (64 options, 120 atom
  pairs x 16 option pairs) have different waveforms and are at least the threshold apart
  (exact arithmetic);
- the amendment chain and its recheck threshold.

## 5. `amend` (reserve rule)

`amend_bank(bank_dir, profile=, atom_id=, rank=1..3, reason=, unheard_confirmed=True)`
(CLI `banks amend ... --unheard-confirmed`) replaces a shown option with the cell's
reserve. Whether the option is unheard and uncommitted and the wave's menu not yet heard
by either partner is only known to the session (#70), so the operator confirms it; the
command refuses without the confirmation. It verifies the whole bank, rechecks the
reserve (re-render, WAV hash, technical validity, compatibility with all 60 options of the
other 15 atoms under the profile) and appends one `bank_amendment` record to
`amendments.jsonl`, chained by `prev_sha256` (the bank hash for the first line). One
amendment per cell. The manifest and the bank hash never change; consumers apply the log
with `bank_manifest.effective_menu(manifest, amendments)`.

## 6. Command line

```bash
# from the repository root; real banks go to restricted storage, never into git
uv run --project banks banks build \
  --bank-id bank-P001 --permutation <restricted>/B-P01/permutation.json \
  --runs-root <restricted>/bank-runs --run-id P-banks-01 \
  --generation-config <restricted>/generation-config.json \
  --meanings <restricted>/meanings --prompts <restricted>/prompts-b \
  --decoding-schema <restricted>/decoding-schema.json \
  --llm-url http://<llm-host>:8000 --workers 3
uv run --project banks banks verify <restricted>/bank-runs/P-banks-01/banks/bank-P001
uv run --project banks banks amend <bank dir> --profile P2 --atom K-a3 --rank 2 \
  --reason "cached asset unusable" --unheard-confirmed
uv run --project banks banks hash <bank dir>
```

Repeat `--bank-id`/`--permutation` pairs to build several banks in one run
(`--parallel-banks N`). Confirmatory builds add `--freeze-manifest`. `build` prints a JSON
summary and exits 0 (all complete), 3 (a bank is unavailable) or 2 (refused). `verify`
exits 0 or 1.

## 7. Python API

| Module | API |
| --- | --- |
| `builder` | `bank_spec(bank_id, permutation, *, bank_version="1.0.0", seed_namespace=None) -> BankSpec`; `BankBuilder(spec, bank_dir, *, config, proposer, clock, run_id, kind=None, freeze_manifest=None, ledger_factory=slot_ledger, workers=1, reserved=None, fsync=True)` with `.check()`, `.open()`, `.run_attempt(n) -> AttemptSummary`, `.build() -> BuildResult`; `AttemptRun(builder, attempt).run_slot(profile, atom) -> SlotRecord`; `default_seed_namespace`; `BankBuildError` (`E_EXISTS`, `E_ORDER`, `E_SPEC`, `E_INTERNAL`); `LedgerLike`, `LedgerFactory` |
| `proposer` | `SlotProposer` protocol (`check_config(config)`, `propose(cell, *, seed_key, slot_id) -> Proposal`); `LlmSlotProposer(client, prompt_set, decoding_schema, *, prompt_builder=build_b_prompt, parser=parse_output)`; `check_prompt_inputs`; `Proposal`; `ProposerConfigError` |
| `run` | `build_banks(specs, *, runs_root, run_id, config, proposer, clock, kind=None, freeze_manifest=None, freeze_manifest_sha256=None, llm_runtime=None, workers=3, parallel_banks=1, ledger_factory=slot_ledger) -> RunResult` |
| `manifest` | `BankManifest` (typed `bank-manifest`; `.read`, `.write`, `.bank_sha256()`, `.cell()`, `.menu(amendments)`), `read_manifest`, `read_amendments`, `manifest_from_files`, `AttemptSummary` (`attempt.json`), `to_dyad_bank(manifest) -> av_sound.dyad_bank.DyadBank` |
| `verify` | `verify_bank(bank_dir) -> VerifyReport` (`ok`, `problems`, `bank_sha256`, `pairs_checked`, ...); `check_pairs`, `check_against`; `PAIRS_PER_PROFILE = 1920` |
| `amend` | `amend_bank(bank_dir, *, profile, atom_id, rank, reason, unheard_confirmed, date=None) -> AmendResult`; `AmendError` (`E_HEARD`, `E_BANK`, `E_CELL`, `E_RECHECK`, `E_INPUT`) |
| `permutation` | `load_permutation(path) -> UnitPermutation`, `parse_permutation`, `expected_unit_id(bank_id)`, `check_bank_unit(bank_id, permutation)` |
| `layout`, `throughput` | `BankLayout`, `option_wav`, `option_id`; `throughput(records, wall_ms)` |

For #13: `to_dyad_bank(read_manifest(bank_dir))` gives the `DyadBank` the dyad package
builder takes (option `source` = slot ID). Amendments are applied by consumers with
`effective_menu`, not by the conversion.

## 8. Tests

`tests/banks/` (run: `uv run --project banks pytest --import-mode=importlib tests/banks`;
CI `.github/workflows/banks.yml`, Linux, macOS, Windows). #16 and #17 are built in
parallel, so the tests use stand-ins with their contracts (`conftest.py`): a memory
ledger, a prompt builder that serializes the whole cell state, a strict parser and a
scripted model (`llm_fake.ScriptedLlmClient`) whose output per slot is a pure function
of the slot and a scripted kind (valid, repeated, copied or near another atom's
proposal, malformed, schema or domain violation, short event, timeout, output and input
overflow, server error, failed token count). An independent oracle applies the
retention rule to the same script. `test_bank_real_components.py` repeats a build with
#17's real ledger, prompt builder and parser; it skips until they are filled.

## 9. Pending and open items

- **Pending (hardware):** one example bank built with the real model on the LLM host,
  with its manifest and timing log. Run the `build` command above with a pilot or DEMO
  configuration on the LLM host, then `banks verify`; record the bank hash, `attempt.json`
  throughput and `timing.jsonl` (restricted storage; hashes only in the PR or register).
- **Pending (human):** review of the manifest format with the #70 owner (Study B menus and
  yoked replay).
- The seed key uses the bank's seed namespace (`B|<bank_ns>|...`, generation architecture
  section 15) instead of #16's `B|bank_id|...`; the bank ID is the namespace of a first build.
- The separation threshold is the config's (`0.10` pilot default until G4, #25).
