"""Synthetic fault contracts; these cases are not native injection evidence."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.mock_visit.faults import FAULTS, semantics, validate_plan, validate_observation, verify_startup, main
from tools.mock_visit.records import EvidenceError, sha, strict, verify_chain
from tools.mock_visit.suite import coverage

H = "a" * 64
G = "a" * 32


def compact(v):
    return json.dumps(v, separators=(",", ":"), ensure_ascii=True).encode()


def seal(rows, kind):
    previous = "0" * 64; raw = b""
    for index, row in enumerate(rows):
        row.update(sequence=index, previous_sha256=previous)
        row.pop("sha256", None)
        row["sha256"] = previous = sha(compact(row) + (b"\n" if kind == "data" else b""))
        raw += compact(row) + b"\n"
    return verify_chain([("test.jsonl", raw)], kind)


def fixture():
    identity = dict(session_id=G, coded_id="DEMO", visit_id="D7", station_id="SIM", protocol_version="simulation-test-v1", build_sha256=H)
    session = dict(event="state_before", clock_epoch=G, schedule_sha256=H, trial_id="novel-1", retry_of=None,
                   block_index=0, item_index=0, host_mono_ms=50., scheduled_onset_mono_ms=100., state="CueRequested",
                   audible_status="Uncertain", exposure_consumed=True, reset_ok=True, focus_ok=True,
                   technical_fault_code=None, response_code=None, evidence_sha256=None, opportunity_id="novel-1", audio_request_ids=[G])
    data = []
    def add(kind, stamp, p, opportunity="novel-1"):
        row = dict(schema_version="data-events-provisional-1", event_id=f"{len(data):032x}", clock_epoch=G,
                   host_mono_ms=float(stamp), identity=identity, event_type=kind, opportunity_id=opportunity,
                   attempt_id=opportunity, audio_request_id=G if kind.startswith("audio_") else None, payload=p)
        data.append(row)
    add("session", 50, session)
    add("audio_request", 60, dict(code="AUDIO_REQUESTED"))
    add("device", 110, dict(kind="frame_freeze", observed_mono_ms=110., value=None, duration_ms=300., code="FRAME_FREEZE"))
    add("session", 120, dict(session, event="item_fault", state="ResponseOpen", host_mono_ms=120., technical_fault_code="FRAME_FREEZE"))
    add("session", 200, dict(session, event="session_paused", state=None, trial_id=None, opportunity_id=None, host_mono_ms=200.), None)
    add("session", 320, dict(session, event="operator_resume", state=None, trial_id=None, opportunity_id=None, host_mono_ms=320.), None)
    data = seal(data, "data")
    request = dict(version=1, kind="private_command", control_session_id=G, request_id="b"*32, command="reset", args={})
    health = dict(control_session_id=G, mode="test", paused=False, stopped=False, fault=None, demo_active=False,
                  publisher_ready=True, neutral_verification_age_ms=1., publisher_age_ms=1., health_sample_host_mono_ms=500.,
                  exposure_ready=True, public_stream_recovered=False)
    reply = dict(version=1, kind="private_reply", request_id="b"*32, accepted=True, reason="RESET_COMPLETE", mode="test",
                 host_mono_ms=500., sim_time=10., reset_ok=True, duplicate=False, health=health)
    joined = seal([dict(version=1, clock_epoch="b"*32, host_mono_ms=250., kind="control", payload=dict(kind="control_request", local_mono_ms=250., request=request)),
                   dict(version=1, clock_epoch="b"*32, host_mono_ms=300., kind="control", payload=dict(kind="control_reply", local_mono_ms=299., reply=reply))], "joined")
    command = dict(version=1, session_nonce=G, request_id="c"*32, sequence=3, command="resume", run_sheet_manifest_sha256=H, schedule_sha256=H)
    base = dict(request=command, previous_consumed_sequence=2, stop_supersedes_through_sequence=None, prior_receipt_acknowledgement=None)
    operator = seal([dict(version=1, session_nonce=G, record=dict(base, kind="request", host_mono_ms=310., receipt=None)),
                     dict(version=1, session_nonce=G, record=dict(base, kind="result", host_mono_ms=330., receipt=dict(request_id="c"*32, sequence=3, status="accepted", code="COMMAND_ACCEPTED")))], "operator")
    manifest = dict(run_id="fault-test", build_manifest=dict(sha256=H), config=dict(sha256=H), schedule=dict(sha256=H))
    plan = dict(version=1, scope="SIMULATION_TEST", case_id="stall-01", scenario="presentation_stall", run_id="fault-test",
                build_manifest_sha256=H, config_sha256=H, schedule_sha256=H, expected_native_codes=["FRAME_FREEZE"],
                planned_opportunity_id="novel-1", minimum_observation_ms=1000)
    refs = dict(last_committed_data_sha256=data[1]["sha256"], cause=dict(journal="data", sha256=data[2]["sha256"]),
                detected=dict(journal="data", sha256=data[3]["sha256"]), paused_data_sha256=data[4]["sha256"],
                resumed_data_sha256=data[5]["sha256"], recovery_operator_sha256=operator[1]["sha256"],
                reset_reply_joined_sha256=joined[1]["sha256"], deviation=None)
    process = dict(started_utc="2026-10-05T00:00:00Z", ended_utc="2026-10-05T00:00:10Z")
    observation = dict(version=1, scope="SIMULATION_TEST", case_id="stall-01", plan_sha256=H, run_manifest_sha256=H,
                       injection=dict(method="software_harness", requested_utc="2026-10-05T00:00:01Z", observed_utc="2026-10-05T00:00:02Z",
                                      outcome="applied", supporting_artifacts=[dict(path="injection.txt", sha256=H)]), refs=refs)
    return plan, observation, data, joined, operator, {"novel-1": dict(block="novel")}, dict(cleanup_succeeded=True, export_succeeded=True, host_mono_ms=2000.), manifest, process


class NativeFaults(unittest.TestCase):
    def run_case(self, f):
        return semantics(*f[:7], G)

    def test_bound_native_stall_pause_reset_resume_sequence(self):
        f = fixture(); validate_plan(f[0], f[7]); validate_observation(f[1], f[0], H, H, f[8])
        r = self.run_case(f)
        self.assertTrue(r["native_sequence_complete"])
        self.assertTrue(r["consumed_novel_not_replayed"])
        self.assertFalse(r["physical_injection_verified"])
        self.assertFalse(r["predeclaration_custody_verified"])
        self.assertFalse(r["no_answer_during_pause_verified"])
        self.assertFalse(r["issue81_accepted"])

    def test_no_closed_export_cannot_support_absence(self):
        for terminal in (None, dict(cleanup_succeeded=False, export_succeeded=True, host_mono_ms=2000.)):
            f = list(fixture()); f[6] = terminal; r = self.run_case(f)
            self.assertIsNone(r["no_new_audio_request_while_unresolved"])
            self.assertIsNone(r["consumed_novel_not_replayed"])
            self.assertFalse(r["native_sequence_complete"])

    def test_restart_clock_requires_separate_provenance(self):
        f = fixture(); f[2][-1]["clock_epoch"] = "c"*32
        self.assertIsNone(self.run_case(f)["consumed_novel_not_replayed"])

    def test_short_observation_never_implies_no_replay(self):
        f = fixture(); f[6]["host_mono_ms"] = 1199.
        r = self.run_case(f); self.assertIn("POST_PAUSE_OBSERVATION_TOO_SHORT", r["incomplete_reasons"])
        self.assertIsNone(r["no_new_audio_request_while_unresolved"])

    def test_missing_ref_or_wrong_code_rejected(self):
        f = fixture(); f[1]["refs"]["detected"]["sha256"] = H
        with self.assertRaisesRegex(EvidenceError, "MOCK_FAULT_REF_MISSING"): self.run_case(f)
        f = fixture(); f[0]["expected_native_codes"] = ["OTHER"]
        with self.assertRaisesRegex(EvidenceError, "MOCK_FAULT_UNEXPECTED_CODE"): self.run_case(f)

    def test_pre_fault_reset_cannot_authorize_recovery(self):
        f = fixture(); f[3][0]["host_mono_ms"] = 100.
        f[3][0]["payload"]["local_mono_ms"] = 100.
        with self.assertRaisesRegex(EvidenceError, "MOCK_FAULT_RESET_ORDER"): self.run_case(f)

    def test_failed_or_stale_reset_not_success(self):
        for key, value in (("reset_ok", False), ("accepted", False), ("reason", "REJECTED"), ("duplicate", True)):
            f = fixture(); f[3][1]["payload"]["reply"][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(EvidenceError, "MOCK_FAULT_RESET_REPLY"): self.run_case(f)
        f = fixture(); f[3][1]["payload"]["reply"]["health"]["publisher_age_ms"] = 251.
        with self.assertRaisesRegex(EvidenceError, "MOCK_FAULT_RESET_STALE"): self.run_case(f)

    def test_mutually_matching_wrong_control_session_rejected(self):
        f = fixture(); f[3][0]["payload"]["request"]["control_session_id"] = "d"*32
        f[3][1]["payload"]["reply"]["health"]["control_session_id"] = "d"*32
        with self.assertRaisesRegex(EvidenceError, "MOCK_FAULT_RESET_SESSION"): self.run_case(f)

    def test_each_health_age_nonnegative_before_max(self):
        for key in ("neutral_verification_age_ms", "publisher_age_ms"):
            f = fixture(); f[3][1]["payload"]["reply"]["health"][key] = -1.
            with self.subTest(key=key), self.assertRaisesRegex(EvidenceError, "MOCK_FAULT_RESET_HEALTH"): self.run_case(f)

    def test_teaching_reset_cannot_authorize_protected_opportunity(self):
        f = fixture(); r = f[3][1]["payload"]["reply"]
        r["mode"] = r["health"]["mode"] = "teaching"
        with self.assertRaisesRegex(EvidenceError, "MOCK_FAULT_RESET_MODE"): self.run_case(f)

    def test_rejected_operator_receipt_not_recovery(self):
        f = fixture(); f[4][1]["record"]["receipt"]["status"] = "rejected"
        with self.assertRaisesRegex(EvidenceError, "MOCK_FAULT_OPERATOR_RESUME"): self.run_case(f)

    def test_auto_resume_without_operator_is_incomplete(self):
        f = fixture(); f[1]["refs"]["recovery_operator_sha256"] = None
        self.assertIn("EXPLICIT_OPERATOR_RECOVERY_MISSING", self.run_case(f)["incomplete_reasons"])

    def test_new_request_during_fault_is_rejected(self):
        for kind, payload in (("audio_request", {}), ("grammar_stage", dict(kind="request")),
                              ("session", dict(event="state_before", state="CueRequested"))):
            f = fixture(); f[2].append(dict(f[2][1], event_type=kind, payload=payload, host_mono_ms=240., sha256="c"*64))
            with self.subTest(kind=kind), self.assertRaisesRegex(EvidenceError, "MOCK_FAULT_AUDIO_DURING_PAUSE"): self.run_case(f)

    def test_new_request_between_observed_cause_and_detection_rejected(self):
        f = fixture(); f[2].append(dict(f[2][1], host_mono_ms=115., sha256="c"*64))
        with self.assertRaisesRegex(EvidenceError, "MOCK_FAULT_AUDIO_DURING_PAUSE"): self.run_case(f)

    def test_unrelated_later_fault_cannot_hide_behind_selected_reference(self):
        f = fixture(); f[2].append(dict(f[2][3], host_mono_ms=500., sha256="c"*64,
                                       payload=dict(f[2][3]["payload"], technical_fault_code="OTHER_FAILURE")))
        r = self.run_case(f)
        self.assertFalse(r["native_sequence_complete"])
        self.assertEqual(r["unexpected_native_fault_codes"], ["OTHER_FAILURE"])

    def test_consumed_novel_cannot_reenter_cue_or_clear_consumption(self):
        f = fixture(); f[2].append(dict(f[2][0], host_mono_ms=500., sha256="c"*64))
        with self.assertRaisesRegex(EvidenceError, "MOCK_FAULT_NOVEL_REPLAY"): self.run_case(f)
        f = fixture(); f[2][3]["payload"]["exposure_consumed"] = False
        with self.assertRaisesRegex(EvidenceError, "MOCK_FAULT_NOVEL_UNCONSUMED"): self.run_case(f)

    def test_no_novel_in_history_is_not_a_replay_pass(self):
        f = list(fixture()); f[5] = {}
        self.assertIsNone(self.run_case(f)["consumed_novel_not_replayed"])

    def test_first_novel_after_recovery_does_not_cover_pre_fault_replay(self):
        f = fixture(); f[2][0]["host_mono_ms"] = 400.
        self.assertIsNone(self.run_case(f)["consumed_novel_not_replayed"])

    def test_cause_from_other_opportunity_rejected(self):
        f = fixture(); f[2][2]["opportunity_id"] = "other"
        with self.assertRaisesRegex(EvidenceError, "MOCK_FAULT_OPPORTUNITY"): self.run_case(f)

    def test_physical_headset_cause_not_inferred_from_other_code(self):
        f = fixture(); f[0]["scenario"] = "headset_disconnect"
        r = self.run_case(f); self.assertFalse(r["software_scenario_cause_observed"])
        self.assertIn("SCENARIO_CAUSE_SOURCE_NOT_VERIFIED", r["incomplete_reasons"])

    def test_exact_250ms_not_stall_above_threshold(self):
        f = fixture(); f[2][2]["payload"]["duration_ms"] = 250.
        self.assertFalse(self.run_case(f)["software_scenario_cause_observed"])

    def test_unknown_or_not_applied_receipt_not_success(self):
        for outcome in ("unknown", "not_applied"):
            f = fixture(); f[1]["injection"]["outcome"] = outcome
            self.assertFalse(self.run_case(f)["native_sequence_complete"])

    def test_plan_identity_codes_numeric_and_unknown_keys_strict(self):
        for field, value in (("run_id", "wrong"), ("config_sha256", "b"*64), ("minimum_observation_ms", True), ("expected_native_codes", ["X", "X"])):
            f = fixture(); f[0][field] = value
            with self.subTest(field=field), self.assertRaises(EvidenceError): validate_plan(f[0], f[7])
        f = fixture(); f[0]["approved"] = True
        with self.assertRaisesRegex(EvidenceError, "MOCK_FIELDS"): validate_plan(f[0], f[7])

    def test_observer_times_bound_to_process_without_epoch_subtraction(self):
        f = fixture(); f[1]["injection"]["observed_utc"] = "2026-10-05T00:00:11Z"
        with self.assertRaisesRegex(EvidenceError, "MOCK_FAULT_OBSERVER_INTERVAL"): validate_observation(f[1], f[0], H, H, f[8])

    def test_suite_aggregates_bound_fault_results_not_labels(self):
        reports = [(s, dict(study="A", visit="D7", role="reference", software_reconciliation_complete=False, fault_codes=["X"], manifest_sha256=f"{i:064x}")) for i,s in enumerate(sorted(FAULTS))]
        results = [dict(scenario=s, manifest_sha256=r["manifest_sha256"], injection_artifact_bindings_verified=True, native_sequence_complete=True) for s,r in reports]
        result = coverage(reports, results)
        self.assertTrue(result["native_fault_sequences_complete"])
        self.assertTrue(result["fault_evidence_bindings_verified"])
        self.assertFalse(result["fault_injection_provenance_verified"])
        self.assertFalse(result["suite_complete"])
        results[0]["manifest_sha256"] = H
        with self.assertRaisesRegex(EvidenceError, "MOCK_SUITE_FAULT_MANIFEST"): coverage(reports, results)


class StartupNegative(unittest.TestCase):
    def files(self, root):
        def save(name, value):
            raw = value if isinstance(value, bytes) else compact(value)
            (root/name).write_bytes(raw); return dict(path=name, sha256=sha(raw))
        identity = dict(schema_version=1, build_id="simulation-test", commit_sha="b"*40, protocol_version="simulation-test-v1", editor_version="6000.6.0f1", target="StandaloneWindows64", dirty_source=False, station_schema_sha256=H, development_only=True)
        build = save("build.json", dict(build_identity=identity, result="Succeeded", errors=0, duration_seconds=1., total_bytes=10, development_build=False, files=[dict(path="app.exe", bytes=10, sha256=H)]))
        process = save("process.json", dict(version=1, process_id=123, process_exit=0, source_commit="b"*40, build_manifest_sha256=build["sha256"], started_utc="2026-10-05T00:00:00Z", ended_utc="2026-10-05T00:00:10Z"))
        line = b"JOINED_ENGINEERING_STATUS JOIN_CONFIG_REQUIRED participant_admission=false"
        log = save("native.log", b"Unity startup\n" + line + b"\r\n")
        plan = save("plan.json", dict(version=1, scope="SIMULATION_TEST", case_id="startup-01", scenario="startup_preflight", run_id="startup-01", build_manifest_sha256=build["sha256"], expected_native_codes=["JOIN_CONFIG_REQUIRED"]))
        observation = dict(version=1, scope="SIMULATION_TEST", case_id="startup-01", plan_sha256=plan["sha256"], run_id="startup-01", build_manifest=build, process_result=process, native_log=log, log_selection=dict(offset=len(b"Unity startup\n"), bytes=len(line)))
        return plan, observation, save

    def test_actual_shape_startup_refusal_needs_no_invented_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); p,o,save = self.files(root); pin = save("observation.json", o)
            r = verify_startup(root/p["path"], p["sha256"], root/pin["path"], pin["sha256"])
            self.assertTrue(r["startup_observation_bound"])
            self.assertFalse(r["counts_toward_seven_faults"])
            self.assertFalse(r["native_sequence_complete"])
            self.assertFalse(r["configuration_loaded_verified"])

    def test_wrong_source_log_offset_or_admission_refused(self):
        for change in ("offset", "source", "admission"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); p,o,save = self.files(root)
                if change == "offset": o["log_selection"]["offset"] += 1
                if change == "source":
                    v = strict((root/"process.json").read_bytes()); v["source_commit"] = "c"*40; o["process_result"] = save("process.json", v)
                if change == "admission":
                    raw = (root/"native.log").read_bytes().replace(b"admission=false", b"admission=true")
                    o["native_log"] = save("native.log", raw); o["log_selection"]["bytes"] -= 1
                pin = save("observation.json", o)
                with self.assertRaises(EvidenceError): verify_startup(root/p["path"], p["sha256"], root/pin["path"], pin["sha256"])

    def test_startup_cli_always_incomplete_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); p,o,save = self.files(root); pin = save("observation.json", o)
            args = ["faults", "--startup", "--plan", str(root/p["path"]), "--sha256", p["sha256"], "--observation", str(root/pin["path"]), "--observation-sha256", pin["sha256"], "--out", str(root/"result.json")]
            with patch("sys.argv", args): self.assertEqual(main(), 3)
            with patch("sys.argv", args): self.assertEqual(main(), 2)


if __name__ == "__main__": unittest.main()
