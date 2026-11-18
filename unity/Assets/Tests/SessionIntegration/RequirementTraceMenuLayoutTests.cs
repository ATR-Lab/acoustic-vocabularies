using System;
using System.Linq;
using System.Reflection;
using AcousticVocab.Foundation;
using AcousticVocab.SelectionMenus;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.UI;

namespace AcousticVocab.SessionIntegration.Tests
{
    // Requirement trace for the menu screen built by the real MenuSessionHost.
    public sealed class RequirementTraceMenuLayoutTests
    {
        const BindingFlags Private=BindingFlags.Instance|BindingFlags.NonPublic;

        [Test, Category("REQ_MENU_08")]
        public void DefaultMenuScreenHasNoVisibleCountdown()
        {
            var holder=new GameObject("Inactive synthetic foundation");holder.SetActive(false);
            var presentation=new GameObject("Synthetic gated presentation");var lease=new GameObject("Synthetic module lease");
            try
            {
                var foundation=holder.AddComponent<FoundationBootstrap>();var rotation=Quaternion.Euler(0,180,0);
                var config=new JObject{["observer_reference"]=new JObject{["position_m"]=new JArray(0,1.5,1.45),["rotation_xyzw"]=new JArray(rotation.x,rotation.y,rotation.z,rotation.w)}};
                typeof(FoundationBootstrap).GetField("configuration",Private).SetValue(foundation,config);foundation.presentationRoot=presentation;
                var host=lease.AddComponent<MenuSessionHost>();host.foundation=foundation;host.presentationParent=presentation.transform;host.font=Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
                typeof(MenuSessionHost).GetMethod("CreateView",Private).Invoke(host,null);
                var canvas=presentation.GetComponentInChildren<Canvas>(true);
                // Every text on the menu screen: instructions, meaning and the three stored labels.
                var texts=canvas.GetComponentsInChildren<Text>(true).Select(x=>x.name).OrderBy(x=>x,StringComparer.Ordinal).ToArray();
                Assert.That(texts,Is.EqualTo(new[]{"Instructions","Meaning","Stored label","Stored label","Stored label"}));
                Assert.That(canvas.GetComponentsInChildren<Transform>(true).Select(x=>x.name).Where(x=>x.IndexOf("count",StringComparison.OrdinalIgnoreCase)>=0||x.IndexOf("timer",StringComparison.OrdinalIgnoreCase)>=0),Is.Empty);
                // A countdown may exist only behind a station flag that is off by default (#317):
                // the view built with the default configuration holds no countdown element.
            }
            finally{UnityEngine.Object.DestroyImmediate(presentation);UnityEngine.Object.DestroyImmediate(lease);UnityEngine.Object.DestroyImmediate(holder);}
        }
    }
}
