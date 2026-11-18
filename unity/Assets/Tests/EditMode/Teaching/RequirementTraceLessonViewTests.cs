using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using AcousticVocab.Foundation;
using AcousticVocab.ResponsePanel;
using AcousticVocab.SessionEngine;
using AcousticVocab.StateIntegration;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace AcousticVocab.Teaching.Tests
{
    // Requirement trace for the rendered lesson view (#277). The real content
    // wiring (TeachingContentFactory content) drives the real TeachingSessionHost
    // canvas. Only audio scheduling is replaced by a recorder; the timeline clock,
    // onsets and responses are synthetic. Silence, DEMO identifiers only.
    public sealed class RequirementTraceLessonViewTests
    {
        const string Hash = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        const BindingFlags Hidden = BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public;
        const string Png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a7V8AAAAASUVORK5CYII=";
        static T New<T>(params object[] args) => (T)Activator.CreateInstance(typeof(T), Hidden, null, args, null);
        static object Get(object owner, string field) => owner.GetType().GetField(field, Hidden).GetValue(owner);
        static object Property(object owner, string name) => owner.GetType().GetProperty(name).GetValue(owner);

        sealed class Backend : ITeachingBackend
        {
            int resets;
            public void Pump() { }
            public void RequestTeachingMode() { }
            public bool TeachingModeAcknowledged => true;
            public bool NeutralHoldHealthy => true;
            public string RequestReset() => "DEMO-reset-" + ++resets;
            public bool ResetAcknowledged(string id) => true;
            public void Interrupt() { }
        }

        // One visible-state sample of the participant canvas.
        readonly struct View : IEquatable<View>
        {
            public readonly bool Visible, ImageVisible; public readonly string Definition, Action, Target, Feedback, Highlight;
            public View(bool visible, bool image, string definition, string action, string target, string feedback, string highlight)
            { Visible = visible; ImageVisible = image; Definition = definition; Action = action; Target = target; Feedback = feedback; Highlight = highlight; }
            public View WithoutHighlight() => new View(Visible, ImageVisible, Definition, Action, Target, Feedback, "-");
            public bool Equals(View o) => Visible == o.Visible && ImageVisible == o.ImageVisible && Definition == o.Definition && Action == o.Action && Target == o.Target && Feedback == o.Feedback && Highlight == o.Highlight;
            public override bool Equals(object o) => o is View v && Equals(v);
            public override int GetHashCode() => (Definition ?? "").GetHashCode();
            public override string ToString() => $"{Visible}/{ImageVisible}/{Definition}/{Action}/{Target}/{Feedback}/{Highlight}";
        }

        sealed class Rig : IDisposable
        {
            readonly List<GameObject> objects = new List<GameObject>();
            internal readonly TeachingSessionHost Host;
            internal readonly TeachingContentFactory Factory;
            internal readonly AudioPlayer Player;
            internal readonly TeachingCatalog Catalog;
            internal readonly List<LessonEvent> Events = new List<LessonEvent>();
            internal readonly List<string> Responses = new List<string>(), Faults = new List<string>();
            internal Rig()
            {
                var holder = new GameObject("Inactive synthetic dependencies"); holder.SetActive(false); objects.Add(holder);
                var foundation = holder.AddComponent<FoundationBootstrap>();
                var config = new JObject { ["observer_reference"] = new JObject { ["position_m"] = new JArray(0, 1.5, 1.45), ["rotation_xyzw"] = new JArray(0, 1, 0, 0) } };
                typeof(FoundationBootstrap).GetField("configuration", Hidden).SetValue(foundation, config);
                var presentation = new GameObject("Synthetic presentation root"); objects.Add(presentation); foundation.presentationRoot = presentation;
                var panel = holder.AddComponent<ResponsePanelController>(); var source = holder.AddComponent<StateSourceHost>();
                var hostObject = new GameObject("Synthetic teaching host"); objects.Add(hostObject);
                Host = hostObject.AddComponent<TeachingSessionHost>(); Host.foundation = foundation; Host.presentationParent = presentation.transform;
                Host.font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf"); Host.panel = panel; Host.source = source;
                var audioObject = new GameObject("Synthetic silent audio", typeof(AudioSource)); objects.Add(audioObject);
                Player = audioObject.AddComponent<AudioPlayer>(); typeof(AudioPlayer).GetMethod("Awake", Hidden).Invoke(Player, null); Host.player = Player;
                typeof(TeachingSessionHost).GetMethod("CreateView", Hidden).Invoke(Host, null);
                var feedback = new JObject();
                foreach (string kind in new[] { "atomic", "correct", "incorrect", "timeout" })
                    feedback[kind] = new JObject { ["id"] = "DEMO-feedback-" + kind, ["text"] = "Synthetic " + kind + " feedback" };
                Catalog = New<TeachingCatalog>(null, new JObject(), new JObject { ["feedback"] = feedback }, Hash, Hash, Hash, Hash, null, "D0");
                Factory = new TeachingContentFactory(Catalog, null, new Backend(), Player, panel, source, Host, () => true, Events.Add, _ => { }, Responses.Add, Faults.Add);
                Player.Configure(AudioRouteCalibration.Unmeasured("ENGINEERING_UNMEASURED"), () => false);
                typeof(TeachingSessionHost).GetField("factory", Hidden).SetValue(Host, Factory);
                Assert.That(Host.Installed, Is.True);
            }
            string plainAction, plainTarget;
            string Look(string field)
            {
                var words = (Component)Get(Host, field);
                var parts = new List<string> { ((Color)Property(words, "color")).ToString() };
                var backing = ((Canvas)Get(Host, "canvas")).transform.Find(words.gameObject.name + " backing");
                if (backing != null)
                    foreach (var part in backing.GetComponents<Component>())
                        foreach (string name in new[] { "color", "effectColor", "enabled" })
                        {
                            var value = part.GetType().GetProperty(name)?.GetValue(part);
                            if (value != null) parts.Add(name + "=" + value);
                        }
                return string.Join("|", parts);
            }
            internal View Sample()
            {
                var canvas = (Canvas)Get(Host, "canvas");
                string Text(string field) => (string)Property(Get(Host, field), "text");
                // Style-agnostic emphasis: a label is highlighted when its look (text colour plus
                // any backing colour/edge) differs from its plain look captured before the lesson.
                string action = Look("actionWords"), target = Look("targetWords");
                if (plainAction == null) { plainAction = action; plainTarget = target; }
                string highlight = action != plainAction ? "action" : target != plainTarget ? "target" : "none";
                bool visible = canvas.gameObject.activeSelf;
                var image = (Component)Get(Host, "image");
                return visible ? new View(true, image.gameObject.activeSelf, Text("definition"), Text("actionWords"), Text("targetWords"), Text("feedback"), highlight)
                    : new View(false, false, null, null, null, null, "none");
            }
            internal Component[] Interactive()
            {
                var canvas = (Canvas)Get(Host, "canvas"); var selectable = Type.GetType("UnityEngine.UI.Selectable, UnityEngine.UI");
                Assert.That(selectable, Is.Not.Null);
                return canvas.GetComponentsInChildren(selectable, true).Concat(canvas.GetComponentsInChildren<Collider>(true)).ToArray();
            }
            internal bool AnyRaycastTarget()
            {
                var canvas = (Canvas)Get(Host, "canvas"); var graphic = Type.GetType("UnityEngine.UI.Graphic, UnityEngine.UI");
                return canvas.GetComponentsInChildren(graphic, true).Any(x => (bool)Property(x, "raycastTarget"));
            }
            public void Dispose()
            {
                try { Factory.Dispose(); } catch { }
                // Edit mode forbids Destroy; release every object explicitly.
                AudioSettings.OnAudioConfigurationChanged -= (AudioSettings.AudioConfigurationChangeHandler)Delegate.CreateDelegate(typeof(AudioSettings.AudioConfigurationChangeHandler), Player, typeof(AudioPlayer).GetMethod("OnAudioConfigurationChanged", Hidden));
                foreach (var prepared in ((IDictionary)Get(Player, "prepared")).Values) { var clip = (UnityEngine.Object)Get(prepared, "Clip"); if (clip != null) UnityEngine.Object.DestroyImmediate(clip); }
                foreach (var texture in ((IDictionary)Get(Host, "textures")).Values) if (texture is UnityEngine.Object o && o != null) UnityEngine.Object.DestroyImmediate(o);
                typeof(TeachingSessionHost).GetField("factory", Hidden).SetValue(Host, null);
                typeof(TeachingSessionHost).GetField("canvas", Hidden).SetValue(Host, null);
                foreach (var item in objects) if (item != null) UnityEngine.Object.DestroyImmediate(item);
            }
        }

        sealed class Result
        {
            internal readonly List<(double at, View view)> Samples = new List<(double, View)>();
            internal readonly List<double> Plays = new List<double>();
            internal List<LessonEvent> Events; internal List<string> Responses, Faults;
            internal TeachingDisplay Display; internal Component[] Interactive; internal bool RaycastTarget; internal bool EndedAtSlotEnd;
            internal View At(double elapsed) => Samples.Single(x => x.at == elapsed).view;
        }

        // kind: correct, wrong, timeout
        static Result Lesson(bool atomic, bool aligned, string kind)
        {
            using var rig = new Rig();
            var row = new JObject { ["meaning_display_id"] = "DEMO-display", ["definition"] = "Synthetic meaning", ["action_words"] = "Synthetic action", ["target_words"] = atomic ? "" : "Synthetic target", ["image_id"] = "fixture" };
            byte[] png = Convert.FromBase64String(Png);
            var material = new LessonMaterial
            {
                Display = New<TeachingDisplay>(row, png, PcmWave.Hash(png)), Atomic = atomic, Aligned = aligned, ExpectedAction = "FLIP_CARD", ExpectedTarget = atomic ? null : "B",
                Action = New<PcmWave>(new byte[24000 * 2], null, null, null), Referent = atomic ? null : New<PcmWave>(new byte[33600 * 2], null, null, null)
            };
            material.Wave = atomic ? material.Action : New<PcmWave>(new byte[(24000 + 9600 + 33600) * 2], null, null, null);
            var item = new SlotItem("DEMO-view", atomic ? "atomic_lesson" : "message_lesson", atomic ? "K-a1" : "K-a1-r1", atomic ? "action" : null,
                atomic ? null : aligned ? "structured" : "dictionary", "teaching", false, atomic ? 20 : 24, 3, 1);
            var context = new SlotContext(item, 750, null);
            var contentType = typeof(TeachingContentFactory).GetNestedType("Content", BindingFlags.NonPublic);
            var content = (ISlotContent)Activator.CreateInstance(contentType, Hidden, null, new object[] { rig.Factory, material }, null);
            ((IList)Get(rig.Factory, "live")).Add(content);
            // PNG decoding is refused on the headless batch test host; seed the
            // host's texture cache for this image so the real Show path still selects it.
            ((IDictionary)Get(rig.Host, "textures"))[material.Display.ImageSha256] = new Texture2D(1, 1);
            content.Prepare(context);
            var timeline = (LessonTimeline)Get(content, "Timeline");
            var result = new Result { Display = material.Display };
            // Audio scheduling is outside the view under test: record requested onsets instead.
            typeof(LessonTimeline).GetField("PlayRequested", Hidden).SetValue(timeline, null);
            timeline.PlayRequested += (index, id, at) => result.Plays.Add(at - 750);
            double[] offsets = atomic ? new[] { 0d, 6000, 14000 } : new[] { 0d, 8000, 18000 };
            double duration = atomic ? 500 : 1400, end = item.SlotSeconds * 1000, now = 0;
            var request = new PanelRequest(item.TrialId, atomic ? PanelMode.LessonAtomic : PanelMode.LessonMessage, atomic ? PanelRole.Action : PanelRole.Command, 750);
            var state = new ResponseState(() => now, _ => { });
            var respond = typeof(TeachingContentFactory).GetMethod("PanelResponse", Hidden);
            state.Responded += value => respond.Invoke(rig.Factory, new object[] { value });
            timeline.Start(now);
            for (now = 5; now <= 750 + end; now += 5)
            {
                timeline.Tick(now);
                for (int i = 0; i < 3; i++)
                {
                    if (now == 750 + offsets[i]) timeline.Onset(context.AudioRequestIds[i], now, 10, now);
                    if (now == 750 + offsets[i] + duration) timeline.Completed(context.AudioRequestIds[i], now);
                }
                if (now == request.OpensMonoMs + 100)
                {
                    state.Open(request);
                    if (kind != "timeout")
                    {
                        if (!atomic) state.SelectTarget(kind == "wrong" ? "C" : "B");
                        state.SelectAction(kind == "wrong" && atomic ? "ADD_ONE" : "FLIP_CARD"); Assert.That(state.Commit(), Is.True);
                    }
                }
                if (now == request.DeadlineMonoMs && kind == "timeout") state.Tick();
                if (now == request.DeadlineMonoMs) content.RequestReset(context);
                result.Samples.Add((now - 750, rig.Sample()));
            }
            result.EndedAtSlotEnd = timeline.Ended && timeline.AllPlaysComplete;
            result.Interactive = rig.Interactive(); result.RaycastTarget = rig.AnyRaycastTarget();
            result.Events = rig.Events.ToList(); result.Responses = rig.Responses.ToList(); result.Faults = rig.Faults.ToList();
            return result;
        }

        static IEnumerable<(double at, View view)> Between(Result r, double from, double to) => r.Samples.Where(x => x.at >= from && x.at < to);

        static void AssertPhases(Result r, bool atomic, string feedbackText)
        {
            double opens = atomic ? 6000 : 8000, corrective = atomic ? 14000 : 18000, end = atomic ? 20000 : 24000;
            var meaning = new View(true, true, r.Display.Definition, r.Display.ActionWords, r.Display.TargetWords, "", "-");
            Assert.That(Between(r, -1000, 0).All(x => !x.view.Visible), Is.True, "Nothing is shown before onset");
            Assert.That(Between(r, 0, opens).All(x => x.view.WithoutHighlight().Equals(meaning)), Is.True, "Guided playback shows the whole meaning");
            Assert.That(Between(r, opens, corrective).All(x => !x.view.Visible), Is.True, "Retrieval shows no answer");
            var corrected = new View(true, true, r.Display.Definition, r.Display.ActionWords, r.Display.TargetWords, feedbackText, "-");
            Assert.That(Between(r, corrective, end).All(x => x.view.WithoutHighlight().Equals(corrected)), Is.True, "Corrective playback shows meaning and feedback");
            Assert.That(r.At(end).Visible, Is.False);
            Assert.That(r.Plays, Is.EqualTo(atomic ? new[] { 0d, 6000, 14000 } : new[] { 0d, 8000, 18000 }));
            Assert.That(r.EndedAtSlotEnd, Is.True); Assert.That(r.Faults, Is.Empty); Assert.That(r.Responses.Count, Is.EqualTo(1));
            Assert.That(r.Interactive, Is.Empty, "No replay or other control on the lesson view"); Assert.That(r.RaycastTarget, Is.False);
        }

        [Test, Category("REQ_LES_06"), NUnit.Framework.Property("Requirement", "REQ-LES-06"), Category("REQ_LES_07"), NUnit.Framework.Property("Requirement", "REQ-LES-07"), Category("REQ_LES_08"), NUnit.Framework.Property("Requirement", "REQ-LES-08"), Category("REQ_LES_09"), NUnit.Framework.Property("Requirement", "REQ-LES-09"), Category("REQ_LES_10"), NUnit.Framework.Property("Requirement", "REQ-LES-10"), Category("REQ_LES_14"), NUnit.Framework.Property("Requirement", "REQ-LES-14")]
        public void RenderedMessageFormatsShowTheSameMeaningHideTheAnswerInRetrievalAndDifferOnlyByHighlight()
        {
            var aligned = Lesson(false, true, "correct"); var dictionary = Lesson(false, false, "correct");
            AssertPhases(aligned, false, "Synthetic correct feedback"); AssertPhases(dictionary, false, "Synthetic correct feedback");
            Assert.That(aligned.Samples.Select(x => (x.at, x.view.WithoutHighlight())), Is.EqualTo(dictionary.Samples.Select(x => (x.at, x.view.WithoutHighlight()))));
            Assert.That(dictionary.Samples.All(x => x.view.Highlight == "none"), Is.True, "Static lesson never changes emphasis");
            foreach (double play in new[] { 0d, 18000 })
            {
                Assert.That(Between(aligned, play, play + 500).All(x => x.view.Highlight == "action"), Is.True, "Action label during action motif");
                Assert.That(Between(aligned, play + 500, play + 700).All(x => x.view.Highlight == "none"), Is.True, "No label during the gap");
                Assert.That(Between(aligned, play + 700, play + 1400).All(x => x.view.Highlight == "target"), Is.True, "Target label during target motif");
            }
            Assert.That(aligned.Samples.Where(x => x.view.Highlight != "none").All(x => x.at < 1400 || x.at >= 18000 && x.at < 19400), Is.True);
            Assert.That(aligned.Events.Count(x => x.Kind == "retrieval_opportunity"), Is.EqualTo(1)); Assert.That(dictionary.Events.Count(x => x.Kind == "retrieval_opportunity"), Is.EqualTo(1));
            Assert.That(aligned.Events.Single(x => x.Kind == "retrieval_result").FeedbackContentId, Is.EqualTo(dictionary.Events.Single(x => x.Kind == "retrieval_result").FeedbackContentId));
        }

        [TestCase("wrong", "Synthetic incorrect feedback")]
        [TestCase("timeout", "Synthetic timeout feedback")]
        [Category("REQ_LES_10"), NUnit.Framework.Property("Requirement", "REQ-LES-10"), Category("REQ_LES_11"), NUnit.Framework.Property("Requirement", "REQ-LES-11")]
        public void RenderedMessageLessonWithWrongOrMissingResponseKeepsTheSameLessonInBothFormats(string kind, string feedback)
        {
            var aligned = Lesson(false, true, kind); var dictionary = Lesson(false, false, kind);
            AssertPhases(aligned, false, feedback); AssertPhases(dictionary, false, feedback);
            Assert.That(aligned.Samples.Select(x => (x.at, x.view.WithoutHighlight())), Is.EqualTo(dictionary.Samples.Select(x => (x.at, x.view.WithoutHighlight()))));
        }

        [Test, Category("REQ_LES_05"), NUnit.Framework.Property("Requirement", "REQ-LES-05"), Category("REQ_LES_09"), NUnit.Framework.Property("Requirement", "REQ-LES-09")]
        public void RenderedAtomicLessonIsIdenticalForBothFamilyFormatsAndHidesTheAnswerInRetrieval()
        {
            var aligned = Lesson(true, true, "correct"); var dictionary = Lesson(true, false, "correct");
            // The fixture catalogue has a fixed atomic text on main; a response-based catalogue
            // (#276) shows the correct-outcome text. Either way both formats must match.
            string shown = aligned.At(16000).Feedback;
            Assert.That(shown, Is.AnyOf("Synthetic atomic feedback", "Synthetic correct feedback"));
            AssertPhases(aligned, true, shown); AssertPhases(dictionary, true, shown);
            Assert.That(aligned.Samples, Is.EqualTo(dictionary.Samples));
            Assert.That(aligned.Samples.All(x => x.view.Highlight == "none"), Is.True);
        }
    }
}
