"""Fail when an existing golden hash changes without a version bump (#12). Stdlib only.

Usage (in CI on pull requests, with full history):

    python sound/tools/check_golden_bump.py origin/<base-branch>

The script compares tests/golden/manifest.json at the merge base of BASE_REF and
HEAD with the manifest in the working tree:

- New items are allowed.
- A changed or removed item needs, in the same pull request, a change of one of the
  header fields that govern its category. The rules come from the `version_rules` of
  the BASE manifest (so a pull request cannot relax them): `renderer_version` for
  every category, plus `asset_spec_version` for nonlexical assets and
  `validator_version`, `store_record_version`, `renderer_hash` or `validator_hash`
  for the store round trip (a book records the code hashes, so a byte-neutral code
  change moves the store chain heads).
- A version field (`*_version`) must increase when it changes; a hash field
  (`renderer_hash`, `validator_hash`) only needs to change.
- Deleting the manifest fails.

It prints a Markdown report (for the job summary) that lists every changed item; a
reviewer note in the pull request must explain the change. Exit status: 0 pass,
1 fail, 2 usage or git error.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MANIFEST = "tests/golden/manifest.json"
DEFAULT_RULE = ("renderer_version",)
VERSION_FIELDS = (
    "renderer_version",
    "asset_spec_version",
    "validator_version",
    "store_record_version",
)
HASH_FIELDS = ("renderer_hash", "validator_hash")


@dataclass
class Report:
    ok: bool = True
    violations: list[str] = field(default_factory=list)
    changed: list[tuple[str, str]] = field(default_factory=list)  # (item id, what changed)
    removed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    bumps: dict[str, tuple[Any, Any]] = field(default_factory=dict)  # version increases
    hash_changes: list[str] = field(default_factory=list)  # changed code-hash fields
    notes: list[str] = field(default_factory=list)

    def fail(self, message: str) -> None:
        self.ok = False
        self.violations.append(message)


def version_key(value: Any) -> tuple[int, ...] | None:
    """`"1.2.3"` -> (1, 2, 3); `4` -> (4,); anything else -> None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return (value,)
    if isinstance(value, str):
        try:
            return tuple(int(part) for part in value.split("."))
        except ValueError:
            return None
    return None


def _items(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in manifest.get("items", [])}


def _what_changed(old: Mapping[str, Any], new: Mapping[str, Any]) -> str:
    parts = []
    if old.get("category") != new.get("category") or old.get("inputs") != new.get("inputs"):
        parts.append("inputs")
    old_out, new_out = old.get("outputs", {}), new.get("outputs", {})
    keys = sorted(k for k in old_out.keys() | new_out.keys() if old_out.get(k) != new_out.get(k))
    parts += [f"outputs.{k}" for k in keys]
    return ", ".join(parts) or "item"


def check(base: Mapping[str, Any] | None, head: Mapping[str, Any] | None) -> Report:
    """Apply the bump rules to a base and a head manifest (either may be None)."""
    report = Report()
    if base is None:
        report.notes.append("No golden manifest at the merge base: every item is new.")
        if head is not None:
            report.added = sorted(_items(head))
        return report
    if head is None:
        report.fail(f"{MANIFEST} was deleted")
        return report

    for name in VERSION_FIELDS:
        old, new = base.get(name), head.get(name)
        if old == new:
            continue
        old_key, new_key = version_key(old), version_key(new)
        if old_key is None or new_key is None or new_key <= old_key:
            report.fail(f"`{name}` must increase when it changes: {old!r} -> {new!r}")
        else:
            report.bumps[name] = (old, new)
    report.hash_changes = [n for n in HASH_FIELDS if base.get(n) != head.get(n)]
    if report.hash_changes:
        names = " and ".join(f"`{n}`" for n in report.hash_changes)
        report.notes.append(
            f"{names} changed: the renderer or validator code changed. If no waveform hash "
            "changed, the bytes are the same; say why in a reviewer note."
        )

    rules: Mapping[str, Any] = base.get("version_rules") or {}
    base_items, head_items = _items(base), _items(head)
    report.added = sorted(head_items.keys() - base_items.keys())
    for item_id in sorted(base_items):
        old_item = base_items[item_id]
        if item_id not in head_items:
            report.removed.append(item_id)
            what = "removed"
        elif head_items[item_id] != old_item:
            what = _what_changed(old_item, head_items[item_id])
            report.changed.append((item_id, what))
        else:
            continue
        governing = tuple(rules.get(old_item.get("category"), DEFAULT_RULE))
        justified = set(report.bumps) | set(report.hash_changes)
        if not justified.intersection(governing):
            report.fail(
                f"`{item_id}` {what}: needs a change of {' or '.join(governing)} "
                "in the same pull request"
            )
    if report.changed or report.removed:
        report.notes.append(
            "Golden items changed: a reviewer note in the pull request must explain why "
            "(renderer spec D10)."
        )
    return report


def markdown(report: Report, base_label: str) -> str:
    lines = ["## Golden version guard", "", f"Base: `{base_label}`.", ""]
    if report.bumps:
        bumps = ", ".join(f"`{k}` {old} -> {new}" for k, (old, new) in sorted(report.bumps.items()))
        lines.append(f"Version bumps: {bumps}.")
    else:
        lines.append("Version bumps: none.")
    if report.hash_changes:
        lines.append(f"Changed code hashes: {', '.join(f'`{n}`' for n in report.hash_changes)}.")
    lines.append(
        f"Items: {len(report.added)} added, {len(report.changed)} changed, "
        f"{len(report.removed)} removed."
    )
    if report.changed or report.removed:
        lines += ["", "| Item | Change |", "| --- | --- |"]
        lines += [f"| `{item}` | {what} |" for item, what in report.changed]
        lines += [f"| `{item}` | removed |" for item in report.removed]
    for note in report.notes:
        lines += ["", f"Note: {note}"]
    lines.append("")
    if report.ok:
        lines.append("Result: pass.")
    else:
        lines += ["Result: **fail**.", ""] + [f"- {v}" for v in report.violations]
    return "\n".join(lines) + "\n"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8"
    )


def load_base(repo: Path, base_ref: str, path: str) -> tuple[str, dict[str, Any] | None]:
    """(merge-base SHA, manifest at the merge base or None if it has none)."""
    merge_base = _git(repo, "merge-base", base_ref, "HEAD")
    if merge_base.returncode != 0:
        raise RuntimeError(f"git merge-base {base_ref} HEAD failed: {merge_base.stderr.strip()}")
    sha = merge_base.stdout.strip()
    exists = _git(repo, "cat-file", "-e", f"{sha}:{path}")
    if exists.returncode != 0:
        return sha, None
    shown = _git(repo, "show", f"{sha}:{path}")
    if shown.returncode != 0:
        raise RuntimeError(f"git show {sha}:{path} failed: {shown.stderr.strip()}")
    return sha, json.loads(shown.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("base_ref", help="base branch, e.g. origin/main")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="repository root")
    parser.add_argument("--manifest", default=MANIFEST, help="manifest path in the repository")
    args = parser.parse_args(argv)
    try:
        sha, base = load_base(args.repo, args.base_ref, args.manifest)
    except (RuntimeError, OSError, json.JSONDecodeError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    head_path = args.repo / args.manifest
    head = json.loads(head_path.read_text(encoding="utf-8")) if head_path.is_file() else None
    report = check(base, head)
    sys.stdout.write(markdown(report, f"{args.base_ref} (merge base {sha[:12]})"))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
