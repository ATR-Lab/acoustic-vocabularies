using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.StudyAudio;
using NUnit.Framework;

namespace AcousticVocab.SessionEngine.Tests
{
    public sealed class SessionEngineTests
    {
        const string Hash="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        const string Package="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
        sealed class Clock : ISessionClock { public double Time; public double NowMs=>Time; }
        sealed class Memory : ISessionJournal
        {
            public readonly List<SessionRecord> Rows=new List<SessionRecord>();
            public Func<SessionRecord,bool> Fail;
            public IReadOnlyList<SessionRecord> Records=>Rows;
            public void Append(SessionRecord value){if(Fail?.Invoke(value)==true)throw new IOException();Rows.Add(value);}
        }
        sealed class Content : ISlotContent
        {
            public readonly bool[] Gates=Enumerable.Repeat(true,8).ToArray();
            public SlotReadiness Readiness=>new SlotReadiness(Gates[0],Gates[1],Gates[2],Gates[3],Gates[4],Gates[5],Gates[6],Gates[7]);
            public bool ResetComplete { get; private set; }
            public bool CompleteReset=true,ThrowCue,InvalidateOnInterrupt;
            public int Cues,Interrupts,Resets;
            public SlotContext Context;
            public INovelSlotAuthorization Permit;
            public void Prepare(SlotContext context){Context=context;}
            public void RequestCue(SlotContext context,INovelSlotAuthorization permit){Cues++;Permit=permit;if(ThrowCue)throw new IOException();}
            public void OpenResponse(SlotContext context){}
            public void CloseResponse(SlotContext context){}
            public void RequestReset(SlotContext context){Resets++;ResetComplete=CompleteReset;}
            public void Interrupt(string code){Interrupts++;if(InvalidateOnInterrupt)for(int i=0;i<Gates.Length;i++)Gates[i]=false;}
        }
        sealed class Factory : ISlotContentFactory
        {
            public readonly List<Content> Items=new List<Content>();
            public Action<Content> Configure;
            public ISlotContent Create(SlotItem item){var value=new Content();Configure?.Invoke(value);Items.Add(value);return value;}
        }
        sealed class Fixture
        {
            public readonly Clock Clock=new Clock();
            public readonly Memory Journal;
            public readonly Factory Factory=new Factory();
            public readonly VisitSchedule Schedule;
            public readonly FixedSlotEngine Engine;
            public Fixture(int count=1,bool heldout=false,int plays=1,string type="trained",Memory journal=null,Action<SlotGateRefusal> diagnostic=null)
            {
                Journal=journal??new Memory();
                var items=Enumerable.Range(0,count).Select(i=>new SlotItem("DEMO-"+i,type,"K-a1-r1",null,null,"protected",heldout,type=="atomic"?9:type=="atomic_lesson"?20:type=="message_lesson"?24:14,plays,1)).ToArray();
                Schedule=new VisitSchedule(Hash,Package,"DEMO","D0",true,new[]{new ScheduleBlock("trained",items)});
                Engine=new FixedSlotEngine(Schedule,Clock,Journal,Factory,diagnostic);
            }
            public void Start(){Engine.ConfirmResume();Engine.Tick();}
            public void At(double time){Clock.Time=time;Engine.Tick();}
            public void Run(double until){while(Clock.Time<until&&Engine.Status==SessionState.Running){Clock.Time+=50;Engine.Tick();}}
        }
        [Test] public void ThirtySixFixedSlotsKeep504SecondsDespiteEarlyResponses()
        {
            var f=new Fixture(36);f.Start();
            while(f.Engine.Status==SessionState.Running)
            {
                f.Clock.Time+=50;f.Engine.Tick();
                if(f.Engine.CurrentState==ItemState.ResponseOpen && !f.Journal.Rows.Any(r=>r.Event=="response"&&r.TrialId==f.Engine.CurrentTrialId))f.Engine.RecordResponse("commit");
            }
            Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Complete));
            Assert.That(f.Clock.Time,Is.EqualTo(504750));
            Assert.That(f.Factory.Items.Select(x=>x.Context.OnsetMonoMs),Is.EqualTo(Enumerable.Range(0,36).Select(x=>750d+14000*x)));
            Assert.That(f.Factory.Items.All(x=>x.Cues==1),Is.True);
        }
        [TestCase(0)][TestCase(1)][TestCase(2)][TestCase(3)][TestCase(4)][TestCase(5)][TestCase(6)][TestCase(7)]
        public void EveryIndependentGateMustPassBeforeCue(int gate)
        {
            var f=new Fixture();f.Factory.Configure=x=>x.Gates[gate]=false;f.Start();f.At(650);
            Assert.That(f.Factory.Items[0].Cues,Is.Zero);Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Paused));
            Assert.That(f.Journal.Rows.Any(x=>x.ExposureConsumed),Is.False);
        }
        [TestCase(0)][TestCase(1)][TestCase(2)][TestCase(3)][TestCase(4)][TestCase(5)][TestCase(6)][TestCase(7)]
        public void CueRefusalCapturesTheEvaluatedGateBeforeInterruptionInvalidatesOtherGates(int gate)
        {
            SlotGateRefusal observed=null;Fixture f=null;
            f=new Fixture(2,diagnostic:x=>{observed=x;Assert.That(f.Factory.Items.Last().Interrupts,Is.Zero);f.Engine.Tick();});
            f.Start();f.At(12750); // prepare next item while previous fixed tail remains
            f.At(13000);Assert.That(f.Engine.CurrentState,Is.EqualTo(ItemState.Ready));
            var second=f.Factory.Items[1];second.Gates[gate]=false;second.InvalidateOnInterrupt=true;
            f.At(14014); // 736 ms before onset, as in the retained native failure
            Assert.That(observed,Is.Not.Null);Assert.That(observed.Code,Is.EqualTo("SESSION_CUE_GATE_REFUSED"));
            var r=observed.Readiness;Assert.That(new[]{r.HashVerified,r.AudioPreloaded,r.ResetAcknowledged,r.RendererReady,r.PanelIdle,r.FocusOk,r.InputOk,r.ModeAcknowledged},Is.EqualTo(Enumerable.Range(0,8).Select(i=>i!=gate)));
            Assert.That(observed.CheckedMonoMs,Is.EqualTo(14014));Assert.That(observed.Context.OnsetMonoMs-observed.CheckedMonoMs,Is.EqualTo(736));Assert.That(observed.MinimumLeadMs,Is.EqualTo(150));
            Assert.That(second.Cues,Is.Zero);Assert.That(second.Interrupts,Is.GreaterThan(0));Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Paused));
            Assert.That(f.Journal.Rows.Where(x=>x.TrialId=="DEMO-1").All(x=>!x.ExposureConsumed),Is.True);
        }
        [TestCase(600,true)][TestCase(600.001,false)]
        public void DiagnosticDoesNotChangeExactMinimumLeadDeadline(double time,bool allowed)
        {
            int calls=0;var f=new Fixture(diagnostic:_=>calls++);f.Engine.ConfirmResume();f.At(time);
            Assert.That(f.Factory.Items[0].Cues,Is.EqualTo(allowed?1:0));Assert.That(calls,Is.EqualTo(allowed?0:1));
        }
        [Test]public void FailedDiagnosticSinkKeepsOriginalCauseAndRefusesFurtherResume()
        {
            var f=new Fixture(diagnostic:_=>throw new IOException("synthetic diagnostic failure"));f.Factory.Configure=x=>x.Gates[2]=false;f.Start();f.At(601);
            Assert.That(f.Engine.DiagnosticFailed,Is.True);Assert.That(f.Engine.PrimaryFaultCode,Is.EqualTo("SESSION_READY_DEADLINE_MISSED"));Assert.That(f.Factory.Items[0].Cues,Is.Zero);
            f.Engine.Fault("CONTROL_UNAVAILABLE");Assert.That(f.Engine.PrimaryFaultCode,Is.EqualTo("SESSION_READY_DEADLINE_MISSED"));
            Assert.That(f.Journal.Rows.Where(x=>x.Event=="item_fault").Select(x=>x.TechnicalFaultCode),Is.EqualTo(new[]{"SESSION_READY_DEADLINE_MISSED","CONTROL_UNAVAILABLE"}));
            Assert.Throws<SessionFault>(()=>f.Engine.ConfirmResume());
        }
        [Test]public void ExplicitSafeResumeStartsANewFaultEpisodeWithoutErasingPriorRows()
        {
            var f=new Fixture();f.Engine.ConfirmResume();f.Engine.Fault("FIRST_FAULT");f.Engine.Fault("SECONDARY_FAULT");Assert.That(f.Engine.PrimaryFaultCode,Is.EqualTo("FIRST_FAULT"));
            f.Engine.ConfirmResume();Assert.That(f.Engine.PrimaryFaultCode,Is.Null);f.Engine.Fault("NEXT_FAULT");Assert.That(f.Engine.PrimaryFaultCode,Is.EqualTo("NEXT_FAULT"));
            Assert.That(f.Journal.Rows.Where(x=>x.Event=="item_fault").Select(x=>x.TechnicalFaultCode),Is.EqualTo(new[]{"FIRST_FAULT","SECONDARY_FAULT","NEXT_FAULT"}));
        }
        [Test] public void DurableUncertainIntentPrecedesCueAndCrashSkipsOpportunity()
        {
            var f=new Fixture(2);f.Start();
            var intent=f.Journal.Rows.Single(x=>x.Event=="state_before"&&x.State==ItemState.CueRequested);
            Assert.That(intent.ExposureConsumed,Is.True);Assert.That(intent.AudibleStatus,Is.EqualTo(AudibleStatus.Uncertain));
            Assert.That(intent.AudioRequestIds.Count,Is.EqualTo(1));Assert.That(intent.OpportunityId,Is.EqualTo("DEMO-0"));
            var resumed=new Fixture(2,journal:f.Journal);Assert.That(resumed.Engine.NeedsOperatorConfirmation,Is.True);
            resumed.Start();Assert.That(resumed.Engine.CurrentTrialId,Is.EqualTo("DEMO-1"));
        }
        [Test] public void CrashBeforeCueAllowsSameUnplayedOpportunityAfterConfirmation()
        {
            var f=new Fixture(2);f.Engine.ConfirmResume();
            var resumed=new Fixture(2,journal:f.Journal);Assert.That(resumed.Factory.Items.Count,Is.Zero);
            resumed.Start();Assert.That(resumed.Engine.CurrentTrialId,Is.EqualTo("DEMO-0"));
        }
        [Test] public void FailedDurableIntentNeverCallsAudio()
        {
            var f=new Fixture();f.Journal.Fail=x=>x.State==ItemState.CueRequested;f.Start();
            Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Faulted));Assert.That(f.Factory.Items[0].Cues,Is.Zero);
        }
        [Test] public void MissingCallbackNeverCreatesRetry()
        {
            var f=new Fixture();f.Start();f.Run(15000);
            Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Complete));Assert.That(f.Factory.Items.Count,Is.EqualTo(1));
            Assert.That(f.Journal.Rows.Any(x=>x.Event=="retry_queued"),Is.False);
        }
        [Test] public void AudioModuleExceptionAfterIntentKeepsConsumedOpportunity()
        {
            var f=new Fixture(2);f.Factory.Configure=x=>x.ThrowCue=true;f.Start();
            Assert.That(f.Factory.Items[0].Cues,Is.EqualTo(1));
            Assert.That(f.Journal.Rows.Last(x=>x.TrialId!=null).ExposureConsumed,Is.True);
            var resumed=new Fixture(2,journal:f.Journal);resumed.Start();
            Assert.That(resumed.Engine.CurrentTrialId,Is.EqualTo("DEMO-1"));
        }
        [Test] public void MenuReservesEightDistinctPlaybackCreditsWithoutUsingThem()
        {
            var f=new Fixture(plays:8,type:"profile_menu");f.Engine.ConfirmResume();
            Assert.That(f.Factory.Items[0].Context.AudioRequestIds.Distinct().Count(),Is.EqualTo(8));
            Assert.That(f.Factory.Items[0].Cues,Is.Zero);
        }
        [Test] public void ProvenNoOnsetQueuesOneBlockEndRetryWithNewAudioIdentity()
        {
            var f=new Fixture(heldout:true);f.Start();f.Engine.ObserveAudible(AudibleStatus.ConfirmedNoOnset,Hash);f.At(12750);
            Assert.That(f.Factory.Items.Count,Is.EqualTo(2));var first=f.Factory.Items[0].Context;var retry=f.Factory.Items[1].Context;
            Assert.That(retry.RetryOf,Is.EqualTo(first.Item.TrialId));Assert.That(retry.OpportunityId,Is.EqualTo(first.OpportunityId));
            Assert.That(retry.Item.TrialId,Is.Not.EqualTo(first.Item.TrialId));Assert.That(retry.AudioRequestIds[0],Is.Not.EqualTo(first.AudioRequestIds[0]));
            f.At(14000);f.Engine.ObserveAudible(AudibleStatus.ConfirmedNoOnset,Hash);f.Run(29000);
            Assert.That(f.Factory.Items.Count,Is.EqualTo(2));Assert.That(f.Journal.Rows.Count(x=>x.Event=="retry_queued"),Is.EqualTo(1));
        }
        [TestCase(AudibleStatus.Uncertain)][TestCase(AudibleStatus.ConfirmedAudible)]
        public void LaterConflictingEvidenceDisablesUnplayedRetryIncludingAfterCrash(AudibleStatus status)
        {
            var f=new Fixture();f.Start();f.Engine.ObserveAudible(AudibleStatus.ConfirmedNoOnset,Hash);f.Engine.ObserveAudible(status,Package);
            var resumed=new Fixture(journal:f.Journal);Assert.That(resumed.Engine.Status,Is.EqualTo(SessionState.Complete));
            f.Run(15000);Assert.That(f.Factory.Items.Count,Is.EqualTo(1));
        }
        [Test] public void ConfirmedAudibleCannotBecomeNoOnset()
        {
            var f=new Fixture();f.Start();f.Engine.ObserveAudible(AudibleStatus.ConfirmedAudible,Hash);
            Assert.Throws<SessionFault>(()=>f.Engine.ObserveAudible(AudibleStatus.ConfirmedNoOnset,Hash));
        }
        [Test] public void MultiPlaySlotCannotUseSingleNoOnsetReceipt()
        {
            var f=new Fixture(plays:3,type:"atomic_lesson");f.Start();Assert.That(f.Factory.Items[0].Context.AudioRequestIds.Distinct().Count(),Is.EqualTo(3));
            Assert.Throws<SessionFault>(()=>f.Engine.ObserveAudible(AudibleStatus.ConfirmedNoOnset,Hash));
        }
        [Test] public void NoCueSlotHasNoAudioIdentityAndNeverConsumesExposure()
        {
            var f=new Fixture(plays:0,type:"no_cue");f.Start();f.Run(15000);
            Assert.That(f.Journal.Rows.All(x=>!x.ExposureConsumed&&x.AudioRequestIds.Count==0),Is.True);
        }
        [Test] public void NovelPermitIsBoundToExactPackageMessageAndOneUse()
        {
            var f=new Fixture(heldout:true);f.Start();var permit=f.Factory.Items[0].Permit;
            Assert.That(permit.TryConsume(Hash,"K-a1-r1"),Is.False);Assert.That(permit.TryConsume(Package,"K-a2-r1"),Is.False);
            Assert.That(permit.TryConsume(Package,"K-a1-r1"),Is.True);Assert.That(permit.TryConsume(Package,"K-a1-r1"),Is.False);
            Assert.That(f.Journal.Rows.Last().Event,Is.EqualTo("novel_buffer_authorized"));
        }
        [Test] public void PauseBeforeCueIsUnplayedAndRequiresExplicitResume()
        {
            var f=new Fixture();f.Engine.ConfirmResume();f.Engine.RequestPause();f.At(1000);
            Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Paused));Assert.That(f.Factory.Items[0].Cues,Is.Zero);
            f.Engine.ConfirmResume();f.Engine.Tick();Assert.That(f.Factory.Items[1].Context.OnsetMonoMs,Is.EqualTo(1750));
        }
        [Test] public void PauseAfterScheduleBeforeOnsetInterruptsButDoesNotInferNoOnset()
        {
            var f=new Fixture();f.Start();f.Engine.RequestPause();f.Engine.Tick();f.Run(15000);
            Assert.That(f.Factory.Items[0].Interrupts,Is.GreaterThan(0));Assert.That(f.Factory.Items[0].Resets,Is.EqualTo(1));
            Assert.That(f.Journal.Rows.Any(x=>x.Event=="retry_queued"),Is.False);
            Assert.That(f.Journal.Rows.Last(x=>x.TrialId!=null).ExposureConsumed,Is.True);
        }
        [Test] public void ResetFailurePausesAtFixedEndWithoutAnotherCue()
        {
            var f=new Fixture(2);f.Factory.Configure=x=>x.CompleteReset=false;f.Start();f.Run(15000);
            Assert.That(f.Clock.Time,Is.EqualTo(14800));Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Paused));
            Assert.That(f.Factory.Items.Count,Is.EqualTo(1));
        }
        [Test] public void ResponseFirstCommitLocksAndDeadlineRejectsLateCommit()
        {
            var f=new Fixture();f.Start();f.At(750);f.Engine.RecordResponse("commit");
            Assert.Throws<SessionFault>(()=>f.Engine.RecordResponse("dont_know"));
            var late=new Fixture();late.Start();late.At(750);late.Clock.Time=12750;
            Assert.Throws<SessionFault>(()=>late.Engine.RecordResponse("commit"));
        }
        [Test] public void ClockRegressionFailsClosed()
        {
            var f=new Fixture();f.Start();f.At(500);f.Clock.Time=499;
            Assert.Throws<SessionFault>(()=>f.Engine.Tick());Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Faulted));
        }
        static SessionRecord Record(double time=1)=>new SessionRecord("operator_resume",new string('a',32),Hash,null,null,0,0,time,null,null,AudibleStatus.NotRequested,false,false,false,null,null);
        [Test] public void PublicRecordCodecRoundtripsWithoutRetainingMutableJson()
        {
            var json=SessionRecordCodec.ToJson(Record());var decoded=SessionRecordCodec.FromJson(json);
            json["host_mono_ms"]=999;
            Assert.That(decoded.MonoMs,Is.EqualTo(1));Assert.That(decoded.Event,Is.EqualTo("operator_resume"));
            Assert.That(SessionRecordCodec.ToJson(decoded)["audio_request_ids"].Count(),Is.Zero);
        }
        [Test] public void PublicRecordCodecRejectsUnknownFieldsWrongTypesAndInvalidExposure()
        {
            var json=SessionRecordCodec.ToJson(Record());json["target"]="A";Assert.Throws<SessionFault>(()=>SessionRecordCodec.FromJson(json));
            json=SessionRecordCodec.ToJson(Record());json["clock_epoch"]=Newtonsoft.Json.Linq.JToken.Parse(new string('1',32));Assert.Throws<SessionFault>(()=>SessionRecordCodec.FromJson(json));
            json=SessionRecordCodec.ToJson(Record());json["exposure_consumed"]=true;Assert.Throws<SessionFault>(()=>SessionRecordCodec.FromJson(json));
            Assert.Throws<SessionFault>(()=>SessionRecordCodec.FromJson(null));Assert.Throws<SessionFault>(()=>SessionRecordCodec.ToJson(null));
        }
        static string Temp()=>Path.Combine(Path.GetTempPath(),"av-session-test-"+Guid.NewGuid().ToString("N"));
        [Test] public void JournalReopensHashChainAndRejectsSameEpochClockRegression()
        {
            string path=Temp();using(var first=new SessionJournal(path)){first.Append(Record());Assert.Throws<SessionFault>(()=>first.Append(Record(0)));}
            using(var next=new SessionJournal(path)){Assert.That(next.Records.Count,Is.EqualTo(1));next.Append(Record(2));}
            using(var third=new SessionJournal(path))Assert.That(third.Records.Count,Is.EqualTo(2));
        }
        [Test] public void TornTailIsPreservedBoundAndReopenableAfterRecovery()
        {
            string path=Temp();using(var first=new SessionJournal(path))first.Append(Record());
            string original=Path.Combine(path,"segment-0000.local.jsonl");File.AppendAllText(original,"{\"torn\":",new UTF8Encoding(false));var bytes=File.ReadAllBytes(original);
            using(var recovered=new SessionJournal(path)){Assert.That(recovered.IgnoredFinalTailSha256,Is.Not.Null);recovered.Append(Record(2));}
            Assert.That(File.ReadAllBytes(original),Is.EqualTo(bytes));
            using(var reopen=new SessionJournal(path))Assert.That(reopen.Records.Count,Is.EqualTo(2));
        }
        [Test] public void CrashDuringRecoveryMarkerCanBeRecoveredWithoutDeletingEitherTail()
        {
            string path=Temp();using(var first=new SessionJournal(path))first.Append(Record());
            File.AppendAllText(Path.Combine(path,"segment-0000.local.jsonl"),"{torn",new UTF8Encoding(false));
            // Simulate a new process that crashed while writing its recovery marker.
            File.WriteAllText(Path.Combine(path,"segment-0001.local.jsonl"),"{partial-marker",new UTF8Encoding(false));
            using(var recovered=new SessionJournal(path))recovered.Append(Record(2));
            using(var reopen=new SessionJournal(path))Assert.That(reopen.Records.Count,Is.EqualTo(2));
        }
        [Test] public void JournalTamperingFailsInsteadOfReconstructingFromCorruptRecords()
        {
            string path=Temp();using(var first=new SessionJournal(path))first.Append(Record());
            string original=Path.Combine(path,"segment-0000.local.jsonl");File.WriteAllText(original,File.ReadAllText(original).Replace("operator_resume","pause_requested"),new UTF8Encoding(false));
            Assert.Throws<SessionFault>(()=>new SessionJournal(path));
        }
    }
}
