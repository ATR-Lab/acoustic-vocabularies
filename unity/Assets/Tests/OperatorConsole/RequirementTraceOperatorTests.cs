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
    // Requirement trace for the operator side of test requests. The mailbox is used
    // without its file publication, so the check runs on every editor platform.
    public sealed class RequirementTraceOperatorTests
    {
        const string Manifest = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        const string Schedule = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
        const string Package = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc";
        const string Nonce = "11111111111111111111111111111111";
        static T Construct<T>(params object[] values) => (T)Activator.CreateInstance(typeof(T), BindingFlags.Instance | BindingFlags.NonPublic, null, values, null);
        static byte[] Packet(long sequence, string command, string extraField = null)
        {
            var value = new JObject { ["version"] = 1, ["session_nonce"] = Nonce, ["request_id"] = Guid.NewGuid().ToString("N"), ["sequence"] = sequence,
                ["command"] = command, ["run_sheet_manifest_sha256"] = Manifest, ["schedule_sha256"] = Schedule };
            if (extraField != null) value[extraField] = "K-a1-r1";
            return System.Text.Encoding.UTF8.GetBytes(value.ToString(Formatting.None));
        }
        sealed class Clock : ISessionClock { internal double Time; public double NowMs => Time; }
        sealed class SessionMemory : ISessionJournal
        {
            internal readonly List<SessionRecord> List = new List<SessionRecord>();
            public IReadOnlyList<SessionRecord> Records => List;
            public void Append(SessionRecord record) => List.Add(record);
        }
        sealed class Audit : IOperatorCommandJournal { public void Append(OperatorAuditRecord record) { } }
        sealed class Content : ISlotContent
        {
            internal SlotItem Item; internal int Cues;
            public SlotReadiness Readiness => new SlotReadiness(true, true, true, true, true, true, true, true);
            public bool ResetComplete { get; private set; }
            public void Prepare(SlotContext context) { Item = context.Item; }
            public void RequestCue(SlotContext context, INovelSlotAuthorization authorization) { Cues++; }
            public void OpenResponse(SlotContext context) { }
            public void CloseResponse(SlotContext context) { }
            public void RequestReset(SlotContext context) { ResetComplete = true; }
            public void Interrupt(string code) { }
        }
        sealed class Factory : ISlotContentFactory
        {
            internal readonly List<Content> Created = new List<Content>();
            public ISlotContent Create(SlotItem item) { var value = new Content(); Created.Add(value); return value; }
        }

        [Test, Category("REQ-TEST-01")]
        public void OperatorCanOnlyLoadStartPauseResumeOrStopAndNeverSeesTheRequestedCommand()
        {
            foreach (string command in new[] { "select", "play", "replay", "skip", "next", "choose" })
                Assert.That(Assert.Throws<OperatorFault>(() => OperatorRequest.Parse(Packet(1, command))).Code, Is.EqualTo("command_invalid"));
            foreach (string field in new[] { "trial_id", "message_id", "target", "action" })
                Assert.That(Assert.Throws<OperatorFault>(() => OperatorRequest.Parse(Packet(1, "start", field))).Code, Is.EqualTo("command_invalid"));
            var contents = new[] { "K-a2-r3", "Q-a4-r1", "K-a1-r2" };
            var items = contents.Select((c, i) => Construct<SlotItem>("private-trial-" + i, "trained", c, null, null, "protected", false, 14, 1, 1)).ToArray();
            var visit = Construct<VisitSchedule>(Schedule, Package, "private-person", "private-visit", true, new[] { Construct<ScheduleBlock>("private-block", items) }, null, null, null);
            var clock = new Clock(); var factory = new Factory(); var engine = new FixedSlotEngine(visit, clock, new SessionMemory(), factory);
            string directory = Path.Combine(Environment.GetEnvironmentVariable("OPERATOR_TEST_ROOT") ?? Path.Combine(Environment.CurrentDirectory, ".local", "operator-tests"), Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            var snapshots = new List<string>();
            using (var mailbox = new OperatorMailbox(directory, Nonce, Manifest, engine, new Audit(), () => new OperatorAdmission(true, true, true),
                () => new OperatorHealth(true, true, true, true, 5, 14, 14), () => clock.Time))
            {
                Assert.That(mailbox.Handle(OperatorRequest.Parse(Packet(1, "load"))).Accepted, Is.True);
                Assert.That(mailbox.Handle(OperatorRequest.Parse(Packet(2, "start"))).Accepted, Is.True);
                for (int guard = 0; guard < 20000 && engine.Status == SessionState.Running; guard++)
                { clock.Time += 50; engine.Tick(); snapshots.Add(mailbox.Snapshot().ToString(Formatting.None)); }
            }
            Assert.That(engine.Status, Is.EqualTo(SessionState.Complete));
            Assert.That(factory.Created.Select(x => x.Item.TrialId), Is.EqualTo(items.Select(x => x.TrialId)));
            Assert.That(factory.Created.All(x => x.Cues == 1), Is.True);
            foreach (string snapshot in snapshots) foreach (var item in items)
            { Assert.That(snapshot, Does.Not.Contain(item.ContentId)); Assert.That(snapshot, Does.Not.Contain(item.TrialId)); }
        }
    }
}
