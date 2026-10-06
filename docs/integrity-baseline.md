# DEMO integrity baseline (#78)

This executable baseline joins three independently sealed producer packages to
the real Unity package loader, fixed-slot engine, response state, data adapters,
journal verifier and CSV exporter, then checks the retained exports offline.
It is not a participant-ready or audible-delivery certificate.

Use an existing environment with the locked `sound` dependencies and the pinned,
licensed Unity editor. No dependency is installed by the command:

```powershell
python tools/run_integrity.py --unity '<pinned Unity executable>' --out '<fresh private output directory>'
```

The command refuses dirty source unless `--allow-dirty` explicitly marks the
report as a local development run. It creates private DEMO fixtures, runs three
native test cases, verifies all 480 exported responses, runs the Python negative
and producer tests, and retains hashes in `report.local.json`. Native tests must
pass 3/3 and Python must have zero failures or skips. Each run needs a fresh
output directory. No audio, learner package, native log or private path belongs
in the public repository.

For retained native evidence, pass `--fixtures`, an independently retained
`--fixtures-sha256`, and `--existing-exports` instead of `--unity`. This is labelled
`retained_exports: true`; it never claims to rerun Unity. Each export's mapping
receipt must have its companion SHA-256. Preserve those receipts separately
from any mutable CSV copy.

## Evidence covered

- All 32 command tuples under three actual generator permutations, each with
  correct, wrong-action, wrong-target, don't-know and timeout responses. Scoring
  is offline only and never enters the participant view.
- The engine issues one-use novel permits. The ordinary trained-message path
  rejects held-out requests. Actual producer PCM and stored waveform hashes are
  checked against raw request observations, CSVs and export manifests.
- No audio callback is fabricated. The inactive audio adapter records requests;
  their delivery remains uncertain and exposure consumed. This proves the data
  and scoring path, not what was physically played.
- The existing real store grows 8 → 12 → 16 atoms for all three profiles; old
  bytes, recipes, semantic bindings and profiles remain unchanged, and all four
  overwrite types are rejected and logged for each profile.
- Generated histories for one complete Study A allocation unit (12 learners)
  and one Study B dyad satisfy the existing history/oracle checks: 48/108 A
  teaching plays and 128/8 B menu/profile plays per person. These are generated
  schedule counts, not counts measured in complete joined audio histories.
- Negative tests reject duplicate CSV headings, ragged rows, extra/traversing
  export files, stale hashes, torn tails, and rehashed semantic mutations of
  real native exports (wrong score, PCM, removed novel permission and false
  confirmed delivery).

## Observed verification and limits

On 2026-10-05 the real Unity run passed 3/3 tests, exported 160 cases per package,
and all three offline verifications passed. The combined Python suite passed
59 tests with no skips, including the four real-export mutations. Initial test
harness mistakes were corrected before this final pass; retained local logs
include those failed runs.

Hosted Linux/Windows Python contracts are separate from the licensed Unity
workflow. The latter now prepares real fixtures before its full edit-mode suite
and verifies their exports afterward. It still requires the existing licensed
runner configuration; a local pass does not turn that CI gate green.

#78 remains open for complete joined Study A and active/yoked Study B histories,
actual playback/count/contamination reconciliation, a qualified pilot-station
run and a green licensed-Unity CI run. Existing retry and response-family tests
remain separate component evidence. Exact methodology reconciliation is also
pending the authoritative external folder.
