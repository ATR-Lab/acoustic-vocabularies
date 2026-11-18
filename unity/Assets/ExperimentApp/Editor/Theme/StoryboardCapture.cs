using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.Serialization;
using AcousticVocab.Foundation;
using AcousticVocab.Foundation.Editor;
using AcousticVocab.Orientation;
using AcousticVocab.ResponsePanel;
using AcousticVocab.Teaching;
using AcousticVocab.Workcell.Editor;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.UI;

namespace AcousticVocab.Theme.Editor
{
    // Editor-only review captures of the storyboard panels (frames 2-7). Each
    // screen is built by the real view code in a scratch scene, rendered by one
    // camera to a RenderTexture and written as PNG to AV_STORYBOARD_CAPTURE_DIR
    // (required, never under Assets/). The scene is never saved. Lesson text is
    // example text and the lesson picture is a procedural placeholder: no study
    // content, audio or storyboard image is read.
    //
    //   Unity -batchmode -projectPath unity -executeMethod AcousticVocab.Theme.Editor.StoryboardCapture.CaptureAll -quit
    public static class StoryboardCapture
    {
        const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
        const int Width = 1920, Height = 1080;

        public static void CaptureAll()
        {
            try { Run(); Debug.Log("STORYBOARD_CAPTURE_COMPLETE"); }
            catch (Exception error)
            {
                Debug.LogError("STORYBOARD_CAPTURE_FAILED " + error);
                if (Application.isBatchMode) EditorApplication.Exit(1);
                else throw;
            }
        }

        static string OutputDirectory()
        {
            string value = Environment.GetEnvironmentVariable("AV_STORYBOARD_CAPTURE_DIR");
            if (string.IsNullOrWhiteSpace(value)) throw new InvalidOperationException("Set AV_STORYBOARD_CAPTURE_DIR to a directory outside the project Assets folder.");
            string full = Path.GetFullPath(value), assets = Path.GetFullPath(Application.dataPath);
            if (full.TrimEnd(Path.DirectorySeparatorChar).StartsWith(assets.TrimEnd(Path.DirectorySeparatorChar), StringComparison.Ordinal))
                throw new InvalidOperationException("AV_STORYBOARD_CAPTURE_DIR must not be under Assets/.");
            Directory.CreateDirectory(full);
            return full;
        }

        static void Run()
        {
            string output = OutputDirectory();
            var frames = new (string File, Action<Stage> Build)[]
            {
                ("frame-02-orientation.png", s => Orientation(s, false)),
                ("frame-03-practice-correct.png", s => Orientation(s, true)),
                ("frame-04-grammar.png", Grammar),
                ("frame-05-lesson-listen.png", s => Lesson(s, LessonDisplayPhase.Definition, false, true)),
                ("frame-05-lesson-your-turn.png", s => Lesson(s, LessonDisplayPhase.Retrieval, false, true)),
                ("frame-06-aligned.png", s => Lesson(s, LessonDisplayPhase.Definition, true, false)),
                ("frame-07-static.png", s => Lesson(s, LessonDisplayPhase.Definition, false, false)),
            };
            var record = new JObject { ["width"] = Width, ["height"] = Height, ["frames"] = new JArray() };
            foreach (var (file, build) in frames)
            {
                var stage = Stage.Open();
                build(stage);
                stage.Render(Path.Combine(output, file));
                ((JArray)record["frames"]).Add(new JObject { ["file"] = file, ["backdrop"] = stage.Backdrop });
                stage.Close();
            }
            File.WriteAllText(Path.Combine(output, "capture.json"), record.ToString() + "\n");
        }

        // A scratch scene with the workcell when it can be built here, a
        // neutral floor otherwise, a synthetic foundation and one camera.
        sealed class Stage
        {
            public string Backdrop;
            public FoundationBootstrap Foundation;
            public GameObject Presentation, Host;
            public Camera Camera;
            public Pose Reference;

            public static Stage Open()
            {
                var stage = new Stage();
                EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                Vector3 eye = new Vector3(0, 1.2f, 0); Quaternion yaw = Quaternion.identity;
                try
                {
                    EditorSceneManager.OpenScene(FoundationBuild.ScenePath, OpenSceneMode.Single);
                    var registry = WorkcellBuild.AddToOpenScene();
                    var bootstrap = UnityEngine.Object.FindAnyObjectByType<FoundationBootstrap>(FindObjectsInactive.Include);
                    bootstrap.presentationRoot.SetActive(true);
                    foreach (var other in UnityEngine.Object.FindObjectsByType<Camera>(FindObjectsInactive.Include, FindObjectsSortMode.None)) other.enabled = false;
                    var observer = WorkcellBuild.ReadLayout()["observer"];
                    eye = WorkcellBuild.Position(observer["position_m"]);
                    var look = WorkcellBuild.Position(observer["look_at_m"]) - eye; look.y = 0;
                    yaw = Quaternion.LookRotation(look.normalized, Vector3.up);
                    stage.Backdrop = "workcell (layout and converted G1 description; robot in its neutral pose)";
                    stage.Presentation = new GameObject("Storyboard capture presentation");
                }
                catch (Exception error)
                {
                    Debug.LogWarning("STORYBOARD_CAPTURE_NEUTRAL_BACKDROP " + error.Message);
                    EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                    var floor = GameObject.CreatePrimitive(PrimitiveType.Plane); floor.name = "Neutral floor";
                    floor.transform.localScale = Vector3.one * 2; floor.GetComponent<Renderer>().sharedMaterial = new Material(Shader.Find("Unlit/Color")) { color = new Color(.16f, .18f, .2f) };
                    stage.Backdrop = "neutral";
                    stage.Presentation = new GameObject("Storyboard capture presentation");
                }
                // Panels are placed from a level reference pose, as at the station;
                // the camera looks slightly down so the workcell shows below them.
                stage.Reference = new Pose(eye, yaw);
                var holder = new GameObject("Synthetic foundation"); holder.SetActive(false);
                stage.Foundation = holder.AddComponent<FoundationBootstrap>();
                var q = yaw;
                var config = new JObject { ["observer_reference"] = new JObject { ["position_m"] = new JArray(eye.x, eye.y, eye.z), ["rotation_xyzw"] = new JArray(q.x, q.y, q.z, q.w) } };
                typeof(FoundationBootstrap).GetField("configuration", Private).SetValue(stage.Foundation, config);
                stage.Foundation.presentationRoot = stage.Presentation;
                stage.Host = new GameObject("Capture host");
                var cam = new GameObject("Storyboard capture camera").AddComponent<Camera>();
                cam.transform.SetPositionAndRotation(eye, yaw * Quaternion.Euler(8, 0, 0));
                cam.fieldOfView = 70; cam.nearClipPlane = .05f; cam.farClipPlane = 30;
                cam.clearFlags = CameraClearFlags.SolidColor; cam.backgroundColor = new Color(.2f, .23f, .26f);
                stage.Camera = cam;
                return stage;
            }

            public void Render(string path)
            {
                Canvas.ForceUpdateCanvases();
                var target = new RenderTexture(Width, Height, 24) { antiAliasing = 4 };
                Camera.targetTexture = target; Camera.Render();
                var previous = RenderTexture.active; RenderTexture.active = target;
                var image = new Texture2D(Width, Height, TextureFormat.RGB24, false);
                image.ReadPixels(new Rect(0, 0, Width, Height), 0, 0); image.Apply();
                RenderTexture.active = previous; Camera.targetTexture = null;
                File.WriteAllBytes(path, image.EncodeToPNG());
                UnityEngine.Object.DestroyImmediate(image); target.Release(); UnityEngine.Object.DestroyImmediate(target);
            }

            public void Close() => EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        }

        static Font Font => Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");

        static OrientationPlan Plan()
        {
            var v = JObject.Parse(File.ReadAllText(Path.Combine(FoundationBuild.RepositoryRoot, "apparatus/orientation/orientation-plan.example.json")));
            return OrientationPlan.Parse(v.ToString(), (string)v["protocol_version"]);
        }

        // Frames 2 and 3: the real orientation screen, driven to the action
        // demonstration or to feedback after a correct practice answer.
        static void Orientation(Stage stage, bool practice)
        {
            var plan = Plan(); double now = 0;
            var flow = new OrientationFlow(plan, () => now, _ => { });
            flow.Start();
            for (int i = 0; i < 2; i++) { now += 10000; flow.CompleteDemo(flow.CurrentCard.Id, 10000, 10000, 1000d / 30); flow.Next(); }
            if (practice)
            {
                for (int i = 2; i < 8; i++) { now += 10000; flow.CompleteDemo(flow.CurrentCard.Id, 10000, 10000, 1000d / 30); flow.Next(); }
                for (int i = 0; i < 8; i++) flow.Next();
                var item = flow.CurrentItem; var panel = new ResponseState(() => now, _ => { }); panel.Responded += flow.Respond; panel.Open(flow.OpenPractice());
                panel.SelectTarget(item.Target); panel.SelectAction(item.Action); now += 1; panel.Commit();
            }
            var host = stage.Host.AddComponent<OrientationHost>();
            host.foundation = stage.Foundation; host.font = Font; host.shader = Shader.Find("Unlit/Color");
            var setup = (OrientationSetup)FormatterServices.GetUninitializedObject(typeof(OrientationSetup));
            typeof(OrientationSetup).GetProperty("Plan").SetValue(setup, plan);
            typeof(OrientationHost).GetField("setup", Private).SetValue(host, setup);
            typeof(OrientationHost).GetProperty("Flow").SetValue(host, flow);
            typeof(OrientationHost).GetField("initialized", Private).SetValue(host, true);
            typeof(OrientationHost).GetMethod("CreateDisplay", Private).Invoke(host, new object[] { stage.Reference });
        }

        static TeachingSessionHost Teaching(Stage stage)
        {
            var host = stage.Host.AddComponent<TeachingSessionHost>();
            host.foundation = stage.Foundation; host.presentationParent = stage.Presentation.transform; host.font = Font;
            typeof(TeachingSessionHost).GetMethod("CreateView", Private).Invoke(host, null);
            return host;
        }

        static void Grammar(Stage stage)
        {
            var host = Teaching(stage);
            typeof(TeachingSessionHost).GetMethod("ShowGrammar", Private).Invoke(host, new object[] { "roles" });
        }

        // Frames 5-7. Show() needs an installed lesson factory, so the capture
        // fills the same fields Show() fills, then uses the public view hooks.
        static void Lesson(Stage stage, LessonDisplayPhase phase, bool aligned, bool atomic)
        {
            var host = Teaching(stage);
            var canvas = stage.Presentation.GetComponentsInChildren<Canvas>(true).Single(x => x.name == "Teaching presentation");
            Text Label(string name) => canvas.transform.Find(name).GetComponent<Text>();
            const string owner = "capture";
            Label("Definition").text = atomic ? "Tag" : "Tag container G";
            Label("Action words").text = "tag"; Label("Target words").text = atomic ? "" : "container G"; Label("Feedback").text = "";
            var picture = canvas.transform.Find("Task image").GetComponent<RawImage>();
            picture.gameObject.SetActive(true); picture.texture = StoryIcons.Texture(atomic ? StoryIcon.Tag : StoryIcon.Box); picture.color = StudyTheme.EdgeIdle;
            typeof(TeachingSessionHost).GetMethod("SetLessonStrips", Private).Invoke(host, new object[] { true, !atomic });
            typeof(TeachingSessionHost).GetField("visibleOwner", Private).SetValue(host, owner);
            canvas.gameObject.SetActive(true);
            var question = atomic ? LessonQuestion.Action : LessonQuestion.Message;
            host.Step(owner, LessonDisplayPhase.Definition, question);
            if (phase == LessonDisplayPhase.Retrieval) { host.Hide(owner); host.Step(owner, LessonDisplayPhase.Retrieval, question); }
            if (aligned) { host.Highlight(owner, LessonHighlight.Action); host.Playhead(owner, LessonHighlight.Action, .45f); }
        }
    }
}
