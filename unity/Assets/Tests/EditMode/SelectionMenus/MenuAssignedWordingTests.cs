using System;
using System.Collections;
using System.Linq;
using System.Reflection;
using System.Runtime.Serialization;
using AcousticVocab.Foundation;
using AcousticVocab.StudyAudio;
using AcousticVocab.Teaching;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace AcousticVocab.SelectionMenus.Tests
{
    // What each role's menu screen shows in every phase, through the real
    // MenuSessionHost view (CreateView, Prepare, Show, Hide). Synthetic DEMO
    // text and a generated image only; no XR input, audio, store or package.
    public sealed class MenuAssignedWordingTests
    {
        const BindingFlags Private=BindingFlags.Instance|BindingFlags.NonPublic;
        const string ProfileGeneral="DEMO compare the three profiles",AtomGeneral="DEMO compare the three options",ActiveChoice="DEMO choose an option",Assigned="DEMO an option is assigned";
        static readonly string[] Labels={"DEMO option 1","DEMO option 2","DEMO option 3"};
        static readonly MenuPhase[] Visible={MenuPhase.Instructions,MenuPhase.Audition,MenuPhase.Choice,MenuPhase.Selected};

        static byte[] Png()
        {var texture=new Texture2D(4,4,TextureFormat.RGBA32,false);try{return texture.EncodeToPNG();}finally{UnityEngine.Object.DestroyImmediate(texture);}}
        static TeachingDisplay Meaning(byte[] png)=>(TeachingDisplay)typeof(TeachingDisplay).GetConstructors(Private).Single().Invoke(new object[]
            {new JObject{["meaning_display_id"]="DEMO-meaning-K-a3",["definition"]="DEMO meaning definition",["action_words"]="DEMO action",["target_words"]="DEMO target",["image_id"]="DEMO-image"},png,PcmWave.Hash(png)});
        // Mirrors MenuCatalog.Prepare: both roles get the same key, meaning, shared
        // instructions, labels and options; only the choice wording follows the role.
        static MenuMaterial Material(string key,TeachingDisplay meaning,MenuOption[] options,string choiceWording)
        {bool profile=key=="profile";return new MenuMaterial(key,profile?"DEMO-profile-display":meaning.MeaningDisplayId,meaning,profile?ProfileGeneral:AtomGeneral,choiceWording,(string[])Labels.Clone(),options);}

        sealed class View:IDisposable
        {
            static readonly Type Host=typeof(MenuSessionHost);
            readonly GameObject holder,presentation,lease;
            internal readonly MenuSessionHost Menu;internal readonly Canvas Canvas;
            internal View()
            {
                holder=new GameObject("Inactive synthetic foundation");holder.SetActive(false);presentation=new GameObject("Synthetic presentation");lease=new GameObject("Synthetic menu lease");
                var foundation=holder.AddComponent<FoundationBootstrap>();
                typeof(FoundationBootstrap).GetField("configuration",Private).SetValue(foundation,new JObject{["observer_reference"]=new JObject{["position_m"]=new JArray(0,1.5,1.45),["rotation_xyzw"]=new JArray(0,0,0,1)}});
                Menu=lease.AddComponent<MenuSessionHost>();Menu.foundation=foundation;Menu.presentationParent=presentation.transform;Menu.font=Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
                Host.GetMethod("CreateView",Private).Invoke(Menu,null);Canvas=presentation.GetComponentInChildren<Canvas>(true);
                // Marks the view installed without a live module or XR input; Prepare and Show check only that.
                Host.GetField("factory",Private).SetValue(Menu,FormatterServices.GetUninitializedObject(typeof(MenuContentFactory)));
                Host.GetProperty("InputAvailable").GetSetMethod(true).Invoke(Menu,new object[]{true});
            }
            object Field(string name)=>Host.GetField(name,Private).GetValue(Menu);
            static object Read(object component,string property)=>component.GetType().GetProperty(property).GetValue(component);
            internal string Instructions=>(string)Read(Field("instructions"),"text");
            // Everything else on the screen: visibility, meaning text and image, option labels and card colours.
            internal string Rest
            {
                get
                {
                    var picture=(Component)Field("picture");var texture=(Texture)Read(picture,"texture");
                    var labels=((Array)Field("labels")).Cast<object>().Select(x=>(string)Read(x,"text"));
                    var cards=((Array)Field("cards")).Cast<object>().Select(x=>((Color)Read(x,"color")).ToString("F3"));
                    return string.Join("|",new[]{Canvas.gameObject.activeSelf.ToString(),(string)Read(Field("meaning"),"text"),picture.gameObject.activeSelf.ToString(),
                        texture==null?"no texture":texture.width+"x"+texture.height,(string)Field("visibleImage")??"no image"}.Concat(labels).Concat(cards));
                }
            }
            public void Dispose()
            {
                Host.GetField("factory",Private).SetValue(Menu,null);
                foreach(Texture2D texture in ((IDictionary)Field("textures")).Values)UnityEngine.Object.DestroyImmediate(texture);
                UnityEngine.Object.DestroyImmediate(presentation);UnityEngine.Object.DestroyImmediate(lease);UnityEngine.Object.DestroyImmediate(holder);
            }
        }

        [TestCase("profile")][TestCase("K-a3")][Category("REQ_YOKE_04"), Category("REQ_MENU_02")]
        [NUnit.Framework.Property("Requirement","REQ-YOKE-04"), NUnit.Framework.Property("Requirement","REQ-MENU-02")]
        public void AssignedRoleKeepsItsWordingInEveryPhaseAndOtherwiseSeesTheActiveScreen(string key)
        {
            bool profile=key=="profile";var meaning=profile?null:Meaning(Png());string general=profile?ProfileGeneral:AtomGeneral;
            var options=Enumerable.Range(1,3).Select(i=>new MenuOption("DEMO-"+key+"-"+i,SelectionMenuTests.Wave(profile?96000:21600,i*1000))).ToArray();
            var activeMaterial=Material(key,meaning,options,ActiveChoice);var assignedMaterial=Material(key,meaning,options,Assigned);
            using var active=new View();using var assigned=new View();active.Menu.Prepare(activeMaterial);assigned.Menu.Prepare(assignedMaterial);
            // Selected covers the selected option's two confirmation plays.
            foreach(var phase in Visible)
            {
                int? selected=phase==MenuPhase.Selected?2:(int?)null;
                active.Menu.Show("DEMO-active",activeMaterial,phase,selected,false);assigned.Menu.Show("DEMO-assigned",assignedMaterial,phase,selected,true);
                Assert.That(active.Instructions,Is.EqualTo(phase==MenuPhase.Choice?ActiveChoice:general),key+" "+phase+": the active role's text is unchanged");
                Assert.That(assigned.Instructions,Is.EqualTo(phase==MenuPhase.Choice?Assigned:Assigned+"\n"+general),key+" "+phase+": the assigned wording leads in every phase");
                Assert.That(assigned.Rest,Is.EqualTo(active.Rest),key+" "+phase+": same meaning, image, options and highlight for both roles");
                Assert.That(assigned.Menu.SimulationChoiceOwner,Is.Null,key+" "+phase+": the assigned role has no choice surface");
                Assert.That(active.Menu.SimulationChoiceOwner,Is.EqualTo(phase==MenuPhase.Choice?"DEMO-active":null),key+" "+phase);
            }
            // The blank tail (Neutral) and Ended hide both views: no text is left on either screen.
            active.Menu.Hide("DEMO-active");assigned.Menu.Hide("DEMO-assigned");
            Assert.That(active.Canvas.gameObject.activeSelf,Is.False);Assert.That(assigned.Canvas.gameObject.activeSelf,Is.False);
            Assert.That(assigned.Rest,Is.EqualTo(active.Rest),key+": both views hidden alike");
        }
    }
}
