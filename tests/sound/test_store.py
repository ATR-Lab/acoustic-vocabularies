"""Vocabulary store tests (issue #11; Study B protocol §4; Common procedures §2, check 2).

Every book here holds synthetic recipes (`av_sound.synthetic`) and lives in a
temporary directory. Book IDs start with `DEMO-` except where a test exercises the
`study` and `fallback` kinds. Blobs and logs are read-only at rest, so the fixtures
make them writable again before pytest removes the directory (needed on Windows).
"""

from __future__ import annotations

import errno
import hashlib
import importlib
import importlib.util
import json
import os
import stat
import sys
import tempfile
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from hypothesis import event, given, settings
from hypothesis import strategies as st

from av_sound import (
    RENDERER_VERSION,
    VALIDATOR_VERSION,
    BookFrozen,
    CommitRejected,
    OverwriteRejected,
    Profile,
    Recipe,
    Reference,
    ReservedEntry,
    StoreEntry,
    StoreError,
    StoreIntegrityError,
    VocabularyStore,
    compose_message,
    composite_hash,
    file_sha256,
    load_reserved_registry,
    message_length,
    persistence_violations,
    render,
    renderer_hash,
    snapshot_digest,
)
from av_sound._schemas import schema_validator
from av_sound.grammar import ATOM_IDS, parse_atom_id
from av_sound.store import (
    E_CONCURRENT,
    E_LABEL,
    E_POLICY,
    E_PROFILE,
    E_RECOVERY,
    E_VERSION,
    E_VOID,
    E_WAVEFORM,
    FROZEN_MARKER,
    GENESIS_SHA256,
    SEMANTIC_LABELS,
    VOID_MARKER,
    BookExists,
    InvalidIdentifier,
    NotFound,
    StoreLocked,
    canonical_json,
    check_book_id,
    record_sha256,
    validator_code_hash,
)
from av_sound.synthetic import synthetic_recipes

store_mod = importlib.import_module("av_sound.store")

REPO = Path(__file__).resolve().parents[2]
SOUND = REPO / "sound"
VECTORS = SOUND / "testvectors" / "store" / "growth.json"
P = Profile.P1
BOOK = "DEMO-T1"
RECIPES = synthetic_recipes(P)
START = datetime(2026, 1, 1, tzinfo=UTC)
WAVES = (
    ("K-a1", "K-a2", "K-r1", "K-r2", "Q-a1", "Q-a2", "Q-r1", "Q-r2"),
    ("K-a3", "K-r3", "Q-a3", "Q-r3"),
    ("K-a4", "K-r4", "Q-a4", "Q-r4"),
)
IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


def label(atom_id: str, shift: int = 0) -> str:
    """Synthetic meaning: matrix index i -> i-th ontology label (shifted for conflicts)."""
    atom = parse_atom_id(atom_id)
    return SEMANTIC_LABELS[(atom.family, atom.role)][(atom.index - 1 + shift) % 4]


def clock() -> Callable[[], datetime]:
    calls = iter(range(10_000))
    return lambda: START + timedelta(seconds=next(calls))


def make_store(root: Path, **kwargs: Any) -> VocabularyStore:
    kwargs.setdefault("clock", clock())
    kwargs.setdefault("reserved", ())
    return VocabularyStore(root, **kwargs)


def make_writable(path: Path) -> None:
    for p in path.rglob("*"):
        if p.is_file():
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)


def write_bytes(path: Path, data: bytes) -> None:
    """Tamper with a store file (read-only at rest)."""
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    path.write_bytes(data)


def commit(store: VocabularyStore, atom_id: str, book: str = BOOK, **kwargs: Any):
    lbl = kwargs.pop("semantic_label", label(atom_id))
    recipe = kwargs.pop("recipe", RECIPES[atom_id])
    return store.commit(book, atom_id, lbl, recipe, source=f"t-{atom_id}", **kwargs)


def lines(store: VocabularyStore, book: str = BOOK) -> list[bytes]:
    data = store.log_path(book).read_bytes()
    assert data.endswith(b"\n")
    return data[:-1].split(b"\n")


def check_log_format(store: VocabularyStore, book: str = BOOK) -> list[dict[str, Any]]:
    """Every line: canonical ASCII JSON + LF, valid against the published schema, chained."""
    validator = schema_validator("store-record.schema.json")
    records = []
    prev = GENESIS_SHA256
    for seq, line in enumerate(lines(store, book)):
        assert b"\r" not in line
        record = json.loads(line.decode("ascii"))
        assert canonical_json(record) == line
        assert list(validator.iter_errors(record)) == []
        assert (record["seq"], record["prev_sha256"]) == (seq, prev)
        assert record["record_sha256"] == record_sha256(record)
        prev = hashlib.sha256(line).hexdigest()
        records.append(record)
    assert prev == store.head(book)
    return records


def is_read_only(path: Path) -> bool:
    return not stat.S_IMODE(path.stat().st_mode) & stat.S_IWUSR


@pytest.fixture
def root(tmp_path: Path) -> Iterator[Path]:
    yield tmp_path / "store"
    make_writable(tmp_path)


@pytest.fixture
def store(root: Path) -> VocabularyStore:
    s = make_store(root)
    s.create_book(BOOK, P, kind="synthetic")
    return s


@pytest.fixture
def grown(store: VocabularyStore) -> VocabularyStore:
    """BOOK with K-a1, K-a2 and K-r1 committed."""
    for atom_id in ("K-a1", "K-a2", "K-r1"):
        commit(store, atom_id)
    return store


# --- Creating books -----------------------------------------------------------------


def test_create_book_writes_one_genesis_record(root: Path):
    s = make_store(root)
    head = s.create_book(BOOK, "P2", kind="synthetic")
    [record] = check_log_format(s)
    assert head == hashlib.sha256(lines(s)[0]).hexdigest() == s.head(BOOK)
    assert record["event"] == "create_book" and record["prev_sha256"] == GENESIS_SHA256
    assert record["timestamp"] == "2026-01-01T00:00:00.000Z"
    info = s.book(BOOK)
    assert (info.profile, info.kind, info.n_entries, info.frozen) == (
        Profile.P2,
        "synthetic",
        0,
        False,
    )
    assert str(info.threshold) == "1/10" and record["threshold"] == "0.1"  # sound/config default
    assert (info.renderer_version, info.validator_version) == (RENDERER_VERSION, VALIDATOR_VERSION)
    assert s.books() == [BOOK] and s.list(BOOK) == [] and s.snapshot_hashes(BOOK) == {}
    assert is_read_only(s.log_path(BOOK))


def test_create_book_twice_or_in_another_case_is_refused(store: VocabularyStore):
    before = store.log_path(BOOK).read_bytes()
    for book_id in (BOOK, BOOK.lower().replace("demo", "DEMO")):
        with pytest.raises(BookExists):
            store.create_book(book_id, P, kind="synthetic")
    assert store.log_path(BOOK).read_bytes() == before


@pytest.mark.parametrize(
    "book_id",
    [
        pytest.param("", id="empty"),
        pytest.param("ab", id="short"),
        pytest.param("x" * 65, id="long"),
        pytest.param("-abc", id="lead-hyphen"),
        pytest.param("abc-", id="trail-hyphen"),
        pytest.param("a/b/c", id="slash"),
        pytest.param("..x", id="dots"),
        pytest.param("bk_1", id="underscore"),
        pytest.param("CON", id="win-con"),
        pytest.param("nul", id="win-nul"),
        pytest.param("lpt1", id="win-lpt1"),
        pytest.param("COM0", id="win-com0"),
        pytest.param("lpt0", id="win-lpt0"),
        pytest.param("BK-A1-07", id="method-a1"),
        pytest.param("bk-a3", id="method-a3"),
        pytest.param("HANDMADE-1", id="method-hand"),
        pytest.param("BK-TRANSFORMER", id="method-transformer"),
        pytest.param("evolution-07", id="method-evolution"),
        pytest.param("BK-LLM-2", id="method-llm"),
        pytest.param(7, id="not-str"),
    ],
)
def test_book_ids_must_be_anonymous_and_portable(root: Path, book_id: Any):
    with pytest.raises(InvalidIdentifier):
        make_store(root).create_book(book_id, P)
    assert not root.exists()


@pytest.mark.parametrize(
    "book_id",
    [
        pytest.param("DEMO-P1", id="demo"),
        pytest.param("bk-7a1f", id="hex-like"),
        pytest.param("B07", id="three"),
        pytest.param("X" * 64, id="max-len"),
    ],
)
def test_acceptable_book_ids(book_id: str):
    assert check_book_id(book_id) == book_id


def test_synthetic_prefix_rule_and_kinds(root: Path):
    s = make_store(root)
    with pytest.raises(StoreError) as err:
        s.create_book("BK-0001", P, kind="synthetic")
    assert err.value.code == E_POLICY
    with pytest.raises(StoreError) as err:
        s.create_book("DEMO-0001", P, kind="study")
    assert err.value.code == E_POLICY
    with pytest.raises(InvalidIdentifier):
        s.create_book("DEMO-0001", P, kind="demo")
    with pytest.raises(InvalidIdentifier):
        s.create_book("DEMO-0001", "P4", kind="synthetic")
    assert s.books() == []


def test_study_books_cannot_live_in_this_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fake_repo = tmp_path / "checkout"  # stands in for the source tree; nothing is written there
    monkeypatch.setattr(store_mod, "_repo_root", lambda: fake_repo.resolve())
    for target in (fake_repo, fake_repo / "sound" / "store"):
        s = make_store(target)
        for kind in ("study", "fallback"):
            with pytest.raises(StoreError) as err:
                s.create_book("BK-0001", P, kind=kind)
            assert err.value.code == E_POLICY
    assert not fake_repo.exists()
    make_store(fake_repo / "fixtures").create_book(BOOK, P, kind="synthetic")  # fixtures may


@pytest.mark.parametrize("marker", ["dir", "file"])
def test_study_books_cannot_live_in_any_git_work_tree(root: Path, marker: str):
    tree = root.parent / "other-checkout"
    tree.mkdir()
    if marker == "dir":
        (tree / ".git").mkdir()
    else:  # git worktrees and submodules have a .git file
        (tree / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8", newline="\n")
    s = make_store(tree / "deep" / "store")
    with pytest.raises(StoreError) as err:
        s.create_book("BK-0001", P)
    assert err.value.code == E_POLICY and "work tree" in str(err.value)
    assert not (tree / "deep").exists()


def test_git_work_tree_check_fails_closed(root: Path, monkeypatch: pytest.MonkeyPatch):
    real_stat = Path.stat

    def guarded(self: Path, *args: Any, **kwargs: Any) -> os.stat_result:
        if self.name == ".git":
            raise PermissionError(errno.EACCES, "denied", str(self))
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", guarded)
    with pytest.raises(StoreError) as err:
        make_store(root).create_book("BK-0001", P)
    assert err.value.code == E_POLICY and "cannot check" in str(err.value)


def test_study_and_fallback_books_outside_the_repository(root: Path):
    s = make_store(root)
    s.create_book("BK-0001", P)
    s.create_book("FB-P1-0001", P, kind="fallback")
    assert s.books() == ["BK-0001", "FB-P1-0001"]
    entry, _ = s.commit("BK-0001", "K-a1", "ADD_ONE", RECIPES["K-a1"], source="slot-1")
    assert entry.semantic_label == "ADD_ONE"
    entry, _ = s.commit("FB-P1-0001", "K-a1", None, RECIPES["K-a1"], source="bank-0")
    assert entry.semantic_label is None
    with pytest.raises(StoreError) as err:
        s.commit("FB-P1-0001", "K-a2", "REMOVE_ONE", RECIPES["K-a2"], source="bank-1")
    assert err.value.code == E_LABEL
    with pytest.raises(StoreError) as err:
        s.commit("BK-0001", "K-a2", None, RECIPES["K-a2"], source="slot-2")
    assert err.value.code == E_LABEL
    with pytest.raises(CommitRejected) as rejected:
        s.commit("BK-0001", "K-a2", "REMOVE_ONE", 42, source="slot-2")
    assert rejected.value.result.codes == ("E_SCHEMA",)
    with pytest.raises(OverwriteRejected) as overwrite:
        s.commit("BK-0001", "K-a1", "ADD_ONE", 42, source="slot-3")
    assert overwrite.value.reasons == ("recipe", "waveform")
    # One blob serves both books (content addressed).
    assert len(list((root / "blobs").glob("*.wav"))) == 1
    assert s.verify("BK-0001").ok and s.verify("FB-P1-0001").ok
    records = [json.loads(line) for line in lines(s, "FB-P1-0001")]
    records[1]["semantic_label"] = "ADD_ONE"  # a meaning in the fallback namespace
    forge(s, records, "FB-P1-0001")
    assert s.verify("FB-P1-0001").codes == ("E_RECORD",)


def test_custom_threshold_is_the_book_threshold(root: Path):
    s = make_store(root)
    s.create_book(BOOK, P, kind="synthetic", threshold="0.5")
    entry, _ = commit(s, "K-a1")
    assert str(entry.threshold) == "1/2"
    commit(s, "K-a2")  # distance 0.564 to K-a1
    commit(s, "K-a3")  # nearest at 0.549
    with pytest.raises(CommitRejected) as err:
        commit(s, "K-a4")  # nearest at 0.456: admissible at 0.10, not at 0.5
    assert err.value.result.codes == ("E_SEPARATION",)
    assert check_log_format(s)[-1]["threshold"] == "0.5"


# --- Committing -------------------------------------------------------------------------


def test_commit_stores_meaning_recipe_profile_waveform_and_hashes(store: VocabularyStore):
    entry, head = commit(store, "Q-r3")
    rendered = render(RECIPES["Q-r3"], P)
    assert isinstance(entry, StoreEntry)
    assert (entry.atom_id, entry.family, entry.role, entry.matrix_index) == (
        "Q-r3",
        "Q",
        "referent",
        3,
    )
    assert (entry.semantic_label, entry.profile, entry.recipe) == ("G", P, RECIPES["Q-r3"])
    assert entry.pcm == rendered.pcm and entry.pcm_sha256 == rendered.pcm_sha256
    assert entry.file_sha256 == file_sha256(rendered) and entry.n_samples == rendered.n_samples
    assert (entry.commit_index, entry.seq, entry.source) == (0, 1, "t-Q-r3")
    assert (entry.renderer_version, entry.validator_version) == (
        RENDERER_VERSION,
        VALIDATOR_VERSION,
    )
    assert head == store.head(BOOK)
    blob = store.blob_path(entry.pcm_sha256)
    assert blob.name == f"{entry.pcm_sha256}.wav" and blob.parent.name == "blobs"
    assert hashlib.sha256(blob.read_bytes()).hexdigest() == entry.file_sha256
    assert is_read_only(blob) and is_read_only(store.log_path(BOOK))
    assert entry.reference() == Reference.from_rendered("Q-r3", rendered)
    record = check_log_format(store)[-1]
    assert record["event"] == "commit" and record["recipe"] == RECIPES["Q-r3"].to_dict()
    assert record["recipe_sha256"] == RECIPES["Q-r3"].sha256()
    assert record["extra_references_sha256"] is None
    assert record["reserved_sha256"] == hashlib.sha256(b"[]").hexdigest()
    assert store.get(BOOK, "Q-r3") == entry


@pytest.mark.parametrize("form", ["recipe", "dict", "json", "bytes"])
def test_commit_accepts_recipe_dict_or_json(store: VocabularyStore, form: str):
    recipe = RECIPES["K-a1"]
    value = {
        "recipe": recipe,
        "dict": recipe.to_dict(),
        "json": recipe.canonical_json(),
        "bytes": recipe.canonical_json().encode(),
    }[form]
    entry, _ = commit(store, "K-a1", recipe=value)
    assert entry.recipe == recipe


def test_entries_compose_like_atom_audio(grown: VocabularyStore):
    action, referent = grown.get(BOOK, "K-a1"), grown.get(BOOK, "K-r1")
    message = compose_message(action, referent)
    assert message.message_id == "K-a1-r1"
    assert message.pcm_sha256 == composite_hash(action, referent)
    assert message.n_samples == message_length(action, referent)
    assert [e.atom_id for e in grown.list(BOOK)] == ["K-a1", "K-a2", "K-r1"]
    assert [e.commit_index for e in grown.list(BOOK)] == [0, 1, 2]


def test_validator_runs_against_the_book_entries(grown: VocabularyStore):
    before = grown.log_path(BOOK).read_bytes()
    with pytest.raises(CommitRejected) as err:
        commit(grown, "Q-a1", recipe=RECIPES["K-a2"], semantic_label="SCAN")  # same waveform
    assert (
        err.value.result.codes == ("E_DUPLICATE", "E_SEPARATION") and err.value.code == "E_REJECTED"
    )
    near = Recipe(
        **{
            **RECIPES["K-r1"].to_dict(),
            "pitches": [p + 1 if p < 6 else p - 1 for p in RECIPES["K-r1"].pitches],
        }
    )
    with pytest.raises(CommitRejected) as err:
        commit(grown, "Q-a1", recipe=near)
    assert err.value.result.codes == ("E_SEPARATION",)
    assert err.value.result.nearest_id == "K-r1"
    with pytest.raises(CommitRejected) as err:
        commit(grown, "Q-a1", recipe="{not json")
    assert err.value.result.codes == ("E_JSON",)
    with pytest.raises(CommitRejected) as err:
        commit(grown, "Q-a1", recipe={"total_ms": 600})
    assert err.value.result.codes == ("E_SCHEMA",)
    assert grown.log_path(BOOK).read_bytes() == before  # rejected candidates are not logged


def test_reserved_signals_and_default_registry(root: Path):
    rendered = render(RECIPES["K-a1"], P)
    entry = ReservedEntry(
        id="demo-ready",
        kind="ready_cue",
        profile=P,
        n_samples=rendered.n_samples,
        pcm_sha256=rendered.pcm_sha256,
        file_sha256=file_sha256(rendered),
        recipe=None,
        description="synthetic test entry",
    )
    s = make_store(root, reserved=[entry])
    s.create_book(BOOK, P, kind="synthetic")
    with pytest.raises(CommitRejected) as err:
        commit(s, "K-a1")
    assert err.value.result.codes == ("E_RESERVED",)
    default = VocabularyStore(root, clock=clock())  # reserved=None: sound/reserved/registry.json
    commit(default, "K-a2")
    registry = load_reserved_registry()
    expected = hashlib.sha256(canonical_json([e.to_dict() for e in registry.entries])).hexdigest()
    assert check_log_format(default)[-1]["reserved_sha256"] == expected


def test_extra_references_are_checked_and_recorded(store: VocabularyStore):
    other = Reference.from_rendered("opt-K-a2-1", render(RECIPES["K-a2"], P))
    with pytest.raises(CommitRejected) as err:
        commit(store, "K-a1", recipe=RECIPES["K-a2"], references=[other])
    assert err.value.result.codes == ("E_DUPLICATE", "E_SEPARATION")
    entry, _ = commit(store, "K-a1", references=[other])
    expected = hashlib.sha256(
        canonical_json([["opt-K-a2-1", RECIPES["K-a2"].sha256(), other.pcm_sha256]])
    ).hexdigest()
    assert check_log_format(store)[-1]["extra_references_sha256"] == expected
    wrong_profile = Reference.from_rendered("opt-x", render(RECIPES["K-a3"], Profile.P2))
    with pytest.raises(ValueError):
        commit(store, "K-r1", references=[wrong_profile])


def test_semantic_labels_follow_the_ontology(grown: VocabularyStore):
    before = grown.log_path(BOOK).read_bytes()
    for atom_id, lbl in (("K-a3", "SCAN"), ("K-a3", "A"), ("K-a3", "ADD_ONE"), ("K-a3", None)):
        with pytest.raises(StoreError) as err:
            commit(grown, atom_id, semantic_label=lbl)
        assert err.value.code == E_LABEL
    for bad in ("add_one", "ADD ONE", "", "X" * 40):
        with pytest.raises(InvalidIdentifier):
            commit(grown, "K-a3", semantic_label=bad)
    assert grown.log_path(BOOK).read_bytes() == before


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        pytest.param({"profile": "P2"}, E_PROFILE, id="profile"),
        pytest.param({"pcm_sha256": "0" * 64}, E_WAVEFORM, id="waveform"),
    ],
)
def test_assertions_on_a_new_atom(store: VocabularyStore, kwargs: dict[str, Any], code: str):
    before = store.log_path(BOOK).read_bytes()
    with pytest.raises(StoreError) as err:
        commit(store, "K-a1", **kwargs)
    assert err.value.code == code
    assert store.log_path(BOOK).read_bytes() == before
    entry, _ = commit(store, "K-a1", profile="P1", pcm_sha256=render(RECIPES["K-a1"], P).pcm_sha256)
    assert entry.profile is P


@pytest.mark.parametrize(
    ("atom_id", "source", "pcm"),
    [
        pytest.param("K-x1", "s", None, id="atom"),
        pytest.param("K-a1", "has space", None, id="source-space"),
        pytest.param("K-a1", "", None, id="source-empty"),
        pytest.param("K-a1", "s", "ABC", id="pcm-hash"),
    ],
)
def test_malformed_arguments(store: VocabularyStore, atom_id: str, source: str, pcm: str | None):
    with pytest.raises(InvalidIdentifier):
        store.commit(BOOK, atom_id, "ADD_ONE", RECIPES["K-a1"], source=source, pcm_sha256=pcm)


def test_version_change_blocks_new_commits(grown: VocabularyStore, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(store_mod, "RENDERER_VERSION", "9.9.9")
    with pytest.raises(StoreError) as err:
        commit(grown, "K-a3")
    assert err.value.code == E_VERSION
    monkeypatch.setattr(store_mod, "RENDERER_VERSION", RENDERER_VERSION)
    monkeypatch.setattr(store_mod, "VALIDATOR_VERSION", "9.9.9")
    with pytest.raises(StoreError) as err:
        commit(grown, "K-a3")
    assert err.value.code == E_VERSION


def test_lookups(grown: VocabularyStore, root: Path):
    with pytest.raises(NotFound):
        grown.get(BOOK, "Q-r4")
    with pytest.raises(NotFound):
        grown.get("DEMO-NONE", "K-a1")
    with pytest.raises(NotFound):
        commit(grown, "K-a1", book="DEMO-NONE")
    with pytest.raises(InvalidIdentifier):
        grown.get(BOOK, "nope")
    assert make_store(root / "empty").books() == []
    assert grown.root == root
    records = grown.records(BOOK)
    assert [r["event"] for r in records] == ["create_book", "commit", "commit", "commit"]
    assert grown.snapshot(BOOK)["K-a2"] == {
        "recipe_sha256": RECIPES["K-a2"].sha256(),
        "pcm_sha256": render(RECIPES["K-a2"], P).pcm_sha256,
        "profile": "P1",
        "semantic_label": "REMOVE_ONE",
    }


def test_clock_must_be_timezone_aware(root: Path):
    s = VocabularyStore(root, clock=lambda: datetime(2026, 1, 1), reserved=())
    with pytest.raises(ValueError):
        s.create_book(BOOK, P, kind="synthetic")
    assert s.books() == []
    offset = timezone(timedelta(hours=-5))
    s = VocabularyStore(root, clock=lambda: datetime(2026, 1, 1, 7, 0, 0, 123456, offset))
    s.create_book(BOOK, P, kind="synthetic")
    assert s.records(BOOK)[0]["timestamp"] == "2026-01-01T12:00:00.123Z"
    s = VocabularyStore(root)  # default clock: UTC now
    commit(s, "K-a1")
    assert s.records(BOOK)[-1]["timestamp"].endswith("Z")


# --- Overwrites, no-ops and freezing ----------------------------------------------------


OVERWRITES = [
    pytest.param({"recipe": RECIPES["K-a2"]}, ("recipe", "waveform"), id="recipe"),
    pytest.param(
        {"pcm_sha256": render(RECIPES["K-a3"], P).pcm_sha256}, ("waveform",), id="waveform"
    ),
    pytest.param({"profile": "P2"}, ("profile", "waveform"), id="profile"),
    pytest.param(
        {"profile": "P3", "pcm_sha256": render(RECIPES["K-r1"], P).pcm_sha256},
        ("profile",),
        id="profile-same-hash",
    ),
    pytest.param({"semantic_label": "FLIP_CARD"}, ("semantic_label",), id="label"),
    pytest.param({"semantic_label": "Q"}, ("semantic_label",), id="label-off-ontology"),
    pytest.param({"recipe": "{broken"}, ("recipe", "waveform"), id="unparseable"),
    pytest.param(
        {"recipe": RECIPES["K-a2"], "profile": "P2", "semantic_label": "B"},
        ("profile", "recipe", "semantic_label", "waveform"),
        id="everything",
    ),
]


@pytest.mark.parametrize(("kwargs", "reasons"), OVERWRITES)
def test_overwrite_attempts_are_rejected_logged_and_change_nothing(
    grown: VocabularyStore, kwargs: dict[str, Any], reasons: tuple[str, ...]
):
    target = "K-r1"
    entry = grown.get(BOOK, target)
    blob = grown.blob_path(entry.pcm_sha256).read_bytes()
    snapshot = grown.snapshot(BOOK)
    n_before = len(lines(grown))
    with pytest.raises(OverwriteRejected) as err:
        commit(grown, target, **kwargs)
    assert err.value.reasons == reasons and err.value.code == "E_OVERWRITE"
    records = check_log_format(grown)
    assert len(records) == n_before + 1
    logged = records[-1]
    assert logged == err.value.record and err.value.chain_head == grown.head(BOOK)
    assert logged["event"] == "overwrite_rejected" and tuple(logged["reasons"]) == reasons
    assert logged["atom_id"] == target and logged["original_seq"] == entry.seq
    assert grown.get(BOOK, target) == entry
    assert grown.blob_path(entry.pcm_sha256).read_bytes() == blob
    assert grown.snapshot(BOOK) == snapshot
    assert grown.verify(BOOK).ok


def test_identical_recommit_is_a_logged_no_op(grown: VocabularyStore):
    entry = grown.get(BOOK, "K-a2")
    head = grown.head(BOOK)
    again, new_head = grown.commit(
        BOOK,
        "K-a2",
        "REMOVE_ONE",
        RECIPES["K-a2"].to_dict(),
        source="retry-after-resume",
        profile="P1",
        pcm_sha256=entry.pcm_sha256,
    )
    assert again == entry and new_head != head
    record = check_log_format(grown)[-1]
    assert record["event"] == "recommit_noop" and record["original_seq"] == entry.seq
    assert record["source"] == "retry-after-resume" and record["pcm_sha256"] == entry.pcm_sha256
    assert [e.atom_id for e in grown.list(BOOK)] == ["K-a1", "K-a2", "K-r1"]
    assert grown.verify(BOOK).ok


def test_frozen_book_rejects_commits_and_logs_them(grown: VocabularyStore):
    snapshot = grown.snapshot_hashes(BOOK)
    head = grown.freeze(BOOK)
    assert grown.freeze(BOOK) == head == grown.head(BOOK)  # idempotent, nothing appended
    freeze = check_log_format(grown)[-1]
    assert freeze["event"] == "freeze" and freeze["n_entries"] == 3
    assert freeze["snapshot_sha256"] == snapshot_digest(snapshot)
    assert grown.book(BOOK).frozen
    for atom_id, recipe in (("K-a3", RECIPES["K-a3"]), ("K-a1", RECIPES["K-a1"]), ("K-a1", "{")):
        with pytest.raises(BookFrozen) as err:
            commit(grown, atom_id, recipe=recipe)
        assert err.value.code == "E_FROZEN"
        record = check_log_format(grown)[-1]
        assert record == err.value.record and record["event"] == "commit_rejected_frozen"
        assert record["freeze_seq"] == freeze["seq"] and record["atom_id"] == atom_id
    assert record["attempted_recipe_sha256"] is None
    assert grown.snapshot_hashes(BOOK) == snapshot
    assert grown.verify(BOOK, expected_head=head).ok


# --- Study B growth 8 -> 12 -> 16 ----------------------------------------------------------


@pytest.mark.parametrize("profile", list(Profile), ids=[p.value for p in Profile])
def test_growth_keeps_old_entries_identical(root: Path, profile: Profile):
    s = make_store(root)
    book = f"DEMO-G{profile.value}"
    s.create_book(book, profile, kind="synthetic")
    recipes = synthetic_recipes(profile)
    snapshots: list[dict[str, Any]] = []
    heads = []
    for atoms in WAVES:
        before_hashes = s.snapshot_hashes(book)
        before_full = s.snapshot(book)
        before_blobs = {a: s.blob_path(h).read_bytes() for a, h in before_hashes.items()}
        for atom_id in atoms:
            s.commit(book, atom_id, label(atom_id), recipes[atom_id], source=f"opt-{atom_id}")
        after_hashes = s.snapshot_hashes(book)
        assert persistence_violations(before_hashes, after_hashes) == ()
        assert persistence_violations(before_full, s.snapshot(book)) == ()
        assert {a: after_hashes[a] for a in before_hashes} == before_hashes
        assert all(s.blob_path(h).read_bytes() == before_blobs[a] for a, h in before_hashes.items())
        snapshots.append(after_hashes)
        heads.append(s.head(book))
    assert [len(x) for x in snapshots] == [8, 12, 16]
    assert sum(snapshots[1][a] == h for a, h in snapshots[0].items()) == 8
    assert sum(snapshots[2][a] == h for a, h in snapshots[1].items()) == 12
    # Old entries are locked after both growth steps; an overwrite is rejected and recorded.
    with pytest.raises(OverwriteRejected):
        s.commit(book, "K-a1", label("K-a1"), recipes["K-a4"], source="late-change")
    assert s.snapshot_hashes(book) == snapshots[2]
    for i, head in enumerate(heads):
        report = s.verify(book, expected_head=head)
        assert report.ok and report.anchored_seq == [8, 12, 16][i]
    check_log_format(s, book)


def test_persistence_violations():
    assert persistence_violations({"a": 1}, {"a": 1, "b": 2}) == ()
    assert persistence_violations({"a": 1, "b": 2}, {"a": 3}) == ("a: changed", "b: missing")


def test_growth_vectors_reproduce_on_this_platform():
    spec = importlib.util.spec_from_file_location(
        "store_growth_demo", SOUND / "tools" / "store_growth_demo.py"
    )
    assert spec and spec.loader
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    fresh = tool.build()
    assert tool.serialize(fresh) == VECTORS.read_text(encoding="utf-8")
    for book in fresh["books"]:
        assert [w["n_entries"] for w in book["waves"]] == [8, 12, 16]
        assert [w["old_unchanged"] for w in book["waves"]] == [0, 8, 12]
        assert all(a["rejected"] for a in book["overwrite_attempts"])
        assert book["entries_unchanged_after_attempts"] and book["frozen_rejected"]
        assert book["verify_ok"]
    report = tool.markdown(fresh)
    assert "**8/8**" in report and "**12/12**" in report and "**NO**" not in report
    # The example line in sound/docs/store.md is line 0 of DEMO-P1 in these vectors.
    doc = (SOUND / "docs" / "store.md").read_text(encoding="utf-8")
    assert tool.document(fresh, doc) == doc
    [example] = [x for x in doc.splitlines() if x.startswith('{"book_id":"DEMO-P1"')]
    assert hashlib.sha256(example.encode("ascii")).hexdigest() == fresh["books"][0]["create_head"]
    assert f"`{fresh['books'][0]['create_head']}` (`create_head` in" in doc


# --- Tampering ---------------------------------------------------------------------------


@pytest.fixture
def sealed(grown: VocabularyStore) -> VocabularyStore:
    """BOOK with three commits, a no-op, an overwrite attempt, a freeze and a frozen reject."""
    commit(grown, "K-a1")
    with pytest.raises(OverwriteRejected):
        commit(grown, "K-a2", recipe=RECIPES["K-a3"])
    grown.freeze(BOOK)
    with pytest.raises(BookFrozen):
        commit(grown, "K-a3")
    assert grown.verify(BOOK).ok
    return grown


def flip(path: Path, index: int, mask: int = 0x01) -> bytes:
    data = bytearray(path.read_bytes())
    original = bytes(data)
    data[index] ^= mask
    write_bytes(path, bytes(data))
    return original


def test_every_byte_of_the_last_log_line_is_covered(sealed: VocabularyStore):
    path = sealed.log_path(BOOK)
    data = path.read_bytes()
    start = data.rindex(b"\n", 0, len(data) - 1) + 1
    for index in range(start, len(data)):
        original = flip(path, index)
        report = sealed.verify(BOOK, rerender=False)
        assert not report.ok, f"byte {index} of the last line"
        write_bytes(path, original)
    assert sealed.verify(BOOK).ok


def test_one_byte_changes_anywhere_in_the_log_fail_verify(sealed: VocabularyStore):
    path = sealed.log_path(BOOK)
    size = len(path.read_bytes())
    for n, index in enumerate([*range(0, size, 5), size - 1]):
        mask = (0x01, 0x20, 0x7F)[n % 3]
        original = flip(path, index, mask)
        assert not sealed.verify(BOOK, rerender=False).ok, f"byte {index} ^ {mask:#x}"
        write_bytes(path, original)
    assert sealed.verify(BOOK).ok


@settings(max_examples=60, deadline=None)
@given(data=st.data())
def test_random_byte_changes_fail_verify(data: st.DataObject):
    with tempfile.TemporaryDirectory() as tmp:
        try:
            s = make_store(Path(tmp))
            s.create_book(BOOK, P, kind="synthetic")
            entry, _ = commit(s, "K-a1")
            commit(s, "Q-r2")
            target = data.draw(st.sampled_from(["log", "blob"]))
            path = s.log_path(BOOK) if target == "log" else s.blob_path(entry.pcm_sha256)
            size = len(path.read_bytes())
            index = data.draw(st.integers(0, size - 1))
            mask = data.draw(st.integers(1, 255))
            flip(path, index, mask)
            assert not s.verify(BOOK, rerender=False).ok
        finally:
            make_writable(Path(tmp))


def test_one_byte_changes_in_a_blob_fail_verify_and_block_reads(sealed: VocabularyStore):
    entry = sealed.get(BOOK, "K-a2")
    blob = sealed.blob_path(entry.pcm_sha256)
    size = len(blob.read_bytes())
    positions = list(range(44)) + list(range(44, size, 997)) + [size - 1]
    for index in positions:
        original = flip(blob, index)
        report = sealed.verify(BOOK, rerender=False)
        assert not report.ok and set(report.codes) <= {"E_BLOB_FORMAT", "E_BLOB_HASH"}
        assert {i.atom_id for i in report.issues} == {"K-a2"}
        write_bytes(blob, original)
    flip(blob, 1000)
    with pytest.raises(StoreIntegrityError) as err:
        sealed.get(BOOK, "K-a1")
    assert err.value.issues[0].code == "E_BLOB_HASH"
    with pytest.raises(StoreIntegrityError):
        sealed.list(BOOK)


def test_missing_or_swapped_blob(grown: VocabularyStore):
    a1, a2 = grown.get(BOOK, "K-a1"), grown.get(BOOK, "K-a2")
    path = grown.blob_path(a1.pcm_sha256)
    original = path.read_bytes()
    write_bytes(path, grown.blob_path(a2.pcm_sha256).read_bytes())  # a valid WAV, wrong name
    assert grown.verify(BOOK).codes == ("E_BLOB_HASH",)
    write_bytes(path, original)
    os.chmod(path, stat.S_IWRITE)
    path.unlink()
    report = grown.verify(BOOK)
    assert report.codes == ("E_BLOB_MISSING",) and report.issues[0].atom_id == "K-a1"
    with pytest.raises(StoreIntegrityError):
        commit(grown, "K-a3")


def test_truncation_and_deleted_lines(sealed: VocabularyStore):
    path = sealed.log_path(BOOK)
    data = path.read_bytes()
    head = sealed.head(BOOK)
    log = data[:-1].split(b"\n")
    cases = {
        "torn-tail": data[:-10],
        "no-final-lf": data[:-1],
        "middle-line-deleted": b"\n".join(log[:2] + log[3:]) + b"\n",
        "first-line-deleted": b"\n".join(log[1:]) + b"\n",
        "lines-swapped": b"\n".join(log[:2] + [log[3], log[2]] + log[4:]) + b"\n",
        "line-duplicated": data + log[-1] + b"\n",
        "empty-line": data + b"\n",
        "crlf": data.replace(b"\n", b"\r\n"),
        "empty-log": b"",
    }
    for name, tampered in cases.items():
        write_bytes(path, tampered)
        report = sealed.verify(BOOK, rerender=False)
        assert not report.ok, name
        with pytest.raises(StoreIntegrityError):
            sealed.snapshot_hashes(BOOK)
    # Whole lines removed from the end leave a valid prefix: only an anchor detects it ...
    write_bytes(path, b"\n".join(log[:-1]) + b"\n")
    assert sealed.verify(BOOK).ok
    report = sealed.verify(BOOK, expected_head=head)
    assert report.codes == ("E_ANCHOR",) and report.anchored_seq is None
    for read in (
        lambda: sealed.get(BOOK, "K-a1", expected_head=head),
        lambda: sealed.list(BOOK, expected_head=head),
        lambda: sealed.snapshot(BOOK, expected_head=head),
        lambda: sealed.snapshot_hashes(BOOK, expected_head=head),
        lambda: sealed.records(BOOK, expected_head=head),
        lambda: sealed.book(BOOK, expected_head=head),
        lambda: commit(sealed, "K-a3", expected_head=head),
        lambda: sealed.freeze(BOOK, expected_head=head),
    ):
        with pytest.raises(StoreIntegrityError) as err:
            read()
        assert [i.code for i in err.value.issues] == ["E_ANCHOR"]
    with pytest.raises(InvalidIdentifier):
        sealed.verify(BOOK, expected_head="nope")
    with pytest.raises(InvalidIdentifier):
        sealed.get(BOOK, "K-a1", expected_head="nope")
    # ... unless the removed lines include the freeze record: the FROZEN marker names it.
    write_bytes(path, b"\n".join(log[:-2]) + b"\n")
    assert sealed.verify(BOOK).codes == ("E_MARKER",)
    with pytest.raises(StoreIntegrityError):
        commit(sealed, "K-a3")
    os.chmod(path, stat.S_IWRITE)
    path.unlink()
    report = sealed.verify(BOOK)
    assert report.codes == ("E_LOG_MISSING",) and report.chain_head is None
    with pytest.raises(NotFound):
        sealed.get(BOOK, "K-a1")


def test_recover_torn_tail_removes_only_the_incomplete_line(grown: VocabularyStore):
    path = grown.log_path(BOOK)
    data = path.read_bytes()
    torn = b'{"atom_id":"K-a3","book_'  # an interrupted append (for example a power cut)
    write_bytes(path, data + torn)
    report = grown.verify(BOOK)
    assert report.codes == ("E_LOG_TORN",) and report.chain_head == grown_head(data)
    with pytest.raises(StoreIntegrityError):
        commit(grown, "K-a3")
    with pytest.raises(InvalidIdentifier):
        grown.recover_torn_tail(BOOK, reason=" padded ")
    head = grown.recover_torn_tail(BOOK, reason="DEV-0001 power cut during append")
    assert path.read_bytes().startswith(data) and is_read_only(path)
    record = check_log_format(grown)[-1]
    assert record["event"] == "deviation" and record["deviation"] == "torn_tail_removed"
    assert record["removed_bytes"] == len(torn)
    assert record["removed_sha256"] == hashlib.sha256(torn).hexdigest()
    assert record["reason"] == "DEV-0001 power cut during append" and head == grown.head(BOOK)
    assert grown.verify(BOOK, expected_head=grown_head(data)).ok
    commit(grown, "K-a3")
    with pytest.raises(StoreError) as err:
        grown.recover_torn_tail(BOOK, reason="DEV-0002")
    assert err.value.code == E_RECOVERY


def test_recover_torn_tail_refuses_other_damage(grown: VocabularyStore):
    path = grown.log_path(BOOK)
    data = bytearray(path.read_bytes())
    data[30] ^= 0x01  # damage inside line 0
    write_bytes(path, bytes(data) + b'{"partial')
    with pytest.raises(StoreIntegrityError) as err:
        grown.recover_torn_tail(BOOK, reason="DEV-0003")
    assert "E_LOG_TORN" not in {i.code for i in err.value.issues}
    write_bytes(path, b'{"only a torn line')
    with pytest.raises(StoreError) as err2:
        grown.recover_torn_tail(BOOK, reason="DEV-0004")
    assert err2.value.code == E_RECOVERY


def grown_head(data: bytes) -> str:
    return hashlib.sha256(data[:-1].split(b"\n")[-1]).hexdigest()


def forge(
    store: VocabularyStore,
    records: list[dict[str, Any]],
    book: str = BOOK,
    seqs: list[int] | None = None,
) -> None:
    """Rewrite the log from records with consistent hashes and chain, and rewrite the
    FROZEN and VOID markers to match (a forger with full write access). `seqs` overrides
    the line numbers written into the records."""
    head = GENESIS_SHA256
    out = []
    hashes = []
    for i, rec in enumerate(records):
        rec = {k: v for k, v in rec.items() if k != "record_sha256"}
        rec.update(seq=i if seqs is None else seqs[i], prev_sha256=head)
        rec["record_sha256"] = record_sha256(rec)
        line = canonical_json(rec)
        out.append(line)
        head = hashlib.sha256(line).hexdigest()
        hashes.append(head)
    write_bytes(store.log_path(book), b"\n".join(out) + b"\n")
    events = [r.get("event") for r in records]
    closing = next((i for i, e in enumerate(events) if e in ("freeze", "void")), None)
    void = next((i for i, e in enumerate(events) if e == "void"), None)
    for name, seq in ((FROZEN_MARKER, closing), (VOID_MARKER, void)):
        path = store.book_dir(book) / name
        if path.exists():
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            path.unlink()
        if seq is not None:
            path.write_bytes(store_mod._marker_bytes(book, events[seq], seq, hashes[seq]))


def _swap_recipe(records: list[dict[str, Any]]) -> None:
    # Q-r1 has the same total_ms as K-a1 (450 ms), so only re-rendering can tell.
    records[1]["recipe"] = RECIPES["Q-r1"].to_dict()
    records[1]["recipe_sha256"] = RECIPES["Q-r1"].sha256()


Mutation = Callable[[list[dict[str, Any]]], Any]
Expected = list[tuple[str, int | None, str]]
"""Every issue `verify` must report: (code, seq, message fragment)."""

FORGERIES: dict[str, tuple[Mutation, Expected]] = {
    "commit-after-freeze": (
        lambda r: r.append(r.pop(3)),
        [
            ("E_RECORD", 5, "n_entries 3 is not 2"),
            ("E_RECORD", 5, "snapshot_sha256"),
            ("E_RECORD", 6, "freeze_seq"),  # the frozen reject moved up one line
            ("E_EVENT", 7, "commit after freeze"),
        ],
    ),
    "second-create": (lambda r: r.append(dict(r[0])), [("E_EVENT", 8, "create_book after")]),
    "missing-create": (
        lambda r: r.pop(0),
        [("E_EVENT", 0, "line 0 must be")]
        + [("E_EVENT", i, "before create_book") for i in range(7)]
        + [("E_MARKER", None, "FROZEN marker exists")],
    ),
    "second-commit-same-atom": (
        lambda r: r.insert(4, {**r[1], "commit_index": 3}),
        [("E_EVENT", 4, "second commit record"), ("E_RECORD", 8, "freeze_seq")],
    ),
    "noop-unknown-atom": (
        lambda r: r[4].update(atom_id="Q-r4"),
        [("E_EVENT", 4, "not committed")],
    ),
    "noop-mismatch": (
        lambda r: r[4].update(pcm_sha256="1" * 64),
        [("E_RECORD", 4, "different pcm_sha256")],
    ),
    "noop-original-seq": (
        lambda r: r[4].update(original_seq=2),
        [("E_RECORD", 4, "original_seq")],
    ),
    "overwrite-reasons": (
        lambda r: r[5].update(reasons=["profile"]),
        [("E_RECORD", 5, "should be ['recipe', 'waveform']")],
    ),
    "overwrite-sha": (
        lambda r: r[5].update(attempted_recipe_sha256="2" * 64),
        [("E_RECORD", 5, "attempted_recipe_sha256")],
    ),
    "overwrite-after-freeze": (
        lambda r: r.append(dict(r[5])),
        [("E_EVENT", 8, "overwrite_rejected after freeze")],
    ),
    "frozen-reject-unfrozen": (
        lambda r: r.insert(4, dict(r[7])),
        [("E_EVENT", 4, "not frozen"), ("E_RECORD", 8, "freeze_seq")],
    ),
    "frozen-reject-seq": (
        lambda r: r[7].update(freeze_seq=1),
        [("E_RECORD", 7, "freeze_seq")],
    ),
    "second-freeze": (lambda r: r.append(dict(r[6])), [("E_EVENT", 8, "freeze after freeze")]),
    "freeze-count": (lambda r: r[6].update(n_entries=2), [("E_RECORD", 6, "n_entries 2")]),
    "freeze-snapshot": (
        lambda r: r[6].update(snapshot_sha256="3" * 64),
        [("E_RECORD", 6, "snapshot_sha256")],
    ),
    "commit-index": (
        lambda r: r[2].update(commit_index=5),
        [("E_RECORD", 2, "commit_index 5 is not 1")],
    ),
    "atom-fields": (
        lambda r: r[2].update(matrix_index=4),
        [("E_RECORD", 2, "matrix_index does not match")],
    ),
    "label-off-family": (
        lambda r: r[2].update(semantic_label="TAG"),
        [("E_RECORD", 2, "TAG is not a K action label"), ("E_RECORD", 5, "should be")],
    ),
    "label-clash": (
        lambda r: r[2].update(semantic_label="ADD_ONE"),
        [("E_RECORD", 2, "ADD_ONE is already bound to K-a1"), ("E_RECORD", 5, "should be")],
    ),
    "label-null": (
        lambda r: r[2].update(semantic_label=None),
        [("E_RECORD", 2, "None is not a K action label"), ("E_RECORD", 5, "should be")],
    ),
    "profile": (
        lambda r: r[3].update(profile="P2"),
        [("E_RECORD", 3, "profile P2 differs from the book's P1")],
    ),
    "threshold": (
        lambda r: r[3].update(threshold="0.2"),
        [("E_RECORD", 3, "threshold differs")],
    ),
    "renderer-version": (
        lambda r: r[3].update(renderer_version="0.0.1"),
        [("E_RECORD", 3, "renderer_version 0.0.1 differs")],
    ),
    "recipe-sha": (
        lambda r: r[3].update(recipe_sha256="4" * 64),
        [("E_RECORD", 3, "recipe_sha256 does not match")],
    ),
    "n-samples": (
        lambda r: r[3].update(n_samples=43200),
        [("E_RECORD", 3, "does not match total_ms"), ("E_RECORD", 3, "does not match the blob")],
    ),
    "file-sha": (
        lambda r: r[3].update(file_sha256="5" * 64),
        [("E_BLOB_FILE_HASH", 3, "file_sha256")],
    ),
    "float-total-ms": (
        lambda r: r[3]["recipe"].update(total_ms=float(r[3]["recipe"]["total_ms"])),
        [
            ("E_RECORD", 3, "malformed record"),
            ("E_RECORD", 6, "n_entries 3 is not 2"),
            ("E_RECORD", 6, "snapshot_sha256"),
        ],
    ),
    "book-id": (lambda r: r[3].update(book_id="DEMO-OTHER"), [("E_BOOK_ID", 3, "DEMO-OTHER")]),
    "kind-prefix": (lambda r: r[0].update(kind="study"), [("E_RECORD", 0, "DEMO-")]),
    "schema": (
        lambda r: r[3].update(extra_field=1),
        [
            ("E_LOG_SCHEMA", 3, "extra_field"),
            ("E_RECORD", 6, "n_entries 3 is not 2"),
            ("E_RECORD", 6, "snapshot_sha256"),
        ],
    ),
    "recipe-swapped": (
        _swap_recipe,
        [("E_RERENDER", 1, "re-rendering"), ("E_RECORD", 4, "different recipe_sha256")],
    ),
    "duplicate-entry": (
        lambda r: r[3].update(
            {
                k: r[1][k]
                for k in ("recipe", "recipe_sha256", "pcm_sha256", "file_sha256", "n_samples")
            }
        ),
        [
            ("E_ADMISSIBILITY", 3, "E_DUPLICATE"),
            ("E_RECORD", 6, "snapshot_sha256"),
        ],
    ),
}


def assert_issues(report: Any, expected: Expected) -> None:
    """`report.issues` are exactly `expected`, in any order."""
    actual = sorted((i.code, -1 if i.seq is None else i.seq, i.message) for i in report.issues)
    wanted = sorted((c, -1 if s is None else s, m) for c, s, m in expected)
    assert len(actual) == len(wanted), report.issues
    for (code, seq, message), (w_code, w_seq, fragment) in zip(actual, wanted, strict=True):
        assert (code, seq) == (w_code, w_seq) and fragment in message, report.issues


@pytest.mark.parametrize("name", list(FORGERIES))
def test_forged_logs_with_valid_hashes_are_caught_by_record_checks(
    sealed: VocabularyStore, name: str
):
    mutate, expected = FORGERIES[name]
    records = [json.loads(line) for line in lines(sealed)]
    assert [r["event"] for r in records] == [
        "create_book",
        "commit",
        "commit",
        "commit",
        "recommit_noop",
        "overwrite_rejected",
        "freeze",
        "commit_rejected_frozen",
    ]
    mutate(records)
    forge(sealed, records)
    assert_issues(sealed.verify(BOOK), expected)


def test_rerender_catches_a_consistent_recipe_swap(sealed: VocabularyStore):
    records = [json.loads(line) for line in lines(sealed)]
    _swap_recipe(records)  # recipe and recipe_sha256 of K-a1; the waveform stays
    records[4]["recipe_sha256"] = records[1]["recipe_sha256"]  # the no-op record too
    forge(sealed, records)
    assert sealed.verify(BOOK, rerender=False).ok
    assert sealed.verify(BOOK, rerender=True).codes == ("E_RERENDER",)
    head = hashlib.sha256(lines(sealed)[-1]).hexdigest()
    assert sealed.verify(BOOK, rerender=False, expected_head=head).ok


def test_non_json_and_non_object_lines(grown: VocabularyStore):
    path = grown.log_path(BOOK)
    data = path.read_bytes()
    for name, extra in (
        ("not-json", b"{oops"),
        ("array", b"[1,2]"),
        ("non-ascii", '{"é":1}'.encode()),
    ):
        write_bytes(path, data + extra + b"\n")
        report = grown.verify(BOOK)
        assert report.codes[0] == "E_LOG_JSON", name
    report_dict = grown.verify(BOOK).to_dict()
    assert report_dict["issues"][0]["code"] == "E_LOG_JSON" and report_dict["ok"] is False
    write_bytes(path, data)
    report = grown.verify(BOOK)
    assert report.ok and report.to_dict()["n_entries"] == 3


# --- Files: write-once blobs, read-only logs, Windows-safe handling -------------------------


def test_blobs_are_write_once_and_read_only(grown: VocabularyStore):
    entry = grown.get(BOOK, "K-a1")
    blob = grown.blob_path(entry.pcm_sha256)
    assert is_read_only(blob) and is_read_only(grown.log_path(BOOK))
    if not IS_ROOT:
        with pytest.raises(PermissionError):
            open(blob, "r+b")  # noqa: SIM115
        with pytest.raises(PermissionError):
            open(grown.log_path(BOOK), "ab")  # noqa: SIM115
    assert not list(blob.parent.glob("*.partial"))


@pytest.mark.parametrize(
    "junk",
    [
        pytest.param(b"not the waveform", id="junk"),
        pytest.param(b"truncated", id="truncated"),
    ],
)
def test_blob_with_other_bytes_is_quarantined_and_replaced(store: VocabularyStore, junk: bytes):
    # For example a crash during an exclusive create left a truncated file under the name.
    rendered = render(RECIPES["K-a1"], P)
    blob = store.blob_path(rendered.pcm_sha256)
    blob.parent.mkdir(parents=True)
    if junk == b"truncated":
        junk = store_mod.wav_bytes(rendered.pcm)[:5000]
    blob.write_bytes(junk)
    os.chmod(blob, stat.S_IREAD)
    entry, _ = commit(store, "K-a1")
    assert blob.read_bytes() == store_mod.wav_bytes(rendered.pcm) and is_read_only(blob)
    kept = blob.parent / "quarantine" / f"{blob.name}.{hashlib.sha256(junk).hexdigest()}"
    assert kept.read_bytes() == junk  # kept for the audit
    assert store.verify(BOOK).ok and entry.pcm_sha256 == rendered.pcm_sha256
    # The same junk again: the quarantined copy already exists, the new one is dropped.
    write_bytes(blob, junk)
    store.create_book("DEMO-T2", P, kind="synthetic")
    commit(store, "K-a1", book="DEMO-T2")
    assert sorted(p.name for p in kept.parent.iterdir()) == [kept.name]
    assert store.verify(BOOK).ok and store.verify("DEMO-T2").ok


def test_leftover_partial_file_is_replaced(store: VocabularyStore):
    rendered = render(RECIPES["K-a1"], P)
    final = store.blob_path(rendered.pcm_sha256)
    partial = final.with_name(f"{final.name}.{os.getpid()}-{threading.get_ident()}.partial")
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"interrupted")
    os.chmod(partial, stat.S_IREAD)
    entry, _ = commit(store, "K-a1")
    assert not partial.exists() and store.blob_path(entry.pcm_sha256).is_file()


def test_blob_publish_without_hard_links(store: VocabularyStore, monkeypatch: pytest.MonkeyPatch):
    def no_links(src: Any, dst: Any) -> None:
        raise OSError("hard links not supported")

    monkeypatch.setattr(store_mod.os, "link", no_links)
    entry, _ = commit(store, "K-a1")
    assert store.verify(BOOK).ok and is_read_only(store.blob_path(entry.pcm_sha256))
    assert not list(store.blob_path(entry.pcm_sha256).parent.glob("*.partial"))


def test_publish_without_hard_links_onto_a_read_only_blob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def no_links(src: Any, dst: Any) -> None:
        raise OSError("hard links not supported")

    def windows_replace(src: Any, dst: Any) -> None:
        Path(dst).write_bytes(Path(src).read_bytes())  # another writer won the race
        raise PermissionError(errno.EACCES, "read-only target", str(dst))

    monkeypatch.setattr(store_mod.os, "link", no_links)
    monkeypatch.setattr(store_mod.os, "replace", windows_replace)
    partial, final = tmp_path / "a.partial", tmp_path / "a.wav"
    partial.write_bytes(b"x")
    store_mod._publish(partial, final)
    assert final.read_bytes() == b"x"

    def failing_replace(src: Any, dst: Any) -> None:
        raise PermissionError(errno.EACCES, "denied", str(dst))

    monkeypatch.setattr(store_mod.os, "replace", failing_replace)
    with pytest.raises(PermissionError):
        store_mod._publish(partial, tmp_path / "b.wav")


def test_concurrent_identical_blob_is_accepted(
    store: VocabularyStore, monkeypatch: pytest.MonkeyPatch
):
    real_link = os.link

    def racing_link(src: Any, dst: Any) -> None:
        real_link(src, dst)  # another writer published the same blob first
        raise FileExistsError(dst)

    monkeypatch.setattr(store_mod.os, "link", racing_link)
    commit(store, "K-a1")
    assert store.verify(BOOK).ok


def test_same_waveform_in_two_books_shares_one_blob(root: Path):
    s = make_store(root)
    s.create_book("DEMO-A", P, kind="synthetic")
    s.create_book("DEMO-B", P, kind="synthetic")
    a, _ = commit(s, "K-a1", book="DEMO-A")
    b, _ = commit(s, "K-a1", book="DEMO-B")
    assert a.pcm_sha256 == b.pcm_sha256 and a.pcm == b.pcm
    assert len(list((root / "blobs").iterdir())) == 1
    assert s.books() == ["DEMO-A", "DEMO-B"]


# --- Random commit, overwrite and freeze sequences ---------------------------------------


OPS = st.lists(
    st.tuples(
        st.sampled_from(["commit", "commit", "other_recipe", "relabel", "garbage", "freeze"]),
        st.sampled_from(ATOM_IDS),
        st.sampled_from(ATOM_IDS),
    ),
    max_size=24,
)


@settings(max_examples=60, deadline=None)
@given(ops=OPS)
def test_random_sequences_never_change_committed_entries(ops: list[tuple[str, str, str]]):
    with tempfile.TemporaryDirectory() as tmp:
        try:
            _run_sequence(Path(tmp), ops)
        finally:
            make_writable(Path(tmp))


def _run_sequence(root: Path, ops: list[tuple[str, str, str]]) -> None:
    s = make_store(root)
    s.create_book(BOOK, P, kind="synthetic")
    model: dict[str, tuple[Recipe, str]] = {}
    blobs: dict[str, bytes] = {}
    frozen = False
    overwrites = rejected_frozen = 0
    for op, atom_id, other in ops:
        n_before = len(lines(s))
        if op == "freeze":
            s.freeze(BOOK)
            assert len(lines(s)) == n_before + (0 if frozen else 1)
            frozen = True
            continue
        recipe: Recipe | str = RECIPES[atom_id]
        lbl = label(atom_id)
        if op == "other_recipe":
            recipe = RECIPES[other]
        elif op == "relabel":
            lbl = label(atom_id, shift=1)
        elif op == "garbage":
            recipe = '{"total_ms": 600'
        if frozen:
            expected = "frozen"
        elif atom_id in model:
            expected = "noop" if model[atom_id] == (recipe, lbl) else "overwrite"
        elif lbl in {v[1] for v in model.values()}:
            expected = "label"
        elif not isinstance(recipe, Recipe) or recipe in {v[0] for v in model.values()}:
            expected = "rejected"
        else:
            expected = "commit"
        try:
            entry, _ = s.commit(BOOK, atom_id, lbl, recipe, source="hyp")
            outcome = "noop" if atom_id in model else "commit"
        except BookFrozen:
            outcome = "frozen"
            rejected_frozen += 1
        except OverwriteRejected:
            outcome = "overwrite"
            overwrites += 1
        except CommitRejected:
            outcome = "rejected"
        except StoreError as err:
            assert err.code == E_LABEL
            outcome = "label"
        assert outcome == expected, (op, atom_id, other)
        event(f"outcome:{outcome}")
        logged = {"commit": 1, "noop": 1, "overwrite": 1, "frozen": 1}.get(outcome, 0)
        assert len(lines(s)) == n_before + logged
        if outcome == "commit":
            assert isinstance(recipe, Recipe)
            model[atom_id] = (recipe, lbl)
            blobs[atom_id] = s.blob_path(entry.pcm_sha256).read_bytes()
        current = {e.atom_id: e for e in s.list(BOOK)}
        assert list(current) == list(model)
        for committed, (r, lb) in model.items():
            assert (current[committed].recipe, current[committed].semantic_label) == (r, lb)
            assert s.blob_path(current[committed].pcm_sha256).read_bytes() == blobs[committed]
    records = check_log_format(s)
    assert sum(r["event"] == "overwrite_rejected" for r in records) == overwrites
    assert sum(r["event"] == "commit_rejected_frozen" for r in records) == rejected_frozen
    assert list(s.snapshot_hashes(BOOK)) == list(model)
    assert s.verify(BOOK).ok


# --- Exact issue sets (each check is load-bearing) ---------------------------------------


def test_middle_line_replaced_by_a_self_consistent_record_breaks_only_the_chain(
    sealed: VocabularyStore,
):
    data = lines(sealed)
    record = json.loads(data[4])  # the no-op record
    record["source"] = "forged-source"
    record["record_sha256"] = record_sha256(record)  # seq and prev_sha256 stay right
    data[4] = canonical_json(record)
    write_bytes(sealed.log_path(BOOK), b"\n".join(data) + b"\n")
    assert_issues(sealed.verify(BOOK), [("E_CHAIN", 5, "prev_sha256")])


def test_reserialized_last_line_is_only_noncanonical(sealed: VocabularyStore):
    data = lines(sealed)
    data[-1] = json.dumps(json.loads(data[-1])).encode("ascii")  # spaces after separators
    write_bytes(sealed.log_path(BOOK), b"\n".join(data) + b"\n")
    assert_issues(sealed.verify(BOOK), [("E_LOG_NONCANONICAL", 7, "canonical JSON")])


def test_duplicated_seq_is_only_a_seq_error(sealed: VocabularyStore):
    records = [json.loads(line) for line in lines(sealed)]
    seqs = list(range(len(records)))
    seqs[4] = 3  # duplicates line 3's seq; hashes and chain recomputed
    forge(sealed, records, seqs=seqs)
    assert_issues(sealed.verify(BOOK), [("E_SEQ", 4, "seq is 3, expected 4")])


def test_label_swap_with_fresh_hashes_needs_an_anchor(sealed: VocabularyStore):
    """Swapping two meanings and recomputing every hash and marker is internally
    consistent; only a chain head recorded elsewhere detects it (sound/docs/store.md)."""
    anchor = sealed.head(BOOK)
    records = [json.loads(line) for line in lines(sealed)]
    records[1]["semantic_label"], records[2]["semantic_label"] = "REMOVE_ONE", "ADD_ONE"
    records[4]["semantic_label"] = "REMOVE_ONE"  # the no-op record of K-a1
    records[5]["attempted_semantic_label"] = "ADD_ONE"  # and the overwrite of K-a2
    forge(sealed, records)
    assert sealed.verify(BOOK).ok
    assert sealed.get(BOOK, "K-a1").semantic_label == "REMOVE_ONE"
    assert sealed.verify(BOOK, expected_head=anchor).codes == ("E_ANCHOR",)
    with pytest.raises(StoreIntegrityError):
        sealed.get(BOOK, "K-a1", expected_head=anchor)


# --- Locking and failed writes -------------------------------------------------------------


def _run_threads(target: Callable[[int], None], n: int) -> list[BaseException]:
    errors: list[BaseException] = []
    barrier = threading.Barrier(n)

    def run(i: int) -> None:
        try:
            barrier.wait()
            target(i)
        except BaseException as err:  # noqa: BLE001 - reported to the main thread
            errors.append(err)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


def test_concurrent_writers_are_serialized(root: Path):
    make_store(root).create_book(BOOK, P, kind="synthetic")
    atoms = list(WAVES[0])

    def write(i: int) -> None:  # one store object per thread, like separate processes
        commit(VocabularyStore(root, reserved=()), atoms[i])

    assert _run_threads(write, len(atoms)) == []
    s = make_store(root)
    records = check_log_format(s)
    assert sorted(r["atom_id"] for r in records[1:]) == sorted(atoms)
    assert [r["commit_index"] for r in records[1:]] == list(range(len(atoms)))
    assert s.verify(BOOK).ok


def test_concurrent_commits_of_one_atom_give_one_commit(root: Path):
    s = VocabularyStore(root, reserved=())
    s.create_book(BOOK, P, kind="synthetic")
    assert _run_threads(lambda i: commit(s, "K-a1"), 5) == []
    events = [r["event"] for r in check_log_format(s)]
    assert events == ["create_book", "commit"] + ["recommit_noop"] * 4
    assert s.verify(BOOK).ok


def test_lock_timeout(store: VocabularyStore):
    impatient = make_store(store.root, lock_timeout=0.05)
    with store._lock(BOOK):
        with pytest.raises(StoreLocked) as err:
            commit(impatient, "K-a1")
        assert err.value.code == "E_LOCKED"
        with pytest.raises(StoreLocked):
            impatient.get(BOOK, "K-a1")
    commit(impatient, "K-a1")  # released
    assert store.verify(BOOK).ok


def test_append_refuses_a_log_that_changed_after_the_scan(
    store: VocabularyStore, monkeypatch: pytest.MonkeyPatch
):
    real_run = store_mod._Scanner.run

    def run_then_write(self: Any) -> Any:
        state = real_run(self)
        path = self.store.log_path(self.state.book_id)
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        with open(path, "ab") as f:  # a writer that ignores the lock
            f.write(b"x")
        return state

    before = store.log_path(BOOK).read_bytes()
    monkeypatch.setattr(store_mod._Scanner, "run", run_then_write)
    with pytest.raises(StoreError) as err:
        commit(store, "K-a1")
    assert err.value.code == E_CONCURRENT
    monkeypatch.undo()
    assert store.log_path(BOOK).read_bytes() == before + b"x"


def _failing_write(real: Callable[..., None], failing_mode: str) -> Callable[..., None]:
    def write(path: Path, data: bytes, mode: str) -> None:
        if mode != failing_mode:
            return real(path, data, mode)
        with open(path, mode) as f:
            f.write(data[: len(data) // 2])
        raise OSError(errno.ENOSPC, "No space left on device")

    return write


def test_failed_append_is_rolled_back(grown: VocabularyStore, monkeypatch: pytest.MonkeyPatch):
    path = grown.log_path(BOOK)
    before = path.read_bytes()
    monkeypatch.setattr(store_mod, "_write_new", _failing_write(store_mod._write_new, "ab"))
    with pytest.raises(OSError) as err:
        commit(grown, "K-a3")
    assert err.value.errno == errno.ENOSPC
    assert path.read_bytes() == before and is_read_only(path)
    monkeypatch.undo()
    assert grown.verify(BOOK).ok
    commit(grown, "K-a3")
    assert grown.verify(BOOK).ok


def test_failed_create_leaves_no_book(root: Path, monkeypatch: pytest.MonkeyPatch):
    s = make_store(root)
    monkeypatch.setattr(store_mod, "_write_new", _failing_write(store_mod._write_new, "xb"))
    with pytest.raises(OSError):
        s.create_book(BOOK, P, kind="synthetic")
    monkeypatch.undo()
    assert s.books() == [] and not s.log_path(BOOK).exists()
    s.create_book(BOOK, P, kind="synthetic")
    assert s.verify(BOOK).ok


@pytest.mark.skipif(sys.platform == "win32" or IS_ROOT, reason="POSIX permissions, non-root")
def test_read_only_store_copy_can_be_read(grown: VocabularyStore):
    lock = grown.book_dir(BOOK) / ".lock"
    os.chmod(lock, stat.S_IREAD)  # an audit copy where nothing is writable
    dirs = [grown.book_dir(BOOK), grown.root / "blobs"]
    try:
        for d in dirs:
            os.chmod(d, 0o555)
        assert grown.verify(BOOK).ok and len(grown.list(BOOK)) == 3  # read-only lock file
        with pytest.raises(PermissionError):
            commit(grown, "K-a3")
        os.chmod(grown.book_dir(BOOK), 0o755)
        lock.unlink()
        os.chmod(grown.book_dir(BOOK), 0o555)
        assert grown.get(BOOK, "K-a1").atom_id == "K-a1"  # no lock file and none can be made
    finally:
        for d in dirs:
            os.chmod(d, 0o755)


# --- Freeze and void markers -------------------------------------------------------------


def test_frozen_marker_names_the_freeze_record(grown: VocabularyStore):
    head = grown.freeze(BOOK)
    marker = grown.book_dir(BOOK) / FROZEN_MARKER
    freeze = check_log_format(grown)[-1]
    assert marker.read_bytes() == store_mod._marker_bytes(BOOK, "freeze", freeze["seq"], head)
    assert is_read_only(marker) and not (grown.book_dir(BOOK) / VOID_MARKER).exists()
    path = grown.log_path(BOOK)
    data = path.read_bytes()
    write_bytes(path, b"\n".join(data[:-1].split(b"\n")[:-1]) + b"\n")  # drop the freeze line
    assert_issues(grown.verify(BOOK), [("E_MARKER", None, "FROZEN marker exists")])
    with pytest.raises(StoreIntegrityError):
        commit(grown, "K-a3")  # never accepted as an open book
    write_bytes(path, data)
    write_bytes(marker, marker.read_bytes().replace(b'"seq":4', b'"seq":3'))
    assert_issues(grown.verify(BOOK), [("E_MARKER", 4, "does not match")])
    with pytest.raises(StoreIntegrityError):
        grown.freeze(BOOK)  # a wrong marker is not repaired
    os.chmod(marker, stat.S_IWRITE | stat.S_IREAD)
    marker.unlink()
    assert_issues(grown.verify(BOOK), [("E_MARKER", 4, "missing")])
    with pytest.raises(StoreIntegrityError):
        grown.get(BOOK, "K-a1")
    assert grown.freeze(BOOK) == head  # completes the marker, appends nothing
    assert grown.verify(BOOK, expected_head=head).ok


def test_interrupted_freeze_is_completed_by_freeze(
    grown: VocabularyStore, monkeypatch: pytest.MonkeyPatch
):
    def crash(*args: Any) -> None:
        raise OSError(errno.EIO, "power cut")

    monkeypatch.setattr(VocabularyStore, "_write_marker", crash)
    with pytest.raises(OSError):
        grown.freeze(BOOK)
    monkeypatch.undo()
    assert grown.verify(BOOK).codes == ("E_MARKER",)
    with pytest.raises(StoreIntegrityError):
        commit(grown, "K-a3")
    head = grown.freeze(BOOK)
    assert grown.verify(BOOK).ok and grown.book(BOOK).frozen and head == grown.head(BOOK)


def test_void_book(grown: VocabularyStore):
    snapshot = grown.snapshot_hashes(BOOK)
    with pytest.raises(InvalidIdentifier):
        grown.void(BOOK, cause="lost", reason="DEV-0005")
    with pytest.raises(InvalidIdentifier):
        grown.void(BOOK, cause="other", reason="")
    with pytest.raises(InvalidIdentifier):
        grown.void(BOOK, cause="other", reason="DEV-0005", superseded_by=BOOK)
    with pytest.raises(InvalidIdentifier):
        grown.void(BOOK, cause="other", reason="DEV-0005", superseded_by="BK-A2-1")
    head = grown.void(
        BOOK,
        cause="failed_generation",
        reason="DEV-0005 no eligible bank recipe",
        superseded_by="DEMO-T9",
    )
    record = check_log_format(grown)[-1]
    assert record["event"] == "void" and record["cause"] == "failed_generation"
    assert record["superseded_by"] == "DEMO-T9" and record["n_entries"] == 3
    assert record["snapshot_sha256"] == snapshot_digest(snapshot)
    for name in (FROZEN_MARKER, VOID_MARKER):
        marker = grown.book_dir(BOOK) / name
        assert marker.read_bytes() == store_mod._marker_bytes(BOOK, "void", record["seq"], head)
    info = grown.book(BOOK)
    assert info.void and info.frozen and grown.verify(BOOK).void
    with pytest.raises(BookFrozen) as err:
        commit(grown, "K-a3")
    assert err.value.void and "void" in str(err.value)
    assert check_log_format(grown)[-1]["freeze_seq"] == record["seq"]
    assert grown.freeze(BOOK) == grown.head(BOOK)  # closed already: nothing appended
    with pytest.raises(StoreError) as err2:
        grown.void(BOOK, cause="other", reason="DEV-0006")
    assert err2.value.code == E_VOID
    assert grown.snapshot_hashes(BOOK) == snapshot and grown.verify(BOOK).ok
    grown.create_book("DEMO-T9", P, kind="synthetic")
    assert grown.books() == [BOOK, "DEMO-T9"]
    assert grown.books(void=True) == [BOOK] and grown.books(void=False) == ["DEMO-T9"]


def test_void_after_freeze_and_removed_void_line(grown: VocabularyStore):
    freeze_head = grown.freeze(BOOK)
    grown.void(BOOK, cause="batch_rebuild", reason="DEV-0007 rater withdrew")
    records = check_log_format(grown)
    frozen = grown.book_dir(BOOK) / FROZEN_MARKER
    assert frozen.read_bytes() == store_mod._marker_bytes(
        BOOK, "freeze", records[-2]["seq"], freeze_head
    )
    assert grown.verify(BOOK).ok and grown.book(BOOK).void
    path = grown.log_path(BOOK)
    write_bytes(path, b"\n".join(lines(grown)[:-1]) + b"\n")  # drop the void line
    assert_issues(grown.verify(BOOK), [("E_MARKER", None, "VOID marker exists")])
    with pytest.raises(StoreIntegrityError):
        grown.books(void=False)


# --- Versions, thresholds and lookups -------------------------------------------------------


def test_book_records_code_hashes(store: VocabularyStore):
    info = store.book(BOOK)
    assert info.renderer_hash == renderer_hash()
    assert info.validator_hash == validator_code_hash()
    record = store.records(BOOK)[0]
    assert (record["renderer_hash"], record["validator_hash"]) == (
        info.renderer_hash,
        info.validator_hash,
    )


@pytest.mark.parametrize("which", [0, 1], ids=["renderer-hash", "validator-hash"])
def test_code_change_blocks_new_commits(
    grown: VocabularyStore, monkeypatch: pytest.MonkeyPatch, which: int
):
    hashes = list(store_mod._code_hashes())
    hashes[which] = "f" * 64
    monkeypatch.setattr(store_mod, "_code_hashes", lambda: tuple(hashes))
    with pytest.raises(StoreError) as err:
        commit(grown, "K-a3")
    assert err.value.code == E_VERSION
    with pytest.raises(OverwriteRejected):  # old entries are still protected and logged
        commit(grown, "K-a1", recipe=RECIPES["K-a3"])


@pytest.mark.parametrize(
    ("threshold", "text"),
    [
        pytest.param(Fraction(1, 8), "0.125", id="fraction"),
        pytest.param(Decimal("0.15"), "0.15", id="decimal"),
        pytest.param("0.10", "0.1", id="str"),
        pytest.param(0, "0", id="int"),
    ],
)
def test_thresholds_are_stored_as_decimals(root: Path, threshold: Any, text: str):
    s = make_store(root)
    s.create_book(BOOK, P, kind="synthetic", threshold=threshold)
    assert s.records(BOOK)[0]["threshold"] == text
    commit(s, "K-a1")
    assert s.verify(BOOK).ok


def test_non_terminating_threshold_is_refused(root: Path):
    s = make_store(root)
    with pytest.raises(ValueError, match="finite decimal"):
        s.create_book(BOOK, P, kind="synthetic", threshold=Fraction(1, 3))
    assert s.books() == []


def test_book_ids_match_exactly_on_every_platform(grown: VocabularyStore):
    for other in ("demo-t1", "Demo-T1", "DEMO-t1"):
        with pytest.raises(NotFound):
            grown.get(other, "K-a1")
        with pytest.raises(NotFound):
            grown.head(other)
        with pytest.raises(NotFound):
            commit(grown, "K-a3", book=other)
        assert grown.verify(other).codes == ("E_LOG_MISSING",)
    assert grown.books() == [BOOK]


def test_gitignore_covers_store_layouts():
    text = (REPO / ".gitignore").read_text(encoding="utf-8")
    python_block = text.split("# Python / tools", 1)[1].split("\n\n", 1)[0]
    for pattern in (
        "**/books/*/log.jsonl",
        "**/books/*/FROZEN",
        "**/books/*/VOID",
        "**/books/*/.lock",
        "**/blobs/*.wav",
        "**/blobs/quarantine/",
        "*.partial",
    ):
        assert pattern in python_block.splitlines(), pattern


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        pytest.param(
            lambda r: r.append(dict(r[-1])),
            [("E_EVENT", 5, "second void record")],
            id="second-void",
        ),
        pytest.param(
            lambda r: r[-1].update(superseded_by=BOOK),
            [("E_RECORD", 4, "cannot supersede itself")],
            id="self-supersede",
        ),
        pytest.param(
            lambda r: r[-1].update(n_entries=1),
            [("E_RECORD", 4, "n_entries 1 is not 3")],
            id="void-count",
        ),
    ],
)
def test_forged_void_records(grown: VocabularyStore, mutate: Mutation, expected: Expected):
    grown.void(BOOK, cause="other", reason="DEV-0008")
    records = [json.loads(line) for line in lines(grown)]
    mutate(records)
    forge(grown, records)
    assert_issues(grown.verify(BOOK), expected)
