using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using NUnit.Framework;
namespace AcousticVocab.SessionIntegration.Tests
{
    // Native attempt simulation-test-B-V1-022-isaac2-015 completed the visit and
    // recorded the forms; the quit-on-complete close then disposed the joined
    // owner, which faulted the completed engine: item_fault SESSION_JOIN_DISPOSED
    // and session_paused after visit_complete. These drive the real engine and
    // multiplexer through the owner's disposal call.
    public sealed class CompletedVisitCloseTests
    {
        const string Disposed="SESSION_JOIN_DISPOSED";
        static T New<T>(params object[] a)=>(T)Activator.CreateInstance(typeof(T),BindingFlags.Instance|BindingFlags.NonPublic,null,a,null);
        sealed class Clock:ISessionClock{internal double Time;public double NowMs=>Time;}
        sealed class Journal:ISessionJournal{internal readonly List<SessionRecord> Rows=new List<SessionRecord>();public IReadOnlyList<SessionRecord> Records=>Rows;public void Append(SessionRecord r)=>Rows.Add(r);}
        sealed class Factory:ISlotContentFactory,ISessionContentPump,ISlotStartPlan,IDisposable
        {
            readonly HashSet<string> prepared=new HashSet<string>();internal bool Interrupted;
            public ISlotContent Create(SlotItem i)=>new Content(this);public void Pump(){}
            internal void Prepare(SlotContext c){if(!prepared.Add(c.Item.TrialId))throw new SessionFault("TEST_REUSED_CONTENT");}
            public double MinimumGapBeforeMs(SlotItem item,double start)=>0;public void Dispose(){}
        }
        sealed class Content:ISlotContent
        {readonly Factory owner;internal Content(Factory owner){this.owner=owner;}public SlotReadiness Readiness=>new SlotReadiness(true,true,true,true,true,true,true,true);public bool ResetComplete=>true;public void Prepare(SlotContext c)=>owner.Prepare(c);public void RequestCue(SlotContext c,INovelSlotAuthorization p){}public void OpenResponse(SlotContext c){}public void CloseResponse(SlotContext c){}public void RequestReset(SlotContext c){}public void Interrupt(string code){owner.Interrupted=true;}}
        sealed class Visit:IDisposable
        {
            internal readonly Clock Clock=new Clock();internal readonly Journal Journal=new Journal();internal readonly ExclusiveContentMultiplexer Mux;internal readonly FixedSlotEngine Engine;
            internal Visit()
            {
                SlotItem Item(string id)=>New<SlotItem>(id,"atomic","K-a1","action","atomic","protected",false,10,0,1);
                var schedule=New<VisitSchedule>(new string('a',64),new string('b',64),"synthetic","DEMO",true,new[]{New<ScheduleBlock>("first",new[]{Item("one")}),New<ScheduleBlock>("second",new[]{Item("two")})},"A","S",null);
                Mux=new ExclusiveContentMultiplexer(schedule,Clock,new Dictionary<string,Func<ModuleConstructionScope,ISlotContentFactory>>{{"first",_=>new Factory()},{"second",_=>new Factory()}});
                Engine=new FixedSlotEngine(schedule,Clock,Journal,Mux);
            }
            internal void StartBlock(){Mux.PrepareBlockAtBoundary(Engine);Engine.ConfirmResume();Engine.Tick();Assert.That(Engine.Status,Is.EqualTo(SessionState.Running));}
            internal void ToTail(){Clock.Time=Mux.RetainedTailEndMs-3000;Engine.Tick();}
            internal void FinishBlock(){ToTail();Clock.Time=Mux.RetainedTailEndMs;Engine.Tick();}
            internal void Complete(){StartBlock();FinishBlock();StartBlock();FinishBlock();Assert.That(Engine.Status,Is.EqualTo(SessionState.Complete));Assert.That(Journal.Rows.Last().Event,Is.EqualTo("visit_complete"));}
            public void Dispose()=>Mux.Dispose();
        }
        static void AssertDisposeFault(Visit v,int before)
        {
            var added=v.Journal.Rows.Skip(before).ToArray();
            Assert.That(added.Any(r=>r.Event=="item_fault"&&r.TechnicalFaultCode==Disposed),Is.True,"An unfinished visit is still faulted on dispose");
            Assert.That(v.Engine.PrimaryFaultCode,Is.EqualTo(Disposed));Assert.That(v.Engine.Status,Is.Not.EqualTo(SessionState.Complete));
        }
        [Test] public void CompletedVisitClosesWithoutAnyRecord()
        {
            using var v=new Visit();v.Complete();int rows=v.Journal.Rows.Count;v.Clock.Time+=6000; // forms are recorded after visit_complete
            v.Engine.CloseForDisposal(Disposed);v.Engine.CloseForDisposal(Disposed);
            Assert.That(v.Journal.Rows.Count,Is.EqualTo(rows),"No item_fault or session_paused after visit_complete");
            Assert.That(v.Engine.Status,Is.EqualTo(SessionState.Complete));Assert.That(v.Engine.PrimaryFaultCode,Is.Null);
        }
        [Test] public void DisposeDuringAnItemStillFaults()
        {using var v=new Visit();v.StartBlock();v.Clock.Time=8000;v.Engine.Tick();int rows=v.Journal.Rows.Count;v.Engine.CloseForDisposal(Disposed);AssertDisposeFault(v,rows);}
        [Test] public void DisposeAtABlockBoundaryStillFaults()
        {using var v=new Visit();v.StartBlock();v.FinishBlock();Assert.That(v.Engine.Status,Is.EqualTo(SessionState.Paused));int rows=v.Journal.Rows.Count;v.Engine.CloseForDisposal(Disposed);AssertDisposeFault(v,rows);}
        [Test] public void DisposeDuringTheFinalTailStillFaults()
        {
            using var v=new Visit();v.StartBlock();v.FinishBlock();v.StartBlock();v.ToTail();
            Assert.That(v.Journal.Rows.Any(r=>r.Event=="visit_complete"),Is.False);int rows=v.Journal.Rows.Count;v.Engine.CloseForDisposal(Disposed);AssertDisposeFault(v,rows);
        }
        [Test] public void DisposeBeforeTheVisitStartsStillFaults()
        {using var v=new Visit();int rows=v.Journal.Rows.Count;v.Engine.CloseForDisposal(Disposed);AssertDisposeFault(v,rows);}
        [Test] public void ARealFaultAfterCompletionIsStillRecorded()
        {
            using var v=new Visit();v.Complete();int rows=v.Journal.Rows.Count;v.Engine.Fault("JOIN_FORMS_FAILED");
            Assert.That(v.Journal.Rows.Skip(rows).Any(r=>r.Event=="item_fault"&&r.TechnicalFaultCode=="JOIN_FORMS_FAILED"),Is.True,"Only clean disposal is silent");
        }
    }
}
