# DEMO confirmatory bank register (synthetic)

These are the public outputs of one rehearsal of the confirmatory bank campaign (#28).
They come from the synthetic `DEMO-` set, not from study banks:

- banks `DEMO-C001`..`DEMO-C072`;
- DEMO unit stand-ins;
- a DEMO generation config with a DEMO draft freeze manifest (no tag: a DEMO config is
  never frozen, so `register.json` shows `tag_checked` and `guard_checked` false);
- a scripted proposer instead of the model;
- DEMO pilot namespaces `DEMO-P001`..`DEMO-P008`.

Design and procedure: [`banks/docs/confirmatory-banks.md`](../../docs/confirmatory-banks.md).

| File | What it is |
| --- | --- |
| `register.csv` | the register: 72 bank records with status, budget use, config hash, verify result and bank SHA-256 |
| `register.json` | counts, checks, decision (`ready`: 69 complete, 3 unavailable), unavailable bank IDs, hashes of the restricted files and of the archive |
| `verification-log.txt` | `banks verify` of all 72 banks (re-render, hashes, caps, 1,920 pairs per profile) |
| `timing.csv` | run timing log: one row per bank and an `ALL` row (`wall_estimated` is 0: no crash, no killed runner) |
| `g5b-report.md` | the report for the G5B owner |

The archive (`DEMO-cbanks-01-banks.tar`, 14,142 files, 932 MB, SHA-256 in
`register.json`) and the other restricted files (`plan.json`, slot logs, WAVs,
`slot-timing.csv`) are not committed.

Regenerate the files with the command below, then copy the five files here. It needs
#17's slot ledger. Until #17 is in the checkout, call `rehearsal.rehearse(out,
parallel_banks=4, jobs=4, ledger_factory=...)` with the tests' `MemoryLedger`
(`tests/banks/conftest.py`), which writes the same slot records; these files were made
that way.

```bash
uv run --project banks python -m av_banks.confirmatory rehearse --out <scratch dir> --parallel-banks 4 --jobs 4
```

`register.csv`, `verification-log.txt` and the bank rows of `timing.csv` are the same on
every machine. In a rehearsal, model latency is simulated (150-900 ms per slot) on each
bank's own clock. So a bank row's wall time is simulated, and its start time is the real
time at which the bank started. The `ALL` row's wall time and slots per minute are the
real runner time of the rehearsal machine. `register.json`, `g5b-report.md` and the
archive hash also carry the real compile time. `tests/banks/test_confirmatory_rehearsal.py`
rebuilds `DEMO-C001`, `DEMO-C007` (unavailable) and `DEMO-C065` (spare) and checks that
their rows match these files.
