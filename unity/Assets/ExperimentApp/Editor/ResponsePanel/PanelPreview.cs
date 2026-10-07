using System;
using System.IO;
using System.Linq;
using System.Reflection;
using AcousticVocab.Foundation;
using AcousticVocab.Foundation.Editor;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace AcousticVocab.ResponsePanel.Editor
{
    // Editor-only rendering harness. Never supplies tracked HMD state or participant configuration.
    public static class PanelPreview
    {
        static readonly BindingFlags Flags = BindingFlags.NonPublic | BindingFlags.Instance;
        public static ResponsePanelController Populate(FoundationBootstrap foundation, PanelMode mode, PanelRole role, bool select = false)
        {
            if (Application.isPlaying) throw new InvalidOperationException("Preview is edit-mode only");
            var config = StationConfig.ParseStrict(File.ReadAllText(Path.Combine(FoundationBuild.RepositoryRoot, "apparatus/examples/station.example.json")));
            config["observer_reference"]["position_m"] = new JArray(0, 1.5, 1.45);
            config["observer_reference"]["rotation_xyzw"] = new JArray(0, 1, 0, 0);
            typeof(FoundationBootstrap).GetField("configuration", Flags).SetValue(foundation, config);
            var source = StationConfig.ParseStrict(File.ReadAllText(Path.Combine(FoundationBuild.RepositoryRoot, "apparatus/response-panel/response-panel.example.json")));
            source["configuration_status"] = "provisioned_engineering";
            var settings = PanelSettings.Parse(source.ToString(), File.ReadAllText("Assets/ExperimentApp/Resources/ResponsePanelSchema.json"), "engineering-pending-review");
            var controller = ResponsePanelBuild.AddToOpenScene();
            typeof(ResponsePanelController).GetField("settings", Flags).SetValue(controller, settings);
            var request = new PanelRequest("synthetic-editor-preview", mode, role, 0, mode == PanelMode.Practice ? 60000 : (double?)null);
            var state = new ResponseState(() => request.OpensMonoMs, _ => { }); state.Open(request);
            typeof(ResponsePanelController).GetProperty("State").SetValue(controller, state);
            typeof(ResponsePanelController).GetMethod("CreatePresentation", Flags).Invoke(controller, new object[] { StationConfig.ReferencePose(config) });
            if (select) { state.SelectTarget("B"); state.SelectAction("FLIP_CARD"); }
            foundation.presentationRoot.SetActive(true);
            var panel = (Transform)typeof(ResponsePanelController).GetField("panel", Flags).GetValue(controller); panel.gameObject.SetActive(true);
            typeof(ResponsePanelController).GetMethod("Refresh", Flags).Invoke(controller, null);
            return controller;
        }
        public static void Capture()
        {
            string directory = Environment.GetEnvironmentVariable("RESPONSE_PANEL_CAPTURE_OUTPUT") ?? throw new ArgumentException("Private capture path required");
            string prefix = Path.GetFullPath(Path.Combine(FoundationBuild.RepositoryRoot, ".local")) + Path.DirectorySeparatorChar;
            if (!Path.GetFullPath(directory).StartsWith(prefix, StringComparison.OrdinalIgnoreCase) || Directory.Exists(directory)) throw new ArgumentException("Use a fresh directory inside this checkout .local");
            Directory.CreateDirectory(directory); var records = new JArray();
            foreach (var item in new[] { ("full-empty", PanelMode.FullMessage, PanelRole.Command, false), ("full-selected", PanelMode.FullMessage, PanelRole.Command, true),
                ("atomic-action", PanelMode.AtomicProbe, PanelRole.Action, false), ("atomic-target", PanelMode.AtomicProbe, PanelRole.Target, false),
                ("lesson-atomic", PanelMode.LessonAtomic, PanelRole.Action, false), ("lesson-message", PanelMode.LessonMessage, PanelRole.Command, false), ("practice", PanelMode.Practice, PanelRole.Command, false) })
            {
                ResponsePanelBuild.Configure(); var foundation = UnityEngine.Object.FindAnyObjectByType<FoundationBootstrap>();
                Populate(foundation, item.Item2, item.Item3, item.Item4);
                var camera = foundation.observerCamera; camera.transform.SetPositionAndRotation(new Vector3(0, 1.5f, 1.45f), Quaternion.LookRotation(new Vector3(0, -.18f, -.7f), Vector3.up));
                camera.fieldOfView = 60; var target = new RenderTexture(1920, 1080, 24); var pixels = new Texture2D(1920, 1080, TextureFormat.RGB24, false);
                camera.targetTexture = target; var previous = RenderTexture.active;
                try
                {
                    camera.Render(); RenderTexture.active = target; pixels.ReadPixels(new Rect(0, 0, 1920, 1080), 0, 0); pixels.Apply();
                    byte[] png = pixels.EncodeToPNG(); string name = item.Item1 + ".png"; File.WriteAllBytes(Path.Combine(directory, name), png);
                    records.Add(new JObject { ["file"] = name, ["sha256"] = FoundationBuild.Hash(png), ["mode"] = item.Item2.ToString(), ["role"] = item.Item3.ToString(),
                        ["selected"] = item.Item4, ["active_button_labels"] = new JArray(foundation.presentationRoot.GetComponentsInChildren<TextMesh>().Select(x => x.text)),
                        ["glyph_metrics"] = new JArray(foundation.presentationRoot.GetComponentsInChildren<TextMesh>().Where(x => x.text.Length > 0).Select(x => new JObject {
                            ["label"] = x.text, ["scale"] = x.transform.localScale.x, ["local_height"] = x.GetComponent<MeshRenderer>().localBounds.size.y,
                            ["world_height"] = x.GetComponent<MeshRenderer>().bounds.size.y, ["world_width"] = x.GetComponent<MeshRenderer>().bounds.size.x,
                            ["ink_height_m"] = PanelTypography.LocalInkHeight(x) * x.transform.localScale.y,
                            ["font_size"] = x.fontSize, ["character_size"] = x.characterSize })) });
                }
                finally { RenderTexture.active = previous; camera.targetTexture = null; UnityEngine.Object.DestroyImmediate(target); UnityEngine.Object.DestroyImmediate(pixels); }
            }
            File.WriteAllText(Path.Combine(directory, "capture.json"), new JObject { ["status"] = "engineering_editor_preview_not_headset_legibility", ["width"] = 1920, ["height"] = 1080, ["captures"] = records }.ToString() + "\n");
            Debug.Log("RESPONSE_PANEL_PREVIEW_CAPTURED count=7");
        }
    }
}
