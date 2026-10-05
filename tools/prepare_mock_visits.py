"""Create private, conspicuously synthetic materials for SIMULATION_TEST builds.

Uses a verified DEMO package; never manufactures approval, calibration, participant
admission, study material, or a signed methodology record. Real visit schedules
and their durations are preserved. Generated files must remain in ignored storage.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct
import sys
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "sound/src"), str(ROOT / "schedules/src")]
from tools.prepare_joined_engineering import (
    PreparationError, digest, is_hash, json_bytes, local_path, need, read_file,
    strict_json, write_new,
)

SCOPE = "SIMULATION_TEST"
SCRIPT_KEYS = ("pre_old", "trained", "novel", "atomic", "validity", "break", "forms", "post_w4_optional")


def attestation(role, fixture_hash, bindings):
    need(role in ("teaching", "menu", "assessment", "rating", "grammar", "speech"), "MOCK_ROLE")
    need(is_hash(fixture_hash), "MOCK_FIXTURE_HASH")
    return dict(version=1, scope=SCOPE, role=role,
                fixture_set_sha256=fixture_hash, bindings=bindings)


def rating_items(study, visit):
    def item(identifier, question, low_label, high_label, low=1, high=7):
        return dict(id=identifier, question=question, low_label=low_label,
                    high_label=high_label, minimum=low, maximum=high)
    difficulty = item("difficulty", "How difficult were the sounds to understand?", "Not at all difficult", "Extremely difficult")
    pleasant = item("pleasantness", "How pleasant were the sounds?", "Not at all pleasant", "Extremely pleasant")
    if (study, visit) == ("A", "D0"):
        return [difficulty, pleasant]
    if (study, visit) == ("A", "D7"):
        return [item("usability", "How easy was the system to use?", "Very difficult", "Very easy"), difficulty]
    need(study == "B" and visit in ("V1", "V2", "V3", "W1", "W4"), "MOCK_VISIT")
    return [item("ownership", "How much did the sound vocabulary feel like your own?", "Not at all", "Very much"),
            item("preference_fit", "How well did the sounds fit your preferences?", "Not at all", "Very well"),
            item("influence", "How much influence did you have over the sounds?", "None", "Very much"), pleasant,
            item("mental_demand", "How mentally demanding was the task?", "Not at all demanding", "Extremely demanding", 0, 10)]


def mock_png():
    """Small deterministic checkerboard, not a semantic teaching illustration."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    pixels = b"".join(b"\0" + b"".join(bytes((240, 160, 32, 255) if (x//8+y//8)%2 else (32, 32, 32, 255))
                                      for x in range(64)) for y in range(64))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")


def prepare(package_directory, package_hash, output, *, speech_directory=None, speech_manifest_hash=None):
    from av_sound.package import load_package
    source = local_path(package_directory)
    output = local_path(output)
    need(any(part in (".local", "private", "local-data") for part in output.parts), "MOCK_PRIVATE_OUTPUT")
    need(not output.exists() and output != source and source not in output.parents, "MOCK_OUTPUT_EXISTS_OR_OVERLAPS")
    need(is_hash(package_hash), "MOCK_PACKAGE_HASH")
    package = load_package(source)
    need(package.demo is True and package.package_sha256 == package_hash, "MOCK_DEMO_PACKAGE_REQUIRED")
    manifest_raw = read_file(source / "manifest.json", 2 * 1024**2)
    manifest = strict_json(manifest_raw)
    permutation_raw = read_file(source / "permutation.json", 2 * 1024**2)
    permutation = strict_json(permutation_raw)
    registry_raw = read_file(ROOT / "sound/reserved/registry.json", 2 * 1024**2)
    provenance = dict(version=1, scope=SCOPE, purpose="native mock visit software exercise",
                      participant_admission=False, acoustic_qualification=False, materials_reviewed=False,
                      package_sha256=package_hash, package_manifest_sha256=digest(manifest_raw),
                      permutation_sha256=digest(permutation_raw), registry_sha256=digest(registry_raw),
                      generator_sha256=digest(Path(__file__).read_bytes()), speech_manifest_sha256=None,
                      schedules={name: entry["sha256"] for name, entry in manifest["files"].items()
                                 if name.startswith("schedules/")})
    speech = None
    if speech_directory is not None:
        from tools.validity_speech import validate_manifest
        speech_source = local_path(speech_directory)
        speech_raw = read_file(speech_source / "manifest.local.json", 1024**2, speech_manifest_hash)
        speech = strict_json(speech_raw)
        need(is_hash(speech_manifest_hash) and speech["demo"] is True and speech["status"] == "engineering_unreviewed"
             and speech["study"] == package.study, "MOCK_UNREVIEWED_DEMO_SPEECH_REQUIRED")
        validate_manifest(speech, speech_source)
        provenance["speech_manifest_sha256"] = digest(speech_raw)
    else:
        need(speech_manifest_hash is None, "MOCK_SPEECH_PAIR")
    provenance_raw = json_bytes(provenance)
    fixture_hash = digest(provenance_raw)
    output.mkdir(parents=True, exist_ok=False)
    files = {}

    def put(relative, value):
        raw = value if isinstance(value, bytes) else json_bytes(value)
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        write_new(destination, raw)
        files[relative] = digest(raw)
        return digest(raw)

    put("fixture-provenance.local.json", provenance_raw)
    png_hash = put("teaching/images/mock.png", mock_png())
    rows = permutation["atoms"] + [r for r in permutation["messages"] if r["status"] == "trained"]
    content = []
    for row in rows:
        identifier = row.get("atom_id", row.get("message_id"))
        content.append(dict(content_id=identifier, meaning_display_id="DEMO-display-" + identifier,
                            definition="SIMULATION TEST: synthetic definition",
                            action_words="SIMULATION action", target_words="SIMULATION target", image_id="mock"))
    catalog_hash = put("teaching/catalog.local.json", dict(format="av-teaching/1", package_sha256=package_hash,
                        content=content, images=dict(mock=dict(file="images/mock.png", sha256=png_hash)),
                        feedback={kind: dict(id="DEMO-feedback-" + kind, text="SIMULATION TEST: " + kind)
                                  for kind in ("atomic", "correct", "incorrect", "timeout")}))
    teaching_review = put("teaching/review.local.json", attestation("teaching", fixture_hash, dict(catalog_sha256=catalog_hash)))
    menu_hash = put("menu/menu-script.local.json", dict(format="av-menu-script/1", package_sha256=package_hash,
                    profile_display_id="DEMO-profile-display", profile_names={p: "SIMULATION " + p for p in ("P1", "P2", "P3")},
                    profile_instructions="SIMULATION TEST: compare the three synthetic profiles.",
                    atom_instructions="SIMULATION TEST: compare the three synthetic options.",
                    active_choice_instructions="SIMULATION TEST: choose an option.",
                    yoked_choice_instructions="SIMULATION TEST: an option is assigned.",
                    candidate_labels=["DEMO option 1", "DEMO option 2", "DEMO option 3"]))
    put("menu/review.local.json", attestation("menu", fixture_hash, dict(script_sha256=menu_hash, teaching_review_sha256=teaching_review)))
    script_hash = put("assessment/scripts.local.json", dict(version=1, scripts={key: "SIMULATION TEST: " + key.replace("_", " ") for key in SCRIPT_KEYS}))
    put("assessment/review.local.json", attestation("assessment", fixture_hash, dict(scripts_sha256=script_hash)))
    put("grammar-review.local.json", attestation("grammar", fixture_hash, dict(registry_sha256=digest(registry_raw),
        boundary="before_first_teaching_block", repeat_policy="once_per_visit_no_partial_replay", scheduling_lead_ms=750,
        completion_authority="software_delivery_only", labels=dict(ready="READY", action="Action", target="Target"))))
    visits = ("D0", "D7") if package.study == "A" else ("V1", "V2", "V3", "W1", "W4")
    for visit in visits:
        put("ratings/" + visit + ".local.json", attestation("rating", fixture_hash, dict(items=rating_items(package.study, visit))))
    if speech is not None:
        # Copy only the verified playable bank. The original bank remains unmodified
        # and unreviewed; simulation attestation is a separate authority type.
        for name in ["manifest.local.json", "manifest.sha256", "selection.local.json", "manifest.local.csv", "listening-review.template.local.json"] + [r["speech_id"] + ".wav" for r in speech["items"]]:
            put("speech/" + name, read_file(speech_source / name, 2 * 1024**2))
        put("speech/listening-review.local.json", attestation("speech", fixture_hash, dict(manifest_sha256=digest(speech_raw), speech_list_sha256=speech["source"]["speech_list_sha256"])))
    index = dict(version=1, scope=SCOPE, fixture_set_sha256=fixture_hash, package_sha256=package_hash,
                 study=package.study, visits=list(visits), participant_admission=False, acoustic_qualification=False,
                 materials_reviewed=False, files=files)
    write_new(output / "index.local.json", json_bytes(index))
    return index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--package-sha256", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--speech", type=Path)
    parser.add_argument("--speech-manifest-sha256")
    args = parser.parse_args()
    try:
        result = prepare(args.package, args.package_sha256, args.out,
                         speech_directory=args.speech, speech_manifest_hash=args.speech_manifest_sha256)
    except (PreparationError, ValueError, OSError):
        print(json.dumps(dict(error="MOCK_PREPARATION_FAILED")), file=sys.stderr)
        return 1
    print(json.dumps({key: result[key] for key in ("scope", "fixture_set_sha256", "package_sha256", "study", "visits", "participant_admission", "acoustic_qualification")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
