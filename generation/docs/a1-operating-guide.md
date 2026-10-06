# A1 designer interface: operating guide

A short guide for hand designers and the generation operator (handoff to O1.2.4). The
technical reference is [`a1-interface.md`](a1-interface.md).

## For designers

### The screen

- **Top:** the atom and its meaning, the round (1 to 4), the profile and its base
  frequency, the slots used for this atom (12 in total) and the time left in the round.
- **Slots of this round:** three slots. Each one is not opened, open (with a countdown),
  or closed with its result.
- **Recipe:** the parameter form. It unlocks only while a slot is open. Every control
  offers only allowed values: total duration 450/600/750/900 ms, three pitches from -6
  to +6 semitones, three rhythm weights 1 to 4, two gaps 20/40/60 ms, three amplitudes
  0.6/0.8/1.0. The schematic under the form shows the timing and pitch contour of the
  values you entered. It makes no sound and does not check the recipe.
- **Feedback: your book only:** after each round, your candidates with their recipes,
  technical status, the panel's ratings (association / distinguishability / comfort, one
  line per rater), eligibility, score and the current incumbent. **Load** copies one of
  your recipes into the form while a slot is open.
- **Committed atoms of your book:** the recipes already committed, as numbers. They
  cannot be played.
- **Interface familiarization:** start and end your familiarization time here when the
  operator asks you to.

### A round

1. When the round starts, read the feedback and decide on your next candidates.
2. Press **Open next slot**. The slot's 40-second countdown starts. The round clock
   keeps running between slots, so open the next slot when you are ready to work on it.
3. Set the recipe and press **Submit and play**. The recipe is checked, logged and, if
   it is valid, played once. If it is not valid, the screen shows why. Either way the
   slot is used and closed.
4. If the countdown reaches zero before you submit, the slot closes as a timeout.
5. After three slots the round is closed. The next round brings the panel's feedback.

### Rules

- One recipe per slot. A submitted recipe cannot be changed, and a slot never reopens.
- Each valid recipe plays once, right after you submit it. There is no replay, and you
  cannot play committed atoms, other meanings or anything else.
- Do not use other tools to make or hear sounds, do not use a generative model, and do
  not ask other people for feedback.
- The common selector, not you, chooses the committed motif from the panel's scores.

## For the operator

### Practice session (training)

From the repository root on the practice host:

```bash
uv run --project generation python -m av_generation._a1_cli practice \
    --runs-root <restricted runs directory> --run-id PRACTICE-D1-01 --designer D1 \
    --atoms K-a1,K-r1 --rounds 4 --station S9
```

Open `http://127.0.0.1:8741/a1/` in the kiosk browser. The PRACTICE banner must be
visible. Practice uses non-study meanings (`--meanings <dir>` selects the set; default:
the synthetic DEMO set), shows technical feedback only, and stores everything in its own
practice run. Nothing from it enters a book. Stop the server with Ctrl-C when the
designer has finished.

### Study session

The round orchestrator starts the A1 server for the batch. Open the URL it prints in the
kiosk browser of the designer's station. Check that no PRACTICE banner is shown and that
the run's designer ID is the designer present.

### Kiosk checks before every session

- The browser runs in kiosk mode with the managed policy (`a1-interface.md`, section 8):
  no address bar, no developer tools, no other site.
- The headphones are connected and the OS volume matches the station log.
- The countdowns move, and **Open next slot** becomes active when a round starts.

### Screen recording (evidence, human task)

Record one full round once, on the kiosk, with a synthetic practice run:

```bash
uv run --project generation python -m av_generation._a1_cli practice \
    --runs-root <scratch directory> --run-id DEMO-A1-REC-01 --designer D0 \
    --atoms K-a1 --rounds 1 --demo
```

Start the OS screen recorder (macOS: Shift-Command-5; Windows: Win+Alt+R or OBS; Linux:
OBS or the desktop recorder), open `http://127.0.0.1:8741/a1/`, then complete one round:
open slot 1 and submit a valid recipe (it plays once), open slot 2 and submit a recipe
with a very short event (for example 450 ms, gaps 60/60, weights 4/1/1: it shows
`event_too_short`), open slot 3 and let it time out. Attach the video to the issue; do
not commit it (binary files are not allowed in the repository).

### Troubleshooting

| Symptom | Action |
| --- | --- |
| "Connection to the server lost" | Check the network or the server process; the page recovers by itself and nothing is lost: slots and timers live on the server. |
| A valid recipe did not play | The play is logged when the server sends the sound. Note the slot ID in the station log; do not try to replay it. |
| The page was reloaded right after a submit | If the sound was not fetched yet, the slot shows **Play once**; it plays once. |
| A slot shows "refused by the ledger" | The atom's 12 slots are already used (for example after a resumed batch); report it to the generation operator. |
