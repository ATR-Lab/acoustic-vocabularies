"""Build the fallback banks and books (#15) from a seed and print the manifest summary.

Usage (from the repository root):

    # Public example: a DEMO- seed. Writes or checks the committed example manifest.
    uv run --project sound python sound/tools/build_fallback.py --demo-seed DEMO-fallback-v1 \\
        --manifest sound/testvectors/fallback/demo-manifest.json
    uv run --project sound python sound/tools/build_fallback.py --demo-seed DEMO-fallback-v1 \\
        --check sound/testvectors/fallback/demo-manifest.json

    # Restricted build (pilot or confirmatory). Seed file and output stay in restricted
    # storage, outside any git work tree; publish only fallback_bank_hash.
    uv run --project sound python sound/tools/build_fallback.py \\
        --seed-file <restricted>/fallback-seed.txt --out <restricted>/fallback-<date>

`--out DIR` writes the manifest, the build log (every draw and its codes), each recipe
as JSON and WAV, and a vocabulary store `DIR/store` with the three fallback books
committed (`kind="fallback"`) and frozen; then it re-renders and verifies everything.
`DIR` must be empty or missing and outside every git work tree. `--manifest PATH`
writes the manifest JSON only (inside the repository only for a DEMO seed).
`--check PATH` rebuilds and exits 1 if the manifest at PATH differs.

The summary prints counts and whole-bank digests only. A seed file holds one line: the
seed (at least 32 characters of `[A-Za-z0-9._-]`, e.g. `secrets.token_hex(32)`).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from av_sound import (
    FallbackError,
    FallbackSet,
    VocabularyStore,
    build_fallback,
    freeze_fallback_books,
    verify_fallback,
)
from av_sound.fallback import (
    E_POLICY,
    E_SEED,
    check_seed,
    inside_work_tree,
    is_demo_seed,
    write_fallback,
)


def read_seed_file(path: Path) -> str:
    """The seed in a restricted seed file (one line)."""
    if inside_work_tree(path):
        raise FallbackError(
            E_POLICY, "the seed file must stay in restricted storage, outside any git work tree"
        )
    seed = path.read_text(encoding="utf-8").strip()
    if is_demo_seed(seed):
        raise FallbackError(E_SEED, "a seed file holds a restricted seed; use --demo-seed")
    return check_seed(seed)


def summary_markdown(fset: FallbackSet, seconds: float) -> str:
    s = fset.summary()
    who = (
        f"DEMO seed `{fset.demo_seed}` (public example, not study material)"
        if fset.demo_seed is not None
        else "restricted seed (study material: keep the outputs in restricted storage)"
    )
    lines = [
        f"### Fallback banks and books: {who}",
        "",
        f"- seed fingerprint `{s['seed_fingerprint']}`",
        f"- threshold {s['threshold']}; renderer {s['renderer_version']}, validator "
        f"{s['validator_version']}, builder {s['builder_version']}",
        f"- reserved signals `{s['reserved_sha256']}`",
        f"- build time {seconds:.2f} s",
        "",
        "| Profile | Bank | Draws | `bank_sha256` | Book | Draws | `book_sha256` | Rejections |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for p in s["profiles"]:
        codes = ", ".join(f"{c} {n}" for c, n in p["rejection_codes"].items()) or "none"
        lines.append(
            f"| {p['profile']} | {p['bank_entries']} | {p['bank_draws']} | "
            f"`{p['bank_sha256']}` | {p['book_atoms']} | {p['book_draws']} | "
            f"`{p['book_sha256']}` | {codes} |"
        )
    lines += ["", f"`fallback_bank_hash` = `{s['fallback_bank_hash']}`"]
    return "\n".join(lines)


def write_manifest(fset: FallbackSet, path: Path) -> None:
    if fset.demo_seed is None:
        if inside_work_tree(path):
            raise FallbackError(
                E_POLICY, "a restricted manifest must not be written inside a git work tree"
            )
        mode = "x"  # a restricted manifest is never overwritten
    else:
        mode = "w"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(fset.manifest(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    with open(path, mode, encoding="utf-8", newline="\n") as f:
        f.write(text)


def write_out(fset: FallbackSet, out: Path) -> dict[str, Any]:
    if inside_work_tree(out):
        raise FallbackError(
            E_POLICY,
            "--out writes WAVs and a fallback store; use a directory outside any git work "
            "tree (for a DEMO manifest in the repository use --manifest)",
        )
    manifest_path = write_fallback(fset, out)
    store = VocabularyStore(out / "store")
    frozen = freeze_fallback_books(store, fset)
    store_ok = all(store.verify(f.book_id, expected_head=f.chain_head).ok for f in frozen)
    problems = verify_fallback(manifest_path)
    return {
        "manifest": str(manifest_path),
        "store_books": [f.to_dict() for f in frozen],
        "store_verified": store_ok,
        "problems": list(problems),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    seed = parser.add_mutually_exclusive_group(required=True)
    seed.add_argument("--seed-file", type=Path, help="restricted seed file (one line)")
    seed.add_argument("--demo-seed", help="public example seed starting with DEMO-")
    parser.add_argument("--threshold", help="separation threshold (default: config, '0.10')")
    parser.add_argument("--out", type=Path, help="full output directory (restricted storage)")
    parser.add_argument("--manifest", type=Path, help="write only the manifest JSON here")
    parser.add_argument("--check", type=Path, help="exit 1 if this manifest differs")
    parser.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = parser.parse_args(argv)

    try:
        if args.demo_seed is not None:
            if not is_demo_seed(args.demo_seed):
                raise FallbackError(E_SEED, "--demo-seed must start with DEMO-")
            seed_value = check_seed(args.demo_seed)
        else:
            seed_value = read_seed_file(args.seed_file)
        started = time.perf_counter()
        fset = build_fallback(seed_value, threshold=args.threshold)
        seconds = time.perf_counter() - started
        report: dict[str, Any] = {"summary": fset.summary(), "build_seconds": round(seconds, 3)}
        if args.manifest is not None:
            write_manifest(fset, args.manifest)
            report["manifest"] = str(args.manifest)
        if args.out is not None:
            report["out"] = write_out(fset, args.out)
        status = 0
        if args.check is not None:
            committed = json.loads(args.check.read_text(encoding="utf-8"))
            same = committed == fset.manifest()
            report["check"] = {"path": str(args.check), "identical": same}
            status = 0 if same else 1
        if args.out is not None and (
            report["out"]["problems"] or not report["out"]["store_verified"]
        ):
            status = 1
    except (FallbackError, FileExistsError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(summary_markdown(fset, seconds))
        if "out" in report:
            out = report["out"]
            print(f"\nWrote {out['manifest']} and the recipes, WAVs and build log beside it.")
            for book in out["store_books"]:
                print(
                    f"- store book `{book['book_id']}` frozen, chain head "
                    f"`{book['chain_head']}`, snapshot `{book['snapshot_sha256']}`"
                )
            print(f"- store verify: {'ok' if out['store_verified'] else 'FAILED'}")
            print(f"- re-render and admissibility: {out['problems'] or 'ok'}")
        if "check" in report:
            same = report["check"]["identical"]
            print(f"\n{args.check}: {'identical' if same else 'DIFFERS'}")
    return status


if __name__ == "__main__":
    sys.exit(main())
