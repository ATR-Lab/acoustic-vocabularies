# Separation-threshold listening tool (#23)

The listening tool collects same/different answers for pairs of atomic motifs at
controlled 12-feature distances (0.05 to 0.20). O6.2.2 uses the data to keep or revise the
pilot separation threshold of Study A protocol §3.2 (default 0.10) before G4. The tool
reports data only. It does not choose the threshold.

Modules:

| Module | Contents |
| --- | --- |
| `av_generation.threshold` | configuration, stimulus sets, session plans, checks, CSV, summary, plot |
| `av_generation.threshold_runner` | run directory, session runner, web app, bot listener, export |
| `av_generation.threshold_cli` | command line (`python -m av_generation.threshold_cli`) |
| `av_generation/web/threshold/` | listener page (plain HTML, CSS and JavaScript) |

Records and schemas are shared contracts (`records`, `generation/schema/`):
`ThresholdStimulusSet` (`threshold-stimuli`), `ThresholdSession` (`threshold-session`),
`ThresholdTrial` (`threshold-trial`), `PlayEvent` (`play-event`, contexts
`threshold_first` and `threshold_second`) and `TimingEvent`.

## 1. Commands

Run from the repository root. Replace `<...>` with your values.

```bash
G="uv run --project generation python -m av_generation.threshold_cli"

# 1. Build the stimulus set once (restricted storage for a non-DEMO set) and check it.
$G stimuli --set-id <SET-ID> [--config <config.json>] --out <restricted>/stimuli.json
$G check --stimuli <restricted>/stimuli.json

# 2. One listener session per command (serves the page on the station until Ctrl-C).
$G session --runs-root <restricted>/runs --run-id <RUN-ID> --kind pilot \
  --stimuli <restricted>/stimuli.json --session-id <SESSION-ID> --listener <CODED-ID> \
  --station S1 --gain-db <fixed station gain> [--tryout] [--host 127.0.0.1 --port 8023]

# 3. Export: trials CSV, summary CSV, fits CSV, summary JSON and plot.
$G export --run-dir <restricted>/runs/<RUN-ID> --out-dir <restricted>/export [--tryout]

# The summary script alone, from an exported trials CSV.
$G summary --trials trials.csv --out-dir <dir> --label "<label>" [--stimuli stimuli.json]

# Synthetic rehearsal: DEMO set, bot listeners through the real server, export.
$G demo --out-dir generation/out/threshold-demo [--sessions 2] [--small]
```

`check` and `export` exit with status 1 when they find a problem.

## 2. Stimulus set

### 2.1 Configuration

All counts are configuration values (`ThresholdConfig`, the `config` object of the set).
O6.2.2 can change them with `stimuli --config`.

| Field | Default | Meaning |
| --- | --- | --- |
| `profiles` | `P1`, `P2`, `P3` | render profiles (300, 450 and 675 Hz) |
| `bin_centers` | `0.050` .. `0.200` in steps of `0.025` (7 bins) | target distances, ascending decimal strings; every bin within (0, 0.5] |
| `bin_halfwidth` | `0.0125` | a bin is `[center - halfwidth, center + halfwidth)` |
| `pairs_per_bin` | 8 | different pairs per profile and bin |
| `same_pairs` | 56 | identical-pair catch trials in total |
| `gap_ms` | 500 | silence between motif A and motif B |
| `threshold_default` | `0.10` | the pilot default (plot line, fitted P(same) at it, reserved-signal check) |

Default session: 3 profiles x 7 bins x 8 pairs + 56 catch pairs = 224 trials. The
smallest non-zero distance is one semitone on one note (0.0241), so a bin below it
cannot be filled. Catch pairs
are spread round-robin over the profiles (P1 19, P2 19, P3 18).

### 2.2 How pairs are built

- Distance: the exact normalized 12-feature distance of `av_sound.features`
  (`sqrt(sum((x_j - y_j)^2) / 12)`). Bin membership is decided exactly on the sum of
  squares: `12 (c - h)^2 <= S < 12 (c + h)^2`. A boundary value belongs to the upper bin.
- Base motif: each pair has its own seed key and PCG64 stream,
  `THRESHOLD|<set>|pair|<profile>|<center>|<k>` (catch pairs
  `THRESHOLD|<set>|same|<profile>|<k>`). The base recipe is drawn uniformly from the
  recipe domain until it is valid.
- Partner motif: a random walk from the base. It visits the 12 coordinates in a random
  order and moves each by 1 or 2 positions (semitones for pitches) up or down. A move is
  kept when the sum of squares stays below the bin's upper limit. The walk stops when the
  sum of squares enters the bin. A walk that ends below the bin is discarded. After 400
  failed walks a new base is drawn; after 50 bases the build stops with `E_SEARCH`. So
  the coordinates that differ change from pair to pair.
- Validity: both motifs pass `av_sound.validate` with no committed references. Thus every
  admissibility rule applies (schema, domain, event length, finite samples, clipping,
  reserved signals at `threshold_default`) except the separation rule, which a close pair
  breaks on purpose. A different pair never has two identical waveforms. No waveform is
  used twice in a set.
- Pairs are atomic motifs only. They never come from a study book and are never complete
  messages.
- `ThresholdPair.search_steps` is the number of walks until the pair was found (0 for
  catch pairs).

Single amplitude, gap and total-duration steps are large (one step gives a distance of
0.144, 0.144 and 0.096), so pitch and rhythm-weight changes dominate the low bins. In the
DEMO set, the differing coordinates are counted as follows: pitches 240, rhythm weights
151, total 34, gaps 32, amplitudes 30. The trials CSV holds both recipes of every trial,
so O6.2.2 can split the answers by feature.

### 2.3 Reproducibility and hashes

The same set ID and configuration rebuild an identical set: same pairs, same
`ThresholdStimulusSet.sha256()` (canonical JSON). The CI matrix checks this on Linux,
macOS and Windows against the committed DEMO manifest. The set stores both recipes, PCM
and WAV SHA-256 of each motif, the exact sum of squares, the seed key, the renderer and
validator versions and the config. No WAV file is stored. The runner renders each motif
when the session opens and refuses to start when a hash differs (`E_ASSET_HASH`).

DEMO example (synthetic, committed): `generation/examples/threshold/demo-stimuli.json`,
set `DEMO-T1`, default config, 224 pairs, set hash
`34e4643900312cd81a325153f0ccba5fe443ea3fce6029822f94ccf6f83dd90e` (file SHA-256
`52605973df15db9ffb75d9ddbafe57f904993dd400226a646a890b4c9ea0ff1f`).

| Bin | Min distance | Mean | Max | Coordinates that differ (min-max) |
| --- | --- | --- | --- | --- |
| 0.050 | 0.0406 | 0.0520 | 0.0601 | 1-3 |
| 0.075 | 0.0636 | 0.0711 | 0.0833 | 1-5 |
| 0.100 | 0.0876 | 0.0981 | 0.1102 | 1-5 |
| 0.125 | 0.1128 | 0.1214 | 0.1363 | 1-6 |
| 0.150 | 0.1389 | 0.1482 | 0.1620 | 1-6 |
| 0.175 | 0.1632 | 0.1728 | 0.1866 | 1-5 |
| 0.200 | 0.1884 | 0.1991 | 0.2110 | 1-7 |

A non-DEMO set (any set ID without `DEMO-`) is development material: `stimuli` refuses
to write it inside a git work tree. Commit only its hash.

## 3. Sessions

### 3.1 Plan

`plan_session` draws everything from one stream, `THRESHOLD|<set>|order|<session>`:

1. A/B order (`ab_order_rule = "balanced_per_bin"`): within each profile x kind x bin
   group, half the pairs play A first and half play B first, in shuffled assignment. An
   odd group gets one seeded coin flip.
2. Trial order: a uniform permutation of all pairs. Each pair is one trial.

The session document (`threshold/sessions/<session_id>.json`) is written before the
first trial. It holds the set hash, the coded listener ID, the station, the fixed gain,
the seed key and seed, the A/B rule, the plan, the tryout flag and the creation time.
`check_session` checks a session document against its set: the hash, the seed, every
pair exactly once, the A/B balance, and that the plan is the seeded plan.

### 3.2 Trial procedure

1. The listener presses **Play**. The page gets the current trial: two single-use audio
   URLs and the gap. The page never gets the pair ID, kind, profile, bin or distance.
2. The page fetches both WAV files once and schedules motif A, the gap and motif B on the
   Web Audio clock. It plays at the station's output gain: the page has no volume control
   and no gain node. The page reports the two onsets. The server logs one
   `threshold_first` and one `threshold_second` play event.
3. When motif B ends, the **Same** and **Different** buttons open. There is no time
   limit. The answer is logged as a `threshold_trial` record. `rt_ms` is the time from
   the end of motif B to the click.
4. There is no replay control and no feedback. A second fetch of an audio URL is refused
   (`E_TOKEN_USED`, HTTP 410). A second play report is refused (`E_ALREADY_PLAYED`). Both
   refusals are logged as play events with `result = "refused"`.
5. The next trial starts when the listener presses Play again, so the listener can rest
   between trials.

Recovery: if the page reloads or the server restarts, the session resumes from the logs.
A trial that has played can be answered but is not played again. A trial whose audio
cannot play (for example, a reload after the audio was fetched) shows **Skip this trial
(operator)**. A skipped trial is logged with `response` null and is never replayed. Torn
log lines after a crash are cut and logged (`log_repaired`).

### 3.3 Routes

| Route | Body | Reply |
| --- | --- | --- |
| `GET /threshold/` | | the listener page |
| `GET /threshold/api/state` | | `n_trials`, `n_done`, `status` (`running`/`done`), `trial_index`, `phase` (`listen`/`respond`) |
| `POST /threshold/api/next` | | `trial_index`, `n_trials`, `phase`; for `listen` also `gap_ms`, `first`, `second` (audio URLs) |
| `GET /threshold/api/audio/{token}` | | `audio/wav`, once |
| `POST /threshold/api/trials/{i}/played` | `onset_first_ms`, `onset_second_ms` (ms since the page got the trial) | `{"ok": true}` |
| `POST /threshold/api/trials/{i}/response` | `response` (`same`/`different`), `rt_ms` | `ok`, `n_trials`, `n_done`, `status` (no score) |
| `POST /threshold/api/trials/{i}/skip` | | as `response` |

Refusals reply `{"error": "<code>", "detail": "..."}` with HTTP 404, 409, 410 or 422.
The onsets are stored on the run clock as issue time plus the page offsets.

## 4. Operator guide (O6.2.2)

Listeners: about 8 (O2.2.3). A listener must never join a learner cohort. Use coded IDs
only (pattern `[A-Za-z0-9][A-Za-z0-9._-]{0,31}`), never names. IRB approval (O2.1.7)
comes before the first listener session.

Before the study:

1. Agree the configuration (section 2.1). Build the set with a non-DEMO set ID in
   restricted storage. Run `check`. Record the set hash in the O6.2.2 record. Do not
   commit the set.
2. Use one run per listening study (`--kind pilot`) in restricted storage. The first
   `session` command creates it; the later commands reopen it and check the set hash.

Before each session:

1. Use a rater station (O1.3.6). Set the station output to its fixed calibrated gain.
   Enter the same value with `--gain-db`. Do not change the gain during the session.
2. Make sure that the listener is not in, and will not join, a learner cohort.
3. Run `session` with a new session ID (for example `TH01-L03`) and the listener's coded
   ID. Open the printed URL in the station browser in kiosk mode. Do not open it in a
   second tab.
4. Read the instructions on the page to the listener. Do not explain the distances or
   which answers are expected.

During the session:

- Do not give feedback. Do not replay a pair. If the audio fails, press **Skip this
  trial (operator)** and write a note.
- A session of 224 trials takes about 15 to 20 minutes (estimate: 4 to 5 s per trial). The listener can rest before any
  Play press.

After the session:

1. Stop the server (Ctrl-C) after the page shows "The session is complete".
2. Run `export`. Every session must show `"ok": true` in `play_checks` (each pair played
   exactly once, no replay). Keep the export in restricted storage.

Internal tryout: two team members (not counted as listeners) run sessions with
`--tryout`. Export them with `export --tryout`. The tryout label is on the plot and in
the summary. Listener exports never include tryout sessions, and the summary refuses a
mix of the two.

## 5. Outputs

### 5.1 Trials CSV (`trials.csv`, `TRIAL_CSV_COLUMNS`)

One row per trial, sorted by session and trial index. UTF-8, `\n` line ends, `1`/`0`
for booleans, and empty cells for null values.

| Column | Meaning |
| --- | --- |
| `session_id`, `listener_id`, `trial_index` | session, coded listener, trial number 1..N |
| `pair_id`, `profile`, `kind`, `bin_center` | pair, profile, `different`/`same`, bin (empty for catch pairs) |
| `distance` | normalized 12-feature distance (float, shortest repr) |
| `order`, `gap_ms` | `AB` or `BA`, gap played |
| `recipe_first`, `recipe_second` | the two recipes in play order (canonical compact JSON) |
| `pcm_sha256_first`, `pcm_sha256_second` | PCM hashes in play order |
| `response`, `rt_ms` | `same`/`different`/empty (skipped), time from the end of motif B |
| `tryout` | `1` for internal tryout sessions |
| `run_id`, `sum_sq` | run, exact sum of squares (`p/q`) |
| `onset_first_ms`, `onset_second_ms`, `t_ms` | onsets and answer time on the run clock (the clock restarts with each server start) |

`read_trials_csv` rebuilds the trial records exactly.

### 5.2 Summary CSV (`summary.csv`, `SUMMARY_CSV_COLUMNS`)

One row per profile (then `all`, pooled) and bin. The catch row (`kind = same`, empty
`bin_center`) comes first. `n_trials` and `n_same` count answered trials. `p_same =
n_same / n_trials`. `wilson_low` and `wilson_high` are the Wilson 95% score interval
(z = 1.959963984540054):
`(p + z^2/2n +/- z sqrt(p(1-p)/n + z^2/4n^2)) / (1 + z^2/n)`, set to exactly 0 or 1 at
`k = 0` or `k = n`. `n_no_response` counts skipped trials. `mean_distance` is the mean
distance of the group. Floats have 6 decimals.

### 5.3 Logistic fit (`fits.csv`, `FIT_COLUMNS`)

Maximum likelihood fit of `P(same) = 1 / (1 + exp(-(intercept + slope * distance)))` on
the answered different-pair trials, per profile and pooled. The fit uses Newton-Raphson
in pure Python with `math.fsum`, so it does not depend on BLAS. Reported: estimates,
standard errors (inverse Fisher information), `d50 = -intercept / slope` (the distance
where P(same) = 0.5, only when the slope is negative), `p_same_at_default` (fitted
P(same) at `threshold_default`), iterations and log-likelihood. `status` is `separated`
when the answers are completely or quasi-completely separable by distance (no finite
estimate), and `degenerate` with fewer than two distances or only one kind of answer.

### 5.4 Summary document (`summary.json`) and plot

`summary.json` (`format = "av-generation/threshold-summary"`, version 1) is the evidence
file for O6.2.2 and the G4 freeze (#25). It has the label, tryout flag, set ID, set hash,
config, listeners, sessions (station, gain, counts, play-check result), the summary rows,
the fits, the trials CSV hash, the code versions and `decision: null`. The tool never
fills in a decision.

`summary.png` shows P(same) per bin with the Wilson bars, the catch trials at distance
0, the logistic fit and a dashed line at the default threshold. It has one panel per
profile and one pooled panel. The title is the label: `SYNTHETIC ...` for DEMO runs,
`Internal tryout (team members; not listener data)` for tryout exports, or `Listener
sessions`. The plot is never committed. Keep it in restricted storage. CI uploads
synthetic plots as artifacts.

## 6. Checks

| Check | Where |
| --- | --- |
| Every pair valid (all rules but separation) and inside its bin | `check_stimuli`, `check` command, `tests/generation/test_threshold_tool.py` |
| Coverage of every profile x bin with the configured counts | `check_stimuli` (pair-ID set), `coverage()` |
| Same seed, identical set (hash) on Linux, macOS and Windows | `test_demo_set_regenerates_byte_identical` (CI matrix) |
| Each pair played exactly once per session | `check_plays`, `export` play checks, `tests/generation/test_threshold_runner.py` |
| Summary reproduces hand-computed proportions | `tests/generation/fixtures/threshold/hand-summary.csv` (computed by hand with Decimal arithmetic from `hand-trials.csv`) |

## 7. Decisions

| Item | Decision | Rationale |
| --- | --- | --- |
| Bins | 0.050..0.200 in steps of 0.025, half-width 0.0125, half-open `[c-h, c+h)` | the issue's proposal; half-open bins do not overlap, decided exactly on the sum of squares |
| Catch pairs | 56 in total, round-robin over the profiles | the issue's 224-trial session; the config field is a total |
| Pair search | uniform valid base, random walk of 1-2 position steps over randomly ordered coordinates | reaches every bin quickly (the default set builds in about 0.5 s); varies which features differ; deterministic per pair seed |
| Validity | `av_sound.validate` without references, reserved check at `threshold_default`; no reused waveform | "passes the validator apart from the separation rule"; one motif per pair keeps listeners from learning motifs |
| Seeds | one key per pair (`pair`/`same`), one per session (`order`), bot answers (`bot`) | seeds survive config changes of other bins; the session key is already in the shared contract |
| A/B order | balanced within each profile x kind x bin group, shuffled | counterbalanced per bin, not only overall |
| Trial runner | FastAPI page on the station (same stack as the rater client); listener-paced Play; buttons open at the end of B; RT from the end of B | the issue's proposal (500 ms gap, self-paced, no replay, no feedback); every answer follows both complete motifs |
| No replay | single-use audio URLs, one play report per trial, refusals logged | play log proves "played exactly once" |
| Gain | recorded per session (`gain_db`), not changed by the page | playback gain is fixed by calibration, not generated (Study A protocol §3.2); the page cannot change it |
| Missing answers | operator skip, `response` null, never replayed | a failed play cannot become a replay |
| Summary | answered trials only; Wilson 95%; logistic fit on different pairs; tryout and listener data never mixed | the issue's summary; tryouts are not listener data |
| CSV columns | the skeleton columns plus `run_id`, `sum_sq`, onsets, `t_ms` (appended) | the CSV rebuilds the trial records, so the summary script works from the CSV handed to O6.2.2 |
| Summary columns | the skeleton columns plus `kind`, `n_no_response`, `mean_distance` (appended) | catch rows and skipped trials stay visible |

## 8. Pending (human or hardware)

- Internal tryout with two team members on a rater station. Run section 4 with
  `--kind pilot --tryout` in restricted storage, then `export --tryout`. Attach the
  tryout plot (labelled "Internal tryout") to the O6.2.2 record. It is not committed.
- Listener sessions (O6.2.2), after IRB approval and recruitment.
