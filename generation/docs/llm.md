# LLM server, client, manifest and benchmark (#16)

Owner: #16 (`O4.2.1`). This component makes one schema-constrained JSON recipe per call
from the pinned `Qwen/Qwen2.5-7B-Instruct`. Every call uses the frozen decoding values,
a derived per-slot seed and a hard 40-s slot cap (Study A protocol §3.3 and §3.6).

Consumers:

- the A3 proposer and the B prompts (#17);
- the dry run (#22);
- the Study B bank builder (#26), which uses this component unchanged (Study B
  protocol §4);
- the G4 freeze (#25), through the manifest values.

| Module / file | Contents |
| --- | --- |
| `av_generation.llm` | `LlmClient` contract, `OpenAICompatibleClient`, `RawOutcome`, `TokenCountError`, `decoding_schema()`, request body, status mapping |
| `av_generation.llm_manifest` | `LlmManifest`, `load_llm_manifest`, `verify_model_dir`, Hugging Face pinning, hardware record, apparatus and freeze mapping; CLI |
| `av_generation.llm_server` | `ServerConfig`, `prepare_launch` (all refusal checks), `start_server`, `vllm_command`, `OFFLINE_ENV`; CLI |
| `av_generation.mock_llm` | `MockLlmServer`: mock OpenAI-compatible server (`/v1/chat/completions`, `/tokenize`); stand-in `vllm serve` |
| `av_generation.llm_bench` | Latency and same-seed benchmark; CLI |
| `generation/llm/manifest.json` | The LLM manifest (schema `manifest.schema.json` next to it) |
| `generation/llm/server-config.json` | The pinned server configuration (schema `server-config.schema.json`) |
| `generation/bench/` | Benchmark outputs. **MOCK only** for now; see [`../bench/README.md`](../bench/README.md) |

The two schemas are kept in `generation/llm/`, not in `generation/schema/`. The shared
schema-set test pins the contents of `generation/schema/`, and both files are read only
by this component.

## 1. Pinned model (`generation/llm/manifest.json`)

| Item | Value |
| --- | --- |
| Model | `Qwen/Qwen2.5-7B-Instruct`, architecture `Qwen2ForCausalLM` |
| Revision (model and tokenizer) | `a09a35458c702b33eeacc393d103063234e8bc28` (`constants.MODEL_REVISION`) |
| Licence | `apache-2.0` (file `LICENSE`, git blob pinned) |
| Weights | 4 safetensors shards, each pinned by size and LFS SHA-256. `weights_sha256` = `jsonio.file_set_sha256({path: sha256})` = `2eb5af88...2f1b81` |
| Other files | config, tokenizer, vocabulary, merges, index and licence, each pinned by size and git blob SHA-1 (Hugging Face `blobId`) |
| Chat template | SHA-256 of the UTF-8 `chat_template` string in `tokenizer_config.json`. The template adds a default system message when the first message is not a system message |
| Decoding schema | `sound/schema/recipe.schema.json`, unchanged. Hash `jsonio.schema_sha256` = `39ba4e4e...249c41` |

The values come from the Hugging Face API for the pinned revision
(`/api/models/Qwen/Qwen2.5-7B-Instruct/revision/<sha>?blobs=true`, retrieved
2026-10-05). The weights were never downloaded (`provenance.weights_downloaded =
false`). The API gives a SHA-256 only for LFS files. All other files are pinned by
their git blob ID, which also identifies the exact content.

To pin the manifest again, save the API response to a file and run:

```bash
uv run --project generation python -m av_generation.llm_manifest pin --api-json info.json \
    --vllm-version 0.30.0 --retrieved <date> --out generation/llm/manifest.json
```

Then update `manifest_sha256` in `server-config.json`, because the server config names
the manifest by its file hash.

`load_llm_manifest()` refuses a manifest (`E_MANIFEST`) when any of these is true:

- it does not match its schema;
- the model ID or revision differs from `constants`;
- an LFS pointer does not give its blob ID;
- `weights_sha256` is wrong;
- the decoding values differ from `constants.FROZEN_DECODING`;
- `max_model_len` is below 16,896;
- the decoding-schema hash differs from the current recipe schema.

So a change to the recipe schema or to a frozen value fails CI until the manifest is
updated on purpose.

### Hardware (Pending)

The `hardware` section stays `pending` until it is recorded on the LLM host:

```bash
python -m av_generation.llm_manifest record-hardware
```

This command reads the GPU name, VRAM, driver and CUDA versions with `nvidia-smi`, and
the torch and vLLM versions from the environment. It refuses when the installed vLLM is
not the pinned version. The schema requires at least 24,000 MiB of VRAM once the
section is `recorded`.

### Apparatus and freeze values

- `apparatus_values(manifest, prompts=PromptHashes)` gives the apparatus-manifest
  fields:
  - `model_revision`;
  - `runtime_precision` (`bfloat16`);
  - `prompt_hash` = `canonical_sha256({"a3_sha256", "b_sha256"})` of #17's two
    prompt-set hashes.
- `freeze_values(manifest, manifest_file_sha256=...)` gives the values of these freeze
  items (#25): `model.*`, `runtime.*`, `decoding.implementation`,
  `schema.decoding_sha256` and `llm.manifest_sha256`.
- `llm_manifest_sha256` (generation config, run manifests, freeze) is the file SHA-256
  of `manifest.json`. JSON files are checked out with LF on every OS (`.gitattributes`),
  so the hash is the same everywhere.

To print the values:

```bash
python -m av_generation.llm_manifest show
```

## 2. Server (`generation/llm/server-config.json`, `llm_server`)

Runtime: vLLM `0.30.0` (proposed; the ADR-006 decision belongs to #50). Precision is
`bfloat16`, on a dedicated GPU with at least 24 GB that is not shared with Isaac Sim.
The command is built from the config file:

```text
vllm serve <model_dir> --served-model-name Qwen/Qwen2.5-7B-Instruct --dtype bfloat16
  --max-model-len 16896 --generation-config vllm
  --structured-outputs-config {"backend":"xgrammar"} --seed 0 --load-format safetensors
  --gpu-memory-utilization 0.9 --tensor-parallel-size 1 --host <addr> --port 8000
```

- `--generation-config vllm`: the model's `generation_config.json` defaults are never
  loaded. Those defaults are temperature 0.7, top_p 0.8, top_k 20 and
  repetition_penalty 1.05, which differ from the frozen values. Every request also
  sends all five decoding values.
- The structured-outputs backend is pinned to `xgrammar`. With `auto`, vLLM may choose
  a different backend in a later release.
- The process environment adds `OFFLINE_ENV`: `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE`,
  `HF_DATASETS_OFFLINE`, `HF_HUB_DISABLE_TELEMETRY`, `VLLM_NO_USAGE_STATS`,
  `VLLM_DO_NOT_TRACK` and `DO_NOT_TRACK` are all set to `1`. vLLM sends usage
  statistics by default; these variables stop it.
- No watermark is configured. A request's `watermarking` flag only applies an
  engine-level watermark, and there is none.

### Refusal checks

`prepare_launch` runs every check before anything starts. Each failure raises
`ServerRefused` with a code and the list of problems. It also writes a
`startup_end` timing event with the detail `refused <code>`.

| Code | Rule |
| --- | --- |
| `E_MANIFEST` | The manifest is invalid or disagrees with the code |
| `E_CONFIG` | The config is invalid, or its model name, dtype, `max_model_len`, generation config, backend, engine seed or load format differs from the manifest |
| `E_CONFIG_MANIFEST` | The config names a different manifest file hash |
| `E_RUNTIME_MISSING`, `E_RUNTIME_VERSION` | vLLM is not installed, or it is not the pinned version |
| `E_MISSING`, `E_SIZE` | A pinned file is missing or has the wrong size |
| `E_REVISION`, `E_REVISION_UNKNOWN` | The download metadata or the snapshot directory names another revision, or names no revision |
| `E_FILE_BLOB` | A git-stored file (config, tokenizer, licence) differs |
| `E_CHAT_TEMPLATE` | The chat-template hash differs |
| `E_EXTRA_WEIGHTS` | A weight file that is not in the manifest is present |
| `E_WEIGHTS_SHA256` | A safetensors shard has the wrong SHA-256 |
| `E_STARTUP` | The server process exited, or `/health` did not answer within `startup_timeout_s` |

Revision evidence comes from one of two sources:

- `hf download --local-dir` metadata: the first line of each
  `.cache/huggingface/download/<file>.metadata` file;
- the cache layout `.../snapshots/<revision>/`.

### LLM-host runbook (Pending hardware)

```bash
# 1. Install the pinned runtime in the server environment (not in this uv project).
pip install vllm==0.30.0
# 2. Download the pinned revision (once, before the station network is isolated).
hf download Qwen/Qwen2.5-7B-Instruct --revision a09a35458c702b33eeacc393d103063234e8bc28 --local-dir <dir>
# 3. Verify and record the hardware.
python -m av_generation.llm_manifest verify --model-dir <dir>
python -m av_generation.llm_manifest record-hardware
# 4. Check, then serve on the station-network address.
python -m av_generation.llm_server check --model-dir <dir>
python -m av_generation.llm_server serve --model-dir <dir> --host <station-net address> \
    --timing-log <run>/logs/timing.jsonl --run-id <run id>
```

Startup is logged separately from slot time. The `startup_start` and `startup_end`
events have `component="llm"`. The `startup_end` detail holds `verify_ms` and
`load_ms`, and its `duration_ms` is the total.

## 3. Client (`OpenAICompatibleClient`)

```python
client = OpenAICompatibleClient.from_manifest(
    "http://<llm-host>:8000",
    load_llm_manifest(),
    run_id=run_id,
    clock=clock,
    request_log=RecordWriter(layout.log("llm_request"), types=(LlmRequest,)),
)
n = client.count_prompt_tokens(messages)  # POST /tokenize; TokenCountError
outcome = client.propose(
    messages, decoding_schema(), a3_seed_key(ns, atom, rnd, slot), slot_id=slot_id
)  # RawOutcome
```

- **Request.** The body holds:
  - `temperature` 0.7, `top_p` 0.9, `top_k` 50, `repetition_penalty` 1.0;
  - `max_completion_tokens` 512, which is the frozen `max_tokens` (vLLM 0.30
    deprecates the old name);
  - `seed` = `wire_seed(seed_from_key(key))`;
  - `n` 1, `stream` false;
  - `response_format` = `{"type": "json_schema", "json_schema": {"name":
    "acoustic_motif_recipe", "schema": <decoding schema>, "strict": true}}`.

  There is no `guided_json`; vLLM removed it in v0.12. A3 or B seed keys only. The
  decoding values are frozen: the constructor refuses others.
- **Statuses.**

  | Result | Status |
  | --- | --- |
  | `finish_reason` `stop` | `ok` |
  | `finish_reason` `length` (512 tokens) | `overflow_output` (partial text kept) |
  | Any other finish reason, no text, a non-200 status, an unreadable body, a refused or reset connection | `server_error` |
  | No complete response at the cap | `timeout` |

  The client never raises for a model or network failure, and it never retries.
- **Cap.** The request runs against `timeout_ms` (40,000) of run-clock time. At the cap
  the client cancels the request: it closes the connection, and vLLM aborts a request
  whose client disconnected. It then returns `timeout`. Measured times:
  - real clock: returns at 40.0 s (`test_slow_real_time_cap_returns_timeout_within_40_5_s`);
  - `ScaledClock(100)`: about 0.4 s;
  - `ManualClock`: exactly 40,000 ms.

  The cap never fires early, even when the event loop wakes timers ahead of a coarse
  clock. An unreachable host whose connection hangs also ends as `timeout`.
- **Log.** There is one `LlmRequest` line per call. It holds the seed key, the seed and
  the wire seed, the five decoding values (schema constants), `prompt_sha256 =
  messages_sha256(messages)`, `schema_sha256`, the status, latency, token counts,
  finish reason, runtime (`vllm 0.30.0`), model revision and `slot_id`. A `logging`
  line (`av_generation.llm`) repeats the decoding values and the seed.
- **Network.** HTTP clients ignore proxy variables (`trust_env=False`) and use no retry
  transport. Sessions run under `netguard.deny_outbound(allowed_hosts=[<llm-host>])`,
  as the benchmark does. Tests show that a non-loopback server is refused and that a
  whole session makes no outbound connection.
- **Threads and loops.** The client is safe to call from parallel proposer threads.
  Inside a running event loop it runs the call on a helper thread.

## 4. Mock server (`mock_llm`)

`MockLlmServer` serves the same endpoints as vLLM, plus `/health`, `/version` and
`/v1/models`. Its answers are deterministic: a recipe sampled from the request schema
with PCG64 seeded by the seed and the prompt hash. Its token counts come from a
stand-in ChatML template with a mock default system message, so they are not Qwen
counts.

It refuses what vLLM refuses:

- unknown fields such as `guided_json`;
- a wrong model;
- a seed outside the signed 64-bit range;
- a bad `response_format`;
- a prompt plus `max_completion_tokens` above `max_model_len`.

Replies are scripted with `MockReply`: a recipe, fixed text, an overflow, an HTTP
error, an unreadable body or no choice, each with an optional delay (the slowed
server). The mock records a client disconnect during a delay
(`MockCall.aborted`). `python -m av_generation.mock_llm serve ...` takes `vllm serve`
arguments, so the launcher tests use it as a stand-in executable.

## 5. Benchmark and repeatability (`llm_bench`)

The benchmark makes 300 calls for each prompt profile through the real client:

- `realistic`: about 3,000 prompt tokens;
- `worst_case`: up to the 16,384-token input cap.

Each profile is padded with the server's own `/tokenize` count, or it comes from #17
prompts given with `--messages`. The latency CSV is built from the `LlmRequest` log
lines, so it shows what every log line holds. The summary gives these values per
profile:

- the count of rows with the frozen values and the derived seed;
- statuses;
- p50, p95 and max latency;
- `p95_under_slot_cap`;
- refused outbound attempts.

`--repeat N` calls N seed keys twice each and writes `llm_repeatability.csv`, which
says whether each pair is byte-identical. See [`../bench/README.md`](../bench/README.md)
for the commands. The committed CSV is from the mock and is labelled `MOCK`. The LLM-GPU
run is Pending (hardware).

## 6. Decisions

| Decision | Rationale |
| --- | --- |
| The decoding schema carries the §3.2 enums and ranges (the recipe schema unchanged) | It is the issue's proposal. The schema already gives every field as an enum, so constrained decoding can only emit in-domain values. The #9 validator still runs on every output |
| Runtime: vLLM 0.30.0 with `response_format` json_schema and the `xgrammar` backend | Structured outputs through the OpenAI field; `guided_json` was removed in v0.12. 0.30.0 is the newest minor release that has been out for two weeks (0.31.0 came out on 2026-10-05). The backend is pinned because `auto` "is subject to change in each release". Validate on the LLM host (Pending); ADR-006 (#50) records the final choice |
| `--generation-config vllm` | The model's generation defaults (top_p 0.8, top_k 20, repetition_penalty 1.05) can never mix with the frozen values |
| Status mapping: only `finish_reason` decides `ok` or `overflow_output`; everything else is `server_error` | The protocol counts overflow as reaching the 512-token cap. An unknown finish reason (for example `abort`) is not a model answer |
| The cap is measured on the run clock, and it cancels by closing the connection | One rule for real, accelerated and test clocks. vLLM aborts requests whose client disconnected |
| `max_completion_tokens` on the wire | vLLM 0.30 deprecates `max_tokens`. The log keeps `max_tokens=512` |
| Non-LFS files are pinned by git blob SHA-1, weights by LFS SHA-256 | The API metadata gives no SHA-256 for git-stored files without a download. The blob ID identifies the content, and the launcher recomputes both |
| Revision evidence comes from the HF download metadata or the snapshot directory name | Checksums alone prove the content. The revision check proves where the files came from (acceptance: refuse on a revision mismatch) |
| The engine seed is 0, and per-request seeds are derived | Per-request seeds make each slot reproducible as far as the runtime allows. The waveform hash, not the seed, is the reproducibility record |
| Hardware ≥ 24 GB VRAM is enforced by the manifest schema once recorded | From the issue: bf16 needs about 15-20 GB on a dedicated GPU |
| The real-time 40.5-s test runs in CI on all three OSes | Acceptance evidence on Linux, macOS and Windows. It is selectable with `-k slow`, because `tests/generation/conftest.py` (shared) registers no `slow` marker |
