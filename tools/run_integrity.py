"""Run the DEMO integrity baseline with real Unity exports and offline scoring.

Requires existing Python dependencies and a licensed, pinned Unity editor. This
does not install software, change XR runtime or qualify physical presentation.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "sound/src"), str(ROOT / "schedules/src")]
from analysis.integrity import verify_mapping
from tools.prepare_integrity_fixtures import prepare
from tools.prepare_joined_engineering import digest, json_bytes, local_path, need, read_file, strict_json, write_new


def run(command, environment, logfile):
    with logfile.open("xb") as log:
        result = subprocess.run(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    need(result.returncode == 0, "INTEGRITY_COMMAND_FAILED_SEE_PRIVATE_LOG")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unity", type=Path, help="Required unless verifying retained native exports")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--fixtures", type=Path)
    parser.add_argument("--fixtures-sha256")
    parser.add_argument("--existing-exports", type=Path)
    parser.add_argument("--allow-dirty", action="store_true", help="Report an explicit local development run")
    args = parser.parse_args()
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT))
    need(not dirty or args.allow_dirty, "INTEGRITY_SOURCE_DIRTY")
    output = local_path(args.out.absolute()); output.mkdir(parents=True, exist_ok=False)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(ROOT / "sound/src"), str(ROOT / "schedules/src"), environment.get("PYTHONPATH", "")])
    if args.existing_exports:
        need(args.fixtures is not None and args.fixtures_sha256 is not None, "INTEGRITY_EXISTING_PINS_REQUIRED")
        fixture = local_path(args.fixtures.absolute()); expected = args.fixtures_sha256
        exports = local_path(args.existing_exports.absolute())
    else:
        need(args.unity is not None and args.unity.is_file(), "INTEGRITY_UNITY_REQUIRED")
        expected = prepare(output / "fixtures")
        fixture = output / "fixtures/fixtures.local.json"
        exports = output / "exports"
        environment.update(AV_INTEGRITY_FIXTURES=str(fixture), AV_INTEGRITY_FIXTURES_SHA256=expected,
                           AV_INTEGRITY_RESULTS=str(exports))
        run([str(args.unity), "-batchmode", "-nographics", "-projectPath", str(ROOT / "unity"),
             "-runTests", "-testPlatform", "EditMode", "-testFilter", "AcousticVocab.DataLogging.Tests.CommandIntegrityTests",
             "-testResults", str(output / "unity.xml"), "-logFile", str(output / "unity.log")], environment, output / "unity-process.log")
        xml = ET.parse(output / "unity.xml").getroot()
        need(int(xml.attrib["passed"]) == 3 and all(xml.attrib.get(key, "0") == "0" for key in ("failed", "skipped", "inconclusive")), "INTEGRITY_UNITY_RESULTS")
    fixtures = strict_json(read_file(fixture, 65536, expected))
    need(fixtures.get("scope") == "DEMO_INTEGRITY" and len(fixtures["packages"]) == 3, "INTEGRITY_FIXTURES")
    reports = []
    for number, package in enumerate(fixtures["packages"]):
        need(package["directory"] == f"package-{number}", "INTEGRITY_PACKAGE_DIRECTORY")
        folder = package["directory"]
        mapping = exports / folder / "mapping.local.json"
        # ExportBundle's companion receipt is retained independently of the CSV files.
        mapping_pin = read_file(mapping.with_suffix(".json.sha256"), 65).decode().strip()
        reports.append(verify_mapping(fixture.parent / folder, package["package_sha256"], mapping, mapping_pin))
    environment.update(AV_INTEGRITY_NATIVE_PACKAGE=str(fixture.parent / "package-0"),
                       AV_INTEGRITY_NATIVE_EXPORT=str(exports / "package-0"))
    run([sys.executable, "-m", "pytest", "tests/test_command_scoring.py", "tests/test_integrity_export.py",
         "tests/test_integrity_producers.py", "-q", "--junitxml=" + str(output / "python.xml")], environment, output / "python.log")
    suites = ET.parse(output / "python.xml").getroot()
    need(all(int(s.attrib.get(k, "0")) == 0 for s in suites.iter("testsuite") for k in ("failures", "errors", "skipped")), "INTEGRITY_PYTHON_RESULTS")
    report = {"scope": "controlled_clock_integrity_baseline", "participant_qualified": False,
              "source_revision": revision, "dirty_source": dirty, "retained_exports": bool(args.existing_exports),
              "fixtures_sha256": expected, "mapping_runs": reports,
              "python_results_sha256": digest((output / "python.xml").read_bytes()),
              "remaining": ["complete joined A and active/yoked B audio histories", "acoustic onset and actual audible delivery", "pilot station qualification"]}
    write_new(output / "report.local.json", json_bytes(report))
    print("PASS: DEMO integrity baseline, 480 mapped cases; participant qualification remains false")


if __name__ == "__main__":
    main()
