"""Synthetic screening records plus the real allocation/schedule producers.

No receipt in these fixtures represents a human review, consent or native run.
"""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.mock_visit.screening import ACTIONS, TARGETS, verify_packet, orientation
from tools.mock_visit.records import EvidenceError, sha
from tools.mock_visit.suite import verify_suite
from av_schedules.admission import DurableRevealLog, _receipt
from av_schedules.admission_io import canonical
from av_schedules.assign_output import assign_files
from av_schedules.design import build_units
from av_schedules.orders import build_visit_schedule
from av_schedules.reveal import A_CHECKS, B_CHECKS, GENESIS
from av_schedules.seeds import demo_seed

H = sha(b"synthetic package identity; no sound package is admitted")


class Fixture:
    def __init__(self, root, study="A", second=False, person_prefix="SYNTHETIC-"):
        self.root = root
        self.study = study
        root.mkdir(parents=True)
        self.packet = dict(version=1, scope="SIMULATION_TEST", orientations=[], allocation=None, joins=[])
        self.plan = dict(version=1, protocol_version="SYNTHETIC-test", content_status="protocol_reviewed",
                         review_evidence_sha256=sha(b"synthetic authority shape only; not a review"), source_note="SYNTHETIC test only",
                         practice_window_ms=12000,
                         actions=[dict(id=x, title=x, meaning="SYNTHETIC") for x in ACTIONS],
                         targets=[dict(id=x, title=x, meaning="SYNTHETIC") for x in TARGETS],
                         practice_pairs=[dict(id=f"p{i}", target=t, action=a, request="SYNTHETIC") for i,(t,a) in enumerate(zip(TARGETS,ACTIONS))],
                         second_order=[f"p{i}" for i in reversed(range(8))])
        pp=self.put("plan.json", self.plan); di=self.put("index.json", {"synthetic_index": True})
        people=[f"{person_prefix}{i}" for i in range(1 if study=="A" else 2)]
        self.rows=[];self.receipts=[]
        for i,person in enumerate(people):
            receipt=dict(schema_version=1,receipt_type="orientation-outcome",screening_id=person,station_id="SYNTHETIC-station",
                protocol_version=self.plan["protocol_version"],orientation_id=f"{i+1:032x}",plan_sha256=pp["sha256"],demo_index_sha256=di["sha256"],
                outcome="pass_second" if second else "pass_first",engineering_draft=False,eligible=True)
            rows=self.orientation_rows(receipt,second)
            self.rows.append(rows)
            journal=self.put(f"orientation-{i}.jsonl", b"".join(canonical(r)+b"\n" for r in rows))
            receipt=_receipt(dict(receipt,journal_sha256=journal["sha256"],journal_bytes=len((root/journal["path"]).read_bytes())))
            self.receipts.append(receipt)
            self.packet["orientations"].append(dict(receipt=self.put(f"receipt-{i}.json",receipt),journal=journal,plan=pp,demo_index=di,handoff=None))
        seed=demo_seed("DEMO-screening-verifier")
        # Real producer list, deterministic durable facade and real visit schedule.
        name="pilot-slots.json" if study=="A" else "pilot-dyads.json"
        listing=self.put("allocation.json",assign_files(seed,study,"pilot")[name])
        log=DurableRevealLog(root/listing["path"],listing["sha256"],root/"allocation.jsonl",expected_head=GENESIS,
                             clock=lambda:"2027-01-01T00:00:00+00:00")
        proof_files=[(root/r["receipt"]["path"],r["receipt"]["sha256"],root/r["journal"]["path"]) for r in self.packet["orientations"]]
        e=log.log_eligibility(people,staff="SYNTHETIC",checks=dict.fromkeys(A_CHECKS if study=="A" else B_CHECKS,True),orientation_files=proof_files)
        r=log.reveal_next(e["eligibility_id"],eligibility_receipt_sha256=e["receipt_sha256"],staff="SYNTHETIC")
        self.packet["allocation"]=dict(list=listing,journal=self.pin("allocation.jsonl"),checkpoint=self.pin("allocation.jsonl.head.json"),
            retained_head_sha256=log.head,eligibility=self.put("eligibility.json",e),reveal=self.put("reveal.json",r))
        self.entry=r["entry"]
        unit=next(x for x in build_units(seed,study,"pilot") if x.unit_id==self.entry["unit_id"])
        key=self.entry["book_id"] if study=="A" else self.entry["unit_id"]
        package_map=self.put("packages.json",dict(format="av-schedules/package-hashes",format_version=1,study=study,set="pilot",demo=True,placeholder=False,packages={key:H}))
        sheet=self.put("run-sheet-manifest.json",dict(package_hashes=dict(sha256=package_map["sha256"])))
        self.expected={}
        for i,person in enumerate(people):
            member=self.entry if study=="A" else self.entry["members"][i]
            visit="D0" if study=="A" else "V1"
            schedule=self.put(f"schedule-{i}.json",build_visit_schedule(seed,unit,member["slot_id"],visit))
            config=dict(version=1,scope="DEMO_ENGINEERING",protocol_version=self.plan["protocol_version"],
                identity=dict(station_id="SYNTHETIC-station",unit_id=unit.unit_id,coded_id=member["slot_id"],session_id=f"{i+100:032x}",visit_id=visit,build_id="SYNTHETIC-build"),
                files=dict(schedule=schedule,run_sheet_manifest=sheet),directories={},pins=dict(package_sha256=H),control={})
            c=self.put(f"config-{i}.json",config)
            self.packet["joins"].append(dict(screening_id=person,config=c,package_hashes=package_map))
            self.expected[c["sha256"]]=(study,visit,"reference" if study=="A" else member["role"])
            handoff=dict(version=1,request_id=f"{i+200:032x}",orientation_receipt_sha256=self.receipts[i]["receipt_sha256"],
                         allocation_receipt_sha256=r["receipt_sha256"],joined_config_sha256=c["sha256"],participant_admission=False)
            self.packet["orientations"][i]["handoff"]=self.put(f"handoff-{i}.json",handoff)

    def pin(self,name): return dict(path=name,sha256=sha((self.root/name).read_bytes()))
    def put(self,name,value):
        (self.root/name).write_bytes(value if isinstance(value,bytes) else canonical(value))
        return self.pin(name)
    def get(self,pin): return json.loads((self.root/pin["path"]).read_bytes())
    def update(self,pin,fn,reseal=False):
        v=self.get(pin);fn(v)
        if reseal:v=_receipt({k:x for k,x in v.items() if k!="receipt_sha256"})
        pin.update(self.put(pin["path"],v));return v
    def verify(self,expected=True):
        p=self.put("packet.json",self.packet)
        return verify_packet(self.root/p["path"],p["sha256"],self.expected if expected else None)

    def orientation_rows(self,receipt,second):
        header=dict(event="orientation_header",schema="silent-orientation-v1",build_identity={"protocol_version":receipt["protocol_version"]},preallocation=True,study_audio_loaded=False,
                    **{k:receipt[k] for k in ("screening_id","station_id","protocol_version","orientation_id","plan_sha256","demo_index_sha256")})
        rows=[header,dict(event="orientation_assets_validated",mono_ms=0,source_kind="snapshot",demo_count=8,nominal_duration_seconds=10,
                         timing_tolerance="one recorded sample period; provisional",study_package_access=False)]
        now=1
        def add(event,attempt,**fields):
            nonlocal now
            row=dict(event=event,mono_ms=now,attempt=attempt,engineering_draft=False,**fields);rows.append(row);now+=1;return row
        add("orientation_started",1)
        checks=[]
        for attempt in range(1,3 if second else 2):
            if attempt==2:add("standard_reexplanation",2)
            for action in ACTIONS:
                add("action_screen",attempt,action=action,kinematic_visualization=True);now+=10000
                add("kinematic_demo_completed",attempt,action=action,observed_duration_ms=10000,nominal_duration_ms=10000,tolerance_ms=1000/30)
            for target in TARGETS:add("target_screen",attempt,target=target)
            pairs=self.plan["practice_pairs"] if attempt==1 else list(reversed(self.plan["practice_pairs"]))
            correct=[]
            for ordinal,item in enumerate(pairs,1):
                trial=f"orientation-{attempt}-{ordinal-1}"
                add("practice_open",attempt,item_id=item["id"],trial_id=trial,ordinal=ordinal)
                target=item["target"]
                if second and attempt==1 and ordinal==1:target="B"
                answer=target==item["target"];correct.append(answer)
                add("practice_response",attempt,item_id=item["id"],trial_id=trial,ordinal=ordinal,response_code="COMMIT",
                    response_target=target,response_action=item["action"],selected_target=target,selected_action=item["action"],response_mono_ms=now,correct=answer)
            add("check_completed",attempt,correct=sum(correct),total=8);checks.append(correct)
        add("eligibility_outcome",len(checks),outcome=receipt["outcome"],first_correct=checks[0],second_correct=checks[1] if second else [],reexplanations=int(second),preallocation=True,learning_result=False)
        return rows


class ScreeningTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)/".local"
    def tearDown(self): self.temp.cleanup()

    def test_real_producer_allocation_and_schedule_match_both_studies(self):
        for study in ("A","B"):
            with self.subTest(study=study):
                f=Fixture(self.root/study,study)
                before={p.relative_to(f.root).as_posix():p.read_bytes() for p in f.root.rglob('*') if p.is_file()}
                report=f.verify()
                self.assertTrue(report["software_chain_verified"])
                self.assertEqual(report["orientation_sequences_verified"],1 if study=="A" else 2)
                self.assertEqual({r["role"] for r in report["joined_bindings"]},{"reference"} if study=="A" else {"active","yoked"})
                self.assertFalse(report["participant_admission"] or report["physical_screening_verified"] or report["material_review_verified"] or report["issue81_accepted"])
                self.assertNotIn("SYNTHETIC-0",json.dumps(report))
                self.assertEqual(before,{p.relative_to(f.root).as_posix():p.read_bytes() for p in f.root.rglob('*') if p.is_file() and p.name!='packet.json'})

    def test_actual_two_attempt_flow_requires_the_single_reexplanation(self):
        f=Fixture(self.root,second=True)
        self.assertTrue(f.verify()["software_chain_verified"])
        rows=copy.deepcopy(f.rows[0]);rows=[r for r in rows if r["event"]!='standard_reexplanation']
        raw=b''.join(canonical(r)+b'\n' for r in rows)
        receipt=_receipt(dict({k:v for k,v in f.receipts[0].items() if k!='receipt_sha256'},journal_sha256=sha(raw),journal_bytes=len(raw)))
        with self.assertRaises(EvidenceError):orientation(receipt,raw,f.plan)

    def test_existing_screening_id_grammar_is_preserved(self):
        f=Fixture(self.root,person_prefix='_SYNTHETIC-')
        self.assertTrue(f.verify()['software_chain_verified'])

    def test_draft_and_failed_screening_can_be_verified_without_reveal(self):
        f=Fixture(self.root)
        for failed in (False,True):
            with self.subTest(failed=failed):
                rows=copy.deepcopy(f.rows[0] if not failed else Fixture(self.root/'failed',second=True).rows[0])
                plan=copy.deepcopy(f.plan);plan['content_status']='engineering_draft';plan['review_evidence_sha256']=None
                for row in rows:
                    if 'engineering_draft' in row:row['engineering_draft']=True
                if failed:
                    response=next(r for r in rows if r.get('attempt')==2 and r['event']=='practice_response')
                    response['correct']=False;response['response_target']=response['selected_target']='E'
                    next(r for r in rows if r.get('attempt')==2 and r['event']=='check_completed')['correct']=7
                    rows[-1]['second_correct'][0]=False;rows[-1]['outcome']='fail'
                raw=b''.join(canonical(r)+b'\n' for r in rows)
                receipt=dict(f.receipts[0],engineering_draft=True,eligible=False,outcome=rows[-1]['outcome'],journal_sha256=sha(raw),journal_bytes=len(raw))
                receipt=_receipt({k:v for k,v in receipt.items() if k!='receipt_sha256'})
                self.assertEqual(orientation(receipt,raw,plan)['events'],len(rows))

    def test_rehashed_native_event_identity_order_and_truth_mutations_refused(self):
        f=Fixture(self.root)
        mutations={
            'practice_answer':lambda rs:next(r for r in rs if r['event']=='practice_response').update(response_target='B',selected_target='B'),
            'ordinal':lambda rs:next(r for r in rs if r['event']=='practice_response').update(ordinal=2),
            'early_commit':lambda rs:next(r for r in rs if r['event']=='practice_response').update(response_mono_ms=0),
            'late_commit':lambda rs:next(r for r in rs if r['event']=='practice_response').update(response_mono_ms=999999),
            'demo_duration':lambda rs:next(r for r in rs if r['event']=='kinematic_demo_completed').update(observed_duration_ms=9000),
            'wrong_action':lambda rs:next(r for r in rs if r['event']=='action_screen').update(action='SCAN'),
            'unknown':lambda rs:rs[-1].update(approved=True),
            'bool_attempt':lambda rs:rs[2].update(attempt=True),
            'backward':lambda rs:rs[-1].update(mono_ms=0),
            'extra_end':lambda rs:rs.append(copy.deepcopy(rs[-1])),
            'missing_assets':lambda rs:rs.pop(1),
            'study_loaded':lambda rs:rs[0].update(study_audio_loaded=True),
        }
        for name,mutate in mutations.items():
            with self.subTest(name=name):
                rows=copy.deepcopy(f.rows[0]);mutate(rows);raw=b''.join(canonical(r)+b'\n' for r in rows)
                receipt=_receipt(dict({k:v for k,v in f.receipts[0].items() if k!='receipt_sha256'},journal_sha256=sha(raw),journal_bytes=len(raw)))
                with self.assertRaises(EvidenceError):orientation(receipt,raw,f.plan)

    def test_missing_artifacts_incomplete_but_declared_wrong_hash_invalid(self):
        f=Fixture(self.root)
        (f.root/f.packet['orientations'][0]['demo_index']['path']).unlink()
        report=f.verify();self.assertFalse(report['software_chain_verified'])
        self.assertIn('ORIENTATION_DEMO_INDEX_MISSING',report['incomplete_reasons'])
        f.packet['orientations'][0]['receipt']['sha256']='f'*64
        with self.assertRaisesRegex(EvidenceError,'HASH'):f.verify()

    def test_no_allocation_or_handoff_never_infers_eligibility(self):
        f=Fixture(self.root);f.packet['allocation']=None;f.packet['orientations'][0]['handoff']=None
        report=f.verify();self.assertFalse(report['software_chain_verified'])
        self.assertFalse(report['allocation_chain_verified'])
        self.assertIn('ORIENTATION_HANDOFF_MISSING',report['incomplete_reasons'])

    def test_changed_receipt_head_and_member_refused_even_resealed(self):
        for mutation in ('head','person','bool_line','eligibility_link','unknown'):
            with self.subTest(mutation=mutation):
                f=Fixture(self.root/mutation)
                def change(r):
                    if mutation=='head':r['journal_head_sha256']='f'*64
                    elif mutation=='person':r['entry']['participant_id']='SYNTHETIC-other';r['entry_sha256']=sha(canonical(r['entry']))
                    elif mutation=='bool_line':r['journal_line']=True
                    elif mutation=='eligibility_link':r['eligibility_receipt_sha256']='f'*64
                    else:r['approved']=True
                f.update(f.packet['allocation']['reveal'],change,True)
                with self.assertRaises(EvidenceError):f.verify()

    def test_raw_chain_reordering_or_rewritten_reveal_is_not_accepted(self):
        for mutation in ('order','member','head','checkpoint'):
            with self.subTest(mutation=mutation):
                f=Fixture(self.root/mutation)
                a=f.packet['allocation'];rows=[json.loads(x) for x in (f.root/a['journal']['path']).read_bytes().splitlines()]
                if mutation=='order':rows.reverse()
                elif mutation=='member':rows[-1]['policy']['entry']['participant_id']='SYNTHETIC-other'
                elif mutation=='head':a['retained_head_sha256']='f'*64
                else:f.update(a['checkpoint'],lambda x:x.update(journal_line=1))
                a['journal'].update(f.put(a['journal']['path'],b''.join(canonical(r)+b'\n' for r in rows)))
                with self.assertRaises(EvidenceError):f.verify()

    def test_wrong_join_identity_mapping_and_role_refused(self):
        for mutation in ('coded','unit','package','role','handoff','protocol'):
            with self.subTest(mutation=mutation):
                f=Fixture(self.root/mutation,'B');row=f.packet['joins'][0]
                if mutation=='role':
                    pin=row['config']['sha256'];s,v,r=f.expected[pin];f.expected[pin]=(s,v,'yoked' if r=='active' else 'active')
                elif mutation=='handoff':f.update(f.packet['orientations'][0]['handoff'],lambda x:x.update(joined_config_sha256='f'*64))
                else:
                    def change(x):
                        if mutation=='coded':x['identity']['coded_id']='B-P99-M1'
                        elif mutation=='unit':x['identity']['unit_id']='B-P99'
                        elif mutation=='package':x['pins']['package_sha256']='f'*64
                        else:x['protocol_version']='other'
                    f.update(row['config'],change)
                with self.assertRaises(EvidenceError):f.verify()

    def test_duplicate_person_config_and_unknown_packet_fields_refused(self):
        for mutation in ('person','config','field'):
            with self.subTest(mutation=mutation):
                f=Fixture(self.root/mutation)
                if mutation=='person':f.packet['orientations'].append(copy.deepcopy(f.packet['orientations'][0]))
                elif mutation=='config':f.packet['joins'].append(copy.deepcopy(f.packet['joins'][0]))
                else:f.packet['approved']=True
                with self.assertRaises(EvidenceError):f.verify()

    def test_path_escape_refused_before_read(self):
        f=Fixture(self.root);f.packet['orientations'][0]['receipt']['path']='../escape.json'
        with self.assertRaisesRegex(EvidenceError,'PATH'):f.verify()

    def test_missing_receipt_does_not_hide_malformed_supplied_plan_or_journal(self):
        for field in ('plan', 'journal', 'reveal', 'checkpoint'):
            with self.subTest(field=field):
                f=Fixture(self.root/field)
                f.packet['orientations'][0]['receipt']=None
                f.packet['allocation']['list']=None
                owner=f.packet['orientations'][0] if field in ('plan','journal') else f.packet['allocation']
                owner[field].update(f.put(owner[field]['path'], b'{}\n'))
                with self.assertRaises(EvidenceError):f.verify()

    def test_panel_deadline_is_exclusive_for_commit_inclusive_for_timeout(self):
        f=Fixture(self.root,second=True)
        for code,offset,valid in (('TIMEOUT',0,True),('TIMEOUT',-.001,False),('COMMIT',0,False),('DONT_KNOW',-.001,True)):
            with self.subTest(code=code,offset=offset):
                rows=copy.deepcopy(f.rows[0]);index=next(i for i,r in enumerate(rows) if r['event']=='practice_response')
                deadline=rows[index-1]['mono_ms']+f.plan['practice_window_ms'];delta=deadline+offset-rows[index]['mono_ms']
                for row in rows[index:]:
                    row['mono_ms']+=delta
                    if 'response_mono_ms' in row:row['response_mono_ms']+=delta
                response=rows[index];response['response_code']=code
                if code!='COMMIT':response['response_action']=response['response_target']=None
                raw=b''.join(canonical(r)+b'\n' for r in rows)
                receipt=_receipt(dict({k:v for k,v in f.receipts[0].items() if k!='receipt_sha256'},journal_sha256=sha(raw),journal_bytes=len(raw)))
                if valid:self.assertEqual(orientation(receipt,raw,f.plan)['events'],len(rows))
                else:
                    with self.assertRaisesRegex(EvidenceError,'DEADLINE'):orientation(receipt,raw,f.plan)

    def test_suite_versions_keep_screening_pending_until_exact_run_binding(self):
        f=Fixture(self.root);packet=f.put('packet.json',f.packet)
        manifest=f.put('run.json',dict(config=f.packet['joins'][0]['config'],artifacts=[]))
        report=dict(study='A',visit='D0',role='reference',software_reconciliation_complete=True,fault_codes=[],selection_snapshot_sha256=None)
        for version in (1,2,3):
            with self.subTest(version=version):
                value=dict(version=version,scope='SIMULATION_TEST',runs=[dict(scenario='normal',manifest=manifest)],screening_artifacts=[],screen_recordings=[],external_script_reports=[])
                if version>=2:value['fault_cases']=[]
                if version==3:value['screening_chains']=[packet]
                pin=f.put('suite.json',value)
                with patch('tools.mock_visit.suite.reconcile',return_value=copy.deepcopy(report)):
                    result=verify_suite(f.root/pin['path'],pin['sha256'])
                self.assertEqual(result['screening_chains_verified_for_present_runs'],version==3)
                self.assertFalse(result['screening_chain_coverage_complete'] or result['suite_complete'] or result['issue81_accepted'])
        value['screening_chains']=[packet,packet];pin=f.put('suite.json',value)
        with patch('tools.mock_visit.suite.reconcile',return_value=copy.deepcopy(report)):
            with self.assertRaisesRegex(EvidenceError,'DUPLICATE_SCREENING'):verify_suite(f.root/pin['path'],pin['sha256'])

    def test_suite_rejects_valid_packet_bound_to_a_different_actual_run(self):
        f=Fixture(self.root);packet=f.put('packet.json',f.packet)
        manifest=f.put('run.json',dict(config=dict(f.packet['joins'][0]['config'],sha256='f'*64),artifacts=[]))
        value=dict(version=3,scope='SIMULATION_TEST',runs=[dict(scenario='normal',manifest=manifest)],screening_artifacts=[],screen_recordings=[],external_script_reports=[],fault_cases=[],screening_chains=[packet])
        pin=f.put('suite.json',value)
        report=dict(study='A',visit='D0',role='reference',software_reconciliation_complete=True,fault_codes=[],selection_snapshot_sha256=None)
        with patch('tools.mock_visit.suite.reconcile',return_value=report):
            with self.assertRaisesRegex(EvidenceError,'SUITE_BINDING'):verify_suite(f.root/pin['path'],pin['sha256'])

    def test_missing_first_join_is_incomplete_not_reconstructed(self):
        f=Fixture(self.root);f.packet['joins'][0]['config']=None
        report=f.verify();self.assertFalse(report['software_chain_verified'])
        self.assertIn('HANDOFF_JOIN_MISSING',report['incomplete_reasons'])

    def test_cli_reports_only_software_status_and_never_replaces_output(self):
        f=Fixture(self.root);pin=f.put('packet.json',f.packet)
        command=[sys.executable,'-m','tools.mock_visit.screening','--packet',str(f.root/pin['path']),'--sha256',pin['sha256'],'--out',str(f.root/'result.json')]
        run=subprocess.run(command,capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr)
        report=json.loads((f.root/'result.json').read_bytes());self.assertFalse(report['participant_admission'] or report['issue81_accepted'])
        before=(f.root/'result.json').read_bytes()
        self.assertEqual(subprocess.run(command,capture_output=True).returncode,2)
        self.assertEqual(before,(f.root/'result.json').read_bytes())
        f.packet['allocation']=None;pin=f.put('packet.json',f.packet);command[6]=pin['sha256'];command[8]=str(f.root/'incomplete.json')
        self.assertEqual(subprocess.run(command,capture_output=True).returncode,3)

    @unittest.skipUnless(os.environ.get('AV_ORIENTATION_EXPORT'), 'Actual C# producer export not supplied')
    def test_actual_csharp_exports_preserve_draft_and_boundary_semantics(self):
        root=Path(os.environ['AV_ORIENTATION_EXPORT'])
        for outcome in ('pass_first','pass_second','fail'):
            with self.subTest(outcome=outcome):
                directory=root/outcome;export=json.loads((directory/'export.json').read_bytes())
                self.assertEqual(export['scope'],'SYNTHETIC_LOGIC_ONLY');self.assertFalse(export['participant_admission'] or export['native_visit'])
                self.assertTrue(export['actual_csharp_producers'])
                for entry in export['files']:
                    raw=(directory/entry['path']).read_bytes();self.assertEqual(sha(raw),entry['sha256']);self.assertEqual(len(raw),entry['bytes'])
                receipt=json.loads((directory/'receipt.json').read_bytes());plan=json.loads((directory/'plan.json').read_bytes())
                raw=next(directory.glob('orientation-*.jsonl')).read_bytes()
                result=orientation(receipt,raw,plan)
                self.assertEqual(result['receipt']['outcome'],outcome);self.assertTrue(receipt['engineering_draft']);self.assertFalse(receipt['eligible'])


if __name__=='__main__':unittest.main()
