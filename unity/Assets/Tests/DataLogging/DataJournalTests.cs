using System;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.SessionEngine;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.DataLogging.Tests
{
    public static class SyntheticData
    {
        public static readonly DataIdentity Identity=new DataIdentity(new string('1',32),"SYNTHETIC","DEMO","synthetic-station","engineering-test",new string('a',64));
        public static string Folder(string label)=>Path.Combine(Environment.GetEnvironmentVariable("DATA_TEST_ROOT")??Path.Combine(Environment.CurrentDirectory,".local","data-tests"),label+"-"+Guid.NewGuid().ToString("N"));
        public static SessionRecord Session(string trial,string state,string response=null,string retry=null,string audible="Uncertain",string kind="state_after",string requestId=null,double observed=0)
        {
            var p=new JObject{["event"]=kind,["clock_epoch"]=new string('2',32),["schedule_sha256"]=new string('b',64),["trial_id"]=trial,["retry_of"]=retry,["block_index"]=0,["item_index"]=0,["host_mono_ms"]=observed,["scheduled_onset_mono_ms"]=0,["state"]=state,["audible_status"]=audible,["exposure_consumed"]=audible=="Uncertain"||audible=="ConfirmedAudible",["reset_ok"]=state=="Done",["focus_ok"]=true,["technical_fault_code"]=null,["response_code"]=response,["evidence_sha256"]=kind=="onset_evidence"?new string('e',64):null,["opportunity_id"]=retry??trial,["audio_request_ids"]=requestId==null?new JArray():new JArray(requestId)};
            foreach(var property in p.Properties().ToArray())
                if(property.Value.Type==JTokenType.String&&(string)property.Value==null)property.Value=JValue.CreateNull();
            return SessionRecordCodec.FromJson(p);
        }
        public static EventDraft Audio(EventContext c,string code="AUDIO_REQUESTED",bool callback=false,bool estimated=false)=>new EventDraft(code=="AUDIO_REQUESTED"?"audio_request":"audio_observation",c,new JObject{
            ["code"]=code,["audio_id"]="synthetic-silence",["waveform_sha256"]=new string('c',64),["pcm_sha256"]=new string('d',64),["action_pcm_sha256"]=null,["referent_pcm_sha256"]=null,["observed_mono_ms"]=900,["request_mono_ms"]=800,["scheduled_mono_ms"]=1000,["scheduled_dsp_s"]=5,["onset_estimate_mono_ms"]=estimated?(JToken)1000:JValue.CreateNull(),["onset_uncertainty_ms"]=estimated?(JToken)5:JValue.CreateNull(),["first_callback_dsp_s"]=callback?(JToken)4.98:JValue.CreateNull(),["delivered_samples"]=callback?48000:0,["callback_count"]=callback?100:0});
        public static EventDraft Panel(EventContext c,string code)=>new EventDraft("panel_response",c,new JObject{["kind"]="response",["observed_mono_ms"]=1300,["mode"]="FullMessage",["role"]="Command",["input"]=null,["selected_target"]="A",["selected_action"]="ADD_ONE",["response_code"]=code,["response_target"]=code=="commit"?"A":null,["response_action"]=code=="commit"?"ADD_ONE":null});
        public static void Trial(DataJournal writer,string id,string response="commit",string retry=null,bool completed=true)
        {
            string request=Guid.NewGuid().ToString("N");var c=new EventContext(retry??id,id,request);var session=new SessionDataJournal(writer);
            session.Append(Session(id,"Loaded",retry:retry,audible:"NotRequested",requestId:request));session.Append(Session(id,"CueRequested",retry:retry,kind:"state_before",requestId:request));
            writer.Append(Audio(c));writer.Append(Audio(c,"AUDIO_PLAYBACK_COMPLETED",true,true));writer.Append(Panel(new EventContext(retry??id,id),response));
            session.Append(Session(id,"Closed",response,retry,requestId:request));if(completed)session.Append(Session(id,"Done",response,retry,requestId:request));
        }
    }
    public sealed class DataJournalTests
    {
        [Test] public void ThirtySixTrialsProduceThirtySixAttemptsAndActualRequests()
        {
            string raw=SyntheticData.Folder("36-virtual-trials");double now=0;
            using(var writer=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>now++))
                for(int i=0;i<36;i++){string id="synthetic-"+i;writer.Append(DataObservations.Opportunity(id,"primary","masked-test",new string('b',64)));SyntheticData.Trial(writer,id,i==35?"timeout":"commit");}
            var snapshot=DataJournal.Verify(raw,SyntheticData.Identity);var tables=DataDeriver.Derive(snapshot,SyntheticData.Identity);
            Assert.That(tables.Trials.Count,Is.EqualTo(36));Assert.That(tables.Exposures.Count,Is.EqualTo(36));Assert.That(tables.Trials.Last()["response_code"],Is.EqualTo("timeout"));Assert.That(tables.Trials.All(x=>x["interrupted"]=="false"&&x["exposure_consumed"]=="true"),Is.True);
            Assert.That(tables.Exposures.All(x=>x["audible_status"]=="estimated"&&x["callback_observed"]=="true"),Is.True,"Callback completion is not acoustic confirmation");
        }
        [Test] public void TornTailIsPreservedAndCommittedResponseSurvivesRecovery()
        {
            string raw=SyntheticData.Folder("crash-tail");string first;
            using(var writer=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>100)){SyntheticData.Trial(writer,"synthetic-crash",completed:false);first=writer.SegmentPath;}
            File.AppendAllText(first,"{\"schema_version\":",new UTF8Encoding(false));byte[] preserved=File.ReadAllBytes(first);
            var before=DataJournal.Verify(raw,SyntheticData.Identity);Assert.That(before.HasUnacknowledgedTail,Is.True);
            var interrupted=DataDeriver.Derive(before,SyntheticData.Identity).Trials.Single();Assert.That(interrupted["response_code"],Is.EqualTo("commit"));Assert.That(interrupted["interrupted"],Is.EqualTo("true"));Assert.That(interrupted["exposure_consumed"],Is.EqualTo("true"));
            using(var writer=new DataJournal(raw,SyntheticData.Identity,new string('4',32),()=>1)){Assert.That(writer.Records.Last().Kind,Is.EqualTo("recovery"));Assert.That(new SessionDataJournal(writer).Records.Any(x=>x.ResponseCode=="commit"),Is.True);}
            Assert.That(File.ReadAllBytes(first),Is.EqualTo(preserved));Assert.That(DataJournal.Verify(raw,SyntheticData.Identity).HasUnacknowledgedTail,Is.False);
        }
        [Test] public void TornRecoveryMarkerCanRecoverAgainWithoutTruncatingEitherFile()
        {
            string raw=SyntheticData.Folder("double-tail");using(var w=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>1))w.Append(DataObservations.Device(null,"focus",1,true));
            string first=Directory.GetFiles(raw).Single();File.AppendAllText(first,"torn");string empty=Path.Combine(raw,"events-0001.local.jsonl");File.WriteAllText(empty,"torn-recovery");byte[] a=File.ReadAllBytes(first),b=File.ReadAllBytes(empty);
            using(var w=new DataJournal(raw,SyntheticData.Identity,new string('4',32),()=>0))Assert.That(((JArray)w.Records.Last().Payload["preserved_tails"]).Count,Is.EqualTo(2));
            Assert.That(File.ReadAllBytes(first),Is.EqualTo(a));Assert.That(File.ReadAllBytes(empty),Is.EqualTo(b));Assert.That(DataJournal.Verify(raw,SyntheticData.Identity).HasUnacknowledgedTail,Is.False);
        }
        [Test] public void CompleteCorruptLineFailsClosedAndLeavesOriginalUntouched()
        {
            string raw=SyntheticData.Folder("complete-corruption");using(var w=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>1))w.Append(DataObservations.Device(null,"focus",1,true));
            string first=Directory.GetFiles(raw).Single();File.AppendAllText(first,"{}\n");byte[] original=File.ReadAllBytes(first);
            Assert.Throws<DataFault>(()=>new DataJournal(raw,SyntheticData.Identity,new string('4',32),()=>0));Assert.That(File.ReadAllBytes(first),Is.EqualTo(original));Assert.That(Directory.GetFiles(raw).Length,Is.EqualTo(1));
        }
        [Test] public void AppendFailureLatchesAndNeverReportsUnflushedRecordAsDurable()
        {
            string raw=SyntheticData.Folder("io-failure");using var w=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>1);w.BeforeDurableFlush=()=>throw new IOException("synthetic storage fault");
            Assert.Throws<DataFault>(()=>w.Append(DataObservations.Device(null,"focus",1,true)));Assert.That(w.Health.Failed,Is.True);Assert.That(w.Health.DurableRecordCount,Is.Zero);Assert.That(w.Health.Writable,Is.False);Assert.Throws<DataFault>(()=>w.Append(DataObservations.Device(null,"focus",2,true)));
        }
        [Test] public void HostAndSessionClockEpochsAreOrderedIndependently()
        {
            string raw=SyntheticData.Folder("clock");double time=50;
            using(var w=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>time)){var s=new SessionDataJournal(w);s.Append(SyntheticData.Session("t","Loaded",audible:"NotRequested",observed:100));time=51;s.Append(SyntheticData.Session("t","Ready",audible:"NotRequested",observed:101));time=52;Assert.Throws<DataFault>(()=>s.Append(SyntheticData.Session("t","Ready",audible:"NotRequested",observed:99)));Assert.That(w.Failed,Is.True);}
            using(var w=new DataJournal(raw,SyntheticData.Identity,new string('4',32),()=>0))Assert.That(w.Records.Count,Is.EqualTo(2));
        }
        [Test] public void GlobalClockRegressionFailsEvenIfPayloadTimestampIncreases()
        {
            string raw=SyntheticData.Folder("global-clock");double now=10;using var w=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>now);w.Append(DataObservations.Device(null,"focus",100,true));now=9;Assert.Throws<DataFault>(()=>w.Append(DataObservations.Device(null,"focus",101,true)));
        }
        [Test] public void MissingOnsetConsumesExposureDespiteSuccessfulRequest()
        {
            string raw=SyntheticData.Folder("missing-onset");using(var w=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>10)){var s=new SessionDataJournal(w);s.Append(SyntheticData.Session("t","CueRequested"));w.Append(SyntheticData.Audio(new EventContext("t","t",new string('5',32))));}
            var t=DataDeriver.Derive(DataJournal.Verify(raw,SyntheticData.Identity),SyntheticData.Identity);Assert.That(t.Exposures.Single()["playback_status"],Is.EqualTo("uncertain"));Assert.That(t.Exposures.Single()["exposure_consumed"],Is.EqualTo("true"));Assert.That(t.Exposures.Single()["audio_onset_estimate_mono_ms"],Is.Empty);
        }
        [Test] public void SimulationSoftwareAnchorNeverFillsAcousticColumnsOrGrantsUnheardReplay()
        {
            string raw=SyntheticData.Folder("simulation-observation");var context=new EventContext("t","t",new string('5',32));
            // Synthetic unit observations test the real codec/deriver, not a native callback claim.
            EventDraft Observation(string code,bool callback)
            {
                var original=SyntheticData.Audio(context,code,callback,false);var payload=original.Payload;
                payload["simulation_test"]=true;payload["software_output_estimate_mono_ms"]=1000;payload["software_output_uncertainty_ms"]=11;
                return new EventDraft(original.Kind,context,payload);
            }
            using(var writer=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>10))
            {
                writer.Append(Observation("AUDIO_REQUESTED",false));writer.Append(Observation("SIMULATION_DELIVERY_OBSERVED",true));writer.Append(Observation("AUDIO_PLAYBACK_COMPLETED",true));
                new SessionDataJournal(writer).Append(SyntheticData.Session("t","Done",requestId:context.AudioRequestId));
            }
            var snapshot=DataJournal.Verify(raw,SyntheticData.Identity);var tables=DataDeriver.Derive(snapshot,SyntheticData.Identity);
            foreach(var row in new[]{tables.Trials.Single(),tables.Exposures.Single()})
            {Assert.That(row["audio_onset_estimate_mono_ms"],Is.Empty);Assert.That(row["onset_uncertainty_ms"],Is.Empty);Assert.That(row["exposure_consumed"],Is.EqualTo("true"));}
            Assert.That(tables.Exposures.Single()["audible_status"],Is.EqualTo("uncertain"));Assert.That(tables.Exposures.Single()["callback_observed"],Is.EqualTo("true"));
            Assert.That(tables.Exposures.Single()["technical_fault_code"],Is.Empty);
            Assert.That((double)snapshot.Records.First(r=>r.Kind=="audio_observation").Payload["software_output_estimate_mono_ms"],Is.EqualTo(1000));
            var bundle=ExportBundle.Create(raw,raw+"-export",SyntheticData.Identity,ExportHeaders.Provisional());bundle.VerifyAll();Assert.That(bundle.HeadersQualified,Is.False);
        }
        [Test] public void ConfirmedNoOnsetRetryRetainsBothAttemptsAndSeparateAudioRequests()
        {
            string raw=SyntheticData.Folder("retry");string request=new string('5',32);var c=new EventContext("t","t",request);
            using(var w=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>10)){var s=new SessionDataJournal(w);s.Append(SyntheticData.Session("t","CueRequested",requestId:request));w.Append(SyntheticData.Audio(c));w.Append(DataObservations.TrustedAudioEvidence(c,"confirmed_no_onset",new string('e',64),"trusted_delivery_evidence",12));s.Append(SyntheticData.Session("t","Closed",audible:"ConfirmedNoOnset",kind:"onset_evidence",requestId:request));s.Append(SyntheticData.Session("t","Done",audible:"ConfirmedNoOnset",requestId:request));SyntheticData.Trial(w,"t.retry1",retry:"t");}
            var tables=DataDeriver.Derive(DataJournal.Verify(raw,SyntheticData.Identity),SyntheticData.Identity);Assert.That(tables.Trials.Count,Is.EqualTo(2));Assert.That(tables.Exposures.Count,Is.EqualTo(2));Assert.That(tables.Trials[0]["exposure_consumed"],Is.EqualTo("false"));Assert.That(tables.Trials[1]["retry_of"],Is.EqualTo("t"));Assert.That(tables.Exposures.Select(x=>x["audio_request_id"]).Distinct().Count(),Is.EqualTo(2));
        }
        [Test] public void RejectedAndYokedCandidatePlaysAreBothRetained()
        {
            string raw=SyntheticData.Folder("rejected-yoked");using(var w=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>10))
                for(int i=0;i<2;i++){var c=new EventContext("menu","menu",Guid.NewGuid().ToString("N"));w.Append(SyntheticData.Audio(c));w.Append(DataObservations.Choice(c,"synthetic-candidate","rejected",i==0?null:"synthetic-source-event",250,null));}
            var rows=DataDeriver.Derive(DataJournal.Verify(raw,SyntheticData.Identity),SyntheticData.Identity).Exposures;Assert.That(rows.Count,Is.EqualTo(2));Assert.That(rows.All(x=>x["accepted_or_rejected"]=="rejected"&&x["exposure_consumed"]=="true"),Is.True);Assert.That(rows[1]["yoked_source_event_id"],Is.EqualTo("synthetic-source-event"));
        }
        [Test] public void CallbackCannotTurnConfirmedNoOnsetIntoAFreeReplay()
        {
            string raw=SyntheticData.Folder("conflicting-onset");var c=new EventContext("t","t",new string('5',32));using(var w=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>10)){w.Append(SyntheticData.Audio(c));w.Append(DataObservations.TrustedAudioEvidence(c,"confirmed_no_onset",new string('e',64),"trusted_delivery_evidence",11));w.Append(SyntheticData.Audio(c,"AUDIO_PLAYBACK_COMPLETED",true,true));}
            var row=DataDeriver.Derive(DataJournal.Verify(raw,SyntheticData.Identity),SyntheticData.Identity).Exposures.Single();Assert.That(row["exposure_consumed"],Is.EqualTo("true"));Assert.That(row["audible_status"],Is.EqualTo("uncertain"));Assert.That(row["technical_fault_code"],Does.Contain("DATA_ONSET_EVIDENCE_CONFLICT"));
        }
        [Test] public void LaterSessionUncertaintyRestoresConsumedExposureAfterNoOnset()
        {
            string raw=SyntheticData.Folder("session-uncertainty");string id=new string('5',32);var c=new EventContext("t","t",id);
            using(var w=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>1)){var session=new SessionDataJournal(w);w.Append(SyntheticData.Audio(c));session.Append(SyntheticData.Session("t","Closed",audible:"ConfirmedNoOnset",kind:"onset_evidence",requestId:id));session.Append(SyntheticData.Session("t","Closed",audible:"Uncertain",kind:"onset_evidence",requestId:id));}
            var row=DataDeriver.Derive(DataJournal.Verify(raw,SyntheticData.Identity),SyntheticData.Identity).Exposures.Single();Assert.That(row["exposure_consumed"],Is.EqualTo("true"));Assert.That(row["audible_status"],Is.EqualTo("uncertain"));
        }
        [Test] public void ConfirmedAudibleThenUncertainThenNoOnsetCannotEraseExposure()
        {
            string raw=SyntheticData.Folder("ever-audible");var c=new EventContext("t","t",new string('5',32));
            using(var w=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>1)){w.Append(SyntheticData.Audio(c));foreach(string status in new[]{"confirmed_audible","uncertain","confirmed_no_onset","confirmed_no_onset"})w.Append(DataObservations.TrustedAudioEvidence(c,status,new string('e',64),"trusted_delivery_evidence",1));}
            var row=DataDeriver.Derive(DataJournal.Verify(raw,SyntheticData.Identity),SyntheticData.Identity).Exposures.Single();Assert.That(row["exposure_consumed"],Is.EqualTo("true"));Assert.That(row["audible_status"],Is.EqualTo("uncertain"));Assert.That(row["technical_fault_code"],Does.Contain("DATA_ONSET_EVIDENCE_CONFLICT"));
        }
        [Test] public void MissingFocusEvidenceNeverInventsAPass()
        {string raw=SyntheticData.Folder("unknown-focus");using(var w=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>1))w.Append(SyntheticData.Audio(new EventContext("t","t",new string('5',32))));Assert.That(DataDeriver.Derive(DataJournal.Verify(raw,SyntheticData.Identity),SyntheticData.Identity).Trials.Single()["focus_ok"],Is.Empty);}
        [Test] public void EmptyVisitMakesNoTrialOrExposureRows()
        {string raw=SyntheticData.Folder("empty-visit");using(new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>0)){}var rows=DataDeriver.Derive(DataJournal.Verify(raw,SyntheticData.Identity),SyntheticData.Identity);Assert.That(rows.Trials,Is.Empty);Assert.That(rows.Exposures,Is.Empty);}
        [Test] public void ClosedSchemaRejectsPersonalFieldsAndUnknownEventKinds()
        {var value=DataObservations.Device(null,"focus",1,true).Payload;value["name"]="synthetic-not-allowed";Assert.Throws<DataFault>(()=>new EventDraft("device",null,value));Assert.Throws<DataFault>(()=>new EventDraft("unregistered",null,new JObject()));}
    }
}
