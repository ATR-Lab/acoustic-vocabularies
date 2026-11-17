using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.DataLogging.Tests
{
    // Requirement trace tests for the durable record: the real session engine
    // writes through SessionDataJournal into one synthetic DataJournal.
    public sealed class RequirementTraceDataLoggingTests
    {
        sealed class Clock : ISessionClock { internal double Time; public double NowMs=>Time; }
        sealed class Factory : ISlotContentFactory
        {
            readonly DataJournal journal;internal readonly HashSet<string> Underrun=new HashSet<string>(StringComparer.Ordinal);internal bool Ready=true;
            internal Factory(DataJournal journal){this.journal=journal;}
            public ISlotContent Create(SlotItem item)=>new Content(this,journal);
            sealed class Content : ISlotContent
            {
                readonly Factory owner;readonly DataJournal journal;internal Content(Factory owner,DataJournal journal){this.owner=owner;this.journal=journal;}
                public SlotReadiness Readiness=>new SlotReadiness(owner.Ready,true,true,true,true,true,true,true);
                public bool ResetComplete{get;private set;}
                public void Prepare(SlotContext c){}
                public void RequestCue(SlotContext c,INovelSlotAuthorization permit)
                {
                    var context=new EventContext(c.OpportunityId,c.Item.TrialId,c.AudioRequestIds[0]);
                    journal.Append(SyntheticData.Audio(context));
                    journal.Append(SyntheticData.Audio(context,"AUDIO_ONSET_ESTIMATED",callback:true,estimated:true));
                    if(owner.Underrun.Contains(c.Item.TrialId)) journal.Append(SyntheticData.Audio(context,"AUDIO_UNDERRUN",callback:true,estimated:true));
                }
                public void OpenResponse(SlotContext c){} public void CloseResponse(SlotContext c){}
                public void RequestReset(SlotContext c){ResetComplete=true;} public void Interrupt(string code){}
            }
        }
        static VisitSchedule Schedule(params string[] ids)=>new VisitSchedule(new string('b',64),new string('c',64),"SYNTHETIC","DEMO",true,
            new[]{new ScheduleBlock("trained",ids.Select(id=>new SlotItem(id,"trained","synthetic-content",null,null,"protected",false,14,1,1)).ToArray())},"A","A",null);
        static IReadOnlyDictionary<string,string> Row(IReadOnlyList<IReadOnlyDictionary<string,string>> rows,string attempt)=>rows.Single(r=>r["attempt_id"]==attempt);

        [Test, Category("REQ_LOG_01"), NUnit.Framework.Property("Requirement","REQ-LOG-01")]
        public void FaultedAndAnsweredOpportunitiesKeepAudioIdHashOnsetsFaultAndResponse()
        {
            string raw=SyntheticData.Folder("trace-log-01");var clock=new Clock();
            using(var writer=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>clock.Time))
            {
                var factory=new Factory(writer);factory.Underrun.Add("SYNTHETIC-0");
                var engine=new FixedSlotEngine(Schedule("SYNTHETIC-0","SYNTHETIC-1"),clock,new SessionDataJournal(writer),factory);
                engine.ConfirmResume();engine.Tick();bool faulted=false,answered=false;
                while(engine.Status!=SessionState.Complete && clock.Time<60000)
                {
                    clock.Time+=50;
                    if(engine.Status==SessionState.Paused){engine.ConfirmResume();continue;}
                    engine.Tick();
                    if(engine.CurrentState==ItemState.ResponseOpen && engine.CurrentTrialId=="SYNTHETIC-0" && !faulted){faulted=true;engine.Fault("AUDIO_UNDERRUN");}
                    if(engine.CurrentState==ItemState.ResponseOpen && engine.CurrentTrialId=="SYNTHETIC-1" && !answered)
                    {answered=true;writer.Append(SyntheticData.Panel(new EventContext("SYNTHETIC-1","SYNTHETIC-1"),"commit"));engine.RecordResponse("commit");}
                }
                Assert.That(engine.Status,Is.EqualTo(SessionState.Complete));Assert.That(faulted&&answered,Is.True);
            }
            var tables=DataDeriver.Derive(DataJournal.Verify(raw,SyntheticData.Identity),SyntheticData.Identity);
            Assert.That(tables.Trials.Count,Is.EqualTo(2));Assert.That(tables.Exposures.Count,Is.EqualTo(2));
            var faultedTrial=Row(tables.Trials,"SYNTHETIC-0");var faultedPlay=Row(tables.Exposures,"SYNTHETIC-0");
            Assert.That(faultedTrial["technical_fault_code"].Split(';'),Does.Contain("AUDIO_UNDERRUN"));
            Assert.That(faultedTrial["exposure_consumed"],Is.EqualTo("true"));Assert.That(faultedTrial["interrupted"],Is.EqualTo("false"));
            Assert.That(faultedPlay["technical_fault_code"].Split(';'),Does.Contain("AUDIO_UNDERRUN"));Assert.That(faultedPlay["playback_status"],Is.EqualTo("uncertain"));
            foreach(var attempt in new[]{"SYNTHETIC-0","SYNTHETIC-1"})
            {
                var play=Row(tables.Exposures,attempt);var trial=Row(tables.Trials,attempt);
                Assert.That(play["opportunity_id"],Is.EqualTo(attempt));Assert.That(play["audio_request_id"],Does.Match("^[0-9a-f]{32}$"));
                Assert.That(play["audio_id"],Is.EqualTo("synthetic-silence"));Assert.That(play["waveform_sha256"],Is.EqualTo(new string('c',64)));
                Assert.That(play["audio_request_mono_ms"],Is.EqualTo("800"));Assert.That(play["scheduled_onset_mono_ms"],Is.EqualTo("1000"));
                Assert.That(play["audio_onset_estimate_mono_ms"],Is.EqualTo("1000"));Assert.That(play["onset_uncertainty_ms"],Is.EqualTo("5"));
                Assert.That(trial["waveform_sha256"],Is.EqualTo(new string('c',64)));Assert.That(trial["audio_request_ids"],Does.Contain(play["audio_request_id"]));
            }
            var answeredTrial=Row(tables.Trials,"SYNTHETIC-1");
            Assert.That(answeredTrial["response_code"],Is.EqualTo("commit"));Assert.That(answeredTrial["response_target"],Is.EqualTo("A"));Assert.That(answeredTrial["response_action"],Is.EqualTo("ADD_ONE"));
            Assert.That(answeredTrial["technical_fault_code"],Is.Empty);
        }

        [Test, Category("REQ_LOG_01"), NUnit.Framework.Property("Requirement","REQ-LOG-01")]
        public void OpportunityRefusedBeforeAnyAudioIsStillLoggedWithItsFault()
        {
            string raw=SyntheticData.Folder("trace-log-01-refused");var clock=new Clock();
            using(var writer=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>clock.Time))
            {
                var factory=new Factory(writer){Ready=false};
                var engine=new FixedSlotEngine(Schedule("SYNTHETIC-0"),clock,new SessionDataJournal(writer),factory);
                engine.ConfirmResume();while(engine.Status==SessionState.Running && clock.Time<2000){engine.Tick();clock.Time+=50;}
                Assert.That(engine.Status,Is.EqualTo(SessionState.Paused));Assert.That(engine.PrimaryFaultCode,Is.EqualTo("SESSION_READY_DEADLINE_MISSED"));
            }
            var tables=DataDeriver.Derive(DataJournal.Verify(raw,SyntheticData.Identity),SyntheticData.Identity);
            Assert.That(tables.Exposures,Is.Empty);var trial=tables.Trials.Single();
            Assert.That(trial["opportunity_id"],Is.EqualTo("SYNTHETIC-0"));Assert.That(trial["technical_fault_code"].Split(';'),Does.Contain("SESSION_READY_DEADLINE_MISSED"));
            Assert.That(trial["exposure_consumed"],Is.EqualTo("false"));Assert.That(trial["playback_status"],Is.EqualTo("not_requested"));
        }

        [Test, Category("REQ_LOG_03"), NUnit.Framework.Property("Requirement","REQ-LOG-03")]
        public void ReopenedJournalAppendsANewSegmentAndNeverRewritesEarlierBytes()
        {
            string raw=SyntheticData.Folder("trace-append-only");double now=0;
            using(var w=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>now++)) for(int i=0;i<3;i++) w.Append(DataObservations.Device(null,"focus",i,true));
            string first=Directory.GetFiles(raw).Single();byte[] firstBytes=File.ReadAllBytes(first);
            using(var w=new DataJournal(raw,SyntheticData.Identity,new string('4',32),()=>now++)) for(int i=0;i<2;i++) w.Append(DataObservations.Device(null,"focus",i,true));
            Assert.That(File.ReadAllBytes(first),Is.EqualTo(firstBytes));
            Assert.That(Directory.GetFiles(raw).Select(x=>Path.GetFileName(x)).OrderBy(x=>x,StringComparer.Ordinal),Is.EqualTo(new[]{"events-0000.local.jsonl","events-0001.local.jsonl"}));
            var records=DataJournal.Verify(raw,SyntheticData.Identity).Records;
            Assert.That(records.Select(r=>r.Sequence),Is.EqualTo(new long[]{0,1,2,3,4}));
            string previous=new string('0',64);
            foreach(var record in records)
            {
                var row=record.ToJson();Assert.That((string)row["previous_sha256"],Is.EqualTo(previous));
                row.Remove("sha256");Assert.That(DataJson.HashBytes(DataJson.Bytes(row)),Is.EqualTo(record.Sha256));previous=record.Sha256;
            }
        }

        [TestCase("remove")][TestCase("swap")][TestCase("edit")]
        [Category("REQ_LOG_03"), NUnit.Framework.Property("Requirement","REQ-LOG-03")]
        public void RemovingReorderingOrEditingACompleteRecordBreaksTheChain(string change)
        {
            string raw=SyntheticData.Folder("trace-chain-"+change);double now=0;
            using(var w=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>now++)) for(int i=0;i<3;i++) w.Append(DataObservations.Device(null,"focus",i,true));
            string file=Directory.GetFiles(raw).Single();var lines=File.ReadAllText(file).Split('\n').Where(x=>x.Length>0).ToList();
            Assert.That(DataJournal.Verify(raw,SyntheticData.Identity).Records.Count,Is.EqualTo(3),"positive control");
            if(change=="remove") lines.RemoveAt(1);
            else if(change=="swap") { var a=lines[1];lines[1]=lines[2];lines[2]=a; }
            else { string edited=lines[1].Replace("\"value\":true","\"value\":false");Assert.That(edited,Is.Not.EqualTo(lines[1]));lines[1]=edited; }
            File.WriteAllText(file,string.Join("\n",lines)+"\n",new UTF8Encoding(false));
            Assert.Throws<DataFault>(()=>DataJournal.Verify(raw,SyntheticData.Identity));
            Assert.Throws<DataFault>(()=>new DataJournal(raw,SyntheticData.Identity,new string('4',32),()=>now++));
        }

        [Test, Category("REQ_LOG_03"), NUnit.Framework.Property("Requirement","REQ-LOG-03")]
        public void ExportHashesEveryRawSegmentAndDerivesTablesOnlyFromThoseRecords()
        {
            string raw=SyntheticData.Folder("trace-export-raw");
            using(var writer=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>1)){SyntheticData.Trial(writer,"synthetic-a");SyntheticData.Trial(writer,"synthetic-b",response:"dont_know");}
            string output=SyntheticData.Folder("trace-export");var bundle=ExportBundle.Create(raw,output,SyntheticData.Identity,ExportHeaders.Provisional());
            foreach(string segment in Directory.GetFiles(raw))
            {
                var entry=bundle.Files.Single(f=>f.RelativePath=="raw/"+Path.GetFileName(segment));
                Assert.That(entry.Sha256,Is.EqualTo(DataJson.HashBytes(File.ReadAllBytes(segment))));
                Assert.That(File.ReadAllBytes(Path.Combine(output,"raw",Path.GetFileName(segment))),Is.EqualTo(File.ReadAllBytes(segment)));
            }
            var manifest=JObject.Parse(File.ReadAllText(Path.Combine(output,"manifest.json")));
            var snapshot=DataJournal.Verify(raw,SyntheticData.Identity);
            Assert.That((long)manifest["record_count"],Is.EqualTo(snapshot.Records.Count));Assert.That((string)manifest["last_record_sha256"],Is.EqualTo(snapshot.Records.Last().Sha256));
            // Re-deriving from the exported raw copy alone reproduces the derived tables byte for byte.
            string again=SyntheticData.Folder("trace-export-again");ExportBundle.Create(Path.Combine(output,"raw"),again,SyntheticData.Identity,ExportHeaders.Provisional());
            foreach(string table in new[]{"trial-log.csv","exposure-ledger.csv"})
                Assert.That(File.ReadAllBytes(Path.Combine(again,table)),Is.EqualTo(File.ReadAllBytes(Path.Combine(output,table))),table);
            var trials=File.ReadAllLines(Path.Combine(output,"trial-log.csv"));Assert.That(trials.Length,Is.EqualTo(3));
            Assert.That(trials[0],Is.EqualTo(string.Join(",",DataDeriver.TrialHeaders)));
        }
    }
}
