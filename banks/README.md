# Banks

Study B candidate-bank builder (`av-banks`, import `av_banks`; #26): builds, verifies and
amends the bank of one dyad (16 atoms x 3 profiles, 4 options per cell: 3 shown and 1
reserve). Only synthetic `DEMO-` fixtures belong here; real pilot and confirmatory banks,
their runs and their seeds stay in restricted storage (the builder refuses to create
them inside a git work tree), with only their hashes committed in registers.

Design, files, rules and decisions: [`docs/bank-builder.md`](docs/bank-builder.md).
Pilot banks (#27: IDs, spares, run, register, archive, throughput): [`docs/pilot-banks.md`](docs/pilot-banks.md).
Cross-team contract: [`docs/interfaces/generation.md`](../docs/interfaces/generation.md),
section "Study B bank builder (#26)".

## Setup

Python 3.11 and [uv](https://docs.astral.sh/uv/). `av-generation` and `av-sound` are
editable path dependencies. From the repository root:

```bash
uv sync --project banks --locked
uv run --project banks ruff check --config banks/pyproject.toml banks tests/banks
uv run --project banks ruff format --config banks/pyproject.toml --check banks tests/banks
(cd banks && uv run mypy)
uv run --project banks pytest --import-mode=importlib -p no:cacheprovider --timeout=300 tests/banks
```

Nothing needs the network at runtime except the LLM host named by `banks build --llm-url`;
the tests refuse any non-loopback connection.

## Layout

| Path | Contents |
| --- | --- |
| `src/av_banks/` | The package: `builder`, `proposer`, `run`, `manifest`, `verify`, `amend`, `permutation`, `layout`, `throughput`, `cli`; pilot runs (#27): `pilot`, `register`, `archive`, `metrics` |
| `schema/bank-attempt.schema.json` | `attempts/<n>/attempt.json` (status, reason, timing, throughput) |
| `docs/bank-builder.md` | Builder design and usage |
| `docs/pilot-banks.md` | Pilot banks runbook (#27) |
| `examples/demo-pilot/` | DEMO pilot rehearsal outputs: plan, register, verify log, throughput note, archive hash (synthetic) |
| `../generation/schema/bank-manifest.schema.json`, `bank-amendment.schema.json` | The bank manifest and amendment formats (shared contract, owned by #26) |
| `../tests/banks/` | Tests (scripted model, memory ledger, oracle; DEMO unit permutation fixture copied from the schedules examples) |

## Command line

| Command | Purpose |
| --- | --- |
| `banks build --bank-id ID --permutation P ... --runs-root R --run-id RUN ...` | Build one or more banks in a new run directory; prints the bank hashes |
| `banks verify BANK_DIR [--json]` | Re-render every option and recheck hashes, provenance, caps and all 1,920 different-atom pairs per profile |
| `banks amend BANK_DIR --profile P --atom A --rank R --reason TEXT --unheard-confirmed` | Reserve rule: replace an unheard shown option with the cell's reserve (logged, chained) |
| `banks hash BANK_DIR` | Print the bank hash of a manifest |
| `python -m av_banks.pilot plan\|run\|finish\|check\|archive\|load ...` | Pilot banks (#27): build the 8 banks and spares, verify, register, summarize, archive; `load --mode confirmatory` refuses a pilot bank |

## API

| Name | Purpose |
| --- | --- |
| `builder.bank_spec`, `builder.BankBuilder`, `builder.AttemptRun` | Bind a bank ID to its unit permutation; run attempts and slots |
| `proposer.LlmSlotProposer`, `proposer.SlotProposer` | One model proposal per slot (#16 client, #17 B prompt and parser) |
| `run.build_banks` | A run directory with several banks, run manifest, parallel banks |
| `manifest.BankManifest`, `manifest.manifest_from_files`, `manifest.to_dyad_bank` | Typed manifest, manifest from stored files, package-builder input (#13) |
| `verify.verify_bank` | Verification report |
| `amend.amend_bank` | Reserve-rule amendment |
| `permutation.load_permutation`, `permutation.expected_unit_id` | Unit permutation and the bank-ID to dyad-slot rule |
| `pilot.pilot_plan`, `pilot.run_pilot`, `pilot.finish_pilot`, `pilot.check_pilot`, `pilot.archive_pilot` | Pilot banks (#27) |
| `register.write_register`, `register.register_problems`, `register.open_bank` | Bank register, register check (recomputed hashes), set-bound loading |
| `archive.archive_tree`, `archive.archive_problems` | Hash every file, make it read-only (append-only logs excepted), check |
| `metrics.summarize_banks`, `metrics.summary_markdown` | Throughput and failure metrics, projection for #28 |
