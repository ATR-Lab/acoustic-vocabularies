"""Seed keys and derivation (#16 acceptance: identical on every OS, 0 collisions A vs B)."""

import hashlib

import numpy as np
import pytest
from av_sound.grammar import ATOM_IDS
from hypothesis import given
from hypothesis import strategies as st

from av_generation import seeds
from av_generation.seeds import (
    SEED_MAX,
    SeedKeyError,
    SeedNamespace,
    a1_seed_key,
    a2_seed_key,
    a3_seed_key,
    b_seed_key,
    bot_seed_key,
    derive_seed,
    join_key,
    panel_seed_key,
    parse_seed_key,
    rng_for,
    seed_from_key,
    seeds_digest,
    threshold_seed_key,
    unwire_seed,
    wire_seed,
)

# Pinned on macOS arm64; CI recomputes them on Linux, macOS and Windows.
PINNED = {
    "A1|A-P01|K-a1|1|1": 15574008062404909364,
    "A2|A-P01|K-a1|1|1": 7006589691015645119,
    "A3|A-P01|K-a1|2|3": 7336025732795478269,
    "B|bank-C001|1|P2|Q-r4|7": 4054462099563635070,
    "B|DEMO-bank-01|4|P3|Q-r4|12": 7024697643409767515,
}
SAMPLE_DIGEST = "f1c589280be645fc2e266c008ac1219f773e9f728fcc1492e442b7f7f05c2b38"


def sample_keys() -> list[str]:
    """11,520 keys: A2 and A3 for 18 confirmatory batches, B for two banks (all slots)."""
    keys = []
    for builder in (a2_seed_key, a3_seed_key):
        for batch in range(1, 19):
            for atom in ATOM_IDS:
                for rnd in range(1, 5):
                    for slot in range(1, 4):
                        keys.append(builder(f"A-C{batch:02d}", atom, rnd, slot))
    for bank in ("bank-C001", "bank-C002"):
        for attempt in range(1, 5):
            for profile in ("P1", "P2", "P3"):
                for atom in ATOM_IDS:
                    for slot in range(1, 13):
                        keys.append(b_seed_key(bank, attempt, profile, atom, slot))
    return keys


def test_definition_is_first_8_bytes_of_sha256_big_endian():
    key = "A3|A-P01|K-a1|2|3"
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    assert seed_from_key(key) == int.from_bytes(digest[:8], "big")
    assert derive_seed("A3", "A-P01", "K-a1", 2, 3) == seed_from_key(key)


@pytest.mark.parametrize(("key", "seed"), sorted(PINNED.items()))
def test_pinned_values(key, seed):
    assert seed_from_key(key) == seed


def test_10000_keys_identical_everywhere_and_collision_free():
    keys = sample_keys()
    assert len(keys) >= 10_000
    assert len(set(keys)) == len(keys)
    values = [seed_from_key(k) for k in keys]
    assert len(set(values)) == len(values), "seed collision across the A and B key spaces"
    assert all(0 <= v <= SEED_MAX for v in values)
    assert seeds_digest(keys) == SAMPLE_DIGEST


def test_key_builders_format():
    assert a1_seed_key("A-P01", "K-a1", 1, 1) == "A1|A-P01|K-a1|1|1"
    assert a2_seed_key("A-P01", "Q-r4", 4, 3) == "A2|A-P01|Q-r4|4|3"
    assert a3_seed_key("A-P01.rebuild-2", "K-a1", 2, 3) == "A3|A-P01.rebuild-2|K-a1|2|3"
    assert b_seed_key("bank-C001", 1, "P2", "Q-r4", 7) == "B|bank-C001|1|P2|Q-r4|7"
    assert panel_seed_key("A-C", "orders") == "PANEL|A-C|orders"
    assert bot_seed_key("DEMO-dry-1", "R2", "rating", "A-P01.K-a1.r1p3") == (
        "BOT|DEMO-dry-1|R2|rating|A-P01.K-a1.r1p3"
    )
    assert threshold_seed_key("DEMO-T1", "pair", "P1", 3, 7) == "THRESHOLD|DEMO-T1|pair|P1|3|7"


@pytest.mark.parametrize(
    "call",
    [
        lambda: a3_seed_key("A-P01", "K-a5", 1, 1),
        lambda: a3_seed_key("A-P01", "K-a1", 0, 1),
        lambda: a3_seed_key("A-P01", "K-a1", 5, 1),
        lambda: a3_seed_key("A-P01", "K-a1", 1, 4),
        lambda: a3_seed_key("A|P01", "K-a1", 1, 1),
        lambda: a3_seed_key("", "K-a1", 1, 1),
        lambda: a2_seed_key("A-P01", "K-a1", True, 1),
        lambda: b_seed_key("bank-C001", 5, "P1", "K-a1", 1),
        lambda: b_seed_key("bank-C001", 1, "P4", "K-a1", 1),
        lambda: b_seed_key("bank-C001", 1, "P1", "K-a1", 13),
        lambda: join_key(),
        lambda: join_key("C1", "x"),
        lambda: join_key("A3", -1),
        lambda: join_key("A3", "x y"),
        lambda: seed_from_key(""),
    ],
)
def test_bad_keys_raise(call):
    with pytest.raises(SeedKeyError):
        call()


def test_parse_round_trip_and_rejections():
    for key in [*PINNED, "PANEL|A-C|orders", "THRESHOLD|DEMO-T1|pair|P1|3|7"]:
        parsed = parse_seed_key(key)
        assert str(parsed) == key
    assert parse_seed_key("B|bank-C001|1|P2|Q-r4|7").namespace is SeedNamespace.B
    for bad in ["A3|A-P01|K-a1|02|3", "A3|A-P01|K-a1|2", "B|bank-C001|1|P2|Q-r4", "X|y", 3]:
        with pytest.raises(SeedKeyError):
            parse_seed_key(bad)


@given(st.integers(min_value=0, max_value=SEED_MAX))
def test_wire_seed_is_a_signed_int64_bijection(seed):
    wired = wire_seed(seed)
    assert -(2**63) <= wired < 2**63
    assert unwire_seed(wired) == seed
    assert wired.to_bytes(8, "big", signed=True) == seed.to_bytes(8, "big")


def test_wire_seed_rejects_out_of_range():
    for bad in (-1, 2**64, True):
        with pytest.raises(SeedKeyError):
            wire_seed(bad)
    with pytest.raises(SeedKeyError):
        unwire_seed(2**63)


def test_rng_for_is_pcg64_of_the_seed():
    key = a2_seed_key("A-P01", "K-a1", 1, 1)
    expected = np.random.Generator(np.random.PCG64(seed_from_key(key))).integers(0, 1000, 5)
    assert list(rng_for(key).integers(0, 1000, 5)) == list(expected)
    assert {SeedNamespace(n) for n in ("A1", "A2", "A3", "B")} == seeds.SLOT_NAMESPACES
