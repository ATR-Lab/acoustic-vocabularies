"""Private stager checks with an actual sealed synthetic producer package.

Minimal transport fixtures below do not assert review/calibration or runtime
schema acceptance. The stager deliberately leaves that decision to domain loaders.
"""
import copy
import csv
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sys

import pytest

pytest.importorskip("numpy", reason="actual producer staging tests run in locked sound CI on all three OS")
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "sound/src")]
from tools import prepare_joined_engineering as stage
from av_sound.package import load_package, seal


@pytest.fixture(scope="module")
def producer(tmp_path_factory):
    root = tmp_path_factory.mktemp("sealed-demo-package")
    spec = importlib.util.spec_from_file_location("joined_package_example", ROOT / "sound/tools/build_example_package.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.build_a_example(root / "package")
    seal(root / "package", schedules=module.A_UNIT / "schedules")
    return root / "package"


@pytest.fixture
def inputs(tmp_path, producer):
    source = tmp_path / "source"
    source.mkdir()
    package = load_package(producer)
    schedule_path = producer / "schedules/A-C01-L01/D0.json"
    schedule = json.loads(schedule_path.read_bytes())
    rows = io.StringIO(newline="")
    writer = csv.writer(rows, lineterminator="\n")
    writer.writerow(["participant_id", "visit", "block", "expected_count", "actual_count", "start_time", "end_time", "comfort_check", "phone_locked", "hash_check", "deviations", "operator_signoff"])
    for block in schedule["blocks"]:
        writer.writerow(["A-C01-L01", "D0", block["block"], block["expected_count"], "", "", "", "", "", "sha256:" + package.package_sha256, "", ""])
    sheet = rows.getvalue().encode()
    identity = {k: schedule[k] for k in ("study", "set", "demo", "seed_label")}
    schedules = dict(identity, files={"A-C01/schedules/A-C01-L01/D0.json": stage.digest(schedule_path.read_bytes())})
    schedules_raw = stage.json_bytes(schedules)
    run = dict(identity, schedules_manifest_sha256=stage.digest(schedules_raw), checks={"findings": 0},
               package_hashes={"placeholder": False}, files={"A-C01/run-sheets/A-C01-L01/D0.csv": stage.digest(sheet)})
    neutral = b'{"synthetic_transport_fixture":true}\n'
    data = {"run_sheet_manifest": stage.json_bytes(run), "schedule_manifest": schedules_raw, "run_sheet_csv": sheet,
            "station": stage.json_bytes({"station_id": "simulator-01", "protocol_version": "DEMO-protocol-1"}),
            "state_source": stage.json_bytes({"neutral_file": "neutral_v1.json", "neutral_sha256": stage.digest(neutral)}), "neutral": neutral}
    files = {}
    for role in stage.REQUIRED_FILES:
        if role == "package_manifest":
            path = producer / "manifest.json"
        elif role == "permutation":
            path = producer / "permutation.json"
        elif role == "schedule":
            path = schedule_path
        else:
            path = source / (role + ".json")
            path.write_bytes(data.get(role, b'{"synthetic_transport_fixture":true}\n'))
        files[role] = {"path": str(path), "sha256": stage.digest(path.read_bytes())}
    doc = {"version": 1, "scope": "DEMO_ENGINEERING", "protocol_version": "DEMO-protocol-1",
           "identity": {"station_id": "simulator-01", "unit_id": "A-C01", "coded_id": "A-C01-L01", "session_id": "a" * 32, "visit_id": "D0", "build_id": "DEMO-build-1"},
           "files": files, "directories": {"package": str(producer)},
           "pins": dict.fromkeys(stage.PIN_NAMES), "control": {"endpoint": "ws://127.0.0.1:18767/commands", "session_id": "b" * 32}}
    doc["pins"]["package_sha256"] = package.package_sha256
    return doc, tmp_path


def invoke(inputs, *, doc=None, output=None):
    base, root = inputs
    path = root / "inputs.local.json"
    raw = stage.json_bytes(doc or base)
    path.write_bytes(raw)
    out = output or root / "prepared"
    return stage.prepare(path, stage.digest(raw), out), out


def change_file(doc, role, value):
    path = Path(doc["files"][role]["path"])
    path.write_bytes(stage.json_bytes(value))
    doc["files"][role]["sha256"] = stage.digest(path.read_bytes())


def test_actual_package_staged_without_source_or_runtime_mutation(inputs):
    doc, root = inputs
    before = stage.inventory(Path(doc["directories"]["package"]))
    report, out = invoke(inputs)
    config = json.loads((out / "join.local.json").read_bytes())
    assert report["participant_admission"] is False
    assert report["runtime_or_appdata_modified"] is False
    assert report["services_launched"] is False
    assert report["package_combinations_checked"] == 32
    assert report["missing_optional_files"] == list(stage.OPTIONAL_FILES)
    assert report["config_sha256"] == stage.digest((out / "join.local.json").read_bytes())
    assert config["identity"]["coded_id"] == "A-C01-L01"
    assert set(config["files"]) == set(stage.REQUIRED_FILES + stage.OPTIONAL_FILES)
    assert set(config["directories"]) == set(stage.CONTENT_DIRS + stage.OUTPUT_DIRS)
    assert not (out / "runtime").exists()
    assert stage.inventory(Path(doc["directories"]["package"])) == before
    for role, entry in config["files"].items():
        if entry:
            assert stage.digest((out / entry["path"]).read_bytes()) == entry["sha256"]
    assert next(x for x in report["manual_persistent_provisioning"] if x["role"] == "neutral")["persistent_filename"] == "neutral_v1.json"


@pytest.mark.parametrize("scope", ["PARTICIPANT", "demo_engineering", True])
def test_scope_cannot_grant_admission(inputs, scope):
    doc = copy.deepcopy(inputs[0]); doc["scope"] = scope
    with pytest.raises(stage.PreparationError, match="MAP_SCOPE"):
        invoke(inputs, doc=doc)


@pytest.mark.parametrize("key", ["approved", "qualified", "gain"])
def test_unknown_authority_fields_refused(inputs, key):
    doc = copy.deepcopy(inputs[0]); doc[key] = True
    with pytest.raises(stage.PreparationError, match="MAP_SHAPE"):
        invoke(inputs, doc=doc)


def test_independent_raw_map_pin_and_duplicate_json(inputs):
    _, root = inputs
    path = root / "inputs.local.json"; raw = stage.json_bytes(inputs[0]); path.write_bytes(raw)
    with pytest.raises(stage.PreparationError, match="FILE_PIN"):
        stage.prepare(path, "0" * 64, root / "out")
    raw = b'{"version":1,"version":1}'
    path.write_bytes(raw)
    with pytest.raises(stage.PreparationError, match="JSON_DUPLICATE"):
        stage.prepare(path, stage.digest(raw), root / "out")


def test_file_pin_required_before_output_created(inputs):
    doc, root = inputs
    Path(doc["files"]["frame"]["path"]).write_bytes(b"changed")
    with pytest.raises(stage.PreparationError, match="FILE_PIN"):
        invoke(inputs)
    assert not (root / "prepared").exists()


def test_canonical_package_pin_checked_by_real_producer(inputs):
    from av_sound.package import PackageIntegrityError
    doc = copy.deepcopy(inputs[0]); doc["pins"]["package_sha256"] = "0" * 64
    with pytest.raises(PackageIntegrityError):
        invoke(inputs, doc=doc)


@pytest.mark.parametrize("key,value", [("unit_id", "OTHER"), ("coded_id", "A-C01-L02"), ("visit_id", "D7")])
def test_exact_visit_identity(inputs, key, value):
    doc = copy.deepcopy(inputs[0]); doc["identity"][key] = value
    with pytest.raises(stage.PreparationError, match="IDENTITY_MISMATCH"):
        invoke(inputs, doc=doc)


def test_non_demo_schedule_refused_not_relabelled(inputs):
    doc = copy.deepcopy(inputs[0]); schedule = json.loads(Path(doc["files"]["schedule"]["path"]).read_bytes())
    path = inputs[1] / "external-schedule.json"; doc["files"]["schedule"]["path"] = str(path)
    schedule["demo"] = False; change_file(doc, "schedule", schedule)
    with pytest.raises(stage.PreparationError, match="DEMO_REQUIRED"):
        invoke(inputs, doc=doc)


def test_external_schedule_cannot_replace_sealed_slot(inputs):
    doc = copy.deepcopy(inputs[0]); raw = Path(doc["files"]["schedule"]["path"]).read_bytes() + b" "
    path = inputs[1] / "external-schedule.json"; path.write_bytes(raw)
    doc["files"]["schedule"] = {"path": str(path), "sha256": stage.digest(raw)}
    with pytest.raises(stage.PreparationError, match="PACKAGE_SLOT_BINDING"):
        invoke(inputs, doc=doc)


@pytest.mark.parametrize("endpoint", ["ws://localhost/commands", "ws://192.168.1.2/commands", "wss://127.0.0.1/commands", "ws://127.0.0.1/state", "ws://a:b@127.0.0.1/commands", "ws://127.0.0.1/commands?secret=x"])
def test_control_remains_literal_loopback(inputs, endpoint):
    doc = copy.deepcopy(inputs[0]); doc["control"]["endpoint"] = endpoint
    with pytest.raises(stage.PreparationError, match="CONTROL_ENDPOINT"):
        invoke(inputs, doc=doc)


def test_output_is_fresh_and_cannot_nest_in_source(inputs):
    root = inputs[1]; existing = root / "existing"; existing.mkdir()
    with pytest.raises(stage.PreparationError, match="OUTPUT_EXISTS"):
        invoke(inputs, output=existing)
    with pytest.raises(stage.PreparationError, match="OUTPUT_SOURCE_OVERLAP"):
        invoke(inputs, output=Path(inputs[0]["directories"]["package"]) / "nested-output")


@pytest.mark.parametrize("path", ["../escape", "absolute/back\\slash", "CON.json", "a//b", "a./b", "/root", "a:b", "name space"])
def test_staged_relative_names_are_portable_and_confined(path):
    with pytest.raises(stage.PreparationError, match="RELATIVE_PATH"):
        stage.relative(path)


def test_actual_symlink_refused(inputs):
    doc = copy.deepcopy(inputs[0]); root = inputs[1]
    target = root / "linked-file.json"
    try:
        target.symlink_to(Path(doc["files"]["station"]["path"]))
    except OSError as error:
        pytest.skip("OS cannot create test symlink: " + str(error.winerror if hasattr(error, "winerror") else error.errno))
    doc["files"]["station"]["path"] = str(target)
    with pytest.raises(stage.PreparationError, match="PATH_LINK"):
        invoke(inputs, doc=doc)


def test_conventional_domain_loader_bytes_bound(inputs):
    doc = copy.deepcopy(inputs[0]); root = inputs[1]
    teaching = root / "teaching"; teaching.mkdir()
    for role, name in (("teaching_manifest", "catalog.local.json"), ("teaching_review", "review.local.json")):
        path = teaching / name; path.write_bytes(b'{"synthetic_transport_fixture":true}\n')
        doc["files"][role] = {"path": str(path), "sha256": stage.digest(path.read_bytes())}
    doc["directories"]["teaching"] = str(teaching)
    report, out = invoke(inputs, doc=doc)
    config = json.loads((out / "join.local.json").read_bytes())
    assert config["files"]["teaching_review"]["path"] == "content/teaching/review.local.json"
    assert "teaching_review" not in report["missing_optional_files"]
    assert report["runtime_authority_decision"] == "REQUIRED_DOMAIN_VALIDATION_NOT_PERFORMED_BY_STAGER"


def test_descriptor_cannot_pin_different_conventional_review(inputs):
    doc = copy.deepcopy(inputs[0]); root = inputs[1]
    teaching = root / "teaching"; teaching.mkdir()
    (teaching / "review.local.json").write_bytes(b'{"actual":false}')
    path = root / "review.json"; path.write_bytes(b'{"actual":true}')
    doc["directories"]["teaching"] = str(teaching)
    doc["files"]["teaching_review"] = {"path": str(path), "sha256": stage.digest(path.read_bytes())}
    with pytest.raises(stage.PreparationError, match="FILE_PIN"):
        invoke(inputs, doc=doc)


def test_source_mutation_during_copy_leaves_no_completion_config(inputs, monkeypatch):
    original = stage.write_new
    def changing(path, raw):
        original(path, raw)
        if path.name == "manifest.json":
            Path(inputs[0]["files"]["frame"]["path"]).write_bytes(b"changed")
    monkeypatch.setattr(stage, "write_new", changing)
    with pytest.raises(stage.PreparationError, match="FILE_PIN"):
        invoke(inputs)
    assert (inputs[1] / "prepared").is_dir()
    assert not (inputs[1] / "prepared/join.local.json").exists()


def test_run_sheet_and_neutral_chains_are_not_invented(inputs):
    doc = copy.deepcopy(inputs[0])
    change_file(doc, "state_source", {"neutral_file": "../outside.json", "neutral_sha256": doc["files"]["neutral"]["sha256"]})
    with pytest.raises(stage.PreparationError, match="NEUTRAL_BASENAME"):
        invoke(inputs, doc=doc)


def test_placeholder_run_sheet_package_hash_refused(inputs):
    doc = copy.deepcopy(inputs[0]); run = json.loads(Path(doc["files"]["run_sheet_manifest"]["path"]).read_bytes())
    run["package_hashes"]["placeholder"] = True; change_file(doc, "run_sheet_manifest", run)
    with pytest.raises(stage.PreparationError, match="RUN_SHEET_CHAIN"):
        invoke(inputs, doc=doc)


def test_active_mailbox_not_copied(inputs):
    doc = copy.deepcopy(inputs[0]); doc["directories"]["menu_mailbox"] = str(inputs[1])
    with pytest.raises(stage.PreparationError, match="DIRECTORIES_SHAPE"):
        invoke(inputs, doc=doc)


def test_size_and_type_caps(inputs):
    doc = copy.deepcopy(inputs[0]); doc["version"] = True
    with pytest.raises(stage.PreparationError, match="MAP_SCOPE"):
        invoke(inputs, doc=doc)
    path = Path(inputs[0]["files"]["frame"]["path"]); path.write_bytes(b"x" * (stage.cap("frame") + 1))
    with pytest.raises(stage.PreparationError, match="FILE_SIZE_OR_KIND"):
        invoke(inputs)


def test_cli_returns_bounded_error_not_private_input(inputs, capsys):
    path = inputs[1] / "bad.json"; raw = b'{"secret":"DO-NOT-ECHO-PRIVATE-CONTENT"}'; path.write_bytes(raw)
    code = stage.main(["--map", str(path), "--map-sha256", stage.digest(raw), "--out", str(inputs[1] / "out")])
    captured = capsys.readouterr()
    assert code == 2 and "JOIN_PREPARATION_REFUSED MAP_SHAPE" in captured.err
    assert "DO-NOT-ECHO" not in captured.err + captured.out
