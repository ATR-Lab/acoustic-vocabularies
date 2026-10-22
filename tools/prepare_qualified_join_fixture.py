"""Assemble a private V1 active SIMULATION_TEST join from an existing qualified DEMO bank.

Run in the locked banks environment. This exercises joined domain loaders and the
real store bridge; apparatus files are transport fixtures, never station approval.
Only the selected unit's ten schedules/run sheets are rendered and checked. No
full-set balance, participant admission or acoustic qualification is asserted.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "schedules/src")]
from tools.prepare_joined_engineering import (  # noqa: E402
    digest,
    json_bytes,
    local_path,
    need,
    relative,
    write_new,
)
from tools.prepare_joined_engineering import prepare as stage  # noqa: E402


def prepare(source, output):
    from av_schedules import (
        SetRun,
        build_b_allocation,
        check_visit_schedule,
        demo_seed,
        register_demo_bank,
    )
    from av_schedules.assign_output import b_dyads_document
    from av_schedules.curriculum import dumps_json, permutation_json
    from av_schedules.design import build_units
    from av_schedules.orders import unit_schedules, visit_schedule_json
    from av_schedules.run_sheet_output import render_run_sheets
    from av_schedules.run_sheets import (
        package_hashes_document,
        parse_package_hashes,
        run_sheet_csv,
        run_sheet_findings,
    )
    from av_schedules.schedule_output import render_schedules
    from av_sound.nonlexical import nonlexical_assets
    from av_sound.package import build_dyad_package, load_package, seal
    from av_sound.wav import write_wav

    from av_banks.handoff import qualified_dyad_bank
    from tools.menu_store_fixture import prepare_fixture
    from tools.prepare_mock_visits import prepare as mock

    source, output = local_path(source), local_path(output)
    need(
        any(p in (".local", "private", "local-data") for p in output.parts[2:]),
        "QUALIFIED_JOIN_PRIVATE_OUTPUT",
    )
    need(
        not output.exists() and source != output and source not in output.parents,
        "QUALIFIED_JOIN_OUTPUT_OVERLAP",
    )
    index = json.loads((source / "fixture.json").read_bytes())
    need(index["demo"] is True and index["participant_ready"] is False, "QUALIFIED_JOIN_DEMO")
    bankdir = local_path(source / relative(index["bank_dir"]))
    bank = qualified_dyad_bank(bankdir, expected_bank_sha256=index["bank_sha256"])
    need(bank.demo is True and bank.bank_id == index["bank_id"], "QUALIFIED_JOIN_BANK_ID")
    master = demo_seed("DEMO-o4.4.1-example")
    unit = build_units(master, "B", "confirmatory")[0]
    permutation = permutation_json(unit)
    need((bankdir / "permutation.json").read_bytes() == permutation, "QUALIFIED_JOIN_PERMUTATION")
    allocation = register_demo_bank(
        build_b_allocation(master, "confirmatory"), unit.unit_id, bank.bank_id
    )
    dyad = next(d for d in allocation.dyads if d.unit_id == unit.unit_id)
    person = next(m.slot_id for m in dyad.members if m.role == "active")
    schedules = unit_schedules(master, unit)
    need(
        all(not check_visit_schedule(d) for d in schedules.values()),
        "QUALIFIED_JOIN_SCHEDULE_CHECK",
    )
    output.mkdir(parents=True, exist_ok=False)
    package_dir = output / "package"
    build_dyad_package(bank, package_dir)
    package_hash = seal(
        package_dir,
        permutation=permutation,
        schedules={name: visit_schedule_json(doc) for name, doc in schedules.items()},
        allocation_extras={
            "swap_w1_w4": unit.swap_w1_w4,
            "structured_family": unit.structured_family,
        },
    )
    load_package(package_dir, expected_package_sha256=package_hash)
    docs, sheets = {}, {}
    for name, doc in schedules.items():
        who, visit = name.split("/")
        visit = visit.removesuffix(".json")
        docs.setdefault(who, {})[visit] = doc
        sheet = run_sheet_csv(doc, "sha256:" + package_hash)
        need(
            not run_sheet_findings(sheet, doc, "sha256:" + package_hash),
            "QUALIFIED_JOIN_RUN_SHEET_CHECK",
        )
        sheets.setdefault(who, {})[visit] = sheet
    hashes = parse_package_hashes(
        dumps_json(
            package_hashes_document("B", "confirmatory", {unit.unit_id: package_hash}, demo=True)
        )
    )
    run = SetRun(
        master,
        "B",
        "confirmatory",
        (unit,),
        docs,
        dict.fromkeys(docs, unit.unit_id),
        allocation,
        dict.fromkeys(docs, "sha256:" + package_hash),
        sheets,
        hashes,
    )
    rendered = render_schedules((unit,), master) | render_run_sheets(run)
    for name, raw in rendered.items():
        write_new(output / "schedules" / name, raw)
    allocation_path = output / "allocation.local.json"
    write_new(allocation_path, dumps_json(b_dyads_document(allocation)))
    materials = output / "mock-materials"
    mock_index = mock(package_dir, package_hash, materials)
    grammar = output / "grammar"
    grammar.mkdir()
    for asset in nonlexical_assets():
        write_wav(asset, grammar / (asset.id + ".wav"))
    config = dict(
        schema_version=1,
        demo_only=True,
        unit_id="DEMO-" + unit.unit_id,
        book_id="DEMO-qualified-join",
        bank_path=str(bankdir / "manifest.json"),
        bank_file_sha256=digest((bankdir / "manifest.json").read_bytes()),
        bank_sha256=index["bank_sha256"],
        package_path=str(package_dir),
        package_sha256=package_hash,
        store_root=str(output / "unused-source-store"),
    )
    config_path = output / "source-config.local.json"
    write_new(config_path, json_bytes(config))
    store = output / "store-service"
    summary = prepare_fixture(config_path, digest(config_path.read_bytes()), store, mode="service")
    snapshot = json.loads((store / "initial-snapshot.json").read_bytes())
    transport = output / "transport"
    transport.mkdir()
    for role, doc in dict(
        station=dict(station_id="DEMO-station", protocol_version="simulation-test-v1"),
        neutral=dict(synthetic_transport_fixture=True),
        frame=dict(synthetic_transport_fixture=True),
        response_panel=dict(synthetic_transport_fixture=True),
    ).items():
        write_new(transport / (role + ".json"), json_bytes(doc))
    write_new(
        transport / "state_source.json",
        json_bytes(
            dict(
                neutral_file="neutral.json",
                neutral_sha256=digest((transport / "neutral.json").read_bytes()),
            )
        ),
    )
    sources = dict(
        schedule=package_dir / "schedules" / person / "V1.json",
        permutation=package_dir / "permutation.json",
        package_manifest=package_dir / "manifest.json",
        bank_manifest=bankdir / "manifest.json",
        run_sheet_manifest=output / "schedules/confirmatory-run-sheets-manifest.json",
        schedule_manifest=output / "schedules/confirmatory-schedules-manifest.json",
        run_sheet_csv=output / "schedules" / unit.unit_id / "run-sheets" / person / "V1.csv",
        schedule_schema=ROOT / "schedules/schema/visit-schedule.schema.json",
        permutation_schema=ROOT / "schedules/schema/permutation.schema.json",
        run_sheet_schema=ROOT / "schedules/schema/run-sheets-manifest.schema.json",
        teaching_manifest=materials / "teaching/catalog.local.json",
        teaching_review=materials / "teaching/review.local.json",
        teaching_allocation=allocation_path,
        menu_allocation=allocation_path,
        menu_script=materials / "menu/menu-script.local.json",
        menu_review=materials / "menu/review.local.json",
        reserved_registry=ROOT / "sound/reserved/registry.json",
        grammar_review=materials / "grammar-review.local.json",
        assessment_script=materials / "assessment/scripts.local.json",
        assessment_review=materials / "assessment/review.local.json",
        rating_review=materials / "ratings/V1.local.json",
        menu_snapshot=store / "initial-snapshot.json",
        menu_bridge_config=store / "config.json",
    )
    sources.update(
        {
            role: transport / (role + ".json")
            for role in ("station", "neutral", "frame", "state_source", "response_panel")
        }
    )
    mapping = dict(
        version=4,
        scope="DEMO_ENGINEERING",
        protocol_version="simulation-test-v1",
        yoked_start=None,
        identity=dict(
            station_id="DEMO-station",
            unit_id=unit.unit_id,
            coded_id=person,
            session_id="a" * 32,
            visit_id="V1",
            build_id="DEMO-qualified-join",
        ),
        files={
            name: dict(path=str(path), sha256=digest(path.read_bytes()))
            for name, path in sources.items()
        },
        directories=dict(
            package=str(package_dir),
            teaching=str(materials / "teaching"),
            grammar=str(grammar),
            menu_examples=str(grammar),
            menu_scripts=str(materials / "menu"),
        ),
        pins=dict(
            package_sha256=package_hash,
            bank_sha256=index["bank_sha256"],
            menu_manifest_sha256=summary["initial_manifest_sha256"],
            menu_head_sha256=None,
            menu_snapshot_sha256=snapshot["snapshot_sha256"],
        ),
        control=dict(endpoint="ws://127.0.0.1:18767/commands", session_id="b" * 32),
    )
    map_path = output / "inputs.local.json"
    write_new(map_path, json_bytes(mapping))
    staged = output / "simulation-test-joined"
    stage(map_path, digest(map_path.read_bytes()), staged)
    capability = dict(
        version=1,
        scope="SIMULATION_TEST",
        fixture_set_sha256=mock_index["fixture_set_sha256"],
        package_sha256=package_hash,
        schedule_sha256=digest(sources["schedule"].read_bytes()),
        build_id=mapping["identity"]["build_id"],
        protocol_version=mapping["protocol_version"],
        output_directory=str(staged / "runtime/evidence"),
        audio_gain=0.05,
        participant_admission=False,
        acoustic_qualification=False,
    )
    write_new(output / "capability.local.json", json_bytes(capability))
    result = dict(
        scope="SIMULATION_TEST",
        participant_admission=False,
        acoustic_qualification=False,
        apparatus_transport_fixture=True,
        full_set_balance_checked=False,
        checked_unit=unit.unit_id,
        checked_schedules=len(schedules),
        menu_role="active",
        joined_config=str(staged / "join.local.json"),
        capability=str(output / "capability.local.json"),
        source_bank_sha256=index["bank_sha256"],
        bank_manifest_file_sha256=config["bank_file_sha256"],
        package_sha256=package_hash,
        store_service=str(store),
        qualified_bank=True,
    )
    write_new(output / "qualified-join.local.json", json_bytes(result))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qualified-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.qualified_root, args.out), sort_keys=True))
