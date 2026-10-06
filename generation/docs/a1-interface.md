# A1 hand-designer interface (#19)

The web interface in which A1 hand designers author candidates inside the shared slot
budget (Study A protocol §3.3 and §3.4). The server owns every rule; the page only
offers constrained controls and plays what the server hands out, once.

Designer and operator instructions: [`a1-operating-guide.md`](a1-operating-guide.md).
Cross-team summary: [`docs/interfaces/generation.md`](../../docs/interfaces/generation.md)
(section "A1 hand-designer interface").

## 1. Components

| Path | Contents |
| --- | --- |
| `src/av_generation/a1.py` | `A1SlotService` (the A1 `RoundProposer` and the API state), `create_a1_app`, `ROUTES`, `A1Error`, `is_practice_batch`, `recipe_text` |
| `src/av_generation/_a1_app.py` | The FastAPI app (`build_app`): routes, strict JSON bodies, security headers |
| `src/av_generation/web/a1/` | `index.html`, `a1.css`, `a1.js`: the designer screen (plain HTML/CSS/JS, no third-party code) |
| `src/av_generation/_a1_practice.py` | Practice mode: `open_practice_session`, `PracticeSession.run`, practice IDs and labels |
| `src/av_generation/_a1_cli.py` | `python -m av_generation._a1_cli practice ...` (training sessions) |
| `examples/demo-practice-meanings/` | Synthetic non-study practice texts (`DEMO-practice-meanings-01`) |
| `kiosk/a1-chrome-policy.json` | Managed browser policy for the kiosk (section 8) |
| `../tests/generation/test_a1_api.py` | API, timer, ledger, audio-token, practice, sentinel, 48-slot and browser tests |

## 2. Round and slot flow

The round orchestrator (#20) calls `A1SlotService.propose_round(request)` for each
round's proposal window; the call blocks until the window's three slots have closed
and returns their `SlotRecord`s. Meanwhile the designer's kiosk page talks to the app
from `create_a1_app(service)`:

1. The window opens: the page shows the atom's meaning (shared `MeaningSet`), the round,
   the profile, the slots used for the atom and the window's time left.
2. **Open next slot** (`POST /a1/api/slots/open`) reserves the slot in the ledger
   (`SlotLedger.reserve`, #17) before anything is designed or heard, and starts the
   server timer: the slot's deadline is 40 s after opening (`SLOT_CAP_MS`), or the
   window's end if that comes first. Slots open in order, one at a time.
3. The form unlocks. The designer sets the recipe with radio groups and range inputs that
   only hold domain values; the schematic shows the entered timing and pitch contour.
4. **Submit and play** (`POST /a1/api/slots/{slot_id}/submit`) validates and renders the
   recipe (`av_sound.validate` against the book's committed references and the book's
   threshold), maps the result to a slot outcome (`outcomes`), and consumes the slot with
   exactly one `SlotRecord` (`SlotLedger.consume`). A valid recipe gets one single-use
   audio token; the page fetches it at once and plays the waveform once through Web
   Audio. An invalid recipe shows its outcome and validator messages; nothing plays.
5. No submission by the deadline: the slot closes as `timeout`, with `t_ms` equal to the
   deadline. Slots never opened close as `timeout` at the window's end.
6. When all three slots are closed the window closes and `propose_round` returns. The
   next round's request brings the feedback for the rounds closed so far.

Rules and where they are enforced:

| Rule | Enforcement | Test |
| --- | --- | --- |
| A slot closes on submit or at its deadline and never reopens | server timer (checked before every API call and every 10 ms while a window is open); a closed slot only answers `E_SLOT_CLOSED` | `test_slot_times_out_at_40_s_server_time`, `test_slot_auto_closes_in_real_time_within_tolerance` |
| One submitted recipe per slot; no edit after submit | second submit refused and logged (`slot_refusal`, `slot_closed`); no PUT/PATCH/DELETE route | `test_submitted_recipe_cannot_be_edited` |
| At most one audition per slot; a preview is a submitted slot | tokens only for consumed valid slots; first request serves and logs `played`, later ones are refused and logged | `test_second_audio_request_is_refused_and_logged`, `test_48_slot_session_has_no_play_without_a_consumed_slot`, `test_every_play_joins_one_consumed_valid_slot` |
| 12 slots per atom | ledger cap; a request after the atom's 12 slots is refused and logged (`slot_cap`) | `test_13th_slot_request_for_an_atom_is_refused`, `test_resumed_atom_at_its_cap_refuses_every_slot` |
| Out-of-domain values cannot be entered | controls built from the server's domain; the server validates anyway | `test_browser_controls_only_allow_domain_values`, `test_out_of_domain_and_malformed_submissions` |
| No audio of committed atoms, other meanings or other methods | the only audio route is the token route; the book view has no audio; requests naming another book are refused | `test_feedback_payload_holds_no_other_method_fields`, `test_requests_naming_another_book_are_refused` |
| Own book only | payloads are built from the request's own book state and feedback; the service never reads other books' ledger records | `test_feedback_payload_holds_no_other_method_fields` (sentinel) |

## 3. HTTP API

All bodies are JSON (`api_version` 1). Request bodies are strict JSON objects of at most
64 KiB; a malformed body is a client error (`400 E_BAD_REQUEST`, `413 E_TOO_LARGE`) and
consumes nothing. Errors are `{"error": {"code": "E_...", "message": "..."}}`. Every
response carries `Cache-Control: no-store`, a same-origin Content-Security-Policy,
`X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and
`X-Frame-Options: DENY`. Times are run-clock milliseconds.

| Route | Request | Response (200) | Errors |
| --- | --- | --- | --- |
| `GET /a1/` | - | the page (`/a1/static/a1.js`, `/a1/static/a1.css`) | - |
| `GET /a1/api/state` | - | `mode` (`study`/`practice`), `server_ms`, `slot_cap_ms`, `slots_per_round`, `domain` (allowed values per field), `active`, `familiarization` (`running`, `total_ms`), `window` (below) or `null` | - |
| `POST /a1/api/slots/open` | none | the slot view (below) | `409 E_NO_WINDOW`, `E_SLOT_OPEN`, `E_SLOT_CAP` (logged), `E_SLOT_REUSED` (logged by the ledger) |
| `POST /a1/api/slots/{slot_id}/submit` | `{"recipe": <JSON value>}` (an object, or JSON text) | `slot_id`, `slot`, `round`, `outcome`, `valid`, `validator_codes`, `validator_messages`, `recipe`, `t_open_ms`, `t_ms`, `audio` (`{token, url, expires_ms}` or `null`) | `404 E_UNKNOWN_SLOT`, `409 E_SLOT_CLOSED` (logged), `400`, `413` |
| `GET /a1/api/audio/{token}` | - | `audio/wav` (canonical 48-kHz WAV), once | `404 E_UNKNOWN_TOKEN`, `410 E_TOKEN_USED` (logged), `410 E_TOKEN_EXPIRED` (logged) |
| `GET /a1/api/feedback` | - | `available`, `practice`, `ratings_shown`, `atom_id`, `round`, `rounds_closed`, `candidates` (below), `incumbent_slot_id`, `incumbent_score` (`p/q`), `current_round` (this round's closed slots: `slot_id`, `round`, `slot`, `slot_index`, `recipe`, `outcome`, `validator_codes`) | - |
| `GET /a1/api/book` | - | `available`, `practice`, `profile`, `f0_hz`, `threshold`, `playback: false`, `committed` (`atom_id`, `commit_index`, `label`, `meaning`, `recipe`) | - |
| `POST /a1/api/activity` | `{"kind": "active" \| "idle" \| "familiarization_start" \| "familiarization_end"}` | `active`, `familiarization` | `400 E_BAD_ACTIVITY`, `409 E_SLOT_OPEN` (familiarization during a slot) |

`window`: `open`, `atom_id`, `round`, `label` (`null` in practice), `meaning`, `profile`,
`f0_hz`, `window_end_ms`, `window_remaining_ms`, `atom_slots_used`, `atom_slots_cap`,
`slots` (three slot views). Slot view: `slot`, `slot_id`, `state` (`unopened`, `open`,
`closed`, `refused`), `t_open_ms`, `deadline_ms`, `remaining_ms`, `t_close_ms`,
`outcome`, `audio` (an unused, unexpired token, so a reloaded page can still play it
once; else `null`).

Feedback `candidates` (the request's `AtomFeedback`, closed rounds only): `slot_id`,
`round`, `slot`, `slot_index`, `recipe`, `outcome`, `validator_codes`, `ratings`
(`association`, `distinguishability`, `comfort` per rater, without rater identity),
`eligible`, `score` (`p/q`), `score_value`, `incumbent`. No payload carries a method,
designer ID, seed, prompt or model field, or another book's data.

The in-process API (`A1SlotService.state()`, `open_slot()`, `submit(slot_id, recipe)`,
`audio(token)`, `feedback()`, `book()`, `activity(kind)`, `tick()`) returns the same
dicts and raises `A1Error(code, status, message)`.

## 4. Logs

All records go through the run's writers and validate against their schemas.

| Record | When | A1 specifics |
| --- | --- | --- |
| `slot` (ledger) | every closed slot | `method A1`, `designer_id`, `practice`, `latency_ms` (open to submit; `null` for a timeout), `design_ms` (section 5), `raw_output` (the submitted value: a string as given, anything else as compact sorted JSON), `recipe`, `recipe_sha256`, `validator_codes`/`messages`, `pcm_sha256` and `file_sha256` whenever the render is usable; `seed_key`/`seed` stay `null` (a human designer draws no random numbers) |
| `slot_refusal` | a submit to a closed slot (`slot_closed`), an open request after the atom's 12 slots (`slot_cap`, `requested` `<book>.<atom>.slot13`); the ledger logs its own cap and reuse refusals | default file: `slot-refusals.jsonl` next to the plays log |
| `play` | every audio request for a known token | `context` `a1_preview` (study) or `a1_practice`, `audio_kind atom`, `asset_id` = WAV file SHA-256, `pcm_sha256`, `slot_id`, `token_id`, `actor_id` (designer), `station`; `result played` once, then `refused` with `E_TOKEN_USED` / `E_TOKEN_EXPIRED` |
| `timing` | familiarization and design activity; practice sessions | `familiarization_start` / `familiarization_end` (`duration_ms`), `design_active_start` / `design_active_end` (`duration_ms`, `detail` `slot <id>`), practice `session_start` / `session_end`; `component a1`, `actor_id` the designer |

Join rule (acceptance): every `play` with `result played` has a `slot` record with the
same `slot_id`, outcome `valid`, the same `pcm_sha256` and `file_sha256 == asset_id`,
and no slot has two `played` events.

## 5. Design time and familiarization

- Active design time of a slot (`design_ms`) is the part of the slot's open interval in
  which the page was active: focused, visible, and with input in the last 15 s. The page
  reports changes with `POST /a1/api/activity` (`active` / `idle`); opening a slot counts
  as activity. Each active interval inside a slot is logged as `design_active_start` /
  `design_active_end`.
- Familiarization time: the designer starts and ends it on the page (or the operator
  asks them to); opening a slot ends a running interval. Each interval is logged with its
  `duration_ms`; `A1SlotService.familiarization_ms` is the total. Events before the
  first round are buffered until the run ID is known.

## 6. Practice mode

`practice=True` services take only practice batches (a `PRACTICE` token in the batch
ID) and study services refuse them, so practice records never carry a study batch or
book. `open_practice_session(runs_root, run_id, *, designer_id, meanings, clock, ...)`
creates a separate run directory with `RunManifest.purpose = "practice"`:

- kind `practice` (real training): non-`DEMO-` run ID, outside any git work tree
  (restricted storage, `rundir` policy); batch `PRACTICE`, book `BK-PRACTICE`;
- kind `demo` (synthetic trials, CI): `DEMO-` run ID; batch `DEMO-PRACTICE`, book
  `DEMO-BK-PRACTICE`.

Records carry `practice=true` and plays the context `a1_practice`; nothing is committed
to a store. `PracticeSession.run(atoms, rounds=4)` drives the study's round and slot
budget without a panel: feedback is technical only (no ratings, eligibility or
incumbent), and the last valid candidate of each practice atom becomes a practice
reference for the next atoms (so duplicate and separation checks behave as in a book).
The page shows a PRACTICE banner and practice meaning texts only (no study labels).

Practice meanings are a `MeaningSet` with non-study texts: the synthetic
`examples/demo-practice-meanings/`, or a set supplied by the training owner (O1.2.4).

## 7. Serving the app

Study mode is started by the orchestrator (#20), which owns the batch run:

```python
service = A1SlotService(
    ledger, plays, timing, clock=clock, designer_id="D1", meanings=meanings, station="S9"
)
with serve_in_thread(create_a1_app(service), host="<lab interface>", port=8741):
    orchestrator.run_appointment(...)  # calls service.propose_round(request)
```

Practice mode (training, O1.2.4) from the repository root:

```bash
uv run --project generation python -m av_generation._a1_cli practice \
    --runs-root <restricted runs directory> --run-id PRACTICE-D1-01 --designer D1 \
    --atoms K-a1,K-r1 --rounds 4 --host 127.0.0.1 --port 8741 --station S9
```

`--meanings <dir>` selects a practice meaning set (default: the DEMO set); `--demo`
makes a synthetic `DEMO-` run that may live anywhere.

## 8. Kiosk and lab machine

Hardware and room:

- One dedicated computer per designer station, wired to the lab network (or running the
  practice server itself on `127.0.0.1`), with a fixed display (at least 1366 x 768).
- Closed-back headphones; proposed: the same model as the rater stations (O1.3.6). Set
  the OS output volume once with the calibration signal, then lock it (no volume keys
  in the kiosk account) and record the setting in the station log.
- No other audio source, no speakers, no phone use at the station: unlogged listening is
  not allowed.

Operating system:

- A local standard account used only for the kiosk, signed in automatically; no admin
  rights, no other applications at login. Use the OS's single-app or assigned-access
  feature where available (Windows Assigned Access or Shell Launcher, macOS managed
  login items with a restricted account, a Linux session that starts only the browser).
- Disable sleep and screen lock during sessions, notifications and automatic updates.

Browser (Google Chrome or Chromium):

1. Install the managed policy `generation/kiosk/a1-chrome-policy.json` after replacing
   the allowlist and start URLs with the A1 server's address
   (`http://<host>:8741/a1/`):
   - Linux: copy it to `/etc/opt/chrome/policies/managed/a1.json`
     (Chromium: `/etc/chromium/policies/managed/a1.json`);
   - Windows: set the same keys under `HKLM\Software\Policies\Google\Chrome` (Group
     Policy with the Chrome ADMX templates; list policies as numbered values, e.g.
     `URLBlocklist\1 = *`);
   - macOS: deploy the keys for `com.google.Chrome` as a configuration profile.

   The policy disables developer tools (no replay through the console), downloads,
   printing, extensions, incognito and guest mode, sign-in, sync, history and
   translation, blocks every URL except the A1 page, and opens the A1 page at start.
2. Launch in kiosk mode with a dedicated profile, e.g.
   `google-chrome --kiosk --app=http://<host>:8741/a1/ --user-data-dir=<kiosk profile> --no-first-run --noerrdialogs --overscroll-history-navigation=0`
   (Windows: `chrome.exe` with the same flags; macOS:
   `open -na "Google Chrome" --args --kiosk --app=...`).
3. Check `chrome://policy` once after installing (from an admin account): every key is
   listed as Mandatory with no error.

Network: the A1 app binds to the lab interface only; the host firewall admits the
designer station's address on port 8741 and nothing else. Nothing needs the internet.

Before each session (operator): the station shows the A1 page with the right mode
(PRACTICE banner only for training), the headphones are connected, the volume setting
matches the log, the countdowns move, and the designer ID shown in the run manifest is
the designer present.

## 9. Evidence and how to reproduce it

- API and service tests: `uv run --project generation pytest --import-mode=importlib -p no:cacheprovider tests/generation/test_a1_api.py -m "not browser"`.
- Browser tests (Chromium): `uv run --project generation pytest --import-mode=importlib -p no:cacheprovider tests/generation/test_a1_api.py -m browser`
  (CI job `browser`; locally `AV_GENERATION_BROWSER_CHANNEL=chrome` uses an installed
  Chrome).
- 48-slot session: `test_48_slot_session_has_no_play_without_a_consumed_slot` drives 4
  atoms x 4 rounds x 3 slots through the HTTP API with a scripted designer (valid,
  replayed, never-played, invalid, out-of-domain, edited, timed-out and skipped slots)
  and joins the play log to the slot ledger. In CI (or with
  `AV_GENERATION_CI_OUT=<dir>`) it writes the logs and `join-summary.json` to
  `generation/out/ci/a1-session/DEMO-A1-SESSION-48/` (CI artifact
  `generation-ci-<os>`).
- Browser round recording: in CI the full-round browser test records a video and a
  screenshot of the feedback view to `generation/out/ci/a1-browser/` (artifact
  `generation-ci-browser`). A screen recording of a person doing a full round in the
  kiosk is a human task (operating guide, "Screen recording").

## 10. Decisions

| Decision | Rationale |
| --- | --- |
| The slot deadline is 40 s after opening or the window's end, whichever is first; unopened slots time out at the window's end | the window (`RoundRequest.window_end_ms`, 3 x 40 s) bounds every method's proposal time; the designer opens slots when ready |
| The timeout record's `t_ms` is the deadline; a submit at or after the deadline is refused | timeouts are exact in server time whatever the polling delay |
| A play is logged when the server hands out the WAV (`result played`) | the server cannot observe the headphones; serving is the last point it controls, so no audition can go unlogged |
| Audio tokens expire 60 s after the submit | the page plays at once; an unused token cannot be kept for later |
| A transport-level malformed body consumes nothing; any `recipe` value consumes the slot | a broken request is not a submitted recipe; everything that reaches the validator is |
| No admissibility hints before submit; the schematic shows only the entered parameters | validation feedback is the slot's technical status, as for the other methods |
| A submit to a closed slot and a request after 12 slots are logged as `slot_refusal` | refused edits and over-budget requests stay visible in the audit |
| Optional constructor keywords (`refusals`, `station`, `run_id`, `token_factory`, `audio_ttl_ms`, `poll_interval_s`) | backwards compatible with the skeleton signature used by #20 and #22 |
| Practice batches carry a `PRACTICE` token and practice runs have their own directory | practice data can never be mistaken for, or written into, a study book |
