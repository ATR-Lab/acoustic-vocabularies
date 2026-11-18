using System;
using System.Collections.Generic;
using System.Linq;
using AcousticVocab.StudyAudio;
using NUnit.Framework;

namespace AcousticVocab.SessionEngine.Tests
{
    // Requirement trace tests for pause, interruption, retry and fault records
    // on the real FixedSlotEngine with synthetic content and an in-memory journal.
    public sealed class RequirementTraceSessionEngineTests
    {
        const string Hash="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        const string Package="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
        const string Evidence="eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee";
        sealed class Clock : ISessionClock { public double Time; public double NowMs=>Time; }
        sealed class Memory : ISessionJournal
        {
            public readonly List<SessionRecord> Rows=new List<SessionRecord>();
            public IReadOnlyList<SessionRecord> Records=>Rows;
            public void Append(SessionRecord value)=>Rows.Add(value);
        }
        sealed class Content : ISlotContent
        {
            public SlotReadiness Readiness=>new SlotReadiness(true,true,true,true,true,true,true,true);
            public bool ResetComplete { get; private set; }
            public bool CompleteReset=true;
            public int Cues,Interrupts;
            public SlotContext Context;
            public void Prepare(SlotContext context){Context=context;}
            public void RequestCue(SlotContext context,INovelSlotAuthorization permit){Cues++;}
            public void OpenResponse(SlotContext context){}
            public void CloseResponse(SlotContext context){}
            public void RequestReset(SlotContext context){ResetComplete=CompleteReset;}
            public void Interrupt(string code){Interrupts++;}
        }
        sealed class Factory : ISlotContentFactory
        {
            public readonly List<Content> Items=new List<Content>();
            public Action<Content> Configure;
            public ISlotContent Create(SlotItem item){var value=new Content();Configure?.Invoke(value);Items.Add(value);return value;}
            public IEnumerable<string> Trials=>Items.Select(x=>x.Context.Item.TrialId);
        }
        sealed class Fixture
        {
            public readonly Clock Clock=new Clock();
            public readonly Memory Journal=new Memory();
            public readonly Factory Factory=new Factory();
            public readonly FixedSlotEngine Engine;
            public Fixture(params string[][] blocks)
            {
                var schedule=new VisitSchedule(Hash,Package,"DEMO","D0",true,blocks.Select((ids,b)=>new ScheduleBlock("block-"+b,
                    ids.Select(id=>new SlotItem(id,"trained","K-a1-r1",null,null,"protected",false,14,1,1)).ToArray())).ToArray());
                Engine=new FixedSlotEngine(schedule,Clock,Journal,Factory);
            }
            public void Start(){Engine.ConfirmResume();Engine.Tick();}
            public void At(double time){Clock.Time=time;Engine.Tick();}
            public void Run(double until){while(Clock.Time<until&&Engine.Status==SessionState.Running){Clock.Time+=50;Engine.Tick();}}
            public IEnumerable<SessionRecord> For(string trial)=>Journal.Rows.Where(r=>r.TrialId==trial);
        }

        [Test, Category("REQ_INT_05"), NUnit.Framework.Property("Requirement","REQ-INT-05")]
        public void PauseAfterOnsetKeepsTheExposureFlagsItAndHoldsTheNextUnplayedTrial()
        {
            var f=new Fixture(new[]{"T0","T1","T2"});f.Start();f.At(2000);
            Assert.That(f.Engine.CurrentState,Is.EqualTo(ItemState.ResponseOpen));Assert.That(f.Factory.Items[0].Cues,Is.EqualTo(1));
            f.Engine.RequestPause();
            var flagged=f.For("T0").Single(r=>r.Event=="pause_requested");
            Assert.That(flagged.TechnicalFaultCode,Is.EqualTo("SESSION_PAUSE_DURING_ITEM"));Assert.That(flagged.ExposureConsumed,Is.True);
            f.Run(30000);
            Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Paused));
            Assert.That(f.Factory.Trials,Is.EqualTo(new[]{"T0"}),"the next unplayed trial is not prepared or cued");
            Assert.That(f.For("T0").Last().State,Is.EqualTo(ItemState.Done));Assert.That(f.For("T0").Last().ExposureConsumed,Is.True);
            Assert.That(f.For("T1"),Is.Empty);Assert.That(f.Journal.Rows.Any(r=>r.Event=="retry_queued"),Is.False);
            f.Engine.ConfirmResume();f.Engine.Tick();
            Assert.That(f.Engine.CurrentTrialId,Is.EqualTo("T1"),"the kept exposure is not replayed after resume");
        }

        [Test, Category("REQ_INT_05"), NUnit.Framework.Property("Requirement","REQ-INT-05")]
        public void PauseBetweenTrialsTakesEffectBeforeTheNextCue()
        {
            var f=new Fixture(new[]{"T0","T1","T2"});f.Start();f.Run(13000);
            Assert.That(f.Engine.CurrentTrialId,Is.EqualTo("T1"));Assert.That(f.Engine.CurrentState,Is.EqualTo(ItemState.Ready));
            f.Engine.RequestPause();f.Run(40000);
            Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Paused));
            Assert.That(f.Factory.Items.Sum(x=>x.Cues),Is.EqualTo(1));Assert.That(f.For("T1").Any(r=>r.State==ItemState.CueRequested),Is.False);
            f.Engine.ConfirmResume();f.Engine.Tick();Assert.That(f.Engine.CurrentTrialId,Is.EqualTo("T1"),"an unplayed trial stays available");
        }

        [Test, Category("REQ_INT_05"), NUnit.Framework.Property("Requirement","REQ-INT-05")]
        public void InterruptionAfterOnsetIsFlaggedAndCountedWithoutReplay()
        {
            var f=new Fixture(new[]{"T0","T1"});f.Start();f.At(1500);
            f.Engine.Fault("AUDIO_FOCUS_LOST");
            var fault=f.For("T0").Single(r=>r.Event=="item_fault");
            Assert.That(fault.TechnicalFaultCode,Is.EqualTo("AUDIO_FOCUS_LOST"));Assert.That(fault.ExposureConsumed,Is.True);
            Assert.That(fault.AudibleStatus,Is.EqualTo(AudibleStatus.Uncertain));
            Assert.That(f.Factory.Items[0].Interrupts,Is.GreaterThan(0));
            f.Run(30000);Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Paused));
            Assert.That(f.For("T0").Last().ExposureConsumed,Is.True);Assert.That(f.Journal.Rows.Any(r=>r.Event=="retry_queued"),Is.False);
            f.Engine.ConfirmResume();f.Engine.Tick();Assert.That(f.Engine.CurrentTrialId,Is.EqualTo("T1"));
        }

        [Test, Category("REQ_INT_06"), NUnit.Framework.Property("Requirement","REQ-INT-06")]
        public void ConfirmedNoOnsetRetryRunsOnceAfterTheRestOfItsBlockLinkedToTheOriginal()
        {
            var f=new Fixture(new[]{"T0","T1","T2"},new[]{"U0"});f.Start();f.At(1000);
            var original=f.Factory.Items[0].Context;
            f.Engine.ObserveAudible(AudibleStatus.ConfirmedNoOnset,Evidence);
            f.Run(60000);
            var trials=f.Factory.Trials.ToArray();
            Assert.That(trials.Length,Is.EqualTo(4));Assert.That(trials.Take(3),Is.EqualTo(new[]{"T0","T1","T2"}));
            var retry=f.Factory.Items[3].Context;
            Assert.That(retry.RetryOf,Is.EqualTo("T0"));Assert.That(retry.OpportunityId,Is.EqualTo(original.OpportunityId));
            Assert.That(retry.Item.TrialId,Is.Not.EqualTo("T0"));Assert.That(retry.AudioRequestIds[0],Is.Not.EqualTo(original.AudioRequestIds[0]));
            Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Paused),"block boundary after the retry");
            Assert.That(f.Journal.Rows.Count(r=>r.Event=="retry_queued"),Is.EqualTo(1));
            var queued=f.Journal.Rows.Single(r=>r.Event=="retry_queued");Assert.That(queued.RetryOf,Is.EqualTo("T0"));
            f.Engine.ConfirmResume();f.Engine.Tick();Assert.That(f.Engine.CurrentTrialId,Is.EqualTo("U0"),"the retry stays in its own block");
            // A second confirmed no-onset on the retry does not grant another attempt.
            var g=new Fixture(new[]{"T0"});g.Start();g.At(1000);g.Engine.ObserveAudible(AudibleStatus.ConfirmedNoOnset,Evidence);g.Run(14800);
            Assert.That(g.Factory.Items.Count,Is.EqualTo(2));g.At(g.Clock.Time+500);g.Engine.ObserveAudible(AudibleStatus.ConfirmedNoOnset,Evidence);g.Run(60000);
            Assert.That(g.Factory.Items.Count,Is.EqualTo(2));Assert.That(g.Journal.Rows.Count(r=>r.Event=="retry_queued"),Is.EqualTo(1));
        }

        [Test, Category("REQ_INT_06"), NUnit.Framework.Property("Requirement","REQ-INT-06")]
        public void UncertainOnsetCountsAsExposureAndIsNeverRetried()
        {
            var f=new Fixture(new[]{"T0","T1"});f.Start();f.At(1000);
            var intent=f.For("T0").Single(r=>r.Event=="state_before"&&r.State==ItemState.CueRequested);
            Assert.That(intent.AudibleStatus,Is.EqualTo(AudibleStatus.Uncertain));Assert.That(intent.ExposureConsumed,Is.True);
            f.Engine.ObserveAudible(AudibleStatus.Uncertain,Evidence);f.Run(60000);
            Assert.That(f.Journal.Rows.Any(r=>r.Event=="retry_queued"),Is.False);
            Assert.That(f.Factory.Trials,Is.EqualTo(new[]{"T0","T1"}));Assert.That(f.For("T0").Last().ExposureConsumed,Is.True);
        }

        [Test, Category("REQ_INT_07"), NUnit.Framework.Property("Requirement","REQ-INT-07")]
        public void FailedResetIsRecordedWithItsOwnFaultCodeAndNoFurtherCue()
        {
            var f=new Fixture(new[]{"T0","T1"});f.Factory.Configure=x=>x.CompleteReset=false;f.Start();f.Run(30000);
            Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Paused));Assert.That(f.Factory.Items.Count,Is.EqualTo(1));
            var done=f.For("T0").Last();Assert.That(done.State,Is.EqualTo(ItemState.Done));
            Assert.That(done.TechnicalFaultCode,Is.EqualTo("SESSION_RESET_DEADLINE_MISSED"));
        }
    }
}
