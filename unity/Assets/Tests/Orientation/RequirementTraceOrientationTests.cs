using System.Collections.Generic;
using System.IO;
using System.Linq;
using AcousticVocab.Foundation.Editor;
using AcousticVocab.ResponsePanel;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Orientation.Tests
{
    // Requirement trace for the silent orientation and its eligibility outcome.
    // Synthetic clock and the public example plan; no human eligibility evidence.
    public sealed class RequirementTraceOrientationTests
    {
        double now;
        OrientationFlow flow;
        List<JObject> rows;

        static OrientationPlan Plan()
        {
            var value = JObject.Parse(File.ReadAllText(Path.Combine(FoundationBuild.RepositoryRoot, "apparatus/orientation/orientation-plan.example.json")));
            value["content_status"] = "protocol_reviewed"; value["review_evidence_sha256"] = new string('a', 64);
            return OrientationPlan.Parse(value.ToString(), (string)value["protocol_version"]);
        }
        void Setup() { now = 0; rows = new List<JObject>(); flow = new OrientationFlow(Plan(), () => now, rows.Add); }
        void Introduction()
        {
            for (int i = 0; i < 8; i++) { now += 10000; flow.CompleteDemo(flow.CurrentCard.Id, 10000, 10000, 1000d / 30); flow.Next(); }
            for (int i = 0; i < 8; i++) flow.Next();
        }
        void Check(int wrong)
        {
            for (int i = 0; i < 8; i++)
            {
                var item = flow.CurrentItem; var request = flow.OpenPractice();
                var panel = new ResponseState(() => now, _ => { }); panel.Responded += flow.Respond; panel.Open(request); now += 1;
                if (i == wrong) Assert.That(panel.DontKnow(), Is.True);
                else { panel.SelectTarget(item.Target); panel.SelectAction(item.Action); Assert.That(panel.Commit(), Is.True); }
                flow.Next();
            }
        }
        static string Event(JObject row) => (string)row["event"];

        [Test, Category("REQ_ORI_01"), NUnit.Framework.Property("Requirement", "REQ-ORI-01"), Category("REQ_ORI_02"), NUnit.Framework.Property("Requirement", "REQ-ORI-02")]
        public void AllEightActionsAndTargetsAreShownVisuallyWithoutSoundBeforePractice()
        {
            Setup(); flow.Start(); Introduction();
            Assert.That(flow.Stage, Is.EqualTo(OrientationStage.Practice));
            var actions = rows.Where(r => Event(r) == "action_screen").ToArray();
            Assert.That(actions.Select(r => (string)r["action"]), Is.EquivalentTo(PublicCommands.Actions));
            Assert.That(actions.All(r => (bool)r["kinematic_visualization"]), Is.True);
            Assert.That(rows.Where(r => Event(r) == "kinematic_demo_completed").Select(r => (string)r["action"]), Is.EquivalentTo(PublicCommands.Actions));
            Assert.That(rows.Where(r => Event(r) == "target_screen").Select(r => (string)r["target"]), Is.EquivalentTo(PublicCommands.Targets));
            var plan = Plan();
            Assert.That(plan.Actions.Concat(plan.Targets).All(c => c.Title.Length > 0 && c.Meaning.Length > 0), Is.True);
            // Every meaning is introduced before the first practice item or any outcome.
            Assert.That(rows.Any(r => Event(r) is "practice_open" or "eligibility_outcome"), Is.False);
            Assert.That(flow.Outcome, Is.Null); Assert.That(flow.EligibleOutcomeRecorded, Is.False);
            // No audio, waveform or package field is ever journaled by the flow.
            Assert.That(rows.SelectMany(r => r.Properties()).Any(p => p.Name.Contains("audio") || p.Name.Contains("pcm") || p.Name.Contains("package") || p.Name.Contains("wave")), Is.False);
        }

        [Test, Category("REQ_ELIG_01"), NUnit.Framework.Property("Requirement", "REQ-ELIG-01")]
        public void OnlyACompletedNonAcousticInterfaceCheckGrantsEligibility()
        {
            Setup(); Assert.That(flow.EligibleOutcomeRecorded, Is.False); flow.Start(); Introduction();
            for (int i = 0; i < 8; i++)
            {
                Assert.That(flow.EligibleOutcomeRecorded, Is.False);
                var item = flow.CurrentItem; var request = flow.OpenPractice();
                Assert.That(request.Mode, Is.EqualTo(PanelMode.Practice)); Assert.That(request.Role, Is.EqualTo(PanelRole.Command));
                Assert.That(item.Request.Length, Is.GreaterThan(0));
                var panel = new ResponseState(() => now, _ => { }); panel.Responded += flow.Respond; panel.Open(request); now += 1;
                panel.SelectTarget(item.Target); panel.SelectAction(item.Action); Assert.That(panel.Commit(), Is.True); flow.Next();
            }
            Assert.That(flow.Outcome.Code, Is.EqualTo(EligibilityCode.PassFirst)); Assert.That(flow.EligibleOutcomeRecorded, Is.True);
        }

        [TestCase(-1, -1, EligibilityCode.PassFirst, "pass_first")]
        [TestCase(3, -1, EligibilityCode.PassSecond, "pass_second")]
        [TestCase(3, 5, EligibilityCode.Fail, "fail")]
        [TestCase(0, 7, EligibilityCode.Fail, "fail")]
        [Category("REQ_ORI_03"), NUnit.Framework.Property("Requirement", "REQ-ORI-03")]
        public void SecondCheckNeedsEightOfEightAfterOneRepeatAndOutcomeIsNeverALearningResult(int firstWrong, int secondWrong, EligibilityCode code, string label)
        {
            Setup(); flow.Start(); Introduction(); Check(firstWrong);
            if (firstWrong >= 0)
            {
                Assert.That(flow.Stage, Is.EqualTo(OrientationStage.Reexplanation)); Assert.That(flow.Outcome, Is.Null);
                flow.Next(); Introduction(); Check(secondWrong);
            }
            Assert.That(flow.Stage, Is.EqualTo(OrientationStage.RecordedOutcome));
            Assert.That(flow.Outcome.Code, Is.EqualTo(code)); Assert.That(flow.Reexplanations, Is.EqualTo(firstWrong >= 0 ? 1 : 0));
            Assert.That(rows.Count(r => Event(r) == "check_completed"), Is.EqualTo(firstWrong >= 0 ? 2 : 1));
            Assert.That(rows.Count(r => Event(r) == "practice_response"), Is.EqualTo(firstWrong >= 0 ? 16 : 8));
            var outcome = rows.Single(r => Event(r) == "eligibility_outcome");
            Assert.That((string)outcome["outcome"], Is.EqualTo(label));
            Assert.That((bool)outcome["preallocation"], Is.True); Assert.That((bool)outcome["learning_result"], Is.False);
            // No third check and no second repeat after a recorded outcome.
            Assert.Throws<OrientationFault>(() => flow.Next()); Assert.Throws<OrientationFault>(() => flow.OpenPractice());
            Assert.That(rows.Count(r => Event(r) == "standard_reexplanation"), Is.EqualTo(firstWrong >= 0 ? 1 : 0));
            Assert.That(flow.EligibleOutcomeRecorded, Is.EqualTo(code != EligibilityCode.Fail));
        }
    }
}
