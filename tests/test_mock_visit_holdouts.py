"""Full schedule-shaped synthetic journals; never captured visit evidence."""
import copy
import csv
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest

from tools.mock_visit import content, holdouts as h
from tools.mock_visit.records import EvidenceError, sha, verify_chain
from tools.mock_visit.reconcile import read, relative, schedule_items

ROOT = Path(__file__).resolve().parents[1]
HASH = "a"*64


def compact(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode()


def rechain(records):
    previous = "0"*64; lines = []
    for i, row in enumerate(records):
        row = dict(row, sequence=i, previous_sha256=previous); row.pop("sha256", None)
        row["sha256"] = previous = sha(compact(row)+b"\n")
        lines.append(compact(row)+b"\n")
    return verify_chain([("events-0000.local.jsonl", b"".join(lines))], "data")


def history(study, package_root=None):
    unit, person = study+"-C01", study+"-C01-"+("L01" if study == "A" else "M1")
    directory = ROOT/"schedules/examples/demo"/study/unit
    if package_root is None:
        with (directory/"curriculum.csv").open(newline="", encoding="utf-8") as stream:
            messages = {r["message_id"]: dict(message_id=r["message_id"], status="trained" if r["training_wave"] else "heldout",
                        composite_sha256=sha((study+r["message_id"]).encode())) for r in csv.DictReader(stream)}
        known = {m["composite_sha256"] for m in messages.values()} | {HASH}
    else:
        manifest = package_root/"manifest.json"; raw = read(manifest); value = json.loads(raw)
        _, atoms, messages = content.package(package_root, dict(path="manifest.json", sha256=sha(raw)), value["package_sha256"], read, relative)
        known = {a["pcm_sha256"] for a in atoms.values()} | {HASH}
        for message in messages.values(): known.update(c["composite_sha256"] for c in message.get("combinations", [message]))
        # These are the exact producer schedules sealed inside the package.
        schedule_paths = {visit:package_root/next(n for n in value["files"] if n.endswith("/"+visit+".json") and "schedules/" in n) for visit in h.VISITS[study]}
    result = []
    for vi, visit in enumerate(h.VISITS[study]):
        path = directory/"schedules"/person/(visit+".json") if package_root is None else schedule_paths[visit]
        raw = read(path); schedule = json.loads(raw); items = schedule_items(schedule, study, visit)
        identity = dict(session_id=f"session-{study}-{vi}", coded_id=schedule["person_id"], visit_id=visit,
                        station_id="synthetic-station", protocol_version="SIMULATION_TEST", build_sha256=HASH)
        epoch = sha((study+visit+"clock").encode())[:32]; records = []
        def add(kind, payload, item=None, request=None):
            i = len(records)
            records.append(dict(schema_version="data-events-provisional-1", sequence=i,
                event_id=sha((study+visit+str(i)).encode())[:32], clock_epoch=epoch, host_mono_ms=float(i), identity=identity,
                event_type=kind, opportunity_id=item, attempt_id=item, audio_request_id=request,
                previous_sha256="0"*64, payload=copy.deepcopy(payload), sha256=HASH))
        for item in items.values():
            key = item["trial_id"]; ids = [sha((study+visit+key+str(i)).encode())[:32] for i in range(item["plays"])]
            p = dict(event="state_after", clock_epoch=epoch, schedule_sha256=sha(raw), trial_id=key, retry_of=None,
                block_index=item["block_index"], item_index=item["item_index"], host_mono_ms=float(len(records)),
                scheduled_onset_mono_ms=float(len(records)+1000), state="CueRequested", audible_status="Uncertain" if ids else "NoCue",
                exposure_consumed=bool(ids), reset_ok=True, focus_ok=True, technical_fault_code=None,
                response_code=None, evidence_sha256=None, opportunity_id=key, audio_request_ids=ids)
            add("session", p, key)
            if item["block"] == "novel":
                p["event"] = "novel_buffer_authorized"; add("session", p, key)
            message = messages.get(item.get("message_id"))
            digest = HASH if message is None else message.get("combinations", [message])[0]["composite_sha256"]
            for request in ids:
                a = dict(code="AUDIO_REQUESTED", audio_id=request, waveform_sha256=None, pcm_sha256=digest,
                    action_pcm_sha256=None, referent_pcm_sha256=None, observed_mono_ms=float(len(records)),
                    request_mono_ms=float(len(records)), scheduled_mono_ms=float(len(records)+1000), scheduled_dsp_s=1.,
                    onset_estimate_mono_ms=None, onset_uncertainty_ms=None, first_callback_dsp_s=None,
                    delivered_samples=0, callback_count=0, simulation_test=True,
                    software_output_estimate_mono_ms=float(len(records)+1000), software_output_uncertainty_ms=2.)
                add("audio_request", a, key, request)
                a.update(code="SIMULATION_DELIVERY_OBSERVED", delivered_samples=480, callback_count=1, first_callback_dsp_s=1.)
                add("audio_observation", a, key, request)
                a.update(code="AUDIO_PLAYBACK_COMPLETED", delivered_samples=48000, callback_count=100)
                add("audio_observation", a, key, request)
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)+timedelta(days=vi)
        result.append(h.VisitEvidence(visit, identity, sha(raw), items, rechain(records), messages,
            frozenset(known), True, start.isoformat(), (start+timedelta(hours=2)).isoformat()))
    return result


def grammar_row(visit, request, event_id, phase="ready"):
    row=copy.deepcopy(next(r for r in visit.records if r["event_type"]=="audio_request"))
    audio=row["payload"];audio.update(audio_id=request,pcm_sha256=HASH)
    row.update(event_type="grammar_stage",event_id=event_id,opportunity_id=None,attempt_id=None,audio_request_id=None)
    row["payload"]=dict(version=1,schedule_sha256=visit.schedule_sha256,registry_sha256=HASH,review_sha256=HASH,
        clock_epoch=visit.records[0]["clock_epoch"],host_mono_ms=0.,kind="audio",phase=phase,
        audio_request_id=request,operator_command=None,audio=audio)
    return row


class HoldoutHistory(unittest.TestCase):
    def test_all_actual_public_producer_schedule_items_are_scanned(self):
        for study, expected, novel in (("A",276,8),("B",500,14)):
            with self.subTest(study=study):
                report=h.scan_visits(study,history(study))
                self.assertTrue(report["software_history_complete"])
                self.assertEqual(report["audio_requests"],expected)
                self.assertEqual(report["authorized_heldout_requests"],novel)
                self.assertFalse(report["participant_qualified"])
                self.assertFalse(report["omitted_run_custody_verified"])

    def test_rehashed_early_complete_phrase_in_atom_or_trained_context_refuses(self):
        for block in ("atomic_lessons","message_lessons","trained"):
            visits=history("A");v=visits[0];heldout=next(iter(h.phrase_index(v.messages)))
            request=next(r for r in v.records if r["event_type"]=="audio_request" and v.items[r["opportunity_id"]]["block"]==block)
            request["payload"]["pcm_sha256"]=heldout
            visits[0]=replace(v,records=rechain(v.records))
            with self.subTest(block=block), self.assertRaisesRegex(EvidenceError,"EARLY_OR_WRONG"):
                h.scan_visits("A",visits)

    def test_even_unselected_b_profile_rank_heldout_is_recognized(self):
        visits=history("B");v=visits[0];mid=next(k for k,m in v.messages.items() if m["status"]=="heldout")
        # Add the other 47 combinations to this synthetic registry, then inject
        # a never-selected one into a profile example request.
        v.messages[mid]["combinations"]=[dict(composite_sha256=sha((mid+str(i)).encode())) for i in range(48)]
        digest=v.messages[mid]["combinations"][-1]["composite_sha256"]
        visits[0]=replace(v,known_pcm=v.known_pcm | {digest})
        next(r for r in v.records if r["event_type"]=="audio_request")["payload"]["pcm_sha256"]=digest
        with self.assertRaisesRegex(EvidenceError,"EARLY_OR_WRONG"):h.scan_visits("B",visits)

    def test_unknown_pcm_never_counts_as_clear(self):
        for kind in ("audio_request","audio_observation"):
            visits=history("A");next(r for r in visits[0].records if r["event_type"]==kind)["payload"]["pcm_sha256"]="f"*64
            with self.subTest(kind=kind),self.assertRaisesRegex(EvidenceError,"UNINDEXED_AUDIO"):h.scan_visits("A",visits)

    def test_permit_removed_reordered_duplicated_or_rebound_refuses(self):
        for change in ("remove","early","duplicate","request","opportunity","epoch"):
            visits=history("A");rows=visits[0].records;i=next(i for i,r in enumerate(rows) if r["payload"].get("event")=="novel_buffer_authorized")
            if change=="remove":rows.pop(i)
            elif change=="early":rows.insert(i-1,rows.pop(i))
            elif change=="duplicate":
                duplicate=copy.deepcopy(rows[i]);duplicate["event_id"]="f"*32;rows.insert(i+1,duplicate)
            elif change=="request":rows[i]["payload"]["audio_request_ids"]=["f"*32]
            elif change=="opportunity":rows[i]["payload"]["opportunity_id"]="different"
            else:rows[i]["payload"]["clock_epoch"]="f"*32
            with self.subTest(change=change),self.assertRaises(EvidenceError):h.scan_visits("A",visits)

    def test_consumed_message_cannot_reappear_under_new_visit_permit(self):
        visits=history("A");v0,v1=visits
        first=next(r for r in v0.records if r["event_type"]=="audio_request" and v0.items[r["opportunity_id"]]["block"]=="novel")
        second=next(r for r in v1.records if r["event_type"]=="audio_request" and v1.items[r["opportunity_id"]]["block"]=="novel")
        v1.items[second["opportunity_id"]]["message_id"]=v0.items[first["opportunity_id"]]["message_id"]
        second["payload"]["pcm_sha256"]=first["payload"]["pcm_sha256"]
        with self.assertRaisesRegex(EvidenceError,"CONSUMED_PHRASE_REPLAY"):h.scan_visits("A",visits)

    def test_cross_session_event_request_epoch_or_identity_grafts_refuse(self):
        for change in ("session","event","request","epoch","engine_epoch","person","observation"):
            visits=history("A");a,b=visits
            if change=="session":b.identity["session_id"]=a.identity["session_id"]
            elif change=="event":b.records[0]["event_id"]=a.records[0]["event_id"]
            elif change=="request":
                next(r for r in b.records if r["event_type"]=="audio_request")["audio_request_id"]=next(r for r in a.records if r["event_type"]=="audio_request")["audio_request_id"]
            elif change=="epoch":b.records[0]["clock_epoch"]=a.records[0]["clock_epoch"]
            elif change=="engine_epoch":b.records[0]["payload"]["clock_epoch"]=a.records[0]["payload"]["clock_epoch"]
            elif change=="person":b.identity["coded_id"]="another-person"
            else:next(r for r in b.records if r["event_type"]=="audio_observation")["attempt_id"]="another-attempt"
            with self.subTest(change=change),self.assertRaises(EvidenceError):h.scan_visits("A",visits)

    def test_grammar_cannot_hide_phrase_or_unknown_pcm(self):
        for digest in ("f"*64, "heldout"):
            visits=history("A");v=visits[0]
            row=copy.deepcopy(next(r for r in v.records if r["event_type"]=="audio_request"))
            audio=row["payload"];audio["pcm_sha256"]=next(iter(h.phrase_index(v.messages))) if digest=="heldout" else digest
            row.update(event_type="grammar_stage",event_id="f"*32,opportunity_id=None,attempt_id=None,audio_request_id=None)
            row["payload"]=dict(version=1,schedule_sha256=v.schedule_sha256,registry_sha256=HASH,review_sha256=HASH,
                clock_epoch=v.records[0]["clock_epoch"],host_mono_ms=0.,kind="audio",phase="ready",
                audio_request_id=audio["audio_id"],operator_command=None,audio=audio)
            v.records.insert(0,row)
            with self.subTest(digest=digest),self.assertRaisesRegex(EvidenceError,"GRAMMAR_CONTAMINATION|UNINDEXED_AUDIO"):
                h.scan_visits("A",visits)

    def test_recovery_is_never_absence_proof_even_with_declared_complete(self):
        visits=history("A");v=visits[0];row=copy.deepcopy(v.records[0])
        row.update(event_id="f"*32,event_type="recovery",opportunity_id=None,attempt_id=None,audio_request_id=None,payload=dict(preserved_tails=[]))
        v.records.insert(0,row)
        self.assertIn("RECOVERED_OR_MISSING_PROCESS_HISTORY",h.scan_visits("A",visits)["incomplete_reasons"])

    def test_ambiguous_phrase_pcm_refuses_label_based_disambiguation(self):
        messages=history("A")[0].messages
        a,b=[m for m in messages.values() if m["status"]=="heldout"][:2]
        b["composite_sha256"]=a["composite_sha256"]
        with self.assertRaisesRegex(EvidenceError,"AMBIGUOUS_PCM"):h.phrase_index(messages)

    def test_null_unused_menu_directory_does_not_invent_auxiliary_audio(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);raw=compact(dict(entries=[]));(root/"registry.json").write_bytes(raw)
            config=dict(files=dict(reserved_registry=dict(path="registry.json",sha256=sha(raw))),directories=dict(menu_examples=None))
            self.assertEqual(h.known_audio(root,config,[],{},{}),frozenset())

    def test_grammar_observations_repeat_only_in_same_immutable_context(self):
        visits=history("A");v=visits[0]
        for i in range(3):v.records.insert(i,grammar_row(v,"f"*32,f"{9000+i:032x}"))
        self.assertEqual(h.scan_visits("A",visits)["grammar_audio_events"],3)
        v.records[2]["payload"]["phase"]="clicks"
        with self.assertRaisesRegex(EvidenceError,"GRAMMAR_REQUEST_GRAFT"):h.scan_visits("A",visits)

    def test_cross_visit_grammar_request_and_both_channel_collision_orders_refuse(self):
        for change in ("cross_visit","grammar_first","study_first"):
            visits=history("A");a,b=visits
            if change=="cross_visit":
                a.records.insert(0,grammar_row(a,"f"*32,"e"*32))
                b.records.insert(0,grammar_row(b,"f"*32,"d"*32))
            else:
                request=next(r["audio_request_id"] for r in a.records if r["event_type"]=="audio_request")
                row=grammar_row(a,request,"e"*32)
                a.records.insert(0,row) if change=="grammar_first" else a.records.append(row)
            with self.subTest(change=change),self.assertRaisesRegex(EvidenceError,"REQUEST_GRAFT|REQUEST_REUSE"):
                h.scan_visits("A",visits)

    def test_empty_history_is_not_a_clear_scan(self):
        report=h.scan_visits("A",[])
        self.assertFalse(report["recorded_history_scan_passed"])
        self.assertIn("NO_RETAINED_AUDIO_RECORDS",report["incomplete_reasons"])

    def test_incomplete_prefix_has_no_complete_history_claim(self):
        visits=history("A")
        report=h.scan_visits("A",[replace(visits[0],software_complete=False)])
        self.assertFalse(report["software_history_complete"])
        self.assertIn("NATIVE_RUN_INCOMPLETE",report["incomplete_reasons"])
        self.assertEqual(report["missing_complete_visits"],["D0","D7"])

    def test_missing_request_after_valid_permit_remains_incomplete(self):
        visits=history("A");v=visits[-1]
        request=next(r for r in v.records if r["event_type"]=="audio_request" and v.items[r["opportunity_id"]]["block"]=="novel")
        visits[-1]=replace(v,records=[r for r in v.records if r["audio_request_id"]!=request["audio_request_id"]])
        self.assertIn("PERMIT_WITHOUT_RECORDED_REQUEST",h.scan_visits("A",visits)["incomplete_reasons"])

    def test_order_and_overlapping_processes_refuse(self):
        for change in ("reverse","overlap"):
            visits=history("A")
            if change=="reverse":visits.reverse()
            else:visits[1]=replace(visits[1],process_started_utc=visits[0].process_started_utc)
            with self.subTest(change=change),self.assertRaises(EvidenceError):h.scan_visits("A",visits)

    def test_missing_run_is_incomplete_and_changed_plan_pin_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"history.json";plan=dict(version=1,scope="SIMULATION_TEST",study="A",role="reference",
                coded_id="A-C01-L01",unit_id="A-C01",package_sha256=HASH,runs=[dict(path="missing.json",sha256=HASH)])
            raw=compact(plan);path.write_bytes(raw)
            self.assertIn("PINNED_RUN_MISSING",h.verify_history(path,sha(raw))["incomplete_reasons"])
            with self.assertRaisesRegex(EvidenceError,"FILE_HASH"):h.verify_history(path,"f"*64)

    @unittest.skipUnless(os.environ.get("AV_HOLDOUT_PACKAGE_FIXTURES"),"Optional existing sealed producer packages")
    def test_existing_sealed_producer_pcm_and_schedule_histories(self):
        root=Path(os.environ["AV_HOLDOUT_PACKAGE_FIXTURES"])
        for study,name in (("A","package-demo"),("B","dyad-demo")):
            with self.subTest(study=study):
                report=h.scan_visits(study,history(study,root/name))
                self.assertTrue(report["software_history_complete"])
                self.assertFalse(report["acoustic_qualified"])


if __name__=="__main__":unittest.main()
