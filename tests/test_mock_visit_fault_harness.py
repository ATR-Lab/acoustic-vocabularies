"""Cross-language fault-harness contract. Synthetic inputs; not native evidence.

The committed plan and receipt fixtures are shared with the Unity EditMode
suite (SimulationFaultInjectionTests): Unity must accept every plan and emit
exactly these receipts, and faults.py must accept what is composed from them.
"""
import json
from pathlib import Path
import tempfile
import unittest

from tools.mock_visit import fault_harness as harness
from tools.mock_visit.faults import FAULTS, semantics, validate_observation, validate_plan
from tools.mock_visit.records import EvidenceError, sha, strict, verify_chain

ROOT = Path(__file__).resolve().parent / "fixtures" / "simulation_faults"
BUILD, CONFIG, SCHEDULE, CAPABILITY, NONCE = "b" * 64, "c" * 64, "d" * 64, "f" * 64, "e" * 32
G = "a" * 32
CODES = {
    "presentation_stall": ["FRAME_FREEZE", "SESSION_JOIN_DISPOSED"],
    "audio_underrun": ["AUDIO_UNDERRUN"],
    "corrupt_file_hash": ["HASH_MISMATCH"],
    "missing_response_log": ["DATA_APPEND_FAILED", "SESSION_JOURNAL_FAILED"],
    "failed_reset": ["SESSION_RESET_DEADLINE_MISSED"],
    "input_loss": ["FRAME_INTERFACE_UNAVAILABLE", "SESSION_FOCUS_OR_INPUT_LOST"],
    "headset_disconnect": ["JOIN_FOCUS_LOST"],
}


def plan_for(scenario):
    return harness.build_plan(case_id=scenario.replace("_", "-") + "-01", scenario=scenario, run_id="fault-run-01",
                              build_manifest_sha256=BUILD, config_sha256=CONFIG, schedule_sha256=SCHEDULE,
                              expected_native_codes=CODES[scenario],
                              planned_opportunity_id=None if scenario == "headset_disconnect" else "DEMO-novel-01",
                              minimum_observation_ms=5000)


def fixture(kind, scenario):
    raw = (ROOT / kind / (scenario + ".json")).read_bytes()
    return raw, strict(raw)


def compact(v):
    return json.dumps(v, separators=(",", ":"), ensure_ascii=True).encode()


def seal(rows, kind):
    previous = "0" * 64; raw = b""
    for index, row in enumerate(rows):
        row.update(sequence=index, previous_sha256=previous); row.pop("sha256", None)
        row["sha256"] = previous = sha(compact(row) + (b"\n" if kind == "data" else b""))
        raw += compact(row) + b"\n"
    return verify_chain([("test.jsonl", raw)], kind)


def chains(paused=True):
    """A native-shaped stall episode on the planned novel opportunity."""
    identity = dict(session_id=G, coded_id="DEMO", visit_id="D7", station_id="SIM", protocol_version="simulation-test-v1", build_sha256=BUILD)
    session = dict(event="state_before", clock_epoch=G, schedule_sha256=SCHEDULE, trial_id="DEMO-novel-01", retry_of=None,
                   block_index=0, item_index=0, host_mono_ms=50., scheduled_onset_mono_ms=100., state="CueRequested",
                   audible_status="Uncertain", exposure_consumed=True, reset_ok=True, focus_ok=True, technical_fault_code=None,
                   response_code=None, evidence_sha256=None, opportunity_id="DEMO-novel-01", audio_request_ids=[G])
    data = []
    def add(kind, stamp, p, opportunity="DEMO-novel-01"):
        data.append(dict(schema_version="data-events-provisional-1", event_id=f"{len(data):032x}", clock_epoch=G, host_mono_ms=float(stamp),
                         identity=identity, event_type=kind, opportunity_id=opportunity, attempt_id=opportunity,
                         audio_request_id=G if kind.startswith("audio_") else None, payload=p))
    add("session", 50, session)
    add("audio_request", 60, dict(code="AUDIO_REQUESTED"))
    add("device", 110, dict(kind="frame_freeze", observed_mono_ms=110., value=None, duration_ms=401., code="FRAME_FREEZE"))
    add("session", 120, dict(session, event="item_fault", state="ResponseOpen", host_mono_ms=120., technical_fault_code="FRAME_FREEZE"))
    if paused:
        add("session", 200, dict(session, event="session_paused", state=None, trial_id=None, opportunity_id=None, host_mono_ms=200.), None)
        add("session", 320, dict(session, event="operator_resume", state=None, trial_id=None, opportunity_id=None, host_mono_ms=320.), None)
    data = seal(data, "data")
    request = dict(version=1, kind="private_command", control_session_id=G, request_id="b" * 32, command="reset", args={})
    health = dict(control_session_id=G, mode="test", paused=False, stopped=False, fault=None, demo_active=False, publisher_ready=True,
                  neutral_verification_age_ms=1., publisher_age_ms=1., health_sample_host_mono_ms=500., exposure_ready=True,
                  public_stream_recovered=False)
    reply = dict(version=1, kind="private_reply", request_id="b" * 32, accepted=True, reason="RESET_COMPLETE", mode="test",
                 host_mono_ms=500., sim_time=10., reset_ok=True, duplicate=False, health=health)
    joined = seal([dict(version=1, clock_epoch="b" * 32, host_mono_ms=250., kind="control", payload=dict(kind="control_request", local_mono_ms=250., request=request)),
                   dict(version=1, clock_epoch="b" * 32, host_mono_ms=300., kind="control", payload=dict(kind="control_reply", local_mono_ms=299., reply=reply))], "joined")
    command = dict(version=1, session_nonce=G, request_id="c" * 32, sequence=3, command="resume", run_sheet_manifest_sha256=BUILD, schedule_sha256=SCHEDULE)
    base = dict(request=command, previous_consumed_sequence=2, stop_supersedes_through_sequence=None, prior_receipt_acknowledgement=None)
    operator = seal([dict(version=1, session_nonce=G, record=dict(base, kind="request", host_mono_ms=310., receipt=None)),
                     dict(version=1, session_nonce=G, record=dict(base, kind="result", host_mono_ms=330., receipt=dict(request_id="c" * 32, sequence=3, status="accepted", code="COMMAND_ACCEPTED")))], "operator")
    return data, joined, operator


class Plans(unittest.TestCase):
    def test_committed_plan_fixtures_are_exact_faults_plans(self):
        manifest = dict(run_id="fault-run-01", build_manifest=dict(sha256=BUILD), config=dict(sha256=CONFIG), schedule=dict(sha256=SCHEDULE))
        self.assertEqual({p.stem for p in (ROOT / "plans").glob("*.json")}, FAULTS)
        for scenario in sorted(FAULTS):
            raw, plan = fixture("plans", scenario)
            with self.subTest(scenario=scenario):
                self.assertEqual(raw, harness.plan_bytes(plan_for(scenario)))
                validate_plan(plan, manifest)

    def test_planned_opportunity_required_except_boundary_focus_loss(self):
        with self.assertRaisesRegex(EvidenceError, "MOCK_FAULT_OPPORTUNITY"):
            harness.build_plan(case_id="x", scenario="audio_underrun", run_id="r", build_manifest_sha256=BUILD, config_sha256=CONFIG,
                               schedule_sha256=SCHEDULE, expected_native_codes=["AUDIO_UNDERRUN"], planned_opportunity_id=None,
                               minimum_observation_ms=5000)
        with self.assertRaises(EvidenceError):
            harness.build_plan(case_id="x", scenario="unknown", run_id="r", build_manifest_sha256=BUILD, config_sha256=CONFIG,
                               schedule_sha256=SCHEDULE, expected_native_codes=["X"], planned_opportunity_id="o", minimum_observation_ms=5000)

    def test_plan_cli_writes_only_fresh_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); build = root / "build.json"; config = root / "join.local.json"
            build.write_bytes(b"{}"); config.write_bytes(compact(dict(files=dict(schedule=dict(path="s.json", sha256=SCHEDULE)))))
            args = ["plan", "--case-id", "stall-01", "--scenario", "presentation_stall", "--run-id", "run-01", "--build-manifest", str(build),
                    "--build-manifest-sha256", sha(build.read_bytes()), "--config", str(config), "--config-sha256", sha(config.read_bytes()),
                    "--expected-code", "FRAME_FREEZE", "--opportunity", "DEMO-novel-01", "--minimum-observation-ms", "5000", "--out", str(root / "plan.json")]
            self.assertEqual(harness.main(args), 0)
            self.assertEqual(strict((root / "plan.json").read_bytes())["schedule_sha256"], SCHEDULE)
            self.assertEqual(harness.main(args), 2)


class Receipts(unittest.TestCase):
    def test_unity_receipt_fixtures_bind_to_their_plans(self):
        self.assertEqual({p.stem for p in (ROOT / "receipts").glob("*.json")}, FAULTS)
        for scenario in sorted(FAULTS):
            raw, plan = fixture("plans", scenario); _, receipt = fixture("receipts", scenario)
            with self.subTest(scenario=scenario):
                harness.validate_receipt(receipt, plan, sha(raw))
                self.assertEqual(receipt["hook"], harness.HOOKS[scenario])

    def test_receipt_cannot_change_binding_admission_or_claim_refused_success(self):
        raw, plan = fixture("plans", "presentation_stall"); _, base = fixture("receipts", "presentation_stall")
        for key, value in (("plan_sha256", "0" * 64), ("hook", "audio_thread_stall"), ("participant_admission", True),
                           ("refusal_code", "FRAME_INJECTION_REFUSED"), ("applied_opportunity_id", "other"),
                           ("observed_utc", "2000-01-01T00:00:00Z"), ("method", "operator_observation")):
            receipt = dict(base, **{key: value})
            with self.subTest(key=key), self.assertRaises(EvidenceError):
                harness.validate_receipt(receipt, plan, sha(raw))
        with self.assertRaisesRegex(EvidenceError, "MOCK_FAULT_RECEIPT"):
            harness.validate_receipt(dict(base, extra=True), plan, sha(raw))

    def test_refused_hook_is_recorded_as_not_applied(self):
        raw, plan = fixture("plans", "presentation_stall"); _, base = fixture("receipts", "presentation_stall")
        harness.validate_receipt(dict(base, outcome="not_applied", refusal_code="FRAME_INJECTION_REFUSED"), plan, sha(raw))


class Observations(unittest.TestCase):
    def compose(self, data, joined, operator, prefix=1):
        raw, plan = fixture("plans", "presentation_stall"); _, receipt = fixture("receipts", "presentation_stall")
        receipt = dict(receipt, last_committed_data_sha256=data[prefix]["sha256"])
        refs = harness.select_refs(plan, receipt, data, joined, operator)
        value = harness.observation(plan, sha(raw), receipt, dict(path="run/simulation-fault-injection.local.json", sha256="9" * 64), "8" * 64, refs)
        process = dict(started_utc="2026-10-07T00:00:00Z", ended_utc="2026-10-07T01:00:00Z")
        validate_observation(value, plan, sha(raw), "8" * 64, process)
        terminal = dict(cleanup_succeeded=True, export_succeeded=True, host_mono_ms=10000.)
        return value, semantics(plan, value, data, joined, operator, {"DEMO-novel-01": dict(block="novel")}, terminal, G)

    def test_composed_observation_is_exact_and_selects_native_edges(self):
        data, joined, operator = chains()
        value, result = self.compose(data, joined, operator)
        refs = value["refs"]
        self.assertEqual(refs["cause"], dict(journal="data", sha256=data[2]["sha256"]))
        self.assertEqual(refs["detected"], dict(journal="data", sha256=data[3]["sha256"]))
        self.assertEqual((refs["paused_data_sha256"], refs["resumed_data_sha256"]), (data[4]["sha256"], data[5]["sha256"]))
        self.assertEqual(refs["recovery_operator_sha256"], operator[1]["sha256"])
        self.assertEqual(refs["reset_reply_joined_sha256"], joined[1]["sha256"])
        self.assertTrue(result["native_sequence_complete"])
        self.assertFalse(result["physical_injection_verified"]); self.assertFalse(result["issue81_accepted"])

    def test_terminal_fail_closed_run_stays_incomplete_not_faked(self):
        data, joined, operator = chains(paused=False)
        value, result = self.compose(data, joined, [])
        self.assertIsNone(value["refs"]["paused_data_sha256"]); self.assertIsNone(value["refs"]["recovery_operator_sha256"])
        self.assertFalse(result["native_sequence_complete"])
        self.assertIn("DURABLE_PAUSE_MISSING", result["incomplete_reasons"])
        self.assertIn("EXPLICIT_OPERATOR_RECOVERY_MISSING", result["incomplete_reasons"])

    def test_missing_prefix_row_refused(self):
        data, joined, operator = chains()
        raw, plan = fixture("plans", "presentation_stall"); _, receipt = fixture("receipts", "presentation_stall")
        with self.assertRaisesRegex(EvidenceError, "MOCK_FAULT_PREFIX_REF"):
            harness.select_refs(plan, dict(receipt, last_committed_data_sha256="7" * 64), data, joined, operator)

    def test_cause_from_another_opportunity_is_not_selected(self):
        data, joined, operator = chains()
        data[2]["opportunity_id"] = "DEMO-other"
        raw, plan = fixture("plans", "presentation_stall"); _, receipt = fixture("receipts", "presentation_stall")
        refs = harness.select_refs(plan, dict(receipt, last_committed_data_sha256=data[1]["sha256"]), data, joined, operator)
        self.assertIsNone(refs["cause"])


if __name__ == "__main__":
    unittest.main()
