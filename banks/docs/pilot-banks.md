# Study B pilot banks

Issue #27 (WBS O4.3.2), module `av_banks.pilot` with `av_banks.register`,
`av_banks.archive` and `av_banks.metrics`. The pilot banks are runs of the #26 builder
([`bank-builder.md`](bank-builder.md)). This page covers the IDs, the seed namespaces,
the spare rule, the run on the LLM host, the register, the archive and the throughput
summary for the confirmatory run (#28).

The pilot has 8 dyads (Study B protocol section 2). Its banks are built before G4, so they
use the generation config current at the time: an unfrozen config with the 0.10
pilot-default separation threshold, and the current prompt and model revision. Pilot
banks are never reused for confirmatory dyads.

## 1. IDs, seed namespaces and spares

| Item | Value | Rule |
| --- | --- | --- |
| Dyad slots | `B-P01`..`B-P08` | schedules pilot set (#29, #31); the pilot has no spare slots |
| Bank IDs | `bank-P001`..`bank-P008` | dyad-slot sequence (`ids.PILOT_BANK_RE`, `permutation.expected_unit_id`) |
| Rehearsal IDs | `DEMO-bank-P001`..`DEMO-bank-P008` | DEMO units only |
| Seed namespace, first build | the bank ID (`bank-P003`) | B seed key `B\|bank-P003\|<attempt>\|<profile>\|<atom>\|<slot>` |
| Spare `n` of a slot | same bank ID, version `1.<n>.0`, namespace `bank-P003-v1.<n>.0` | `PilotPlan.spare_spec` |
| Spare budget | 2 banks for the whole pilot | `PILOT_SPARES` |
| Replacement run after a crash | every bank at version `2.0.0`, spares `2.<n>.0` | `--major 2` |

**Pilot and confirmatory seeds never meet.** A namespace must name its bank and version
(`builder.seed_namespace_error`). Every pilot namespace therefore starts with `bank-P`
and every confirmatory namespace with `bank-C`. A property test checks that no namespace
accepted for a pilot bank is accepted for a confirmatory bank.

**When a spare is built.** A spare is built for a dyad slot whose bank ended
`unavailable` (4 failed attempts) or failed `banks verify`. It is built right after the
main banks, before any pilot session. It has the same bank ID and `permutation.json`, a
new version and seed namespace, and its own 4-attempt budget. Its failed attempts are
kept too. Slots are served in order until the budget is used. A slot left without a
usable bank is the **shortfall**. The summary and the exit code (3) report it, and it
must be raised before O6.3.2.

A pair that fails compatibility screening uses no bank. Eligibility, including
compatibility, is logged before the reveal (schedules `RevealLog`), and the next eligible
pair takes the same slot and bank.

## 2. The run on the LLM host

Prerequisites (all in restricted storage, never in git):

- The pilot units `B-P01/permutation.json`..`B-P08/permutation.json`, from the schedules
  pilot set (#29, #31).
- The current `generation-config.json`. A pilot config is not DEMO and needs no freeze
  manifest. Its hash is recorded in the plan and in the register.
- The meaning set, the B prompt set (#17) and the decoding schema. Their hashes must
  equal the config's.
- The pinned LLM server (#16) on the LLM host.
- A checkout of this repository at the commit being used. Record `git rev-parse HEAD`
  in the run notes; the plan records the `av-banks` and `av-generation` versions.

```bash
# from the repository root, on the LLM host
uv sync --project banks --locked
uv run --project banks python -m av_banks.pilot plan \
  --units <restricted>/units --run-id P-banks-01        # check IDs, units, namespaces
uv run --project banks python -m av_banks.pilot run \
  --units <restricted>/units --root <restricted>/pilot-banks --run-id P-banks-01 \
  --generation-config <restricted>/generation-config.json \
  --meanings <restricted>/meanings --prompts <restricted>/prompts \
  --decoding-schema <restricted>/decoding-schema.json \
  --llm-url http://<llm-host>:8000 --workers 3 --parallel-banks 2
uv run --project banks python -m av_banks.pilot check --root <restricted>/pilot-banks --verify
uv run --project banks python -m av_banks.pilot archive --root <restricted>/pilot-banks
uv run --project banks python -m av_banks.pilot check --root <restricted>/pilot-banks
```

`run` does the following:

1. Writes `pilot-plan.json`: the bank IDs, dyad slots, seed namespaces and permutation
   hashes; the spare budget; the config name, hash and threshold, the model revision and
   the B prompt hash; and the builder versions.
2. Builds the 8 banks in run `P-banks-01` (`run.build_banks`: the config check before
   slot 1, the run manifest, `parallel_banks` at a time with `workers` profile streams).
3. Runs `banks verify` on each bank and builds spares in runs `P-banks-01-S1`, `-S2`
   where needed.
4. Runs `finish`: verifies every bank and writes the verify log and reports, the register
   and the throughput summary.

**After a crash.** A crashed build is not resumed (#26). Keep the crashed root as the
record (run `finish` on it, then `archive`; `finish` lists the bank directories that
have no manifest). Then run again in a new root with a new run ID and `--major 2`. Every
bank is then built at version `2.0.0` under new seed namespaces (`bank-P001-v2.0.0`).

Exit codes: 0 when every slot has a usable bank, 3 for a shortfall, 1 for a verify or
plan problem, 2 when the command is refused. `finish` repeats step 4 until the root is
archived. `check` compares the register with hashes recomputed from the stored files
(`--verify` also re-runs `banks verify`), and checks the archive once there is one.

## 3. Files under the root

```
<root>/
  pilot-plan.json             av-banks/pilot-plan v1
  runs/P-banks-01/            main run: run-manifest.json, generation-config.json, logs/,
                              banks/bank-P001..bank-P008/ (bank-builder.md section 3)
  runs/P-banks-01-S1/         spare runs, one bank each
  verify/verify-log.txt       banks verify output of every bank, then a total line
  verify/<bank>-v<ver>.json   VerifyReport of every bank
  pilot-register.csv          the register (section 4)
  throughput.json             av-banks/pilot-summary v1 (section 6)
  throughput.md               the throughput note
  archive-manifest.json       written by `archive` (section 5)
  archive-sha256.txt
```

Every attempt is kept, failed ones included: its slot log (every slot with its outcome,
model status, latency and hashes), refusals, `attempt.json` (status, reason, failed
cell, timing, throughput) and the bank's `timing.jsonl`.

## 4. Register (`pilot-register.csv`)

The register is a CSV file: UTF-8, `\n` line ends, one header row, and one row per bank
built (main banks and spares, complete and unavailable). Rows are sorted by dyad slot and
then by version. The columns are `av_banks.register.REGISTER_COLUMNS`:

| Column | Meaning |
| --- | --- |
| `bank_id`, `bank_version`, `role` | `role`: `main` (planned) or `spare` |
| `dyad_slot` | the unit, e.g. `B-P03` (manifest `dyad_slot`) |
| `set` | `pilot` (`demo` in a rehearsal) |
| `status` | `complete` or `unavailable` |
| `use` | `1` for the bank its dyad slot uses: the first complete bank that verifies. At most one per slot |
| `attempt_used`, `attempts`, `slots_used` | the attempt used (empty when unavailable), attempts on disk, slots of all attempts |
| `verify` | `pass` or `fail` (`banks verify`) |
| `reason` | `complete at attempt 2; failed cells: t1 P1 K-a3`, or `unavailable: 4 attempts failed (cells: ...); never assign` |
| `seed_namespace` | as in the manifest |
| `generation_config_sha256` | the config hash (`GenerationConfig.frozen_sha256()`) |
| `separation_threshold` | the config threshold (0.10 for the pilot) |
| `permutation_sha256` | SHA-256 of the unit's `permutation.json` |
| `bank_sha256` | the bank hash (`bank_manifest.bank_sha256` of the manifest) |
| `run_id`, `bank_path` | the run, and the bank directory relative to the root (POSIX) |

`register_problems(register, root, expected_set=...)` reads every row and recomputes the
bank hash from the stored files (`manifest.manifest_from_files`). That hash must equal
the register's, the stored manifest's and `bank-sha256.txt`. It also compares every
other column with the manifest, the config and the run manifest, refuses banks of
another set, and checks that IDs are unique and each slot uses at most one bank.

## 5. Archive

`archive` refuses to run while `check` reports a problem. It then writes
`archive-manifest.json` (`av-banks/archive-manifest` v1, which lists the SHA-256 of
every file under the root) and `archive-sha256.txt`. The archive hash is
`jsonio.file_set_sha256` of that list. Then it removes the write permission of every
file (on Windows it sets the read-only attribute). After that, `finish` and `archive`
refuse the root. `check` reports any file that is missing, changed, added or writable.
Record the archive hash and the register hash in the run notes and in the PR or issue.
The files themselves stay in restricted storage.

## 6. Throughput summary (`throughput.json`, `throughput.md`)

`av_banks.metrics.summarize_banks` reads the stored files only. The definitions are in
the module docstring.

- **Slots per hour, per bank builder:** all slots divided by the summed wall time of all
  attempts.
- **Slots per hour, per run:** all slots divided by the summed run time, which includes
  the banks built in parallel.
- **Attempts per bank:** the mean, minimum, maximum and a histogram.
- **Attempt failure rate.**
- **Slots per complete and per failed attempt.**
- **Cell failure rate:** failed cells divided by concluded cells, also per profile. Cells
  that a parallel stream left unfinished are not concluded.
- **Slots per complete cell.**
- **Slot outcomes** and **model calls by status.**
- **Latency and slot time:** p50 and p95, and the number of slots over the 40-s cap.
- **Projection for the confirmatory run:** 72 banks by default (64 slots and 8 spares).
  It gives attempts, slots, hours at both rates and the expected number of unavailable
  banks.

The JSON also holds the shortfall, the usable slots, the spares used, the config hash and
the register hash. With 8-10 banks the projection is a rough planning figure.

## 7. Confirmatory mode refuses pilot banks

`register.open_bank(bank_dir, mode="confirmatory")` is the loader for confirmatory
tooling (#28, #13, #70). It refuses a pilot bank before it reads anything else, with
`BankSetError: bank 'bank-P001' is a pilot bank; confirmatory mode refuses it`. It
refuses anything that is not `bank-C...` in the same way. That includes the issue's
proposed form `PILOT-B-01`, which is not a bank ID. The loader also refuses a manifest
that does not hash to its `bank-sha256.txt`. `register_problems(...,
expected_set="confirmatory")` refuses every pilot row. To check a bank on the command
line, run `python -m av_banks.pilot load <bank_dir> --mode confirmatory`, which exits 2
with the refusal.

## 8. Rehearsal and evidence (DEMO)

`tests/banks/test_pilot_banks.py` runs the whole flow on `DEMO-bank-P001`..`P008`. It
uses a fake model and the #17 stand-ins of the bank tests. The fake model's outputs and
simulated latencies are pure functions of the B seed key. `DEMO-bank-P003` fails its
first attempt, and `DEMO-bank-P006` is unavailable, so one spare (`DEMO-bank-P006`
v1.1.0) is built. The committed outputs are in
[`../examples/demo-pilot/`](../examples/demo-pilot/README.md). To regenerate them, run
the following from the repository root:

```bash
AV_BANKS_DEMO_PILOT_OUT=banks/examples/demo-pilot uv run --project banks pytest \
  --import-mode=importlib -p no:cacheprovider tests/banks/test_pilot_banks.py -k demo_evidence
```

The test compares every register column except the hash-valued ones with a fresh
rehearsal, and it also compares the summary figures. Hash values change whenever a shared
format or a code pin changes.

## 9. Pending

- **Pending (hardware):** the real pilot run on the LLM host. Run section 2 with the
  pilot units and the current config, then record the register hash, the archive hash,
  the verify log total line and the throughput note in the issue.
- **Pending (hardware/human):** the Unity smoke test (#70). #70's menu-store bridge pins
  a provisional DEMO `DyadBank` today. When #70 reads #26 bank manifests, load one pilot
  bank (`runs/P-banks-01/banks/bank-P001`, after
  `python -m av_banks.pilot load <bank_dir> --mode pilot`) into the menu build. Open one
  atom menu and check that its 3 cards play the rank 1-3 options (file hashes in the
  manifest), then take a screenshot.
- If the run has a shortfall, raise it before O6.3.2.
