"""Private operator handoff CLI; never opens study audio, package or schedule files."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from .admission import DurableRevealLog, _exact, _hash
from .admission_io import canonical, checked_path, parse, read, sha, sync_directory
from .reveal import RevealError


def write_receipt(path: Path, value: dict[str, Any]) -> None:
    checked_path(path, missing=True)
    if not any(p.lower() in {"private", ".local", "local-data"} for p in path.parent.parts):
        raise RevealError("PRIVATE_RECEIPT_REQUIRED")
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(canonical(value))
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(path.parent)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("allocation-list", "list-file-sha256", "journal", "expected-head",
                 "request", "request-sha256", "output"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--recover-tail", action="store_true")
    args = parser.parse_args(argv)
    try:
        output = checked_path(Path(args.output), missing=True)
        if output.exists():
            raise RevealError("OUTPUT_EXISTS")
        if not any(p.lower() in {"private", ".local", "local-data"} for p in output.parent.parts):
            raise RevealError("PRIVATE_RECEIPT_REQUIRED")
        raw = read(Path(args.request), 65536)
        if sha(raw) != _hash(args.request_sha256):
            raise RevealError("REQUEST_HASH")
        request = parse(raw)
        if not isinstance(request, dict) or type(request.get("schema_version")) is not int or request["schema_version"] != 1:
            raise RevealError("REQUEST_VERSION")
        log = DurableRevealLog(Path(args.allocation_list), args.list_file_sha256, Path(args.journal),
                               expected_head=args.expected_head, recover_tail=args.recover_tail)
        op = request.get("operation")
        if op == "eligibility":
            _exact(request, {"schema_version", "operation", "screening_ids", "staff", "checks", "orientation_files"})
            files = []
            if not isinstance(request["orientation_files"], list) or not 1 <= len(request["orientation_files"]) <= 2:
                raise RevealError("ORIENTATION_FILES_REQUIRED")
            for item in request["orientation_files"]:
                _exact(item, {"receipt_path", "receipt_file_sha256", "journal_path"})
                files.append((Path(item["receipt_path"]), item["receipt_file_sha256"], Path(item["journal_path"])))
            receipt = log.log_eligibility(request["screening_ids"], staff=request["staff"],
                                          checks=request["checks"], orientation_files=files)
        elif op == "reveal":
            _exact(request, {"schema_version", "operation", "eligibility_id", "eligibility_receipt_sha256", "staff"})
            receipt = log.reveal_next(request["eligibility_id"], staff=request["staff"],
                                      eligibility_receipt_sha256=request["eligibility_receipt_sha256"])
        elif op == "eligibility_receipt":
            _exact(request, {"schema_version", "operation", "eligibility_id"})
            receipt = log.eligibility_receipt(request["eligibility_id"])
        else:
            raise RevealError("OPERATION_INVALID")
        write_receipt(output, receipt)
        print(canonical({"receipt_file_sha256": sha(canonical(receipt)),
                         "receipt_sha256": receipt["receipt_sha256"],
                         "current_journal_head_sha256": log.head}).decode("ascii"))
        return 0
    except (RevealError, OSError, ValueError, TypeError, KeyError):
        # No raw path, participant code, allocation identity or exception text on stdout.
        print("ALLOCATION_HANDOFF_REFUSED")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
