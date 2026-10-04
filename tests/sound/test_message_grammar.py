"""Message grammar and fixed matrix (issue O4.1.4; Protocol constants)."""

from __future__ import annotations

from collections import Counter

import pytest

from av_sound.grammar import (
    ATOM_IDS,
    FAMILIES,
    HELDOUT_MESSAGE_IDS,
    HELDOUT_SETS,
    INDICES,
    MATRIX,
    MESSAGES,
    TRAINED_MESSAGE_IDS,
    AtomRef,
    GrammarError,
    MessageRef,
    atom_id,
    message_id,
    parse_atom_id,
    parse_message_id,
)

PROTOCOL_MATRIX = (
    ("Train V1", "H-V1", "Train V2", "H-W1"),
    ("H-W4", "Train V1", "H-V2", "Train V3"),
    ("Train V2", "H-W1", "Train V2", "Train V3"),
    ("H-V3", "Train V3", "Train V3", "H-W4"),
)


def test_matrix_matches_protocol_constants():
    assert MATRIX == PROTOCOL_MATRIX


def test_counts_18_trained_14_heldout():
    assert len(ATOM_IDS) == len(set(ATOM_IDS)) == 16
    assert len(MESSAGES) == len({m.message_id for m in MESSAGES}) == 32
    assert len(TRAINED_MESSAGE_IDS) == 18
    assert len(HELDOUT_MESSAGE_IDS) == 14
    assert set(TRAINED_MESSAGE_IDS).isdisjoint(HELDOUT_MESSAGE_IDS)
    assert set(TRAINED_MESSAGE_IDS) | set(HELDOUT_MESSAGE_IDS) == {m.message_id for m in MESSAGES}


@pytest.mark.parametrize("family", FAMILIES)
def test_per_family_waves_and_heldout_sets(family):
    cells = [m for m in MESSAGES if m.family == family]
    waves = Counter(m.training_wave for m in cells if not m.is_heldout)
    assert waves == {1: 2, 2: 3, 3: 4}  # 2 + 3 + 4 = 9 trained
    sets = Counter(m.heldout_set for m in cells if m.is_heldout)
    assert sets == {"H-V1": 1, "H-V2": 1, "H-V3": 1, "H-W1": 2, "H-W4": 2}  # 7 held out
    assert set(sets) == set(HELDOUT_SETS)
    # Every atom is a component of at least one trained message.
    trained = [m for m in cells if not m.is_heldout]
    assert {m.action_index for m in trained} == set(INDICES)
    assert {m.referent_index for m in trained} == set(INDICES)


def test_known_cells():
    m = parse_message_id("K-a1-r2")
    assert (m.status, m.heldout_set, m.training_wave, m.is_heldout) == ("H-V1", "H-V1", None, True)
    m = parse_message_id("Q-a4-r3")
    assert (m.status, m.heldout_set, m.training_wave, m.is_heldout) == ("Train V3", None, 3, False)
    assert m.action == AtomRef("Q", "action", 4)
    assert m.referent.atom_id == "Q-r3"
    assert TRAINED_MESSAGE_IDS[:2] == ("K-a1-r1", "K-a1-r3")
    assert ATOM_IDS[:5] == ("K-a1", "K-a2", "K-a3", "K-a4", "K-r1")


def test_ids_round_trip():
    for a in ATOM_IDS:
        ref = parse_atom_id(a)
        assert ref.atom_id == a == atom_id(ref.family, ref.role, ref.index)
    for m in MESSAGES:
        assert parse_message_id(m.message_id) == m
        assert message_id(m.family, m.action_index, m.referent_index) == m.message_id


@pytest.mark.parametrize(
    "value", ["K-a0", "K-a5", "k-a1", "K-a1 ", "K-b1", "R-a1", "Ka1", "K-a1-r1", "", 7, None]
)
def test_bad_atom_ids(value):
    with pytest.raises(GrammarError):
        parse_atom_id(value)


@pytest.mark.parametrize(
    "value", ["K-a1-r5", "K-r1-a1", "K-a1", "Q-a1-r1-", "K-a1_r1", "X-a1-r1", 12, b"K-a1-r1"]
)
def test_bad_message_ids(value):
    with pytest.raises(GrammarError):
        parse_message_id(value)


def test_constructors_reject_values_outside_the_grammar():
    with pytest.raises(GrammarError):
        atom_id("X", "action", 1)
    with pytest.raises(GrammarError):
        atom_id("K", "target", 1)
    with pytest.raises(GrammarError):
        atom_id("K", "action", True)
    with pytest.raises(GrammarError):
        message_id("K", 1, 5)
    with pytest.raises(GrammarError):
        MessageRef("Q", 0, 1)
    assert isinstance(GrammarError("x"), ValueError)
