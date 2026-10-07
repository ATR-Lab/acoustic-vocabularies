"""Write the package-hash mapping of built packages for the run sheets (#32).

Usage (from the repository root):

    uv run --project sound python sound/tools/package_hashes.py --set pilot \\
        [--key PACKAGE_ID=BK-P-XXXXXX ...] --out OUT/pilot-package-hashes.json PACKAGE_DIR ...

Every package is verified (`load_package`) before its hash is used. The output matches
`schedules/schema/package-hashes.schema.json` (format `av-schedules/package-hashes`
version 1): Study A keys are the book IDs of the learner-facing slot list (by default
the package ID; `--key` maps a package ID to it), Study B keys are the dyad slot IDs
(by default the unit of the sealed permutation). The run-sheet generator reads it with
`--package-hashes`. A mapping of study packages is written only outside the repository.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from av_sound.package import PackageError, package_hashes, write_package_hashes


def parse_keys(items: list[str]) -> dict[str, str]:
    keys: dict[str, str] = {}
    for item in items:
        package_id, sep, key = item.partition("=")
        if not sep or not package_id or not key:
            raise SystemExit(f"--key needs PACKAGE_ID=KEY, got {item!r}")
        keys[package_id] = key
    return keys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("packages", nargs="+", type=Path, help="package directories")
    parser.add_argument("--set", required=True, choices=("pilot", "confirmatory"))
    parser.add_argument("--key", action="append", default=[], help="PACKAGE_ID=KEY")
    parser.add_argument("--out", required=True, type=Path, help="mapping JSON to write")
    args = parser.parse_args(argv)
    try:
        doc = package_hashes(args.packages, set_name=args.set, keys=parse_keys(args.key))
        digest = write_package_hashes(doc, args.out)
    except PackageError as err:
        print(f"{err.code}: {err}", file=sys.stderr)
        return 1
    print(
        f"Study {doc['study']} {doc['set']} (demo={str(doc['demo']).lower()}): "
        f"{len(doc['packages'])} packages -> {args.out.name} sha256 {digest}"
    )
    for key, value in doc["packages"].items():
        print(f"- {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
