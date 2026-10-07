"""Private provisional apparatus manifests and the G4 freeze-manifest handoff converter.
No device discovery or approval inference."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
MAX_JSON = 1024 * 1024
MAX_FILE = 8 * 1024**3
MAX_TOTAL = 32 * 1024**3
GENERATION_FIELDS = ("model_id_provisional", "model_revision", "runtime_precision", "prompt_hash",
                     "fallback_bank_hash", "renderer_recipe_schema_hash")
DERIVED_FIELDS = {
    "asset_sha256": ("g1_dex3_asset", "sha256"),
    "g1_dex3_asset_path": ("g1_dex3_asset", "path"),
    "scene_sha256": ("scene_usd", "sha256"),
    "reset_snapshot_sha256": ("reset_snapshot", "sha256"),
    "app_build_sha256": ("app_build", "sha256"),
    "operator_console_build_sha256": ("operator_console_build", "sha256"),
    "protocol_hash": ("protocol", "sha256"),
    "audio_onset_calibration_record": ("audio_onset_calibration", "path"),
    "rendered_view_recording": ("rendered_view_recording", "path"),
    "timing_validation_record": ("timing_validation", "path"),
    "schedule_sha256": ("schedules", "sha256"),
}
# G4 freeze manifest (#25, av_generation.freeze) -> handoff field. The freeze manifest's
# own `apparatus` block holds five fields (freeze.APPARATUS_FIELDS); model_id_provisional
# is reconciled from the code-pinned `model.id` item, prompt_hash from both prompt items.
FREEZE_FORMAT = "av-generation/freeze-manifest"
FREEZE_SCHEMAS = ("generation/schema/common.schema.json",
                  "generation/schema/freeze-manifest.schema.json")
FREEZE_ITEMS = {
    "model_id_provisional": "model.id",
    "model_revision": "model.revision",
    "runtime_precision": "runtime.precision",
    "fallback_bank_hash": "fallback.bank_hash",
    "renderer_recipe_schema_hash": "renderer.recipe_schema_hash",
}
FREEZE_PROMPT_ITEMS = ("prompts.a3_sha256", "prompts.b_sha256")
FREEZE_HASH_ITEMS = ("fallback.bank_hash", "renderer.recipe_schema_hash", *FREEZE_PROMPT_ITEMS)


class ManifestFault(ValueError):
    """Only bounded codes are printed; private paths and values stay private."""


def fail(code):
    raise ManifestFault(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def strict_json(data, *, allow_null=False):
    # allow_null is only for G4 freeze manifests, whose pending values are null.
    if len(data) > MAX_JSON:
        fail("JSON_TOO_LARGE")
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                fail("DUPLICATE_JSON_KEY")
            obj[key] = value
        return obj
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=lambda _: fail("NONFINITE_JSON"))
        def walk(item, depth=0):
            if depth > 20:
                fail("JSON_TOO_DEEP")
            if item is None and not allow_null:
                fail("NULL_FORBIDDEN")
            if isinstance(item, float) and not math.isfinite(item):
                fail("NONFINITE_JSON")
            if isinstance(item, (dict, list)):
                for v in (item.values() if isinstance(item, dict) else item):
                    walk(v, depth + 1)
        walk(value)
        return value
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        fail("INVALID_JSON")


def validate(value, name):
    # Schema files are tracked code, never selected by the private input document.
    schema = json.loads((ROOT / "apparatus/schemas" / name).read_text(encoding="utf-8"))
    if next(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value), None):
        fail("SCHEMA_INVALID")
    strict_json(canonical(value))


def local_path(raw, base=None):
    if not isinstance(raw, str) or not raw or "\x00" in raw or raw.startswith(("\\\\", "//")):
        fail("LOCAL_PATH_REQUIRED")
    path = Path(raw)
    if ".." in path.parts or (path.drive and not path.is_absolute()) or (":" in raw[2:] if os.name == "nt" else False):
        fail("UNSAFE_PATH")
    if not path.is_absolute():
        if base is None:
            fail("ABSOLUTE_PATH_REQUIRED")
        path = base / path
    path = Path(os.path.abspath(path))
    if os.name == "nt":
        import ctypes
        # Mapped network drives are not acceptable substitutes for UNC paths.
        if ctypes.windll.kernel32.GetDriveTypeW(str(path.anchor)) == 4:
            fail("NETWORK_PATH_FORBIDDEN")
    return path


def inspect_path(path, *, directory=False, missing_leaf=False):
    for item in reversed([path, *path.parents]):
        try:
            info = item.lstat()
        except FileNotFoundError:
            if missing_leaf and item == path:
                return
            fail("FILE_MISSING")
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            fail("LINK_FORBIDDEN")
        if item != path or directory:
            if not stat.S_ISDIR(info.st_mode):
                fail("DIRECTORY_REQUIRED")
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            fail("REGULAR_UNLINKED_FILE_REQUIRED")
    return info


def identity(info):
    # Python 3.12 Windows lstat uses creation time for deprecated st_ctime while
    # fstat can expose change time. Compare the explicitly named birth time there.
    stamp = getattr(info, "st_birthtime_ns", 0) if os.name == "nt" else info.st_ctime_ns
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, stamp


def hash_file(raw, base=None, *, expected=None, limit=MAX_FILE, retain=False):
    path = local_path(raw, base)
    before = inspect_path(path)
    if before.st_size > limit:
        fail("FILE_TOO_LARGE")
    chunks = []
    total = 0
    hasher = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        with os.fdopen(os.open(path, flags), "rb") as stream:
            if identity(os.fstat(stream.fileno())) != identity(before):
                fail("FILE_CHANGED")
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > limit:
                    fail("FILE_TOO_LARGE")
                hasher.update(chunk)
                if retain:
                    chunks.append(chunk)
            if identity(os.fstat(stream.fileno())) != identity(before):
                fail("FILE_CHANGED")
        if identity(inspect_path(path)) != identity(before) or total != before.st_size:
            fail("FILE_CHANGED")
    except OSError:
        fail("FILE_IO_FAILED")
    actual = hasher.hexdigest()
    if expected is not None and (not re.fullmatch(r"[0-9a-f]{64}", expected) or actual != expected):
        fail("HASH_MISMATCH")
    record = {"path": str(path), "sha256": actual, "bytes": total}
    return (record, b"".join(chunks)) if retain else record


def read_pinned(raw, expected, base=None):
    record, data = hash_file(raw, base, expected=expected, limit=MAX_JSON, retain=True)
    return record, strict_json(data)


def pending(reason):
    return {"status": "pending", "reason": reason}


def recorded(value, source):
    return {"status": "recorded", "value": value, "source": source}


def collect_artifact(spec, base):
    if spec["status"] != "recorded":
        return copy.deepcopy(spec)
    files = []
    for source in spec["files"]:
        files.append(hash_file(source["path"], base, expected=source.get("expected_sha256")))
    if len({v["path"] for v in files}) != len(files):
        fail("DUPLICATE_FILE")
    return {"status": "recorded", "files": files}


def derive_fields(artifacts):
    values = {}
    for name, (artifact, attr) in DERIVED_FIELDS.items():
        source = artifacts[artifact]
        values[name] = (recorded([x[attr] for x in source["files"]] if artifact == "schedules"
                                  else source["files"][0][attr], "artifact:" + artifact)
                        if source["status"] == "recorded" else copy.deepcopy(source))
    return values


def validate_freeze_schema(manifest):
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
    schemas = [json.loads((ROOT / name).read_text(encoding="utf-8")) for name in FREEZE_SCHEMAS]
    registry = Registry().with_resources(
        (s["$id"], Resource.from_contents(s, DRAFT202012)) for s in schemas)
    if next(Draft202012Validator(schemas[-1], registry=registry).iter_errors(manifest), None):
        fail("FREEZE_SCHEMA_INVALID")


def freeze_item_sha256_ok(value, sha):
    # av_generation.freeze.item_sha256: hash items carry their own value, objects and
    # arrays their canonical SHA-256, everything else (and pending values) null.
    if isinstance(value, (dict, list)):
        return sha == digest(canonical(value))
    if isinstance(value, str) and sha is not None:
        return sha == value
    return sha is None


def freeze_to_handoff(manifest, freeze_sha256, name):
    """G4 freeze manifest -> g4-manifest-handoff record. Never upgrades a draft."""
    validate_freeze_schema(manifest)
    items = {}
    for item in manifest["items"]:
        if item["key"] in items:
            fail("FREEZE_ITEM_DUPLICATE")
        if not freeze_item_sha256_ok(item["value"], item["sha256"]):
            fail("FREEZE_ITEM_HASH_MISMATCH")
        items[item["key"]] = item
    if any(key not in items for key in (*FREEZE_ITEMS.values(), *FREEZE_PROMPT_ITEMS)):
        fail("FREEZE_ITEM_MISSING")
    values = {key: item["value"] for key, item in items.items()}
    for key in FREEZE_HASH_ITEMS:
        if values[key] is not None and (items[key]["sha256"] != values[key]
                                        or not re.fullmatch(r"[0-9a-f]{64}", str(values[key]))):
            fail("FREEZE_ITEM_HASH_MISMATCH")
    if not isinstance(values["model.id"], str) or not all(
            values[key] is None or isinstance(values[key], str) for key in FREEZE_ITEMS.values()):
        fail("FREEZE_VALUE_INVALID")
    revision = values["model.revision"]
    if revision is not None and not re.fullmatch(r"[0-9a-f]{40}", revision):
        fail("FREEZE_VALUE_INVALID")
    # Reconcile the model pin with the frozen generation config once it is recorded.
    document = values.get("config.document")
    if document is not None:
        model = document.get("model") if isinstance(document, dict) else None
        if not isinstance(model, dict) or (model.get("model_id"), model.get("revision")) != (
                values["model.id"], values["model.revision"]):
            fail("FREEZE_MODEL_CONFLICT")
    # freeze.apparatus_values: one prompt hash over both prompt sets.
    a3, b = (values[key] for key in FREEZE_PROMPT_ITEMS)
    derived = {field: values[key] for field, key in FREEZE_ITEMS.items()}
    derived["prompt_hash"] = (digest(canonical({"a3_sha256": a3, "b_sha256": b}))
                              if None not in (a3, b) else None)
    if manifest["apparatus"] != {k: v for k, v in derived.items() if k != "model_id_provisional"}:
        fail("FREEZE_APPARATUS_MISMATCH")
    status = manifest["status"]
    if status == "frozen" and not {"owner", "advisor"} <= {s["role"] for s in manifest["signoff"]}:
        fail("FREEZE_SIGNOFF_MISSING")
    sources = dict(FREEZE_ITEMS)
    sources["prompt_hash"] = next((k for k in FREEZE_PROMPT_ITEMS if values[k] is None), "")
    fields = {}
    for field in GENERATION_FIELDS:
        if derived[field] is not None:
            fields[field] = derived[field]
            continue
        key = sources[field]
        reason = f"G4 not frozen (#25); freeze item {key} pending: {items[key]['source']}"
        fields[field] = pending(re.sub(r"[\x00-\x1f]", " ", reason)[:512])
    handoff = {"schema_version": 1, "record_type": "g4-generation-freeze-handoff",
               "freeze_reference": f"{name} sha256:{freeze_sha256} status:{status} "
                                   f"freeze_version:{manifest['freeze_version']}",
               "freeze_status": status, "fields": fields}
    validate(handoff, "g4-manifest-handoff.schema.json")
    return handoff


def read_freeze(raw, expected, base=None):
    record, data = hash_file(raw, base, expected=expected, limit=MAX_JSON, retain=True)
    manifest = strict_json(data, allow_null=True)
    if not isinstance(manifest, dict) or manifest.get("format") != FREEZE_FORMAT:
        fail("FREEZE_FORMAT_INVALID")
    return freeze_to_handoff(manifest, record["sha256"], Path(record["path"]).name)


def g4_fields(spec, base):
    if spec["status"] != "recorded":
        return copy.deepcopy(spec), {name: copy.deepcopy(spec) for name in GENERATION_FIELDS}
    record, value = read_pinned(spec["path"], spec["expected_sha256"], base)
    validate(value, "g4-manifest-handoff.schema.json")
    # Draft provenance stays visible on every copied value; pending fields stay pending.
    source = ("g4:" if value["freeze_status"] == "frozen" else "g4-draft:") + record["sha256"]
    fields = {name: value["fields"][name] for name in GENERATION_FIELDS}
    return {"status": "recorded", "file": record}, {
        name: recorded(field, source) if isinstance(field, str) else copy.deepcopy(field)
        for name, field in fields.items()}


def verify_release(spec, base):
    if spec["status"] != "recorded":
        return copy.deepcopy(spec)
    repo = local_path(spec["repository"], base)
    inspect_path(repo, directory=True)
    try:
        actual = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--end-of-options",
                                 "refs/tags/" + spec["tag"] + "^{commit}"],
                                capture_output=True, timeout=10, check=True).stdout.decode().strip()
    except (OSError, subprocess.SubprocessError, UnicodeError):
        fail("RELEASE_TAG_UNAVAILABLE")
    if actual != spec["commit"]:
        fail("RELEASE_TAG_MISMATCH")
    return {"status": "recorded", "repository": str(repo), "tag": spec["tag"], "commit": actual}


def collect(config_path, config_sha256):
    config_ref, config = read_pinned(config_path, config_sha256)
    validate(config, "apparatus-manifest-input.schema.json")
    base = Path(config_ref["path"]).parent
    # Enforce the aggregate read budget before hashing any large artifact.
    sources = [v for a in config["artifacts"].values() if a["status"] == "recorded" for v in a["files"]]
    if sum(inspect_path(local_path(v["path"], base)).st_size for v in sources) > MAX_TOTAL:
        fail("TOTAL_TOO_LARGE")
    for name in ("camera_pose_fov", "observer_reference"):
        entry = config["fields"][name]
        if entry["status"] == "recorded":
            q = entry["value"]["pose"]["rotation_xyzw"]
            if abs(sum(x * x for x in q) - 1) > 0.001:
                fail("INVALID_POSE_ROTATION")
    artifacts = {name: collect_artifact(value, base) for name, value in config["artifacts"].items()}
    if sum(v["bytes"] for a in artifacts.values() if a["status"] == "recorded"
           for v in a["files"]) > MAX_TOTAL:
        fail("TOTAL_TOO_LARGE")
    g4, generation = g4_fields(config["g4_freeze"], base)
    manifest = {
        "schema_version": 1, "manifest_version": "0.9-provisional", "purpose": "engineering-evidence",
        "participant_qualified": False, "station_id": config["station_id"],
        "template_reconciliation": copy.deepcopy(config["template_reconciliation"]),
        "config": config_ref, "release_candidate": verify_release(config["release_candidate"], base),
        "g4_freeze": g4, "artifacts": artifacts,
        "fields": {**copy.deepcopy(config["fields"]), **derive_fields(artifacts), **generation},
    }
    validate(manifest, "apparatus-manifest.schema.json")
    release = manifest["release_candidate"]
    build = manifest["fields"]["app_build_source_commit"]
    if release["status"] == build["status"] == "recorded" and release["commit"] != build["value"]:
        fail("APP_SOURCE_RELEASE_MISMATCH")
    return manifest


def public_summary(manifest):
    counts = {key: 0 for key in ("recorded", "pending", "not_applicable")}
    for value in manifest["fields"].values():
        counts[value["status"]] += 1
    return {"schema_version": 1, "scope": "hash-verification-only", "participant_qualified": False,
            "manifest_sha256": digest(canonical(manifest)), "field_counts": counts,
            "recorded_artifact_files": sum(len(v["files"]) for v in manifest["artifacts"].values()
                                           if v["status"] == "recorded"),
            "template_reconciliation": "pending", "release_accepted": False,
            "g4_approval_verified": False}


def verify(manifest_path, manifest_sha256):
    _, manifest = read_pinned(manifest_path, manifest_sha256)
    validate(manifest, "apparatus-manifest.schema.json")
    # Recollection rehashes every configured input and reconstructs all derived fields.
    # This also detects post-generation config/G4/tag changes and forged derived values.
    actual = collect(manifest["config"]["path"], manifest["config"]["sha256"])
    if canonical(manifest) != canonical(actual):
        fail("MANIFEST_RECOMPUTE_MISMATCH")
    return public_summary(manifest)


def write_new(path, value, *, private=True):
    target = local_path(path)
    # Skip the root and first level: on macOS every temp path resolves under the system /private.
    if private and not any(p.lower() in {".local", "private", "local-data"} for p in target.parent.parts[2:]):
        fail("PRIVATE_OUTPUT_REQUIRED")
    inspect_path(target, missing_leaf=True)
    # Caller must provision a private local parent directory. Never replace evidence.
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    except FileExistsError:
        fail("OUTPUT_EXISTS")
    except OSError:
        fail("OUTPUT_IO_FAILED")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("collect")
    make.add_argument("--config", required=True)
    make.add_argument("--config-sha256", required=True)
    make.add_argument("--output", required=True)
    make.add_argument("--public-summary")
    check = commands.add_parser("verify")
    check.add_argument("--manifest", required=True)
    check.add_argument("--manifest-sha256", required=True)
    handoff = commands.add_parser("g4-handoff", help="convert a pinned G4 freeze manifest")
    handoff.add_argument("--freeze", required=True)
    handoff.add_argument("--freeze-sha256", required=True)
    handoff.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "g4-handoff":
            record = read_freeze(args.freeze, args.freeze_sha256)
            # Public generation values only; the output is still never overwritten.
            write_new(args.output, record, private=False)
            pending_count = sum(not isinstance(v, str) for v in record["fields"].values())
            summary = {"handoff_sha256": digest(canonical(record)),
                       "freeze_status": record["freeze_status"],
                       "pending_fields": pending_count, "g4_approval_verified": False}
        elif args.command == "collect":
            manifest = collect(args.config, args.config_sha256)
            write_new(args.output, manifest)
            summary = public_summary(manifest)
            if args.public_summary:
                write_new(args.public_summary, summary, private=False)
        else:
            summary = verify(args.manifest, args.manifest_sha256)
        print(canonical(summary).decode("ascii"))
        return 0
    except ManifestFault as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, ValueError):
        print("INPUT_IO_OR_VALUE_INVALID", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
