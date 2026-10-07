"""Compare the golden digests of several CI runners (#12). Standard library only.

Usage:

    python sound/tools/compare_golden_digests.py DIGEST_DIR \
        --expect linux-x86_64,linux-arm64,macos-arm64,macos-x86_64,windows-x86_64 \
        --manifest tests/golden/manifest.json

Each runner writes `<label>.json` with `make_goldens.py --digest-out`. This script
prints one Markdown table row per runner and exits with status 1 if a runner is
missing, if any digest differs between runners or from the committed manifest, if
a runner reported mismatches, or if a label that ends in an architecture (`-x86_64`,
`-arm64`) ran on another one (for example x86_64 Python emulated on arm64). That
table is the cross-OS evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

CATEGORIES = ("recipe", "atom", "message", "nonlexical", "store")
SHORT = 12
ARCHES = {
    "x86_64": "x86_64",
    "amd64": "x86_64",
    "x64": "x86_64",
    "arm64": "arm64",
    "aarch64": "arm64",
}


def arch(machine: object) -> str:
    """Normalized CPU architecture of `platform.machine()` (`x86_64` or `arm64`)."""
    return ARCHES.get(str(machine).lower(), str(machine))


def load_records(directory: Path) -> list[dict[str, Any]]:
    """Every `*.json` digest record in `directory`, sorted by runner label."""
    records = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.json"))]
    return sorted(records, key=lambda r: str(r.get("runner")))


def compare(
    records: list[dict[str, Any]],
    expect: list[str],
    manifest_digests: dict[str, str] | None = None,
) -> tuple[bool, str]:
    """Return (ok, Markdown report) for the runner records."""
    problems: list[str] = [] if records else ["no digest records"]
    labels = [str(r.get("runner")) for r in records]
    for label in expect:
        if label not in labels:
            problems.append(f"runner `{label}` produced no digest record")
    for label in sorted({x for x in labels if labels.count(x) > 1}):
        problems.append(f"runner `{label}` has more than one digest record")
    reference = manifest_digests or (records[0]["digests"] if records else {})
    keys = ("all", *CATEGORIES)
    lines = [
        "## Golden digests per runner",
        "",
        "| Runner | OS | Arch | Python | numpy | Items | "
        + " | ".join(keys)
        + " | Reproduces manifest |",
        "| --- " * (7 + len(keys)) + "|",
    ]
    for record in records:
        digests = record.get("digests", {})
        cells = []
        for key in keys:
            value = str(digests.get(key, "missing"))
            same = value == reference.get(key)
            cells.append(f"`{value[:SHORT]}`" if same else f"**`{value[:SHORT]}`**")
            if not same:
                problems.append(f"`{record.get('runner')}`: {key} digest differs ({value})")
        label = str(record.get("runner"))
        claimed = label.rsplit("-", 1)[-1]
        if claimed in set(ARCHES.values()) and arch(record.get("machine")) != claimed:
            problems.append(
                f"`{label}` ran on {record.get('machine')} ({arch(record.get('machine'))}), "
                f"not {claimed}"
            )
        if not record.get("matches_manifest"):
            problems.append(
                f"`{record.get('runner')}`: {record.get('mismatches')} mismatches with the manifest"
            )
        n_items = sum(record.get("counts", {}).values())
        lines.append(
            f"| {record.get('runner')} | {record.get('system')} | {record.get('machine')} | "
            f"{record.get('python')} | {record.get('numpy')} | {n_items} | "
            + " | ".join(cells)
            + f" | {'yes' if record.get('matches_manifest') else '**NO**'} |"
        )
    hashes = {str(r.get("renderer_hash")) for r in records}
    if len(hashes) > 1:
        problems.append(f"renderer_hash differs between runners: {sorted(hashes)}")
    lines.append("")
    if manifest_digests:
        lines.append(f"Manifest digest (all): `{manifest_digests.get('all')}`.")
    lines.append(f"Renderer hash: `{', '.join(sorted(hashes))}`.")
    lines.append("")
    if problems:
        lines += ["**Determinism check failed:**", ""] + [f"- {p}" for p in problems]
    else:
        lines.append(
            f"All {len(records)} runners produced identical golden digests, equal to the manifest."
        )
    return not problems, "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("directory", type=Path, help="directory with <runner>.json records")
    parser.add_argument("--expect", default="", help="comma-separated runner labels")
    parser.add_argument("--manifest", type=Path, help="committed golden manifest")
    args = parser.parse_args(argv)
    expect = [x for x in args.expect.split(",") if x]
    manifest_digests = None
    if args.manifest is not None:
        manifest_digests = json.loads(args.manifest.read_text(encoding="utf-8"))["digests"]
    ok, report = compare(load_records(args.directory), expect, manifest_digests)
    sys.stdout.write(report)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
