using System;
using System.Linq;
using System.Reflection;
using AcousticVocab.Foundation;
using AcousticVocab.SelectionMenus;
using AcousticVocab.Teaching;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.UI;

namespace AcousticVocab.SessionIntegration.Tests
{
    public sealed class JoinedTextContrastTests
    {
        const BindingFlags Private=BindingFlags.Instance|BindingFlags.NonPublic;

        [TestCase(typeof(TeachingSessionHost),4)]
        [TestCase(typeof(MenuSessionHost),2)]
        public void RealHostBackingsCoverTextWithoutInterceptingInputAndShareConcealment(Type type,int count)
        {
            using(var view=new View(type))
            {
                var canvas=view.Canvas;
                var backings=canvas.GetComponentsInChildren<Image>(true).Where(x=>x.name.EndsWith(" backing",StringComparison.Ordinal)).ToArray();
                Assert.That(backings.Length,Is.EqualTo(count));
                Assert.That(canvas.gameObject.activeSelf,Is.False,"Construction must not reveal text or its backing");
                foreach(var backing in backings)
                {
                    var name=backing.name.Substring(0,backing.name.Length-" backing".Length);
                    var text=canvas.transform.Find(name).GetComponent<Text>();
                    Assert.That(backing.transform.parent,Is.SameAs(text.transform.parent));
                    Assert.That(backing.transform.GetSiblingIndex(),Is.LessThan(text.transform.GetSiblingIndex()),"Backing must render before foreground");
                    Assert.That(backing.rectTransform.anchoredPosition,Is.EqualTo(text.rectTransform.anchoredPosition));
                    Assert.That(backing.rectTransform.sizeDelta,Is.EqualTo(text.rectTransform.sizeDelta));
                    Assert.That(backing.rectTransform.anchorMin,Is.EqualTo(text.rectTransform.anchorMin));
                    Assert.That(backing.rectTransform.anchorMax,Is.EqualTo(text.rectTransform.anchorMax));
                    Assert.That(backing.rectTransform.pivot,Is.EqualTo(text.rectTransform.pivot));
                    Assert.That(backing.color.a,Is.EqualTo(1));
                    Assert.That(backing.raycastTarget,Is.False);
                    Assert.That(backing.GetComponentsInChildren<Collider>(true),Is.Empty);
                    Assert.That(text.color,Is.EqualTo(Color.white));
                    Assert.That(Contrast(text.color,backing.color),Is.GreaterThanOrEqualTo(7),"Software palette screen only; native readability still requires capture");
                    Assert.That(Contrast(Color.yellow,backing.color),Is.GreaterThanOrEqualTo(7));
                    Assert.That(text.fontSize,Is.EqualTo(type==typeof(TeachingSessionHost)?27:name=="Instructions"?28:26));
                }
                Assert.That(canvas.transform.localScale,Is.EqualTo(Vector3.one*.001f));
                canvas.gameObject.SetActive(true);view.Presentation.SetActive(false);
                Assert.That(canvas.GetComponentsInChildren<Graphic>(true).All(x=>!x.gameObject.activeInHierarchy),Is.True,"The foundation gate must conceal every new graphic");
                view.Presentation.SetActive(true);
                type.GetField("visibleOwner",Private).SetValue(view.Host,"synthetic-owner");
                type.GetMethod("Hide").Invoke(view.Host,new object[]{"another-owner"});
                Assert.That(canvas.gameObject.activeSelf,Is.True,"A different owner cannot change this view");
                type.GetMethod("Hide").Invoke(view.Host,new object[]{"synthetic-owner"});
                Assert.That(canvas.GetComponentsInChildren<Graphic>(true).All(x=>!x.gameObject.activeInHierarchy),Is.True,"Owner hide must include every backing");
            }
        }

        [Test]
        public void RealTeachingRoleHighlightRetainsReadableWhiteAndYellowUntilHidden()
        {
            using(var view=new View(typeof(TeachingSessionHost)))
            {
                var host=(TeachingSessionHost)view.Host;
                typeof(TeachingSessionHost).GetMethod("ShowGrammar",Private).Invoke(host,new object[]{"roles"});
                var action=view.Canvas.transform.Find("Action words").GetComponent<Text>();
                var target=view.Canvas.transform.Find("Target words").GetComponent<Text>();
                Assert.That(action.text,Is.EqualTo("Action"));Assert.That(target.text,Is.EqualTo("Target"));
                host.Highlight("grammar",LessonHighlight.Action);Assert.That(action.color,Is.EqualTo(Color.yellow));Assert.That(target.color,Is.EqualTo(Color.white));
                host.Highlight("grammar",LessonHighlight.Target);Assert.That(action.color,Is.EqualTo(Color.white));Assert.That(target.color,Is.EqualTo(Color.yellow));
                foreach(var text in new[]{action,target})
                    Assert.That(Contrast(text.color,view.Canvas.transform.Find(text.name+" backing").GetComponent<Image>().color),Is.GreaterThanOrEqualTo(7));
                typeof(TeachingSessionHost).GetMethod("ShowGrammar",Private).Invoke(host,new object[]{"hidden"});
                Assert.That(view.Canvas.gameObject.activeSelf,Is.False);
            }
        }

        [Test]
        public void RealMenuCandidateCardsKeepTheirGeometryAndInputSurface()
        {
            using(var view=new View(typeof(MenuSessionHost)))
            {
                Assert.That(view.Canvas.GetComponentsInChildren<BoxCollider>(true).Length,Is.EqualTo(3));
                for(int i=0;i<3;i++)
                {
                    var card=view.Canvas.transform.Find("Candidate "+(i+1));var rect=(RectTransform)card;
                    Assert.That(rect.anchoredPosition,Is.EqualTo(new Vector2((i-1)*270,-165)));
                    Assert.That(rect.sizeDelta,Is.EqualTo(new Vector2(245,130)));
                    Assert.That(card.GetComponent<BoxCollider>().size,Is.EqualTo(new Vector3(245,130,15)));
                    var text=card.GetComponentInChildren<Text>(true);Assert.That(text.fontSize,Is.EqualTo(27));
                    Assert.That(Contrast(text.color,card.GetComponent<Image>().color),Is.GreaterThanOrEqualTo(7));
                    Assert.That(card.GetComponentsInChildren<Image>(true).Length,Is.EqualTo(1),"Already-backed candidate cards must not gain an overlay");
                }
            }
        }

        static double Contrast(Color foreground,Color background)
        {
            double Linear(float value)=>value<=.04045?value/12.92:Math.Pow((value+.055)/1.055,2.4);
            double Luminance(Color value)=>.2126*Linear(value.r)+.7152*Linear(value.g)+.0722*Linear(value.b);
            var first=Luminance(foreground);var second=Luminance(background);
            return (Math.Max(first,second)+.05)/(Math.Min(first,second)+.05);
        }

        sealed class View:IDisposable
        {
            readonly GameObject holder,lease;
            public readonly GameObject Presentation;
            public readonly Component Host;
            public readonly Canvas Canvas;
            public View(Type type)
            {
                holder=new GameObject("Inactive synthetic foundation");holder.SetActive(false);
                Presentation=new GameObject("Synthetic gated presentation");lease=new GameObject("Synthetic module lease");
                var foundation=holder.AddComponent<FoundationBootstrap>();
                var rotation=Quaternion.Euler(0,180,0);
                var config=new JObject{["observer_reference"]=new JObject{["position_m"]=new JArray(0,1.5,1.45),["rotation_xyzw"]=new JArray(rotation.x,rotation.y,rotation.z,rotation.w)}};
                typeof(FoundationBootstrap).GetField("configuration",Private).SetValue(foundation,config);
                Presentation.transform.SetPositionAndRotation(new Vector3(2,0,-3),Quaternion.Euler(0,37,0));foundation.presentationRoot=Presentation;
                Host=lease.AddComponent(type);type.GetField("foundation").SetValue(Host,foundation);type.GetField("presentationParent").SetValue(Host,Presentation.transform);type.GetField("font").SetValue(Host,Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf"));
                type.GetMethod("CreateView",Private).Invoke(Host,null);Canvas=Presentation.GetComponentInChildren<Canvas>(true);
            }
            public void Dispose(){UnityEngine.Object.DestroyImmediate(Presentation);UnityEngine.Object.DestroyImmediate(lease);UnityEngine.Object.DestroyImmediate(holder);}
        }
    }
}
