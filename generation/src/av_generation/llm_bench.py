"""Latency and repeatability benchmark of the LLM server (#16 acceptance evidence).

`run_benchmark` sends `calls` proposals per prompt profile through the real client
(`llm.OpenAICompatibleClient`), so every call is logged as an `LlmRequest` line with the
frozen decoding values and the derived seed. The latency CSV is built from those log
lines (`latency_rows`), not from the client's return values, so it shows what the log
holds. Profiles: `realistic` (about 3,000 prompt tokens) and `worst_case` (the 16,384-token
input cap), each a synthetic DEMO prompt padded to its target with the server's own
token count (`/tokenize`); the real run may pass #17's built prompts instead
(`--messages PROFILE=FILE`). Seeds are A3 keys in a `DEMO-bench-...` namespace.

`repeat_check` calls the same seed key and prompt twice and records whether the two
texts are byte-identical (the same-seed repeatability note; Pending on the LLM host).

Outputs of the CLI (one directory): `llm-requests.jsonl`, `llm_latency.csv`
(`LATENCY_COLUMNS`), `llm_latency_summary.json` and, with `--repeat N`,
`llm_repeatability.csv`. The whole run is guarded by `netguard.deny_outbound` with only
the server host allowed; refused attempts are listed in the summary (must be none).

    # LLM host (real numbers; Pending hardware):
    python -m av_generation.llm_bench --base-url http://<llm-host>:8000 \\
        --environment "<gpu> vllm 0.30.0" --run-id DEMO-llm-bench-gpu-1 \\
        --calls 300 --repeat 50 --out-dir <dir>
    # Development (mock server on loopback; labelled MOCK):
    python -m av_generation.llm_bench --mock --calls 300 --out-dir generation/bench \\
        --request-log <scratch>/llm-requests.jsonl --run-id DEMO-llm-bench-mock-1
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import math
import os
import sys
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

import numpy as np
from av_sound.grammar import ATOM_IDS

from av_generation.clock import Clock, SystemClock
from av_generation.constants import FROZEN_DECODING, MAX_INPUT_TOKENS, SLOT_CAP_MS
from av_generation.jsonio import document_text, read_json
from av_generation.llm import ChatMessage, OpenAICompatibleClient, decoding_schema
from av_generation.llm_manifest import load_llm_manifest
from av_generation.mock_llm import MOCK_RUNTIME, MockLlmServer
from av_generation.netguard import deny_outbound
from av_generation.records import LlmRequest, RecordWriter, read_records
from av_generation.seeds import a3_seed_key, seed_from_key
from av_generation.webserve import serve_in_thread

PROFILE_TARGETS: Final[Mapping[str, int]] = {"realistic": 3_000, "worst_case": MAX_INPUT_TOKENS}
"""Prompt profiles and their target prompt tokens (server count, generation prompt included)."""
MOCK_ENVIRONMENT: Final = "MOCK (mock_llm on loopback; no GPU, no model)"
"""Label of mock runs: their latencies are not evidence for the 40-s p95 criterion."""
LATENCY_COLUMNS: Final[tuple[str, ...]] = (
    "environment",
    "profile",
    "call",
    "seed_key",
    "seed",
    "wire_seed",
    "target_prompt_tokens",
    "prompt_tokens",
    "tokens_in",
    "tokens_out",
    "status",
    "finish_reason",
    "latency_ms",
    "temperature",
    "top_p",
    "top_k",
    "repetition_penalty",
    "max_tokens",
    "model",
    "model_revision",
    "runtime",
    "prompt_sha256",
    "schema_sha256",
)
REPEAT_COLUMNS: Final[tuple[str, ...]] = (
    "environment",
    "seed_key",
    "seed",
    "status_1",
    "status_2",
    "text_sha256_1",
    "text_sha256_2",
    "identical",
)
SYSTEM_TEXT: Final = (
    "DEMO benchmark prompt (synthetic; not the study prompt set). Propose one acoustic "
    "motif recipe that matches the schema. Return one JSON object and no explanation."
)
FILLER_FIELDS: Final[tuple[str, ...]] = ("total_ms", "pitches", "rhythm_weights", "gaps_ms")
SEED_PREFIX: Final = "DEMO-bench"
KEYS_PER_NAMESPACE: Final = len(ATOM_IDS) * 4 * 3


# ---------------------------------------------------------------------------
# Prompts and seed keys


def bench_seed_key(profile: str, call: int, *, prefix: str = SEED_PREFIX) -> str:
    """A3 seed key of benchmark call `call` (0-based) of `profile`: 192 keys per
    namespace `<prefix>-<profile>-<n>` (atoms x rounds x slots)."""
    block, rest = divmod(call, KEYS_PER_NAMESPACE)
    atom = ATOM_IDS[rest // 12]
    round_ = rest % 12 // 3 + 1
    slot = rest % 3 + 1
    return a3_seed_key(f"{prefix}-{profile.replace('_', '-')}-{block + 1}", atom, round_, slot)


def _filler_line(rng: np.random.Generator, index: int) -> str:
    pitches = [int(v) for v in rng.integers(-6, 7, size=3)]
    weights = [int(v) for v in rng.integers(1, 5, size=3)]
    gaps = [int(v) for v in rng.choice([20, 40, 60], size=2)]
    total = int(rng.choice([450, 600, 750, 900]))
    return (
        f"DEMO candidate {index}: total_ms={total} pitches={pitches} "
        f"rhythm_weights={weights} gaps_ms={gaps} status=rated score=4.5"
    )


def demo_messages(n_lines: int) -> list[ChatMessage]:
    """The synthetic prompt with `n_lines` deterministic history lines."""
    rng = np.random.Generator(np.random.PCG64(0))
    lines = [_filler_line(rng, i + 1) for i in range(n_lines)]
    user = "DEMO atom K-a1 (synthetic meaning text).\n" + "\n".join(lines)
    return [{"role": "system", "content": SYSTEM_TEXT}, {"role": "user", "content": user}]


def fit_messages(
    target_tokens: int, counter: Callable[[Sequence[ChatMessage]], int]
) -> tuple[list[ChatMessage], int]:
    """The longest synthetic prompt whose server token count is at most `target_tokens`
    (binary search on the number of history lines); returns the messages and the count."""
    low, high = 0, 1
    while counter(demo_messages(high)) <= target_tokens:
        low, high = high, high * 2
    while high - low > 1:
        mid = (low + high) // 2
        if counter(demo_messages(mid)) <= target_tokens:
            low = mid
        else:
            high = mid
    messages = demo_messages(low)
    return messages, counter(messages)


@dataclass(frozen=True, slots=True)
class BenchProfile:
    name: str
    target_tokens: int
    messages: tuple[ChatMessage, ...]
    prompt_tokens: int


def make_profiles(
    client: OpenAICompatibleClient,
    targets: Mapping[str, int] = PROFILE_TARGETS,
    messages: Mapping[str, Sequence[ChatMessage]] | None = None,
) -> list[BenchProfile]:
    """Profiles from given messages (counted by the server) or fitted synthetic prompts."""
    out = []
    for name, target in targets.items():
        if messages is not None and name in messages:
            msgs = list(messages[name])
            count = client.count_prompt_tokens(msgs)
        else:
            msgs, count = fit_messages(target, client.count_prompt_tokens)
        out.append(BenchProfile(name, target, tuple(msgs), count))
    return out


# ---------------------------------------------------------------------------
# Running and reporting


def run_benchmark(
    client: OpenAICompatibleClient,
    profiles: Sequence[BenchProfile],
    *,
    calls: int,
    schema: Mapping[str, Any] | None = None,
    prefix: str = SEED_PREFIX,
) -> dict[str, BenchProfile]:
    """`calls` proposals per profile; returns `{seed_key: profile}` for `latency_rows`."""
    schema = schema if schema is not None else decoding_schema()
    keys: dict[str, BenchProfile] = {}
    for profile in profiles:
        for call in range(calls):
            key = bench_seed_key(profile.name, call, prefix=prefix)
            keys[key] = profile
            client.propose(list(profile.messages), schema, key)
    return keys


def latency_rows(
    records: Sequence[LlmRequest], keys: Mapping[str, BenchProfile], *, environment: str
) -> list[dict[str, Any]]:
    """One CSV row per logged request of the benchmark (log order)."""
    rows = []
    counters: Counter[str] = Counter()
    for record in records:
        profile = keys.get(record.seed_key)
        if profile is None:
            continue
        counters[profile.name] += 1
        rows.append(
            {
                "environment": environment,
                "profile": profile.name,
                "call": counters[profile.name],
                "seed_key": record.seed_key,
                "seed": record.seed,
                "wire_seed": record.wire_seed,
                "target_prompt_tokens": profile.target_tokens,
                "prompt_tokens": profile.prompt_tokens,
                "tokens_in": record.tokens_in,
                "tokens_out": record.tokens_out,
                "status": record.status.value,
                "finish_reason": record.finish_reason,
                "latency_ms": record.latency_ms,
                "temperature": record.temperature,
                "top_p": record.top_p,
                "top_k": record.top_k,
                "repetition_penalty": record.repetition_penalty,
                "max_tokens": record.max_tokens,
                "model": record.model,
                "model_revision": record.model_revision,
                "runtime": record.runtime,
                "prompt_sha256": record.prompt_sha256,
                "schema_sha256": record.schema_sha256,
            }
        )
    return rows


def _cell(value: object) -> str:
    return "" if value is None else str(value)


def write_csv(
    rows: Sequence[Mapping[str, Any]], path: str | os.PathLike[str], columns: Sequence[str]
) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow([_cell(row[c]) for c in columns])


def percentile(values: Sequence[int], q: float) -> int | None:
    """Nearest-rank percentile (`q` in (0, 1]); `None` for no values."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def frozen_logged(row: Mapping[str, Any]) -> bool:
    """The row's log line holds the frozen decoding values and the key's derived seed."""
    d = FROZEN_DECODING
    return bool(
        row["temperature"] == d.temperature
        and row["top_p"] == d.top_p
        and row["top_k"] == d.top_k
        and row["repetition_penalty"] == d.repetition_penalty
        and row["max_tokens"] == d.max_tokens
        and row["seed"] == seed_from_key(row["seed_key"])
    )


def summarize(rows: Sequence[Mapping[str, Any]], *, environment: str) -> dict[str, Any]:
    """Per-profile counts, statuses and latency percentiles of `latency_rows`."""
    profiles: dict[str, Any] = {}
    for name in sorted({str(r["profile"]) for r in rows}):
        mine = [r for r in rows if r["profile"] == name]
        latencies = [int(r["latency_ms"]) for r in mine]
        p95 = percentile(latencies, 0.95)
        profiles[name] = {
            "calls": len(mine),
            "frozen_values_and_seed_logged": sum(frozen_logged(r) for r in mine),
            "distinct_seeds": len({r["seed"] for r in mine}),
            "statuses": dict(sorted(Counter(str(r["status"]) for r in mine).items())),
            "prompt_tokens": sorted({r["prompt_tokens"] for r in mine})[0],
            "target_prompt_tokens": mine[0]["target_prompt_tokens"],
            "latency_ms": {
                "p50": percentile(latencies, 0.50),
                "p95": p95,
                "max": max(latencies),
            },
            "p95_under_slot_cap": p95 is not None and p95 < SLOT_CAP_MS,
        }
    return {"environment": environment, "slot_cap_ms": SLOT_CAP_MS, "profiles": profiles}


def repeat_check(
    client: OpenAICompatibleClient,
    profile: BenchProfile,
    *,
    keys: int,
    environment: str,
    schema: Mapping[str, Any] | None = None,
    prefix: str = SEED_PREFIX + "-repeat",
) -> list[dict[str, Any]]:
    """Same seed key and prompt twice for `keys` keys; one row per key."""
    schema = schema if schema is not None else decoding_schema()
    rows = []
    for call in range(keys):
        key = bench_seed_key(profile.name, call, prefix=prefix)
        first = client.propose(list(profile.messages), schema, key)
        second = client.propose(list(profile.messages), schema, key)

        def digest(text: str | None) -> str:
            return "" if text is None else hashlib.sha256(text.encode("utf-8")).hexdigest()

        rows.append(
            {
                "environment": environment,
                "seed_key": key,
                "seed": first.seed,
                "status_1": first.status.value,
                "status_2": second.status.value,
                "text_sha256_1": digest(first.text),
                "text_sha256_2": digest(second.text),
                "identical": int(first.text is not None and first.text == second.text),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# CLI


@contextmanager
def _server(args: argparse.Namespace) -> Iterator[tuple[str, str, str, str | None]]:
    """(base_url, model, runtime, model_revision) of the server to benchmark."""
    if args.mock:
        mock = MockLlmServer()
        with serve_in_thread(mock.app) as base_url:
            yield base_url, mock.model, MOCK_RUNTIME, None
        return
    manifest = load_llm_manifest(args.manifest)
    yield args.base_url, manifest.model.id, manifest.runtime_label(), manifest.model.revision


def run_cli(args: argparse.Namespace, *, clock: Clock | None = None) -> dict[str, Any]:
    """Run the benchmark described by parsed CLI arguments; returns the summary."""
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    environment = MOCK_ENVIRONMENT if args.mock else args.environment
    if not environment:
        raise SystemExit("--environment is required for a real server")
    log_path = Path(args.request_log) if args.request_log else out / "llm-requests.jsonl"
    if log_path.exists():
        raise SystemExit(f"{log_path} exists; use a new --out-dir or --request-log")
    clock = clock if clock is not None else SystemClock()
    with ExitStack() as stack:
        base_url, model, runtime, revision = stack.enter_context(_server(args))
        host = urlsplit(base_url).hostname or ""
        refused = stack.enter_context(deny_outbound(allowed_hosts=[host]))
        writer = RecordWriter(log_path, types=(LlmRequest,), fsync=False)
        client = OpenAICompatibleClient(
            base_url,
            model,
            run_id=args.run_id,
            clock=clock,
            request_log=writer,
            runtime=runtime,
            model_revision=revision,
        )
        messages = None
        if args.messages:
            messages = {}
            for item in args.messages:
                name, _, file = item.partition("=")
                messages[name] = read_json(file)
        profiles = make_profiles(client, messages=messages)
        keys = run_benchmark(client, profiles, calls=args.calls)
        repeat_rows = []
        if args.repeat:
            repeat_rows = repeat_check(
                client, profiles[0], keys=args.repeat, environment=environment
            )
        refused_attempts = list(refused)
    records = read_records(log_path, LlmRequest)
    rows = latency_rows(records, keys, environment=environment)
    write_csv(rows, out / "llm_latency.csv", LATENCY_COLUMNS)
    summary = summarize(rows, environment=environment)
    summary["outbound_refused"] = refused_attempts
    summary["run_id"] = args.run_id
    summary["runtime"] = runtime
    if repeat_rows:
        write_csv(repeat_rows, out / "llm_repeatability.csv", REPEAT_COLUMNS)
        summary["repeatability"] = {
            "keys": len(repeat_rows),
            "byte_identical": sum(int(r["identical"]) for r in repeat_rows),
        }
    with open(out / "llm_latency_summary.json", "w", encoding="utf-8", newline="\n") as handle:
        handle.write(document_text(summary))
    return summary


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m av_generation.llm_bench")
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--base-url", help="pinned server root, e.g. http://<llm-host>:8000")
    where.add_argument("--mock", action="store_true", help="in-process mock server (MOCK)")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--calls", type=int, default=300, help="calls per prompt profile")
    parser.add_argument("--repeat", type=int, default=0, help="same-seed repeat keys")
    parser.add_argument("--environment", default=None, help="GPU, driver and runtime label")
    parser.add_argument("--run-id", default="DEMO-llm-bench-1")
    parser.add_argument("--manifest", default=None)
    parser.add_argument(
        "--request-log", default=None, help="LlmRequest JSONL (default: OUT_DIR/llm-requests.jsonl)"
    )
    parser.add_argument(
        "--messages", action="append", default=[], help="PROFILE=FILE (JSON chat messages)"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    summary = run_cli(parse_args(argv))
    sys.stdout.write(document_text(summary))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
