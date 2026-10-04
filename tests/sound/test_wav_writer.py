"""Canonical WAV writer and hash helpers (renderer spec D8, D9)."""

from __future__ import annotations

import hashlib
import struct
import wave

import pytest

import av_sound.renderer as renderer_mod
from av_sound import (
    Profile,
    Recipe,
    file_sha256,
    pcm_sha256,
    read_wav,
    render,
    wav_bytes,
    write_wav,
)
from av_sound.wav import HEADER_SIZE, pcm_from_wav, wav_header

RECIPE = Recipe(750, (0, 4, 1), (1, 1, 2), (20, 60), (0.8, 1.0, 0.6))


def test_header_layout_is_canonical():
    n = 21600
    expected = (
        b"RIFF"
        + struct.pack("<I", 36 + 2 * n)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<I", 16)
        + struct.pack("<HH", 1, 1)
        + struct.pack("<II", 48000, 96000)
        + struct.pack("<HH", 2, 16)
        + b"data"
        + struct.pack("<I", 2 * n)
    )
    assert wav_header(n) == expected
    assert len(expected) == HEADER_SIZE


def test_file_is_header_plus_pcm(tmp_path):
    r = render(RECIPE, Profile.P1)
    path = tmp_path / "motif.wav"
    digest = write_wav(r, path)
    data = path.read_bytes()
    assert data == wav_header(r.n_samples) + r.pcm
    assert digest == hashlib.sha256(data).hexdigest() == file_sha256(r)
    assert pcm_sha256(r) == r.pcm_sha256 == hashlib.sha256(r.pcm).hexdigest()
    assert not (tmp_path / "motif.wav.tmp").exists()


def test_stdlib_reader_agrees(tmp_path):
    r = render(RECIPE, Profile.P3)
    path = tmp_path / "motif.wav"
    write_wav(r, path)
    with wave.open(str(path), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == 48000
        assert w.getsampwidth() == 2
        assert w.getnframes() == r.n_samples
        assert w.readframes(w.getnframes()) == r.pcm


def test_round_trip_and_bytes_api(tmp_path):
    r = render(RECIPE, Profile.P2)
    path = tmp_path / "x.wav"
    write_wav(r.pcm, path)  # bytes are accepted as well as rendered motifs
    assert read_wav(path) == r.pcm
    assert wav_bytes(r) == wav_bytes(r.pcm)
    assert pcm_sha256(r.pcm) == r.pcm_sha256


def test_rewrite_gives_identical_bytes(tmp_path):
    r = render(RECIPE, Profile.P2)
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    write_wav(r, a)
    write_wav(render(RECIPE, Profile.P2), b)
    assert a.read_bytes() == b.read_bytes()


def test_non_canonical_files_are_rejected():
    r = render(RECIPE, Profile.P1)
    good = wav_bytes(r)
    with pytest.raises(ValueError):
        pcm_from_wav(good[:-1])  # odd length
    with pytest.raises(ValueError):
        pcm_from_wav(good[:20])  # truncated header
    with pytest.raises(ValueError):
        pcm_from_wav(good[:22] + struct.pack("<H", 2) + good[24:])  # stereo header
    list_chunk = b"LIST" + struct.pack("<I", 4) + b"INFO"
    with pytest.raises(ValueError):
        pcm_from_wav(good[:36] + list_chunk + good[36:])  # extra chunk


def test_odd_pcm_length_is_rejected():
    with pytest.raises(ValueError):
        wav_bytes(b"\x00\x01\x02")


def test_overflowed_motif_cannot_be_written(tmp_path, monkeypatch):
    monkeypatch.setattr(renderer_mod, "RMS_TARGET", 30000)
    r = render(RECIPE, Profile.P1)
    with pytest.raises(OverflowError):
        write_wav(r, tmp_path / "bad.wav")
    assert not (tmp_path / "bad.wav").exists()
