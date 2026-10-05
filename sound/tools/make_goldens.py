"""Write or check the golden manifest tests/golden/manifest.json (#12; synthetic only).

Usage (from the repository root):

    uv run --project sound python sound/tools/make_goldens.py            # rewrite the manifest
    uv run --project sound python sound/tools/make_goldens.py --check    # exit 1 on any drift
    uv run --project sound python sound/tools/make_goldens.py --check --summary \
        --runner linux-x86_64 --digest-out OUT/linux-x86_64.json --wav-dir WAVS

The golden set is defined in `av_sound.golden`: 61 synthetic recipes x 3 profiles,
the atoms and all 32 messages of the three synthetic DEMO books, the seven
nonlexical assets and a vocabulary-store round trip per book.

`--check` recomputes every item twice: from the inputs recorded in the committed
manifest (do the hashes reproduce on this machine?) and from the definitions in code
(is the manifest up to date?). `--digest-out` writes this machine's digests for the
cross-runner comparison (`compare_golden_digests.py`). `--wav-dir` writes the golden
WAVs (never commit them; Git LFS is disabled) and checks them against the hashes.

Changing an existing golden hash needs a version bump in the same pull request
(`check_golden_bump.py`, `sound/docs/golden.md`).
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

import numpy

from av_sound import golden

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "tests" / "golden" / "manifest.json"
MAX_REPORTED = 40


def runner_record(label: str, fresh: dict, committed: dict | None, mismatches: int) -> dict:
    """This machine's digests and platform facts, for the CI compare job."""
    return {
        "runner": label,
        "system": platform.system(),
        "machine": platform.machine(),
        "platform": platform.platform(),
        "byteorder": sys.byteorder,
        "python": platform.python_version(),
        "numpy": numpy.__version__,
        "renderer_version": fresh["renderer_version"],
        "renderer_hash": fresh["renderer_hash"],
        "counts": fresh["counts"],
        "digests": fresh["digests"],
        "manifest_digests": None if committed is None else committed.get("digests"),
        "matches_manifest": committed is not None and mismatches == 0,
        "mismatches": mismatches,
    }


def summary(record: dict) -> str:
    lines = [
        f"### Golden set on {record['runner']} ({record['system']} {record['machine']})",
        "",
        f"Python {record['python']}, numpy {record['numpy']}, renderer "
        f"{record['renderer_version']} (`{record['renderer_hash'][:16]}`), "
        f"byte order {record['byteorder']}.",
        "",
        "| Category | Items | Digest of live results | Equals manifest |",
        "| --- | --- | --- | --- |",
    ]
    manifest = record["manifest_digests"] or {}
    for category in ("all", *golden.CATEGORIES):
        n = sum(record["counts"].values()) if category == "all" else record["counts"][category]
        digest = record["digests"][category]
        same = "yes" if manifest.get(category) == digest else "**NO**"
        lines.append(f"| {category} | {n} | `{digest}` | {same} |")
    verdict = (
        "all golden hashes reproduce"
        if record["matches_manifest"]
        else (f"**{record['mismatches']} mismatches**")
    )
    lines += ["", f"Result: {verdict}.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare instead of writing")
    parser.add_argument("--manifest", type=Path, default=MANIFEST, help="manifest path")
    parser.add_argument("--wav-dir", type=Path, help="also write the golden WAVs here")
    parser.add_argument("--digest-out", type=Path, help="write this runner's digest record")
    parser.add_argument("--runner", default=platform.system().lower(), help="runner label")
    parser.add_argument("--summary", action="store_true", help="print a Markdown summary")
    args = parser.parse_args(argv)

    items = golden.compute_items(golden.golden_specs())
    fresh = golden.manifest_from_items(items)
    text = golden.manifest_text(fresh)
    committed: dict | None = None
    problems: list[str] = []
    if args.check:
        if not args.manifest.is_file():
            problems.append(f"{args.manifest} does not exist; run without --check")
        else:
            committed = golden.load_manifest(args.manifest)
            # 1. Recompute from the recorded inputs; 2. compare with the code definitions.
            problems += [f"reproduce: {m}" for m in golden.verify_manifest(committed)]
            if args.manifest.read_text(encoding="utf-8") != text:
                drift = golden.compare_items(committed["items"], items)
                problems += [f"out of date: {m}" for m in drift] or [
                    "out of date: the manifest text differs from a fresh build "
                    "(header or formatting); rerun without --check"
                ]
    else:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        with open(args.manifest, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        committed = fresh
        print(f"wrote {args.manifest} ({len(items)} items)", file=sys.stderr)

    if args.wav_dir is not None:
        count = golden.write_wavs(items, args.wav_dir)
        problems += [
            f"wav: {m}" for m in golden.check_wav_dir(items, args.wav_dir, require_all=True)
        ]
        with open(args.wav_dir / "manifest.json", "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print(f"wrote {count} WAV files under {args.wav_dir}", file=sys.stderr)

    record = runner_record(args.runner, fresh, committed, len(problems))
    if args.digest_out is not None:
        args.digest_out.parent.mkdir(parents=True, exist_ok=True)
        with open(args.digest_out, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
    if args.summary:
        sys.stdout.write(summary(record))
    for problem in problems[:MAX_REPORTED]:
        print(problem, file=sys.stderr)
    if len(problems) > MAX_REPORTED:
        print(f"... and {len(problems) - MAX_REPORTED} more", file=sys.stderr)
    if problems:
        return 1
    if args.check:
        print(f"{args.manifest}: all {len(items)} golden items reproduce", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
