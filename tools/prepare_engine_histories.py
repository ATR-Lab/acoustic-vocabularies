"""Build private DEMO inputs for the engine-produced full-history export (#78).

Synthetic only: the public curriculum example seed (`DEMO-o4.4.1-example`, the seed of
the committed DEMO schedules and allocation lists), its first Study A unit (A-C01) and
first Study B dyad (B-C01). Nothing here is study material or a confirmatory bank.

    python tools/prepare_engine_histories.py --out .local/engine-histories/fixtures

Writes, into a new directory:

- `package-a/`: the synthetic `DEMO-BOOK-P1` book packaged with the A-C01 meanings and
  sealed with A-C01's permutation and every learner's D0/D7 schedule;
- `package-b/`: the synthetic provisional DEMO dyad bank with the B-C01 meanings,
  sealed with B-C01's permutation, both members' V1/V2/V3/W1/W4 schedules and the
  allocation extras of the committed DEMO allocation;
- `examples/calibration-P1..P3.wav`: the fixed #14 reserved calibration examples;
- `fixtures.local.json` (+ `.sha256`): relative paths and independent pins for the
  Unity driver (`EngineHistoryExportTests`, `AV_ENGINE_HISTORY_FIXTURES`).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "sound/src"), str(ROOT / "schedules/src")]
from tools.prepare_joined_engineering import digest, json_bytes, local_path, write_new

SEED = "DEMO-o4.4.1-example"
ALLOCATION = ROOT / "schedules/examples/demo-allocation/B/confirmatory-dyads.json"
REGISTRY = ROOT / "sound/reserved/registry.json"


def _example_module():
    spec = importlib.util.spec_from_file_location("engine_history_example", ROOT / "sound/tools/build_example_package.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare(output):
    from av_schedules.curriculum import permutation_json
    from av_schedules.design import build_units
    from av_schedules.orders import unit_schedules, visit_schedule_json
    from av_schedules.seeds import demo_seed
    from av_sound.dyad_bank import synthetic_dyad_bank
    from av_sound.nonlexical import calibration_example
    from av_sound.package import build_dyad_package, build_package, load_package, seal
    from av_sound.wav import write_wav

    example = _example_module()
    output = local_path(output)
    output.mkdir(parents=True, exist_ok=False)
    master = demo_seed(SEED)
    unit_a = build_units(master, "A", "confirmatory")[0]
    unit_b = build_units(master, "B", "confirmatory")[0]
    allocation = json.loads(ALLOCATION.read_bytes())
    dyad = [d for d in allocation["dyads"] if d["unit_id"] == unit_b.unit_id]
    assert allocation["demo"] is True and len(dyad) == 1
    assert dyad[0]["swap_w1_w4"] == unit_b.swap_w1_w4 and dyad[0]["structured_family"] == unit_b.structured_family

    packages = {}
    for key, unit in (("A", unit_a), ("B", unit_b)):
        permutation = permutation_json(unit)
        labels = {row["atom_id"]: row["semantic_label"] for row in json.loads(permutation)["atoms"]}
        directory = output / f"package-{key.lower()}"
        if key == "A":
            store = example.demo_store_book(output / "store-a", labels=labels)
            build_package(store, example.BOOK_ID, directory)
            extras = {"swap_w1_w4": unit.swap_w1_w4}
        else:
            build_dyad_package(synthetic_dyad_bank("DEMO-DYAD-01", labels=labels), directory)
            extras = {"swap_w1_w4": unit.swap_w1_w4, "structured_family": unit.structured_family}
        schedules = unit_schedules(master, unit)
        seal(directory, permutation=permutation,
             schedules={name: visit_schedule_json(doc) for name, doc in schedules.items()},
             allocation_extras=extras)
        package = load_package(directory)
        assert package.demo
        persons = sorted({name.split("/")[0] for name in schedules})
        packages[key] = {"directory": directory.name, "package_sha256": package.package_sha256,
                         "unit_id": unit.unit_id, "persons": persons}
    example.make_writable(output / "store-a")
    examples = output / "examples"
    examples.mkdir()
    for profile in ("P1", "P2", "P3"):
        asset = calibration_example(profile)
        write_wav(asset, examples / f"{asset.id}.wav")
    roles = {m["slot_id"]: m["role"] for m in dyad[0]["members"]}
    raw = json_bytes({
        "version": 1, "scope": "DEMO_ENGINE_HISTORY", "synthetic": True, "seed_label": SEED,
        "packages": packages, "examples": examples.name,
        "allocation": {"path": ALLOCATION.relative_to(ROOT).as_posix(), "sha256": digest(ALLOCATION.read_bytes())},
        "registry": {"path": REGISTRY.relative_to(ROOT).as_posix(), "sha256": digest(REGISTRY.read_bytes())},
        "study_a_person": packages["A"]["persons"][0],
        "study_b_roles": roles,
    })
    write_new(output / "fixtures.local.json", raw)
    write_new(output / "fixtures.local.json.sha256", (digest(raw) + "\n").encode())
    return digest(raw)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(prepare(args.out.resolve()))
