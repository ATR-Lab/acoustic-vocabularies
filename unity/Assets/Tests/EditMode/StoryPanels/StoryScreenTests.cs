using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using AcousticVocab.Foundation;
using AcousticVocab.Orientation;
using AcousticVocab.ResponsePanel;
using AcousticVocab.Teaching;
using AcousticVocab.Theme;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.UI;

namespace AcousticVocab.StoryPanels.Tests
{
    public sealed class StoryScreenTests
    {
        const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
        static readonly string[] ExistingLabels = { "Definition", "Action words", "Target words", "Feedback" };
        static readonly string[] StepTexts = { "Listen", "Your turn", "Check" };
        static readonly string[] Questions = { "Which action was that?", "Which target was that?", "Which target and action was that?" };

        // The teaching view as the session builds it, on a synthetic foundation.
        sealed class LessonView : IDisposable
        {
            readonly GameObject holder, lease;
            public readonly GameObject Presentation;
            public readonly TeachingSessionHost Host;
            public readonly Canvas Canvas, Steps;
            public LessonView()
            {
                holder = new GameObject("Inactive synthetic foundation"); holder.SetActive(false);
                Presentation = new GameObject("Synthetic presentation"); lease = new GameObject("Synthetic lease");
                var foundation = holder.AddComponent<FoundationBootstrap>();
                var config = new JObject { ["observer_reference"] = new JObject { ["position_m"] = new JArray(0, 1.2, 0), ["rotation_xyzw"] = new JArray(0, 0, 0, 1) } };
                typeof(FoundationBootstrap).GetField("configuration", Private).SetValue(foundation, config);
                foundation.presentationRoot = Presentation;
                Host = lease.AddComponent<TeachingSessionHost>(); Host.foundation = foundation; Host.presentationParent = Presentation.transform;
                Host.font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
                typeof(TeachingSessionHost).GetMethod("CreateView", Private).Invoke(Host, null);
                var canvases = Presentation.GetComponentsInChildren<Canvas>(true);
                Canvas = canvases.Single(x => x.name == "Teaching presentation"); Steps = canvases.Single(x => x.name == "Lesson steps");
            }
            public Text Label(string name) => Canvas.transform.Find(name).GetComponent<Text>();
            public StoryWaveform Strip(string name) => Canvas.transform.Find(name).GetComponent<StoryWaveform>();
            public StoryStepChips Chips => Steps.GetComponentInChildren<StoryStepChips>(true);
            public Text Question => Steps.transform.Find("Question").GetComponent<Text>();
            // Mirrors Show(): the real Show needs an installed lesson factory.
            public void ShowLesson(string owner, string definition, string action, string target)
            {
                Label("Definition").text = definition; Label("Action words").text = action; Label("Target words").text = target; Label("Feedback").text = "";
                Canvas.transform.Find("Task image").gameObject.SetActive(true);
                typeof(TeachingSessionHost).GetMethod("SetLessonStrips", Private).Invoke(Host, new object[] { !string.IsNullOrEmpty(action), !string.IsNullOrEmpty(target) });
                Canvas.transform.Find("Grammar timeline").gameObject.SetActive(false);
                typeof(TeachingSessionHost).GetField("visibleOwner", Private).SetValue(Host, owner);
                Canvas.gameObject.SetActive(true);
            }
            public void Grammar(string phase) => typeof(TeachingSessionHost).GetMethod("ShowGrammar", Private).Invoke(Host, new object[] { phase });
            public void Dispose() { UnityEngine.Object.DestroyImmediate(Presentation); UnityEngine.Object.DestroyImmediate(lease); UnityEngine.Object.DestroyImmediate(holder); }
        }

        static Rect Bounds(RectTransform rect) => StoryComponentTests.Bounds(rect);
        static string Geometry(Transform root, IEnumerable<string> names) => string.Join(";", names.Select(n =>
        { var r = (RectTransform)root.Find(n); var t = r.GetComponent<Text>(); return n + ":" + r.anchoredPosition + ":" + r.sizeDelta + ":" + r.localScale + ":" + (t != null ? t.fontSize.ToString() : ""); }));

        [Test, Category("REQ_APP_07")]
        public void LessonCardKeepsExistingLabelGeometryAndPlacesDecorClearOfIt()
        {
            using var view = new LessonView();
            // The reviewed label layout (ADR-004 legibility): unchanged positions, sizes and 27-unit type.
            Assert.That(Geometry(view.Canvas.transform, ExistingLabels.Concat(new[] { "Task image" })), Is.EqualTo(
                "Definition:(0.00, 220.00):(780.00, 100.00):(1.00, 1.00, 1.00):27;Action words:(-190.00, 140.00):(370.00, 70.00):(1.00, 1.00, 1.00):27;" +
                "Target words:(190.00, 140.00):(370.00, 70.00):(1.00, 1.00, 1.00):27;Feedback:(0.00, -220.00):(780.00, 80.00):(1.00, 1.00, 1.00):27;Task image:(0.00, -25.00):(460.00, 270.00):(1.00, 1.00, 1.00):"));
            Assert.That(view.Canvas.transform.localScale, Is.EqualTo(Vector3.one * .001f));
            var card = view.Canvas.GetComponentInChildren<StoryPanel>(true);
            Assert.That(card.transform.GetSiblingIndex(), Is.EqualTo(0), "Glass card renders behind every backing and label");
            var labels = ExistingLabels.Select(n => Bounds((RectTransform)view.Canvas.transform.Find(n))).ToArray();
            var image = Bounds((RectTransform)view.Canvas.transform.Find("Task image"));
            foreach (var decor in new[] { "Action motif strip", "Target motif strip", "Motif speaker" })
            {
                var r = Bounds((RectTransform)view.Canvas.transform.Find(decor));
                Assert.That(labels.Any(l => l.Overlaps(r)) || image.Overlaps(r), Is.False, decor + " must not overlap an existing label or the image");
            }
            // The steps canvas sits above the definition, in front of the card.
            var stepsRow = Bounds((RectTransform)view.Chips.transform);
            Assert.That(stepsRow.yMin, Is.GreaterThan(Bounds((RectTransform)view.Canvas.transform.Find("Definition")).yMax));
            Assert.That(view.Steps.transform.position, Is.Not.EqualTo(view.Canvas.transform.position));
            Assert.That(view.Steps.sortingOrder, Is.GreaterThan(view.Canvas.sortingOrder));
            Assert.That(view.Canvas.gameObject.activeSelf || view.Steps.gameObject.activeSelf, Is.False, "Construction reveals nothing");
            foreach (var root in new[] { view.Canvas, view.Steps })
                Assert.That(root.GetComponentsInChildren<Graphic>(true).Any(x => x.raycastTarget), Is.False, "Decor never intercepts input");
        }

        [Test, Category("REQ_LES_02"), Category("REQ_LES_09"), Category("REQ_LES_12")]
        public void StepChipsFollowTheLessonAndTheTryNeverShowsTheAnswer()
        {
            using var view = new LessonView();
            view.ShowLesson("T1", "Close container G", "close", "container G");
            view.Host.Step("T1", LessonDisplayPhase.Definition, LessonQuestion.Action);
            Assert.That(view.Steps.gameObject.activeSelf, Is.True);
            Assert.That(view.Chips.Current, Is.EqualTo(0));
            Assert.That(view.Chips.Labels.Select(x => x.text), Is.EqualTo(StepTexts));
            Assert.That(view.Question.gameObject.activeSelf, Is.False);

            // Retrieval: the factory hides the meaning, then lights "Your turn".
            view.Host.Hide("T1");
            view.Host.Step("T1", LessonDisplayPhase.Retrieval, LessonQuestion.Action);
            Assert.That(view.Canvas.gameObject.activeSelf, Is.False, "Meaning canvas stays hidden during the try");
            Assert.That(view.Chips.Current, Is.EqualTo(1));
            Assert.That(view.Question.gameObject.activeSelf, Is.True);
            Assert.That(view.Question.text, Is.EqualTo("Which action was that?"));
            StoryComponentTests.AssertTextOnOpaqueAaaSurface(view.Question, view.Steps.transform.Find("Question surface").GetComponent<Image>());
            var visible = view.Steps.GetComponentsInChildren<Text>(false).Select(x => x.text).ToArray();
            Assert.That(visible.All(t => StepTexts.Contains(t) || Questions.Contains(t)), Is.True, "Only fixed step and prompt wording is visible: " + string.Join("|", visible));
            foreach (var answer in new[] { "close", "container G", "Close container G" })
                Assert.That(visible.Any(t => t.IndexOf(answer, StringComparison.OrdinalIgnoreCase) >= 0 && !Questions.Contains(t) && !StepTexts.Contains(t)), Is.False);

            view.Host.Step("T1", LessonDisplayPhase.Retrieval, LessonQuestion.Target);
            Assert.That(view.Question.text, Is.EqualTo("Which target was that?"));
            view.Host.Step("T1", LessonDisplayPhase.Retrieval, LessonQuestion.Message);
            Assert.That(view.Question.text, Is.EqualTo("Which target and action was that?"));

            view.ShowLesson("T1", "Close container G", "close", "container G");
            view.Host.Step("T1", LessonDisplayPhase.Feedback, LessonQuestion.Message);
            Assert.That(view.Chips.Current, Is.EqualTo(2));
            Assert.That(view.Chips.Checks[0].enabled && view.Chips.Checks[1].enabled, Is.True);
            Assert.That(view.Question.gameObject.activeSelf, Is.False);
            // A different owner cannot drive this lesson's chips.
            view.Host.Step("T2", LessonDisplayPhase.Ended, LessonQuestion.Message);
            Assert.That(view.Steps.gameObject.activeSelf, Is.True);
            view.Host.Step("T1", LessonDisplayPhase.Ended, LessonQuestion.Message);
            Assert.That(view.Steps.gameObject.activeSelf, Is.False);
            for (int i = 0; i < 3; i++) StoryComponentTests.AssertTextOnOpaqueAaaSurface(view.Chips.Labels[i], view.Chips.Surfaces[i]);
        }

        [Test, Category("REQ_LES_07"), Category("REQ_LES_10")]
        public void PlayheadFollowsOnlyTheLitSegmentAndLeavesLabelGeometryAlone()
        {
            using var view = new LessonView();
            view.ShowLesson("T1", "Close container G", "close", "container G");
            var action = view.Strip("Action motif strip"); var target = view.Strip("Target motif strip");
            var before = Geometry(view.Canvas.transform, ExistingLabels);
            Assert.That(action.PlayheadVisible || target.PlayheadVisible, Is.False);
            view.Host.Playhead("T1", LessonHighlight.Action, .4f);
            Assert.That(action.PlayheadVisible, Is.True); Assert.That(action.Fraction, Is.EqualTo(.4f));
            Assert.That(target.PlayheadVisible, Is.False);
            view.Host.Playhead("T1", LessonHighlight.Target, .9f);
            Assert.That(action.PlayheadVisible, Is.False); Assert.That(target.Fraction, Is.EqualTo(.9f));
            view.Host.Playhead("other", LessonHighlight.Action, .1f);
            Assert.That(target.Fraction, Is.EqualTo(.9f), "Another owner cannot move the playhead");
            view.Host.Playhead("T1", LessonHighlight.None, null);
            Assert.That(action.PlayheadVisible || target.PlayheadVisible, Is.False);
            Assert.That(Geometry(view.Canvas.transform, ExistingLabels), Is.EqualTo(before));
        }

        [Test, Category("REQ_LES_08"), Category("REQ_LES_10")]
        public void StaticAndAtomicLessonsAreIdenticalCardsWithNoPlayhead()
        {
            using var view = new LessonView();
            view.ShowLesson("T1", "Close container G", "close", "container G");
            // The view has no format input: aligned and static share one structure.
            Assert.That(view.Strip("Action motif strip").gameObject.activeSelf && view.Strip("Target motif strip").gameObject.activeSelf, Is.True);
            // An atomic lesson shows the strip only under the label it teaches.
            view.ShowLesson("T2", "Close", "close", "");
            Assert.That(view.Strip("Action motif strip").gameObject.activeSelf, Is.True);
            Assert.That(view.Strip("Target motif strip").gameObject.activeSelf, Is.False);
            // LessonPlayheadTests (Teaching.Tests) shows their timelines never yield a playhead fraction.
        }

        [Test, Category("REQ_GRAM_01"), Category("REQ_GRAM_04")]
        public void GrammarRowShowsActionPauseTargetOnlyWithTheRoleLabels()
        {
            using var view = new LessonView();
            var row = view.Canvas.transform.Find("Grammar timeline").gameObject;
            view.Grammar("ready");
            Assert.That(row.activeSelf, Is.False);
            view.Grammar("roles");
            Assert.That(row.activeSelf, Is.True);
            Assert.That(view.Canvas.transform.Find("Task image").gameObject.activeSelf, Is.False, "The row uses the image area only while the image is hidden");
            Assert.That(view.Strip("Action motif strip").gameObject.activeSelf || view.Strip("Target motif strip").gameObject.activeSelf, Is.False);
            Assert.That(view.Steps.gameObject.activeSelf, Is.False, "Grammar has no lesson steps");
            var parts = row.GetComponentsInChildren<StoryWaveform>(true);
            Assert.That(parts.Select(x => x.name), Is.EqualTo(new[] { "Action part", "Target part" }));
            Assert.That(parts.All(p => !p.PlayheadVisible && p.Bars.All(b => b.color == StudyTheme.WaveLit)), Is.True, "Decorative glyphs, not a playback display");
            Assert.That(row.GetComponentsInChildren<AudioSource>(true), Is.Empty);
            var pause = row.transform.Find("Pause").GetComponent<Text>();
            Assert.That(pause.text, Is.EqualTo("pause"));
            StoryComponentTests.AssertTextOnOpaqueAaaSurface(pause, row.transform.Find("Pause surface").GetComponent<Image>());
            var r = Bounds((RectTransform)row.transform);
            foreach (var n in ExistingLabels) Assert.That(Bounds((RectTransform)view.Canvas.transform.Find(n)).Overlaps(r), Is.False, n);
            view.Grammar("hidden");
            Assert.That(view.Canvas.gameObject.activeSelf, Is.False);
        }

        static OrientationPlan Plan()
        {
            var v = JObject.Parse(File.ReadAllText(Path.Combine(AcousticVocab.Foundation.Editor.FoundationBuild.RepositoryRoot, "apparatus/orientation/orientation-plan.example.json")));
            v["content_status"] = "protocol_reviewed"; v["review_evidence_sha256"] = new string('a', 64);
            return OrientationPlan.Parse(v.ToString(), "engineering-pending-review");
        }

        [Test, Category("REQ_ORI_01"), Category("REQ_ORI_02"), Category("REQ_ORI_03")]
        public void OrientationStateShowsIconProgressAndFeedbackOnlyFromTheFlow()
        {
            var plan = Plan(); double now = 0;
            var flow = new OrientationFlow(plan, () => now, _ => { });
            Assert.That(OrientationStoryState.From(flow, plan).Progress, Is.Null, "Nothing before the start");
            flow.Start();
            var s = OrientationStoryState.From(flow, plan);
            Assert.That(s.Icon, Is.EqualTo(StoryIcons.ForAction(plan.Actions[0].Id))); Assert.That(s.Progress, Is.EqualTo(0f)); Assert.That(s.ShowCorrect, Is.False);
            now += 10000; flow.CompleteDemo(flow.CurrentCard.Id, 10000, 10000, 1000d / 30);
            Assert.That(OrientationStoryState.From(flow, plan).Progress, Is.EqualTo(1 / 8f), "The bar fills once the demonstration completes");
            for (int i = 0; i < 8; i++) { if (i > 0) { now += 10000; flow.CompleteDemo(flow.CurrentCard.Id, 10000, 10000, 1000d / 30); } flow.Next(); }
            s = OrientationStoryState.From(flow, plan);
            Assert.That(flow.Stage, Is.EqualTo(OrientationStage.Target));
            Assert.That(s.Icon, Is.EqualTo(StoryIcons.ForTarget(plan.Targets[0].Id))); Assert.That(s.Progress, Is.EqualTo(1 / 8f));
            for (int i = 0; i < 8; i++) flow.Next();
            Assert.That(flow.Stage, Is.EqualTo(OrientationStage.Practice));
            s = OrientationStoryState.From(flow, plan);
            Assert.That(s.Icon, Is.EqualTo(StoryIcon.None), "Practice shows the written request only"); Assert.That(s.ShowCorrect, Is.False);
            foreach (bool right in new[] { true, false })
            {
                var item = flow.CurrentItem; var panel = new ResponseState(() => now, _ => { }); panel.Responded += flow.Respond; panel.Open(flow.OpenPractice());
                string target = right ? item.Target : PublicCommands.Targets[(PublicCommands.Index(PublicCommands.Targets, item.Target) / 4) * 4 + (PublicCommands.Index(PublicCommands.Targets, item.Target) + 1) % 4];
                panel.SelectTarget(target); panel.SelectAction(item.Action); now += 1; panel.Commit();
                s = OrientationStoryState.From(flow, plan);
                Assert.That(s.ShowCorrect, Is.EqualTo(right), "The chip appears only after a correct practice answer");
                flow.Next();
            }
            Assert.That(OrientationStoryState.From(null, plan).Progress, Is.Null);
        }

        [Test, Category("REQ_ORI_01"), Category("REQ_APP_07")]
        public void OrientationDecorSitsClearOfTheExistingLabelsAndButton()
        {
            var display = new GameObject("Silent orientation screen").transform;
            try
            {
                var decor = new OrientationStoryDecor(display, Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf"));
                decor.Present(new OrientationStoryState(StoryIcon.AddRing, .5f, true));
                Assert.That(decor.Icon.enabled, Is.True); Assert.That(decor.Progress.Fraction, Is.EqualTo(.5f)); Assert.That(decor.Correct.Visible, Is.True);
                Assert.That(decor.Correct.Label.text, Is.EqualTo("Correct"));
                StoryComponentTests.AssertTextOnOpaqueAaaSurface(decor.Correct.Label, decor.Correct.Surface);
                // Existing layout in mm: backing 720 x 280, button 320 x 45 at y -102, title centred at y 95.
                var icon = Bounds(decor.Icon.rectTransform); var bar = Bounds((RectTransform)decor.Progress.transform); var chip = Bounds((RectTransform)decor.Correct.transform);
                var button = new Rect(-160, -124.5f, 320, 45);
                Assert.That(icon.xMax, Is.LessThanOrEqualTo(-300)); Assert.That(chip.xMin, Is.GreaterThanOrEqualTo(185));
                Assert.That(bar.Overlaps(button), Is.False); Assert.That(bar.yMin, Is.GreaterThan(-140));
                foreach (var r in new[] { icon, bar, chip }) Assert.That(new Rect(-360, -140, 720, 280).Contains(r.min) && new Rect(-360, -140, 720, 280).Contains(r.max), Is.True);
                Assert.That(decor.Canvas.transform.localPosition.z, Is.InRange(-.012f, -.006f), "Between the backing face and the labels");
                Assert.That(decor.Edge.transform.parent.localPosition.z, Is.GreaterThan(.006f), "Edge glow sits behind the backing");
                decor.Present(OrientationStoryState.Hidden);
                Assert.That(decor.Icon.enabled || decor.Correct.Visible || decor.Progress.gameObject.activeSelf, Is.False);
                Assert.That(display.GetComponentsInChildren<Graphic>(true).Any(x => x.raycastTarget), Is.False);
                decor.Dispose();
                Assert.That(display.GetComponentsInChildren<Canvas>(true), Is.Empty);
            }
            finally { UnityEngine.Object.DestroyImmediate(display.gameObject); }
        }
    }
}
