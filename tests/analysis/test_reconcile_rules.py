"""Single rules of C1, C3 and C5 that the fault suite does not reach (#33 review
follow-up): exit-manifest sizes, receipt self-hashes, rejected receipts, and package JSON
in the format the sound stack writes (fixture copied from its DEMO example)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from av_analysis.fileio import json_bytes, read_bytes
from av_analysis.paths import DataRoot, write_synthetic_input
from av_analysis.reconcile import reconcile_visit
from av_analysis.references import InputReader, _load_package, canonical_sha256, input_path
from av_analysis.synthetic_logs import build_synthetic_root, suite_visits

SEED = "DEMO-test-33-rules"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "package-demo"


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    return build_synthetic_root(
        tmp_path_factory.mktemp("rules") / "base", seed_label=SEED, max_persons=1
    )


@pytest.fixture
def root(base, tmp_path):
    target = tmp_path / "root"
    shutil.copytree(base.path, target)
    return DataRoot.open(target)


def details(report, code):
    return [d.detail for c in report.checks for d in c.discrepancies if d.code == code]


def test_exit_manifest_size_alone_is_compared(root):
    vid = suite_visits(root)["D0"]
    manifest = json.loads(read_bytes(root.raw_visit_dir(vid) / "exit-manifest.json"))
    entry = next(f for f in manifest["files"] if f["path"] == "trial-log.csv")
    entry["bytes"] += 1  # the SHA-256 still matches
    write_synthetic_input(root, "raw", f"{vid}/exit-manifest.json", json_bytes(manifest))
    report = reconcile_visit(root, vid)
    found = [(d.code, d.rows) for c in report.checks for d in c.discrepancies if c.check == "C1"]
    assert found == [("RAW_HASH_CHANGED", ("trial-log.csv",))]


def receipts(root, vid):
    rel = input_path("store_receipts", unit_id=vid[:5]).removeprefix("inputs/")
    lines = [json.loads(x) for x in read_bytes(root.input_path("inputs", rel)).splitlines()]
    return rel, lines


def put_receipts(root, rel, lines):
    data = b"".join(
        json.dumps(x, sort_keys=True, separators=(",", ":")).encode() + b"\n" for x in lines
    )
    write_synthetic_input(root, "inputs", rel, data)


def test_receipt_self_hash_is_checked(root):
    vid = suite_visits(root)["V2"]
    rel, lines = receipts(root, vid)
    lines[1]["request_id"] = "0" * 32  # receipt_sha256 left as it was
    put_receipts(root, rel, lines)
    report = reconcile_visit(root, vid)
    assert details(report, "STORE_CHAIN_BROKEN") == ["receipt self-hash differs"]


def test_rejected_receipt_must_not_move_the_head(root):
    vid = suite_visits(root)["V1"]
    rel, lines = receipts(root, vid)
    lines[1]["status"] = "rejected"
    lines[1]["receipt_sha256"] = canonical_sha256(lines[1], "receipt_sha256")
    put_receipts(root, rel, lines)
    found = details(reconcile_visit(root, vid), "STORE_CHAIN_BROKEN")
    assert "rejected receipt moved the head" in found
    assert "receipt self-hash differs" not in found


def test_package_json_of_the_sound_stack_loads(tmp_path):
    """``sound/examples/package-demo`` manifest and audio JSON (DEMO, Study A) as #13
    writes them: the package hash, every atom and message, composed held-out audio."""
    manifest = json.loads((FIXTURES / "manifest.json").read_bytes())
    audio = json.loads((FIXTURES / "audio.json").read_bytes())
    assert manifest["demo"] is True and manifest["study"] == "A"
    root = DataRoot.create(tmp_path / "pkg", "SYNTHETIC", label="DEMO-test-33-package")
    package_id = manifest["package_id"]
    for name in ("manifest.json", "audio.json"):
        write_synthetic_input(
            root, "inputs", f"packages/{package_id}/{name}", (FIXTURES / name).read_bytes()
        )
    reader = InputReader(root)
    sha, expected, profile = _load_package(root, reader, "A", package_id)
    assert sha == manifest["package_sha256"] and profile == audio["profile"]
    assert len(reader.sha) == 2
    assert set(expected) == {a["atom_id"] for a in audio["atoms"]} | {
        m["message_id"] for m in audio["messages"]
    }
    for atom in audio["atoms"]:
        e = expected[atom["atom_id"]]
        assert e.matches(atom["pcm_sha256"]) and e.matches(atom["file_sha256"])
    for message in audio["messages"]:
        e = expected[message["message_id"]]
        assert e.matches(message["composite_sha256"])
        assert e.file_sha256 == message["file_sha256"]
    held = [m for m in audio["messages"] if m["status"] == "heldout"]
    assert held and all(expected[m["message_id"]].file_sha256 is None for m in held)
    assert not expected["K-a1"].matches("0" * 64)
