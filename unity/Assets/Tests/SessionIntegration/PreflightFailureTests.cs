using System;
using System.IO;
using System.Reflection;
using AcousticVocab.SessionEngine;
using AcousticVocab.StateIntegration;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace AcousticVocab.SessionIntegration.Tests
{
    public sealed class PreflightFailureTests
    {
        sealed class Clock:ISessionClock {public double Value=100;public double NowMs=>Value;}
        const BindingFlags Fields=BindingFlags.Instance|BindingFlags.NonPublic;
        string root,path;GameObject go;JoinedEngineeringBootstrap host;JoinedAudit audit;ModuleConstructionScope scope;
        PrivateModeResetClient client;object preflight;Type preflightType;Clock clock;
        [SetUp]public void Setup()
        {
            root=Path.Combine(Path.GetTempPath(),".local","simulation-test-preflight-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(root);path=Path.Combine(root,"audit.local.jsonl");
            go=new GameObject("Actual preflight failure regression");go.SetActive(false);host=go.AddComponent<JoinedEngineeringBootstrap>();clock=new Clock();
            audit=new JoinedAudit(path,()=>clock.Value);typeof(JoinedEngineeringBootstrap).GetField("audit",Fields).SetValue(host,audit);
            scope=new ModuleConstructionScope();preflightType=typeof(JoinedEngineeringBootstrap).GetNestedType("Preflight",BindingFlags.NonPublic);
            preflight=Activator.CreateInstance(preflightType,Fields,null,new object[]{host,"profile_menu",JoinedModuleKind.Menus,scope},null);
            client=(PrivateModeResetClient)Activator.CreateInstance(typeof(PrivateModeResetClient),Fields,null,
                new object[]{"ws://127.0.0.1:1/commands",new string('a',32),"test",new Action<JObject>(_=>{}),new Func<double>(()=>clock.Value),false},null);
            scope.Own(client);preflightType.GetField("control",Fields).SetValue(preflight,client);
        }
        Exception PumpFailure()=>Assert.Throws<TargetInvocationException>(()=>preflightType.GetMethod("Pump").Invoke(preflight,null)).InnerException;
        void Overflow()
        {
            var receive=typeof(PrivateModeResetClient).GetMethod("ReceiveHealthProbe",Fields);
            for(int i=0;i<9;i++)receive.Invoke(client,new object[]{Guid.NewGuid().ToString("N"),"{}",90d,95d});
        }
        JObject ReadPayload(){audit.Dispose();return JObject.Parse(File.ReadAllLines(path)[0])["payload"] as JObject;}
        [TearDown]public void Cleanup()
        {
            scope?.Dispose();audit?.Dispose();if(go!=null){typeof(JoinedEngineeringBootstrap).GetField("closed",Fields).SetValue(host,true);UnityEngine.Object.DestroyImmediate(go);}
            if(Directory.Exists(root))Directory.Delete(root,true);
        }
        [Test]public void ActualPreflightRecordsQueueFailureBeforeCandidateDisposalCanChangeDiagnostic()
        {
            Overflow();var hostClock=(ISessionClock)typeof(JoinedEngineeringBootstrap).GetField("clock",Fields).GetValue(host);double before=hostClock.NowMs;
            var error=PumpFailure();double after=hostClock.NowMs;Assert.That(error,Is.TypeOf<ControlFault>());Assert.That(((ControlFault)error).Code,Is.EqualTo("CONTROL_UNAVAILABLE"));
            scope.Dispose();var payload=ReadPayload();Assert.That((string)payload["kind"],Is.EqualTo("preflight_failure"));Assert.That((string)payload["phase"],Is.EqualTo("control_pump"));
            Assert.That((string)payload["code"],Is.EqualTo("CONTROL_UNAVAILABLE"));Assert.That((double)payload["observed_mono_ms"],Is.InRange(before,after));
            Assert.That((string)payload["control_health"]["first_failure_code"],Is.EqualTo("CONTROL_ARRIVAL_CAPACITY"));Assert.That((string)payload["control_health"]["first_failure_phase"],Is.EqualTo("receive_queue"));
            Assert.That((bool)payload["control_health"]["disposed"],Is.False,"Snapshot must precede candidate cleanup");Assert.That((int)payload["control_health"]["queued_arrivals"],Is.EqualTo(8),"Diagnostic must not pump or reparse queued bytes");
        }
        [Test]public void ClosedActualAuditCannotReplaceOriginalControlFailureOrPreventCleanup()
        {
            Overflow();audit.Dispose();var error=PumpFailure();Assert.That(error,Is.TypeOf<ControlFault>());Assert.That(((ControlFault)error).Code,Is.EqualTo("CONTROL_UNAVAILABLE"));
            scope.Dispose();Assert.That((bool)client.ReadinessDiagnostic(null)["disposed"],Is.True);Assert.That(new FileInfo(path).Length,Is.Zero);
        }
        // Attempt simulation-test-B-V1-018-assess-007 through the actual Preflight
        // pump: four health replies queued during a main-thread stall, the newest
        // 266 ms old when Update resumes. Only the unexposed policy drops them.
        [TestCase(true)][TestCase(false)]
        public void ActualIdlePreflightSurvivesStaleHealthProbesOnlyUnderTheUnexposedPolicy(bool unexposed)
        {
            if(unexposed)client.DropStaleHealthProbesWhile(()=>true);
            var receive=typeof(PrivateModeResetClient).GetMethod("ReceiveHealthProbe",Fields);
            for(int i=0;i<4;i++)receive.Invoke(client,new object[]{Guid.NewGuid().ToString("N"),"{}",93d+75*i,100d+75*i});
            clock.Value=100+75*3+266;
            if(unexposed)
            {
                Assert.DoesNotThrow(()=>preflightType.GetMethod("Pump").Invoke(preflight,null));
                var detail=client.ReadinessDiagnostic(null);Assert.That((bool)detail["failed"],Is.False);Assert.That((int)detail["stale_probes_dropped"],Is.EqualTo(4));
                Assert.That((int)detail["queued_arrivals"],Is.Zero);Assert.That(client.NeutralHoldHealthy,Is.False,"A fresh probe is still required");
                audit.Dispose();Assert.That(new FileInfo(path).Length,Is.Zero,"No preflight failure is recorded");
            }
            else
            {
                var error=PumpFailure();Assert.That(error,Is.TypeOf<ControlFault>());Assert.That(((ControlFault)error).Code,Is.EqualTo("CONTROL_QUEUED"));
                var payload=ReadPayload();Assert.That((string)payload["phase"],Is.EqualTo("control_pump"));Assert.That((string)payload["control_health"]["first_failure_code"],Is.EqualTo("CONTROL_QUEUED"));
            }
        }
        // Attempts simulation-test-A-D0-023-isaac2-019/-020 (#242): after
        // visit_complete the committed assessment lease saw a forms-transition
        // stall with 4/3 health replies queued and latched CONTROL_QUEUED. Once
        // the actual engine is Complete no cue can be scheduled; mid-visit the
        // committed lease keeps the permanent latch.
        sealed class EngineJournal:ISessionJournal{readonly System.Collections.Generic.List<SessionRecord> rows=new System.Collections.Generic.List<SessionRecord>();public System.Collections.Generic.IReadOnlyList<SessionRecord> Records=>rows;public void Append(SessionRecord r)=>rows.Add(r);}
        sealed class EngineContent:ISlotContentFactory,ISlotContent
        {
            public ISlotContent Create(SlotItem item)=>this;public SlotReadiness Readiness=>new SlotReadiness(true,true,true,true,true,true,true,true);public bool ResetComplete=>true;
            public void Prepare(SlotContext c){}public void RequestCue(SlotContext c,AcousticVocab.StudyAudio.INovelSlotAuthorization p){}public void OpenResponse(SlotContext c){}public void CloseResponse(SlotContext c){}public void RequestReset(SlotContext c){}public void Interrupt(string code){}
        }
        FixedSlotEngine InstallCommittedLeaseEngine(bool complete)
        {
            T New<T>(params object[] a)=>(T)Activator.CreateInstance(typeof(T),Fields,null,a,null);
            var item=New<SlotItem>("DEMO-forms","atomic","K-a1","action","atomic","protected",false,10,0,1);
            var schedule=New<VisitSchedule>(new string('a',64),new string('b',64),"DEMO-person","D0",true,new[]{New<ScheduleBlock>("atomic",new[]{item})},"A","S",null);
            var engineClock=new Clock{Value=0};var engine=new FixedSlotEngine(schedule,engineClock,new EngineJournal(),new EngineContent());engine.ConfirmResume();engine.Tick();
            if(complete)for(double t=250;t<=30000&&engine.Status!=SessionState.Complete;t+=250){engineClock.Value=t;engine.Tick();}
            else{engineClock.Value=1000;engine.Tick();}
            Assert.That(engine.Status,Is.EqualTo(complete?SessionState.Complete:SessionState.Running));
            var owner=System.Runtime.Serialization.FormatterServices.GetUninitializedObject(typeof(SessionIntegrationOwner));
            typeof(SessionIntegrationOwner).GetField("<Engine>k__BackingField",Fields).SetValue(owner,engine);
            typeof(JoinedEngineeringBootstrap).GetField("owner",Fields).SetValue(host,owner);
            preflightType.GetField("committed",Fields).SetValue(preflight,true);
            var unexposed=preflightType.GetMethod("Unexposed",Fields);client.DropStaleHealthProbesWhile(()=>(bool)unexposed.Invoke(preflight,null));
            return engine;
        }
        [TestCase(true)][TestCase(false)]
        public void ActualCommittedLeaseDropsStaleHealthProbesOnlyAfterVisitComplete(bool complete)
        {
            InstallCommittedLeaseEngine(complete);
            var receive=typeof(PrivateModeResetClient).GetMethod("ReceiveHealthProbe",Fields);
            for(int i=0;i<4;i++)receive.Invoke(client,new object[]{Guid.NewGuid().ToString("N"),"{}",93d+75*i,100d+75*i});
            clock.Value=100+75*3+333; // the 019 forms frame: 323 ms stall
            // After commit the lease's client is pumped by the assessment host
            // (019: first_failure_phase "pump"), under the policy the lease installed.
            if(complete)
            {
                Assert.DoesNotThrow(()=>client.Pump());
                var detail=client.ReadinessDiagnostic(null);Assert.That((bool)detail["failed"],Is.False);Assert.That((int)detail["stale_probes_dropped"],Is.EqualTo(4));
                Assert.That((int)detail["queued_arrivals"],Is.Zero);Assert.That(client.NeutralHoldHealthy,Is.False,"A fresh probe is still required; stale data never grants");
            }
            else
            {
                Assert.That(Assert.Throws<ControlFault>(()=>client.Pump()).Code,Is.EqualTo("CONTROL_QUEUED"),"Mid-visit the committed lease keeps the latch");
                var detail=client.ReadinessDiagnostic(null);Assert.That((int)detail["stale_probes_dropped"],Is.Zero);Assert.That((bool)detail["failed"],Is.True);
            }
        }
        [Test]public void ActualInitialTimeoutKeepsBoundedOriginalCodeAndDoesNotQueryControlReadiness()
        {
            preflightType.GetField("started",Fields).SetValue(preflight,-16000d);var error=PumpFailure();Assert.That(error,Is.TypeOf<SessionFault>());Assert.That(((SessionFault)error).Code,Is.EqualTo("JOIN_PREFLIGHT_TIMEOUT"));
            var payload=ReadPayload();Assert.That((string)payload["phase"],Is.EqualTo("timeout"));Assert.That((string)payload["code"],Is.EqualTo("JOIN_PREFLIGHT_TIMEOUT"));
            Assert.That(payload["control_health"]["first_failure_code"].Type,Is.EqualTo(JTokenType.Null));Assert.That((int)payload["control_health"]["queued_arrivals"],Is.Zero);
        }
    }
}
