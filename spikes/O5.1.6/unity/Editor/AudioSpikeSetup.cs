using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace AcousticVocab.Spikes.Audio.Editor
{
    public static class AudioSpikeSetup
    {
        [MenuItem("Spikes/Audio/Add to open seated scene")]
        public static void AddToOpenScene()
        {
            const string path = "Assets/Spikes/Audio/Resources/public-click.wav";
            AssetDatabase.Refresh();
            var importer = AssetImporter.GetAtPath(path) as AudioImporter;
            if (importer == null) throw new System.InvalidOperationException("Generate the public test WAV first; see runbook.");
            var sample = importer.defaultSampleSettings;
            sample.loadType = AudioClipLoadType.DecompressOnLoad;
            sample.compressionFormat = AudioCompressionFormat.PCM;
            sample.sampleRateSetting = AudioSampleRateSetting.PreserveSampleRate;
            importer.defaultSampleSettings = sample;
            importer.forceToMono = true; importer.preloadAudioData = true; importer.loadInBackground = false;
            importer.SaveAndReimport();
            var origin = GameObject.Find("SeatedOrigin");
            if (origin == null) throw new System.InvalidOperationException("Open the #45 seated workcell scene first.");
            var spike = origin.GetComponent<AudioOnsetSpike>() ?? origin.AddComponent<AudioOnsetSpike>();
            spike.click = AssetDatabase.LoadAssetAtPath<AudioClip>(path);
            spike.observer = Camera.main;
            EditorUtility.SetDirty(spike);
            EditorSceneManager.MarkSceneDirty(origin.scene);
            EditorSceneManager.SaveScene(origin.scene);
        }
    }
}
