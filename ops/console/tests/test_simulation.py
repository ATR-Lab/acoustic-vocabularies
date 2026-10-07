import copy
import json
from pathlib import Path
import threading
import urllib.request

import pytest

from av_schedules.planning import RUN_SHEET_COLUMNS, TEMPLATE_SHA256, RUN_SHEET_TEMPLATE
from av_schedules.run_sheets import run_sheet_csv
from ops.console.core import Audit, Console, ConsoleFault, digest, encoded
from ops.console.server import demo_catalog, make_server
from ops.console.simulation import load_catalog
from ops.console.transport import DemoEngine
from tools.prepare_joined_engineering import PreparationError


@pytest.fixture
def fixture(tmp_path):
    root = tmp_path / ".local/simulation-test-console"
    root.mkdir(parents=True)
    repo = Path(__file__).resolve().parents[3]
    schedule_raw = (repo / "tests/sound/fixtures/schedules-demo/A-C01/schedules/A-C01-L01/D0.json").read_bytes()
    schedule = json.loads(schedule_raw)
    package_hash = "a" * 64
    sheet = run_sheet_csv(schedule, "sha256:" + package_hash)
    identity = {k: schedule[k] for k in ("study", "set", "demo", "seed_label")}
    schedule_manifest = dict(identity, format="av-schedules/schedules-manifest", files={"A-C01/schedules/A-C01-L01/D0.json": digest(schedule_raw)})
    run_manifest = dict(identity, format="av-schedules/run-sheets-manifest", checks=dict(findings=0),
                        schedules_manifest_sha256=digest(encoded(schedule_manifest)),
                        template=dict(columns=list(RUN_SHEET_COLUMNS), sha256=TEMPLATE_SHA256[RUN_SHEET_TEMPLATE]),
                        files={"A-C01/run-sheets/A-C01-L01/D0.csv": digest(sheet)})
    files = {}
    for role, value in dict(schedule=schedule_raw, run_sheet_csv=sheet, run_sheet_manifest=encoded(run_manifest),
                            schedule_manifest=encoded(schedule_manifest), package_manifest=encoded(dict(demo=True, package_sha256=package_hash))).items():
        path = root / (role + ".json")
        path.write_bytes(value)
        files[role] = dict(path=path.name, sha256=digest(value))
    join = dict(scope="DEMO_ENGINEERING", protocol_version="simulation-test-v1",
                identity=dict(build_id="simulation-build", coded_id="A-C01-L01", unit_id="A-C01", visit_id="D0"),
                pins=dict(package_sha256=package_hash), files=files,
                directories=dict(evidence="evidence", operator_mailbox="mailbox"))
    capability = dict(version=1, scope="SIMULATION_TEST", fixture_set_sha256="b" * 64, package_sha256=package_hash,
                      schedule_sha256=digest(schedule_raw), build_id="simulation-build", protocol_version="simulation-test-v1",
                      output_directory=str(root / "evidence"), audio_gain=0.05, participant_admission=False, acoustic_qualification=False)
    entry = dict(join_config=str(root / "join.json"), join_sha256=None, capability=str(root / "capability.json"),
                 capability_sha256=None, anchors={}, role=None)
    config = dict(version=1, scope="SIMULATION_TEST", visits={"simulation-a-d0": entry})

    def seal():
        for field, value, pin in [("join_config", join, "join_sha256"), ("capability", capability, "capability_sha256")]:
            raw = encoded(value); Path(entry[field]).write_bytes(raw); entry[pin] = digest(raw)
        raw = encoded(config); path = root / "console.json"; path.write_bytes(raw)
        return path, digest(raw), root / "evidence/console/audit.jsonl", "simulation-test-v1"
    return root, join, capability, config, seal


def test_real_run_sheet_oracle_and_reload_recheck_without_allocation(fixture):
    root, join, capability, config, seal = fixture
    args = seal()
    catalog, mailbox = load_catalog(*args)
    visit = catalog["simulation-a-d0"]()
    assert visit.demo and visit.study == "A" and visit.visit == "D0"
    assert len(visit.rows) == 5 and mailbox == root / "mailbox"
    assert not (root / "reveal.jsonl").exists()
    (root / "run_sheet_csv.json").write_bytes(b"changed")
    with pytest.raises(PreparationError):
        catalog["simulation-a-d0"]()


@pytest.mark.parametrize("field,value", [("scope", "PARTICIPANT"), ("participant_admission", True),
    ("acoustic_qualification", True), ("audio_gain", 1.0), ("version", True),
    ("schedule_sha256", "c" * 64), ("build_id", "different")])
def test_resealed_capability_cannot_cross_scope_or_identity(fixture, field, value):
    root, join, cap, config, seal = fixture
    cap[field] = value
    with pytest.raises(ConsoleFault):
        load_catalog(*seal())


def test_public_audit_and_wrong_protocol_refused(fixture):
    root, join, cap, config, seal = fixture
    args = list(seal())
    args[2] = root.parent / "pilot/audit.jsonl"
    with pytest.raises(ConsoleFault):
        load_catalog(*args)
    args = list(seal()); args[3] = "protocol-v1"
    with pytest.raises(ConsoleFault):
        load_catalog(*args)


def test_non_demo_source_refused_even_if_all_pins_are_changed(fixture):
    root, join, cap, config, seal = fixture
    source = json.loads((root / "package_manifest.json").read_bytes())
    source["demo"] = False
    raw = encoded(source); (root / "package_manifest.json").write_bytes(raw)
    join["files"]["package_manifest"]["sha256"] = digest(raw)
    with pytest.raises(ConsoleFault, match="simulation_demo_required"):
        load_catalog(*seal())


def test_native_simulation_banner_is_independent_of_fake_demo_transport(tmp_path):
    console = Console(demo_catalog(), DemoEngine(), Audit(tmp_path / "audit.jsonl", "simulation-test-v1"))
    server = make_server(console, simulation=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/state") as response:
            value = json.load(response)
        assert value["simulation_test"] is True and value["demo_transport"] is False
        assert "package_sha256" not in value and "capability" not in value
    finally:
        server.shutdown(); server.server_close(); worker.join()
