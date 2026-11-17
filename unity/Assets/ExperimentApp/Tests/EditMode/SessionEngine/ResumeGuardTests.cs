using System;
using System.Collections.Generic;
using System.Linq;
using AcousticVocab.StudyAudio;
using NUnit.Framework;

namespace AcousticVocab.SessionEngine.Tests
{
    // Interrupted-battery resumption (#278). Synthetic DEMO schedules only.
    public sealed class ResumeGuardTests
    {
        const string Hash="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        const string Package="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
        const string Session="0123456789abcdef0123456789abcdef";
        const string OtherSession="fedcba9876543210fedcba9876543210";
        static readonly TimeSpan Offset=TimeSpan.FromHours(-5);
        static DateTimeOffset Day(int day,int hour=10)=>new DateTimeOffset(2027,1,day,hour,0,0,Offset);

        sealed class Clock:ISessionClock{public double Time;public double NowMs=>Time;}
        sealed class Memory:ISessionJournal
        {
            public readonly List<SessionRecord> Rows=new List<SessionRecord>();
            public IReadOnlyList<SessionRecord> Records=>Rows;
            public void Append(SessionRecord record)=>Rows.Add(record);
        }
        sealed class Content:ISlotContent
        {
            public SlotContext Context;public int Cues;
            public SlotReadiness Readiness=>new SlotReadiness(true,true,true,true,true,true,true,true);
            public bool ResetComplete{get;private set;}
            public void Prepare(SlotContext context){Context=context;}
            public void RequestCue(SlotContext context,INovelSlotAuthorization permit){Cues++;}
            public void OpenResponse(SlotContext context){}
            public void CloseResponse(SlotContext context){}
            public void RequestReset(SlotContext context){ResetComplete=true;}
            public void Interrupt(string code){}
        }
        sealed class Factory:ISlotContentFactory
        {
            public readonly List<Content> Items=new List<Content>();
            public ISlotContent Create(SlotItem item){var value=new Content();Items.Add(value);return value;}
        }
        sealed class Record:IResumeRecord
        {
            public ResumeAnchor Anchor{get;set;}
            public readonly List<string> Closures=new List<string>();
            public string ClosedCode=>Closures.FirstOrDefault();
            public void RecordAnchor(ResumeAnchor anchor){Assert.That(Anchor,Is.Null);Anchor=anchor;}
            public void RecordClosed(string code)=>Closures.Add(code);
        }
        // One host process: an engine recovered from the shared journal and a
        // guard over the shared durable resume record.
        sealed class Sitting
        {
            public readonly Clock Clock=new Clock();public readonly Factory Factory=new Factory();
            public readonly FixedSlotEngine Engine;public readonly ResumeGuard Guard;public DateTimeOffset Now;
            readonly Memory journal;
            public Sitting(VisitSchedule schedule,Memory journal,Record record,ResumePolicy policy,DateTimeOffset now)
            {this.journal=journal;Now=now;Engine=new FixedSlotEngine(schedule,Clock,journal,Factory);Guard=new ResumeGuard(policy,record,()=>Now);}
            public string Admit()=>Guard.Admit(Engine,journal.Records);
            public void Resume(){Assert.That(Admit(),Is.Null);Engine.ConfirmResume();}
            public void Until(double time){while(Clock.Time<time&&Engine.Status==SessionState.Running){Clock.Time+=50;Engine.Tick();}}
            public void ToEnd()
            {
                for(int guard=0;guard<100000&&Engine.Status!=SessionState.Complete;guard++)
                {
                    if(Engine.NeedsOperatorConfirmation)Resume();
                    Clock.Time+=50;Engine.Tick();
                    Assert.That(Engine.Status,Is.Not.EqualTo(SessionState.Faulted));
                }
                Assert.That(Engine.Status,Is.EqualTo(SessionState.Complete));
            }
        }
        static VisitSchedule Visit(string study,string visit,int count=3)
        {
            var items=Enumerable.Range(1,count).Select(i=>new SlotItem("DEMO-TR-0"+i,"trained","K-a1-r1",null,null,"protected",false,14,1,1)).ToArray();
            return new VisitSchedule(Hash,Package,"DEMO-person",visit,true,new[]{new ScheduleBlock("trained",items)},study,"pilot",null);
        }
        // Start the battery, play the first trial and commit its response, then
        // lose the host before that trial's fixed end.
        static Memory Interrupted(VisitSchedule schedule,Record record,ResumePolicy policy,DateTimeOffset when)
        {
            var journal=new Memory();var first=new Sitting(schedule,journal,record,policy,when);
            first.Resume();first.Until(800);
            Assert.That(first.Engine.CurrentState,Is.EqualTo(ItemState.ResponseOpen));
            first.Engine.RecordResponse("commit");
            Assert.That(first.Factory.Items.Single().Cues,Is.EqualTo(1));
            return journal;
        }
        static void AssertClosed(Sitting sitting,Record record,Memory journal,int rowsBefore,string code)
        {
            Assert.That(sitting.Engine.Status,Is.EqualTo(SessionState.Stopped));
            Assert.That(sitting.Factory.Items,Is.Empty,"no trial is prepared, replayed or re-run");
            Assert.That(record.Closures,Is.EqualTo(new[]{code}));
            Assert.That(journal.Rows.Count,Is.EqualTo(rowsBefore+1));
            Assert.That(journal.Rows.Last().Event,Is.EqualTo("session_stopped"));
            Assert.That(journal.Rows.Count(x=>x.Event=="response"&&x.ResponseCode=="commit"&&x.TrialId=="DEMO-TR-01"),Is.EqualTo(1),"committed response is kept");
        }

        [Test,Category("REQ_INT_02")]
        public void ImmediateTestCannotResumeOnALaterDay()
        {
            var schedule=Visit("A","D0");var record=new Record();var policy=new ResumePolicy("A","D0",Session,null);
            var journal=Interrupted(schedule,record,policy,Day(4,16));
            Assert.That(record.Anchor.SessionId,Is.EqualTo(Session));Assert.That(record.Anchor.LocalDate,Is.EqualTo("2027-01-04"));
            int rows=journal.Rows.Count;
            var later=new Sitting(schedule,journal,record,policy,Day(5,9));
            Assert.That(later.Engine.NeedsOperatorConfirmation,Is.True);
            Assert.That(later.Admit(),Is.EqualTo(ResumePolicy.OutsideSession));
            AssertClosed(later,record,journal,rows,ResumePolicy.OutsideSession);
        }

        [Test,Category("REQ_INT_02")]
        public void ImmediateTestCannotResumeInANewVisitSession()
        {
            var schedule=Visit("A","D0");var record=new Record();
            var journal=Interrupted(schedule,record,new ResumePolicy("A","D0",Session,null),Day(4,16));int rows=journal.Rows.Count;
            var appointment=new Sitting(schedule,journal,record,new ResumePolicy("A","D0",OtherSession,null),Day(4,17));
            Assert.That(appointment.Admit(),Is.EqualTo(ResumePolicy.OutsideSession));
            AssertClosed(appointment,record,journal,rows,ResumePolicy.OutsideSession);
        }

        [Test,Category("REQ_INT_01"),Category("REQ_INT_02")]
        public void ImmediateTestResumesInTheSameSessionWithUnplayedTrialsOnly()
        {
            var schedule=Visit("A","D0");var record=new Record();var policy=new ResumePolicy("A","D0",Session,null);
            var journal=Interrupted(schedule,record,policy,Day(4,16));
            var same=new Sitting(schedule,journal,record,policy,Day(4,17));
            same.Resume();
            Assert.That(same.Engine.CurrentTrialId,Is.EqualTo("DEMO-TR-02"),"the played and committed trial is not offered again");
            same.ToEnd();
            Assert.That(same.Factory.Items.Select(x=>x.Context.Item.TrialId),Is.EqualTo(new[]{"DEMO-TR-02","DEMO-TR-03"}));
            Assert.That(same.Factory.Items.All(x=>x.Cues==1),Is.True);
            Assert.That(record.Closures,Is.Empty);
            Assert.That(journal.Rows.Count(x=>x.Event=="response"),Is.EqualTo(1));
        }

        [Test,Category("REQ_INT_03")]
        public void DelayedVisitResumesOnlyThroughItsWindowEnd()
        {
            var schedule=Visit("B","W1");var record=new Record();var policy=new ResumePolicy("B","W1",Session,"2027-01-08");
            var journal=Interrupted(schedule,record,policy,Day(7,16));
            // A later day inside the window may continue with unplayed trials.
            var inside=new Sitting(schedule,journal,record,policy,Day(8,23));
            inside.Resume();Assert.That(inside.Engine.CurrentTrialId,Is.EqualTo("DEMO-TR-02"));
            inside.Until(inside.Clock.Time+800);Assert.That(inside.Factory.Items.Single().Cues,Is.EqualTo(1));
            int rows=journal.Rows.Count;
            var late=new Sitting(schedule,journal,record,policy,Day(9,0));
            Assert.That(late.Admit(),Is.EqualTo(ResumePolicy.OutsideWindow));
            Assert.That(late.Engine.Status,Is.EqualTo(SessionState.Stopped));Assert.That(late.Factory.Items,Is.Empty);
            Assert.That(record.Closures,Is.EqualTo(new[]{ResumePolicy.OutsideWindow}));Assert.That(journal.Rows.Count,Is.EqualTo(rows+1));
        }

        [Test,Category("REQ_INT_03")]
        public void DelayedVisitWithoutAWindowEndContinuesOnlyOnItsStartDay()
        {
            var schedule=Visit("A","D7");var record=new Record();var policy=new ResumePolicy("A","D7",Session,null);
            var journal=Interrupted(schedule,record,policy,Day(11,9));
            var sameDay=new Sitting(schedule,journal,record,policy,Day(11,18));
            Assert.That(sameDay.Admit(),Is.Null);Assert.That(sameDay.Engine.Status,Is.EqualTo(SessionState.AwaitingOperator));
            int rows=journal.Rows.Count;
            var nextDay=new Sitting(schedule,journal,record,policy,Day(12,9));
            Assert.That(nextDay.Admit(),Is.EqualTo(ResumePolicy.OutsideWindow));
            Assert.That(nextDay.Engine.Status,Is.EqualTo(SessionState.Stopped));Assert.That(journal.Rows.Count,Is.EqualTo(rows+1));
        }

        [Test,Category("REQ_INT_03")]
        public void LateDelayedVisitContinuesOnlyOnItsOwnStartDay()
        {
            // Started after its window end (an operator-logged window deviation).
            var schedule=Visit("A","D7");var record=new Record();var policy=new ResumePolicy("A","D7",Session,"2027-01-08");
            var journal=Interrupted(schedule,record,policy,Day(10,9));
            Assert.That(new Sitting(schedule,journal,record,policy,Day(10,15)).Admit(),Is.Null);
            Assert.That(new Sitting(schedule,journal,record,policy,Day(11,9)).Admit(),Is.EqualTo(ResumePolicy.OutsideWindow));
        }

        [Test,Category("REQ_INT_03")]
        public void StudyBDayZeroUsesItsOwnDayAsItsWindow()
        {
            var schedule=Visit("B","V1");var record=new Record();var policy=new ResumePolicy("B","V1",Session,null);
            var journal=Interrupted(schedule,record,policy,Day(4,23));
            Assert.That(new Sitting(schedule,journal,record,policy,Day(4,23)).Admit(),Is.Null);
            int rows=journal.Rows.Count;
            var later=new Sitting(schedule,journal,record,policy,Day(5,0));
            Assert.That(later.Admit(),Is.EqualTo(ResumePolicy.OutsideWindow));
            AssertClosed(later,record,journal,rows,ResumePolicy.OutsideWindow);
        }

        [Test,Category("REQ_INT_04")]
        public void RefusedBatteryStaysClosedAndKeepsItsCommittedRecords()
        {
            var schedule=Visit("A","D0");var record=new Record();var policy=new ResumePolicy("A","D0",Session,null);
            var journal=Interrupted(schedule,record,policy,Day(4,16));
            var before=journal.Rows.ToArray();
            var refused=new Sitting(schedule,journal,record,policy,Day(5,9));
            Assert.That(refused.Admit(),Is.EqualTo(ResumePolicy.OutsideSession));
            // The closure is final: even the original day and session cannot reopen it.
            var again=new Sitting(schedule,journal,record,policy,Day(4,17));
            Assert.That(again.Engine.NeedsOperatorConfirmation,Is.True);
            Assert.That(again.Admit(),Is.EqualTo(ResumePolicy.OutsideSession));
            Assert.That(again.Engine.Status,Is.EqualTo(SessionState.Stopped));Assert.That(again.Factory.Items,Is.Empty);
            Assert.That(record.Closures.Count,Is.EqualTo(1),"the refusal is recorded once");
            Assert.That(journal.Rows.Take(before.Length),Is.EqualTo(before),"no committed or played record is rewritten");
            Assert.That(journal.Rows.Skip(before.Length).Select(x=>x.Event),Is.EqualTo(new[]{"session_stopped","session_stopped"}));
            Assert.That(journal.Rows.Where(x=>x.State==ItemState.CueRequested&&x.Event=="state_before").Select(x=>x.TrialId),Is.EqualTo(new[]{"DEMO-TR-01"}));
        }

        [Test,Category("REQ_INT_02"),Category("REQ_INT_03")]
        public void StartedBatteryWithoutItsStartFactsCannotResume()
        {
            foreach(var (study,visit,code) in new (string,string,string)[]{("A","D0",ResumePolicy.OutsideSession),("B","V1",ResumePolicy.OutsideWindow),("B","V3",ResumePolicy.OutsideWindow)})
            {
                var schedule=Visit(study,visit);var policy=new ResumePolicy(study,visit,Session,null);
                var journal=Interrupted(schedule,new Record(),policy,Day(4,9));
                var missing=new Sitting(schedule,journal,new Record(),policy,Day(4,10));
                Assert.That(missing.Admit(),Is.EqualTo(code),study+" "+visit);
                Assert.That(missing.Engine.Status,Is.EqualTo(SessionState.Stopped));
            }
        }

        [Test]
        public void FirstStartRecordsItsStationDayOnceWithoutTouchingTheEngine()
        {
            var schedule=Visit("A","D0");var record=new Record();var journal=new Memory();
            // 23:30 at UTC-5 is already the next UTC day; the station's calendar day counts.
            var sitting=new Sitting(schedule,journal,record,new ResumePolicy("A","D0",Session,null),Day(4,23).AddMinutes(30));
            Assert.That(sitting.Admit(),Is.Null);
            Assert.That(record.Anchor.LocalDate,Is.EqualTo("2027-01-04"));Assert.That(record.Anchor.SessionId,Is.EqualTo(Session));
            Assert.That(sitting.Engine.Status,Is.EqualTo(SessionState.AwaitingOperator));Assert.That(journal.Rows,Is.Empty);Assert.That(sitting.Factory.Items,Is.Empty);
            sitting.Engine.ConfirmResume();sitting.Engine.RequestPause();
            Assert.That(sitting.Engine.Status,Is.EqualTo(SessionState.Paused));
            Assert.That(sitting.Admit(),Is.Null,"a same-sitting pause may continue");
            Assert.That(record.Anchor.LocalDate,Is.EqualTo("2027-01-04"));
            sitting.Engine.ConfirmResume();Assert.That(sitting.Engine.Status,Is.EqualTo(SessionState.Running));
            Assert.That(()=>sitting.Admit(),Throws.TypeOf<SessionFault>().With.Property("Code").EqualTo("SESSION_NOT_AT_BOUNDARY"));
        }

        [Test]
        public void PolicyRefusesUnknownVisitsMissingSessionsAndDayZeroWindows()
        {
            foreach(var (study,visit,session,window) in new (string,string,string,string)[]{("A","V1",Session,null),("B","D7",Session,null),("A","D0",null,null),("A","D0","not-a-session",null),
                ("A","D0",Session,"2027-01-04"),("B","V1",Session,"2027-01-04"),("B","W4",Session,"2027-1-30"),("B","W4",Session,"2027-02-30")})
                Assert.That(()=>new ResumePolicy(study,visit,session,window),Throws.TypeOf<SessionFault>().With.Property("Code").EqualTo("RESUME_POLICY_INVALID"),study+" "+visit);
            Assert.That(new ResumePolicy("B","W4",Session,"2027-02-28").WindowEnd,Is.EqualTo("2027-02-28"));
            Assert.That(()=>new ResumeAnchor(Session,"04/01/2027"),Throws.TypeOf<SessionFault>());
        }
    }
}
