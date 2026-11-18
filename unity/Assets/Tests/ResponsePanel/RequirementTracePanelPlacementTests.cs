using System.Linq;
using System.Reflection;
using AcousticVocab.Foundation;
using AcousticVocab.ResponsePanel.Editor;
using NUnit.Framework;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace AcousticVocab.ResponsePanel.Tests
{
    // Requirement trace: the response panel is built as world-space geometry in the
    // headset presentation root at the configured observer reference.
    public sealed class RequirementTracePanelPlacementTests
    {
        [TestCase(PanelMode.LessonMessage, PanelRole.Command)]
        [TestCase(PanelMode.LessonAtomic, PanelRole.Target)]
        [TestCase(PanelMode.FullMessage, PanelRole.Command)]
        [Category("REQ_APP_03"), NUnit.Framework.Property("Requirement", "REQ-APP-03")]
        public void ResponsePanelIsWorldGeometryAtTheObserverReferenceInsideTheHeadsetView(PanelMode mode, PanelRole role)
        {
            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            try
            {
                var root = new GameObject("SyntheticPreviewRoot"); var foundation = root.AddComponent<FoundationBootstrap>();
                foundation.presentationRoot = new GameObject("SyntheticPresentation"); foundation.presentationRoot.transform.SetParent(root.transform);
                var camera = new GameObject("SyntheticObserver").AddComponent<Camera>(); camera.transform.SetParent(root.transform); foundation.observerCamera = camera;
                foundation.seatedOrigin = root.transform;
                var controller = PanelPreview.Populate(foundation, mode, role);
                const BindingFlags flags = BindingFlags.NonPublic | BindingFlags.Instance;
                var panel = (Transform)typeof(ResponsePanelController).GetField("panel", flags).GetValue(controller);
                var settings = (PanelSettings)typeof(ResponsePanelController).GetField("settings", flags).GetValue(controller);
                var reference = StationConfig.ReferencePose(foundation.Configuration);
                Assert.That(panel.parent, Is.SameAs(foundation.presentationRoot.transform));
                Assert.That(Vector3.Distance(panel.position, reference.position + reference.rotation * new Vector3(0, settings.VerticalOffset, settings.Distance)), Is.LessThan(.0001f));
                Assert.That(Quaternion.Angle(panel.rotation, reference.rotation), Is.LessThan(.01f));
                Assert.That(Vector3.Dot(panel.position - reference.position, reference.rotation * Vector3.forward), Is.GreaterThan(0), "In front of the observer");
                var labels = panel.GetComponentsInChildren<TextMesh>(true); var keys = panel.GetComponentsInChildren<Collider>(true);
                Assert.That(labels.Any(x => x.text == "Commit"), Is.True); Assert.That(keys.Length, Is.GreaterThan(0));
                Assert.That(Object.FindObjectsByType<Canvas>(FindObjectsInactive.Include, FindObjectsSortMode.None).Where(c => c.renderMode != RenderMode.WorldSpace), Is.Empty);
                Assert.That(Object.FindObjectsByType<TextMesh>(FindObjectsInactive.Include, FindObjectsSortMode.None).All(x => x.transform.IsChildOf(foundation.presentationRoot.transform)), Is.True);
            }
            finally { EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single); }
        }
    }
}
