using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.Serialization;
using System.Text;
using AcousticVocab.Foundation;
using AcousticVocab.Foundation.Editor;
using AcousticVocab.ResponsePanel;
using AcousticVocab.ResponsePanel.Editor;
using AcousticVocab.SelectionMenus;
using AcousticVocab.StudyAudio;
using AcousticVocab.StudyAudio.Editor;
using AcousticVocab.Teaching;
using AcousticVocab.Theme;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace AcousticVocab.StoryboardScreens.Editor
{
    // Editor-only captures of the storyboard participant screens (frames 1 and
    // 8-11) for private review. Synthetic DEMO text, a generated picture and
    // synthetic tones only; never a participant configuration, study audio or
    // tracked headset state. Output goes to AV_STORYBOARD_CAPTURE_DIR, which
    // must be an absolute path outside this checkout; images are never committed.
    public static class StoryboardCaptureScreens
    {
        const BindingFlags Private = BindingFlags.NonPublic | BindingFlags.Instance;
        const int Width = 1920, Height = 1080;

        public static void Capture()
        {
            try { Run(); EditorApplication.Exit(0); }
            catch (Exception error) { Debug.LogError("STORYBOARD_CAPTURE_FAILED " + error); EditorApplication.Exit(1); }
        }

        static void Run()
        {
            string directory = Environment.GetEnvironmentVariable("AV_STORYBOARD_CAPTURE_DIR") ?? throw new ArgumentException("Set AV_STORYBOARD_CAPTURE_DIR");
            string checkout = Path.GetFullPath(FoundationBuild.RepositoryRoot) + Path.DirectorySeparatorChar;
            if (!Path.IsPathRooted(directory) || Path.GetFullPath(directory).StartsWith(checkout, StringComparison.Ordinal)) throw new ArgumentException("Use an absolute directory outside the checkout");
            Directory.CreateDirectory(directory);
            Level(directory);
            Panel(directory, "frame-08-test-trial.png", PanelMode.FullMessage, PanelRole.Command, false);
            Panel(directory, "frame-08-atomic-probe.png", PanelMode.AtomicProbe, PanelRole.Target, false);
            Panel(directory, "frame-09-commit-hover.png", PanelMode.FullMessage, PanelRole.Command, true);
            foreach (bool readOnly in new[] { false, true })
                foreach (var phase in new[] { MenuPhase.Choice, MenuPhase.Selected })
                    Menu(directory, (readOnly ? "frame-11-assigned-" : "frame-10-active-") + (phase == MenuPhase.Choice ? "choice" : "selected") + ".png", readOnly, phase);
            Debug.Log("STORYBOARD_CAPTURE_DONE " + directory);
        }

        // Frame 1: the comfortable-level screen in the calibration scene.
        static void Level(string directory)
        {
            AudioBuild.Configure();
            var host = UnityEngine.Object.FindAnyObjectByType<AudioCalibrationHost>();
            // Picture-only state: three controls enabled and a mid level.
            host.gainText.text = "Level: 45%"; CalibrationLevelView.Show(host.levelFill, host.levelKnob, .45f);
            host.statusText.text = "Example 1 of 3. Choose a comfortable level, then listen.";
            host.listenButton.interactable = host.quieterButton.interactable = host.louderButton.interactable = true;
            var camera = host.foundation.observerCamera;
            var canvas = host.canvas.transform;
            camera.transform.SetPositionAndRotation(canvas.position - canvas.forward * 1.15f, Quaternion.LookRotation(canvas.forward, Vector3.up));
            Render(camera, Path.Combine(directory, "frame-01-level.png"), 50);
        }

        // Frames 8 and 9: the response panel in the workcell scene.
        static void Panel(string directory, string file, PanelMode mode, PanelRole role, bool hover)
        {
            ResponsePanelBuild.Configure();
            var foundation = UnityEngine.Object.FindAnyObjectByType<FoundationBootstrap>();
            var controller = PanelPreview.Populate(foundation, mode, role);
            var state = controller.State;
            if (role != PanelRole.Action) state.SelectTarget("G");
            if (role != PanelRole.Target) state.SelectAction("TAG");
            var type = typeof(ResponsePanelController);
            if (hover)
            {
                var keys = ((System.Collections.IEnumerable)type.GetField("keys", Private).GetValue(controller)).Cast<object>();
                var commit = keys.Single(k => (string)k.GetType().GetField("Value").GetValue(k) == "commit");
                type.GetField("hovered", Private).SetValue(controller, commit);
                var reference = StationConfig.ReferencePose(foundation.Configuration);
                var ray = (LineRenderer)type.GetField("ray", Private).GetValue(controller);
                var end = ((BoxCollider)commit.GetType().GetField("Collider").GetValue(commit)).transform.position;
                ray.useWorldSpace = true; ray.enabled = true;
                ray.SetPosition(0, reference.position + reference.rotation * new Vector3(.22f, -.42f, .2f)); ray.SetPosition(1, end);
            }
            type.GetMethod("Refresh", Private).Invoke(controller, null);
            var camera = foundation.observerCamera;
            camera.transform.SetPositionAndRotation(new Vector3(0, 1.5f, 1.45f), Quaternion.LookRotation(new Vector3(0, -.18f, -.7f), Vector3.up));
            Render(camera, Path.Combine(directory, file), 60);
        }

        // Frames 10 and 11: the Study B menu, active and assigned roles.
        static void Menu(string directory, string file, bool readOnly, MenuPhase phase)
        {
            ResponsePanelBuild.Configure();
            var foundation = UnityEngine.Object.FindAnyObjectByType<FoundationBootstrap>();
            var config = StationConfig.ParseStrict(File.ReadAllText(Path.Combine(FoundationBuild.RepositoryRoot, "apparatus/examples/station.example.json")));
            config["observer_reference"]["position_m"] = new JArray(0, 1.5, 1.45); config["observer_reference"]["rotation_xyzw"] = new JArray(0, 1, 0, 0);
            typeof(FoundationBootstrap).GetField("configuration", Private).SetValue(foundation, config);
            foundation.presentationRoot.SetActive(true);
            var host = new GameObject("Storyboard capture menu").AddComponent<MenuSessionHost>();
            host.foundation = foundation; host.presentationParent = foundation.presentationRoot.transform; host.font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            var type = typeof(MenuSessionHost);
            type.GetMethod("CreateView", Private).Invoke(host, null);
            type.GetField("factory", Private).SetValue(host, FormatterServices.GetUninitializedObject(typeof(MenuContentFactory)));
            type.GetProperty("InputAvailable").GetSetMethod(true).Invoke(host, new object[] { true });
            var material = Material(readOnly ? "DEMO: This sound is assigned to you." : "DEMO: Choose the sound for ALIGN ARROW.");
            host.Prepare(material);
            host.Show(readOnly ? "DEMO-assigned" : "DEMO-active", material, phase, phase == MenuPhase.Selected ? 2 : (int?)null, readOnly);
            var camera = foundation.observerCamera; var reference = StationConfig.ReferencePose(config);
            camera.transform.SetPositionAndRotation(reference.position, reference.rotation * Quaternion.Euler(8, 0, 0));
            Render(camera, Path.Combine(directory, file), 60);
            type.GetField("factory", Private).SetValue(host, null);
        }

        static MenuMaterial Material(string choice)
        {
            var picture = new Texture2D(240, 170, TextureFormat.RGBA32, false);
            var pixels = new Color32[240 * 170];
            for (int y = 0; y < 170; y++)
                for (int x = 0; x < 240; x++)
                {
                    bool shaft = y >= 78 && y < 92 && x >= 50 && x < 160, head = x >= 160 && x < 200 && Mathf.Abs(y - 85) < (200 - x) * .9f;
                    bool frame = x < 3 || y < 3 || x >= 237 || y >= 167;
                    pixels[y * 240 + x] = shaft || head ? (Color32)StudyTheme.EdgeLit : frame ? (Color32)StudyTheme.EdgeIdle : (Color32)StudyTheme.SurfaceEnabled;
                }
            picture.SetPixels32(pixels); picture.Apply(); byte[] png = picture.EncodeToPNG(); UnityEngine.Object.DestroyImmediate(picture);
            var meaning = (TeachingDisplay)Make(typeof(TeachingDisplay), new JObject { ["meaning_display_id"] = "DEMO-meaning", ["definition"] = "DEMO: align the arrow",
                ["action_words"] = "DEMO align", ["target_words"] = "DEMO arrow", ["image_id"] = "DEMO-image" }, png, PcmWave.Hash(png));
            var options = Enumerable.Range(1, 3).Select(i => (MenuOption)Make(typeof(MenuOption), "DEMO-option-" + i, Tone(21600, i * 1000))).ToArray();
            return (MenuMaterial)Make(typeof(MenuMaterial), "K-a3", "DEMO-meaning", meaning, "DEMO: Listen to the three options.", choice, new[] { "Option 1", "Option 2", "Option 3" }, options);
        }

        static object Make(Type type, params object[] args) => type.GetConstructors(Private | BindingFlags.Public).Single(c => c.GetParameters().Length == args.Length).Invoke(args);

        static PcmWave Tone(int samples, int level)
        {
            using var stream = new MemoryStream(); using var writer = new BinaryWriter(stream);
            writer.Write(Encoding.ASCII.GetBytes("RIFF")); writer.Write(36 + samples * 2); writer.Write(Encoding.ASCII.GetBytes("WAVEfmt ")); writer.Write(16); writer.Write((short)1); writer.Write((short)1);
            writer.Write(48000); writer.Write(96000); writer.Write((short)2); writer.Write((short)16); writer.Write(Encoding.ASCII.GetBytes("data")); writer.Write(samples * 2);
            for (int i = 0; i < samples; i++) writer.Write((short)(i % 100 < 50 ? level : -level)); writer.Flush(); return PcmWave.ParseCanonical(stream.ToArray());
        }

        static void Render(Camera camera, string path, float fieldOfView)
        {
            Canvas.ForceUpdateCanvases();
            camera.fieldOfView = fieldOfView; camera.clearFlags = CameraClearFlags.SolidColor; camera.backgroundColor = new Color(.02f, .03f, .045f);
            var target = new RenderTexture(Width, Height, 24) { antiAliasing = 4 }; var pixels = new Texture2D(Width, Height, TextureFormat.RGB24, false);
            var previous = RenderTexture.active; camera.targetTexture = target;
            try
            {
                camera.Render(); RenderTexture.active = target; pixels.ReadPixels(new Rect(0, 0, Width, Height), 0, 0); pixels.Apply();
                File.WriteAllBytes(path, pixels.EncodeToPNG()); Debug.Log("STORYBOARD_CAPTURE " + Path.GetFileName(path));
            }
            finally { camera.targetTexture = null; RenderTexture.active = previous; UnityEngine.Object.DestroyImmediate(target); UnityEngine.Object.DestroyImmediate(pixels); }
        }
    }
}
