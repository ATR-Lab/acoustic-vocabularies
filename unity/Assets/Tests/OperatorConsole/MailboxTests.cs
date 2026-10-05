using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.OperatorConsole.Tests
{
    public sealed class MailboxTests
    {
        const string Manifest = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        const string Schedule = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
        const string Package = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc";
        const string Nonce = "11111111111111111111111111111111";
        static T Construct<T>(params object[] values) => (T)Activator.CreateInstance(typeof(T), BindingFlags.Instance | BindingFlags.NonPublic, null, values, null);
        static string Folder() { string path = Path.Combine(Environment.GetEnvironmentVariable("OPERATOR_TEST_ROOT") ?? Path.Combine(Environment.CurrentDirectory,".local","operator-tests"), Guid.NewGuid().ToString("N")); Directory.CreateDirectory(path); return path; }
        static byte[] Bytes(JObject value) => System.Text.Encoding.UTF8.GetBytes(value.ToString(Formatting.None));
        static JObject Packet(long sequence, string command, string id = null, string nonce = Nonce, string hash = Manifest) => new JObject {
            ["version"] = 1, ["session_nonce"] = nonce, ["request_id"] = id ?? Guid.NewGuid().ToString("N"), ["sequence"] = sequence,
            ["command"] = command, ["run_sheet_manifest_sha256"] = hash, ["schedule_sha256"] = Schedule };
        sealed class Clock : ISessionClock { internal double Time; public double NowMs => Time; }
        sealed class SessionMemory : ISessionJournal
        {
            internal readonly List<SessionRecord> List = new List<SessionRecord>();
            public IReadOnlyList<SessionRecord> Records => List;
            public void Append(SessionRecord record) => List.Add(record);
        }
        sealed class Audit : IOperatorCommandJournal
        {
            internal readonly List<OperatorAuditRecord> Records = new List<OperatorAuditRecord>();
            internal bool Fail;
            public void Append(OperatorAuditRecord record) { if (Fail) throw new IOException("synthetic"); Records.Add(record); }
        }
        sealed class Content : ISlotContent
        {
            internal int Cues, Interrupts;
            public SlotReadiness Readiness => new SlotReadiness(true,true,true,true,true,true,true,true);
            public bool ResetComplete { get; private set; }
            public void Prepare(SlotContext context) { }
            public void RequestCue(SlotContext context, INovelSlotAuthorization authorization) { Cues++; }
            public void OpenResponse(SlotContext context) { }
            public void CloseResponse(SlotContext context) { }
            public void RequestReset(SlotContext context) { ResetComplete=true; }
            public void Interrupt(string code) { Interrupts++; }
        }
        sealed class Factory : ISlotContentFactory
        {
            internal readonly List<Content> Created = new List<Content>();
            internal Action BeforeCreate;
            public ISlotContent Create(SlotItem item) { BeforeCreate?.Invoke(); var value=new Content();Created.Add(value);return value; }
        }
        sealed class Fixture : IDisposable
        {
            internal readonly string Directory = Folder();
            internal readonly Clock Clock = new Clock();
            internal readonly SessionMemory Session = new SessionMemory();
            internal readonly Audit Journal = new Audit();
            internal readonly Factory Factory = new Factory();
            internal readonly VisitSchedule Visit;
            internal readonly FixedSlotEngine Engine;
            internal readonly OperatorMailbox Mailbox;
            internal bool Admitted = true, Healthy = true;
            internal Fixture(int count = 2)
            {
                var items=Enumerable.Range(0,count).Select(i=>Construct<SlotItem>("private-trial-"+i,"trained","private-content",null,null,"protected",false,14,1,1)).ToArray();
                Visit=Construct<VisitSchedule>(Schedule,Package,"private-person","private-visit",true,new[]{Construct<ScheduleBlock>("private-block",items)});
                Engine=new FixedSlotEngine(Visit,Clock,Session,Factory);
                Mailbox=new OperatorMailbox(Directory,Nonce,Manifest,Engine,Journal,()=>new OperatorAdmission(Admitted,Admitted,Admitted),()=>new OperatorHealth(Healthy,Healthy,Healthy,Healthy,Healthy?5:300,14,14),()=>Clock.Time);
            }
            internal OperatorReceipt Send(long sequence,string command,string id=null,string nonce=Nonce,string hash=Manifest)=>Mailbox.Handle(OperatorRequest.Parse(Bytes(Packet(sequence,command,id,nonce,hash))));
            public void Dispose()=>Mailbox.Dispose();
        }
        [Test] public void StartsOnlyAfterLoadAndDurableRequestPrecedesRealEngineCall()
        {
            using var f=new Fixture();Assert.That(f.Send(1,"start").Accepted,Is.False);Assert.That(f.Factory.Created,Is.Empty);Assert.That(f.Send(2,"load").Accepted,Is.True);
            f.Factory.BeforeCreate=()=>Assert.That(f.Journal.Records.Last().Kind,Is.EqualTo("request"));Assert.That(f.Send(3,"start").Accepted,Is.True);Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Running));Assert.That(f.Factory.Created.Count,Is.EqualTo(1));Assert.That(f.Journal.Records.Last().Receipt.Accepted,Is.True);
        }
        [Test] public void DuplicateExactRequestNeverRepeatsEngineAction()
        {
            using var f=new Fixture();f.Send(1,"load");string id=Guid.NewGuid().ToString("N");var first=f.Send(2,"start",id);int audit=f.Journal.Records.Count;var again=f.Send(2,"start",id);
            Assert.That(again.RequestId,Is.EqualTo(first.RequestId));Assert.That(again.Accepted,Is.True);Assert.That(f.Factory.Created.Count,Is.EqualTo(1));Assert.That(f.Journal.Records.Count,Is.EqualTo(audit));
        }
        [Test] public void ReusedIdOrWrongHashCannotActAndEachValidSequenceIsConsumed()
        {
            using var f=new Fixture();string id=Guid.NewGuid().ToString("N");f.Send(1,"load",id);Assert.That(f.Send(2,"start",id).Code,Is.EqualTo("request_reused"));Assert.That(f.Send(3,"start",hash:new string('f',64)).Code,Is.EqualTo("hash_mismatch"));Assert.That(f.Mailbox.ConsumedSequence,Is.EqualTo(3));Assert.That(f.Factory.Created,Is.Empty);
        }
        [Test] public void RestartNonceAndOutOfOrderStartRemainReadOnly()
        {
            using var f=new Fixture();Assert.That(f.Send(1,"load",nonce:new string('2',32)).Accepted,Is.False);Assert.That(f.Mailbox.ConsumedSequence,Is.Zero);Assert.That(f.Send(2,"start").Code,Is.EqualTo("sequence_rejected"));Assert.That(f.Factory.Created,Is.Empty);
        }
        [Test] public void StopAloneSupersedesUncertainLowerSequenceAndRejectsOlderCommands()
        {
            using var f=new Fixture();f.Send(1,"load");f.Send(2,"start");Assert.That(f.Send(5,"resume").Code,Is.EqualTo("sequence_rejected"));var stop=f.Send(5,"stop");Assert.That(stop.Accepted,Is.True);Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Stopped));Assert.That(f.Mailbox.ConsumedSequence,Is.EqualTo(5));Assert.That(f.Send(3,"start").Code,Is.EqualTo("sequence_rejected"));Assert.That((long)f.Mailbox.Snapshot()["receipt"]["sequence"],Is.EqualTo(5));Assert.That(f.Journal.Records.Any(x=>x.Request.Command=="stop"&&x.PreviousConsumedSequence==2&&x.Kind=="request"),Is.True);
        }
        [Test] public void LoadDoesNotGrantAdmissionWhenTrustedOwnerRejectsIt()
        {
            using var f=new Fixture();f.Admitted=false;Assert.That(f.Send(1,"load").Code,Is.EqualTo("admission_failed"));Assert.That(f.Mailbox.Loaded,Is.False);Assert.That(f.Send(2,"stop").Accepted,Is.True);
        }
        [Test] public void StopAndPauseRemainAvailableAfterAdmissionFailure()
        {
            using var f=new Fixture();f.Send(1,"load");f.Send(2,"start");f.Admitted=false;f.Healthy=false;Assert.That(f.Send(3,"pause").Accepted,Is.True);Assert.That(f.Send(4,"resume").Accepted,Is.False);Assert.That(f.Send(5,"stop").Accepted,Is.True);Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Stopped));
        }
        [Test] public void MidTrialPauseHoldsTheNextUnplayedOpportunity()
        {
            using var f=new Fixture();f.Send(1,"load");f.Send(2,"start");f.Mailbox.Tick();f.Clock.Time=1000;f.Mailbox.Tick();Assert.That(f.Send(3,"pause").Accepted,Is.True);Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Running));
            f.Clock.Time=12750;f.Mailbox.Tick();f.Clock.Time=14750;f.Mailbox.Tick();Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Paused));Assert.That(f.Factory.Created.Count,Is.EqualTo(1));Assert.That(f.Engine.CompletedCounts.Single(),Is.EqualTo(1));
        }
        [Test] public void JournalFailureNeverStartsCueAndLatchesAdapter()
        {
            using var f=new Fixture();f.Send(1,"load");f.Journal.Fail=true;Assert.Throws<OperatorFault>(()=>f.Send(2,"start"));Assert.That(f.Mailbox.Failed,Is.True);Assert.That(f.Factory.Created,Is.Empty);Assert.That(f.Mailbox.Loaded,Is.False);Assert.That((string)f.Mailbox.Snapshot()["engine_state"],Is.EqualTo("faulted"));
        }
        [Test] public void DisposalOfActiveAdapterStopsItsEngine()
        {
            using var f=new Fixture();f.Send(1,"load");f.Send(2,"start");f.Mailbox.Dispose();Assert.That(f.Engine.Status,Is.Not.EqualTo(SessionState.Running));Assert.Throws<OperatorFault>(()=>f.Mailbox.Tick());
        }
        [Test] public void ClosedSnapshotCannotLeakPrivateScheduleIdentifiers()
        {
            using var f=new Fixture();var a=f.Mailbox.Snapshot();var b=f.Mailbox.Snapshot();Assert.That((long)b["sequence"],Is.GreaterThan((long)a["sequence"]));Assert.That(a.Properties().Count(),Is.EqualTo(12));Assert.That(a.ToString(),Does.Not.Contain("private-"));Assert.That(a["completed_counts"].ToObject<int[]>(),Is.EqualTo(new[]{0}));Assert.That(a["receipt"].Type,Is.EqualTo(JTokenType.Null));
        }
        [Test] public void FileMailboxReceiptsBindRequestAndRetainClosedState()
        {
            using var f=new Fixture();f.Mailbox.Tick();var packet=Packet(1,"load");File.WriteAllBytes(Path.Combine(f.Directory,"command.json"),Bytes(packet));f.Mailbox.Tick();var state=JObject.Parse(File.ReadAllText(Path.Combine(f.Directory,"state.json")));Assert.That((string)state["receipt"]["request_id"],Is.EqualTo((string)packet["request_id"]));Assert.That((string)state["receipt"]["status"],Is.EqualTo("accepted"));Assert.That(f.Mailbox.Loaded,Is.True);
        }
        [Test] public void SecondEngineWriterCannotTakeSameMailbox()
        {
            using var f=new Fixture();Assert.Throws<IOException>(()=>new OperatorMailbox(f.Directory,new string('2',32),Manifest,f.Engine,f.Journal,()=>new OperatorAdmission(true,true,true),()=>new OperatorHealth(true,true,true,true,0,0,0),()=>0));
        }
        [Test] public void PersistentPublicationContentionFailsClosedAndPreservesPriorState()
        {
            using var f=new Fixture(); f.Mailbox.Tick();
            string path=Path.Combine(f.Directory,"state.json"); byte[] prior=File.ReadAllBytes(path);
            using(var reader=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.Read))
            {
                f.Clock.Time=250;
                var failure=Assert.Throws<OperatorFault>(()=>f.Mailbox.Tick());
                Assert.That(failure.Code,Is.EqualTo("state_publish_failed"));
                Assert.That(f.Mailbox.Failed,Is.True);
            }
            Assert.That(File.ReadAllBytes(path),Is.EqualTo(prior));
            Assert.That(System.IO.Directory.GetFiles(f.Directory,"state.json.*.tmp").Length,Is.EqualTo(1));
        }
        [Test] public void UnknownFieldsDuplicateKeysAndNonIntegerSequenceFailBeforeEngine()
        {
            var packet=Packet(1,"load");packet["answer"]="forbidden";Assert.Throws<OperatorFault>(()=>OperatorRequest.Parse(Bytes(packet)));packet.Remove("answer");packet["sequence"]=1.5;Assert.Throws<OperatorFault>(()=>OperatorRequest.Parse(Bytes(packet)));var valid=System.Text.Encoding.UTF8.GetString(Bytes(Packet(1,"load")));Assert.Throws<OperatorFault>(()=>OperatorRequest.Parse(System.Text.Encoding.UTF8.GetBytes(valid.Replace("\"sequence\":1","\"sequence\":99999999999999999999999999999999999999"))));Assert.Throws<OperatorFault>(()=>OperatorRequest.Parse(System.Text.Encoding.UTF8.GetBytes(valid.Replace("\"version\":1","\"version\":1,\"version\":1"))));Assert.Throws<OperatorFault>(()=>OperatorRequest.Parse(System.Text.Encoding.UTF8.GetBytes(valid.Replace('"','\''))));
        }
        [Test] public void RecoveredConsumedOriginalCountsOnceWithoutExposingRetries()
        {
            using var f=new Fixture(1);f.Send(1,"load");f.Send(2,"start");f.Mailbox.Tick();var resumed=new FixedSlotEngine(f.Visit,new Clock(),f.Session,new Factory());Assert.That(resumed.CompletedCounts.Single(),Is.EqualTo(1));Assert.That(resumed.Status,Is.EqualTo(SessionState.Complete));Assert.That(resumed.CompletedCounts,Is.Not.SameAs(resumed.CompletedCounts));
        }
        [Test] public void DurableFileJournalUsesFreshNonceAndNeverOverwritesOldFile()
        {
            string folder=Folder();using var f=new Fixture();using(var journal=new FileOperatorCommandJournal(folder,Nonce))
            {var request=OperatorRequest.Parse(Bytes(Packet(1,"load")));f.Send(1,"load");journal.Append(f.Journal.Records[0]);journal.Append(f.Journal.Records[1]);}
            string file=System.IO.Directory.GetFiles(folder).Single();byte[] prior=File.ReadAllBytes(file);Assert.That(File.ReadLines(file).Count(),Is.EqualTo(2));Assert.Throws<IOException>(()=>new FileOperatorCommandJournal(folder,Nonce));using(new FileOperatorCommandJournal(folder,new string('2',32))){}Assert.That(File.ReadAllBytes(file),Is.EqualTo(prior));
        }
    }
}
