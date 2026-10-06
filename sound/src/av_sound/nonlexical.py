"""Nonlexical assets: calibration examples, READY cue and grammar clicks (#14).

These are the only sounds besides study motifs that participants hear. They must
never be admissible as motifs, so each one is listed in the reserved-signal
registry (`sound/reserved/registry.json`), and no asset has a motif length
(21,600, 28,800, 36,000 or 43,200 samples): no recipe can render to an asset.

All assets use the renderer primitives (`synth_event`, `envelope`, `normalize` and
the integer tables), integer arithmetic only. The exact byte recipe of every asset
is in `sound/docs/nonlexical.md`; `sound/tools/make_reserved_assets.py` writes
the registry and, on request, the WAV files.

| Asset | ID | Samples |
| --- | --- | --- |
| Calibration example, one per profile | `calibration-P1` .. `calibration-P3` | 96,000 |
| READY cue | `ready-cue` | 15,360 |
| Action click (single) | `click-action` | 192 |
| Target click (double) | `click-target` | 4,032 |
| Grammar demonstration | `click-grammar-demo` | 13,824 |
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Final

import numpy as np

from av_sound.composer import GAP_SAMPLES, MOTIF_SAMPLES
from av_sound.recipe import Profile
from av_sound.renderer import (
    FULL_SCALE,
    RENDERER_VERSION,
    RMS_TARGET,
    envelope,
    normalize,
    overflows,
    synth_event,
)
from av_sound.reserved import REGISTRY_VERSION, ReservedEntry, ReservedRegistry
from av_sound.tables import (
    ATTACK,
    ATTACK_SAMPLES,
    ENV_Q,
    PHASE_BITS,
    SAMPLE_RATE,
    SAMPLES_PER_MS,
    SINE,
    SINE_BITS,
    SINE_Q,
    IntArray,
)
from av_sound.wav import file_sha256, pcm_sha256

ASSET_SPEC_VERSION: Final = "0.1.0"
"""Version of the asset definitions; bumped whenever any asset's bytes change."""

# Calibration examples (Study B protocol §3 and §5.1; Study A protocol §5).
CALIBRATION_MS: Final = 2000
CALIBRATION_SAMPLES: Final = CALIBRATION_MS * SAMPLES_PER_MS
"""96,000 samples: exactly 2.000 s."""
CALIBRATION_PITCH: Final = 0

# READY cue (Protocol constants: familiarization only, never a semantic item).
READY_BURST_SAMPLES: Final = 120 * SAMPLES_PER_MS
READY_GAP_SAMPLES: Final = 80 * SAMPLES_PER_MS
READY_SAMPLES: Final = 2 * READY_BURST_SAMPLES + READY_GAP_SAMPLES
"""15,360 samples (320 ms): burst, gap, burst."""
READY_RMS: Final = RMS_TARGET // 2
"""3,668 LSB over the two bursts: 6 dB below the motif RMS target (-19.02 dBFS)."""
READY_NOISE_KEY: Final = b"av-sound/nonlexical/ready-cue/v1"
"""Key of the SHA-256 counter stream that supplies the READY noise."""
READY_BOXCARS: Final[tuple[int, ...]] = (6, 8, 10)

# Grammar clicks (Common procedures §5).
CLICK_SAMPLES: Final = 4 * SAMPLES_PER_MS
"""192 samples (4 ms) per click."""
CLICK_HZ: Final = 3000
CLICK_PEAK: Final = 10_362
"""Peak of every click in LSB: -10.00 dBFS."""
DOUBLE_CLICK_ONSET: Final = 80 * SAMPLES_PER_MS
"""The second click of the target (double) click starts 3,840 samples after the first."""
TARGET_CLICK_SAMPLES: Final = DOUBLE_CLICK_ONSET + CLICK_SAMPLES
GRAMMAR_DEMO_SAMPLES: Final = CLICK_SAMPLES + GAP_SAMPLES + TARGET_CLICK_SAMPLES

ASSET_IDS: Final[tuple[str, ...]] = (
    "calibration-P1",
    "calibration-P2",
    "calibration-P3",
    "ready-cue",
    "click-action",
    "click-target",
    "click-grammar-demo",
)
"""Every asset ID, in registry order."""

_SINE_SHIFT: Final = PHASE_BITS - SINE_BITS


def _dbfs(value: float) -> float:
    return 20 * math.log10(value / FULL_SCALE) if value > 0 else -math.inf


def _frozen(samples: IntArray) -> IntArray:
    out = np.ascontiguousarray(samples, dtype=np.int64)
    if overflows(out):  # pragma: no cover - levels are fixed far below full scale
        raise OverflowError("nonlexical asset exceeds full scale")
    out.setflags(write=False)
    return out


@dataclass(frozen=True, slots=True, eq=False)
class NonlexicalAsset:
    """One reserved nonlexical asset: samples and the facts the registry records.

    `segments` lists the sounding parts as `(onset, n_samples)` pairs; everything
    else is digital silence. `pcm` makes the asset usable with `write_wav`.
    """

    id: str
    kind: str
    profile: Profile | None
    samples: IntArray = field(repr=False)
    segments: tuple[tuple[int, int], ...]
    description: str

    @property
    def n_samples(self) -> int:
        return int(self.samples.size)

    @property
    def duration_ms(self) -> float:
        return self.n_samples / SAMPLES_PER_MS

    @property
    def pcm(self) -> bytes:
        """int16 little-endian sample bytes."""
        return self.samples.astype("<i2").tobytes()

    @property
    def pcm_sha256(self) -> str:
        return pcm_sha256(self.pcm)

    @property
    def file_sha256(self) -> str:
        return file_sha256(self.pcm)

    @property
    def peak(self) -> int:
        return int(np.max(np.abs(self.samples)))

    @property
    def peak_dbfs(self) -> float:
        """Peak relative to full-scale code 32,767, for reporting only."""
        return _dbfs(self.peak)

    @property
    def rms_dbfs(self) -> float:
        """RMS over the whole asset, silences included, for reporting only."""
        return _dbfs(math.sqrt(int(np.sum(self.samples * self.samples)) / self.n_samples))

    @property
    def active_rms_dbfs(self) -> float:
        """RMS over the sounding segments only, for reporting only."""
        active = np.concatenate([self.samples[o : o + n] for o, n in self.segments])
        return _dbfs(math.sqrt(int(np.sum(active * active)) / active.size))

    def to_entry(self) -> ReservedEntry:
        """The registry entry: no recipe, because no asset is recipe-shaped."""
        return ReservedEntry(
            id=self.id,
            kind=self.kind,
            profile=self.profile,
            n_samples=self.n_samples,
            pcm_sha256=self.pcm_sha256,
            file_sha256=self.file_sha256,
            recipe=None,
            description=self.description,
        )


# ---------------------------------------------------------------------------
# Calibration examples


def calibration_example(profile: Profile | str) -> NonlexicalAsset:
    """The 2.000-s calibration example of `profile`.

    One event of 96,000 samples at the profile's f0 (pitch 0) with the motif
    partials and envelope (`synth_event`), normalized to the motif `RMS_TARGET`.
    """
    prof = Profile(profile)
    x = synth_event(prof, CALIBRATION_PITCH, CALIBRATION_SAMPLES, 1)
    out = normalize(x, RMS_TARGET)
    return NonlexicalAsset(
        id=f"calibration-{prof.value}",
        kind="calibration",
        profile=prof,
        samples=_frozen(out.samples),
        segments=((0, CALIBRATION_SAMPLES),),
        description=(
            f"Calibration example for {prof.value}: 2.000 s steady tone at f0 = "
            f"{prof.f0_hz} Hz (pitch 0), partials 1/0.15/0.05, 10/30 ms raised-cosine "
            f"envelope, RMS {RMS_TARGET} LSB (the motif target). Nonsemantic; used for "
            "comfort screening, gain calibration and the profile menu."
        ),
    )


# ---------------------------------------------------------------------------
# READY cue


def ready_filter() -> tuple[int, ...]:
    """Integer FIR band-pass: first difference, then boxcars of 6, 8 and 10 taps (23 taps)."""
    h = np.array([1, -1], dtype=np.int64)
    for taps in READY_BOXCARS:
        h = np.convolve(h, np.ones(taps, dtype=np.int64))
    return tuple(int(c) for c in h)


def noise_stream(n_samples: int, key: bytes = READY_NOISE_KEY) -> IntArray:
    """Uniform int16 noise: block k is SHA-256(key || k as uint64 LE), read as 16 int16 LE."""
    blocks = -(-n_samples // 16)
    data = b"".join(hashlib.sha256(key + k.to_bytes(8, "little")).digest() for k in range(blocks))
    return np.frombuffer(data, dtype="<i2")[:n_samples].astype(np.int64)


def _band_noise(n_samples: int) -> IntArray:
    """`n_samples` of noise through `ready_filter()`, without edge transients ("valid")."""
    h = ready_filter()
    taps = len(h)
    u = noise_stream(n_samples + taps - 1)
    y = np.zeros(n_samples, dtype=np.int64)
    for k, coefficient in enumerate(h):
        start = taps - 1 - k
        y += coefficient * u[start : start + n_samples]
    return y


def ready_cue() -> NonlexicalAsset:
    """Two 120-ms bursts of band-limited noise with 80 ms of silence between them."""
    n = READY_BURST_SAMPLES
    noise = _band_noise(2 * n)
    env = envelope(n)
    half = 1 << (ENV_Q - 1)
    bursts = [(noise[j * n : (j + 1) * n] * env + half) >> ENV_Q for j in range(2)]
    level = normalize(np.concatenate(bursts), READY_RMS).samples
    samples = np.concatenate([level[:n], np.zeros(READY_GAP_SAMPLES, dtype=np.int64), level[n:]])
    return NonlexicalAsset(
        id="ready-cue",
        kind="ready_cue",
        profile=None,
        samples=_frozen(samples),
        segments=((0, n), (n + READY_GAP_SAMPLES, n)),
        description=(
            "READY cue (familiarization only, never a semantic item): two 120 ms bursts of "
            "band-limited noise (about 0.9-2.9 kHz) with 80 ms of silence, 320 ms in total; "
            f"RMS over the bursts {READY_RMS} LSB. Profile-independent."
        ),
    )


# ---------------------------------------------------------------------------
# Grammar clicks


def click() -> IntArray:
    """One 4-ms click: a 3 kHz sine (12 cycles) under a raised-cosine window, peak 10,362."""
    increment, rest = divmod(CLICK_HZ << PHASE_BITS, SAMPLE_RATE)
    assert rest == 0, "the click frequency must give an exact phase increment"
    half = CLICK_SAMPLES // 2
    stride, rest = divmod(ATTACK_SAMPLES, half)
    assert rest == 0, "the click window must sample the attack table exactly"
    i = np.arange(CLICK_SAMPLES, dtype=np.int64)
    sine = SINE[((i * increment) & ((1 << PHASE_BITS) - 1)) >> _SINE_SHIFT]
    window = ATTACK[stride * np.minimum(i, CLICK_SAMPLES - i)]
    v = (sine * window + (1 << (SINE_Q - 1))) >> SINE_Q  # Q30
    m = int(np.max(np.abs(v)))
    return np.asarray((2 * v * CLICK_PEAK + m) // (2 * m), dtype=np.int64)


def _target_click_samples() -> IntArray:
    one = click()
    out = np.zeros(TARGET_CLICK_SAMPLES, dtype=np.int64)
    out[:CLICK_SAMPLES] = one
    out[DOUBLE_CLICK_ONSET:] = one
    return out


def action_click() -> NonlexicalAsset:
    """The action click: one click (192 samples)."""
    return NonlexicalAsset(
        id="click-action",
        kind="click",
        profile=None,
        samples=_frozen(click()),
        segments=((0, CLICK_SAMPLES),),
        description=(
            "Grammar click for 'action': one 4 ms click (3 kHz, raised-cosine window, "
            f"peak {CLICK_PEAK} LSB). Profile-independent."
        ),
    )


def target_click() -> NonlexicalAsset:
    """The target click: two clicks with onsets 80 ms apart (4,032 samples)."""
    return NonlexicalAsset(
        id="click-target",
        kind="click",
        profile=None,
        samples=_frozen(_target_click_samples()),
        segments=((0, CLICK_SAMPLES), (DOUBLE_CLICK_ONSET, CLICK_SAMPLES)),
        description=(
            "Grammar click for 'target': two 4 ms clicks with onsets 80 ms apart "
            f"(84 ms in total, peak {CLICK_PEAK} LSB). Profile-independent."
        ),
    )


def grammar_demo() -> NonlexicalAsset:
    """Action click + 9,600 zero samples + target click: the message grammar without motifs."""
    one = click()
    double = _target_click_samples()
    samples = np.concatenate([one, np.zeros(GAP_SAMPLES, dtype=np.int64), double])
    target_onset = CLICK_SAMPLES + GAP_SAMPLES
    return NonlexicalAsset(
        id="click-grammar-demo",
        kind="click",
        profile=None,
        samples=_frozen(samples),
        segments=(
            (0, CLICK_SAMPLES),
            (target_onset, CLICK_SAMPLES),
            (target_onset + DOUBLE_CLICK_ONSET, CLICK_SAMPLES),
        ),
        description=(
            "Grammar demonstration: action click, exactly 9600 zero samples (200 ms), "
            "target click; mirrors action motif, gap, referent motif. Profile-independent."
        ),
    )


# ---------------------------------------------------------------------------
# All assets and the registry


def nonlexical_assets() -> tuple[NonlexicalAsset, ...]:
    """Every reserved asset, in `ASSET_IDS` order."""
    assets = (
        *(calibration_example(p) for p in Profile),
        ready_cue(),
        action_click(),
        target_click(),
        grammar_demo(),
    )
    assert tuple(a.id for a in assets) == ASSET_IDS
    assert all(a.n_samples not in MOTIF_SAMPLES for a in assets)
    return assets


def nonlexical_asset(asset_id: str) -> NonlexicalAsset:
    """One asset by ID; raises `KeyError` for an unknown ID."""
    if asset_id not in ASSET_IDS:
        raise KeyError(f"unknown nonlexical asset {asset_id!r}; expected one of {ASSET_IDS}")
    if asset_id.startswith("calibration-"):
        return calibration_example(asset_id.removeprefix("calibration-"))
    builders = {
        "ready-cue": ready_cue,
        "click-action": action_click,
        "click-target": target_click,
        "click-grammar-demo": grammar_demo,
    }
    return builders[asset_id]()


def build_reserved_registry() -> ReservedRegistry:
    """The registry that `sound/reserved/registry.json` must equal (current renderer)."""
    return ReservedRegistry(
        registry_version=REGISTRY_VERSION,
        renderer_version=RENDERER_VERSION,
        entries=tuple(a.to_entry() for a in nonlexical_assets()),
    )
