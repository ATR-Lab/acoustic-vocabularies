"""Measure 17 real mailbox operations using an existing pinned qualified DEMO bank.

The source bank, package, configuration and store are read only. A new dedicated
store and mailbox remain under --out. Startup verification is recorded separately
before publishing requests. Use an otherwise quiet host for timing.
Exit 0 means all invariants passed within the unchanged 2,000 ms deadline.
Exit 2 retains a valid diagnostic report with one or more deadline misses.
Neither result qualifies participant timing or apparatus behavior.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import menu_store_bridge as bridge_module  # noqa: E402
from tools.menu_store_benchmark import run_bridge  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument("--out", type=Path, required=True, help="fresh dedicated local directory")
    args = parser.parse_args(argv)
    raw = args.config.read_bytes()
    bridge_module.require(hashlib.sha256(raw).hexdigest() == args.config_sha256, "CONFIG_FILE_HASH")
    config = bridge_module.strict_loads(raw)
    source_bridge = bridge_module.MenuStoreBridge(config)
    bank, _, _ = source_bridge._inputs()
    bridge_module.require(bank.qualified, "QUALIFIED_BANK_REQUIRED")
    root = bridge_module._local_path(args.out, directory=True)
    bridge_module.require(not root.exists(), "BENCHMARK_EXISTS")
    for protected in (
        Path(config["bank_path"]).resolve().parent,
        Path(config["package_path"]).resolve(),
        Path(config["store_root"]).resolve(),
    ):
        bridge_module.require(
            root != protected and protected not in root.parents, "BENCHMARK_SOURCE_OVERLAP"
        )
    root.mkdir(parents=True)
    config["store_root"] = str(root / "store")
    bridge_module.write_new(root / "config.json", config)
    result = run_bridge(bridge_module, config, root, "qualified", startup_check=True)
    elapsed = [row["elapsed_ms"] for row in result["operations"]]
    within = all(row["within_client_deadline"] for row in result["operations"])
    report = dict(
        format="av-sound/qualified-menu-store-benchmark",
        format_version=1,
        qualified_bank=True,
        demo_only=True,
        participant_ready=False,
        timing_qualified=False,
        client_deadline_ms=2000,
        source_config_file_sha256=args.config_sha256,
        bridge_source_sha256=hashlib.sha256(Path(bridge_module.__file__).read_bytes()).hexdigest(),
        bank_manifest_file_sha256=config["bank_file_sha256"],
        bank_sha256=config["bank_sha256"],
        package_sha256=config["package_sha256"],
        within_client_deadline=within,
        max_elapsed_ms=max(elapsed),
        result=result,
    )
    bridge_module.write_new(root / "report.json", report)
    print(json.dumps({k: v for k, v in report.items() if k != "result"}, sort_keys=True))
    return 0 if within else 2


if __name__ == "__main__":
    raise SystemExit(main())
