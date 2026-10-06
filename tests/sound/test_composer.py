"""Message composer tests (issue O4.1.4, sound/docs/composition.md)."""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import itertools
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import av_sound
import av_sound.composer as composer_mod
import av_sound.renderer as renderer_mod
import av_sound.synthetic as synthetic_mod
from av_sound import (
    GAP_SAMPLES,
    MAX_MESSAGE_SAMPLES,
    MIN_MESSAGE_SAMPLES,
    RENDERER_VERSION,
    AtomAudio,
    CompositionError,
    GrammarError,
    HeldOutMessageError,
    Profile,
    Recipe,
    Rendered,
    compose,
    compose_message,
    composite_hash,
    file_sha256,
    message_length,
    read_wav,
    render,
    write_message_wav,
)
from av_sound.composer import (
    E_FAMILY_MISMATCH,
    E_HELDOUT,
    E_INTEGRITY,
    E_MOTIF_LENGTH,
    E_PROFILE_MISMATCH,
    E_ROLE_ORDER,
    MOTIF_SAMPLES,
)
from av_sound.grammar import ATOM_IDS, HELDOUT_MESSAGE_IDS, MESSAGES, TRAINED_MESSAGE_IDS
from av_sound.recipe import TOTAL_MS
from av_sound.synthetic import synthetic_book, synthetic_book_id, synthetic_recipes

SOUND_ROOT = Path(__file__).resolve().parents[2] / "sound"
VECTORS = SOUND_ROOT / "testvectors" / "composition" / "vectors.json"
PROFILES = list(Profile)
SR = 48_000


@pytest.fixture(scope="module")
def books():
    return {p: synthetic_book(p) for p in Profile}


def _atoms(book, m):
    return book[m.action.atom_id], book[m.referent.atom_id]


def _reference_concat(action_pcm: bytes, referent_pcm: bytes) -> bytes:
    """Independent reference: numpy concatenation of int16 samples with 9,600 zeros."""
    parts = [
        np.frombuffer(action_pcm, dtype="<i2"),
        np.zeros(9_600, dtype="<i2"),
        np.frombuffer(referent_pcm, dtype="<i2"),
    ]
    return np.concatenate(parts).astype("<i2").tobytes()


def _pattern(n: int, mul: int, add: int) -> bytes:
    """Pattern rule of composition.md: sample[i] = (mul * i + add) mod 65535 - 32767."""
    return b"".join(
        ((mul * i + add) % 65_535 - 32_767).to_bytes(2, "little", signed=True) for i in range(n)
    )


# --- Synthetic books -------------------------------------------------------------------


def test_synthetic_books_are_in_domain_distinct_and_span_all_lengths():
    hashes = set()
    for profile in Profile:
        recipes = synthetic_recipes(profile)
        assert list(recipes) == list(ATOM_IDS)
        assert synthetic_book_id(profile) == f"DEMO-{profile.value}"
        for atom, recipe in recipes.items():
            r = render(recipe, profile)
            assert not r.short_event and not r.overflow, (profile, atom)
            hashes.add(r.pcm_sha256)
        for family in ("K", "Q"):
            for role in ("a", "r"):
                totals = {recipes[f"{family}-{role}{i}"].total_ms for i in range(1, 5)}
                assert totals == set(TOTAL_MS)
    assert len(hashes) == 48  # distinct within and across the three books


# --- Duration and gap ------------------------------------------------------------------


@pytest.mark.parametrize("profile", PROFILES)
def test_all_32_legal_messages_last_1100_to_2000_ms(books, profile):
    book = books[profile]
    lengths = []
    for m in MESSAGES:
        action, referent = _atoms(book, m)
        n = message_length(action, referent)
        assert n == action.n_samples + GAP_SAMPLES + referent.n_samples
        assert 52_800 <= n <= 96_000
        assert 1.100 <= n / SR <= 2.000
        lengths.append(n)
        if m.message_id in TRAINED_MESSAGE_IDS:
            msg = compose_message(action, referent)
            assert msg.n_samples == n == len(msg.pcm) // 2
            assert 1.100 <= msg.duration_s <= 2.000
    assert len(lengths) == 32
    assert min(lengths) == MIN_MESSAGE_SAMPLES == 52_800
    assert max(lengths) == MAX_MESSAGE_SAMPLES == 96_000


@pytest.mark.parametrize(("t_action", "t_referent"), list(itertools.product(TOTAL_MS, repeat=2)))
def test_every_total_ms_combination(t_action, t_referent):
    n = message_length(t_action, t_referent)
    assert n == (t_action + 200 + t_referent) * 48
    assert 1.100 <= n / SR <= 2.000
    recipe = Recipe(t_action, (0, 2, 4), (1, 1, 2), (20, 20), (1.0, 0.8, 0.6))
    assert message_length(recipe, recipe.to_dict() | {"total_ms": t_referent}) == n


@pytest.mark.parametrize("profile", PROFILES)
def test_gap_is_exactly_9600_zero_samples_in_every_composed_message(books, profile):
    book = books[profile]
    composed = 0
    # Trained messages with the default table, plus every message of this synthetic book
    # with an empty held-out table (synthetic fixtures are not study material).
    for heldout, subset in ((None, TRAINED_MESSAGE_IDS), ((), [m.message_id for m in MESSAGES])):
        for mid in subset:
            m = next(x for x in MESSAGES if x.message_id == mid)
            action, referent = _atoms(book, m)
            msg = compose_message(action, referent, heldout=heldout)
            samples = np.frombuffer(msg.pcm, dtype="<i2")
            na = action.n_samples
            gap = samples[na : na + GAP_SAMPLES]
            assert GAP_SAMPLES == 9_600 and gap.size == 9_600
            assert not gap.any()
            # The motifs' own end and start samples are zero too, so the zero run is
            # longer than the gap: the boundary is positional (composition.md rule 2).
            assert samples[na - 1] == 0 and samples[na + 9_600] == 0
            assert msg.referent_onset == na + 9_600
            assert samples[:na].tobytes() == action.pcm
            assert samples[na + 9_600 :].tobytes() == referent.pcm
            assert msg.pcm == _reference_concat(action.pcm, referent.pcm)
            composed += 1
    assert composed == 18 + 32


@pytest.mark.parametrize("profile", PROFILES)
def test_message_record_and_composite_hash_match_reference(books, profile):
    book = books[profile]
    for m in MESSAGES:
        action, referent = _atoms(book, m)
        expected = hashlib.sha256(_reference_concat(action.pcm, referent.pcm)).hexdigest()
        assert composite_hash(action, referent) == expected
        if m.is_heldout:
            continue
        msg = compose_message(action, referent)
        assert msg.pcm_sha256 == expected
        assert msg.message_id == m.message_id
        assert msg.profile is profile
        assert (msg.action_id, msg.referent_id) == (m.action.atom_id, m.referent.atom_id)
        assert msg.action_pcm_sha256 == action.pcm_sha256
        assert msg.referent_pcm_sha256 == referent.pcm_sha256
        assert (msg.action_samples, msg.referent_samples) == (action.n_samples, referent.n_samples)


# --- Held-out guard --------------------------------------------------------------------


@pytest.mark.parametrize("profile", PROFILES)
def test_every_heldout_id_is_refused_logged_and_writes_nothing(
    books, profile, caplog, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)

    def no_write(*_args, **_kwargs):
        raise AssertionError("the composer tried to write a file")

    monkeypatch.setattr(composer_mod, "write_wav", no_write)
    events = []
    refused = 0
    caplog.set_level(logging.WARNING, logger="av_sound.composer")
    for mid in HELDOUT_MESSAGE_IDS:
        m = next(x for x in MESSAGES if x.message_id == mid)
        action, referent = _atoms(books[profile], m)
        with pytest.raises(HeldOutMessageError) as info:
            compose_message(action, referent, audit=events.append)
        assert info.value.code == E_HELDOUT
        assert info.value.message_id == mid
        assert isinstance(info.value, CompositionError)
        refused += 1
    assert refused == len(HELDOUT_MESSAGE_IDS) == 14  # 100% of held-out IDs
    assert [e["message_id"] for e in events] == list(HELDOUT_MESSAGE_IDS)
    assert all(e["event"] == "heldout_refused" for e in events)
    assert all(e["operation"] == "compose_message" for e in events)
    logged = [r for r in caplog.records if r.name == "av_sound.composer"]
    assert [r.levelno for r in logged] == [logging.WARNING] * 14
    assert all(mid in r.getMessage() for mid, r in zip(HELDOUT_MESSAGE_IDS, logged, strict=True))
    assert list(tmp_path.iterdir()) == []


def test_heldout_refusal_never_reads_samples():
    class NoSamples:
        def __init__(self, atom_id):
            self.atom_id = atom_id
            self.profile = Profile.P1

        @property
        def pcm(self):
            raise AssertionError("samples were read")

    with pytest.raises(HeldOutMessageError):
        compose_message(NoSamples("Q-a2"), NoSamples("Q-r1"))


def test_heldout_table_override(books):
    book = books[Profile.P2]
    k11 = (book["K-a1"], book["K-r1"])  # trained in the fixed matrix
    k12 = (book["K-a1"], book["K-r2"])  # H-V1 in the fixed matrix
    with pytest.raises(HeldOutMessageError):
        compose_message(*k11, heldout={"K-a1-r1"})
    assert compose_message(*k12, heldout=["K-a1-r1"]).message_id == "K-a1-r2"
    assert compose_message(*k11, heldout=frozenset()).message_id == "K-a1-r1"
    with pytest.raises(TypeError):
        compose_message(*k11, heldout="K-a1-r1")
    with pytest.raises(TypeError):
        compose_message(*k11, heldout={"K-a1-r1": "H-V1"})
    with pytest.raises(GrammarError):
        compose_message(*k11, heldout={"K-a1r1"})


def test_write_message_wav_trained_only(books, tmp_path):
    book = books[Profile.P3]
    msg = compose_message(book["Q-a3"], book["Q-r3"])
    path = tmp_path / "Q-a3-r3.wav"
    digest = write_message_wav(msg, path)
    assert read_wav(path) == msg.pcm
    assert digest == file_sha256(msg.pcm) == hashlib.sha256(path.read_bytes()).hexdigest()

    events = []
    forged = dataclasses.replace(msg, message_id="Q-a3-r2")  # H-W1
    with pytest.raises(HeldOutMessageError):
        write_message_wav(forged, tmp_path / "forged.wav", audit=events.append)
    assert events[0]["operation"] == "write_message_wav"
    assert events[0]["message_id"] == "Q-a3-r2"
    tampered = dataclasses.replace(msg, pcm=msg.pcm[:-2] + b"\x01\x00")
    with pytest.raises(CompositionError) as info:
        write_message_wav(tampered, tmp_path / "tampered.wav")
    assert info.value.code == E_INTEGRITY
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Q-a3-r3.wav"]


# --- Structural checks -----------------------------------------------------------------


@pytest.mark.parametrize("fn", [compose_message, composite_hash])
def test_mixing_profiles_families_or_role_order_raises(books, fn):
    p1, p2 = books[Profile.P1], books[Profile.P2]
    cases = [
        ((p1["K-a1"], p2["K-r1"]), E_PROFILE_MISMATCH),
        ((p1["K-a1"], p1["Q-r1"]), E_FAMILY_MISMATCH),
        ((p1["Q-a3"], p1["K-r3"]), E_FAMILY_MISMATCH),
        ((p1["K-r1"], p1["K-a1"]), E_ROLE_ORDER),
        ((p1["K-a1"], p1["K-a3"]), E_ROLE_ORDER),
        ((p1["Q-r1"], p1["Q-r3"]), E_ROLE_ORDER),
    ]
    for (action, referent), code in cases:
        with pytest.raises(CompositionError) as info:
            fn(action, referent)
        assert info.value.code == code, (action.atom_id, referent.atom_id)


@pytest.mark.parametrize("fn", [compose_message, composite_hash])
def test_motif_length_is_checked(fn):
    good = AtomAudio("K-r1", Profile.P1, bytes(2 * 21_600))
    for n in (0, 1, 21_599, 21_601, 50_000):
        bad = AtomAudio("K-a1", Profile.P1, bytes(2 * n))
        with pytest.raises(CompositionError) as info:
            fn(bad, good)
        assert info.value.code == E_MOTIF_LENGTH
    odd = SimpleNamespace(atom_id="K-a1", profile="P1", pcm=bytes(2 * 21_600 + 1))
    with pytest.raises(CompositionError):
        fn(odd, good)
    not_bytes = SimpleNamespace(atom_id="K-a1", profile="P1", pcm=bytearray(2 * 21_600))
    with pytest.raises(TypeError):
        fn(not_bytes, good)


def test_protocol_objects_are_accepted(books):
    book = books[Profile.P1]
    a = SimpleNamespace(atom_id="K-a2", profile="P1", pcm=book["K-a2"].pcm)
    r = SimpleNamespace(atom_id="K-r2", profile=Profile.P1, pcm=book["K-r2"].pcm)
    assert compose_message(a, r).pcm_sha256 == composite_hash(book["K-a2"], book["K-r2"])
    assert compose is compose_message
    assert av_sound.compose is compose_message


def test_atom_audio():
    rendered = render(Recipe(450, (0, 1, 2), (1, 1, 1), (20, 20), (1.0, 0.8, 0.6)), "P2")
    atom = AtomAudio.from_rendered("Q-r4", rendered)
    assert atom.profile is Profile.P2 and atom.n_samples == 21_600
    assert atom.pcm_sha256 == rendered.pcm_sha256
    assert "pcm" not in repr(atom)
    assert AtomAudio("K-a1", "P1", bytearray(4)).pcm == bytes(4)
    with pytest.raises(GrammarError):
        AtomAudio("K-x1", Profile.P1, b"")
    with pytest.raises(ValueError):
        AtomAudio("K-a1", "P4", b"")
    with pytest.raises(ValueError):
        AtomAudio("K-a1", Profile.P1, b"\x00")
    with pytest.raises(TypeError):
        AtomAudio("K-a1", Profile.P1, [0, 0])


# --- Metadata only ---------------------------------------------------------------------


def test_message_length_never_renders(monkeypatch):
    recipe_a = Recipe(900, (1, 2, 3), (1, 2, 1), (40, 40), (0.6, 0.8, 1.0))
    recipe_r = Recipe(450, (3, 2, 1), (1, 1, 1), (20, 20), (1.0, 0.8, 0.6))
    rendered_a, rendered_r = render(recipe_a, "P1"), render(recipe_r, "P1")

    def boom(*_args, **_kwargs):
        raise AssertionError("message_length rendered audio")

    for module in (renderer_mod, av_sound, synthetic_mod):
        monkeypatch.setattr(module, "render", boom)
    monkeypatch.setattr(renderer_mod, "synth_event", boom)
    monkeypatch.setattr(renderer_mod, "normalize", boom)
    monkeypatch.setattr(Rendered, "pcm", property(boom))

    expected = (900 + 200 + 450) * 48
    assert message_length(900, 450) == expected
    assert message_length(recipe_a, recipe_r) == expected
    assert message_length(recipe_a.to_dict(), recipe_r.to_dict()) == expected
    assert message_length(rendered_a, rendered_r) == expected
    entry_a = SimpleNamespace(atom_id="K-a1", recipe=recipe_a)  # store-entry shaped
    entry_r = SimpleNamespace(atom_id="K-r1", recipe=recipe_r.to_dict())
    assert message_length(entry_a, entry_r) == expected
    assert message_length(SimpleNamespace(n_samples=43_200), 450) == expected
    assert message_length(SimpleNamespace(pcm=bytes(2 * 43_200)), 450) == expected
    assert message_length(AtomAudio("K-a1", "P1", bytes(2 * 43_200)), recipe_r) == expected


@pytest.mark.parametrize("bad", [449, 1_000, 21_600, 0, -450])
def test_message_length_rejects_out_of_domain_total_ms(bad):
    with pytest.raises(CompositionError) as info:
        message_length(bad, 450)
    assert info.value.code == E_MOTIF_LENGTH


def test_message_length_rejects_other_inputs():
    with pytest.raises(TypeError):
        message_length(True, 450)
    with pytest.raises(TypeError):
        message_length(object(), 450)
    with pytest.raises(TypeError):
        message_length("450", 450)
    with pytest.raises(CompositionError):
        message_length(SimpleNamespace(n_samples=1_000), 450)
    with pytest.raises(av_sound.RecipeError):
        message_length({"total_ms": 450}, 450)
    assert all(n % 48 == 0 for n in MOTIF_SAMPLES)


# --- Test vectors ----------------------------------------------------------------------


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "make_composition_vectors", SOUND_ROOT / "tools" / "make_composition_vectors.py"
    )
    assert spec and spec.loader
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


def test_composition_vectors_reproduce():
    data = json.loads(VECTORS.read_text(encoding="utf-8"))
    assert data["synthetic"] is True
    assert data["renderer_version"] == RENDERER_VERSION
    assert (data["sample_rate"], data["channels"], data["gap_samples"]) == (48_000, 1, 9_600)
    assert [b["profile"] for b in data["books"]] == ["P1", "P2", "P3"]
    for book_data in data["books"]:
        profile = Profile(book_data["profile"])
        assert book_data["book_id"] == synthetic_book_id(profile)
        recipes = synthetic_recipes(profile)
        atoms = {}
        assert [a["atom_id"] for a in book_data["atoms"]] == list(ATOM_IDS)
        for row in book_data["atoms"]:
            recipe = Recipe.from_dict(row["recipe"])
            assert recipe == recipes[row["atom_id"]]
            assert recipe.sha256() == row["recipe_sha256"]
            r = render(recipe, profile)
            assert (r.n_samples, r.pcm_sha256) == (row["n_samples"], row["pcm_sha256"])
            assert file_sha256(r) == row["file_sha256"]
            atoms[row["atom_id"]] = AtomAudio.from_rendered(row["atom_id"], r)
        assert [m["message_id"] for m in book_data["messages"]] == [m.message_id for m in MESSAGES]
        for row, m in zip(book_data["messages"], MESSAGES, strict=True):
            assert row["heldout"] is m.is_heldout
            assert row["heldout_set"] == m.heldout_set
            assert row["training_wave"] == m.training_wave
            assert row["matrix_status"] == m.status
            action, referent = atoms[row["action_id"]], atoms[row["referent_id"]]
            reference = _reference_concat(action.pcm, referent.pcm)
            assert row["n_samples"] == len(reference) // 2 == message_length(action, referent)
            assert row["duration_ms"] * 48 == row["n_samples"]
            assert 1_100 <= row["duration_ms"] <= 2_000
            assert row["composite_sha256"] == hashlib.sha256(reference).hexdigest()
            assert row["composite_sha256"] == composite_hash(action, referent)
        assert sum(row["heldout"] for row in book_data["messages"]) == 14
    assert len(data["patterns"]) == 4
    for row in data["patterns"]:
        a = _pattern(row["action"]["n_samples"], row["action"]["mul"], row["action"]["add"])
        r = _pattern(row["referent"]["n_samples"], row["referent"]["mul"], row["referent"]["add"])
        assert hashlib.sha256(a).hexdigest() == row["action_pcm_sha256"]
        assert hashlib.sha256(r).hexdigest() == row["referent_pcm_sha256"]
        assert hashlib.sha256(a + bytes(19_200) + r).hexdigest() == row["composite_sha256"]
        assert row["n_samples"] == (len(a) + len(r)) // 2 + 9_600


def test_vector_file_is_what_the_tool_writes():
    tool = _load_tool()
    assert VECTORS.read_bytes() == tool.render_text().encode("utf-8")
    assert tool.main(["--check"]) == 0


def test_tool_writes_atoms_and_trained_messages_only(tmp_path):
    tool = _load_tool()
    assert tool.write_wavs(tmp_path) == 3 * (16 + 18)
    data = json.loads(VECTORS.read_text(encoding="utf-8"))
    for book_data in data["books"]:
        book_dir = tmp_path / book_data["book_id"]
        assert len(list((book_dir / "atoms").iterdir())) == 16
        written = sorted(p.stem for p in (book_dir / "messages").iterdir())
        assert written == sorted(TRAINED_MESSAGE_IDS)
        for row in book_data["messages"]:
            path = book_dir / "messages" / f"{row['message_id']}.wav"
            if row["heldout"]:
                assert not path.exists()
            else:
                pcm = read_wav(path)
                assert hashlib.sha256(pcm).hexdigest() == row["composite_sha256"]
        for row in book_data["atoms"]:
            pcm = read_wav(book_dir / "atoms" / f"{row['atom_id']}.wav")
            assert hashlib.sha256(pcm).hexdigest() == row["pcm_sha256"]
