using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using NUnit.Framework;

namespace AcousticVocab.ResponsePanel.Tests
{
    // Requirement trace for the response state of full-message test trials.
    public class RequirementTraceResponsePanelTests
    {
        double now;
        List<PanelProcessEvent> events;
        ResponseState state;
        [SetUp] public void Setup() { now = 1000; events = new List<PanelProcessEvent>(); state = new ResponseState(() => now, events.Add); }
        void Open() => state.Open(new PanelRequest("synthetic-trial", PanelMode.FullMessage, PanelRole.Command, 1000));

        [Test, Category("REQ_TEST_03")]
        public void AResponseNeedsATargetAnActionAndACommitPress()
        {
            Open();
            Assert.That(state.Commit(), Is.False); Assert.That(events.Last().Kind, Is.EqualTo("commit_disabled"));
            Assert.That(state.SelectAction("ADD_ONE"), Is.False); Assert.That(state.SelectedAction, Is.Null);
            Assert.That(state.SelectTarget("A"), Is.True);
            Assert.That(state.Commit(), Is.False); Assert.That(events.Last().Kind, Is.EqualTo("commit_disabled"));
            Assert.That(state.SelectAction("ADD_ONE"), Is.True); Assert.That(state.CanCommit, Is.True);
            now = 12999; state.Tick(); Assert.That(state.Result, Is.Null);
            Assert.That(state.Commit(), Is.True);
            Assert.That(state.Result.Code, Is.EqualTo(ResponseCode.Commit));
            Assert.That(state.Result.Target, Is.EqualTo("A")); Assert.That(state.Result.Action, Is.EqualTo("ADD_ONE"));
            // The same complete selection without a Commit press ends as a timeout, not a response tuple.
            Setup(); Open(); state.SelectTarget("B"); state.SelectAction("FLIP_CARD"); now = 13000; state.Tick();
            Assert.That(state.Result.Code, Is.EqualTo(ResponseCode.Timeout));
            Assert.That(state.Result.Target, Is.Null); Assert.That(state.Result.Action, Is.Null);
            Assert.That(state.Result.SelectedTarget, Is.EqualTo("B")); Assert.That(state.Result.SelectedAction, Is.EqualTo("FLIP_CARD"));
        }

        [Test, Category("REQ_TEST_06")]
        public void SelectionsStayEditableUntilCommitAndOnlyOneCommitIsAccepted()
        {
            int responses = 0; state.Responded += _ => responses++;
            Open();
            Assert.That(state.SelectTarget("A"), Is.True); Assert.That(state.SelectAction("ADD_ONE"), Is.True);
            Assert.That(state.SelectAction("ALIGN_ARROW"), Is.True); Assert.That(state.SelectedAction, Is.EqualTo("ALIGN_ARROW"));
            Assert.That(state.SelectTarget("C"), Is.True); Assert.That(state.SelectedTarget, Is.EqualTo("C")); Assert.That(state.SelectedAction, Is.EqualTo("ALIGN_ARROW"));
            Assert.That(state.SelectTarget("G"), Is.True); Assert.That(state.SelectAction("TAG"), Is.True);
            Assert.That(state.SelectTarget("D"), Is.True); Assert.That(state.SelectAction("REMOVE_ONE"), Is.True);
            now = 5000; Assert.That(state.Commit(), Is.True);
            var first = state.Result;
            Assert.That(first.Target, Is.EqualTo("D")); Assert.That(first.Action, Is.EqualTo("REMOVE_ONE"));
            now = 6000;
            Assert.That(state.SelectTarget("A"), Is.False); Assert.That(state.SelectAction("ADD_ONE"), Is.False);
            Assert.That(state.Commit(), Is.False); Assert.That(state.DontKnow(), Is.False);
            now = 13000; state.Tick();
            Assert.That(state.Result, Is.SameAs(first)); Assert.That(responses, Is.EqualTo(1));
            Assert.That(events.Count(x => x.Kind == "commit"), Is.EqualTo(1)); Assert.That(events.Count(x => x.Kind == "timeout"), Is.Zero);
        }

        [Test, Category("REQ_TEST_07")]
        public void PanelClockIsTheHostMonotonicStopwatchAndWindowsFollowTheSuppliedOnset()
        {
            double before = Stopwatch.GetTimestamp() * 1000.0 / Stopwatch.Frequency;
            double panel = PanelJournal.NowMs;
            double after = Stopwatch.GetTimestamp() * 1000.0 / Stopwatch.Frequency;
            Assert.That(panel, Is.InRange(before, after));
            Assert.That(typeof(ResponseState).Assembly.GetReferencedAssemblies().Any(x => x.Name.Contains("StateSources")), Is.False);
            var request = new PanelRequest("synthetic-onset", PanelMode.FullMessage, PanelRole.Command, 123456.5);
            Assert.That(request.OpensMonoMs, Is.EqualTo(123456.5)); Assert.That(request.DeadlineMonoMs, Is.EqualTo(135456.5));
            Assert.That(request.SlotEndMonoMs, Is.EqualTo(137456.5));
        }
    }
}
