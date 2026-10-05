# AV Sound Demo (macOS)

A native macOS app to try the sound engine (`av_sound`) by ear and by hash. The app does
not reimplement the engine. It starts the real Python engine as a child process (the
bridge) and sends it one JSON request per line; see [`PROTOCOL.md`](PROTOCOL.md) and
[`bridge/README.md`](bridge/README.md). The app plays audio only after the WAV bytes match
both the file hash and the waveform hash that the engine reports.

The package also holds `AVSoundSpec`, an independent Swift port of the
[renderer spec](../../docs/renderer-spec.md) that uses only integer arithmetic. The app
uses it to check that Swift and Python produce the same bytes for the same recipe.

| Path | Contents |
| --- | --- |
| `AVSoundDemo/` | Swift package: the app (`AVSoundDemo`), the bridge client (`AVSoundDemoCore`), the spec port (`AVSoundSpec`) and their tests (`AVSoundDemoCoreTests`, `AVSoundSpecTests`, `AVSoundDemoAppTests`) |
| `bridge/av_sound_bridge.py` | The bridge (standard library and `av_sound` only) |
| `PROTOCOL.md` | The contract between the bridge and the app |
| `build-app.sh` | Builds `AV Sound Demo.app` |

## Requirements

- macOS 15 or later.
- Xcode 16 or later (Swift 6 toolchain). The app is tested with Xcode 27.1 (Swift 6.4).
- [uv](https://docs.astral.sh/uv/).
- A checkout of this repository. The app runs the engine from the checkout.

Nothing needs the network after `uv sync`. The Swift package has no dependencies.

## Build and run

From the repository root:

```bash
uv sync --project sound --locked     # once: the engine's Python environment
sound/demo/macos/build-app.sh        # writes sound/demo/macos/AVSoundDemo/build/AV Sound Demo.app
open "sound/demo/macos/AVSoundDemo/build/AV Sound Demo.app"
```

`build-app.sh` runs `swift build -c release`, puts the executable and an `Info.plist` into
the app bundle, and signs the bundle ad hoc. Use `--scratch-path DIR` to keep build files
outside the package. The `build/` folder is ignored by git.

The app finds the repository by going up from its own location, so build it inside the
checkout. It finds `uv` on `PATH` and in the usual install folders
(`/opt/homebrew/bin`, `/usr/local/bin`, `~/.local/bin`, `~/.cargo/bin`). If it cannot find
one of them, a setup sheet asks for the paths. `AV_SOUND_REPO=<path>` overrides the
repository search. When it is set to a folder that is not the repository, the app does
not use another checkout in its place: the setup sheet names the variable and the path
(and the self-check fails, see below).

Without the app bundle:

```bash
AV_SOUND_REPO="$PWD" swift run --package-path sound/demo/macos/AVSoundDemo AVSoundDemo
```

Tests:

```bash
swift test --package-path sound/demo/macos/AVSoundDemo
AV_SOUND_BRIDGE_TESTS=1 swift test --package-path sound/demo/macos/AVSoundDemo   # also against the real bridge
uv run --project sound pytest --import-mode=importlib -p no:cacheprovider tests/sound/test_demo_bridge.py
```

`AV_SOUND_BRIDGE_TESTS=1` also runs the app models (Store, Fallback, profile changes)
against the real bridge. `AV_SOUND_AUDIO_TESTS=1` also plays short sounds through the
audio device: at volume 0, except one quiet DC level (about -40 dBFS, nothing to hear)
that checks that the output gets `sample / 32768` at unity gain, also on 44.1 kHz
devices.

## Headless self-check

```bash
"sound/demo/macos/AVSoundDemo/build/AV Sound Demo.app/Contents/MacOS/AVSoundDemo" --self-check
```

The self-check opens no window. It starts the bridge and does these checks:

- the engine's `self_test`;
- a render of the spec's worked example, which must give the pinned waveform hash;
- `validate` of an admissible recipe and of a recipe with a short event;
- the composition of a trained message, and the refusal of a held-out message;
- the nonlexical asset list and the reference vectors;
- Swift against Python byte identity for random recipes, and the Swift port's tables
  against the digests of spec D4 (a table mismatch fails the check).

Then it stops the bridge and prints a JSON summary (`ok`, `passed`, `failed`, `checks`) on
stdout. Progress goes to stderr. Options: `--repo PATH`, `--uv PATH` and `--count N` (the
number of random recipes, default 10). `--repo` comes before `AV_SOUND_REPO`, and
`AV_SOUND_REPO` before the search. The exit status is 0 when all checks pass, 1 when an
engine check fails, and 2 when the bridge cannot run: the repository or `uv` is not
found (also when `--repo` or `AV_SOUND_REPO` names a folder that is not the
repository), or the bridge does not start and answer `hello` (for example when the
engine's Python environment is missing).

## Sections

- **Recipe Lab:** edit a 3-event motif (profile, total length, pitches, rhythm weights,
  amplitudes, gaps). Each change is rendered by the engine and played (with "Play after
  every change" off, only Play or Space plays). The waveform view marks the events and
  gaps, and its playhead follows what you hear, output latency included. The panel shows
  the render metadata and the canonical recipe JSON.
- **Validator & Book:** validate the current recipe or any JSON text against a scratch
  book of committed atoms. Rejections are results with reason codes (`E_EVENT_SHORT`,
  `E_SEPARATION`, `E_JSON`, ...) and the nearest committed atom. The threshold is a
  plain decimal such as `0.1` (the engine refuses fractions like `1/10`). Cmd-Return
  validates the JSON text while you edit it, and the current recipe otherwise.
- **Messages:** the 4 × 4 message matrix of each family. Trained cells compose action +
  200 ms of silence + referent from the scratch book or the `DEMO` book. Held-out cells
  show only the engine's refusal and the expected hash (a click stops any sound). The
  selected message follows the scratch book: change one of its atoms and it is composed
  again. A message plays only while Messages is shown. When the engine cannot answer
  (for example the bridge stopped), the card says so and Try Again asks again. A trained
  message that the scratch book makes equal to a held-out message (for example when two
  slots hold the same recipe) is not composed: the card names the held-out message.
  Missing atoms are listed as the book changes.
- **Nonlexical:** the reserved assets (calibration tones, the READY cue, grammar clicks),
  with their levels. Each WAV is checked against both hashes before it plays.
- **Store:** the append-only vocabulary store in the bridge's temp directory. Create a
  `DEMO-` book, commit atoms, try an overwrite, freeze the book, then damage the files and
  see `store_verify` find the damage. "Truncate the log" removes the last log record. A
  cut of the freeze record or of the only record (`create_book`) is found without help
  (`E_MARKER`, `E_EVENT`). Any other cut, for example of a commit of an open book or of a
  refused commit logged after the freeze, leaves a self-consistent log: only "Require
  the recorded chain head" finds that one (`E_ANCHOR`). The Store log says which case
  the cut left. Each profile has its own book; going back to a book keeps its own
  recorded head and damage.
- **Fallback:** the fallback bank and book from the public seed `DEMO-fallback-v1`, and a
  bank scan against the scratch book. The DEMO books are too far from every bank recipe
  to cause a rejection. To see one, scan, put the entry the scan selected (the starred
  row; the scan also selects it in the Bank table) into a scratch-book slot, and scan
  again: the scan rejects it (`E_DUPLICATE`, `E_SEPARATION`) and selects a later entry.
  The scan stops at its first admissible entry, so a later row is never reached and
  cannot be rejected. Used indices are integers separated by commas or spaces.
- **Packages:** build, seal, load and leak-scan the synthetic `DEMO` package in a temp
  directory. Build Again builds a new package (the previous one is removed). When the
  bridge stops or restarts, the package goes with its temp directory.
- **Determinism:** the engine's reference vectors and golden manifest on this machine,
  recomputed on every run;
  Swift (`AVSoundSpec`) against Python for random recipes, all atoms and all 32 message
  hashes; the table digests of spec D4.
- **Settings & About:** the repository and `uv` paths, the engine versions from `hello`,
  and the bridge log.

## Safety

- Synthetic data only. The bridge refuses book IDs that do not start with `DEMO-`, uses
  only the public demo seed for fallbacks, and never reads study material.
- Stores, packages and other outputs go into a temp directory that the bridge makes
  outside every git work tree. The bridge deletes it when it exits, also when the app
  stops it with SIGTERM (sent to `uv`, which forwards it; repeated signals do not cut the
  cleanup short). Do not commit WAVs.
- The bridge writes no Python bytecode (`__pycache__`) into the checkout. The only
  write inside the work tree is the engine's virtual environment `sound/.venv` (ignored
  by git), which `uv run` creates or syncs. The app launches the bridge with
  `uv run --frozen`, so uv uses `sound/uv.lock` as it is: it never re-resolves the
  dependencies (no network) and never rewrites that tracked file, even when
  `sound/pyproject.toml` or your uv settings no longer match the lock.
- The app never composes a held-out message, under its own ID or another one. For a
  held-out ID, `compose` returns the engine's refusal (`HeldOutMessageError`,
  `E_HELDOUT`) and the app shows only the expected hash. The app also does not compose
  a trained message whose `composite_hash` equals that of a held-out message of the
  scratch book: it compares the hashes first, without audio.
- `store_tamper` damages only the bridge's own temp store.
