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
             "freeze_reference": "SYNTHETIC-NO-APPROVAL", "freeze_status": "draft", "fields": values}
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
        assert result["fields"][name]["source"] == "g4-draft:" + draft[1]["g4_freeze"]["expected_sha256"]
    assert not m.public_summary(result)["g4_approval_verified"]


# --- G4 freeze manifest (#25) -> handoff -> collector (#83) -----------------------------
# The committed draft is real `python -m av_generation.freeze` output (its CI guard keeps it
# equal to a regeneration); tests/generation/test_freeze_apparatus_handoff.py repeats the
# chain on a freshly built draft. Nothing here is, or may be presented as, a G4 freeze.

def freeze_source():
    # Same choice as av_generation.freeze.active_manifest_path: frozen file, else the draft.
    generation = ROOT / "generation"
    found = [*sorted(generation.glob("FREEZE-v*.*[0-9].json"), reverse=True), generation / "FREEZE-v1.0.draft.json"]
    found = [p for p in found if p.is_file()]
    if not found:
        pytest.skip("no committed G4 freeze manifest")
    return json.loads(found[0].read_bytes())


def items(freeze):
    return {item["key"]: item for item in freeze["items"]}


def write_freeze(draft, freeze, name="FREEZE-v1.0.draft.json"):
    path = draft[0] / name
    path.write_text(json.dumps(freeze, indent=2) + "\n", encoding="utf-8")
    return str(path), m.digest(path.read_bytes())


def convert(draft, freeze):
    path, pin = write_freeze(draft, freeze)
    output = draft[0] / "g4-handoff.json"
    assert m.main(["g4-handoff", "--freeze", path, "--freeze-sha256", pin, "--output", str(output)]) == 0
    draft[1]["g4_freeze"] = {"status": "recorded", "path": output.name,
                             "expected_sha256": m.digest(output.read_bytes())}
    return json.loads(output.read_bytes()), pin


def test_freeze_manifest_to_handoff_to_collector(draft, capsys):
    freeze = source = freeze_source()
    handoff, pin = convert(draft, freeze)
    summary = json.loads(capsys.readouterr().out)
    assert summary["g4_approval_verified"] is False
    assert handoff["freeze_status"] == summary["freeze_status"] == source["status"]
    assert pin in handoff["freeze_reference"]
    result = m.collect(*save(draft))
    m.validate(result, "apparatus-manifest.schema.json")
    assert b"null" not in m.canonical(result)
    expected = {**source["apparatus"], "model_id_provisional": items(source)["model.id"]["value"]}
    assert set(expected) == set(m.GENERATION_FIELDS)
    prefix = "g4:" if source["status"] == "frozen" else "g4-draft:"
    for name, value in expected.items():
        field = result["fields"][name]
        if value is None:  # pending in #25: stays pending, with the freeze's fill-in source
            assert field["status"] == "pending" and "G4 not frozen (#25)" in field["reason"]
        else:
            assert field == {"status": "recorded", "value": value,
                             "source": prefix + draft[1]["g4_freeze"]["expected_sha256"]}
    output = draft[0] / "manifest.json"
    m.write_new(str(output), result)
    assert m.verify(str(output), m.digest(output.read_bytes()))["g4_approval_verified"] is False


def test_model_id_reconciled_from_frozen_config_when_recorded(draft):
    freeze = freeze_source()
    by_key = items(freeze)
    model = {"model_id": by_key["model.id"]["value"], "revision": by_key["model.revision"]["value"]}
    by_key["config.document"].update(value={"model": model}, sha256=m.digest(m.canonical({"model": model})))
    assert m.freeze_to_handoff(freeze, "0" * 64, "x")["fields"]["model_id_provisional"] == model["model_id"]
    model["model_id"] = "SYNTHETIC/other-model"
    by_key["config.document"].update(value={"model": model}, sha256=m.digest(m.canonical({"model": model})))
    with pytest.raises(m.ManifestFault, match="FREEZE_MODEL_CONFLICT"):
        m.freeze_to_handoff(freeze, "0" * 64, "x")


def synthetic_frozen(freeze):
    """Every pending item filled with labelled synthetic values, signed by both roles."""
    for item in freeze["items"]:
        if item["value"] is None:
            hashed = item["key"].endswith(("sha256", "bank_hash"))
            item["value"] = "5" * 64 if hashed else "SYNTHETIC-NOT-G4"
            item["sha256"] = item["value"] if hashed else None
    by_key = items(freeze)
    model = {"model_id": by_key["model.id"]["value"], "revision": by_key["model.revision"]["value"]}
    by_key["config.document"].update(value={"model": model}, sha256=m.digest(m.canonical({"model": model})))
    freeze["apparatus"].update(runtime_precision="SYNTHETIC-NOT-G4", fallback_bank_hash="5" * 64,
                               prompt_hash=m.digest(m.canonical({"a3_sha256": "5" * 64, "b_sha256": "5" * 64})))
    issue = "https://github.com/ATR-Lab/acoustic-vocabularies/issues/25"
    freeze.update(status="frozen", repo_commit="0" * 40, tag="SYNTHETIC-NOT-G4", signoff=[
        {"role": "owner", "date": "2000-01-01", "reference": issue},
        {"role": "advisor", "date": "2000-01-01", "reference": issue}])
    return freeze


def test_frozen_status_only_mirrors_a_signed_freeze_manifest(draft):
    freeze = synthetic_frozen(freeze_source())
    handoff, _ = convert(draft, freeze)
    assert handoff["freeze_status"] == "frozen"
    assert all(isinstance(v, str) for v in handoff["fields"].values())
    result = m.collect(*save(draft))
    assert result["fields"]["prompt_hash"]["source"].startswith("g4:")
    assert m.public_summary(result)["g4_approval_verified"] is False
    freeze["signoff"][1]["role"] = "owner"
    with pytest.raises(m.ManifestFault, match="FREEZE_SIGNOFF_MISSING"):
        m.freeze_to_handoff(freeze, "0" * 64, "x")


def test_frozen_handoff_with_pending_field_refused(draft):
    _, record = g4(draft)
    record.update(freeze_status="frozen")
    record["fields"]["prompt_hash"] = m.pending("not frozen")
    with pytest.raises(m.ManifestFault, match="SCHEMA_INVALID"):
        m.validate(record, "g4-manifest-handoff.schema.json")
    record.update(freeze_status="draft")
    m.validate(record, "g4-manifest-handoff.schema.json")


def tamper_value(freeze):
    items(freeze)["renderer.recipe_schema_hash"]["value"] = "6" * 64


def tamper_value_and_hash(freeze):
    items(freeze)["renderer.recipe_schema_hash"].update(value="6" * 64, sha256="6" * 64)


def tamper_object(freeze):
    item = next(i for i in freeze["items"] if isinstance(i["value"], dict))
    item["value"] = {**item["value"], "synthetic": 1}


def forge_frozen(freeze):
    freeze["status"] = "frozen"


@pytest.mark.parametrize("tamper, code", [
    (tamper_value, "FREEZE_ITEM_HASH_MISMATCH"),
    (tamper_object, "FREEZE_ITEM_HASH_MISMATCH"),
    (tamper_value_and_hash, "FREEZE_APPARATUS_MISMATCH"),
    (lambda f: f["apparatus"].update(model_revision="7" * 40), "FREEZE_APPARATUS_MISMATCH"),
    (lambda f: f["items"].append(copy.deepcopy(f["items"][0])), "FREEZE_ITEM_DUPLICATE"),
    (lambda f: f.update(items=[i for i in f["items"] if i["key"] != "model.id"]), "FREEZE_ITEM_MISSING"),
    (lambda f: f.update(items=[i for i in f["items"] if i["key"] != "prompts.b_sha256"]), "FREEZE_ITEM_MISSING"),
    (lambda f: f["apparatus"].pop("prompt_hash"), "FREEZE_SCHEMA_INVALID"),
    (lambda f: f["apparatus"].update(model_id_provisional="invented"), "FREEZE_SCHEMA_INVALID"),
    (forge_frozen, "FREEZE_SCHEMA_INVALID"),
    (lambda f: items(f)["model.revision"].update(value="not-a-revision"), "FREEZE_VALUE_INVALID"),
    (lambda f: f.update(format="something-else"), "FREEZE_FORMAT_INVALID"),
])
def test_tampered_or_incomplete_freeze_manifest_refused(draft, tamper, code):
    freeze = freeze_source()
    tamper(freeze)
    path, pin = write_freeze(draft, freeze)
    with pytest.raises(m.ManifestFault, match=code):
        m.read_freeze(path, pin)
    assert m.main(["g4-handoff", "--freeze", path, "--freeze-sha256", pin,
                   "--output", str(draft[0] / "refused.json")]) == 2
    assert not (draft[0] / "refused.json").exists()


def test_freeze_manifest_changed_after_pin_refused(draft):
    path, pin = write_freeze(draft, freeze_source())
    Path(path).write_bytes(Path(path).read_bytes().replace(b"Qwen", b"Qwex"))
    with pytest.raises(m.ManifestFault, match="HASH_MISMATCH"):
        m.read_freeze(path, pin)


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
