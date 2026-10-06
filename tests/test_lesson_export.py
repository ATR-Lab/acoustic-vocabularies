"""Synthetic software records only; no participant/display/acoustic authority."""
import copy
import csv
import io
import json
import re
import unittest
from pathlib import Path
from jsonschema import Draft202012Validator
from tools.mock_visit import lessons
from tools.mock_visit.records import EvidenceError, validate_data_payload

H, G = "a"*64, "a"*32
IDS = [str(i)*32 for i in (1, 2, 3)]
IDENTITY = dict(session_id=G, coded_id="DEMO", visit_id="D0", station_id="SIM")


def fixture():
    rows, exposures = [], []
    def add(kind, payload):
        row = dict(event_type=kind, sequence=len(rows), clock_epoch=G, host_mono_ms=payload.get("observed_mono_ms", 0),
                   sha256=f"{len(rows):064x}", opportunity_id="DEMO-lesson", attempt_id="DEMO-lesson",
                   audio_request_id=payload.get("audio_request_id"), payload=copy.deepcopy(payload))
        rows.append(row); return row
    add("session", dict(event="state_after",state="Loaded",schedule_sha256=H,clock_epoch=G,opportunity_id="DEMO-lesson",audio_request_ids=IDS,scheduled_onset_mono_ms=750,host_mono_ms=0))
    def event(kind, now, index=None, expected=None, feedback=None):
        return add("lesson", dict(schema_version="lesson-events-provisional-1", schedule_sha256=H, package_sha256=H,
            session_clock_epoch=G, lesson_type="atomic_lesson",slot_start_mono_ms=750,audio_request_ids=IDS,kind=kind,
            attempt_id="DEMO-lesson",opportunity_id="DEMO-lesson",audio_request_id=IDS[index-1] if index else None,presentation_index=index,
            observed_mono_ms=now,expected_mono_ms=expected,meaning_display_id="DEMO-meaning",feedback_content_id=feedback,
            highlight=None,pcm_sha256=H,action_pcm_sha256=None,referent_pcm_sha256=None))
    for i, onset in enumerate((750, 6750, 14750), 1):
        event("play_request", onset-750, i, onset)
        row=add("audio_request", dict(pcm_sha256=H,action_pcm_sha256=None,referent_pcm_sha256=None)); row["audio_request_id"]=IDS[i-1]
        exposures.append(dict(audio_request_id=IDS[i-1],audible_status="uncertain",exposure_consumed="true",audio_onset_estimate_mono_ms="",onset_uncertainty_ms=""))
        if i == 1: event("display_start", onset, expected=onset)
        if i == 2:
            event("display_end", onset, expected=onset);event("retrieval_opportunity",onset,expected=onset)
        if i == 3: event("display_start",onset,expected=onset,feedback="DEMO-feedback")
        event("onset_authority",onset,i,onset);event("play_complete",onset+500,i)
        if i == 2: event("retrieval_result",onset+600,feedback="DEMO-feedback")
    event("display_end",20750,expected=20750,feedback="DEMO-feedback");event("lesson_end",20750,expected=20750)
    return rows, exposures


def files(rows, exposures):
    output=io.StringIO();writer=csv.DictWriter(output,fieldnames=lessons.COLUMNS,lineterminator="\n");writer.writeheader();writer.writerows(lessons.derive(rows,IDENTITY,exposures))
    return {lessons.TABLE:output.getvalue().encode(),lessons.CONTRACT:json.dumps(dict(schema_version=lessons.VERSION,qualified=False,headers=lessons.COLUMNS,
        exposure_ledger_relation="play_rows_join_by_audio_request_id_no_additional_exposures",evidence_level="software_event_calls_not_physical_presentation",acoustic_authority=False)).encode()}


class LessonExportTests(unittest.TestCase):
    def test_six_software_rows_link_three_uncertain_audio_rows(self):
        rows, audio=fixture(); derived=lessons.derive(rows,IDENTITY,audio)
        self.assertEqual([r["row_kind"] for r in derived].count("play"),3)
        self.assertEqual([r["row_kind"] for r in derived].count("display"),2)
        self.assertEqual([r["row_kind"] for r in derived].count("retrieval"),1)
        self.assertTrue(all(r["interval_status"]=="software_observed" for r in derived))
        for row in derived:
            if row["row_kind"] == "play":
                self.assertEqual(row["audible_status"],"uncertain");self.assertEqual(row["exposure_consumed"],"true")
            else: self.assertEqual(row["audible_status"],"")
            self.assertEqual(row["audio_onset_estimate_mono_ms"],"")
        lessons.verify_export(files(rows,audio),rows,IDENTITY,audio)

    def test_missing_end_and_audio_request_remain_incomplete(self):
        rows,audio=fixture();rows=rows[:4];rows=[r for r in rows if r["event_type"]!="audio_request"]
        result=lessons.derive(rows,IDENTITY,[])
        self.assertEqual(result[0]["audio_ledger_status"],"request_missing")
        self.assertEqual(result[1]["display_end_mono_ms"],"")
        self.assertTrue(all(r["interval_status"]=="incomplete" for r in result))

    def test_old_export_compatibility_and_pair_required_only_when_typed(self):
        lessons.verify_export({},[],IDENTITY,[])
        rows,audio=fixture()
        for value in ({},{lessons.TABLE:b""},{lessons.CONTRACT:b""}):
            with self.assertRaisesRegex(EvidenceError,"EXPORT_PAIR"): lessons.verify_export(value,rows,IDENTITY,audio)

    def test_modified_or_extra_derived_rows_rejected(self):
        rows,audio=fixture(); f=files(rows,audio)
        for changed in (f[lessons.TABLE].replace(b"uncertain",b"confirmed_audible",1),f[lessons.TABLE]+f[lessons.TABLE].splitlines(keepends=True)[1]):
            with self.assertRaises(EvidenceError): lessons.verify_export(dict(f,**{lessons.TABLE:changed}),rows,IDENTITY,audio)

    def test_wrong_loaded_identity_or_audio_hash_rejected(self):
        for field,value in (("schedule_sha256","b"*64),("session_clock_epoch","b"*32),("slot_start_mono_ms",1),("opportunity_id","other")):
            rows,audio=fixture();rows[1]["payload"][field]=value
            with self.subTest(field=field),self.assertRaises(EvidenceError): lessons.derive(rows,IDENTITY,audio)
        rows,audio=fixture();rows[2]["payload"]["pcm_sha256"]="b"*64
        with self.assertRaisesRegex(EvidenceError,"AUDIO_HASH"): lessons.derive(rows,IDENTITY,audio)

    def test_duplicate_play_or_display_end_rejected(self):
        for event in ("play_request","display_end"):
            rows,audio=fixture(); at=next(i for i,r in enumerate(rows) if r["event_type"]=="lesson" and r["payload"]["kind"]==event)
            rows.insert(at+1,copy.deepcopy(rows[at]))
            with self.subTest(event=event),self.assertRaises(EvidenceError): lessons.derive(rows,IDENTITY,audio)

    def test_new_epoch_cannot_close_old_interval(self):
        rows,audio=fixture();rows[-2]["clock_epoch"]="b"*32
        with self.assertRaisesRegex(EvidenceError,"CLOCK"): lessons.derive(rows,IDENTITY,audio)

    def test_actual_schedule_and_side_journal_are_bound(self):
        rows,audio=fixture();joined=[dict(kind="lesson",payload={k:r["payload"][k] for k in lessons.EVENT_FIELDS}) for r in rows if r["event_type"]=="lesson"]
        items={"DEMO-lesson":dict(block="atomic_lessons",trial_type="atomic_lesson")}
        self.assertEqual(lessons.reconcile(rows,joined,items,H,H),set())
        self.assertIn("LESSON_SUPPLEMENTAL_WRITE_INCOMPLETE",lessons.reconcile(rows,joined[:-1],items,H,H))
        with self.assertRaisesRegex(EvidenceError,"SCHEDULE"):lessons.reconcile(rows,joined,{"DEMO-lesson":dict(block="trained",trial_type="atomic_lesson")},H,H)
        joined[0]["payload"]["meaning_display_id"]="other"
        with self.assertRaisesRegex(EvidenceError,"SUPPLEMENTAL"):lessons.reconcile(rows,joined,items,H,H)

    def test_closed_payload_json_schema_and_runtime_header_oracle(self):
        root=Path(__file__).resolve().parents[1];schema=json.loads((root/'apparatus/data/data-event.schema.json').read_text());validator=Draft202012Validator(schema)
        base=json.loads((root/'docs/data/synthetic-visit/events.jsonl').read_text().splitlines()[0]);rows,_=fixture()
        for r in rows:
            if r["event_type"] != "lesson":continue
            full=dict(base,**r);validator.validate(full);validate_data_payload(r,H)
        bad=copy.deepcopy(rows[1]);bad["payload"]["private_answer"]="hidden"
        with self.assertRaises(EvidenceError):lessons.validate(bad)
        self.assertTrue(list(validator.iter_errors(dict(base,**bad))))
        text=(root/'unity/Assets/ExperimentApp/Runtime/DataLogging/LessonExport.cs').read_text()
        literal=re.search(r'Headers=Array.AsReadOnly\(new\[\]\{(.*?)\}\)',text).group(1)
        self.assertEqual(re.findall(r'"([a-z0-9_]+)"',literal),lessons.COLUMNS)
        self.assertEqual((root/'apparatus/data/lesson-exposures.provisional.csv').read_text().strip(),','.join(lessons.COLUMNS))


if __name__ == "__main__": unittest.main()
