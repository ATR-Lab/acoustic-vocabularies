"""Identifier formats shared by every log."""

import pytest

from av_generation.ids import (
    PANEL_ALIAS_ALPHABET,
    IdError,
    bank_set,
    bank_slot_id,
    check_book,
    check_id,
    check_panel_alias,
    is_demo,
    parse_bank_slot_id,
    parse_proposal_slot_id,
    parse_rating_slot_id,
    proposal_slot_id,
    rating_slot_id,
    round_and_slot,
    slot_index,
)


def test_formats_and_parsing():
    sid = proposal_slot_id("BK-C-7QX4MN", "K-a1", 2, 3)
    assert sid == "BK-C-7QX4MN.K-a1.r2s3"
    assert parse_proposal_slot_id(sid) == ("BK-C-7QX4MN", "K-a1", 2, 3)
    bid = bank_slot_id("bank-C001", 1, "P2", "Q-r4", 7)
    assert bid == "bank-C001.t1.P2.Q-r4.s07"
    assert parse_bank_slot_id(bid) == ("bank-C001", 1, "P2", "Q-r4", 7)
    rid = rating_slot_id("A-P01", "K-a1", 2, 5)
    assert rid == "A-P01.K-a1.r2p5"
    assert parse_rating_slot_id(rid) == ("A-P01", "K-a1", 2, 5)


def test_slot_index():
    assert [slot_index(r, s) for r in range(1, 5) for s in range(1, 4)] == list(range(1, 13))
    assert all(round_and_slot(slot_index(r, s)) == (r, s) for r in range(1, 5) for s in (1, 2, 3))


@pytest.mark.parametrize(
    "call",
    [
        lambda: proposal_slot_id("BK-A1-XYZ", "K-a1", 1, 1),
        lambda: proposal_slot_id("BK-C-7QX4MN", "K-a9", 1, 1),
        lambda: proposal_slot_id("BK-C-7QX4MN", "K-a1", 5, 1),
        lambda: bank_slot_id("bank-C001", 0, "P1", "K-a1", 1),
        lambda: bank_slot_id("bank-C001", 1, "P9", "K-a1", 1),
        lambda: rating_slot_id("A-P01", "K-a1", 1, 10),
        lambda: check_id("a|b"),
        lambda: check_book("BK-transformer-1"),
        lambda: slot_index(1, True),
        lambda: round_and_slot(13),
        lambda: parse_proposal_slot_id("BK.K-a1.r1s1"),
        lambda: parse_bank_slot_id("bank-C001.t1.P2.Q-r4.s7"),
        lambda: parse_rating_slot_id("A-P01.K-a1.r2p0"),
        lambda: parse_rating_slot_id(5),
    ],
)
def test_rejections(call):
    with pytest.raises(IdError):
        call()


def test_demo():
    assert is_demo("DEMO-A-P01") and not is_demo("A-P01")


def test_panel_aliases_and_bank_sets():
    assert check_panel_alias("PB-K7MW") == "PB-K7MW"
    for bad in ("PB-A1XX", "PB-D2XX", "PB-K7M", "BK-K7MW"):
        with pytest.raises(IdError):
            check_panel_alias(bad)
    assert not set("AD0123") & set(PANEL_ALIAS_ALPHABET)
    assert [bank_set(b) for b in ("bank-P001", "bank-C072", "DEMO-bank-01")] == [
        "pilot",
        "confirmatory",
        "demo",
    ]
    for bad in ("PILOT-B-01", "B-001", "bank-C01", None):
        with pytest.raises(IdError):
            bank_set(bad)
