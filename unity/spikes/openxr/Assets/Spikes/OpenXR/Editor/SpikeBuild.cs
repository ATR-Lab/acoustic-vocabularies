using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEditor.XR.Management;
using UnityEditor.XR.Management.Metadata;
using UnityEditor.XR.OpenXR.Features;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.XR;
using UnityEngine.Rendering;
using UnityEngine.UI;
using UnityEngine.XR.Management;
using UnityEngine.XR.OpenXR;
using Unity.XR.CoreUtils;

namespace AcousticVocab.Spikes.OpenXR.Editor
{
    public static class SpikeBuild
    {
        public const string ScenePath = "Assets/Spikes/OpenXR/Scenes/SeatedWorkcell.unity";

        [MenuItem("Spikes/OpenXR/Configure project")]
        public static void Configure()
        {
            EditorSettings.serializationMode = SerializationMode.ForceText;
            EditorSettings.externalVersionControl = "Visible Meta Files";
            PlayerSettings.companyName = "AcousticVocab";
            PlayerSettings.productName = "OpenXR Architecture Spike";
            PlayerSettings.bundleVersion = "0.1.0";
            PlayerSettings.SetApplicationIdentifier(NamedBuildTarget.Android, "org.acousticvocab.openxrspike");
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
                        name == "MetaQuestTouchProControllerProfile" || name == "HandTracking" ||
                        name == "SessionStateProbe";
                    EditorUtility.SetDirty(feature);
                }
                EditorUtility.SetDirty(settings);
                EditorUtility.SetDirty(general.Manager);
                EditorUtility.SetDirty(general);
            }
            EditorUtility.SetDirty(perTarget);
            if (!File.Exists(ScenePath)) RecreateScene();
            EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(ScenePath, true) };
            AssetDatabase.SaveAssets();
            Debug.Log("OPENXR_SPIKE_CONFIGURED editor=" + Application.unityVersion);
        }

        [MenuItem("Spikes/OpenXR/Recreate placeholder scene")]
        public static void RecreateScene()
        {
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var root = new GameObject("SeatedWorkcell");
            var light = new GameObject("WorkcellLight").AddComponent<Light>();
            light.type = LightType.Directional;
            light.transform.rotation = Quaternion.Euler(45, -30, 0);
            RenderSettings.ambientLight = new Color(.55f, .55f, .55f);
            Shape("Floor", PrimitiveType.Cube, new Vector3(0, -.05f, 1), new Vector3(5, .1f, 5), Color.gray, root.transform);
            Shape("Table", PrimitiveType.Cube, new Vector3(0, .7f, 1.6f), new Vector3(2.4f, .1f, .9f), new Color(.5f,.44f,.35f), root.transform);
            for (int i = 0; i < 8; ++i)
            {
                float x = (i % 4 - 1.5f) * .53f;
                float z = i < 4 ? 1.3f : 1.85f;
                Shape("Station " + (char)('A' + i), PrimitiveType.Cube, new Vector3(x, .79f, z),
                    new Vector3(.38f, i < 4 ? .04f : .12f, .3f), new Color(.18f,.26f,.3f), root.transform);
                Label(((char)('A' + i)).ToString(), new Vector3(x, 1.0f, z), root.transform, .08f);
            }
            var robot = new GameObject("SyntheticRobotPlaceholder"); robot.transform.SetParent(root.transform);
            Shape("Torso", PrimitiveType.Capsule, new Vector3(0, 1.17f, 2.5f), new Vector3(.35f,.3f,.2f), Color.white, robot.transform);
            Shape("Head", PrimitiveType.Sphere, new Vector3(0,1.62f,2.5f), Vector3.one*.2f, Color.white, robot.transform);
            for(int side=-1;side<=1;side+=2)
            {
                Shape("Arm", PrimitiveType.Capsule, new Vector3(side*.32f,1.03f,2.5f), new Vector3(.1f,.25f,.1f), Color.white,robot.transform);
                Shape("Leg", PrimitiveType.Capsule, new Vector3(side*.12f,.43f,2.5f), new Vector3(.12f,.36f,.12f), Color.white,robot.transform);
                for(int finger=0;finger<3;++finger)
                    Shape("Dex3 placeholder finger", PrimitiveType.Capsule,new Vector3(side*.32f+(finger-1)*.035f,.73f,2.5f),new Vector3(.025f,.055f,.025f),Color.white,robot.transform);
            }
            Label("STATIC ENGINEERING PLACEHOLDER", new Vector3(0,1.9f,2.5f),root.transform,.045f);
            var originObject = new GameObject("SeatedOrigin");
            var origin = originObject.AddComponent<XROrigin>();
            var offset = new GameObject("CameraOffset"); offset.transform.SetParent(originObject.transform,false);
            var cameraObject = new GameObject("Main Camera"); cameraObject.tag="MainCamera";
            cameraObject.transform.SetParent(offset.transform,false);
            var camera = cameraObject.AddComponent<Camera>(); camera.nearClipPlane=.05f; camera.farClipPlane=50;
            cameraObject.AddComponent<AudioListener>();
            var pose = cameraObject.AddComponent<TrackedPoseDriver>();
            pose.positionInput = new InputActionProperty(new InputAction("Head position", InputActionType.Value, "<XRHMD>/centerEyePosition", expectedControlType:"Vector3"));
            pose.rotationInput = new InputActionProperty(new InputAction("Head rotation", InputActionType.Value, "<XRHMD>/centerEyeRotation", expectedControlType:"Quaternion"));
            origin.Camera=camera; origin.CameraFloorOffsetObject=offset; origin.CameraYOffset=1.2f;
            origin.RequestedTrackingOriginMode=XROrigin.TrackingOriginMode.Device;
            var canvasObject=new GameObject("ResponsePanel",typeof(Canvas),typeof(CanvasScaler));
            canvasObject.transform.SetParent(root.transform,false);
            var canvas=canvasObject.GetComponent<Canvas>();canvas.renderMode=RenderMode.WorldSpace;canvas.worldCamera=camera;
            var rect=canvasObject.GetComponent<RectTransform>();rect.sizeDelta=new Vector2(800,250);
            rect.position=new Vector3(0,.85f,.78f);rect.localScale=Vector3.one*.0012f;
            for(int i=0;i<13;++i)
            {
                string label=i<8 ? ((char)('A'+i)).ToString() : i<12 ? "Action "+(i-7) : "Commit";
                var buttonObject=new GameObject(label,typeof(RectTransform),typeof(Image),typeof(Button));
                buttonObject.transform.SetParent(canvasObject.transform,false);
                var buttonRect=buttonObject.GetComponent<RectTransform>();
                buttonRect.sizeDelta=new Vector2(i==12?150:85,55);
                buttonRect.anchoredPosition=i<8?new Vector2((i-3.5f)*95,70):i<12?new Vector2((i-9.5f)*105,-5):new Vector2(0,-80);
                buttonObject.GetComponent<Image>().color=new Color(.2f,.3f,.4f);
                var textObject=new GameObject("Label",typeof(RectTransform),typeof(Text));textObject.transform.SetParent(buttonObject.transform,false);
                var textRect=textObject.GetComponent<RectTransform>();textRect.anchorMin=Vector2.zero;textRect.anchorMax=Vector2.one;textRect.sizeDelta=Vector2.zero;
                var text=textObject.GetComponent<Text>();text.text=label;text.font=Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");text.fontSize=18;text.alignment=TextAnchor.MiddleCenter;
            }
            new GameObject("FrameLogger").AddComponent<FrameIntervalLogger>().transform.SetParent(root.transform);
            Directory.CreateDirectory(Path.GetDirectoryName(ScenePath));
            EditorSceneManager.SaveScene(scene,ScenePath);
            int triangles=UnityEngine.Object.FindObjectsByType<MeshFilter>(FindObjectsSortMode.None).Sum(m=>m.sharedMesh.triangles.Length/3);
            Debug.Log("OPENXR_SPIKE_SCENE triangles="+triangles+" synthetic_uncalibrated=true");
        }

        static void Shape(string name,PrimitiveType type,Vector3 position,Vector3 scale,Color color,Transform parent)
        {
            var obj=GameObject.CreatePrimitive(type);obj.name=name;obj.transform.SetParent(parent);obj.transform.position=position;obj.transform.localScale=scale;
            // Use built-in material to keep the scene portable. Colors are applied through a saved material.
            string folder="Assets/Spikes/OpenXR/Materials";Directory.CreateDirectory(folder);
            string path=folder+"/"+ColorUtility.ToHtmlStringRGB(color)+".mat";
            var material=AssetDatabase.LoadAssetAtPath<Material>(path);
            if(material==null){material=new Material(Shader.Find("Standard")){color=color};AssetDatabase.CreateAsset(material,path);}
            obj.GetComponent<Renderer>().sharedMaterial=material;
        }
        static void Label(string content,Vector3 position,Transform parent,float height)
        {
            var obj=new GameObject(content);obj.transform.SetParent(parent);obj.transform.position=position;
            var text=obj.AddComponent<TextMesh>();text.text=content;text.characterSize=height;text.fontSize=64;text.anchor=TextAnchor.MiddleCenter;text.alignment=TextAlignment.Center;
        }

        [MenuItem("Spikes/OpenXR/Build Android")]
        public static void BuildAndroid() => Build(BuildTarget.Android,"Builds/Android/openxr-spike.apk");
        [MenuItem("Spikes/OpenXR/Build Windows Link")]
        public static void BuildWindows() => Build(BuildTarget.StandaloneWindows64,"Builds/Windows/openxr-spike.exe");
        static void Build(BuildTarget target,string output)
        {
            if(!File.Exists(ScenePath))throw new Exception("Run SpikeBuild.Configure before building.");
            Directory.CreateDirectory(Path.GetDirectoryName(output));
            var report=BuildPipeline.BuildPlayer(new BuildPlayerOptions{scenes=new[]{ScenePath},locationPathName=output,target=target,options=BuildOptions.Development});
            Debug.Log("OPENXR_SPIKE_BUILD target="+target+" result="+report.summary.result+" errors="+report.summary.totalErrors);
            if(report.summary.result!=BuildResult.Succeeded)throw new Exception("Spike build failed: "+report.summary.result);
        }
    }
}
