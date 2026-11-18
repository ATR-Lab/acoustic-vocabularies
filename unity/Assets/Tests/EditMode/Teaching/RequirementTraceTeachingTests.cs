using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using AcousticVocab.ResponsePanel;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Teaching.Tests
{
    // Requirement trace for lesson timing, lesson feedback, grammar familiarisation
    // assets and the corpus lock. Synthetic clocks, silence and DEMO identifiers only.
    public sealed class RequirementTraceTeachingTests
    {
        const string Hash = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        const BindingFlags Hidden = BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public;
        static T New<T>(params object[] args) => (T)Activator.CreateInstance(typeof(T), Hidden, null, args, null);

        static SlotContext Context(bool atomic, string presentation = "structured", int? slot = null, int plays = 3) =>
            new SlotContext(new SlotItem("DEMO-trace", atomic ? "atomic_lesson" : "message_lesson", atomic ? "K-a1" : "K-a1-r1", atomic ? "action" : null,
                atomic ? null : presentation, "teaching", false, slot ?? (atomic ? 20 : 24), plays, 1), 750, null);

        // Drives the real timeline with the same synthetic delivery as LessonTimelineTests.
        sealed class Run
        {
            internal readonly List<LessonEvent> Events = new List<LessonEvent>();
            internal readonly List<double> Plays = new List<double>();
            internal readonly List<(double at, LessonDisplayPhase phase)> Phases = new List<(double, LessonDisplayPhase)>();
            internal readonly List<(double at, LessonHighlight value)> Highlights = new List<(double, LessonHighlight)>();
            internal readonly LessonTimeline Timeline;
            internal readonly SlotContext Context;
            readonly bool atomic;
            double now;
            internal Run(bool atomic, bool aligned)
            {
                this.atomic = atomic; Context = RequirementTraceTeachingTests.Context(atomic);
                Timeline = new LessonTimeline(Context, aligned, 24000, atomic ? 0 : 33600, "DEMO-meaning", Hash, atomic ? null : Hash, atomic ? null : Hash, Events.Add);
                Timeline.PlayRequested += (index, id, at) => Plays.Add(at - 750);
                Timeline.DisplayChanged += (phase, feedback) => Phases.Add((now - 750, phase));
                Timeline.HighlightChanged += value => Highlights.Add((now - 750, value));
                Timeline.Start(0);
            }
            internal double[] Offsets => atomic ? new[] { 0d, 6000, 14000 } : new[] { 0d, 8000, 18000 };
            internal void AdvanceTo(double until, Action<double> step = null)
            {
                double duration = atomic ? 500 : 1400;
                for (now = Math.Max(5, now + 5); now <= until; now += 5)
                {
                    Timeline.Tick(now);
                    for (int i = 0; i < 3; i++)
                    {
                        if (now == 750 + Offsets[i]) Timeline.Onset(Context.AudioRequestIds[i], now, 10, now);
                        if (now == 750 + Offsets[i] + duration) Timeline.Completed(Context.AudioRequestIds[i], now);
                    }
                    step?.Invoke(now);
                }
                now -= 5;
            }
            // feedback: content id to submit; at: elapsed time of the response; timeout uses the deadline frame.
            internal void Complete(string feedback = "DEMO-correct", double? at = null, bool timeout = false)
            {
                double respond = 750 + (timeout ? Context.Item.ResponseClosesSeconds * 1000 : at ?? Offsets[1] + 100);
                AdvanceTo(Context.EndMonoMs, t =>
                {
                    if (t == respond) Timeline.Response(feedback, t, timeout);
                    if (t == 750 + Context.Item.ResponseClosesSeconds * 1000) Timeline.RequestReset();
                });
            }
        }

        static void AssertLessonTimeline(bool atomic, double[] plays, double opens, double closes, double end)
        {
            var run = new Run(atomic, true); run.Complete();
            Assert.That(run.Plays, Is.EqualTo(plays));
            Assert.That(run.Phases, Is.EqualTo(new[] { (0d, LessonDisplayPhase.Definition), (opens, LessonDisplayPhase.Retrieval), (plays[2], LessonDisplayPhase.Feedback), (end, LessonDisplayPhase.Ended) }));
            Assert.That(run.Events.Count(x => x.Kind == "retrieval_opportunity"), Is.EqualTo(1));
            Assert.That(run.Events.Single(x => x.Kind == "lesson_end").ExpectedMonoMs, Is.EqualTo(750 + end));
            var item = run.Context.Item;
            Assert.That(item.ResponseOpensSeconds * 1000, Is.EqualTo(opens)); Assert.That(item.ResponseClosesSeconds * 1000, Is.EqualTo(closes));
            Assert.That(item.SlotSeconds * 1000, Is.EqualTo(end));
            var request = new PanelRequest("DEMO-trace", atomic ? PanelMode.LessonAtomic : PanelMode.LessonMessage, atomic ? PanelRole.Action : PanelRole.Command, 750);
            Assert.That(request.OpensMonoMs, Is.EqualTo(750 + opens)); Assert.That(request.DeadlineMonoMs, Is.EqualTo(750 + closes)); Assert.That(request.SlotEndMonoMs, Is.EqualTo(750 + end));
            // The timeline accepts one participant response exactly inside [opens, closes).
            foreach (var (at, accepted) in new[] { (opens - 5, false), (opens, true), (closes - 5, true), (closes, false) })
            {
                var probe = new Run(atomic, true); probe.AdvanceTo(750 + at);
                if (accepted) Assert.DoesNotThrow(() => probe.Timeline.Response("DEMO-correct", 750 + at), at.ToString());
                else Assert.Throws<SessionFault>(() => probe.Timeline.Response("DEMO-correct", 750 + at), at.ToString());
            }
        }

        [Test, Category("REQ_LES_02"), NUnit.Framework.Property("Requirement", "REQ-LES-02")]
        public void AtomicLessonPlaysAtZeroSixFourteenAcceptsSixToThirteenAndEndsAtTwenty() =>
            AssertLessonTimeline(true, new[] { 0d, 6000, 14000 }, 6000, 13000, 20000);

        [Test, Category("REQ_LES_04"), NUnit.Framework.Property("Requirement", "REQ-LES-04")]
        public void MessageLessonPlaysAtZeroEightEighteenAcceptsEightToSeventeenAndEndsAtTwentyFour() =>
            AssertLessonTimeline(false, new[] { 0d, 8000, 18000 }, 8000, 17000, 24000);

        [Test, Category("REQ_LES_01"), NUnit.Framework.Property("Requirement", "REQ-LES-01"), Category("REQ_LES_03"), NUnit.Framework.Property("Requirement", "REQ-LES-03")]
        public void LessonContractAcceptsOnlyTwentySecondAtomicAndTwentyFourSecondMessageSlotsWithThreePlays()
        {
            Assert.DoesNotThrow(() => new Run(true, true));
            Assert.DoesNotThrow(() => new Run(false, true));
            Assert.Throws<SessionFault>(() => new LessonTimeline(Context(true, slot: 24), true, 24000, 0, "DEMO-meaning", Hash, null, null, _ => { }));
            Assert.Throws<SessionFault>(() => new LessonTimeline(Context(false, slot: 20), true, 24000, 33600, "DEMO-meaning", Hash, Hash, Hash, _ => { }));
            Assert.Throws<SessionFault>(() => new LessonTimeline(Context(true, plays: 2), true, 24000, 0, "DEMO-meaning", Hash, null, null, _ => { }));
            Assert.Throws<SessionFault>(() => new LessonTimeline(Context(false, plays: 4), true, 24000, 33600, "DEMO-meaning", Hash, Hash, Hash, _ => { }));
        }

        static string Key(LessonEvent e) => string.Join("|", e.Kind, e.MonoMs, e.ExpectedMonoMs, e.PresentationIndex, e.MeaningDisplayId, e.FeedbackContentId, e.Highlight, e.PcmSha256, e.ActionPcmSha256, e.ReferentPcmSha256);

        [Test, Category("REQ_LES_05"), NUnit.Framework.Property("Requirement", "REQ-LES-05")]
        public void AtomicLessonIsIdenticalWhateverFormatTheFamilyIsAssigned()
        {
            var aligned = new Run(true, true); var dictionary = new Run(true, false); aligned.Complete(); dictionary.Complete();
            Assert.That(aligned.Events.Select(Key), Is.EqualTo(dictionary.Events.Select(Key)));
            Assert.That(aligned.Highlights, Is.Empty); Assert.That(dictionary.Highlights, Is.Empty);
            Assert.That(aligned.Events.Any(x => x.Kind.StartsWith("highlight", StringComparison.Ordinal)), Is.False);
            Assert.That(aligned.Plays, Is.EqualTo(dictionary.Plays)); Assert.That(aligned.Phases, Is.EqualTo(dictionary.Phases));
        }

        [Test, Category("REQ_LES_06"), NUnit.Framework.Property("Requirement", "REQ-LES-06"), Category("REQ_LES_07"), NUnit.Framework.Property("Requirement", "REQ-LES-07"), Category("REQ_LES_08"), NUnit.Framework.Property("Requirement", "REQ-LES-08")]
        public void MessageFormatsDifferOnlyByHighlightDuringGuidedAndCorrectivePlayback()
        {
            var aligned = new Run(false, true); var dictionary = new Run(false, false); aligned.Complete(); dictionary.Complete();
            Assert.That(aligned.Events.Where(x => !x.Kind.StartsWith("highlight", StringComparison.Ordinal)).Select(Key), Is.EqualTo(dictionary.Events.Select(Key)));
            Assert.That(dictionary.Highlights, Is.Empty, "Static lessons never change the shown meaning");
            // Action label lights for the 500 ms action motif, target label for the 700 ms target motif after the 200 ms gap.
            var expected = new List<(double, LessonHighlight)>();
            foreach (double play in new[] { 0d, 18000 })
                expected.AddRange(new[] { (play, LessonHighlight.Action), (play + 500, LessonHighlight.None), (play + 700, LessonHighlight.Target), (play + 1400, LessonHighlight.None) });
            Assert.That(aligned.Highlights, Is.EqualTo(expected));
            // No highlight during the retrieval phase or its play.
            Assert.That(aligned.Highlights.Where(x => x.at >= 8000 && x.at < 18000), Is.Empty);
        }

        [TestCase(true)][TestCase(false)]
        [Category("REQ_LES_11"), NUnit.Framework.Property("Requirement", "REQ-LES-11"), Category("REQ_LES_14"), NUnit.Framework.Property("Requirement", "REQ-LES-14")]
        public void WrongMissingOrFastResponsesNeitherAddPlaysNorRepeatNorEndTheLessonEarly(bool atomic)
        {
            var reference = new Run(atomic, true); reference.Complete("DEMO-correct");
            var cases = new List<Run>();
            var fast = new Run(atomic, true); fast.Complete("DEMO-correct", fast.Offsets[1]); cases.Add(fast);
            var wrong = new Run(atomic, true); wrong.Complete("DEMO-incorrect"); cases.Add(wrong);
            var late = new Run(atomic, true); late.Complete("DEMO-incorrect", late.Context.Item.ResponseClosesSeconds * 1000 - 5); cases.Add(late);
            var missing = new Run(atomic, true); missing.Complete("DEMO-timeout", timeout: true); cases.Add(missing);
            foreach (var run in cases)
            {
                Assert.That(run.Plays, Is.EqualTo(reference.Plays)); Assert.That(run.Phases, Is.EqualTo(reference.Phases));
                Assert.That(run.Events.Count(x => x.Kind == "play_request"), Is.EqualTo(3));
                Assert.That(run.Events.Count(x => x.Kind == "retrieval_opportunity"), Is.EqualTo(1));
                Assert.That(run.Events.Single(x => x.Kind == "lesson_end").MonoMs, Is.EqualTo(run.Context.EndMonoMs));
                Assert.That(run.Timeline.Ended, Is.True);
            }
            // The timeline exposes no replay, repeat or restart entry point.
            var names = typeof(LessonTimeline).GetMethods(BindingFlags.Instance | BindingFlags.Public | BindingFlags.DeclaredOnly).Select(x => x.Name).ToArray();
            Assert.That(names.Where(x => x.Contains("Replay") || x.Contains("Repeat") || x.Contains("Restart") || x.Contains("Skip")), Is.Empty);
            Assert.Throws<SessionFault>(() => reference.Timeline.Start(reference.Context.EndMonoMs + 5));
        }

        // ---- Lesson feedback ----

        static TeachingCatalog FeedbackCatalog()
        {
            var feedback = new JObject();
            foreach (string kind in new[] { "atomic", "correct", "incorrect", "timeout" })
                feedback[kind] = new JObject { ["id"] = "DEMO-feedback-" + kind, ["text"] = "Synthetic " + kind + " feedback" };
            return New<TeachingCatalog>(null, new JObject(), new JObject { ["feedback"] = feedback }, Hash, Hash, Hash, Hash, null, "D0");
        }
        static LessonMaterial Material(bool atomic, bool aligned) => new LessonMaterial
        {
            Atomic = atomic, Aligned = aligned, ExpectedAction = "FLIP_CARD", ExpectedTarget = atomic ? null : "B"
        };
        static PanelResponse Respond(bool atomic, string kind)
        {
            double now = 750; var request = new PanelRequest("DEMO-trace", atomic ? PanelMode.LessonAtomic : PanelMode.LessonMessage, atomic ? PanelRole.Action : PanelRole.Command, 750);
            var state = new ResponseState(() => now, _ => { }); PanelResponse result = null; state.Responded += x => result = x;
            now = request.OpensMonoMs + 100; state.Open(request);
            if (kind == "timeout") { now = request.DeadlineMonoMs; state.Tick(); }
            else if (kind == "dont_know") state.DontKnow();
            else
            {
                if (!atomic) state.SelectTarget(kind == "wrong_target" ? "C" : "B");
                state.SelectAction(kind == "wrong_action" ? "ADD_ONE" : "FLIP_CARD"); state.Commit();
            }
            Assert.That(result, Is.Not.Null); return result;
        }

        [Test, Category("REQ_LES_12"), NUnit.Framework.Property("Requirement", "REQ-LES-12"), Category("REQ_LES_10"), NUnit.Framework.Property("Requirement", "REQ-LES-10")]
        public void MessageLessonFeedbackFollowsTheSubmittedResponseIdenticallyForBothFormats()
        {
            var catalog = FeedbackCatalog();
            var expected = new Dictionary<string, string> { ["correct"] = "correct", ["wrong_target"] = "incorrect", ["wrong_action"] = "incorrect", ["dont_know"] = "incorrect", ["timeout"] = "timeout" };
            foreach (var pair in expected)
            {
                string aligned = catalog.Feedback(Material(false, true), Respond(false, pair.Key)), dictionary = catalog.Feedback(Material(false, false), Respond(false, pair.Key));
                Assert.That(aligned, Is.EqualTo("DEMO-feedback-" + pair.Value), pair.Key); Assert.That(dictionary, Is.EqualTo(aligned), pair.Key);
                Assert.That(catalog.FeedbackText(aligned), Is.EqualTo("Synthetic " + pair.Value + " feedback"));
            }
        }

        [Test, Category("REQ_LES_12"), NUnit.Framework.Property("Requirement", "REQ-LES-12")]
        [Ignore("Requirement gap: #276 atomic lesson feedback is one fixed text and ignores the submitted response (TeachingCatalog.Feedback)")]
        public void AtomicLessonFeedbackFollowsTheSubmittedResponseWithTheSameWordingAsMessageLessons()
        {
            var catalog = FeedbackCatalog();
            string correct = catalog.Feedback(Material(true, false), Respond(true, "correct"));
            string wrong = catalog.Feedback(Material(true, false), Respond(true, "wrong_action"));
            string missing = catalog.Feedback(Material(true, false), Respond(true, "timeout"));
            Assert.That(new[] { correct, wrong, missing }.Distinct().Count(), Is.EqualTo(3));
            Assert.That(correct, Is.EqualTo(catalog.Feedback(Material(false, true), Respond(false, "correct"))));
            Assert.That(wrong, Is.EqualTo(catalog.Feedback(Material(false, true), Respond(false, "wrong_action"))));
            Assert.That(missing, Is.EqualTo(catalog.Feedback(Material(false, true), Respond(false, "timeout"))));
        }

        // ---- Grammar familiarisation ----

        static byte[] Wave(int samples)
        {
            using var stream = new MemoryStream(); using var w = new BinaryWriter(stream);
            w.Write(Encoding.ASCII.GetBytes("RIFF")); w.Write(36 + samples * 2); w.Write(Encoding.ASCII.GetBytes("WAVEfmt ")); w.Write(16); w.Write((short)1); w.Write((short)1);
            w.Write(48000); w.Write(96000); w.Write((short)2); w.Write((short)16); w.Write(Encoding.ASCII.GetBytes("data")); w.Write(samples * 2);
            for (int i = 0; i < samples; i++) w.Write((short)((i * 37) % 2001 - 1000));
            return stream.ToArray();
        }
        static (string directory, JObject registry) GrammarFixture()
        {
            string directory = Path.Combine(Path.GetTempPath(), "av-grammar-trace-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(directory);
            var entries = new JArray();
            foreach (var (id, kind, samples) in new[] { ("ready-cue", "ready_cue", 15360), ("click-grammar-demo", "click", 13824) })
            {
                byte[] bytes = Wave(samples); File.WriteAllBytes(Path.Combine(directory, id + ".wav"), bytes); var parsed = PcmWave.ParseCanonical(bytes);
                entries.Add(new JObject { ["id"] = id, ["kind"] = kind, ["profile"] = null, ["recipe"] = null, ["n_samples"] = samples, ["file_sha256"] = parsed.FileSha256, ["pcm_sha256"] = parsed.PcmSha256 });
            }
            return (directory, new JObject { ["registry_version"] = 1, ["entries"] = entries });
        }
        static GrammarAssets LoadGrammar(string directory, JObject registry)
        { byte[] bytes = Encoding.UTF8.GetBytes(registry.ToString()); return GrammarAssets.Load(directory, bytes, PcmWave.Hash(bytes)); }

        [Test, Category("REQ_GRAM_04"), NUnit.Framework.Property("Requirement", "REQ-GRAM-04")]
        public void GrammarDemonstrationLoadsOnlyProfileFreeReservedAssetsThatCannotBeMotifsOrMessages()
        {
            var (directory, registry) = GrammarFixture();
            try
            {
                var assets = LoadGrammar(directory, registry);
                var motifLengths = new[] { 21600, 28800, 36000, 43200 };
                Assert.That(assets.Ready.SampleCount, Is.EqualTo(15360)); Assert.That(assets.Clicks.SampleCount, Is.EqualTo(13824));
                Assert.That(motifLengths.Contains(assets.Ready.SampleCount) || motifLengths.Contains(assets.Clicks.SampleCount), Is.False);
                // A complete message is at least two motifs plus the 200 ms gap.
                Assert.That(Math.Max(assets.Ready.SampleCount, assets.Clicks.SampleCount), Is.LessThan(2 * 21600 + 9600));
                Assert.That(assets.Preload("ready", "clicks").Keys, Is.EquivalentTo(new[] { "ready", "clicks" }));
                foreach (var (field, value) in new (string, JToken)[] { ("profile", "P1"), ("recipe", new JObject { ["motif"] = "DEMO" }), ("kind", "motif") })
                {
                    var changed = (JObject)registry.DeepClone(); changed["entries"][1][field] = value;
                    Assert.Throws<SessionFault>(() => LoadGrammar(directory, changed), field);
                }
                var renamed = (JObject)registry.DeepClone(); renamed["entries"][1]["id"] = "K-a1-r1";
                Assert.Throws<SessionFault>(() => LoadGrammar(directory, renamed));
            }
            finally { Directory.Delete(directory, true); }
        }

        [Test, Category("REQ_GRAM_01"), NUnit.Framework.Property("Requirement", "REQ-GRAM-01")]
        [Ignore("Requirement gap: #275 no spoken description of the message structure is delivered before the first lesson")]
        public void FamiliarizationPlaysTheSpokenStructureDescriptionBeforeTheReadyCueAndDemonstration()
        {
            var requests = new List<string>(); var flow = new GrammarFlow((kind, request, at) => { if (kind == "grammar_request") requests.Add(request); });
            var explanation = typeof(GrammarFlow).GetField("ExplanationId", Hidden);
            Assert.That(explanation, Is.Not.Null, "No spoken explanation request exists");
            double now = 0; flow.Tick(now);
            while (!flow.Complete && now < 120000) { flow.Completed(requests.Last(), now += 5000); flow.Tick(now += 10); }
            Assert.That(requests.Count, Is.EqualTo(3)); Assert.That(requests[0], Is.EqualTo((string)explanation.GetValue(flow)));
        }

        [Test, Category("REQ_GRAM_02"), NUnit.Framework.Property("Requirement", "REQ-GRAM-02")]
        [Ignore("Requirement gap: #275 the grammar registry pins no spoken explanation asset (GrammarAssets loads only the READY cue and click demonstration)")]
        public void SpokenExplanationIsOneHashPinnedAssetIndependentOfStudyRoleAndFormat()
        {
            var property = typeof(GrammarAssets).GetProperty("Explanation", Hidden);
            Assert.That(property, Is.Not.Null); Assert.That(property.PropertyType, Is.EqualTo(typeof(PcmWave)));
            // The loader takes no study, role or lesson-format input, so one pinned asset serves every visit.
            Assert.That(typeof(GrammarAssets).GetMethod("Load").GetParameters().Select(x => x.Name), Is.EqualTo(new[] { "directory", "registry", "expectedRegistrySha256" }));
        }

        [Test, Category("REQ_GRAM_03"), NUnit.Framework.Property("Requirement", "REQ-GRAM-03")]
        [Ignore("Requirement gap: #275 no explanation delivery (hash, onset, completion) is logged, so lessons cannot be gated on it")]
        public void ExplanationDeliveryIsLoggedWithHashOnsetAndCompletionBeforeLessonsMayStart()
        {
            var rows = new List<(string kind, string id)>(); var flow = new GrammarFlow((kind, request, at) => rows.Add((kind, request)));
            var explanation = typeof(GrammarFlow).GetField("ExplanationId", Hidden);
            Assert.That(explanation, Is.Not.Null, "No spoken explanation request exists");
            flow.Tick(0); string id = (string)explanation.GetValue(flow);
            Assert.That(rows.First(), Is.EqualTo(("grammar_request", id)));
        }

        // ---- Corpus lock ----

        sealed class Permit : IPostStudyDictionaryAuthorization, ISelectionMeaningAuthorization
        { public int Uses; public bool TryConsume(string package, string schedule, string atom) { Uses++; return true; } }

        static TeachingCatalog LockCatalog(string study, string visit)
        {
            var manifest = new JObject { ["study"] = study, ["package_id"] = "DEMO-package", ["package_sha256"] = Hash, ["demo"] = true, ["profile"] = study == "A" ? "P1" : null };
            var package = New<LoadedAudioPackage>(null, Hash, manifest, null, null, null, 0);
            var permutation = new JObject { ["atoms"] = new JArray(new JObject { ["atom_id"] = "K-a1" }) };
            var catalog = New<TeachingCatalog>(package, permutation, new JObject(), Hash, Hash, Hash, Hash, study == "B" ? "K" : null, visit);
            var row = new JObject { ["meaning_display_id"] = "DEMO-display-K-a1", ["definition"] = "Synthetic", ["action_words"] = "Synthetic", ["target_words"] = "", ["image_id"] = "fixture" };
            var displays = (IDictionary<string, TeachingDisplay>)typeof(TeachingCatalog).GetField("displays", Hidden).GetValue(catalog);
            displays.Add("K-a1", New<TeachingDisplay>(row, new byte[] { 1 }, Hash));
            ((HashSet<string>)typeof(TeachingCatalog).GetField("selectionAtoms", Hidden).GetValue(catalog)).Add("K-a1");
            return catalog;
        }

        [TestCase("A", "D7")][TestCase("B", "W1")]
        [Category("REQ_ACC_04"), NUnit.Framework.Property("Requirement", "REQ-ACC-04"), Category("REQ_VIS_03"), NUnit.Framework.Property("Requirement", "REQ-VIS-03")]
        public void NoDictionaryOrMenuMeaningIsReachableAtOrBetweenFollowUpVisits(string study, string visit)
        {
            var catalog = LockCatalog(study, visit); var permit = new Permit();
            Assert.Throws<SessionFault>(() => catalog.ReadPostStudyDictionaryAtom("K-a1", permit));
            Assert.Throws<SessionFault>(() => catalog.ReadSelectionAtom("K-a1", permit));
            Assert.That(permit.Uses, Is.Zero, "Refusal happens before any authorization is consumed");
        }

        [Test, Category("REQ_ACC_04"), NUnit.Framework.Property("Requirement", "REQ-ACC-04")]
        public void DictionaryOpensOnlyAtTheFinalStudyBVisitThroughAConsumedPermit()
        {
            var final = LockCatalog("B", "W4"); var permit = new Permit();
            Assert.That(final.ReadPostStudyDictionaryAtom("K-a1", permit).MeaningDisplayId, Is.EqualTo("DEMO-display-K-a1")); Assert.That(permit.Uses, Is.EqualTo(1));
            Assert.Throws<SessionFault>(() => final.ReadSelectionAtom("K-a1", permit));
            Assert.Throws<SessionFault>(() => final.ReadPostStudyDictionaryAtom("K-a1", null));
            var acquisition = LockCatalog("B", "V2");
            Assert.Throws<SessionFault>(() => acquisition.ReadPostStudyDictionaryAtom("K-a1", new Permit()));
            Assert.That(acquisition.ReadSelectionAtom("K-a1", new Permit()).MeaningDisplayId, Is.EqualTo("DEMO-display-K-a1"));
        }
    }
}
