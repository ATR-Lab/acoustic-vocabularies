using System.IO;
using AcousticVocab.Foundation;
using AcousticVocab.Foundation.Editor;
using AcousticVocab.ResponsePanel.Editor;
using AcousticVocab.StateIntegration;
using AcousticVocab.StateIntegration.Editor;
using AcousticVocab.StudyAudio;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace AcousticVocab.ViewCapture.Editor
{
    // Separate O6.1.2 capture scene: foundation, workcell, state source, the
    // fixed response panel and the inert shared audio owner, plus the capture
    // host. No session engine, schedule, demo path or participant admission.
    public static class ViewCaptureBuild
    {
        public const string ScenePath="Assets/Generated.local.data/ViewCapture/ViewCapture.unity";
        public static void Configure()
        {
            StateSourceBuild.Configure();var foundation=Object.FindAnyObjectByType<FoundationBootstrap>();var panel=ResponsePanelBuild.AddToOpenScene();
            new GameObject("Visit audio owner",typeof(AudioSource),typeof(AudioPlayer));
            var host=foundation.gameObject.AddComponent<ViewCaptureHost>();host.foundation=foundation;host.source=foundation.GetComponent<StateSourceHost>();host.panel=panel;
            Directory.CreateDirectory(Path.GetDirectoryName(ScenePath));EditorSceneManager.SaveScene(UnityEngine.SceneManagement.SceneManager.GetActiveScene(),ScenePath);
            FoundationBuild.ParticipantScenePath=ScenePath;AssetDatabase.SaveAssets();FoundationBuild.VerifyParticipantScene();
            Debug.Log("VIEW_CAPTURE_SCENE_CONFIGURED participant_admission=false simulation_capability_required=true");
        }
        // Windows-only and always compiled with AV_SIMULATION_TEST; ordinary
        // builds exclude the capture assembly through its define constraint.
        public static void BuildWindows(){Configure();FoundationBuild.SimulationTestBuild=true;try{FoundationBuild.BuildWindows();}finally{FoundationBuild.SimulationTestBuild=false;}}
    }
}
