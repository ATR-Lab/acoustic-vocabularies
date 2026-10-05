using System;
using System.Collections.Generic;
using System.Reflection;
using AcousticVocab.SessionEngine;
using AcousticVocab.FrameBudget;
using AcousticVocab.StudyAudio;
using NUnit.Framework;
namespace AcousticVocab.SessionIntegration.Tests
{
    public sealed class ExclusiveModuleTests
    {
        static T New<T>(params object[] a)=>(T)Activator.CreateInstance(typeof(T),BindingFlags.Instance|BindingFlags.NonPublic,null,a,null);
        sealed class Clock:ISessionClock{internal double Time;public double NowMs=>Time;}
        sealed class Journal:ISessionJournal{readonly List<SessionRecord> rows=new List<SessionRecord>();public IReadOnlyList<SessionRecord> Records=>rows;public void Append(SessionRecord r)=>rows.Add(r);}
        sealed class Factory:ISlotContentFactory,ISessionContentPump,ISlotStartPlan,IDisposable
        {
            internal int Pumps,Closes;internal bool ThrowClose;internal readonly List<string> Order;internal string Name;
            internal Factory(string name,List<string> order){Name=name;Order=order;Order.Add("create:"+name);}
            public ISlotContent Create(SlotItem i)=>new Content();public void Pump()=>Pumps++;
            public double MinimumGapBeforeMs(SlotItem item,double start)=>0;
            public void Dispose(){Closes++;Order.Add("dispose:"+Name);if(ThrowClose)throw new InvalidOperationException();}
        }
        sealed class Content:ISlotContent
        {public SlotReadiness Readiness=>new SlotReadiness(true,true,true,true,true,true,true,true);public bool ResetComplete=>true;public void Prepare(SlotContext c){}public void RequestCue(SlotContext c,INovelSlotAuthorization p){}public void OpenResponse(SlotContext c){}public void CloseResponse(SlotContext c){}public void RequestReset(SlotContext c){}public void Interrupt(string code){}}
        sealed class Capture:IFrameCapture,IFrameEvidence
        {
            readonly Clock clock;internal readonly FrameMonitor Monitor;internal int Cancelled;
            internal Capture(Clock clock){this.clock=clock;Monitor=new FrameMonitor(72,this);Monitor.Render(0,new RenderSample(0),true);}
            public bool Ready=>Monitor.Healthy;public void Drain(){}
            public void Register(SlotContext c)=>Monitor.Register(new FrameAttempt(c.OpportunityId,c.Item.TrialId,c.OnsetMonoMs,c.EndMonoMs,c.Item.Plays),new FrameWindow("response","response",c.OnsetMonoMs+c.Item.ResponseOpensSeconds*1000,c.OnsetMonoMs+c.Item.ResponseClosesSeconds*1000),clock.Time);
            public void Cancel(string id,bool requested=true)=>Monitor.Cancel(id,clock.Time,requested);
            public void Interval(FrameInterval x){}public void Fault(FrameFaultRecord x){}public void Summary(FrameSummary x){if(x.CancelledBeforeWindow)Cancelled++;}
        }
        sealed class Fixture
        {
            internal readonly Clock Clock=new Clock();internal readonly List<string> Order=new List<string>();internal readonly List<Factory> Made=new List<Factory>();
            internal readonly VisitSchedule Schedule;internal readonly ExclusiveContentMultiplexer Mux;internal readonly FixedSlotEngine Engine;
            internal readonly Capture Capture;
            internal Fixture(Func<string,ModuleConstructionScope,ISlotContentFactory> special=null,bool framed=false)
            {
                SlotItem Item(string id)=>New<SlotItem>(id,"atomic","K-a1","action","atomic","protected",false,10,0,1);
                Schedule=New<VisitSchedule>(new string('a',64),new string('b',64),"synthetic","DEMO",true,new[]{New<ScheduleBlock>("first",new[]{Item("one")}),New<ScheduleBlock>("second",new[]{Item("two")})},"A","S",null);
                ISlotContentFactory Make(string name,ModuleConstructionScope s){if(special!=null)return special(name,s);var f=new Factory(name,Order);Made.Add(f);return f;}
                Mux=new ExclusiveContentMultiplexer(Schedule,Clock,new Dictionary<string,Func<ModuleConstructionScope,ISlotContentFactory>>{{"first",s=>Make("first",s)},{"second",s=>Make("second",s)}});
                Capture=new Capture(Clock);Engine=new FixedSlotEngine(Schedule,Clock,new Journal(),framed?(ISlotContentFactory)new FrameContentFactory(Mux,Capture):Mux);
            }
            internal void Start(){Mux.PrepareBlockAtBoundary(Engine);Engine.ConfirmResume();Engine.Tick();}
            internal void ToTail(){Clock.Time=Mux.RetainedTailEndMs-3000;Engine.Tick();}
            internal void ToNextBoundary(){ToTail();Clock.Time=Mux.RetainedTailEndMs;Engine.Tick();Assert.That(Engine.Status,Is.EqualTo(SessionState.Paused));}
        }
        [Test] public void LazyFactoriesCreateNothingUntilExplicitBoundaryAndNeverOverlap()
        {
            var f=new Fixture();Assert.That(f.Made,Is.Empty);f.Start();f.ToTail();Assert.That(f.Made.Count,Is.EqualTo(1));int pumps=f.Made[0].Pumps;f.Clock.Time=8000;f.Engine.Tick();Assert.That(f.Made[0].Pumps,Is.GreaterThan(pumps));Assert.That(f.Made[0].Closes,Is.Zero);
            f.Clock.Time=f.Mux.RetainedTailEndMs;f.Engine.Tick();f.Mux.PrepareBlockAtBoundary(f.Engine);Assert.That(f.Order,Is.EqualTo(new[]{"create:first","dispose:first","create:second"}));f.Engine.ConfirmResume();f.Mux.Dispose();Assert.That(f.Made[1].Closes,Is.EqualTo(1));
        }
        [Test] public void SwitchingDuringRetainedTailLatchesAndDoesNotCreateSecondLease()
        {var f=new Fixture();f.Start();f.ToTail();Assert.Throws<SessionFault>(()=>f.Mux.PrepareBlockAtBoundary(f.Engine));Assert.That(f.Made.Count,Is.EqualTo(1));Assert.That(f.Mux.Failed,Is.True);f.Mux.Dispose();}
        [Test] public void CreateWithoutBoundaryPreparationIsRejected()
        {var f=new Fixture();Assert.Throws<SessionFault>(()=>f.Mux.Create(f.Schedule.Blocks[0].Items[0]));Assert.That(f.Made,Is.Empty);}
        [Test] public void OldDisposeFailureBlocksNewLeaseAndIsNeverRetried()
        {var f=new Fixture();f.Start();f.Made[0].ThrowClose=true;f.ToNextBoundary();Assert.Throws<SessionFault>(()=>f.Mux.PrepareBlockAtBoundary(f.Engine));Assert.That(f.Made.Count,Is.EqualTo(1));Assert.That(f.Made[0].Closes,Is.EqualTo(1));f.Mux.Dispose();Assert.That(f.Made[0].Closes,Is.EqualTo(1));}
        [Test] public void FailedCreatorClosesEveryPartiallyRegisteredResource()
        {int closed=0;var f=new Fixture((_,scope)=>{scope.RegisterCleanup(()=>closed++);scope.RegisterCleanup(()=>{closed++;throw new Exception();});scope.RegisterCleanup(()=>closed++);throw new Exception();});Assert.Throws<SessionFault>(()=>f.Mux.PrepareBlockAtBoundary(f.Engine));Assert.That(closed,Is.EqualTo(3));Assert.That(f.Mux.Failed,Is.True);}
        [Test] public void StalePlanAnchorFailsBeforeAnotherCue()
        {var f=new Fixture();f.Start();f.Clock.Time=100;Assert.Throws<SessionFault>(()=>f.Mux.MinimumGapBeforeMs(f.Schedule.Blocks[0].Items[0],99));Assert.That(f.Mux.Failed,Is.True);f.Mux.Dispose();}
        [Test] public void PauseBeforeCueKeepsFrameReadinessAndAllowsImmediateExplicitResume()
        {
            var f=new Fixture(framed:true);f.Mux.PrepareBlockAtBoundary(f.Engine);f.Engine.ConfirmResume();
            // Do not Tick: loaded and prepared, no cue admitted or window begun.
            f.Engine.RequestPause();Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Paused));Assert.That(f.Mux.RetainedTailEndMs,Is.Zero);
            Assert.That(f.Capture.Ready,Is.True);Assert.That(f.Capture.Cancelled,Is.EqualTo(1));
            f.Mux.PrepareBlockAtBoundary(f.Engine);f.Engine.ConfirmResume();f.Engine.Tick();
            Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Running));Assert.That(f.Capture.Ready,Is.True);Assert.That(f.Made.Count,Is.EqualTo(1));f.Mux.Dispose();
        }
        [Test] public void SameBoundaryPreparationReusesOneLeaseAndScopeOwnershipIsIdempotent()
        {var f=new Fixture();f.Mux.PrepareBlockAtBoundary(f.Engine);f.Mux.PrepareBlockAtBoundary(f.Engine);Assert.That(f.Made.Count,Is.EqualTo(1));f.Mux.Dispose();f.Mux.Dispose();Assert.That(f.Made[0].Closes,Is.EqualTo(1));}
    }
}
