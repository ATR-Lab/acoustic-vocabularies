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
uv run --project generation pytest --import-mode=importlib -p no:cacheprovider -m "not browser" tests/generation
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
| `schema/` | JSON Schemas of every log record, run document, the rater protocol, audit summary, freeze manifest and bank manifest |
| `examples/demo-batch-config.json` | Synthetic batch configuration (`DEMO-A-P01`) |
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
| `records` | Log records and run documents with schemas; `RecordWriter`, `read_records` |
| `domain` | The 12 recipe coordinates in protocol order |
| `ids`, `config`, `proposers` | IDs, the batch configuration, round request/result types |
| `rater_protocol` | Rater panel WebSocket protocol |
| `rundir`, `masking` | Run directories (public vs restricted) and masking checks |
| `clock`, `netguard`, `webserve`, `llm_fake` | Clocks, outbound-network guard, uvicorn helper, scripted model client |

Interfaces filled by their issues: `llm` (#16), `ledger`, `prompts`, `parser`, `a3` (#17),
`a2` (#18), `a1` (#19), `orchestrator`, `selector` (#20), `rater` (#21), `dryrun` (#22),
`threshold` (#23), `audit` (#24), `freeze` (#25), `bank_manifest` (#26).
