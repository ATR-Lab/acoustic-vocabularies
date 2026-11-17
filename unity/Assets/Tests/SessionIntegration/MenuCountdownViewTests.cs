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
    // The real MenuSessionHost view with and without the station flag menu_countdown_visible.
    public sealed class MenuCountdownViewTests
    {
        const BindingFlags Private=BindingFlags.Instance|BindingFlags.NonPublic;

        sealed class View:IDisposable
        {
            readonly GameObject holder,lease;
            public readonly GameObject Presentation;public readonly MenuSessionHost Host;public readonly Canvas Canvas;
            public View(bool? flag)
            {
                holder=new GameObject("Inactive synthetic foundation");holder.SetActive(false);
                Presentation=new GameObject("Synthetic gated presentation");lease=new GameObject("Synthetic module lease");
                var foundation=holder.AddComponent<FoundationBootstrap>();var rotation=Quaternion.Euler(0,180,0);
                var config=new JObject{["observer_reference"]=new JObject{["position_m"]=new JArray(0,1.5,1.45),["rotation_xyzw"]=new JArray(rotation.x,rotation.y,rotation.z,rotation.w)}};
                if(flag.HasValue)config["menu_countdown_visible"]=flag.Value;
                typeof(FoundationBootstrap).GetField("configuration",Private).SetValue(foundation,config);foundation.presentationRoot=Presentation;
                Host=lease.AddComponent<MenuSessionHost>();Host.foundation=foundation;Host.presentationParent=Presentation.transform;Host.font=Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
                typeof(MenuSessionHost).GetMethod("CreateView",Private).Invoke(Host,null);Canvas=Presentation.GetComponentInChildren<Canvas>(true);
            }
            public void Show(string owner,bool readOnly){typeof(MenuSessionHost).GetField("visibleOwner",Private).SetValue(Host,owner);typeof(MenuSessionHost).GetField("readOnly",Private).SetValue(Host,readOnly);Canvas.gameObject.SetActive(true);}
            public void Dispose(){UnityEngine.Object.DestroyImmediate(Presentation);UnityEngine.Object.DestroyImmediate(lease);UnityEngine.Object.DestroyImmediate(holder);}
        }

        static string[] Names(Canvas canvas)=>canvas.GetComponentsInChildren<Transform>(true).Select(x=>x.name).ToArray();

        [TestCase(false)][TestCase(true)]
        [Category("REQ_MENU_08"),NUnit.Framework.Property("Requirement","REQ-MENU-08")]
        public void DefaultOrDisabledFlagCreatesNoCountdownElement(bool explicitFalse)
        {
            using(var view=new View(explicitFalse?false:(bool?)null))
            {
                Assert.That(view.Canvas.GetComponentsInChildren<Text>(true).Select(x=>x.name).OrderBy(x=>x,StringComparer.Ordinal),
                    Is.EqualTo(new[]{"Instructions","Meaning","Stored label","Stored label","Stored label"}));
                Assert.That(Names(view.Canvas).Where(x=>x.IndexOf("countdown",StringComparison.OrdinalIgnoreCase)>=0||x.IndexOf("timer",StringComparison.OrdinalIgnoreCase)>=0),Is.Empty);
                var before=Names(view.Canvas);view.Show("synthetic-owner",false);
                Assert.DoesNotThrow(()=>view.Host.ShowCountdown("synthetic-owner",12));
                Assert.That(Names(view.Canvas),Is.EqualTo(before),"Updates cannot create an element when the flag is off");
            }
        }

        [Test,Category("REQ_MENU_08"),NUnit.Framework.Property("Requirement","REQ-MENU-08")]
        public void EnabledFlagShowsOnlyTheSecondsAndTheSameTextForBothRoles()
        {
            using(var view=new View(true))
            {
                var texts=view.Canvas.GetComponentsInChildren<Text>(true);var countdown=texts.Single(x=>x.name=="Menu countdown");
                Assert.That(texts.Length,Is.EqualTo(6));Assert.That(view.Canvas.gameObject.activeSelf,Is.False,"Creation cannot reveal the countdown");
                var backing=view.Canvas.transform.Find("Menu countdown backing").GetComponent<Image>();
                Assert.That(backing.transform.GetSiblingIndex(),Is.LessThan(countdown.transform.GetSiblingIndex()));
                Assert.That(backing.rectTransform.anchoredPosition,Is.EqualTo(countdown.rectTransform.anchoredPosition));Assert.That(backing.rectTransform.sizeDelta,Is.EqualTo(countdown.rectTransform.sizeDelta));
                Assert.That(backing.raycastTarget||countdown.raycastTarget,Is.False);Assert.That(countdown.GetComponentsInChildren<Collider>(true),Is.Empty);
                Assert.That(view.Canvas.GetComponentsInChildren<BoxCollider>(true).Length,Is.EqualTo(3),"No new input surface");
                Assert.That(view.Canvas.GetComponentsInChildren<AudioSource>(true),Is.Empty,"No audio");
                var cards=Enumerable.Range(1,3).Select(i=>(RectTransform)view.Canvas.transform.Find("Candidate "+i)).ToArray();
                Rect Area(RectTransform x)=>new Rect(x.anchoredPosition-x.sizeDelta/2,x.sizeDelta);
                Assert.That(cards.Any(x=>Area(x).Overlaps(Area(countdown.rectTransform))),Is.False,"The countdown cannot cover a candidate");
                Assert.That(Rect.MinMaxRect(-420,-285,420,285).Contains(Area(countdown.rectTransform).min)&&Rect.MinMaxRect(-420,-285,420,285).Contains(Area(countdown.rectTransform).max),Is.True);

                view.Host.ShowCountdown("synthetic-owner",15);Assert.That(countdown.text,Is.Empty,"Nothing is written while no owner is visible");
                string Render(bool readOnly,int? seconds){view.Show("synthetic-owner",readOnly);view.Host.ShowCountdown("synthetic-owner",seconds);return countdown.text;}
                foreach(var seconds in new int?[]{24,15,13,8,1,null})
                    Assert.That(Render(true,seconds),Is.EqualTo(Render(false,seconds)),"Assigned and active text must match");
                Assert.That(Render(false,15),Is.EqualTo("15"));Assert.That(Render(true,15),Is.EqualTo("15"));Assert.That(Render(false,null),Is.Empty);
                Render(false,7);view.Host.ShowCountdown("another-owner",3);Assert.That(countdown.text,Is.EqualTo("7"),"A different owner cannot change this view");
                view.Host.Hide("synthetic-owner");Assert.That(countdown.gameObject.activeInHierarchy||backing.gameObject.activeInHierarchy,Is.False,"Hiding the menu hides the countdown");
                view.Host.ShowCountdown("synthetic-owner",5);Assert.That(countdown.text,Is.EqualTo("7"),"A hidden owner is no longer visible");
            }
        }
    }
}
