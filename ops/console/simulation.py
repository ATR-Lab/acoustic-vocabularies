"""Explicit native simulation catalog. Never performs participant allocation.

Normal --config admission is unchanged. This catalog only accepts hash-pinned
DEMO join files and the separate native SIMULATION_TEST capability, and uses the
real mailbox. All audit output is confined to that capability's mock directory.
"""
from pathlib import Path

from av_schedules.planning import RUN_SHEET_COLUMNS, TEMPLATE_SHA256, RUN_SHEET_TEMPLATE
from av_schedules.run_sheets import read_run_sheet, run_sheet_findings
from tools.prepare_joined_engineering import local_path, read_file
from .core import Visit, code, digest, hash_value, masked, require, strict_json


def _read(path, pin):
    return read_file(local_path(path), 8_000_000, hash_value(pin))


def _mock_path(path):
    result = local_path(path)
    require(any(p in (".local", "private", "local-data") for p in result.parts)
            and any(p.startswith("simulation-test-") for p in result.parts), "simulation_path_required")
    return result


def load_visit(entry, audit_path, protocol):
    require(set(entry) == {"join_config", "join_sha256", "capability", "capability_sha256", "anchors", "role"}, "simulation_entry_invalid")
    join_path = _mock_path(entry["join_config"])
    join = strict_json(_read(join_path, entry["join_sha256"]))
    capability = strict_json(_read(_mock_path(entry["capability"]), entry["capability_sha256"]))
    require(set(capability) == {"version", "scope", "fixture_set_sha256", "package_sha256", "schedule_sha256", "build_id",
                               "protocol_version", "output_directory", "audio_gain", "participant_admission", "acoustic_qualification"}, "simulation_capability_shape")
    require(type(capability["version"]) is int and capability["version"] == 1 and capability["scope"] == "SIMULATION_TEST"
            and capability["participant_admission"] is False and capability["acoustic_qualification"] is False
            and type(capability["audio_gain"]) is float and capability["audio_gain"] == 0.05, "simulation_capability_invalid")
    hash_value(capability["fixture_set_sha256"])
    evidence = _mock_path(capability["output_directory"])
    audit = _mock_path(audit_path)
    require(evidence in audit.parents, "simulation_audit_outside_mock")
    require(protocol.startswith("simulation-test-") and protocol == capability["protocol_version"] == join["protocol_version"], "simulation_protocol_invalid")
    require(join["scope"] == "DEMO_ENGINEERING" and join["identity"]["build_id"] == capability["build_id"]
            and join["pins"]["package_sha256"] == capability["package_sha256"]
            and join["files"]["schedule"]["sha256"] == capability["schedule_sha256"], "simulation_join_binding")
    require((join_path.parent / join["directories"]["evidence"]).absolute() == evidence, "simulation_evidence_binding")

    def read_role(role):
        row = join["files"][role]
        require(set(row) == {"path", "sha256"}, "simulation_pin_shape")
        path = local_path(join_path.parent / row["path"])
        require(join_path.parent in path.parents, "simulation_input_escape")
        return _read(path, row["sha256"])

    manifest_raw, schedules_raw, schedule_raw, sheet_raw, package_raw = (
        read_role(k) for k in ("run_sheet_manifest", "schedule_manifest", "schedule", "run_sheet_csv", "package_manifest"))
    manifest, schedules, schedule, package = (strict_json(r) for r in (manifest_raw, schedules_raw, schedule_raw, package_raw))
    require(all(row.get("demo") is True for row in (manifest, schedules, schedule, package)), "simulation_demo_required")
    require(package["package_sha256"] == capability["package_sha256"], "simulation_package_binding")
    require(manifest["format"] == "av-schedules/run-sheets-manifest" and schedules["format"] == "av-schedules/schedules-manifest"
            and manifest["template"]["columns"] == list(RUN_SHEET_COLUMNS)
            and manifest["template"]["sha256"] == TEMPLATE_SHA256[RUN_SHEET_TEMPLATE]
            and manifest["schedules_manifest_sha256"] == digest(schedules_raw)
            and manifest["checks"]["findings"] == 0, "simulation_producer_chain")
    for key in ("study", "set", "demo", "seed_label"):
        require(schedule[key] == manifest[key] == schedules[key], "simulation_identity")
    person, unit, visit = (code(schedule[k]) for k in ("person_id", "unit_id", "visit"))
    require(person == join["identity"]["coded_id"] and visit == join["identity"]["visit_id"] and unit == join["identity"]["unit_id"], "simulation_identity")
    require(schedules["files"].get(f"{unit}/schedules/{person}/{visit}.json") == digest(schedule_raw)
            and manifest["files"].get(f"{unit}/run-sheets/{person}/{visit}.csv") == digest(sheet_raw), "simulation_file_chain")
    require(not run_sheet_findings(sheet_raw, schedule, "sha256:" + capability["package_sha256"]), "simulation_sheet_invalid")
    role = entry["role"]
    if schedule["study"] == "A":
        require(role is None, "simulation_role")
    else:
        require(schedule["study"] == "B" and role in ("active", "yoked"), "simulation_role")
        allocation = strict_json(read_role("menu_allocation" if join["files"].get("menu_allocation") else "teaching_allocation"))
        require(allocation.get("demo") is True, "simulation_demo_required")
        members = [m for d in allocation["dyads"] if d["unit_id"] == unit for m in d["members"] if m["slot_id"] == person]
        require(len(members) == 1 and members[0]["role"] == role, "simulation_role")
    _, rows = read_run_sheet(sheet_raw)
    masked(rows)
    mailbox = _mock_path(join_path.parent / join["directories"]["operator_mailbox"])
    return Visit(person, schedule["study"], visit, unit, role, True, digest(manifest_raw), digest(schedule_raw),
                 capability["package_sha256"], tuple(rows), dict(entry["anchors"])), mailbox


def load_catalog(path, pin, audit_path, protocol):
    value = strict_json(_read(_mock_path(path), pin))
    require(set(value) == {"version", "scope", "visits"} and type(value["version"]) is int and value["version"] == 1
            and value["scope"] == "SIMULATION_TEST" and isinstance(value["visits"], dict) and 1 <= len(value["visits"]) <= 12, "simulation_config_invalid")
    catalog, mailbox = {}, None
    for alias, entry in value["visits"].items():
        code(alias)
        _, current = load_visit(entry, audit_path, protocol)
        require(mailbox is None or current == mailbox, "simulation_mailbox_mismatch")
        mailbox = current
        catalog[alias] = lambda entry=entry: load_visit(entry, audit_path, protocol)[0]
    return catalog, mailbox
