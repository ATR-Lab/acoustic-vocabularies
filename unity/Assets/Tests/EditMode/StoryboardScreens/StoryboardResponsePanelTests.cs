using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using AcousticVocab.Foundation;
using AcousticVocab.ResponsePanel;
using AcousticVocab.ResponsePanel.Editor;
using AcousticVocab.Theme;
using NUnit.Framework;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.UI;

namespace AcousticVocab.StoryboardScreens.Tests
{
    // The storyboard look of the test response panel (frames 8 and 9): theme
    // tokens per key state, the hover edge, the constant "Heard once" chip and
    // unchanged key geometry. Synthetic engineering panel only; no XR input.
    public sealed class StoryboardResponsePanelTests
    {
        const BindingFlags Private = BindingFlags.NonPublic | BindingFlags.Instance;
        static readonly Type Controller = typeof(ResponsePanelController);

        sealed class Panel : IDisposable
        {
            internal readonly ResponsePanelController Controller;
            internal readonly FoundationBootstrap Foundation;
            internal double Now;
            internal Panel(PanelMode mode, PanelRole role)
            {
                EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                var root = new GameObject("SyntheticPreviewRoot"); Foundation = root.AddComponent<FoundationBootstrap>();
                Foundation.presentationRoot = new GameObject("SyntheticPresentation"); Foundation.presentationRoot.transform.SetParent(root.transform);
                var camera = new GameObject("SyntheticObserver").AddComponent<Camera>(); camera.transform.SetParent(root.transform); Foundation.observerCamera = camera;
                Foundation.seatedOrigin = root.transform;
                Controller = PanelPreview.Populate(Foundation, mode, role);
            }
            // A fresh state on a controllable clock, opened on a new synthetic request.
            internal ResponseState Open(PanelMode mode, PanelRole role, string trial = "synthetic-trial")
            {
                Now = 0; var state = new ResponseState(() => Now, _ => { });
                typeof(ResponsePanelController).GetProperty("State").SetValue(Controller, state);
                state.Open(new PanelRequest(trial, mode, role, 0, mode == PanelMode.Practice ? 60000 : (double?)null));
                Refresh(); return state;
            }
            internal void Refresh() => Controller.GetType().GetMethod("Refresh", Private).Invoke(Controller, null);
            internal object Field(string name) => Controller.GetType().GetField(name, Private).GetValue(Controller);
            internal IEnumerable<object> Keys => ((IEnumerable)Field("keys")).Cast<object>();
            internal ResponsePanelStyle Style => (ResponsePanelStyle)Field("style");
            internal Transform PanelRoot => (Transform)Field("panel");
            internal PanelSettings Settings => (PanelSettings)Field("settings");
            public void Dispose() => EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        }
        // Material colours round-trip through the renderer at float precision.
        static void Near(Color actual, Color expected, string message)
        {
            float d = Mathf.Max(Mathf.Max(Mathf.Abs(actual.r - expected.r), Mathf.Abs(actual.g - expected.g)), Mathf.Max(Mathf.Abs(actual.b - expected.b), Mathf.Abs(actual.a - expected.a)));
            Assert.That(d, Is.LessThan(1e-3f), message + ": expected " + expected + " but was " + actual);
        }
        static T Get<T>(object key, string name) => (T)key.GetType().GetField(name).GetValue(key);
        static Color Surface(object key) => Get<MeshRenderer>(key, "Surface").sharedMaterial.color;
        static Color Edge(object key) => Get<MeshRenderer>(key, "Edge").sharedMaterial.color;
        static string Value(object key) => Get<string>(key, "Value");
        static bool Shown(object key) => Get<Transform>(key, "Root").gameObject.activeSelf;

        static void AssertKeyTokens(Panel panel, ResponseState state, object hovered, string context)
        {
            foreach (var key in panel.Keys.Where(Shown))
            {
                string value = Value(key);
                bool control = value == "commit" || value == "dont_know";
                bool selected = !control && (value == state.SelectedTarget || value == state.SelectedAction);
                bool enabled = value == "commit" ? state.CanCommit : state.WindowOpen;
                Near(Surface(key), StudyTheme.SurfaceFor(enabled, selected), context + " surface " + value);
                Assert.That(StudyTheme.ContrastRatio(StudyTheme.TextPrimary, Surface(key)), Is.GreaterThanOrEqualTo(StudyTheme.MinimumTextContrast), context + " contrast " + value);
                Assert.That(Get<TextMesh>(key, "Text").color, Is.EqualTo(StudyTheme.TextPrimary), context + " text " + value);
                Near(Edge(key), StudyTheme.EdgeFor(selected || enabled && key == hovered), context + " edge " + value);
            }
        }

        [Test, Category("REQ_RESP_01"), Category("REQ_TEST_03"), Category("REQ_TEST_08")]
        public void EveryKeyStateUsesThemeTokensWithAaaContrast()
        {
            foreach (var (mode, role) in new[] { (PanelMode.FullMessage, PanelRole.Command), (PanelMode.AtomicProbe, PanelRole.Action), (PanelMode.AtomicProbe, PanelRole.Target), (PanelMode.Practice, PanelRole.Command) })
            {
                using var panel = new Panel(mode, role);
                var state = panel.Open(mode, role); string context = mode + "/" + role;
                AssertKeyTokens(panel, state, null, context + " empty");
                if (role != PanelRole.Action) state.SelectTarget("G");
                if (role != PanelRole.Target) state.SelectAction("TAG");
                panel.Refresh(); AssertKeyTokens(panel, state, null, context + " selected");
                state.Commit(); panel.Refresh(); Assert.That(state.Locked, Is.True);
                AssertKeyTokens(panel, state, null, context + " locked");
                // Role label backing and pointer colours.
                Near(((GameObject)panel.Field("roleBacking")).GetComponent<Renderer>().sharedMaterial.color, StudyTheme.PanelFill, "role backing");
                Near(((LineRenderer)panel.Field("ray")).sharedMaterial.color, StudyTheme.Pointer, "ray");
                Near(((GameObject)panel.Field("tip")).GetComponent<Renderer>().sharedMaterial.color, StudyTheme.Pointer, "tip");
            }
        }

        [Test, Category("REQ_TEST_06"), Category("REQ_TEST_08"), Category("REQ_TEST_03")]
        public void RayHoverLightsOnlyTheEdgeOfAnEnabledKeyAndNeverSelectsOrCommits()
        {
            using var panel = new Panel(PanelMode.FullMessage, PanelRole.Command);
            var state = panel.Open(PanelMode.FullMessage, PanelRole.Command);
            var commit = panel.Keys.Single(k => Value(k) == "commit");
            Physics.SyncTransforms();
            var target = Get<BoxCollider>(commit, "Collider").bounds.center;
            var pointer = new Ray(target - panel.PanelRoot.forward * .5f, panel.PanelRoot.forward);
            var pointed = Controller.GetMethod("Pointed", Private).Invoke(panel.Controller, new object[] { pointer, 2f });
            Assert.That(pointed, Is.SameAs(commit), "The ray picks the Commit key it points at");
            Controller.GetField("hovered", Private).SetValue(panel.Controller, commit);
            // Commit is disabled until a legal pair is selected: hovering it stays unlit.
            panel.Refresh(); Near(Edge(commit), StudyTheme.EdgeIdle, "hovered disabled Commit"); AssertKeyTokens(panel, state, commit, "hover disabled");
            state.SelectTarget("G"); state.SelectAction("TAG"); panel.Refresh();
            Near(Edge(commit), StudyTheme.EdgeLit, "hovered Commit"); Near(Surface(commit), StudyTheme.SurfaceEnabled, "hovered Commit");
            AssertKeyTokens(panel, state, commit, "hover enabled");
            Assert.That(state.Locked, Is.False, "Hover never commits"); Assert.That(state.Result, Is.Null);
            Assert.That(state.SelectedTarget, Is.EqualTo("G")); Assert.That(state.SelectedAction, Is.EqualTo("TAG"));
        }

        [Test, Category("REQ_RESP_01"), Category("REQ_TEST_03")]
        public void KeyAndLabelGeometryIsUnchangedAndTheStyleAddsNoInputSurface()
        {
            foreach (var (mode, role) in new[] { (PanelMode.FullMessage, PanelRole.Command), (PanelMode.AtomicProbe, PanelRole.Target) })
            {
                using var panel = new Panel(mode, role); panel.Open(mode, role); Physics.SyncTransforms();
                var s = panel.Settings; float w = s.ButtonWidth, h = s.ButtonHeight, g = s.Gap;
                var keys = panel.Keys.ToArray(); Assert.That(keys.Length, Is.EqualTo(14));
                for (int i = 0; i < keys.Length; i++)
                {
                    Vector3 expected = i < 8 ? new Vector3((i % 4 - 1.5f) * (w + g), -i / 4 * (h + g), 0)
                        : i < 12 ? new Vector3((i % 4 - 1.5f) * (w + g), -2 * (h + g), 0)
                        : new Vector3((i == 12 ? -1 : 1) * (w + g), -3 * (h + g), 0);
                    var root = Get<Transform>(keys[i], "Root"); var box = Get<BoxCollider>(keys[i], "Collider");
                    Assert.That(Vector3.Distance(root.localPosition, expected), Is.LessThan(1e-6f), "key " + i + " position");
                    Assert.That(Vector3.Distance(box.transform.localScale, new Vector3(w * (i >= 12 ? 1.5f : 1), h, .015f)), Is.LessThan(1e-6f), "key " + i + " size");
                    Assert.That(box.transform.localPosition, Is.EqualTo(Vector3.zero)); Assert.That(box.size, Is.EqualTo(Vector3.one));
                    Assert.That(Get<TextMesh>(keys[i], "Text").transform.localPosition, Is.EqualTo(new Vector3(0, 0, -.012f)));
                }
                var colliders = panel.PanelRoot.GetComponentsInChildren<Collider>(true);
                Assert.That(colliders, Is.EquivalentTo(keys.Select(k => (Collider)Get<BoxCollider>(k, "Collider"))), "Only the existing keys take input");
                Assert.That(panel.PanelRoot.GetComponentsInChildren<Graphic>(true).All(x => !x.raycastTarget), Is.True);
                Assert.That(((TextMesh)panel.Field("roleLabel")).transform.localPosition, Is.EqualTo(new Vector3(0, h + g, -.015f)));
            }
        }

        static string Snapshot(ResponsePanelStyle style) => string.Join("|", style.Chip.activeSelf, style.ChipText.text, style.ChipText.fontSize, style.ChipText.color,
            style.ChipBacking.color, ((RectTransform)style.Chip.transform).sizeDelta, style.Chip.transform.position.ToString("F5"), style.Chip.transform.lossyScale.ToString("F5"));

        [Test, Category("REQ_NEUT_01"), Category("REQ_NEUT_02"), Category("REQ_NEUT_03"), Category("REQ_TEST_02")]
        public void HeardOnceChipIsIdenticalForCorrectWrongDontKnowAndTimeout()
        {
            foreach (var (mode, role, target, action, other) in new[] { (PanelMode.FullMessage, PanelRole.Command, "G", "TAG", "A"), (PanelMode.AtomicProbe, PanelRole.Target, "G", (string)null, "A") })
            {
                using var panel = new Panel(mode, role); string reference = null;
                // The panel never knows the requested command, so "correct" and
                // "wrong" are two different committed answers to the same request.
                foreach (string outcome in new[] { "correct", "wrong", "dont_know", "timeout" })
                    for (int trial = 0; trial < 32; trial += 31)
                    {
                        var state = panel.Open(mode, role, "synthetic-" + outcome + "-" + trial);
                        string before = Snapshot(panel.Style); reference ??= before;
                        Assert.That(before, Is.EqualTo(reference), mode + " " + outcome + " before response");
                        if (outcome == "correct" || outcome == "wrong")
                        {
                            state.SelectTarget(outcome == "correct" ? target : other);
                            if (action != null) state.SelectAction(outcome == "correct" ? action : "ADD_ONE");
                            Assert.That(state.Commit(), Is.True);
                        }
                        else if (outcome == "dont_know") Assert.That(state.DontKnow(), Is.True);
                        else { panel.Now = 20000; state.Tick(); }
                        Assert.That(state.Locked, Is.True); panel.Refresh();
                        Assert.That(Snapshot(panel.Style), Is.EqualTo(reference), mode + " " + outcome + " after response");
                    }
                Assert.That(panel.Style.Chip.activeSelf, Is.True); Assert.That(panel.Style.ChipText.text, Is.EqualTo("Heard once"));
            }
            // Lessons and engineering practice show no chip.
            foreach (var (mode, role) in new[] { (PanelMode.LessonAtomic, PanelRole.Action), (PanelMode.LessonMessage, PanelRole.Command), (PanelMode.Practice, PanelRole.Command) })
            {
                using var panel = new Panel(mode, role); panel.Open(mode, role);
                Assert.That(panel.Style.Chip.activeSelf, Is.False, mode.ToString());
            }
        }

        [Test, Category("REQ_NEUT_01"), Category("REQ_TEST_02")]
        public void HeardOnceChipMeetsTheConfiguredGlyphAngleAndAaaContrast()
        {
            using var panel = new Panel(PanelMode.FullMessage, PanelRole.Command); panel.Open(PanelMode.FullMessage, PanelRole.Command);
            var text = panel.Style.ChipText; var font = text.font;
            font.RequestCharactersInTexture("H", text.fontSize, text.fontStyle);
            Assert.That(font.GetCharacterInfo('H', out CharacterInfo cap, text.fontSize, text.fontStyle), Is.True);
            float capMetres = (cap.maxY - cap.minY) * text.transform.lossyScale.y;
            var observer = StationConfig.ReferencePose(panel.Foundation.Configuration).position;
            float distance = Vector3.Distance(observer, text.transform.position);
            float angle = 2 * Mathf.Atan(capMetres / 2 / distance) * Mathf.Rad2Deg;
            Assert.That(angle, Is.GreaterThanOrEqualTo(panel.Settings.TextAngleDegrees * .999f), "Chip capitals are at least the configured key-label angle");
            Assert.That(text.color, Is.EqualTo(StudyTheme.TextPrimary)); Assert.That(text.fontStyle, Is.EqualTo(StudyTheme.TextStyle));
            Assert.That(panel.Style.ChipBacking.color.a, Is.EqualTo(1f));
            Assert.That(StudyTheme.ContrastRatio(text.color, panel.Style.ChipBacking.color), Is.GreaterThanOrEqualTo(StudyTheme.MinimumTextContrast));
            // The chip sits above the role-label row and does not overlap it.
            var s = panel.Settings; float chipBottom = panel.Style.Chip.transform.localPosition.y - ((RectTransform)panel.Style.Chip.transform).sizeDelta.y * .001f / 2;
            Assert.That(chipBottom, Is.GreaterThan(s.ButtonHeight + s.Gap + s.ButtonHeight * .75f / 2));
        }
    }
}
