"""Paired, synthetic-only mailbox benchmark of two menu-store bridge implementations.

Run with the locked sound environment on an otherwise quiet machine. Both bridges
use the same verified bank/package, deterministic clock, request IDs and config
paths, but fresh stores and mailboxes. Full receipt and snapshot equality is checked;
the 2,000 ms client deadline is reported without changing or asserting a new limit.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sound/src"))
from av_sound.dyad_bank import synthetic_dyad_bank
from av_sound.grammar import ATOM_IDS
from av_sound.package import ATOM_WAVES, build_dyad_package
from av_sound.store import VocabularyStore, canonical_json


def load_bridge(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("bridge module is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_bridge(module, config, root, name):
    bridge = module.MenuStoreBridge(config)
    bridge.store = VocabularyStore(
        config["store_root"], clock=lambda: datetime(2026, 1, 1, tzinfo=UTC)
    )
    mailbox = root / "mailbox"
    (mailbox / "requests").mkdir(parents=True)
    operations = []
    previous = None
    old_entries = {}
    counts = []
    atoms = [None, *sorted(ATOM_IDS, key=lambda atom: (ATOM_WAVES[atom], ATOM_IDS.index(atom)))]
    for index, atom in enumerate(atoms):
        request = dict(
            schema_version=1,
            request_id=f"{index:032x}",
            operation="atom" if atom else "profile",
            unit_id=config["unit_id"],
            book_id=config["book_id"],
            bank_sha256=config["bank_sha256"],
            package_sha256=config["package_sha256"],
            expected_head=previous["after_head"] if previous else None,
            expected_snapshot_sha256=previous["after_snapshot_sha256"]
            if previous
            else module.EMPTY_SNAPSHOT,
            profile="P1",
            menu_key=atom or "profile",
            rank=1 if atom else None,
        )
        started = time.perf_counter_ns()
        module._atomic_new(mailbox / "requests" / (request["request_id"] + ".json"), request)
        result = module.serve_mailbox(bridge, mailbox, seconds=300, max_requests=1)
        response = module.read_json(mailbox / "responses" / (request["request_id"] + ".json"))
        elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
        if (
            result["processed"] != 1
            or response["error"] is not None
            or response["response_sha256"]
            != module.digest({k: v for k, v in response.items() if k != "response_sha256"})
        ):
            raise ValueError(f"{name}: invalid mailbox response for {atom}")
        receipt = response["receipt"]
        snapshot = response["snapshot"]
        entries = {entry["atom_id"]: entry for entry in snapshot["entries"]}
        if (
            receipt["status"] != ("committed" if atom else "profile_selected")
            or snapshot["book_head"] != receipt["after_head"]
            or snapshot["snapshot_sha256"] != receipt["after_snapshot_sha256"]
            or set(entries) != (set(old_entries) | {atom} if atom else set())
            or any(entries[key] != value for key, value in old_entries.items())
        ):
            raise ValueError(f"{name}: changed selection or snapshot for {atom}")
        operations.append(
            dict(
                operation=request["operation"],
                menu_key=request["menu_key"],
                wave=ATOM_WAVES[atom] if atom else None,
                elapsed_ms=round(elapsed_ms, 3),
                within_client_deadline=elapsed_ms <= 2000,
                receipt=receipt,
                snapshot=snapshot,
            )
        )
        old_entries = entries
        previous = receipt
        if atom and len(entries) in (8, 12, 16):
            counts.append(len(entries))
    if counts != [8, 12, 16] or len(bridge.store.records(bridge.book_id)) != 17:
        raise ValueError(f"{name}: incomplete 8/12/16 growth")
    report = dict(
        accepted_counts=counts,
        operations=operations,
        store_log_sha256=hashlib.sha256(
            bridge.store.log_path(bridge.book_id).read_bytes()
        ).hexdigest(),
        journal_sha256=hashlib.sha256(bridge.journal.read_bytes()).hexdigest(),
    )
    (root / "store").rename(root / f"{name}-store")
    mailbox.rename(root / f"{name}-mailbox")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-bridge", type=Path, required=True)
    parser.add_argument("--candidate-bridge", type=Path, required=True)
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="absent or empty local directory; synthetic assets retained",
    )
    args = parser.parse_args(argv)
    root = args.out.resolve()
    if root.exists() and any(root.iterdir()):
        parser.error("--out must be absent or empty")
    root.mkdir(parents=True, exist_ok=True)
    baseline = load_bridge(args.baseline_bridge, "menu_benchmark_baseline")
    candidate = load_bridge(args.candidate_bridge, "menu_benchmark_candidate")
    bank = synthetic_dyad_bank("DEMO-MENU-BENCHMARK")
    bank_path = root / "bank.json"
    bank_path.write_bytes(canonical_json(bank.to_dict()))
    package = build_dyad_package(bank, root / "package")
    config = dict(
        schema_version=1,
        demo_only=True,
        unit_id="DEMO-MENU-BENCHMARK",
        book_id="DEMO-MENU-BENCHMARK",
        bank_path=str(bank_path),
        bank_file_sha256=hashlib.sha256(bank_path.read_bytes()).hexdigest(),
        bank_sha256=bank.bank_sha256(),
        package_path=str(package.path),
        package_sha256=package.package_sha256,
        store_root=str(root / "store"),
    )
    report = dict(
        format="av-sound/menu-store-benchmark",
        format_version=1,
        demo_only=True,
        participant_ready=False,
        timing_qualified=False,
        client_deadline_ms=2000,
        baseline_bridge_sha256=hashlib.sha256(args.baseline_bridge.read_bytes()).hexdigest(),
        candidate_bridge_sha256=hashlib.sha256(args.candidate_bridge.read_bytes()).hexdigest(),
        baseline=run_bridge(baseline, config, root, "baseline"),
        candidate=run_bridge(candidate, config, root, "candidate"),
    )
    report["identical_receipts_and_snapshots"] = all(
        a["receipt"] == b["receipt"] and a["snapshot"] == b["snapshot"]
        for a, b in zip(
            report["baseline"]["operations"], report["candidate"]["operations"], strict=True
        )
    )
    report["identical_durable_log_and_journal"] = (
        report["baseline"]["store_log_sha256"] == report["candidate"]["store_log_sha256"]
        and report["baseline"]["journal_sha256"] == report["candidate"]["journal_sha256"]
    )
    (root / "report.json").write_bytes(
        (json.dumps(report, sort_keys=True, indent=2) + "\n").encode("utf-8")
    )
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in ("baseline", "candidate")}, sort_keys=True
        )
    )
    return (
        0
        if report["identical_receipts_and_snapshots"]
        and report["identical_durable_log_and_journal"]
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
