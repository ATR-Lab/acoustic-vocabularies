"""O4.1 integration demo: three synthetic books from recipes to sealed packages (DEMO only).

Usage (from the repository root):

    uv run --project sound python sound/tools/integration_demo.py --out DIR
    uv run --project sound python sound/tools/integration_demo.py --check
    uv run --project sound python sound/tools/integration_demo.py --write

For each profile (P1, P2, P3), with no manual step:

1. render and validate the 16 synthetic recipes (`av_sound.synthetic`) by committing them
   to an append-only store under the default reserved-signal registry
   (`sound/reserved/registry.json`), with the meanings of the DEMO permutation A-C01;
   any rejection stops the demo;
2. freeze the book and verify the store (chain, blobs, re-render, re-validation);
3. check that all 32 legal messages last 1.1-2.0 s (52,800-96,000 samples at 48 kHz),
   from the committed atoms' sample counts; held-out messages are never composed;
4. build the learner package, seal it with the A-C01 permutation, reload it (hash and
   length checks of every combination) and run the leak scan, which must find 0
   held-out complete-message audio files and 0 method or designer strings.

The result is a hash table per book (`summary.json`, `hash-table.md`). `--out DIR`
writes the stores, packages and both summaries to `DIR`; `--check` rebuilds in a
temporary directory and exits 1 if the summary differs from
`sound/examples/integration-demo/summary.json`; `--write` rewrites that file. The store
clock is fixed, so every byte is the same on every platform. Stores and WAVs never
enter git; only the summary JSON is committed.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any

from av_sound import (
    MAX_MESSAGE_SAMPLES,
    MIN_MESSAGE_SAMPLES,
    RENDERER_VERSION,
    Profile,
    VocabularyStore,
    build_package,
    load_package,
    message_length,
    renderer_hash,
    scan_package,
    seal,
)
from av_sound.grammar import ATOM_IDS, HELDOUT_MESSAGE_IDS, MESSAGES, TRAINED_MESSAGE_IDS
from av_sound.synthetic import synthetic_recipes

REPO = Path(__file__).resolve().parents[2]
TOOLS = Path(__file__).resolve().parent
SUMMARY = REPO / "sound" / "examples" / "integration-demo" / "summary.json"
FORMAT = "av-sound/integration-demo"
FORMAT_VERSION = 1


def _example_tool() -> ModuleType:
    """`build_example_package.py`: the DEMO clock, permutation and allocation."""
    spec = importlib.util.spec_from_file_location(
        "build_example_package", TOOLS / "build_example_package.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EXAMPLE = _example_tool()


def book_id(profile: Profile) -> str:
    return f"DEMO-BOOK-{profile.value}"


def commit_book(store: VocabularyStore, profile: Profile, labels: dict[str, str]) -> str:
    """Commit the 16 synthetic recipes of `profile` (validated on commit) and freeze."""
    book = book_id(profile)
    store.create_book(book, profile, kind="synthetic")
    recipes = synthetic_recipes(profile)
    for atom in ATOM_IDS:
        store.commit(book, atom, labels[atom], recipes[atom], source=f"demo-slot-{atom}")
    return store.freeze(book)


def message_lengths(store: VocabularyStore, book: str) -> dict[str, int]:
    """Message ID -> length in samples for all 32 legal messages (metadata only)."""
    entries = {e.atom_id: e for e in store.list(book)}
    return {
        m.message_id: message_length(entries[m.action.atom_id], entries[m.referent.atom_id])
        for m in MESSAGES
    }


def run_book(out: Path, profile: Profile, labels: dict[str, str]) -> dict[str, Any]:
    """Steps 1-4 for one profile under `out`; raises on the first failed check."""
    book = book_id(profile)
    store = VocabularyStore(out / f"store-{profile.value}", clock=EXAMPLE.fixed_clock())
    frozen_head = commit_book(store, profile, labels)
    report = store.verify(book, rerender=True, expected_head=frozen_head)
    if not report.ok:
        raise SystemExit(f"{book}: store verification failed: {report.issues}")
    lengths = message_lengths(store, book)
    outside = {
        m: n for m, n in lengths.items() if not MIN_MESSAGE_SAMPLES <= n <= MAX_MESSAGE_SAMPLES
    }
    if len(lengths) != 32 or outside:
        raise SystemExit(f"{book}: messages outside 1.1-2.0 s: {outside}")
    package_dir = out / f"package-{profile.value}"
    build_package(store, book, package_dir)
    sealed = seal(
        package_dir,
        permutation=EXAMPLE.A_UNIT / "permutation.json",
        allocation_extras=EXAMPLE.A_ALLOCATION,
    )
    pkg = load_package(package_dir)
    sources = [e.source for e in store.list(book)]
    leak = scan_package(package_dir, forbidden_strings=sources)
    if not leak.ok or leak.heldout_audio:
        raise SystemExit(f"{book}: leak scan failed: {leak.to_dict()}")
    message_wavs = sorted(
        r.removeprefix("messages/").removesuffix(".wav")
        for r in pkg.files
        if r.startswith("messages/")
    )
    if message_wavs != sorted(TRAINED_MESSAGE_IDS):
        raise SystemExit(f"{book}: package message audio is not exactly the 18 trained messages")
    entries = store.list(book)
    return {
        "book_id": book,
        "profile": profile.value,
        "atoms_committed": len(entries),
        "frozen_head": frozen_head,
        "store_verified": report.ok,
        "atom_pcm_sha256": {e.atom_id: e.pcm_sha256 for e in entries},
        "messages": {
            "legal": len(lengths),
            "min_samples": min(lengths.values()),
            "max_samples": max(lengths.values()),
            "outside_1_1_to_2_0_s": len(outside),
        },
        "package": {
            "package_id": pkg.package_id,
            "package_sha256": sealed,
            "trained_message_wavs": len(message_wavs),
            "heldout_message_wavs": len(set(message_wavs) & set(HELDOUT_MESSAGE_IDS)),
            "combinations_checked": pkg.combinations_checked,
        },
        "leak_scan": {
            "ok": leak.ok,
            "heldout_audio": leak.heldout_audio,
            "method_strings": leak.method_strings,
            "heldout_hashes": leak.heldout_hashes,
            "files_scanned": leak.files_scanned,
        },
    }


def run_demo(out: Path) -> dict[str, Any]:
    """The whole demo under `out` (must be empty or absent); returns the summary."""
    out.mkdir(parents=True, exist_ok=True)
    labels = EXAMPLE.permutation_labels(EXAMPLE.A_UNIT)
    try:
        books = [run_book(out, profile, labels) for profile in Profile]
    finally:
        EXAMPLE.make_writable(out)
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "demo": True,
        "renderer_version": RENDERER_VERSION,
        "renderer_hash": renderer_hash(),
        "books": books,
    }


def summary_bytes(summary: dict[str, Any]) -> bytes:
    return (json.dumps(summary, indent=2, sort_keys=True) + "\n").encode("utf-8")


def hash_table(summary: dict[str, Any]) -> str:
    lines = [
        "### O4.1 integration demo (synthetic books, DEMO only)",
        f"- renderer {summary['renderer_version']}, renderer_hash `{summary['renderer_hash']}`",
        "",
        "| book | atoms | store verified | messages (samples) | trained WAVs | held-out WAVs "
        "| leak scan | frozen head | package_sha256 |",
        "| --- | ---: | --- | --- | ---: | ---: | --- | --- | --- |",
    ]
    for b in summary["books"]:
        msg, pkg, leak = b["messages"], b["package"], b["leak_scan"]
        lines.append(
            f"| {b['book_id']} | {b['atoms_committed']} | {str(b['store_verified']).lower()} "
            f"| {msg['legal']} legal, {msg['min_samples']}-{msg['max_samples']} "
            f"| {pkg['trained_message_wavs']} | {pkg['heldout_message_wavs']} "
            f"| ok={str(leak['ok']).lower()}, held-out audio {leak['heldout_audio']} "
            f"| `{b['frozen_head'][:12]}` | `{pkg['package_sha256'][:12]}` |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--out", type=Path, help="write stores, packages and summaries here")
    mode.add_argument("--check", action="store_true", help="compare with the committed summary")
    mode.add_argument("--write", action="store_true", help="rewrite the committed summary")
    args = parser.parse_args(argv)
    if args.out is not None:
        if args.out.exists() and any(args.out.iterdir()):
            parser.error(f"{args.out} is not empty")
        summary = run_demo(args.out)
        (args.out / "summary.json").write_bytes(summary_bytes(summary))
        table = hash_table(summary)
        (args.out / "hash-table.md").write_text(table, encoding="utf-8", newline="\n")
        print(table, end="")
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        summary = run_demo(Path(tmp) / "demo")
    fresh = summary_bytes(summary)
    if args.write:
        SUMMARY.parent.mkdir(parents=True, exist_ok=True)
        SUMMARY.write_bytes(fresh)
        print(f"wrote {SUMMARY.relative_to(REPO).as_posix()}")
        return 0
    if not SUMMARY.is_file() or SUMMARY.read_bytes() != fresh:
        print("integration demo summary differs from a fresh build", file=sys.stderr)
        return 1
    print(hash_table(summary), end="")
    print("- summary.json matches a fresh build")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
