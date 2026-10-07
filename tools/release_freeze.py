"""v1.0 release-freeze preparation: change-log coverage check and SHA256SUMS writer.

Prepares evidence for #85, #86 and #87; it does not freeze, tag, sign off or
approve anything. Verify a written bundle with the separate
`tools/release_freeze_verify.py`, which recomputes every hash on its own.

  changelog  Check that every path changed between two Git refs is covered by
             exactly one row of the change-log CSV, and no row lists anything else.
             Rows need a pilot finding ID, except trigger `engineering` rows,
             which must leave it empty and still need an issue link.
  write      Write FREEZE.json and SHA256SUMS for a bundle directory laid out as
             <bundle>/<item_id>/..., refusing forbidden or concealed content.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import release_policy as policy  # noqa: E402
from tools.release_policy import PolicyFault  # noqa: E402

# ---------------------------------------------------------------- change log

CHANGELOG_COLUMNS = ["change_id", "component", "paths", "description", "pilot_finding_id", "trigger",
                     "issue", "reason", "checks_rerun", "commit", "reviewer"]
REVIEWER_PENDING = "PENDING"
CELL_PATTERNS = {
    "change_id": re.compile(r"CHG-[0-9]{3,4}\Z"),
    "component": re.compile(r"[a-z][a-z0-9-]{0,39}\Z"),
    "pilot_finding_id": re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}\Z"),
    "trigger": re.compile(r"[a-z0-9][a-z0-9-]{0,39}\Z"),
    "issue": re.compile(r"(?:#[1-9][0-9]{0,6}|https://github\.com/ATR-Lab/acoustic-vocabularies/"
                        r"(?:issues|pull)/[1-9][0-9]{0,6})\Z"),
    "reviewer": re.compile(r"(?:PENDING|github:[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))\Z"),
}
CHECK_TOKEN = re.compile(r"[a-z0-9][a-z0-9._-]{0,39}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
PLACEHOLDER_FINDINGS = {"none", "n/a", "na", "tbd", "todo", "pending", "unknown", "-"}
# Maintainer decision on #204: an engineering-only change with no pilot finding is
# logged with this trigger, an empty pilot_finding_id and a mandatory issue link.
# Every other trigger (including `none`) still needs a real pilot finding ID.
ENGINEERING_TRIGGER = "engineering"
MAX_CHANGELOG = 4 * 1024 * 1024


class ChangeLogFault(ValueError):
    """Structural change-log or Git error: bounded code plus a public-safe detail."""


def cell_text(row_no, column, value, limit=500):
    if not value or len(value) > limit or value != value.strip():
        raise ChangeLogFault(f"CELL_INVALID: row {row_no} {column}")
    # No control characters and no spreadsheet formula prefixes.
    if any(ord(c) < 32 or ord(c) == 127 for c in value) or value[0] in "=+-@":
        raise ChangeLogFault(f"CELL_UNSAFE: row {row_no} {column}")
    return value


def split_list(row_no, column, value):
    parts = value.split(";")
    if any(not p or p != p.strip() for p in parts):
        raise ChangeLogFault(f"CELL_INVALID: row {row_no} {column}")
    if len(set(parts)) != len(parts):
        raise ChangeLogFault(f"DUPLICATE_IN_CELL: row {row_no} {column}")
    return parts


def repo_path(row_no, value):
    if value.startswith("/") or "\\" in value or any(p in {"", ".", ".."} for p in value.split("/")):
        raise ChangeLogFault(f"PATH_INVALID: row {row_no}")
    return value


def parse_changelog(data):
    """Parse change-log bytes (UTF-8, optional BOM, LF or CRLF) into validated rows."""
    if len(data) > MAX_CHANGELOG:
        raise ChangeLogFault("CHANGELOG_TOO_LARGE")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError:
        raise ChangeLogFault("CHANGELOG_NOT_UTF8") from None
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        table = list(reader)
    except csv.Error:
        raise ChangeLogFault("CHANGELOG_CSV_INVALID") from None
    if not table or table[0] != CHANGELOG_COLUMNS:
        raise ChangeLogFault("CHANGELOG_COLUMNS_MISMATCH")
    rows, ids = [], set()
    for row_no, cells in enumerate(table[1:], start=2):
        if len(cells) != len(CHANGELOG_COLUMNS):
            raise ChangeLogFault(f"ROW_WIDTH_INVALID: row {row_no}")
        row = dict(zip(CHANGELOG_COLUMNS, cells))
        engineering = row["trigger"] == ENGINEERING_TRIGGER
        if row["issue"] == "":
            raise ChangeLogFault(f"ISSUE_REQUIRED: row {row_no}")
        if not engineering:
            if row["pilot_finding_id"].strip().lower() in PLACEHOLDER_FINDINGS | {""}:
                raise ChangeLogFault(f"PILOT_FINDING_REQUIRED: row {row_no}")
        elif row["pilot_finding_id"] != "":
            # Engineering rows carry no finding; a change answering a pilot finding
            # is logged under its pilot trigger (or `none`) so it is reviewed as one.
            raise ChangeLogFault(f"ENGINEERING_ROW_HAS_FINDING: row {row_no}")
        for column in CHANGELOG_COLUMNS:
            if column == "pilot_finding_id" and engineering:
                continue
            cell_text(row_no, column, row[column], limit=20000 if column == "paths" else 500)
            pattern = CELL_PATTERNS.get(column)
            if pattern and not pattern.fullmatch(row[column]):
                raise ChangeLogFault(f"CELL_INVALID: row {row_no} {column}")
        if row["change_id"] in ids:
            raise ChangeLogFault(f"DUPLICATE_CHANGE_ID: row {row_no}")
        ids.add(row["change_id"])
        row["paths"] = [repo_path(row_no, p) for p in split_list(row_no, "paths", row["paths"])]
        row["checks_rerun"] = split_list(row_no, "checks_rerun", row["checks_rerun"])
        if not all(CHECK_TOKEN.fullmatch(t) for t in row["checks_rerun"]):
            raise ChangeLogFault(f"CELL_INVALID: row {row_no} checks_rerun")
        row["commit"] = split_list(row_no, "commit", row["commit"])
        if not all(COMMIT.fullmatch(c) for c in row["commit"]):
            raise ChangeLogFault(f"CELL_INVALID: row {row_no} commit")
        row["row"] = row_no
        rows.append(row)
    if not rows:
        raise ChangeLogFault("CHANGELOG_EMPTY")
    return rows


def git(repo, *args, ok=(0,)):
    try:
        result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        raise ChangeLogFault("GIT_UNAVAILABLE") from None
    if result.returncode not in ok:
        raise ChangeLogFault("GIT_COMMAND_FAILED")
    return result


def resolve(repo, ref):
    if not ref or ref.startswith("-") or any(ord(c) <= 32 or ord(c) == 127 for c in ref):
        raise ChangeLogFault("REF_INVALID")
    try:
        out = git(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", ref + "^{commit}")
    except ChangeLogFault:
        raise ChangeLogFault("REF_UNRESOLVED") from None
    return out.stdout.decode("ascii").strip()


def changed_paths(repo, base, candidate):
    # Tree-to-tree comparison: independent of the working tree, autocrlf and smudge filters.
    # Renames are reported as a deletion plus an addition so both paths need a row.
    out = git(repo, "-c", "core.quotepath=off", "diff", "--no-renames", "--no-ext-diff",
              "--no-textconv", "--name-only", "-z", base, candidate, "--").stdout
    return sorted(p.decode("utf-8", "surrogateescape") for p in out.split(b"\0") if p)


def is_ancestor(repo, commit, ref):
    return git(repo, "merge-base", "--is-ancestor", commit, ref, ok=(0, 1)).returncode == 0


def check_changelog(repo, base_ref, candidate_ref, changelog_path, *, data=None, require_reviewed=False):
    """Return a public summary; summary["problems"] is empty only when coverage is exact."""
    repo = Path(repo)
    base, candidate = resolve(repo, base_ref), resolve(repo, candidate_ref)
    if base == candidate or not is_ancestor(repo, base, candidate):
        raise ChangeLogFault("BASE_NOT_ANCESTOR_OF_CANDIDATE")
    repo_path(0, changelog_path)
    if data is None:
        # Read the committed change log from the candidate, never the working tree.
        data = git(repo, "cat-file", "blob", f"{candidate}:{changelog_path}").stdout
    rows = parse_changelog(data)
    changed = changed_paths(repo, base, candidate)
    exempt = [changelog_path] if changelog_path in changed else []
    required = set(changed) - set(exempt)
    problems, owners = [], {}
    for row in rows:
        for path in row["paths"]:
            owners.setdefault(path, []).append(row["change_id"])
            if path == changelog_path:
                problems.append({"code": "CHANGELOG_LISTS_ITSELF", "change_id": row["change_id"]})
            elif path not in required:
                problems.append({"code": "EXTRA_ROW_PATH", "change_id": row["change_id"], "path": path})
        for commit in row["commit"]:
            if not (git(repo, "cat-file", "-e", commit + "^{commit}", ok=(0, 1, 128)).returncode == 0
                    and is_ancestor(repo, commit, candidate) and not is_ancestor(repo, commit, base)):
                problems.append({"code": "COMMIT_OUTSIDE_RANGE", "change_id": row["change_id"],
                                 "commit": commit})
        if require_reviewed and row["reviewer"] == REVIEWER_PENDING:
            problems.append({"code": "REVIEWER_PENDING", "change_id": row["change_id"]})
    for path, ids in sorted(owners.items()):
        if len(ids) > 1:
            problems.append({"code": "PATH_IN_MULTIPLE_ROWS", "path": path, "change_ids": ids})
    for path in sorted(required - set(owners)):
        if ";" in path:
            problems.append({"code": "UNREPRESENTABLE_PATH"})
        else:
            problems.append({"code": "UNCOVERED_PATH", "path": path})
    return {"result": "FAIL" if problems else "PASS", "base": base, "candidate": candidate,
            "changed_paths": len(changed), "exempt_paths": exempt, "rows": len(rows),
            "covered_paths": len(required & set(owners)),
            "engineering_rows": sum(r["trigger"] == ENGINEERING_TRIGGER for r in rows),
            "reviewer_pending": sum(r["reviewer"] == REVIEWER_PENDING for r in rows),
            "problems": problems}

# ---------------------------------------------------------------- freeze writer

MAX_PLAN = 1024 * 1024
MAX_FILE = 8 * 1024**3
MAX_TOTAL = 64 * 1024**3
PLAN_KEYS = {"schema_version", "study", "label", "source_commit", "items"}


def _unique_pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise PolicyFault("DUPLICATE_JSON_KEY")
        obj[key] = value
    return obj


def _nonfinite(_):
    raise PolicyFault("NONFINITE_JSON")


def strict_json(data, code):
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_unique_pairs, parse_constant=_nonfinite)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise PolicyFault(code) from None


def load_plan(path):
    data = Path(path).read_bytes()
    if len(data) > MAX_PLAN:
        raise PolicyFault("PLAN_TOO_LARGE")
    plan = strict_json(data, "PLAN_INVALID_JSON")
    if not isinstance(plan, dict) or set(plan) != PLAN_KEYS or plan["schema_version"] != 1 \
            or not isinstance(plan["items"], dict):
        raise PolicyFault("PLAN_INVALID")
    study = plan["study"]
    declared = policy.items(study if isinstance(study, str) else "")
    if not isinstance(plan["label"], str) or not policy.LABEL.fullmatch(plan["label"]):
        raise PolicyFault("LABEL_INVALID")
    if not isinstance(plan["source_commit"], str) or not COMMIT.fullmatch(plan["source_commit"]):
        raise PolicyFault("SOURCE_COMMIT_INVALID")
    for item in declared:
        if item not in plan["items"]:
            raise PolicyFault("MISSING_ITEM", item)
    for item, entry in plan["items"].items():
        policy.check_item(study, item, entry, recorded=False)
    return plan


def is_link(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def bundle_files(bundle):
    """Every regular file below the bundle as a POSIX relative path; links refused."""
    found = []
    def walk(directory, prefix):
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda e: e.name):
                rel = prefix + entry.name
                info = entry.stat(follow_symlinks=False)
                if is_link(info):
                    raise PolicyFault("LINK_FORBIDDEN", rel if policy.SEGMENT.fullmatch(entry.name) else None)
                if stat.S_ISDIR(info.st_mode):
                    walk(entry.path, rel + "/")
                elif stat.S_ISREG(info.st_mode):
                    found.append(rel)
                else:
                    raise PolicyFault("NONREGULAR_FILE", None)
    walk(bundle, "")
    return found


def hash_and_scan(path, rel):
    before = os.lstat(path)
    if is_link(before) or not stat.S_ISREG(before.st_mode):
        raise PolicyFault("LINK_FORBIDDEN", rel)
    if before.st_size > MAX_FILE:
        raise PolicyFault("FILE_TOO_LARGE", rel)
    hasher, scanner, total = hashlib.sha256(), policy.ContentScanner(rel), 0
    with open(path, "rb") as stream:
        while chunk := stream.read(1024 * 1024):
            total += len(chunk)
            hasher.update(chunk)
            scanner.feed(chunk)
    scanner.finish()
    after = os.lstat(path)
    if total != before.st_size or (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
        raise PolicyFault("FILE_CHANGED", rel)
    return hasher.hexdigest(), total


def freeze_document(plan, item_files):
    items = {}
    for item, entry in plan["items"].items():
        record = dict(entry)
        if entry["status"] == "files":
            record["files"] = item_files[item]
        items[item] = record
    return {"schema_version": 1, "tool": "tools/release_freeze.py",
            "coverage_version": policy.COVERAGE_VERSION, "study": plan["study"], "label": plan["label"],
            "source_commit": plan["source_commit"], "items": items,
            "signoff": {"status": "pending",
                        "note": "Tags, second-person recomputation and sign-off are human steps."}}


def write_freeze(bundle, plan_path):
    plan = load_plan(plan_path)
    bundle = Path(bundle)
    info = os.lstat(bundle)
    if is_link(info) or not stat.S_ISDIR(info.st_mode):
        raise PolicyFault("BUNDLE_DIRECTORY_REQUIRED")
    for name in (policy.SUMS_NAME, policy.FREEZE_NAME):
        if os.path.lexists(bundle / name):
            # A frozen bundle is never rewritten; later changes start a new version.
            raise PolicyFault("FREEZE_OUTPUT_EXISTS", name)
    statuses = {item: entry["status"] for item, entry in plan["items"].items()}
    item_files = {item: [] for item, status in statuses.items() if status == "files"}
    folded = set()
    for rel in bundle_files(bundle):
        if "/" not in rel:
            raise PolicyFault("UNEXPECTED_ROOT_FILE", rel if policy.SEGMENT.fullmatch(rel) else None)
        item = rel.split("/", 1)[0]
        if item not in statuses:
            raise PolicyFault("UNDECLARED_ITEM", item if policy.ITEM_ID.fullmatch(item) else None)
        if statuses[item] != "files":
            # Concealed contents (e.g. allocation lists) must never sit in a bundle.
            raise PolicyFault("CONCEALED_OR_NA_CONTENT_PRESENT", item)
        policy.check_relpath(rel)
        policy.check_name(rel)
        if rel.lower() in folded:
            raise PolicyFault("CASE_COLLISION", rel)
        folded.add(rel.lower())
        item_files[item].append(rel)
    for item, files in item_files.items():
        if not files:
            raise PolicyFault("EMPTY_ITEM", item)
    sums, total = {}, 0
    for files in item_files.values():
        for rel in files:
            sums[rel], size = hash_and_scan(bundle / rel, rel)
            total += size
            if total > MAX_TOTAL:
                raise PolicyFault("TOTAL_TOO_LARGE")
    for files in item_files.values():
        files.sort()
    document = freeze_document(plan, item_files)
    freeze_bytes = (json.dumps(document, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode("ascii")
    sums[policy.FREEZE_NAME] = hashlib.sha256(freeze_bytes).hexdigest()
    # sha256sum-compatible text: "<hex>  <path>\n", sorted, LF only on every platform.
    sums_bytes = "".join(f"{sums[rel]}  {rel}\n" for rel in sorted(sums)).encode("ascii")
    with open(bundle / policy.FREEZE_NAME, "xb") as stream:
        stream.write(freeze_bytes)
    with open(bundle / policy.SUMS_NAME, "xb") as stream:
        stream.write(sums_bytes)
    counts = {s: sum(v == s for v in statuses.values()) for s in ("files", "concealed", "not_applicable")}
    return {"result": "WRITTEN", "study": plan["study"], "label": plan["label"],
            "files_hashed": len(sums) - 1, "items": counts,
            "sha256sums_sha256": hashlib.sha256(sums_bytes).hexdigest(),
            "freeze_accepted": False, "next": "verify independently, then human tag and sign-off"}

# ---------------------------------------------------------------- CLI


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    log = commands.add_parser("changelog", help="check change-log coverage between two refs")
    log.add_argument("--repo", default=str(ROOT))
    log.add_argument("--base", required=True, help="e.g. the v0.9 tag")
    log.add_argument("--candidate", required=True, help="the v1.0 candidate ref")
    log.add_argument("--changelog", required=True, help="repository path, read from the candidate commit")
    log.add_argument("--draft-file", help="check this local file instead of the committed change log")
    log.add_argument("--require-reviewed", action="store_true", help="refuse rows whose reviewer is PENDING")
    make = commands.add_parser("write", help="write FREEZE.json and SHA256SUMS for a bundle")
    make.add_argument("--bundle", required=True)
    make.add_argument("--plan", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "changelog":
            data = Path(args.draft_file).read_bytes() if args.draft_file else None
            summary = check_changelog(args.repo, args.base, args.candidate, args.changelog, data=data,
                                      require_reviewed=args.require_reviewed)
        else:
            summary = write_freeze(args.bundle, args.plan)
    except (ChangeLogFault, PolicyFault) as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, ValueError):
        print("INPUT_IO_OR_VALUE_INVALID", file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if summary.get("problems") else 0


if __name__ == "__main__":
    raise SystemExit(main())
