# Demo bridge

`av_sound_bridge.py` connects the macOS demo app (`../AVSoundDemo`) to the real sound
engine (`av_sound`). It implements [`../PROTOCOL.md`](../PROTOCOL.md) (protocol version 1):
one JSON request per line on stdin, one JSON response per line on stdout. Log messages go
to stderr only. It uses only the standard library and `av_sound`.

## Run

From the repository root:

```bash
uv sync --project sound --locked   # once
uv run --frozen --project sound python sound/demo/macos/bridge/av_sound_bridge.py
```

`--frozen` makes uv use `sound/uv.lock` as it is. Without it, uv re-resolves and rewrites
the lock file (a tracked file) when `sound/pyproject.toml` or your uv settings no longer
match it, and may need the network to do so. The app launches the bridge with this same
command.

Options:

- `--verbose`: debug logging (tracebacks of failed requests) on stderr.
- `--keep-temp`: keep the temp directory when the bridge exits (for example to look at
  the package that `package_demo` built). The path is logged on stderr.

The bridge exits with status 0 after `shutdown` or at the end of stdin. On SIGTERM or
SIGHUP it removes its temp directory and exits with status 128 + the signal number (143
for SIGTERM); the app sends SIGTERM (to `uv`, which forwards it) when the bridge does not
stop in time. Further SIGTERM and SIGHUP signals are ignored while it removes the
directory, so they cannot cut the removal short.

## Talk to it by hand

Start the bridge, then type one request per line and press Return:

```json
{"id": 1, "cmd": "hello", "args": {}}
{"id": 2, "cmd": "render", "args": {"recipe": {"total_ms": 600, "pitches": [-3, 0, 4], "rhythm_weights": [2, 1, 3], "gaps_ms": [40, 20], "amplitudes": [1.0, 0.6, 0.8]}, "profile": "P2"}}
{"id": 3, "cmd": "shutdown", "args": {}}
```

Each request gets one line back, for example
`{"id":3,"ok":true,"result":{}}`. A failed request gets
`{"id":…,"ok":false,"error":{"type":…,"code":…,"message":…}}`, and the bridge keeps
running. A line that is not JSON gets an error with `"id":null`.

To send requests from a file without typing:

```bash
printf '%s\n' '{"id":1,"cmd":"vectors_check"}' '{"id":2,"cmd":"golden_check"}' \
  | uv run --frozen --project sound python sound/demo/macos/bridge/av_sound_bridge.py
```

## Safety

- Synthetic data only. Book IDs must start with `DEMO-`; the bridge refuses all others.
- Stores and packages go into one temp directory that the bridge makes outside every
  git work tree. The bridge deletes it when it exits, also on SIGTERM or SIGHUP (unless
  `--keep-temp`).
- Run as a script, the bridge writes no Python bytecode (`__pycache__`) into the
  checkout. `uv run --frozen --project sound` itself creates or syncs `sound/.venv`
  (ignored by git) from the lock file, and never rewrites `sound/uv.lock`.
- `store_tamper` damages only the bridge's own temp store.
- `compose` refuses a held-out message by its message ID: it returns the engine's
  refusal (`HeldOutMessageError`, `E_HELDOUT`) and no audio. That is the only check the
  bridge makes. It takes the recipes of each request as they come, so it composes a
  trained message ID whose recipes make the same audio as a held-out message (for
  example a held-out message's referent recipe sent as the referent of a trained
  message, also with `book_id` `DEMO-P1`), and `render` returns the motif of any recipe.
  The demo app adds the check that refuses such a trained message: it compares
  `composite_hash` values first, without audio (`../PROTOCOL.md`, "Safety rules"). When
  you drive the bridge by hand, or write another client, no such check runs unless you
  make it.
- The bridge serves only its own checkout's engine (`sound/src/av_sound`, installed in
  editable mode by `uv sync`). When `av_sound` is imported from elsewhere, for example
  because `PYTHONPATH` points at another checkout, it logs why on stderr and answers
  every command but `shutdown` with `ProtocolError` / `E_ENGINE_PATH` (`../PROTOCOL.md`,
  "Engine location"). Unset `PYTHONPATH` before you run it by hand. Unset
  `PYTHONOPTIMIZE` too: the bridge runs under it, but the engine's `assert` checks do
  not. The app removes both from the bridge's environment.

## Tests

```bash
uv run --project sound pytest --import-mode=importlib -p no:cacheprovider tests/sound/test_demo_bridge.py
```
