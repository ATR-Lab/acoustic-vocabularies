using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text.RegularExpressions;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.SessionEngine.Tests
{
    // Requirement trace for the fixed-slot engine. Synthetic DEMO schedules and
    // the public DEMO producer fixtures only.
    public sealed class RequirementTraceEngineTests
    {
        const string Hash="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        const string Package="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
        const string Evidence="cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc";
        sealed class Clock:ISessionClock{public double Time;public double NowMs=>Time;}
        sealed class Memory:ISessionJournal
        {
            public readonly List<SessionRecord> Rows=new List<SessionRecord>();
            public IReadOnlyList<SessionRecord> Records=>Rows;
            public void Append(SessionRecord record)=>Rows.Add(record);
        }
        sealed class Content:ISlotContent
        {
            public SlotContext Context;public int Cues;public INovelSlotAuthorization Permit;
            public SlotReadiness Readiness=>new SlotReadiness(true,true,true,true,true,true,true,true);
            public bool ResetComplete{get;private set;}
            public void Prepare(SlotContext context){Context=context;}
            public void RequestCue(SlotContext context,INovelSlotAuthorization permit){Cues++;Permit=permit;}
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
        sealed class Run
        {
            public readonly Clock Clock=new Clock();public readonly Memory Journal;public readonly Factory Factory=new Factory();
            public readonly FixedSlotEngine Engine;
            public Run(VisitSchedule schedule,Memory journal=null,double start=0)
            {Journal=journal??new Memory();Clock.Time=start;Engine=new FixedSlotEngine(schedule,Clock,Journal,Factory);}
            public void Step(double ms=50){Clock.Time+=ms;Engine.Tick();}
            public void Until(double time){while(Clock.Time<time&&Engine.Status==SessionState.Running)Step();}
            public void ToEnd(Action<Run> each=null,double step=50)
            {
                for(int guard=0;guard<2000000&&Engine.Status!=SessionState.Complete;guard++)
                {
                    if(Engine.NeedsOperatorConfirmation)Engine.ConfirmResume();
                    Step(step);each?.Invoke(this);
                    if(Engine.Status==SessionState.Faulted)Assert.Fail("engine faulted: "+Engine.PrimaryFaultCode);
                }
                Assert.That(Engine.Status,Is.EqualTo(SessionState.Complete));
            }
        }
        static SlotItem Item(string id,string type,string content,bool heldout=false,string role=null,string phase="protected")
            =>new SlotItem(id,type,content,role,null,phase,heldout,type=="atomic"?9:14,type=="no_cue"?0:1,1);
        static VisitSchedule Visit(string study,string visit,params ScheduleBlock[] blocks)
            =>new VisitSchedule(Hash,Package,"DEMO-person",visit,true,blocks,study,"pilot",null);
        static string Repo()
        {
            for(var path=new DirectoryInfo(Directory.GetCurrentDirectory());path!=null;path=path.Parent)
                if(File.Exists(Path.Combine(path.FullName,"schedules/schema/visit-schedule.schema.json")))return path.FullName;
            throw new Exception("Repository public schedule fixtures unavailable");
        }
        static (VisitSchedule schedule,JObject doc,JObject permutation) Producer(string study,string visit)
        {
            string unit=study+"-C01",person=unit+(study=="A"?"-L01":"-M1");
            string root=Path.Combine(Repo(),"schedules/examples/demo",study,unit);
            var doc=SessionJson.Parse(File.ReadAllBytes(Path.Combine(root,"schedules",person,visit+".json")));
            var permutation=SessionJson.Parse(File.ReadAllBytes(Path.Combine(root,"permutation.json")));
            var blocks=ScheduleLoader.ValidateBlocks(doc,permutation);
            return (new VisitSchedule(Hash,Package,person,visit,true,blocks,study,(string)doc["set"]),doc,permutation);
        }
        static string Observable(FixedSlotEngine e)=>string.Join("|",e.Status,e.CurrentState,e.CurrentTrialId,e.CurrentBlock,e.ExposureConsumed,
            e.CompletedOpportunities,string.Join(",",e.CompletedCounts),e.NeedsOperatorConfirmation,e.ScheduleSha256,e.PackageSha256,e.PrimaryFaultCode);

        [Test,Category("REQ_TEST_01")]
        public void HiddenQueuePresentsScheduleOrderWithoutExposingTheRequestInStatusOrJournal()
        {
            var items=new[]{Item("DEMO-T-01","trained","K-a2-r3"),Item("DEMO-T-02","trained","Q-a4-r1"),Item("DEMO-T-03","novel","K-a1-r2",heldout:true)};
            var run=new Run(Visit("A","D0",new ScheduleBlock("trained",items.Take(2).ToArray()),new ScheduleBlock("novel",items.Skip(2).ToArray())));
            var observed=new List<string>();
            run.ToEnd(r=>observed.Add(Observable(r.Engine)));
            Assert.That(run.Factory.Items.Select(x=>x.Context.Item.TrialId),Is.EqualTo(items.Select(x=>x.TrialId)));
            Assert.That(run.Factory.Items.All(x=>x.Cues==1),Is.True);
            string journal=string.Join("\n",run.Journal.Rows.Select(x=>SessionRecordCodec.ToJson(x).ToString()));
            foreach(var item in items)
            {
                Assert.That(journal,Does.Not.Contain(item.ContentId));
                Assert.That(observed.Any(x=>x.Contains(item.ContentId)),Is.False);
            }
            // No public engine operation takes an item, block, index or slot to choose what plays next.
            var choosers=typeof(FixedSlotEngine).GetMethods(BindingFlags.Public|BindingFlags.Instance|BindingFlags.DeclaredOnly)
                .Where(m=>!m.IsSpecialName&&m.GetParameters().Any(p=>p.ParameterType==typeof(SlotItem)||p.ParameterType==typeof(ScheduleBlock)||
                    p.ParameterType==typeof(int)||p.ParameterType==typeof(SlotContext))).Select(m=>m.Name).ToArray();
            Assert.That(choosers,Is.Empty);
        }

        [Test,Category("REQ_RSV_01")]
        public void ReservedMessagesReceiveCompleteAudioAuthorityOnlyAtTheirOwnTestTrial()
        {
            var message=new Regex(@"\A[KQ]-a[1-4]-r[1-4]\z");
            foreach(var person in new[]{("A",new[]{"D0","D7"}),("B",new[]{"V1","V2","V3","W1","W4"})})
            {
                var presented=new HashSet<string>(StringComparer.Ordinal);var tested=new HashSet<string>(StringComparer.Ordinal);
                foreach(string visit in person.Item2)
                {
                    var producer=Producer(person.Item1,visit);
                    var reserved=new HashSet<string>(((JArray)producer.permutation["messages"]).Where(x=>(string)x["status"]=="heldout").Select(x=>(string)x["message_id"]));
                    Assert.That(reserved.Count,Is.EqualTo(14));
                    Assert.That(((JArray)producer.doc["dictionary_messages"]).Select(x=>(string)x).Any(reserved.Contains),Is.False);
                    var run=new Run(producer.schedule);run.ToEnd(step:100);
                    var scheduled=producer.schedule.Blocks.SelectMany(b=>b.Items).ToArray();
                    Assert.That(run.Factory.Items.Select(x=>x.Context.Item.TrialId),Is.EqualTo(scheduled.Select(x=>x.TrialId)));
                    foreach(var content in run.Factory.Items)
                    {
                        var item=content.Context.Item;
                        // Only a held-out test trial receives the single-use complete-message authority.
                        Assert.That(content.Permit!=null,Is.EqualTo(item.Heldout),item.TrialId);
                        if(item.Heldout)
                        {
                            Assert.That(item.TrialType,Is.EqualTo("novel"),item.TrialId);Assert.That(item.Phase,Is.EqualTo("protected"),item.TrialId);
                            Assert.That(reserved.Contains(item.ContentId),Is.True,item.TrialId);
                            Assert.That(presented.Contains(item.ContentId),Is.False,"reserved message presented before its test trial: "+item.TrialId);
                            Assert.That(tested.Add(item.ContentId),Is.True,item.TrialId);
                        }
                        else Assert.That(item.ContentId!=null&&reserved.Contains(item.ContentId),Is.False,"reserved message outside its test trial: "+item.TrialId);
                        if(item.ContentId!=null&&item.Plays>0&&message.IsMatch(item.ContentId))presented.Add(item.ContentId);
                    }
                }
                Assert.That(tested.Count,Is.GreaterThan(0));
            }
        }

        [TestCase(AudibleStatus.ConfirmedAudible)][TestCase(AudibleStatus.Uncertain)]
        [Category("REQ_RSV_02")]
        public void FirstAudiblePlayOfAReservedMessageConsumesItsExposureDespiteALaterFault(AudibleStatus evidence)
        {
            var schedule=Visit("A","D0",new ScheduleBlock("novel",new[]{Item("DEMO-NV-01","novel","K-a1-r2",heldout:true),Item("DEMO-NV-02","novel","Q-a3-r2",heldout:true)}));
            var run=new Run(schedule);run.Engine.ConfirmResume();run.Step(100);
            Assert.That(run.Engine.CurrentState,Is.EqualTo(ItemState.CueRequested));Assert.That(run.Factory.Items[0].Cues,Is.EqualTo(1));
            // The consumed intent is durable before the cue reaches the audio module.
            var intent=run.Journal.Rows.Single(x=>x.Event=="state_before"&&x.State==ItemState.CueRequested);
            Assert.That(intent.ExposureConsumed,Is.True);Assert.That(intent.TrialId,Is.EqualTo("DEMO-NV-01"));
            run.Clock.Time=800;run.Engine.ObserveAudible(evidence,Evidence);
            run.Engine.Fault("SYNTHETIC_DELIVERY_FAULT");
            var after=run.Journal.Rows.Where(x=>x.TrialId=="DEMO-NV-01"&&x.MonoMs>=800).ToArray();
            Assert.That(after.Any(x=>x.Event=="item_fault"),Is.True);
            Assert.That(after.All(x=>x.ExposureConsumed),Is.True);
            Assert.That(run.Journal.Rows.Any(x=>x.Event=="retry_queued"),Is.False);
            // A later engine over the same journal treats the opportunity as used and moves on.
            var resumed=new Run(schedule,run.Journal,run.Clock.Time);resumed.Engine.ConfirmResume();
            Assert.That(resumed.Engine.CurrentTrialId,Is.EqualTo("DEMO-NV-02"));
            Assert.That(resumed.Factory.Items.Select(x=>x.Context.Item.ContentId),Has.None.EqualTo("K-a1-r2"));
        }

        [Test,Category("REQ_RSV_06")]
        public void HeardReservedMessageIsNeverPresentedAgainAndAnUnheardOneRetriesOnlyAsALinkedRepeat()
        {
            var heldout=new[]{Item("DEMO-NV-01","novel","K-a1-r2",heldout:true),Item("DEMO-NV-02","novel","Q-a3-r2",heldout:true)};
            var schedule=Visit("A","D0",new ScheduleBlock("novel",heldout));
            var heard=new Run(schedule);heard.Engine.ConfirmResume();heard.Step(100);heard.Clock.Time=800;
            var permit=heard.Factory.Items[0].Permit;
            Assert.That(permit.TryConsume(Package,"K-a1-r2"),Is.True);Assert.That(permit.TryConsume(Package,"K-a1-r2"),Is.False);
            heard.Engine.ObserveAudible(AudibleStatus.ConfirmedAudible,Evidence);
            Assert.Throws<SessionFault>(()=>heard.Engine.ObserveAudible(AudibleStatus.ConfirmedNoOnset,Evidence));
            heard.ToEnd();
            Assert.That(heard.Factory.Items.Count(x=>x.Context.Item.ContentId=="K-a1-r2"),Is.EqualTo(1));
            Assert.That(heard.Journal.Rows.Any(x=>x.Event=="retry_queued"),Is.False);
            Assert.That(new Run(schedule,heard.Journal).Engine.Status,Is.EqualTo(SessionState.Complete));

            // A proven silent first attempt is not an exposure; its single retry stays linked to the original opportunity.
            var silent=new Run(Visit("A","D0",new ScheduleBlock("novel",new[]{Item("DEMO-NV-01","novel","K-a1-r2",heldout:true)})));
            silent.Engine.ConfirmResume();silent.Step(100);silent.Clock.Time=800;silent.Engine.ObserveAudible(AudibleStatus.ConfirmedNoOnset,Evidence);
            silent.ToEnd();
            Assert.That(silent.Factory.Items.Count,Is.EqualTo(2));
            var retry=silent.Factory.Items[1].Context;
            Assert.That(retry.RetryOf,Is.EqualTo("DEMO-NV-01"));Assert.That(retry.OpportunityId,Is.EqualTo("DEMO-NV-01"));
            Assert.That(retry.Item.TrialId,Is.Not.EqualTo("DEMO-NV-01"));
            Assert.That(silent.Journal.Rows.Where(x=>x.TrialId==retry.Item.TrialId).All(x=>x.RetryOf=="DEMO-NV-01"&&x.OpportunityId=="DEMO-NV-01"),Is.True);
        }

        [Test,Category("REQ_VAL_05")]
        public void NoCueTrialPlaysNothingLogsItsScheduledReferenceAndHasNoAudibleOnset()
        {
            var items=new[]{Item("DEMO-VA-01","no_cue",null,phase:"validity"),Item("DEMO-VA-02","no_cue",null,phase:"validity")};
            var run=new Run(Visit("A","D7",new ScheduleBlock("validity",items)));
            run.Engine.ConfirmResume();run.Step(100);
            var first=run.Factory.Items[0];
            Assert.That(first.Cues,Is.EqualTo(1));Assert.That(first.Permit,Is.Null);Assert.That(first.Context.AudioRequestIds,Is.Empty);
            Assert.Throws<SessionFault>(()=>run.Engine.ObserveAudible(AudibleStatus.ConfirmedAudible,Evidence));
            Assert.Throws<SessionFault>(()=>run.Engine.ObserveAudible(AudibleStatus.ConfirmedNoOnset,Evidence));
            run.ToEnd();
            Assert.That(run.Factory.Items.Count,Is.EqualTo(2));
            foreach(var content in run.Factory.Items)
            {
                var rows=run.Journal.Rows.Where(x=>x.TrialId==content.Context.Item.TrialId).ToArray();
                Assert.That(rows,Is.Not.Empty);
                Assert.That(rows.All(x=>x.AudibleStatus==AudibleStatus.NoCue&&!x.ExposureConsumed&&x.AudioRequestIds.Count==0&&x.EvidenceSha256==null),Is.True);
                Assert.That(rows.All(x=>x.ScheduledOnsetMonoMs==content.Context.OnsetMonoMs),Is.True);
                Assert.That(rows.Any(x=>x.Event=="onset_evidence"),Is.False);
                var json=SessionRecordCodec.ToJson(rows.Last());
                Assert.That((double)json["scheduled_onset_mono_ms"],Is.EqualTo(content.Context.OnsetMonoMs));
                Assert.That((string)json["audible_status"],Is.EqualTo("NoCue"));
            }
        }

        [Test,Category("REQ_INT_01")]
        public void ResumptionRunsOnlyUnplayedTrialsAndNeverRepeatsAPlayedOrCommittedOne()
        {
            var items=Enumerable.Range(1,5).Select(i=>Item("DEMO-TR-0"+i,"trained","K-a1-r1")).ToArray();
            var schedule=Visit("A","D0",new ScheduleBlock("trained",items));
            var run=new Run(schedule);run.Engine.ConfirmResume();
            run.Until(800);Assert.That(run.Engine.CurrentState,Is.EqualTo(ItemState.ResponseOpen));run.Engine.RecordResponse("commit");
            run.Until(14800);
            Assert.That(run.Engine.CurrentTrialId,Is.EqualTo("DEMO-TR-02"));Assert.That(run.Engine.CurrentState,Is.EqualTo(ItemState.ResponseOpen));
            run.Engine.ObserveAudible(AudibleStatus.ConfirmedAudible,Evidence);run.Engine.RecordResponse("commit");
            // Interruption: the engine is lost after trial 2 played and was committed, before its fixed end.
            var second=new Run(schedule,run.Journal,run.Clock.Time);
            Assert.That(second.Engine.NeedsOperatorConfirmation,Is.True);second.Engine.ConfirmResume();
            Assert.That(second.Engine.CurrentTrialId,Is.EqualTo("DEMO-TR-03"));Assert.That(second.Factory.Items.Single().Cues,Is.Zero);
            // Interruption again while trial 3 is prepared but unplayed: it is offered again.
            var third=new Run(schedule,run.Journal,second.Clock.Time);third.Engine.ConfirmResume();
            Assert.That(third.Engine.CurrentTrialId,Is.EqualTo("DEMO-TR-03"));
            third.Step();Assert.That(third.Factory.Items.Single().Cues,Is.EqualTo(1));
            // Interruption after trial 3's cue without any response: it is never re-run.
            var fourth=new Run(schedule,run.Journal,third.Clock.Time);fourth.Engine.ConfirmResume();
            Assert.That(fourth.Engine.CurrentTrialId,Is.EqualTo("DEMO-TR-04"));
            fourth.ToEnd();
            var cued=new[]{run,second,third,fourth}.SelectMany(r=>r.Factory.Items.Where(x=>x.Cues>0)).Select(x=>x.Context.Item.TrialId).ToArray();
            Assert.That(cued,Is.EqualTo(items.Select(x=>x.TrialId)));
            Assert.That(run.Journal.Rows.Count(x=>x.Event=="response"),Is.EqualTo(2));
        }
    }
}
