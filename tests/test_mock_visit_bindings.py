"""Synthetic tamper cases for evidence binding; never native-run evidence."""
import copy
from collections import defaultdict
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from tools.mock_visit import content, native, provenance, store
from tools.mock_visit.records import EvidenceError, sha
from tools.mock_visit.reconcile import capture_pin, read, relative
from tools.mock_visit.suite import REQUIRED, coverage

H="a"*64


def sealed(value,key):
    value=copy.deepcopy(value);value.pop(key,None);value[key]=sha(content.canonical(value));return value


class Provenance(unittest.TestCase):
    def build(self):
        identity=dict(schema_version=1,build_id="simulation-test",commit_sha="b"*40,protocol_version="simulation-test-v1",
                      editor_version="6000.6.0f1",target="StandaloneWindows64",dirty_source=False,station_schema_sha256=H,development_only=True)
        b=dict(build_identity=identity,result="Succeeded",errors=0,duration_seconds=8.4,total_bytes=10,
               development_build=False,files=[dict(path="app.exe",bytes=10,sha256=H)])
        m=dict(source_commit="b"*40); c=dict(identity=dict(build_id="simulation-test"),protocol_version="simulation-test-v1")
        return b,m,c,dict(build_id="simulation-test",protocol_version="simulation-test-v1")

    def test_exact_native_build_identity(self):
        result=provenance.build(*self.build(),relative)
        self.assertTrue(result["source_commit_bound"]);self.assertFalse(result["executable_bytes_reverified"])

    def test_mismatched_source_refused_even_if_pinned(self):
        args=self.build();args[0]["build_identity"]["commit_sha"]="c"*40
        with self.assertRaisesRegex(EvidenceError,"MOCK_BUILD_IDENTITY"):provenance.build(*args,relative)

    def test_dirty_or_wrong_build_refused(self):
        for key,value in (("dirty_source",True),("build_id","other"),("development_only",False)):
            args=self.build();args[0]["build_identity"][key]=value
            with self.subTest(key=key),self.assertRaisesRegex(EvidenceError,"MOCK_BUILD_IDENTITY"):provenance.build(*args,relative)

    def test_duplicate_build_inventory_refused(self):
        args=self.build();args[0]["files"]*=2
        with self.assertRaisesRegex(EvidenceError,"MOCK_BUILD_FILE"):provenance.build(*args,relative)

    def test_capability_gain_matches_native_closed_range(self):
        cap=dict(version=1,scope="SIMULATION_TEST",build_id="test",protocol_version="simulation-test-v1",output_directory="private",
                 schedule_sha256=H,package_sha256=H,fixture_set_sha256=H,audio_gain=.05,acoustic_qualification=False,participant_admission=False)
        m=dict(schedule=dict(sha256=H),package_sha256=H,fixture_set_sha256=H)
        provenance.capability(cap,m)
        for bad in (0,.100001,1,True):
            with self.subTest(bad=bad),self.assertRaisesRegex(EvidenceError,"MOCK_CAPABILITY_BINDING"):
                provenance.capability(dict(cap,audio_gain=bad),m)

    def test_observer_duration_bounds_retained_native_span(self):
        p=dict(started_utc="2026-10-05T00:00:00Z",ended_utc="2026-10-05T00:01:00Z")
        self.assertEqual(provenance.interval(p,[dict(host_mono_ms=1000),dict(host_mono_ms=61000)],[])["retained_native_span_ms"],60000)
        with self.assertRaisesRegex(EvidenceError,"MOCK_PROCESS_NATIVE_INTERVAL"):
            provenance.interval(p,[dict(host_mono_ms=1000),dict(host_mono_ms=62000)],[])

    def test_non_utc_and_reversed_observer_refused(self):
        for start,end in (("2026-10-05T00:00:00","2026-10-05T00:01:00"),("2026-10-05T00:02:00Z","2026-10-05T00:01:00Z")):
            with self.assertRaisesRegex(EvidenceError,"MOCK_PROCESS_UTC"):provenance.process_duration(dict(started_utc=start,ended_utc=end))

    def test_claimed_native_complete_requires_exact_terminal_status(self):
        row=dict(kind="module",payload=dict(kind="native_run_end",status="JOIN_FAULT",complete=True,scope="SIMULATION_TEST",participant_admission=False))
        self.assertIn("NATIVE_RUN_END_INCOMPLETE",native.display([row],{}, {},"A","D0"))
        row["payload"]["status"]="JOIN_COMPLETE_FORMS_RECORDED"
        self.assertNotIn("NATIVE_RUN_END_INCOMPLETE",native.display([row],{}, {},"A","D0"))

    def test_all_normal_successes_cannot_certify_fault_suite(self):
        r=coverage([("normal",dict(study=s,visit=v,role=r,software_reconciliation_complete=True,fault_codes=[])) for s,v,r in REQUIRED])
        self.assertTrue(r["normal_software_complete"]);self.assertFalse(r["suite_complete"])

    def test_capture_stream_handles_larger_than_json_limit(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"screen.mp4"
            with p.open("wb") as f:f.truncate(65*1024**2)
            size,pin=capture_pin(p);self.assertEqual(size,65*1024**2)
            self.assertEqual(capture_pin(p,pin),(size,pin))
            with self.assertRaisesRegex(EvidenceError,"MOCK_FILE_HASH"):capture_pin(p,H)

    def terminal(self):
        p=dict(version=1,scope="SIMULATION_TEST",session_nonce="a"*32,process_id=123,source_commit="b"*40,
               config_sha256=H,simulation_capability_sha256=H,status="JOIN_COMPLETE_FORMS_RECORDED",complete=True,
               cleanup_succeeded=True,export_succeeded=True,export_manifest_sha256=H,host_mono_ms=2000.,participant_admission=False)
        manifest=dict(source_commit="b"*40,config=dict(sha256=H),simulation_capability=dict(sha256=H))
        process=dict(process_id=123,source_commit="b"*40)
        return p,manifest,process,H,["a"*32]

    def test_post_cleanup_success_is_separate_from_close_intent(self):
        self.assertEqual(provenance.native_result(*self.terminal()),set())
        p=dict(kind="module",payload=dict(kind="native_run_end",status="JOIN_COMPLETE_FORMS_RECORDED",complete=False,scope="SIMULATION_TEST",participant_admission=False))
        self.assertNotIn("NATIVE_RUN_END_INCOMPLETE",native.display([p],{},{},"A","D0"))

    def test_throwing_cleanup_cannot_be_completed_by_successful_export(self):
        args=self.terminal();args[0].update(status="JOIN_DISPOSE_FAILED",complete=False,cleanup_succeeded=False)
        self.assertIn("NATIVE_POST_CLEANUP_RESULT_INCOMPLETE",provenance.native_result(*args))
        args[0]["complete"]=True
        with self.assertRaisesRegex(EvidenceError,"MOCK_NATIVE_RESULT_FALSE_SUCCESS"):provenance.native_result(*args)

    def test_throwing_export_cannot_be_complete(self):
        args=self.terminal();args[0].update(status="JOIN_EXPORT_FAILED",complete=False,export_succeeded=False,export_manifest_sha256=None)
        self.assertIn("NATIVE_POST_CLEANUP_RESULT_INCOMPLETE",provenance.native_result(*args))

    def test_native_result_process_source_and_capability_pins(self):
        for key,value in (("process_id",124),("source_commit","c"*40),("simulation_capability_sha256","c"*64)):
            args=self.terminal();args[0][key]=value
            with self.subTest(key=key),self.assertRaisesRegex(EvidenceError,"MOCK_NATIVE_RESULT_BINDING"):provenance.native_result(*args)

    def test_native_result_requires_exact_export_and_nonce(self):
        args=self.terminal();args[0]["export_manifest_sha256"]="c"*64
        with self.assertRaisesRegex(EvidenceError,"MOCK_NATIVE_RESULT_EXPORT"):provenance.native_result(*args)
        args=self.terminal();args[0]["session_nonce"]="c"*32
        with self.assertRaisesRegex(EvidenceError,"MOCK_NATIVE_RESULT_NONCE"):provenance.native_result(*args)

    def test_native_result_cannot_predate_last_durable_row(self):
        with self.assertRaisesRegex(EvidenceError,"MOCK_NATIVE_RESULT_ORDER"):provenance.native_result(*self.terminal(),2001.)


class Profiles(unittest.TestCase):
    def fixture(self,root):
        entries=[]
        for n,p in enumerate(("P1","P2","P3")):
            data=struct.pack("<h",n)*96000
            wav=struct.pack('<4sI4s4sIHHIIHH4sI',b'RIFF',len(data)+36,b'WAVE',b'fmt ',16,1,1,48000,96000,2,16,b'data',len(data))+data
            (root/("calibration-"+p+".wav")).write_bytes(wav)
            entries.append(dict(id="calibration-"+p,kind="calibration",profile=p,recipe=None,n_samples=96000,pcm_sha256=sha(data),file_sha256=sha(wav)))
        files={}
        for name,obj in (("reserved_registry",dict(registry_version=1,entries=entries)),("menu_allocation",dict(demo=True,dyads=[dict(members=[dict(slot_id="M1",role="active")],profile_menu_order=["P3","P1","P2"])]))):
            raw=content.canonical(obj);(root/(name+".json")).write_bytes(raw);files[name]=dict(path=name+".json",sha256=sha(raw))
        # A child directory keeps the production path grammar unchanged.
        examples=root/"examples";examples.mkdir()
        for p in root.glob("*.wav"):p.rename(examples/p.name)
        return dict(files=files,directories=dict(menu_examples="examples")),dict(person_slot="M1",visit="V1")

    def test_registry_order_and_full_canonical_samples(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);c,s=self.fixture(root)
            order,examples=content.profile_examples(c,root,s,"active",read,relative)
            self.assertEqual(order,["P3","P1","P2"]);self.assertEqual(examples["P1"]["n_samples"],96000)
            path=root/"examples/calibration-P1.wav";path.write_bytes(path.read_bytes()[:-2])
            with self.assertRaisesRegex(EvidenceError,"MOCK_FILE_HASH"):content.profile_examples(c,root,s,"active",read,relative)

    def test_all_eight_profile_plays_bind_frozen_order_and_selected_profile(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);c,s=self.fixture(root);order,examples=content.profile_examples(c,root,s,"active",read,relative)
            snap=dict(profile="P1",entries=[]);requests={};observations={}
            for i,p in enumerate(("P3","P3","P1","P1","P2","P2","P1","P1")):
                key=f"{i:032x}";r=examples[p]
                requests[key]=dict(opportunity_id="menu",audio_request_id=key,payload=dict(pcm_sha256=r["pcm_sha256"],waveform_sha256=r["file_sha256"]))
                observations[key]=[dict(payload=dict(code="AUDIO_PLAYBACK_COMPLETED",delivered_samples=96000,callback_count=48))]
            artifacts=defaultdict(list);artifacts["package_manifest"]=[({},b"")];items=dict(menu=dict(block="profile_menu"))
            def run():return content.verify(root,artifacts,items,requests,H,read,relative,observations,config=c,config_root=root,schedule=s,role="active")
            with patch.object(content,"package",return_value=(dict(study="B"),{},{})),patch.object(store,"verify",return_value=(set(),snap)):
                self.assertEqual(run()[1],8)
                first=next(iter(requests));requests[first]["payload"]["pcm_sha256"]=examples["P1"]["pcm_sha256"]
                with self.assertRaisesRegex(EvidenceError,"MOCK_PROFILE_PLAY_ORDER"):run()
                requests[first]["payload"]["pcm_sha256"]=examples["P3"]["pcm_sha256"]
                observations[first][0]["payload"]["delivered_samples"]=95999
                with self.assertRaisesRegex(EvidenceError,"MOCK_TRUNCATED_COMPLETION"):run()


class Stores(unittest.TestCase):
    def setup_store(self,root):
        bridge=dict(schema_version=1,demo_only=True,unit_id="DEMO-B-C01",book_id="DEMO-book",bank_path="private",bank_file_sha256=H,bank_sha256=H,package_path="private",package_sha256=H,store_root="private")
        self.binding={k:bridge[k] for k in ("unit_id","book_id","bank_sha256","package_sha256")};self.binding["config_sha256"]=sha(content.canonical(bridge))
        self.atoms={(p,f"K-a{i}",rank):dict(pcm_sha256=sha(f"{p}-{i}-{rank}".encode()),file_sha256=H) for p in ("P1","P2","P3") for i in range(1,5) for rank in range(1,4)}
        initial=sealed(dict(schema_version=1,**self.binding,profile=None,profile_selection_receipt_sha256=None,book_head=None,snapshot_sha256=sha(b"{}"),journal_head=None,source_kind="synthetic",participant_ready=False,entries=[]),"manifest_sha256")
        files={}
        for name,obj in (("menu_bridge_config",bridge),("menu_snapshot",initial)):
            raw=content.canonical(obj);(root/(name+".json")).write_bytes(raw);files[name]=dict(path=name+".json",sha256=sha(raw))
        self.config=dict(files=files,identity=dict(unit_id="B-C01"),pins=dict(bank_sha256=H,menu_manifest_sha256=initial["manifest_sha256"],menu_head_sha256=None,menu_snapshot_sha256=initial["snapshot_sha256"]))
        self.initial=initial;self.root=root

    def operation(self,before,operation,index):
        q=dict(schema_version=1,request_id=f"{index:032x}",operation=operation,**{k:v for k,v in self.binding.items() if k!="config_sha256"},expected_head=before["book_head"],expected_snapshot_sha256=before["snapshot_sha256"],profile=before["profile"] if operation=="verify" else "P1",menu_key="verify" if operation=="verify" else "profile" if operation=="profile" else "K-a1",rank=1 if operation=="atom" else None)
        after=copy.deepcopy(before);receipt=None
        if operation!="verify":
            data=self.atoms["P1","K-a1",1] if operation=="atom" else dict(pcm_sha256=None,file_sha256=None)
            after.update(profile="P1",book_head=sha(str(index).encode()),journal_head=sha(str(index+1).encode()))
            if operation=="atom":after["snapshot_sha256"]=sha(content.canonical({"K-a1":data["pcm_sha256"]}))
            receipt=sealed(dict(**{k:v for k,v in q.items() if k not in ("expected_head","expected_snapshot_sha256")},request_sha256=sha(content.canonical(q)),config_sha256=self.binding["config_sha256"],status="profile_selected" if operation=="profile" else "committed",accepted=True,reason=None,before_head=before["book_head"],after_head=after["book_head"],before_snapshot_sha256=before["snapshot_sha256"],after_snapshot_sha256=after["snapshot_sha256"],**data,source_kind="synthetic",participant_ready=False),"receipt_sha256")
            if operation=="profile":after["profile_selection_receipt_sha256"]=receipt["receipt_sha256"]
            else:after["entries"].append(dict(atom_id="K-a1",profile="P1",rank=1,**data,selection_receipt_sha256=receipt["receipt_sha256"]))
            after=sealed(after,"manifest_sha256")
        response=sealed(dict(schema_version=1,request_id=q["request_id"],request_sha256=sha(content.canonical(q)),config_sha256=self.binding["config_sha256"],receipt=receipt,snapshot=after,error=None),"response_sha256")
        return [dict(kind="store",payload=dict(event="menu_store_intent",mono_ms=index*100,request=q)),dict(kind="store",payload=dict(event="menu_store_verified",mono_ms=index*100+10,response=response))],after

    def run_rows(self,rows,menus=()):
        return store.verify(self.config,self.root,rows,menus,self.atoms,H,read,relative,"active","V1")

    def test_actual_envelope_shape_and_menu_receipt_link(self):
        with tempfile.TemporaryDirectory() as d:
            self.setup_store(Path(d));rows,current=self.operation(self.initial,"profile",1)
            more,current=self.operation(current,"atom",2);rows+=more
            menus=[[dict(record=dict(kind="selection_verified",menu_key="K-a1",selected_index=1,receipt_sha256=current["entries"][0]["selection_receipt_sha256"],mono_ms=220))]]
            more,current=self.operation(current,"verify",3);rows+=more
            missing,result=self.run_rows(rows,menus)
            self.assertEqual(len(result["entries"]),1);self.assertIn("STORE_FINAL_WAVE_INCOMPLETE",missing)
            self.assertNotIn("STORE_FINAL_FRESH_VERIFICATION_MISSING",missing)
            menus[0][0]["record"]["receipt_sha256"]=H
            with self.assertRaisesRegex(EvidenceError,"MOCK_MENU_SELECTION_RECEIPT_LINK"):self.run_rows(rows,menus)

    def test_response_without_intent_and_changed_request_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            self.setup_store(Path(d));rows,_=self.operation(self.initial,"profile",1)
            with self.assertRaisesRegex(EvidenceError,"MOCK_STORE_RESPONSE_WITHOUT_REQUEST"):self.run_rows(rows[1:])
            rows[0]["payload"]["request"]["profile"]="P2"
            with self.assertRaisesRegex(EvidenceError,"MOCK_STORE_RESPONSE_BINDING"):self.run_rows(rows)

    def test_fresh_verify_required_after_selection(self):
        with tempfile.TemporaryDirectory() as d:
            self.setup_store(Path(d));rows,current=self.operation(self.initial,"profile",1)
            self.assertIn("STORE_FINAL_FRESH_VERIFICATION_MISSING",self.run_rows(rows)[0])
            more,_=self.operation(current,"verify",2);rows+=more
            self.assertNotIn("STORE_FINAL_FRESH_VERIFICATION_MISSING",self.run_rows(rows)[0])

    def test_cross_visit_old_selection_cannot_change(self):
        before=dict(profile="P1",profile_selection_receipt_sha256=H,entries=[dict(atom_id="K-a1",rank=1,pcm_sha256=H)])
        after=copy.deepcopy(before);after["entries"][0]["rank"]=2
        with self.assertRaisesRegex(EvidenceError,"MOCK_STORE_OLD_ENTRY_CHANGED"):store.unchanged(before,after)
        self.assertFalse(store.compare_growth({})["cross_visit_growth_verified"])


if __name__ == "__main__":unittest.main()
