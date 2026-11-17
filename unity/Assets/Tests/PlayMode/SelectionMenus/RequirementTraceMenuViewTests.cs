using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.Serialization;
using System.Text;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using NUnit.Framework;
using UnityEngine;

namespace AcousticVocab.SelectionMenus.PlayModeTests
{
    // Requirement trace for what each role's menu screen shows. The real
    // MenuSessionHost.Show runs against synthetic UI components; no XR input,
    // audio, store or package is involved and no frame elapses.
    public sealed class RequirementTraceMenuViewTests
    {
        const BindingFlags Private=BindingFlags.Instance|BindingFlags.NonPublic;
        const string General="DEMO general menu instructions",ActiveChoice="DEMO choose a sound",Assigned="DEMO this sound is assigned to you";
        static readonly string[] Labels={"DEMO option 1","DEMO option 2","DEMO option 3"};
        static readonly MenuPhase[] Visible={MenuPhase.Instructions,MenuPhase.Audition,MenuPhase.Choice,MenuPhase.Selected};

        static PcmWave Tone(int amplitude)
        {
            const int samples=21600;using var stream=new MemoryStream();using var writer=new BinaryWriter(stream);
            writer.Write(Encoding.ASCII.GetBytes("RIFF"));writer.Write(36+samples*2);writer.Write(Encoding.ASCII.GetBytes("WAVEfmt "));writer.Write(16);writer.Write((short)1);writer.Write((short)1);writer.Write(48000);writer.Write(96000);writer.Write((short)2);writer.Write((short)16);writer.Write(Encoding.ASCII.GetBytes("data"));writer.Write(samples*2);
            for(int i=0;i<samples;i++)writer.Write((short)(i%100<50?amplitude:-amplitude));writer.Flush();return PcmWave.ParseCanonical(stream.ToArray());
        }
        // Mirrors MenuCatalog.Prepare: both roles get the same options, labels and
        // general instructions; only the choice wording depends on the role.
        static MenuMaterial Material(string choiceWording)
        {
            var options=Enumerable.Range(1,3).Select(i=>new MenuOption("DEMO-K-a3-"+i,Tone(i*1000))).ToArray();
            var constructor=typeof(MenuMaterial).GetConstructors(Private).Single();
            return (MenuMaterial)constructor.Invoke(new object[]{"K-a3","DEMO-meaning-K-a3",null,General,choiceWording,(string[])Labels.Clone(),options});
        }

        sealed class View:IDisposable
        {
            readonly GameObject root,canvasObject;readonly Type type=typeof(MenuSessionHost);readonly Component instructions;readonly Array labels;
            internal readonly MenuSessionHost Host;
            internal View()
            {
                canvasObject=new GameObject("Synthetic menu canvas",typeof(RectTransform),typeof(Canvas));canvasObject.SetActive(false);
                root=new GameObject("Synthetic menu host");Host=root.AddComponent<MenuSessionHost>();
                var textType=type.GetField("instructions",Private).FieldType;
                instructions=Child(textType,"Instructions");type.GetField("instructions",Private).SetValue(Host,instructions);
                type.GetField("meaning",Private).SetValue(Host,Child(textType,"Meaning"));
                type.GetField("picture",Private).SetValue(Host,Child(type.GetField("picture",Private).FieldType,"Meaning image"));
                labels=(Array)type.GetField("labels",Private).GetValue(Host);var cards=(Array)type.GetField("cards",Private).GetValue(Host);
                for(int i=0;i<3;i++){labels.SetValue(Child(textType,"Stored label "+i),i);cards.SetValue(Child(cards.GetType().GetElementType(),"Candidate "+(i+1)),i);}
                type.GetField("canvas",Private).SetValue(Host,canvasObject.GetComponent<Canvas>());
                // Marks the host installed without a live module; Show checks only that.
                var factory=type.GetField("factory",Private);factory.SetValue(Host,FormatterServices.GetUninitializedObject(factory.FieldType));
                type.GetProperty("InputAvailable").GetSetMethod(true).Invoke(Host,new object[]{true});
            }
            Component Child(Type component,string name){var node=new GameObject(name,typeof(RectTransform));node.transform.SetParent(canvasObject.transform,false);return node.AddComponent(component);}
            static string Text(Component value)=>(string)value.GetType().GetProperty("text").GetValue(value);
            internal string Instructions=>Text(instructions);
            internal string[] Labels=>Enumerable.Range(0,3).Select(i=>Text((Component)labels.GetValue(i))).ToArray();
            public void Dispose()
            {
                type.GetField("factory",Private).SetValue(Host,null);
                UnityEngine.Object.DestroyImmediate(canvasObject);UnityEngine.Object.DestroyImmediate(root);
            }
        }

        [Test, Category("REQ_YOKE_04"), Category("REQ_MENU_02")]
        public void AssignedRoleSeesTheSameMenuWithoutAChoiceSurface()
        {
            using var view=new View();var active=Material(ActiveChoice);var assigned=Material(Assigned);
            view.Host.Show("DEMO-active",active,MenuPhase.Choice,null,false);
            Assert.That(view.Host.SimulationChoiceOwner,Is.EqualTo("DEMO-active"),"The active member's choice surface is live");
            Assert.That(view.Labels,Is.EqualTo(Labels));
            view.Host.Show("DEMO-assigned",assigned,MenuPhase.Choice,null,true);
            Assert.That(view.Host.SimulationChoiceOwner,Is.Null,"The assigned member's screen has no choice surface");
            Assert.That(view.Labels,Is.EqualTo(Labels),"Same options for both roles");
            Assert.That(view.Instructions,Is.EqualTo(Assigned));
            Assert.That(Assert.Throws<SessionFault>(()=>view.Host.Show("DEMO-assigned",assigned,MenuPhase.Choice,2,true)).Code,Is.EqualTo("MENU_YOKED_DISCLOSURE"),"No pending choice is shown to the assigned member");
        }

        [Test, Category("REQ_YOKE_04")]
        [Ignore("Requirement gap: the assigned-role wording is shown only in the choice phase; other phases show the general instructions")]
        public void AssignedWordingStaysOnScreenThroughEveryMenuPhase()
        {
            using var view=new View();var assigned=Material(Assigned);
            foreach(var phase in Visible)
            {
                view.Host.Show("DEMO-assigned",assigned,phase,phase==MenuPhase.Selected?2:(int?)null,true);
                Assert.That(view.Instructions,Does.Contain(Assigned),phase.ToString());
            }
        }
    }
}
