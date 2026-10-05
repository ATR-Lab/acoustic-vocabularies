"""Fallback banks and fallback books (issue #15; Study A protocol §3.5, §3.7).

Every set built here uses the public seed `DEMO-fallback-v1` (or another `DEMO-` seed,
or a made-up restricted-style seed that is never stored) and lives in memory or in a
temporary directory. The DEMO manifest in `sound/testvectors/fallback/` is a public
example, not study material. The DEMO build is made once per session (about 0.4 s).
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import itertools
import json
import os
import stat
import time
from collections.abc import Iterator
from fractions import Fraction
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from av_sound import (
    BANK_SIZE,
    BookFrozen,
    FallbackBank,
    FallbackError,
    FallbackSet,
    Profile,
    Recipe,
    Reference,
    ScanResult,
    StoreError,
    VocabularyStore,
    build_fallback,
    event_samples,
    fallback_bank_hash,
    file_sha256,
    freeze_fallback_books,
    load_fallback,
    load_reserved_registry,
    read_wav,
    render,
    scan_fallback,
    separated,
    validate,
    verify_fallback,
)
from av_sound._schemas import schema_validator
from av_sound.fallback import (
    BOOK_SIZE,
    DEMO_SEED,
    E_EXHAUSTED,
    E_EXISTS,
    E_MANIFEST,
    E_POLICY,
    E_SEED,
    E_STALE,
    E_VERSION,
    MANIFEST_NAME,
    PROFILE_ORDER,
    build_bank,
    build_book,
    check_seed,
    draw_key,
    draw_recipe,
    fallback_book_id,
    inside_work_tree,
    is_demo_seed,
    seed_fingerprint,
    stream_bytes,
    uniform_index,
    write_fallback,
)
from av_sound.grammar import ATOM_IDS
from av_sound.recipe import AMPLITUDES, GAPS_MS, PITCHES, RHYTHM_WEIGHTS, TOTAL_MS
from av_sound.renderer import MIN_EVENT_SAMPLES
from av_sound.store import E_POLICY as STORE_E_POLICY
from av_sound.synthetic import synthetic_recipes

REPO = Path(__file__).resolve().parents[2]
SOUND = REPO / "sound"
VECTOR = SOUND / "testvectors" / "fallback" / "demo-manifest.json"
TOOL = SOUND / "tools" / "build_fallback.py"
THRESHOLD = Fraction(1, 10)
RESTRICTED_STYLE_SEED = "test-only-not-a-study-seed-0123456789abcdef"  # never stored
REGISTRY = load_reserved_registry()


# --- Fixtures -------------------------------------------------------------------------


@pytest.fixture(scope="session")
def demo_timed() -> tuple[FallbackSet, float]:
    started = time.perf_counter()
    fset = build_fallback(DEMO_SEED)
    return fset, time.perf_counter() - started


@pytest.fixture(scope="session")
def demo(demo_timed: tuple[FallbackSet, float]) -> FallbackSet:
    return demo_timed[0]


def make_writable(path: Path) -> None:
    for p in path.rglob("*"):
        if p.is_file():
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)


@pytest.fixture
def tmp(tmp_path: Path) -> Iterator[Path]:
    """A temporary directory whose read-only outputs are made writable for clean-up."""
    yield tmp_path
    make_writable(tmp_path)


def restricted_variant(fset: FallbackSet) -> FallbackSet:
    """The DEMO set relabelled as a restricted-seed build (no rebuild needed)."""
    return dataclasses.replace(
        fset, demo_seed=None, seed_fingerprint=seed_fingerprint(RESTRICTED_STYLE_SEED)
    )


def near(recipe: Recipe) -> Recipe:
    """The recipe with its first pitch one semitone away: distance 0.024 (< 0.10)."""
    p = list(recipe.pitches)
    p[0] = p[0] + 1 if p[0] < 6 else p[0] - 1
    return dataclasses.replace(recipe, pitches=tuple(p))


def ref(ref_id: str, recipe: Recipe, profile: Profile) -> Reference:
    return Reference(ref_id, recipe, render(recipe, profile).pcm_sha256, profile)


def pair_count(n: int) -> int:
    return n * (n - 1) // 2


# --- Build: banks ---------------------------------------------------------------------


def test_demo_build_counts_and_time(demo_timed):
    fset, seconds = demo_timed
    assert seconds < 30  # about 0.4 s on a laptop; CI runners are slower
    assert [b.profile for b in fset.banks] == list(PROFILE_ORDER)
    assert [k.profile for k in fset.books] == list(PROFILE_ORDER)
    for bank, book in zip(fset.banks, fset.books, strict=True):
        assert len(bank) == BANK_SIZE == 64
        assert len(book) == BOOK_SIZE == 16
        assert [e.index for e in bank] == list(range(64))
        assert len(bank.log) == bank.draws >= 64
        assert len(book.log) == book.draws >= 16
        assert bank.threshold == book.threshold == fset.threshold == THRESHOLD


@pytest.mark.parametrize("profile", PROFILE_ORDER)
def test_bank_is_acceptance_order_and_every_draw_is_logged(demo, profile):
    bank = demo.bank(profile)
    assert [r.draw for r in bank.log] == list(range(bank.draws))
    accepted = [r for r in bank.log if r.position is not None]
    assert [r.position for r in accepted] == list(range(64))
    assert [(r.draw, r.recipe) for r in accepted] == [(e.draw, e.recipe) for e in bank]
    assert all(r.codes for r in bank.log if r.position is None)  # rejected for a reason
    assert all(not r.codes for r in accepted)
    assert all(r.recipe == draw_recipe(DEMO_SEED, profile, "bank", r.draw) for r in bank.log)
    # Every rejection is replayable: the draw fails validate() against the entries kept so far.
    for r in bank.log:
        if r.position is None:
            kept = [e.reference() for e in bank if e.draw < r.draw]
            assert validate(r.recipe, profile, kept).codes == r.codes


@pytest.mark.parametrize("profile", PROFILE_ORDER)
def test_every_bank_recipe_is_admissible_and_the_bank_is_pairwise_separated(demo, profile):
    bank = demo.bank(profile)
    for e in bank:
        result = validate(e.recipe, profile, reserved=None)
        assert result.ok, (e.index, result.codes)
        assert result.pcm_sha256 == e.pcm_sha256
        assert result.rendered is not None and file_sha256(result.rendered) == e.file_sha256
        r = e.recipe
        assert min(event_samples(r.total_ms, r.rhythm_weights, r.gaps_ms)) >= MIN_EVENT_SAMPLES
        assert e.profile is profile
    pairs = list(itertools.combinations(bank.entries, 2))
    assert len(pairs) == pair_count(64) == 2016
    for a, b in pairs:
        assert a.pcm_sha256 != b.pcm_sha256
        assert separated(a.recipe, b.recipe, THRESHOLD), (a.index, b.index)
    # Acceptance-order validation: each entry passes against all earlier entries.
    for e in bank:
        earlier = [x.reference() for x in bank.entries[: e.index]]
        assert validate(e.recipe, profile, earlier).ok


# --- Build: books ---------------------------------------------------------------------


@pytest.mark.parametrize("profile", PROFILE_ORDER)
def test_book_has_16_atoms_in_stored_order_and_passes_all_120_pairs(demo, profile):
    book = demo.book(profile)
    assert [a.atom_id for a in book] == list(ATOM_IDS)
    assert [a.position for a in book] == list(range(16))
    assert list(book.recipes()) == list(ATOM_IDS)
    accepted = [r for r in book.log if r.position is not None]
    assert [(r.draw, r.recipe) for r in accepted] == [(a.draw, a.recipe) for a in book]
    pairs = list(itertools.combinations(book.atoms, 2))
    assert len(pairs) == 120
    for a, b in pairs:
        assert a.pcm_sha256 != b.pcm_sha256
        assert separated(a.recipe, b.recipe, THRESHOLD), (a.atom_id, b.atom_id)
    refs = book.references()
    for i, atom in enumerate(book):
        others = refs[:i] + refs[i + 1 :]
        result = validate(atom.recipe, profile, others, reserved=None)
        assert result.ok, (atom.atom_id, result.codes)
        assert result.pcm_sha256 == atom.pcm_sha256
    assert book.atom("Q-r4") is book.atoms[-1]
    with pytest.raises(KeyError):
        book.atom("Q-r5")


def test_every_fallback_recipe_differs_from_every_reserved_asset(demo):
    # Closes the #14 item "differs from all fallback-bank recipes": no bank or book
    # waveform equals a reserved asset, and none fails with E_RESERVED.
    assert len(REGISTRY.entries) == 7
    reserved_pcm = {e.pcm_sha256 for e in REGISTRY.entries}
    reserved_files = {e.file_sha256 for e in REGISTRY.entries}
    reserved_lengths = {e.n_samples for e in REGISTRY.entries}
    for bank, book in zip(demo.banks, demo.books, strict=True):
        for item in (*bank.entries, *book.atoms):
            assert item.pcm_sha256 not in reserved_pcm
            assert item.file_sha256 not in reserved_files
            assert item.recipe.total_ms * 48 not in reserved_lengths
            result = validate(item.recipe, bank.profile, reserved=REGISTRY)
            assert "E_RESERVED" not in result.codes


@pytest.mark.parametrize("profile", PROFILE_ORDER)
def test_bank_and_book_are_independent_streams(demo, profile):
    # Decision: bank and book are not separated from each other. They never meet in
    # one book (a bank recipe repairs one atom of an assigned book; the fallback book
    # replaces a whole book), and scan_fallback checks every bank recipe against the
    # actual book. Each is rebuilt alone from its own stream.
    assert build_book(DEMO_SEED, profile) == demo.book(profile)
    bank_pcm = {e.pcm_sha256 for e in demo.bank(profile)}
    assert not bank_pcm & {a.pcm_sha256 for a in demo.book(profile)}  # DEMO: no overlap


# --- Rebuild, manifest and vector -----------------------------------------------------


def test_rebuild_from_the_stored_seed_gives_identical_hashes(demo):
    again = build_fallback(DEMO_SEED)
    assert again == demo
    assert again.manifest() == demo.manifest()
    assert again.fallback_bank_hash == demo.fallback_bank_hash
    for a, b in zip((*again.banks, *again.books), (*demo.banks, *demo.books), strict=True):
        assert [r.to_dict() for r in a.log] == [r.to_dict() for r in b.log]
    assert [b.bank_sha256 for b in again.banks] == [b.bank_sha256 for b in demo.banks]
    assert [k.book_sha256 for k in again.books] == [k.book_sha256 for k in demo.books]


def test_demo_manifest_matches_the_committed_vector(demo):
    manifest = demo.manifest()
    text = VECTOR.read_text(encoding="utf-8")
    assert text == json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    data = json.loads(text)
    assert data == manifest
    assert data["seed_kind"] == "demo" and data["demo_seed"] == DEMO_SEED
    assert data["fallback_bank_hash"] == fallback_bank_hash(data) == demo.fallback_bank_hash
    assert not list(schema_validator("fallback-manifest.schema.json").iter_errors(data))
    assert data["threshold"] == "0.1" and data["bank_size"] == 64


def test_fallback_bank_hash_is_sha256_of_the_canonical_manifest(demo):
    manifest = demo.manifest()
    body = {k: v for k, v in manifest.items() if k != "fallback_bank_hash"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    assert manifest["fallback_bank_hash"] == hashlib.sha256(canonical.encode()).hexdigest()
    bank = demo.bank("P2")
    rows = [[e.recipe.sha256(), e.pcm_sha256] for e in bank]
    digest = json.dumps({"entries": rows, "profile": "P2"}, separators=(",", ":"))
    assert bank.bank_sha256 == hashlib.sha256(digest.encode()).hexdigest()


def test_summary_has_digests_and_counts_only(demo):
    summary = demo.summary()
    text = json.dumps(summary)
    assert summary["fallback_bank_hash"] == demo.fallback_bank_hash
    assert "recipe" not in text and "pcm_sha256" not in text
    p1 = summary["profiles"][0]
    assert p1["bank_entries"] == 64 and p1["book_atoms"] == 16
    assert sum(p1["rejection_codes"].values()) == (p1["bank_draws"] - 64) + (p1["book_draws"] - 16)


# --- Stream and seeds -----------------------------------------------------------------


def reference_stream(key: bytes) -> Iterator[int]:
    k = 0
    while True:
        yield from hashlib.sha256(key + k.to_bytes(8, "little")).digest()
        k += 1


def reference_draw(seed: str, profile: str, purpose: str, draw: int) -> Recipe:
    """Independent re-implementation of the documented draw (sound/docs/fallback.md)."""
    stream = reference_stream(f"av-sound/fallback/v1|{seed}|{profile}|{purpose}|{draw}".encode())

    def pick(values):
        n = len(values)
        while True:
            b = next(stream)
            if b < 256 - 256 % n:
                return values[b % n]

    t = pick(TOTAL_MS)
    p = tuple(pick(PITCHES) for _ in range(3))
    w = tuple(pick(RHYTHM_WEIGHTS) for _ in range(3))
    g = tuple(pick(GAPS_MS) for _ in range(2))
    a = tuple(pick(AMPLITUDES) for _ in range(3))
    return Recipe(t, p, w, g, a)


@pytest.mark.parametrize("purpose", ["bank", "book"])
@pytest.mark.parametrize("profile", ["P1", "P2", "P3"])
def test_draws_follow_the_documented_sha256_counter_stream(profile, purpose):
    for draw in (0, 1, 2, 63, 64, 1000):
        assert draw_recipe(DEMO_SEED, profile, purpose, draw) == reference_draw(
            DEMO_SEED, profile, purpose, draw
        )
    assert draw_key(DEMO_SEED, profile, purpose, 7) == (
        f"av-sound/fallback/v1|DEMO-fallback-v1|{profile}|{purpose}|7".encode("ascii")
    )


def test_stream_bytes_and_rejection_sampling():
    key = b"k"
    first = hashlib.sha256(b"k" + bytes(8)).digest()
    second = hashlib.sha256(b"k" + (1).to_bytes(8, "little")).digest()
    assert bytes(itertools.islice(stream_bytes(key), 64)) == first + second
    # n = 13 accepts bytes below 247 only; 250 is skipped, 20 gives 20 % 13 = 7.
    assert uniform_index(iter([250, 247, 20]), 13) == 7
    assert uniform_index(iter([255, 254]), 3) == 254 % 3
    assert uniform_index(iter([255]), 4) == 3  # 256 % 4 == 0: no rejection


def test_draws_are_uniform_over_every_field():
    recipes = [draw_recipe("DEMO-uniformity", "P1", "bank", d) for d in range(3000)]
    columns = {
        "total_ms": ([r.total_ms for r in recipes], TOTAL_MS),
        "pitches": ([p for r in recipes for p in r.pitches], PITCHES),
        "rhythm_weights": ([w for r in recipes for w in r.rhythm_weights], RHYTHM_WEIGHTS),
        "gaps_ms": ([g for r in recipes for g in r.gaps_ms], GAPS_MS),
        "amplitudes": ([a for r in recipes for a in r.amplitudes], AMPLITUDES),
    }
    for name, (values, allowed) in columns.items():
        expected = len(values) / len(allowed)
        counts = {v: values.count(v) for v in allowed}
        assert sum(counts.values()) == len(values), name
        chi2 = sum((c - expected) ** 2 / expected for c in counts.values())
        assert chi2 < 3 * len(allowed), (name, counts)  # deterministic seed: never flaky
        assert all(abs(c - expected) < 0.2 * expected for c in counts.values()), (name, counts)


def test_streams_differ_by_profile_and_purpose():
    first = {
        (p, purpose): draw_recipe(DEMO_SEED, p, purpose, 0)
        for p in ("P1", "P2", "P3")
        for purpose in ("bank", "book")
    }
    assert len(set(first.values())) == 6


def test_seed_rules():
    assert check_seed(DEMO_SEED) == DEMO_SEED and is_demo_seed(DEMO_SEED)
    assert check_seed("DEMO-x") == "DEMO-x"
    assert check_seed(RESTRICTED_STYLE_SEED) == RESTRICTED_STYLE_SEED
    assert not is_demo_seed(RESTRICTED_STYLE_SEED)
    for bad in ("", "has space", "a|b", "x" * 129, "short-restricted-seed", 42):
        with pytest.raises(FallbackError) as err:
            check_seed(bad)  # type: ignore[arg-type]
        assert err.value.code == E_SEED
    fp = seed_fingerprint(DEMO_SEED)
    assert fp == hashlib.sha256(b"av-sound/fallback/seed/v1|DEMO-fallback-v1").hexdigest()
    assert fp != seed_fingerprint("DEMO-fallback-v2")
    with pytest.raises(ValueError, match="purpose"):
        draw_key(DEMO_SEED, "P1", "candidate", 0)
    for bad_draw in (-1, True, 1.0):
        with pytest.raises(ValueError, match="draw"):
            draw_key(DEMO_SEED, "P1", "bank", bad_draw)  # type: ignore[arg-type]
    with pytest.raises(FallbackError):
        build_fallback("too-short")


# --- Thresholds and exhaustion --------------------------------------------------------


@pytest.mark.parametrize("threshold", ["0.15", "0.25"])
def test_other_thresholds_build_separated_banks_and_books(threshold):
    limit = Fraction(threshold)
    bank = build_bank(DEMO_SEED, "P2", threshold=threshold)
    book = build_book(DEMO_SEED, "P2", threshold=threshold)
    assert len(bank) == 64 and len(book) == 16
    assert bank.threshold == book.threshold == limit
    for a, b in itertools.combinations(bank.entries, 2):
        assert separated(a.recipe, b.recipe, limit)
    for a, b in itertools.combinations(book.atoms, 2):
        assert separated(a.recipe, b.recipe, limit)
    if threshold == "0.25":  # the separation screen binds at this threshold
        assert any("E_SEPARATION" in r.codes for r in bank.log)


def test_full_build_at_another_threshold_changes_the_hash(demo):
    other = build_fallback(DEMO_SEED, threshold="0.25")
    manifest = other.manifest()
    assert manifest["threshold"] == "0.25"
    assert other.fallback_bank_hash != demo.fallback_bank_hash
    assert verify_fallback(manifest) == ()
    assert not list(schema_validator("fallback-manifest.schema.json").iter_errors(manifest))
    # A scan with the book threshold records both thresholds.
    result = scan_fallback(other.bank("P1"), (), threshold="0.25")
    assert result.bank_threshold == result.threshold == Fraction(1, 4)


def test_threshold_must_have_a_decimal_form():
    with pytest.raises(ValueError, match="decimal"):
        build_bank(DEMO_SEED, "P1", threshold=Fraction(1, 3))


def test_a_build_that_cannot_reach_its_size_fails():
    with pytest.raises(FallbackError) as err:
        build_bank(DEMO_SEED, "P1", max_draws=10)
    assert err.value.code == E_EXHAUSTED
    with pytest.raises(FallbackError) as err:
        build_book(DEMO_SEED, "P1", threshold="0.9", max_draws=200)
    assert err.value.code == E_EXHAUSTED


# --- scan_fallback --------------------------------------------------------------------


def oracle(bank: FallbackBank, refs, used, threshold):
    """Independent rule: duplicates by waveform hash, separation by `separated()`,
    reserved by waveform hash (no registry entry has a recipe), short events by layout."""
    assert all(e.recipe is None for e in REGISTRY.entries)
    reserved = {e.pcm_sha256 for e in REGISTRY.entries}
    steps = []
    for e in bank:
        if e.index in used:
            steps.append((e.index, "used", ()))
            continue
        codes = []
        r = e.recipe
        if min(event_samples(r.total_ms, r.rhythm_weights, r.gaps_ms)) < MIN_EVENT_SAMPLES:
            codes.append("E_EVENT_SHORT")
        if any(x.pcm_sha256 == e.pcm_sha256 for x in refs):
            codes.append("E_DUPLICATE")
        if e.pcm_sha256 in reserved:
            codes.append("E_RESERVED")
        if not all(separated(r, x.recipe, threshold) for x in refs):
            codes.append("E_SEPARATION")
        if codes:
            steps.append((e.index, "rejected", tuple(codes)))
        else:
            steps.append((e.index, "selected", ()))
            return e.index, steps
    return None, steps


def check_against_oracle(result: ScanResult, bank, refs, used, threshold):
    expected, steps = oracle(bank, refs, set(used), Fraction(threshold))
    assert result.index == expected
    assert [(s.index, s.outcome, s.codes) for s in result.log] == steps
    if expected is None:
        assert result.exhausted and result.selected is None and len(result.log) == len(bank)
        assert result.validation is None
    else:
        assert result.selected is bank[expected] and not result.exhausted
        assert result.validation is not None and result.validation.ok
        assert result.log[-1].outcome == "selected"
    assert all(len(s.messages) == len(s.codes) for s in result.log)
    record = result.to_dict()
    assert not list(schema_validator("fallback-scan.schema.json").iter_errors(record))
    assert record["outcome"] == ("exhausted" if expected is None else "selected")
    assert [s["index"] for s in record["log"]] == [s[0] for s in steps]


def test_scan_of_an_empty_book_selects_index_0(demo):
    bank = demo.bank("P1")
    result = scan_fallback(bank, ())
    assert result.index == 0 and len(result.log) == 1
    assert result.selected is not None and result.selected.source == "fallback-bank-P1-00"
    assert result.reference_ids == () and result.used == ()
    check_against_oracle(result, bank, [], [], THRESHOLD)


def test_scan_skips_used_recipes_and_logs_them(demo):
    bank = demo.bank("P2")
    result = scan_fallback(bank, (), used={2, 0, 1})
    assert result.index == 3 and result.used == (0, 1, 2)
    assert [(s.index, s.outcome) for s in result.log] == [
        (0, "used"),
        (1, "used"),
        (2, "used"),
        (3, "selected"),
    ]
    check_against_oracle(result, bank, [], [0, 1, 2], THRESHOLD)


@pytest.mark.parametrize("k", [1, 5, 16])
@pytest.mark.parametrize("profile", PROFILE_ORDER)
def test_scan_after_near_copies_of_the_first_k_entries(demo, profile, k):
    bank = demo.bank(profile)
    refs = [ref(f"near-{i}", near(bank[i].recipe), profile) for i in range(k)]
    result = scan_fallback(bank, refs)
    check_against_oracle(result, bank, refs, [], THRESHOLD)
    assert result.index is not None and result.index >= k
    assert all("E_SEPARATION" in s.codes for s in result.log[:k])


def test_scan_against_a_book_holding_bank_recipes(demo):
    bank = demo.bank("P3")
    refs = [bank[i].reference() for i in (0, 1, 3)]
    result = scan_fallback(bank, refs, used=[3])
    check_against_oracle(result, bank, refs, [3], THRESHOLD)
    assert result.index == 2
    assert result.log[0].codes == ("E_DUPLICATE", "E_SEPARATION")
    assert result.log[3 - 1].outcome == "selected"


def test_scan_where_no_bank_recipe_passes_returns_none(demo):
    bank = demo.bank("P1")
    # 1. Every recipe already used.
    result = scan_fallback(bank, (), used=range(64))
    assert result.selected is None and result.exhausted
    assert [s.outcome for s in result.log] == ["used"] * 64
    check_against_oracle(result, bank, [], list(range(64)), THRESHOLD)
    # 2. A reference list holding the whole bank: all 64 are duplicates.
    refs = [e.reference() for e in bank]
    result = scan_fallback(bank, refs)
    assert result.selected is None and len(result.log) == 64
    assert all(s.outcome == "rejected" and "E_DUPLICATE" in s.codes for s in result.log)
    check_against_oracle(result, bank, refs, [], THRESHOLD)
    # 3. A complete 16-atom book at threshold 1: no pair of recipes reaches distance 1.
    book = demo.book("P1").references()
    result = scan_fallback(bank, book, threshold="1")
    assert result.selected is None and len(result.log) == 64
    assert all(s.codes == ("E_SEPARATION",) for s in result.log)
    check_against_oracle(result, bank, book, [], 1)
    record = result.to_dict()
    assert record["outcome"] == "exhausted" and record["selected_index"] is None
    assert record["reference_ids"] == list(ATOM_IDS)


def ref_pool() -> list[Reference]:
    """P1 references: bank recipes, near copies, a synthetic book and random draws."""
    fset_bank = build_bank(DEMO_SEED, "P1")
    pool = [e.reference() for e in fset_bank.entries[:24]]
    pool += [ref(f"near-{i}", near(fset_bank[i].recipe), Profile.P1) for i in range(24)]
    pool += [ref(a, r, Profile.P1) for a, r in synthetic_recipes("P1").items()]
    pool += [
        ref(f"rand-{d}", draw_recipe("DEMO-scan-pool", "P1", "book", d), Profile.P1)
        for d in range(24)
    ]
    return pool


POOL: list[Reference] = []


def pool() -> list[Reference]:
    if not POOL:
        POOL.extend(ref_pool())
    return POOL


@settings(max_examples=80, deadline=None)
@given(
    picks=st.lists(st.integers(0, 87), max_size=16),
    used=st.one_of(
        st.sets(st.integers(0, 12)), st.sets(st.integers(0, 63), max_size=48), st.just(set())
    ),
    threshold=st.sampled_from(["0.10", "0.25", "0.5"]),
)
def test_scan_returns_the_lowest_index_unused_passing_recipe(picks, used, threshold):
    bank = build_bank_cached()
    refs = [pool()[i] for i in picks]
    result = scan_fallback(bank, refs, used=used, threshold=threshold)
    check_against_oracle(result, bank, refs, used, threshold)
    assert result.reference_ids == tuple(r.ref_id for r in refs)


BANK_CACHE: list[FallbackBank] = []


def build_bank_cached() -> FallbackBank:
    if not BANK_CACHE:
        BANK_CACHE.append(build_bank(DEMO_SEED, "P1"))
    return BANK_CACHE[0]


def test_scan_is_pure_and_deterministic(demo):
    bank = demo.bank("P2")
    refs = [ref("x", near(bank[0].recipe), Profile.P2), bank[1].reference()]
    used = {4, 2}
    before = (list(refs), set(used))
    first = scan_fallback(bank, refs, used=used)
    second = scan_fallback(bank, refs, used=used)
    assert first == second and first.to_dict() == second.to_dict()
    assert (list(refs), set(used)) == before
    assert bank == demo.bank("P2") and bank.bank_sha256 == demo.bank("P2").bank_sha256
    assert first.index == 3  # 0 near, 1 duplicate, 2 used, 3 passes


def test_scan_accepts_store_entries(demo, tmp):
    store = VocabularyStore(tmp / "store", reserved=())
    store.create_book("DEMO-SCAN", "P1", kind="synthetic")
    recipes = synthetic_recipes("P1")
    labels = {"K-a1": "ADD_ONE", "K-a2": "REMOVE_ONE", "K-r1": "A"}
    for atom_id, label in labels.items():
        store.commit("DEMO-SCAN", atom_id, label, recipes[atom_id], source=f"t-{atom_id}")
    entries = store.list("DEMO-SCAN")
    bank = demo.bank("P1")
    from_store = scan_fallback(bank, entries)
    from_refs = scan_fallback(bank, [e.reference() for e in entries])
    assert from_store == from_refs
    assert from_store.reference_ids == ("K-a1", "K-a2", "K-r1")


def test_scan_argument_errors(demo):
    bank = demo.bank("P1")
    for bad in ([64], [-1], [True], ["3"]):
        with pytest.raises(ValueError, match="used bank index"):
            scan_fallback(bank, (), used=bad)
    with pytest.raises(ValueError, match="profile"):
        scan_fallback(bank, [demo.bank("P2")[0].reference()])
    with pytest.raises(TypeError, match="FallbackBank"):
        scan_fallback(list(bank), ())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="Reference"):
        scan_fallback(bank, [bank[0].recipe])  # type: ignore[list-item]

    class NotAnEntry:
        def reference(self):
            return "K-a1"

    with pytest.raises(TypeError, match="Reference"):
        scan_fallback(bank, [NotAnEntry()])  # type: ignore[list-item]


def test_scan_refuses_a_stale_bank(demo):
    bank = demo.bank("P1")
    wrong = dataclasses.replace(bank[0], pcm_sha256="0" * 64)
    stale = dataclasses.replace(bank, entries=(wrong, *bank.entries[1:]))
    with pytest.raises(FallbackError) as err:
        scan_fallback(stale, ())
    assert err.value.code == E_STALE
    assert scan_fallback(stale, (), used=[0]).index == 1  # a used entry is not re-rendered


# --- Store, files, manifest loading ---------------------------------------------------


def test_freeze_books_into_a_fallback_store_namespace(demo, tmp):
    store = VocabularyStore(tmp / "store")
    frozen = freeze_fallback_books(store, demo)
    assert [f.profile for f in frozen] == list(PROFILE_ORDER)
    for f, book in zip(frozen, demo.books, strict=True):
        assert f.book_id == fallback_book_id(demo, f.profile)
        assert f.book_id == f"FB-{f.profile.value}-{demo.fallback_bank_hash[:12]}"
        info = store.book(f.book_id)
        assert info.kind == "fallback" and info.frozen and info.n_entries == 16
        assert info.threshold == THRESHOLD and info.chain_head == f.chain_head
        assert (
            f.snapshot_sha256 == book.book_sha256 == store.records(f.book_id)[-1]["snapshot_sha256"]
        )
        entries = store.list(f.book_id)
        assert [e.atom_id for e in entries] == list(ATOM_IDS)
        assert all(e.semantic_label is None for e in entries)
        assert [e.source for e in entries] == [a.source for a in book]
        assert [e.pcm_sha256 for e in entries] == [a.pcm_sha256 for a in book]
        assert store.verify(f.book_id, expected_head=f.chain_head).ok
        with pytest.raises(BookFrozen):
            store.commit(f.book_id, "K-a1", None, book.atoms[0].recipe, source="late")
    assert frozen[0].to_dict()["book_id"] == frozen[0].book_id


def test_fallback_store_inside_the_repository_is_refused(demo):
    target = SOUND / "never-created-fallback-store"
    with pytest.raises(StoreError) as err:
        freeze_fallback_books(VocabularyStore(target), demo)
    assert err.value.code == STORE_E_POLICY
    assert not target.exists()


def test_write_fallback_layout_and_hashes(demo, tmp):
    out = tmp / "fallback"
    manifest_path = write_fallback(demo, out)
    assert manifest_path == out / MANIFEST_NAME
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == demo.manifest()
    log = json.loads((out / "fallback-build-log.json").read_text(encoding="utf-8"))
    assert log["fallback_bank_hash"] == demo.fallback_bank_hash
    assert len(log["draws"]) == sum(b.draws for b in demo.banks) + sum(k.draws for k in demo.books)
    for bank, book in zip(demo.banks, demo.books, strict=True):
        p = bank.profile.value
        for e in bank:
            wav = out / p / "bank" / f"{e.index:02d}.wav"
            assert hashlib.sha256(wav.read_bytes()).hexdigest() == e.file_sha256
            assert hashlib.sha256(read_wav(wav)).hexdigest() == e.pcm_sha256
            recipe = Recipe.from_json((out / p / "bank" / f"{e.index:02d}.json").read_bytes())
            assert recipe == e.recipe
        for a in book:
            wav = out / p / "book" / f"{a.atom_id}.wav"
            assert hashlib.sha256(wav.read_bytes()).hexdigest() == a.file_sha256
    files = [f for f in out.rglob("*") if f.is_file()]
    assert len(files) == 2 + 2 * 3 * (64 + 16)
    assert all(not os.access(f, os.W_OK) for f in files) or os.name != "posix"
    with pytest.raises(FallbackError) as err:
        write_fallback(demo, out)
    assert err.value.code == E_EXISTS
    assert verify_fallback(manifest_path) == ()


def test_restricted_outputs_are_refused_inside_a_work_tree(demo, tmp):
    restricted = restricted_variant(demo)
    assert restricted.manifest()["seed_kind"] == "restricted"
    assert restricted.manifest()["demo_seed"] is None
    target = SOUND / "never-created-fallback-output"
    with pytest.raises(FallbackError) as err:
        write_fallback(restricted, target)
    assert err.value.code == E_POLICY
    assert not target.exists()
    other_tree = tmp / "clone"
    (other_tree / ".git").mkdir(parents=True)
    with pytest.raises(FallbackError) as err:
        write_fallback(restricted, other_tree / "out")
    assert err.value.code == E_POLICY
    # A loaded set has no draw logs: no build log is written.
    loaded = load_fallback(restricted.manifest())
    path = write_fallback(loaded, tmp / "restricted")
    assert not (path.parent / "fallback-build-log.json").exists()


def test_inside_work_tree(tmp):
    assert inside_work_tree(REPO)
    assert inside_work_tree(SOUND / "does" / "not" / "exist")
    assert not inside_work_tree(tmp / "anything")


def test_load_round_trip(demo, tmp):
    loaded = load_fallback(demo.manifest())
    assert loaded == demo and loaded.manifest() == demo.manifest()
    assert all(not log for log in loaded.draw_logs())
    assert load_fallback(VECTOR) == demo
    assert verify_fallback(VECTOR) == ()


def rehash(manifest: dict) -> dict:
    manifest["fallback_bank_hash"] = fallback_bank_hash(manifest)
    return manifest


def int_amplitude(manifest: dict) -> None:
    """Write one amplitude 1.0 as the JSON integer 1 (same value, not canonical)."""
    for entry in manifest["profiles"][0]["bank"]["entries"]:
        amplitudes = entry["recipe"]["amplitudes"]
        if 1.0 in amplitudes:
            amplitudes[amplitudes.index(1.0)] = 1
            return
    raise AssertionError("no amplitude 1.0 in the P1 bank")


def test_load_rejects_changed_manifests(demo, tmp):
    def changed(edit) -> dict:
        m = json.loads(json.dumps(demo.manifest()))
        edit(m)
        return m

    cases = {
        "hash": changed(lambda m: m["profiles"][0]["bank"]["entries"][5].update(draw=999)),
        "schema": changed(lambda m: m.pop("stream")),
        "bank digest": rehash(
            changed(lambda m: m["profiles"][1]["bank"]["entries"][0].update(pcm_sha256="0" * 64))
        ),
        "book digest": rehash(
            changed(lambda m: m["profiles"][2]["book"]["atoms"][0].update(pcm_sha256="0" * 64))
        ),
        "recipe hash": rehash(
            changed(lambda m: m["profiles"][0]["book"]["atoms"][3].update(recipe_sha256="1" * 64))
        ),
        "index": rehash(changed(lambda m: m["profiles"][0]["bank"]["entries"][2].update(index=3))),
        "atom order": rehash(
            changed(lambda m: m["profiles"][0]["book"]["atoms"][1].update(atom_id="Q-r4"))
        ),
        "profile order": rehash(changed(lambda m: m["profiles"].reverse())),
        "draws": rehash(changed(lambda m: m["profiles"][0]["bank"].update(draws=10**6))),
        "noncanonical": rehash(changed(int_amplitude)),
    }
    for name, manifest in cases.items():
        with pytest.raises(FallbackError) as err:
            load_fallback(manifest)
        assert err.value.code == E_MANIFEST, name
    stale = rehash(changed(lambda m: m.update(renderer_version="9.9.9")))
    with pytest.raises(FallbackError) as err:
        load_fallback(stale)
    assert err.value.code == E_VERSION
    not_object = tmp / "list.json"
    not_object.write_text("[]\n", encoding="utf-8")
    with pytest.raises(FallbackError) as err:
        load_fallback(not_object)
    assert err.value.code == E_MANIFEST


def test_verify_reports_every_problem(demo):
    assert verify_fallback(demo) == ()
    book = demo.book("P1")
    twin = dataclasses.replace(book.atoms[1], recipe=book.atoms[0].recipe)  # a duplicate
    bad_file = dataclasses.replace(book.atoms[2], file_sha256="0" * 64)
    bad_pcm = dataclasses.replace(demo.bank("P3")[7], pcm_sha256="0" * 64)
    broken = dataclasses.replace(
        demo,
        books=(
            dataclasses.replace(book, atoms=(book.atoms[0], twin, bad_file, *book.atoms[3:15])),
            *demo.books[1:],
        ),
        banks=(
            demo.banks[0],
            dataclasses.replace(demo.banks[1], entries=demo.banks[1].entries[:63]),
            dataclasses.replace(
                demo.banks[2],
                entries=(*demo.banks[2].entries[:7], bad_pcm, *demo.banks[2].entries[8:]),
            ),
        ),
    )
    problems = verify_fallback(broken, reserved=())
    text = "\n".join(problems)
    assert "reserved signals differ" in text
    assert "P1 book has 15 atoms" in text
    assert "P1 book K-a2: re-render gives another waveform" in text
    assert "P1 book K-a2: not admissible" in text and "E_DUPLICATE" in text
    assert "P1 book K-a3: file_sha256 does not match" in text
    assert "P3 bank bank-07: re-render gives another waveform" in text
    assert "P2 bank has 63 entries" in text


def test_write_refuses_a_set_that_no_longer_renders(demo, tmp):
    bank = demo.bank("P1")
    wrong = dataclasses.replace(bank[5], pcm_sha256="0" * 64)
    stale = dataclasses.replace(
        demo,
        banks=(
            dataclasses.replace(bank, entries=(*bank.entries[:5], wrong, *bank.entries[6:])),
            *demo.banks[1:],
        ),
    )
    with pytest.raises(FallbackError) as err:
        write_fallback(stale, tmp / "stale")
    assert err.value.code == E_STALE


# --- Tool -----------------------------------------------------------------------------


def load_tool():
    spec = importlib.util.spec_from_file_location("build_fallback", TOOL)
    assert spec and spec.loader
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


def test_tool_writes_and_checks_the_demo_manifest(demo, tmp, monkeypatch, capsys):
    tool = load_tool()
    monkeypatch.setattr(tool, "build_fallback", lambda seed, threshold=None: demo)
    target = tmp / "demo.json"
    assert tool.main(["--demo-seed", DEMO_SEED, "--manifest", str(target)]) == 0
    assert target.read_bytes() == VECTOR.read_bytes()
    out = capsys.readouterr().out
    assert demo.fallback_bank_hash in out and "public example" in out
    assert tool.main(["--demo-seed", DEMO_SEED, "--check", str(VECTOR)]) == 0
    changed = json.loads(VECTOR.read_text(encoding="utf-8"))
    changed["threshold"] = "0.2"
    target.write_text(json.dumps(changed), encoding="utf-8")
    capsys.readouterr()
    assert tool.main(["--demo-seed", DEMO_SEED, "--check", str(target), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["check"] == {"path": str(target), "identical": False}
    assert report["summary"]["fallback_bank_hash"] == demo.fallback_bank_hash


def test_tool_policies(demo, tmp, monkeypatch, capsys):
    tool = load_tool()
    monkeypatch.setattr(tool, "build_fallback", lambda seed, threshold=None: demo)
    inside = SOUND / "never-created-fallback-out"
    assert tool.main(["--demo-seed", DEMO_SEED, "--out", str(inside)]) == 2
    assert not inside.exists()
    assert tool.main(["--demo-seed", "fallback-v1", "--manifest", str(tmp / "m.json")]) == 2
    assert tool.main(["--seed-file", str(SOUND / "seed.txt"), "--out", str(tmp / "o")]) == 2
    seed_file = tmp / "seed.txt"
    seed_file.write_text(DEMO_SEED + "\n", encoding="utf-8")
    assert tool.main(["--seed-file", str(seed_file), "--out", str(tmp / "o")]) == 2
    seed_file.write_text("too-short\n", encoding="utf-8")
    assert tool.main(["--seed-file", str(seed_file), "--out", str(tmp / "o")]) == 2
    # A restricted manifest is never written inside the repository or overwritten.
    seed_file.write_text(RESTRICTED_STYLE_SEED + "\n", encoding="utf-8")
    restricted = restricted_variant(demo)
    monkeypatch.setattr(tool, "build_fallback", lambda seed, threshold=None: restricted)
    inside_manifest = SOUND / "never-created-fallback-manifest.json"
    assert tool.main(["--seed-file", str(seed_file), "--manifest", str(inside_manifest)]) == 2
    assert not inside_manifest.exists()
    outside = tmp / "restricted.json"
    assert tool.main(["--seed-file", str(seed_file), "--manifest", str(outside)]) == 0
    assert json.loads(outside.read_text(encoding="utf-8"))["demo_seed"] is None
    assert tool.main(["--seed-file", str(seed_file), "--manifest", str(outside)]) == 2
    assert "restricted seed" in capsys.readouterr().out
    assert not (tmp / "o").exists()


def test_tool_end_to_end_with_a_restricted_style_seed(tmp, capsys):
    # A real build from a seed file outside the work tree: manifest, build log, recipes,
    # WAVs and a frozen fallback store, then re-render and store verification.
    tool = load_tool()
    seed_file = tmp / "seed.txt"
    seed_file.write_text(RESTRICTED_STYLE_SEED + "\n", encoding="utf-8")
    out = tmp / "out"
    assert tool.main(["--seed-file", str(seed_file), "--out", str(out), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["summary"]["seed_kind"] == "restricted"
    assert report["out"]["store_verified"] is True and report["out"]["problems"] == []
    manifest = json.loads((out / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["demo_seed"] is None
    assert manifest["seed_fingerprint"] == seed_fingerprint(RESTRICTED_STYLE_SEED)
    assert manifest["fallback_bank_hash"] == report["summary"]["fallback_bank_hash"]
    books = VocabularyStore(out / "store").books()
    assert books == sorted(b["book_id"] for b in report["out"]["store_books"])
    assert len(books) == 3
    assert tool.main(["--seed-file", str(seed_file), "--check", str(out / MANIFEST_NAME)]) == 0
    assert "identical" in capsys.readouterr().out


def test_doc_lists_the_demo_hashes(demo):
    doc = (SOUND / "docs" / "fallback.md").read_text(encoding="utf-8")
    assert f"`fallback_bank_hash` = `{demo.fallback_bank_hash}`" in doc
    for bank, book in zip(demo.banks, demo.books, strict=True):
        row = (
            f"| {bank.profile.value} | {bank.draws} | `{bank.bank_sha256}` | {book.draws} | "
            f"`{book.book_sha256}` |"
        )
        assert row in doc
