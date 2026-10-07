"""Simulation fixtures cannot manufacture human or calibration authority."""
import importlib.util
import json
from pathlib import Path

import pytest

from tools.prepare_joined_engineering import PreparationError, digest
from tools.prepare_mock_visits import ROOT, attestation, prepare, rating_items


@pytest.fixture(scope="module")
def demo_package(tmp_path_factory):
    pytest.importorskip("numpy", reason="Actual mock package fixtures run in the locked sound CI on all three OS")
    spec = importlib.util.spec_from_file_location("mock_example", ROOT / "sound/tools/build_example_package.py")
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    directory = tmp_path_factory.mktemp("mock-source") / "package"
    example.build_a_example(directory)
    manifest = json.loads((directory / "manifest.json").read_bytes())
    return directory, manifest["package_sha256"]


def test_actual_producer_package_generates_closed_simulation_materials(demo_package, tmp_path):
    directory, package_hash = demo_package
    destination = tmp_path / ".local" / "mock"
    index = prepare(directory, package_hash, destination)
    assert index["study"] == "A" and index["visits"] == ["D0", "D7"]
    assert index["participant_admission"] is False
    assert index["acoustic_qualification"] is False
    assert index["materials_reviewed"] is False
    assert digest((destination / "fixture-provenance.local.json").read_bytes()) == index["fixture_set_sha256"]
    reviews = []
    for relative, expected in index["files"].items():
        raw = (destination / relative).read_bytes()
        assert digest(raw) == expected
        if relative.endswith(".json"):
            value = json.loads(raw)
            if "role" in value:
                reviews.append(value)
                assert set(value) == {"version", "scope", "role", "fixture_set_sha256", "bindings"}
                assert value["scope"] == "SIMULATION_TEST"
                assert value["fixture_set_sha256"] == index["fixture_set_sha256"]
                assert "approved" not in value and "methodology_sha256" not in value
    assert len(reviews) == 6
    catalog = json.loads((destination / "teaching/catalog.local.json").read_bytes())
    assert len(catalog["content"]) == 34  # 16 atoms, 18 trained phrases, no novel phrases.
    assert all(row["definition"].startswith("SIMULATION TEST") for row in catalog["content"])
    assert all(row["image_id"] == "mock" for row in catalog["content"])


def test_wrong_pin_and_public_output_refused_before_creation(demo_package, tmp_path):
    directory, package_hash = demo_package
    for output, pin in [(tmp_path / "public", package_hash), (tmp_path / ".local/mock", "0" * 64)]:
        with pytest.raises(PreparationError):
            prepare(directory, pin, output)
        assert not output.exists()


def test_existing_evidence_is_never_overwritten(demo_package, tmp_path):
    directory, package_hash = demo_package
    output = tmp_path / ".local/mock"
    output.mkdir(parents=True)
    (output / "preserve.txt").write_bytes(b"retained evidence")
    with pytest.raises(PreparationError):
        prepare(directory, package_hash, output)
    assert list(output.iterdir()) == [output / "preserve.txt"]
    assert (output / "preserve.txt").read_bytes() == b"retained evidence"


def test_package_source_is_unchanged(demo_package, tmp_path):
    directory, package_hash = demo_package
    before = {p.relative_to(directory).as_posix(): digest(p.read_bytes()) for p in directory.rglob("*") if p.is_file()}
    prepare(directory, package_hash, tmp_path / ".local/mock")
    after = {p.relative_to(directory).as_posix(): digest(p.read_bytes()) for p in directory.rglob("*") if p.is_file()}
    assert after == before


def test_speech_hash_cannot_be_supplied_without_bank(demo_package, tmp_path):
    with pytest.raises(PreparationError, match="MOCK_SPEECH_PAIR"):
        prepare(*demo_package, tmp_path / ".local/mock", speech_manifest_hash="a" * 64)


def test_rating_items_cover_all_supported_visits_and_refuse_other_contexts():
    assert [r["id"] for r in rating_items("A", "D0")] == ["difficulty", "pleasantness"]
    assert [r["id"] for r in rating_items("A", "D7")] == ["usability", "difficulty"]
    for visit in ("V1", "V2", "V3", "W1", "W4"):
        assert len(rating_items("B", visit)) == 5
    for study, visit in [("A", "W1"), ("B", "D0"), ("C", "V1")]:
        with pytest.raises(PreparationError):
            rating_items(study, visit)


@pytest.mark.parametrize("role,hash_value", [("approval", "a" * 64), ("grammar", "invalid")])
def test_no_unknown_attestation_authority(role, hash_value):
    with pytest.raises(PreparationError):
        attestation(role, hash_value, {})
