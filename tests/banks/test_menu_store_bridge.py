"""Qualified synthetic #26 bank -> sealed #13 package -> real private #11 mailbox.

The shared bank fixture uses the actual builder and a scripted model. All files
are synthetic, local test output. No participant bank or study asset is used.
"""

import hashlib
import json
import shutil
import sys
import uuid
from pathlib import Path

import pytest
from av_sound.package import build_dyad_package, seal
from av_sound.store import canonical_json
from jsonschema import Draft202012Validator

from av_banks.amend import amend_bank
from av_banks.handoff import qualified_dyad_bank

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.menu_store_bridge import (  # noqa: E402 - root tools are outside the banks distribution
    EMPTY_SNAPSHOT,
    BridgeError,
    MenuStoreBridge,
    _atomic_new,
    read_json,
    serve_mailbox,
)
from tools.menu_store_fixture import prepare_fixture  # noqa: E402


def pin(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def qualified_producer(built_bank, tmp_path_factory):
    root = tmp_path_factory.mktemp("qualified-menu-producer")
    bank = qualified_dyad_bank(built_bank.bank_dir, expected_bank_sha256=built_bank.bank_sha256)
    result = build_dyad_package(bank, root / "package")
    permutation = built_bank.bank_dir / "permutation.json"
    package_hash = seal(
        result.path,
        permutation=permutation,
        allocation_extras={"swap_w1_w4": False, "structured_family": "K"},
    )
    return dict(
        schema_version=1,
        demo_only=True,
        unit_id="DEMO-" + json.loads(permutation.read_bytes())["unit_id"],
        book_id="DEMO-QUALIFIED-MENU",
        bank_path=str(built_bank.bank_dir / "manifest.json"),
        bank_file_sha256=pin(built_bank.bank_dir / "manifest.json"),
        bank_sha256=built_bank.bank_sha256,
        package_path=str(result.path),
        package_sha256=package_hash,
    )


@pytest.fixture
def bridge(qualified_producer, tmp_path):
    return MenuStoreBridge(dict(qualified_producer, store_root=str(tmp_path / "store")))


def request(bridge, previous=None, *, atom=None):
    return dict(
        schema_version=1,
        request_id=uuid.uuid4().hex,
        operation="atom" if atom else "profile",
        unit_id=bridge.config["unit_id"],
        book_id=bridge.book_id,
        bank_sha256=bridge.config["bank_sha256"],
        package_sha256=bridge.config["package_sha256"],
        expected_head=previous["after_head"] if previous else None,
        expected_snapshot_sha256=(
            previous["after_snapshot_sha256"] if previous else EMPTY_SNAPSHOT
        ),
        profile="P1",
        menu_key=atom or "profile",
        rank=1 if atom else None,
    )


def copied_bank(bridge, tmp_path):
    original = Path(bridge.config["bank_path"]).parent
    target = tmp_path / "copy" / original.name
    shutil.copytree(original, target)
    return MenuStoreBridge(dict(bridge.config, bank_path=str(target / "manifest.json")))


def test_qualified_manifest_pin_and_real_mailbox_commit(bridge, tmp_path):
    bank, package, _ = bridge._inputs()
    assert bank.qualified and bank.handoff.bank_sha256 == bridge.config["bank_sha256"]
    assert bank.bank_sha256() != bank.handoff.bank_sha256
    assert package.manifest["bank"] == bank.package_bank()
    mailbox = tmp_path / "mailbox"
    (mailbox / "requests").mkdir(parents=True)
    previous = None
    validator = Draft202012Validator(
        read_json(ROOT / "apparatus/schemas/menu-store-bridge.schema.json")
    )
    for atom in (None, "K-a1"):
        value = request(bridge, previous, atom=atom)
        _atomic_new(mailbox / "requests" / (value["request_id"] + ".json"), value)
        serve_mailbox(bridge, mailbox, seconds=30, max_requests=1)
        response = read_json(mailbox / "responses" / (value["request_id"] + ".json"))
        validator.validate(response)
        assert response["error"] is None
        previous = response["receipt"]
        assert previous["status"] == ("committed" if atom else "profile_selected")
        assert previous["bank_sha256"] == bank.handoff.bank_sha256
        assert previous["source_kind"] == "synthetic" and not previous["participant_ready"]
        assert response["snapshot"]["book_head"] == previous["after_head"]
    assert bridge.select(value) == previous
    assert len(bridge.store.records(bridge.book_id)) == 2


def test_qualified_handoff_does_not_accept_the_converted_provisional_digest(bridge):
    bank, _, _ = bridge._inputs()
    other = MenuStoreBridge(dict(bridge.config, bank_sha256=bank.bank_sha256()))
    with pytest.raises(BridgeError, match="BANK_QUALIFICATION"):
        other.select(request(other))
    assert not other.store.log_path(other.book_id).exists()


def test_qualified_unit_must_use_the_exact_existing_demo_mapping(bridge):
    other = MenuStoreBridge(dict(bridge.config, unit_id="DEMO-B-C02"))
    with pytest.raises(BridgeError, match="BANK_UNIT_BINDING"):
        other.select(request(other))
    assert not other.pending.exists() and not other.journal.exists()


@pytest.mark.parametrize("with_allocation", [False, True])
def test_qualified_package_requires_permutation_and_complete_allocation(
    bridge, tmp_path, with_allocation
):
    bank, _, _ = bridge._inputs()
    result = build_dyad_package(bank, tmp_path / "unsealed")
    package_hash = result.package_sha256
    if with_allocation:
        package_hash = seal(
            result.path,
            permutation=Path(bridge.config["bank_path"]).parent / "permutation.json",
            allocation_extras={"swap_w1_w4": False},
        )
    other = MenuStoreBridge(
        dict(bridge.config, package_path=str(result.path), package_sha256=package_hash)
    )
    with pytest.raises(BridgeError, match="QUALIFIED_PACKAGE_UNSEALED"):
        other.select(request(other))
    assert not other.store.log_path(other.book_id).exists()


def test_qualified_permutation_bytes_must_equal_the_bank_source(bridge, tmp_path):
    bank, package, _ = bridge._inputs()
    result = build_dyad_package(bank, tmp_path / "different-permutation")
    differently_encoded = canonical_json(package.permutation) + b"\n"
    assert differently_encoded != package.read_file("permutation.json")
    package_hash = seal(
        result.path,
        permutation=differently_encoded,
        allocation_extras={"swap_w1_w4": False, "structured_family": "K"},
    )
    other = MenuStoreBridge(
        dict(bridge.config, package_path=str(result.path), package_sha256=package_hash)
    )
    with pytest.raises(BridgeError, match="BANK_PERMUTATION_BINDING"):
        other.select(request(other))


def test_qualified_bank_rejects_an_amendment_even_with_the_same_bank_hash(bridge, tmp_path):
    other = copied_bank(bridge, tmp_path)
    amend_bank(
        Path(other.config["bank_path"]).parent,
        profile="P1",
        atom_id="K-a1",
        rank=2,
        reason="DEMO qualified bridge amendment rejection",
        unheard_confirmed=True,
    )
    assert pin(Path(other.config["bank_path"])) == other.config["bank_file_sha256"]
    with pytest.raises(BridgeError, match="BANK_QUALIFICATION"):
        other.select(request(other))
    assert not other.pending.exists()


def test_qualified_bank_rechecks_the_actual_source_waveforms(bridge, tmp_path):
    other = copied_bank(bridge, tmp_path)
    path = Path(other.config["bank_path"]).parent / "options/P1/K-a1-1.wav"
    raw = bytearray(path.read_bytes())
    raw[-1] ^= 1
    path.write_bytes(raw)
    with pytest.raises(BridgeError, match="BANK_QUALIFICATION"):
        other.select(request(other))
    assert not other.store.log_path(other.book_id).exists()


def test_qualified_source_rejects_non_demo_scope_before_verification(bridge, tmp_path):
    other = copied_bank(bridge, tmp_path)
    path = Path(other.config["bank_path"])
    document = read_json(path)
    document.update(demo=False, set="confirmatory", bank_id="bank-C001")
    path.write_bytes(canonical_json(document))
    other = MenuStoreBridge(dict(other.config, bank_file_sha256=pin(path)))
    with pytest.raises(BridgeError, match="QUALIFIED_BANK_UNAVAILABLE"):
        other.select(request(other))


def test_qualified_crash_after_actual_commit_recovers_identical_request(bridge, monkeypatch):
    profile = bridge.select(request(bridge))
    value = request(bridge, profile, atom="K-a1")

    def interrupted(*args):
        raise OSError("injected after qualified actual store append")

    monkeypatch.setattr(bridge, "_append", interrupted)
    with pytest.raises(OSError, match="injected"):
        bridge.select(value)
    before = bridge.store.log_path(bridge.book_id).read_bytes()
    receipt = MenuStoreBridge(bridge.config).select(value)
    assert receipt["status"] == "committed"
    assert bridge.store.log_path(bridge.book_id).read_bytes() == before
    assert len(bridge.store.records(bridge.book_id)) == 2 and not bridge.pending.exists()


@pytest.mark.parametrize("mode", ["service", "codec"])
def test_qualified_fixture_preserves_sources_and_stages_the_actual_bridge(bridge, tmp_path, mode):
    source = tmp_path / "source.json"
    source.write_bytes(canonical_json(bridge.config))
    bank_root = Path(bridge.config["bank_path"]).parent
    before = {p.relative_to(bank_root): pin(p) for p in bank_root.rglob("*") if p.is_file()}
    result = prepare_fixture(source, pin(source), tmp_path / "fixture", mode=mode)
    assert result["qualified_bank"] and not result["participant_ready"]
    assert result["bank_sha256"] == bridge.config["bank_sha256"]
    after = {p.relative_to(bank_root): pin(p) for p in bank_root.rglob("*") if p.is_file()}
    assert before == after and not bridge.store.log_path(bridge.book_id).exists()
    if mode == "codec":
        response = read_json(tmp_path / "fixture/atom-response.json")
        assert response["receipt"]["status"] == "committed"


def test_qualified_fixture_refuses_output_inside_the_source_bank(bridge, tmp_path):
    source = tmp_path / "source.json"
    source.write_bytes(canonical_json(bridge.config))
    destination = Path(bridge.config["bank_path"]).parent / "new-fixture"
    with pytest.raises(BridgeError, match="FIXTURE_SOURCE_OVERLAP"):
        prepare_fixture(source, pin(source), destination, mode="service")
    assert not destination.exists()


@pytest.mark.parametrize(
    "relative",
    [
        "options/P3/Q-r4-4.wav",
        "attempts/1/slots.jsonl",
        "generation-config.json",
        "permutation.json",
        "bank-sha256.txt",
    ],
)
def test_warm_qualified_proof_rechecks_every_source_byte_even_with_old_mtime(
    bridge, tmp_path, relative
):
    import os

    other = copied_bank(bridge, tmp_path)
    other._inputs()
    path = Path(other.config["bank_path"]).parent / relative
    info = path.stat()
    raw = bytearray(path.read_bytes())
    raw[-1] ^= 1
    path.write_bytes(raw)
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    with pytest.raises(BridgeError, match="BANK_SOURCE_CHANGED"):
        other.select(request(other))
    assert not other.pending.exists() and not other.store.log_path(other.book_id).exists()


@pytest.mark.parametrize("change", ["extra", "amendment", "missing", "directory", "link"])
def test_warm_qualified_proof_refuses_changed_file_inventory(bridge, tmp_path, change):
    other = copied_bank(bridge, tmp_path)
    other._inputs()
    root = Path(other.config["bank_path"]).parent
    if change == "extra":
        (root / "extra.json").write_bytes(b"{}\n")
    elif change == "amendment":
        (root / "amendments.jsonl").write_bytes(b"")
    elif change == "missing":
        (root / "options/P3/Q-r4-4.wav").unlink()
    elif change == "directory":
        (root / "extra-directory").mkdir()
    else:
        try:
            (root / "extra-link").symlink_to(root / "manifest.json")
        except OSError:
            pytest.skip("host cannot create a symlink")
    with pytest.raises(BridgeError, match="BANK_SOURCE_CHANGED|MAILBOX_LINK"):
        other.select(request(other))
    assert not other.pending.exists()


def test_warm_qualified_inputs_are_detached_from_previous_return_values(bridge):
    first, _, _ = bridge._inputs()
    expected = dict(first.labels)
    first.labels["K-a1"] = "changed outside the bridge"
    first.cells.clear()
    second, _, _ = bridge._inputs()
    assert second.labels == expected and len(second.cells) == 48


def test_mutation_during_full_qualification_does_not_establish_a_warm_proof(
    bridge, tmp_path, monkeypatch
):
    import av_banks.handoff as handoff

    other = copied_bank(bridge, tmp_path)
    original = handoff.qualified_dyad_bank

    def interrupted(*args, **kwargs):
        bank = original(*args, **kwargs)
        (Path(other.config["bank_path"]).parent / "extra.json").write_bytes(b"{}\n")
        return bank

    monkeypatch.setattr(handoff, "qualified_dyad_bank", interrupted)
    with pytest.raises(BridgeError, match="BANK_SOURCE_CHANGED"):
        other.select(request(other))
    assert other._qualified_source is None and not other.pending.exists()


def test_source_mutation_before_mailbox_publication_preserves_uncertain_commit_recovery(
    bridge, tmp_path, monkeypatch
):
    other = copied_bank(bridge, tmp_path)
    profile = other.select(request(other))
    value = request(other, profile, atom="K-a1")
    mailbox = tmp_path / "mailbox"
    (mailbox / "requests").mkdir(parents=True)
    _atomic_new(mailbox / "requests" / (value["request_id"] + ".json"), value)
    path = Path(other.config["bank_path"]).parent / "options/P3/Q-r4-4.wav"
    original_bytes = path.read_bytes()
    original_read = other._verified_snapshot_locked

    def interrupted(*args):
        snapshot = original_read(*args)
        changed = bytearray(original_bytes)
        changed[-1] ^= 1
        path.write_bytes(changed)
        return snapshot

    monkeypatch.setattr(other, "_verified_snapshot_locked", interrupted)
    with pytest.raises(BridgeError, match="BANK_SOURCE_CHANGED"):
        serve_mailbox(other, mailbox, seconds=30, max_requests=1)
    assert not (mailbox / "responses" / (value["request_id"] + ".json")).exists()
    journal = other.journal.read_bytes()
    store = other.store.log_path(other.book_id).read_bytes()
    receipt = json.loads(journal.splitlines()[-1])["receipt"]
    assert receipt["status"] == "committed" and not other.pending.exists()
    path.write_bytes(original_bytes)
    serve_mailbox(MenuStoreBridge(other.config), mailbox, seconds=30, max_requests=1)
    result = read_json(mailbox / "responses" / (value["request_id"] + ".json"))
    assert result["receipt"] == receipt
    assert other.journal.read_bytes() == journal
    assert other.store.log_path(other.book_id).read_bytes() == store
