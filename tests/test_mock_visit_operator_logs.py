"""Synthetic tests: operator-console audit and player operator logs kept with native evidence (#293)."""
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "schedules/src")]

from ops.console.core import Audit
from tools.mock_visit import operator_logs
from tools.mock_visit.records import EvidenceError, sha, strict
from tools.mock_visit.reconcile import KINDS
from tools.write_mock_run_manifest import emit

H = "a" * 64
PROTOCOL = "simulation-test-engineering"
AUDIT = "runtime/evidence/console-audit.jsonl"
FOUNDATION = "foundation-" + "1" * 32 + ".jsonl"
SAMPLE_AGE = "sample-age-" + "2" * 32 + ".csv"
PANEL = "panel-" + "3" * 32 + ".jsonl"


def compact(value):
    return json.dumps(value, separators=(",", ":")).encode()


def windows_lines(*rows):
    # The player's StreamWriter.WriteLine ends each record with CRLF on Windows.
    return b"".join(compact(row) + b"\r\n" for row in rows)


class NativeRun:
    """A closed synthetic run root plus a separate player operator-logs directory."""

    def __init__(self, directory):
        directory = Path(directory).resolve()  # The emitter refuses symlinked parents, e.g. macOS /var.
        self.root = directory / "run"
        self.player_logs = directory / "player" / "operator-logs"
        self.root.mkdir()
        self.player_logs.mkdir(parents=True)
        schedule = self.save("schedule.json", dict(study="A", visit="D0"))
        package = self.save("package/manifest.json", dict(format="test-only"))
        config = self.save("config.json", dict(files=dict(schedule=dict(path="schedule.json", sha256=schedule),
                                                          package_manifest=dict(path="package/manifest.json", sha256=package)),
                                               pins=dict(package_sha256=H)))
        fixture = self.save("fixture-source.json", dict(scope="SIMULATION_TEST", test_only=True))
        capability = self.save("cap.json", dict(scope="SIMULATION_TEST", participant_admission=False, acoustic_qualification=False,
                                                schedule_sha256=schedule, package_sha256=H, fixture_set_sha256=fixture,
                                                protocol_version=PROTOCOL))
        build = self.save("build.json", dict(source_commit="a" * 40))
        process = self.save("process.json", dict(version=1, process_id=123, process_exit=0, source_commit="a" * 40,
                                                 build_manifest_sha256=build, started_utc="2026-10-05T00:00:00Z",
                                                 ended_utc="2026-10-05T00:01:00Z"))
        self.save("export/manifest.json", dict(schema_version="data-export-provisional-1"))
        self.schedule_sha256 = schedule
        self.args = dict(run_id="synthetic-test", role="reference", config="config.json", config_sha256=config,
                         capability="cap.json", capability_sha256=capability, build_manifest="build.json",
                         build_manifest_sha256=build, process_result="process.json", process_result_sha256=process,
                         fixture_provenance=self.root / "fixture-source.json", fixture_provenance_sha256=fixture)
        audit = Audit(self.root / AUDIT, PROTOCOL, clock=lambda: datetime(2026, 10, 5, tzinfo=timezone.utc))
        audit.append("load_requested", "op-1", dict(schedule=schedule))
        audit.append("checks", "op-1", dict(comfort=True, phone=True))
        audit.append("start_requested", "op-1", dict(schedule=schedule))
        audit.append("start_acknowledged", "op-1", dict(request_id="b" * 32, sequence=2, status="accepted", superseded_sequence=None))
        build_identity = dict(build_id="sim", protocol_version=PROTOCOL, target="StandaloneWindows64")
        self.log(FOUNDATION, windows_lines(
            dict(event_schema="foundation-engineering-v1", station_id="SIM", build_identity=build_identity, event="header", monotonic_ticks=1),
            dict(code="STATE_ECHO_CORRELATION", start_mono_s=1.5, observed_mono_s=1.5, duration_s=0.0, event="state_source_event", monotonic_ticks=2)))
        self.log(SAMPLE_AGE, b"host_mono_s,sample_age_s,sim_time,stale,reset_confirmed\r\n1.5,0.01,2.0,0,1\r\n")
        self.log(PANEL, windows_lines(
            dict(event="response", trial_id="t-1", mode="FullMessage", mono_ms=10.0, response_code="COMMIT",
                 response_target="A", response_action="ADD_ONE", selected_target="A", selected_action="ADD_ONE", commit_mono_ms=10.0)))

    def save(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = compact(value)
        path.write_bytes(raw)
        return sha(raw)

    def log(self, name, raw):
        (self.player_logs / name).write_bytes(raw)

    def emit(self, **extra):
        logs = sorted(self.player_logs.iterdir())
        return emit(self.root, **dict(self.args, console_audit=AUDIT, operator_logs=logs, **extra))

    def manifest(self):
        return strict((self.root / "mock-run.manifest.json").read_bytes())

    def artifacts(self):
        grouped = defaultdict(list)
        for a in self.manifest()["artifacts"]:
            grouped[a["kind"]].append((a, (self.root / a["path"]).read_bytes()))
        return grouped


class RetainedOperatorEvidence(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.run = NativeRun(directory.name)

    def test_emitter_hashes_console_audit_and_copied_player_logs(self):
        """REQ-LOG-03: the console audit and player operator logs are hashed in the run manifest, byte for byte."""
        sources = {p.name: p.read_bytes() for p in self.run.player_logs.iterdir()}
        pin = self.run.emit()
        manifest = self.run.manifest()
        self.assertEqual(pin, sha((self.run.root / "mock-run.manifest.json").read_bytes()))
        by_path = {a["path"]: a for a in manifest["artifacts"]}
        self.assertEqual(by_path[AUDIT]["kind"], "console_audit")
        self.assertEqual(by_path[AUDIT + ".tip"]["kind"], "console_audit_tip")
        self.assertEqual(by_path[AUDIT]["sha256"], sha((self.run.root / AUDIT).read_bytes()))
        for name, raw in sources.items():
            copy = by_path["operator-logs/" + name]
            self.assertEqual((copy["kind"], copy["sha256"], copy["bytes"]), ("operator_log", sha(raw), len(raw)))
            self.assertEqual((self.run.root / "operator-logs" / name).read_bytes(), raw)
            self.assertEqual((self.run.player_logs / name).read_bytes(), raw)
        self.assertEqual(operator_logs.verify(self.run.artifacts(), PROTOCOL, self.run.schedule_sha256), set())
        self.assertFalse(manifest["complete"])

    def test_reemitting_accepts_identical_copies_and_refuses_changed_ones(self):
        """REQ-LOG-03: retained copies are never rewritten; a differing copy is refused."""
        self.run.emit()
        (self.run.root / "mock-run.manifest.json").unlink()
        self.run.emit()
        self.assertEqual(sum(a["kind"] == "operator_log" for a in self.run.manifest()["artifacts"]), 3)
        (self.run.root / "mock-run.manifest.json").unlink()
        self.run.log(SAMPLE_AGE, b"host_mono_s,sample_age_s,sim_time,stale,reset_confirmed\r\n9,9,9,1,0\r\n")
        with self.assertRaisesRegex(EvidenceError, "MOCK_OPERATOR_LOG_CONFLICT"):
            self.run.emit()

    def test_answer_field_in_a_player_log_is_refused_before_any_write(self):
        """REQ-LOG-03: no retained operator log may carry an answer field."""
        for name, raw in ((FOUNDATION, windows_lines(dict(event="trial", details=dict(target_action="ADD_ONE")))),
                          (PANEL, windows_lines(dict(event="response", correct=True))),
                          (SAMPLE_AGE, b"host_mono_s,exact_correct\n1,1\n")):
            with self.subTest(name=name):
                original = (self.run.player_logs / name).read_bytes()
                self.run.log(name, raw)
                with self.assertRaisesRegex(EvidenceError, "MOCK_OPERATOR_LOG_ANSWER_FIELD"):
                    self.run.emit()
                self.run.log(name, original)
                for written in ("mock-run.manifest.json", "operator-logs", "fixture-provenance.local.json"):
                    self.assertFalse((self.run.root / written).exists(), written)

    def test_log_already_in_the_root_is_checked_too(self):
        """REQ-LOG-03: a log the harness placed in operator-logs/ itself is classified and checked."""
        (self.run.root / "operator-logs").mkdir()
        (self.run.root / "operator-logs" / FOUNDATION).write_bytes(windows_lines(dict(event="x", intended=dict(family="f"))))
        with self.assertRaisesRegex(EvidenceError, "MOCK_OPERATOR_LOG_ANSWER_FIELD"):
            emit(self.run.root, **dict(self.run.args, console_audit=AUDIT))

    def test_open_edited_or_partial_console_audit_is_refused(self):
        """REQ-LOG-03: the console audit is retained only closed, unedited and with its tip."""
        audit = self.run.root / AUDIT
        lock = audit.with_name(audit.name + ".lock")
        lock.write_text("single-console-writer\n")
        with self.assertRaisesRegex(EvidenceError, "MOCK_CONSOLE_AUDIT_OPEN"):
            self.run.emit()
        lock.unlink()
        original = audit.read_bytes()
        audit.write_bytes(original.replace(b'"comfort":true', b'"comfort":false'))
        with self.assertRaisesRegex(EvidenceError, "MOCK_CONSOLE_AUDIT_CHAIN"):
            self.run.emit()
        audit.write_bytes(b"".join(original.splitlines(keepends=True)[:-1]))
        with self.assertRaisesRegex(EvidenceError, "MOCK_CONSOLE_AUDIT_TIP"):
            self.run.emit()
        audit.write_bytes(original)
        with self.assertRaisesRegex(EvidenceError, "MOCK_CONSOLE_AUDIT_CHAIN"):
            self.run.emit(capability_sha256=self.run.save("cap.json", dict(
                strict((self.run.root / "cap.json").read_bytes()), protocol_version="simulation-test-other")))

    def test_console_audit_without_tip_or_unknown_log_name_is_refused(self):
        """REQ-LOG-03: missing tips and unrecognised log names are not silently inventoried."""
        tip = self.run.root / (AUDIT + ".tip")
        tip.rename(tip.with_name("moved.tip"))
        with self.assertRaisesRegex(EvidenceError, "MOCK_CONSOLE_AUDIT_REQUIRED"):
            self.run.emit()
        tip.with_name("moved.tip").rename(tip)
        self.run.log("notes.txt", b"free text\n")
        with self.assertRaisesRegex(EvidenceError, "MOCK_OPERATOR_LOG_NAME"):
            self.run.emit()


class ReconciledOperatorEvidence(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.run = NativeRun(directory.name)
        self.run.emit()

    def test_reconciler_accepts_the_new_kinds(self):
        """REQ-LOG-03: the reconciler recognises every retained operator evidence kind."""
        self.assertLessEqual(operator_logs.KINDS, KINDS)

    def test_missing_evidence_stays_incomplete(self):
        """REQ-LOG-03: a run without console audit or operator logs is reported incomplete, not refused."""
        artifacts = self.run.artifacts()
        self.assertEqual(operator_logs.verify(defaultdict(list), PROTOCOL, self.run.schedule_sha256),
                         {"OPERATOR_LOGS_MISSING", "CONSOLE_AUDIT_MISSING"})
        self.assertEqual(operator_logs.verify(artifacts, PROTOCOL, "c" * 64), {"CONSOLE_AUDIT_SCHEDULE_UNBOUND"})

    def test_reconciler_refuses_answer_fields_and_moved_tips(self):
        """REQ-LOG-03: integrity faults in retained operator evidence refuse the run."""
        artifacts = self.run.artifacts()
        entry, _ = artifacts["operator_log"][0]
        artifacts["operator_log"][0] = (entry, windows_lines(dict(event="x", answer="A")))
        with self.assertRaisesRegex(EvidenceError, "MOCK_OPERATOR_LOG_ANSWER_FIELD"):
            operator_logs.verify(artifacts, PROTOCOL, self.run.schedule_sha256)
        artifacts = self.run.artifacts()
        tip, raw = artifacts["console_audit_tip"][0]
        artifacts["console_audit_tip"][0] = (dict(tip, path="elsewhere.tip"), raw)
        with self.assertRaisesRegex(EvidenceError, "MOCK_CONSOLE_AUDIT_TIP"):
            operator_logs.verify(artifacts, PROTOCOL, self.run.schedule_sha256)

    def test_answer_field_rule_allows_build_target_and_responses(self):
        """REQ-LOG-03: the build target and recorded responses are not answer fields; the intended tuple is."""
        allowed = ("target", "response_target", "selected_action", "action_pcm_sha256", "reset_confirmed", "event")
        refused = ("answer", "correct", "exact_correct", "intended", "target_action", "target-referent", "semantic_action")
        self.assertEqual([k for k in allowed if operator_logs.answer_field(k)], [])
        self.assertEqual([k for k in refused if not operator_logs.answer_field(k)], [])


if __name__ == "__main__":
    unittest.main()
