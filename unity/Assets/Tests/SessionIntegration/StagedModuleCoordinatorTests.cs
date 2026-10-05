using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using NUnit.Framework;

namespace AcousticVocab.SessionIntegration.Tests
{
    // Controlled-clock software lifecycle tests. The fake content produces no
    // audio and makes no backend, acoustic or physical presentation claim.
    public sealed class StagedModuleCoordinatorTests
    {
        static T New<T>(params object[] args)=>(T)Activator.CreateInstance(typeof(T),BindingFlags.Instance|BindingFlags.NonPublic,null,args,null);
        sealed class Clock:ISessionClock { internal double Time;public double NowMs=>Time; }
        sealed class Journal:ISessionJournal
        {
            readonly List<SessionRecord> rows=new List<SessionRecord>();
            public IReadOnlyList<SessionRecord> Records=>rows;
            public void Append(SessionRecord row)=>rows.Add(row);
        }
        sealed class Factory:ISlotContentFactory,IDisposable
        {
            readonly List<string> order;readonly string block;
            internal readonly List<string> Prepared=new List<string>();internal int Closed;
            internal Factory(string block,List<string> order){this.block=block;this.order=order;order.Add("factory:"+block);}
            public ISlotContent Create(SlotItem item)=>new Content(this);
            public void Dispose(){Closed++;order.Add("dispose:"+block);}
        }
        sealed class Content:ISlotContent
        {
            readonly Factory owner;
            internal Content(Factory owner){this.owner=owner;}
            public SlotReadiness Readiness=>new SlotReadiness(true,true,true,true,true,true,true,true);
            public bool ResetComplete=>true;
            public void Prepare(SlotContext context)=>owner.Prepared.Add(context.Item.TrialId);
            public void RequestCue(SlotContext context,INovelSlotAuthorization permit){}
            public void OpenResponse(SlotContext context){}
            public void CloseResponse(SlotContext context){}
            public void RequestReset(SlotContext context){}
            public void Interrupt(string code){}
        }
        sealed class Preflight:IModulePreflight
        {
            readonly Fixture fixture;readonly string block;
            internal bool Allowed,ThrowPump,ThrowCommit;internal int Pumps,Commits,Closed;
            internal Preflight(Fixture fixture,string block,ModuleConstructionScope scope)
            {
                this.fixture=fixture;this.block=block;fixture.Order.Add("preflight:"+block);
                scope.RegisterCleanup(()=>{Closed++;fixture.Order.Add("close-preflight:"+block);});
            }
            public bool Ready=>Allowed;
            public void Pump(){Pumps++;if(ThrowPump)throw new SessionFault("TEST_PREFLIGHT_FAILED");}
            public ISlotContentFactory Commit(ModuleConstructionScope scope)
            {
                Commits++;if(ThrowCommit)throw new SessionFault("TEST_COMMIT_FAILED");
                var factory=new Factory(block,fixture.Order);fixture.Factories.Add(factory);return factory;
            }
        }
        sealed class Fixture:IDisposable
        {
            internal readonly Clock Clock=new Clock();internal readonly Journal Journal=new Journal();
            internal readonly List<string> Order=new List<string>();internal readonly List<Preflight> Candidates=new List<Preflight>();
            internal readonly List<Factory> Factories=new List<Factory>();
            internal readonly FixedSlotEngine Engine;internal readonly ExclusiveContentMultiplexer Modules;internal readonly StagedModuleCoordinator Stage;
            internal Fixture(bool separateBlocks=false,Func<ModuleConstructionScope,IModulePreflight> creator=null)
            {
                SlotItem Item(string id)=>New<SlotItem>(id,"atomic","K-a1","action","atomic","protected",false,9,1,1);
                var blocks=separateBlocks?new[]{New<ScheduleBlock>("first",new[]{Item("one")}),New<ScheduleBlock>("second",new[]{Item("two")})}:
                    new[]{New<ScheduleBlock>("first",new[]{Item("one"),Item("two")})};
                var schedule=New<VisitSchedule>(new string('a',64),new string('b',64),"DEMO-person","DEMO",true,blocks,"A","S",null);
                Modules=new ExclusiveContentMultiplexer(schedule,Clock,blocks.ToDictionary(b=>b.Name,b=>(Func<ModuleConstructionScope,ISlotContentFactory>)(_=>throw new SessionFault("TEST_BYPASSED_STAGING"))));
                Engine=new FixedSlotEngine(schedule,Clock,Journal,Modules);
                Stage=new StagedModuleCoordinator(Engine,Modules,blocks.ToDictionary(b=>b.Name,b=>(Func<ModuleConstructionScope,IModulePreflight>)(scope=>
                {
                    if(creator!=null)return creator(scope);
                    var candidate=new Preflight(this,b.Name,scope);Candidates.Add(candidate);return candidate;
                })));
            }
            internal void Start()
            {
                Stage.Pump();Candidates.Last().Allowed=true;Stage.Pump();Stage.CommitForResume(Engine);Engine.ConfirmResume();Engine.Tick();
            }
            internal void FinishFirstAtTail()
            {
                Clock.Time=7750;Engine.Tick();Assert.That(Engine.CompletedOpportunities,Is.EqualTo(1));
                Assert.That(Engine.Status,Is.EqualTo(SessionState.Running));
                Clock.Time=9750;Engine.Tick();Assert.That(Engine.Status,Is.EqualTo(SessionState.Paused));
            }
            public void Dispose(){Stage.Dispose();Modules.Dispose();}
        }

        [Test] public void AsyncPreparationNeverAdvancesOrAutomaticallyResumesTheEngine()
        {
            using var f=new Fixture();f.Stage.Pump();f.Stage.Pump();
            Assert.That(f.Candidates.Count,Is.EqualTo(1));Assert.That(f.Stage.Ready,Is.False);
            Assert.That(f.Factories,Is.Empty);Assert.That(f.Journal.Records,Is.Empty);
            f.Candidates[0].Allowed=true;f.Stage.Pump();
            Assert.That(f.Stage.Ready,Is.True);Assert.That(f.Engine.Status,Is.EqualTo(SessionState.AwaitingOperator));
            Assert.That(f.Engine.CurrentTrialId,Is.Null);Assert.That(f.Factories,Is.Empty);
            f.Stage.CommitForResume(f.Engine);
            Assert.That(f.Engine.Status,Is.EqualTo(SessionState.AwaitingOperator));Assert.That(f.Stage.Ready,Is.False);
            f.Engine.ConfirmResume();f.Engine.Tick();
            Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Running));Assert.That(f.Engine.CurrentTrialId,Is.EqualTo("one"));
            Assert.That(f.Candidates[0].Commits,Is.EqualTo(1));
        }

        [Test] public void SameBlockPauseAfterCueRenewsOnlyAfterTailAndDoesNotRepeatExposure()
        {
            using var f=new Fixture();f.Start();f.Clock.Time=1000;f.Engine.RequestPause();
            f.Stage.Pump();Assert.That(f.Candidates.Count,Is.EqualTo(1));Assert.That(f.Factories[0].Closed,Is.Zero);
            f.Clock.Time=7750;f.Engine.Tick();f.Stage.Pump();
            Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Running));Assert.That(f.Candidates.Count,Is.EqualTo(1));
            Assert.That(f.Factories[0].Closed,Is.Zero);
            f.Clock.Time=9750;f.Engine.Tick();f.Stage.Pump();
            Assert.That(f.Candidates.Count,Is.EqualTo(2));Assert.That(f.Factories[0].Closed,Is.EqualTo(1));
            Assert.That(f.Candidates[0].Closed,Is.EqualTo(1));Assert.That(f.Stage.Ready,Is.False);
            f.Candidates[1].Allowed=true;f.Stage.Pump();
            Assert.That(f.Stage.Ready,Is.True);Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Paused));
            f.Stage.CommitForResume(f.Engine);f.Engine.ConfirmResume();f.Engine.Tick();
            Assert.That(f.Engine.CompletedOpportunities,Is.EqualTo(1));Assert.That(f.Engine.CurrentTrialId,Is.EqualTo("two"));
            Assert.That(f.Factories.SelectMany(x=>x.Prepared),Is.EqualTo(new[]{"one","two"}));
            Assert.That(f.Order,Is.EqualTo(new[]{"preflight:first","factory:first","dispose:first","close-preflight:first","preflight:first","factory:first"}));
        }

        [Test] public void NextBlockReleasesOldOwnerBeforeNewPreflightAndRequiresExplicitResume()
        {
            using var f=new Fixture(separateBlocks:true);f.Start();f.FinishFirstAtTail();f.Stage.Pump();
            Assert.That(f.Modules.ActiveBlock,Is.Null);Assert.That(f.Factories[0].Closed,Is.EqualTo(1));
            Assert.That(f.Order.Last(),Is.EqualTo("preflight:second"));
            f.Candidates[1].Allowed=true;f.Stage.Pump();
            Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Paused));Assert.That(f.Engine.CurrentTrialId,Is.Null);
            f.Stage.CommitForResume(f.Engine);f.Engine.ConfirmResume();f.Engine.Tick();
            Assert.That(f.Modules.ActiveBlock,Is.EqualTo("second"));Assert.That(f.Engine.CurrentTrialId,Is.EqualTo("two"));
        }

        [Test] public void LateSameBlockBoundaryWaitsForRetainedTailBeforeRenewal()
        {
            using var f=new Fixture();f.Start();f.Clock.Time=9601;f.Engine.Tick();
            Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Paused));Assert.That(f.Engine.CompletedOpportunities,Is.EqualTo(1));
            f.Stage.Pump();Assert.That(f.Candidates.Count,Is.EqualTo(1));Assert.That(f.Factories[0].Closed,Is.Zero);
            f.Clock.Time=9750;f.Stage.Pump();Assert.That(f.Candidates.Count,Is.EqualTo(2));
            f.Candidates[1].Allowed=true;f.Stage.CommitForResume(f.Engine);f.Engine.ConfirmResume();f.Engine.Tick();
            Assert.That(f.Engine.CurrentTrialId,Is.EqualTo("two"));Assert.That(f.Factories[0].Closed,Is.EqualTo(1));
        }

        [Test] public void StopWhileWaitingDisposesPendingResourcesWithoutCreatingContent()
        {
            using var f=new Fixture();f.Stage.Pump();f.Engine.RequestStop();f.Stage.Pump();f.Stage.Pump();
            Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Stopped));Assert.That(f.Candidates[0].Closed,Is.EqualTo(1));
            Assert.That(f.Factories,Is.Empty);Assert.That(f.Stage.Ready,Is.False);
        }

        [Test] public void PendingOnlyPumpMaintainsUnreadyCandidateWithoutAcquiringOrResuming()
        {
            using var f=new Fixture();f.Stage.PumpPending();Assert.That(f.Candidates,Is.Empty);
            f.Stage.Pump();int before=f.Candidates[0].Pumps;f.Stage.PumpPending();f.Stage.PumpPending();
            Assert.That(f.Candidates[0].Pumps,Is.EqualTo(before+2));Assert.That(f.Candidates.Count,Is.EqualTo(1));
            Assert.That(f.Stage.Ready,Is.False);Assert.That(f.Factories,Is.Empty);
            Assert.That(f.Engine.Status,Is.EqualTo(SessionState.AwaitingOperator));
            f.Candidates[0].Allowed=true;f.Stage.CommitForResume(f.Engine);f.Engine.ConfirmResume();
            f.Stage.PumpPending();Assert.That(f.Candidates.Count,Is.EqualTo(1));Assert.That(f.Factories.Count,Is.EqualTo(1));
        }

        [Test] public void FailedCreatorDisposesPartialResourcesAndCannotRetryAutomatically()
        {
            int closed=0,calls=0;
            using var f=new Fixture(creator:scope=>{calls++;scope.RegisterCleanup(()=>closed++);throw new SessionFault("TEST_CREATOR_FAILED");});
            Assert.Throws<SessionFault>(()=>f.Stage.Pump());f.Stage.Pump();
            Assert.That(calls,Is.EqualTo(1));Assert.That(closed,Is.EqualTo(1));Assert.That(f.Stage.Ready,Is.False);
            Assert.That(f.Engine.Status,Is.Not.EqualTo(SessionState.Running));Assert.That(f.Factories,Is.Empty);
        }

        [Test] public void FailedAsyncPumpClosesCandidateAndLatchesTheCoordinator()
        {
            using var f=new Fixture();f.Stage.Pump();f.Candidates[0].ThrowPump=true;
            Assert.Throws<SessionFault>(()=>f.Stage.Pump());f.Stage.Pump();
            Assert.That(f.Candidates.Count,Is.EqualTo(1));Assert.That(f.Candidates[0].Closed,Is.EqualTo(1));
            Assert.That(f.Stage.Ready,Is.False);Assert.That(f.Factories,Is.Empty);
        }

        [Test] public void FailedCommitClosesCandidateAndNeverPublishesAnActiveModule()
        {
            using var f=new Fixture();f.Stage.Pump();f.Candidates[0].Allowed=true;f.Candidates[0].ThrowCommit=true;
            Assert.Throws<SessionFault>(()=>f.Stage.CommitForResume(f.Engine));f.Stage.Pump();
            Assert.That(f.Candidates[0].Closed,Is.EqualTo(1));Assert.That(f.Modules.Failed,Is.True);
            Assert.That(f.Modules.ActiveBlock,Is.Null);Assert.That(f.Stage.Ready,Is.False);Assert.That(f.Factories,Is.Empty);
        }

        [Test] public void PreparedCapabilityIsConsumedOnceAndRejectsAnUnreadyResume()
        {
            using var f=new Fixture();f.Stage.Pump();Assert.Throws<SessionFault>(()=>f.Stage.CommitForResume(f.Engine));
            Assert.That(f.Candidates[0].Commits,Is.Zero);Assert.That(f.Engine.Status,Is.EqualTo(SessionState.AwaitingOperator));
            f.Candidates[0].Allowed=true;f.Stage.CommitForResume(f.Engine);
            Assert.Throws<SessionFault>(()=>f.Stage.CommitForResume(f.Engine));
            Assert.That(f.Candidates[0].Commits,Is.EqualTo(1));Assert.That(f.Factories.Count,Is.EqualTo(1));
        }
    }
}
