"""Simulate Study B growth 8 -> 12 -> 16 in the vocabulary store (synthetic books only).

Usage (from the repository root):

    uv run --project sound python sound/tools/store_growth_demo.py            # Markdown report
    uv run --project sound python sound/tools/store_growth_demo.py --full     # full hashes
    uv run --project sound python sound/tools/store_growth_demo.py --write    # rewrite vectors
    uv run --project sound python sound/tools/store_growth_demo.py --check    # exit 1 on drift

For each profile, a store in a temporary directory gets one synthetic book
(`DEMO-P1` .. `DEMO-P3`, `av_sound.synthetic` recipes). The atoms are committed in the
Study B wave order (Protocol constants: wave 1 adds a1, a2, r1, r2 of each family,
wave 2 adds a3, r3, wave 3 adds a4, r4), so the book grows 8 -> 12 -> 16. The tool
takes `snapshot_hashes()` after each wave and checks that every old entry is
unchanged. Then it attempts one overwrite of each kind (recipe, waveform, profile,
meaning), a no-op re-commit, a freeze and a commit to the frozen book, and verifies
the book with re-rendering.

The clock is fixed (1 s per record from 2026-01-01T00:00:00Z) and the reserved set is
empty, so the log bytes and chain heads are identical on every platform. `--write`
stores them in sound/testvectors/store/growth.json and refreshes the example line in
sound/docs/store.md; tests/sound/test_store.py recomputes them in CI on Linux, macOS
and Windows. Line 0 of each book carries renderer_hash and validator_hash, so any
code change to the renderer or validator modules needs `--write` (for example after a
restack onto a changed base). Never commit store directories or
WAV files.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from av_sound import RENDERER_VERSION, VALIDATOR_VERSION, Profile, render
from av_sound.grammar import parse_atom_id
from av_sound.store import (
    SEMANTIC_LABELS,
    BookFrozen,
    OverwriteRejected,
    VocabularyStore,
    persistence_violations,
    snapshot_digest,
)
from av_sound.synthetic import synthetic_book_id, synthetic_recipes

OUT = Path(__file__).resolve().parents[1] / "testvectors" / "store" / "growth.json"
DOC = Path(__file__).resolve().parents[1] / "docs" / "store.md"
FORMAT_VERSION = "1.0.0"
START = datetime(2026, 1, 1, tzinfo=UTC)

WAVES: tuple[tuple[str, ...], ...] = (
    ("K-a1", "K-a2", "K-r1", "K-r2", "Q-a1", "Q-a2", "Q-r1", "Q-r2"),
    ("K-a3", "K-r3", "Q-a3", "Q-r3"),
    ("K-a4", "K-r4", "Q-a4", "Q-r4"),
)
"""Atoms introduced at V1, V2 and V3 (8, 4, 4)."""


def demo_label(atom_id: str) -> str:
    """Synthetic meaning: the identity permutation (matrix index i -> i-th label)."""
    atom = parse_atom_id(atom_id)
    return SEMANTIC_LABELS[(atom.family, atom.role)][atom.index - 1]


def fixed_clock() -> Callable[[], datetime]:
    """A clock that starts at 2026-01-01T00:00:00Z and advances 1 s per call."""
    calls = 0

    def now() -> datetime:
        nonlocal calls
        calls += 1
        return START + timedelta(seconds=calls - 1)

    return now


def run_book(root: Path, profile: Profile) -> dict[str, Any]:
    """Grow one synthetic book in a fresh store under `root` and return the evidence."""
    store = VocabularyStore(root, clock=fixed_clock(), reserved=())
    book = synthetic_book_id(profile)
    recipes = synthetic_recipes(profile)
    create_head = store.create_book(book, profile, kind="synthetic")
    waves: list[dict[str, Any]] = []
    before: dict[str, str] = {}
    for number, atoms in enumerate(WAVES, start=1):
        for atom_id in atoms:
            store.commit(
                book, atom_id, demo_label(atom_id), recipes[atom_id], source=f"demo-{atom_id}"
            )
        after = store.snapshot_hashes(book)
        waves.append(
            {
                "wave": number,
                "added": list(atoms),
                "n_entries": len(after),
                "old_entries": len(before),
                "old_unchanged": sum(after.get(a) == h for a, h in before.items()),
                "violations": list(persistence_violations(before, after)),
                "snapshot": after,
                "snapshot_sha256": snapshot_digest(after),
                "chain_head": store.head(book),
            }
        )
        before = after

    full_before = store.snapshot(book)
    blob_bytes = {a: store.blob_path(h).read_bytes() for a, h in before.items()}
    target = "K-a1"
    original = recipes[target]
    other = recipes["K-a2"]
    other_profile = next(p for p in Profile if p != profile)
    attempts: list[dict[str, Any]] = []
    for kind, kwargs in (
        ("recipe", {"recipe": other}),
        ("waveform", {"recipe": original, "pcm_sha256": render(other, profile).pcm_sha256}),
        ("profile", {"recipe": original, "profile": other_profile}),
        ("semantic_label", {"recipe": original, "semantic_label": demo_label("K-a2")}),
    ):
        args: dict[str, Any] = {"semantic_label": demo_label(target), **kwargs}
        label = args.pop("semantic_label")
        recipe = args.pop("recipe")
        try:
            store.commit(book, target, label, recipe, source=f"demo-overwrite-{kind}", **args)
        except OverwriteRejected as err:
            attempts.append(
                {
                    "attempt": kind,
                    "rejected": True,
                    "reasons": list(err.reasons),
                    "logged_event": err.record["event"],
                    "seq": err.record["seq"],
                }
            )
        else:  # pragma: no cover - would be a store defect
            attempts.append({"attempt": kind, "rejected": False})
    _, noop_head = store.commit(book, target, demo_label(target), original, source="demo-again")
    freeze_head = store.freeze(book)
    try:
        store.commit(book, target, demo_label(target), original, source="demo-after-freeze")
        frozen_rejected = False
    except BookFrozen as err:
        frozen_rejected = err.record["event"] == "commit_rejected_frozen"
    unchanged_after_attempts = store.snapshot(book) == full_before and all(
        store.blob_path(h).read_bytes() == blob_bytes[a] for a, h in before.items()
    )
    report = store.verify(book, rerender=True, expected_head=create_head)
    log = store.log_path(book).read_bytes()
    first_line = log.split(b"\n", 1)[0].decode("ascii")
    return {
        "book_id": book,
        "profile": profile.value,
        "create_head": create_head,
        "waves": waves,
        "overwrite_attempts": attempts,
        "noop_head": noop_head,
        "freeze_head": freeze_head,
        "frozen_rejected": frozen_rejected,
        "entries_unchanged_after_attempts": unchanged_after_attempts,
        "verify_ok": report.ok,
        "verify_codes": list(report.codes),
        "events": [r["event"] for r in store.records(book)],
        "n_records": report.n_records,
        "final_head": report.chain_head,
        "log_bytes": len(log),
        "create_line": first_line,
    }


def build() -> dict[str, Any]:
    books = []
    for profile in Profile:
        with tempfile.TemporaryDirectory(prefix="av-store-demo-") as tmp:
            books.append(run_book(Path(tmp), profile))
            _make_writable(Path(tmp))  # read-only blobs: Windows needs this before cleanup
    return {
        "format_version": FORMAT_VERSION,
        "synthetic": True,
        "description": (
            "Synthetic Study B growth 8 -> 12 -> 16 in the vocabulary store "
            "(sound/tools/store_growth_demo.py). DEMO books only; not study material."
        ),
        "renderer_version": RENDERER_VERSION,
        "validator_version": VALIDATOR_VERSION,
        "clock": "2026-01-01T00:00:00.000Z, +1 s per record",
        "reserved": "none",
        "books": books,
    }


def _make_writable(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file():
            path.chmod(0o644)


def _short(value: str, full: bool) -> str:
    return value if full else value[:16]


def markdown(data: dict[str, Any], *, full: bool = False) -> str:
    lines = [
        "## Study B growth simulation (synthetic DEMO books)",
        "",
        f"Renderer {data['renderer_version']}, validator {data['validator_version']}, "
        "threshold from the book (pilot default 0.10). Fixed clock, no reserved signals.",
    ]
    for book in data["books"]:
        waves = book["waves"]
        lines += ["", f"### {book['book_id']} (profile {book['profile']})", ""]
        for prev, cur in zip(waves, waves[1:], strict=False):
            lines += [
                f"Wave {prev['wave']} -> {cur['wave']} "
                f"({prev['n_entries']} -> {cur['n_entries']} atoms):",
                "",
                "| Atom | Label | pcm_sha256 before | pcm_sha256 after | Same |",
                "| --- | --- | --- | --- | --- |",
            ]
            for atom_id, digest in prev["snapshot"].items():
                after = cur["snapshot"].get(atom_id, "missing")
                lines.append(
                    f"| {atom_id} | {demo_label(atom_id)} | `{_short(digest, full)}` | "
                    f"`{_short(after, full)}` | {'yes' if after == digest else '**NO**'} |"
                )
            added = ", ".join(cur["added"])
            lines += [
                "",
                f"Added: {added}. Old hashes unchanged: "
                f"**{cur['old_unchanged']}/{cur['old_entries']}**; violations: "
                f"{cur['violations'] or 'none'}.",
                "",
            ]
        lines += [
            "| Wave | Entries | Snapshot digest | Chain head |",
            "| --- | --- | --- | --- |",
        ]
        for wave in waves:
            lines.append(
                f"| {wave['wave']} | {wave['n_entries']} | `{wave['snapshot_sha256']}` | "
                f"`{wave['chain_head']}` |"
            )
        lines += [
            "",
            "| Overwrite attempt on K-a1 | Rejected | Reasons | Logged as |",
            "| --- | --- | --- | --- |",
        ]
        for attempt in book["overwrite_attempts"]:
            lines.append(
                f"| {attempt['attempt']} | {'yes' if attempt['rejected'] else '**NO**'} | "
                f"{', '.join(attempt.get('reasons', []))} | "
                f"{attempt.get('logged_event', '-')} (line {attempt.get('seq', '-')}) |"
            )
        lines += [
            "",
            f"- Entries and blobs byte-identical after the attempts: "
            f"{book['entries_unchanged_after_attempts']}",
            f"- Identical re-commit logged as `recommit_noop`; frozen book rejected a commit: "
            f"{book['frozen_rejected']}",
            f"- `verify(rerender=True)`: ok={book['verify_ok']}, {book['n_records']} records, "
            f"final chain head `{book['final_head']}`",
            f"- Events: {', '.join(book['events'])}",
        ]
    return "\n".join(lines) + "\n"


def serialize(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def document(data: dict[str, Any], doc: str) -> str:
    """`sound/docs/store.md` with its example (line 0 of DEMO-P1 and its hash) updated."""
    book = data["books"][0]
    lines = doc.split("\n")
    for i, line in enumerate(lines):
        if line.startswith('{"book_id":"DEMO-P1","event":"create_book"'):
            lines[i] = book["create_line"]
        elif line.startswith("`") and line.endswith("` (`create_head` in"):
            lines[i] = f"`{book['create_head']}` (`create_head` in"
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--write", action="store_true", help=f"rewrite {OUT.name} and the example in {DOC.name}"
    )
    group.add_argument(
        "--check", action="store_true", help="exit 1 if the vectors or the example drifted"
    )
    parser.add_argument("--full", action="store_true", help="print full hashes")
    args = parser.parse_args(argv)
    data = build()
    text = serialize(data)
    doc = DOC.read_text(encoding="utf-8")
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        for path, content in ((OUT, text), (DOC, document(data, doc))):
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
            print(f"wrote {path}")
        return 0
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text or document(data, doc) != doc:
            print(f"{OUT} or the example in {DOC} differs from a fresh build", file=sys.stderr)
            return 1
        print(f"{OUT} and the example in {DOC.name} are up to date")
        return 0
    sys.stdout.write(markdown(data, full=args.full))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
