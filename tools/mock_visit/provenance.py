"""Bind observer metadata to the native build and retained clock interval.

This checks consistency of pinned evidence, not authenticity of an operator's
receipt. UTC bounds establish a duration bound only; they do not calibrate the
process Stopwatch clock or prove executable custody.
"""
from datetime import datetime, timezone

from .records import exact, hash_value, number, require, sha, strict


def capability(value, manifest):
    exact(value, "version scope build_id protocol_version output_directory schedule_sha256 package_sha256 fixture_set_sha256 audio_gain acoustic_qualification participant_admission")
    require(type(value["version"]) is int and value["version"] == 1 and value["scope"] == "SIMULATION_TEST"
            and value["acoustic_qualification"] is False and value["participant_admission"] is False
            and value["schedule_sha256"] == manifest["schedule"]["sha256"]
            and value["package_sha256"] == manifest["package_sha256"]
            and value["fixture_set_sha256"] == manifest["fixture_set_sha256"]
            and number(value["audio_gain"]) and 0 < value["audio_gain"] <= .1, "MOCK_CAPABILITY_BINDING")


def build(value, manifest, config, cap, relative):
    exact(value, "build_identity result errors duration_seconds total_bytes development_build files")
    identity = value["build_identity"]
    exact(identity, "schema_version build_id commit_sha protocol_version editor_version target dirty_source station_schema_sha256 development_only")
    require(type(identity["schema_version"]) is int and identity["schema_version"] == 1
            and identity["commit_sha"] == manifest["source_commit"]
            and identity["build_id"] == config["identity"]["build_id"] == cap["build_id"]
            and identity["protocol_version"] == config["protocol_version"] == cap["protocol_version"]
            and identity["dirty_source"] is False and identity["development_only"] is True
            and identity["target"] == "StandaloneWindows64" and hash_value(identity["station_schema_sha256"])
            and isinstance(identity["editor_version"], str) and 0 < len(identity["editor_version"]) <= 64,
            "MOCK_BUILD_IDENTITY")
    require(value["result"] == "Succeeded" and type(value["errors"]) is int and value["errors"] == 0
            and number(value["duration_seconds"]) and type(value["total_bytes"]) is int and value["total_bytes"] > 0
            and type(value["development_build"]) is bool and isinstance(value["files"], list)
            and 1 <= len(value["files"]) <= 20000, "MOCK_BUILD_RESULT")
    names = set()
    for item in value["files"]:
        exact(item, "path bytes sha256")
        name = relative(item["path"])
        require(name not in names and type(item["bytes"]) is int and item["bytes"] >= 0
                and hash_value(item["sha256"]), "MOCK_BUILD_FILE")
        names.add(name)
    return {"build_id": identity["build_id"], "source_commit_bound": True,
            "build_inventory_files": len(names), "executable_bytes_reverified": False,
            "embedded_build_identity_bytes_reverified": False}


def process_duration(value):
    try:
        dates = []
        for key in ("started_utc", "ended_utc"):
            text = value[key]
            require(isinstance(text, str) and 20 <= len(text) <= 40, "MOCK_PROCESS_UTC")
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            require(parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed), "MOCK_PROCESS_UTC")
            dates.append(parsed)
        seconds = (dates[1] - dates[0]).total_seconds()
        require(seconds > 0, "MOCK_PROCESS_UTC")
        return seconds * 1000
    except (TypeError, ValueError) as error:
        require(False, "MOCK_PROCESS_UTC")


def interval(process, records, joined):
    elapsed = process_duration(process)
    stamps = [r["host_mono_ms"] for r in records] + [r["host_mono_ms"] for r in joined]
    require(stamps and all(number(t) for t in stamps), "MOCK_PROCESS_NATIVE_INTERVAL")
    span = max(stamps) - min(stamps)
    # Receipts may have millisecond UTC precision. No seconds-long arbitrary
    # slack is allowed to conceal a shortened observer process interval.
    require(span <= elapsed + 2, "MOCK_PROCESS_NATIVE_INTERVAL")
    return {"observer_elapsed_ms": elapsed, "retained_native_span_ms": span,
            "duration_bound_verified": True, "absolute_clock_mapping_qualified": False}


def fixtures(artifacts, config, config_root, manifest, read, relative):
    files={}
    for key,pin in config["files"].items():
        if pin is None:continue
        exact(pin,"path sha256");files[key]=read(config_root/relative(pin["path"]),pin["sha256"])
    require(sha(files["schedule"]) == manifest["schedule"]["sha256"],"MOCK_CONFIG_SCHEDULE_PIN")
    rows=artifacts["fixture_provenance"]
    if not rows:return {"FIXTURE_PROVENANCE_BYTES_MISSING"}
    require(len(rows) == 1,"MOCK_FIXTURE_PROVENANCE_COUNT")
    _,raw=rows[0];p=strict(raw)
    # The first retained fixture predates the additive producer schedule index.
    # Both exact v1 shapes are known; the run's schedule always has its own pin.
    fields="version scope purpose participant_admission acoustic_qualification materials_reviewed package_sha256 package_manifest_sha256 permutation_sha256 registry_sha256 generator_sha256 speech_manifest_sha256"
    exact(p,fields + (" schedules" if "schedules" in p else ""))
    require(sha(raw) == manifest["fixture_set_sha256"] and type(p["version"]) is int and p["version"] == 1
            and p["scope"] == "SIMULATION_TEST" and p["purpose"] == "native mock visit software exercise"
            and p["participant_admission"] is False and p["acoustic_qualification"] is False and p["materials_reviewed"] is False
            and p["package_sha256"] == manifest["package_sha256"]
            and p["package_manifest_sha256"] == sha(files["package_manifest"])
            and p["permutation_sha256"] == sha(files["permutation"])
            and p["registry_sha256"] == sha(files["reserved_registry"])
            and hash_value(p["generator_sha256"]),"MOCK_FIXTURE_PROVENANCE_BINDING")
    if "schedules" in p:
        require(isinstance(p["schedules"],dict) and p["schedules"] and all(hash_value(h) for h in p["schedules"].values()),"MOCK_FIXTURE_SCHEDULE_PROVENANCE")
    if "speech_manifest" in files:
        require(p["speech_manifest_sha256"] == sha(files["speech_manifest"]),"MOCK_FIXTURE_SPEECH_BINDING")
    # These are explicit simulation attestations. A normal approved:true
    # material review cannot be substituted for this separate capability.
    roles={"teaching_review":"teaching","menu_review":"menu","assessment_review":"assessment",
           "rating_review":"rating","grammar_review":"grammar","speech_review":"speech"}
    for key,role in roles.items():
        if key not in files:continue
        value=strict(files[key]);exact(value,"version scope role fixture_set_sha256 bindings")
        require(type(value["version"]) is int and value["version"] == 1 and value["scope"] == "SIMULATION_TEST"
                and value["role"] == role and value["fixture_set_sha256"] == manifest["fixture_set_sha256"]
                and isinstance(value["bindings"],dict),"MOCK_MATERIAL_ATTESTATION_SCOPE")
    return set()


def native_result(value, manifest, process, export_hash, operator_nonces, earliest_result_mono_ms=0):
    """Only this post-cleanup native record may certify software termination."""
    exact(value,"version scope session_nonce process_id source_commit config_sha256 simulation_capability_sha256 status complete cleanup_succeeded export_succeeded export_manifest_sha256 host_mono_ms participant_admission")
    from .records import guid
    require(type(value["version"]) is int and value["version"] == 1 and value["scope"] == "SIMULATION_TEST"
            and value["participant_admission"] is False and guid(value["session_nonce"])
            and type(value["process_id"]) is int and value["process_id"] == process["process_id"]
            and value["source_commit"] == manifest["source_commit"] == process["source_commit"]
            and value["config_sha256"] == manifest["config"]["sha256"]
            and value["simulation_capability_sha256"] == manifest["simulation_capability"]["sha256"]
            and number(value["host_mono_ms"]) and all(type(value[k]) is bool for k in ("complete","cleanup_succeeded","export_succeeded")),
            "MOCK_NATIVE_RESULT_BINDING")
    require(not operator_nonces or set(operator_nonces) == {value["session_nonce"]},"MOCK_NATIVE_RESULT_NONCE")
    require(value["host_mono_ms"] >= earliest_result_mono_ms,"MOCK_NATIVE_RESULT_ORDER")
    require(isinstance(value["status"],str) and 1 <= len(value["status"]) <= 128,"MOCK_NATIVE_RESULT_STATUS")
    if value["export_succeeded"]:
        require(value["export_manifest_sha256"] == export_hash,"MOCK_NATIVE_RESULT_EXPORT")
    else:require(value["export_manifest_sha256"] is None,"MOCK_NATIVE_RESULT_EXPORT")
    complete=value["cleanup_succeeded"] and value["export_succeeded"] and value["status"] == "JOIN_COMPLETE_FORMS_RECORDED"
    require(value["complete"] == complete,"MOCK_NATIVE_RESULT_FALSE_SUCCESS")
    return set() if complete else {"NATIVE_POST_CLEANUP_RESULT_INCOMPLETE"}
