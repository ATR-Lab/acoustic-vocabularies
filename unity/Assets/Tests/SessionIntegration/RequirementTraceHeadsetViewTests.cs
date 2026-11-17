using System;
using System.Linq;
using System.Reflection;
using AcousticVocab.Foundation;
using AcousticVocab.SelectionMenus;
using AcousticVocab.Teaching;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace AcousticVocab.SessionIntegration.Tests
{
    // Requirement trace: lesson and menu views are world-space canvases placed in
    // front of the configured observer reference, inside the headset presentation root.
    public sealed class RequirementTraceHeadsetViewTests
    {
        [TestCase(typeof(TeachingSessionHost))]
        [TestCase(typeof(MenuSessionHost))]
        [Category("REQ_APP_03"), NUnit.Framework.Property("Requirement", "REQ-APP-03")]
        public void ParticipantViewIsAWorldSpaceCanvasInFrontOfTheObserverReference(Type hostType)
        {
            var holder = new GameObject("Inactive synthetic foundation"); holder.SetActive(false);
            var presentation = new GameObject("Synthetic headset presentation root"); var lease = new GameObject("Synthetic module lease");
            try
            {
                var foundation = holder.AddComponent<FoundationBootstrap>(); var position = new Vector3(.3f, 1.4f, -.8f); var rotation = Quaternion.Euler(0, 120, 0);
                var config = new JObject { ["observer_reference"] = new JObject { ["position_m"] = new JArray(position.x, position.y, position.z), ["rotation_xyzw"] = new JArray(rotation.x, rotation.y, rotation.z, rotation.w) } };
                typeof(FoundationBootstrap).GetField("configuration", BindingFlags.NonPublic | BindingFlags.Instance).SetValue(foundation, config);
                foundation.presentationRoot = presentation;
                var host = lease.AddComponent(hostType);
                hostType.GetField("foundation").SetValue(host, foundation); hostType.GetField("presentationParent").SetValue(host, presentation.transform);
                hostType.GetField("font").SetValue(host, Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf"));
                hostType.GetMethod("CreateView", BindingFlags.NonPublic | BindingFlags.Instance).Invoke(host, null);
                var canvases = presentation.GetComponentsInChildren<Canvas>(true);
                Assert.That(canvases.Length, Is.GreaterThanOrEqualTo(1));
                Assert.That(canvases.All(c => c.renderMode == RenderMode.WorldSpace), Is.True, "No screen-space participant canvas");
                Assert.That(lease.GetComponentsInChildren<Canvas>(true), Is.Empty, "The view lives only under the headset presentation root");
                var canvas = presentation.GetComponentInChildren<Canvas>(true); Assert.That(canvas.transform.parent, Is.SameAs(presentation.transform));
                var offset = canvas.transform.position - position; float ahead = Vector3.Dot(offset, rotation * Vector3.forward);
                Assert.That(ahead, Is.InRange(.5f, 2f), "In front of the observer at reading distance");
                Assert.That(Vector3.Angle(canvas.transform.forward, rotation * Vector3.forward), Is.LessThan(.1f), "Facing away from the observer along the reference heading");
            }
            finally { UnityEngine.Object.DestroyImmediate(presentation); UnityEngine.Object.DestroyImmediate(lease); UnityEngine.Object.DestroyImmediate(holder); }
        }
    }
}
