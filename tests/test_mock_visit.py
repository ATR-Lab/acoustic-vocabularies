"""Synthetic protocol tests. They are not captured native visit evidence."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from tools.mock_visit.records import EvidenceError, sha, strict, unhashed_bytes, verify_chain
from tools.mock_visit.reconcile import reconcile_records, schedule_items, relative, read
from tools.mock_visit import native, content
from tools.mock_visit.suite import coverage
from tools.write_mock_run_manifest import emit

H = "a" * 64
G = "a" * 32
IDENTITY = dict(session_id="SIMULATION_TEST", coded_id="DEMO", visit_id="D7", station_id="SIM",
                protocol_version="engineering", build_sha256=H)


def compact(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode()


def chain(rows, kind="data"):
    result, previous = [], "0" * 64
    for index, row in enumerate(rows):
        row = dict(row, sequence=index, previous_sha256=previous)
        row.pop("sha256", None)
        row["sha256"] = previous = sha(compact(row) + (b"\n" if kind == "data" else b""))
        result.append(compact(row) + b"\n")
    return b"".join(result)


def fixture(block="trained", start=1000):
    item = dict(trial_id="trial-1", block=block, plays=1, slot_s=14, block_index=0, item_index=0)
    items = {"trial-1": item}
    records, session_rows = [], []
    def add(kind, p):
        index = len(records)
        row = dict(schema_version="data-events-provisional-1", sequence=index, event_id=f"{index:032x}",
                   clock_epoch=G, host_mono_ms=float(index), identity=IDENTITY, event_type=kind,
                   opportunity_id="trial-1", attempt_id="trial-1", audio_request_id=G if kind.startswith("audio_") else None,
                   previous_sha256="0" * 64, payload=copy.deepcopy(p), sha256=H)
        records.append(row)
    p = dict(event="state_before", clock_epoch=G, schedule_sha256=H, trial_id="trial-1", retry_of=None,
             block_index=0, item_index=0, host_mono_ms=0., scheduled_onset_mono_ms=float(start), state=None,
             audible_status="NotRequested", exposure_consumed=False, reset_ok=True, focus_ok=True,
             technical_fault_code=None, response_code=None, evidence_sha256=None, opportunity_id="trial-1", audio_request_ids=[G])
    def state(name, stamp):
        p.update(state=name, host_mono_ms=stamp)
        if name == "CueRequested": p.update(audible_status="Uncertain", exposure_consumed=True)
        for event in ("state_before", "state_after"):
            p["event"] = event; add("session", p)
    state("Loaded", start-200); state("Ready", start-150); state("CueRequested", start-100)
    if block == "novel":
        p["event"] = "novel_buffer_authorized"; add("session", p)
    audio = dict(code="AUDIO_REQUESTED", audio_id=G, waveform_sha256=H, pcm_sha256=H,
                 action_pcm_sha256=None, referent_pcm_sha256=None, observed_mono_ms=start-100.,
                 request_mono_ms=start-100., scheduled_mono_ms=float(start), scheduled_dsp_s=4.,
                 onset_estimate_mono_ms=None, onset_uncertainty_ms=None, first_callback_dsp_s=None,
                 delivered_samples=0, callback_count=0, simulation_test=True,
                 software_output_estimate_mono_ms=float(start), software_output_uncertainty_ms=2.)
    add("audio_request", audio)
    audio.update(code="SIMULATION_DELIVERY_OBSERVED", observed_mono_ms=float(start), first_callback_dsp_s=4., delivered_samples=480, callback_count=1)
    add("audio_observation", audio)
    audio.update(code="AUDIO_PLAYBACK_COMPLETED", observed_mono_ms=start+1000., delivered_samples=48000, callback_count=100)
    add("audio_observation", audio)
    state("ResponseOpen", start)
    p.update(event="response", host_mono_ms=start+2000., response_code="timeout"); add("session", p)
    state("Closed", start+12000); state("Reset", start+12000); state("Done", start+12500)
    p.update(event="visit_complete", trial_id=None, opportunity_id=None, state=None,
             scheduled_onset_mono_ms=None, host_mono_ms=start+14000., audio_request_ids=[])
    add("session", p); records[-1].update(opportunity_id=None, attempt_id=None)
    trials = [dict(attempt_id="trial-1", opportunity_id="trial-1", retry_of="", audio_request_ids=json.dumps([G]),
                   interrupted="false", exposure_consumed="true", response_code="timeout")]
    exposures = [dict(audio_request_id=G, attempt_id="trial-1", opportunity_id="trial-1", audio_id=G,
                      waveform_sha256=H, audible_status="uncertain", exposure_consumed="true",
                      audio_onset_estimate_mono_ms="", onset_uncertainty_ms="", audio_request_mono_ms=str(start-100.),
                      scheduled_onset_mono_ms=str(float(start)), callback_observed="true")]
    return records, trials, exposures, items


def replanned_fixture():
    """One cancelled planning context followed by an explicit fresh resume."""
    f = fixture(); prefix = copy.deepcopy(f[0][:4])
    for index, row in enumerate(prefix):
        row["payload"].update(scheduled_onset_mono_ms=500., audio_request_ids=["b"*32], host_mono_ms=100.+index)
    resume = copy.deepcopy(prefix[-1])
    resume.update(attempt_id=None, opportunity_id=None)
    resume["payload"].update(event="operator_resume", trial_id=None, opportunity_id=None, state=None,
                             scheduled_onset_mono_ms=None, audio_request_ids=[], host_mono_ms=700.)
    f[0][:0] = prefix + [resume]
    for index, row in enumerate(f[0]): row["sequence"] = index
    return f


class Chains(unittest.TestCase):
    def test_exact_native_hash_bytes_keep_double_and_escapes(self):
        original = b'{"x":1.0,"text":"\\u00e9, \\\"sha256\\\":x","nested":{"sha256":"keep"}}'
        hashed = original[:-1] + b',"sha256":"' + sha(original+b"\n").encode() + b'"}\n'
        self.assertEqual(unhashed_bytes(hashed, newline=True), original+b"\n")

    def test_data_hash_chain_and_tampered_byte(self):
        rows = fixture()[0]
        raw = chain(rows)
        self.assertEqual(len(verify_chain([("events-0000.local.jsonl", raw)], "data")), len(rows))
        with self.assertRaisesRegex(EvidenceError, "MOCK_CHAIN_HASH"):
            verify_chain([("x", raw.replace(b'"station_id":"SIM"', b'"station_id":"BAD"', 1))], "data")

    def test_duplicate_key_and_nonfinite_refused(self):
        for raw in [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e9999}', b'\xef\xbb\xbf{}']:
            with self.subTest(raw=raw), self.assertRaises(EvidenceError): strict(raw)

    def test_unacknowledged_tail_is_not_completion(self):
        with self.assertRaisesRegex(EvidenceError, "MOCK_UNACKNOWLEDGED_TORN_TAIL"):
            verify_chain([("events-0000.local.jsonl", chain(fixture()[0]) + b'{"torn":')], "data")

    def test_exact_recovery_receipt_preserves_torn_bytes(self):
        rows = fixture()[0][:2]
        prefix = chain(rows); tail = b'{"torn":'
        recovery = copy.deepcopy(rows[0])
        recovery.update(sequence=2, previous_sha256=strict(prefix.splitlines()[-1])["sha256"], event_type="recovery",
                        event_id="f"*32, host_mono_ms=3., opportunity_id=None, attempt_id=None)
        recovery["payload"] = {"preserved_tails": [{"segment":"events-0000.local.jsonl", "tail_offset":len(prefix),
            "tail_sha256":sha(tail), "segment_sha256":sha(prefix+tail)}]}
        del recovery["sha256"]; recovery["sha256"] = sha(compact(recovery)+b"\n")
        recovered = compact(recovery)+b"\n"
        self.assertEqual(len(verify_chain([("events-0000.local.jsonl",prefix+tail),("events-0001.local.jsonl",recovered)],"data")),3)
        recovery["payload"]["preserved_tails"][0]["tail_offset"] += 1
        with self.assertRaisesRegex(EvidenceError,"MOCK_RECOVERY_ACK"):
            verify_chain([("events-0000.local.jsonl",prefix+tail),("events-0001.local.jsonl",compact(recovery)+b"\n")],"data")

    def test_bad_paths(self):
        for value in ["../secret", "C:/secret", "a\\b", "/tmp/x", "a//b", "a/./b", "a. /b"]:
            with self.subTest(value=value), self.assertRaises(EvidenceError): relative(value)

    def test_independent_file_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/"file"; p.write_bytes(b"valid")
            self.assertEqual(read(p,sha(b"valid")),b"valid")
            p.write_bytes(b"changed")
            with self.assertRaisesRegex(EvidenceError,"MOCK_FILE_HASH"): read(p,sha(b"valid"))


class Reconciliation(unittest.TestCase):
    def run_fixture(self, f): return reconcile_records(*f,H)

    def test_positive_software_history_consumed_without_acoustic_claim(self):
        r = self.run_fixture(fixture())
        self.assertEqual(r["incomplete"],set()); self.assertEqual(r["callback_plays"],1)

    def test_positive_novel_requires_prior_durable_permit(self):
        self.assertEqual(self.run_fixture(fixture("novel"))["done"],{"trial-1"})

    def test_novel_without_permit_rejected(self):
        f=fixture("novel"); f[0][:]=[r for r in f[0] if r["payload"].get("event")!="novel_buffer_authorized"]
        with self.assertRaisesRegex(EvidenceError,"MOCK_NOVEL_OR_NO_CUE_AUDIO"): self.run_fixture(f)

    def test_native_complete_without_done_is_incomplete(self):
        f=fixture(); f[0][:]=[r for r in f[0] if r["payload"].get("state")!="Done"]
        f[1][0]["interrupted"]="true"
        self.assertIn("SCHEDULED_OPPORTUNITIES_INCOMPLETE",self.run_fixture(f)["incomplete"])

    def test_tail_cannot_be_shortened(self):
        f=fixture();f[0][-1]["payload"]["host_mono_ms"]-=1
        with self.assertRaisesRegex(EvidenceError,"MOCK_TAIL_SHORTENED"):self.run_fixture(f)

    def test_acoustic_authority_cannot_be_made_from_callback(self):
        f=fixture();next(r for r in f[0] if r["event_type"]=="audio_observation")["payload"]["onset_estimate_mono_ms"]=1000
        with self.assertRaisesRegex(EvidenceError,"MOCK_ACOUSTIC_AUTHORITY_FORBIDDEN"):self.run_fixture(f)

    def test_consistent_rehashed_wrong_pcm_still_fails_binding(self):
        f=fixture();next(r for r in f[0] if r["event_type"]=="audio_observation")["payload"]["pcm_sha256"]="b"*64
        with self.assertRaisesRegex(EvidenceError,"MOCK_AUDIO_BINDING_CHANGED"):self.run_fixture(f)

    def test_uncertain_exposure_not_free_replay(self):
        f=fixture();f[2][0]["exposure_consumed"]="false"
        with self.assertRaisesRegex(EvidenceError,"MOCK_CSV_ACOUSTIC_SCOPE"):self.run_fixture(f)

    def test_consumption_reversal_rejected(self):
        f=fixture();r=next(r for r in f[0] if r["event_type"]=="session" and r["payload"].get("state")=="Done")
        r["payload"].update(exposure_consumed=False,audible_status="NotRequested")
        with self.assertRaisesRegex(EvidenceError,"MOCK_CONSUMPTION_REVERSED"):self.run_fixture(f)

    def test_observation_requires_original_request(self):
        f=fixture();f[0][:]=[r for r in f[0] if r["event_type"]!="audio_request"]
        with self.assertRaisesRegex(EvidenceError,"MOCK_OBSERVATION_WITHOUT_REQUEST"):self.run_fixture(f)

    def test_missing_response_cannot_pass_completed_protected_item(self):
        f=fixture();f[0][:]=[r for r in f[0] if r["payload"].get("event")!="response"];f[1][0]["response_code"]=""
        with self.assertRaisesRegex(EvidenceError,"MOCK_RESPONSE_LOG_MISSING"):self.run_fixture(f)

    def test_19ms_deviation_is_not_within_2ms_software_uncertainty(self):
        f=fixture()
        for r in f[0]:
            if r["event_type"].startswith("audio_"):r["payload"]["software_output_estimate_mono_ms"]+=19
        with self.assertRaisesRegex(EvidenceError,"MOCK_PLAY_OFFSET"):self.run_fixture(f)

    def test_boundary_uncertainty_is_allowed(self):
        f=fixture()
        for r in f[0]:
            if r["event_type"].startswith("audio_"):r["payload"]["software_output_estimate_mono_ms"]+=2
        self.assertEqual(self.run_fixture(f)["incomplete"],set())

    def test_incomplete_callback_is_not_complete(self):
        f=fixture();f[0][:]=[r for r in f[0] if r["payload"].get("code")!="AUDIO_PLAYBACK_COMPLETED"]
        self.assertIn("AUDIO_CALLBACK_OR_COMPLETION_MISSING",self.run_fixture(f)["incomplete"])

    def test_safe_precue_context_replacement_is_allowed(self):
        f=replanned_fixture();a=self.run_fixture(f)["attempts"]["trial-1"]
        self.assertEqual(a["retired_unplayed_contexts"],1)
        self.assertEqual(a["retired_contexts"][0]["first"]["payload"]["audio_request_ids"],["b"*32])
        self.assertEqual(a["first"]["payload"]["audio_request_ids"],[G])

    def test_replanning_requires_explicit_resume_and_increasing_context(self):
        for mutation in ("resume", "onset", "clock", "pending"):
            f=replanned_fixture()
            if mutation=="resume": f[0][4]["payload"]["event"]="session_paused"
            elif mutation=="onset": f[0][5]["payload"]["scheduled_onset_mono_ms"]=400.
            elif mutation=="clock": f[0][5]["payload"]["clock_epoch"]="c"*32
            else: del f[0][3]
            with self.subTest(mutation=mutation),self.assertRaisesRegex(EvidenceError,"MOCK_CONTEXT_REPLAN_ORDER|MOCK_CONSUMED_CONTEXT_REPLAY"):
                self.run_fixture(f)

    def test_replanning_cannot_reuse_a_retired_audio_id(self):
        f=replanned_fixture();f[0][5]["payload"]["audio_request_ids"]=["b"*32]
        with self.assertRaisesRegex(EvidenceError,"MOCK_CONTEXT_AUDIO_ID_REUSE"): self.run_fixture(f)

    def test_new_engine_epoch_preserves_unplayed_history_without_clock_claim(self):
        f=replanned_fixture()
        for row in f[0][4:]:
            if row["event_type"]=="session":row["payload"]["clock_epoch"]="c"*32
        result=self.run_fixture(f)
        self.assertIn("PLANNING_CLOCK_EPOCH_UNBOUND",result["incomplete"])
        self.assertEqual(result["attempts"]["trial-1"]["retired_unplayed_contexts"],1)

    def test_torn_precue_intent_is_not_a_retirable_context(self):
        f=replanned_fixture();f[0][3]["payload"].update(event="state_before",state="CueRequested")
        with self.assertRaisesRegex(EvidenceError,"MOCK_STATE_PAIR|MOCK_CONSUMED_CONTEXT_REPLAY"): self.run_fixture(f)

    def test_consumed_context_cannot_be_replaced(self):
        f=fixture();prefix=copy.deepcopy(f[0][:6])
        for row in prefix:
            row["payload"]["scheduled_onset_mono_ms"]=500
            row["payload"]["audio_request_ids"]=["b"*32]
            row["payload"]["host_mono_ms"]=100
        f[0][:0]=prefix
        with self.assertRaisesRegex(EvidenceError,"MOCK_CONSUMED_CONTEXT_REPLAY"):self.run_fixture(f)

    def test_unbounded_uncertainty_cannot_hide_shift(self):
        f=fixture()
        for row in f[0]:
            if row["event_type"].startswith("audio_"):row["payload"]["software_output_uncertainty_ms"]=10000
        with self.assertRaisesRegex(EvidenceError,"MOCK_SOFTWARE_UNCERTAINTY_LIMIT"):self.run_fixture(f)

    def test_real_producer_schedule_counts(self):
        root=Path(__file__).resolve().parents[1]/"schedules/examples/demo"
        for study,person in (("A","A-C01-L01"),("B","B-C01-M1")):
            for path in (root/study/f"{study}-C01"/"schedules"/person).glob("*.json"):
                s=json.loads(path.read_bytes())
                with self.subTest(path=path.name): self.assertGreater(len(schedule_items(s,study,s["visit"])),0)

    def test_schedule_count_cannot_shrink_to_one_case(self):
        root=Path(__file__).resolve().parents[1]
        s=json.loads((root/"schedules/examples/demo/A/A-C01/schedules/A-C01-L01/D7.json").read_bytes())
        s["blocks"][0]["items"]=s["blocks"][0]["items"][:1];s["blocks"][0]["expected_count"]=1
        with self.assertRaisesRegex(EvidenceError,"MOCK_SCHEDULE_COUNTS"):schedule_items(s,"A","D7")


class Supplemental(unittest.TestCase):
    def operator_rows(self):
        rows=[]
        for n,command in enumerate(("load","start"),1):
            request=dict(version=1,session_nonce=G,request_id=f"{n:032x}",sequence=n,command=command,run_sheet_manifest_sha256=H,schedule_sha256=H)
            p=dict(kind="request",request=request,host_mono_ms=n*100,previous_consumed_sequence=n-1,
                   stop_supersedes_through_sequence=None,prior_receipt_acknowledgement=None,receipt=None)
            rows.append(dict(session_nonce=G,record=copy.deepcopy(p)))
            p.update(kind="result",receipt=dict(request_id=request["request_id"],sequence=n,status="accepted",code="accepted"))
            rows.append(dict(session_nonce=G,record=p))
        return rows

    def test_operator_exact_request_result_pairs(self):
        self.assertEqual(native.operator([self.operator_rows()],H),set())

    def test_operator_accepted_result_without_request_rejected(self):
        rows=self.operator_rows();del rows[0]
        with self.assertRaisesRegex(EvidenceError,"MOCK_OPERATOR_RESULT_ORDER"):native.operator([rows],H)

    def test_operator_unfinished_request_incomplete(self):
        rows=self.operator_rows();rows.pop()
        self.assertIn("OPERATOR_REQUEST_WITHOUT_RESULT",native.operator([rows],H))

    def test_operator_changed_schedule_rejected(self):
        rows=self.operator_rows();rows[0]["record"]["request"]["schedule_sha256"]="b"*64
        with self.assertRaisesRegex(EvidenceError,"MOCK_OPERATOR_BINDING"):native.operator([rows],H)

    def views(self):
        return [dict(kind="view",payload=dict(kind="assessment_view_command",attempt_id="trial-1",phase=phase,
                       observed_mono_ms=stamp,text_sha256=H,visible=True,evidence_level="native_view_command_not_physical_capture"))
                for phase,stamp in (("protected_neutral",1000),("acknowledgment",13000),("tail_end",15000))]

    def test_display_tail_software_only(self):
        f=fixture();r=reconcile_records(*f,H)
        missing=native.display(self.views(),r["attempts"],f[3],"A","D7")
        self.assertNotIn("PROTECTED_DISPLAY_TRACE_INCOMPLETE",missing)
        self.assertIn("NATIVE_RUN_END_MISSING",missing)

    def test_display_ack_cannot_start_early(self):
        f=fixture();r=reconcile_records(*f,H);v=self.views();v[1]["payload"]["observed_mono_ms"]=12000
        with self.assertRaisesRegex(EvidenceError,"MOCK_ACK_TAIL_EARLY"):native.display(v,r["attempts"],f[3],"A","D7")

    def test_suite_cannot_infer_missing_visits_from_one_success(self):
        result=coverage([("normal",dict(study="A",visit="D0",role="reference",software_reconciliation_complete=True,fault_codes=[]))])
        self.assertFalse(result["normal_software_complete"]);self.assertEqual(len(result["missing_normal_visits"]),11)
        self.assertFalse(result["fault_injection_provenance_verified"])

    def test_suite_duplicate_visit_rejected(self):
        row=("normal",dict(study="A",visit="D0",role="reference",software_reconciliation_complete=True,fault_codes=[]))
        with self.assertRaisesRegex(EvidenceError,"MOCK_SUITE_DUPLICATE_VISIT"):coverage([row,row])

    def test_canonical_wave_hash_rejects_metadata_and_wrong_rate(self):
        import struct
        raw=struct.pack('<4sI4s4sIHHIIHH4sI',b'RIFF',40,b'WAVE',b'fmt ',16,1,1,48000,96000,2,16,b'data',4)+b'\x00\x00\x01\x00'
        self.assertEqual(content.pcm(raw),raw[44:])
        bad=bytearray(raw);struct.pack_into('<I',bad,24,44100)
        with self.assertRaisesRegex(EvidenceError,"MOCK_WAV_CANONICAL"):content.pcm(bytes(bad))
        with self.assertRaisesRegex(EvidenceError,"MOCK_WAV_CANONICAL"):content.pcm(raw+b'INFO')

    def yoke_rows(self):
        binding=dict(package_sha256=H,bank_sha256=H,allocation_sha256=H,unit_binding_sha256=H,review_sha256=H,visit="V2",menu_keys=["K-a1"],role="active")
        source=dict(event_id=G,menu_key="K-a1",meaning_display_id="m",slot_start_mono_ms=1000.,kind="play_request",
                    presentation_index=1,candidate_id="c",pcm_sha256=H,file_sha256=H)
        onset=dict(source,event_id="b"*32,kind="onset_authority",expected_mono_ms=6001.)
        a=[dict(record=dict(binding=binding)),dict(record=source),dict(record=onset),dict(record=dict(kind="sealed"))]
        row=dict(source,yoked_source_event_id=G,slot_start_mono_ms=10000.,expected_mono_ms=15001.,kind="onset_authority",onset_uncertainty_ms=2.)
        y=[dict(record=dict(binding=dict(binding,role="yoked"))),dict(record=row),dict(record=dict(kind="sealed_yoked",active_ledger_sha256=H))]
        return a,y

    def test_yoked_software_offsets_are_compared_to_active_observations(self):
        a,y=self.yoke_rows()
        self.assertTrue(native.compare_yoked(a,y,H)["software_trace_timing_matched"])
        y[1]["record"]["expected_mono_ms"]+=19
        with self.assertRaisesRegex(EvidenceError,"MOCK_YOKED_OUTPUT_TIMING"):native.compare_yoked(a,y,H)

    def test_yoked_intermediate_choice_forbidden(self):
        a,y=self.yoke_rows();y[1]["record"]["kind"]="choice_revised"
        with self.assertRaisesRegex(EvidenceError,"MOCK_YOKED_SOURCE_EVENT"):native.compare_yoked(a,y,H)

    def test_yoked_wrong_source_pin_forbidden(self):
        a,y=self.yoke_rows()
        with self.assertRaisesRegex(EvidenceError,"MOCK_YOKED_SEAL_PIN"):native.compare_yoked(a,y,"b"*64)

    def test_frame_summary_recomputed_from_raw_intervals(self):
        from tools.mock_visit.reconcile import table
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            files={"metadata.json":compact(dict(selected_refresh_hz=100.,physical_qualification=False)),
                   "frames.csv":b'attempt_id,opportunity_id,window_id,window_kind,frame_index,from_mono_ms,to_mono_ms,render_interval_ms,overlap_ms,runtime_refresh_hz\ntrial-1,trial-1,response,response,1,0,10,10,10,100\ntrial-1,trial-1,response,response,2,10,30,20,20,100\n',
                   "events.jsonl":compact(dict(kind="summary",attempt_id="trial-1",opportunity_id="trial-1",observed_mono_ms=30.,frame_freeze_ms=20.,frame_count=2,within_1_5x_count=1,capture_complete=True,cancelled_before_window=False))+b'\n'}
            entries=[]
            for name,raw in files.items():(root/name).write_bytes(raw);entries.append(dict(path=name,bytes=len(raw),sha256=sha(raw)))
            manifest=compact(dict(version=1,capture_kind="application_render_callbacks_not_photon_timestamps",files=entries))
            missing,report=native.frames(root,[(dict(path="manifest.json"),manifest)],{"trial-1":{}},table,read,relative)
            self.assertIn("FRAME_BUDGET_SCREEN_FAILED",missing);self.assertEqual(report["max_render_interval_ms"],20.)
            changed=strict(files["events.jsonl"]);changed["within_1_5x_count"]=2;raw=compact(changed)+b'\n';(root/"events.jsonl").write_bytes(raw)
            entries[-1].update(bytes=len(raw),sha256=sha(raw));manifest=compact(dict(version=1,capture_kind="application_render_callbacks_not_photon_timestamps",files=entries))
            with self.assertRaisesRegex(EvidenceError,"MOCK_FRAME_COUNTS"):native.frames(root,[(dict(path="manifest.json"),manifest)],{"trial-1":{}},table,read,relative)


class RetiredFrameEpochs(unittest.TestCase):
    def capture(self):
        attempts=reconcile_records(*replanned_fixture(),H)["attempts"]
        cancelled=dict(kind="summary",attempt_id="trial-1",opportunity_id="trial-1",observed_mono_ms=150.,
            frame_freeze_ms=None,frame_count=0,within_1_5x_count=0,capture_complete=False,cancelled_before_window=True)
        final=dict(cancelled,observed_mono_ms=15000.,frame_freeze_ms=10.,frame_count=1,within_1_5x_count=1,
            capture_complete=True,cancelled_before_window=False)
        rows=[dict(attempt_id="trial-1",opportunity_id="trial-1",window_id="response",window_kind="response",
                   frame_index=1,from_mono_ms=1000.,to_mono_ms=1010.,render_interval_ms=10.,overlap_ms=10.,runtime_refresh_hz=100.)]
        metadata=dict(selected_refresh_hz=100.,physical_qualification=False,clock="Unity_process_Stopwatch_ms",clock_epoch="d"*32)
        return attempts,[cancelled,final],rows,metadata

    def verify(self, capture):
        import csv,io
        from tools.mock_visit.reconcile import table
        attempts,events,rows,metadata=capture
        columns="attempt_id opportunity_id window_id window_kind frame_index from_mono_ms to_mono_ms render_interval_ms overlap_ms runtime_refresh_hz".split()
        text=io.StringIO();writer=csv.DictWriter(text,fieldnames=columns);writer.writeheader();writer.writerows(rows)
        files={"metadata.json":compact(metadata),"frames.csv":text.getvalue().encode(),
               "events.jsonl":b"".join(compact(row)+b"\n" for row in events)}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);descriptors=[]
            for name,raw in files.items():
                (root/name).write_bytes(raw);descriptors.append(dict(path=name,bytes=len(raw),sha256=sha(raw)))
            manifest=compact(dict(version=1,capture_kind="application_render_callbacks_not_photon_timestamps",files=descriptors))
            return native.frames(root,[(dict(path="manifest.json"),manifest)],attempts,table,read,relative)

    def test_retired_context_and_final_capture_are_both_retained(self):
        missing,report=self.verify(self.capture())
        self.assertIn("FRAME_CAPTURE_INCOMPLETE",missing)
        self.assertEqual(report["final_attempt_summaries"],1)
        self.assertEqual(report["raw_attributed_intervals"],1)
        retired=report["retired_unplayed_planning_epochs"]
        self.assertEqual(len(retired),1);self.assertEqual(retired[0]["audio_request_ids"],["b"*32])
        self.assertEqual(retired[0]["scheduled_onset_mono_ms"],500.)
        self.assertFalse(report["physical_display_timing_qualified"])

    def test_two_cancelled_epochs_bind_in_order_without_overwriting(self):
        f=fixture(start=2000);prefix=copy.deepcopy(replanned_fixture()[0][:5]);middle=copy.deepcopy(f[0][:4])
        for index,row in enumerate(middle):
            row["payload"].update(scheduled_onset_mono_ms=1000.,audio_request_ids=["c"*32],host_mono_ms=800.+index)
        resume=copy.deepcopy(prefix[-1]);resume["payload"]["host_mono_ms"]=1700.
        f[0][:0]=prefix+middle+[resume]
        for index,row in enumerate(f[0]):row["sequence"]=index
        attempts=reconcile_records(*f,H)["attempts"]
        capture=self.capture();capture=capture[:];capture=(attempts,capture[1],capture[2],capture[3])
        capture[1].insert(1,dict(capture[1][0],observed_mono_ms=850.))
        capture[2][0].update(from_mono_ms=2000.,to_mono_ms=2010.)
        _,report=self.verify(capture)
        self.assertEqual([e["audio_request_ids"] for e in report["retired_unplayed_planning_epochs"]],[["b"*32],["c"*32]])
        capture[1][0],capture[1][1]=capture[1][1],capture[1][0]
        with self.assertRaisesRegex(EvidenceError,"MOCK_FRAME_RETIRED_BOUNDARY"):self.verify(capture)

    def test_both_zero_frame_epochs_keep_later_fault_and_incompletion(self):
        capture=self.capture();capture[2].clear()
        capture[1][-1].update(frame_count=0,within_1_5x_count=0,frame_freeze_ms=None,capture_complete=False)
        capture[1].insert(1,dict(kind="fault",attempt_id="trial-1",opportunity_id="trial-1",observed_mono_ms=900.,
            technical_fault_code="FRAME_ATTEMPT_INTERRUPTED",render_gap_ms=0.,watchdog=False))
        missing,report=self.verify(capture)
        self.assertTrue({"FRAME_FAULT_PRESENT","FRAME_CAPTURE_INCOMPLETE"}<=missing)
        self.assertEqual(report["raw_attributed_intervals"],0)

    def test_missing_cancelled_summary_is_not_inferred(self):
        capture=self.capture();del capture[1][0]
        with self.assertRaisesRegex(EvidenceError,"MOCK_FRAME_RETIRED_STARTED"):self.verify(capture)

    def test_missing_all_summaries_refuses_retired_history(self):
        capture=self.capture();capture[1].clear()
        with self.assertRaisesRegex(EvidenceError,"MOCK_FRAME_RETIRED_SUMMARY_MISSING"):self.verify(capture)

    def test_duplicate_or_reordered_summaries_refused(self):
        for which in ("cancelled","final","reversed"):
            capture=self.capture()
            if which=="cancelled":capture[1].insert(1,copy.deepcopy(capture[1][0]))
            elif which=="final":capture[1].append(copy.deepcopy(capture[1][-1]))
            else:capture[1].reverse()
            with self.subTest(which=which),self.assertRaises(EvidenceError):self.verify(capture)

    def test_started_or_nonboolean_retired_summary_refused(self):
        for key,value in (("frame_count",1),("capture_complete",True),("cancelled_before_window",False),
                          ("within_1_5x_count",True),("frame_freeze_ms",0.)):
            capture=self.capture();capture[1][0][key]=value
            with self.subTest(key=key),self.assertRaises(EvidenceError):self.verify(capture)

    def test_cancel_stamp_must_bind_to_loaded_precue_preresume_epoch(self):
        for stamp in (50.,500.,701.,801.):
            capture=self.capture();capture[1][0]["observed_mono_ms"]=stamp
            with self.subTest(stamp=stamp),self.assertRaisesRegex(EvidenceError,"MOCK_FRAME_RETIRED_BOUNDARY"):self.verify(capture)

    def test_retired_or_boundary_straddling_interval_refused(self):
        for start,end in ((140.,150.),(799.,809.)):
            capture=self.capture();capture[2][0].update(from_mono_ms=start,to_mono_ms=end)
            with self.subTest(start=start),self.assertRaisesRegex(EvidenceError,"MOCK_FRAME_RETIRED_INTERVAL"):self.verify(capture)

    def test_stalled_interval_spanning_registration_counts_full_gap_without_false_integrity_failure(self):
        capture=self.capture();capture[2][0].update(from_mono_ms=100.,to_mono_ms=1010.,render_interval_ms=910.,overlap_ms=10.)
        capture[1][-1].update(frame_freeze_ms=910.,within_1_5x_count=0)
        missing,report=self.verify(capture)
        self.assertIn("FRAME_BUDGET_SCREEN_FAILED",missing)
        self.assertEqual(report["max_render_interval_ms"],910.)

    def test_stalled_interval_cannot_charge_old_window_overlap_or_old_cue_id(self):
        for mutation in ("overlap","cue"):
            capture=self.capture();capture[2][0].update(from_mono_ms=100.,to_mono_ms=1010.,render_interval_ms=910.,overlap_ms=10.)
            if mutation=="overlap":capture[2][0]["overlap_ms"]=910.
            else:capture[2][0].update(window_kind="cue",window_id="b"*32)
            with self.subTest(mutation=mutation),self.assertRaises(EvidenceError):self.verify(capture)

    def test_new_engine_epoch_has_no_implicit_frame_clock_mapping(self):
        capture=self.capture()
        capture[0]["trial-1"]["retired_contexts"][0]["replacement"]["payload"]["clock_epoch"]="e"*32
        with self.assertRaisesRegex(EvidenceError,"MOCK_FRAME_CONTEXT_CLOCK"):self.verify(capture)

    def test_fault_in_retired_epoch_is_not_a_safe_empty_cancellation(self):
        capture=self.capture();capture[1].insert(0,dict(kind="fault",attempt_id="trial-1",opportunity_id="trial-1",
            observed_mono_ms=140.,technical_fault_code="FRAME_FREEZE",render_gap_ms=300.,watchdog=True))
        with self.assertRaisesRegex(EvidenceError,"MOCK_FRAME_RETIRED_STARTED"):self.verify(capture)

    def test_wrong_capture_clock_cannot_join_contexts(self):
        capture=self.capture();capture[3]["clock"]="different_process_ms"
        with self.assertRaisesRegex(EvidenceError,"MOCK_FRAME_CONTEXT_CLOCK"):self.verify(capture)

    def test_summary_cannot_precede_its_raw_interval(self):
        capture=self.capture();capture[1][-1]["observed_mono_ms"]=1001.
        with self.assertRaisesRegex(EvidenceError,"MOCK_FRAME_SUMMARY_BEFORE_INTERVAL"):self.verify(capture)

    def test_changed_time_does_not_make_old_cancel_a_valid_consumed_final(self):
        capture=self.capture();capture[2].clear()
        capture[1][-1]=dict(capture[1][0],observed_mono_ms=850.)
        with self.assertRaisesRegex(EvidenceError,"MOCK_FRAME_CANCELLED_AFTER_CUE"):self.verify(capture)

    def menu_fixture(self):
        attempts=reconcile_records(*replanned_fixture(),H)["attempts"]
        binding=dict(package_sha256=H,bank_sha256=H,allocation_sha256=H,schedule_sha256=H,
                     unit_binding_sha256=H,review_sha256=H,visit="V1",role="active",menu_keys=["profile"])
        header=dict(kind="header",format="av-menu-ledger/1",binding=binding,created_utc="2026-10-05T00:00:00Z",clock_epoch=G)
        interrupted={key:None for key in "expected_mono_ms onset_uncertainty_ms audio_request_id presentation_index candidate_id pcm_sha256 file_sha256 yoked_source_event_id selected_index defaulted phase receipt_sha256".split()}
        interrupted.update(kind="menu_interrupted",event_id=G,attempt_id="trial-1",opportunity_id="trial-1",menu_key="profile",
                           meaning_display_id="synthetic-profile",slot_start_mono_ms=500.,mono_ms=150.,matching_deviation_id=G)
        return [dict(record=header),dict(record=interrupted)],attempts,{"trial-1":dict(block="profile_menu")}

    def verify_menu(self, value):
        rows,attempts,items=value
        return native.menu([rows],attempts,items,{},"active",H,H)

    def test_known_empty_retired_menu_interruption_stays_incomplete(self):
        missing=self.verify_menu(self.menu_fixture())
        self.assertTrue({"MENU_EVENTS_INCOMPLETE","MENU_LEDGER_UNSEALED","MENU_SEAL_MISSING",
                         "MENU_RETIRED_PLANNING_INTERRUPTION","MENU_ATTEMPT_COVERAGE_INCOMPLETE"}<=missing)

    def test_retired_menu_cannot_contain_started_content_or_receipt(self):
        mutations=[("kind",kind) for kind in ("menu_start","play_request","display_changed","choice_final","selection_verified")]
        mutations += [("audio_request_id",G),("pcm_sha256",H),("selected_index",1),("phase","Instructions"),
                      ("receipt_sha256",H),("expected_mono_ms",500.),("matching_deviation_id","b"*32)]
        for key,value in mutations:
            f=self.menu_fixture();f[0][1]["record"][key]=value
            with self.subTest(key=key,value=value),self.assertRaisesRegex(EvidenceError,"MOCK_MENU_RETIRED_CONTENT"):self.verify_menu(f)

    def test_retired_menu_wrong_anchor_clock_or_duplicate_refused(self):
        for mutation in ("anchor","time","duplicate","cue"):
            f=self.menu_fixture();p=f[0][1]["record"]
            if mutation=="anchor":p["slot_start_mono_ms"]=499.
            elif mutation=="time":p["mono_ms"]=700.
            elif mutation=="duplicate":
                duplicate=copy.deepcopy(f[0][1]);duplicate["record"].update(event_id="e"*32,matching_deviation_id="e"*32);f[0].append(duplicate)
            else:f[1]["trial-1"]["retired_contexts"][0]["records"][0]["payload"]["state"]="CueRequested"
            with self.subTest(mutation=mutation),self.assertRaises(EvidenceError):self.verify_menu(f)

    def test_retired_menu_interruption_cannot_follow_final_menu_start(self):
        f=self.menu_fixture();started=copy.deepcopy(f[0][1]);started["record"].update(kind="menu_start",event_id="e"*32,
            matching_deviation_id=None,slot_start_mono_ms=1000.,mono_ms=800.,expected_mono_ms=1000.)
        f[0].insert(1,started)
        with self.assertRaisesRegex(EvidenceError,"MOCK_MENU_RETIRED_CONTENT"):self.verify_menu(f)


class ManifestEmitter(unittest.TestCase):
    def test_closed_inventory_never_sets_complete_true(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def save(name,obj):
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);raw=compact(obj);path.write_bytes(raw);return sha(raw)
            sp=save("schedule.json",dict(study="A",visit="D0"));pp=save("package/manifest.json",dict(format="test-only"))
            config=dict(files=dict(schedule=dict(path="schedule.json",sha256=sp),package_manifest=dict(path="package/manifest.json",sha256=pp)),pins=dict(package_sha256=H))
            cp=save("config.json",config)
            fp=save("fixture-source.json",dict(scope="SIMULATION_TEST",test_only=True))
            cap=dict(scope="SIMULATION_TEST",participant_admission=False,acoustic_qualification=False,schedule_sha256=sp,package_sha256=H,fixture_set_sha256=fp)
            ap=save("cap.json",cap);bp=save("build.json",dict(source_commit="a"*40))
            rp=save("process.json",dict(version=1,process_id=123,process_exit=0,source_commit="a"*40,build_manifest_sha256=bp,started_utc="2026-10-05T00:00:00Z",ended_utc="2026-10-05T00:01:00Z"))
            args=dict(run_id="synthetic-test",role="reference",config="config.json",config_sha256=cp,capability="cap.json",capability_sha256=ap,
                      build_manifest="build.json",build_manifest_sha256=bp,process_result="process.json",process_result_sha256=rp,
                      fixture_provenance=root/"fixture-source.json",fixture_provenance_sha256=fp)
            with self.assertRaisesRegex(EvidenceError,"MOCK_CLOSED_EXPORT_REQUIRED"):emit(root,**args)
            self.assertFalse((root/"fixture-provenance.local.json").exists())
            save("export/manifest.json",dict(schema_version="data-export-provisional-1"))
            wrong=save("wrong-fixture.json",dict(scope="SIMULATION_TEST",wrong=True))
            with self.assertRaisesRegex(EvidenceError,"MOCK_FIXTURE_PROVENANCE_BINDING"):
                emit(root,**dict(args,fixture_provenance=root/"wrong-fixture.json",fixture_provenance_sha256=wrong))
            destination=root/"fixture-provenance.local.json";destination.write_bytes(b'{}\n')
            with self.assertRaisesRegex(EvidenceError,"MOCK_FILE_HASH"):emit(root,**args)
            self.assertEqual(destination.read_bytes(),b'{}\n');destination.unlink()
            pin=emit(root,**args);m=strict((root/"mock-run.manifest.json").read_bytes())
            self.assertFalse(m["complete"]);self.assertEqual(pin,sha((root/"mock-run.manifest.json").read_bytes()))
            self.assertEqual(sha(destination.read_bytes()),fp)
            self.assertEqual(sum(a["kind"]=="fixture_provenance" for a in m["artifacts"]),1)
            with self.assertRaisesRegex(EvidenceError,"MOCK_MANIFEST_EXISTS"):emit(root,**args)


ACCESS_VIOLATION = 3221225477  # 0xC0000005, as independently observed in #150.
NONCE = "5" * 32
EPOCH = "c" * 32
SOURCE = "b" * 40


class EndToEndProcessExit(unittest.TestCase):
    """Synthetic closed run on disk through top-level ``reconcile()`` (#150).

    Mirrors only the *shape* of an interrupted native attempt: a synthetic DEMO
    schedule, an intact closed export, and a post-cleanup receipt reporting
    successful cleanup/export. No captured evidence or private bytes are used.
    """

    def setUp(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def save(self, name, raw):
        path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
        return dict(path=name, sha256=sha(raw))

    def build_run(self, *, process_exit=0, manifest_exit=None, receipt=True, terminal=None):
        """Write a closed run and return ``(manifest_path, manifest_sha256)``."""
        from tools.mock_visit.reconcile import TRIAL_COLUMNS, EXPOSURE_COLUMNS
        repo = Path(__file__).resolve().parents[1]
        schedule = self.save("schedule.json", (repo/"schedules/examples/demo/A/A-C01/schedules/A-C01-L01/D0.json").read_bytes())
        protocol, build_id = "simulation-test-v1", "simulation-test"
        identity = dict(session_id="SIMULATION_TEST", coded_id="A-C01-L01", visit_id="D0", station_id="SIM",
                        protocol_version=protocol, build_sha256="d" * 64)
        config = self.save("config.json", compact(dict(
            identity=dict({k: identity[k] for k in ("session_id", "coded_id", "visit_id", "station_id")}, build_id=build_id),
            protocol_version=protocol, directories=dict(evidence="evidence"),
            files=dict(schedule=schedule), pins=dict(package_sha256=H))))
        capability = self.save("capability.json", compact(dict(
            version=1, scope="SIMULATION_TEST", build_id=build_id, protocol_version=protocol,
            output_directory=str(self.root / "evidence"), schedule_sha256=schedule["sha256"], package_sha256=H,
            fixture_set_sha256=H, audio_gain=.05, acoustic_qualification=False, participant_admission=False)))
        build = self.save("build.json", compact(dict(
            build_identity=dict(schema_version=1, build_id=build_id, commit_sha=SOURCE, protocol_version=protocol,
                                editor_version="6000.6.0f1", target="StandaloneWindows64", dirty_source=False,
                                station_schema_sha256=H, development_only=True),
            result="Succeeded", errors=0, duration_seconds=8.4, total_bytes=10, development_build=False,
            files=[dict(path="app.exe", bytes=10, sha256=H)])))
        if receipt:
            self.save("process.json", compact(dict(
                version=1, process_id=4242, process_exit=process_exit, source_commit=SOURCE,
                build_manifest_sha256=build["sha256"], started_utc="2026-10-05T00:00:00Z", ended_utc="2026-10-05T00:01:00Z")))
        # Intact closed export: one hash-chained visit exit, empty study CSVs.
        events = chain([dict(schema_version="data-events-provisional-1", event_id="e" * 32, clock_epoch=EPOCH,
                             host_mono_ms=1000., identity=identity, event_type="visit_exit", opportunity_id=None,
                             attempt_id=None, audio_request_id=None, payload=dict(code="JOIN_FOCUS_LOST"))])
        exported = {
            "raw/events-0000.local.jsonl": events,
            "trial-log.csv": (",".join(TRIAL_COLUMNS) + "\n").encode(),
            "exposure-ledger.csv": (",".join(EXPOSURE_COLUMNS) + "\n").encode(),
            "header-contract.json": compact(dict(
                schema_version="data-header-contract-provisional-1", qualified=False, review_evidence_sha256=None,
                trial_template_sha256=None, exposure_template_sha256=None,
                trial_headers=TRIAL_COLUMNS, exposure_headers=EXPOSURE_COLUMNS)),
        }
        export_dir = f"evidence/export-{NONCE}"
        for name, raw in exported.items(): self.save(f"{export_dir}/{name}", raw)
        export = self.save(f"{export_dir}/manifest.json", compact(dict(
            schema_version="data-export-provisional-1", export_id="f" * 32, identity=identity,
            headers_qualified=False, unacknowledged_torn_tail=False, record_count=1,
            last_record_sha256=strict(events)["sha256"], trial_rows=0, exposure_rows=0,
            files=[dict(path=n, bytes=len(r), sha256=sha(r)) for n, r in exported.items()])))
        joined_dir = f"evidence/joined-{NONCE}"
        self.save(f"{joined_dir}/joined.local.jsonl", chain([dict(
            version=1, clock_epoch=EPOCH, host_mono_ms=1500., kind="module",
            payload=dict(kind="native_run_end", status="JOIN_FOCUS_LOST", complete=False,
                         scope="SIMULATION_TEST", participant_admission=False))], "joined"))
        # Post-cleanup receipt: cleanup and export both reported successful.
        result = dict(version=1, scope="SIMULATION_TEST", session_nonce=NONCE, process_id=4242, source_commit=SOURCE,
                      config_sha256=config["sha256"], simulation_capability_sha256=capability["sha256"],
                      status="JOIN_FOCUS_LOST", complete=False, cleanup_succeeded=True, export_succeeded=True,
                      export_manifest_sha256=export["sha256"], host_mono_ms=2000., participant_admission=False)
        result.update(terminal or {})
        self.save(f"{joined_dir}/native-result.local.json", compact(result))
        kinds = {"process.json": "process_result", f"{export_dir}/manifest.json": "export_manifest",
                 f"{joined_dir}/native-result.local.json": "native_result", f"{joined_dir}/joined.local.jsonl": "joined_journal"}
        artifacts = [dict(kind=kinds.get(name, "other"), path=name, sha256=sha(raw), bytes=len(raw))
                     for name, raw in sorted((f.relative_to(self.root).as_posix(), f.read_bytes())
                                             for f in self.root.rglob("*") if f.is_file())]
        manifest = dict(version=1, scope="SIMULATION_TEST", run_id="synthetic-process-exit", study="A", visit="D0",
                        role="reference", source_commit=SOURCE, build_manifest=build, config=config,
                        simulation_capability=capability, schedule=schedule, package_sha256=H, fixture_set_sha256=H,
                        artifacts=artifacts, complete=False,
                        process_exit=process_exit if manifest_exit is None else manifest_exit)
        raw = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
        path = self.root / "mock-run.manifest.json"; path.write_bytes(raw)
        return path, sha(raw)

    def reconcile(self, **kwargs):
        from tools.mock_visit.reconcile import reconcile
        return reconcile(*self.build_run(**kwargs))

    def test_clean_exit_with_intact_export_has_no_process_failure(self):
        report = self.reconcile(process_exit=0)
        self.assertTrue(report["integrity_verified"])
        self.assertEqual(report["process_exit"], 0)
        self.assertNotIn("NATIVE_PROCESS_NOT_SUCCESSFUL", report["incomplete_reasons"])
        # A clean exit is not an acceptance certificate for an interrupted visit.
        self.assertFalse(report["software_reconciliation_complete"])
        self.assertIn("NATIVE_POST_CLEANUP_RESULT_INCOMPLETE", report["incomplete_reasons"])

    def test_nonzero_exit_is_retained_separately_from_verified_export(self):
        clean = self.reconcile(process_exit=0)
        self.setUp()
        failed = self.reconcile(process_exit=ACCESS_VIOLATION)
        self.assertTrue(failed["integrity_verified"])
        self.assertEqual(failed["process_exit"], ACCESS_VIOLATION)
        self.assertIn("NATIVE_PROCESS_NOT_SUCCESSFUL", failed["incomplete_reasons"])
        self.assertFalse(failed["software_reconciliation_complete"])
        # The only difference the exit makes is the separate process failure.
        self.assertEqual(set(failed["incomplete_reasons"]) - set(clean["incomplete_reasons"]), {"NATIVE_PROCESS_NOT_SUCCESSFUL"})
        self.assertEqual(set(clean["incomplete_reasons"]) - set(failed["incomplete_reasons"]), set())
        for key in ("requested_plays", "completed_opportunities", "scheduled_opportunities", "process_interval"):
            self.assertEqual(failed[key], clean[key], key)

    def test_successful_cleanup_export_receipt_cannot_override_nonzero_exit(self):
        terminal = dict(status="JOIN_COMPLETE_FORMS_RECORDED", complete=True)
        report = self.reconcile(process_exit=ACCESS_VIOLATION, terminal=terminal)
        self.assertTrue(report["integrity_verified"]); self.assertTrue(report["native_post_cleanup_complete"])
        self.assertNotIn("NATIVE_POST_CLEANUP_RESULT_INCOMPLETE", report["incomplete_reasons"])
        self.assertIn("NATIVE_PROCESS_NOT_SUCCESSFUL", report["incomplete_reasons"])
        self.assertFalse(report["software_reconciliation_complete"])

    def test_manifest_and_process_receipt_exit_must_agree(self):
        for receipt_exit, manifest_exit in ((ACCESS_VIOLATION, 0), (0, ACCESS_VIOLATION), (ACCESS_VIOLATION, 1)):
            self.setUp()
            with self.subTest(receipt=receipt_exit, manifest=manifest_exit), \
                    self.assertRaisesRegex(EvidenceError, "MOCK_PROCESS_BINDING"):
                self.reconcile(process_exit=receipt_exit, manifest_exit=manifest_exit)

    def test_missing_process_receipt_is_refused(self):
        with self.assertRaisesRegex(EvidenceError, "MOCK_PROCESS_RESULT_REQUIRED"):
            self.reconcile(process_exit=ACCESS_VIOLATION, receipt=False)

    def test_cli_exit_codes_keep_refusal_and_process_failure_distinct(self):
        import contextlib, io
        from unittest.mock import patch
        from tools.mock_visit import reconcile as module
        def run(path, pin, out):
            argv = ["reconcile", "--manifest", str(path), "--sha256", pin, "--out", str(out)]
            with patch("sys.argv", argv), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                return module.main(), err.getvalue()
        path, pin = self.build_run(process_exit=ACCESS_VIOLATION)
        code, _ = run(path, pin, self.root / "report.json")
        self.assertEqual(code, 3)
        report = strict((self.root / "report.json").read_bytes())
        self.assertTrue(report["integrity_verified"]); self.assertIn("NATIVE_PROCESS_NOT_SUCCESSFUL", report["incomplete_reasons"])
        self.setUp()
        path, pin = self.build_run(process_exit=ACCESS_VIOLATION, receipt=False)
        code, err = run(path, pin, self.root / "report.json")
        self.assertEqual((code, err.strip()), (2, "MOCK_PROCESS_RESULT_REQUIRED"))
        self.assertFalse((self.root / "report.json").exists())


if __name__=="__main__": unittest.main()
