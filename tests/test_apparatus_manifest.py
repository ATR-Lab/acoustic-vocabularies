"""Synthetic files only. No station, G4 approval, release or timing qualification."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("apparatus_manifest", ROOT / "tools/apparatus_manifest.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture
def draft(tmp_path):
    config = json.loads((ROOT / "apparatus/examples/apparatus-manifest-input.example.json").read_text())
    root = tmp_path / ".local"
    root.mkdir()
    return root, config


def save(draft):
    root, config = draft
    data = m.canonical(config)
    path = root / "input.json"
    path.write_bytes(data)
    return str(path), m.digest(data)


def artifact(draft, name="app_build", data=b"synthetic binary bytes", expected=False):
    root, config = draft
    path = root / (name + ".bin")
    path.write_bytes(data)
    source = {"path": path.name}
    if expected:
        source["expected_sha256"] = m.digest(data)
    config["artifacts"][name] = {"status": "recorded", "files": [source]}
    return path


def g4(draft):
    root, config = draft
    values = {k: ("3" * 64 if k.endswith("hash") else "SYNTHETIC-" + k)
              for k in m.GENERATION_FIELDS}
    value = {"schema_version": 1, "record_type": "g4-generation-freeze-handoff",
             "freeze_reference": "SYNTHETIC-NO-APPROVAL", "fields": values}
    data = m.canonical(value)
    path = root / "g4.json"
    path.write_bytes(data)
    config["g4_freeze"] = {"status": "recorded", "path": path.name, "expected_sha256": m.digest(data)}
    return path, value


def test_pending_example_has_every_field_no_null_and_cannot_claim_acceptance(draft):
    result = m.collect(*save(draft))
    m.validate(result, "apparatus-manifest.schema.json")
    assert result["participant_qualified"] is False
    assert result["template_reconciliation"]["status"] == "pending"
    assert all(x["status"] == "pending" for x in result["fields"].values())
    assert len(result["fields"]) == 47
    assert b"null" not in m.canonical(result)


def test_actual_hashes_links_and_independent_verify(draft):
    for name in ("app_build", "scene_usd", "reset_snapshot", "g1_dex3_asset", "schedules",
                 "audio_onset_calibration", "rendered_view_recording", "timing_validation"):
        artifact(draft, name, name.encode(), expected=True)
    result = m.collect(*save(draft))
    assert result["fields"]["app_build_sha256"]["value"] == hashlib.sha256(b"app_build").hexdigest()
    assert result["fields"]["schedule_sha256"]["value"] == [m.digest(b"schedules")]
    assert result["fields"]["rendered_view_recording"]["value"].endswith("rendered_view_recording.bin")
    output = draft[0] / "manifest.json"
    m.write_new(str(output), result)
    report = m.verify(str(output), m.digest(output.read_bytes()))
    assert report["recorded_artifact_files"] == 8
    assert report["release_accepted"] is False


def test_g4_fields_copied_only_from_pinned_record(draft):
    _, record = g4(draft)
    result = m.collect(*save(draft))
    for name in m.GENERATION_FIELDS:
        assert result["fields"][name]["value"] == record["fields"][name]
        assert result["fields"][name]["source"] == "g4:" + draft[1]["g4_freeze"]["expected_sha256"]
    assert not m.public_summary(result)["g4_approval_verified"]


@pytest.mark.parametrize("field", m.GENERATION_FIELDS)
def test_generation_override_in_config_refused(draft, field):
    draft[1]["fields"][field] = m.recorded("invented", "caller")
    with pytest.raises(m.ManifestFault, match="SCHEMA_INVALID"):
        m.collect(*save(draft))


def test_g4_changed_after_pin_refused(draft):
    path, _ = g4(draft)
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(m.ManifestFault, match="HASH_MISMATCH"):
        m.collect(*save(draft))


@pytest.mark.parametrize("change", ["artifact", "config", "g4", "forged_derived"])
def test_verify_refuses_changed_source_or_forged_manifest(draft, change):
    source = artifact(draft)
    g4_path, _ = g4(draft)
    args = save(draft)
    result = m.collect(*args)
    if change == "artifact": source.write_bytes(b"replacement")
    elif change == "config": Path(args[0]).write_bytes(Path(args[0]).read_bytes() + b" ")
    elif change == "g4": g4_path.write_bytes(g4_path.read_bytes() + b" ")
    else: result["fields"]["app_build_sha256"]["value"] = "4" * 64
    path = draft[0] / "manifest.json"
    m.write_new(str(path), result)
    with pytest.raises(m.ManifestFault, match="HASH_MISMATCH|MANIFEST_RECOMPUTE_MISMATCH"):
        m.verify(str(path), m.digest(path.read_bytes()))


def test_missing_configured_file_is_not_converted_to_pending(draft):
    artifact(draft).unlink()
    with pytest.raises(m.ManifestFault, match="FILE_MISSING"):
        m.collect(*save(draft))


def test_wrong_independent_artifact_hash_fails(draft):
    artifact(draft, expected=True)
    draft[1]["artifacts"]["app_build"]["files"][0]["expected_sha256"] = "1" * 64
    with pytest.raises(m.ManifestFault, match="HASH_MISMATCH"):
        m.collect(*save(draft))


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf")])
def test_null_nonfinite_rejected(bad):
    with pytest.raises((m.ManifestFault, ValueError)):
        m.strict_json(json.dumps({"value": bad}).encode())


def test_duplicate_json_keys_refused():
    with pytest.raises(m.ManifestFault, match="DUPLICATE_JSON_KEY"):
        m.strict_json(b'{"a":1,"a":2}')


@pytest.mark.parametrize("mutation", [
    lambda c: c["fields"]["sample_rate"].update(status="recorded", value=44100, source="measured"),
    lambda c: c["fields"].update(hidden_private_target="forbidden"),
    lambda c: c["fields"]["headset_units"].update(status="not_applicable", reason=""),
    lambda c: c.update(template_reconciliation={"status":"recorded","value":"invented"}),
    lambda c: c.update(schema_version=True),
])
def test_closed_schema_strict_types_and_reason(draft, mutation):
    mutation(draft[1])
    with pytest.raises(m.ManifestFault, match="SCHEMA_INVALID"):
        m.collect(*save(draft))


@pytest.mark.parametrize("raw", ["\\\\server\\share\\file", "//server/share/file", "../escape"])
def test_nonlocal_or_traversal_refused(draft, raw):
    with pytest.raises(m.ManifestFault):
        m.local_path(raw, draft[0])


def test_links_refused(draft):
    target = artifact(draft)
    link = draft[0] / "hardlink.bin"
    os.link(target, link)
    with pytest.raises(m.ManifestFault, match="REGULAR_UNLINKED_FILE_REQUIRED"):
        m.collect(*save(draft))


def test_symlink_ancestor_refused(draft):
    target = draft[0] / "real"
    target.mkdir()
    (target / "data").write_bytes(b"x")
    link = draft[0] / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("OS has not granted symlink creation; Linux CI covers this path")
    with pytest.raises(m.ManifestFault, match="LINK_FORBIDDEN"):
        m.hash_file(str(link / "data"))


def test_file_change_during_read_fails(draft, monkeypatch):
    path = artifact(draft)
    original = m.inspect_path
    calls = 0
    def changed(item, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            path.write_bytes(b"other bytes")
        return original(item, **kwargs)
    monkeypatch.setattr(m, "inspect_path", changed)
    with pytest.raises(m.ManifestFault, match="FILE_CHANGED"):
        m.hash_file(str(path))


def test_file_and_total_limits(draft, monkeypatch):
    path = artifact(draft)
    with pytest.raises(m.ManifestFault, match="FILE_TOO_LARGE"):
        m.hash_file(str(path), limit=2)
    monkeypatch.setattr(m, "MAX_TOTAL", 2)
    with pytest.raises(m.ManifestFault, match="TOTAL_TOO_LARGE"):
        m.collect(*save(draft))


def test_duplicate_schedule_path_refused(draft):
    artifact(draft, "schedules")
    draft[1]["artifacts"]["schedules"]["files"] *= 2
    with pytest.raises(m.ManifestFault, match="DUPLICATE_FILE"):
        m.collect(*save(draft))


def test_output_exclusive_and_summary_does_not_disclose_private_fields(draft):
    draft[1]["fields"]["allocation_seed"] = m.recorded("PRIVATE-SEED-SENTINEL", "private allocation")
    draft[1]["station_id"] = "PRIVATE-STATION-SENTINEL"
    artifact(draft)
    result = m.collect(*save(draft))
    output = draft[0] / "out.json"
    m.write_new(str(output), result)
    original = output.read_bytes()
    with pytest.raises(m.ManifestFault, match="OUTPUT_EXISTS"):
        m.write_new(str(output), {"replacement": True})
    assert output.read_bytes() == original
    summary = m.canonical(m.public_summary(result))
    for private in (b"PRIVATE", str(draft[0]).encode(), b"app_build.bin", m.digest(b"synthetic binary bytes").encode()):
        assert private not in summary


def test_quaternion_not_merely_four_finite_numbers(draft):
    draft[1]["fields"]["observer_reference"] = m.recorded(
        {"pose":{"position_m":[0,0,0],"rotation_xyzw":[0,0,0,0]},"reference_frame":"synthetic"}, "test")
    with pytest.raises(m.ManifestFault, match="INVALID_POSE_ROTATION"):
        m.collect(*save(draft))


def test_local_release_tag_matches_exact_commit_and_detects_retarget(draft):
    root, config = draft
    repo = root / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL).decode().strip()
    git("init", "-q")
    git("-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid", "commit", "--allow-empty", "-qm", "synthetic")
    head = git("rev-parse", "HEAD")
    git("tag", "v0.9-demo")
    config["release_candidate"] = {"status":"recorded","repository":str(repo),"tag":"v0.9-demo","commit":head}
    assert m.collect(*save(draft))["release_candidate"]["commit"] == head
    config["fields"]["app_build_source_commit"] = m.recorded("1" * 40, "synthetic")
    with pytest.raises(m.ManifestFault, match="APP_SOURCE_RELEASE_MISMATCH"):
        m.collect(*save(draft))
    config["fields"]["app_build_source_commit"] = m.pending("Not available")
    git("-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid", "commit", "--allow-empty", "-qm", "next")
    git("tag", "-f", "v0.9-demo")
    with pytest.raises(m.ManifestFault, match="RELEASE_TAG_MISMATCH"):
        m.collect(*save(draft))


def test_cli_roundtrip_and_bounded_failure(draft, capsys):
    artifact(draft)
    config, pin = save(draft)
    output = str(draft[0] / "out.json")
    assert m.main(["collect","--config",config,"--config-sha256",pin,"--output",output]) == 0
    assert m.main(["verify","--manifest",output,"--manifest-sha256",m.digest(Path(output).read_bytes())]) == 0
    assert m.main(["verify","--manifest",output,"--manifest-sha256","0" * 64]) == 2
    assert capsys.readouterr().err == "HASH_MISMATCH\n"


def test_private_output_guard_does_not_create_public_manifest(draft):
    target = draft[0].parent / "accidentally-public.json"
    with pytest.raises(m.ManifestFault, match="PRIVATE_OUTPUT_REQUIRED"):
        m.write_new(str(target), {"allocation_seed":"private"})
    assert not target.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows drive-relative path syntax")
def test_windows_drive_relative_and_alternate_stream_refused(draft):
    for path in ("C:ambiguous", "C:\\data\\file:stream"):
        with pytest.raises(m.ManifestFault, match="UNSAFE_PATH"):
            m.local_path(path, draft[0])
