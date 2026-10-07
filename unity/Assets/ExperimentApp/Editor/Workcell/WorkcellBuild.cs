using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using AcousticVocab.Foundation;
using AcousticVocab.Foundation.Editor;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;

namespace AcousticVocab.Workcell.Editor
{
    public static class WorkcellBuild
    {
        public const string Generated = "Assets/Generated.local.data/Workcell";
        public const string ScenePath = Generated + "/Workcell.unity";
        public const string LayoutPath = "Assets/ExperimentApp/Resources/Workcell/Layout.json";
        public const string ExpectedLayoutHash = "173048c7109741b3c8b3e34e9166a480e17b72fcc3b60f15a1edacba482879eb";
        static readonly Dictionary<char, string[]> Glyphs = new()
        {
            ['A']=new[]{"010","101","111","101","101"}, ['B']=new[]{"110","101","110","101","110"},
            ['C']=new[]{"011","100","100","100","011"}, ['D']=new[]{"110","101","101","101","110"},
            ['E']=new[]{"111","100","110","100","111"}, ['F']=new[]{"111","100","110","100","100"},
            ['G']=new[]{"011","100","101","101","011"}, ['H']=new[]{"101","101","111","101","101"},
            ['I']=new[]{"111","010","010","010","111"}, ['L']=new[]{"100","100","100","100","111"},
            ['N']=new[]{"101","111","111","111","101"}, ['P']=new[]{"110","101","110","100","100"},
            ['R']=new[]{"110","101","110","101","101"}, ['S']=new[]{"011","100","010","001","110"},
            ['T']=new[]{"111","010","010","010","010"}, ['U']=new[]{"101","101","101","101","111"},
            ['Y']=new[]{"101","101","010","010","010"}, ['0']=new[]{"111","101","101","101","111"},
            ['1']=new[]{"010","110","010","010","111"}
        };
        public static Vector3 Vector(JToken t) => new((float)t[0], (float)t[1], (float)t[2]);
        public static Vector3 Position(JToken t) => SceneCoordinates.Position(Vector(t));
        public static Quaternion Rotation(JToken t) => SceneCoordinates.Rotation(new Quaternion((float)t[0], (float)t[1], (float)t[2], (float)t[3]));
        public static JObject ReadLayout()
        {
            byte[] raw = File.ReadAllBytes(LayoutPath);
            if (FoundationBuild.Hash(raw) != ExpectedLayoutHash || !raw.SequenceEqual(File.ReadAllBytes(Path.Combine(FoundationBuild.RepositoryRoot, "apparatus/workcell_layout.json"))))
                throw new InvalidDataException("Workcell layout bytes differ from the reviewed Isaac export.");
            var layout = JObject.Parse(System.Text.Encoding.UTF8.GetString(raw));
            if ((int)layout["version"] != 1 || (string)layout["coordinate_frame"] != "isaac_world_rh_z_up" ||
                (string)layout["length_unit"] != "m" || (string)layout["quaternion_order"] != "xyzw") throw new InvalidDataException("Unsupported workcell convention.");
            if (layout["objects"].Count() != 60 || layout["objects"].Select(x => (string)x["id"]).Distinct().Count() != 60)
                throw new InvalidDataException("Expected all 60 unique public objects.");
            return layout;
        }
        public static void Configure()
        {
            FoundationBuild.Configure();
            EditorSceneManager.OpenScene(FoundationBuild.ScenePath);
            AddToOpenScene();
            Directory.CreateDirectory(Generated);
            EditorSceneManager.SaveScene(UnityEngine.SceneManagement.SceneManager.GetActiveScene(), ScenePath);
            FoundationBuild.ParticipantScenePath = ScenePath;
            AssetDatabase.SaveAssets();
            FoundationBuild.VerifyParticipantScene();
            Debug.Log("WORKCELL_CONFIGURED objects=60 joints=43 layout=" + ExpectedLayoutHash);
        }
        public static WorkcellRegistry AddToOpenScene()
        {
            var layout = ReadLayout();
            var bootstrap = UnityEngine.Object.FindAnyObjectByType<FoundationBootstrap>();
            if (bootstrap == null) throw new InvalidOperationException("Open a foundation scene first.");
            var old = bootstrap.presentationRoot.GetComponentInChildren<WorkcellRegistry>(true);
            if (old != null) UnityEngine.Object.DestroyImmediate(old.gameObject);
            var root = Child(bootstrap.presentationRoot.transform, "Workcell");
            var registry = root.gameObject.AddComponent<WorkcellRegistry>();
            registry.importedLayout = AssetDatabase.LoadAssetAtPath<TextAsset>(LayoutPath);
            registry.layoutSha256 = ExpectedLayoutHash;
            registry.anchorIds = layout["anchor_ids"].Values<string>().ToArray();
            Directory.CreateDirectory(Generated + "/Materials"); Directory.CreateDirectory(Generated + "/Meshes");
            var materials = new Dictionary<string, Material>();
            foreach (var property in ((JObject)layout["materials"]).Properties())
            {
                string path = Generated + "/Materials/" + property.Name + ".mat";
                var material = AssetDatabase.LoadAssetAtPath<Material>(path);
                if (material == null) { material = new Material(Shader.Find("Standard")); AssetDatabase.CreateAsset(material, path); }
                var color = Vector(property.Value); material.color = new Color(color.x, color.y, color.z);
                material.SetFloat("_Glossiness", .3f); material.SetFloat("_Metallic", 0); EditorUtility.SetDirty(material);
                materials.Add(property.Name, material);
            }
            // Fixed dome approximation in the built-in Unity renderer, not a photometric equivalence claim.
            RenderSettings.ambientMode = AmbientMode.Trilight;
            RenderSettings.ambientSkyColor = new Color(.7f,.7f,.7f); RenderSettings.ambientEquatorColor = new Color(.4f,.4f,.4f); RenderSettings.ambientGroundColor = new Color(.2f,.2f,.2f);
            RenderSettings.ambientIntensity = 1; RenderSettings.reflectionIntensity = 0; RenderSettings.skybox = null; RenderSettings.fog = false;
            bootstrap.observerCamera.backgroundColor = new Color(.95f, .95f, .95f);
            var fixedLight = Child(root,"FixedDomeApproximation").gameObject.AddComponent<Light>();
            fixedLight.type=LightType.Directional;fixedLight.color=Color.white;fixedLight.intensity=1;
            fixedLight.transform.localRotation=Quaternion.Euler(55,180,0);fixedLight.shadows=LightShadows.Soft;
            var bindings = new List<ObjectBinding>();
            foreach (var definition in layout["objects"].Cast<JObject>())
            {
                string id = (string)definition["id"], kind = (string)definition["kind"], label = (string)definition["label"];
                var obj = Child(root, id.Replace('/', '_')); var visual = Child(obj, "Visual");
                var state = (JObject)definition["state"];
                var binding = new ObjectBinding { id = id, kind = kind, label = label, root = obj, visual = visual,
                    neutralPosition = Position(definition["position_m"]), neutralRotation = Rotation(definition["rotation_xyzw"]),
                    neutralVisible = (bool)definition["visible"], neutralEnabled = (bool)definition["enabled"],
                    hasCardFace = state["card_face"] != null, neutralCardFace = (int?)state["card_face"] ?? 0,
                    hasArrowAngle = state["arrow_angle_rad"] != null, neutralArrowAngle = (float?)state["arrow_angle_rad"] ?? 0,
                    hasLidFraction = state["lid_open_fraction"] != null, neutralLidFraction = (float?)state["lid_open_fraction"] ?? 0,
                    hasTagAttached = state["tag_attached"] != null, neutralTagAttached = (bool?)state["tag_attached"] ?? false,
                    hasLocation = state["location"] != null, neutralLocation = (string)state["location"],
                    lidOpenAngleRad = (float?)definition["open_angle_rad"] ?? 0 };
                BuildGeometry(definition, layout, binding, materials); bindings.Add(binding);
            }
            registry.objects = bindings.ToArray();
            G1Import.Import(registry, layout);
            registry.ResetToImportedNeutral();
            bootstrap.presentationRoot.SetActive(false);
            return registry;
        }
        public static Transform Child(Transform parent, string name)
        { var child = new GameObject(name).transform; child.SetParent(parent, false); return child; }
        static Transform Cube(Transform parent, string name, Vector3 size, Vector3 position, Material material)
        {
            var obj = GameObject.CreatePrimitive(PrimitiveType.Cube); obj.name = name; UnityEngine.Object.DestroyImmediate(obj.GetComponent<Collider>());
            obj.transform.SetParent(parent, false); obj.transform.localPosition = SceneCoordinates.Position(position);
            obj.transform.localScale = new Vector3(size.y, size.z, size.x); obj.GetComponent<MeshRenderer>().sharedMaterial = material;
            return obj.transform;
        }
        static void Text(Transform parent, string label, Vector3 position, float pixel, bool horizontal, Material material)
        {
            float width = (label.Length * 4 - 1) * pixel;
            for (int i = 0; i < label.Length; i++) for (int row = 0; row < 5; row++) for (int col = 0; col < 3; col++)
            {
                if (Glyphs[label[i]][row][col] != '1') continue;
                float u = (i * 4 + col + .5f) * pixel - width / 2, v = (2 - row) * pixel;
                Cube(parent, $"c{i}_r{row}_p{col}", horizontal ? new Vector3(pixel*.84f,pixel*.84f,.0007f) : new Vector3(.0007f,pixel*.84f,pixel*.84f),
                    position + (horizontal ? new Vector3(v,u,.0005f) : new Vector3(.0005f,u,v)), material);
            }
        }
        static void BuildGeometry(JObject d, JObject layout, ObjectBinding b, Dictionary<string, Material> m)
        {
            var size = Vector(d["dimensions_m"]); float x = size.x, y = size.y, z = size.z;
            var v = b.visual; float primary = (float)layout["label_style"]["primary_label_pixel_m"];
            Transform Box(string name, Vector3 s, Vector3 p = default, string mat = "neutral") => Cube(v, name, s, p, m[mat]);
            void Label(string text, Vector3 p, float pixel, bool horizontal = false, Transform parent = null) => Text(Child(parent ?? v, "Text"), text, p, pixel, horizontal, m["ink"]);
            switch (b.kind)
            {
                case "washer":
                    var mesh = Washer(size); string meshPath = Generated + "/Meshes/washer.asset";
                    var existing = AssetDatabase.LoadAssetAtPath<Mesh>(meshPath);
                    if (existing == null) { AssetDatabase.CreateAsset(mesh, meshPath); existing = mesh; } else UnityEngine.Object.DestroyImmediate(mesh);
                    var ring = Child(v, "Ring"); ring.gameObject.AddComponent<MeshFilter>().sharedMesh = existing; ring.gameObject.AddComponent<MeshRenderer>().sharedMaterial = m["washer"]; break;
                case "tray": case "container": case "cup":
                    const float wall = .004f;
                    Box("Bottom", new(x,y,wall), new(0,0,-z/2+wall/2));
                    Box("Back", new(wall,y,z), new(-x/2+wall/2,0,0)); Box("Front",new(wall,y,z),new(x/2-wall/2,0,0));
                    Box("Left",new(x,wall,z),new(0,-y/2+wall/2,0)); Box("Right",new(x,wall,z),new(0,y/2-wall/2,0));
                    if (b.label.Length != 0)
                    {
                        float pixel = b.kind == "tray" ? primary : Mathf.Min((float)layout["label_style"]["secondary_cup_max_pixel_m"], y*.85f/(b.label.Length*4+1));
                        float labelZ = (float?)d["label_offset_z_m"] ?? 0;
                        Box("Label",new(.001f,y*.90f,.020f),new(x/2+.0007f,0,labelZ),"label");
                        if (labelZ != 0) Box("LabelPost",new(.003f,.008f,labelZ),new(x/2,0,labelZ/2));
                        Label(b.label,new(x/2+.0013f,0,labelZ),pixel); b.primaryPixelMetres = pixel;
                    } break;
                case "surface":
                    Box("Top",size,default,(string)d["material"]);
                    foreach (float xx in new[]{-x*.4f,x*.4f}) foreach (float yy in new[]{-y*.4f,y*.4f}) Box("Leg",new(.035f,.035f,.79f),new(xx,yy,-.41f),"surface"); break;
                case "card":
                    Box("Card",size,default,"card"); float cardPixel = (float)layout["label_style"]["card_face_pixel_m"];
                    Label("0",new(0,0,z/2+.0005f),cardPixel,true); var back = Child(v,"BackFace"); back.localRotation = SceneCoordinates.AxisRotation(Vector3.right,Mathf.PI);
                    Label("1",new(0,0,z/2+.0005f),cardPixel,true,back); break;
                case "arrow":
                    Cube(b.root,"MarkedSlot",new(x,.006f,.001f),new(0,0,-.002f),m["label"]);
                    Box("Shaft",new(x*.6f,.006f,z),new(-x*.1f,0,0),"arrow");
                    foreach (int sign in new[]{-1,1})
                    {
                        // Frozen USD authors Translate, Scale, Rotate, hence T*S*R.
                        // A scaled parent and rotated unit cube preserve that exact order.
                        var scaled=Child(v,"HeadScale"); scaled.localPosition=SceneCoordinates.Position(new(x*.25f,sign*x*.1f,0));
                        scaled.localScale=new(.006f,z,x*.35f);
                        Cube(scaled,"Head",Vector3.one,Vector3.zero,m["arrow"]).localRotation=SceneCoordinates.AxisRotation(Vector3.forward,-sign*Mathf.PI/4);
                    } break;
                case "lid": Box("Panel",size,new(x/2,0,0),(string)d["material"]); break;
                case "code":
                    Box("Plate",size,default,"label"); Box("Post",new(.002f,.006f,.055f),new(0,0,-.038f));
                    Label(b.label,new(x/2+.0005f,0,0),primary); b.primaryPixelMetres = primary; break;
                case "quarantine":
                    foreach (float yy in new[]{-y/2,y/2}) Box("EdgeY",new(x,.003f,z),new(0,yy,0),"quarantine");
                    foreach (float xx in new[]{-x/2,x/2}) Box("EdgeX",new(.003f,y,z),new(xx,0,0),"quarantine");
                    Label(b.label,new(0,0,z/2+.0005f),primary,true); b.primaryPixelMetres = primary; break;
                case "tag": Box("Plate",size,default,"tag"); Box("Clip",new(.005f,y*.6f,z*1.5f),new(-x/2,0,.003f),"washer"); break;
                default: throw new InvalidDataException("Unsupported object kind " + b.kind);
            }
        }
        static Mesh Washer(Vector3 size)
        {
            var points = new List<Vector3>(); var indices = new List<int>(); const int n = 32;
            foreach (float z in new[]{-size.z/2,size.z/2}) foreach (float r in new[]{size.x/4,size.x/2})
                for (int i=0;i<n;i++) points.Add(SceneCoordinates.Position(new(r*Mathf.Cos(i*2*Mathf.PI/n),r*Mathf.Sin(i*2*Mathf.PI/n),z)));
            void Quad(int a,int b,int c,int d) { indices.AddRange(new[]{a,c,b,a,d,c}); }
            for(int i=0;i<n;i++) { int j=(i+1)%n; Quad(i,j,n+j,n+i); Quad(2*n+i,3*n+i,3*n+j,2*n+j); Quad(i,2*n+i,2*n+j,j); Quad(n+i,n+j,3*n+j,3*n+i); }
            var mesh=new Mesh{name="Washer32"};mesh.SetVertices(points);mesh.SetTriangles(indices,0);mesh.RecalculateNormals();mesh.RecalculateBounds();return mesh;
        }
        public static void BuildAndroid() { Configure(); FoundationBuild.BuildAndroid(); }
        public static void BuildWindows() { Configure(); FoundationBuild.BuildWindows(); }
        public static void Capture()
        {
            Configure(); var layout=ReadLayout(); var observer=layout["observer"];
            string output=Environment.GetEnvironmentVariable("WORKCELL_CAPTURE_OUTPUT") ?? throw new InvalidOperationException("Set WORKCELL_CAPTURE_OUTPUT.");
            if(Directory.Exists(output))throw new IOException("Use a fresh capture directory."); Directory.CreateDirectory(output);
            var registry=UnityEngine.Object.FindAnyObjectByType<WorkcellRegistry>(FindObjectsInactive.Include); registry.transform.parent.gameObject.SetActive(true);
            var go=new GameObject("EngineeringCaptureCamera");var camera=go.AddComponent<Camera>();
            camera.clearFlags=CameraClearFlags.SolidColor;camera.backgroundColor=new Color(.95f,.95f,.95f);camera.nearClipPlane=.01f;camera.farClipPlane=20;
            int width=(int)observer["width"],height=(int)observer["height"];
            camera.fieldOfView=2*Mathf.Atan((float)observer["horizontal_aperture_mm"]*height/width/(2*(float)observer["focal_length_mm"]))*Mathf.Rad2Deg;
            camera.transform.position=Position(observer["position_m"]);camera.transform.LookAt(Position(observer["look_at_m"]),Vector3.up);
            var target=new RenderTexture(width,height,24){antiAliasing=4};camera.targetTexture=target;camera.Render();
            var previous=RenderTexture.active;RenderTexture.active=target;var image=new Texture2D(width,height,TextureFormat.RGB24,false);
            image.ReadPixels(new Rect(0,0,width,height),0,0);image.Apply();RenderTexture.active=previous;
            File.WriteAllBytes(Path.Combine(output,"observer.png"),image.EncodeToPNG());
            var evidence=new JObject{["layout_sha256"]=ExpectedLayoutHash,["objects"]=new JArray(registry.objects.Select(o=>new JObject{["id"]=o.id,["label"]=o.label,["position_unity_m"]=new JArray(o.root.localPosition.x,o.root.localPosition.y,o.root.localPosition.z),["rotation_unity_xyzw"]=new JArray(o.root.localRotation.x,o.root.localRotation.y,o.root.localRotation.z,o.root.localRotation.w)})),["camera"]=observer.DeepClone(),["image_sha256"]=FoundationBuild.Hash(File.ReadAllBytes(Path.Combine(output,"observer.png")))};
            File.WriteAllText(Path.Combine(output,"capture.json"),evidence.ToString()+"\n");
            UnityEngine.Object.DestroyImmediate(image);target.Release();UnityEngine.Object.DestroyImmediate(target);UnityEngine.Object.DestroyImmediate(go);
            Debug.Log("WORKCELL_CAPTURE_COMPLETE");
        }
    }
}
