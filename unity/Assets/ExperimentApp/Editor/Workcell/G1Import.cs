using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;

namespace AcousticVocab.Workcell.Editor
{
    [Serializable] public sealed class Origin { public float[] xyz; public float[] rpy; }
    [Serializable] public sealed class Visual { public Origin origin; public string mesh_file; public float[] scale; public float[] color; }
    [Serializable] public sealed class Link { public string name; public Visual[] visuals; }
    [Serializable] public sealed class Joint
    {
        public string name, type, parent, child;
        public Origin origin;
        public float[] axis;
        public double lower, upper;
    }
    [Serializable] public sealed class RobotDescription
    {
        public int schema_version;
        public string robot, source_sha256, root_link;
        public Link[] links;
        public Joint[] joints;
    }

    public static class G1Import
    {
        const string Generated = "Assets/Generated.local.data/Workcell/G1";
        const string PreviewScene = Generated + "/G1Preview.unity";
        [Serializable] public sealed class FixedFrameSet { public int schema_version; public FixedFrame[] frames; }
        [Serializable] public sealed class FixedFrame
        {
            public string name, parent;
            public float[] local_position, local_quaternion_wxyz;
        }

        public static void Import(WorkcellRegistry registry, Newtonsoft.Json.Linq.JObject layout)
        {
            string source = RequiredEnvironment("G1_DESCRIPTION_JSON");
            string basePath = Path.GetDirectoryName(source);
            if (Foundation.Editor.FoundationBuild.Hash(File.ReadAllBytes(source)) != "6ff5a6f555cb650e628fff674d28827df6f8fb4618afcb61506c0f868d1cbb41")
                throw new InvalidDataException("Converted description differs from the deterministic reviewed #46 conversion.");
            var manifest = Newtonsoft.Json.Linq.JObject.Parse(File.ReadAllText(source));
            var expectedMeshes = manifest["meshes"].ToDictionary(m => (string)m["file"], m => (string)m["converted_sha256"]);
            string materialPath = Path.Combine(Foundation.Editor.FoundationBuild.RepositoryRoot, "docs/workcell/robot-material-bindings.json");
            if (Foundation.Editor.FoundationBuild.Hash(File.ReadAllBytes(materialPath)) != "87318a34dce2ca6d5ffb84b11252d201054406ef9ba3973e62155c226f9fd67b")
                throw new InvalidDataException("Authored USD material metadata differs from reviewed export.");
            var authored = Newtonsoft.Json.Linq.JObject.Parse(File.ReadAllText(materialPath));
            var visualMaterials = authored["meshes"].Where(m => (string)m["purpose"] == "default" && ((string)m["path"]).Contains("/visuals/"))
                .ToDictionary(m => ((string)m["rigid_body"]).Split('/').Last(), m => (string)m["material"]);
            var description = JsonUtility.FromJson<RobotDescription>(File.ReadAllText(source));
            if (description.schema_version != 1) throw new InvalidDataException("Unsupported description version");
            Directory.CreateDirectory(Generated);
            Directory.CreateDirectory(Generated + "/Resources");
            string license = Path.Combine(Foundation.Editor.FoundationBuild.RepositoryRoot, "unity/Assets/ThirdParty/Unitree/G1/LICENSE");
            File.Copy(license, Generated + "/Resources/UnitreeLicense.txt", true);
            AssetDatabase.Refresh();
            if (description.source_sha256 != "97da67732d067c3147fc5fb7b7bafc8982718f4e7f8c92ff82266a4d9c07200d")
                throw new InvalidDataException("Converted G1 does not match the reviewed #46 URDF hash.");
            var holder = new GameObject("ImportedG1"); holder.transform.SetParent(registry.transform, false);
            var links = new Dictionary<string, Transform>(StringComparer.Ordinal);
            var bindings = new List<RobotJointBinding>();
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
                    if (!expectedMeshes.TryGetValue(visual.mesh_file, out var digest) || Foundation.Editor.FoundationBuild.Hash(File.ReadAllBytes(meshPath)) != digest)
                        throw new InvalidDataException("Converted mesh hash mismatch.");
                    var mesh = ReadMesh(meshPath);
                    triangles += mesh.triangles.Length / 3;
                    string meshAsset = Generated + "/" + definition.name + "-" + link.childCount + ".asset";
                    var oldMesh = AssetDatabase.LoadAssetAtPath<Mesh>(meshAsset);
                    if (oldMesh != null) { EditorUtility.CopySerialized(mesh, oldMesh); UnityEngine.Object.DestroyImmediate(mesh); mesh = oldMesh; }
                    else AssetDatabase.CreateAsset(mesh, meshAsset);
                    var obj = new GameObject("Visual", typeof(MeshFilter), typeof(MeshRenderer));
                    obj.transform.SetParent(link, false);
                    obj.transform.localPosition = P(visual.origin.xyz);
                    obj.transform.localRotation = OriginRotation(visual.origin.rpy);
                    obj.transform.localScale = new Vector3(visual.scale[1], visual.scale[2], visual.scale[0]);
                    obj.GetComponent<MeshFilter>().sharedMesh = mesh;
                    if (!visualMaterials.TryGetValue(definition.name, out var materialName)) throw new InvalidDataException("No authored USD material for rendered link.");
                    var inputs = authored["materials"][materialName]["shaders"][0]["inputs"];
                    var diffuse = inputs["diffuse_color_constant"] ?? inputs["diffuse_reflection_color"];
                    var color = new Color((float)diffuse[0], (float)diffuse[1], (float)diffuse[2], 1);
                    string matPath = Generated + "/USD_" + materialName.Split('/').Last() + ".mat";
                    var mat = AssetDatabase.LoadAssetAtPath<Material>(matPath);
                    if (mat == null) { mat = new Material(Shader.Find("Standard")); AssetDatabase.CreateAsset(mat, matPath); }
                    // Authored diffuse binding approximation only: OmniPBR tint, metallic,
                    // MDL defaults and RTX response are not silently treated as equivalent.
                    mat.color=color; mat.SetFloat("_Metallic",0); mat.SetFloat("_Glossiness",.3f); EditorUtility.SetDirty(mat);
                    obj.GetComponent<MeshRenderer>().sharedMaterial = mat;
                }
            }
            foreach (var joint in description.joints)
            {
                var origin = new GameObject(joint.name + "__origin").transform;
                origin.SetParent(links[joint.parent], false);
                origin.localPosition = P(joint.origin.xyz);
                origin.localRotation = OriginRotation(joint.origin.rpy);
                var child = links[joint.child];
                child.SetParent(origin, false);
                child.localPosition = Vector3.zero;
                child.localRotation = Quaternion.identity;
                if (joint.type == "revolute") bindings.Add(new RobotJointBinding
                {
                    name = joint.name, link = child, unityAxis = -P(joint.axis),
                    lowerRad = (float)joint.lower, upperRad = (float)joint.upper
                });
            }
            string framePath = Path.Combine(Foundation.Editor.FoundationBuild.RepositoryRoot, "docs/spikes/urdf/usd-fixed-frames.json");
            if (File.Exists(framePath))
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
                    link.localPosition=P(frame.local_position);
                    link.localRotation=SceneCoordinates.Rotation(new Quaternion(frame.local_quaternion_wxyz[1], frame.local_quaternion_wxyz[2], frame.local_quaternion_wxyz[3], frame.local_quaternion_wxyz[0]));
                }
            }
            var map = File.ReadAllLines(Path.Combine(Foundation.Editor.FoundationBuild.RepositoryRoot, "docs/spikes/urdf/joint_map.csv")).Skip(1).Where(x => x.Length != 0).Select(x => x.Split(',')).ToArray();
            if (map.Length != 43 || bindings.Count != 43) throw new InvalidDataException("Expected 43 verified joints.");
            registry.joints = map.Select((row, index) => {
                if (int.Parse(row[0]) != index || row[1] != row[2] || row[4] != "1" || row[5] != "0" || row[10] != "true") throw new InvalidDataException("Unverified map row.");
                var joint = bindings.Single(item => item.name == row[1]);
                joint.lowerRad = float.Parse(row[8], System.Globalization.CultureInfo.InvariantCulture);
                joint.upperRad = float.Parse(row[9], System.Globalization.CultureInfo.InvariantCulture);
                joint.neutralRad = (float?)layout["robot"]["neutral_joint_overrides_rad"][joint.name] ?? 0;
                return joint;
            }).ToArray();
            registry.links = links.Select(pair => new RobotLinkBinding { name = pair.Key, link = pair.Value }).ToArray();
            holder.transform.localPosition = WorkcellBuild.Position(layout["robot"]["position_m"]);
            holder.transform.localRotation = Quaternion.identity;
            Debug.Log($"WORKCELL_G1_IMPORTED links={links.Count} driven_joints={bindings.Count} rendered_triangles={triangles}");
        }
        static Vector3 P(float[] x) => SceneCoordinates.Position(new Vector3(x[0], x[1], x[2]));
        static Quaternion OriginRotation(float[] x) => SceneCoordinates.Rotation(Quaternion.AngleAxis(x[2] * Mathf.Rad2Deg, Vector3.forward) * Quaternion.AngleAxis(x[1] * Mathf.Rad2Deg, Vector3.up) * Quaternion.AngleAxis(x[0] * Mathf.Rad2Deg, Vector3.right));
        static string RequiredEnvironment(string key) => Environment.GetEnvironmentVariable(key) ?? throw new InvalidDataException("Set " + key + " to the reviewed #46 converted description.");

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

    }
}
