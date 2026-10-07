using System.IO;
using AcousticVocab.Foundation;
using AcousticVocab.Foundation.Editor;
using AcousticVocab.Assessment.Editor;
using AcousticVocab.ResponsePanel;
using AcousticVocab.ResponsePanel.Editor;
using AcousticVocab.StateIntegration;
using AcousticVocab.StateIntegration.Editor;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
namespace AcousticVocab.FrameBudget.Editor
{
    public static class FrameBudgetBuild
    {
        public const string ScenePath="Assets/Generated.local.data/FrameBudget/FrameBudget.unity";
        public const string ProbePath="Assets/Generated.local.data/FrameBudget/EngineeringProbe.unity";
        public static void Configure(){AssessmentBuild.Configure();var f=Object.FindAnyObjectByType<FoundationBootstrap>();var capture=f.gameObject.AddComponent<FrameCaptureHost>();capture.foundation=f;capture.panel=f.GetComponent<ResponsePanelController>();Save(ScenePath);}
        public static void ConfigureProbe(){StateSourceBuild.Configure();var f=Object.FindAnyObjectByType<FoundationBootstrap>();var panel=ResponsePanelBuild.AddToOpenScene();var probe=f.gameObject.AddComponent<FrameEngineeringProbe>();probe.foundation=f;probe.source=f.GetComponent<StateSourceHost>();probe.panel=panel;Save(ProbePath);}
        static void Save(string path){Directory.CreateDirectory(Path.GetDirectoryName(path));EditorSceneManager.SaveScene(UnityEngine.SceneManagement.SceneManager.GetActiveScene(),path);FoundationBuild.ParticipantScenePath=path;AssetDatabase.SaveAssets();FoundationBuild.VerifyParticipantScene();}
        public static void BuildWindows(){Configure();FoundationBuild.BuildWindows();}
        public static void BuildAndroid(){Configure();FoundationBuild.BuildAndroid();}
        public static void BuildProbeWindows(){ConfigureProbe();FoundationBuild.BuildWindows();}
    }
}
