using System.IO;
using AcousticVocab.Foundation;
using AcousticVocab.Foundation.Editor;
using AcousticVocab.FrameBudget;
using AcousticVocab.ResponsePanel.Editor;
using AcousticVocab.StateIntegration;
using AcousticVocab.StateIntegration.Editor;
using AcousticVocab.StudyAudio;
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
            Directory.CreateDirectory(Path.GetDirectoryName(ScenePath));EditorSceneManager.SaveScene(UnityEngine.SceneManagement.SceneManager.GetActiveScene(),ScenePath);
            FoundationBuild.ParticipantScenePath=ScenePath;AssetDatabase.SaveAssets();FoundationBuild.VerifyParticipantScene();
            Debug.Log("JOINED_ENGINEERING_SCENE_CONFIGURED participant_admission=false config_and_authority_required=true");
        }
        public static void BuildWindows(){Configure();FoundationBuild.BuildWindows();}
        public static void BuildAndroid(){Configure();FoundationBuild.BuildAndroid();}
    }
}
