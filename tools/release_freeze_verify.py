"""Independent verifier for a v1.0 freeze bundle (FREEZE.json + SHA256SUMS).

Deliberately does not import tools/release_freeze.py: it parses SHA256SUMS,
walks the bundle and recomputes every SHA-256 with its own code. It shares only
the declared coverage and public-content guard in tools/release_policy.py.

A PASS means the bytes match and coverage is complete. It is not a freeze, a tag
or a sign-off; those remain human steps. Equivalent manual recomputation:
`sha256sum -c SHA256SUMS` (Linux) or `Get-FileHash -Algorithm SHA256` (Windows).
"""
from __future__ import annotations

import argparse
import hashlib
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
from tools import repo_guard  # noqa: E402

SUMS_LINE = re.compile(r"([0-9a-f]{64})  ([^\n]+)\Z")
FREEZE_KEYS = {"schema_version", "tool", "coverage_version", "study", "label", "source_commit", "items",
               "signoff"}
MAX_FREEZE = 1024 * 1024
BUFFER = 4 * 1024 * 1024


class VerifyFault(ValueError):
    """Refusal before verification can proceed (unreadable or malformed inputs)."""


def read_sums(data):
    """Parse SHA256SUMS bytes exactly as the writer emits them; no normalisation."""
    if b"\r" in data:
        # A checkout or editor converted line endings: the archive is no longer byte-exact.
        raise VerifyFault("SUMS_NOT_LF")
    try:
        text = data.decode("ascii")
    except UnicodeError:
        raise VerifyFault("SUMS_NOT_ASCII") from None
    if not text.endswith("\n"):
        raise VerifyFault("SUMS_NOT_CANONICAL")
    entries = []
    for line in text[:-1].split("\n"):
        match = SUMS_LINE.fullmatch(line)
        if not match:
            raise VerifyFault("SUMS_LINE_INVALID")
        entries.append((match[2], match[1]))
    paths = [p for p, _ in entries]
    if paths != sorted(set(paths)):
        raise VerifyFault("SUMS_NOT_CANONICAL")
    if policy.FREEZE_NAME not in paths:
        raise VerifyFault("FREEZE_RECORD_UNLISTED")
    for path in paths:
        if path != policy.FREEZE_NAME:
            try:
                policy.check_relpath(path)
            except policy.PolicyFault:
                raise VerifyFault("SUMS_PATH_UNSAFE") from None
    return dict(entries)


def present_files(bundle):
    """Breadth-first listing of regular files; any link or special file is refused."""
    found, queue = [], [(Path(bundle), "")]
    while queue:
        directory, prefix = queue.pop(0)
        for name in sorted(os.listdir(directory)):
            full = directory / name
            info = os.lstat(full)
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise VerifyFault("LINK_FORBIDDEN")
            if stat.S_ISDIR(info.st_mode):
                queue.append((full, prefix + name + "/"))
            elif stat.S_ISREG(info.st_mode):
                found.append(prefix + name)
            else:
                raise VerifyFault("NONREGULAR_FILE")
    return set(found)


def digest_file(path, scanner=None):
    hasher = hashlib.sha256()
    buffer = bytearray(BUFFER)
    view = memoryview(buffer)
    head = b""
    with open(path, "rb", buffering=0) as stream:
        while count := stream.readinto(buffer):
            hasher.update(view[:count])
            if scanner is not None:
                scanner.feed(bytes(view[:count]))
            if len(head) < 256:
                head += bytes(view[:min(count, 256)])
    if scanner is not None:
        scanner.finish()
    return hasher.hexdigest(), head


def load_freeze(data):
    if len(data) > MAX_FREEZE:
        raise VerifyFault("FREEZE_RECORD_TOO_LARGE")
    def pairs(items):
        if len({k for k, _ in items}) != len(items):
            raise VerifyFault("FREEZE_RECORD_DUPLICATE_KEY")
        return dict(items)
    try:
        record = json.loads(data.decode("ascii"), object_pairs_hook=pairs)
    except (UnicodeError, json.JSONDecodeError):
        raise VerifyFault("FREEZE_RECORD_INVALID") from None
    if not isinstance(record, dict) or set(record) != FREEZE_KEYS or record["schema_version"] != 1:
        raise VerifyFault("FREEZE_RECORD_INVALID")
    if record["coverage_version"] != policy.COVERAGE_VERSION:
        raise VerifyFault("COVERAGE_VERSION_MISMATCH")
    if record["signoff"] != {"status": "pending",
                             "note": "Tags, second-person recomputation and sign-off are human steps."}:
        # The tool never records approval; a changed sign-off block was edited by hand.
        raise VerifyFault("FREEZE_RECORD_INVALID")
    if not isinstance(record["label"], str) or not policy.LABEL.fullmatch(record["label"]) \
            or not isinstance(record["source_commit"], str) \
            or not re.fullmatch(r"[0-9a-f]{40}", record["source_commit"]):
        raise VerifyFault("FREEZE_RECORD_INVALID")
    return record


def check_coverage(record, listed, problems):
    study = record["study"] if isinstance(record["study"], str) else ""
    try:
        declared = policy.items(study)
    except policy.PolicyFault:
        raise VerifyFault("UNKNOWN_STUDY") from None
    items = record["items"]
    if not isinstance(items, dict):
        raise VerifyFault("FREEZE_RECORD_INVALID")
    for item in sorted(set(declared) - set(items)):
        problems.append({"code": "MISSING_ITEM", "item": item})
    recorded_files = set()
    for item in sorted(items):
        try:
            status = policy.check_item(study, item, items[item], recorded=True)
        except policy.PolicyFault as fault:
            problems.append({"code": fault.code, "item": fault.path})
            continue
        if status == "files":
            files = items[item]["files"]
            if not isinstance(files, list) or not files or not all(isinstance(f, str) for f in files) \
                    or files != sorted(set(files)) or not all(f.startswith(item + "/") for f in files):
                problems.append({"code": "ITEM_FILES_INVALID", "item": item})
                continue
            recorded_files.update(files)
    bundle_files = set(listed) - {policy.FREEZE_NAME}
    for path in sorted(bundle_files - recorded_files):
        problems.append({"code": "FILE_NOT_IN_ANY_ITEM", "path": path})
    for path in sorted(recorded_files - bundle_files):
        problems.append({"code": "ITEM_FILE_NOT_IN_SUMS", "path": path})
    return declared


def verify_bundle(bundle, *, sums_sha256=None, concealed=(), repo=None, tag=None, station_records=()):
    bundle = Path(bundle)
    info = os.lstat(bundle)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise VerifyFault("BUNDLE_DIRECTORY_REQUIRED")
    sums_path = bundle / policy.SUMS_NAME
    if not os.path.isfile(sums_path) or os.path.islink(sums_path):
        raise VerifyFault("SUMS_MISSING")
    raw = sums_path.read_bytes()
    problems = []
    if sums_sha256 is not None and hashlib.sha256(raw).hexdigest() != sums_sha256.lower():
        problems.append({"code": "SUMS_PIN_MISMATCH"})
    listed = read_sums(raw)
    present = present_files(bundle) - {policy.SUMS_NAME}
    for path in sorted(present - set(listed)):
        problems.append({"code": "UNLISTED_FILE", "path": path if all(
            policy.SEGMENT.fullmatch(p) for p in path.split("/")) else None})
    for path in sorted(set(listed) - present):
        problems.append({"code": "MISSING_FILE", "path": path})
    verified = 0
    for path in sorted(set(listed) & present):
        scanner = None
        if path != policy.FREEZE_NAME:
            try:
                policy.check_name(path)
            except policy.PolicyFault as fault:
                problems.append({"code": fault.code, "path": path})
                continue
            scanner = policy.ContentScanner(path)
        try:
            actual, head = digest_file(bundle / path, scanner)
        except policy.PolicyFault as fault:
            problems.append({"code": fault.code, "path": path})
            continue
        if actual != listed[path]:
            pointer = repo_guard.POINTER.match(head) is not None
            problems.append({"code": "LFS_POINTER_NOT_MATERIALIZED" if pointer else "HASH_MISMATCH",
                             "path": path})
        else:
            verified += 1
    if policy.FREEZE_NAME not in present:
        raise VerifyFault("FREEZE_RECORD_MISSING")
    record = load_freeze((bundle / policy.FREEZE_NAME).read_bytes())
    check_coverage(record, listed, problems)
    items = record["items"]
    statuses = {k: v.get("status") for k, v in items.items() if isinstance(v, dict)}

    recomputed = []
    for item, private in concealed:
        entry = items.get(item)
        if not isinstance(entry, dict) or entry.get("status") != "concealed":
            problems.append({"code": "NOT_A_CONCEALED_ITEM", "item": item})
            continue
        actual, _ = digest_file(Path(private))
        if actual != entry.get("sha256"):
            problems.append({"code": "CONCEALED_HASH_MISMATCH", "item": item})
        else:
            recomputed.append(item)

    tag_checked = False
    if tag is not None:
        if repo is None or tag.startswith("-"):
            raise VerifyFault("TAG_CHECK_NEEDS_REPO")
        result = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", "--end-of-options",
                                 "refs/tags/" + tag + "^{commit}"], capture_output=True, timeout=30)
        if result.returncode or result.stdout.decode("ascii", "replace").strip() != record["source_commit"]:
            problems.append({"code": "TAG_MISMATCH"})
        tag_checked = True

    stations = {"checked": 0, "matching": 0}
    if station_records:
        from tools import quest_station
        app = items.get("app_build") if isinstance(items.get("app_build"), dict) else {}
        files = app.get("files") if isinstance(app.get("files"), list) else []
        expected = ({listed[f] for f in files if isinstance(f, str) and f in listed} if app.get("status") == "files"
                    else {app.get("sha256")} if app.get("status") == "concealed" else set())
        for index, path in enumerate(station_records, start=1):
            try:
                row = quest_station.read_record(Path(path))
            except (quest_station.ObservationError, OSError):
                problems.append({"code": "STATION_RECORD_INVALID", "record": index})
                continue
            stations["checked"] += 1
            if row["installed_artifact_match"] == "true" and row["installed_apk_sha256"] in expected:
                stations["matching"] += 1
            else:
                # Serials and station identifiers are never echoed; only the record's position.
                problems.append({"code": "STATION_BUILD_NOT_ARCHIVED", "record": index})

    concealed_items = sorted(k for k, s in statuses.items() if s == "concealed")
    return {
        "result": "FAIL" if problems else "PASS",
        "study": record["study"], "label": record["label"], "source_commit": record["source_commit"],
        "files_listed": len(listed), "files_verified": verified,
        "items": {s: sum(v == s for v in statuses.values()) for s in ("files", "concealed", "not_applicable")},
        "not_applicable": sorted(k for k, s in statuses.items() if s == "not_applicable"),
        "concealed_recomputed": sorted(recomputed),
        "concealed_not_recomputed": [k for k in concealed_items if k not in recomputed],
        "sums_pinned": sums_sha256 is not None, "tag_checked": tag_checked, "station_records": stations,
        "freeze_accepted": False, "signoff": "human-required", "problems": problems,
    }


def concealed_arg(value):
    item, sep, path = value.partition("=")
    if not sep or not policy.ITEM_ID.fullmatch(item) or not path:
        raise argparse.ArgumentTypeError("expected ITEM=PATH")
    return item, path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--sums-sha256", help="independently retained SHA-256 of the SHA256SUMS file")
    parser.add_argument("--concealed", type=concealed_arg, action="append", default=[],
                        metavar="ITEM=PATH", help="recompute a concealed item from its private copy")
    parser.add_argument("--repo", help="repository holding the freeze tag")
    parser.add_argument("--tag", help="tag that must point to FREEZE.json source_commit")
    parser.add_argument("--station-record", action="append", default=[],
                        help="private quest_station inventory record (*.local.csv)")
    args = parser.parse_args(argv)
    try:
        summary = verify_bundle(args.bundle, sums_sha256=args.sums_sha256, concealed=args.concealed,
                                repo=args.repo, tag=args.tag, station_records=args.station_record)
    except VerifyFault as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, ValueError, subprocess.SubprocessError):
        print("INPUT_IO_OR_VALUE_INVALID", file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if summary["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
