using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.Serialization;
using AcousticVocab.Foundation;
using AcousticVocab.Foundation.Editor;
using AcousticVocab.ResponsePanel.Editor;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace AcousticVocab.ResponsePanel.Tests
{
    // Native attempt simulation-test-B-V1-021-full-013 staged 1.0 deg glyphs on the
    // default 0.135 m buttons. Atomic panels fit, but at message lesson 3 the first
    // Command-role target selection made Refresh measure the second-row action
    // labels, which did not fit; the exception escaped SimulationResponse before
    // ADD_ONE was pressed and the owner reported JOIN_RUNTIME_FAILED. A participant
    // would have faulted the panel at the same selection. These run the real
    // controller: the geometry is now refused when the panel is created, and every
    // scheduled panel completes at geometries that fit.
    public sealed class SimulatedPanelInputTests
    {
        const BindingFlags Flags=BindingFlags.NonPublic|BindingFlags.Instance;
        double now;
        ResponsePanelController Build(double textAngle,double buttonWidth,bool rootActive=false)
        {
            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene,NewSceneMode.Single);
            var root=new GameObject("SyntheticPanelRoot");var foundation=root.AddComponent<FoundationBootstrap>();
            foundation.presentationRoot=new GameObject("SyntheticPresentation");foundation.presentationRoot.transform.SetParent(root.transform);
            // The player creates the panel before the foundation shows its presentation root.
            foundation.presentationRoot.SetActive(rootActive);
            var camera=new GameObject("SyntheticObserver").AddComponent<Camera>();camera.transform.SetParent(root.transform);foundation.observerCamera=camera;foundation.seatedOrigin=root.transform;
            var config=StationConfig.ParseStrict(File.ReadAllText(Path.Combine(FoundationBuild.RepositoryRoot,"apparatus/examples/station.example.json")));
            config["observer_reference"]["position_m"]=new JArray(0,1.5,1.45);config["observer_reference"]["rotation_xyzw"]=new JArray(0,1,0,0);
            typeof(FoundationBootstrap).GetField("configuration",Flags).SetValue(foundation,config);typeof(FoundationBootstrap).GetProperty("Ready").SetValue(foundation,true);
            var source=StationConfig.ParseStrict(File.ReadAllText(Path.Combine(FoundationBuild.RepositoryRoot,"apparatus/response-panel/response-panel.example.json")));
            source["configuration_status"]="provisioned_engineering";source["text_angle_deg"]=textAngle;source["button_width_m"]=buttonWidth;
            var settings=PanelSettings.Parse(source.ToString(),File.ReadAllText("Assets/ExperimentApp/Resources/ResponsePanelSchema.json"),"engineering-pending-review");
            var controller=ResponsePanelBuild.AddToOpenScene();typeof(ResponsePanelController).GetField("settings",Flags).SetValue(controller,settings);
            typeof(ResponsePanelController).GetProperty("State").SetValue(controller,new ResponseState(()=>now,_=>{}));
            try{typeof(ResponsePanelController).GetMethod("CreatePresentation",Flags).Invoke(controller,new object[]{StationConfig.ReferencePose(config)});}
            catch(TargetInvocationException error){throw error.InnerException;}
            foundation.presentationRoot.SetActive(true);typeof(ResponsePanelController).GetProperty("InputAvailable").SetValue(controller,true);
            return controller;
        }
        static SimulationTestAuthority Authority=>(SimulationTestAuthority)FormatterServices.GetUninitializedObject(typeof(SimulationTestAuthority));
        static readonly object[] Fitting={new object[]{.6,.135},new object[]{1.0,.18}}; // example default; documented provisional common geometry
        static readonly (PanelMode mode,PanelRole role,string response)[] Scheduled=
        {
            // Every panel a B V1 visit uses: atomic and message lessons, trained and
            // novel full messages, and atomic assessment probes.
            (PanelMode.LessonAtomic,PanelRole.Action,"commit"),(PanelMode.LessonAtomic,PanelRole.Target,"commit"),(PanelMode.LessonAtomic,PanelRole.Action,"dont_know"),(PanelMode.LessonAtomic,PanelRole.Target,"timeout"),
            (PanelMode.LessonMessage,PanelRole.Command,"commit"),(PanelMode.LessonMessage,PanelRole.Command,"dont_know"),(PanelMode.LessonMessage,PanelRole.Command,"timeout"),
            (PanelMode.FullMessage,PanelRole.Command,"commit"),(PanelMode.FullMessage,PanelRole.Command,"dont_know"),(PanelMode.FullMessage,PanelRole.Command,"timeout"),
            (PanelMode.AtomicProbe,PanelRole.Action,"commit"),(PanelMode.AtomicProbe,PanelRole.Target,"commit"),(PanelMode.AtomicProbe,PanelRole.Action,"dont_know"),(PanelMode.AtomicProbe,PanelRole.Target,"timeout"),
        };
        static System.Collections.Generic.IEnumerable<TestCaseData> ScheduledCases()
        {
            foreach(object[] geometry in Fitting)foreach(var item in Scheduled)foreach(string target in new[]{"A","H"})
                yield return new TestCaseData(geometry[0],geometry[1],item.mode,item.role,item.response,target);
        }
        [TestCaseSource(nameof(ScheduledCases))]
        public void SimulatedResponseCompletesThroughTheRealControllerForEveryScheduledPanel(double angle,double width,PanelMode mode,PanelRole role,string response,string target)
        {
            try
            {
                var panel=Build(angle,width);var request=new PanelRequest("B-C01-M1-V1-SIM",mode,role,1000);now=request.OpensMonoMs;panel.Open(request);
                now=request.OpensMonoMs+1000;
                string action=PublicCommands.Actions[PublicCommands.Family(target)*4+1]; // REMOVE_ONE or TAG: a second-row action
                if(response=="timeout"){now=request.DeadlineMonoMs;panel.State.Tick();}
                else Assert.That(panel.SimulationResponse(Authority,response,target,action),Is.True,"The simulated input locks the real panel");
                var result=panel.State.Result;Assert.That(result,Is.Not.Null);Assert.That(panel.FaultLatched,Is.False);
                Assert.That(result.Code,Is.EqualTo(response=="commit"?ResponseCode.Commit:response=="dont_know"?ResponseCode.DontKnow:ResponseCode.Timeout));
                if(response=="commit"){Assert.That(result.Target,Is.EqualTo(role==PanelRole.Action?null:target));Assert.That(result.Action,Is.EqualTo(role==PanelRole.Target?null:action));}
            }
            finally{EditorSceneManager.NewScene(NewSceneSetup.EmptyScene,NewSceneMode.Single);}
        }
        [Test]public void CommandPanelShowsEveryActionFamilyAtFittingGeometries([Values(0,1)]int geometryIndex,[Values("A","B","C","D","E","F","G","H")]string target)
        {
            try
            {
                var geometry=(object[])Fitting[geometryIndex];var panel=Build((double)geometry[0],(double)geometry[1]);var request=new PanelRequest("B-C01-M1-V1-SIM",PanelMode.FullMessage,PanelRole.Command,1000);now=request.OpensMonoMs;panel.Open(request);
                panel.State.SelectTarget(target);Assert.DoesNotThrow(()=>typeof(ResponsePanelController).GetMethod("Refresh",Flags).Invoke(panel,null));
                var visible=panel.foundation.presentationRoot.GetComponentsInChildren<TextMesh>().Where(x=>x.text.Length>0).ToArray();
                Assert.That(visible.Length,Is.EqualTo(14));Assert.That(visible.All(x=>x.transform.localScale.x>0&&x.transform.localScale.x!=1&&float.IsFinite(x.transform.localScale.x)),Is.True,"Every label is scaled to the configured angle");
                var actions=PublicCommands.Actions.Skip(PublicCommands.Family(target)*4).Take(4).Select(x=>x.Replace('_',' '));
                Assert.That(visible.Select(x=>x.text),Is.SupersetOf(actions));
            }
            finally{EditorSceneManager.NewScene(NewSceneSetup.EmptyScene,NewSceneMode.Single);}
        }
        [Test]public void AttemptGeometryIsRefusedWhenThePanelIsCreated([Values(false,true)]bool rootActive)
        {
            try
            {
                var fault=Assert.Throws<ConfigurationFault>(()=>Build(1.0,.135,rootActive));
                Assert.That(fault.Message,Is.EqualTo("panel_glyph_does_not_fit"),"Refused before any session, not at the first Command selection");
            }
            finally{EditorSceneManager.NewScene(NewSceneSetup.EmptyScene,NewSceneMode.Single);}
        }
    }
}
