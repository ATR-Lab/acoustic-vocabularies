"""Export admission failures and mutations of an independently pinned Unity run."""
import copy
import csv
import json
import os
from pathlib import Path
import shutil

import pytest

from analysis.integrity import csv_rows, verified_export, verify_mapping
from tools.prepare_joined_engineering import PreparationError, digest, json_bytes


@pytest.mark.parametrize("raw", [b"a,a\n1,2\n", b",b\n1,2\n", b"", b"a,b\n1,2,3\n", b"a,b\n1\n"])
def test_ambiguous_or_ragged_csv_is_rejected(raw):
    with pytest.raises(PreparationError, match="INTEGRITY_CSV"):
        csv_rows(raw)


def test_quoted_csv_roundtrips():
    assert csv_rows(b'a,b\r\n"one,two","three"\r\n') == [{"a": "one,two", "b": "three"}]


@pytest.fixture
def export(tmp_path):
    files = {"trial-log.csv": b"a,b\n", "exposure-ledger.csv": b"a,b\n", "header-contract.json": b"{}"}
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    manifest = {"schema_version": "data-export-provisional-1", "unacknowledged_torn_tail": False,
                "files": [{"path": name, "bytes": len(data), "sha256": digest(data)} for name, data in files.items()]}
    return tmp_path, manifest


def save_manifest(root, manifest):
    raw = json_bytes(manifest)
    (root / "manifest.json").write_bytes(raw)
    return digest(raw)


@pytest.mark.parametrize("mutation,code", [
    ("torn", "INTEGRITY_TORN_TAIL"), ("duplicate", "INTEGRITY_EXPORT_DUPLICATE"),
    ("size", "INTEGRITY_EXPORT_SIZE"), ("extra", "INTEGRITY_EXPORT_INVENTORY"),
    ("escape", "RELATIVE_PATH"), ("bytes", "FILE_PIN"),
])
def test_manifest_does_not_admit_changed_export(export, mutation, code):
    root, manifest = export
    if mutation == "torn": manifest["unacknowledged_torn_tail"] = True
    if mutation == "duplicate": manifest["files"].append(copy.deepcopy(manifest["files"][0]))
    if mutation == "size": manifest["files"][0]["bytes"] += 1
    if mutation == "extra": (root / "unexpected.csv").write_bytes(b"x")
    if mutation == "escape": manifest["files"][0]["path"] = "../outside.csv"
    if mutation == "bytes": (root / "trial-log.csv").write_bytes(b"wrong")
    with pytest.raises(PreparationError, match=code):
        verified_export(root, save_manifest(root, manifest))


@pytest.fixture
def native_run(tmp_path):
    source = os.environ.get("AV_INTEGRITY_NATIVE_PACKAGE")
    exports = os.environ.get("AV_INTEGRITY_NATIVE_EXPORT")
    if not source or not exports:
        pytest.skip("Native export mutations run after the Unity integrity runner")
    mapping = Path(exports) / "mapping.local.json"
    expected = mapping.with_suffix(mapping.suffix + ".sha256").read_text().strip()
    assert digest(mapping.read_bytes()) == expected
    plan = json.loads(mapping.read_bytes())
    # Admit the original independent pins before copying or mutating anything.
    verify_mapping(Path(source), plan["package_sha256"], mapping, expected)
    destination = tmp_path / "copy"
    shutil.copytree(exports, destination)
    return Path(source), destination, plan


def repin_export(root, plan):
    export = root / "export"
    manifest = json.loads((export / "manifest.json").read_bytes())
    for item in manifest["files"]:
        raw = (export / item["path"]).read_bytes()
        item.update(bytes=len(raw), sha256=digest(raw))
    plan["export_sha256"] = save_manifest(export, manifest)
    mapping = root / "mapping.local.json"
    mapping.write_bytes(json_bytes(plan))
    return mapping, digest(mapping.read_bytes())


@pytest.mark.parametrize("mutation,code", [
    ("score", "INTEGRITY_SCORE"), ("pcm", "INTEGRITY_PCM_BINDING"),
    ("heldout", "INTEGRITY_NOVEL_AUTHORIZATION"), ("delivery", "INTEGRITY_SYNTHETIC_DELIVERY_SCOPE"),
])
def test_native_semantics_survive_rehashed_mutations(native_run, mutation, code):
    package, root, plan = native_run
    export = root / "export"
    if mutation in ("score", "delivery"):
        name = "trial-log.csv" if mutation == "score" else "exposure-ledger.csv"
        path = export / name
        rows = csv_rows(path.read_bytes())
        if mutation == "score":
            # Correct first case becomes legal but wrong; retain every independent binding.
            from analysis.command_scoring import ACTIONS, legal
            row = rows[0]
            row["response_action"] = next(a for group in ACTIONS for a in group
                                          if legal(a, row["response_target"]) and a != row["response_action"])
        else:
            rows[0]["audible_status"] = "confirmed"
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader(); writer.writerows(rows)
    else:
        changed = False
        for path in sorted((export / "raw").iterdir()):
            rows = [json.loads(line) for line in path.read_bytes().splitlines()]
            for record in rows:
                if mutation == "pcm" and record.get("event_type") == "audio_request" and not changed:
                    record["payload"]["pcm_sha256"] = "0" * 64
                    changed = True
                if mutation == "heldout" and record.get("payload", {}).get("event") == "novel_buffer_authorized":
                    record["payload"]["event"] = "authorization_removed"
                    changed = True
            path.write_bytes(b"".join((json.dumps(row, separators=(",", ":")) + "\n").encode() for row in rows))
        assert changed
    mapping, pin = repin_export(root, plan)
    with pytest.raises(PreparationError, match=code):
        verify_mapping(package, plan["package_sha256"], mapping, pin)
