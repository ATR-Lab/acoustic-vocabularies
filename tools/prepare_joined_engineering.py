"""Assemble existing, independently pinned DEMO inputs into a fresh private root.

This is a staging utility, not a producer, review authority, admission decision,
calibrator, runtime installer or service launcher. No source is modified.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import sys
from urllib.parse import urlsplit

REQUIRED_FILES = (
    "schedule", "permutation", "package_manifest", "run_sheet_manifest",
    "schedule_manifest", "run_sheet_csv", "schedule_schema", "permutation_schema",
    "run_sheet_schema", "station", "frame", "state_source", "neutral", "response_panel",
)
OPTIONAL_FILES = (
    "teaching_manifest", "teaching_review", "teaching_allocation", "menu_script",
    "menu_review", "menu_allocation", "reserved_registry", "assessment_script",
    "assessment_review", "rating_review", "speech_manifest", "speech_review",
    "audio_calibration", "menu_snapshot", "menu_bridge_config", "comfort_gain",
    "menu_replay_ledger",
)
YOKED_FILES = (
    "yoked_active_schedule", "yoked_active_run_sheet_manifest",
    "yoked_active_schedule_manifest", "yoked_active_run_sheet_csv",
)
CONTENT_DIRS = ("package", "teaching", "grammar", "speech", "menu_examples", "menu_scripts")
OUTPUT_DIRS = ("menu_mailbox", "operator_mailbox", "evidence")
PIN_NAMES = ("package_sha256", "bank_sha256", "menu_manifest_sha256", "menu_head_sha256", "menu_snapshot_sha256")
IDENTITY_NAMES = ("station_id", "unit_id", "coded_id", "session_id", "visit_id", "build_id")
CONVENTIONS = {
    "package_manifest": ("package", "manifest.json"),
    "permutation": ("package", "permutation.json"),
    "teaching_manifest": ("teaching", "catalog.local.json"),
    "teaching_review": ("teaching", "review.local.json"),
    "menu_script": ("menu_scripts", "menu-script.local.json"),
    "menu_review": ("menu_scripts", "review.local.json"),
    "speech_manifest": ("speech", "manifest.local.json"),
    "speech_review": ("speech", "listening-review.local.json"),
}
MAX_MAP = 65536
MAX_TREE_FILES = 20000
MAX_TREE_BYTES = 2 * 1024**3
MAX_TREE_FILE = 64 * 1024**2


class PreparationError(ValueError):
    """A bounded code; CLI errors never echo private source contents."""


def need(condition, code):
    if not condition:
        raise PreparationError(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def is_hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def opaque_id(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", value) is not None


def exact(value, names, code="MAP_SHAPE"):
    need(isinstance(value, dict) and set(value) == set(names), code)


def strict_json(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            need(key not in result, "JSON_DUPLICATE")
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(PreparationError("JSON_NUMBER")))
        def depth(node, level=0):
            need(level <= 32, "JSON_DEPTH")
            if isinstance(node, dict):
                for item in node.values():
                    depth(item, level + 1)
            elif isinstance(node, list):
                for item in node:
                    depth(item, level + 1)
        depth(value)
        return value
    except PreparationError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise PreparationError("JSON_INVALID") from None


def local_path(value):
    need(isinstance(value, (str, Path)) and str(value), "LOCAL_PATH")
    text = str(value)
    need(not text.startswith(("\\\\", "//")) and not any(ord(c) < 32 for c in text), "LOCAL_PATH")
    path = Path(text)
    need(path.is_absolute(), "ABSOLUTE_PATH_REQUIRED")
    # Never resolve links before checking; resolve() would hide the link itself.
    path = Path(os.path.abspath(path))
    no_links(path)
    return path


def no_links(path):
    for current in (path, *path.parents):
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        need(not stat.S_ISLNK(info.st_mode) and not (getattr(info, "st_file_attributes", 0) & 0x400), "PATH_LINK")


def relative(value):
    need(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._/-]{1,1024}", value), "RELATIVE_PATH")
    for part in value.split("/"):
        need(part not in ("", ".", "..") and len(part) <= 128 and not part.endswith(".")
             and not re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", part, re.I), "RELATIVE_PATH")
    return value


def cap(name):
    return (32 if name == "menu_replay_ledger" else 16 if name in (
        "schedule", "neutral", "run_sheet_csv", "yoked_active_schedule", "yoked_active_run_sheet_csv"
    ) else 1) * 1024**2


def yoked_start_check(value):
    if value is None:
        return
    exact(value, ("policy", "lead_ms"), "YOKED_START_SHAPE")
    need(value["policy"] == "operator_start_plus_lead"
         and type(value["lead_ms"]) is int and 2000 <= value["lead_ms"] <= 60000,
         "YOKED_START_POLICY")


def read_file(path, maximum, expected=None):
    no_links(path)
    with path.open("rb") as stream:
        info = os.fstat(stream.fileno())
        need(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= maximum, "FILE_SIZE_OR_KIND")
        raw = stream.read(maximum + 1)
        need(len(raw) == info.st_size and len(raw) <= maximum, "FILE_CHANGED")
    no_links(path)
    if expected is not None:
        need(is_hash(expected) and digest(raw) == expected, "FILE_PIN")
    return raw


def write_new(path, raw):
    no_links(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    no_links(path)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def inside(path, root):
    return path == root or root in path.parents


def inventory(root):
    need(root.is_dir(), "DIRECTORY_REQUIRED")
    no_links(root)
    files, total, folded = {}, 0, set()
    for parent, dirs, names in os.walk(root, followlinks=False):
        for name in sorted(dirs + names):
            path = Path(parent) / name
            no_links(path)
            rel = relative(path.relative_to(root).as_posix())
            need(rel.casefold() not in folded, "CASE_COLLISION")
            folded.add(rel.casefold())
            if path.is_dir():
                continue
            raw = read_file(path, MAX_TREE_FILE)
            total += len(raw)
            need(len(files) < MAX_TREE_FILES and total <= MAX_TREE_BYTES, "TREE_LIMIT")
            files[rel] = {"sha256": digest(raw), "bytes": len(raw)}
    return dict(sorted(files.items()))


def control_check(value):
    exact(value, ("endpoint", "session_id"), "CONTROL_SHAPE")
    need(isinstance(value["session_id"], str) and re.fullmatch(r"[0-9a-f]{32}", value["session_id"]), "CONTROL_SESSION")
    try:
        text = value["endpoint"]
        need(isinstance(text, str) and not any(ord(c) < 33 for c in text), "CONTROL_ENDPOINT")
        url = urlsplit(text)
        need(url.scheme == "ws" and url.path == "/commands" and not url.username and not url.password
             and not url.query and not url.fragment and ipaddress.ip_address(url.hostname).is_loopback
             and (url.port is None or 0 < url.port <= 65535), "CONTROL_ENDPOINT")
    except (ValueError, TypeError):
        raise PreparationError("CONTROL_ENDPOINT") from None


def verify_bindings(doc, files, package):
    """Bounded cross-file checks; the runtime still performs full domain validation."""
    identity = doc["identity"]
    schedule = strict_json(files["schedule"][1])
    permutation = strict_json(files["permutation"][1])
    station = strict_json(files["station"][1])
    state = strict_json(files["state_source"][1])
    need(isinstance(schedule, dict) and schedule.get("demo") is True and package.demo is True, "DEMO_REQUIRED")
    need(schedule.get("person_id") == identity["coded_id"] and schedule.get("visit") == identity["visit_id"]
         and schedule.get("unit_id") == identity["unit_id"] and permutation.get("unit_id") == identity["unit_id"], "IDENTITY_MISMATCH")
    need(station.get("station_id") == identity["station_id"] and station.get("protocol_version") == doc["protocol_version"], "STATION_BINDING")
    need(schedule.get("permutation_json_sha256") == digest(files["permutation"][1]), "PERMUTATION_BINDING")
    need(files["package_manifest"][1] == (package.path / "manifest.json").read_bytes(), "PACKAGE_MANIFEST_BINDING")
    for role, rel in (("permutation", "permutation.json"), ("schedule", f"schedules/{identity['coded_id']}/{identity['visit_id']}.json")):
        need(rel in package.files and package.read_file(rel) == files[role][1], "PACKAGE_SLOT_BINDING")
    neutral_name = state.get("neutral_file")
    need(isinstance(neutral_name, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\.json", neutral_name)
         and ".." not in neutral_name and "/" not in relative(neutral_name), "NEUTRAL_BASENAME")
    need(state.get("neutral_sha256") == digest(files["neutral"][1]), "NEUTRAL_BINDING")
    if files.get("comfort_gain"):
        need(files["comfort_gain"][0].name == digest(identity["coded_id"].encode("ascii")) + ".local.jsonl", "GAIN_BASENAME")
    run = strict_json(files["run_sheet_manifest"][1])
    schedules = strict_json(files["schedule_manifest"][1])
    prefix = identity["unit_id"]
    need(run.get("schedules_manifest_sha256") == digest(files["schedule_manifest"][1])
         and run.get("checks", {}).get("findings") == 0 and run.get("package_hashes", {}).get("placeholder") is False,
         "RUN_SHEET_CHAIN")
    for key in ("study", "set", "demo", "seed_label"):
        need(schedule.get(key) == run.get(key) == schedules.get(key), "RUN_SHEET_IDENTITY")
    need(schedules.get("files", {}).get(f"{prefix}/schedules/{identity['coded_id']}/{identity['visit_id']}.json") == digest(files["schedule"][1])
         and run.get("files", {}).get(f"{prefix}/run-sheets/{identity['coded_id']}/{identity['visit_id']}.csv") == digest(files["run_sheet_csv"][1]), "RUN_SHEET_FILES")
    rows = list(csv.reader(io.StringIO(files["run_sheet_csv"][1].decode("utf-8"), newline="")))
    header = ["participant_id", "visit", "block", "expected_count", "actual_count", "start_time", "end_time", "comfort_check", "phone_locked", "hash_check", "deviations", "operator_signoff"]
    blocks = schedule.get("blocks", [])
    need(rows and rows[0] == header and len(rows) == len(blocks) + 1, "RUN_SHEET_ROWS")
    for row, block in zip(rows[1:], blocks):
        need(len(row) == len(header) and row[:4] == [identity["coded_id"], identity["visit_id"], block["block"], str(block["expected_count"])]
             and row[9] == "sha256:" + package.package_sha256, "RUN_SHEET_ROWS")
    return neutral_name


def prepare(map_path, map_sha256, output):
    map_path = local_path(map_path)
    need(is_hash(map_sha256), "MAP_PIN")
    doc = strict_json(read_file(map_path, MAX_MAP, map_sha256))
    need(isinstance(doc, dict) and "version" in doc and "scope" in doc, "MAP_SHAPE")
    need(type(doc.get("version")) is int and doc["version"] in (1, 2)
         and doc.get("scope") == "DEMO_ENGINEERING", "MAP_SCOPE")
    version = doc["version"]
    exact(doc, ("version", "scope", "protocol_version", "identity", "files", "directories", "pins", "control")
          + (("yoked_start",) if version == 2 else ()))
    if version == 2:
        yoked_start_check(doc["yoked_start"])
    optional_files = OPTIONAL_FILES + (YOKED_FILES if version == 2 else ())
    need(opaque_id(doc["protocol_version"]), "PROTOCOL_ID")
    exact(doc["identity"], IDENTITY_NAMES, "IDENTITY_SHAPE")
    for key, value in doc["identity"].items():
        need((isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value)) if key == "session_id" else opaque_id(value), "IDENTITY_VALUE")
    control_check(doc["control"])
    exact(doc["pins"], PIN_NAMES, "PIN_SHAPE")
    for key, value in doc["pins"].items():
        need(is_hash(value) or (key != "package_sha256" and value is None), "PIN_VALUE")
    need(isinstance(doc["files"], dict) and set(REQUIRED_FILES) <= set(doc["files"])
         and set(doc["files"]) <= set(REQUIRED_FILES + optional_files), "FILES_SHAPE")
    need(isinstance(doc["directories"], dict) and "package" in doc["directories"]
         and set(doc["directories"]) <= set(CONTENT_DIRS), "DIRECTORIES_SHAPE")
    output = local_path(output)
    need(not output.exists() and not inside(map_path, output), "OUTPUT_EXISTS_OR_OVERLAP")
    files, dirs, inventories = {}, {}, {}
    for name in REQUIRED_FILES + optional_files:
        row = doc["files"].get(name)
        if row is None:
            need(name in optional_files, "REQUIRED_FILE")
            files[name] = None
            continue
        exact(row, ("path", "sha256"), "FILE_SHAPE")
        path = local_path(row["path"])
        need(not inside(path, output), "OUTPUT_SOURCE_OVERLAP")
        files[name] = (path, read_file(path, cap(name), row["sha256"]))
    for name in CONTENT_DIRS:
        source = doc["directories"].get(name)
        if source is None:
            need(name != "package", "PACKAGE_DIRECTORY")
            dirs[name] = None
            continue
        path = local_path(source)
        need(not inside(path, output) and not inside(output, path), "OUTPUT_SOURCE_OVERLAP")
        dirs[name] = path
        inventories[name] = inventory(path)
    # No weakened loader options: this recomputes actual PCM/composite integrity.
    from av_sound.package import load_package
    package = load_package(dirs["package"], expected_package_sha256=doc["pins"]["package_sha256"])
    neutral_name = verify_bindings(doc, files, package)
    # Match conventional filenames consumed by the runtime directory loaders.
    for role, (directory, rel) in CONVENTIONS.items():
        if files[role] is not None:
            need(dirs[directory] is not None, "DOMAIN_DIRECTORY_MISSING")
            expected = dirs[directory] / rel
            need(read_file(expected, cap(role), digest(files[role][1])) == files[role][1], "DOMAIN_FILE_BINDING")
    output.mkdir(parents=True, exist_ok=False)
    config = {key: doc[key] for key in ("version", "scope", "protocol_version", "identity", "pins", "control")}
    if version == 2:
        # Copy explicit future-start policy only. An anchor cannot be authored
        # by staging or copied from another process's monotonic clock.
        config["yoked_start"] = doc["yoked_start"]
    config["files"], config["directories"] = {}, {}
    copied = {}
    for name in CONTENT_DIRS:
        source = dirs[name]
        config["directories"][name] = None if source is None else "content/" + name
        if source is None:
            continue
        destination = output / "content" / name
        destination.mkdir(parents=True)
        for rel, row in inventories[name].items():
            raw = read_file(source / rel, MAX_TREE_FILE, row["sha256"])
            need(len(raw) == row["bytes"], "TREE_CHANGED")
            write_new(destination / rel, raw)
        need(inventory(source) == inventories[name] == inventory(destination), "TREE_CHANGED")
        copied[name] = {"files": len(inventories[name]), "bytes": sum(x["bytes"] for x in inventories[name].values()),
                        "inventory_sha256": digest(json_bytes(inventories[name]))}
    for name in OUTPUT_DIRS:
        config["directories"][name] = "runtime/" + name.replace("_", "-")
    for name, source in files.items():
        if source is None:
            config["files"][name] = None
            continue
        path, raw = source
        need(read_file(path, cap(name), digest(raw)) == raw, "SOURCE_CHANGED")
        if name in CONVENTIONS:
            directory, suffix = CONVENTIONS[name]
            rel = "content/" + directory + "/" + suffix
        else:
            containing = [(len(str(root)), directory, root) for directory, root in dirs.items() if root is not None and inside(path, root)]
            if containing:
                _, directory, root = max(containing)
                rel = "content/" + directory + "/" + path.relative_to(root).as_posix()
            else:
                rel = "files/" + name + "/" + relative(path.name)
        relative(rel)
        target = output / rel
        if target.exists():
            need(read_file(target, cap(name), digest(raw)) == raw, "COPIED_FILE_BINDING")
        else:
            write_new(target, raw)
        config["files"][name] = {"path": rel, "sha256": digest(raw)}
    staged = load_package(output / config["directories"]["package"], expected_package_sha256=package.package_sha256)
    need(staged.demo is True, "DEMO_REQUIRED")
    config_raw = json_bytes(config)
    need(len(config_raw) <= MAX_MAP, "CONFIG_SIZE")
    config_sha = digest(config_raw)
    persistent = [{"role": role, "source": config["files"][role]["path"], "persistent_filename": name,
                   "sha256": config["files"][role]["sha256"]}
                  for role, name in (("station", "station.local.json"), ("state_source", "state-source.local.json"),
                                     ("response_panel", "response-panel.local.json"), ("neutral", neutral_name))]
    report = {"version": 1, "scope": "DEMO_ENGINEERING", "participant_admission": False,
              "source_map_sha256": map_sha256, "config_sha256": config_sha,
              "package_sha256": staged.package_sha256, "package_combinations_checked": staged.combinations_checked,
              "missing_optional_files": [k for k in optional_files if files[k] is None],
              "runtime_authority_decision": "REQUIRED_DOMAIN_VALIDATION_NOT_PERFORMED_BY_STAGER",
              "copied_directory_snapshots": copied, "manual_persistent_provisioning": persistent,
              "runtime_or_appdata_modified": False, "services_launched": False}
    # Config is the completion marker, published last. A failed partial directory
    # remains inspectable and cannot be silently reused.
    write_new(output / "preparation-report.local.json", json_bytes(report))
    write_new(output / "join.local.json.sha256", (config_sha + "\n").encode("ascii"))
    write_new(output / "join.local.json", config_raw)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map", required=True, dest="map_path")
    parser.add_argument("--map-sha256", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        report = prepare(args.map_path, args.map_sha256, args.out)
    except PreparationError as error:
        print("JOIN_PREPARATION_REFUSED " + str(error), file=sys.stderr)
        return 2
    except Exception:
        print("JOIN_PREPARATION_REFUSED INPUT_OR_DOMAIN_VALIDATION", file=sys.stderr)
        return 2
    print("Prepared DEMO engineering staging; participant admission remains false.")
    print("Missing optional authorities: " + ", ".join(report["missing_optional_files"]))
    for row in report["manual_persistent_provisioning"]:
        print(str(Path(args.out) / row["source"]) + " -> persistentDataPath/" + row["persistent_filename"])
    print('-joinedConfig "' + str(Path(args.out) / "join.local.json") + '" -joinedConfigSha256 ' + report["config_sha256"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
