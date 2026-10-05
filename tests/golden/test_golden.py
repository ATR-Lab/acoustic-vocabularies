"""Golden-file and cross-machine determinism tests (#12).

`tests/golden/manifest.json` holds the expected hashes of the golden set
(`av_sound.golden`). CI runs this module on Linux, macOS and Windows, on x86_64 and
arm64 (`.github/workflows/sound-golden.yml`), and compares the digests across
runners. Any difference is a defect: fix the arithmetic, never loosen the test
(`sound/docs/golden.md`).
"""

from __future__ import annotations

import dataclasses
import importlib.util
import itertools
import json
import re
import sys
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from av_sound import (
    RENDERER_VERSION,
    VALIDATOR_VERSION,
    Profile,
    Recipe,
    golden,
    load_reserved_registry,
    renderer_hash,
    snapshot_digest,
)
from av_sound.golden import GoldenItem, Mismatch
from av_sound.grammar import ATOM_IDS, HELDOUT_MESSAGE_IDS, TRAINED_MESSAGE_IDS
from av_sound.nonlexical import ASSET_IDS, ASSET_SPEC_VERSION
from av_sound.recipe import AMPLITUDES, GAPS_MS, PITCHES, RHYTHM_WEIGHTS, TOTAL_MS
from av_sound.renderer import MIN_EVENT_SAMPLES, Rendered
from av_sound.store import RECORD_VERSION, validator_code_hash
from av_sound.synthetic import synthetic_recipes

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "tests" / "golden" / "manifest.json"
WAV_DIR = REPO / "tests" / "golden" / "wav"
SOUND = REPO / "sound"
N_RECIPES = 61


def load_tool(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SOUND / "tools" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def committed() -> dict[str, Any]:
    return golden.load_manifest(MANIFEST)


@pytest.fixture(scope="module")
def live(committed: dict[str, Any]) -> list[GoldenItem]:
    """The golden set recomputed on this machine from the manifest's recorded inputs."""
    return golden.compute_items(golden.specs_of(committed))


def items_of(manifest: dict[str, Any], category: str) -> list[dict[str, Any]]:
    return [i for i in manifest["items"] if i["category"] == category]


def by_id(items: list[Any]) -> dict[str, Any]:
    return {(i.id if isinstance(i, GoldenItem) else i["id"]): i for i in items}


# --- Acceptance: hashes reproduce, twice, on this runner ----------------------------------


def test_every_golden_hash_reproduces_on_this_machine(committed, live):
    mismatches = golden.verify_manifest(committed, live)
    assert mismatches == [], "\n".join(map(str, mismatches[:20]))


def test_manifest_is_up_to_date_with_the_definitions():
    data = MANIFEST.read_bytes()
    assert b"\r" not in data and data.endswith(b"}\n")
    assert golden.manifest_text(golden.build_manifest()) == data.decode("utf-8")


def test_rendering_the_golden_set_twice_gives_identical_hashes(committed, live):
    # Second pass in reverse order: no state may leak from one item into the next.
    again = golden.compute_items(list(reversed(golden.specs_of(committed))))
    assert [i.to_dict() for i in again] == [i.to_dict() for i in live]
    assert [i.pcm for i in again] == [i.pcm for i in live]
    assert golden.digests(again) == golden.digests(live) == committed["digests"]


def test_manifest_header(committed):
    assert committed["format"] == golden.FORMAT
    assert committed["format_version"] == golden.FORMAT_VERSION
    assert committed["synthetic"] is True
    assert committed["renderer_version"] == RENDERER_VERSION
    assert committed["renderer_hash"] == renderer_hash()
    assert committed["validator_version"] == VALIDATOR_VERSION
    assert committed["validator_hash"] == validator_code_hash()
    assert committed["asset_spec_version"] == ASSET_SPEC_VERSION
    assert committed["store_record_version"] == RECORD_VERSION
    assert committed["version_rules"] == {c: list(r) for c, r in golden.VERSION_RULES.items()}
    assert set(committed["version_rules"]) == set(golden.CATEGORIES)
    ids = [i["id"] for i in committed["items"]]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    assert all(i["id"].split("/")[0] == i["category"] for i in committed["items"])
    assert committed["counts"] == {
        "recipe": 3 * N_RECIPES,
        "atom": 48,
        "message": 96,
        "nonlexical": 7,
        "store": 3,
    }
    assert golden.header_mismatches(committed) == []


def test_header_mismatches_report_versions_and_digests(committed):
    tampered = json.loads(json.dumps(committed))
    tampered["renderer_version"] = "9.9.9"
    tampered["items"][0]["outputs"]["pcm_sha256"] = "0" * 64
    fields = {m.field for m in golden.header_mismatches(tampered)}
    assert fields == {"renderer_version", "digests"}


# --- Acceptance: a one-sample change fails ------------------------------------------------


def perturb(monkeypatch, recipe: Recipe, profile: Profile, index: int) -> None:
    """Make the renderer, as the golden module sees it, change one sample of one motif."""
    original = golden.render

    def render(r: Recipe | dict[str, Any], p: Profile | str) -> Rendered:
        out = original(r, p)
        if out.recipe == recipe and out.profile == profile:
            samples = out.samples.copy()
            samples[index] += 1
            samples.setflags(write=False)
            return dataclasses.replace(out, samples=samples)
        return out

    monkeypatch.setattr(golden, "render", render)


def test_one_sample_change_in_a_rendered_waveform_is_reported(committed, monkeypatch):
    target = "recipe/spec-worked-example/P2"
    item = by_id(committed["items"])[target]
    # Sample 9,000 lies in the first gap (8,640 to 10,559): 0 becomes 1 LSB.
    perturb(monkeypatch, Recipe.from_dict(item["inputs"]["recipe"]), Profile.P2, 9000)
    mismatches = golden.verify_manifest(committed)
    assert {(m.item_id, m.field) for m in mismatches} == {
        (target, "outputs.pcm_sha256"),
        (target, "outputs.file_sha256"),
    }
    assert all(m.expected == item["outputs"][m.field.split(".")[1]] for m in mismatches)


def test_one_sample_change_in_an_atom_reaches_its_messages_and_the_store(committed, monkeypatch):
    perturb(monkeypatch, synthetic_recipes(Profile.P1)["K-a1"], Profile.P1, 100)
    mismatches = golden.verify_manifest(committed)
    expected_ids = {"atom/DEMO-P1/K-a1", "store/DEMO-P1"}
    expected_ids |= {f"message/DEMO-P1/K-a1-r{i}" for i in range(1, 5)}
    assert {m.item_id for m in mismatches} == expected_ids
    store_fields = {m.field for m in mismatches if m.item_id == "store/DEMO-P1"}
    assert store_fields == {"outputs.atoms_match", "outputs.messages_match"}


def test_one_sample_change_in_a_golden_wav_file_is_reported(committed, live, tmp_path):
    chosen = [i for i in live if i.category in {"nonlexical", "recipe"}][:40]
    assert golden.write_wavs(chosen, tmp_path) == 40
    assert golden.check_wav_dir(chosen, tmp_path, require_all=True) == []
    target = chosen[0]
    path = golden.wav_path(tmp_path, target.id)
    data = bytearray(path.read_bytes())
    k = 44 + 2 * 50  # sample 50
    value = int.from_bytes(data[k : k + 2], "little", signed=True) + 1
    data[k : k + 2] = value.to_bytes(2, "little", signed=True)
    path.write_bytes(bytes(data))
    found = golden.check_wav_dir(chosen, tmp_path)
    assert {(m.item_id, m.field) for m in found} == {
        (target.id, "file_sha256"),
        (target.id, "pcm"),
    }
    assert any("sample 50" in str(m) for m in found)
    # Against the manifest entries alone (no samples) the file hash still catches it.
    manifest_items = [by_id(committed["items"])[i.id] for i in chosen]
    assert [(m.item_id, m.field) for m in golden.check_wav_dir(manifest_items, tmp_path)] == [
        (target.id, "file_sha256")
    ]


def test_wav_check_reports_stray_missing_pointer_and_malformed_files(live, tmp_path):
    chosen = [i for i in live if i.category == "nonlexical"]
    golden.write_wavs(chosen, tmp_path)
    golden.wav_path(tmp_path, chosen[0].id).unlink()
    (tmp_path / "nonlexical" / "stray.wav").write_bytes(b"RIFF")
    pointer = golden.wav_path(tmp_path, chosen[1].id)
    pointer.write_bytes(b"version https://git-lfs.github.com/spec/v1\noid sha256:00\nsize 1\n")
    truncated = golden.wav_path(tmp_path, chosen[2].id)
    truncated.write_bytes(truncated.read_bytes()[:-2])
    swapped = golden.wav_path(tmp_path, chosen[3].id)  # click-action <- click-grammar-demo
    swapped.write_bytes(golden.wav_path(tmp_path, chosen[4].id).read_bytes())
    found = {
        (m.item_id, m.field): m for m in golden.check_wav_dir(chosen, tmp_path, require_all=True)
    }
    assert set(found) == {
        ("nonlexical/stray", "file"),
        (chosen[0].id, "file"),
        (chosen[1].id, "file"),
        (chosen[2].id, "file_sha256"),
        (chosen[2].id, "file"),
        (chosen[3].id, "file_sha256"),
        (chosen[3].id, "pcm"),
    }
    assert "LFS pointer" in found[(chosen[1].id, "file")].actual
    assert found[(chosen[3].id, "pcm")].actual == "13824 samples instead of 192"
    assert found[(chosen[0].id, "file")].actual == "missing"


def test_committed_golden_wavs_match(live):
    """WAVs under tests/golden/wav/ (Git LFS) are byte-compared when present."""
    if not WAV_DIR.is_dir() or not any(WAV_DIR.rglob("*.wav")):
        pytest.skip("no golden WAVs committed: Git LFS uploads are disabled (sound/docs/golden.md)")
    mismatches = golden.check_wav_dir(live, WAV_DIR)
    assert mismatches == [], "\n".join(map(str, mismatches[:20]))


def test_load_manifest_rejects_other_json(tmp_path):
    other = tmp_path / "other.json"
    other.write_text('{"format": "something else"}', encoding="utf-8")
    with pytest.raises(ValueError, match="not an av-sound golden manifest"):
        golden.load_manifest(other)


def test_mismatch_text():
    m = Mismatch("recipe/x/P1", "outputs.pcm_sha256", "aa", "bb")
    assert str(m) == "recipe/x/P1: outputs.pcm_sha256: expected 'aa', got 'bb'"


def test_compare_items_reports_missing_extra_and_input_changes(committed):
    expected = committed["items"][:3]
    actual = json.loads(json.dumps(committed["items"][1:4]))
    actual[0]["inputs"]["profile"] = "P9"
    del actual[1]["outputs"]["n_samples"]
    found = [(m.item_id, m.field) for m in golden.compare_items(expected, actual)]
    ids = [i["id"] for i in committed["items"][:4]]
    assert found == [
        (ids[0], "item"),
        (ids[1], "inputs"),
        (ids[2], "outputs.n_samples"),
        (ids[3], "item"),
    ]


# --- The golden set covers the corners of the domain --------------------------------------


def recipe_rows(committed: dict[str, Any]) -> list[dict[str, Any]]:
    return items_of(committed, "recipe")


def test_golden_recipes_cover_the_domain_corners(committed):
    rows = recipe_rows(committed)
    names = Counter(r["inputs"]["name"] for r in rows)
    assert len(names) == N_RECIPES and set(names.values()) == {3}
    for name in names:
        profiles = {r["inputs"]["profile"] for r in rows if r["inputs"]["name"] == name}
        assert profiles == {"P1", "P2", "P3"}
    recipes = {r["inputs"]["name"]: Recipe.from_dict(r["inputs"]["recipe"]) for r in rows}
    values = list(recipes.values())
    assert {r.total_ms for r in values} == set(TOTAL_MS)
    assert {p for r in values for p in r.pitches} == set(PITCHES)
    for j in range(3):
        assert {-6, 0, 6} <= {r.pitches[j] for r in values}
        assert {r.rhythm_weights[j] for r in values} == set(RHYTHM_WEIGHTS)
        assert {r.amplitudes[j] for r in values} == set(AMPLITUDES)
    for uniform in ((-6, -6, -6), (0, 0, 0), (6, 6, 6)):
        assert {r.total_ms for r in values if r.pitches == uniform} == set(TOTAL_MS)
    assert any(len(set(r.pitches)) == 3 and {-6, 6} <= set(r.pitches) for r in values)
    assert {r.gaps_ms for r in values} == set(itertools.product(GAPS_MS, repeat=2))
    assert {(a, a, a) for a in AMPLITUDES} <= {r.amplitudes for r in values}
    assert set(itertools.permutations(AMPLITUDES)) <= {r.amplitudes for r in values}
    assert {(1, 1, 1), (4, 4, 4)} <= {r.rhythm_weights for r in values}


def test_golden_recipes_include_the_event_length_boundaries(committed):
    rows = [r for r in recipe_rows(committed) if r["inputs"]["profile"] == "P1"]
    legal = [r["outputs"]["event_samples"] for r in rows if not r["outputs"]["short_event"]]
    short = [r["outputs"]["event_samples"] for r in rows if r["outputs"]["short_event"]]
    assert all(min(e) >= MIN_EVENT_SAMPLES for e in legal)
    assert all(min(e) < MIN_EVENT_SAMPLES for e in short)
    assert min(min(e) for e in legal) == MIN_EVENT_SAMPLES == 2880
    # The shortest legal event in every position, and flagged short events.
    assert {e.index(2880) for e in legal if 2880 in e} == {0, 1, 2}
    assert len(short) == 3 and max(min(e) for e in short) == 2812  # longest rejected event
    assert min(min(e) for e in short) == 1760
    # Rounding (spec D1): some event lengths are not exact multiples of D / W.
    inexact = 0
    for r in rows:
        recipe = Recipe.from_dict(r["inputs"]["recipe"])
        d = (recipe.total_ms - sum(recipe.gaps_ms)) * 48
        inexact += any(d * w % sum(recipe.rhythm_weights) for w in recipe.rhythm_weights)
    assert inexact >= 10
    assert not any(r["outputs"]["overflow"] for r in recipe_rows(committed))


def test_golden_recipes_pin_the_renderer_spec_examples(committed):
    items = by_id(committed["items"])
    worked = items["recipe/spec-worked-example/P2"]["outputs"]
    assert worked["pcm_sha256"] == (
        "4c0467de354c076c0b30bc9af794e31384afdf161af24605fb621cc455c35d87"
    )  # renderer spec section 5
    assert worked["file_sha256"] == (
        "32ade67b9c996e11deb3e5c1ee4b51381c0d49c6e22b4bbce9095980e119fca8"
    )
    assert worked["peak"] == 13865
    assert items["recipe/worst-crest-factor/P1"]["outputs"]["peak"] == 20238  # spec D6


def test_identical_waveforms_are_exactly_the_documented_ones(committed):
    """Uniform amplitude triples (spec D5) and equal layouts (1-1-1 vs 4-4-4) share bytes."""
    for profile in Profile:
        groups: dict[str, list[str]] = {}
        for r in recipe_rows(committed):
            if r["inputs"]["profile"] == profile.value:
                groups.setdefault(r["outputs"]["pcm_sha256"], []).append(r["inputs"]["name"])
        shared = sorted(sorted(v) for v in groups.values() if len(v) > 1)
        assert shared == [
            ["amps-0.6-0.6-0.6", "amps-0.8-0.8-0.8", "amps-1.0-1.0-1.0"],
            ["weights-1-1-1", "weights-4-4-4"],
        ]


# --- Messages, nonlexical assets and the store round trip ---------------------------------


def test_message_goldens_agree_with_the_composition_vectors(committed):
    vectors = json.loads((SOUND / "testvectors/composition/vectors.json").read_text("utf-8"))
    items = by_id(committed["items"])
    for book in vectors["books"]:
        for atom in book["atoms"]:
            out = items[f"atom/{book['book_id']}/{atom['atom_id']}"]["outputs"]
            assert (out["pcm_sha256"], out["file_sha256"]) == (
                atom["pcm_sha256"],
                atom["file_sha256"],
            )
        for m in book["messages"]:
            item = items[f"message/{book['book_id']}/{m['message_id']}"]
            assert item["outputs"]["pcm_sha256"] == m["composite_sha256"]
            assert item["outputs"]["n_samples"] == m["n_samples"]
            assert item["inputs"]["heldout"] == m["heldout"]


def test_heldout_messages_have_a_hash_but_no_audio(committed, live, tmp_path):
    for book in ("DEMO-P1", "DEMO-P2", "DEMO-P3"):
        rows = [r for r in items_of(committed, "message") if r["inputs"]["book_id"] == book]
        held = {r["inputs"]["message_id"] for r in rows if r["inputs"]["heldout"]}
        trained = [r for r in rows if not r["inputs"]["heldout"]]
        assert held == set(HELDOUT_MESSAGE_IDS)
        assert {r["inputs"]["message_id"] for r in trained} == set(TRAINED_MESSAGE_IDS)
        assert all(r["outputs"]["file_sha256"] is None for r in rows if r["inputs"]["heldout"])
        assert all(r["outputs"]["composite_matches"] for r in trained)
    heldout_live = [i for i in live if i.category == "message" and i.inputs["heldout"]]
    assert len(heldout_live) == 42 and all(i.pcm is None for i in heldout_live)
    n = golden.write_wavs(live, tmp_path)
    assert n == 3 * N_RECIPES + 48 + 54 + 7
    assert not (tmp_path / "store").exists()
    assert not any(golden.wav_path(tmp_path, i.id).exists() for i in heldout_live)


def test_nonlexical_goldens_agree_with_the_reserved_registry(committed):
    registry = load_reserved_registry()
    items = by_id(committed["items"])
    assert [e.id for e in registry.entries] == list(ASSET_IDS)
    for entry in registry.entries:
        out = items[f"nonlexical/{entry.id}"]["outputs"]
        assert (out["pcm_sha256"], out["file_sha256"], out["n_samples"]) == (
            entry.pcm_sha256,
            entry.file_sha256,
            entry.n_samples,
        )


def test_store_round_trip(committed):
    items = by_id(committed["items"])
    growth = json.loads((SOUND / "testvectors/store/growth.json").read_text("utf-8"))
    for profile, grown in zip(Profile, growth["books"], strict=True):
        book = f"DEMO-{profile.value}"
        out = items[f"store/{book}"]["outputs"]
        assert out["readback_ok"] and out["verify_ok"] and out["verify_codes"] == []
        assert out["atoms_match"] and out["messages_match"] and out["messages_checked"] == 32
        assert (out["n_entries"], out["n_records"]) == (16, 18)  # create, 16 commits, freeze
        atoms = {a: items[f"atom/{book}/{a}"]["outputs"]["pcm_sha256"] for a in ATOM_IDS}
        assert out["snapshot_sha256"] == snapshot_digest(atoms)
        # Same first record as the growth vectors (#11): same book, clock and threshold.
        assert out["create_head"] == grown["create_head"]
        assert len({out["create_head"], out["commit_head"], out["freeze_head"]}) == 3


def test_store_round_trip_records_errors_instead_of_raising(committed, live):
    spec = golden.specs_of(committed)
    store_spec = by_id(spec)["store/DEMO-P1"]
    atoms = {i.inputs["atom_id"]: i for i in live if i.id.startswith("atom/DEMO-P1/")}
    strict = {**store_spec, "inputs": {**store_spec["inputs"], "threshold": "0.99"}}
    out = golden.store_round_trip(strict, atoms)
    assert set(out) == {"error"} and "CommitRejected" in out["error"]
    with pytest.raises(ValueError, match="reserved"):
        golden.store_round_trip(
            {**store_spec, "inputs": {**store_spec["inputs"], "reserved": "registry"}}, atoms
        )


def test_compute_items_rejects_inconsistent_specs(committed):
    spec = golden.specs_of(committed)
    with pytest.raises(ValueError, match="unknown category"):
        golden.compute_items([{**spec[0], "category": "video"}])
    with pytest.raises(ValueError, match="duplicate"):
        golden.compute_items([spec[0], spec[0]])
    specs = by_id(spec)
    message = specs["message/DEMO-P1/K-a1-r2"]  # held out (H-V1)
    atoms = [specs["atom/DEMO-P1/K-a1"], specs["atom/DEMO-P1/K-r2"]]
    flipped = {**message, "inputs": {**message["inputs"], "heldout": False}}
    with pytest.raises(ValueError, match="held-out flag"):
        golden.compute_items([*atoms, flipped])


def test_fixed_clock_and_labels():
    clock = golden.fixed_clock()
    assert (clock() - golden.STORE_CLOCK_START).total_seconds() == 0
    assert (clock() - golden.STORE_CLOCK_START).total_seconds() == 1
    assert golden.demo_label("K-a1") == "ADD_ONE" and golden.demo_label("Q-r4") == "H"


# --- The regeneration tool ---------------------------------------------------------------


def test_make_goldens_check_digest_and_summary(tmp_path, capsys):
    tool = load_tool("make_goldens")
    out = tmp_path / "digest" / "test-runner.json"
    code = tool.main(["--check", "--summary", "--runner", "test-runner", "--digest-out", str(out)])
    assert code == 0
    record = json.loads(out.read_text(encoding="utf-8"))
    assert record["runner"] == "test-runner" and record["matches_manifest"] is True
    assert record["digests"] == record["manifest_digests"]
    assert "all golden hashes reproduce" in capsys.readouterr().out


def test_make_goldens_writes_and_detects_drift(committed, tmp_path, capsys):
    tool = load_tool("make_goldens")
    target = tmp_path / "manifest.json"
    assert tool.main(["--manifest", str(target)]) == 0
    assert target.read_bytes() == MANIFEST.read_bytes()
    tampered = json.loads(json.dumps(committed))
    tampered["items"][-1]["outputs"]["freeze_head"] = "0" * 64
    target.write_text(golden.manifest_text(tampered), encoding="utf-8", newline="\n")
    assert tool.main(["--check", "--manifest", str(target)]) == 1
    err = capsys.readouterr().err
    assert "reproduce: store/DEMO-P3: outputs.freeze_head" in err
    assert "out of date: store/DEMO-P3: outputs.freeze_head" in err
    assert tool.main(["--check", "--manifest", str(tmp_path / "absent.json")]) == 1


def test_make_goldens_wav_dir(tmp_path, capsys):
    tool = load_tool("make_goldens")
    wavs = tmp_path / "wav"
    assert tool.main(["--check", "--wav-dir", str(wavs)]) == 0
    assert (wavs / "manifest.json").read_bytes() == MANIFEST.read_bytes()
    assert len(list(wavs.rglob("*.wav"))) == 3 * N_RECIPES + 48 + 54 + 7
    assert "WAV files" in capsys.readouterr().err


def test_ci_matrix_compare_list_and_docs_agree():
    workflow = (REPO / ".github" / "workflows" / "sound-golden.yml").read_text(encoding="utf-8")
    entry = r"- \{ label: ([\w-]+), os: ([\w.-]+), python: cpython-3\.11\.15-(\w+)-(\w+)-\w+ \}"
    rows = re.findall(entry, workflow)
    for label, _, system, cpu in rows:
        assert label == f"{system}-{cpu.replace('aarch64', 'arm64')}"  # native Python
    matrix = [(label, runner) for label, runner, _, _ in rows]
    runners = re.search(r"GOLDEN_RUNNERS: (\S+)", workflow)
    assert matrix and runners and [label for label, _ in matrix] == runners.group(1).split(",")
    doc = (SOUND / "docs" / "golden.md").read_text(encoding="utf-8")
    for label, runner in matrix:
        assert f"| `{label}` | `{runner}` |" in doc
    sound_ci = (REPO / ".github" / "workflows" / "sound.yml").read_text(encoding="utf-8")
    assert "-p no:cacheprovider tests/sound tests/golden" in sound_ci
