using System.IO;
using AcousticVocab.Foundation;
using AcousticVocab.Foundation.Editor;
using AcousticVocab.Workcell;
using AcousticVocab.Workcell.Editor;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace AcousticVocab.StateIntegration.Editor
{
    public static class StateSourceBuild
    {
        public const string ScenePath="Assets/Generated.local.data/StateSources/StateSources.unity";
        public static void Configure()
        {
            WorkcellBuild.Configure();
            var foundation=Object.FindAnyObjectByType<FoundationBootstrap>();
            var registry=Object.FindAnyObjectByType<WorkcellRegistry>(FindObjectsInactive.Include);
            var host=foundation.gameObject.AddComponent<StateSourceHost>();
            host.foundation=foundation;host.workcell=registry;
            Directory.CreateDirectory(Path.GetDirectoryName(ScenePath));
            EditorSceneManager.SaveScene(UnityEngine.SceneManagement.SceneManager.GetActiveScene(),ScenePath);
            FoundationBuild.ParticipantScenePath=ScenePath;
            AssetDatabase.SaveAssets(); FoundationBuild.VerifyParticipantScene();
            Debug.Log("STATE_SOURCES_CONFIGURED objects=60 joints=43");
        }
        public static void BuildAndroid() { Configure();FoundationBuild.BuildAndroid(); }
        public static void BuildWindows() { Configure();FoundationBuild.BuildWindows(); }
    }
}
