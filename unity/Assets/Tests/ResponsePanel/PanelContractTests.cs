using System;
using System.IO;
using System.Linq;
using AcousticVocab.Foundation;
using AcousticVocab.Foundation.Editor;
using AcousticVocab.ResponsePanel.Editor;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace AcousticVocab.ResponsePanel.Tests
{
    public class PanelContractTests
    {
        static string Schema => File.ReadAllText("Assets/ExperimentApp/Resources/ResponsePanelSchema.json");
        static JObject Example => JObject.Parse(File.ReadAllText(Path.Combine(FoundationBuild.RepositoryRoot, "apparatus/response-panel/response-panel.example.json")));
        [Test] public void CanonicalSchemaMatchesEmbeddedBytes() => ResponsePanelBuild.VerifySchema();
        [Test] public void PublicExampleAndUnknownOrInconsistentSettingsCannotRun()
        {
            var source = Example;
            Assert.Throws<ConfigurationFault>(() => PanelSettings.Parse(source.ToString(), Schema, "engineering-pending-review"));
            source["configuration_status"] = "provisioned_engineering";
            var settings = PanelSettings.Parse(source.ToString(), Schema, "engineering-pending-review");
            Assert.Throws<ConfigurationFault>(() => settings.VerifyStationInput("hands")); settings.VerifyStationInput("controllers");
            source["unexpected"] = "value"; Assert.Throws<ConfigurationFault>(() => PanelSettings.Parse(source.ToString(), Schema, "engineering-pending-review"));
            source.Remove("unexpected"); source["text_angle_deg"] = .01;
            Assert.Throws<ConfigurationFault>(() => PanelSettings.Parse(source.ToString(), Schema, "engineering-pending-review"));
        }
        [Test] public void DurableJournalKeepsSelectionSeparateFromFinalTimeoutResponse()
        {
            string directory = Path.Combine(FoundationBuild.RepositoryRoot, ".local", "panel-journal-test-" + Guid.NewGuid().ToString("N"));
            double now = 0;
            using (var journal = new PanelJournal(directory, new JObject { ["build_id"] = "synthetic" }, "synthetic-station", new JObject { ["source"] = "synthetic" }))
            {
                var state = new ResponseState(() => now, journal.Process); state.Responded += journal.Response;
                state.Open(new PanelRequest("synthetic", PanelMode.AtomicProbe, PanelRole.Action, 0)); state.SelectAction("SCAN"); now = 7000; state.Tick();
            }
            var rows = File.ReadAllLines(Directory.GetFiles(directory).Single()).Select(JObject.Parse).ToArray();
            Assert.That((string)rows[0]["event"], Is.EqualTo("header"));
            var response = rows.Single(x => (string)x["event"] == "response");
            Assert.That((string)response["response_code"], Is.EqualTo("TIMEOUT")); Assert.That(response["response_action"].Type, Is.EqualTo(JTokenType.Null));
            Assert.That((string)response["selected_action"], Is.EqualTo("SCAN")); Assert.That((double)response["mono_ms"], Is.EqualTo(7000));
        }
        [Test] public void ComponentDisableAbortsAndPreventsSafeBoundaryRecovery()
        {
            var root = new GameObject("SyntheticDisableTest");
            try
            {
                var foundation = root.AddComponent<FoundationBootstrap>();
                typeof(FoundationBootstrap).GetProperty("Ready").SetValue(foundation, true);
                var panel = root.AddComponent<ResponsePanelController>(); panel.foundation = foundation;
                var state = new ResponseState(() => 0, _ => { }); state.Open(new PanelRequest("synthetic", PanelMode.Practice, PanelRole.Command, 0, 1000));
                typeof(ResponsePanelController).GetProperty("State").SetValue(panel, state);
                typeof(ResponsePanelController).GetProperty("InputAvailable").SetValue(panel, true);
                Assert.That(panel.ReadyForTrial, Is.True);
                typeof(ResponsePanelController).GetMethod("OnApplicationFocus", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance).Invoke(panel, new object[] { false });
                Assert.That(panel.ReadyForTrial, Is.False); Assert.That(panel.ConfirmInputRecoveryAtSafeBoundary(), Is.False);
                typeof(ResponsePanelController).GetMethod("OnApplicationFocus", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance).Invoke(panel, new object[] { true });
                Assert.That(panel.ConfirmInputRecoveryAtSafeBoundary(), Is.True);
                state.Open(new PanelRequest("synthetic-second", PanelMode.Practice, PanelRole.Command, 0, 1000));
                typeof(ResponsePanelController).GetMethod("OnDisable", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance).Invoke(panel, null);
                Assert.That(panel.InputAvailable, Is.False); Assert.That(panel.FaultLatched, Is.True); Assert.That(state.Aborted, Is.True); Assert.That(state.Result, Is.Null);
            }
            finally { UnityEngine.Object.DestroyImmediate(root); }
        }
        [TestCase(PanelMode.FullMessage, PanelRole.Command, false, 10)]
        [TestCase(PanelMode.FullMessage, PanelRole.Command, true, 14)]
        [TestCase(PanelMode.AtomicProbe, PanelRole.Action, false, 11)]
        [TestCase(PanelMode.AtomicProbe, PanelRole.Target, false, 11)]
        public void ActualGeneratedPanelShowsOnlyItsRoleAndFitsText(PanelMode mode, PanelRole role, bool select, int labels)
        {
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            try
            {
                var root = new GameObject("SyntheticPreviewRoot"); var foundation = root.AddComponent<FoundationBootstrap>();
                foundation.presentationRoot = new GameObject("SyntheticPresentation"); foundation.presentationRoot.transform.SetParent(root.transform);
                var camera = new GameObject("SyntheticObserver").AddComponent<Camera>(); camera.transform.SetParent(root.transform); foundation.observerCamera = camera;
                foundation.seatedOrigin = root.transform;
                var panel = PanelPreview.Populate(foundation, mode, role, select);
                var actual = foundation.presentationRoot.GetComponentsInChildren<TextMesh>().Where(x => x.text.Length > 0).ToArray();
                Assert.That(actual.Length, Is.EqualTo(labels)); Assert.That(actual.Any(x => x.text == "Commit"), Is.True); Assert.That(actual.Any(x => x.text == "Don't know"), Is.True);
                Assert.That(actual.All(x => x.transform.localScale.x > 0 && float.IsFinite(x.transform.localScale.x)), Is.True);
                if (!select) { Assert.That(panel.State.SelectedTarget, Is.Null); Assert.That(panel.State.SelectedAction, Is.Null); Assert.That(panel.State.CanCommit, Is.False); }
                if (role == PanelRole.Action) Assert.That(actual.Any(x => x.text == "A"), Is.False);
            }
            finally { EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single); }
        }
    }
}
