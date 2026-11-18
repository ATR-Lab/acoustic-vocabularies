using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using AcousticVocab.SessionEngine;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.OperatorConsole.Tests
{
    // Decision A6: a participant may stop because of discomfort. The stop is its
    // own durable exit (never a technical fault or a withdrawal) and the visit's
    // remaining trials are not run. These tests drive the engine directly; the
    // file mailbox publication is Windows-only and is not needed here.
    public sealed class DiscomfortStopTests
    {
        const string Manifest = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        const string Schedule = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
        const string Package = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc";
        const string Nonce = "22222222222222222222222222222222";
        static T Construct<T>(params object[] values) => (T)Activator.CreateInstance(typeof(T), BindingFlags.Instance | BindingFlags.NonPublic, null, values, null);
        static string Folder() { string path = System.IO.Path.Combine(Environment.GetEnvironmentVariable("OPERATOR_TEST_ROOT") ?? System.IO.Path.Combine(Environment.CurrentDirectory, ".local", "operator-tests"), Guid.NewGuid().ToString("N")); System.IO.Directory.CreateDirectory(path); return path; }
        static JObject Packet(long sequence, string command, string reason = null)
        {
            var value = new JObject { ["version"] = 1, ["session_nonce"] = Nonce, ["request_id"] = Guid.NewGuid().ToString("N"), ["sequence"] = sequence,
                ["command"] = command, ["run_sheet_manifest_sha256"] = Manifest, ["schedule_sha256"] = Schedule };
            if (reason != null) value["stop_reason"] = reason;
            return value;
        }
        static OperatorRequest Parse(JObject value) => OperatorRequest.Parse(System.Text.Encoding.UTF8.GetBytes(value.ToString(Formatting.None)));
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
            public void Append(OperatorAuditRecord record) => Records.Add(record);
        }
        sealed class Content : ISlotContent
        {
            internal int Cues, Interrupts;
            public SlotReadiness Readiness => new SlotReadiness(true, true, true, true, true, true, true, true);
            public bool ResetComplete { get; private set; }
            public void Prepare(SlotContext context) { }
            public void RequestCue(SlotContext context, INovelSlotAuthorization authorization) { Cues++; }
            public void OpenResponse(SlotContext context) { }
            public void CloseResponse(SlotContext context) { }
            public void RequestReset(SlotContext context) { ResetComplete = true; }
            public void Interrupt(string code) { Interrupts++; }
        }
        sealed class Factory : ISlotContentFactory
        {
            internal readonly List<Content> Created = new List<Content>();
            public ISlotContent Create(SlotItem item) { var value = new Content(); Created.Add(value); return value; }
        }
        sealed class Fixture : IDisposable
        {
            internal readonly Clock Clock = new Clock();
            internal readonly SessionMemory Session = new SessionMemory();
            internal readonly Audit Journal = new Audit();
            internal readonly Factory Factory = new Factory();
            internal readonly VisitSchedule Visit;
            internal readonly FixedSlotEngine Engine;
            internal readonly OperatorMailbox Mailbox;
            long sequence;
            internal Fixture(int count = 3)
            {
                var items = Enumerable.Range(0, count).Select(i => Construct<SlotItem>("private-trial-" + i, "trained", "private-content", null, null, "protected", false, 14, 1, 1)).ToArray();
                Visit = Construct<VisitSchedule>(Schedule, Package, "private-person", "private-visit", true, new[] { Construct<ScheduleBlock>("private-block", items) }, null, null, null);
                Engine = new FixedSlotEngine(Visit, Clock, Session, Factory);
                Mailbox = new OperatorMailbox(Folder(), Nonce, Manifest, Engine, Journal, () => new OperatorAdmission(true, true, true),
                    () => new OperatorHealth(true, true, true, true, 5, 14, 14), () => Clock.Time);
            }
            internal OperatorReceipt Send(string command, string reason = null) => Mailbox.Handle(Parse(Packet(++sequence, command, reason)));
            public void Dispose() => Mailbox.Dispose();
        }

        [Test, Category("REQ_LOG_01"), Category("REQ_INT_07")]
        public void DiscomfortStopIsADistinctDurableExitAndNoFurtherTrialRuns()
        {
            using var f = new Fixture();
            f.Send("load"); f.Send("start"); f.Engine.Tick();
            Assert.That(f.Factory.Created.Count, Is.EqualTo(1));
            var receipt = f.Send("stop", OperatorRequest.ParticipantDiscomfort);
            Assert.That(receipt.Accepted, Is.True);
            Assert.That(f.Engine.Status, Is.EqualTo(SessionState.Stopped));
            Assert.That(f.Engine.DiscomfortStopped, Is.True);
            var exit = f.Session.List.Single(r => r.Event == "participant_discomfort_stop");
            Assert.That(exit.TechnicalFaultCode, Is.Null, "A discomfort exit is not a technical fault");
            Assert.That(f.Session.List.Any(r => r.TechnicalFaultCode == "SESSION_OPERATOR_STOP"), Is.False);
            Assert.That(SessionRecordCodec.ToJson(exit)["event"].ToString(), Is.EqualTo("participant_discomfort_stop"), "The data log codec keeps the exit");
            var row = f.Journal.Records.Single(r => r.Kind == "visit_exit");
            Assert.That(row.Request.StopReason, Is.EqualTo("participant_discomfort"));
            Assert.That(row.Receipt.Accepted, Is.True);
            Assert.That(f.Journal.Records.Last(), Is.SameAs(row));
            Assert.That(f.Send("resume").Code, Is.EqualTo("not_at_boundary"));
            Assert.That(f.Send("start").Code, Is.EqualTo("not_at_boundary"));
            for (int i = 1; i <= 40; i++) { f.Clock.Time = i * 1000; f.Engine.Tick(); }
            Assert.That(f.Factory.Created.Count, Is.EqualTo(1), "The remaining trials are not run");
            Assert.That(f.Engine.CompletedCounts.Single(), Is.EqualTo(0));
        }

        [Test, Category("REQ_LOG_01")]
        public void RestartAfterADiscomfortStopNeverResumesTheVisit()
        {
            using var f = new Fixture();
            f.Send("load"); f.Send("start"); f.Engine.Tick();
            f.Send("stop", OperatorRequest.ParticipantDiscomfort);
            var restarted = new FixedSlotEngine(f.Visit, new Clock { Time = 50000 }, f.Session, new Factory());
            Assert.That(restarted.Status, Is.EqualTo(SessionState.Stopped));
            Assert.That(restarted.DiscomfortStopped, Is.True);
            Assert.That(restarted.NeedsOperatorConfirmation, Is.False);
            var plain = new Fixture();
            plain.Send("load"); plain.Send("start"); plain.Engine.Tick(); plain.Send("stop");
            Assert.That(new FixedSlotEngine(plain.Visit, new Clock { Time = 50000 }, plain.Session, new Factory()).Status, Is.EqualTo(SessionState.AwaitingOperator),
                "Only the discomfort exit is terminal across restarts; a plain stop keeps today's behaviour");
            plain.Dispose();
        }

        [Test, Category("REQ_LOG_01"), Category("REQ_INT_07")]
        public void MidTrialDiscomfortStopUsesItsOwnCodeAndKeepsTheExposure()
        {
            using var f = new Fixture();
            f.Send("load"); f.Send("start"); f.Engine.Tick(); f.Clock.Time = 1000; f.Engine.Tick();
            Assert.That(f.Factory.Created.Single().Cues, Is.EqualTo(1));
            Assert.That(f.Send("stop", OperatorRequest.ParticipantDiscomfort).Accepted, Is.True);
            Assert.That(f.Factory.Created.Single().Interrupts, Is.EqualTo(1));
            var stop = f.Session.List.Single(r => r.Event == "stop_requested");
            Assert.That(stop.TechnicalFaultCode, Is.EqualTo(FixedSlotEngine.DiscomfortStopCode));
            Assert.That(stop.TechnicalFaultCode, Is.Not.EqualTo("SESSION_OPERATOR_STOP"));
            Assert.That(f.Session.List.FindIndex(r => r.Event == "participant_discomfort_stop"), Is.LessThan(f.Session.List.FindIndex(r => r.Event == "stop_requested")),
                "The reason is durable before the item is interrupted");
            Assert.That(f.Engine.Status, Is.EqualTo(SessionState.Stopped));
        }

        [Test, Category("REQ_LOG_01")]
        public void PlainStopCanBeMarkedAsDiscomfortOnceAndCompleteVisitsCannot()
        {
            using var f = new Fixture();
            f.Send("load"); f.Send("start"); f.Engine.Tick(); f.Send("stop");
            Assert.That(f.Session.List.Any(r => r.Event == "participant_discomfort_stop"), Is.False);
            Assert.That(f.Send("stop", OperatorRequest.ParticipantDiscomfort).Accepted, Is.True);
            Assert.That(f.Send("stop", OperatorRequest.ParticipantDiscomfort).Accepted, Is.True);
            Assert.That(f.Session.List.Count(r => r.Event == "participant_discomfort_stop"), Is.EqualTo(1));
            Assert.That(f.Journal.Records.Count(r => r.Kind == "visit_exit"), Is.EqualTo(2), "Each accepted request keeps its own audit row");
            Assert.Throws<SessionFault>(() => f.Engine.RequestStop("technical"));
        }

        [Test, Category("REQ_LOG_01")]
        public void StopReasonIsAcceptedOnlyOnStopWithTheOneKnownValue()
        {
            Assert.That(Parse(Packet(1, "stop")).StopReason, Is.Null);
            Assert.That(Parse(Packet(1, "stop", "participant_discomfort")).StopReason, Is.EqualTo("participant_discomfort"));
            foreach (var bad in new[] { Packet(1, "pause", "participant_discomfort"), Packet(1, "resume", "participant_discomfort"),
                Packet(1, "stop", "technical"), Packet(1, "stop", "withdrawal"), Packet(1, "stop", "") })
                Assert.That(Assert.Throws<OperatorFault>(() => Parse(bad)).Code, Is.EqualTo("command_invalid"));
            var number = Packet(1, "stop"); number["stop_reason"] = 1;
            Assert.That(Assert.Throws<OperatorFault>(() => Parse(number)).Code, Is.EqualTo("command_invalid"));
        }
    }
}
