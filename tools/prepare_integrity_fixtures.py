"""Build private DEMO integrity fixtures with the real sound/schedule producers."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "sound/src"), str(ROOT / "schedules/src")]
from tools.prepare_joined_engineering import digest, json_bytes, local_path, write_new


def prepare(output):
    from av_schedules.curriculum import permutation_json
    from av_schedules.design import build_units
    from av_schedules.orders import unit_schedules, visit_schedule_json
    from av_schedules.seeds import demo_seed
    from av_sound.package import build_package, load_package, seal
    spec = importlib.util.spec_from_file_location("integrity_example", ROOT / "sound/tools/build_example_package.py")
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    output = local_path(output)
    output.mkdir(parents=True, exist_ok=False)
    master = demo_seed("DEMO-integrity-01")
    packages = []
    for index, unit in enumerate(build_units(master, "A", "confirmatory")[:3]):
        permutation = permutation_json(unit)
        meanings = {row["atom_id"]: row["semantic_label"] for row in json.loads(permutation)["atoms"]}
        store = example.demo_store_book(output / f"store-{index}", labels=meanings)
        directory = output / f"package-{index}"
        build_package(store, example.BOOK_ID, directory)
        seal(directory, permutation=permutation,
             schedules={key: visit_schedule_json(doc) for key, doc in unit_schedules(master, unit).items()},
             allocation_extras={"swap_w1_w4": unit.swap_w1_w4})
        package = load_package(directory)
        assert package.demo and package.combinations_checked == 32
        packages.append({"directory": directory.name, "package_sha256": package.package_sha256})
    raw = json_bytes({"version": 1, "scope": "DEMO_INTEGRITY", "packages": packages})
    write_new(output / "fixtures.local.json", raw)
    write_new(output / "fixtures.local.json.sha256", (digest(raw) + "\n").encode())
    return digest(raw)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(prepare(args.out.resolve()))
