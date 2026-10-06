# Generation

Python generation tooling (`av-generation`, import `av_generation`): the Study A
proposers (A1 hand design, A2 mutation search, A3 schema-constrained model proposals),
the round orchestrator and common selector, the rater panel, the separation-threshold
listening tool, audit reports, the G4 freeze manifest, and the contracts the Study B bank
builder (`banks/`) reuses. Model credentials, model weights, candidate banks and study
material remain outside this repository; only synthetic `DEMO-` examples are committed.

Architecture, module owners, log contracts, seeds, masking and the timing budget:
[`docs/architecture.md`](docs/architecture.md). Cross-team API summary:
[`docs/interfaces/generation.md`](../docs/interfaces/generation.md).

## Setup

Python 3.11 and [uv](https://docs.astral.sh/uv/). From the repository root:

```bash
uv sync --project generation --locked
uv run --project generation ruff check --config generation/pyproject.toml generation tests/generation
uv run --project generation ruff format --config generation/pyproject.toml --check generation tests/generation
(cd generation && uv run mypy)
uv run --project generation pytest --import-mode=importlib -p no:cacheprovider --timeout=300 -m "not browser" tests/generation
```

Browser tests (`-m browser`) need Playwright Chromium
(`uv run --project generation playwright install chromium`), or an installed Google
Chrome with `AV_GENERATION_BROWSER_CHANNEL=chrome`. They are skipped when no browser is
available, except in CI (`AV_GENERATION_BROWSER=1`). Nothing needs the network at
runtime; the tests refuse any non-loopback connection.

## Layout

| Path | Contents |
| --- | --- |
| `src/av_generation/` | The package (module map in `docs/architecture.md`) |
| `schema/` | JSON Schemas of every log record and run document, the generation config, meaning sets, the rater protocol, the dry-run plan, audit summary, freeze manifest, bank manifest and bank amendments |
| `examples/demo-batch-config.json` | Synthetic batch configuration (`DEMO-A-P01`) |
| `examples/demo-meanings/` | Synthetic meaning set (`DEMO-meanings-01`; placeholder texts, not study texts) |
| `examples/demo-slot-ledger/` | Synthetic slot ledger and refusals (`DEMO-slot-ledger-01`; every outcome code) |
| `prompts/` | A3/B prompt set `prompts-v1`: fixed instruction, context templates, hash file (frozen at G4) |
| `docs/` | Architecture and component docs |
| `../tests/generation/` | Test suite (shared fixtures in `conftest.py`) |

Runs for pilot, confirmatory or practice work are created outside any git work tree;
only DEMO/synthetic run summaries and hashes may be committed (under `runs/`).

## API

Stable shared contracts (implemented):

| Module | Purpose |
| --- | --- |
| `seeds` | Seed keys (`A1`/`A2`/`A3`/`B`/`PANEL`/`BOT`/`THRESHOLD`), `derive_seed`, `wire_seed`, `rng_for` |
| `outcomes` | The 14 slot outcome codes and their mapping from model statuses and validator codes |
| `jsonio` | Canonical JSON/JSONL, shared hash definitions (prompt, schema, file set), torn-tail repair |
| `records` | Log records and run documents with schemas; `RecordWriter`, `read_records` |
| `domain` | The 12 recipe coordinates in protocol order |
| `ids`, `config`, `proposers` | IDs and panel aliases, the batch configuration, round request/result types |
| `meanings`, `genconfig` | The shared meaning texts; the generation config and its frozen hash |
| `panel_session`, `rater_protocol` | Orchestrator <-> panel server contract; rater WebSocket protocol |
| `rundir`, `masking` | Run directories (public vs restricted) and masking checks |
| `bank_manifest` | Bank manifest, bank hash, amendment log and effective menu |
| `clock`, `netguard`, `webserve`, `llm_fake` | Clocks, outbound-network guard, uvicorn helper, scripted model client |

Interfaces filled by their issues: `llm` (#16), `ledger`, `prompts`, `parser`, `a3` (#17),
`a2` (#18), `a1` (#19), `orchestrator`, `selector` (#20), `panel`, `rater` (#21),
`dryrun` (#22), `threshold` (#23), `audit` (#24), `freeze` (#25); the bank builder (#26)
goes in `banks/`.
