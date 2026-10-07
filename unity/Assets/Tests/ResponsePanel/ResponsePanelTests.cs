using System;
using System.Collections.Generic;
using System.Linq;
using NUnit.Framework;

namespace AcousticVocab.ResponsePanel.Tests
{
    public class ResponsePanelTests
    {
        double now;
        List<PanelProcessEvent> events;
        ResponseState state;
        [SetUp] public void Setup() { now = 1000; events = new List<PanelProcessEvent>(); state = new ResponseState(() => now, events.Add); }
        void Open(PanelMode mode = PanelMode.FullMessage, PanelRole role = PanelRole.Command, double? practice = null)
            => state.Open(new PanelRequest("synthetic-trial", mode, role, 1000, practice));
        public static IEnumerable<TestCaseData> LegalTuples()
        {
            foreach (string target in PublicCommands.Targets) foreach (string action in PublicCommands.Actions)
                if (PublicCommands.Legal(target, action)) yield return new TestCaseData(target, action);
        }
        [TestCaseSource(nameof(LegalTuples))] public void All32LegalTuplesCommitAndCannotBeReplaced(string target, string action)
        {
            Open(); Assert.That(state.SelectedTarget, Is.Null); Assert.That(state.SelectedAction, Is.Null); Assert.That(state.CanCommit, Is.False);
            Assert.That(state.SelectTarget(target), Is.True); Assert.That(state.SelectAction(action), Is.True); Assert.That(state.CanCommit, Is.True);
            Assert.That(state.Commit(), Is.True); var first = state.Result;
            Assert.That(first.Target, Is.EqualTo(target)); Assert.That(first.Action, Is.EqualTo(action)); Assert.That(first.Code, Is.EqualTo(ResponseCode.Commit));
            Assert.That(state.SelectTarget("H"), Is.False); Assert.That(state.SelectAction("CLOSE"), Is.False); Assert.That(state.Commit(), Is.False); Assert.That(state.DontKnow(), Is.False);
            Assert.That(state.Result, Is.SameAs(first)); Assert.That(events.Count(x => x.Kind == "post_lock_input"), Is.EqualTo(4));
        }
        [Test] public void EveryCrossFamilyChangeClearsWithoutMappingAction()
        {
            int count = 0;
            foreach (string target in PublicCommands.Targets) foreach (string action in PublicCommands.Actions) foreach (string other in PublicCommands.Targets)
            {
                if (!PublicCommands.Legal(target, action) || PublicCommands.Family(target) == PublicCommands.Family(other)) continue;
                Setup(); Open(); state.SelectTarget(target); state.SelectAction(action); state.SelectTarget(other);
                Assert.That(state.SelectedTarget, Is.EqualTo(other)); Assert.That(state.SelectedAction, Is.Null); Assert.That(state.CanCommit, Is.False);
                Assert.That(events.Last().Kind, Is.EqualTo("action_cleared_family_change")); Assert.That(state.SelectAction(action), Is.False); count++;
            }
            Assert.That(count, Is.EqualTo(128));
        }
        [Test] public void AllCrossFamilyTuplesAreRejected()
        {
            int count = 0;
            foreach (string target in PublicCommands.Targets) foreach (string action in PublicCommands.Actions)
            {
                if (PublicCommands.Legal(target, action)) continue;
                Setup(); Open(); state.SelectTarget(target); Assert.That(state.SelectAction(action), Is.False); Assert.That(state.Commit(), Is.False); count++;
            }
            Assert.That(count, Is.EqualTo(32));
        }
        [Test] public void SameFamilyRetainsTheActualChosenAction()
        {
            foreach (string target in PublicCommands.Targets) foreach (string action in PublicCommands.Actions) foreach (string other in PublicCommands.Targets)
            {
                if (!PublicCommands.Legal(target, action) || PublicCommands.Family(target) != PublicCommands.Family(other)) continue;
                Setup(); Open(); state.SelectTarget(target); state.SelectAction(action); state.SelectTarget(other);
                Assert.That(state.SelectedAction, Is.EqualTo(action)); Assert.That(state.CanCommit, Is.True);
            }
        }
        [TestCase(12999.999, true)] [TestCase(13000, false)] [TestCase(13000.001, false)]
        public void CommitDeadlineUsesOnsetAndRejectsExactBoundary(double instant, bool accepted)
        {
            Open(); state.SelectTarget("A"); state.SelectAction("ADD_ONE"); now = instant;
            Assert.That(state.Commit(), Is.EqualTo(accepted)); Assert.That(state.Result.Code, Is.EqualTo(accepted ? ResponseCode.Commit : ResponseCode.Timeout));
            Assert.That(state.Request.SlotEndMonoMs, Is.EqualTo(15000));
            if (!accepted) { Assert.That(state.Result.Target, Is.Null); Assert.That(state.Result.SelectedTarget, Is.EqualTo("A")); Assert.That(events.Last().Kind, Is.EqualTo("post_lock_input")); }
        }
        [Test] public void DontKnowAndTimeoutAreDistinctAndKeepProcessSelections()
        {
            Open(); state.SelectTarget("B"); state.DontKnow(); Assert.That(state.Result.Code, Is.EqualTo(ResponseCode.DontKnow)); Assert.That(state.Result.Target, Is.Null); Assert.That(state.Result.SelectedTarget, Is.EqualTo("B"));
            now = 20000; state.Open(new PanelRequest("another", PanelMode.FullMessage, PanelRole.Command, 20000)); state.SelectTarget("C"); now = 32000; state.Tick();
            Assert.That(state.Result.Code, Is.EqualTo(ResponseCode.Timeout)); Assert.That(state.Result.SelectedTarget, Is.EqualTo("C")); Assert.That(state.Result.Target, Is.Null);
        }
        [TestCase(PanelRole.Action)] [TestCase(PanelRole.Target)] public void AtomicRoleHasAllEightOptionsAndExplicitCommit(PanelRole role)
        {
            var choices = role == PanelRole.Action ? PublicCommands.Actions : PublicCommands.Targets;
            foreach (string value in choices)
            {
                Setup(); Open(PanelMode.AtomicProbe, role);
                Assert.That(role == PanelRole.Action ? state.SelectAction(value) : state.SelectTarget(value), Is.True);
                Assert.That(state.Result, Is.Null); Assert.That(role == PanelRole.Action ? state.SelectTarget("A") : state.SelectAction("SCAN"), Is.False);
                now = 7999; Assert.That(state.Commit(), Is.True); Assert.That(state.Request.DeadlineMonoMs, Is.EqualTo(8000)); Assert.That(state.Request.SlotEndMonoMs, Is.EqualTo(10000));
            }
        }
        [TestCase(PanelMode.LessonAtomic, PanelRole.Action, 7000, 14000, 21000)]
        [TestCase(PanelMode.LessonMessage, PanelRole.Command, 9000, 18000, 25000)]
        public void LessonWindowsUseLessonAnchorAndDoNotEndTheSlot(PanelMode mode, PanelRole role, double opens, double closes, double slot)
        {
            Open(mode, role); now = opens - .001; Assert.That(state.DontKnow(), Is.False); Assert.That(state.Result, Is.Null);
            now = opens; Assert.That(state.DontKnow(), Is.True); Assert.That(state.Request.DeadlineMonoMs, Is.EqualTo(closes)); Assert.That(state.Request.SlotEndMonoMs, Is.EqualTo(slot));
            Assert.That(events.Any(x => x.Kind.Contains("slot")), Is.False);
        }
        [Test] public void AbortedInputDoesNotBecomeAResponseOrAutoResume()
        {
            Open(); state.SelectTarget("E"); state.Abort(); now += 500; state.Tick(); Assert.That(state.Locked, Is.True); Assert.That(state.Aborted, Is.True); Assert.That(state.Result, Is.Null); Assert.That(state.Commit(), Is.False);
        }
        [TestCase(PanelMode.AtomicProbe, PanelRole.Target, 8000)]
        [TestCase(PanelMode.LessonAtomic, PanelRole.Action, 14000)]
        [TestCase(PanelMode.LessonMessage, PanelRole.Command, 18000)]
        [TestCase(PanelMode.Practice, PanelRole.Command, 2000)]
        public void EveryModeLocksAtItsExactDeadline(PanelMode mode, PanelRole role, double deadline)
        {
            Open(mode, role, mode == PanelMode.Practice ? 1000 : (double?)null); now = state.Request.OpensMonoMs; state.Tick();
            if (role != PanelRole.Action) state.SelectTarget("A"); if (role != PanelRole.Target) state.SelectAction("ADD_ONE");
            now = deadline; Assert.That(state.Commit(), Is.False); Assert.That(state.Result.Code, Is.EqualTo(ResponseCode.Timeout));
            Assert.That(events.Last().Kind, Is.EqualTo("post_lock_input")); Assert.That(state.Result.ResponseMonoMs, Is.EqualTo(deadline));
        }
        [Test] public void LogFailureLocksBeforeDeliveryToResponseConsumer()
        {
            bool fail = false, delivered = false; state = new ResponseState(() => now, e => { if (fail) throw new InvalidOperationException("synthetic I/O fault"); });
            state.Responded += _ => delivered = true; Open(); state.SelectTarget("A"); state.SelectAction("ADD_ONE"); fail = true;
            Assert.Throws<InvalidOperationException>(() => state.Commit()); Assert.That(state.Aborted, Is.True); Assert.That(state.Locked, Is.True); Assert.That(delivered, Is.False);
        }
        [Test] public void RegressedClockAndInvalidTimingCannotContinue()
        {
            Open(); now = 999; Assert.Throws<InvalidOperationException>(() => state.Tick()); Assert.That(state.Aborted, Is.True);
            Assert.Throws<ArgumentException>(() => new PanelRequest("x", PanelMode.Practice, PanelRole.Command, 0));
            Assert.Throws<ArgumentException>(() => new PanelRequest("x", PanelMode.FullMessage, PanelRole.Command, 0, 300));
            Assert.Throws<ArgumentException>(() => new PanelRequest("x", PanelMode.AtomicProbe, PanelRole.Command, 0));
            Assert.Throws<ArgumentException>(() => new PanelRequest("x", PanelMode.FullMessage, PanelRole.Command, double.NaN));
            Assert.Throws<ArgumentException>(() => new PanelRequest("x", PanelMode.FullMessage, PanelRole.Command, double.MaxValue));
        }
        [Test] public void FailedFinalResponsePersistenceMarksTheLockedAttemptAborted()
        {
            Open(); state.SelectTarget("A"); state.SelectAction("ADD_ONE"); state.Responded += _ => throw new System.IO.IOException("synthetic final response write failure");
            Assert.Throws<System.IO.IOException>(() => state.Commit()); Assert.That(state.Aborted, Is.True); Assert.That(state.Locked, Is.True);
            Assert.That(state.Commit(), Is.False); Assert.That(events.Count(x => x.Kind == "commit"), Is.EqualTo(1));
        }
        [TestCase("synthetic\n")] [TestCase("synthetic\r\n")] [TestCase("synthetic ")]
        public void OpaqueIdentifierRejectsTrailingWhitespace(string value)
        { Assert.Throws<ArgumentException>(() => new PanelRequest(value, PanelMode.FullMessage, PanelRole.Command, 0)); }
        [Test] public void EverySelectionChangeHasTheSuppliedMonotonicTimestamp()
        {
            Open(); now = 1100; state.SelectTarget("A"); now = 1123; state.SelectAction("FLIP_CARD"); now = 1177; state.SelectTarget("F");
            Assert.That(events.Single(x => x.Kind == "select_action").MonoMs, Is.EqualTo(1123));
            Assert.That(events.Last().Kind, Is.EqualTo("action_cleared_family_change")); Assert.That(events.Last().MonoMs, Is.EqualTo(1177));
            Assert.That(events.Last().Target, Is.EqualTo("F")); Assert.That(events.Last().Action, Is.Null);
        }
    }
}
