# Retained held-out phrase history scan

This read-only #78 tool checks audio records from the existing native
`SIMULATION_TEST` run manifests. It re-runs their ordinary reconciliation,
validates package/WAV bytes, and joins the resulting durable journals in one
person's visit order. Generated schedule counts alone are not its input proof.

Prepare a private, independently pinned JSON plan with exactly these fields:

```json
{
  "version": 1,
  "scope": "SIMULATION_TEST",
  "study": "A",
  "role": "reference",
  "coded_id": "A-C01-L01",
  "unit_id": "A-C01",
  "package_sha256": "<actual canonical package SHA-256>",
  "runs": [{"path": "D0/mock-run.manifest.json", "sha256": "<actual raw manifest SHA-256>"}]
}
```

Study B uses `active` or `yoked` and its actual revealed person/unit. Each role
has its own history; the scanner additionally binds B's role through the pinned
allocation. Include every retained attempt, including failed attempts, in actual
process-UTC order. Do not omit earlier consumed exposures to obtain a pass.
Paths are local, relative to the plan, without traversal or links. The existing
run manifest pins configuration, schedule, export, package, native terminal and
independent process observation. No runtime or service is started.

```text
python -m tools.mock_visit.holdouts --plan private/history.json --sha256 RAW_PLAN_SHA256 --out private/fresh-history-result.json
```

The output is created once. Exit 0 is complete **software history only**, 3 is
valid but incomplete evidence, and 2 is malformed or contradictory evidence.
Missing runs, unfinished visits, unused composition permits and recovered clock
histories cannot prove absence. An empty history is explicitly a nonpassing
scan. The bounds are 64 run manifests and 500,000 total durable records, in
addition to the existing per-file and per-run limits.

Every study audio request and observation, and every grammar audio event, is
checked. Known PCM comes from verified package atoms/all complete composites,
reserved nonsemantic WAVs and any verified speech files. An unknown digest is
rejected. Every held-out composite is indexed, including all Study B profile and
rank combinations, so a different label or unselected candidate combination
cannot conceal a phrase. An ambiguous digest shared by held-out message IDs is
refused rather than resolved from a label.

A held-out request needs its exact scheduled novel item, preceding durable cue
intent and one-use composition permit, and the permit's exact audio request ID.
The first request consumes the phrase even if no callback follows. Another run,
session, request ID or rank combination cannot make it unheard again. Permits,
event IDs, data/engine clock epochs and audio request IDs cannot be grafted across
runs. Grammar and study requests share the same ownership namespace; repeated
grammar observations are allowed only for the same session, phase and PCM.

UTC provides process ordering only. No audio onset or clock offset is inferred
by subtracting timestamps from different processes. Acoustic fields remain null;
request/callback evidence does not establish audibility. Recovered or omitted
prefixes cannot establish a complete history. This first version intentionally
does not certify restart/retry histories that would need additional explicit
authority and causal-clock joins.

`recorded_history_scan_passed` describes the supplied audio records only.
`software_history_complete` additionally requires every A D0/D7 or B V1/V2/V3/W1/W4
visit to have passed the full native reconciliation. `omitted_run_custody_verified`,
`acoustic_qualified`, `participant_qualified` and `issue78_accepted` always remain
false. The operator/reviewer still owns evidence completeness and private
material custody. This is not a replacement for the full #78/#81 apparatus checks.

Tests scan full public producer schedule shapes (A: 276 study requests across
D0/D7; B: 500 across V1–W4), with explicitly synthetic journals. Optional existing
sealed producer packages exercise actual PCM/composition hashes and their sealed
schedules via `AV_HOLDOUT_PACKAGE_FIXTURES`; they do not turn those journals into
native runs. Adversarial cases include rehashed early phrase substitution,
unselected B combinations, missing/reordered/reused permits, cross-session and
cross-channel grafts, unknown PCM, process overlap and truncated coverage.
The retained actual A008 prefix is checked separately and remains incomplete.
