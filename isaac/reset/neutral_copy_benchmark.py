"""Offline copy-only comparison on independently pinned recorded neutral data.

No simulator, physics, reset, hold or qualification is performed. GC policy is
left unchanged; allocation tracing runs separately from the timed comparison.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
from pathlib import Path
import platform
import statistics
import subprocess
import time
import tracemalloc
from types import SimpleNamespace

from .manager import ResetManager
from .snapshot import load_snapshot, sha256


def summary(values):
    ordered = sorted(values)
    return {"count": len(values), "min": ordered[0], "median": statistics.median(values),
            "p95": ordered[math.ceil(.95 * len(values)) - 1], "max": ordered[-1]}


def benchmark(snapshot_path, expected_sha256, *, iterations=200, cycles=5):
    if not 1 <= iterations <= 10000 or not 1 <= cycles <= 20:
        raise ValueError("bounded positive iteration and cycle counts required")
    snapshot = load_snapshot(snapshot_path, expected_sha256)
    manager = ResetManager(SimpleNamespace(scene_sha256=snapshot["scene_sha256"]),
                           snapshot, expected_sha256, lambda _: None)
    methods = {"full_state_then_robot": lambda: manager.neutral_state["robot"],
               "robot_only": lambda: manager.neutral_robot_state}
    expected = snapshot["state"]["robot"]
    for method in methods.values():
        assert method() == expected
    # Fixed ABBA order balances ordering effects without treating this as a
    # native randomized crossover. Each result is released after its copy.
    blocks = []
    times = {key: [] for key in methods}
    for cycle in range(cycles):
        for key in ("full_state_then_robot", "robot_only", "robot_only", "full_state_then_robot"):
            before = gc.get_stats()
            samples = []
            for _ in range(iterations):
                started = time.perf_counter_ns()
                methods[key]()
                samples.append(time.perf_counter_ns() - started)
            after = gc.get_stats()
            times[key].extend(samples)
            blocks.append({"cycle": cycle, "method": key, "copy_ns": summary(samples),
                           "gc_collections_delta": [b["collections"] - a["collections"]
                                                    for a, b in zip(before, after)]})
    allocations = {}
    if tracemalloc.is_tracing():
        raise RuntimeError("allocation measurement requires an unused tracer")
    for key, method in methods.items():
        peaks = []
        tracemalloc.start()
        try:
            for _ in range(20):
                baseline = tracemalloc.get_traced_memory()[0]
                tracemalloc.reset_peak()
                copied = method()
                _, peak = tracemalloc.get_traced_memory()
                peaks.append(max(0, peak - baseline))
                assert copied == expected
                del copied
        finally:
            tracemalloc.stop()
        allocations[key] = summary(peaks)
    return {"version": 1, "scope": "offline_copy_only", "snapshot_sha256": expected_sha256,
            "scene_sha256": snapshot["scene_sha256"], "robot_sha256": sha256(expected),
            "joint_count": len(expected["joint_names"]), "object_count": len(snapshot["state"]["objects"]),
            "python": platform.python_version(), "platform": platform.platform(),
            "gc_enabled": gc.isenabled(), "gc_thresholds": gc.get_threshold(),
            "gc_policy_changed": False, "iterations_per_block": iterations, "cycles": cycles,
            "copy_ns": {key: summary(value) for key, value in times.items()}, "blocks": blocks,
            "separate_tracemalloc_peak_increment_bytes": allocations,
            "payloads_equal": True, "native_run": False, "timing_qualified": False,
            "participant_admission": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--cycles", type=int, default=5)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True))
    if dirty:
        raise ValueError("commit source before collecting benchmark evidence")
    if args.out.exists():
        raise FileExistsError("preserve prior evidence; choose a fresh output")
    report = benchmark(args.snapshot, args.sha256, iterations=args.iterations, cycles=args.cycles)
    report["source_commit"] = source
    report["source_sha256"] = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                               for name in ("isaac/reset/manager.py", "isaac/commands/dispatcher.py",
                                            "isaac/reset/neutral_copy_benchmark.py")}
    # Exclusive creation prevents accidentally replacing a retained observation.
    with args.out.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, sort_keys=True, indent=2, allow_nan=False)
        handle.write("\n")
    print(json.dumps({"source_commit": source, "copy_ns": report["copy_ns"],
                      "timing_qualified": False}, sort_keys=True))


if __name__ == "__main__":
    main()
