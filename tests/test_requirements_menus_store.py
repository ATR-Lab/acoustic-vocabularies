"""Requirement trace: profile and vocabulary immutability at the menu store bridge.

Real DEMO dyad bank/package and the actual vocabulary store; no participant data.
"""
import hashlib
import os
from pathlib import Path
import stat
import sys
import uuid

import pytest

pytest.importorskip('numpy', reason='actual store bridge tests run in the locked sound environment')

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'sound/src')]
from tools.menu_store_bridge import MenuStoreBridge, BridgeError, EMPTY_SNAPSHOT  # noqa: E402
from av_sound.dyad_bank import synthetic_dyad_bank  # noqa: E402
from av_sound.package import build_dyad_package  # noqa: E402
from av_sound.store import canonical_json  # noqa: E402


@pytest.fixture(scope='module')
def producer(tmp_path_factory):
    root = tmp_path_factory.mktemp('requirements-producer-demo')
    bank = synthetic_dyad_bank()
    path = root/'bank.json'; path.write_bytes(canonical_json(bank.to_dict()))
    result = build_dyad_package(bank, root/'package')
    return dict(schema_version=1, demo_only=True, unit_id='DEMO-UNIT-01', book_id='DEMO-BOOK-01',
                bank_path=str(path), bank_file_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                bank_sha256=bank.bank_sha256(), package_path=str(result.path), package_sha256=result.package_sha256)


@pytest.fixture
def bridge(producer, tmp_path):
    return MenuStoreBridge(dict(producer, store_root=str(tmp_path/'store')))


def request(bridge, previous=None, *, atom=None, rank=1, profile='P1'):
    return dict(schema_version=1, request_id=uuid.uuid4().hex, operation='atom' if atom else 'profile',
                unit_id=bridge.config['unit_id'], book_id=bridge.book_id,
                bank_sha256=bridge.config['bank_sha256'], package_sha256=bridge.config['package_sha256'],
                expected_head=previous['after_head'] if previous else None,
                expected_snapshot_sha256=previous['after_snapshot_sha256'] if previous else EMPTY_SNAPSHOT,
                profile=profile, menu_key=atom or 'profile', rank=rank if atom else None)


def state(bridge):
    log = bridge.store.log_path(bridge.book_id)
    return (log.read_bytes() if log.exists() else b'', bridge.journal.read_bytes() if bridge.journal.exists() else b'')


def damage_blob(bridge, atom):
    blob = bridge.store.blob_path(bridge.store.get(bridge.book_id, atom).pcm_sha256)
    os.chmod(blob, stat.S_IWRITE | stat.S_IREAD)
    data = bytearray(blob.read_bytes()); data[-1] ^= 1; blob.write_bytes(data)


def test_profile_is_selected_once_and_never_changes(bridge):
    """REQ-MENU-02: the committed profile never changes; later profile or other-profile atom requests change nothing."""
    first = bridge.select(request(bridge, profile='P1'))
    assert first['status'] == 'profile_selected' and first['profile'] == 'P1'
    before = state(bridge)
    with pytest.raises(BridgeError, match='PROFILE_ALREADY_SELECTED'):
        bridge.select(request(bridge, first, profile='P2'))
    with pytest.raises(BridgeError, match='FIXED_PROFILE'):
        bridge.select(request(bridge, first, atom='K-a1', profile='P3'))
    assert state(bridge) == before and not bridge.pending.exists()
    committed = bridge.select(request(bridge, first, atom='K-a1', profile='P1'))
    assert committed['status'] == 'committed' and committed['profile'] == 'P1'
    assert MenuStoreBridge(bridge.config).verified_snapshot(
        expected_head=committed['after_head'], expected_snapshot_sha256=committed['after_snapshot_sha256'])['profile'] == 'P1'


def test_changed_old_entry_blocks_the_next_expansion_before_anything_is_added(bridge):
    """REQ-VOC-01: old entry hashes are checked before an expansion; a changed entry stops it with no new entry."""
    profile = bridge.select(request(bridge))
    first = bridge.select(request(bridge, profile, atom='K-a1'))
    damage_blob(bridge, 'K-a1')
    before = state(bridge)
    with pytest.raises(BridgeError, match='STORE_INTEGRITY'):
        bridge.select(request(bridge, first, atom='K-a2'))
    assert state(bridge) == before and not bridge.pending.exists()
    assert len(bridge.journal.read_bytes().splitlines()) == 2, 'Only the profile and K-a1 receipts exist'


def test_old_entry_changed_during_an_expansion_is_caught_after_it_and_no_receipt_is_issued(bridge, monkeypatch):
    """REQ-VOC-01: old entry hashes are checked again after each expansion; a change then yields no receipt."""
    profile = bridge.select(request(bridge))
    first = bridge.select(request(bridge, profile, atom='K-a1'))
    commit = bridge.store.commit

    def commit_then_change_old_entry(*args, **kwargs):
        result = commit(*args, **kwargs)
        damage_blob(bridge, 'K-a1')
        return result
    monkeypatch.setattr(bridge.store, 'commit', commit_then_change_old_entry)
    journal = bridge.journal.read_bytes()
    with pytest.raises(BridgeError, match='STORE_INTEGRITY'):
        bridge.select(request(bridge, first, atom='K-a2'))
    assert bridge.journal.read_bytes() == journal, 'No receipt is journalled for the expansion'


def test_expansion_only_adds_entries_and_committed_entries_cannot_be_rewritten(bridge):
    """REQ-VOC-01: each expansion adds exactly its own atom; a committed atom cannot be committed again."""
    receipt = bridge.select(request(bridge))
    snapshots = []
    for atom in ('K-a1', 'K-r1', 'Q-a1'):
        before = bridge.store.snapshot(bridge.book_id)
        receipt = bridge.select(request(bridge, receipt, atom=atom))
        after = bridge.store.snapshot(bridge.book_id)
        assert receipt['status'] == 'committed'
        assert set(after) == set(before) | {atom} and all(after[k] == v for k, v in before.items())
        snapshots.append(after)
    log = state(bridge)
    for rank in (1, 2):
        with pytest.raises(BridgeError, match='ATOM_ALREADY_COMMITTED'):
            bridge.select(request(bridge, receipt, atom='K-a1', rank=rank))
    assert state(bridge) == log and bridge.store.snapshot(bridge.book_id) == snapshots[-1]
