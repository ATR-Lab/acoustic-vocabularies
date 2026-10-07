# v1.0 release-freeze tooling (prepares #85, #86, #87)

**No v1.0 freeze has happened.** No protocol or app v1.0 exists, no change log
has been written, nothing is tagged and nobody has signed off. The Study A and
Study B pilots, the pilot reviews (O6.3.4, O6.3.5) and the sample-size decisions
(O6.4.1, O6.4.3) are still pending. This page describes tooling only. Tagging,
the second-person recomputation, station checks and sign-off remain human steps;
the tools never create tags, record approval or edit a frozen archive.

| Tool | Purpose |
| --- | --- |
| `tools/release_freeze.py changelog` | Every path changed between two refs is covered by exactly one change-log row (#85). |
| `tools/release_freeze.py write` | Writes `FREEZE.json` and `SHA256SUMS` for a freeze bundle, with the content guard (#86, #87). |
| `tools/release_freeze_verify.py` | Independent recomputation of a bundle, optional tag and station install-hash checks. |
| `tools/release_policy.py` | Declared coverage list and public-content guard shared by both. |

Python 3.12 and the standard library only (plus Git on `PATH` for the change-log
check and tag check). No network access, no device access.

## Change-log coverage (#85)

The change log is a UTF-8 CSV (LF or CRLF; a BOM is tolerated) with exactly
this header, in this order:

```text
change_id,component,paths,description,pilot_finding_id,trigger,issue,reason,checks_rerun,commit,reviewer
```

| Column | Rule |
| --- | --- |
| `change_id` | `CHG-` and 3–4 digits, unique. |
| `component` | Lowercase token, e.g. `app`, `console`, `protocol-text`, `run-sheet`. |
| `paths` | `;`-separated repository paths changed by this row. Exact paths, no globs. |
| `description` | What changed (the changed value for protocol text). |
| `pilot_finding_id` | Pilot finding ID from the O6.3.4/O6.3.5 import. Empty, `none`, `TBD`, `n/a` and similar are refused, except that an `engineering` row must leave it empty. |
| `trigger` | Analysis plan §8 trigger that fired, `none`, or `engineering`. Lowercase token. |
| `issue` | Required for every row. `#<n>` or a full `https://github.com/ATR-Lab/acoustic-vocabularies/issues/<n>` (or `pull/<n>`) link. |
| `reason` | Pilot reason for the change, or the engineering reason for an `engineering` row. |
| `checks_rerun` | `;`-separated check tokens, e.g. `integrity;leakage;mock-segment`. |
| `commit` | `;`-separated full 40-digit commits; each must be in `base..candidate`. |
| `reviewer` | `PENDING` placeholder, or `github:<login>` once a person has reviewed the row. |

Cells must not be empty, padded, multiline, or start with `=`, `+`, `-` or `@`
(spreadsheet formula injection). The #85 proposal names the first seven columns;
`paths`, `issue`, `reason` and `reviewer` are added so coverage can be checked
by machine. Escalated items (floor/ceiling, generation changes) are not applied
silently: when they change a file they need a row like any other change.

**Engineering-only rows.** By maintainer decision on PR #204, an
engineering-only change between v0.9 and v1.0 that has no pilot finding (for
example tooling or build fixes) is logged with trigger `engineering`, an empty
`pilot_finding_id` and an issue link that explains it. All other columns follow
the usual rules, and the summary reports `engineering_rows`. The parser refuses:

- `PILOT_FINDING_REQUIRED`: a row with any other trigger (including `none`) and
  an empty or placeholder finding ID. Only the exact token `engineering` is
  exempt; `Engineering`, `engineering-fix` and padded values are not.
- `ENGINEERING_ROW_HAS_FINDING`: an `engineering` row with any finding ID,
  including a placeholder. A change that answers a pilot finding is a pilot
  change: log it under the trigger that fired (or `none`) with its finding ID, so
  it is never relabelled as engineering work and reviewed with less scrutiny.
- `ISSUE_REQUIRED`: any row, engineering or not, with an empty `issue`.

```text
python tools/release_freeze.py changelog --base <v0.9 tag> --candidate <v1.0 candidate ref> --changelog docs/protocol/changes-v1.0.csv [--require-reviewed]
```

The check compares the two commit trees with `git diff --no-renames`, so the
result does not depend on the working tree, `core.autocrlf` or LFS smudging, and
a rename needs both its old and new path in a row. The change log is read from
the candidate commit; `--draft-file <local.csv>` checks a draft instead. The
change log's own path is exempt. Any of these makes the check fail (exit 1) with
a list of problems:

- `UNCOVERED_PATH`: a changed path has no row.
- `EXTRA_ROW_PATH`: a row lists a path that did not change.
- `PATH_IN_MULTIPLE_ROWS`: a path is claimed by more than one row.
- `COMMIT_OUTSIDE_RANGE`: a listed commit is not in `base..candidate`.
- `REVIEWER_PENDING`: only with `--require-reviewed`, for the final check.

Malformed files, unsafe cells and bad refs stop the check with a code (exit 2).
The tool does not prove that a row's commit touched its paths or that the
reason is right; those remain the reviewer's job.

## Freeze bundle (#86, #87)

A bundle is a directory with one subdirectory per declared item, named by its
item ID, for example `app_build/experiment.apk` or `protocol_text/...`. A
plan file states, for every declared item of the study, how it is covered:

```json
{
  "schema_version": 1,
  "study": "A",
  "label": "protocol-A-v1.0",
  "source_commit": "<40-digit tagged commit>",
  "items": {
    "app_build": {"status": "files"},
    "allocation": {"status": "concealed", "sha256": "<sha256 of sealed copy>", "reference": "<opaque ID>"},
    "speech_commands": {"status": "not_applicable", "reason": "<why this item does not apply>"}
  }
}
```

- `files`: the bundle holds the item's files, which are hashed.
- `concealed`: the contents stay in approved private storage; only the SHA-256
  computed there and an opaque reference ID (no path) are recorded. For many
  files, hash their manifest (for example the run-sheets manifest).
- `not_applicable`: a reason of at least 8 characters. Not allowed for items
  marked required below.

```text
python tools/release_freeze.py write --bundle <bundle dir> --plan <plan.json>
```

The writer refuses, before writing anything:

- a plan that misses a declared item or adds an undeclared one;
- `files` for an item whose contents must be concealed (allocation lists, seeds,
  run sheets, curriculum/holdout tables, the private apparatus manifest);
- any file in the directory of a `concealed` or `not_applicable` item;
- names forbidden by `tools/repo_guard.py` (private directories, `*.local.*`,
  keys, `.env`, allocation-list/codebook/confirmatory-seed/participant names)
  and names containing allocation, participant, codebook, vocabulary, learner
  package, candidate bank, confirmatory or master seed, consent, roster or
  enrolment terms;
- CSV/TSV headers with participant, learner, dyad, subject, pseudonym,
  allocation, assignment or randomization columns, and JSON/JSONL keys
  `participant_id`, `learner_id`, `dyad_id` or `subject_id`;
- credential patterns from `tools/repo_guard.py`;
- links, special files, unsafe or case-colliding names, files at the bundle root,
  empty `files` items, and an existing `FREEZE.json` or `SHA256SUMS`.

Pattern checks supplement review. They cannot prove that a file contains no
study material, so review the bundle before it leaves private storage.

It then writes `FREEZE.json` (study, label, source commit, coverage version, every
item's status and file list, sign-off `pending`) and `SHA256SUMS` in
`sha256sum` format (`<hex>  <path>`, sorted, LF only, `FREEZE.json` included).
Both are created exclusively; a frozen bundle is never rewritten. Later changes
start a new version with its own change log. The printed `sha256sums_sha256`
should be kept separately (release notes, IRB/OSF handoff), because someone who
can edit both files can make a consistent forgery.

### Independent verification

```text
python tools/release_freeze_verify.py --bundle <dir> --sums-sha256 <retained pin> [--concealed allocation=<sealed copy>] [--repo <checkout> --tag protocol-A-v1.0] [--station-record <inventory.local.csv> ...]
```

The verifier does not import the writer. It parses `SHA256SUMS` byte for byte
(a CRLF-converted file is refused with `SUMS_NOT_LF`), refuses unlisted, missing
or linked files, and recomputes every hash (`HASH_MISMATCH`, or
`LFS_POINTER_NOT_MATERIALIZED` when `git lfs pull` was skipped). It checks that
`FREEZE.json` covers exactly the declared items with valid statuses, and runs
the content guard again. Optional checks:

- `--concealed ITEM=PATH`: recompute a concealed item from its sealed copy.
- `--tag`: the tag must resolve to `source_commit`.
- `--station-record`: a `tools/quest_station.py inventory` record whose
  `installed_apk_sha256` must equal an archived `app_build` hash. Only the
  record's position is reported, never station IDs or serials.

A second person can also recompute by hand with `sha256sum -c SHA256SUMS`
(Linux) or `Get-FileHash -Algorithm SHA256` (Windows) and compare. PASS means
the bytes and coverage match; it is not acceptance of the freeze.

### Line endings

Hashes are of exact bytes. `.gitattributes` marks `docs/protocol/v*/**` as
`-text`, so an archive committed under the proposed `docs/protocol/v1.0/` keeps
its bytes on every checkout, including `core.autocrlf=true`. Elsewhere the
repository normalizes text to LF, which would change CRLF files. Large binaries
in an archive still follow the LFS rules; run `git lfs pull` before verifying.
What may be committed publicly at all is decided by the data policy, not by
this tool: an archive that holds only concealed hashes and public files is the
expected shape.

## Declared coverage (`o6.4-proposed-1`)

These items restate the freeze contents listed in #86 and #87. They stand in for
the Common procedures §9 list, which is outside this repository. Reconcile them
with that list before a real freeze, and change `COVERAGE_VERSION` when they
change. `conceal` = `required` means the contents never enter a bundle.

| Study | Item | Contents | Conceal | Required |
| --- | --- | --- | --- | --- |
| A | `app_build` | App v1.0 build(s) for the chosen topology | optional | yes |
| A | `console_build` | Operator console build | optional | yes |
| A | `station_config` | Station config files; station identifiers stay in `*.local.*` files | optional | no |
| A | `isaac_scene` | Isaac scene USD | optional | no |
| A | `robot_asset` | Robot asset | optional | no |
| A | `reset_snapshot` | Reset snapshot | optional | no |
| A | `curriculum_tables` | Curriculum, label permutation and holdout tables | required | no |
| A | `trial_orders` | Lesson and trial orders with stored seeds | required | no |
| A | `run_sheets` | Run sheets | required | no |
| A | `allocation` | Study A allocation lists (hash only) | required | yes |
| A | `lesson_test_scripts` | Lesson and test scripts | optional | no |
| A | `experimenter_scripts` | Experimenter scripts | optional | no |
| A | `speech_commands` | Speech-command recordings (#71) | optional | no |
| A | `fault_thresholds` | Fault thresholds, onset calibration and freeze-trigger value | optional | no |
| A | `apparatus_manifest` | Apparatus manifest v1.0 (private manifest; hash only) | required | yes |
| A | `sample_size_decision` | Sample-size decision record (O6.4.1) | optional | no |
| A | `g4_freeze_record` | G4 freeze record (#25) | optional | no |
| A | `protocol_text` | Protocol v1.0 text | optional | yes |
| A | `change_log` | v1.0 change log (#85) | optional | yes |
| B | `app_build` | Study A v1.0 build or documented minor-version build | optional | yes |
| B | `minor_version_record` | Minor-version record with change log and regression evidence | optional | no |
| B | `screening_presets` | Screening procedure, three profile presets and calibration example | optional | no |
| B | `menu_timings` | Profile and atom menu timings | optional | no |
| B | `session_scripts` | Active and yoked scripts | optional | no |
| B | `presentation_ledger_format` | Presentation-ledger format | optional | no |
| B | `visit_schedule` | Atom growth, visit windows and assessment counts | optional | no |
| B | `allocation` | Study B allocation list (hash only) | required | yes |
| B | `bank_generation_config` | Bank generation configuration | optional | no |
| B | `failure_rules` | Absence, withdrawal and failure rules | optional | no |
| B | `sample_size_decision` | Sample-size decision record (O6.4.3) | optional | no |
| B | `protocol_text` | Protocol v1.0 text | optional | yes |

Optional items can still be concealed when their owner decides the contents are
not public (for example scripts that contain study vocabulary). The model, prompt
and decoding settings are frozen at G4 (#25) and enter only through the G4 record.

## Tests

`tests/test_release_freeze.py` builds temporary Git repositories and bundles
only. It covers: a passing change log and freeze; uncovered changes, extra rows,
duplicate claims, out-of-range commits and unsafe cells; engineering rows
accepted with an issue link and refused without one or with a finding ID, and
non-engineering rows refused without a real finding; tampered, added,
missing and LFS-pointer files; a consistent forgery caught only by the retained
pin; forged coverage; forbidden names, columns, keys, credentials and concealed
content; LF-only, deterministic output; byte-exact CRLF hashing; a converted
`SHA256SUMS`; an `autocrlf` clone with and without the archive rule; tag and
station-record checks; and that this table matches `tools/release_policy.py`.
