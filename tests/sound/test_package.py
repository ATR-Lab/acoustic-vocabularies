"""Learner and dyad package builder tests (issue #13; Study A protocol §3.7, §4; Study B
protocol §4-§7; docs/interfaces/package-format.md).

Every book and bank here is synthetic (`DEMO-` IDs, `av_sound.synthetic`,
`synthetic_dyad_bank`) and lives in a temporary directory. The DEMO permutations and visit
schedules in `fixtures/schedules-demo/` are byte copies of the schedules stack's DEMO
examples (#29, #30). Held-out messages of synthetic books are test data: the positive
controls below build them by hand to prove that the leak scan finds them.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import struct
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

import av_sound.package as package_mod
from av_sound import (
    GAP_SAMPLES,
    BookFrozen,
    Profile,
    VocabularyStore,
    composite_hash,
    message_length,
    snapshot_digest,
)
from av_sound._schemas import load_schema, schema_validator
from av_sound.composer import AtomAudio
from av_sound.dyad_bank import (
    BankError,
    BankOption,
    DyadBank,
    identity_labels,
    load_dyad_bank,
    synthetic_dyad_bank,
)
from av_sound.grammar import (
    ATOM_IDS,
    HELDOUT_MESSAGE_IDS,
    MATRIX,
    MESSAGES,
    TRAINED_MESSAGE_IDS,
    parse_message_id,
)
from av_sound.package import (
    ATOM_WAVES,
    LeakReport,
    LoadedPackage,
    PackageError,
    PackageIntegrityError,
    PackageResult,
    build_dyad_package,
    build_package,
    load_package,
    novel_by_visit,
    package_sha256,
    permutation_matrix,
    scan_package,
    seal,
)
from av_sound.store import canonical_json
from av_sound.synthetic import synthetic_recipes
from av_sound.wav import pcm_from_wav, wav_bytes

REPO = Path(__file__).resolve().parents[2]
SOUND = REPO / "sound"
FIXTURES = REPO / "tests" / "sound" / "fixtures" / "schedules-demo"
A_UNIT = FIXTURES / "A-C01"
B_UNIT = FIXTURES / "B-C01"
EXAMPLE = SOUND / "examples" / "package-demo"
VECTORS = SOUND / "testvectors" / "composition" / "vectors.json"


def load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "build_example_package", SOUND / "tools" / "build_example_package.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = load_tool()
A_LABELS = tool.permutation_labels(A_UNIT)
B_LABELS = tool.permutation_labels(B_UNIT)


def make_writable(path: Path) -> None:
    for p in path.rglob("*"):
        if p.is_file():
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)


def book_store(root: Path, profile: Profile, book_id: str, **kwargs: Any) -> VocabularyStore:
    return tool.demo_store_book(root, profile=profile, book_id=book_id, **kwargs)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def wav_files(root: Path, folder: str) -> list[str]:
    return sorted(p.name for p in (root / folder).glob("*.wav"))


def flip_byte(path: Path, offset: int | None = None) -> None:
    data = bytearray(path.read_bytes())
    i = len(data) // 2 if offset is None else offset
    data[i] ^= 0x01
    path.write_bytes(bytes(data))


def rewrite_manifest(root: Path, change: Callable[[dict[str, Any]], None]) -> None:
    """Edit manifest.json and recompute its package hash (a consistent forgery)."""
    manifest = read_json(root / "manifest.json")
    change(manifest)
    manifest["package_sha256"] = package_sha256(manifest)
    (root / "manifest.json").write_bytes(package_mod._json_bytes(manifest))


def heldout_pcm(root: Path, message_id: str) -> bytes:
    """Test data only: a held-out message of a synthetic book, built by hand."""
    ref = parse_message_id(message_id)
    action = pcm_from_wav((root / "atoms" / f"{ref.action.atom_id}.wav").read_bytes())
    referent = pcm_from_wav((root / "atoms" / f"{ref.referent.atom_id}.wav").read_bytes())
    return action + bytes(2 * GAP_SAMPLES) + referent


# --- Fixtures ------------------------------------------------------------------------


@pytest.fixture(scope="module")
def a_books(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    """Three synthetic A books (one per profile), frozen, with A-C01 meanings, packaged."""
    base = tmp_path_factory.mktemp("a-books")
    out: dict[str, Any] = {}
    for profile in Profile:
        book = f"DEMO-BOOK-{profile.value}"
        store = book_store(base / f"store-{profile.value}", profile, book)
        result = build_package(store, book, base / f"pkg-{profile.value}")
        out[profile.value] = {"store": store, "book": book, "result": result}
    yield out
    make_writable(base)


@pytest.fixture(scope="module")
def dyad(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    base = tmp_path_factory.mktemp("dyad")
    bank = synthetic_dyad_bank("DEMO-DYAD-01", labels=B_LABELS)
    return {"bank": bank, "result": build_dyad_package(bank, base / "pkg"), "base": base}


@pytest.fixture
def pkg_a(a_books: dict[str, Any], tmp_path: Path) -> Path:
    """A writable copy of the P1 package."""
    target = tmp_path / "pkg"
    shutil.copytree(a_books["P1"]["result"].path, target)
    return target


@pytest.fixture
def pkg_b(dyad: dict[str, Any], tmp_path: Path) -> Path:
    target = tmp_path / "pkg-b"
    shutil.copytree(dyad["result"].path, target)
    return target


@pytest.fixture
def study_store(tmp_path: Path) -> Iterator[tuple[VocabularyStore, str]]:
    """A non-DEMO (`study`) book in a temporary store outside the repository."""
    book = "BK7Q2X"
    s = VocabularyStore(tmp_path / "store", clock=tool.fixed_clock(), reserved=())
    s.create_book(book, Profile.P2, kind="study")
    recipes = synthetic_recipes(Profile.P2)
    for atom in ATOM_IDS:
        s.commit(book, atom, A_LABELS[atom], recipes[atom], source=f"slot-{atom}-x")
    s.freeze(book)
    yield s, book
    make_writable(tmp_path)


def real_permutation() -> bytes:
    """The A-C01 DEMO permutation re-labelled as a non-DEMO document (test only)."""
    doc = read_json(A_UNIT / "permutation.json")
    doc["demo"] = False
    doc["seed_label"] = "sha256:" + "ab" * 32
    return package_mod._json_bytes(doc)


# --- Acceptance: contents of an A package ----------------------------------------------


@pytest.mark.parametrize("profile", ["P1", "P2", "P3"])
def test_a_package_has_16_atoms_18_trained_messages_and_no_heldout_audio(
    a_books: dict[str, Any], profile: str
):
    result: PackageResult = a_books[profile]["result"]
    root = result.path
    assert wav_files(root, "atoms") == sorted(f"{a}.wav" for a in ATOM_IDS)
    assert len(wav_files(root, "atoms")) == 16
    messages = wav_files(root, "messages")
    assert messages == sorted(f"{m}.wav" for m in TRAINED_MESSAGE_IDS)
    assert len(messages) == 18
    assert not [m for m in messages if m.removesuffix(".wav") in HELDOUT_MESSAGE_IDS]
    files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    assert len(files) == 16 + 18 + 3
    assert sorted(result.manifest["files"]) == [f for f in files if f != "manifest.json"]
    assert len([f for f in files if f.endswith(".wav")]) == 34


@pytest.mark.parametrize("profile", ["P1", "P2", "P3"])
def test_leak_scan_finds_no_heldout_audio_and_no_method_strings(
    a_books: dict[str, Any], profile: str
):
    result: PackageResult = a_books[profile]["result"]
    store, book = a_books[profile]["store"], a_books[profile]["book"]
    sources = [e.source for e in store.list(book)]
    report = scan_package(result.path, forbidden_strings=sources)
    assert report.ok, report.to_dict()
    assert (report.heldout_audio, report.method_strings) == (0, 0)
    assert report.heldout_hashes == 14
    assert report.files_scanned == 37
    assert report == result.leak_report


def test_rebuild_from_the_same_book_reproduces_the_package_hash(
    a_books: dict[str, Any], tmp_path: Path
):
    for profile in ("P1", "P2", "P3"):
        entry = a_books[profile]
        again = build_package(entry["store"], entry["book"], tmp_path / f"again-{profile}")
        first: PackageResult = entry["result"]
        assert again.package_sha256 == first.package_sha256
        assert again.manifest == first.manifest
        for rel in first.manifest["files"]:
            assert (again.path / rel).read_bytes() == (first.path / rel).read_bytes()
        assert (again.path / "manifest.json").read_bytes() == (
            first.path / "manifest.json"
        ).read_bytes()
    hashes = {a_books[p]["result"].package_sha256 for p in ("P1", "P2", "P3")}
    assert len(hashes) == 3


def test_rebuild_from_a_fresh_store_reproduces_the_package_hash(
    a_books: dict[str, Any], tmp_path: Path
):
    store = book_store(tmp_path / "store", Profile.P1, "DEMO-BOOK-P1")
    try:
        again = build_package(store, "DEMO-BOOK-P1", tmp_path / "pkg")
    finally:
        make_writable(tmp_path)
    assert again.package_sha256 == a_books["P1"]["result"].package_sha256


def test_loader_rejects_one_altered_byte_in_every_file(pkg_a: Path):
    assert (
        load_package(pkg_a).package_sha256 == read_json(pkg_a / "manifest.json")["package_sha256"]
    )
    for rel in sorted(read_json(pkg_a / "manifest.json")["files"]):
        path = pkg_a / rel
        original = path.read_bytes()
        flip_byte(path)
        with pytest.raises(PackageIntegrityError) as err:
            load_package(pkg_a)
        assert err.value.codes == ("E_HASH_MISMATCH",), rel
        assert err.value.problems[0].path == rel
        path.write_bytes(original)
    load_package(pkg_a)


def test_loader_rejects_an_altered_wav_header_byte(pkg_a: Path):
    flip_byte(pkg_a / "atoms" / "K-a1.wav", offset=24)  # sample rate field
    with pytest.raises(PackageIntegrityError) as err:
        load_package(pkg_a)
    assert "E_HASH_MISMATCH" in err.value.codes


def test_loader_rejects_an_altered_manifest(pkg_a: Path):
    manifest = pkg_a / "manifest.json"
    original = manifest.read_bytes()
    manifest.write_bytes(original.replace(b'"study": "A"', b'"study": "B"'))
    with pytest.raises(PackageIntegrityError) as err:
        load_package(pkg_a)
    assert err.value.codes[0] in ("E_SCHEMA", "E_PACKAGE_HASH")
    doc = json.loads(original)
    doc["package_id"] = "DEMO-BOOK-P9"
    manifest.write_bytes(package_mod._json_bytes(doc))
    with pytest.raises(PackageIntegrityError) as err:
        load_package(pkg_a)
    assert err.value.codes == ("E_PACKAGE_HASH",)
    manifest.write_bytes(b"{not json")
    with pytest.raises(PackageIntegrityError) as err:
        load_package(pkg_a)
    assert err.value.codes == ("E_JSON",)
    manifest.unlink()
    with pytest.raises(PackageIntegrityError) as err:
        load_package(pkg_a)
    assert err.value.codes == ("E_MANIFEST",)


def test_loader_checks_the_expected_package_hash(pkg_a: Path):
    good = read_json(pkg_a / "manifest.json")["package_sha256"]
    assert load_package(pkg_a, expected_package_sha256=good).package_sha256 == good
    with pytest.raises(PackageIntegrityError) as err:
        load_package(pkg_a, expected_package_sha256="0" * 64)
    assert err.value.codes == ("E_PACKAGE_HASH",)


def test_loader_rejects_missing_and_extra_files(pkg_a: Path):
    (pkg_a / "messages" / "K-a1-r1.wav").rename(pkg_a / "K-a1-r1.wav")
    with pytest.raises(PackageIntegrityError) as err:
        load_package(pkg_a)
    assert err.value.codes == ("E_FILE_MISSING", "E_FILE_EXTRA")
    (pkg_a / "K-a1-r1.wav").rename(pkg_a / "messages" / "K-a1-r1.wav")
    load_package(pkg_a)
    (pkg_a / ".DS_Store").write_bytes(b"x")
    with pytest.raises(PackageIntegrityError) as err:
        load_package(pkg_a)
    assert err.value.codes == ("E_FILE_EXTRA",)
    assert "not listed" in str(err.value)


def test_loader_rejects_a_consistent_forgery_of_the_audio_index(pkg_a: Path):
    """Swap two atom WAVs and fix every hash: the composites no longer match."""
    a1, a2 = pkg_a / "atoms" / "K-a1.wav", pkg_a / "atoms" / "K-a2.wav"
    data1, data2 = a1.read_bytes(), a2.read_bytes()
    a1.write_bytes(data2)
    a2.write_bytes(data1)

    def fix(manifest: dict[str, Any]) -> None:
        f = manifest["files"]
        f["atoms/K-a1.wav"], f["atoms/K-a2.wav"] = f["atoms/K-a2.wav"], f["atoms/K-a1.wav"]

    rewrite_manifest(pkg_a, fix)
    with pytest.raises(PackageIntegrityError) as err:
        load_package(pkg_a)
    assert err.value.codes == ("E_CONTENT",)


def test_loader_recomputes_composites(pkg_a: Path):
    audio = read_json(pkg_a / "audio.json")
    row = next(m for m in audio["messages"] if m["message_id"] == "K-a1-r2")
    row["composite_sha256"] = "0" * 64
    data = package_mod._json_bytes(audio)
    (pkg_a / "audio.json").write_bytes(data)
    rewrite_manifest(
        pkg_a,
        lambda m: m["files"].__setitem__(
            "audio.json", {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        ),
    )
    with pytest.raises(PackageIntegrityError) as err:
        load_package(pkg_a)
    assert err.value.codes == ("E_COMPOSITE",)
    assert load_package(pkg_a, check_composites=False).combinations_checked == 0


def test_loader_rejects_inconsistent_answers(pkg_a: Path):
    answers = read_json(pkg_a / "answers.json")
    answers["messages"][0]["semantic_action"] = answers["messages"][4]["semantic_action"]
    data = package_mod._json_bytes(answers)
    (pkg_a / "answers.json").write_bytes(data)
    rewrite_manifest(
        pkg_a,
        lambda m: m["files"].__setitem__(
            "answers.json", {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        ),
    )
    with pytest.raises(PackageIntegrityError) as err:
        load_package(pkg_a)
    assert err.value.codes == ("E_CONTENT",)


def test_read_file_detects_a_change_after_loading(pkg_a: Path):
    pkg = load_package(pkg_a)
    assert pkg.pcm("atoms/K-a1.wav") == pcm_from_wav((pkg_a / "atoms/K-a1.wav").read_bytes())
    flip_byte(pkg_a / "atoms" / "K-a1.wav")
    with pytest.raises(PackageIntegrityError):
        pkg.read_file("atoms/K-a1.wav")
    with pytest.raises(KeyError):
        pkg.file_path("atoms/K-a9.wav")


# --- Acceptance: all 32 combinations checked for duration and hash ---------------------


@pytest.mark.parametrize("profile", ["P1", "P2", "P3"])
def test_all_32_combinations_are_checked_without_playback(a_books: dict[str, Any], profile: str):
    root = a_books[profile]["result"].path
    pkg = load_package(root)
    assert pkg.combinations_checked == 32
    atoms = {
        a: AtomAudio(a, profile, pcm_from_wav((root / "atoms" / f"{a}.wav").read_bytes()))
        for a in ATOM_IDS
    }
    rows = {m["message_id"]: m for m in pkg.audio["messages"]}
    assert list(rows) == [m.message_id for m in MESSAGES]
    for m in MESSAGES:
        row = rows[m.message_id]
        action, referent = atoms[m.action.atom_id], atoms[m.referent.atom_id]
        n = message_length(action, referent)
        assert row["n_samples"] == n == action.n_samples + 9_600 + referent.n_samples
        assert 52_800 <= n <= 96_000 and 1_100 <= row["duration_ms"] <= 2_000
        assert row["duration_ms"] * 48 == n
        assert row["composite_sha256"] == composite_hash(action, referent)
        assert row["status"] == ("heldout" if m.is_heldout else "trained")
        if m.is_heldout:
            assert row["path"] is None and row["file_sha256"] is None
            assert not (root / "messages" / f"{m.message_id}.wav").exists()
        else:
            pcm = pcm_from_wav((root / row["path"]).read_bytes())
            assert hashlib.sha256(pcm).hexdigest() == row["composite_sha256"]
            assert pcm[: 2 * action.n_samples] == action.pcm
            assert pcm[2 * (n - referent.n_samples) :] == referent.pcm


def test_composite_hashes_match_the_composition_vectors(a_books: dict[str, Any]):
    """Same synthetic recipes as the #10 vectors: every composite hash must agree."""
    vectors = {b["book_id"]: b for b in read_json(VECTORS)["books"]}
    for profile in ("P1", "P2", "P3"):
        audio = read_json(a_books[profile]["result"].path / "audio.json")
        expected = {
            m["message_id"]: (m["composite_sha256"], m["n_samples"])
            for m in vectors[f"DEMO-{profile}"]["messages"]
        }
        got = {m["message_id"]: (m["composite_sha256"], m["n_samples"]) for m in audio["messages"]}
        assert got == expected


def test_builder_never_composes_or_writes_a_heldout_message(
    a_books: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    composed: list[str] = []
    written: list[str] = []
    real_compose, real_write = package_mod.compose_message, package_mod.write_message_wav

    def spy_compose(action: Any, referent: Any, **kwargs: Any) -> Any:
        message = real_compose(action, referent, **kwargs)
        composed.append(message.message_id)
        return message

    def spy_write(message: Any, path: Any, **kwargs: Any) -> str:
        written.append(message.message_id)
        return real_write(message, path, **kwargs)

    monkeypatch.setattr(package_mod, "compose_message", spy_compose)
    monkeypatch.setattr(package_mod, "write_message_wav", spy_write)
    entry = a_books["P2"]
    build_package(entry["store"], entry["book"], tmp_path / "pkg")
    assert composed == written == list(TRAINED_MESSAGE_IDS)


# --- answers.json and anonymity ---------------------------------------------------------


def test_answers_hold_meanings_indices_and_heldout_sets(a_books: dict[str, Any]):
    root = a_books["P1"]["result"].path
    answers = read_json(root / "answers.json")
    assert list(schema_validator_def("answers").iter_errors(answers)) == []
    assert answers["hidden_answer"] is True and answers["demo"] is True
    assert {a["atom_id"]: a["semantic_label"] for a in answers["atoms"]} == A_LABELS
    assert {a["atom_id"]: a["wave"] for a in answers["atoms"]} == dict(ATOM_WAVES)
    perm = read_json(A_UNIT / "permutation.json")
    by_id = {m["message_id"]: m for m in perm["messages"]}
    for msg in answers["messages"]:
        ref = parse_message_id(msg["message_id"])
        p = by_id[msg["message_id"]]
        assert (msg["semantic_action"], msg["semantic_referent"]) == (
            p["semantic_action"],
            p["semantic_referent"],
        )
        assert (msg["action_index"], msg["referent_index"]) == (
            ref.action_index,
            ref.referent_index,
        )
        assert (msg["status"], msg["training_wave"], msg["heldout_set"]) == (
            p["status"],
            p["training_wave"],
            p["heldout_set"],
        )
    assert answers["trained_message_ids"] == list(TRAINED_MESSAGE_IDS)
    assert answers["heldout_message_ids"] == list(HELDOUT_MESSAGE_IDS)
    sizes = {k: len(v) for k, v in answers["heldout_sets"].items()}
    assert sizes == {"H-V1": 2, "H-V2": 2, "H-V3": 2, "H-W1": 4, "H-W4": 4}


def test_package_carries_no_method_designer_rating_or_store_provenance(a_books: dict[str, Any]):
    entry = a_books["P3"]
    root = entry["result"].path
    sources = {e.source for e in entry["store"].list(entry["book"])}
    for path in root.glob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert not any(s in text for s in sources), path.name
        for word in ("source", "designer", "method", "rating", "recipe", "timestamp", "slot"):
            assert f'"{word}' not in text, (path.name, word)
    assert read_json(root / "manifest.json")["package_id"] == "DEMO-BOOK-P3"


def schema_validator_def(name: str) -> Any:
    return package_mod._validator(name)


# --- Leak scan positive controls -------------------------------------------------------


def test_leak_scan_finds_a_planted_heldout_wav(pkg_a: Path):
    (pkg_a / "messages" / "K-a1-r2.wav").write_bytes(wav_bytes(heldout_pcm(pkg_a, "K-a1-r2")))
    report = scan_package(pkg_a)
    assert not report.ok
    assert report.codes == ("E_HELDOUT_AUDIO",)
    assert report.heldout_audio == 2  # PCM hash and embedded message
    assert {f.path for f in report.findings} == {"messages/K-a1-r2.wav"}
    with pytest.raises(PackageIntegrityError) as err:
        load_package(pkg_a)
    assert err.value.codes == ("E_FILE_EXTRA",)


def test_leak_scan_finds_heldout_audio_in_any_container(pkg_a: Path):
    pcm = heldout_pcm(pkg_a, "Q-a4-r4")
    (pkg_a / "raw.pcm").write_bytes(pcm)  # file hash = composite hash
    (pkg_a / "padded.bin").write_bytes(b"\x07" * 101 + pcm + b"\x01")  # odd offset
    report = scan_package(pkg_a)
    pairs = {(f.path, f.code) for f in report.findings}
    assert pairs == {
        ("raw.pcm", "E_HELDOUT_AUDIO"),
        ("padded.bin", "E_HELDOUT_AUDIO"),
        ("raw.pcm", "E_UNREADABLE"),  # neither a canonical WAV nor text
        ("padded.bin", "E_UNREADABLE"),
    }
    messages = [f.message for f in report.findings if f.code == "E_HELDOUT_AUDIO"]
    assert sorted(messages) == [
        "contains held-out message Q-a4-r4 as audio",
        "contains held-out message Q-a4-r4 as audio",
        "file hash equals a held-out composite hash",
    ]


def test_leak_scan_flags_unlisted_message_audio(pkg_a: Path):
    trained = (pkg_a / "messages" / "K-a1-r1.wav").read_bytes()
    (pkg_a / "messages" / "copy.wav").write_bytes(trained)
    (pkg_a / "atoms" / "long.wav").write_bytes(wav_bytes(bytes(2 * 60_000)))
    report = scan_package(pkg_a)
    assert sorted((f.path, f.code) for f in report.findings) == [
        ("atoms/long.wav", "E_MESSAGE_AUDIO"),
        ("messages/copy.wav", "E_MESSAGE_AUDIO"),
        ("messages/copy.wav", "E_MESSAGE_AUDIO"),
    ]
    assert report.heldout_audio == 3 and report.method_strings == 0


def test_leak_scan_rejects_noncanonical_wavs(pkg_a: Path):
    data = (pkg_a / "atoms" / "K-a1.wav").read_bytes()
    riff = data[:4] + struct.pack("<I", len(data) - 8 + 12) + data[8:36]
    chunk = b"LIST" + struct.pack("<I", 4) + b"INFO"
    (pkg_a / "atoms" / "K-a1.wav").write_bytes(riff + chunk + data[36:])
    report = scan_package(pkg_a)
    assert set(report.codes) == {"E_WAV_FORMAT", "E_UNREADABLE"}


@pytest.mark.parametrize(
    ("content", "code"),
    [
        ({"source": "x"}, "E_SOURCE_KEY"),
        ({"note": "made by A2"}, "E_METHOD_LABEL"),
        ({"batch": ["D3"]}, "E_DESIGNER_ID"),
        ({"pipeline": "Transformer proposals"}, "E_METHOD_WORD"),
        ({"x": "hand-designed"}, "E_METHOD_WORD"),
        ({"ratings": [1, 2]}, "E_METHOD_WORD"),
        ({"x": "slot-K-a1-x"}, "E_FORBIDDEN_STRING"),
    ],
)
def test_leak_scan_finds_method_and_designer_strings(pkg_a: Path, content: Any, code: str):
    (pkg_a / "notes.json").write_text(json.dumps(content), encoding="utf-8")
    report = scan_package(pkg_a, forbidden_strings=["slot-K-a1-x", "x"])
    assert report.codes == (code,)
    assert report.method_strings >= 1 and report.heldout_audio == 0


def test_leak_scan_accepts_ids_visits_and_labels(pkg_a: Path):
    text = {"ids": ["K-a1-r2", "Q-r3", "A-C01-L01-D0-TR-01", "D0", "D7", "H-W1", "P3", "A", "D"]}
    (pkg_a / "notes.json").write_text(json.dumps(text), encoding="utf-8")
    (pkg_a / "notes.txt").write_text("K-a3-r2 at D7\n", encoding="utf-8")
    assert scan_package(pkg_a).ok


def test_leak_scan_reads_text_and_binary_files(pkg_a: Path):
    (pkg_a / "notes.txt").write_text("A1 was here", encoding="utf-8")
    (pkg_a / "blob.dat").write_bytes(b"\xff\xfe\x00")
    (pkg_a / "bad.json").write_bytes(b"{")
    report = scan_package(pkg_a)
    assert sorted((f.path, f.code) for f in report.findings) == [
        ("bad.json", "E_UNREADABLE"),
        ("blob.dat", "E_UNREADABLE"),
        ("notes.txt", "E_METHOD_LABEL"),
    ]
    assert report.to_dict()["ok"] is False


def test_leak_scan_needs_the_audio_index(pkg_a: Path):
    (pkg_a / "audio.json").unlink()
    with pytest.raises(PackageError) as err:
        scan_package(pkg_a)
    assert err.value.code == "E_INPUT"
    (pkg_a / "audio.json").write_text("{}", encoding="utf-8")
    with pytest.raises(PackageError) as err:
        scan_package(pkg_a)
    assert err.value.code == "E_SCHEMA"


def test_leak_scan_reports_unreadable_atoms(pkg_a: Path):
    (pkg_a / "atoms" / "K-a1.wav").unlink()
    report = scan_package(pkg_a)
    assert ("atoms/K-a1.wav", "E_UNREADABLE") in [(f.path, f.code) for f in report.findings]


# --- Build refusals ---------------------------------------------------------------------


def test_build_refuses_unsuitable_books(tmp_path: Path):
    store = VocabularyStore(tmp_path / "store", clock=tool.fixed_clock(), reserved=())
    recipes = synthetic_recipes(Profile.P1)
    store.create_book("DEMO-OPEN", Profile.P1, kind="synthetic")
    for atom in ATOM_IDS:
        store.commit("DEMO-OPEN", atom, A_LABELS[atom], recipes[atom], source="s")
    store.create_book("DEMO-HALF", Profile.P1, kind="synthetic")
    for atom in ATOM_IDS[:8]:
        store.commit("DEMO-HALF", atom, A_LABELS[atom], recipes[atom], source="s")
    store.freeze("DEMO-HALF")
    store.create_book("FB0001", Profile.P1, kind="fallback")
    store.freeze("FB0001")
    try:
        for book, text in (
            ("DEMO-OPEN", "not frozen"),
            ("DEMO-HALF", "8 atoms"),
            ("FB0001", "fallback"),
            ("DEMO-NONE", "no book"),
        ):
            with pytest.raises(PackageError) as err:
                build_package(store, book, tmp_path / "out")
            assert err.value.code == "E_BOOK" and text in str(err.value), book
        store.freeze("DEMO-OPEN")
        with pytest.raises(PackageError) as err:
            build_package(store, "DEMO-OPEN", tmp_path / "out", expected_head="0" * 64)
        assert err.value.code == "E_BOOK" and "freeze record" in str(err.value)
        assert not (tmp_path / "out").exists()
        (tmp_path / "busy").mkdir()
        (tmp_path / "busy" / "x").write_text("x", encoding="utf-8")
        with pytest.raises(PackageError) as err:
            build_package(store, "DEMO-OPEN", tmp_path / "busy")
        assert err.value.code == "E_INPUT"
        (tmp_path / "empty").mkdir()
        assert build_package(store, "DEMO-OPEN", tmp_path / "empty").path == tmp_path / "empty"
        assert not list(tmp_path.glob(".*.partial"))
    finally:
        make_writable(tmp_path)


def test_package_records_the_frozen_head_and_ignores_later_log_lines(tmp_path: Path):
    store = VocabularyStore(tmp_path / "store", clock=tool.fixed_clock(), reserved=())
    store.create_book("DEMO-FRZ-P1", Profile.P1, kind="synthetic")
    recipes = synthetic_recipes(Profile.P1)
    for atom in ATOM_IDS:
        store.commit("DEMO-FRZ-P1", atom, A_LABELS[atom], recipes[atom], source="s-" + atom)
    frozen_head = store.freeze("DEMO-FRZ-P1")
    try:
        first = build_package(store, "DEMO-FRZ-P1", tmp_path / "one", expected_head=frozen_head)
        assert first.manifest["book"]["frozen_head"] == frozen_head
        with pytest.raises(BookFrozen):  # logged as commit_rejected_frozen: the head moves
            store.commit("DEMO-FRZ-P1", "K-a1", A_LABELS["K-a1"], recipes["K-a1"], source="late")
        assert store.head("DEMO-FRZ-P1") != frozen_head
        again = build_package(store, "DEMO-FRZ-P1", tmp_path / "two", expected_head=frozen_head)
        assert again.package_sha256 == first.package_sha256
        snapshot = first.manifest["book"]["snapshot_sha256"]
        assert snapshot == snapshot_digest(store.snapshot_hashes("DEMO-FRZ-P1"))
    finally:
        make_writable(tmp_path)


def test_build_refuses_a_void_book(a_books: dict[str, Any], tmp_path: Path, monkeypatch: Any):
    """Superseded books get a `void` event in the store (#11); never package them."""
    entry = a_books["P2"]
    store: VocabularyStore = entry["store"]
    records = store.records(entry["book"])
    void = {**records[-1], "event": "void"}
    monkeypatch.setattr(store, "records", lambda book_id: [*records, void])
    with pytest.raises(PackageError) as err:
        build_package(store, entry["book"], tmp_path / "out")
    assert err.value.code == "E_BOOK" and "void" in str(err.value)


def test_build_refuses_a_damaged_store_book(a_books: dict[str, Any], tmp_path: Path):
    entry = a_books["P1"]
    store: VocabularyStore = entry["store"]
    log = store.log_path(entry["book"])
    original = log.read_bytes()
    os.chmod(log, stat.S_IWRITE | stat.S_IREAD)
    try:
        log.write_bytes(original.replace(b'"seq":3', b'"seq":4', 1))
        with pytest.raises(PackageError) as err:
            build_package(store, entry["book"], tmp_path / "out")
        assert err.value.code == "E_BOOK"
    finally:
        log.write_bytes(original)


def test_study_packages_stay_out_of_the_repository(study_store: tuple[VocabularyStore, str]):
    store, book = study_store
    target = REPO / "sound" / "examples" / "never-written"
    with pytest.raises(PackageError) as err:
        build_package(store, book, target)
    assert err.value.code == "E_POLICY"
    assert not target.exists()


def test_build_cleans_up_after_a_failure(
    a_books: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("disk full")

    monkeypatch.setattr(package_mod, "write_message_wav", boom)
    entry = a_books["P1"]
    with pytest.raises(RuntimeError):
        build_package(entry["store"], entry["book"], tmp_path / "out")
    assert sorted(p.name for p in tmp_path.iterdir()) == []


def test_build_fails_when_the_leak_scan_fails(
    a_books: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(package_mod, "METHOD_WORDS", ("atom",))
    entry = a_books["P1"]
    with pytest.raises(PackageError) as err:
        build_package(entry["store"], entry["book"], tmp_path / "out")
    assert err.value.code == "E_LEAK"
    assert set(err.value.codes) == {"E_METHOD_WORD"}
    assert not (tmp_path / "out").exists()


# --- seal(): reserved slots --------------------------------------------------------------


def test_seal_adds_the_permutation_and_recomputes_the_hash(pkg_a: Path, a_books: dict[str, Any]):
    before = read_json(pkg_a / "manifest.json")
    new_hash = seal(pkg_a, permutation=A_UNIT / "permutation.json")
    after = read_json(pkg_a / "manifest.json")
    assert new_hash == after["package_sha256"] != before["package_sha256"]
    assert new_hash == package_sha256(after)
    assert (pkg_a / "permutation.json").read_bytes() == (A_UNIT / "permutation.json").read_bytes()
    assert set(after["files"]) - set(before["files"]) == {"permutation.json"}
    pkg = load_package(pkg_a)
    assert pkg.permutation is not None and pkg.permutation["unit_id"] == "A-C01"
    # Idempotent; bytes instead of a path give the same result.
    assert seal(pkg_a, permutation=(A_UNIT / "permutation.json").read_bytes()) == new_hash
    assert seal(pkg_a) == new_hash
    with pytest.raises(PackageError) as err:
        seal(pkg_a, permutation=real_permutation())
    assert err.value.code == "E_SLOT"


def test_seal_with_schedules_and_allocation(pkg_a: Path):
    sealed = seal(
        pkg_a,
        permutation=A_UNIT / "permutation.json",
        schedules=A_UNIT / "schedules",
        allocation_extras={"swap_w1_w4": False},
    )
    pkg = load_package(pkg_a)
    assert pkg.package_sha256 == sealed
    assert sorted(pkg.schedules) == [
        "schedules/A-C01-L01/D0.json",
        "schedules/A-C01-L01/D7.json",
    ]
    assert pkg.allocation is not None
    assert pkg.allocation["novel_by_visit"] == novel_by_visit("A", False)
    assert scan_package(pkg_a).ok
    assert len(pkg.files) == 16 + 18 + 2 + 1 + 1 + 2


def test_sealing_is_reproducible_and_order_independent(a_books: dict[str, Any], tmp_path: Path):
    hashes = set()
    for order in ("one", "steps"):
        target = tmp_path / order
        shutil.copytree(a_books["P1"]["result"].path, target)
        schedules = {
            "A-C01-L01/D0.json": A_UNIT / "schedules" / "A-C01-L01" / "D0.json",
            "A-C01-L01/D7.json": (A_UNIT / "schedules" / "A-C01-L01" / "D7.json").read_bytes(),
        }
        if order == "one":
            h = seal(
                target,
                permutation=A_UNIT / "permutation.json",
                schedules=schedules,
                allocation_extras={"swap_w1_w4": False},
            )
        else:
            seal(target, permutation=A_UNIT / "permutation.json")
            seal(target, allocation_extras={"swap_w1_w4": False})
            h = seal(target, schedules=schedules)
        hashes.add(h)
    assert len(hashes) == 1


def test_seal_refuses_a_demo_permutation_for_a_participant_package(
    study_store: tuple[VocabularyStore, str], tmp_path: Path
):
    store, book = study_store
    result = build_package(store, book, tmp_path / "pkg")
    assert result.manifest["demo"] is False
    with pytest.raises(PackageError) as err:
        seal(result.path, permutation=A_UNIT / "permutation.json")
    assert err.value.code == "E_DEMO"
    assert not (result.path / "permutation.json").exists()
    assert seal(result.path, permutation=real_permutation()) != result.package_sha256
    with pytest.raises(PackageError) as err:
        seal(result.path, schedules=A_UNIT / "schedules")
    assert "E_DEMO" in err.value.codes
    assert scan_package(result.path).ok


def test_demo_package_accepts_demo_and_real_permutations(pkg_a: Path):
    seal(pkg_a, permutation=real_permutation())
    assert load_package(pkg_a).permutation is not None


def test_permutation_matrix_equals_the_grammar_matrix():
    """Cross-check deferred from #10: av_sound.grammar vs the #29 permutation documents."""
    for unit in (A_UNIT, B_UNIT):
        doc = read_json(unit / "permutation.json")
        assert doc["format"] == "av-schedules/permutation" and doc["format_version"] == 2
        assert doc["demo"] is True
        assert permutation_matrix(doc) == {"K": MATRIX, "Q": MATRIX}
        trained = sorted(m["message_id"] for m in doc["messages"] if m["status"] == "trained")
        assert trained == sorted(TRAINED_MESSAGE_IDS)
        waves = {a["atom_id"]: a["matrix_wave"] for a in doc["atoms"]}
        assert waves == dict(ATOM_WAVES)


def mutate_permutation(change: Callable[[dict[str, Any]], None], unit: Path = A_UNIT) -> bytes:
    doc = read_json(unit / "permutation.json")
    change(doc)
    return package_mod._json_bytes(doc)


def _swap_cells(doc: dict[str, Any]) -> None:
    msgs = {m["message_id"]: m for m in doc["messages"]}
    a, b = msgs["K-a1-r1"], msgs["K-a1-r2"]
    for key in ("status", "training_wave", "heldout_set"):
        a[key], b[key] = b[key], a[key]


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (_swap_cells, "E_MATRIX"),
        (lambda d: d["atoms"][0].__setitem__("matrix_wave", 3), "E_MATRIX"),
        (lambda d: d["labels"]["K"]["action"].reverse(), "E_PERMUTATION"),
        (lambda d: d["atoms"][0].__setitem__("semantic_label", "ZZZ"), "E_PERMUTATION"),
        (lambda d: d["messages"][0].__setitem__("semantic_action", "TAG"), "E_PERMUTATION"),
        (lambda d: d.__setitem__("study", "B"), "E_PERMUTATION"),
        (lambda d: d.__setitem__("format_version", 1), "E_PERMUTATION"),
        (lambda d: d.__setitem__("demo", "yes"), "E_PERMUTATION"),
        (lambda d: d["messages"].pop(), "E_PERMUTATION"),
        (lambda d: d["messages"][0].__setitem__("status", "maybe"), "E_PERMUTATION"),
        (lambda d: d["messages"][1].__setitem__("message_id", "K-a1-r1"), "E_PERMUTATION"),
        (lambda d: d["messages"][0].__setitem__("family", "Q"), "E_PERMUTATION"),
        (lambda d: d["atom_order"].pop(), "E_PERMUTATION"),
        (lambda d: d.__setitem__("wave_atom_order", {}), "E_PERMUTATION"),
        (lambda d: d.pop("labels"), "E_PERMUTATION"),
    ],
)
def test_seal_refuses_inconsistent_permutations(pkg_a: Path, change: Any, code: str):
    before = (pkg_a / "manifest.json").read_bytes()
    with pytest.raises(PackageError) as err:
        seal(pkg_a, permutation=mutate_permutation(change))
    assert err.value.code == code, err.value.problems
    assert (pkg_a / "manifest.json").read_bytes() == before
    assert not (pkg_a / "permutation.json").exists()


def test_seal_refuses_non_objects_and_bad_json(pkg_a: Path):
    for data, code in ((b"[]", "E_PERMUTATION"), (b"{", "E_JSON")):
        with pytest.raises(PackageError) as err:
            seal(pkg_a, permutation=data)
        assert err.value.code == code
    with pytest.raises(TypeError):
        seal(pkg_a, permutation=42)  # type: ignore[arg-type]


def test_seal_refuses_a_permutation_with_other_meanings(a_books: dict[str, Any], tmp_path: Path):
    store = book_store(tmp_path / "store", Profile.P1, "DEMO-ID-P1", labels=identity_labels())
    try:
        result = build_package(store, "DEMO-ID-P1", tmp_path / "pkg")
    finally:
        make_writable(tmp_path)
    with pytest.raises(PackageError) as err:
        seal(result.path, permutation=A_UNIT / "permutation.json")
    assert err.value.code == "E_PERMUTATION"
    with pytest.raises(PackageError) as err:
        seal(result.path, permutation=B_UNIT / "permutation.json")
    assert err.value.code == "E_PERMUTATION"


def mutate_schedule(visit: str, change: Callable[[dict[str, Any]], None]) -> bytes:
    doc = read_json(A_UNIT / "schedules" / "A-C01-L01" / f"{visit}.json")
    change(doc)
    return package_mod._json_bytes(doc)


def _lesson(doc: dict[str, Any]) -> dict[str, Any]:
    block = next(b for b in doc["blocks"] if b["block"] == "message_lessons")
    return dict(block["items"][0])


def _set_lesson(doc: dict[str, Any], **values: Any) -> None:
    block = next(b for b in doc["blocks"] if b["block"] == "message_lessons")
    block["items"][0].update(values)


def _novel(doc: dict[str, Any], **values: Any) -> None:
    block = next(b for b in doc["blocks"] if b["block"] == "novel")
    block["items"][0].update(values)


def _trained_item(doc: dict[str, Any], **values: Any) -> None:
    block = next(b for b in doc["blocks"] if b["block"] == "trained")
    block["items"][0].update(values)


@pytest.mark.parametrize(
    ("change", "text"),
    [
        (lambda d: _set_lesson(d, message_id="K-a1-r2"), "held-out message K-a1-r2"),
        (lambda d: d["dictionary_messages"].append("K-a2-r3"), "dictionary_messages"),
        (lambda d: _novel(d, message_id="K-a1-r1"), "plays trained message"),
        (lambda d: _novel(d, message_id="K-a1-r2"), "not due at this visit"),
        (lambda d: _set_lesson(d, presentation="dictionary"), "presentation"),
        (
            lambda d: _set_lesson(
                d, intended={**_lesson(d)["intended"], "semantic_action": "SCAN"}
            ),
            "differs from answers.json",
        ),
        (
            lambda d: next(b for b in d["blocks"] if b["block"] == "atomic")["items"][0][
                "intended"
            ].__setitem__("semantic_label", "Z"),
            "differs from answers.json",
        ),
        (lambda d: d.__setitem__("permutation_json_sha256", "0" * 64), "permutation_json_sha256"),
        (lambda d: d.__setitem__("swap_w1_w4", True), "swap_w1_w4"),
        (lambda d: d.__setitem__("unit_id", "A-C02"), "not in unit"),
        (lambda d: d.__setitem__("person_id", "A-C01-L02"), "path does not match"),
        (lambda d: d.__setitem__("visit", "V1"), "not a Study A visit"),
        (lambda d: d.__setitem__("study", "B"), "study 'B'"),
        (lambda d: d.__setitem__("hidden_answer", False), "hidden_answer"),
        (lambda d: d.pop("blocks"), "malformed"),
        (lambda d: _trained_item(d, trial_type="novel"), "plays trained message"),
    ],
)
def test_seal_refuses_inconsistent_schedules(pkg_a: Path, change: Any, text: str):
    seal(pkg_a, permutation=A_UNIT / "permutation.json", allocation_extras={"swap_w1_w4": False})
    before = (pkg_a / "manifest.json").read_bytes()
    with pytest.raises(PackageError) as err:
        seal(pkg_a, schedules={"A-C01-L01/D0.json": mutate_schedule("D0", change)})
    assert err.value.code == "E_SCHEDULE", err.value.problems
    assert text in str(err.value), err.value.problems
    assert (pkg_a / "manifest.json").read_bytes() == before


def test_schedules_need_the_permutation_and_valid_paths(pkg_a: Path, tmp_path: Path):
    with pytest.raises(PackageError) as err:
        seal(pkg_a, schedules=A_UNIT / "schedules")
    assert "need the package's permutation.json" in str(err.value)
    for bad in ({"D0.json": b"{}"}, {"A-C01-L01/D3.json": b"{}"}):
        with pytest.raises(PackageError) as err:
            seal(pkg_a, schedules=bad)
        assert err.value.code == "E_SCHEDULE"
    with pytest.raises(PackageError):
        seal(pkg_a, schedules={"A-C01-L01/D0.json": b"{"})
    with pytest.raises(PackageError):
        seal(pkg_a, schedules=tmp_path / "missing")
    with pytest.raises(PackageError) as err:
        seal(pkg_a, permutation=A_UNIT / "permutation.json", schedules={"A-C01-L01/D0.json": b"[]"})
    assert "not a JSON object" in str(err.value)


def test_swap_must_agree_between_allocation_and_schedules(pkg_a: Path):
    with pytest.raises(PackageError) as err:
        seal(
            pkg_a,
            permutation=A_UNIT / "permutation.json",
            schedules=A_UNIT / "schedules",
            allocation_extras={"swap_w1_w4": True},
        )
    assert err.value.code == "E_SCHEDULE"
    assert "swap_w1_w4 differs" in str(err.value)
    assert "not due at this visit" in str(err.value)


@pytest.mark.parametrize(
    "extras",
    [
        {},
        {"swap_w1_w4": 1},
        {"swap_w1_w4": False, "designer": "x"},
        {"swap_w1_w4": False, "structured_family": "K"},
    ],
)
def test_allocation_extras_are_closed(pkg_a: Path, extras: dict[str, Any]):
    with pytest.raises(PackageError) as err:
        seal(pkg_a, allocation_extras=extras)
    assert err.value.code == "E_ALLOCATION"


def test_loader_rechecks_a_forged_allocation(pkg_a: Path):
    seal(pkg_a, allocation_extras={"swap_w1_w4": True})
    doc = read_json(pkg_a / "allocation.json")
    doc["swap_w1_w4"] = False
    data = package_mod._json_bytes(doc)
    (pkg_a / "allocation.json").write_bytes(data)
    rewrite_manifest(
        pkg_a,
        lambda m: m["files"].__setitem__(
            "allocation.json", {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        ),
    )
    with pytest.raises(PackageIntegrityError) as err:
        load_package(pkg_a)
    assert err.value.codes == ("E_ALLOCATION",)


def test_novel_by_visit_follows_the_swap():
    a, a_swapped = novel_by_visit("A", False), novel_by_visit("A", True)
    assert a["D0"] == ["K-a1-r4", "K-a3-r2", "Q-a1-r4", "Q-a3-r2"]  # H-W1
    assert a["D7"] == ["K-a2-r1", "K-a4-r4", "Q-a2-r1", "Q-a4-r4"]  # H-W4
    assert (a_swapped["D0"], a_swapped["D7"]) == (a["D7"], a["D0"])
    b, b_swapped = novel_by_visit("B", False), novel_by_visit("B", True)
    assert [len(b[v]) for v in ("V1", "V2", "V3", "W1", "W4")] == [2, 2, 2, 4, 4]
    assert b["V1"] == b_swapped["V1"] == ["K-a1-r2", "Q-a1-r2"]
    assert (b_swapped["W1"], b_swapped["W4"]) == (b["W4"], b["W1"])
    every = sorted(m for v in ("V1", "V2", "V3", "W1", "W4") for m in b[v])
    assert every == sorted(HELDOUT_MESSAGE_IDS)
    with pytest.raises(ValueError):
        novel_by_visit("C", False)


def test_atom_waves_follow_the_matrix():
    expected = {1: 1, 2: 1, 3: 2, 4: 3}
    for atom, wave in ATOM_WAVES.items():
        assert wave == expected[int(atom[-1])]
    assert sum(w == 1 for w in ATOM_WAVES.values()) == 8


# --- Study B dyad package (PROVISIONAL bank input) ---------------------------------------


def test_dyad_package_contents(dyad: dict[str, Any]):
    result: PackageResult = dyad["result"]
    root = result.path
    for profile in ("P1", "P2", "P3"):
        names = wav_files(root / "options", profile)
        assert len(names) == 64
        assert names == sorted(f"{a}-{r}.wav" for a in ATOM_IDS for r in range(1, 5))
    assert not (root / "messages").exists() and not (root / "atoms").exists()
    files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    assert len(files) == 192 + 3
    audio = read_json(root / "audio.json")
    assert [(w["visit"], len(w["atoms"])) for w in audio["waves"]] == [
        ("V1", 8),
        ("V2", 4),
        ("V3", 4),
    ]
    assert sum(o["menu"] == "reserve" for o in audio["options"]) == 48
    combos = [c for m in audio["messages"] for c in m["combinations"]]
    assert len(combos) == 3 * 32 * 16 == 1_536
    assert result.leak_report.ok and result.leak_report.heldout_hashes == 672
    manifest = result.manifest
    assert manifest["bank"]["bank_sha256"] == dyad["bank"].bank_sha256()
    assert "profile" not in manifest and "book" not in manifest
    answers = read_json(root / "answers.json")
    assert {a["atom_id"]: a["semantic_label"] for a in answers["atoms"]} == B_LABELS


def test_dyad_combinations_match_the_composer(dyad: dict[str, Any]):
    root = dyad["result"].path
    pkg = load_package(root)
    assert pkg.combinations_checked == 1_536
    options = {
        (o["profile"], o["atom_id"], o["rank"]): AtomAudio(
            o["atom_id"], o["profile"], pcm_from_wav((root / o["path"]).read_bytes())
        )
        for o in pkg.audio["options"]
    }
    lengths = set()
    for msg in pkg.audio["messages"]:
        for c in msg["combinations"]:
            action = options[(c["profile"], msg["action_atom"], c["action_rank"])]
            referent = options[(c["profile"], msg["referent_atom"], c["referent_rank"])]
            assert c["composite_sha256"] == composite_hash(action, referent)
            assert c["n_samples"] == message_length(action, referent) == 48 * c["duration_ms"]
            lengths.add(c["duration_ms"])
    assert lengths == {1100, 1250, 1400, 1550, 1700, 1850, 2000}


def test_dyad_package_has_no_bank_provenance(dyad: dict[str, Any]):
    root = dyad["result"].path
    sources = {o.source for opts in dyad["bank"].cells.values() for o in opts}
    assert len(sources) == 192
    for path in root.glob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert not any(s in text for s in sources if s)
        assert '"source"' not in text and '"recipe"' not in text


def test_dyad_rebuild_reproduces_the_package_hash(dyad: dict[str, Any], tmp_path: Path):
    again = build_dyad_package(synthetic_dyad_bank("DEMO-DYAD-01", labels=B_LABELS), tmp_path / "b")
    assert again.package_sha256 == dyad["result"].package_sha256


def test_dyad_seal_with_permutation_schedules_and_allocation(pkg_b: Path):
    sealed = seal(
        pkg_b,
        permutation=B_UNIT / "permutation.json",
        schedules=B_UNIT / "schedules",
        allocation_extras={"swap_w1_w4": True, "structured_family": "Q"},
    )
    pkg = load_package(pkg_b)
    assert pkg.package_sha256 == sealed
    assert pkg.allocation is not None and pkg.allocation["structured_family"] == "Q"
    assert pkg.allocation["novel_by_visit"]["W1"] == novel_by_visit("B", True)["W1"]
    assert len(pkg.schedules) == 3
    assert scan_package(pkg_b).ok


def test_dyad_seal_checks_scaffold_and_swap(pkg_b: Path):
    with pytest.raises(PackageError) as err:
        seal(
            pkg_b,
            permutation=B_UNIT / "permutation.json",
            schedules=B_UNIT / "schedules",
            allocation_extras={"swap_w1_w4": True, "structured_family": "K"},
        )
    assert "presentation" in str(err.value)
    with pytest.raises(PackageError) as err:
        seal(
            pkg_b,
            permutation=B_UNIT / "permutation.json",
            schedules=B_UNIT / "schedules",
            allocation_extras={"swap_w1_w4": False},
        )
    assert "swap_w1_w4 differs" in str(err.value)
    bad = mutate_permutation(lambda d: d["wave_atom_order"]["1"].reverse(), B_UNIT)
    with pytest.raises(PackageError) as err:
        seal(pkg_b, permutation=bad)
    assert "atom_order is not the wave orders" in str(err.value)
    bad = mutate_permutation(
        lambda d: d["wave_atom_order"].__setitem__("2", [*d["wave_atom_order"]["2"][:3], "K-a4"]),
        B_UNIT,
    )
    with pytest.raises(PackageError) as err:
        seal(pkg_b, permutation=bad)
    assert "wave_atom_order 2" in str(err.value)
    with pytest.raises(PackageError) as err:
        seal(pkg_b, permutation=A_UNIT / "permutation.json")
    assert err.value.code == "E_PERMUTATION"


def test_dyad_leak_scan_flags_any_message_audio(pkg_b: Path):
    def option(rank: int, atom: str) -> bytes:
        return pcm_from_wav((pkg_b / "options" / "P2" / f"{atom}-{rank}.wav").read_bytes())

    trained = option(1, "K-a1") + bytes(2 * GAP_SAMPLES) + option(3, "K-r1")
    heldout = option(4, "Q-a2") + bytes(2 * GAP_SAMPLES) + option(2, "Q-r1")
    (pkg_b / "trained.wav").write_bytes(wav_bytes(trained))
    (pkg_b / "heldout.wav").write_bytes(wav_bytes(heldout))
    report = scan_package(pkg_b)
    codes = sorted({(f.path, f.code) for f in report.findings})
    assert codes == [("heldout.wav", "E_HELDOUT_AUDIO"), ("trained.wav", "E_MESSAGE_AUDIO")]


def test_dyad_loader_rejects_an_altered_option(pkg_b: Path):
    flip_byte(pkg_b / "options" / "P3" / "Q-r4-4.wav")
    with pytest.raises(PackageIntegrityError) as err:
        load_package(pkg_b)
    assert err.value.codes == ("E_HASH_MISMATCH",)


def test_dyad_loader_checks_the_audio_index(pkg_b: Path):
    audio = read_json(pkg_b / "audio.json")
    audio["options"][3]["menu"] = "shown"
    audio["waves"][0]["atoms"].reverse()
    audio["messages"][0]["combinations"][0]["duration_ms"] = 2000
    audio["messages"][1]["combinations"].reverse()
    data = package_mod._json_bytes(audio)
    (pkg_b / "audio.json").write_bytes(data)
    rewrite_manifest(
        pkg_b,
        lambda m: m["files"].__setitem__(
            "audio.json", {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        ),
    )
    with pytest.raises(PackageIntegrityError) as err:
        load_package(pkg_b)
    assert set(err.value.codes) == {"E_CONTENT"}
    text = " | ".join(p.message for p in err.value.problems)
    for part in ("menu does not match", "waves differ", "duration_ms", "combinations are not"):
        assert part in text


def test_dyad_builder_checks_the_bank(dyad: dict[str, Any], tmp_path: Path):
    bank: DyadBank = dyad["bank"]
    cells = dict(bank.cells)
    first = cells[("P1", "K-a1")]
    wrong = BankOption(1, first[0].recipe, "0" * 64)
    cells[("P1", "K-a1")] = (wrong, *first[1:])
    with pytest.raises(PackageError) as err:
        build_dyad_package(DyadBank(bank.bank_id, True, bank.labels, cells), tmp_path / "x")
    assert err.value.code == "E_BANK" and "differs from the bank hash" in str(err.value)
    dup = BankOption(2, first[0].recipe, first[0].pcm_sha256)
    cells[("P1", "K-a1")] = (first[0], dup, *first[2:])
    with pytest.raises(PackageError) as err:
        build_dyad_package(DyadBank(bank.bank_id, True, bank.labels, cells), tmp_path / "y")
    assert "same waveform" in str(err.value)
    assert not (tmp_path / "x").exists() and not (tmp_path / "y").exists()


def test_dyad_bank_round_trip_and_schema(dyad: dict[str, Any], tmp_path: Path):
    bank: DyadBank = dyad["bank"]
    doc = bank.to_dict()
    assert list(schema_validator("provisional-bank.schema.json").iter_errors(doc)) == []
    assert DyadBank.from_dict(doc) == bank
    path = tmp_path / "bank.json"
    path.write_bytes(package_mod._json_bytes(doc))
    loaded = load_dyad_bank(path)
    assert loaded == bank and loaded.bank_sha256() == bank.bank_sha256()
    assert bank.bank_sha256() == hashlib.sha256(canonical_json(doc)).hexdigest()
    assert bank.option("P2", "Q-r3", 4).menu == "reserve"
    assert bank.option(Profile.P2, "Q-r3", 1).menu == "shown"
    with pytest.raises(KeyError):
        bank.option("P2", "Q-r3", 5)


@pytest.mark.parametrize(
    ("change", "text"),
    [
        (lambda d: d.__setitem__("bank_id", "DEMO-A1-x"), "method label"),
        (lambda d: d.__setitem__("demo", False), "DEMO banks"),
        (lambda d: d["labels"].__setitem__("K-a1", "REMOVE_ONE"), "not a permutation"),
        (lambda d: d["cells"].pop(), "does not match"),
        (lambda d: d["cells"][1].__setitem__("atom_id", "K-a1"), "duplicate cell"),
        (lambda d: d["cells"][0]["options"].reverse(), "ranks 1-4"),
        (lambda d: d["cells"][0]["options"][0].__setitem__("source", 7), "does not match"),
    ],
)
def test_dyad_bank_validation(dyad: dict[str, Any], change: Any, text: str):
    doc = copy.deepcopy(dyad["bank"].to_dict())
    change(doc)
    with pytest.raises(BankError) as err:
        DyadBank.from_dict(doc)
    assert text in str(err.value)
    assert err.value.code == "E_BANK"


def test_dyad_bank_file_errors(tmp_path: Path):
    path = tmp_path / "bank.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(BankError):
        load_dyad_bank(path)
    path.write_text('{"a": 1, "a": 2}', encoding="utf-8")
    with pytest.raises(BankError):
        load_dyad_bank(path)
    with pytest.raises(BankError):
        synthetic_dyad_bank("BANK-01")
    with pytest.raises(BankError):
        DyadBank("DEMO-X1", True, identity_labels(), {})
    labels = identity_labels()
    labels.pop("K-a1")
    with pytest.raises(BankError):
        DyadBank("DEMO-X1", True, labels, {})


# --- Schema ---------------------------------------------------------------------------


def test_package_documents_match_the_schema(a_books: dict[str, Any], dyad: dict[str, Any]):
    validator = schema_validator("package.schema.json")
    for root in (a_books["P1"]["result"].path, dyad["result"].path):
        manifest = read_json(root / "manifest.json")
        assert list(validator.iter_errors(manifest)) == []
        assert list(schema_validator_def("audio").iter_errors(read_json(root / "audio.json"))) == []
    alloc = package_mod._allocation_doc("B", "DEMO-DYAD-01", {"swap_w1_w4": False})
    assert list(schema_validator_def("allocation").iter_errors(alloc)) == []


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        (
            lambda m: m["files"].__setitem__(
                "messages/K-a1-r2.wav", {"sha256": "0" * 64, "bytes": 1}
            ),
            "files",
        ),
        (lambda m: m["files"].__setitem__("notes.txt", {"sha256": "0" * 64, "bytes": 1}), "files"),
        (
            lambda m: m["files"].__setitem__(
                "options/P1/K-a1-1.wav", {"sha256": "0" * 64, "bytes": 1}
            ),
            "files",
        ),
        (lambda m: m.__setitem__("bank", {}), "bank"),
        (lambda m: m.__setitem__("demo", False), "package_id"),
        (lambda m: m.__setitem__("method", "A3"), "method"),
        (lambda m: m["files"].pop("answers.json"), "answers"),
    ],
)
def test_schema_rejects_heldout_paths_unknown_files_and_mixed_variants(
    a_books: dict[str, Any], change: Any, fragment: str
):
    manifest = copy.deepcopy(read_json(a_books["P1"]["result"].path / "manifest.json"))
    change(manifest)
    errors = list(schema_validator("package.schema.json").iter_errors(manifest))
    assert errors, fragment


def test_schema_documents_both_variants():
    schema = load_schema("package.schema.json")
    assert schema["properties"]["format_version"] == {"const": 1}
    assert len(schema["oneOf"]) == 2
    assert {"answers", "audio", "allocation"} <= set(schema["$defs"])
    trained = schema["$defs"]["path_a"]["pattern"]
    for m in MESSAGES:
        rel = f"messages/{m.message_id}.wav"
        assert bool(re.fullmatch(trained, rel)) is (not m.is_heldout), rel


def test_package_hash_is_the_canonical_json_hash(a_books: dict[str, Any]):
    manifest = a_books["P2"]["result"].manifest
    body = {k: v for k, v in manifest.items() if k != "package_sha256"}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    assert manifest["package_sha256"] == hashlib.sha256(text.encode("ascii")).hexdigest()


# --- Committed synthetic example ---------------------------------------------------------


def test_committed_example_matches_a_rebuild(tmp_path: Path):
    out = tmp_path / "package-demo"
    tool.build_a_example(out)
    for name in tool.COMMITTED:
        assert (EXAMPLE / name).read_bytes() == (out / name).read_bytes(), name
    assert sorted(p.name for p in EXAMPLE.iterdir()) == sorted(tool.COMMITTED)
    pkg: LoadedPackage = load_package(out)
    manifest = read_json(EXAMPLE / "manifest.json")
    assert manifest["package_sha256"] == package_sha256(manifest) == pkg.package_sha256
    assert manifest["demo"] is True and manifest["package_id"].startswith("DEMO-")
    assert (EXAMPLE / "permutation.json").read_bytes() == (A_UNIT / "permutation.json").read_bytes()
    report: LeakReport = scan_package(out)
    assert report.ok


def test_committed_example_lists_its_wavs_by_hash():
    manifest = read_json(EXAMPLE / "manifest.json")
    wavs = [rel for rel in manifest["files"] if rel.endswith(".wav")]
    assert len(wavs) == 34
    assert not list(EXAMPLE.rglob("*.wav"))
    vectors = {b["book_id"]: b for b in read_json(VECTORS)["books"]}["DEMO-P1"]
    files = {a["atom_id"]: a["file_sha256"] for a in vectors["atoms"]}
    for atom in ATOM_IDS:
        assert manifest["files"][f"atoms/{atom}.wav"]["sha256"] == files[atom]


def test_example_tool_check_and_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any):
    monkeypatch.setattr("sys.argv", ["build_example_package.py", "--check"])
    assert tool.main() == 0
    monkeypatch.setattr("sys.argv", ["x", "--out", str(tmp_path), "--dyad"])
    assert tool.main() == 0
    out = capsys.readouterr().out
    assert "DEMO-BOOK-P1" in out and "DEMO-DYAD-01" in out
    assert "message WAVs: 18 trained, 0 held out" in out
    assert "combinations checked (hash and length, no playback): 1536" in out
    monkeypatch.setattr(tool, "EXAMPLE", tmp_path / "copy")
    monkeypatch.setattr("sys.argv", ["x", "--check"])
    assert tool.main() == 1
    monkeypatch.setattr("sys.argv", ["x", "--write"])
    assert tool.main() == 0
    monkeypatch.setattr("sys.argv", ["x", "--check"])
    assert tool.main() == 0
