using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.SessionEngine.Tests
{
    // Requirement trace for lesson scope per visit (actual public DEMO producer
    // schedules through the app's schedule validator) and for fixed lesson slots
    // in the engine. Synthetic content and clock; no participant data.
    public sealed class RequirementTraceLessonScheduleTests
    {
        static string Repo()
        {
            for (var path = new DirectoryInfo(Directory.GetCurrentDirectory()); path != null; path = path.Parent)
                if (File.Exists(Path.Combine(path.FullName, "schedules/schema/visit-schedule.schema.json"))) return path.FullName;
            throw new Exception("Repository public schedule fixtures unavailable");
        }
        static (JObject doc, JObject permutation) Fixture(string study, string visit)
        {
            string unit = study + "-C01", person = unit + (study == "A" ? "-L01" : "-M1");
            string root = Path.Combine(Repo(), "schedules/examples/demo", study, unit);
            return (SessionJson.Parse(File.ReadAllBytes(Path.Combine(root, "schedules", person, visit + ".json"))),
                SessionJson.Parse(File.ReadAllBytes(Path.Combine(root, "permutation.json"))));
        }
        static JObject Block(JObject doc, string name) => (JObject)((JArray)doc["blocks"]).Single(x => (string)x["block"] == name);
        static int Wave(string study, string visit) => study == "A" ? 3 : visit == "V1" ? 1 : visit == "V2" ? 2 : 3;
        static string[] NewAtoms(JObject permutation, string study, string visit) => permutation["atoms"]
            .Where(x => study == "A" || (int)x["matrix_wave"] == Wave(study, visit)).Select(x => (string)x["atom_id"]).OrderBy(x => x).ToArray();
        static string[] NewMessages(JObject permutation, string study, string visit) => permutation["messages"]
            .Where(x => (string)x["status"] == "trained" && (study == "A" || (int)x["training_wave"] == Wave(study, visit))).Select(x => (string)x["message_id"]).OrderBy(x => x).ToArray();
        static ScheduleBlock Validated(JObject doc, JObject permutation, string name) => ScheduleLoader.ValidateBlocks(doc, permutation).Single(x => x.Name == name);

        [TestCase("A", "D0")][TestCase("B", "V1")][TestCase("B", "V2")][TestCase("B", "V3")]
        [Category("REQ_LES_01"), NUnit.Framework.Property("Requirement", "REQ-LES-01")]
        public void EveryAtomIntroducedAtAVisitGetsExactlyOneTwentySecondLesson(string study, string visit)
        {
            var f = Fixture(study, visit); var lessons = Validated(f.doc, f.permutation, "atomic_lessons").Items;
            Assert.That(lessons.Select(x => x.ContentId).OrderBy(x => x), Is.EqualTo(NewAtoms(f.permutation, study, visit)));
            Assert.That(lessons.All(x => x.TrialType == "atomic_lesson" && x.SlotSeconds == 20 && x.Plays == 3 && x.Phase == "teaching" && !x.Heldout), Is.True);
            var items = (JArray)Block(f.doc, "atomic_lessons")["items"];
            foreach (string key in new[] { "atom_id", "intended" }) items[1][key] = items[0][key].DeepClone();
            Assert.Throws<SessionFault>(() => ScheduleLoader.ValidateBlocks(f.doc, f.permutation), "An atom taught twice is refused");
        }

        [Test, Category("REQ_LES_01"), NUnit.Framework.Property("Requirement", "REQ-LES-01"), Category("REQ_LES_03"), NUnit.Framework.Property("Requirement", "REQ-LES-03")]
        public void AcrossStudyBAcquisitionVisitsEachAtomIsTaughtOnceAndEachTrainedMessageTwice()
        {
            var atoms = new List<string>(); var messages = new List<string>(); JObject permutation = null;
            foreach (string visit in new[] { "V1", "V2", "V3" })
            {
                var f = Fixture("B", visit); permutation = f.permutation; var blocks = ScheduleLoader.ValidateBlocks(f.doc, f.permutation);
                atoms.AddRange(blocks.Single(x => x.Name == "atomic_lessons").Items.Select(x => x.ContentId));
                messages.AddRange(blocks.Single(x => x.Name == "message_lessons").Items.Select(x => x.ContentId));
            }
            Assert.That(atoms.OrderBy(x => x), Is.EqualTo(permutation["atoms"].Select(x => (string)x["atom_id"]).OrderBy(x => x)));
            var trained = permutation["messages"].Where(x => (string)x["status"] == "trained").Select(x => (string)x["message_id"]).ToArray();
            Assert.That(trained.Length, Is.EqualTo(18));
            Assert.That(messages.GroupBy(x => x).ToDictionary(x => x.Key, x => x.Count()), Is.EquivalentTo(trained.ToDictionary(x => x, x => 2)));
        }

        [TestCase("A", "D0")][TestCase("B", "V1")][TestCase("B", "V2")][TestCase("B", "V3")]
        [Category("REQ_LES_03"), NUnit.Framework.Property("Requirement", "REQ-LES-03")]
        public void EveryMessageTrainedAtAVisitGetsExactlyTwoTwentyFourSecondLessonsOnePerPass(string study, string visit)
        {
            var f = Fixture(study, visit); var lessons = Validated(f.doc, f.permutation, "message_lessons").Items;
            var expected = NewMessages(f.permutation, study, visit);
            Assert.That(lessons.Count, Is.EqualTo(2 * expected.Length));
            foreach (int pass in new[] { 1, 2 }) Assert.That(lessons.Where(x => x.Pass == pass).Select(x => x.ContentId).OrderBy(x => x), Is.EqualTo(expected));
            Assert.That(lessons.All(x => x.TrialType == "message_lesson" && x.SlotSeconds == 24 && x.Plays == 3 && x.Phase == "teaching" && !x.Heldout), Is.True);
            var items = (JArray)Block(f.doc, "message_lessons")["items"]; int size = items.Count / 2;
            foreach (string key in new[] { "message_id", "intended" }) items[size][key] = items[size + 1][key].DeepClone();
            Assert.Throws<SessionFault>(() => ScheduleLoader.ValidateBlocks(f.doc, f.permutation), "A third lesson for one message is refused");
        }

        [Test, Category("REQ_SA_01"), NUnit.Framework.Property("Requirement", "REQ-SA-01")]
        public void StudyAFirstVisitTeachesTheWholeVocabularyThroughAlignedLessonsOnly()
        {
            var f = Fixture("A", "D0"); var blocks = ScheduleLoader.ValidateBlocks(f.doc, f.permutation);
            var atoms = blocks.Single(x => x.Name == "atomic_lessons").Items; var messages = blocks.Single(x => x.Name == "message_lessons").Items;
            Assert.That(atoms.Select(x => x.ContentId).Distinct().Count(), Is.EqualTo(16));
            Assert.That(messages.Select(x => x.ContentId).Distinct().Count(), Is.EqualTo(18));
            Assert.That(messages.All(x => x.Presentation == "structured"), Is.True);
            Assert.That(f.doc["dictionary_messages"].Count(), Is.EqualTo(18));
            var items = (JArray)Block(f.doc, "message_lessons")["items"]; items[items.Count - 1]["presentation"] = "dictionary";
            Assert.Throws<SessionFault>(() => ScheduleLoader.ValidateBlocks(f.doc, f.permutation), "Study A refuses a static lesson");
            var d7 = Fixture("A", "D7"); Assert.That(ScheduleLoader.ValidateBlocks(d7.doc, d7.permutation).SelectMany(x => x.Items).Any(x => x.Phase is "teaching" or "selection"), Is.False);
        }

        [TestCase("A", "D7", "A", "D0", "atomic_lessons")]
        [TestCase("B", "W1", "B", "V3", "message_lessons")]
        [TestCase("B", "W4", "B", "V3", "atom_menus")]
        [Category("REQ_SA_03"), NUnit.Framework.Property("Requirement", "REQ-SA-03"), Category("REQ_VIS_03"), NUnit.Framework.Property("Requirement", "REQ-VIS-03"), Category("REQ_ACC_04"), NUnit.Framework.Property("Requirement", "REQ-ACC-04")]
        public void FollowUpVisitsContainNoTeachingSelectionOrReplayAndRefuseAnInjectedOne(string study, string visit, string sourceStudy, string sourceVisit, string block)
        {
            var f = Fixture(study, visit); var items = ScheduleLoader.ValidateBlocks(f.doc, f.permutation).SelectMany(x => x.Items).ToArray();
            Assert.That(items.Any(x => x.Phase is "teaching" or "selection" || x.TrialType.Contains("lesson") || x.TrialType.Contains("menu")), Is.False);
            Assert.That(items.All(x => x.Plays <= 1), Is.True, "No item of a follow-up visit repeats a sound");
            var source = Fixture(sourceStudy, sourceVisit); var injected = Block(source.doc, block).DeepClone();
            ((JArray)f.doc["blocks"]).Insert(0, injected);
            Assert.Throws<SessionFault>(() => ScheduleLoader.ValidateBlocks(f.doc, f.permutation));
        }

        static IEnumerable<string> AtomsOf(SlotItem item, JObject permutation)
        {
            if (item.ContentId == null || item.TrialType is "profile_menu" or "speech" or "no_cue") yield break;
            var message = permutation["messages"].SingleOrDefault(x => (string)x["message_id"] == item.ContentId);
            if (message == null) { yield return item.ContentId; yield break; }
            yield return (string)message["action_atom"]; yield return (string)message["referent_atom"];
        }

        [TestCase("V1")][TestCase("V2")]
        [Category("REQ_CURR_05"), NUnit.Framework.Property("Requirement", "REQ-CURR-05")]
        public void AnAcquisitionVisitPlaysNoFutureAtomOrMessageAndRefusesOne(string visit)
        {
            var f = Fixture("B", visit); int wave = Wave("B", visit);
            var introduced = new HashSet<string>(f.permutation["atoms"].Where(x => (int)x["matrix_wave"] <= wave).Select(x => (string)x["atom_id"]));
            var blocks = ScheduleLoader.ValidateBlocks(f.doc, f.permutation);
            Assert.That(blocks.SelectMany(x => x.Items).SelectMany(x => AtomsOf(x, f.permutation)).All(introduced.Contains), Is.True);
            Assert.That(blocks.Single(x => x.Name == "atomic_lessons").Items.Select(x => x.ContentId).OrderBy(x => x), Is.EqualTo(NewAtoms(f.permutation, "B", visit)));
            var future = f.permutation["atoms"].First(x => (int)x["matrix_wave"] == wave + 1);
            var atomic = Fixture("B", visit); var lesson = (JObject)Block(atomic.doc, "atomic_lessons")["items"][0];
            lesson["atom_id"] = (string)future["atom_id"];
            lesson["intended"] = new JObject { ["atom_id"] = future["atom_id"], ["family"] = future["family"], ["index"] = future["index"], ["kind"] = "atom", ["role"] = future["role"], ["semantic_label"] = future["semantic_label"] };
            Assert.Throws<SessionFault>(() => ScheduleLoader.ValidateBlocks(atomic.doc, atomic.permutation), "A future atom lesson is refused");
            var messages = Fixture("B", visit); var row = (JObject)Block(messages.doc, "message_lessons")["items"][0];
            string family = (string)row["intended"]["family"];
            var later = messages.permutation["messages"].First(x => (string)x["status"] == "trained" && (int)x["training_wave"] == wave + 1 && (string)x["family"] == family);
            string id = (string)later["message_id"]; row["message_id"] = id;
            row["intended"] = new JObject { ["action_index"] = id[3] - '0', ["family"] = family, ["kind"] = "message", ["message_id"] = id, ["referent_index"] = id[6] - '0',
                ["semantic_action"] = later["semantic_action"], ["semantic_referent"] = later["semantic_referent"] };
            Assert.Throws<SessionFault>(() => ScheduleLoader.ValidateBlocks(messages.doc, messages.permutation), "A future message lesson is refused");
        }

        // ---- Engine-owned lesson slots ----

        sealed class Clock : ISessionClock { public double Time; public double NowMs => Time; }
        sealed class Memory : ISessionJournal
        {
            public readonly List<SessionRecord> Rows = new List<SessionRecord>();
            public IReadOnlyList<SessionRecord> Records => Rows;
            public void Append(SessionRecord value) => Rows.Add(value);
        }
        sealed class Probe : ISlotContent
        {
            readonly Clock clock; bool reset;
            public readonly List<(string call, double at)> Calls = new List<(string, double)>();
            public SlotContext Context;
            public Probe(Clock clock) { this.clock = clock; }
            public void Prepare(SlotContext context) { Context = context; }
            public SlotReadiness Readiness => new SlotReadiness(true, true, true, true, true, true, true, true);
            public bool ResetComplete => reset;
            public void RequestCue(SlotContext context, INovelSlotAuthorization permit) => Calls.Add(("cue", clock.Time));
            public void OpenResponse(SlotContext context) => Calls.Add(("open", clock.Time));
            public void CloseResponse(SlotContext context) => Calls.Add(("close", clock.Time));
            public void RequestReset(SlotContext context) { Calls.Add(("reset", clock.Time)); reset = true; }
            public void Interrupt(string code) => Calls.Add(("interrupt", clock.Time));
        }
        sealed class Factory : ISlotContentFactory
        {
            readonly Clock clock; public readonly List<Probe> Items = new List<Probe>();
            public Factory(Clock clock) { this.clock = clock; }
            public ISlotContent Create(SlotItem item) { var value = new Probe(clock); Items.Add(value); return value; }
        }

        [TestCase(true, "commit")][TestCase(true, "dont_know")][TestCase(true, null)]
        [TestCase(false, "commit")][TestCase(false, "dont_know")][TestCase(false, null)]
        [Category("REQ_LES_02"), NUnit.Framework.Property("Requirement", "REQ-LES-02"), Category("REQ_LES_04"), NUnit.Framework.Property("Requirement", "REQ-LES-04"), Category("REQ_LES_11"), NUnit.Framework.Property("Requirement", "REQ-LES-11"), Category("REQ_LES_14"), NUnit.Framework.Property("Requirement", "REQ-LES-14")]
        public void EngineKeepsFixedLessonSlotsWhateverTheResponseAndNeverRepeatsALesson(bool atomic, string response)
        {
            int slot = atomic ? 20 : 24; double opens = atomic ? 6000 : 8000, closes = atomic ? 13000 : 17000;
            var items = Enumerable.Range(0, 3).Select(i => new SlotItem("DEMO-lesson-" + i, atomic ? "atomic_lesson" : "message_lesson", atomic ? "K-a1" : "K-a1-r1",
                atomic ? "action" : null, atomic ? null : "structured", "teaching", false, slot, 3, 1)).ToArray();
            var schedule = new VisitSchedule(new string('a', 64), new string('b', 64), "DEMO", "D0", true, new[] { new ScheduleBlock(atomic ? "atomic_lessons" : "message_lessons", items) });
            var clock = new Clock(); var journal = new Memory(); var factory = new Factory(clock);
            var engine = new FixedSlotEngine(schedule, clock, journal, factory);
            engine.ConfirmResume(); engine.Tick();
            var answered = new HashSet<string>();
            while (engine.Status == SessionState.Running && clock.Time < 200000)
            {
                clock.Time += 50; engine.Tick();
                // A fast response on the first frame of the window.
                if (response != null && engine.CurrentState == ItemState.ResponseOpen && answered.Add(engine.CurrentTrialId)) engine.RecordResponse(response);
            }
            Assert.That(engine.Status, Is.EqualTo(SessionState.Complete));
            Assert.That(factory.Items.Count, Is.EqualTo(3), "No extra lesson is created");
            Assert.That(journal.Rows.Any(x => x.Event == "retry_queued"), Is.False);
            Assert.That(journal.Rows.Count(x => x.Event == "response"), Is.EqualTo(response == null ? 0 : 3));
            double first = factory.Items[0].Context.OnsetMonoMs;
            for (int i = 0; i < 3; i++)
            {
                var probe = factory.Items[i]; double onset = probe.Context.OnsetMonoMs;
                Assert.That(onset, Is.EqualTo(first + i * slot * 1000), "Slot length is fixed");
                Assert.That(probe.Calls.Count(x => x.call == "cue"), Is.EqualTo(1));
                Assert.That(probe.Calls.Single(x => x.call == "open").at - onset, Is.EqualTo(opens));
                Assert.That(probe.Calls.Single(x => x.call == "close").at - onset, Is.EqualTo(closes));
            }
            Assert.That(clock.Time, Is.EqualTo(first + 3 * slot * 1000), "The visit ends only after every full slot");
        }
    }
}
