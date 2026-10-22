"""Seeded synthetic #26 bank and qualified dyad package for #70 hash-check tests.

DEMO only, never study material: no confirmatory bank, allocation list, meaning text or
model output is read or written. From the repository root, in the banks environment:

    uv run --project banks python tools/qualified_bank_fixture.py --out .local/qualified-bank-demo

1. Builds one complete DEMO bank with the real bank builder (`av_banks.run.build_banks`,
   the function behind `banks build`): the DEMO generation config and the seeded
   `DemoSlotProposer` of the confirmatory rehearsal (random recipes drawn from each slot's
   seed key, no model), on the DEMO unit B-C01 permutation copied from the schedules
   examples, one worker, a manual clock. The same checkout gives the same bank hash.
2. `av_banks.handoff.qualified_dyad_bank` verifies it (`verify_bank`, pinned hash, no
   amendments) and converts it; `build_dyad_package` writes `dyad-qualified/` recording
   `bank: {format: av-banks/bank-manifest, format_version: 1, bank_sha256}`, sealed with
   the same B-C01 permutation, member M1 schedules and DEMO allocation extras as the
   provisional `dyad-demo` example.
3. Writes the three fixed #14 `calibration-P*` examples (`examples/`, the registry's
   public reserved assets, needed by the profile menu) and `fixture.json` with the
   relative paths and the independent pins the Unity tests use
   (`AV_QUALIFIED_BANK_ROOT`, default `<repo>/.local/qualified-bank-demo`).

`--out` must not exist (or be empty). Keep it in ignored local storage; never commit it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from av_generation.clock import ManualClock
from av_sound.nonlexical import calibration_example
from av_sound.package import build_dyad_package, load_package, seal
from av_sound.wav import write_wav

from av_banks.builder import bank_spec
from av_banks.confirmatory.rehearsal import DemoSlotProposer, demo_config
from av_banks.handoff import qualified_dyad_bank
from av_banks.permutation import load_permutation
from av_banks.run import build_banks

REPO = Path(__file__).resolve().parents[1]
UNIT = REPO / "tests" / "sound" / "fixtures" / "schedules-demo" / "B-C01"
BANK_ID = "DEMO-QBANK-01"
RUN_ID = "DEMO-qualified-bank-01"
ALLOCATION = {"swap_w1_w4": True, "structured_family": "Q"}
FORMAT = "av-qualified-bank-fixture/1"


def build(out: Path) -> dict[str, object]:
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} exists and is not empty")
    out.mkdir(parents=True, exist_ok=True)
    clock = ManualClock()
    config = demo_config()
    permutation = load_permutation(UNIT / "permutation.json")
    run = build_banks(
        [bank_spec(BANK_ID, permutation)],
        runs_root=out / "runs",
        run_id=RUN_ID,
        config=config,
        proposer=DemoSlotProposer(mode="valid", clock=clock),
        clock=clock,
        workers=1,
        fsync=False,
    )
    (result,) = run.banks
    if result.status != "complete":
        raise SystemExit(f"{BANK_ID} ended {result.status}; no qualified fixture")
    bank_dir = Path(result.bank_dir)
    bank = qualified_dyad_bank(bank_dir, expected_bank_sha256=result.bank_sha256)
    package_dir = out / "dyad-qualified"
    build_dyad_package(bank, package_dir)
    package_sha256 = seal(
        package_dir,
        permutation=UNIT / "permutation.json",
        schedules=UNIT / "schedules",
        allocation_extras=ALLOCATION,
    )
    loaded = load_package(package_dir, expected_package_sha256=package_sha256)
    examples = out / "examples"
    examples.mkdir()
    for profile in ("P1", "P2", "P3"):
        asset = calibration_example(profile)
        write_wav(asset, examples / f"{asset.id}.wav")
    manifest_bytes = (bank_dir / "manifest.json").read_bytes()
    doc: dict[str, object] = {
        "format": FORMAT,
        "synthetic": True,
        "demo": True,
        "participant_ready": False,
        "note": "seeded DEMO bank, real builder, scripted proposer; not study material",
        "bank_id": BANK_ID,
        "bank_dir": bank_dir.relative_to(out).as_posix(),
        "bank_sha256": result.bank_sha256,
        "bank_manifest_file_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "package_dir": package_dir.relative_to(out).as_posix(),
        "package_sha256": loaded.package_sha256,
        "examples_dir": examples.relative_to(out).as_posix(),
        "permutation_sha256": hashlib.sha256(permutation.data).hexdigest(),
    }
    (out / "fixture.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", "utf-8")
    return doc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True, help="new local output directory")
    args = parser.parse_args()
    doc = build(args.out)
    json.dump(doc, sys.stdout, indent=2, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
