using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;

namespace AcousticVocab.Spikes.Urdf.Editor
{
    public static class UrdfSpikeImport
    {
        const string Generated = "Assets/Generated.local.data/G1";
        const string PreviewScene = Generated + "/G1Preview.unity";
        [Serializable] public sealed class FixedFrameSet { public int schema_version; public FixedFrame[] frames; }
        [Serializable] public sealed class FixedFrame
        {
            public string name, parent;
            public float[] local_position, local_quaternion_wxyz;
        }

        [MenuItem("Spikes/URDF/Import from local environment path")]
        public static void Import()
        {
            string source = RequiredEnvironment("G1_DESCRIPTION_JSON");
            string basePath = Path.GetDirectoryName(source);
            var description = JsonUtility.FromJson<RobotDescription>(File.ReadAllText(source));
            if (description.schema_version != 1) throw new InvalidDataException("Unsupported description version");
            Directory.CreateDirectory(Generated);
            Directory.CreateDirectory(Generated + "/Resources");
            string license = Path.GetFullPath(Path.Combine(Application.dataPath, "../../../Assets/ThirdParty/Unitree/G1/LICENSE"));
            File.Copy(license, Generated + "/Resources/UnitreeLicense.txt", true);
            AssetDatabase.Refresh();
            EditorSceneManager.OpenScene(OpenXR.Editor.SpikeBuild.ScenePath);
            var existing = GameObject.Find("ImportedG1");
            if (existing != null) UnityEngine.Object.DestroyImmediate(existing);
            var placeholder = GameObject.Find("SyntheticRobotPlaceholder");
            if (placeholder != null) placeholder.SetActive(false);
            var holder = new GameObject("ImportedG1");
            var hierarchy = holder.AddComponent<RobotHierarchy>();
            var links = new Dictionary<string, Transform>(StringComparer.Ordinal);
            var bindings = new List<JointBinding>();
            int triangles = 0;
            foreach (var definition in description.links)
            {
                var link = new GameObject(definition.name).transform;
                link.SetParent(holder.transform, false);
                links.Add(definition.name, link);
                foreach (var visual in definition.visuals)
                {
                    string meshPath = Path.GetFullPath(Path.Combine(basePath, visual.mesh_file));
                    if (!meshPath.StartsWith(Path.GetFullPath(basePath) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                        throw new InvalidDataException("Mesh path escapes converted directory");
                    var mesh = ReadMesh(meshPath);
                    triangles += mesh.triangles.Length / 3;
                    string meshAsset = Generated + "/" + definition.name + "-" + link.childCount + ".asset";
                    var oldMesh = AssetDatabase.LoadAssetAtPath<Mesh>(meshAsset);
                    if (oldMesh != null) { EditorUtility.CopySerialized(mesh, oldMesh); UnityEngine.Object.DestroyImmediate(mesh); mesh = oldMesh; }
                    else AssetDatabase.CreateAsset(mesh, meshAsset);
                    var obj = new GameObject("Visual", typeof(MeshFilter), typeof(MeshRenderer));
                    obj.transform.SetParent(link, false);
                    obj.transform.localPosition = RobotHierarchy.ToUnity(visual.origin.xyz);
                    obj.transform.localRotation = RobotHierarchy.OriginRotation(visual.origin.rpy);
                    obj.transform.localScale = new Vector3(visual.scale[1], visual.scale[2], visual.scale[0]);
                    obj.GetComponent<MeshFilter>().sharedMesh = mesh;
                    var color = new Color(visual.color[0], visual.color[1], visual.color[2], visual.color[3]);
                    string matPath = Generated + "/" + ColorUtility.ToHtmlStringRGBA(color) + ".mat";
                    var mat = AssetDatabase.LoadAssetAtPath<Material>(matPath);
                    if (mat == null) { mat = new Material(Shader.Find("Standard")) { color = color }; AssetDatabase.CreateAsset(mat, matPath); }
                    obj.GetComponent<MeshRenderer>().sharedMaterial = mat;
                }
            }
            foreach (var joint in description.joints)
            {
                var origin = new GameObject(joint.name + "__origin").transform;
                origin.SetParent(links[joint.parent], false);
                origin.localPosition = RobotHierarchy.ToUnity(joint.origin.xyz);
                origin.localRotation = RobotHierarchy.OriginRotation(joint.origin.rpy);
                var child = links[joint.child];
                child.SetParent(origin, false);
                child.localPosition = Vector3.zero;
                child.localRotation = Quaternion.identity;
                if (joint.type == "revolute") bindings.Add(new JointBinding
                {
                    name = joint.name, link = child, unityAxis = -RobotHierarchy.ToUnity(joint.axis),
                    lower = joint.lower, upper = joint.upper
                });
            }
            string framePath = Path.GetFullPath(Path.Combine(Application.dataPath, "../../../../docs/spikes/urdf/usd-fixed-frames.json"));
            if (File.Exists(framePath) && Environment.GetEnvironmentVariable("G1_SOURCE_ONLY_IMPORT") != "1")
            {
                var fixedFrames = JsonUtility.FromJson<FixedFrameSet>(File.ReadAllText(framePath));
                if(fixedFrames.schema_version != 1)throw new InvalidDataException("Unsupported fixed-frame metadata");
                foreach(var frame in fixedFrames.frames)
                {
                    if(!links.TryGetValue(frame.parent,out var parent))throw new InvalidDataException("Unknown frame parent "+frame.parent);
                    if(bindings.Any(binding=>binding.name==frame.name || binding.link.name==frame.name))throw new InvalidDataException("Cannot override driven joint link");
                    if(!links.TryGetValue(frame.name,out var link))
                    {link=new GameObject(frame.name).transform;links.Add(frame.name,link);}
                    link.SetParent(parent,false);
                    link.localPosition=RobotHierarchy.ToUnity(frame.local_position);
                    link.localRotation=RobotHierarchy.ToUnityQuaternion(frame.local_quaternion_wxyz);
                }
            }
            hierarchy.joints = bindings.ToArray();
            hierarchy.links = links.Select(pair => new LinkBinding { name = pair.Key, link = pair.Value }).ToArray();
            holder.transform.position = new Vector3(0, .75f, 2.5f);
            // The robot faces the observer (URDF forward +x becomes Unity +z).
            holder.transform.rotation = Quaternion.Euler(0, 180, 0);
            PrefabUtility.SaveAsPrefabAsset(holder, Generated + "/G1.prefab");
            EditorSceneManager.SaveScene(holder.scene, PreviewScene);
            AssetDatabase.SaveAssets();
            Debug.Log($"URDF_SPIKE_IMPORTED links={links.Count} driven_joints={bindings.Count} rendered_triangles={triangles}");
        }

        static Mesh ReadMesh(string path)
        {
            using var reader = new BinaryReader(File.OpenRead(path));
            if (new string(reader.ReadChars(4)) != "AVM1") throw new InvalidDataException("Invalid mesh magic");
            uint count = reader.ReadUInt32();
            if (count == 0 || count > 10000000 || reader.BaseStream.Length != 8L + count * 36L)
                throw new InvalidDataException("Invalid mesh length");
            var vertices = new Vector3[count * 3];
            var indices = new int[vertices.Length];
            for (int i = 0; i < vertices.Length; ++i)
            {
                vertices[i] = new Vector3(reader.ReadSingle(), reader.ReadSingle(), reader.ReadSingle()); indices[i] = i;
            }
            var mesh = new Mesh { name = Path.GetFileNameWithoutExtension(path), indexFormat = IndexFormat.UInt32 };
            mesh.vertices = vertices; mesh.triangles = indices; mesh.RecalculateNormals(); mesh.RecalculateBounds();
            return mesh;
        }

        [Serializable] public sealed class PoseSet { public string coordinate_frame; public Pose[] poses; }
        [Serializable] public sealed class Pose
        {
            public string name;
            public float[] root_position, root_quaternion_wxyz;
            public string[] joint_names;
            public double[] joint_positions;
            public LinkPose[] links;
        }
        [Serializable] public sealed class LinkPose { public string name; public float[] position, quaternion_wxyz; }

        [MenuItem("Spikes/URDF/Export comparison poses")]
        public static void ExportPoses()
        {
            EditorSceneManager.OpenScene(PreviewScene);
            var robot = UnityEngine.Object.FindFirstObjectByType<RobotHierarchy>();
            var poses = JsonUtility.FromJson<PoseSet>(File.ReadAllText(RequiredEnvironment("G1_POSES_JSON")));
            if (poses.coordinate_frame != "usd_world_rh_z_up") throw new InvalidDataException("Unsupported pose coordinate frame");
            foreach (var pose in poses.poses)
            {
                robot.ResetJoints();
                robot.transform.position = RobotHierarchy.ToUnity(pose.root_position);
                robot.transform.rotation = RobotHierarchy.ToUnityQuaternion(pose.root_quaternion_wxyz);
                if (pose.joint_names.Length != pose.joint_positions.Length) throw new InvalidDataException("Mismatched joint vector");
                for (int i = 0; i < pose.joint_names.Length; ++i)
                    if (!robot.ApplyJoint(pose.joint_names[i], pose.joint_positions[i])) throw new InvalidDataException("Unmapped joint " + pose.joint_names[i]);
                pose.links = robot.links.Select(link => new LinkPose
                {
                    name = link.name, position = RobotHierarchy.ToRos(link.link.position),
                    quaternion_wxyz = RobotHierarchy.ToRosQuaternion(link.link.rotation)
                }).ToArray();
            }
            File.WriteAllText(RequiredEnvironment("G1_UNITY_POSES_JSON"), JsonUtility.ToJson(poses, true));
            Debug.Log($"URDF_SPIKE_POSES_EXPORTED poses={poses.poses.Length} links={robot.links.Length}");
        }

        // Rendered engineering evidence, separate from Quest wearer/capture evidence.
        // Run without -nographics so Unity can create a graphics device.
        public static void CaptureComparisonImages()
        {
            EditorSceneManager.OpenScene(PreviewScene);
            var robot = UnityEngine.Object.FindFirstObjectByType<RobotHierarchy>();
            var poses = JsonUtility.FromJson<PoseSet>(File.ReadAllText(RequiredEnvironment("G1_POSES_JSON")));
            var selected = RequiredEnvironment("G1_CAPTURE_POSES").Split(',');
            string output = RequiredEnvironment("G1_CAPTURE_DIRECTORY");
            Directory.CreateDirectory(output);
            GameObject.Find("SeatedWorkcell")?.SetActive(false);
            var cameraObject = new GameObject("PoseEvidenceCamera");
            var camera = cameraObject.AddComponent<Camera>();
            camera.clearFlags = CameraClearFlags.SolidColor; camera.backgroundColor = new Color(.12f,.14f,.18f);
            camera.nearClipPlane = .05f; camera.farClipPlane = 20;
            var target = new RenderTexture(1024, 1024, 24) { antiAliasing = 4 };
            camera.targetTexture = target;
            foreach (string name in selected)
            {
                var pose = poses.poses.Single(item => item.name == name);
                robot.ResetJoints();
                robot.transform.position = RobotHierarchy.ToUnity(pose.root_position);
                robot.transform.rotation = RobotHierarchy.ToUnityQuaternion(pose.root_quaternion_wxyz);
                for(int i=0;i<pose.joint_names.Length;++i)
                    if(!robot.ApplyJoint(pose.joint_names[i],pose.joint_positions[i]))throw new InvalidDataException("Unmapped joint");
                camera.transform.position = robot.transform.position + new Vector3(.9f,.65f,2.4f);
                camera.transform.LookAt(robot.transform.position + new Vector3(0,.15f,0));
                camera.Render();
                var previous = RenderTexture.active; RenderTexture.active = target;
                var image = new Texture2D(1024,1024,TextureFormat.RGB24,false);
                image.ReadPixels(new Rect(0,0,1024,1024),0,0); image.Apply(); RenderTexture.active = previous;
                string file = new string(name.Select(c => char.IsLetterOrDigit(c) || c == '_' || c == '-' ? c : '_').ToArray());
                File.WriteAllBytes(Path.Combine(output,file+".png"),image.EncodeToPNG());
                UnityEngine.Object.DestroyImmediate(image);
            }
            target.Release(); UnityEngine.Object.DestroyImmediate(target); UnityEngine.Object.DestroyImmediate(cameraObject);
            Debug.Log("URDF_SPIKE_IMAGES_RENDERED count="+selected.Length);
        }

        public static void BuildAndroidRobot()
        {
            if (!File.Exists(PreviewScene)) throw new InvalidOperationException("Run Import first");
            var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions { scenes = new[] { PreviewScene },
                locationPathName = "Builds/Android/g1-spike.apk", target = BuildTarget.Android, options = BuildOptions.Development });
            if (report.summary.result != BuildResult.Succeeded) throw new Exception("G1 spike build failed: " + report.summary.result);
            Debug.Log("URDF_SPIKE_ANDROID_BUILD_SUCCEEDED");
        }
        static string RequiredEnvironment(string name) => Environment.GetEnvironmentVariable(name) ?? throw new InvalidOperationException("Set local environment variable " + name);
    }
}
