using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace AcousticVocab.Spikes.Input.Editor
{
    public static class InputSpikeSetup
    {
        [MenuItem("Spikes/Input/Add to open seated scene")]
        public static void AddToOpenScene()
        {
            ValidateLegality();
            var origin = GameObject.Find("SeatedOrigin");
            if (origin == null) throw new System.InvalidOperationException("Open the #45 seated scene first");
            var spike = origin.GetComponent<InputLegibilitySpike>() ?? origin.AddComponent<InputLegibilitySpike>();
            spike.trackingSpace = origin.transform.Find("CameraOffset");
            if (spike.trackingSpace == null) throw new System.InvalidOperationException("CameraOffset tracking space missing");
            EditorUtility.SetDirty(spike);
            EditorSceneManager.MarkSceneDirty(origin.scene);
            EditorSceneManager.SaveScene(origin.scene);
        }
        [MenuItem("Spikes/Input/Validate legality model")]
        public static void ValidateLegality() { PanelState.VerifyLegality(); Debug.Log("PASS: 32 legal commands; no preselection; family change clears action."); }
    }
}
