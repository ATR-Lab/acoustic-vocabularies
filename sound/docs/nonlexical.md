# Nonlexical assets: calibration examples, READY cue, grammar clicks

Asset spec version **0.1.0** (`av_sound.nonlexical.ASSET_SPEC_VERSION`), renderer
`0.1.0`. Status: **pilot default; freezes at G4** with the renderer and the
validator. Producer: #14 (`av_sound.nonlexical`). Consumers: validator #9
(`E_RESERVED`), Unity audio subsystem #64, teaching flow #68, Study B menus #70,
golden tests #12.

These are the only sounds besides study motifs that participants hear. They are
nonsemantic: they must never teach or leak a meaning, so they must never be
admissible as motifs. Every asset is listed in
[`../reserved/registry.json`](../reserved/registry.json) and is built from code;
WAV files are never committed.

## 1. What the protocols need

| Need | Source | Asset |
| --- | --- | --- |
| One 2-s nonsemantic calibration example per preset, played twice with a 2-s gap, with a comfort question and the comfortable gain | Study B protocol §3, step 4 | `calibration-P1`, `calibration-P2`, `calibration-P3` |
| Profile menu at V1: the screening example, 8 nonsemantic plays (onsets 4 s apart: 2-s example + 2-s gap) | Study B protocol §5.1 | the same three |
| Comfortable nonlexical audio calibration on day 0, no study motifs | Study A protocol §5 | calibration example(s) |
| A nonlexical READY cue, for familiarization only, never a semantic item | Protocol constants | `ready-cue` |
| Grammar explained with silent labels and neutral clicks distinct from every study motif | Common procedures §5; Study B protocol §3, step 2 | `click-action`, `click-target`, `click-grammar-demo` |

## 2. Assets

All assets are mono, 48 kHz, signed 16-bit PCM in the canonical WAV layout
(renderer spec D8). Hashes follow renderer spec D9 (`pcm_sha256` covers the samples
only; `file_sha256`, in the registry, covers the whole file). Levels are digital,
relative to full-scale code 32,767; they are not a sound-pressure calibration.
"Active RMS" is the RMS over the sounding segments only (silences excluded).

| ID | Kind | Profile | Samples | ms | Peak dBFS | RMS dBFS | Active RMS dBFS | `pcm_sha256` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `calibration-P1` | calibration | P1 | 96,000 | 2000 | -9.94 | -13.00 | -13.00 | `e50e47627a73137392e2cfdd0dac2253adda3c8b10aea49174c9506c3fb90211` |
| `calibration-P2` | calibration | P2 | 96,000 | 2000 | -9.94 | -13.00 | -13.00 | `5c1cf02801d36137df047975043a89613aff99882eca77f3604579a2e223dca8` |
| `calibration-P3` | calibration | P3 | 96,000 | 2000 | -9.94 | -13.00 | -13.00 | `14299502afa920cc0413def79b289f0258dcea794bf4922a0666ad34186ea2b1` |
| `ready-cue` | ready_cue | all | 15,360 | 320 | -6.70 | -20.27 | -19.02 | `fa17509cb859718336a136b21c9555a55a834497b89b8321e97ed5704f800d4d` |
| `click-action` | click | all | 192 | 4 | -10.00 | -17.23 | -17.23 | `7770d01665c23f962cd875a621a8c614eb0944ababa60974e18532890b306b6a` |
| `click-target` | click | all | 4,032 | 84 | -10.00 | -27.44 | -17.23 | `cee7832d4c0cc9e490752917b4fbedd35b438832230c7c2713cbfe49e904b6ff` |
| `click-grammar-demo` | click | all | 13,824 | 288 | -10.00 | -31.03 | -17.23 | `3da116f4c9de748eb745a0f0abbf9333d1435dd93806f2d2b8051af35d449619` |

Profile "all" is `null` in the registry: the asset is reserved for every profile.
Sounding segments (`NonlexicalAsset.segments`, as onset and length in samples):

| ID | Segments | Use |
| --- | --- | --- |
| `calibration-Px` | (0, 96,000) | One steady tone |
| `ready-cue` | (0, 5,760), (9,600, 5,760) | Two noise bursts |
| `click-action` | (0, 192) | Single click = "action" |
| `click-target` | (0, 192), (3,840, 192) | Double click = "target" |
| `click-grammar-demo` | (0, 192), (9,792, 192), (13,632, 192) | Action click; the target click starts at sample 9,792 = 192 + 9,600, as a referent starts at `n_action + 9,600` in a message |

## 3. Byte recipes

Every step is integer arithmetic on the renderer tables (renderer spec D2, D4)
and the renderer functions `synth_event`, `envelope` and `normalize` (D3, D6).
Another implementation can reproduce the bytes from this section.

### 3.1 Calibration example (x3)

1. `x = synth_event(profile, pitch=0, n=96,000, k=1)`: one event at the profile's
   `f0` (300, 450 or 675 Hz), partials 1x/2x/3x with weights 20/3/1 (1, 0.15,
   0.05), phase 0 at sample 0, and the per-event 10-ms attack / 30-ms release
   raised-cosine envelope. This is exactly how a motif event is made (spec D3, D4).
2. `y = normalize(x, RMS_TARGET)`: the motif normalization (spec D6), RMS 7,336 LSB
   over the full 2.000 s.

The example therefore has the motif timbre and the motif loudness, but it is a
single 2.000-s event: 96,000 samples, longer than any motif (at most 43,200).

### 3.2 READY cue (x1)

1. **Noise.** A SHA-256 counter stream: block `k` (k = 0, 1, ...) is
   `SHA-256(key || uint64_le(k))` with key `av-sound/nonlexical/ready-cue/v1`
   (ASCII), read as 16 int16 little-endian values. Take the first
   `2 * 5,760 + 22 = 11,542` values `u`.
2. **Band-pass FIR.** The kernel `h` is a first difference followed by boxcars of 6,
   8 and 10 taps, 23 integer taps:
   `1 2 3 4 5 6 6 6 5 4 2 0 -2 -4 -5 -6 -6 -6 -5 -4 -3 -2 -1`.
   `v[n] = sum_k h[k] * u[n + 22 - k]` for `n = 0 .. 11,519` (valid part only, no
   edge transient). The pass band is about 0.9 to 2.9 kHz (-3 dB), zero at DC;
   98.7% of the cue's energy lies between 400 Hz and 4.5 kHz, centroid about 2.0 kHz.
3. **Bursts.** Burst `j` (0, 1) is `v[5,760 j .. 5,760 j + 5,759]` times the motif
   envelope `envelope(5,760)` (Q30), rounded: `(v * env + 2^29) >> 30`.
4. **Level.** `normalize(burst0 ++ burst1, 3,668)`: RMS over the two bursts is
   `RMS_TARGET // 2` = 3,668 LSB.
5. **Layout.** burst 0, 3,840 zero samples (80 ms), burst 1: 15,360 samples (320 ms).

### 3.3 Grammar clicks (x3)

1. **One click**, 192 samples (4 ms), for `i = 0 .. 191`:
   - sine: `S[((i * 268,435,456) mod 2^32) >> 16]`, a 3 kHz sine (exact increment
     `3,000 * 2^32 / 48,000`, 12 whole cycles);
   - window: `EA[5 * min(i, 192 - i)]`, the attack table sampled every 5 entries,
     which is the raised cosine `sin^2(pi i / 192)` (Q30);
   - `v = (sine * window + 2^23) >> 24` (Q30); `m = max |v|`;
   - `c = floor((2 * v * 10,362 + m) / (2 * m))`: peak exactly 10,362 LSB
     (-10.00 dBFS). The click is odd-symmetric about sample 96, so its sum is 0
     (no DC), and it starts at 0.
2. **`click-action`**: one click.
3. **`click-target`**: one click at sample 0 and one at sample 3,840 (onsets 80 ms
   apart), zeros between: 4,032 samples.
4. **`click-grammar-demo`**: `click-action`, then exactly 9,600 zero samples
   (200 ms), then `click-target`: 13,824 samples. This is the message byte layout
   (`docs/composition.md`) with clicks in place of motifs.

## 4. Levels

- The calibration examples have the motif RMS (7,336 LSB, -13.00 dBFS), because the
  comfortable gain is set on them. Measured over every admissible timing structure
  and amplitude triple (56,619 motifs, pitch 0, P1; `make_reserved_assets.py
  --motif-levels`), the RMS of a motif's sounding events is -12.80 to -11.65 dBFS
  (median -12.49), and its loudest single event is at most -9.13 dBFS (median
  -11.25). So motif events are about 0.5 dB (at most 3.9 dB) above the calibration
  tone; motif peaks reach -4.19 dBFS (renderer spec D6). For the loudest-event
  recipe, pitches -6, +6 and mixed under all three profiles give the same event
  levels to 0.001 dB.
- The READY cue is 6 dB below the motif RMS over its bursts (-19.02 dBFS). Its
  energy lies around 1-3 kHz, where hearing is more sensitive than at the motif
  fundamentals (300-675 Hz), so equal RMS would sound louder than a motif.
- Each click peaks at -10.00 dBFS, the same peak as the calibration tone
  (-9.94 dBFS). A 4-ms transient sounds much softer than a steady tone of the same
  peak, so the clicks stay below the calibrated level while remaining clearly
  audible.
- No asset is louder than the motif RMS over its sounding parts, every peak is at
  least 6.7 dB below full scale, and no sample overflows (`overflows()` is checked
  for every asset).
- Individual gain is set on the chosen audio route (ADR-005, #50); these assets do
  not set sound pressure.

## 5. Why no reserved asset is admissible as a motif

- `render()` always returns `total_ms * 48` samples: 21,600, 28,800, 36,000 or
  43,200. No asset has one of these lengths (96,000; 15,360; 192; 4,032; 13,824),
  so no recipe can render to an asset's bytes, and no asset can pass the validator
  as a motif. The composer also refuses an asset as an atom (`E_MOTIF_LENGTH`).
- The registry still lists every asset's `pcm_sha256`, so `validate()` reports
  `E_RESERVED` if a candidate's waveform ever equals an asset (a test patches the
  renderer to prove this path for each of the seven entries).
- `recipe` is `null` for every entry: none of these is recipe-shaped, so the
  feature-distance part of the reserved check does not apply. The calibration
  example is a single 2-s event; it has no rhythm, gaps or `total_ms` in the
  domain, so it has no point in the 12-feature space.
- A 2.000-s message also has 96,000 samples, but its samples 43,200 to 52,799 are
  zero; the calibration tone has no silence there, so no message equals it.

## 6. Registry and validator path

`build_reserved_registry()` returns the registry that `registry.json` must equal
(`registry_version` 1, `renderer_version` = `RENDERER_VERSION`,
`asset_spec_version` = `ASSET_SPEC_VERSION`, one entry per asset in the order of
section 2). `asset_spec_version` makes a change of asset design visible in the
registry itself; the schema keeps it optional, so a registry without it still loads. `validate(..., reserved=None)` loads this file, so every
proposer and bank builder that uses the default gets the reserved check. The
fallback banks (#15) use this path; `tests/sound/test_fallback.py` checks that every
bank and book recipe passes it and that no fallback waveform equals an asset.

## 7. Rebuilding and listening files

```bash
uv run --project sound python sound/tools/make_reserved_assets.py           # rewrite registry.json
uv run --project sound python sound/tools/make_reserved_assets.py --check   # CI: fail on drift
uv run --project sound python sound/tools/make_reserved_assets.py --check --table --wav-dir /tmp/reserved-assets
```

`--wav-dir` writes `<id>.wav` for each asset and an `index.md` level table. Use a
directory outside the repository; never commit WAV files. CI renders the assets on
Linux, macOS and Windows, fails if any hash differs from `registry.json`, and
uploads the Linux WAVs as the workflow artifact `reserved-assets-wav` (kept 14
days), so reviewers can listen without running code.

Any change to asset design or bytes regenerates `registry.json` and this table in
the same pull request and bumps `ASSET_SPEC_VERSION` (recorded in the registry), or
`RENDERER_VERSION` for a renderer change. `tests/sound/test_nonlexical.py` fails otherwise.

## 8. Comfort listening check

A short check with two lab members, on the intended route and earphones, before
the assets are frozen. No study motif or study phrase is played.

1. Render the WAVs (section 7) and play them in the order of section 2.
2. Set a comfortable gain on `calibration-P2`, then keep it fixed.
3. Play each calibration example twice with a 2-s gap; then the READY cue, the
   action click, the target click and the grammar demonstration, twice each.
4. Record one note per listener:

```text
Listening note: nonlexical assets (#14)
Date:            Asset spec / renderer: 0.1.0 / 0.1.0
Route and earphones:                     Gain setting:
Listener (role, no name):
Asset                Comfortable (y/n)   Audible (y/n)   Comment
calibration-P1
calibration-P2
calibration-P3
ready-cue
click-action
click-target
click-grammar-demo
Single vs double click distinguishable (y/n):
READY cue clearly different from the clicks and the tones (y/n):
Any comfort complaint:
```

The acceptance criterion is a recorded note with no comfort complaints. A
complaint changes a level or a shape here, with a new asset spec version and new
hashes.

## 9. Decisions

- **Profile-independent READY cue and clicks.** They carry no timbre that a learner
  must calibrate to, and one asset each keeps the registry small and the grammar
  screen identical for every participant and condition. Only the calibration
  examples depend on the profile, because their purpose is to present the profile.
- **Calibration example = one steady 2-s tone at pitch 0** with the motif partials,
  envelope and RMS: it conveys the profile's timbre and loudness without any
  motif-like pitch or rhythm pattern.
- **READY cue = band-limited noise**, a different sound type from the tonal motifs
  and from the clicks. Two bursts make it a recognizable cue; two (not three)
  avoids echoing the three-event motif structure. The noise comes from a SHA-256
  counter stream, so it needs no random-number library and is identical on every
  platform.
- **Clicks = single (action) and double (target)**, separated in the demonstration
  by the message gap of 9,600 zero samples. The double click's inner silence
  (76 ms) is well below 200 ms, so the two parts group as action, pause, target.
- **No recipe and no feature neighbourhood** for any entry (section 5).
