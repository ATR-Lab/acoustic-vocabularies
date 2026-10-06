using System;
using System.IO;
using System.Linq;
using AcousticVocab.SessionEngine;
using AcousticVocab.Teaching;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.DataLogging.Tests
{
    public sealed class LessonDataTests
    {
        const string Hash="dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd";
        sealed class Run:IDisposable
        {
            internal readonly string Raw=SyntheticData.Folder("lesson-typed");internal double Now;
            internal readonly SlotContext Context;internal readonly VisitSchedule Schedule;internal readonly DataJournal Data;internal readonly LessonDataJournal Sink;internal readonly LessonTimeline Timeline;
            internal LessonEvent Last;readonly bool atomic;
            internal Run(bool atomic=true,bool bind=true)
            {
                this.atomic=atomic;var item=new SlotItem("DEMO-lesson",atomic?"atomic_lesson":"message_lesson","K-a1",null,"structured","teaching",false,atomic?20:24,3,1);
                Schedule=new VisitSchedule(new string('b',64),new string('a',64),"DEMO","D0",true,new[]{new ScheduleBlock("lessons",new[]{item})});Context=new SlotContext(item,750,null);
                Data=new DataJournal(Raw,SyntheticData.Identity,new string('3',32),()=>Now);Loaded(Data,Context);Sink=new LessonDataJournal(Data,Schedule);if(bind)Sink.Bind(Context);
                Timeline=new LessonTimeline(Context,false,24000,atomic?0:24000,"DEMO-meaning",Hash,atomic?null:Hash,atomic?null:Hash,e=>{Last=e;Sink.Append(e);},()=>Now);
                Timeline.PlayRequested+=(i,id,expected)=>Audio(id,"AUDIO_REQUESTED",expected,false);
            }
            internal static void Loaded(DataJournal data,SlotContext context)
            {
                var p=SessionRecordCodec.ToJson(SyntheticData.Session(context.Item.TrialId,"Loaded",audible:"NotRequested"));
                p["audio_request_ids"]=new JArray(context.AudioRequestIds);p["scheduled_onset_mono_ms"]=context.OnsetMonoMs;
                new SessionDataJournal(data).Append(SessionRecordCodec.FromJson(p));
            }
            void Audio(string id,string code,double expected,bool callback)
            {
                var context=new EventContext(Context.OpportunityId,Context.Item.TrialId,id);var p=SyntheticData.Audio(context,code,callback).Payload;
                p["audio_id"]=id;p["observed_mono_ms"]=Now;p["request_mono_ms"]=expected-750;p["scheduled_mono_ms"]=expected;
                p["action_pcm_sha256"]=atomic?JValue.CreateNull():(JToken)Hash;p["referent_pcm_sha256"]=atomic?JValue.CreateNull():(JToken)Hash;
                p["simulation_test"]=true;p["software_output_estimate_mono_ms"]=expected;p["software_output_uncertainty_ms"]=1;
                Data.Append(new EventDraft(code=="AUDIO_REQUESTED"?"audio_request":"audio_observation",context,p));
            }
            internal void Complete()
            {
                Timeline.Start(0);double[] offsets=atomic?new[]{0d,6000,14000}:new[]{0d,8000,18000};
                for(Now=50;Now<=Context.EndMonoMs;Now+=50)
                {
                    Timeline.Tick(Now);
                    for(int i=0;i<3;i++)
                    {
                        double expected=750+offsets[i];string id=Context.AudioRequestIds[i];
                        if(Now==expected){Audio(id,"SIMULATION_DELIVERY_OBSERVED",expected,true);Timeline.Onset(id,expected,1,Now);}
                        if(Now==expected+1200){Audio(id,"AUDIO_PLAYBACK_COMPLETED",expected,true);Timeline.Completed(id,Now);}
                    }
                    if(Now==750+offsets[1]+100)Timeline.Response("DEMO-feedback",Now);
                }
            }
            public void Dispose()=>Data.Dispose();
        }
        [TestCase(true)][TestCase(false)]public void RealTimelineExportsSixSupplementalRowsWithoutAddingAudioExposures(bool atomic)
        {
            using var r=new Run(atomic);r.Complete();r.Data.Dispose();
            var snapshot=DataJournal.Verify(r.Raw,SyntheticData.Identity);var old=DataDeriver.Derive(snapshot,SyntheticData.Identity);var rows=LessonExport.Derive(snapshot,SyntheticData.Identity,old);
            Assert.That(old.Exposures.Count,Is.EqualTo(3));Assert.That(rows.Count,Is.EqualTo(6));Assert.That(rows.Count(x=>x["row_kind"]=="display"),Is.EqualTo(2));Assert.That(rows.Count(x=>x["retrieval_opportunity"]=="true"),Is.EqualTo(1));
            foreach(var row in rows.Where(x=>x["row_kind"]=="play")){Assert.That(row["audio_ledger_status"],Is.EqualTo("linked"));Assert.That(row["audible_status"],Is.EqualTo("uncertain"));Assert.That(row["exposure_consumed"],Is.EqualTo("true"));Assert.That(row["audio_onset_estimate_mono_ms"],Is.Empty);Assert.That(row["onset_uncertainty_ms"],Is.Empty);}
            Assert.That(rows.All(x=>x["interval_status"]=="software_observed"),Is.True);
            string export=SyntheticData.Folder("lesson-export");var bundle=ExportBundle.Create(r.Raw,export,SyntheticData.Identity,ExportHeaders.Provisional());Assert.That(bundle.HeadersQualified,Is.False);ExportBundle.Load(export,bundle.ManifestSha256).VerifyAll();
            Assert.That(File.ReadLines(Path.Combine(export,"exposure-ledger.csv")).First(),Is.EqualTo(string.Join(",",DataDeriver.ExposureHeaders)));Assert.That(File.ReadLines(Path.Combine(export,LessonExport.Table)).Count(),Is.EqualTo(7));
        }
        [Test]public void AbruptClosePreservesMissingDisplayEndAndRecoveryDoesNotInventIt()
        {
            using var r=new Run();r.Timeline.Start(0);r.Now=750;r.Timeline.Tick(r.Now);r.Data.Dispose();
            using(var reopened=new DataJournal(r.Raw,SyntheticData.Identity,new string('4',32),()=>0))new LessonDataJournal(reopened,r.Schedule);
            var display=LessonExport.Derive(DataJournal.Verify(r.Raw,SyntheticData.Identity),SyntheticData.Identity).Single(x=>x["row_kind"]=="display");
            Assert.That(display["display_start_mono_ms"],Is.EqualTo("750"));Assert.That(display["display_end_mono_ms"],Is.Empty);Assert.That(display["end_event_sha256"],Is.Empty);Assert.That(display["interval_status"],Is.EqualTo("incomplete"));
        }
        [Test]public void MissingAudioRequestRemainsAnIntentNotAnAudibleExposure()
        {
            using var r=new Run();var t=new LessonTimeline(r.Context,false,24000,0,"DEMO-meaning",Hash,null,null,r.Sink.Append);t.Start(0);r.Data.Dispose();var snapshot=DataJournal.Verify(r.Raw,SyntheticData.Identity);
            Assert.That(DataDeriver.Derive(snapshot,SyntheticData.Identity).Exposures,Is.Empty);var row=LessonExport.Derive(snapshot,SyntheticData.Identity).Single();Assert.That(row["audio_ledger_status"],Is.EqualTo("request_missing"));Assert.That(row["audible_status"],Is.Empty);Assert.That(row["observed_end_mono_ms"],Is.Empty);
        }
        [Test]public void FailedDurableSinkStopsTimelineBeforeAudioSchedulingAndLatches()
        {
            using var r=new Run();int plays=0;r.Timeline.PlayRequested+=(_,__,___)=>plays++;r.Data.BeforeDurableFlush=()=>throw new IOException("synthetic");
            Assert.Throws<DataFault>(()=>r.Timeline.Start(0));Assert.That(plays,Is.Zero);Assert.Throws<DataFault>(()=>r.Sink.Append(r.Last));Assert.That(r.Data.Records.Count(x=>x.Kind=="lesson"),Is.Zero);
        }
        [Test]public void DuplicatePresentationIsRejectedBeforeSecondDurableRow()
        {using var r=new Run();r.Timeline.Start(0);Assert.Throws<DataFault>(()=>r.Sink.Append(r.Last));Assert.That(r.Data.Records.Count(x=>x.Kind=="lesson"),Is.EqualTo(1));}
        [Test]public void ActualLoadedContextIsRequiredAndProtectedBindingRejected()
        {
            using var r=new Run(bind:false);Assert.Throws<DataFault>(()=>r.Timeline.Start(0));
            var fresh=new LessonDataJournal(r.Data,r.Schedule);var protectedItem=new SlotItem("DEMO-lesson","atomic_lesson","K-a1",null,null,"protected",false,20,3,1);Assert.Throws<DataFault>(()=>fresh.Bind(new SlotContext(protectedItem,750,null)));
        }
        [TestCase("attempt_id")][TestCase("audio_request_id")][TestCase("presentation_index")][TestCase("pcm_sha256")]
        public void RecoveredTamperedLessonCannotReexport(string field)
        {
            using var r=new Run();r.Complete();r.Data.Dispose();var original=DataJournal.Verify(r.Raw,SyntheticData.Identity).Records.First(x=>x.Kind=="lesson");var p=original.Payload;
            if(field=="presentation_index")p[field]=2;else p[field]=field=="pcm_sha256"?new string('e',64):field=="audio_request_id"?new string('f',32):"other";
            if(field!="pcm_sha256")Assert.Throws<DataFault>(()=>LessonRecordCodec.Validate(p,original.Context));
            else {var trace=new LessonTrace();trace.Accept(original.Payload,original.ToJson());Assert.Throws<DataFault>(()=>trace.Accept(p,original.ToJson()));}
        }
    }
}
