"""Nonlexical assets and the reserved-signal registry (issue #14).

The assets are synthetic, nonsemantic sounds (calibration tones, READY cue, clicks).
No recipe can render to one of them: every asset length differs from every motif
length. The registry hashes must equal a fresh build on every CI platform.
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib
import json
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from av_sound import (
    CALIBRATION_SAMPLES,
    GAP_SAMPLES,
    RENDERER_VERSION,
    RMS_TARGET,
    SAMPLE_RATE,
    AtomAudio,
    CompositionError,
    NonlexicalAsset,
    Profile,
    Recipe,
    ReservedEntry,
    ReservedRegistry,
    build_reserved_registry,
    calibration_example,
    compose_message,
    load_reserved_registry,
    nonlexical_asset,
    nonlexical_assets,
    read_wav,
    render,
    validate,
    wav_bytes,
    write_wav,
)
from av_sound.composer import MOTIF_SAMPLES
from av_sound.nonlexical import (
    ASSET_IDS,
    CLICK_PEAK,
    CLICK_SAMPLES,
    DOUBLE_CLICK_ONSET,
    READY_BURST_SAMPLES,
    READY_GAP_SAMPLES,
    READY_RMS,
    action_click,
    click,
    grammar_demo,
    noise_stream,
    ready_cue,
    ready_filter,
    target_click,
)
from av_sound.recipe import AMPLITUDES, GAPS_MS, PITCHES, RHYTHM_WEIGHTS, TOTAL_MS
from av_sound.renderer import FULL_SCALE
from av_sound.synthetic import synthetic_recipes

# `av_sound.validate` (the package attribute) is the function; this is the module.
validate_mod = importlib.import_module("av_sound.validate")
SOUND = Path(__file__).resolve().parents[2] / "sound"
REGISTRY = SOUND / "reserved" / "registry.json"
ASSETS = nonlexical_assets()
BY_ID = {a.id: a for a in ASSETS}
DEMO = Recipe(600, (-3, 0, 4), (2, 1, 3), (40, 20), (1.0, 0.6, 0.8))  # synthetic


def spectrum(samples: np.ndarray) -> np.ndarray:
    """Power per 1 Hz bin for a 48,000-sample window (reporting only)."""
    return np.abs(np.fft.rfft(samples.astype(np.float64), n=SAMPLE_RATE)) ** 2


# --- acceptance: calibration examples ----------------------------------------------


@pytest.mark.parametrize("profile", list(Profile), ids=[p.value for p in Profile])
def test_calibration_example_is_96000_samples_at_motif_rms(profile):
    a = calibration_example(profile)
    assert a.id == f"calibration-{profile.value}" and a.kind == "calibration"
    assert a.profile is profile
    assert a.n_samples == CALIBRATION_SAMPLES == 96_000 == 2 * SAMPLE_RATE
    assert len(a.pcm) == 2 * 96_000
    assert a.n_samples not in MOTIF_SAMPLES
    # RMS normalized exactly like a motif (renderer spec D6): within rounding of 7,336 LSB.
    sum_sq = int(np.sum(a.samples * a.samples))
    assert abs(sum_sq / a.n_samples - RMS_TARGET**2) < 2 * RMS_TARGET
    assert a.samples[0] == 0 and a.samples[-1] == 0  # raised-cosine envelope ends
    assert a.peak < FULL_SCALE


@pytest.mark.parametrize("profile", list(Profile), ids=[p.value for p in Profile])
def test_calibration_example_carries_the_profile_timbre(profile):
    power = spectrum(calibration_example(profile).samples[24_000:72_000])
    f0 = profile.f0_hz
    assert int(np.argmax(power)) == f0
    amplitude = [np.sqrt(power[h * f0]) for h in (1, 2, 3)]
    assert amplitude[1] / amplitude[0] == pytest.approx(0.15, abs=0.002)
    assert amplitude[2] / amplitude[0] == pytest.approx(0.05, abs=0.002)


def test_calibration_example_cannot_equal_a_composed_message():
    # A 2.0-s message also has 96,000 samples, but its samples 43,200..52,799 are zero.
    for profile in Profile:
        middle = calibration_example(profile).samples[43_200:52_800]
        assert np.count_nonzero(middle) > 9_000


# --- READY cue and clicks ----------------------------------------------------------


def test_ready_cue_layout_and_level():
    a = ready_cue()
    n, g = READY_BURST_SAMPLES, READY_GAP_SAMPLES
    assert (a.id, a.kind, a.profile) == ("ready-cue", "ready_cue", None)
    assert a.n_samples == 2 * n + g == 15_360
    assert a.segments == ((0, n), (n + g, n))
    assert not np.any(a.samples[n : n + g])
    bursts = np.concatenate([a.samples[:n], a.samples[n + g :]])
    assert abs(int(np.sum(bursts * bursts)) / bursts.size - READY_RMS**2) < 2 * READY_RMS
    assert not np.array_equal(a.samples[:n], a.samples[n + g :])  # two different noise bursts


def test_ready_cue_is_band_limited_noise_not_a_tone():
    power = spectrum(ready_cue().samples)
    total = power.sum()
    assert power[400:4500].sum() / total > 0.95
    assert power.max() / total < 0.01  # no tonal component dominates
    h = ready_filter()
    assert len(h) == 23 and sum(h) == 0  # first difference removes DC
    assert h == (1, 2, 3, 4, 5, 6, 6, 6, 5, 4, 2, 0, -2, -4, -5, -6, -6, -6, -5, -4, -3, -2, -1)


def test_noise_stream_is_the_documented_sha256_counter_stream():
    first = hashlib.sha256(b"av-sound/nonlexical/ready-cue/v1" + bytes(8)).digest()
    expected = np.frombuffer(first, dtype="<i2").astype(np.int64)
    assert np.array_equal(noise_stream(16), expected)
    assert np.array_equal(noise_stream(40)[:16], expected)
    assert noise_stream(40).dtype == np.int64 and noise_stream(40).size == 40


def test_click_shape():
    c = click()
    assert c.size == CLICK_SAMPLES == 192
    assert int(np.max(np.abs(c))) == CLICK_PEAK
    assert int(np.sum(c)) == 0  # odd-symmetric: no DC
    assert c[0] == 0
    power = spectrum(c)
    assert 2_900 <= int(np.argmax(power)) <= 3_100


def test_clicks_and_grammar_demo_layout():
    one, double, demo = action_click(), target_click(), grammar_demo()
    assert one.n_samples == 192 and double.n_samples == 4_032 and demo.n_samples == 13_824
    assert np.array_equal(one.samples, click())
    assert np.array_equal(double.samples[:CLICK_SAMPLES], click())
    assert np.array_equal(double.samples[DOUBLE_CLICK_ONSET:], click())
    assert not np.any(double.samples[CLICK_SAMPLES:DOUBLE_CLICK_ONSET])
    # Same byte layout as a message: action + 9,600 zero samples (19,200 bytes) + target.
    assert demo.pcm == one.pcm + bytes(2 * GAP_SAMPLES) + double.pcm
    assert demo.segments == ((0, 192), (9_792, 192), (13_632, 192))
    assert all(a.kind == "click" and a.profile is None for a in (one, double, demo))


@pytest.mark.parametrize("asset_id", ASSET_IDS)
def test_levels_are_comfortable_and_never_overflow(asset_id):
    a = BY_ID[asset_id]
    assert not a.samples.flags.writeable
    assert a.samples.dtype == np.int64
    assert int(np.max(np.abs(a.samples))) <= FULL_SCALE
    assert a.peak_dbfs <= -6.0  # at least 6 dB of digital headroom
    assert a.active_rms_dbfs <= -13.0 + 0.01  # never louder than the motif RMS target
    assert a.rms_dbfs <= a.active_rms_dbfs + 1e-9
    assert a.duration_ms == a.n_samples / 48
    for onset, n in a.segments:
        assert np.count_nonzero(a.samples[onset : onset + n]) > 0.8 * n
    sounding = np.zeros(a.n_samples, dtype=bool)
    for onset, n in a.segments:
        sounding[onset : onset + n] = True
    assert not np.any(a.samples[~sounding])


# --- acceptance: registry -----------------------------------------------------------


def test_registry_lists_every_asset_with_hashes_that_match_a_rebuild():
    text = REGISTRY.read_text(encoding="utf-8")
    doc = json.loads(text)
    assert text == json.dumps(doc, indent=2, sort_keys=True) + "\n"
    registry = load_reserved_registry()
    assert registry.renderer_version == RENDERER_VERSION
    assert registry == build_reserved_registry()
    assert registry.to_dict() == doc
    assert [e.id for e in registry.entries] == list(ASSET_IDS)
    for entry, asset in zip(registry.entries, ASSETS, strict=True):
        assert entry.n_samples == asset.n_samples
        assert entry.pcm_sha256 == hashlib.sha256(asset.pcm).hexdigest()
        assert entry.file_sha256 == hashlib.sha256(wav_bytes(asset.pcm)).hexdigest()
        assert entry.recipe is None and entry.features is None
        assert entry.kind == asset.kind and entry.profile == asset.profile
    assert len({e.pcm_sha256 for e in registry.entries}) == len(ASSET_IDS)


def test_registry_kinds_and_profiles():
    registry = load_reserved_registry()
    kinds = [(e.id, e.kind, e.profile) for e in registry.entries]
    assert kinds == [
        ("calibration-P1", "calibration", Profile.P1),
        ("calibration-P2", "calibration", Profile.P2),
        ("calibration-P3", "calibration", Profile.P3),
        ("ready-cue", "ready_cue", None),
        ("click-action", "click", None),
        ("click-target", "click", None),
        ("click-grammar-demo", "click", None),
    ]


def test_assets_are_deterministic_and_round_trip_through_wav(tmp_path):
    again = nonlexical_assets()
    for a, b in zip(ASSETS, again, strict=True):
        assert a.pcm == b.pcm and a.pcm_sha256 == b.pcm_sha256
        assert nonlexical_asset(a.id).pcm == a.pcm
        path = tmp_path / f"{a.id}.wav"
        assert write_wav(a, path) == a.file_sha256
        assert read_wav(path) == a.pcm


def test_unknown_asset_id_raises():
    with pytest.raises(KeyError, match="unknown nonlexical asset"):
        nonlexical_asset("ready")


def test_to_entry_matches_reserved_entry():
    entry = BY_ID["ready-cue"].to_entry()
    assert isinstance(entry, ReservedEntry)
    assert ReservedEntry.from_dict(entry.to_dict()) == entry


def test_asset_ids_are_unique_and_registry_safe():
    assert len(set(ASSET_IDS)) == len(ASSET_IDS) == 7
    assert all(isinstance(a, NonlexicalAsset) for a in ASSETS)


# --- acceptance: exact match returns E_RESERVED -------------------------------------


@pytest.mark.parametrize("asset_id", ASSET_IDS)
def test_exact_match_with_the_committed_registry_returns_e_reserved(asset_id, monkeypatch):
    """A candidate whose waveform equals an asset is refused with E_RESERVED.

    No recipe renders to an asset, so `render` is patched to return the asset's samples.
    `reserved=None` loads the committed registry: the path every proposer uses.
    """
    asset = BY_ID[asset_id]
    real = render(DEMO, Profile.P2)
    fake = dataclasses.replace(real, samples=asset.samples)
    monkeypatch.setattr(validate_mod, "render", lambda recipe, profile: fake)
    result = validate(DEMO, Profile.P2, reserved=None)
    assert result.codes == ("E_RESERVED",)
    assert result.pcm_sha256 == asset.pcm_sha256
    assert f"waveform identical to reserved {asset_id}" in result.messages[0]


def test_exact_match_with_a_registry_copy_holding_a_motif_hash():
    motif = render(DEMO, Profile.P2)
    entries = [e.to_dict() for e in load_reserved_registry().entries]
    entries[3] = {**entries[3], "pcm_sha256": motif.pcm_sha256}
    copy = ReservedRegistry.from_dict(
        {"registry_version": 1, "renderer_version": RENDERER_VERSION, "entries": entries}
    )
    result = validate(DEMO, Profile.P2, reserved=copy)
    assert result.codes == ("E_RESERVED",)
    assert "ready-cue" in result.messages[0]
    # Profile-independent entry: the same waveform hash is reserved for every profile, but
    # the same recipe renders to different bytes under another profile.
    assert validate(DEMO, Profile.P1, reserved=copy).ok


# --- acceptance: 0 reserved assets are admissible as motifs -------------------------


def test_no_asset_has_a_motif_length_so_no_recipe_renders_to_one():
    lengths = {a.n_samples for a in ASSETS}
    assert tuple(t * 48 for t in TOTAL_MS) == MOTIF_SAMPLES
    assert lengths.isdisjoint(MOTIF_SAMPLES)
    for total in TOTAL_MS:  # render() always returns total_ms * 48 samples
        r = render(Recipe(total, (0, 0, 0), (1, 1, 1), (20, 20), (1.0, 1.0, 1.0)), Profile.P1)
        assert r.n_samples == total * 48 and r.n_samples not in lengths


@pytest.mark.parametrize("asset_id", ASSET_IDS)
def test_composer_refuses_an_asset_as_a_motif(asset_id):
    asset = BY_ID[asset_id]
    motif = AtomAudio.from_rendered("K-r1", render(DEMO, asset.profile or Profile.P1))
    as_atom = AtomAudio("K-a1", asset.profile or Profile.P1, asset.pcm)
    with pytest.raises(CompositionError) as err:
        compose_message(as_atom, motif)
    assert err.value.code == "E_MOTIF_LENGTH"


def test_synthetic_books_never_collide_with_reserved_assets():
    # Stand-in for the fallback banks (#15), which go through the same validate() path.
    reserved = {e.pcm_sha256 for e in load_reserved_registry().entries}
    for profile in Profile:
        for recipe in synthetic_recipes(profile).values():
            result = validate(recipe, profile, reserved=None)
            assert "E_RESERVED" not in result.codes
            assert result.pcm_sha256 not in reserved


recipes = st.builds(
    Recipe,
    st.sampled_from(TOTAL_MS),
    st.tuples(*[st.sampled_from(PITCHES)] * 3),
    st.tuples(*[st.sampled_from(RHYTHM_WEIGHTS)] * 3),
    st.tuples(*[st.sampled_from(GAPS_MS)] * 2),
    st.tuples(*[st.sampled_from(AMPLITUDES)] * 3),
)


@settings(max_examples=150, deadline=None)
@given(recipe=recipes, profile=st.sampled_from(list(Profile)))
def test_no_domain_recipe_collides_with_a_reserved_asset(recipe, profile):
    reserved = {e.pcm_sha256 for e in load_reserved_registry().entries}
    result = validate(recipe, profile, reserved=None)
    assert "E_RESERVED" not in result.codes
    assert result.pcm_sha256 is not None and result.pcm_sha256 not in reserved
    assert len(result.rendered.pcm) // 2 not in {a.n_samples for a in ASSETS}


# --- documentation ------------------------------------------------------------------


def test_doc_lists_every_asset_hash():
    text = (SOUND / "docs" / "nonlexical.md").read_text(encoding="utf-8")
    for a in ASSETS:
        assert f"`{a.id}`" in text
        assert a.pcm_sha256 in text
