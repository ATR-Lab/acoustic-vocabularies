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

namespace AcousticVocab.Teaching.Editor
{
    public static class TeachingBuild
    {
        public const string ScenePath="Assets/Generated.local.data/Teaching/Teaching.unity";
        public static void Configure()
        {
            StateSourceBuild.Configure();
            var foundation=Object.FindAnyObjectByType<FoundationBootstrap>();
            var panel=ResponsePanelBuild.AddToOpenScene();var source=foundation.GetComponent<StateSourceHost>();
            var audioRoot=new GameObject("Verified teaching audio",typeof(AudioSource),typeof(AudioPlayer));
            var host=foundation.gameObject.AddComponent<TeachingSessionHost>();host.foundation=foundation;host.panel=panel;host.source=source;
            host.player=audioRoot.GetComponent<AudioPlayer>();host.presentationParent=foundation.observerCamera.transform.parent;
            host.font=Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            Directory.CreateDirectory(Path.GetDirectoryName(ScenePath));EditorSceneManager.SaveScene(UnityEngine.SceneManagement.SceneManager.GetActiveScene(),ScenePath);
            FoundationBuild.ParticipantScenePath=ScenePath;AssetDatabase.SaveAssets();FoundationBuild.VerifyParticipantScene();
            Debug.Log("TEACHING_SCENE_CONFIGURED admission=required semantic_content=absent");
        }
        public static void BuildWindows(){Configure();FoundationBuild.BuildWindows();}
        public static void BuildAndroid(){Configure();FoundationBuild.BuildAndroid();}
    }
}
