using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEditor.Compilation;
using UnityEditor.SceneManagement;
using UnityEditor.XR.Management;
using UnityEditor.XR.Management.Metadata;
using UnityEditor.XR.OpenXR.Features;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.XR;
using UnityEngine.Rendering;
using UnityEngine.XR.Management;
using UnityEngine.XR.OpenXR;
using Unity.XR.CoreUtils;

namespace AcousticVocab.Foundation.Editor
{
    public static class FoundationBuild
    {
        public const string ScenePath = "Assets/ExperimentApp/Scenes/Foundation.unity";
        public static string RepositoryRoot => Directory.GetParent(Application.dataPath).Parent.FullName;
        public static void Configure()
        {
            EditorSettings.serializationMode = SerializationMode.ForceText;
            VersionControlSettings.mode = "Visible Meta Files";
            PlayerSettings.companyName = "AcousticVocab";
            PlayerSettings.productName = "Acoustic Vocabulary Experiment";
            PlayerSettings.bundleVersion = "0.1.0";
            PlayerSettings.SetApplicationIdentifier(NamedBuildTarget.Android, "org.acousticvocab.experiment");
            PlayerSettings.Android.minSdkVersion = AndroidSdkVersions.AndroidApiLevel32;
            PlayerSettings.Android.targetSdkVersion = AndroidSdkVersions.AndroidApiLevelAuto;
            PlayerSettings.Android.targetArchitectures = AndroidArchitecture.ARM64;
            PlayerSettings.SetScriptingBackend(NamedBuildTarget.Android, ScriptingImplementation.IL2CPP);
            PlayerSettings.SetScriptingBackend(NamedBuildTarget.Standalone, ScriptingImplementation.Mono2x);
            PlayerSettings.SetUseDefaultGraphicsAPIs(BuildTarget.Android, false);
            PlayerSettings.SetGraphicsAPIs(BuildTarget.Android, new[] { GraphicsDeviceType.Vulkan });
            PlayerSettings.SetUseDefaultGraphicsAPIs(BuildTarget.StandaloneWindows64, false);
            PlayerSettings.SetGraphicsAPIs(BuildTarget.StandaloneWindows64, new[] { GraphicsDeviceType.Direct3D11 });
            PlayerSettings.colorSpace = ColorSpace.Linear;
            PlayerSettings.gpuSkinning = true;
            PlayerSettings.MTRendering = true;
            PlayerSettings.graphicsJobs = false;
            PlayerSettings.enableFrameTimingStats = true;
            PlayerSettings.runInBackground = true;
            QualitySettings.vSyncCount = 0;
            QualitySettings.antiAliasing = 4;

            // The public API does not expose activeInputHandler in this editor version.
            var serializedPlayer = new SerializedObject(AssetDatabase.LoadAllAssetsAtPath("ProjectSettings/ProjectSettings.asset")[0]);
            var inputHandler = serializedPlayer.FindProperty("activeInputHandler");
            inputHandler.intValue = 1; // Input System package, not legacy input.
            serializedPlayer.ApplyModifiedPropertiesWithoutUndo();

            Directory.CreateDirectory("Assets/XR/Settings");
            if (!EditorBuildSettings.TryGetConfigObject(XRGeneralSettings.settingsKey, out XRGeneralSettingsPerBuildTarget perTarget))
            {
                perTarget = ScriptableObject.CreateInstance<XRGeneralSettingsPerBuildTarget>();
                AssetDatabase.CreateAsset(perTarget, "Assets/XR/Settings/XRGeneralSettingsPerBuildTarget.asset");
                EditorBuildSettings.AddConfigObject(XRGeneralSettings.settingsKey, perTarget, true);
            }
            foreach (var group in new[] { BuildTargetGroup.Android, BuildTargetGroup.Standalone })
            {
                if (!perTarget.HasManagerSettingsForBuildTarget(group)) perTarget.CreateDefaultManagerSettingsForBuildTarget(group);
                var general = perTarget.SettingsForBuildTarget(group);
                general.InitManagerOnStart = true;
                general.Manager.automaticLoading = true;
                general.Manager.automaticRunning = true;
                if (!XRPackageMetadataStore.AssignLoader(general.Manager, "UnityEngine.XR.OpenXR.OpenXRLoader", group))
                    throw new Exception("Cannot assign OpenXR loader for " + group);
                var featureSet = OpenXRFeatureSetManager.GetFeatureSetWithId(group, "com.unity.openxr.featureset.meta");
                if (featureSet == null) throw new Exception("Meta OpenXR feature group missing: " + group);
                featureSet.isEnabled = true;
                OpenXRFeatureSetManager.SetFeaturesFromEnabledFeatureSets(group);
                var settings = OpenXRSettings.GetSettingsForBuildTargetGroup(group);
                settings.renderMode = OpenXRSettings.RenderMode.SinglePassInstanced;
                foreach (var feature in settings.GetFeatures())
                {
                    // Keep the Meta group, its required composition feature and only the
                    // capabilities this engineering scene needs. No passthrough/scene capture.
                    string name = feature.GetType().Name;
                    feature.enabled = name == "MetaQuestFeature" || name == "DisplayUtilitiesFeature" ||
                        name == "OpenXRCompositionLayersFeature" || name == "OculusTouchControllerProfile" ||
                        name == "MetaQuestTouchProControllerProfile" || name == "HandTracking" || name == "OpenXRLifeCycleFeature";
                    EditorUtility.SetDirty(feature);
                }
                EditorUtility.SetDirty(settings);
                EditorUtility.SetDirty(general.Manager);
                EditorUtility.SetDirty(general);
            }
            EditorUtility.SetDirty(perTarget);
            PlayerSettings.Android.forceInternetPermission = true;
            PlayerSettings.usePlayerLog = true;
            EditorUserBuildSettings.development = false;
            EditorUserBuildSettings.connectProfiler = false;
            EditorUserBuildSettings.allowDebugging = false;
            if (!File.Exists(ScenePath)) CreateScene();
            EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(ScenePath, true) };
            AssetDatabase.SaveAssets();
            VerifySchema();
            Debug.Log("FOUNDATION_CONFIGURED editor=" + Application.unityVersion);
        }

        public static void CreateScene()
        {
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var root = new GameObject("FoundationRoot");
            var rig = new GameObject("SeatedOrigin"); rig.transform.SetParent(root.transform, false);
            var origin = rig.AddComponent<XROrigin>();
            var offset = new GameObject("CameraOffset"); offset.transform.SetParent(rig.transform, false);
            var cameraObject = new GameObject("Main Camera"); cameraObject.tag = "MainCamera";
            cameraObject.transform.SetParent(offset.transform, false);
            var camera = cameraObject.AddComponent<Camera>();
            camera.nearClipPlane = .05f; camera.farClipPlane = 50;
            camera.clearFlags = CameraClearFlags.SolidColor; camera.backgroundColor = Color.black;
            cameraObject.AddComponent<AudioListener>();
            var tracked = cameraObject.AddComponent<TrackedPoseDriver>();
            tracked.positionInput = new InputActionProperty(new InputAction("Head position", InputActionType.Value, "<XRHMD>/centerEyePosition", expectedControlType: "Vector3"));
            tracked.rotationInput = new InputActionProperty(new InputAction("Head rotation", InputActionType.Value, "<XRHMD>/centerEyeRotation", expectedControlType: "Quaternion"));
            origin.Camera = camera; origin.CameraFloorOffsetObject = offset; origin.CameraYOffset = 0;
            origin.RequestedTrackingOriginMode = XROrigin.TrackingOriginMode.Device;
            var presentation = new GameObject("PresentationRoot"); presentation.transform.SetParent(root.transform, false); presentation.SetActive(false);
            var bootstrap = root.AddComponent<FoundationBootstrap>();
            bootstrap.seatedOrigin = rig.transform; bootstrap.observerCamera = camera; bootstrap.presentationRoot = presentation;
            Directory.CreateDirectory(Path.GetDirectoryName(ScenePath));
            EditorSceneManager.SaveScene(scene, ScenePath);
        }
        public static void VerifySchema()
        {
            if (File.ReadAllText(Path.Combine(RepositoryRoot, "apparatus/schemas/station.schema.json")) != File.ReadAllText("Assets/ExperimentApp/Resources/StationConfigSchema.json"))
                throw new BuildFailedException("Embedded station schema differs from canonical schema.");
        }
        public static void VerifyParticipantScene()
        {
            foreach (var assembly in CompilationPipeline.GetAssemblies(AssembliesType.Player))
                foreach (string file in assembly.sourceFiles)
                {
                    string relative = Path.GetRelativePath(Application.dataPath, Path.GetFullPath(file)).Replace('\\', '/');
                    if (!relative.StartsWith("../", StringComparison.Ordinal) && !relative.StartsWith("ExperimentApp/Runtime/", StringComparison.Ordinal))
                        throw new BuildFailedException("Unexpected project runtime source outside the reviewed foundation runtime directory.");
                }
            if (Directory.GetFiles("Assets", "*.local.json", SearchOption.AllDirectories).Length != 0)
                throw new BuildFailedException("Private station configuration must not be imported into Assets.");
            foreach (var group in new[] { BuildTargetGroup.Android, BuildTargetGroup.Standalone })
            {
                var features = OpenXRSettings.GetSettingsForBuildTargetGroup(group).GetFeatures();
                var requiredFeatures = new[] { "DisplayUtilitiesFeature", "OpenXRCompositionLayersFeature", "OpenXRLifeCycleFeature" };
                if (group == BuildTargetGroup.Android) requiredFeatures = requiredFeatures.Append("MetaQuestFeature").ToArray();
                foreach (string required in requiredFeatures)
                    if (!features.Any(x => x.GetType().Name == required && x.enabled))
                        throw new BuildFailedException("Required OpenXR feature disabled: " + required);
            }
            var scene = EditorSceneManager.OpenScene(ScenePath);
            var allowed = new[] { typeof(Transform), typeof(Camera), typeof(AudioListener), typeof(TrackedPoseDriver), typeof(XROrigin), typeof(FoundationBootstrap) };
            foreach (var root in scene.GetRootGameObjects())
                foreach (var component in root.GetComponentsInChildren<Component>(true))
                    if (component == null || !allowed.Contains(component.GetType())) throw new BuildFailedException("Foundation scene has an unexpected component.");
            if (UnityEngine.Object.FindObjectsByType<Camera>(FindObjectsInactive.Include).Length != 1)
                throw new BuildFailedException("Exactly one observer camera required.");
            var bootstrap = UnityEngine.Object.FindAnyObjectByType<FoundationBootstrap>();
            if (bootstrap == null || bootstrap.presentationRoot.activeSelf || bootstrap.seatedOrigin == null || bootstrap.observerCamera == null)
                throw new BuildFailedException("Neutral startup and fixed reference bindings required.");
            if (scene.GetRootGameObjects().SelectMany(x => x.GetComponentsInChildren<Component>(true)).Any(x => x.GetType().GetMethod("OnGUI", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic) != null))
                throw new BuildFailedException("Participant scene contains an OnGUI developer overlay.");
        }
        public static void BuildAndroid() => Build(BuildTarget.Android, "Builds/Android/experiment.apk");
        public static void BuildWindows() => Build(BuildTarget.StandaloneWindows64, "Builds/Windows/experiment.exe");
        static void Build(BuildTarget target, string output)
        {
            if (EditorUserBuildSettings.activeBuildTarget != target)
                throw new BuildFailedException("Launch Unity with -buildTarget Android or Win64 before invoking the build method.");
            EditorUserBuildSettings.selectedBuildTargetGroup = BuildPipeline.GetBuildTargetGroup(target);
            Configure(); VerifyParticipantScene();
            string protocol = RequiredEnvironment("EXPERIMENT_PROTOCOL_VERSION");
            string commit = RequiredEnvironment("EXPERIMENT_COMMIT_SHA");
            string buildId = RequiredEnvironment("EXPERIMENT_BUILD_ID");
            if (!System.Text.RegularExpressions.Regex.IsMatch(commit, "^[0-9a-f]{40}$")) throw new BuildFailedException("Commit SHA must be forty hexadecimal characters.");
            foreach (var token in new[] { protocol, buildId })
                if (!System.Text.RegularExpressions.Regex.IsMatch(token, "^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")) throw new BuildFailedException("Build identity token invalid.");
            output = Path.Combine("Builds", buildId, target == BuildTarget.Android ? "Android" : "Windows", Path.GetFileName(output));
            if (Directory.Exists(Path.GetDirectoryName(output))) throw new BuildFailedException("Use a fresh build identifier; existing output will not be overwritten.");
            Directory.CreateDirectory("Assets/Generated.local.data/Resources");
            var identity = new JObject { ["schema_version"] = 1, ["build_id"] = buildId, ["commit_sha"] = commit,
                ["protocol_version"] = protocol, ["editor_version"] = Application.unityVersion, ["target"] = target.ToString(),
                ["dirty_source"] = Environment.GetEnvironmentVariable("EXPERIMENT_DIRTY_SOURCE") == "true",
                ["station_schema_sha256"] = Hash(File.ReadAllBytes("Assets/ExperimentApp/Resources/StationConfigSchema.json")),
                ["development_only"] = true };
            File.WriteAllText("Assets/Generated.local.data/Resources/BuildIdentity.json", identity.ToString() + "\n");
            AssetDatabase.Refresh();
            Directory.CreateDirectory(Path.GetDirectoryName(output));
            var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions { scenes = new[] { ScenePath }, locationPathName = output, target = target, options = BuildOptions.None });
            var record = new JObject { ["build_identity"] = identity, ["result"] = report.summary.result.ToString(), ["errors"] = report.summary.totalErrors,
                ["duration_seconds"] = report.summary.totalTime.TotalSeconds, ["total_bytes"] = report.summary.totalSize, ["development_build"] = false,
                ["files"] = new JArray(Directory.GetFiles(Path.GetDirectoryName(output), "*", SearchOption.AllDirectories).OrderBy(x => x).Select(x => new JObject {
                    ["path"] = Path.GetRelativePath(Path.GetDirectoryName(output), x).Replace('\\', '/'), ["bytes"] = new FileInfo(x).Length, ["sha256"] = Hash(File.ReadAllBytes(x)) })) };
            File.WriteAllText(output + ".build.json", record.ToString() + "\n");
            Debug.Log("FOUNDATION_BUILD target=" + target + " result=" + report.summary.result + " errors=" + report.summary.totalErrors);
            if (report.summary.result != BuildResult.Succeeded || report.summary.totalErrors != 0) throw new BuildFailedException("Foundation player build failed or reported errors.");
        }
        static string RequiredEnvironment(string key) => Environment.GetEnvironmentVariable(key) ?? throw new BuildFailedException("Missing " + key);
        public static string Hash(byte[] value) { using var hash = SHA256.Create(); return BitConverter.ToString(hash.ComputeHash(value)).Replace("-", "").ToLowerInvariant(); }
    }
}
