using System;
using System.Collections.Generic;
using System.Reflection;
using AcousticVocab.SessionEngine;
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
        sealed class Fixture
        {
            internal readonly Clock Clock=new Clock();internal readonly List<string> Order=new List<string>();internal readonly List<Factory> Made=new List<Factory>();
            internal readonly VisitSchedule Schedule;internal readonly ExclusiveContentMultiplexer Mux;internal readonly FixedSlotEngine Engine;
            internal Fixture(Func<string,ModuleConstructionScope,ISlotContentFactory> special=null)
            {
                SlotItem Item(string id)=>New<SlotItem>(id,"atomic","K-a1","action","atomic","protected",false,10,0,1);
                Schedule=New<VisitSchedule>(new string('a',64),new string('b',64),"synthetic","DEMO",true,new[]{New<ScheduleBlock>("first",new[]{Item("one")}),New<ScheduleBlock>("second",new[]{Item("two")})},"A","S",null);
                ISlotContentFactory Make(string name,ModuleConstructionScope s){if(special!=null)return special(name,s);var f=new Factory(name,Order);Made.Add(f);return f;}
                Mux=new ExclusiveContentMultiplexer(Schedule,Clock,new Dictionary<string,Func<ModuleConstructionScope,ISlotContentFactory>>{{"first",s=>Make("first",s)},{"second",s=>Make("second",s)}});
                Engine=new FixedSlotEngine(Schedule,Clock,new Journal(),Mux);
            }
            internal void Start(){Mux.PrepareBlockAtBoundary(Engine);Engine.ConfirmResume();Engine.Tick();}
            internal void ToTail(){Clock.Time=250;Engine.Tick();Clock.Time=7250;Engine.Tick();}
            internal void ToNextBoundary(){ToTail();Clock.Time=10250;Engine.Tick();Assert.That(Engine.Status,Is.EqualTo(SessionState.Paused));}
        }
        [Test] public void LazyFactoriesCreateNothingUntilExplicitBoundaryAndNeverOverlap()
        {
            var f=new Fixture();Assert.That(f.Made,Is.Empty);f.Start();f.ToTail();Assert.That(f.Made.Count,Is.EqualTo(1));int pumps=f.Made[0].Pumps;f.Clock.Time=8000;f.Engine.Tick();Assert.That(f.Made[0].Pumps,Is.GreaterThan(pumps));Assert.That(f.Made[0].Closes,Is.Zero);
            f.Clock.Time=10250;f.Engine.Tick();f.Mux.PrepareBlockAtBoundary(f.Engine);Assert.That(f.Order,Is.EqualTo(new[]{"create:first","dispose:first","create:second"}));f.Engine.ConfirmResume();f.Mux.Dispose();Assert.That(f.Made[1].Closes,Is.EqualTo(1));
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
        [Test] public void SameBoundaryPreparationReusesOneLeaseAndScopeOwnershipIsIdempotent()
        {var f=new Fixture();f.Mux.PrepareBlockAtBoundary(f.Engine);f.Mux.PrepareBlockAtBoundary(f.Engine);Assert.That(f.Made.Count,Is.EqualTo(1));f.Mux.Dispose();f.Mux.Dispose();Assert.That(f.Made[0].Closes,Is.EqualTo(1));}
    }
}
