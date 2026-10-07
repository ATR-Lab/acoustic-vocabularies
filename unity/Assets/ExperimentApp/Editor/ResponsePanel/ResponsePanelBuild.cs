using System;
using System.IO;
using AcousticVocab.Foundation;
using AcousticVocab.Foundation.Editor;
using AcousticVocab.Workcell.Editor;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace AcousticVocab.ResponsePanel.Editor
{
    public static class ResponsePanelBuild
    {
        public const string ScenePath = "Assets/Generated.local.data/ResponsePanel/ResponsePanel.unity";
        public static void VerifySchema()
        {
            if (File.ReadAllText("Assets/ExperimentApp/Resources/ResponsePanelSchema.json") != File.ReadAllText(Path.Combine(FoundationBuild.RepositoryRoot, "apparatus/response-panel/response-panel.schema.json")))
                throw new InvalidDataException("Embedded response panel schema differs from canonical contract");
        }
        public static void Configure()
        {
            VerifySchema(); WorkcellBuild.Configure(); AddToOpenScene();
            Directory.CreateDirectory(Path.GetDirectoryName(ScenePath));
            EditorSceneManager.SaveScene(UnityEngine.SceneManagement.SceneManager.GetActiveScene(), ScenePath);
            FoundationBuild.ParticipantScenePath = ScenePath; AssetDatabase.SaveAssets(); FoundationBuild.VerifyParticipantScene();
        }
        public static ResponsePanelController AddToOpenScene()
        {
            var foundation = UnityEngine.Object.FindAnyObjectByType<FoundationBootstrap>();
            if (foundation == null) throw new InvalidOperationException("Foundation scene required");
            var controller = foundation.GetComponent<ResponsePanelController>() ?? foundation.gameObject.AddComponent<ResponsePanelController>();
            controller.foundation = foundation; controller.trackingSpace = foundation.observerCamera.transform.parent;
            controller.panelShader = Shader.Find("Unlit/Color"); controller.panelFont = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            if (controller.panelShader == null || controller.panelFont == null) throw new InvalidOperationException("Pinned built-in panel rendering resources unavailable");
            EditorUtility.SetDirty(controller); return controller;
        }
        public static void BuildAndroid() { Configure(); FoundationBuild.BuildAndroid(); }
        public static void BuildWindows() { Configure(); FoundationBuild.BuildWindows(); }
    }
}
