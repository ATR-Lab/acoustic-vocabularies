using System;
using UnityEngine.XR.OpenXR.Features;
#if UNITY_EDITOR
using UnityEditor;
using UnityEditor.XR.OpenXR.Features;
#endif

namespace AcousticVocab.Spikes.OpenXR
{
#if UNITY_EDITOR
    [OpenXRFeature(UiName = "Spike session state observer", BuildTargetGroups = new[] { BuildTargetGroup.Android, BuildTargetGroup.Standalone },
        Company = "Acoustic vocabularies", Desc = "Records OpenXR session state callbacks without changing the session.",
        Version = "0.1.0", FeatureId = "org.acousticvocab.spike.session-state")]
#endif
    public sealed class SessionStateProbe : OpenXRFeature
    {
        public static event Action<int, int> StateChanged;
        protected override void OnSessionStateChange(int oldState, int newState) => StateChanged?.Invoke(oldState, newState);
    }
}
