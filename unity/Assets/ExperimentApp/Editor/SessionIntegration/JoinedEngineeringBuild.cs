using System.IO;
using AcousticVocab.Foundation;
using AcousticVocab.Foundation.Editor;
using AcousticVocab.FrameBudget;
using AcousticVocab.ResponsePanel.Editor;
using AcousticVocab.StateIntegration;
using AcousticVocab.StateIntegration.Editor;
using AcousticVocab.StudyAudio;
using AcousticVocab.Orientation;
using AcousticVocab.Workcell;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
namespace AcousticVocab.SessionIntegration.Editor
{
    public static class JoinedEngineeringBuild
    {
        public const string ScenePath="Assets/Generated.local.data/SessionIntegration/JoinedEngineering.unity";
        public static void Configure()
        {
            StateSourceBuild.Configure();var foundation=Object.FindAnyObjectByType<FoundationBootstrap>();var panel=ResponsePanelBuild.AddToOpenScene();
            var player=new GameObject("Visit audio owner",typeof(AudioSource),typeof(AudioPlayer)).GetComponent<AudioPlayer>();
            var frames=foundation.gameObject.AddComponent<FrameCaptureHost>();frames.foundation=foundation;frames.panel=panel;
            var owner=foundation.gameObject.AddComponent<JoinedEngineeringBootstrap>();owner.foundation=foundation;owner.source=foundation.GetComponent<StateSourceHost>();owner.panel=panel;owner.player=player;owner.frames=frames;
            owner.font=Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");owner.unlitShader=Shader.Find("Unlit/Color");owner.dictionaryShader=Shader.Find("Unlit/Texture");
            foundation.gameObject.AddComponent<JoinedSoakCapture>().bootstrap=owner;
            Directory.CreateDirectory(Path.GetDirectoryName(ScenePath));EditorSceneManager.SaveScene(UnityEngine.SceneManagement.SceneManager.GetActiveScene(),ScenePath);
            FoundationBuild.ParticipantScenePath=ScenePath;AssetDatabase.SaveAssets();FoundationBuild.VerifyParticipantScene();
            Debug.Log("JOINED_ENGINEERING_SCENE_CONFIGURED participant_admission=false config_and_authority_required=true");
        }
        public static void BuildWindows(){Configure();FoundationBuild.BuildWindows();}
        public static void BuildAndroid(){Configure();FoundationBuild.BuildAndroid();}
        public static void ConfigureSimulation()
        {Configure();var owner=Object.FindAnyObjectByType<JoinedEngineeringBootstrap>();owner.simulationTestScene=true;EditorSceneManager.SaveScene(UnityEngine.SceneManagement.SceneManager.GetActiveScene(),ScenePath);AssetDatabase.SaveAssets();}
        public static void BuildSimulationWindows(){ConfigureSimulation();FoundationBuild.SimulationTestBuild=true;try{FoundationBuild.BuildWindows();}finally{FoundationBuild.SimulationTestBuild=false;}}
    }
    public static class PreallocationBuild
    {
        public const string ScenePath="Assets/Generated.local.data/SessionIntegration/PreallocationEngineering.unity";
        public static void Configure()
        {
            JoinedEngineeringBuild.Configure();var joined=Object.FindAnyObjectByType<JoinedEngineeringBootstrap>();
            joined.requirePreallocation=true;joined.enabled=false;
            var orientation=joined.gameObject.AddComponent<OrientationHost>();orientation.foundation=joined.foundation;orientation.stateSource=joined.source;orientation.panel=joined.panel;
            orientation.workcell=Object.FindAnyObjectByType<WorkcellRegistry>(FindObjectsInactive.Include);orientation.font=joined.font;orientation.shader=joined.unlitShader;
            var entry=joined.gameObject.AddComponent<PreallocationEngineeringHost>();entry.joined=joined;entry.orientation=orientation;
            EditorSceneManager.SaveScene(UnityEngine.SceneManagement.SceneManager.GetActiveScene(),ScenePath);FoundationBuild.ParticipantScenePath=ScenePath;
            AssetDatabase.SaveAssets();FoundationBuild.VerifyParticipantScene();Debug.Log("PREALLOCATION_SCENE_CONFIGURED package_loader_disabled=true participant_admission=false");
        }
        public static void BuildWindows(){Configure();FoundationBuild.BuildWindows();}
        public static void BuildAndroid(){Configure();FoundationBuild.BuildAndroid();}
        public static void ConfigureSimulation()
        {Configure();var owner=Object.FindAnyObjectByType<JoinedEngineeringBootstrap>();owner.simulationTestScene=true;EditorSceneManager.SaveScene(UnityEngine.SceneManagement.SceneManager.GetActiveScene(),ScenePath);AssetDatabase.SaveAssets();}
        public static void BuildSimulationWindows(){ConfigureSimulation();FoundationBuild.SimulationTestBuild=true;try{FoundationBuild.BuildWindows();}finally{FoundationBuild.SimulationTestBuild=false;}}
    }
}
