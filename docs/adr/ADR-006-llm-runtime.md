# ADR-006 — Separate GPU and pinned language-model runtime

Status: Proposed — runtime validation and protocol decoding freeze pending

## Context

WBS O5.1.8 / #50, Study A protocol section 3.6 (not yet located). The sound
generation service later needs structured model output. No LLM service is
implemented or launched in Phase 1. This is a proposed reproducible baseline.

## Options

vLLM serving `Qwen/Qwen2.5-7B-Instruct` on a separate GPU; or cohosting with Isaac.
The issue's 15–20 GB bf16 estimate is a planning input, not a measurement here.
KV cache and runtime overhead depend on context, batch and concurrency.

## Measurements

No local model load, throughput, memory or schema-conformance benchmark exists.
Source provenance checked 2026-10-04: [vLLM v0.30.0](https://github.com/vllm-project/vllm/releases/tag/v0.30.0),
commit `ced6857afa0ea7b2e3f0846a62e1394e90f15607`; [Qwen revision](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct/tree/a09a35458c702b33eeacc393d103063234e8bc28).
The model's [license](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct/blob/a09a35458c702b33eeacc393d103063234e8bc28/LICENSE)
is Apache-2.0. `apparatus/model-provenance.json` records API-reported shard hashes;
weights have not been downloaded/rehashed locally. The tokenizer config and its
chat-template string were downloaded and SHA-256 checked independently.

## Decision

Propose vLLM **0.30.0**, the exact model revision above, **bfloat16** weights, on a
dedicated **>=24 GB GPU** (proposed class RTX 4090 24 GB or equivalent qualified
GPU). Its suitability must be measured; capacity alone is not a deployment test.
Do not share this GPU with Isaac by default. Select a matching CUDA/driver and
container digest during the later runtime validation, not by mutable `latest`.

Use OpenAI-compatible `response_format: {type: "json_schema", json_schema:
{name: "...", schema: {...}, strict: true}}`; reject schema-invalid replies.
Do not use removed `guided_json`. The supported structure is documented by
[vLLM](https://docs.vllm.ai/en/stable/features/structured_outputs/).

Chat-template UTF-8 SHA-256:
`cd8e9439f0570856fd70470bf8889ebd8b5d1107207f67a5efb46e342330527f`.
Tokenizer config SHA-256:
`5b5d4f65d0acd3b2d56a35b56d374a36cbc1c8fa5cf3b3febbbfabf22f359583`.

Protocol decoding values (temperature, top-p, top-k, max tokens, seed policy,
repetition penalty, stop tokens and concurrency) remain **unresolved** until
section 3.6 is read. Do not adopt upstream generation defaults as study settings.
Record prompt/template revisions and schema backend; confirmatory seeds remain private.

## Consequences

#16 receives proposed pins and must validate output/schema/memory before use.
ADR-003 reserves a separate GPU. Model weights and generated study material are
not added to this public repository. No procurement or service deployment is
authorized by this Proposed ADR.

## Manifest fields

`llm_runtime`, `runtime_commit`, `container_digest`, `model_revision`, weight
checksums, chat-template hash, tokenizer hash, precision, CUDA/driver, GPU,
decoding parameters, structured-output schema/backend, generation configuration hash.

## Revisit trigger

Runtime/model/template/precision/driver change, output-conformance failure, OOM,
latency failure or proposed cohosting. Reproducibility checks precede an apparatus bump.
