"""Build the synthetic example packages (DEMO only, never study material).

Usage (from the repository root):

    uv run --project sound python sound/tools/build_example_package.py --out DIR [--dyad]
    uv run --project sound python sound/tools/build_example_package.py --check
    uv run --project sound python sound/tools/build_example_package.py --write

Study A example: the synthetic book `DEMO-BOOK-P1` (`av_sound.synthetic` recipes of P1) is
committed to a store in a temporary directory with the meanings of the DEMO permutation
A-C01 (`tests/sound/fixtures/schedules-demo/`, copied from the schedules stack), frozen,
packaged with `build_package` and sealed with that permutation and
`allocation_extras={"swap_w1_w4": False}`. The store clock is fixed, so every byte is the
same on every platform.

`--out DIR` writes the complete package, WAVs included, to `DIR/package-demo`; with
`--dyad` it also writes the synthetic dyad package `DIR/dyad-demo` (`synthetic_dyad_bank`
with the B-C01 meanings, sealed with the B-C01 permutation, schedules of member M1 and
`{"swap_w1_w4": True, "structured_family": "Q"}`), plus the run-sheet package-hash
mappings `DIR/A-confirmatory-package-hashes.json` and `DIR/B-...` (#32). `--check`
rebuilds the Study A example and exits 1 if its JSON files differ from
`sound/examples/package-demo/`; `--write` rewrites them. Only the JSON files are
committed: WAV files never enter git.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from av_sound import Profile, VocabularyStore
from av_sound.dyad_bank import synthetic_dyad_bank
from av_sound.grammar import ATOM_IDS, HELDOUT_MESSAGE_IDS, TRAINED_MESSAGE_IDS
from av_sound.package import (
    PackageResult,
    build_dyad_package,
    build_package,
    load_package,
    package_hashes,
    scan_package,
    seal,
    write_package_hashes,
)
from av_sound.synthetic import synthetic_recipes

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "sound" / "examples" / "package-demo"
FIXTURES = REPO / "tests" / "sound" / "fixtures" / "schedules-demo"
A_UNIT = FIXTURES / "A-C01"
B_UNIT = FIXTURES / "B-C01"
BOOK_ID = "DEMO-BOOK-P1"
BANK_ID = "DEMO-DYAD-01"
A_ALLOCATION = {"swap_w1_w4": False}
B_ALLOCATION = {"swap_w1_w4": True, "structured_family": "Q"}
A_KEY = {BOOK_ID: "BK-C-B4K7QX"}
"""A slot-list book ID for the DEMO book in the run-sheet mapping (DEMO only)."""
COMMITTED = ("allocation.json", "answers.json", "audio.json", "manifest.json", "permutation.json")
"""The JSON files of the example that are committed (no WAVs)."""
START = datetime(2026, 1, 1, tzinfo=UTC)


def fixed_clock() -> Callable[[], datetime]:
    """2026-01-01T00:00:00Z, then 1 s later at each call."""
    calls = iter(range(1_000_000))
    return lambda: START + timedelta(seconds=next(calls))


def permutation_labels(unit: Path) -> dict[str, str]:
    """Atom ID -> semantic label from a unit's permutation.json."""
    doc = json.loads((unit / "permutation.json").read_text(encoding="utf-8"))
    return {a["atom_id"]: a["semantic_label"] for a in doc["atoms"]}


def demo_store_book(
    root: Path,
    *,
    profile: Profile = Profile.P1,
    book_id: str = BOOK_ID,
    labels: dict[str, str] | None = None,
) -> VocabularyStore:
    """A store under `root` with one frozen synthetic book (fixed clock, no reserved set)."""
    store = VocabularyStore(root, clock=fixed_clock(), reserved=())
    store.create_book(book_id, profile, kind="synthetic")
    meanings = labels if labels is not None else permutation_labels(A_UNIT)
    recipes = synthetic_recipes(profile)
    for atom in ATOM_IDS:
        store.commit(book_id, atom, meanings[atom], recipes[atom], source=f"demo-slot-{atom}")
    store.freeze(book_id)
    return store


def make_writable(path: Path) -> None:
    """Store blobs are read-only at rest; allow clean-up (Windows)."""
    for p in path.rglob("*"):
        if p.is_file():
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)


def build_a_example(out: Path) -> PackageResult:
    """Build and seal the Study A example at `out` (must not exist)."""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            store = demo_store_book(Path(tmp) / "store")
            result = build_package(store, BOOK_ID, out)
        finally:
            make_writable(Path(tmp))
    seal(out, permutation=A_UNIT / "permutation.json", allocation_extras=A_ALLOCATION)
    return result


def build_b_example(out: Path) -> PackageResult:
    """Build and seal the synthetic dyad example at `out` (must not exist)."""
    bank = synthetic_dyad_bank(BANK_ID, labels=permutation_labels(B_UNIT))
    result = build_dyad_package(bank, out)
    seal(
        out,
        permutation=B_UNIT / "permutation.json",
        schedules=B_UNIT / "schedules",
        allocation_extras=B_ALLOCATION,
    )
    return result


def summary(path: Path) -> str:
    pkg = load_package(path)
    leak = scan_package(path)
    folders: dict[str, int] = {}
    for rel in pkg.files:
        if rel.endswith(".wav"):
            folder = rel.split("/")[0]
            folders[folder] = folders.get(folder, 0) + 1
    wavs = ", ".join(f"{n} in {folder}/" for folder, n in sorted(folders.items()))
    messages = [r.removeprefix("messages/").removesuffix(".wav") for r in pkg.files]
    trained = [m for m in messages if m in TRAINED_MESSAGE_IDS]
    heldout = [m for m in messages if m in HELDOUT_MESSAGE_IDS]
    return "\n".join(
        [
            f"### {pkg.package_id} (Study {pkg.study}, demo={str(pkg.demo).lower()})",
            f"- package_sha256 `{pkg.package_sha256}`",
            f"- {len(pkg.files)} files besides manifest.json; WAVs: {wavs}",
            f"- message WAVs: {len(trained)} trained, {len(heldout)} held out",
            f"- combinations checked (hash and length, no playback): {pkg.combinations_checked}",
            f"- leak scan: ok={str(leak.ok).lower()}, held-out audio {leak.heldout_audio}, "
            f"method/designer strings {leak.method_strings}, "
            f"{leak.heldout_hashes} held-out composite hashes, {leak.files_scanned} files",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--out", type=Path, help="write the complete example package(s) here")
    mode.add_argument("--check", action="store_true", help="compare with the committed JSON")
    mode.add_argument("--write", action="store_true", help="rewrite the committed JSON")
    parser.add_argument("--dyad", action="store_true", help="with --out: also the dyad package")
    args = parser.parse_args()
    if args.out is not None:
        build_a_example(args.out / "package-demo")
        print(summary(args.out / "package-demo"))
        a_map = package_hashes([args.out / "package-demo"], set_name="confirmatory", keys=A_KEY)
        write_package_hashes(a_map, args.out / "A-confirmatory-package-hashes.json")
        if args.dyad:
            build_b_example(args.out / "dyad-demo")
            print(summary(args.out / "dyad-demo"))
            b_map = package_hashes([args.out / "dyad-demo"], set_name="confirmatory")
            write_package_hashes(b_map, args.out / "B-confirmatory-package-hashes.json")
        print("- run-sheet package-hash mappings (#32): A-/B-confirmatory-package-hashes.json")
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "package-demo"
        build_a_example(out)
        fresh = {name: (out / name).read_bytes() for name in COMMITTED}
    if args.write:
        EXAMPLE.mkdir(parents=True, exist_ok=True)
        for name, data in fresh.items():
            (EXAMPLE / name).write_bytes(data)
        print(f"wrote {len(fresh)} files to {EXAMPLE.name}/")
        return 0
    stale = [
        n
        for n, d in fresh.items()
        if not (EXAMPLE / n).is_file() or (EXAMPLE / n).read_bytes() != d
    ]
    if stale:
        print(f"example JSON differs from a fresh build: {stale}", file=sys.stderr)
        return 1
    print(f"example JSON matches a fresh build ({len(fresh)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
