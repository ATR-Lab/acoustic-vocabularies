using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.DataLogging;
using AcousticVocab.Foundation;
using AcousticVocab.FrameBudget;
using AcousticVocab.ResponsePanel;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using NUnit.Framework;
using Newtonsoft.Json.Linq;
using UnityEngine;
namespace AcousticVocab.SessionIntegration.Tests
{
    // #81 software fault hooks. The committed plan/receipt fixtures are shared
    // with tests/test_mock_visit_fault_harness.py, which validates the same
    // bytes with tools/mock_visit/faults.py. Synthetic; no native run evidence.
    public sealed class SimulationFaultInjectionTests
    {
        static readonly string[] Scenarios={"audio_underrun","corrupt_file_hash","failed_reset","headset_disconnect","input_loss","missing_response_log","presentation_stall"};
        const string Planned="DEMO-novel-01";
        static readonly string Config=new string('c',64),Schedule=new string('d',64),Nonce=new string('e',32);
        string root;SimulationTestAuthority authority;readonly List<GameObject> objects=new List<GameObject>();
        static string Fixtures=>Path.GetFullPath(Path.Combine(Application.dataPath,"..","..","tests","fixtures","simulation_faults"));
        static byte[] PlanBytes(string scenario)=>File.ReadAllBytes(Path.Combine(Fixtures,"plans",scenario+".json"));
        [SetUp]public void Setup()
        {
            root=Path.Combine(Path.GetTempPath(),".local","simulation-test-fault-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(root);
            // The receipt fixtures pin this exact capability hash ('f'*64) as data;
            // the authority below supplies only the compiled capability gate.
            byte[] raw=Encoding.UTF8.GetBytes(new JObject{["version"]=1,["scope"]="SIMULATION_TEST",["fixture_set_sha256"]=new string('a',64),["package_sha256"]=new string('b',64),["schedule_sha256"]=Schedule,["build_id"]="mock-build",["protocol_version"]="simulation-test-v1",["output_directory"]=Path.Combine(root,"evidence"),["audio_gain"]=.05,["participant_admission"]=false,["acoustic_qualification"]=false}.ToString());
            string path=Path.Combine(root,"capability.json");File.WriteAllBytes(path,raw);authority=SimulationTestAuthority.Load(path,PcmWave.Hash(raw),"mock-build","simulation-test-v1");
        }
        [TearDown]public void Cleanup(){foreach(var o in objects)if(o!=null)UnityEngine.Object.DestroyImmediate(o);objects.Clear();if(Directory.Exists(root))Directory.Delete(root,true);}
        SimulationFaultPlan Plan(string scenario,byte[] raw=null){raw??=PlanBytes(scenario);return SimulationFaultPlan.Parse(authority,raw,PcmWave.Hash(raw),Config,Schedule,new[]{Planned,"DEMO-other"});}
        sealed class Engine:IFaultEngineView{public string CurrentOpportunityId{get;set;}public ItemState? CurrentState{get;set;}public SessionState Status{get;set;}=SessionState.Running;}
        sealed class Clock{public double Mono=1000;public DateTime Utc=new DateTime(2026,10,7,0,0,1,DateTimeKind.Utc);public void Advance(){Mono=1500;Utc=Utc.AddMilliseconds(500);}}
        sealed class Targets:ISimulationFaultTargets
        {
            public Func<string,FaultStep> Next;public int Steps,Disarms;public JObject AppliedParameters{get;set;}
            public FaultStep Step(string scenario,Func<bool> plannedCurrent){Steps++;return Next(scenario);}
            public void Disarm(string scenario)=>Disarms++;
        }
        static JObject Parameters(string scenario)=>scenario switch
        {
            "presentation_stall"=>new JObject{["stall_ms"]=400},"audio_underrun"=>new JObject{["audio_thread_stall_ms"]=500},
            "corrupt_file_hash"=>new JObject{["flipped_byte_offset"]=44},"missing_response_log"=>new JObject{["refused_event_type"]="panel_response"},
            "failed_reset"=>new JObject{["withheld_request_id"]=new string('9',32)},"input_loss"=>new JObject{["suppress_ms"]=3000},
            _=>new JObject{["dispatch"]="OnApplicationFocus(false)"},
        };
        (SimulationFaultInjector injector,string path,List<JObject> audit) Injector(SimulationFaultPlan plan,Targets targets,Engine engine,Clock clock)
        {
            string path=Path.Combine(root,SimulationFaultInjector.ReceiptName);var audit=new List<JObject>();
            return (new SimulationFaultInjector(authority,plan,targets,engine,path,Nonce,()=>clock.Mono,()=>clock.Utc,()=>new string('1',64),audit.Add),path,audit);
        }
        static JObject Receipt(string path)=>JObject.Parse(File.ReadAllText(path));

        [Test]public void EveryCommittedFaultsPyPlanParsesAndBindsToTheLoadedRun()
        {
            Assert.That(Directory.GetFiles(Path.Combine(Fixtures,"plans"),"*.json").Select(Path.GetFileNameWithoutExtension).OrderBy(x=>x),Is.EqualTo(Scenarios));
            foreach(string scenario in Scenarios)
            {
                var plan=Plan(scenario);Assert.That(plan.Scenario,Is.EqualTo(scenario));Assert.That(plan.Hook,Is.EqualTo(SimulationFaultPlan.Hooks[scenario]));
                Assert.That(plan.RawSha256,Is.EqualTo(PcmWave.Hash(PlanBytes(scenario))));Assert.That(plan.PlannedOpportunityId,Is.EqualTo(scenario=="headset_disconnect"?null:Planned));
            }
        }
        [TestCase("extra")][TestCase("float_version")][TestCase("config")][TestCase("schedule")][TestCase("unknown_opportunity")][TestCase("null_opportunity")]
        [TestCase("duplicate_codes")][TestCase("short_window")][TestCase("scenario")][TestCase("pin")][TestCase("authority")]
        public void PlansOutsideTheFaultsPyContractOrThisRunAreRefused(string change)
        {
            var value=JObject.Parse(Encoding.UTF8.GetString(PlanBytes("presentation_stall")));string config=Config,schedule=Schedule;var auth=authority;
            switch(change)
            {
                case "extra":value["approved"]=true;break;case "float_version":value["version"]=1.0;break;case "config":config=new string('0',64);break;
                case "schedule":schedule=new string('0',64);break;case "unknown_opportunity":value["planned_opportunity_id"]="DEMO-absent";break;
                case "null_opportunity":value["planned_opportunity_id"]=null;break;case "duplicate_codes":value["expected_native_codes"]=new JArray("A","A");break;
                case "short_window":value["minimum_observation_ms"]=999;break;case "scenario":value["scenario"]="startup_preflight";break;case "authority":auth=null;break;
            }
            byte[] raw=Encoding.UTF8.GetBytes(value.ToString());string pin=change=="pin"?new string('0',64):PcmWave.Hash(raw);
            Assert.That(Assert.Throws<SessionFault>(()=>SimulationFaultPlan.Parse(auth,raw,pin,config,schedule,new[]{Planned})).Code,Is.EqualTo("JOIN_SIMULATION_FAULT_PLAN_INVALID"));
        }
        [Test]public void HooksAndInjectorRequireTheCompiledSimulationAuthority()
        {
            Assert.That(SimulationTestAuthority.CompiledCapability,Is.True,"Editor tests compile the capability; participant players do not (AV_SIMULATION_TEST is simulation-build only).");
            var plan=Plan("presentation_stall");var clock=new Clock();
            Assert.Throws<SessionFault>(()=>new SimulationFaultInjector(null,plan,new Targets(),new Engine(),Path.Combine(root,"x.json"),Nonce,()=>clock.Mono,()=>clock.Utc,()=>null,_=>{}));
            Assert.Throws<SessionFault>(()=>new NativeFaultTargets(null,null,null,null,null,()=>null,null));
            var go=new GameObject("Simulation fault player");objects.Add(go);var player=go.AddComponent<AudioPlayer>();
            Assert.That(Assert.Throws<AudioFault>(()=>player.SimulationStallAudioThread(null,500)).Code,Is.EqualTo("AUDIO_SIMULATION_AUTHORITY"));
            Assert.That(player.SimulationStallAudioThread(authority,500),Is.EqualTo("AUDIO_NOT_DELIVERING"),"No cue is delivering, so nothing is paused");
            Assert.Throws<AudioFault>(()=>player.SimulationStallAudioThread(authority,100));
            var panelObject=new GameObject("Simulation fault panel");objects.Add(panelObject);var panel=panelObject.AddComponent<ResponsePanelController>();
            Assert.Throws<InvalidOperationException>(()=>panel.SimulationSuppressInput(null,3000));Assert.Throws<InvalidOperationException>(()=>panel.SimulationSuppressInput(authority,50));
            using var journal=new DataJournal(Path.Combine(root,"data-gate"),Identity,Guid.NewGuid().ToString("N"),()=>1);
            Assert.Throws<DataFault>(()=>journal.SimulationRefuseNext(null,"panel_response",()=>true,()=>{}));
        }
        static IEnumerable<string> All=>Scenarios;
        [TestCaseSource(nameof(All))]public void AppliedHookWritesTheExactReceiptFaultsPyComposesFrom(string scenario)
        {
            var clock=new Clock();var engine=new Engine{CurrentOpportunityId=scenario=="headset_disconnect"?null:Planned,CurrentState=scenario=="headset_disconnect"?(ItemState?)null:ItemState.ResponseOpen};
            var targets=new Targets{Next=s=>{clock.Advance();return FaultStep.Applied(Parameters(s));}};
            var (injector,path,audit)=Injector(Plan(scenario),targets,engine,clock);
            injector.Tick();Assert.That(injector.Written,Is.True);
            var expected=JObject.Parse(File.ReadAllText(Path.Combine(Fixtures,"receipts",scenario+".json")));
            // The loaded capability's raw pin depends on its temporary output
            // path; the fixture carries 'f'*64 as that opaque pin.
            var actual=Receipt(path);Assert.That((string)actual["simulation_capability_sha256"],Is.EqualTo(authority.RawSha256));
            actual["simulation_capability_sha256"]=new string('f',64);
            Assert.That(JToken.DeepEquals(actual,expected),Is.True,actual.ToString());
            Assert.That(File.ReadAllText(path).Contains("\r"),Is.False);Assert.That(File.Exists(path+".pending"),Is.False);
            Assert.That(audit.Select(x=>(string)x["kind"]),Is.EqualTo(new[]{"injection_requested","injection_resolved"}));Assert.That(targets.Disarms,Is.EqualTo(1));
            injector.Tick();injector.Close();Assert.That(targets.Steps,Is.EqualTo(1),"A case is injected at most once");
        }
        [TestCase("presentation_stall",ItemState.CueRequested)][TestCase("input_loss",ItemState.CueRequested)][TestCase("missing_response_log",ItemState.Reset)]
        [TestCase("audio_underrun",ItemState.Ready)][TestCase("failed_reset",ItemState.Reset)]
        public void HooksWaitForTheirPlannedNativeIntervalAndOtherwiseRecordAMissedWindow(string scenario,ItemState state)
        {
            var clock=new Clock();var engine=new Engine{CurrentOpportunityId=Planned,CurrentState=state};var targets=new Targets{Next=_=>throw new AssertionException("not eligible")};
            var (injector,path,_)=Injector(Plan(scenario),targets,engine,clock);
            injector.Tick();Assert.That(injector.Written,Is.False);engine.CurrentOpportunityId="DEMO-other";engine.CurrentState=ItemState.ResponseOpen;injector.Tick();
            var receipt=Receipt(path);Assert.That((string)receipt["outcome"],Is.EqualTo("not_applied"));Assert.That((string)receipt["refusal_code"],Is.EqualTo("SIMULATION_FAULT_WINDOW_MISSED"));
            Assert.That(receipt["applied_opportunity_id"].Type,Is.EqualTo(JTokenType.Null));Assert.That(targets.Steps,Is.Zero);
        }
        [Test]public void PendingHookKeepsFirstRequestTimeAndRefusalIsNeverSuccess()
        {
            var clock=new Clock();var engine=new Engine{CurrentOpportunityId=Planned,CurrentState=ItemState.CueRequested};int calls=0;
            var targets=new Targets{Next=_=>{calls++;if(calls<3)return FaultStep.Pending;clock.Advance();return FaultStep.Applied(new JObject{["audio_thread_stall_ms"]=500});}};
            var (injector,path,_)=Injector(Plan("audio_underrun"),targets,engine,clock);
            injector.Tick();injector.Tick();Assert.That(injector.Written,Is.False);injector.Tick();
            var receipt=Receipt(path);Assert.That((double)receipt["requested_host_mono_ms"],Is.EqualTo(1000));Assert.That((double)receipt["observed_host_mono_ms"],Is.EqualTo(1500));
            Assert.That((string)receipt["outcome"],Is.EqualTo("applied"));Assert.That((string)receipt["simulation_capability_sha256"],Is.EqualTo(authority.RawSha256));

            File.Delete(path);var refused=new Targets{Next=_=>throw new FrameFault("FRAME_INJECTION_REFUSED")};
            var (second,secondPath,_)=Injector(Plan("presentation_stall"),refused,new Engine{CurrentOpportunityId=Planned,CurrentState=ItemState.ResponseOpen},clock);
            second.Tick();var r=Receipt(secondPath);Assert.That((string)r["outcome"],Is.EqualTo("not_applied"));Assert.That((string)r["refusal_code"],Is.EqualTo("FRAME_INJECTION_REFUSED"));
            Assert.That(r["applied_opportunity_id"].Type,Is.EqualTo(JTokenType.Null));
        }
        [Test]public void CloseNeverInventsSuccessAndNeverOverwrites()
        {
            var clock=new Clock();var (injector,path,_)=Injector(Plan("presentation_stall"),new Targets{Next=_=>throw new AssertionException("unreached")},new Engine{Status=SessionState.AwaitingOperator},clock);
            injector.Tick();injector.Close();var receipt=Receipt(path);
            Assert.That((string)receipt["outcome"],Is.EqualTo("not_applied"));Assert.That((string)receipt["refusal_code"],Is.EqualTo("SIMULATION_FAULT_NOT_REACHED"));
            byte[] bytes=File.ReadAllBytes(path);injector.Close();injector.Tick();Assert.That(File.ReadAllBytes(path),Is.EqualTo(bytes));

            File.Delete(path);var armed=new Targets{Next=_=>FaultStep.Pending};
            var (pending,pendingPath,_)=Injector(Plan("input_loss"),armed,new Engine{CurrentOpportunityId=Planned,CurrentState=ItemState.ResponseOpen},clock);
            pending.Tick();pending.Close();Assert.That((string)Receipt(pendingPath)["outcome"],Is.EqualTo("unknown"));

            File.Delete(pendingPath);var fired=new Targets{Next=_=>FaultStep.Pending,AppliedParameters=new JObject{["refused_event_type"]="panel_response"}};
            var (late,latePath,_)=Injector(Plan("missing_response_log"),fired,new Engine{CurrentOpportunityId=Planned,CurrentState=ItemState.ResponseOpen},clock);
            late.Tick();late.Close();var lateReceipt=Receipt(latePath);
            Assert.That((string)lateReceipt["outcome"],Is.EqualTo("applied"));Assert.That((string)lateReceipt["applied_opportunity_id"],Is.EqualTo(Planned));
        }
        [Test]public void ArmedCorruptionResolvesFromTheReadCallbackEvenAfterThePlannedItemFails()
        {
            var clock=new Clock();var engine=new Engine{CurrentOpportunityId="DEMO-other",CurrentState=ItemState.Done};bool fired=false;
            var targets=new Targets{Next=_=>{if(!fired)return FaultStep.Pending;clock.Advance();return FaultStep.Applied(new JObject{["flipped_byte_offset"]=44});}};
            var (injector,path,audit)=Injector(Plan("corrupt_file_hash"),targets,engine,clock);
            injector.Tick();Assert.That((string)audit.Single()["kind"],Is.EqualTo("injection_requested"));Assert.That(injector.Written,Is.False);
            // The planned Prepare read failed within one engine Tick: the
            // injector never observes it as current, only the hook's callback.
            fired=true;engine.CurrentOpportunityId=null;injector.Tick();
            var receipt=Receipt(path);Assert.That((string)receipt["outcome"],Is.EqualTo("applied"));Assert.That((string)receipt["applied_opportunity_id"],Is.EqualTo(Planned));
        }
        static DataIdentity Identity=>new DataIdentity(new string('1',32),"DEMO-fault","D0","station-01","simulation-test-v1",new string('a',64));
        [Test]public void RefusedResponseWriteKeepsTheCommittedPrefixAndLatchesTheJournal()
        {
            string fresh=Path.Combine(root,"data");int applied=0;bool planned=false;string committed;
            using(var journal=new DataJournal(fresh,Identity,Guid.NewGuid().ToString("N"),()=>1))
            {
                journal.Append(DataObservations.Device(null,"focus",1,true));
                journal.SimulationRefuseNext(authority,"panel_response",()=>planned,()=>applied++);
                // Other event types keep the ordinary append path while armed.
                committed=journal.Append(DataObservations.Device(null,"focus",1,true)).Sha256;Assert.That(applied,Is.Zero);
                planned=true;
                var error=Assert.Throws<DataFault>(()=>journal.Append(new EventDraft("panel_response",new EventContext(Planned,Planned),new JObject{["kind"]="response",["observed_mono_ms"]=1,["mode"]="FullMessage",["role"]="Command",["input"]=null,["selected_target"]="A",["selected_action"]="ADD_ONE",["response_code"]="commit",["response_target"]="A",["response_action"]="ADD_ONE"})));
                Assert.That(error.Message,Does.Contain("DATA_APPEND_FAILED"));Assert.That(applied,Is.EqualTo(1));Assert.That(journal.Failed,Is.True);
                Assert.Throws<DataFault>(()=>journal.Append(DataObservations.Device(null,"focus",1,false)),"The ordinary failure latch refuses later rows");
            }
            var kept=DataJournal.Verify(fresh,Identity).Records;Assert.That(kept.Count,Is.EqualTo(2));Assert.That(kept.Last().Sha256,Is.EqualTo(committed));
        }
    }
}
