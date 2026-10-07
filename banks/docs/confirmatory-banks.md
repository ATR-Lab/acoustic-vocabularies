# Confirmatory Study B banks

Subpackage `av_banks.confirmatory` of `av-banks`, issue #28 (WBS O8.1.1). After the G4
freeze it builds the confirmatory candidate banks for 64 dyads plus 8 spares with the
#26 builder ([`bank-builder.md`](bank-builder.md)), verifies them, and compiles the
register that is committed before the allocation list is unsealed (gate G5B). It never
reads the allocation list, roles or participant data (Study B protocol §4).

The real run needs the G4 freeze (#25) and the LLM GPU host. It is **pending**. Everything
below runs now with `DEMO-` IDs and a scripted proposer (section 9).

Cross-team contract: [`docs/interfaces/generation.md`](../../docs/interfaces/generation.md),
section "Confirmatory banks (#28)".

## 1. Procedure

Run the commands from the repository root. `R` is the campaign directory in restricted
storage. A confirmatory campaign is refused inside a git work tree.

| Step (issue checklist) | Command | Result |
| --- | --- | --- |
| Confirm the G4 freeze manifest and tag; check the builder config hash | `python -m av_banks.confirmatory plan ... --repo .` | refused unless the freeze manifest passes #25's freeze guard (valid, `frozen`, running code and committed files equal to it), the tagged commit holds this manifest byte for byte and `repo_commit` is that commit or an ancestor, and its `config.frozen_sha256` equals the config hash (section 4) |
| Create the 72 bank IDs and seed namespaces and record them | the same `plan` | `R/plan.json`, `R/seed-check.json` |
| Run the builder in parallel on the LLM host with progress monitoring | `run R ...`; `status R` from any shell | one #26 run per bank; `R/progress.jsonl`; a progress line on stderr |
| Run `banks verify` on every complete bank | `verify-all R --jobs N` | `R/verify/*.json`, `R/verification-log.txt` |
| Compile the register and archive; hash the archive | `register R` | `R/register.csv`, `R/register.json`, `R/archive/<id>-banks.tar` and its SHA-256, `R/g5b-report.md` |
| Commit the register with a timestamp before allocation unsealing | `publish R --dest banks/registers/<id>`, then `git add`, `git commit`, `git push`, then `commit-check` for `register.csv` and `register.json` | commit link; the committed bytes and the commit time (before the first screening) are checked |
| Report complete and unavailable counts to the G5B owner | send `R/g5b-report.md` | counts, decision, unavailable bank IDs, hashes |

The full command lines:

```bash
P="uv run --project banks python -m av_banks.confirmatory"
$P plan --campaign-root R --campaign-id C-banks-v1 \
  --generation-config <restricted>/generation-config.json \
  --freeze-manifest generation/FREEZE-v1.0.json --repo . \
  --units <restricted>/schedules-out/B \
  --pilot <restricted>/pilot-banks --parallel-banks 6
$P run R --meanings <restricted>/meanings --prompts <restricted>/prompts \
  --decoding-schema <restricted>/decoding-schema.json --llm-url http://<llm-host>:8000 \
  --parallel-banks 6 --workers 3
$P status R
$P verify-all R --jobs 8
$P register R                  # exit 0 ready or escalated, 3 escalation required, 1 blocked
$P escalate R --reference https://github.com/ATR-Lab/acoustic-vocabularies/issues/28#issuecomment-N --date 2027-04-27
$P publish R --dest banks/registers/C-banks-v1
git add banks/registers/C-banks-v1 && git commit -m "Register confirmatory banks C-banks-v1 (#28)" && git push
for f in register.csv register.json; do
  $P commit-check --repo . --path banks/registers/C-banks-v1/$f \
    --campaign-root R --first-screening 2027-05-03T09:00:00Z
done
```

`--freeze-manifest` is the committed G4 file and `--repo .` the checkout it is
committed in (required for a confirmatory plan; `--freeze-repo-path` if the manifest is
not at `generation/FREEZE-v<freeze version>.json`). `--units` is the schedules output
directory of Study B. It holds
`<unit_id>/permutation.json` for `B-C01`..`B-C64` and `B-S01`..`B-S08`. `--pilot` takes
pilot bank, run or campaign directories, or a CSV with a `seed_namespace` column. Repeat
it as needed, or give `--pilot-namespace NS`.

## 2. Bank IDs, dyad slots and spares

| Bank IDs | Role | Dyad slot (unit) |
| --- | --- | --- |
| `bank-C001`..`bank-C064` | main | `B-C01`..`B-C64` |
| `bank-C065`..`bank-C072` | spare | `B-S01`..`B-S08` |

These are the #26 and #31 dyad-slot sequence: no random draw and no allocation
information (`common.campaign_bank_ids`, `common.dyad_slot`, equal to
`permutation.expected_unit_id`). The issue proposed `B-001`..`B-072`. The shared
contract fixed `bank-C001`.. before this issue, and the reveal API (#31) already uses
it, so the register uses those IDs.

Each bank is bound to its unit's package-safe `permutation.json` (`builder.bank_spec`).
The plan stores a byte copy of each unit, and the run checks the copy against the planned
SHA-256 before it builds the bank.

**Spares and counts.** Spares do not change the count. At least 64 of the 72 banks must
be `complete`. If fewer are, the register decision is `escalation_required`. Stop and
escalate to the advisor before G5B. Then record the escalation with `escalate`: a link to
the issue or PR comment and a date, never a name. Compile again, and the decision becomes
`escalated`. Spares cover the unavailable main banks exactly when at least 64 banks are
complete. An `unavailable` bank is never assignable. The coordinator logs each one with
the reveal API (`RevealLog.log_bank_unavailable`, schedules #31) before the first reveal.
The reveal API then replaces the unavailable main slot, at its position, by the first
unused spare with the same SQ arm and swap flag. The builder never sees arms, so this
match is made at reveal time. If no matching spare is left, the reveal API refuses and
says to escalate.

## 3. Seeds

The seed namespace of each bank is the #26 default: the bank ID for version `1.0.0`. A
rebuilt bank uses `<bank_id>-v<version>`. The model seeds are `seeds.derive_seed` of
`B|<namespace>|<attempt>|<profile>|<atom>|<slot>`. The key space of one bank is
4 x 3 x 16 x 12 = 2,304 keys, so the 72 banks have 165,888 keys. The checks are
automated (`seed_check`):

- **Before the run** (`check_seeds`, at `plan` and at every `rebuild`):
  - every namespace names its bank and version;
  - every bank is a confirmatory bank;
  - the namespaces are unique;
  - the 165,888 keys give 165,888 distinct 64-bit seeds;
  - no namespace and no seed is shared with the pilot. The pilot seeds are the whole
    key space of the pilot namespaces. The namespaces are read from pilot manifests,
    attempt summaries and every pilot slot record's seed key, and each recorded pilot
    seed is checked against its key.

  A confirmatory plan without pilot namespaces is refused. The result is in
  `seed-check.json` (with `seeds.seeds_digest` as a cross-machine check), and its hash
  is in `plan.json`.
- **After the run** (`check_used_seeds`, in `register`): every slot record of every
  bank version, crashed ones included, must meet all of these:
  - its key is in its bank's namespace;
  - its seed is the seed of that key;
  - no other record used the seed;
  - the seed is not a pilot seed.

  The result is in `used-seeds.json`, which stays restricted. The register shows only
  whether the check passed.

## 4. Freeze check and runner

**Freeze check** (`plan`; `freeze_check`). The freeze manifest is read through #25's
freeze guard, `freeze.load_freeze_manifest(path, require_frozen=True)` for a
confirmatory campaign. The guard checks the manifest, refuses a draft, and compares the
running code and the committed files with the manifest, so a checkout with an edited
module, prompt set or LLM manifest never plans a campaign. A checkout without #25's
guard refuses a confirmatory campaign; a DEMO campaign is then checked against
`freeze-manifest.schema.json`. Then `genconfig.check_run_config` must pass: the code
pins equal the config, the manifest is `frozen` and its `config.frozen_sha256` equals
the config hash.

A confirmatory plan also checks the freeze tag in the repository given with `--repo`
(`freeze_check.check_tag`). In #25's procedure (`generation/docs/freeze.md` sections 2
and 6), `repo_commit` is the commit the frozen values were collected from, and the
signed tag marks the later commit that adds the manifest. So the check requires:

- the tag resolves to a commit;
- that commit holds the given manifest byte for byte at
  `generation/FREEZE-v<freeze version>.json` (or `--freeze-repo-path`);
- `repo_commit` is that commit or an ancestor of it.

`plan.json` and `register.json` record `tag_checked`, `tag_commit` and `guard_checked`
(their schemas require all three for a confirmatory campaign), and the G5B report shows
them. A DEMO rehearsal checks neither.

`run` (`runner.run_campaign`) first checks the campaign again: the stored freeze
manifest and config must be the planned ones, the manifest must pass #25's guard again
(the checkout may have changed since `plan`), and `genconfig.check_run_config` must
pass. Only then does it build the *pending* banks.

- Each bank is one #26 run, `R/runs/<campaign>-<bank>/banks/<bank>/`. It runs with
  `parallel_banks` banks at once, and each bank has `workers` profile streams. One bank's
  failure never touches another bank's directory.
- **Budget.** A bank version is built once. Complete and unavailable banks are never
  built again, and the builder caps 12 slots per cell, 576 per attempt and 4 attempts.
  The register checks every attempt again.
- **Progress.** Every `--monitor-interval` seconds, the runner appends a snapshot to
  `R/progress.jsonl` and prints a line. The snapshot holds the banks per state, the
  slots used, slots per minute, each running bank's attempt and slot count, and an
  estimate of the hours left. `status R` gives the same view from another shell.
- **Model-server outage.** A failed model call (`server_error` or `timeout`) uses its
  slot as usual (Study B protocol §4, #26), with two exceptions
  (`runner.InfrastructureBreaker`):
  - the call is the `--breaker`-th failed call in a row (3 by default, 1 to 11) of its
    profile stream in its bank;
  - the call falls on the last (12th) slot of its cell.

  Then the failing call itself raises `CampaignHalted`, before the builder records its
  slot, and so does the next slot of every running bank. A failed call is therefore
  never the slot that fails a cell. During an outage every call fails, so each stream
  halts at its third failed call or at its cell's 12th slot, whichever comes first, with
  at most 11 slots used in that cell. An outage can thus not fail an attempt or make a
  bank `unavailable`, in any attempt. Halted banks are *crashed* and are rebuilt. The
  halting call has no slot record; its request stays in the crashed run's
  `llm-requests.jsonl`. A single failed call on a 12th slot also halts, because one call
  cannot tell an isolated error from the start of an outage. Failed calls that do not
  halt stay visible in `timing.csv` (`llm_server_errors`, `llm_timeouts`).
- **Stopping.** After a failed build, no further bank starts, and the banks already
  running finish. `--continue-on-error` keeps starting banks. Ctrl-C also stops new banks
  from starting.
- **Crashes.** A build that fails after its first slot leaves its bank *crashed*: a run
  directory without a manifest. #26 never resumes a build. `rebuild R --bank-id ID
  --reason TEXT` records a rebuild under the next version (`1.0.0` -> `1.0.1`, namespace
  `<bank>-v1.0.1`) in `rebuilds.jsonl` and checks the seeds again. The next `run`
  builds it. The crashed run stays in the campaign and the archive, and the register
  shows `crashed_versions` and `crashed_slots`. A build refused before slot 1 (for
  example, a prompt set that differs from the config) leaves nothing, and the bank stays
  pending. An `unavailable` bank is final and is never rebuilt.
- **One runner.** `runner.lock` admits one runner at a time. `--break-lock` removes the
  lock that a killed runner leaves behind. It also cuts a line that the killed runner
  left unfinished at the end of `events.jsonl` or `progress.jsonl`, and logs the cut as
  `log_repaired`. The campaign logs are appended through `jsonio.JsonlAppender`: the
  runner's bank threads share one lock per file, so lines never interleave, also on
  Windows.

**Sizing.** If the `--pilot` directories hold pilot banks, `plan` adds `sizing` to
`plan.json`:

- the pooled pilot rate of one bank in slots per minute;
- the expected slots: 72 x the mean pilot slots per bank;
- the worst case: 72 x 2,304 = 165,888 slots;
- the hours for each at `--parallel-banks`.

Check in the first hour that the per-bank rate holds with that many banks at once, and
that the slot p95 stays under the 40-s cap (`timing.csv`, `progress.jsonl`).

## 5. Files

```
R/                                   restricted storage
  plan.json                          confirmatory-plan.schema.json
  freeze-manifest.json, generation-config.json, units/<unit>/permutation.json  (copies)
  seed-check.json, used-seeds.json
  rebuilds.jsonl, events.jsonl, progress.jsonl
  runs/<run_id>/...                  #26 run directories (manifests, slots, attempts, WAVs)
  verify/<bank>-v<x-y-z>.json, verification-log.txt, verification.json
  timing.csv, slot-timing.csv
  register.csv, register.json        public (hashes and counts only)
  escalation.json, g5b-report.md
  archive/<campaign>-banks.tar
```

Only `register.csv` and `register.json` go into git. They hold bank IDs, dyad slots,
statuses, counts and SHA-256 hashes. They hold no seeds or namespaces, no recipes or
WAVs, and no allocation information. Everything else stays restricted.

## 6. Register

`register.csv` has one row per bank, in sequence order (`register.REGISTER_COLUMNS`):

| Column | Meaning |
| --- | --- |
| `sequence`, `bank_id`, `role`, `dyad_slot`, `bank_version` | identity (`main`/`spare`) |
| `status`, `assignable` | `complete`/`unavailable`; `1` only for a complete, verified bank built under the frozen config |
| `generation_config_sha256`, `config_matches_freeze` | the bank manifest's config hash, and whether it equals the G4 `config.frozen_sha256` |
| `attempts`, `attempt_used`, `slots_attempt_used`, `max_slots_per_attempt`, `slots_total` | budget use (at most 4 attempts and 576 slots per attempt) |
| `crashed_versions`, `crashed_slots` | earlier crashed versions of the bank, if any |
| `verify`, `verify_report_sha256` | `banks verify` result and its report |
| `bank_sha256` | the bank hash (`bank_manifest.bank_sha256`; amendments never change it) |

`register.json` follows `banks/schema/confirmatory-register.schema.json`. It holds:

- the counts (all, main and spares; complete and unavailable);
- the checks: 72 records, attempt and slot caps, config equals freeze, every complete
  bank verified, used seeds unique and disjoint;
- the problems, if any;
- the `decision` (`ready`, `escalation_required`, `escalated` or `blocked`) and the
  escalation;
- the unavailable bank IDs and the spare rule;
- the freeze reference (manifest hash, status, tag, `repo_commit`, `tag_checked`,
  `tag_commit`, `guard_checked`);
- the hashes of the plan, seed checks, register CSV, verification log, timing logs and
  archive.

`register` is refused in these cases: a bank is pending, running or crashed; a verify
report is missing or older than its manifest; or a runner holds the lock. A failed check
gives `blocked`, and `publish` refuses a blocked register.

**Timestamp.** `commit-check --path <dir>/register.csv` (or `register.json`) finds the
first commit that added the register file and checks three things: the committed bytes
are the campaign's file of that name, no later commit changed the file, and the commit
time strictly precedes `--first-screening` (required). Another file name, or a campaign
without the file, is refused. It prints the commit link. In the Python API
(`check_register_commit`), a result without the file hash or the screening time lists
what was not checked and is not `ok`. Git commit times are claims made by the committer. The independent record is the
push time on GitHub (the commit page and the PR timeline). Push the register commit, and
give its link in the G5B report, before the first confirmatory screening.

**Timing log.** `timing.csv` has one row per bank version and an `ALL` row
(`register.TIMING_COLUMNS`). Each row holds:

- attempts and slots;
- wall time, slots per minute and `wall_estimated`;
- model latency and open-to-close slot time (nearest-rank p50, p95 and max);
- slots over the 40-s cap;
- failed and timed-out model calls;
- start and end times.

A bank row's wall time is the sum of its attempts (`attempt.json`). An unfinished
attempt of a crashed build has no `attempt.json`: its time runs from its
`attempt_start` timing event to its last timing event or slot record, and the row is
flagged `wall_estimated = 1`. The `ALL` row's wall time is the runner time from
`events.jsonl`, from each `runner_start` to its `runner_end`. A killed runner writes no
`runner_end`: its session ends at its last record (runner event, progress snapshot or
bank activity) before the next `runner_start` or `lock_broken`, and the row is flagged
`wall_estimated = 1`. `slot-timing.csv` has
the latency and slot time of every slot, for methods reporting.

**Archive.** `archive/<campaign>-banks.tar` is a POSIX tar of every campaign file except
the archive itself, `register.json`, `g5b-report.md` and the lock. It holds files only,
in sorted POSIX paths, with mtime 0, uid and gid 0, mode 0644 and no owner names. The
same files therefore give the same SHA-256 on every platform. The hash is in
`register.json`.

## 7. Python API

| Module | API |
| --- | --- |
| `common` | `campaign_bank_ids(*, demo=False)`, `bank_sequence`, `bank_role`, `dyad_slot`, `run_id_for(campaign_id, bank_id, bank_version)`, `CampaignLayout`, `CampaignError` (`.code`: `E_INPUT`, `E_FREEZE`, `E_UNITS`, `E_SEEDS`, `E_EXISTS`, `E_STATE`, `E_LOCKED`, `E_VERIFY`, `E_COMMIT`), `N_MAIN`, `N_SPARES`, `MIN_COMPLETE` |
| `plan` | `create_plan(root, *, campaign_id, config, freeze_manifest, units, pilot, clock, bank_version="1.0.0", repo=None, freeze_repo_path=None, parallel_banks=1) -> CampaignPlan`; `check_freeze(freeze_manifest_path, config, *, kind, repo=None, manifest_path=None) -> FreezeRef` (`tag_checked`, `tag_commit`, `guard_checked`); `size_run(pilot, *, parallel_banks)`; `read_plan`, `effective_banks`, `bank_history`, `read_rebuilds`, `next_version`, `append_event` |
| `freeze_check` | `load_freeze(path, *, kind) -> LoadedFreeze` (#25's guard), `check_tag(repo, manifest, data, *, manifest_path=None) -> str` (tagged commit), `guard_available()`, `default_manifest_path(manifest)` |
| `seed_check` | `bank_seed_keys(namespace)`, `check_seeds(entries, *, expected_set, pilot) -> SeedCheck`, `read_pilot(paths=(), *, namespaces=()) -> PilotSeeds`, `check_used_seeds(banks, *, pilot_namespaces) -> UsedSeeds`, `SeedEntry`, `KEYS_PER_BANK` |
| `runner` | `run_campaign(root, *, proposer_factory, clock, config=None, bank_clock=None, parallel_banks=1, workers=3, only=None, ledger_factory=slot_ledger, fsync=True, breaker_threshold=3, monitor_interval_s=60.0, on_progress=None, stop_on_error=True, break_lock=False, llm_runtime=None) -> CampaignRun`; `campaign_status(root) -> CampaignStatus`; `rebuild_bank(root, bank_id, *, reason, clock) -> PlannedBank`; `check_campaign`; `InfrastructureBreaker(threshold=3)` (`record(cell, proposal, slot_id)`), `GuardedProposer`, `CampaignHalted`, `DEFAULT_BREAKER` |
| `register` | `verify_all(root, *, jobs=1, only=None) -> VerifyAll`; `timing_log(root) -> TimingLog` (`rows`, `runner`), `attempt_times(bank_dir, records)`, `runner_time(layout, activity=())`; `compile_register(root, *, clock) -> RegisterResult`; `register_row`; `tally(rows)`, `escalation_required(counts)`, `decide(counts, *, checks_ok, escalated)`; `write_archive(root, target) -> ArchiveInfo`; `record_escalation(root, *, reference, date, clock)`; `publish_register(root, dest)`; `check_register_commit(repo, path, *, expected_sha256=None, first_screening_utc=None, ref="HEAD") -> RegisterCommit` |
| `rehearsal` | `rehearse(out, *, campaign_id, unavailable, outage=(), parallel_banks=4, workers=1, jobs=1, only=None, ledger_factory=None, ...) -> Rehearsal`; `DemoSlotProposer`, `demo_units`, `demo_config`, `demo_freeze_manifest(config)` (a draft) |
| `cli` | `main(argv)`; `python -m av_banks.confirmatory` |

## 8. Decisions

| Decision | Rationale |
| --- | --- |
| Bank IDs `bank-C001`..`bank-C072` (not the proposed `B-001`..) | the shared contract (#26, #31) fixed them before this issue; the reveal API logs outages by these IDs |
| Spares replace unavailable main banks in allocation order, matched by SQ arm and swap at reveal (#31) | the issue's proposal; the match needs the concealed list, which the builder never reads |
| At least 64 of 72 complete, else `escalation_required`, recorded with a link and a date | the issue's proposal; equivalent to "spares cover every unavailable main bank" in number; arm matching is checked by the reveal API |
| Seed namespace = bank ID (the #26 default); disjointness checked over the full key space | pilot (`bank-P`) and confirmatory (`bank-C`) namespaces differ by construction; the check also covers any rebuild namespace and the recorded pilot seeds |
| One #26 run per bank | a crash or halt affects one bank's run only; the per-bank run manifest names the bank's seed namespace |
| Halt on the third failed model call in a row of one profile stream, or on a failed call on a cell's 12th slot, from the failing call itself; crashed banks are rebuilt under a new version | an outage must not fail a cell, so it can never exhaust attempts and turn a bank `unavailable` (a failed call on the 12th slot would decide its cell); counting per stream finds an outage within a cell's budget whatever the other streams do; isolated failed calls still use their slots (Study B §4); #26 never resumes a build, and a new version brings new seeds; the crashed run is archived and reported |
| Freeze tag: the tagged commit holds the given manifest byte for byte, and `repo_commit` is that commit or an ancestor; `--repo` required for a confirmatory plan; #25's guard on the manifest at `plan` and `run` | #25 defines `repo_commit` as the commit the values come from and the tag as the commit that adds the manifest, so they differ; the byte comparison ties the file the campaign copies to the tagged one; the guard refuses a checkout that differs from the freeze (#25 contract) |
| Only `register.csv` and `register.json` are public | hashes and counts give a timestamped commitment without seeds, recipes or WAVs |
| Deterministic tar archive | the archive hash can be recomputed from the stored files on any machine |
| Commit time from git, push time on GitHub as the independent record | a git timestamp alone can be set by the committer |

## 9. Rehearsal (DEMO)

`rehearse --out DIR` (`rehearsal.rehearse`) runs sections 1 to 6 on `DEMO-C001`..
`DEMO-C072` without a model. It uses these inputs:

- 72 DEMO unit stand-ins (`demo_units`: only the fields the builder reads, marked
  `"demo": true`);
- a DEMO config built from the repository's DEMO meaning set, the recipe schema, the
  DEMO fallback set and synthetic prompt hashes;
- a DEMO draft freeze manifest with that config's hash, without tag or sign-off (a
  DEMO config is never frozen; with #25 in the checkout it is #25's draft format);
- the DEMO pilot namespaces `DEMO-P001`..`DEMO-P008`;
- `DemoSlotProposer`: random recipes drawn from each slot's seed key, with simulated
  model latency on each bank's own clock.

By default, `DEMO-C007`, `DEMO-C030` and `DEMO-C070` receive unusable output and become
unavailable. With `workers=1`, every bank and `register.csv` are the same on every
machine. The rehearsal uses #17's slot ledger.

[`banks/examples/demo-confirmatory/`](../examples/demo-confirmatory/README.md) holds the
public outputs of one rehearsal. `tests/banks/test_confirmatory_rehearsal.py` rebuilds
three of its banks and compares their register rows.

## 10. Pending

- **Pending (G4 and hardware):** the real run. It needs the frozen G4 manifest and tag
  (#25), the pilot banks (#27), the 72 confirmatory units from schedules, and the LLM
  host. Run section 1 in order. Then attach to the issue: the register commit link, the
  archive SHA-256, `verification-log.txt` and `timing.csv` (restricted, by hash),
  `g5b-report.md` and the `commit-check` output.
- **Pending (human):** send `g5b-report.md` to the G5B owner. Escalate to the advisor if
  the decision is `escalation_required`. Log the unavailable banks with the reveal API
  before the first reveal.
