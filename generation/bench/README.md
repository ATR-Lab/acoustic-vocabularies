# LLM benchmark outputs (#16)

The files here come from `python -m av_generation.llm_bench`. They have one row per
model call and are built from the `llm_request` log lines. See
[`../docs/llm.md`](../docs/llm.md) for details.

| File | Contents |
| --- | --- |
| `llm_latency.csv` | One row per call (`llm_bench.LATENCY_COLUMNS`): profile, seed key, derived seed, wire seed, prompt tokens, status, finish reason, latency, the logged decoding values, runtime, prompt and schema hashes |
| `llm_latency_summary.json` | Per-profile counts, statuses, p50/p95/max latency, the "p95 under the 40-s cap" flag, refused outbound attempts |

## Current status: MOCK only

The committed files come from the mock server on loopback, with no GPU and no model.
Every row says `environment = MOCK (mock_llm on loopback; no GPU, no model)` and
`runtime = mock av-generation-mock-llm 1`. They show the following:

- the harness works;
- each of the 300 calls per profile logs `temperature=0.7`, `top_p=0.9`, `top_k=50`,
  `repetition_penalty=1.0`, `max_tokens=512` and its derived seed;
- the run made no outbound connection.

Their latencies are **not** evidence for the acceptance criterion "p95 slot latency at
the worst-case prompt size is under 40 s on the LLM GPU".

Regenerate the mock files:

```bash
uv run --project generation python -m av_generation.llm_bench --mock --calls 300 \
    --out-dir generation/bench --request-log <scratch>/llm-requests.jsonl \
    --run-id DEMO-llm-bench-mock-1
```

## Pending (hardware): the LLM GPU run

On the LLM host, start the pinned server with `python -m av_generation.llm_server serve`.
Then run the benchmark from a station on the isolated network:

```bash
uv run --project generation python -m av_generation.llm_bench \
    --base-url http://<llm-host>:8000 --environment "<GPU> <VRAM> driver <x> CUDA <y> vllm 0.30.0" \
    --run-id DEMO-llm-bench-gpu-1 --calls 300 --repeat 50 --out-dir <dir>
```

To use the real worst-case prompt, pass the prompts built by #17 with
`--messages worst_case=<file> --messages realistic=<file>`. Then replace the two files
here with the GPU outputs, and add `llm_repeatability.csv` for the same-seed note. The
CSV files hold prompt hashes only, never prompt text, so the outputs can be published.
