using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Reflection;
using AcousticVocab.ResponsePanel;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using NUnit.Framework;

namespace AcousticVocab.Assessment.Tests
{
    // Requirement trace for protected test trials: the real engine, content factory
    // and response state with synthetic ports. DEMO content IDs only.
    public sealed class RequirementTraceAssessmentTests
    {
        const string Hash="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        const string Package="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
        const string Acknowledged="Response recorded";
        sealed class Clock:ISessionClock{public double NowMs{get;set;}}
        sealed class Journal:ISessionJournal
        {
            public readonly List<SessionRecord> Rows=new List<SessionRecord>();
            public IReadOnlyList<SessionRecord> Records=>Rows;public void Append(SessionRecord r)=>Rows.Add(r);
        }
        sealed class StagesJournal:IAssessmentJournal
        {
            public readonly List<AssessmentRecord> Rows=new List<AssessmentRecord>();
            public IReadOnlyList<AssessmentRecord> Records=>Rows;public void Append(AssessmentRecord r)=>Rows.Add(r);
        }
        sealed class Scene:IProtectedState
        {
            public readonly List<string> Calls=new List<string>();
            public bool ModeReady=>true;public bool NeutralReady=>true;public bool ResetComplete=>true;public bool FocusOk=>true;
            public void BeginTrial()=>Calls.Add("begin_trial");public void RequestReset()=>Calls.Add("request_reset");public void Pump(){}
        }
        sealed class Audio:IAssessmentAudio
        {
            public readonly List<(string trial,string audio)> Requests=new List<(string,string)>();
            public bool Ready=>true;public double? QualifiedOnsetMonoMs{get;private set;}public string FaultCode=>null;
            public void Prepare(SlotContext c){QualifiedOnsetMonoMs=null;}
            public void Request(SlotContext c,INovelSlotAuthorization novel,ISpeechSlotAuthorization speech)
            {Requests.Add((c.Item.TrialId,c.AudioRequestIds[0]));QualifiedOnsetMonoMs=c.OnsetMonoMs;}
            public void Stop(string code){}
        }
        sealed class Panel:IAssessmentPanel
        {
            public readonly ResponseState State;public readonly List<PanelRequest> Opened=new List<PanelRequest>();
            public bool Visible;public bool Ready=>true;public event Action<string> Responded;
            public Panel(Clock clock)
            {
                State=new ResponseState(()=>clock.NowMs,_=>{});
                State.Responded+=r=>Responded?.Invoke(r.Code==ResponseCode.Commit?"commit":r.Code==ResponseCode.DontKnow?"dont_know":"timeout");
            }
            public void Tick()=>State.Tick();
            public void Open(PanelRequest request){Opened.Add(request);State.Open(request);Visible=true;}
            public void Hide(){Visible=false;State.Abort();}
        }
        sealed class View:IAssessmentView
        {
            readonly Clock clock;public string Visible="";public readonly List<(double time,string text)> Events=new List<(double,string)>();
            public View(Clock clock){this.clock=clock;}
            public void Neutral(){Visible="";Events.Add((clock.NowMs,Visible));}
            public void Acknowledgment(){Visible=Acknowledged;Events.Add((clock.NowMs,Visible));}
        }
        sealed class Recording:ISlotContentFactory,ISessionContentPump
        {
            readonly ProtectedContentFactory inner;public readonly List<ISlotContent> Created=new List<ISlotContent>();
            public Recording(ProtectedContentFactory inner){this.inner=inner;}
            public ISlotContent Create(SlotItem item){var slot=inner.Create(item);Created.Add(slot);return slot;}
            public void Pump()=>inner.Pump();
        }
        sealed class Fixture
        {
            public readonly Clock Clock=new Clock();public readonly Journal Journal=new Journal();public readonly StagesJournal StageJournal=new StagesJournal();
            public readonly Scene Scene=new Scene();public readonly Audio Audio=new Audio();public readonly Panel Panel;public readonly View View;
            public readonly AssessmentStages Stages;public readonly ProtectedContentFactory Factory;public readonly Recording Slots;public readonly FixedSlotEngine Engine;
            public readonly List<string> Codes=new List<string>();
            public Fixture(VisitSchedule schedule)
            {
                Panel=new Panel(Clock);View=new View(Clock);
                Stages=new AssessmentStages(schedule,Journal,StageJournal,Clock,()=>Engine!=null&&Engine.Status is SessionState.Paused or SessionState.Complete);
                Factory=new ProtectedContentFactory(Clock,Scene,Audio,Panel,View,Stages,null,c=>{Codes.Add(c);Engine.RecordResponse(c);},c=>Engine.Fault(c),
                    id=>Engine.CurrentTrialId==id&&Engine.CurrentState==ItemState.CueRequested);
                Slots=new Recording(Factory);Engine=new FixedSlotEngine(schedule,Clock,Journal,Slots);
            }
            public void Start(){Engine.ConfirmResume();At(Clock.NowMs);}
            public void At(double time){Clock.NowMs=time;Engine.Tick();}
            public void Through(double time){while(Clock.NowMs<time)At(Math.Min(time,Clock.NowMs+50));}
            public void Commit(string target,string action){Panel.State.SelectTarget(target);Panel.State.SelectAction(action);Panel.State.Commit();}
        }
        static SlotItem Item(string id,string type,string content,string role=null)
            =>new SlotItem(id,type,content,role,null,type=="pre_old"?"pre_test":"protected",type=="novel",type=="atomic"?9:14,1,1);
        static VisitSchedule Visit(string study,string visit,params SlotItem[] items)
            =>new VisitSchedule(Hash,Package,"DEMO",visit,true,new[]{new ScheduleBlock("protected",items)},study,"A",Hash);
        static double End(SlotItem[] items)=>750+items.Sum(x=>x.SlotSeconds*1000.0);

        [Test,Category("REQ-TEST-01")]
        public void ParticipantPanelAndViewNeverReceiveTheRequestedCommand()
        {
            var items=new[]{Item("DEMO-TR-01","trained","K-a2-r3"),Item("DEMO-TR-02","trained","Q-a4-r1"),Item("DEMO-NV-01","novel","K-a1-r2"),Item("DEMO-AT-01","atomic","Q-a3","action")};
            var f=new Fixture(Visit("A","D0",items));f.Start();f.Through(End(items));
            Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Complete));
            Assert.That(f.Panel.Opened.Select(x=>x.TrialId),Is.EqualTo(items.Select(x=>x.TrialId)));
            // The panel request carries an opaque trial ID, its mode, role and times only.
            Assert.That(typeof(PanelRequest).GetProperties().Select(p=>p.Name).OrderBy(x=>x,StringComparer.Ordinal),
                Is.EqualTo(new[]{"AnchorMonoMs","DeadlineMonoMs","Mode","OpensMonoMs","Role","SlotEndMonoMs","TrialId"}));
            foreach(var request in f.Panel.Opened)foreach(var item in items)Assert.That(request.TrialId,Does.Not.Contain(item.ContentId));
            // The participant view has no input through which a command could be shown.
            Assert.That(typeof(IAssessmentView).GetMethods().All(m=>m.GetParameters().Length==0),Is.True);
            Assert.That(f.View.Events.Select(x=>x.text).Distinct(),Is.SubsetOf(new[]{"",Acknowledged}));
            // Panel input yields response codes only; it never selects the next request.
            Assert.That(f.Codes,Is.EqualTo(Enumerable.Repeat("timeout",items.Length)));
        }

        [Test,Category("REQ-TEST-02")]
        public void EachTestTrialRequestsItsAudioExactlyOnceAndCannotBeCuedAgain()
        {
            var items=new[]{Item("DEMO-PO-01","pre_old","K-a1-r1"),Item("DEMO-TR-01","trained","K-a2-r3"),Item("DEMO-NV-01","novel","K-a1-r2"),Item("DEMO-AT-01","atomic","Q-r2","referent")};
            var f=new Fixture(Visit("B","V2",items));f.Start();f.At(750);f.At(1000);
            Assert.That(f.Audio.Requests.Count,Is.EqualTo(1));
            Assert.Throws<AssessmentFault>(()=>f.Slots.Created[0].RequestCue(default,null));
            f.Commit("A","ADD_ONE");
            f.Through(End(items));
            Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Complete));
            Assert.That(f.Audio.Requests.Select(x=>x.trial),Is.EqualTo(items.Select(x=>x.TrialId)));
            Assert.That(f.Audio.Requests.Select(x=>x.audio).Distinct().Count(),Is.EqualTo(items.Length));
            foreach(var slot in f.Slots.Created)Assert.Throws<AssessmentFault>(()=>slot.RequestCue(default,null));
            Assert.That(f.Audio.Requests.Count,Is.EqualTo(items.Length));
            // A test trial scheduled with more than one play is refused before any audio.
            Assert.Throws<AssessmentFault>(()=>f.Factory.Create(new SlotItem("DEMO-TR-09","trained","K-a1-r1",null,null,"protected",false,14,2,1)));
        }

        [Test,Category("REQ-TEST-07")]
        public void ResponseTimingIsAnchoredToTheQualifiedAudioOnsetOnTheHostMonotonicClock()
        {
            var f=new Fixture(Visit("A","D0",Item("DEMO-TR-01","trained","K-a1-r1")));f.Start();f.At(750);
            var request=f.Panel.Opened.Single();
            Assert.That(request.AnchorMonoMs,Is.EqualTo(f.Audio.QualifiedOnsetMonoMs));
            Assert.That(request.OpensMonoMs,Is.EqualTo(request.AnchorMonoMs));
            Assert.That(request.DeadlineMonoMs-request.AnchorMonoMs,Is.EqualTo(12000));
            f.At(4321);f.Commit("A","ADD_ONE");
            Assert.That(f.Panel.State.Result.ResponseMonoMs-request.AnchorMonoMs,Is.EqualTo(3571));
            // Panel and audio clocks are the host Stopwatch; neither timing assembly references the simulation state stream.
            double before=Stopwatch.GetTimestamp()*1000.0/Stopwatch.Frequency;double panel=PanelJournal.NowMs;double audio=AudioPlayer.Now*1000;
            double after=Stopwatch.GetTimestamp()*1000.0/Stopwatch.Frequency;
            Assert.That(panel,Is.InRange(before,after));Assert.That(audio,Is.InRange(before-1,after+1));
            foreach(var assembly in new[]{typeof(ResponseState).Assembly,typeof(FixedSlotEngine).Assembly,typeof(AudioPlayer).Assembly})
                Assert.That(assembly.GetReferencedAssemblies().Any(x=>x.Name.Contains("StateSources")),Is.False,assembly.GetName().Name);
        }

        [Test,Category("REQ-RESP-01")]
        public void EveryTestTrialOffersAllTargetsWithTheirFamilyActionsAtEveryVisit()
        {
            foreach(var (study,visit) in new[]{("A","D0"),("A","D7"),("B","V1"),("B","V2"),("B","V3"),("B","W1"),("B","W4")})
            {
                var items=new[]{Item("DEMO-PO-01","pre_old","K-a1-r1"),Item("DEMO-TR-01","trained","Q-a2-r3"),Item("DEMO-NV-01","novel","K-a1-r2")};
                var f=new Fixture(Visit(study,visit,items));f.Start();
                for(int i=0;i<items.Length;i++)
                {
                    f.Through(750+14000*i);var state=f.Panel.State;
                    Assert.That(state.Request.TrialId,Is.EqualTo(items[i].TrialId));Assert.That(state.Request.Mode,Is.EqualTo(PanelMode.FullMessage));
                    Assert.That(state.Request.Role,Is.EqualTo(PanelRole.Command));
                    Assert.That(state.SelectedTarget,Is.Null);Assert.That(state.SelectedAction,Is.Null);Assert.That(state.CanCommit,Is.False);
                    var offered=new List<(string target,string action)>();
                    foreach(string target in PublicCommands.Targets)
                    {
                        Assert.That(state.SelectedTarget==target||state.SelectTarget(target),Is.True,study+visit+target);
                        foreach(string action in PublicCommands.Actions)if(state.SelectedAction==action||state.SelectAction(action))offered.Add((target,action));
                    }
                    Assert.That(offered.Count,Is.EqualTo(32),study+visit);
                    Assert.That(offered.GroupBy(x=>x.target).Select(g=>g.Count()),Is.All.EqualTo(4));
                    Assert.That(offered.All(x=>PublicCommands.Family(x.target)==PublicCommands.ActionFamily(x.action)),Is.True);
                }
            }
        }

        [Test,Category("REQ-RESP-02")]
        public void OfferedActionsDependOnlyOnTheSelectedTargetFamilyNotOnTheRequest()
        {
            var items=new[]{"K-a1-r1","K-a4-r4","Q-a1-r1","Q-a3-r2"}.Select((c,i)=>Item("DEMO-TR-0"+(i+1),"trained",c)).ToArray();
            var f=new Fixture(Visit("A","D0",items));f.Start();
            var maps=new List<Dictionary<string,string[]>>();
            for(int i=0;i<items.Length;i++)
            {
                f.Through(750+14000*i);var state=f.Panel.State;Assert.That(state.Request.TrialId,Is.EqualTo(items[i].TrialId));
                var map=new Dictionary<string,string[]>();
                foreach(string target in PublicCommands.Targets)
                {
                    if(state.SelectedTarget!=target)state.SelectTarget(target);
                    var offered=new List<string>();
                    foreach(string action in PublicCommands.Actions)if(state.SelectedAction==action||state.SelectAction(action))offered.Add(action);
                    map[target]=offered.ToArray();
                    Assert.That(map[target],Is.EqualTo(PublicCommands.Actions.Skip(PublicCommands.Family(target)*4).Take(4).ToArray()));
                }
                maps.Add(map);
            }
            for(int i=1;i<maps.Count;i++)foreach(string target in PublicCommands.Targets)Assert.That(maps[i][target],Is.EqualTo(maps[0][target]));
        }

        [Test,Category("REQ-NEUT-01")]
        public void SceneReceivesTheSameNeutralSequenceForEveryRequestAndResponse()
        {
            List<string> expected=null;
            foreach(string content in new[]{"K-a1-r1","Q-a4-r3"})
            foreach(string outcome in new[]{"commit_A","commit_H","dont_know","timeout"})
            {
                var f=new Fixture(Visit("A","D0",Item("DEMO-TR-01","trained",content)));f.Start();f.At(750);f.At(1750);
                if(outcome=="commit_A")f.Commit("A","ADD_ONE");else if(outcome=="commit_H")f.Commit("H","QUARANTINE");else if(outcome=="dont_know")f.Panel.State.DontKnow();
                f.Through(14750);
                Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Complete));
                if(expected==null)expected=f.Scene.Calls.ToList();else Assert.That(f.Scene.Calls,Is.EqualTo(expected),content+outcome);
            }
            Assert.That(expected,Is.EqualTo(new[]{"begin_trial","request_reset"}));
            // The protected scene port only takes neutral reset requests: no command, target or outcome can reach it.
            Assert.That(typeof(IProtectedState).GetMethods().Where(m=>!m.IsSpecialName).All(m=>m.GetParameters().Length==0),Is.True);
        }

        [Test,Category("REQ-ACC-01")]
        public void NoDictionaryIsAvailableWhileATestTrialRuns()
        {
            var items=new[]{Item("DEMO-TR-01","trained","K-a1-r1"),Item("DEMO-TR-02","trained","Q-a1-r1")};
            var f=new Fixture(Visit("B","W4",items));
            Assert.Throws<AssessmentFault>(()=>f.Stages.DictionaryPermit("K-a1"));
            f.Start();
            foreach(double time in new[]{750d,5000,12750,14000,20500})
            {
                f.Through(time);Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Running));
                Assert.Throws<AssessmentFault>(()=>f.Stages.BeginOptional());
                Assert.Throws<AssessmentFault>(()=>f.Stages.DictionaryPermit("K-a1"));
                Assert.That(f.Stages.OptionalStarted,Is.False);
            }
            // The test content factory refuses lesson or dictionary presentations.
            Assert.Throws<AssessmentFault>(()=>f.Factory.Create(new SlotItem("DEMO-ML-01","message_lesson","K-a1-r1",null,"dictionary","protected",false,24,3,1)));
        }

        [Test,Category("REQ-ACC-02")]
        public void NoReplayCanBeRequestedDuringATestTrial()
        {
            var items=new[]{Item("DEMO-TR-01","trained","K-a1-r1"),Item("DEMO-AT-01","atomic","K-a2","action")};
            var f=new Fixture(Visit("A","D0",items));f.Start();f.At(750);f.At(2000);
            Assert.Throws<AssessmentFault>(()=>f.Slots.Created[0].RequestCue(default,null));
            foreach(string code in new[]{"replay","repeat","play_again"})Assert.Throws<SessionFault>(()=>f.Engine.RecordResponse(code));
            f.Panel.State.DontKnow();
            f.Through(End(items));
            Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Complete));
            Assert.That(f.Audio.Requests.Select(x=>x.trial),Is.EqualTo(items.Select(x=>x.TrialId)));
            // The response state's only operations: open, tick, select, commit, don't know, abort.
            Assert.That(typeof(ResponseState).GetMethods(BindingFlags.Public|BindingFlags.Instance|BindingFlags.DeclaredOnly).Where(m=>!m.IsSpecialName)
                .Select(m=>m.Name).OrderBy(x=>x,StringComparer.Ordinal),Is.EqualTo(new[]{"Abort","Commit","DontKnow","Open","SelectAction","SelectTarget","Tick"}));
        }

        [Test,Category("REQ-ACC-03")]
        public void NoCorrectiveFeedbackFollowsAResponse()
        {
            List<string> expected=null;
            foreach(string outcome in new[]{"commit_A","commit_H","dont_know"})
            {
                var f=new Fixture(Visit("A","D0",Item("DEMO-TR-01","trained","K-a1-r1")));f.Start();f.At(750);f.At(1750);
                if(outcome=="commit_A")f.Commit("A","ADD_ONE");else if(outcome=="commit_H")f.Commit("H","QUARANTINE");else f.Panel.State.DontKnow();
                Assert.That(f.Panel.Visible,Is.False);Assert.That(f.View.Visible,Is.Empty);
                var trace=new List<string>();
                for(double time=1800;time<=14750;time+=50){f.At(time);trace.Add(f.View.Visible+"|"+f.Panel.Visible);}
                if(expected==null)expected=trace;else Assert.That(trace,Is.EqualTo(expected),outcome);
                Assert.That(f.View.Events.Select(x=>x.text).Distinct(),Is.SubsetOf(new[]{"",Acknowledged}));
            }
        }

        [TestCase("action",PanelRole.Action)][TestCase("referent",PanelRole.Target)]
        [Category("REQ-BAT-05")]
        public void AtomicProbeUsesANineSecondSlotShowsTheProbedRoleAndOffersItsEightLabels(string role,PanelRole expected)
        {
            var items=new[]{Item("DEMO-AT-01","atomic",role=="action"?"K-a2":"Q-r3",role),Item("DEMO-AT-02","atomic",role=="action"?"Q-a1":"K-r1",role)};
            var f=new Fixture(Visit("A","D0",items));f.Start();f.At(750);
            var request=f.Panel.State.Request;
            Assert.That(request.Mode,Is.EqualTo(PanelMode.AtomicProbe));Assert.That(request.Role,Is.EqualTo(expected));
            Assert.That(request.DeadlineMonoMs-request.AnchorMonoMs,Is.EqualTo(7000));Assert.That(request.SlotEndMonoMs-request.AnchorMonoMs,Is.EqualTo(9000));
            var labels=expected==PanelRole.Action?PublicCommands.Actions:PublicCommands.Targets;
            var accepted=labels.Where(l=>expected==PanelRole.Action?f.Panel.State.SelectAction(l):f.Panel.State.SelectTarget(l)).ToArray();
            Assert.That(accepted,Is.EqualTo(labels.ToArray()));
            Assert.That(expected==PanelRole.Action?f.Panel.State.SelectTarget("A"):f.Panel.State.SelectAction("ADD_ONE"),Is.False);
            f.Through(750+7000-1);Assert.That(f.View.Visible,Is.Empty);
            f.Through(750+7000);Assert.That(f.View.Visible,Is.EqualTo(Acknowledged));
            f.Through(750+9000);Assert.That(f.Panel.State.Request.TrialId,Is.EqualTo("DEMO-AT-02"));Assert.That(f.Panel.State.Request.AnchorMonoMs,Is.EqualTo(9750));
            f.Through(End(items));Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Complete));Assert.That(f.Clock.NowMs,Is.EqualTo(750+18000));
        }

        [Test,Category("REQ-VOC-02")]
        public void TestResponsesHaveNoPathToStoredVocabularyEntries()
        {
            // Stored vocabulary entries live in the selection-menu store; the test runtime cannot reference it.
            foreach(var assembly in new[]{typeof(ProtectedContentFactory).Assembly,typeof(ResponseState).Assembly,typeof(FixedSlotEngine).Assembly})
                Assert.That(assembly.GetReferencedAssemblies().Any(x=>x.Name=="AcousticVocab.SelectionMenus"),Is.False,assembly.GetName().Name);
            // The assessment's selection view is read-only.
            Assert.That(typeof(IAssessmentSelections).GetProperties().All(p=>!p.CanWrite),Is.True);
            Assert.That(typeof(IAssessmentSelections).GetMethods().Where(m=>!m.IsSpecialName).Select(m=>m.Name),Is.EqualTo(new[]{"For"}));
            // Every committed test response leaves the content factory as a bare code without its tuple.
            var items=PublicCommands.Targets.Select((t,i)=>Item("DEMO-TR-"+(i+1).ToString("D2"),"trained","K-a1-r1")).ToArray();
            var f=new Fixture(Visit("B","W1",items));f.Start();
            for(int i=0;i<items.Length;i++)
            {
                f.Through(750+14000*i+1000);string target=PublicCommands.Targets[i];
                f.Commit(target,PublicCommands.Actions[PublicCommands.Family(target)*4]);
            }
            f.Through(End(items));
            Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Complete));
            Assert.That(f.Codes,Is.EqualTo(Enumerable.Repeat("commit",items.Length)));
        }
    }
}
