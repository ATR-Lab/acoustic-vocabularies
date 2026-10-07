using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using AcousticVocab.Spikes.Urdf;

namespace AcousticVocab.Spikes.Bridge.Editor
{
    public static class BridgeSpikeBuild
    {
        const string Preview = "Assets/Generated.local.data/G1/G1Preview.unity";
        const string Scene = "Assets/Generated.local.data/Bridge/BridgeComparison.unity";
        static string ConfigPath()
        {
            var args = Environment.GetCommandLineArgs(); int index = Array.IndexOf(args, "-bridgeConfig");
            string path = index >= 0 && index + 1 < args.Length ? args[index + 1] : Environment.GetEnvironmentVariable("BRIDGE_CONFIG_JSON");
            if (string.IsNullOrEmpty(path) || !File.Exists(path)) throw new InvalidOperationException("Provide private -bridgeConfig JSON or BRIDGE_CONFIG_JSON");
            return path;
        }
        [MenuItem("Spikes/Bridge/Configure isolated comparison scene")]
        public static void Configure()
        {
            var settings = JsonUtility.FromJson<BridgeBenchmark.Settings>(File.ReadAllText(ConfigPath()));
            EditorSceneManager.OpenScene(Preview);
            var robot = UnityEngine.Object.FindFirstObjectByType<RobotHierarchy>();
            if (robot == null || settings.canonical_joint_names == null ||
                !settings.canonical_joint_names.OrderBy(x => x).SequenceEqual(robot.joints.Select(x => x.name).OrderBy(x => x)))
                throw new InvalidOperationException("Canonical map must exactly match imported G1 renderer");
            var previous = GameObject.Find("BridgeComparison"); if (previous != null) UnityEngine.Object.DestroyImmediate(previous);
            var holder = new GameObject("BridgeComparison");
            var benchmark = holder.AddComponent<BridgeBenchmark>(); benchmark.settings = settings;
            holder.AddComponent<RosSharpBenchmarkTransport>();
            var adapter = holder.AddComponent<BridgeRobotAdapter>(); adapter.benchmark = benchmark; adapter.robot = robot;
            var capture = holder.AddComponent<BridgeDiagnosticCapture>(); capture.benchmark = benchmark; capture.robot = robot;
            Directory.CreateDirectory(Path.GetDirectoryName(Scene));
            EditorSceneManager.SaveScene(holder.scene, Scene);
            AssetDatabase.SaveAssets();
            Debug.Log("BRIDGE_SCENE_READY canonical_count=" + settings.canonical_joint_names.Length);
        }
        public static void BuildAndroid() { Configure(); Build(BuildTarget.Android, "Builds/Android/bridge-spike.apk"); }
        public static void BuildWindows() { Configure(); Build(BuildTarget.StandaloneWindows64, "Builds/Windows/bridge-spike.exe"); }
        static void Build(BuildTarget target, string path)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions { scenes = new[] { Scene }, locationPathName = path, target = target, options = BuildOptions.Development });
            if (report.summary.result != BuildResult.Succeeded) throw new Exception("Bridge build failed: " + report.summary.result);
            Debug.Log("BRIDGE_BUILD_PASS target=" + target + " bytes=" + report.summary.totalSize);
        }
        public static void PlayDiagnostic()
        {
            Configure();
            var benchmark = UnityEngine.Object.FindFirstObjectByType<BridgeBenchmark>();
            if (!benchmark.settings.diagnostic_apply) throw new InvalidOperationException("PlayDiagnostic requires explicit diagnostic_apply=true");
            EditorApplication.isPlaying = true; // run without -quit; capture component exits after bounded run
        }
    }
}
