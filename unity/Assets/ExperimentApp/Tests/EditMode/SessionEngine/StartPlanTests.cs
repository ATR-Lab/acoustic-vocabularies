using System;
using System.Collections.Generic;
using System.Linq;
using AcousticVocab.StudyAudio;
using NUnit.Framework;

namespace AcousticVocab.SessionEngine.Tests
{
    public sealed class StartPlanTests
    {
        sealed class Clock:ISessionClock{public double Time;public double NowMs=>Time;}
        sealed class Log:ISessionJournal{public readonly List<SessionRecord> Rows=new List<SessionRecord>();public IReadOnlyList<SessionRecord> Records=>Rows;public void Append(SessionRecord row)=>Rows.Add(row);}
        sealed class Content:ISlotContent
        {
            internal SlotContext Context;internal int Cues;
            public SlotReadiness Readiness=>new SlotReadiness(true,true,true,true,true,true,true,true);
            public bool ResetComplete{get;private set;}
            public void Prepare(SlotContext value){Context=value;}
            public void RequestCue(SlotContext value,INovelSlotAuthorization permit){Cues++;}
            public void OpenResponse(SlotContext value){}public void CloseResponse(SlotContext value){}
            public void RequestReset(SlotContext value){ResetComplete=true;}public void Interrupt(string code){}
        }
        class Factory:ISlotContentFactory
        {internal readonly List<Content> Values=new List<Content>();public ISlotContent Create(SlotItem item){var content=new Content();Values.Add(content);return content;}}
        sealed class Planned:Factory,ISlotStartPlan
        {internal Func<SlotItem,double,double> Plan;public double MinimumGapBeforeMs(SlotItem item,double baseline)=>Plan(item,baseline);}
        static FixedSlotEngine Make(Clock clock,Log log,Factory factory,int count=2)
        {var items=Enumerable.Range(0,count).Select(x=>new SlotItem("DEMO-"+x,"trained","K-a1-r1",null,null,"protected",false,14,1,1)).ToArray();return new FixedSlotEngine(new VisitSchedule(new string('a',64),new string('b',64),"DEMO","D0",true,new[]{new ScheduleBlock("trained",items)}),clock,log,factory);}
        [Test]public void DefaultZeroKeepsExistingContiguousTiming()
        {var c=new Clock();var log=new Log();var f=new Factory();var e=Make(c,log,f);e.ConfirmResume();for(;c.Time<=28750;c.Time+=50)e.Tick();Assert.That(f.Values.Select(x=>x.Context.OnsetMonoMs),Is.EqualTo(new[]{750d,14750}));Assert.That(e.Status,Is.EqualTo(SessionState.Complete));}
        [Test]public void AnchoredLedgerGapPreservesTotalDurationAndRecordedOnset()
        {
            var c=new Clock();var log=new Log();var f=new Planned{Plan=(item,baseline)=>Math.Max(0,(item.TrialId=="DEMO-0"?750:19750)-baseline)};var e=Make(c,log,f);e.ConfirmResume();for(;c.Time<=33750;c.Time+=50)e.Tick();
            Assert.That(f.Values.Select(x=>x.Context.OnsetMonoMs),Is.EqualTo(new[]{750d,19750}));Assert.That(e.Status,Is.EqualTo(SessionState.Complete));Assert.That(log.Rows.Where(x=>x.Event=="state_after"&&x.State==ItemState.Loaded).Select(x=>x.ScheduledOnsetMonoMs),Is.EqualTo(new double?[]{750,19750}));
        }
        [TestCase(-1d)][TestCase(double.NaN)][TestCase(double.PositiveInfinity)][TestCase(double.NegativeInfinity)][TestCase(double.MaxValue)]
        public void InvalidGapFaultsBeforeContextContentOrCue(double gap)
        {var c=new Clock();var log=new Log();var f=new Planned{Plan=(_,__)=>gap};var e=Make(c,log,f);e.ConfirmResume();Assert.That(e.Status,Is.EqualTo(SessionState.Paused));Assert.That(f.Values,Is.Empty);Assert.That(log.Rows.Any(x=>x.ExposureConsumed),Is.False);Assert.That(log.Rows.Single(x=>x.Event=="item_fault").TechnicalFaultCode,Is.EqualTo("SESSION_START_GAP_INVALID"));}
        [Test]public void MissedSecondAnchorLeavesCompletedHistoryAndUpcomingOpportunityUntouched()
        {
            var c=new Clock();var log=new Log();var f=new Planned{Plan=(item,baseline)=>item.TrialId=="DEMO-0"?0:throw new SessionFault("MENU_REPLAY_ANCHOR_MISSED")};var e=Make(c,log,f);
            e.ConfirmResume();for(;c.Time<=13000;c.Time+=50)e.Tick();
            Assert.That(e.Status,Is.EqualTo(SessionState.Paused));Assert.That(e.CompletedOpportunities,Is.EqualTo(1));Assert.That(e.CurrentTrialId,Is.Null);Assert.That(e.ExposureConsumed,Is.False);
            Assert.That(f.Values.Count,Is.EqualTo(1));Assert.That(f.Values[0].Cues,Is.EqualTo(1));
            var prior=log.Rows.Where(x=>x.TrialId=="DEMO-0").ToArray();Assert.That(prior.Last().State,Is.EqualTo(ItemState.Done));Assert.That(prior.Last().ExposureConsumed,Is.True);
            var failure=log.Rows.Single(x=>x.Event=="item_fault");Assert.That(failure.TrialId,Is.Null);Assert.That(failure.ScheduledOnsetMonoMs,Is.Null);Assert.That(failure.State,Is.Null);Assert.That(failure.ExposureConsumed,Is.False);
            Assert.That(log.Rows.Any(x=>x.TrialId=="DEMO-1"),Is.False);
            var resumed=new Factory();var recovered=Make(c,log,resumed);Assert.That(recovered.CompletedOpportunities,Is.EqualTo(1));recovered.ConfirmResume();Assert.That(resumed.Values.Single().Context.Item.TrialId,Is.EqualTo("DEMO-1"));
        }
        [TestCase(false)][TestCase(true)]public void MenusResetInNeutralTailAndKeepFollowingFixedStart(bool profile)
        {
            var clock=new Clock();var log=new Log();var factory=new Factory();int seconds=profile?60:45;
            var items=Enumerable.Range(0,2).Select(i=>new SlotItem("DEMO-menu-"+i,profile?"profile_menu":"atom_menu",profile?null:"K-a1",null,"selection","selection",false,seconds,8,1)).ToArray();
            var engine=new FixedSlotEngine(new VisitSchedule(new string('a',64),new string('b',64),"DEMO","V1",true,new[]{new ScheduleBlock("menus",items)}),clock,log,factory);engine.ConfirmResume();
            for(;clock.Time<750+seconds*1000;clock.Time+=50)engine.Tick();
            Assert.That(factory.Values.Count,Is.EqualTo(2));Assert.That(factory.Values[1].Context.OnsetMonoMs,Is.EqualTo(750+seconds*1000));Assert.That(factory.Values.All(x=>x.Context.AudioRequestIds.Count==8),Is.True);
            Assert.That(log.Rows.First(x=>x.TrialId=="DEMO-menu-0"&&x.State==ItemState.Closed).MonoMs,Is.EqualTo(750+(profile?58000:40000)));
            Assert.That(log.Rows.First(x=>x.TrialId=="DEMO-menu-1"&&x.State==ItemState.CueRequested).MonoMs,Is.GreaterThanOrEqualTo(seconds*1000));
            Assert.That(engine.Status,Is.EqualTo(SessionState.Running));Assert.That(log.Rows.Any(x=>x.Event=="boundary_late"),Is.False);
        }
        [Test]public void ResumeUsesOriginalAnchorInsteadOfAddingPauseAgain()
        {var c=new Clock();var log=new Log();var f=new Planned{Plan=(_,baseline)=>Math.Max(0,10750-baseline)};var e=Make(c,log,f,1);e.ConfirmResume();e.RequestPause();c.Time=3000;e.ConfirmResume();Assert.That(f.Values.Select(x=>x.Context.OnsetMonoMs),Is.EqualTo(new[]{10750d,10750}));Assert.That(f.Values.All(x=>x.Cues==0),Is.True);}
        [Test]public void MissedAnchorRequiresExplicitReconstructionAndNeverConsumesCue()
        {
            var c=new Clock{Time=20000};var log=new Log();double anchor=10750;var f=new Planned{Plan=(_,baseline)=>{if(baseline>anchor)throw new SessionFault("MENU_REPLAY_ANCHOR_MISSED");return anchor-baseline;}};var e=Make(c,log,f,1);
            e.ConfirmResume();Assert.That(e.Status,Is.EqualTo(SessionState.Paused));Assert.That(f.Values,Is.Empty);Assert.That(log.Rows.Any(x=>x.ExposureConsumed),Is.False);
            // Represents a separately reviewed reconstruction, not auto retry.
            anchor=25750;e.ConfirmResume();Assert.That(f.Values.Single().Context.OnsetMonoMs,Is.EqualTo(25750));Assert.That(f.Values.Single().Cues,Is.Zero);
        }
    }
}
