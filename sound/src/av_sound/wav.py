"""Canonical 48 kHz mono 16-bit WAV writer and hash helpers (renderer spec D8, D9).

The header is a pure function of the sample count: no extra chunks, metadata or
timestamps, so file bytes are stable.
"""

from __future__ import annotations

import hashlib
import os
import struct
from pathlib import Path
from typing import Protocol

from av_sound.tables import SAMPLE_RATE

CHANNELS = 1
BITS_PER_SAMPLE = 16
HEADER_SIZE = 44


class HasPcm(Protocol):
    """Anything that exposes int16 little-endian mono samples as `pcm`."""

    @property
    def pcm(self) -> bytes: ...


def _pcm(audio: HasPcm | bytes) -> bytes:
    pcm = audio if isinstance(audio, bytes) else audio.pcm
    if len(pcm) % 2:
        raise ValueError("PCM byte length must be even (int16 samples)")
    return pcm


def wav_header(n_samples: int) -> bytes:
    """The canonical 44-byte header for `n_samples` mono int16 samples at 48 kHz."""
    data_bytes = 2 * n_samples
    block_align = CHANNELS * BITS_PER_SAMPLE // 8
    return b"".join(
        (
            b"RIFF",
            struct.pack("<I", 36 + data_bytes),
            b"WAVE",
            b"fmt ",
            struct.pack(
                "<IHHIIHH",
                16,
                1,
                CHANNELS,
                SAMPLE_RATE,
                SAMPLE_RATE * block_align,
                block_align,
                BITS_PER_SAMPLE,
            ),
            b"data",
            struct.pack("<I", data_bytes),
        )
    )


def wav_bytes(audio: HasPcm | bytes) -> bytes:
    """Complete canonical WAV file bytes."""
    pcm = _pcm(audio)
    return wav_header(len(pcm) // 2) + pcm


def write_wav(audio: HasPcm | bytes, path: str | os.PathLike[str]) -> str:
    """Write a canonical WAV file atomically and return its `file_sha256`."""
    data = wav_bytes(audio)
    target = Path(path)
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, target)
    return hashlib.sha256(data).hexdigest()


def pcm_from_wav(data: bytes) -> bytes:
    """Return the samples of a canonical WAV file; raise `ValueError` on any other layout."""
    if len(data) < HEADER_SIZE or (len(data) - HEADER_SIZE) % 2:
        raise ValueError("not a canonical WAV file: bad length")
    n_samples = (len(data) - HEADER_SIZE) // 2
    if data[:HEADER_SIZE] != wav_header(n_samples):
        raise ValueError("not a canonical WAV file: header differs from the spec D8 layout")
    return data[HEADER_SIZE:]


def read_wav(path: str | os.PathLike[str]) -> bytes:
    """Read a canonical WAV file and return its samples (int16 LE bytes)."""
    return pcm_from_wav(Path(path).read_bytes())


def pcm_sha256(audio: HasPcm | bytes) -> str:
    """SHA-256 of the sample bytes only (spec D9)."""
    return hashlib.sha256(_pcm(audio)).hexdigest()


def file_sha256(audio: HasPcm | bytes) -> str:
    """SHA-256 of the complete canonical WAV file (spec D9)."""
    return hashlib.sha256(wav_bytes(audio)).hexdigest()
