# Message composition: byte contract

Contract version: **1.0.0**. Producer: message composer (#10, `av_sound.composer`).
Consumers: package builder (#13) and the Unity audio subsystem (#64).

A whole message is the action motif, then 200 ms of digital silence, then the
referent motif (Protocol constants; Study A protocol §3.2). This document defines
the exact bytes of a message and its hash, so that another implementation (the
Unity app) can make the same bytes and check them against the reference. The
Python composer is the reference implementation; the test vectors in
[`../testvectors/composition/`](../testvectors/composition/) are the evidence.

## 1. Grammar

- A message has one action atom and one referent atom, in that order.
- Both atoms come from the same book, the same family (K or Q) and the same
  profile (P1, P2 or P3). The reference composer checks the book when both atoms
  carry a `book_id` (`E_BOOK_MISMATCH`).
- Atom IDs: `K-a1` to `K-a4` and `K-r1` to `K-r4`, the same for `Q`. A message ID
  names its two atoms: `K-a2-r3` is action `K-a2` then referent `K-r3`.
- A book has 2 x 4 x 4 = **32 legal messages**.

Semantic labels are permuted onto the matrix indices per unit (#29), so an ID
carries an index, never a meaning. The fixed matrix (Protocol constants) is the
same for both families. Rows are action indices, columns are referent indices:

| | r1 | r2 | r3 | r4 |
| --- | --- | --- | --- | --- |
| a1 | Train V1 | H-V1 | Train V2 | H-W1 |
| a2 | H-W4 | Train V1 | H-V2 | Train V3 |
| a3 | Train V2 | H-W1 | Train V2 | Train V3 |
| a4 | H-V3 | Train V3 | Train V3 | H-W4 |

That gives 9 trained and 7 held-out messages per family: **18 trained and 14
held-out** per book. `av_sound.grammar` encodes this matrix. Its 14
held-out IDs are the composer's fixed held-out set (section 4). The curriculum
generator (#29) encodes the same matrix.

## 2. Bytes of a message

| Item | Value |
| --- | --- |
| Sample format | Signed 16-bit integer, little endian, mono, 48,000 Hz |
| Atom samples `pcm(atom)` | The `data` payload of the atom's canonical WAV: bytes 44 to the end (renderer spec D8) |
| Atom length `n` | 21,600, 28,800, 36,000 or 43,200 samples (450, 600, 750 or 900 ms) |
| Gap | Exactly 9,600 samples of value 0 (19,200 zero bytes) |
| Message | `pcm(action)` then the gap then `pcm(referent)` |
| Message length | `n_action + 9,600 + n_referent`: 52,800 to 96,000 samples (1.100 to 2.000 s) |
| Possible lengths | 1,100, 1,250, 1,400, 1,550, 1,700, 1,850 and 2,000 ms |
| Referent onset | Sample index `n_action + 9,600` |

Rules:

1. Copy the samples without change. Do not apply gain, fades, crossfades,
   dither, resampling, normalization or a limiter to the message. Each atom is
   already normalized on its own (renderer spec D6); the message is not
   normalized again.
2. Do not find the boundary by looking for silence. Every motif starts and ends
   with a zero sample (renderer spec D2, D3) and contains its own 20 to 60 ms
   gaps. The run of zero samples around the message gap is therefore longer than
   9,600 (at least 9,602). Use the atom lengths.
3. A trained-message WAV is the canonical WAV (renderer spec D8) of the message
   bytes. Held-out messages have no WAV and no buffer outside the moment of
   their test (section 4).

## 3. Hashes

- **Composite hash**: SHA-256 of the message bytes, lowercase hex:
  `SHA-256(pcm(action) || 19,200 zero bytes || pcm(referent))`. It can be
  computed incrementally (update with the action bytes, then the zero bytes,
  then the referent bytes) without making the message buffer.
- For a trained message, the composite hash equals the `pcm_sha256` of its WAV
  file. It is not the `file_sha256`, which also covers the 44-byte header.
- For a held-out message, the package (#13) carries the composite hash in its
  audio index (`audio.json`,
  [`package-format.md`](../../docs/interfaces/package-format.md)).
  `composite_hash()` computes it in memory and returns no samples.

## 4. Held-out rule in the reference composer

Held-out messages never exist as complete audio in generation, teaching,
dictionary or practice (Protocol constants).

| Function | Held-out message |
| --- | --- |
| `compose_message` | Refused: `HeldOutMessageError` (code `E_HELDOUT`) |
| `write_message_wav` | Refused: `HeldOutMessageError`; no file is written |
| `composite_hash` | Allowed: returns only the hash |
| `message_length` | Allowed: metadata only, never renders (Study A protocol §3.1) |

A refusal logs a warning on the `av_sound.composer` logger and calls the
optional `audit` callback with an event (`event`, `operation`, `message_id`,
`action_id`, `referent_id`) before it raises. The check happens before any
sample is read.

The 14 held-out IDs of the fixed matrix are **always** refused. They are the
same in every unit; only the visit at which each is tested changes (#29). The
`heldout=` argument can **add** IDs to the refused set (for example a message
whose wave is not taught yet). It can never remove one: an empty table still
refuses all 14. The Python API has no way to compose a held-out message.

## 5. Test vectors

[`../testvectors/composition/vectors.json`](../testvectors/composition/vectors.json)
is written by [`../tools/make_composition_vectors.py`](../tools/make_composition_vectors.py).
`tests/sound/test_composer.py` recomputes every value on Linux, macOS and Windows
in CI. All content is synthetic.

| Key | Contents |
| --- | --- |
| `books[]` | Three synthetic books `DEMO-P1`, `DEMO-P2`, `DEMO-P3` (one per profile, `av_sound.synthetic`) |
| `books[].atoms[]` | `atom_id`, `recipe`, `recipe_sha256`, `n_samples`, `pcm_sha256`, `file_sha256` (16 per book) |
| `books[].messages[]` | `message_id`, `action_id`, `referent_id`, `matrix_status`, `heldout`, `heldout_set`, `training_wave`, `n_samples`, `duration_ms`, `composite_sha256` (all 32 per book, held-out flagged) |
| `patterns[]` | Four vectors that need no renderer and no file (below) |

**Pattern vectors.** Each part has `n_samples`, `mul` and `add`. Sample `i`
(0-based) is `(mul * i + add) mod 65535 - 32767`, written as int16 little endian.
Build both parts, compose them, and compare `action_pcm_sha256`,
`referent_pcm_sha256` and `composite_sha256`. The four vectors cover the shortest
(52,800) and longest (96,000) messages and both mixed cases.

**How to check another implementation (for example #64):**

1. Pattern vectors: generate the parts from the rule, compose, compare the three
   hashes. No audio file is needed.
2. Synthetic books: run
   `uv run --project sound python sound/tools/make_composition_vectors.py --wav-dir OUT`.
   It writes `OUT/DEMO-Pn/atoms/<atom_id>.wav` (16 per book) and
   `OUT/DEMO-Pn/messages/<message_id>.wav` (the 18 trained messages per book).
   Never commit these files. Check each atom file against `file_sha256`.
3. Compose all 32 messages of each book from the atom files and compare each
   result with `composite_sha256`. The app's runtime composer must build
   held-out messages at test time (section 6), and these synthetic books are
   test data, so its tests may compose them. The Python composer never does;
   its held-out values come from `composite_hash`. Compare each trained result
   with the data payload of the matching message WAV.

## 6. Open decision: playback of a held-out message at test time

**Pending: decision by the owner of #64.** The app must play a held-out message
that exists in the package only as two atoms and an expected composite hash.

| Option | What the app does | Gap accuracy |
| --- | --- | --- |
| A. One buffer (proposed in #64) | Composes the message in memory just before its slot with section 2 rules, checks the composite hash, plays one clip | Exact by construction: the gap is in the samples |
| B. Two clips | Checks the composite hash from the two atom buffers and 19,200 zero bytes (incremental hash), plays the action at DSP time `t` and the referent at `t + (n_action + 9,600) / 48,000` s | Depends on the scheduling precision of the audio engine |

In both options this composer is the reference. The app must reproduce
`composite_sha256` for every message in the test vectors and the packaged hash
of every trained message (#64 acceptance criteria), and it must check the hash
before the slot starts. In both options any output-device rate other than
48 kHz resamples the audio; this contract covers the samples given to the audio
engine, not the device output.

## 7. Python API

The signatures are in
[`docs/interfaces/sound-engine.md`](../../docs/interfaces/sound-engine.md#composer-10).
Summary:

```python
from av_sound import AtomAudio, compose_message, composite_hash, message_length, render

action = AtomAudio.from_rendered("K-a1", render(action_recipe, "P1"))
referent = AtomAudio.from_rendered("K-r1", render(referent_recipe, "P1"))
message = compose_message(action, referent)  # K-a1-r1 is trained
assert message.pcm_sha256 == composite_hash(action, referent)
assert message.n_samples == message_length(action_recipe, referent_recipe)
```
