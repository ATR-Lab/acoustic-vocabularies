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

namespace AcousticVocab.Assessment.Editor
{
    public static class AssessmentBuild
    {
        public const string ScenePath="Assets/Generated.local.data/Assessment/Assessment.unity";
        public static void Configure()
        {
            StateSourceBuild.Configure();var foundation=Object.FindAnyObjectByType<FoundationBootstrap>();var panel=ResponsePanelBuild.AddToOpenScene();
            var audio=new GameObject("Verified assessment audio",typeof(AudioSource),typeof(AudioPlayer));var screen=foundation.gameObject.AddComponent<AssessmentScreen>();
            screen.foundation=foundation;screen.trackingSpace=foundation.observerCamera.transform.parent;screen.inputSource=panel;
            screen.font=Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");screen.unlitShader=Shader.Find("Unlit/Color");screen.dictionaryShader=Shader.Find("Unlit/Texture");
            var host=foundation.gameObject.AddComponent<AssessmentSessionHost>();host.foundation=foundation;host.source=foundation.GetComponent<StateSourceHost>();
            host.panel=panel;host.player=audio.GetComponent<AudioPlayer>();host.screen=screen;
            Directory.CreateDirectory(Path.GetDirectoryName(ScenePath));EditorSceneManager.SaveScene(UnityEngine.SceneManagement.SceneManager.GetActiveScene(),ScenePath);
            FoundationBuild.ParticipantScenePath=ScenePath;AssetDatabase.SaveAssets();FoundationBuild.VerifyParticipantScene();
            Debug.Log("ASSESSMENT_SCENE_CONFIGURED admission=required semantic_content=absent");
        }
        public static void BuildWindows(){Configure();FoundationBuild.BuildWindows();}
        public static void BuildAndroid(){Configure();FoundationBuild.BuildAndroid();}
    }
}
