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
        [Test]public void ActualInitialTimeoutKeepsBoundedOriginalCodeAndDoesNotQueryControlReadiness()
        {
            preflightType.GetField("started",Fields).SetValue(preflight,-16000d);var error=PumpFailure();Assert.That(error,Is.TypeOf<SessionFault>());Assert.That(((SessionFault)error).Code,Is.EqualTo("JOIN_PREFLIGHT_TIMEOUT"));
            var payload=ReadPayload();Assert.That((string)payload["phase"],Is.EqualTo("timeout"));Assert.That((string)payload["code"],Is.EqualTo("JOIN_PREFLIGHT_TIMEOUT"));
            Assert.That(payload["control_health"]["first_failure_code"].Type,Is.EqualTo(JTokenType.Null));Assert.That((int)payload["control_health"]["queued_arrivals"],Is.Zero);
        }
    }
}
