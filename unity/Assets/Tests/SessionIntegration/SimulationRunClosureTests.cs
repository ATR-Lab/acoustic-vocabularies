using System;
using System.IO;
using System.Reflection;
using AcousticVocab.DataLogging;
using AcousticVocab.SessionEngine;
using NUnit.Framework;
using Newtonsoft.Json.Linq;
using UnityEngine;
namespace AcousticVocab.SessionIntegration.Tests
{
    public sealed class SimulationRunClosureTests
    {
        string root,raw,export,result;GameObject go;JoinedEngineeringBootstrap host;DataJournal journal;DataIdentity identity;ModuleConstructionScope scope;
        static readonly BindingFlags Flags=BindingFlags.Instance|BindingFlags.NonPublic;
        [SetUp]public void Setup()
        {
            root=Path.Combine(Path.GetTempPath(),".local","simulation-test-close-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(root);
            raw=Path.Combine(root,"raw");export=Path.Combine(root,"export");result=Path.Combine(root,"native-result.local.json");
            identity=new DataIdentity(Guid.NewGuid().ToString("N"),"DEMO-close","D0","station-01","simulation-test-v1",new string('a',64));
            journal=new DataJournal(raw,identity,Guid.NewGuid().ToString("N"),()=>1);
            journal.Append(DataObservations.Device(null,"focus",1,true));
            go=new GameObject("Simulation closure regression");go.SetActive(false);host=go.AddComponent<JoinedEngineeringBootstrap>();
            scope=(ModuleConstructionScope)typeof(JoinedEngineeringBootstrap).GetField("visit",Flags).GetValue(host);scope.Own(journal);
            Set("simulationClosure",new SimulationRunClosure(result,new string('1',32),new string('b',64),new string('c',64),new string('d',40),123,
                ()=>ExportBundle.Create(raw,export,identity,ExportHeaders.Provisional()),()=>2));
            Set("<StatusCode>k__BackingField",SimulationRunClosure.CompleteStatus);
        }
        void Set(string name,object value)=>typeof(JoinedEngineeringBootstrap).GetField(name,Flags).SetValue(host,value);
        void Close()=>typeof(JoinedEngineeringBootstrap).GetMethod("Close",Flags).Invoke(host,null);
        JObject Receipt()=>JObject.Parse(File.ReadAllText(result));
        [TearDown]public void Cleanup(){if(go!=null)UnityEngine.Object.DestroyImmediate(go);journal?.Dispose();if(Directory.Exists(root))Directory.Delete(root,true);}
        [Test]public void ActualHostClosePublishesOnlyAfterClosedJournalAndVerifiedExportAndIsIdempotent()
        {
            Close();var receipt=Receipt();Assert.That(journal.Closed,Is.True);Assert.That((bool)receipt["complete"],Is.True);
            Assert.That((bool)receipt["cleanup_succeeded"]&&(bool)receipt["export_succeeded"],Is.True);
            ExportBundle.Load(export,(string)receipt["export_manifest_sha256"]).VerifyAll();
            Assert.That((string)receipt["source_commit"],Is.EqualTo(new string('d',40)));Assert.That((int)receipt["process_id"],Is.EqualTo(123));
            byte[] bytes=File.ReadAllBytes(result);Close();Assert.That(File.ReadAllBytes(result),Is.EqualTo(bytes));Assert.That(File.Exists(result+".pending"),Is.False);
        }
        [Test]public void ActualHostThrowingDisposalStillClosesEveryStageAndExportsButCannotComplete()
        {
            int attempted=0;scope.RegisterCleanup(()=>attempted++);scope.RegisterCleanup(()=>throw new IOException("synthetic cleanup failure"));scope.RegisterCleanup(()=>attempted++);
            Close();var receipt=Receipt();Assert.That(attempted,Is.EqualTo(2));Assert.That(journal.Closed,Is.True);
            Assert.That(host.StatusCode,Is.EqualTo("JOIN_DISPOSE_FAILED"));Assert.That((bool)receipt["complete"],Is.False);
            Assert.That((bool)receipt["cleanup_succeeded"],Is.False);Assert.That((bool)receipt["export_succeeded"],Is.True);
            ExportBundle.Load(export,(string)receipt["export_manifest_sha256"]).VerifyAll();
        }
        [Test]public void ActualExportCreateFailureIsDurableAndNeverReportsComplete()
        {
            Directory.CreateDirectory(export);Close();var receipt=Receipt();Assert.That(journal.Closed,Is.True);
            Assert.That(host.StatusCode,Is.EqualTo("JOIN_EXPORT_FAILED"));Assert.That((bool)receipt["cleanup_succeeded"],Is.True);
            Assert.That((bool)receipt["export_succeeded"]||(bool)receipt["complete"],Is.False);Assert.That(receipt["export_manifest_sha256"].Type,Is.EqualTo(JTokenType.Null));
        }
        [Test]public void InitialFaultSurvivesLaterCleanupAndExportFailures()
        {
            Set("<StatusCode>k__BackingField","JOIN_FOCUS_LOST");scope.RegisterCleanup(()=>throw new IOException("synthetic cleanup failure"));Directory.CreateDirectory(export);
            Close();var receipt=Receipt();Assert.That(host.StatusCode,Is.EqualTo("JOIN_FOCUS_LOST"));Assert.That((string)receipt["status"],Is.EqualTo("JOIN_FOCUS_LOST"));
            Assert.That((bool)receipt["cleanup_succeeded"]||(bool)receipt["export_succeeded"]||(bool)receipt["complete"],Is.False);
        }
        [Test]public void ActualHostGateSinkPersistsOriginalReadinessSnapshotWithoutManufacturingControlEvidence()
        {
            string path=Path.Combine(root,"gate-audit.local.jsonl");var audit=scope.Own(new JoinedAudit(path,()=>3));Set("audit",audit);
            T New<T>(params object[] args)=>(T)Activator.CreateInstance(typeof(T),Flags,null,args,null);
            var item=New<SlotItem>("DEMO-gate","atomic_lesson",null,null,null,"engineering",false,20,3,1);
            var context=new SlotContext(item,1000,null);var readiness=new SlotReadiness(true,true,false,true,true,true,true,true);
            var refusal=New<SlotGateRefusal>("SESSION_CUE_GATE_REFUSED","lessons",context,readiness,264d,150d);
            typeof(JoinedEngineeringBootstrap).GetMethod("RecordGateRefusal",Flags).Invoke(host,new object[]{refusal});
            var row=JObject.Parse(File.ReadAllLines(path)[0]);var payload=row["payload"];
            Assert.That((string)payload["kind"],Is.EqualTo("slot_gate_refused"));Assert.That((string)payload["attempt_id"],Is.EqualTo("DEMO-gate"));
            Assert.That((double)payload["remaining_lead_ms"],Is.EqualTo(736));Assert.That((bool)payload["readiness"]["reset_acknowledged"],Is.False);
            Assert.That((bool)payload["readiness"]["focus_ok"],Is.True);Assert.That(payload["control_health"].Type,Is.EqualTo(JTokenType.Null));
            Assert.That((string)row["kind"],Is.EqualTo("module"));Assert.That(row["sha256"],Is.Not.Null);
        }
        [TestCase(SimulationRunClosure.CompleteStatus,"JOIN_RESULT_WRITE_FAILED")]
        [TestCase("JOIN_FOCUS_LOST","JOIN_FOCUS_LOST")]
        public void ReceiptPublicationFailurePreservesPriorEvidenceAndNeverRetries(string initial,string expected)
        {
            Set("<StatusCode>k__BackingField",initial);File.WriteAllText(result,"retained conflicting evidence");Close();
            Assert.That(host.StatusCode,Is.EqualTo(expected));Assert.That(File.ReadAllText(result),Is.EqualTo("retained conflicting evidence"));
            Assert.That(File.Exists(result+".pending"),Is.True);byte[] pending=File.ReadAllBytes(result+".pending");
            Close();Assert.That(File.ReadAllBytes(result+".pending"),Is.EqualTo(pending));Assert.That(host.StatusCode,Is.EqualTo(expected));
        }
    }
}
