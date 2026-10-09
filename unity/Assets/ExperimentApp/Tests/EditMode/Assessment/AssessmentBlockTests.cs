using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using AcousticVocab.ResponsePanel;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Assessment.Tests
{
    public sealed class AssessmentBlockTests
    {
        [Test]public void UninstalledHostCannotAbortSubsequentSharedPlayerOwner()
        {
            var playerObject=new UnityEngine.GameObject("Shared audio owner");var player=playerObject.AddComponent<AudioPlayer>();
            var root=new UnityEngine.GameObject("Released assessment owner");var host=root.AddComponent<AssessmentSessionHost>();host.player=player;
            host.Uninstall();Assert.That(host.Installed,Is.False);
            var failed=typeof(AudioPlayer).GetField("failed",System.Reflection.BindingFlags.Instance|System.Reflection.BindingFlags.NonPublic);failed.SetValue(player,false);
            host.GetType().GetMethod("OnApplicationFocus",System.Reflection.BindingFlags.Instance|System.Reflection.BindingFlags.NonPublic).Invoke(host,new object[]{false});host.enabled=false;UnityEngine.Object.DestroyImmediate(root);
            Assert.That((bool)failed.GetValue(player),Is.False);UnityEngine.Object.DestroyImmediate(playerObject);
        }
        const string Hash="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        const string Package="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
        sealed class Clock:ISessionClock { public double NowMs{get;set;} }
        sealed class Journal:ISessionJournal
        {
            public readonly List<SessionRecord> Rows=new List<SessionRecord>();public bool FailResponse;
            public IReadOnlyList<SessionRecord> Records=>Rows;
            public void Append(SessionRecord r){if(FailResponse&&r.Event=="response")throw new IOException();Rows.Add(r);}
        }
        sealed class StagesJournal:IAssessmentJournal
        {
            public readonly List<AssessmentRecord> Rows=new List<AssessmentRecord>();public bool Fail;
            public IReadOnlyList<AssessmentRecord> Records=>Rows;
            public void Append(AssessmentRecord r){if(Fail)throw new IOException();Rows.Add(r);}
        }
        sealed class Scene:IProtectedState
        {
            public bool ModeReady{get;set;}=true;public bool NeutralReady{get;set;}=true;
            public bool ResetComplete{get;set;}=true;public bool FocusOk{get;set;}=true;
            public int Resets;public void BeginTrial(){}public void RequestReset(){Resets++;}
            // One-shot work inside the content pump (private control, frame drain) that takes real time.
            public Action OnPump;public void Pump(){var work=OnPump;OnPump=null;work?.Invoke();}
        }
        sealed class Audio:IAssessmentAudio
        {
            public bool Ready{get;set;}=true;public double? QualifiedOnsetMonoMs{get;set;}
            public string FaultCode{get;set;} public bool Confirm=true;public int Prepares,Requests,Stops;
            public readonly List<string> Types=new List<string>();public INovelSlotAuthorization Novel;
            public void Prepare(SlotContext c){Prepares++;QualifiedOnsetMonoMs=null;}
            public void Request(SlotContext c,INovelSlotAuthorization novel,ISpeechSlotAuthorization speech)
            {Requests++;Types.Add(c.Item.TrialType);Novel=novel;if(Confirm)QualifiedOnsetMonoMs=c.OnsetMonoMs;}
            public void Stop(string code){Stops++;}
        }
        sealed class Panel:IAssessmentPanel
        {
            public readonly ResponseState State;public readonly List<PanelProcessEvent> Events=new List<PanelProcessEvent>();
            public bool Ready{get;set;}=true;public bool ThrowHide;public event Action<string> Responded;
            public Panel(Clock clock){State=new ResponseState(()=>clock.NowMs,Events.Add);State.Responded+=r=>Responded?.Invoke(r.Code==ResponseCode.Commit?"commit":r.Code==ResponseCode.DontKnow?"dont_know":"timeout");}
            public void Tick()=>State.Tick();public void Open(PanelRequest r)=>State.Open(r);public void Hide(){if(ThrowHide)throw new IOException();State.Abort();}
        }
        sealed class View:IAssessmentView
        {
            readonly Clock clock;public string Visible="";public readonly List<(double time,string text)> Events=new List<(double,string)>();
            public View(Clock c){clock=c;}public void Neutral(){Visible="";Events.Add((clock.NowMs,Visible));}
            public void Acknowledgment(){Visible="Response recorded";Events.Add((clock.NowMs,Visible));}
        }
        sealed class Fixture
        {
            public readonly Clock Clock=new Clock();public readonly Journal Journal=new Journal();public readonly StagesJournal StageJournal=new StagesJournal();
            public readonly Scene Scene=new Scene();public readonly Audio Audio=new Audio();public readonly Panel Panel;public readonly View View;
            public readonly AssessmentStages Stages;public readonly ProtectedContentFactory Factory;public readonly FixedSlotEngine Engine;
            public Fixture(VisitSchedule schedule=null,string type="trained",int count=1)
            {
                schedule??=Schedule(type,count);Panel=new Panel(Clock);View=new View(Clock);
                Stages=new AssessmentStages(schedule,Journal,StageJournal,Clock,()=>Engine!=null&&Engine.Status is SessionState.Paused or SessionState.Complete);
                Factory=new ProtectedContentFactory(Clock,Scene,Audio,Panel,View,Stages,null,c=>Engine.RecordResponse(c),c=>Engine.Fault(c),id=>Engine.CurrentTrialId==id&&Engine.CurrentState==ItemState.CueRequested);
                Engine=new FixedSlotEngine(schedule,Clock,Journal,Factory);
            }
            public void Start(){Engine.ConfirmResume();At(Clock.NowMs);}
            public void At(double time){Clock.NowMs=time;Engine.Tick();}
            public void Through(double time){while(Clock.NowMs<time){At(Math.Min(time,Clock.NowMs+50));}}
            public void Respond(string code="commit",string target="A")
            {
                if(code=="dont_know")Panel.State.DontKnow();else if(code=="commit")
                {if(Panel.State.Request.Role!=PanelRole.Action)Panel.State.SelectTarget(target);if(Panel.State.Request.Role!=PanelRole.Target)Panel.State.SelectAction("ADD_ONE");Panel.State.Commit();}
            }
        }
        static VisitSchedule Schedule(string type="trained",int count=1,string study="A",string visit="D0",bool demo=true)
        {
            var rows=Enumerable.Range(0,count).Select(i=>new SlotItem(type+"-"+i,type,type=="atomic"?"K-a1":"K-a1-r1",type=="atomic"?"action":null,null,
                type is "speech" or "no_cue"?"validity":"protected",type=="novel",type=="atomic"?9:14,type=="no_cue"?0:1,1)).ToArray();
            return new VisitSchedule(Hash,Package,"DEMO",visit,demo,new[]{new ScheduleBlock(type,rows)},study,"A",Hash);
        }
        static string Repo()
        {
            for(var p=new DirectoryInfo(Directory.GetCurrentDirectory());p!=null;p=p.Parent)if(File.Exists(Path.Combine(p.FullName,"schedules/schema/visit-schedule.schema.json")))return p.FullName;
            throw new Exception("Public producer schedules unavailable");
        }
        static VisitSchedule Producer(string study,string visit,bool protectedOnly=true)
        {
            string unit=study+"-C01",person=unit+(study=="A"?"-L01":"-M1"),root=Path.Combine(Repo(),"schedules/examples/demo",study,unit);
            byte[] bytes=File.ReadAllBytes(Path.Combine(root,"schedules",person,visit+".json"));var doc=JObject.Parse(System.Text.Encoding.UTF8.GetString(bytes));
            var permutation=JObject.Parse(File.ReadAllText(Path.Combine(root,"permutation.json")));
            new ScheduleSchema(File.ReadAllBytes(Path.Combine(Repo(),"schedules/schema/visit-schedule.schema.json")),ScheduleLoader.ScheduleSchemaSha256).Validate(doc);
            new ScheduleSchema(File.ReadAllBytes(Path.Combine(Repo(),"schedules/schema/permutation.schema.json")),ScheduleLoader.PermutationSchemaSha256).Validate(permutation);
            var blocks=ScheduleLoader.ValidateBlocks(doc,permutation);
            if(protectedOnly)blocks=blocks.Where(x=>x.Items.All(i=>i.Phase is "pre_test" or "protected")).ToArray();
            return new VisitSchedule(PcmWave.Hash(bytes),Package,person,visit,true,blocks,study,(string)doc["set"]);
        }
        [TestCase("A","D0",0,36,4,16,704)][TestCase("A","D7",0,36,4,16,704)]
        [TestCase("B","V1",0,4,2,8,156)][TestCase("B","V2",4,10,2,12,332)]
        [TestCase("B","V3",10,18,2,16,564)][TestCase("B","W1",0,36,4,16,704)][TestCase("B","W4",0,36,4,16,704)]
        public void ProducerProtectedVisitsRunExactCountsAndFixedTime(string study,string visit,int pre,int trained,int novel,int atomic,int seconds)
        {
            var schedule=Producer(study,visit);var rows=schedule.Blocks.SelectMany(x=>x.Items).ToArray();
            Assert.That(rows.Count(x=>x.TrialType=="pre_old"),Is.EqualTo(pre));Assert.That(rows.Count(x=>x.TrialType=="trained"),Is.EqualTo(trained));
            Assert.That(rows.Count(x=>x.TrialType=="novel"),Is.EqualTo(novel));Assert.That(rows.Count(x=>x.TrialType=="atomic"),Is.EqualTo(atomic));
            var f=new Fixture(schedule);f.Start();int boundaries=1;
            for(int guard=0;guard<30000&&f.Engine.Status!=SessionState.Complete;guard++)
            {
                if(f.Engine.Status==SessionState.Paused){f.Engine.ConfirmResume();boundaries++;}
                f.At(f.Clock.NowMs+50);
                Assert.That(f.Engine.Status,Is.Not.EqualTo(SessionState.Faulted));
            }
            Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Complete));Assert.That(f.Clock.NowMs,Is.EqualTo(seconds*1000+boundaries*750));
            Assert.That(f.Journal.Rows.Count(x=>x.Event=="response"),Is.EqualTo(rows.Length));Assert.That(f.Audio.Requests,Is.EqualTo(rows.Length));
            Assert.That(f.Audio.Types.Count(x=>x=="novel"),Is.EqualTo(novel));
            Assert.That(f.Journal.Rows.Where(x=>x.Event=="state_before"&&x.State==ItemState.CueRequested).All(x=>x.ExposureConsumed),Is.True);
        }
        // Native attempt simulation-test-B-V1-017-menu-006, trained item 3 of 4: the
        // frame's content pump began 5 ms before the anchored response deadline
        // and took 10 ms, so the engine's close sample fell after it. The panel
        // timeout at that same instant must be durably recorded before Closed.
        [TestCase("trained",12000,14000)][TestCase("atomic",7000,9000)]
        public void PanelTimeoutIsRecordedBeforeCloseWhenThePumpStraddlesTheDeadline(string type,int deadline,int slot)
        {
            const int count=4,straddled=2;var f=new Fixture(type:type,count:count);f.Start();
            for(int k=0;k<count;k++)
            {
                double onset=750+k*slot;
                if(k==straddled){f.Through(onset+deadline-5);f.Scene.OnPump=()=>f.Clock.NowMs+=10;f.At(f.Clock.NowMs);Assert.That(f.Clock.NowMs,Is.EqualTo(onset+deadline+5));}
                f.Through(onset+slot);Assert.That(f.Engine.Status,Is.Not.EqualTo(SessionState.Faulted));
                Assert.That(f.Journal.Rows.Where(x=>x.Event=="item_fault").Select(x=>x.TechnicalFaultCode),Is.Empty);
            }
            f.Through(750+count*slot+100);Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Complete));
            Assert.That(f.Journal.Rows.Where(x=>x.Event=="response").Select(x=>x.ResponseCode),Is.EqualTo(Enumerable.Repeat("timeout",count)));
            Assert.That(f.Panel.Events.Select(x=>x.Kind),Does.Not.Contain("panel_aborted"));
            Assert.That(f.Panel.Events.Count(x=>x.Kind=="timeout"),Is.EqualTo(count));
            string id=type+"-"+straddled;var rows=f.Journal.Rows.Where(x=>x.TrialId==id).ToList();
            int response=rows.FindIndex(x=>x.Event=="response"),closed=rows.FindIndex(x=>x.Event=="state_before"&&x.State==ItemState.Closed);
            Assert.That(response,Is.GreaterThanOrEqualTo(0).And.LessThan(closed),"The timeout is durable before the engine closes the response window");
            Assert.That(rows[closed].MonoMs,Is.GreaterThanOrEqualTo(750+straddled*slot+deadline));
        }
        [TestCase("trained",12000,14000)][TestCase("atomic",7000,9000)]
        public void CommitWrongDontKnowTimeoutHaveIdenticalAcknowledgmentSuffix(string type,int deadline,int end)
        {
            List<(double,string)> expected=null;
            foreach(string outcome in new[]{"correct","wrong","dont_know","timeout"})
            {
                var f=new Fixture(type:type);f.Start();f.At(750);f.At(1750);
                if(outcome!="timeout")f.Respond(outcome=="dont_know"?"dont_know":"commit",outcome=="wrong"?"B":"A");
                f.At(750+deadline-1);Assert.That(f.View.Visible,Is.Empty);f.At(750+deadline);Assert.That(f.View.Visible,Is.EqualTo("Response recorded"));
                f.At(750+end-1);Assert.That(f.View.Visible,Is.EqualTo("Response recorded"));f.At(750+end);Assert.That(f.View.Visible,Is.Empty);
                var tail=f.View.Events.Where(x=>x.time>=750+deadline).ToList();if(expected==null)expected=tail;else Assert.That(tail,Is.EqualTo(expected));
                Assert.That(f.Journal.Rows.Count(x=>x.Event=="response"),Is.EqualTo(1));Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Complete));
            }
        }
        [Test] public void PreparingNextSlotCannotErasePreviousAcknowledgment()
        {var f=new Fixture(count:2);f.Start();f.At(750);f.At(12750);Assert.That(f.Audio.Prepares,Is.EqualTo(2));Assert.That(f.View.Visible,Is.EqualTo("Response recorded"));f.Through(14749);Assert.That(f.View.Visible,Is.EqualTo("Response recorded"));f.At(14750);Assert.That(f.View.Visible,Is.Empty);}
        [Test] public void MissingQualifiedOnsetNeverOpensResponseOrCompletesAsNormal()
        {var f=new Fixture();f.Audio.Confirm=false;f.Start();f.At(750);Assert.That(f.Panel.State.Request,Is.Null);f.At(12750);Assert.That(f.Journal.Rows.Any(x=>x.TechnicalFaultCode=="ASSESSMENT_ONSET_UNCONFIRMED"),Is.True);Assert.That(f.Journal.Rows.Any(x=>x.Event=="response"),Is.False);}
        [Test] public void MismatchedQualifiedOnsetIsRejected()
        {var f=new Fixture();f.Start();f.Audio.QualifiedOnsetMonoMs=751;f.At(750);Assert.That(f.Journal.Rows.Any(x=>x.TechnicalFaultCode=="ASSESSMENT_ONSET_MISMATCH"),Is.True);}
        [Test] public void NeutralHoldLossInterruptsBeforeAnotherResponse()
        {var f=new Fixture();f.Start();f.At(750);f.Scene.NeutralReady=false;f.At(800);Assert.That(f.Panel.State.Aborted,Is.True);Assert.That(f.Journal.Rows.Any(x=>x.TechnicalFaultCode=="ASSESSMENT_NEUTRAL_LOST"),Is.True);}
        [Test] public void FailedDurableResponseNeverAcknowledges()
        {var f=new Fixture();f.Start();f.At(750);f.Journal.FailResponse=true;f.Respond();f.At(12750);Assert.That(f.View.Events.Any(x=>x.text=="Response recorded"),Is.False);Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Faulted));}
        [Test] public void ActiveDisposeStopsAudioEvenWhenPanelCleanupThrows()
        {
            var f=new Fixture(count:2);f.Start();f.At(750);f.Panel.ThrowHide=true;Assert.DoesNotThrow(()=>f.Factory.Dispose());
            Assert.That(f.Audio.Stops,Is.GreaterThan(0));Assert.That(f.View.Visible,Is.Empty);Assert.That(f.Journal.Rows.Any(x=>x.TechnicalFaultCode=="ASSESSMENT_DISPOSED"),Is.True);
            f.Through(14750);Assert.That(f.Audio.Requests,Is.EqualTo(1));Assert.Throws<AssessmentFault>(()=>f.Factory.Create(Schedule().Blocks[0].Items[0]));
        }
        [Test] public void FaultStopsAudioAndReachesEngineDespiteThrowingCleanup()
        {
            var f=new Fixture();f.Start();f.At(750);f.Panel.ThrowHide=true;f.Scene.NeutralReady=false;f.At(800);
            Assert.That(f.Audio.Stops,Is.GreaterThan(0));Assert.That(f.Journal.Rows.Any(x=>x.TechnicalFaultCode=="ASSESSMENT_NEUTRAL_LOST"),Is.True);
        }
        [Test] public void NovelPermissionIsIssuedOnceAtDurableCue()
        {var f=new Fixture(type:"novel");f.Start();Assert.That(f.Audio.Novel,Is.Not.Null);Assert.That(f.Audio.Novel.TryConsume(Package,"K-a1-r1"),Is.True);Assert.That(f.Audio.Novel.TryConsume(Package,"K-a1-r1"),Is.False);Assert.That(f.Journal.Rows.Single(x=>x.Event=="novel_buffer_authorized").ExposureConsumed,Is.True);}
        [Test] public void FormsWaitForTailBoundaryAndRemainAudioFree()
        {
            var f=new Fixture();f.Start();f.At(750);f.At(12750);Assert.That(f.Stages.ProtectedComplete,Is.True);Assert.Throws<AssessmentFault>(()=>f.Stages.BeginForms());
            f.At(14750);f.Stages.BeginForms();int plays=f.Audio.Requests;
            while(f.Stages.CurrentRating!=null)f.Stages.Rate(f.Stages.CurrentRating.Id,f.Stages.CurrentRating.Minimum);
            Assert.That(f.Stages.FormsComplete,Is.True);Assert.That(f.Audio.Requests,Is.EqualTo(plays));Assert.That(f.StageJournal.Rows.All(x=>x.Stage=="forms"),Is.True);
        }
        [TestCase("A","D0",2)][TestCase("A","D7",2)][TestCase("B","V1",5)][TestCase("B","W4",5)]
        public void RatingsHaveBoundedRangesAndRecoverExactly(string study,string visit,int count)
        {
            var schedule=Schedule(study:study,visit:visit);var f=new Fixture(schedule);f.Start();f.Through(14750);f.Stages.BeginForms();
            Assert.That(f.Stages.RatingItems.Count,Is.EqualTo(count));var first=f.Stages.CurrentRating;
            Assert.Throws<AssessmentFault>(()=>f.Stages.Rate(first.Id,first.Minimum-1));Assert.Throws<AssessmentFault>(()=>f.Stages.Rate(first.Id,first.Maximum+1));
            f.Stages.Rate(first.Id,first.Minimum);var recovered=new AssessmentStages(schedule,f.Journal,f.StageJournal,f.Clock,()=>true);
            Assert.That(recovered.CurrentRating.Id,Is.Not.EqualTo(first.Id));while(recovered.CurrentRating!=null){var item=recovered.CurrentRating;recovered.Rate(item.Id,item.Maximum);}
            Assert.That(recovered.FormsComplete,Is.True);Assert.That(f.StageJournal.Rows.Count(x=>x.EventKind=="rating"),Is.EqualTo(count));
        }
        [Test] public void UnreviewedNonDemoFormsFailClosed()
        {var f=new Fixture(Schedule(demo:false));f.Start();f.Through(14750);Assert.Throws<AssessmentFault>(()=>f.Stages.BeginForms());}
        [Test] public void ValidityRequiresFinalVisitProtectedCompletionAndForms()
        {
            foreach(var pair in new[]{("A","D0"),("A","D7"),("B","W1"),("B","W4")})
            {
                var f=new Fixture(Schedule(study:pair.Item1,visit:pair.Item2));Assert.Throws<AssessmentFault>(()=>f.Stages.RequireValidity());f.Start();f.Through(14750);Assert.Throws<AssessmentFault>(()=>f.Stages.RequireValidity());
                f.Stages.BeginForms();while(f.Stages.CurrentRating!=null)f.Stages.Rate(f.Stages.CurrentRating.Id,1);
                if(pair.Item2 is "D7" or "W4")Assert.DoesNotThrow(()=>f.Stages.RequireValidity());else Assert.Throws<AssessmentFault>(()=>f.Stages.RequireValidity());
            }
        }
        [Test] public void NoCueUsesScheduledAnchorAndNeverTouchesAudio()
        {
            var trained=Schedule().Blocks[0];var noCue=Schedule("no_cue",visit:"D7").Blocks[0];
            var schedule=new VisitSchedule(Hash,Package,"DEMO","D7",true,new[]{trained,noCue},"A","A",Hash);var f=new Fixture(schedule);
            f.Start();f.Through(14750);f.Stages.BeginForms();while(f.Stages.CurrentRating!=null)f.Stages.Rate(f.Stages.CurrentRating.Id,1);int plays=f.Audio.Requests,preloads=f.Audio.Prepares;
            f.Engine.ConfirmResume();f.At(14750);f.At(15500);Assert.That(f.Panel.State.Request.AnchorMonoMs,Is.EqualTo(15500));f.Through(29500);
            Assert.That(f.Audio.Requests,Is.EqualTo(plays));Assert.That(f.Audio.Prepares,Is.EqualTo(preloads));
            var rows=f.Journal.Rows.Where(x=>x.BlockIndex==1&&x.TrialId!=null).ToArray();Assert.That(rows.Length,Is.GreaterThan(0));Assert.That(rows.All(x=>x.AudibleStatus==AudibleStatus.NoCue&&!x.ExposureConsumed&&x.AudioRequestIds.Count==0),Is.True);
        }
        [Test] public void JournalFailureLocksLaterFormsAndOptionalOperations()
        {var f=new Fixture();f.Start();f.Through(14750);f.StageJournal.Fail=true;Assert.Throws<AssessmentFault>(()=>f.Stages.BeginForms());f.StageJournal.Fail=false;Assert.Throws<AssessmentFault>(()=>f.Stages.BeginForms());Assert.That(f.StageJournal.Rows,Is.Empty);}
        [Test] public void OptionalStageRequiresCompletedBW4ValidityAndWritesItsOwnStage()
        {
            var trained=Schedule(study:"B",visit:"W4").Blocks[0];var validity=Schedule("no_cue",study:"B",visit:"W4").Blocks[0];
            var schedule=new VisitSchedule(Hash,Package,"DEMO","W4",true,new[]{trained,validity},"B","A",Hash);var f=new Fixture(schedule);
            Assert.Throws<AssessmentFault>(()=>f.Stages.BeginOptional());f.Start();f.Through(14750);f.Stages.BeginForms();while(f.Stages.CurrentRating!=null)f.Stages.Rate(f.Stages.CurrentRating.Id,1);
            Assert.Throws<AssessmentFault>(()=>f.Stages.BeginOptional());f.Engine.ConfirmResume();f.At(14750);f.Through(27500);Assert.That(f.Stages.ValidityComplete,Is.True);
            Assert.Throws<AssessmentFault>(()=>f.Stages.BeginOptional());f.Through(29500);f.Stages.BeginOptional();f.Stages.RecordOptional("optional_help","K-a1","requested");f.Stages.RecordOptional("optional_help","K-a1","completed");
            Assert.That(f.StageJournal.Rows.Where(x=>x.EventKind.StartsWith("optional",StringComparison.Ordinal)).All(x=>x.Stage=="post_w4_optional"),Is.True);
            Assert.That(f.Journal.Rows.Count(x=>x.Event=="response"),Is.EqualTo(2));
        }
        [Test] public void UnfinishedProtectedHistoryCannotRestoreStartedForms()
        {
            var schedule=Schedule();var rows=new StagesJournal();rows.Append(new AssessmentRecord("forms_started",Hash,0,"forms"));
            Assert.Throws<AssessmentFault>(()=>new AssessmentStages(schedule,new Journal(),rows,new Clock(),()=>true));
        }
        [Test] public void CompletedTrialRecoveryRejectsUnorderedOrIncompleteFormHistory()
        {
            var schedule=Schedule();var f=new Fixture(schedule);f.Start();f.Through(14750);
            Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Complete));
            foreach(var records in new[]{
                new[]{new AssessmentRecord("forms_completed",Hash,0,"forms")},
                new[]{new AssessmentRecord("forms_started",Hash,0,"forms"),new AssessmentRecord("forms_completed",Hash,1,"forms")},
                new[]{new AssessmentRecord("forms_started",Hash,0,"forms"),new AssessmentRecord("rating",Hash,1,"forms","pleasantness",1)}})
            {
                var rows=new StagesJournal();foreach(var record in records)rows.Append(record);
                Assert.Throws<AssessmentFault>(()=>new AssessmentStages(schedule,f.Journal,rows,f.Clock,()=>true));
            }
        }
        static Fixture OptionalFixture()
        {
            var schedule=new VisitSchedule(Hash,Package,"DEMO","W4",true,new[]{Schedule(study:"B",visit:"W4").Blocks[0],Schedule("no_cue",study:"B",visit:"W4").Blocks[0]},"B","A",Hash);
            var f=new Fixture(schedule);f.Start();f.Through(14750);f.Stages.BeginForms();while(f.Stages.CurrentRating!=null)f.Stages.Rate(f.Stages.CurrentRating.Id,1);
            f.Engine.ConfirmResume();f.At(14750);f.Through(29500);f.Stages.BeginOptional();return f;
        }
        [Test] public void DictionaryAuthorityIsOneUseAndCannotSurviveAnotherConsultation()
        {
            var f=OptionalFixture();f.Stages.RecordOptional("optional_help","K-a1","requested");var first=f.Stages.DictionaryPermit("K-a1");var stale=f.Stages.DictionaryPermit("K-a1");
            Assert.That(first.TryConsume(Hash,Hash,"K-a1"),Is.False);Assert.That(first.TryConsume(Package,Hash,"K-a2"),Is.False);
            Assert.That(first.TryConsume(Package,Hash,"K-a1"),Is.True);Assert.That(first.TryConsume(Package,Hash,"K-a1"),Is.False);
            f.Stages.RecordOptional("optional_help","K-a1","completed");f.Stages.RecordOptional("optional_help","K-a1","requested");Assert.That(stale.TryConsume(Package,Hash,"K-a1"),Is.False);
        }
        [Test] public void InterruptedOptionalRequestsCannotRestartWithoutExplicitCancellation()
        {
            var f=OptionalFixture();f.Stages.RecordOptional("optional_execution","execute_A_ADD_ONE","requested");
            Assert.Throws<AssessmentFault>(()=>f.Stages.RecordOptional("optional_help","K-a1","requested"));
            Assert.Throws<AssessmentFault>(()=>f.Stages.RecordOptional("optional_execution","execute_B_ADD_ONE","completed"));
            Assert.That(f.Stages.OptionalRequestPending,Is.True);f.Stages.CancelInterruptedOptional();Assert.That(f.StageJournal.Rows.Last().OutcomeCode,Is.EqualTo("cancelled"));
            f.Stages.RecordOptional("optional_help","K-a1","requested");Assert.That(f.Stages.OptionalRequestPending,Is.True);
        }
        [Test] public void MissingReviewedSpeechBlocksWholeValidityBeforeAnyNoCueExposure()
        {
            var f=new Fixture(Producer("A","D7",false));f.Start();
            for(int i=0;i<20000&&!f.Stages.ProtectedComplete;i++){if(f.Engine.Status==SessionState.Paused)f.Engine.ConfirmResume();f.At(f.Clock.NowMs+50);}
            f.Through(f.Clock.NowMs+2000);Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Paused));f.Stages.BeginForms();while(f.Stages.CurrentRating!=null)f.Stages.Rate(f.Stages.CurrentRating.Id,1);
            f.Engine.ConfirmResume();f.Engine.Tick();Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Paused));
            Assert.That(f.Journal.Rows.Where(x=>x.BlockIndex==3).Any(x=>x.State==ItemState.CueRequested),Is.False);
        }
        [Test] public void ReviewedScriptsArePinnedClosedAndCannotInterpolateAnswers()
        {
            var scripts=new JObject();foreach(string key in new[]{"pre_old","trained","novel","atomic","validity","break","forms","post_w4_optional"})scripts[key]="DEMO "+key;
            byte[] Bytes(JObject obj)=>System.Text.Encoding.UTF8.GetBytes(obj.ToString(Newtonsoft.Json.Formatting.None));
            var doc=new JObject{["version"]=1,["scripts"]=scripts};byte[] bytes=Bytes(doc);
            var review=new JObject{["version"]=1,["approved"]=true,["scripts_sha256"]=PcmWave.Hash(bytes),["methodology_sha256"]=Hash};byte[] reviewBytes=Bytes(review);
            var loaded=AssessmentScripts.Load(bytes,PcmWave.Hash(bytes),reviewBytes,PcmWave.Hash(reviewBytes));Assert.That(loaded.For("break"),Is.EqualTo("DEMO break"));
            Assert.Throws<AssessmentFault>(()=>loaded.For("answer"));Assert.Throws<AssessmentFault>(()=>AssessmentScripts.Load(bytes,Hash,reviewBytes,PcmWave.Hash(reviewBytes)));
            review["approved"]=false;reviewBytes=Bytes(review);Assert.Throws<AssessmentFault>(()=>AssessmentScripts.Load(bytes,PcmWave.Hash(bytes),reviewBytes,PcmWave.Hash(reviewBytes)));
            review["approved"]=true;scripts["trained"]="DEMO {answer}";bytes=Bytes(doc);review["scripts_sha256"]=PcmWave.Hash(bytes);reviewBytes=Bytes(review);
            Assert.Throws<AssessmentFault>(()=>AssessmentScripts.Load(bytes,PcmWave.Hash(bytes),reviewBytes,PcmWave.Hash(reviewBytes)));
        }
        [Test] public void StageJournalRejectsEditedFinalRecordTruncationAndBlankLine()
        {
            foreach(string mutation in new[]{"edit","truncate","blank"})
            {
                string path=Path.Combine(Path.GetTempPath(),"assessment-"+Guid.NewGuid().ToString("N"),"stages.local.jsonl");
                using(var journal=new AssessmentJournal(path,Hash))journal.Append(new AssessmentRecord("forms_started",Hash,0,"forms"));
                string original=File.ReadAllText(path);File.WriteAllText(path,mutation=="edit"?original.Replace("forms_started","forms_completed"):mutation=="truncate"?"":original+"\n");
                Assert.Throws<AssessmentFault>(()=>new AssessmentJournal(path,Hash));Assert.That(File.ReadAllText(path),Is.Not.EqualTo(original));
            }
        }
        [Test] public void StageJournalExcludesSecondWriterAndReopensWithPersistentLockFile()
        {
            string path=Path.Combine(Path.GetTempPath(),"assessment-"+Guid.NewGuid().ToString("N"),"stages.local.jsonl");
            using(var first=new AssessmentJournal(path,Hash)){first.Append(new AssessmentRecord("forms_started",Hash,0,"forms"));Assert.Throws<AssessmentFault>(()=>new AssessmentJournal(path,Hash));}
            using var recovered=new AssessmentJournal(path,Hash);Assert.That(recovered.Records.Count,Is.EqualTo(1));
        }
    }
}
