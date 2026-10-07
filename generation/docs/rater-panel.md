# Rater panel: server, stations and bot rater (#21)

The rater panel runs the 20-s rating slots of Study A (Study A protocol §3.3) on three
synchronized, private stations. The orchestrator (#20) owns the schedule and every panel
log; this component serves the stations and relays.

| Part | Where | Role |
| --- | --- | --- |
| Panel server | `av_generation.panel` (`create_panel_app(host, *, clock)`) | FastAPI app: station page, assets, one WebSocket per station; checks every frame, then calls the session host |
| Station page | `src/av_generation/web/rater/` (`index.html`, `station.js`, `station.css`) | Plain HTML/CSS/JS, no third-party code, served at `/panel/station` |
| Bot rater | `av_generation.rater` (`BotRater`, `BotRatingPolicy`, `bot_rating`) | A station driven by code, same protocol (#22 synthetic panels) |
| Session host contract | `av_generation.panel_session` (shared) | `PanelSessionHost`, implemented by `Orchestrator.panel_host()` (#20) |
| Scripted host | `av_generation.panel_demo` (`ScriptedPanelHost`, `demo_session`) | A host that plays a fixed DEMO schedule: tests, screen recordings, skew sessions |
| Onset tables | `av_generation.panel_skew` | Logged onsets and loopback-capture skew per scheduled sound |

Message formats are fixed by `av_generation.rater_protocol` and
`generation/schema/rater-message.schema.json`; this page states how the server, the
stations and the host use them.

## 1. Session flow

1. The operator opens the station URL on each station:
   `http://<server>:<port>/panel/station?station=S1&rater=R01&key=<seat key>`
   (`panel.station_url`). The page asks for the station and the coded rater ID if the URL
   has none. The rater presses **Start** once: this click unlocks browser audio.
2. The station connects to `/panel/ws` and sends `hello` (`kind="human"`; bots send
   `"bot"`). The server accepts only a seat of `host.seats()` (rater ID, station and
   kind); anything else gets `error E_UNKNOWN_RATER` and the socket closes. A second
   `hello` for a connected station replaces the old socket (`station_left` for the old
   one, then `station_joined`); the old socket is closed with
   `panel.REPLACED_CLOSE_CODE` = 4001, after which the old page shows "opened in another
   window" and does not reconnect (two pages on one seat would otherwise take it from
   each other forever).
3. The server answers `welcome` (`session_id` = run ID, `state` from
   `host.snapshot()`), the pending `preload`, and, during a slot, that `slot` with
   `rejoin=true`. Every later host event follows in `seq` order.
4. The station measures its clock offset (section 2), preloads and hash-checks every
   asset of the round (section 3) and follows the slots (section 4).
5. `pause` shows a neutral pause screen after the current slot locks; `resume` returns
   to the waiting screen; `end` shows the end screen and stops all audio.

Access control. A WebSocket handshake whose `Origin` header names another host gets
HTTP 403 (a page of another site cannot open a station socket); clients that send no
`Origin` (bots, scripts) pass. With `create_panel_app(..., access_secret=secret)` each
seat has a key, `panel.seat_key(secret, rater_id, station)` (HMAC-SHA-256, 32 hex
digits): the station URL carries it as `key`, the page sends it on the WebSocket URL and
on every asset request, and the server refuses a `hello` without the seat's own key
(`E_UNKNOWN_RATER`) and an asset request without any seat's key (HTTP 403). So a device
on the lab network that knows a coded rater ID and a station name cannot take a seat or
download the sounds. The orchestrator should pass a fresh secret per session
(`secrets.token_bytes(32)`, never stored in git) and print the station URLs with
`station_url(..., key=app.state.panel.seat_key(rater_id, station))`; `panel_demo` does
this. Without a secret (tests) no key is needed.

## 2. Clock sync

Stations schedule audio on the server's run clock. A station sends bursts of
`panel.SYNC_BURST` = 8 `sync_request` probes, chained: probe `i + 1` goes out as soon as
the reply to probe `i` arrives, so its `client_ms` is that reply's arrival time. With
`T1` = probe `client_ms`, `T2 = T3` = reply `server_ms` and `T4` = next probe's
`client_ms`:

    offset = T2 - (T1 + T4) / 2        (server minus station clock)
    rtt    = T4 - T1

Each side keeps the sample with the smallest round trip. The station uses its own
estimate (8 samples, exact `T4`); the server computes the same estimate from the probes
alone (`panel.ClockSyncEstimator`, 7 samples) and reports it once per burst with
`host.clock_synced(rater, station, offset_ms, rtt_ms)`. The error of a sample is at most
half its round trip. Stations repeat a burst after every (re)connect and at least every
`panel.SYNC_INTERVAL_MS` = 60 s, so a 50-ppm clock drift adds at most ~3 ms.

The browser maps server time to the audio clock with `AudioContext.getOutputTimestamp()`
(the audio-clock time now leaving the device and its `performance.now()` time), so the
scheduled time is the output time, including the output latency the browser knows of.
Without a usable timestamp it falls back to `currentTime` minus `outputLatency`.

## 3. Assets

Every asset is a canonical WAV identified by `asset_id` = SHA-256 of the file. The
server serves `GET /panel/assets/<asset_id>.wav` from `host.asset_bytes` and refuses
bytes whose SHA-256 differs (HTTP 500), unknown IDs (404) and any other name (404). A
station fetches each asset of a `preload` message once, checks the byte count and the
SHA-256 (`crypto.subtle` on `https`/`localhost`, otherwise the page's own SHA-256, since
plain `http` on the lab network is not a secure context), decodes it and answers
`asset_ready {asset_id, ok}`. An asset that failed its check is never played (it is
fetched once more at its slot; section 4 says what the slot then shows). The host
issues each round's `preload` before the round's first slot (#20: 1 s ahead, as the
candidates exist only after the proposal window); on a LAN the ~18 assets of a round
(about 1.5 MB) load well within that. A slot whose asset is still loading plays as soon
as it is ready, if that is at most `panel.MAX_LATE_START_MS` late.

## 4. Slot timing and no replay

Per slot (`slot` message, times on the server clock):

| Time after `start_server_ms` | Station |
| --- | --- |
| 0 | candidate starts (scheduled on the audio clock); intended meaning shown |
| 2,000 ms | reference starts, if any, and its meaning is shown; without a reference the page says there is no comparison sound and keeps the silence |
| `unlock_offset_ms` | rating controls unlock (host: reference end, or 2,000 ms without a reference) |
| 20,000 ms | controls lock; the station waits for the next slot |

The host publishes each `slot` event ahead of its start (`panel.SLOT_LEAD_MS` = 500 ms,
the #20 default); stations schedule the audio on receipt, so any lead above the network
and processing delay works. About 100 ms after a sound starts, the
station sends `played` with `scheduled_server_ms` (exactly `start_server_ms + offset_ms`)
and `onset_server_ms`: the output time of the scheduled audio-clock time, re-read from
`getOutputTimestamp()` after the start (so a stalled or drifting output device shows up),
plus the clock offset. It is the station's estimate; only a loopback capture (section 9)
measures the sound itself. The server forwards it (`host.report_play`) only if the
slot is known and the asset and scheduled time match the slot (else `error`).

No replay, by construction:

- the page has no replay, seek or volume control, no `<audio>`/`<video>` element and
  no keyboard shortcut; audio is played only by the slot scheduler;
- each `rating_slot_id` is handled at most once per page session and each
  (slot, role) is played at most once; both sets are kept in `sessionStorage`, so a page
  reload never plays a slot again;
- a re-sent `slot` (rejoin, or a host that publishes it twice) is ignored;
- a slot whose candidate onset has passed is not played: by more than
  `panel.ONSET_TOLERANCE_MS` = 50 ms on a rejoin, by more than
  `panel.MAX_LATE_START_MS` = 1 s otherwise (a late `slot` message). Such a slot shows a
  neutral "connected again" screen with no audio and no controls, so its rating is
  missing;
- the server never asks a station to play: it has no play message.

A sound that did not play cannot be rated. The sounds a rater must hear are
`panel.required_plays(slot)`: the candidate, and the reference when the slot asks for
distinguishability. If one of them does not start (its asset failed the fetch or the
hash check, it would start more than `MAX_LATE_START_MS` late, it has not started
`MAX_LATE_START_MS` after its onset because the fetch hangs, the audio context is not
running, or the audio clock has not passed the onset when the station checks 100 ms
later), the slot turns into a neutral "could not be played" screen with no controls,
before the unlock; the sounds of the slot that have not started are cancelled (the
reference is scheduled only after the candidate) and the rating is missing. A `played`
report is sent only for a sound whose onset the audio clock passed. The server enforces
the same rule: it refuses a rating (`E_PROTOCOL`) from a station that has not reported
`played` (accepted by the host) for every sound of `required_plays`, so a station that
skips the rule still cannot store a rating of a sound it did not play.

Reconnects: if only the socket drops (the page keeps its state), the page keeps playing
the slot as scheduled (no second start of anything already started) and the rater may
still rate before the lock; the record gets `reconnected=true`. If the page lost its
state (reload or new browser), the slot is skipped as above and its rating is stored as
missing with `reconnected=true`. `played` and `asset_ready` messages produced while the
socket is down are sent after the next `welcome` (before any rating, so the server has
the plays first); ratings are not queued.

## 5. Ratings

Controls: association 1-7, distinguishability 1-7 (hidden on first-atom slots), comfort
Acceptable/Unacceptable, then **Submit** (enabled when every asked judgment is chosen).
One submission per slot; there is no free-text field. `rt_ms` is the submit time minus
the unlock time. The `rating_ack` goes only to the station that rated: ratings stay
private.

The server refuses, before the host sees anything:

| Code | Rule |
| --- | --- |
| `E_PROTOCOL` | the frame is not a schema-valid station message (values outside 1-7, non-integers, extra fields such as free text, binary frames, frames above `panel.MAX_FRAME_BYTES` = 4 KiB, which also close the socket), or an integer field holds a JSON number with a fraction part of zero such as `5.0` (the schema's `integer` allows it; the server and the #20 host do not) |
| `E_UNKNOWN_SLOT` | the rating names a slot the session never scheduled |
| `E_SLOT_CLOSED` | received at or after the lock (`start + 20,000 ms`) |
| `E_PLACEHOLDER` | the slot is a placeholder (invalid candidate) |
| `E_LOCKED` | received before the controls unlock (`start + unlock_offset_ms`) |
| `E_FIRST_ATOM` | distinguishability given on a first-atom slot |
| `E_PROTOCOL` | distinguishability missing on any other slot |
| `E_PROTOCOL` | the station has not reported `played` for every sound of `panel.required_plays` (section 4) |
| `E_DUPLICATE_RATING` | this station already has an accepted rating for the slot |

The checks run in this order (the same order and codes as the #20 host, which has no
play check of its own). `panel.rating_refusal(slot, submission)` implements the slot
rules (closed, placeholder, locked, first atom, values: only `int` values, so `5.0` and
`True` are refused also when a host calls it directly); the host has the last word
(`host.submit_rating`). The first-atom value 4 is stored by the host, never sent by a
station.

## 6. What the host (#20) provides and records

The server keeps no log. The host (`panel_session.PanelSessionHost`, #20; `ScriptedPanelHost`
is a reference implementation) must:

- issue `preload`, `slot`, `pause`, `resume` and `end` events in `seq` order, `slot` events
  ahead of the slot start, `preload` before the round's first slot;
- set `unlock_offset_ms` = 2,000 + the reference duration (rounded up to the ms), or
  2,000 without a reference;
- accept only schema-checked submissions inside the window (`rating_refusal`), one per
  seat and slot;
- at each slot's lock write one `RatingRecord` per seat: the submission, a placeholder
  record, or a missing record; `distinguishability = 4` with
  `distinguishability_by_rule = true` on rated first-atom slots; `reconnected = true`
  when the station joined again during the slot; `candidate_onset_ms` and
  `reference_onset_ms` from the station's `played` reports (ms after the slot start);
- write one `play` record per `played` report (`rating_candidate`/`rating_reference`,
  `scheduled_ms`, `onset_ms`) and the panel `timing` events (`station_connect`,
  `station_reconnect`, `station_disconnect`, `clock_sync`, `asset_ready`,
  `rater_withdrawal`).

## 7. Withdrawal

**Stop taking part** opens a dialog with four reasons (`rater_request`, `discomfort`,
`technical`, `other`). Confirming shows the neutral withdrawn screen at once, stops all
audio and sends `withdraw`; the server calls `host.report_withdrawal`, answers
`end withdrawn` and closes the socket. If the socket is down when the rater confirms
(or drops before the answer), the page keeps the withdrawal and keeps reconnecting until
the server has answered it: after the next `welcome` it sends the queued `played`
reports and then `withdraw`, ignores every other message, and stops on the server's
`end withdrawn` (or on `end withdrawn` in reply to its `hello`, when the server already
has the withdrawal). So the host always learns of the withdrawal. A later `hello` from
the same seat gets `end withdrawn` again (before any `welcome`), which shows the
withdrawn screen. The host keeps every record and marks the batch incomplete (#20).

The #20 host ends the session when a rater withdraws and broadcasts `end withdrawn` to
every station. A station shows its withdrawn screen only for its own withdrawal or for
`end withdrawn` in reply to its `hello`; a broadcast `end withdrawn` after `welcome`
shows the normal end screen.

## 8. Bot rater (#22)

`BotRater(base_url, *, rater_id, station, run_id, policy, clock=None, access_key=None)
.run() -> BotRunResult` speaks the same protocol as the page: `hello` with `kind="bot"`,
the same sync bursts, preload with hash check, `played` at the scheduled onsets (no audio
output) and one rating per rateable slot at `unlock + rt`, only if it played every sound
of `required_plays` (an asset that failed its check leaves the rating missing). A
withdrawal is sent again after a reconnect until the server's `end`; a socket closed with
`REPLACED_CLOSE_CODE` ends the run (`errors` has `E_REPLACED`). `access_key` is the seat
key when the server has an access secret. `BotRunResult.end_reason` is the reason of the
server's `end` (`withdrawn` also when another rater withdrew and #20 ended the session);
`BotRunResult.withdrawn` says whether this bot withdrew. Judgments come from
`bot_rating(run_id, rater_id, rating_slot_id, *, ask_distinguishability, policy)`, drawn
from `rng_for(bot_seed_key(run_id, rater_id, "rating", rating_slot_id))` in a fixed order
(missing, association, distinguishability, comfort, response time), so they do not depend
on timing.

`BotRatingPolicy` fields: `p_comfort_acceptable` (0.9), `force_unacceptable_slots`
(rating-slot IDs from `BatchConfig.rating_slot_ids`, for zero-eligible injections),
`p_missing`, `drop_slots` (the socket drops 0.5 s after the candidate onset and
reconnects 1 s later; that slot's rating is missing), `withdraw_at` (withdraw at the
unlock of that slot) and `rt_ms_range` (400-4,000 ms). For accelerated runs pass a clock
with the server's speed (for example the server's `ScaledClock` itself); at high speeds
the logged onsets of bots lag by a few real milliseconds times the speed.

## 9. Three stations: setup, skew measurement, screen recording

Station setup (O1.3.6): three matched computers with the same browser (current Chrome
or Chromium), identical closed-back headphones and the same output device settings and
gain; system sounds and notifications off; the browser in kiosk or full-screen mode; the
panel server on the lab network (`--host 0.0.0.0`). Each station opens its own URL.

DEMO session (synthetic assets, DEMO batch config and meanings; never study material):

    uv run --project generation python -m av_generation.panel_demo \
        --host 0.0.0.0 --port 8765 --public-url http://<server-ip>:8765 \
        --rounds 4 --atom-index 5 --run-id DEMO-panel-skew-01 --out-dir generation/out/panel

It prints the three station URLs (with seat keys from a fresh access secret per run, so
only these URLs open a seat), starts the 36 consecutive slots (one atom: 4 rounds x 9)
20 s after the last station joins and writes `logs/plays.jsonl`, `logs/ratings.jsonl`,
`logs/timing.jsonl` and `panel-schedule.json` under `--out-dir/<run-id>/` (ignored by git).
`--bots` seats bot raters instead (no browsers); `--placeholder R:P` adds placeholders.

**Loopback skew measurement** (audio loopback kit, O1.3.4): split each station's headphone
output (one branch to the headphones, one to the recorder) into one input of a single
multichannel audio interface (one sample clock for all three), channel 1 = S1, 2 = S2,
3 = S3. Record the whole 36-slot session to one multichannel WAV (PCM 16/24/32-bit or
float; any rate >= 8 kHz). Then:

    uv run --project generation python -m av_generation.panel_skew loopback \
        --capture capture.wav --channels S1,S2,S3 \
        --schedule generation/out/panel/DEMO-panel-skew-01/panel-schedule.json --out <dir>
    uv run --project generation python -m av_generation.panel_skew logged \
        --plays generation/out/panel/DEMO-panel-skew-01/logs/plays.jsonl \
        --schedule generation/out/panel/DEMO-panel-skew-01/panel-schedule.json --out <dir>

`loopback-skew.csv` has one row per scheduled sound: the onset of each station on the
server clock, `skew_ms` (max minus min), `max_abs_dev_ms` and pass flags.
`loopback-skew-summary.json` gives the maximum, p95 and mean skew, the longest run of
consecutive slots within 100 ms (`pass_skew_36_slots`), the reference-interval error per
station (|reference onset - candidate onset - 2,000 ms|) and the fit of the recorder clock
to the server clock (offset and ppm). Onset detection: first sample above the larger of
-30 dB of the channel peak and 4x the noise floor, after at least 300 ms below it.
`logged-onsets.csv` / `-summary.json` give the same table from the stations' `played`
reports (criterion: every onset within 50 ms of its scheduled time).

Software check committed with this component: `generation/runs/DEMO-panel-onsets-01/`
holds `logged-onsets.csv` and its summary for the 36-slot DEMO session played by three
headless Chrome stations on one computer (`--rounds 4 --atom-index 5`). These are the
stations' own onset estimates (section 4), not an acoustic measurement: every one of the
72 sounds was within 5 ms of its scheduled time and the station-to-station skew was at
most 6 ms. The loopback capture of real stations is still to be done.

**Screen recording of one round**: run the DEMO session with `--rounds 1
--placeholder 1:5`, record the three station screens (one recorder per station or one
capture of three windows) from Start to the end screen, and keep the recording with the
run outside git.

## 10. Tests

- `tests/generation/test_rater_client.py`: rating rules (property test), server
  messages (property test against the schema and masking), clock-sync error bound
  (property test), page/static files/CSP and masking, assets by hash, hello and seat
  checks, invalid ratings (also `5.0`, `True`) and free text never forwarded, binary and
  oversized frames, join snapshot and socket replacement (close code 4001), rating
  window, privacy and records, the first-atom rule, `played` checks, ratings refused
  without the station's reported plays, cross-site handshakes and seat keys (WebSocket and
  assets), withdrawal, event order, the scripted host and its logs, the DEMO session,
  three bot raters over one round (drop and reconnect, forced unacceptable, missing,
  re-published slot), bots that rate only what they played, a bot withdrawal during a
  socket drop, a replaced bot that stops, a first-atom round with a withdrawal, and both
  command lines.
- `tests/generation/test_rater_station_browser.py` (`-m browser`, Chromium in CI): the
  page's SHA-256 against `hashlib`; one round on three stations (controls locked before
  the unlock and after 20 s, each sound started once despite keys, clicks and a
  re-sent slot, placeholder screen, logged onsets within 50 ms and skew within 100 ms,
  private acks, stored ratings); the first-atom slot; a candidate and a reference that
  fail to load (neutral screen, controls locked, ratings missing); a socket drop, a
  reload and a fresh browser mid-slot without replay; a withdrawal during a socket drop
  (reaches the host; the other station shows the normal end after the broadcast
  `end withdrawn`; the withdrawn seat opened again shows the withdrawn screen); two pages
  on one seat (the older one stops); setup, pause, withdrawal and an unknown station.
- `tests/generation/test_panel_skew.py`: WAV formats, onset detection (property test),
  recorder-clock alignment, the 36-slot skew criterion on synthetic captures, and the
  command lines.

## 11. Decisions

| Decision | Rationale |
| --- | --- |
| Clock sync with chained probe bursts, min-RTT sample, every 60 s and on reconnect | the protocol has no station-to-server offset message; chaining gives the server `T4`, so the host logs the same estimate the station uses |
| Audio scheduled on the audio clock via `getOutputTimestamp` | scheduling at the output time removes the output latency the browser knows; timers are not precise enough |
| A rejoin past the candidate onset skips the whole slot; a socket blip with page state continues | a rater who missed the candidate cannot rate it; a rater who heard everything keeps a valid rating; both are `reconnected` |
| A slot whose candidate (or asked-for reference) did not start becomes neutral; the server refuses a rating without the station's `played` reports | the same rule for every way a sound can fail (asset, lateness, stopped audio clock); the server check also holds against a station that does not follow it |
| A withdrawal is kept and resent after reconnects until the server answers | a socket drop must not lose it: the host must mark the batch incomplete |
| Replaced sockets closed with 4001, no reconnect on it | otherwise two pages on one seat replace each other forever |
| Same-origin check and optional seat keys (HMAC of a per-session secret) | a device on the lab network or a page of another site cannot take a seat or fetch the sounds; tests need no key |
| Slots and plays remembered in `sessionStorage` | a reload can never play a slot again |
| Explicit Submit button, one submission | the rater confirms; an unsubmitted choice is missing, as the issue states |
| `E_LOCKED` before the unlock, `E_SLOT_CLOSED` from 20 s; check order and codes as in the #20 host | two different situations, one code each; server and host answer every case alike |
| Server pre-checks and the host decides | invalid frames and free text never reach the host; the host stays authoritative |
| Pause takes effect after the current slot locks | the slot already started; each sound plays once and the slot keeps its 20 s |
| Own SHA-256 in the page | `crypto.subtle` is missing on plain-http lab networks |
| CSP `script-src 'self'` and no inline code | enforces "no third-party JS" in the browser |
