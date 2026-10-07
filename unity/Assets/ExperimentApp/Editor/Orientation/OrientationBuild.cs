using System.IO;
using AcousticVocab.Foundation;
using AcousticVocab.Foundation.Editor;
using AcousticVocab.ResponsePanel.Editor;
using AcousticVocab.StateIntegration;
using AcousticVocab.StateIntegration.Editor;
using AcousticVocab.Workcell;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace AcousticVocab.Orientation.Editor
{
    public static class OrientationBuild
    {
        public const string ScenePath="Assets/Generated.local.data/Orientation/Orientation.unity";
        public static void Configure()
        {
            StateSourceBuild.Configure();var panel=ResponsePanelBuild.AddToOpenScene();
            var foundation=Object.FindAnyObjectByType<FoundationBootstrap>();var host=foundation.gameObject.AddComponent<OrientationHost>();
            host.foundation=foundation;host.stateSource=foundation.GetComponent<StateSourceHost>();host.workcell=Object.FindAnyObjectByType<WorkcellRegistry>(FindObjectsInactive.Include);host.panel=panel;host.font=panel.panelFont;host.shader=panel.panelShader;
            Directory.CreateDirectory(Path.GetDirectoryName(ScenePath));EditorSceneManager.SaveScene(UnityEngine.SceneManagement.SceneManager.GetActiveScene(),ScenePath);
            FoundationBuild.ParticipantScenePath=ScenePath;AssetDatabase.SaveAssets();FoundationBuild.VerifyParticipantScene();
            Debug.Log("ORIENTATION_CONFIGURED silent=true actual_demo_assets_required=true");
        }
        public static void BuildAndroid() { Configure();FoundationBuild.BuildAndroid(); }
        public static void BuildWindows() { Configure();FoundationBuild.BuildWindows(); }
    }
}
