"""Benchmark harness (#16): 300 calls per prompt profile through the real client, each
logging the frozen decoding values and a derived seed; latency CSV and summary. Runs on
the mock server here (labelled MOCK); the LLM-GPU numbers are Pending (hardware)."""

import csv
import json
import os
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from av_generation.constants import MAX_INPUT_TOKENS, SLOT_CAP_MS
from av_generation.llm_bench import (
    LATENCY_COLUMNS,
    MOCK_ENVIRONMENT,
    PROFILE_TARGETS,
    REPEAT_COLUMNS,
    bench_seed_key,
    demo_messages,
    fit_messages,
    frozen_logged,
    main,
    parse_args,
    percentile,
    run_cli,
    summarize,
)
from av_generation.mock_llm import count_tokens
from av_generation.records import LlmRequest, read_records
from av_generation.seeds import SeedNamespace, parse_seed_key, seed_from_key

ROOT = Path(__file__).resolve().parents[2]
BENCH_DIR = ROOT / "generation" / "bench"


def out_dir(tmp_path, name):
    """CI keeps the mock outputs as an artifact (generation/out/ci/); locally a tmp dir."""
    if os.environ.get("CI"):
        path = ROOT / "generation" / "out" / "ci" / name
        if path.exists():
            for child in path.iterdir():
                child.unlink()
        return path
    return tmp_path / name


def test_seed_keys_are_derived_distinct_and_demo():
    keys = [bench_seed_key(p, i) for p in PROFILE_TARGETS for i in range(300)]
    assert len(set(keys)) == len(keys) == 600
    for key in keys:
        parsed = parse_seed_key(key)
        assert parsed.namespace is SeedNamespace.A3 and parsed.parts[0].startswith("DEMO-bench-")
    assert len({seed_from_key(k) for k in keys}) == 600


def test_fit_messages_fills_the_target():
    for target in (500, 3_000, MAX_INPUT_TOKENS):
        messages, count = fit_messages(target, count_tokens)
        assert count == count_tokens(messages) <= target
        lines = messages[1]["content"].count("\n")
        assert count_tokens(demo_messages(lines + 1)) > target


@pytest.mark.timeout(240)
def test_300_calls_per_profile_log_frozen_values_and_seeds(tmp_path):
    out = out_dir(tmp_path, "llm-bench-mock")
    args = parse_args(
        [
            "--mock",
            "--calls",
            "300",
            "--repeat",
            "10",
            "--out-dir",
            str(out),
            "--run-id",
            "DEMO-llm-bench-ci",
        ]
    )
    summary = run_cli(args)
    assert summary["outbound_refused"] == []
    records = read_records(out / "llm-requests.jsonl", LlmRequest)
    assert len(records) == 600 + 2 * 10
    with open(out / "llm_latency.csv", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert tuple(rows[0]) == LATENCY_COLUMNS and len(rows) == 600
    for profile in PROFILE_TARGETS:
        mine = [r for r in rows if r["profile"] == profile]
        assert len(mine) == 300
        assert all(r["environment"] == MOCK_ENVIRONMENT for r in mine)
        for row in mine:
            assert row["temperature"] == "0.7" and row["top_p"] == "0.9"
            assert row["top_k"] == "50" and row["repetition_penalty"] == "1.0"
            assert row["max_tokens"] == "512"
            assert int(row["seed"]) == seed_from_key(row["seed_key"])
            assert row["status"] == "ok"
        stats = summary["profiles"][profile]
        assert stats["calls"] == stats["frozen_values_and_seed_logged"] == 300
        assert stats["distinct_seeds"] == 300 and stats["statuses"] == {"ok": 300}
        assert stats["prompt_tokens"] <= PROFILE_TARGETS[profile]
    assert summary["profiles"]["worst_case"]["prompt_tokens"] > MAX_INPUT_TOKENS - 64
    assert summary["repeatability"] == {"keys": 10, "byte_identical": 10}
    with open(out / "llm_repeatability.csv", encoding="utf-8", newline="") as handle:
        assert tuple(next(csv.reader(handle))) == REPEAT_COLUMNS
    saved = json.loads((out / "llm_latency_summary.json").read_text(encoding="utf-8"))
    assert saved == summary
    with pytest.raises(SystemExit, match="exists"):
        run_cli(args)


def test_given_prompts_and_a_real_server_need_a_label(tmp_path, capsys):
    messages = tmp_path / "worst.json"
    messages.write_text(
        json.dumps([{"role": "user", "content": "DEMO prompt from the prompt builder"}]),
        encoding="utf-8",
    )
    argv = ["--mock", "--calls", "2", "--out-dir", str(tmp_path / "out")]
    assert main([*argv, "--messages", f"worst_case={messages}"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["profiles"]["worst_case"]["calls"] == 2
    assert summary["profiles"]["worst_case"]["prompt_tokens"] < 100
    with pytest.raises(SystemExit, match="--environment"):
        run_cli(parse_args(["--base-url", "http://127.0.0.1:9", "--out-dir", str(tmp_path / "x")]))


def _row(profile, latency, status="ok", **extra):
    row = {
        "profile": profile,
        "latency_ms": latency,
        "status": status,
        "seed_key": "A3|DEMO-x|K-a1|1|1",
        "seed": seed_from_key("A3|DEMO-x|K-a1|1|1"),
        "temperature": 0.7,
        "top_p": 0.9,
        "top_k": 50,
        "repetition_penalty": 1.0,
        "max_tokens": 512,
        "prompt_tokens": 10,
        "target_prompt_tokens": 16_384,
    }
    row.update(extra)
    return row


def test_summary_flags_a_p95_over_the_cap_and_wrong_values():
    rows = [_row("worst_case", 1_000) for _ in range(18)]
    rows += [_row("worst_case", 40_000, "timeout"), _row("worst_case", 40_100, "timeout")]
    summary = summarize(rows, environment="DEMO")
    stats = summary["profiles"]["worst_case"]
    assert stats["latency_ms"] == {"p50": 1_000, "p95": 40_000, "max": 40_100}
    assert stats["p95_under_slot_cap"] is False and summary["slot_cap_ms"] == SLOT_CAP_MS
    assert stats["statuses"] == {"ok": 18, "timeout": 2}
    assert not frozen_logged(_row("x", 1, temperature=0.8))
    assert not frozen_logged(_row("x", 1, seed=1))


@given(st.lists(st.integers(0, 100_000), min_size=1, max_size=200), st.floats(0.01, 1.0))
def test_nearest_rank_percentile(values, q):
    result = percentile(values, q)
    assert result in values
    assert sum(v <= result for v in values) >= q * len(values) - 1e-9
    assert sum(v < result for v in values) < q * len(values) + 1e-9
    assert percentile([], q) is None


def test_committed_mock_latency_csv_is_labelled():
    with open(BENCH_DIR / "llm_latency.csv", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert tuple(rows[0]) == LATENCY_COLUMNS
    assert {r["environment"] for r in rows} == {MOCK_ENVIRONMENT}
    assert {r["runtime"] for r in rows} == {"mock av-generation-mock-llm 1"}
    for profile in PROFILE_TARGETS:
        mine = [r for r in rows if r["profile"] == profile]
        assert len(mine) == 300
        assert len({r["seed"] for r in mine}) == 300
        assert all(int(r["seed"]) == seed_from_key(r["seed_key"]) for r in mine)
        assert all(r["temperature"] == "0.7" and r["max_tokens"] == "512" for r in mine)
    summary = json.loads((BENCH_DIR / "llm_latency_summary.json").read_text(encoding="utf-8"))
    assert summary["environment"] == MOCK_ENVIRONMENT
    assert summary["outbound_refused"] == []
    assert (BENCH_DIR / "README.md").is_file()
