# DEMO pilot-bank rehearsal (synthetic)

These are the small outputs of the #27 pilot flow, run on DEMO IDs with a fake model.
They are **not pilot data**. No model, prompt text, participant or study material is
involved: the units are copies of the committed schedules DEMO unit, the meaning set is
the generation DEMO set, and every output and latency of the fake model is a pure
function of its B seed key.

The run built 8 main banks, `DEMO-bank-P001`..`DEMO-bank-P008` (dyad slots
`B-P01`..`B-P08`), under a DEMO generation config with the 0.10 threshold:

- `DEMO-bank-P003` failed its first attempt and completed at attempt 2.
- `DEMO-bank-P006` failed all 4 attempts and is `unavailable`. The spare
  `DEMO-bank-P006` v1.1.0 (seed namespace `DEMO-bank-P006-v1.1.0`, run
  `DEMO-pilot-banks-S1`) completed.
- Every dyad slot has a usable bank. One of the 2 spares was used.

| File | What it is |
| --- | --- |
| `pilot-plan.json` | The plan: bank IDs, dyad slots, seed namespaces, permutation hashes, spare budget, config hash and threshold, builder versions |
| `pilot-register.csv` | The register: 9 rows, with hashes recomputed and checked |
| `verify-log.txt` | `banks verify` output of the 9 banks (`verified 9 banks: 9 OK, 0 FAILED`) |
| `throughput.json`, `throughput.md` | Throughput and failure summary, with the projection for 72 banks (synthetic latencies) |
| `archive-sha256.txt`, `archive-summary.json` | Archive hash of the whole root (1,627 files, WAVs included), register hash and the result of the check after archiving |

The WAVs, slot logs and manifests are not committed. The test rebuilds them
deterministically, with `workers=1` and a manual clock.

To regenerate these files, run the following from the repository root:

```bash
AV_BANKS_DEMO_PILOT_OUT=banks/examples/demo-pilot uv run --project banks pytest \
  --import-mode=importlib -p no:cacheprovider tests/banks/test_pilot_banks.py -k demo_evidence
```

The hash values (config, permutation, bank, register and archive hashes) depend on the
shared formats and code pins at the commit that wrote them. The test checks the other
register columns and the summary figures against a fresh rehearsal.
