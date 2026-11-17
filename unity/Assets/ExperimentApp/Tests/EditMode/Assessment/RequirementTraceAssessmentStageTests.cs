using System;
using System.Collections.Generic;
using System.Linq;
using AcousticVocab.SessionEngine;
using NUnit.Framework;

namespace AcousticVocab.Assessment.Tests
{
    // Requirement trace for questionnaire forms and the post-study corpus lock.
    // Synthetic schedule and journals; no participant data.
    public sealed class RequirementTraceAssessmentStageTests
    {
        const string Hash = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        const string Package = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
        sealed class Clock : ISessionClock { public double NowMs { get; set; } }
        sealed class SessionRows : ISessionJournal
        {
            public readonly List<SessionRecord> Rows = new List<SessionRecord>();
            public IReadOnlyList<SessionRecord> Records => Rows;
            public void Append(SessionRecord r) => Rows.Add(r);
        }
        sealed class StageRows : IAssessmentJournal
        {
            public readonly List<AssessmentRecord> Rows = new List<AssessmentRecord>();
            public IReadOnlyList<AssessmentRecord> Records => Rows;
            public void Append(AssessmentRecord r) => Rows.Add(r);
        }
        static VisitSchedule Schedule(string study, string visit) => new VisitSchedule(Hash, Package, "DEMO", visit, true,
            new[] { new ScheduleBlock("trained", new[] { new SlotItem("trained-0", "trained", "K-a1-r1", null, null, "protected", false, 14, 1, 1) }) }, study, "pilot", Hash);
        static SessionRecord Done(string trial) => new SessionRecord("state_after", new string('c', 32), Hash, trial, null, 0, 0, 14000, 750, ItemState.Done,
            AudibleStatus.ConfirmedAudible, true, true, true, null, "commit", null, trial, new[] { "DEMO-audio" });

        [TestCase("V1")][TestCase("V2")][TestCase("V3")][TestCase("W1")][TestCase("W4")]
        [Category("REQ_AN_12"), NUnit.Framework.Property("Requirement", "REQ-AN-12")]
        public void OwnershipIsRatedOnTheFormsAfterProtectedTrialsAndKeptOutOfTheTrialJournal(string visit)
        {
            var schedule = Schedule("B", visit); var session = new SessionRows(); var stages = new StageRows(); var clock = new Clock { NowMs = 15000 };
            var forms = new AssessmentStages(schedule, session, stages, clock, () => true);
            var ownership = forms.RatingItems.Single(x => x.Id == "ownership");
            Assert.That((ownership.Minimum, ownership.Maximum), Is.EqualTo((1, 7)));
            Assert.Throws<AssessmentFault>(() => forms.BeginForms(), "Forms wait for the protected trials");
            session.Append(Done("trained-0")); var trialRows = session.Rows.ToArray();
            forms.BeginForms();
            while (forms.CurrentRating != null) { clock.NowMs += 1000; var item = forms.CurrentRating; forms.Rate(item.Id, item.Id == "ownership" ? 6 : item.Minimum); }
            Assert.That(forms.FormsComplete, Is.True);
            var row = stages.Rows.Single(x => x.EventKind == "rating" && x.ItemId == "ownership");
            Assert.That(row.Stage, Is.EqualTo("forms")); Assert.That(row.Value, Is.EqualTo(6));
            Assert.That(session.Rows, Is.EqualTo(trialRows), "Ratings never enter the trial (accuracy) journal");
            Assert.That(session.Rows.All(x => x.ResponseCode is null or "commit" or "dont_know" or "timeout"), Is.True);
            Assert.Throws<AssessmentFault>(() => new AssessmentRecord("rating", Hash, 1, "post_w4_optional", "ownership", 6));
            Assert.Throws<AssessmentFault>(() => new AssessmentRecord("rating", Hash, 1, "forms", "ownership", 8));
        }

        [TestCase("A", "D7")][TestCase("B", "V3")][TestCase("B", "W1")]
        [Category("REQ_ACC_04"), NUnit.Framework.Property("Requirement", "REQ-ACC-04"), Category("REQ_VIS_03"), NUnit.Framework.Property("Requirement", "REQ-VIS-03")]
        public void NoDictionaryOrPostStudyActivityIsReachableBeforeTheFinalStudyBForms(string study, string visit)
        {
            var schedule = Schedule(study, visit); var session = new SessionRows(); var clock = new Clock { NowMs = 15000 };
            session.Append(Done("trained-0"));
            var forms = new AssessmentStages(schedule, session, new StageRows(), clock, () => true);
            forms.BeginForms(); while (forms.CurrentRating != null) { clock.NowMs += 1000; forms.Rate(forms.CurrentRating.Id, forms.CurrentRating.Minimum); }
            Assert.That(forms.FormsComplete, Is.True);
            Assert.Throws<AssessmentFault>(() => forms.BeginOptional());
            Assert.Throws<AssessmentFault>(() => forms.DictionaryPermit("K-a1"));
            Assert.Throws<AssessmentFault>(() => forms.RecordOptional("optional_help", "K-a1", "requested"));
            Assert.That(forms.OptionalStarted, Is.False);
        }
    }
}
