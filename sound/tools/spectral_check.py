"""Spectral check of the renderer: partial frequencies and weights per profile.

Renders one long event (T=900, weights 4/1/1, gaps 20/20; event 1 is 27,520
samples), takes its flat middle section (outside attack and release) and measures
FFT peaks near f, 2f and 3f: frequency from a Hann-windowed FFT (narrow main lobe),
amplitude from a flat-top-windowed FFT (flat peak, about 0.01 dB amplitude error).

Usage: uv run --project sound python sound/tools/spectral_check.py
"""

from __future__ import annotations

import numpy as np

from av_sound import Profile, Recipe, render

FFT_SIZE = 1 << 19
_FLATTOP = (0.21557895, 0.41663158, 0.277263158, 0.083578947, 0.006947368)


def _flattop(n: int) -> np.ndarray:
    k = 2 * np.pi * np.arange(n) / (n - 1)
    a = _FLATTOP
    return (
        a[0] - a[1] * np.cos(k) + a[2] * np.cos(2 * k) - a[3] * np.cos(3 * k) + a[4] * np.cos(4 * k)
    )


def partial_peaks(profile: Profile, pitch: int) -> list[tuple[float, float, float]]:
    """[(expected Hz, measured Hz, amplitude)] for harmonics 1, 2, 3 of event 1."""
    recipe = Recipe(900, (pitch, 0, 0), (4, 1, 1), (20, 20), (1.0, 1.0, 1.0))
    r = render(recipe, profile)
    n1 = r.event_samples[0]
    seg = r.samples[600 : n1 - 1600].astype(np.float64)
    amp_spec = np.abs(np.fft.rfft(seg * _flattop(seg.size), FFT_SIZE))
    loc_spec = np.abs(np.fft.rfft(seg * np.hanning(seg.size), FFT_SIZE))
    freqs = np.fft.rfftfreq(FFT_SIZE, 1 / 48_000)
    f = profile.f0_hz * 2 ** (pitch / 12)
    out = []
    for h in (1, 2, 3):
        band = (freqs > h * f * 0.97) & (freqs < h * f * 1.03)
        i_loc = int(np.argmax(np.where(band, loc_spec, 0)))
        i_amp = int(np.argmax(np.where(band, amp_spec, 0)))
        out.append((h * f, float(freqs[i_loc]), float(amp_spec[i_amp])))
    return out


def main() -> None:
    print("| Profile | pitch | f (Hz) | measured f, 2f, 3f (Hz) | A2/A1 | A3/A1 |")
    print("| --- | --- | --- | --- | --- | --- |")
    for profile in Profile:
        for pitch in (-6, 0, 6):
            peaks = partial_peaks(profile, pitch)
            a1 = peaks[0][2]
            measured = ", ".join(f"{m:.2f}" for _, m, _ in peaks)
            print(
                f"| {profile.value} | {pitch:+d} | {peaks[0][0]:.2f} | {measured} | "
                f"{peaks[1][2] / a1:.4f} | {peaks[2][2] / a1:.4f} |"
            )


if __name__ == "__main__":
    main()
