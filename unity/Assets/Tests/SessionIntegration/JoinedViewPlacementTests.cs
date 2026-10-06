using System;
using System.Reflection;
using AcousticVocab.Foundation;
using AcousticVocab.Teaching;
using AcousticVocab.SelectionMenus;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace AcousticVocab.SessionIntegration.Tests
{
    public sealed class JoinedViewPlacementTests
    {
        [TestCase(typeof(TeachingSessionHost),.05f)]
        [TestCase(typeof(MenuSessionHost),.02f)]
        public void RealHostViewUsesCalibratedWorldPoseAndRemainsUnderFocusRoot(Type hostType,float vertical)
        {
            var holder=new GameObject("Inactive synthetic foundation");holder.SetActive(false);
            var presentation=new GameObject("Synthetic focus-controlled root");var lease=new GameObject("Synthetic module lease");
            try
            {
                var foundation=holder.AddComponent<FoundationBootstrap>();var position=new Vector3(0,1.5f,1.45f);var rotation=Quaternion.Euler(0,180,0);
                var config=new JObject{["observer_reference"]=new JObject{["position_m"]=new JArray(position.x,position.y,position.z),["rotation_xyzw"]=new JArray(rotation.x,rotation.y,rotation.z,rotation.w)}};
                typeof(FoundationBootstrap).GetField("configuration",BindingFlags.NonPublic|BindingFlags.Instance).SetValue(foundation,config);
                // A nonidentity parent catches accidental local/world mixing.
                presentation.transform.SetPositionAndRotation(new Vector3(2,0,-3),Quaternion.Euler(0,37,0));foundation.presentationRoot=presentation;
                var host=lease.AddComponent(hostType);hostType.GetField("foundation").SetValue(host,foundation);hostType.GetField("presentationParent").SetValue(host,presentation.transform);hostType.GetField("font").SetValue(host,Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf"));
                hostType.GetMethod("CreateView",BindingFlags.NonPublic|BindingFlags.Instance).Invoke(host,null);
                var canvas=presentation.GetComponentInChildren<Canvas>(true);Assert.That(canvas,Is.Not.Null);Assert.That(canvas.transform.parent,Is.SameAs(presentation.transform));
                Assert.That(Vector3.Distance(canvas.transform.position,position+rotation*new Vector3(0,vertical,1.05f)),Is.LessThan(.00001f));Assert.That(Quaternion.Angle(canvas.transform.rotation,rotation),Is.LessThan(.001f));
                Assert.That(Vector3.Angle(rotation*Vector3.forward,canvas.transform.position-position),Is.LessThan(3));
                Assert.That(canvas.gameObject.activeSelf,Is.False,"Creation cannot reveal content");canvas.gameObject.SetActive(true);presentation.SetActive(false);Assert.That(canvas.gameObject.activeInHierarchy,Is.False,"Focus concealment must still own the view");
            }
            finally{UnityEngine.Object.DestroyImmediate(presentation);UnityEngine.Object.DestroyImmediate(lease);UnityEngine.Object.DestroyImmediate(holder);}
        }
    }
}
