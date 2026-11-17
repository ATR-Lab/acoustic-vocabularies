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
| `av_generation.llm` | `LlmClient` contract, `OpenAICompatibleClient`, `RawOutcome`, `TokenCountError` / `TokenCountTimeout`, `decoding_schema()`, request body, status mapping |
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

The `hardware` section stays `pending` until it is recorded on the LLM host (section 7;
`bringup.sh` runs this command):

```bash
uv run --project generation python -m av_generation.llm_manifest record-hardware \
    --vllm <vllm-env>/bin/vllm
```

This command reads the GPU name, VRAM, driver and CUDA versions with `nvidia-smi`. It
reads the vLLM version from `<vllm> --version` and the PyTorch version from
`<vllm> collect-env`, so both come from the environment the server runs in, not from
this uv project. It refuses when that vLLM is not the pinned version.

Once the section is `recorded`, the schema requires `vram_mib` of at least 22,889 MiB
(`MIN_VRAM_MIB`). That is 24 GB (24 x 10^9 bytes) rounded up to whole MiB. `vram_mib`
comes from `nvidia-smi memory.total`, which reports nominal 24-GB cards below
24 x 1024 MiB: an L4 shows about 23,034 MiB and an A10 about 23,028 MiB. The workload
needs about 15 GB for the bf16 weights and about 1 GB of KV cache at 16,896 tokens.

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
| `E_RUNTIME_MISSING`, `E_RUNTIME_VERSION` | The vLLM executable that would be started (`--vllm`) cannot run or prints no version for `--version`, or it reports another version than the pin |
| `E_MISSING`, `E_SIZE` | A pinned file is missing or has the wrong size |
| `E_EXTRA_WEIGHTS` | A weight file that is not in the manifest is present |
| `E_EXTRA_FILE` | Any other file outside `.cache/` that is not in the manifest is present, for example a `chat_template.jinja`, `chat_template.json`, `additional_chat_templates/`, `special_tokens_map.json` or `added_tokens.json`. transformers prefers a separate template file over the pinned one in `tokenizer_config.json` |
| `E_REVISION`, `E_REVISION_UNKNOWN` | The download metadata or the snapshot directory names another revision, or names no revision |
| `E_FILE_BLOB` | A git-stored file (config, tokenizer, licence) differs. A changed chat template in `tokenizer_config.json` is caught here or, if the size changed, as `E_SIZE` |
| `E_CHAT_TEMPLATE` | The template in `tokenizer_config.json` does not have the hash that the manifest pins. Only an altered pin reaches this check, because a changed file fails `E_SIZE` or `E_FILE_BLOB` first |
| `E_WEIGHTS_SHA256` | A safetensors shard has the wrong SHA-256 |
| `E_STARTUP` | The server process exited, or `/health` did not answer within `startup_timeout_s` |

Only `.gitattributes` and Markdown files may be pinned as kind `other` (listed, not
verified). Every file that vLLM or transformers can read is verified.

Revision evidence comes from one of two sources:

- `hf download --local-dir` metadata: the first line of each
  `.cache/huggingface/download/<file>.metadata` file;
- the cache layout `.../snapshots/<revision>/`.

### LLM-host runbook (Pending hardware)

The bring-up on the GPU host is one ordered checklist in section 7, with two scripts in
`generation/llm/host/`. There are two environments on the host:

- **The vLLM environment** holds only the pinned runtime. Nothing from this repository
  is installed in it. Its executable, `<vllm-env>/bin/vllm`, is the one the server
  runs.
- **This uv project** (`uv run --project generation ...`) runs the launcher and the
  manifest tools. It never imports vLLM. It runs the vLLM executable named by
  `--vllm`, and it asks that same executable for its version.

`record-hardware` changes the manifest file, so the server config must then name the
manifest's new file hash (`manifest_sha256`); `bringup.sh` does both. The `--vllm`
default is the `vllm` found on `PATH`, and the same check applies to it.

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
ticket = ledger.reserve(cap_key, slot_id, study=..., method=...)
deadline = ticket.t_open_ms + SLOT_CAP_MS  # the slot's 40 s, on the run clock
n = client.count_prompt_tokens(messages, deadline_ms=deadline)  # TokenCountError
outcome = client.propose(
    messages,
    decoding_schema(),
    a3_seed_key(ns, atom, rnd, slot),
    slot_id=slot_id,
    deadline_ms=deadline,
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

  The client never raises for a model or network failure, and it never retries, not
  even a failed connect. A finish reason that the log schema cannot hold (a space,
  more than 32 characters, non-ASCII) is returned and logged as `other`, with
  `server_error`.
- **Cap.** The request runs against `timeout_ms` (40,000) of run-clock time. At the cap
  the client cancels the request: it closes the connection, and vLLM aborts a request
  whose client disconnected. It then returns `timeout`. Measured times:
  - real clock: returns at 40.0 s (`test_slow_real_time_cap_returns_timeout_within_40_5_s`);
  - `ScaledClock(100)`: about 0.4 s;
  - `ManualClock`: exactly 40,000 ms.

  The cap never fires early, even when the event loop wakes timers ahead of a coarse
  clock. An unreachable host whose connection hangs also ends as `timeout`.
- **Token count.** `count_prompt_tokens` has its own run-clock cap,
  `TOKEN_COUNT_TIMEOUT_MS` (5 s; `count_timeout_ms` in the constructor). At the cap the
  request is cancelled and `TokenCountTimeout` is raised. That is a `TokenCountError`,
  so the slot is consumed as `invalid_json` with `llm_status` `server_error`. A hung
  `/tokenize` therefore costs at most 5 s, not 45 s.
- **Slot budget.** Study A §3.3 gives each slot up to 40 s, and the token count and the
  call share it. Both methods take `deadline_ms`, a time on the client's run clock. The
  cap of either call is then the earlier of its own cap and the deadline. With
  `deadline_ms = ticket.t_open_ms + SLOT_CAP_MS` from `SlotLedger.reserve`, count plus
  call end within 40 s of the slot opening (plus the 0.25-s cancel grace), so three
  slots fit the 120-s window. When the deadline has already passed:
  - `propose` makes no call and returns `timeout`, logged with `latency_ms` 0;
  - `count_prompt_tokens` raises `TokenCountTimeout` without a request.

  `llm_fake.ScriptedLlmClient` accepts `deadline_ms` too, and records it in
  `FakeCall.deadline_ms`.
- **Log.** There is one `LlmRequest` line per call. It holds the seed key, the seed and
  the wire seed, the five decoding values (schema constants), `prompt_sha256 =
  messages_sha256(messages)`, `schema_sha256`, the status, latency, token counts,
  finish reason, runtime (`vllm 0.30.0`), model revision and `slot_id`. A `logging`
  line (`av_generation.llm`) repeats the decoding values and the seed.
- **Network.** HTTP clients ignore proxy variables (`trust_env=False`) and use no retry
  transport. Sessions run under `netguard.deny_outbound(allowed_hosts=[<llm-host>])`,
  as the benchmark does. Tests show that a non-loopback server is refused and that a
  whole session makes no outbound connection.
- **Threads and loops.** The methods are synchronous. They block the calling thread
  for up to the cap, and they are safe to call from parallel proposer threads. Async
  code, for example a FastAPI handler or a websocket station, must not call them on
  its event loop. Use `await asyncio.to_thread(client.propose, ...)` and
  `await asyncio.to_thread(client.count_prompt_tokens, ...)`. A call made on a running
  loop still works, because `run_coroutine` runs it on a helper thread, but that loop
  is blocked until the call returns (up to 40 s).

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
(`MockCall.aborted`). `tokenize_delay_s` slows `/tokenize` the same way, and
`tokenize_aborted` counts the cancelled counts. `python -m av_generation.mock_llm serve ...` takes `vllm serve`
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
| Hardware: at least 22,889 MiB (24 GB) of VRAM, enforced by the manifest schema once recorded | From the issue (">= 24 GB"; bf16 needs about 15-20 GB on a dedicated GPU). The rule uses the nominal decimal size because `nvidia-smi` reports 24-GB data-center cards below 24,576 MiB |
| The token count has its own 5-s run-clock cap, and both calls take a slot deadline | A hung `/tokenize` must not use the slot's 40 s. With the deadline, count plus call stay within the 40-s slot cap (Study A §3.3) |
| The runtime version comes from the vLLM executable that is started | The launcher runs from this uv project, and the pinned vLLM lives in its own environment. Reading the launcher's own interpreter could miss an unpinned runtime |
| Every file in the model directory must be in the manifest | A stray `chat_template.jinja` or tokenizer file would replace the pinned template or tokenizer at load time, and the hash check of `tokenizer_config.json` would still pass |
| The real-time 40.5-s test runs in CI on all three OSes | Acceptance evidence on Linux, macOS and Windows. It is selectable with `-k slow`, because `tests/generation/conftest.py` (shared) registers no `slow` marker |

## 7. GPU-host bring-up checklist (Pending hardware)

Everything that #16 still needs is hardware work. This checklist makes the first day on
the GPU host mechanical: from a fresh host to a pinned server, the acceptance evidence
and the G4 freeze items. Run it from a clean checkout of the commit being used.

### 7.1 Memory: why the GPU needs 24 GB

- The pinned bf16 weights are 4 safetensors shards of 15,231,271,888 bytes in all:
  about 15.2 GB (14.2 GiB), from `manifest.json` (`llm_host pins` prints
  `WEIGHTS_BYTES`).
- The KV cache of one full-length sequence (16,896 tokens: 28 layers, 4 KV heads of
  128 dimensions, keys and values in bf16, 57,344 bytes per token) is about 0.97 GB.
  vLLM also needs memory for the CUDA context, activations and CUDA graphs.
- So the frozen configuration needs a GPU with at least 24 GB:
  `llm_manifest.MIN_VRAM_MIB` = 22,889 MiB, the nominal 24 GB rounded up. The manifest
  schema refuses a recorded GPU below it, and `bringup.sh` refuses such a GPU before the
  download.
- **A 16 GB card cannot hold the frozen bf16 configuration with any KV cache**: it has at
  most 16,384 MiB (about 17.2 GB), and the weights alone take 15.2 GB of it before the
  CUDA context, the activations and the cache. Quantization (int8, fp8, AWQ, GPTQ, GGUF)
  is a model change: the protocol does not allow it without a new protocol version (#16,
  out of scope), and `freeze build` compares `runtime.precision` with the manifest.

### 7.2 The checklist

Placeholders: `<vllm-env>` and `<model-dir>` on local disk (about 40 GB free for the
download and the vLLM environment), `<station-net address>` and `<station-net CIDR>` of
the isolated station network, `<restricted>` outside any git work tree.

1. **Host prerequisites.** The NVIDIA driver works (`nvidia-smi` lists a GPU of at least
   22,889 MiB), `python3` (3.10 or later, for the vLLM environment), `uv`, `git`, `ss`
   (iproute2) and `truncate`. The host still reaches the package index and the model hub
   (needed for steps 0, 2 and 3 of `bringup.sh` only).
2. **Bring-up:**

   ```bash
   generation/llm/host/bringup.sh --vllm-env <vllm-env> --model-dir <model-dir> \
       --host <station-net address>
   ```

   It prints every command before it runs it (`--dry-run` prints them only):
   - `uv sync --project generation --locked`;
   - refuses a GPU below `MIN_VRAM_MIB`;
   - creates the vLLM environment and installs `vllm==<runtime.version of manifest.json>`;
   - `hf download <model.id> --revision <model.revision> --local-dir <model-dir>`, the
     last step that uses the network: after it the script exports `UV_OFFLINE`,
     `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE`, `HF_DATASETS_OFFLINE`,
     `HF_HUB_DISABLE_TELEMETRY`, `VLLM_NO_USAGE_STATS`, `VLLM_DO_NOT_TRACK` and
     `DO_NOT_TRACK`;
   - `llm_manifest verify --model-dir <model-dir>`;
   - `llm_manifest record-hardware --vllm <vllm-env>/bin/vllm` and `llm_host
     update-config` while the manifest's hardware is pending (`--record-hardware` to
     record again);
   - `llm_server check` (every refusal check of a server start);
   - prints the `serve` command and the next steps.

   The model ID, revision, vLLM version, port and minimum memory come from
   `generation/llm/manifest.json` and `server-config.json` (`python -m
   av_generation.llm_host pins`); the scripts hold none of them
   (`tests/generation/test_llm_host_scripts.py`).
3. **Isolate the host.** From here on the host reaches the station network only.
4. **Commit the hardware record.** `git diff generation/llm/` shows the manifest's
   `hardware` section and the new `manifest_sha256` of `server-config.json`. Commit both
   in a pull request that says "Part of #16".
5. **Serve.** Fill in `generation/llm/host/av-llm.service.example` (placeholders only),
   install it as `/etc/systemd/system/av-llm.service` and run `sudo systemctl enable
   --now av-llm`. `serve` runs the same checks again and refuses to start on any
   difference. Check: `curl -s http://<station-net address>:8000/v1/models` lists the
   pinned model (the port is the one in `server-config.json`).
6. **Evidence:**

   ```bash
   generation/llm/host/evidence.sh --out <restricted>/llm-evidence \
       --allow <station-net CIDR> --model-dir <model-dir> --vllm-env <vllm-env>
   ```

   It refuses an output directory inside a git work tree, and writes:
   - `refusal-revision.*`, `refusal-weights.*`: `llm_server check` on two throw-away
     copies of the download (symbolic links), one whose download metadata names another
     revision and one with a weight shard replaced by a sparse file of the same size;
     both must be refused (`E_REVISION`, `E_WEIGHTS_SHA256`);
   - `bench/`: `llm_bench` with 300 calls per prompt profile (realistic, about 3,000
     tokens, and worst case, 16,384 tokens) and 50 same-seed pairs (`--repeat`); pass
     #17's built prompts with `--messages realistic=<file> --messages worst_case=<file>`
     when they exist;
   - `ss-listening.txt`, `ss-established.txt` (a sample every 5 s during the benchmark)
     and `allowed.txt`: a connection the host opened to a peer outside loopback and the
     station network is an outbound connection;
   - `slow-cap.*`, `seed.*`: the 40.5-s slowed-server test and the seed tests;
   - `acceptance.md`: #16's acceptance criteria with PASS, FAIL or PENDING (`python -m
     av_generation.llm_host report --out-dir <dir>`, exit 0 only when all pass).
7. **Where the evidence goes.**
   - `bench/llm_latency.csv`, `bench/llm_latency_summary.json` and
     `bench/llm_repeatability.csv` replace the MOCK files in `generation/bench/`, in a
     pull request ("Part of #16"; [`../bench/README.md`](../bench/README.md)). They hold
     prompt hashes only. `test_committed_latency_csv_is_mock_or_gpu_evidence` accepts the
     GPU run.
   - `acceptance.md` goes into a comment on #16, with the same-seed result (how many of
     the 50 pairs were byte-identical) and the measured worst-case p95. The other files
     stay in `<restricted>`: the `ss` samples and the logs name host addresses.
   - The runtime decision itself is ADR-006 (#50), a human step.
8. **G4 freeze items.** The draft `generation/FREEZE-v1.0.draft.json` (#25) has 8 items
   that wait for the GPU host. G4 step 2 (`generation/docs/freeze.md` section 6) records
   them in the restricted copy of the draft:

   ```bash
   uv run --project generation python -m av_generation.llm_host freeze-commands \
       --draft <restricted>/freeze-draft.json      # prints the 8 commands; run them
   ```

   | Item | Value (from the committed manifest after step 4) | Command |
   | --- | --- | --- |
   | `runtime.vllm_version` | `runtime.version` (the executable's `--version` must equal it) | `freeze record runtime.vllm_version --text ...` |
   | `runtime.cuda_version` | `hardware.cuda_version` (the CUDA version `nvidia-smi` reports) | `freeze record runtime.cuda_version --text ...` |
   | `runtime.driver_version` | `hardware.driver_version` | `freeze record runtime.driver_version --text ...` |
   | `runtime.gpu` | `<hardware.gpu> (<hardware.vram_mib> MiB)` | `freeze record runtime.gpu --text ...` |
   | `runtime.precision` | `runtime.precision` (`bfloat16`) | `freeze record runtime.precision --text ...` |
   | `runtime.max_model_len` | `runtime.max_model_len` (16,896) | `freeze record runtime.max_model_len --value ...` |
   | `runtime.chat_template_sha256` | `chat_template.sha256` | `freeze record runtime.chat_template_sha256 --text ...` |
   | `decoding.implementation` | `llm_manifest.decoding_implementation` (vLLM version, `response_format.json_schema`, backend, `--generation-config vllm`) | `freeze record decoding.implementation --text ...` |

   The values are `llm_manifest.freeze_values`, which `python -m
   av_generation.llm_manifest show` also prints. `freeze build` compares
   `runtime.vllm_version`, `runtime.cuda_version`, `runtime.driver_version`,
   `runtime.precision`, `runtime.max_model_len` and `runtime.chat_template_sha256` with
   the committed manifest, so record exactly these values. Note: the draft's fill-in text
   for `runtime.cuda_version` names `torch.version.cuda`, but `freeze build` compares it
   with the manifest's `hardware.cuda_version`, which `record-hardware` reads from
   `nvidia-smi`; the two can differ.
9. **Then** the Study B bank runs can start against the server (unattended runs: #267).
