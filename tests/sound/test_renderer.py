"""Renderer tests (issue O4.1.2, renderer spec D1-D7)."""

from __future__ import annotations

import hashlib
import importlib.util
import itertools
import json
import math
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import av_sound.renderer as renderer_mod
from av_sound import (
    MIN_EVENT_SAMPLES,
    RENDERER_VERSION,
    RMS_TARGET,
    Profile,
    Recipe,
    event_samples,
    file_sha256,
    render,
)
from av_sound.recipe import AMPLITUDES, GAPS_MS, PITCHES, RHYTHM_WEIGHTS, TOTAL_MS
from av_sound.renderer import FULL_SCALE, amplitude_steps, envelope, normalize, synth_event

SOUND_ROOT = Path(__file__).resolve().parents[2] / "sound"

WORKED = Recipe(600, (-3, 0, 4), (2, 1, 3), (40, 20), (1.0, 0.6, 0.8))

recipes = st.builds(
    Recipe,
    total_ms=st.sampled_from(TOTAL_MS),
    pitches=st.tuples(*[st.sampled_from(PITCHES)] * 3),
    rhythm_weights=st.tuples(*[st.sampled_from(RHYTHM_WEIGHTS)] * 3),
    gaps_ms=st.tuples(*[st.sampled_from(GAPS_MS)] * 2),
    amplitudes=st.tuples(*[st.sampled_from(AMPLITUDES)] * 3),
)
profiles = st.sampled_from(list(Profile))


def _event_slices(r):
    return [slice(o, o + n) for o, n in zip(r.timing.event_onsets, r.event_samples, strict=True)]


@pytest.mark.parametrize("total_ms", TOTAL_MS)
@pytest.mark.parametrize("profile", list(Profile))
def test_length_is_exact_for_every_total_and_profile(total_ms, profile):
    r = render(Recipe(total_ms, (0, 3, -3), (1, 2, 3), (20, 40), (0.6, 0.8, 1.0)), profile)
    assert r.n_samples == total_ms * 48
    assert r.samples.size == total_ms * 48
    assert len(r.pcm) == 2 * total_ms * 48


def test_worked_example_matches_spec():
    r = render(WORKED, Profile.P2)
    assert r.event_samples == (8640, 4320, 12960)
    assert r.timing.event_onsets == (0, 10560, 15840)
    assert r.timing.gap_samples == (1920, 960)
    assert r.peak == 13865
    assert r.pcm_sha256 == "4c0467de354c076c0b30bc9af794e31384afdf161af24605fb621cc455c35d87"
    assert r.renderer_version == RENDERER_VERSION


@pytest.mark.parametrize("gaps", list(itertools.product(GAPS_MS, repeat=2)))
def test_gaps_are_digital_silence_at_rule_positions(gaps):
    r = render(Recipe(750, (1, 2, 3), (3, 2, 1), gaps, (1.0, 1.0, 0.6)), Profile.P1)
    n1, n2, n3 = r.event_samples
    g1, g2 = (g * 48 for g in gaps)
    assert r.timing.event_onsets == (0, n1 + g1, n1 + g1 + n2 + g2)
    assert not r.samples[n1 : n1 + g1].any()
    assert not r.samples[n1 + g1 + n2 : n1 + g1 + n2 + g2].any()
    for s in _event_slices(r):
        event = r.samples[s]
        assert event[0] == 0 and event[-1] == 0  # envelope starts and ends at 0
        assert np.count_nonzero(event) > 0.9 * event.size


def test_event_durations_follow_rounding_rule():
    for t, w, g in itertools.product(
        TOTAL_MS, itertools.product(RHYTHM_WEIGHTS, repeat=3), itertools.product(GAPS_MS, repeat=2)
    ):
        n = event_samples(t, w, g)
        d = (t - g[0] - g[1]) * 48
        assert sum(n) == d
        for j in (0, 1):  # half-up rounding of the exact share
            exact = d * w[j] / sum(w)
            assert abs(n[j] - exact) <= 0.5
        assert abs(n[2] - d * w[2] / sum(w)) <= 1.0


def test_envelope_edges():
    env = envelope(5000)
    one = 1 << 30
    assert env[0] == 0
    assert env[479] < one and env[480] == one  # attack reaches amplitude after 480 samples
    assert env[5000 - 1 - 1440] == one and env[5000 - 1440] < one
    assert env[-1] == 0  # release reaches 0 after 1,440 samples
    assert np.all(np.diff(env[:481]) > 0)  # strictly rising to the acceptance point
    assert np.all(np.diff(env[-1441:]) < 0)  # strictly falling to 0
    assert np.all(env[480 : 5000 - 1440] == one)


def test_envelope_short_event_uses_min_of_attack_and_release():
    env = envelope(1000)
    assert env.max() < (1 << 30)
    assert env[0] == 0 and env[-1] == 0


def test_attack_and_release_in_rendered_event():
    # Constant-amplitude event: sample magnitudes after the attack reach the steady peak.
    r = render(Recipe(900, (0, 0, 0), (4, 1, 1), (20, 20), (1.0, 1.0, 1.0)), Profile.P1)
    event = np.abs(r.samples[: r.event_samples[0]])
    steady = event[3000:6000].max()
    assert event[:240].max() < 0.6 * steady  # still rising during the attack
    assert event[-240:].max() < 0.1 * steady  # almost silent at the end of the release


def test_amplitude_ratios():
    r = render(Recipe(900, (0, 0, 0), (1, 1, 1), (20, 20), (0.6, 0.8, 1.0)), Profile.P2)
    rms = []
    for s in _event_slices(r):
        mid = r.samples[s][1000:-2000].astype(np.float64)
        rms.append(math.sqrt(float(np.mean(mid * mid))))
    assert rms[0] / rms[2] == pytest.approx(0.6, rel=2e-3)
    assert rms[1] / rms[2] == pytest.approx(0.8, rel=2e-3)


def test_amplitude_steps_gcd_rule():
    assert amplitude_steps((0.6, 0.8, 1.0)) == (3, 4, 5)
    for a in AMPLITUDES:
        assert amplitude_steps((a, a, a)) == (1, 1, 1)
    non_uniform = [t for t in itertools.product(AMPLITUDES, repeat=3) if len(set(t)) > 1]
    for t in non_uniform:
        assert amplitude_steps(t) == tuple(round(5 * a) for a in t)


def test_uniform_amplitude_triples_render_identically():
    hashes = {
        render(Recipe(600, (2, -2, 5), (1, 3, 2), (40, 40), (a, a, a)), Profile.P3).pcm_sha256
        for a in AMPLITUDES
    }
    assert len(hashes) == 1


def test_profiles_share_timing_but_differ_in_waveform():
    rendered = [render(WORKED, p) for p in Profile]
    assert len({r.pcm_sha256 for r in rendered}) == 3
    assert len({r.timing for r in rendered}) == 1
    for r in rendered:
        assert not r.samples[8640:10560].any()


def test_same_input_gives_one_hash_over_1000_renders():
    assert len({render(WORKED, Profile.P1).pcm_sha256 for _ in range(1000)}) == 1


def test_overflow_is_flagged_never_limited(monkeypatch):
    monkeypatch.setattr(renderer_mod, "RMS_TARGET", 30000)
    r = render(WORKED, Profile.P1)
    assert r.overflow
    assert r.peak > FULL_SCALE  # not clipped
    with pytest.raises(OverflowError):
        _ = r.pcm


def test_normalize_rejects_silence():
    with pytest.raises(ValueError):
        normalize(np.zeros(10, dtype=np.int64))


def test_short_event_is_flagged_not_repaired():
    r = render(Recipe(450, (0, 0, 0), (1, 4, 4), (60, 60), (1.0, 1.0, 1.0)), Profile.P1)
    assert r.short_event
    assert r.event_samples[0] == 1760  # about 36.7 ms
    assert r.n_samples == 450 * 48
    assert not r.overflow


def test_render_accepts_mapping_and_string_profile():
    r = render(WORKED.to_dict(), "P2")
    assert r.pcm_sha256 == render(WORKED, Profile.P2).pcm_sha256
    with pytest.raises(ValueError):
        render(WORKED, "P4")


def test_reporting_properties():
    r = render(WORKED, Profile.P2)
    assert r.rms == pytest.approx(RMS_TARGET, abs=1.0)
    assert r.peak_dbfs == pytest.approx(20 * math.log10(13865 / FULL_SCALE))
    assert r.nonfinite is False
    assert "array(" not in repr(r)  # sample data is kept out of the repr


def test_synth_event_starts_at_phase_zero():
    x = synth_event(Profile.P1, 0, 4000, 1)
    assert x[0] == 0
    assert x.dtype == np.int64


@settings(max_examples=300, deadline=None)
@given(recipe=recipes, profile=profiles)
def test_property_domain_recipes(recipe, profile):
    r = render(recipe, profile)
    assert r.n_samples == recipe.total_ms * 48
    assert not r.overflow
    assert abs(r.rms - RMS_TARGET) < 1.0
    assert r.peak <= FULL_SCALE * 10 ** (-3 / 20)  # at least 3 dB headroom (spec D6)
    assert r.short_event == (min(r.event_samples) < MIN_EVENT_SAMPLES)
    assert r.samples.dtype == np.int64


def test_worst_case_crest_factor_from_sweep():
    worst = Recipe(450, (-1, -2, -2), (1, 1, 3), (60, 60), (0.6, 1.0, 0.6))
    r = render(worst, Profile.P1)
    assert 20 * math.log10(r.peak / r.rms) < 8.82
    assert r.peak_dbfs < -4.0


def test_reference_vectors():
    data = json.loads((SOUND_ROOT / "testvectors" / "renderer" / "vectors.json").read_text("utf-8"))
    assert data["renderer_version"] == RENDERER_VERSION
    assert len(data["vectors"]) == 21
    for v in data["vectors"]:
        r = render(Recipe.from_dict(v["recipe"]), v["profile"])
        assert r.n_samples == v["n_samples"]
        assert list(r.event_samples) == v["event_samples"]
        assert r.short_event == v["short_event"]
        assert r.pcm_sha256 == v["pcm_sha256"], v["name"]
        assert file_sha256(r) == v["file_sha256"], v["name"]


def test_spectral_partials_and_weights():
    spec = importlib.util.spec_from_file_location(
        "spectral_check", SOUND_ROOT / "tools" / "spectral_check.py"
    )
    assert spec and spec.loader
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    bin_hz = 48_000 / tool.FFT_SIZE
    for profile in Profile:
        for pitch in (-6, 0, 6):
            peaks = tool.partial_peaks(profile, pitch)
            for expected, measured, _ in peaks:
                assert abs(measured - expected) <= 1.5 * bin_hz
            a1 = peaks[0][2]
            assert peaks[1][2] / a1 == pytest.approx(0.15, rel=0.01)
            assert peaks[2][2] / a1 == pytest.approx(0.05, rel=0.01)


def test_pcm_hash_is_sha256_of_int16_le():
    r = render(WORKED, Profile.P1)
    assert r.pcm == r.samples.astype("<i2").tobytes()
    assert r.pcm_sha256 == hashlib.sha256(r.pcm).hexdigest()
