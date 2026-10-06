# A2 mutation search

Module `av_generation.a2` (#18). A2 is the frozen feedback-guided evolutionary search of
Study A protocol §3.5 and the primary comparator for A3. It uses the same 12-slot budget,
validator, rating process, selector, four-round stop and fallback as A1 and A3
([architecture](architecture.md) sections 3 and 4).

## API

| Name | Contract |
| --- | --- |
| `A2Proposer(ledger, *, clock)` | `proposers.RoundProposer`; `propose_round(request) -> RoundResult` fills the round's three slots in order |
| `sample_uniform(rng) -> Recipe` | One uniform draw per coordinate (round 1, or no eligible parent) |
| `mutate(parent, k, rng, *, parent_slot_id=None) -> (Recipe, A2Detail)` | Child with exactly `k` distinct mutated coordinates |
| `mutate_pitch(value, step, *, coordinate="pitch_1") -> A2Mutation` | Step, reflection at -6/+6, inward correction (`corrected=True`) |
| `mutate_index(coordinate, value, direction) -> A2Mutation` | One position up (+1) or down (-1), reflecting at the ends |
| `choose_coordinates(rng, k)` | `k` distinct coordinates, uniformly without replacement, in protocol order |
| `select_parent(feedback) -> CandidateFeedback \| None` | The incumbent, checked against the protocol's parent rule |
| `plan_slot(seed_key, slot, parent) -> (Recipe, A2Detail)` | The proposal of one slot (pure function of the key and the parent) |
| `check_request(request)` | Refuses requests A2 must not act on (`A2RequestError`) |
| `draw_index(rng, n)` | Exact uniform integer `0..n-1` from the raw PCG64 output |
| `A2_ALGORITHM` | Name of the frozen draw procedure |
| `A2RequestError(code, message)` | `E_A2_METHOD`, `E_A2_LABEL`, `E_A2_REQUEST`, `E_A2_PARENT` |

## Algorithm

Per round the proposer receives a `RoundRequest` for one atom of its own book and fills
slots 1, 2 and 3:

- **Parent.** Round 1 never has one. In rounds 2-4 the parent is the selector's incumbent
  (`AtomFeedback.incumbent()`): the highest-scoring eligible candidate of this atom seen so
  far, ties to the lowest submission slot (`slot_index`). `select_parent` checks that the
  incumbent is exactly that candidate (eligible, valid, scored, best by the rule, same
  score). A mismatch raises `E_A2_PARENT` before any slot is reserved, so a selector fault
  cannot change A2 silently. The parent stays in the selector's pool and is never proposed
  again (every child differs from it).
- **No parent** (round 1, or no eligible candidate yet): each slot is an independent
  uniform sample over the declared values (`domain.COORDINATES`, which take the value
  lists from `av_sound.recipe`).
- **With a parent:** slot k is child k, which mutates exactly k distinct coordinates of the
  parent, chosen uniformly without replacement from the 12 (3 pitches, `total_ms`, 3 rhythm
  weights, 2 gaps, 3 amplitudes).
  - Pitch: add a step drawn uniformly from {-3, -2, -1, +1, +2, +3}
    (`constants.A2_PITCH_STEPS`) and reflect at -6 and +6. If the reflection returns the
    original value (only +5 with +2 and -5 with -2), use the nearest legal inward
    one-semitone change from the boundary that was hit: +5 -> +4, -5 -> -4. The mutation
    is logged with `reflected` (the reflected value), `result` and `corrected=true`.
  - Other coordinates: move one position up or down with equal probability in the ordered
    allowed values, reflecting at the ends (`gaps_ms` 20 down -> 40, `total_ms` 900 up ->
    750). Every list has at least three values, so the value always changes.
- **Every proposal is charged.** Per slot: `SlotLedger.reserve` (the cap is checked before
  any work), the proposal, `av_sound.validate` against the book's committed references
  (`BookState.references()`, threshold `BookState.threshold`), then `SlotLedger.consume`
  with the outcome (`outcomes.outcome_from_validation`). Invalid proposals consume their
  slot. There is no rejection sampling, repair or retry.

## Seeds and draws

Each slot has its own stream: `rng_for(a2_seed_key(batch_ns, atom, round, slot))`, a NumPy
`Generator(PCG64(seed))` with `seed` = the first 8 bytes of SHA-256 of the key
([architecture](architecture.md) section 6). `batch_ns` is the request's
`seed_namespace`, so a rebuilt batch uses new seeds.

All draws use `draw_index(rng, n)`. This function reads the raw 64-bit outputs of the
PCG64 bit generator and returns `raw % n`. Exact rejection discards raw values at or above
the largest multiple of `n` below 2^64, so each result has probability exactly `1/n`.
NumPy's stream-compatibility policy covers bit generators and `SeedSequence`, but
`Generator` sampling methods may change between NumPy releases. Raw draws keep a
proposal a function of the seed key and the parent only.

The draw order is frozen (`A2_ALGORITHM = "a2-mutation-search/1 ..."`):

1. Uniform sample: one draw per coordinate in protocol order, with `n` the length of
   each value list (13, 13, 13, 4, 4, 4, 4, 3, 3, 3, 3, 3).
2. Child k: k draws of a partial Fisher-Yates shuffle of the coordinate positions
   (`n` = 12, 11, 10). Then the chosen coordinates are mutated in protocol order, one draw
   each: a pitch draws an index into `A2_PITCH_STEPS` (`n` = 6), any other coordinate draws
   a direction (`n` = 2: 0 = down, 1 = up).

Any change to this procedure changes the proposals. It needs a new `A2_ALGORITHM` name
and a rewritten fixture before G4.

## Slot records

Each slot gives one `records.SlotRecord` (`slot-record.schema.json`), written by the
ledger:

| Field | A2 value |
| --- | --- |
| `slot_id`, `slot`, `slot_index`, `round` | `<book>.<atom>.r<round>s<slot>`; the ticket's `slot_index` (1..12) |
| `seed_key`, `seed` | `A2\|<batch_ns>\|<atom>\|<round>\|<slot>` and its unsigned 64-bit seed |
| `raw_output`, `recipe`, `recipe_sha256` | canonical recipe JSON, the recipe object and its SHA-256 |
| `outcome`, `validator_codes`, `validator_messages` | from the validator (all failing codes kept) |
| `pcm_sha256`, `file_sha256` | when the waveform is usable (the WAV-file hash is computed in memory; A2 writes no audio) |
| `t_open_ms`, `t_ms`, `latency_ms` | ticket open time, close time, compute time (run clock) |
| `a2` | `A2Detail(mode, parent_slot_id, mutations)`; every mutation carries `coordinate`, `original`, `step`, `reflected`, `result`, `corrected` |
| prompt, model and A1 fields | `null` |

A2 does not time out. Its proposals are a pure function of the seed keys and the
feedback, so they are the same on every machine and at every clock speed (including the
accelerated runs of #20 and #22). Its compute time, a few milliseconds per slot, is logged
in `latency_ms`.

## Masking

A2 never receives meaning text or semantic labels (Study A protocol §3.5). The
orchestrator gives it `BookState.without_labels()` and `semantic_label=None`. As a guard,
`check_request` refuses a request with a label or with a labelled book state
(`E_A2_LABEL`) before it reserves a slot. The atom ID is used only in the seed key and the
slot ID. The module does not import the meaning or prompt modules (tested).

## Request checks

`propose_round` raises `A2RequestError` before any reservation when the request is not for
A2 (`E_A2_METHOD`), carries a label (`E_A2_LABEL`), or is inconsistent or malformed
(`E_A2_REQUEST`). Inconsistent means one of these: a round outside 1..4, a book state or
feedback of another book, batch, atom or profile, `rounds_closed != round - 1`, feedback
that holds candidates of this or a later round, or an atom that is already committed.
Malformed means a value that A2 would otherwise trip over only after reserving a slot: a
run, batch or book ID that does not match its format, an unknown profile, a seed
namespace or atom ID that does not give valid seed keys, or a book threshold that is not
an exact non-negative decimal. A faulty incumbent raises `E_A2_PARENT`.

The slot IDs and seed keys of all three slots are built before the first reservation.
After these checks only the ledger can interrupt a round, so every reserved slot is
consumed. Ledger refusals (`SlotCapExceeded`, `SlotReused`) propagate after the ledger
has logged them. A bad candidate never raises: it is a slot outcome.

## Tests and the cross-platform fixture

`tests/generation/test_a2_search.py` holds the unit, property and statistical tests:

- Round-1 uniformity: a chi-square test on 30,000 round-1 proposals from A2's own seed
  keys gives p > 0.01 for each of the 12 coordinates. A pairwise independence test over
  all 66 pairs passes with a Bonferroni correction.
- 10,000 random parents: each child k differs in exactly k coordinates and has no
  out-of-domain value. The choice of coordinates (single and pairs), the pitch steps and
  the index directions are uniform (chi-square and binomial tests).
- Pitch edge cases (+5/+2 -> +4, -5/-2 -> -4, both logged as `corrected`), the full pitch
  table, and boundary moves of every index coordinate.
- The proposer: three records per round including invalid ones, reserve before consume,
  no resampling (each recorded recipe equals the planned proposal of its seed key), the
  incumbent as parent, the uniform fallback without an eligible parent, corrections
  written to the slot record, request refusals before any reservation (one case per
  check), and ledger refusals.
- Validation against the book: a slot that re-proposes a committed waveform is recorded
  as `duplicate` (`E_DUPLICATE`, `E_SEPARATION`) and still consumed, and a stricter book
  threshold (0.40) turns a valid proposal into `separation_fail`. Each record's codes and
  messages equal `validate` against `BookState.references()` at `BookState.threshold`.
- Hypothesis properties: children have exactly k changes, the mutation fields agree with
  the values, and the same seed gives the same child.

The slot ledger (#17) and the selector (#20) belong to other issues, so the tests use an
in-memory ledger with the documented `reserve`/`consume` contract (it checks every record
against its schema) and a stub selector that applies the protocol rule.
`test_real_ledger_when_available` runs the proposer against #17's ledger when it is
implemented.

`tests/generation/fixtures/a2-proposals.json` pins the 72 proposals of a fixed DEMO
scenario: two books (P1, P3), three atoms, four rounds, with rounds that have no eligible
parent. The fixture holds the seed keys, seeds, recipes, modes, parents, mutations,
outcomes, waveform hashes and the SHA-256 of every full slot record. The CI matrix
(Linux, macOS, Windows) regenerates and compares it. To rewrite it after a deliberate,
versioned algorithm change:

```bash
AV_GENERATION_WRITE_A2_FIXTURE=1 uv run --project generation pytest \
  --import-mode=importlib tests/generation/test_a2_search.py -k fixture
```

## Credibility check (synthetic scores)

Study A protocol §3.5 asks for a development check that A2 is a credible baseline.
`av_generation._a2_credibility` compares A2 with plain uniform sampling under the same
budget (12 independent uniform samples per atom) on synthetic DEMO books:

- Both methods use the real proposal code, the real validator against the book's
  committed references, and the protocol's selector rule.
- Common random numbers make the comparison paired. The baseline draws from A2's own
  per-slot seed keys, and the synthetic raters use one stream per slot that both methods
  share.
- Synthetic panel (an assumption, not a perceptual model): each atom has a hidden target
  point, and association falls from 7 at the target to 1 at feature distance 0.5.
  Distinguishability rises from 1 to 7 over a distance of 0 to 0.4 from the nearest
  committed reference, and is fixed at 4 while the book has no reference. Three raters
  add normal noise and report integers 1..7. Comfort is unacceptable with probability 0.1
  plus 0.3 times the share of events pitched at +3 or higher.
- An atom without an eligible candidate commits nothing (the fallback bank is out of
  scope). The measure is the noise-free score of the committed candidates, compared per
  book (A2 minus uniform, paired t interval).

Command (about 4 minutes; outputs go to the ignored `generation/out/`):

```bash
uv run --project generation python -m av_generation._a2_credibility --grid \
  --out generation/out/ci/a2-credibility
```

The recorded result is `generation/runs/DEMO-a2-credibility/grid.json`. The run used 40
books per profile (120 books), 16 atoms, separation threshold 0.10, and A2 algorithm
`a2-mutation-search/1`. The first row is the main scenario. "int" rows use integer
ratings (protocol). "real" rows use real-valued ratings (a diagnostic). "atom1" rows use
the first atom of each book only, where both methods search the same space without
committed references.

| Scenario | A2 score | Uniform score | A2 rounds 1 -> 4 | Uniform rounds 1 -> 4 | A2 - uniform [95% CI] | Books A2 better / worse |
| --- | --- | --- | --- | --- | --- | --- |
| book-int-noise1 (main) | 4.152 | 4.359 | 3.995 -> 4.152 | 4.017 -> 4.359 | -0.207 [-0.231, -0.183] | 9 / 111 |
| book-int-noise0 | 4.257 | 4.448 | 4.044 -> 4.257 | 4.052 -> 4.448 | -0.192 [-0.217, -0.166] | 10 / 110 |
| book-real-noise1 | 4.167 | 4.365 | 3.997 -> 4.167 | 4.020 -> 4.365 | -0.198 [-0.220, -0.175] | 7 / 113 |
| book-real-noise0 | 4.431 | 4.514 | 4.046 -> 4.431 | 4.078 -> 4.514 | -0.083 [-0.106, -0.060] | 35 / 85 |
| atom1-int-noise1 | 3.219 | 3.341 | 3.032 -> 3.219 | 3.032 -> 3.341 | -0.122 [-0.216, -0.028] | 42 / 59 |
| atom1-int-noise0 | 3.297 | 3.410 | 3.062 -> 3.297 | 3.062 -> 3.410 | -0.113 [-0.199, -0.027] | 40 / 53 |
| atom1-real-noise1 | 3.290 | 3.342 | 3.041 -> 3.290 | 3.041 -> 3.342 | -0.052 [-0.134, 0.030] | 52 / 55 |
| atom1-real-noise0 | 3.545 | 3.456 | 3.085 -> 3.545 | 3.085 -> 3.456 | +0.089 [0.012, 0.167] | 74 / 45 |

Scores are the mean noise-free score (1..7) of the committed candidates. "noise1" and
"noise0" set the rater noise SD to 1 or 0. No atom needed a fallback in any scenario. In
the main scenario, 95.8% of A2 slots and 90.8% of uniform slots were valid, and 85.6% and
81.0% were eligible.

The grid also measures the drift of unselected A2 mutations (`centrality_drift`): the
mean |feature - 0.5| of 2,000 uniform recipes is 0.286, and it falls to 0.278, 0.253 and
0.238 after 1, 3 and 9 mutations. Reflection at the ends of each value list causes this
drift toward interior values. For a 3-value list, the stationary probability of the middle
value under repeated one-step moves is 1/2, not 1/3.

**Result.** Under these synthetic assumptions A2 improves its incumbent in every round,
but it does not beat plain uniform sampling at the book level in any scenario. A2 is
ahead only on isolated atoms with precise, real-valued feedback. Two likely contributors
(not tested separately):

1. Imprecise feedback (rater noise or integer 1..7 ratings) hides the small gains of
   one-step moves, and ties keep the parent; a fresh uniform sample can jump further.
2. A local search over three generations explores less than 12 independent samples,
   and the reflection rule pulls children toward the centre of the domain.

**Status.** The protocol fixes the algorithm, and tuning it is out of scope for #18. This
result goes to the protocol owner, who decides whether A2 counts as plainly ineffective
and whether to revise it prospectively (versioned, before G4). The decision is tracked on
#18, which stays open until it is recorded there, and it is a precondition of the G4
freeze (#25, item "A2 rules frozen"). Until that decision, A2 stays exactly as §3.5
specifies. The #22 dry run can repeat the check with its bot panel.

## Follow-ups for other issues

- #20 (orchestrator): build A2's `RoundRequest` with `book.without_labels()`,
  `semantic_label=None`, `seed_namespace=BatchConfig.seed_namespace`, and feedback whose
  incumbent follows the selector rule. A2 refuses anything else.
- #25 (freeze): the generation config pins `A2Rules` (steps and child sizes) but not the
  draw procedure. A freeze item for `A2_ALGORITHM` or for the fixture's
  `records_sha256` would make a change to `a2.py` visible in the frozen config.
