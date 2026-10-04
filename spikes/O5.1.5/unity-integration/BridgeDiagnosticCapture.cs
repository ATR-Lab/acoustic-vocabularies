using System;
using System.IO;
using System.Text;
using UnityEngine;
using UnityEngine.InputSystem.XR;
using AcousticVocab.Spikes.Urdf;

namespace AcousticVocab.Spikes.Bridge
{
    // Activated only by explicit diagnostic output CLI argument. Never changes a device run by default.
    public sealed class BridgeDiagnosticCapture : MonoBehaviour
    {
        public BridgeBenchmark benchmark;
        public RobotHierarchy robot;
        string output;
        int applied;
        bool captured;
        double deadline;
        void OnEnable() { if (benchmark != null) benchmark.FrameReceived += OnFrame; }
        void OnDisable() { if (benchmark != null) benchmark.FrameReceived -= OnFrame; }
        void OnFrame(BridgeBenchmark.StateFrame frame) { applied++; }
        static void DurableWrite(string path, byte[] bytes)
        {
            string temporary = path + ".tmp";
            using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            { stream.Write(bytes, 0, bytes.Length); stream.Flush(true); }
            File.Move(temporary, path); // existing evidence is never overwritten
        }
        void Start()
        {
            var args = Environment.GetCommandLineArgs(); int index = Array.IndexOf(args, "-bridgeDiagnosticOutput");
            if (index < 0 || index + 1 >= args.Length) { enabled = false; return; }
            output = args[index + 1]; Directory.CreateDirectory(output);
            if (!benchmark.settings.diagnostic_apply) throw new InvalidOperationException("Capture requires diagnostic_apply=true");
            deadline = Time.realtimeSinceStartupAsDouble + benchmark.settings.duration_s + benchmark.settings.warmup_timeout_s + 15;
            foreach (var driver in FindObjectsByType<TrackedPoseDriver>(FindObjectsSortMode.None)) driver.enabled = false;
            var workcell = GameObject.Find("SeatedWorkcell"); if (workcell != null) workcell.SetActive(false);
            var camera = Camera.main;
            camera.clearFlags = CameraClearFlags.SolidColor; camera.backgroundColor = new Color(.32f, .34f, .37f);
            camera.transform.position = robot.transform.position + new Vector3(0, .65f, -2.2f);
            camera.transform.LookAt(robot.transform.position + new Vector3(0, .15f, 0));
        }
        void LateUpdate()
        {
            if (string.IsNullOrEmpty(output)) return;
            if (!captured && applied >= 10)
            {
                var camera = Camera.main; var old = camera.targetTexture; var active = RenderTexture.active;
                var render = new RenderTexture(1280, 960, 24); var pixels = new Texture2D(1280, 960, TextureFormat.RGB24, false);
                try
                {
                    camera.targetTexture = render; camera.Render(); RenderTexture.active = render;
                    pixels.ReadPixels(new Rect(0, 0, 1280, 960), 0, 0); pixels.Apply();
                    DurableWrite(Path.Combine(output, "bridge-diagnostic.png"), pixels.EncodeToPNG()); captured = true;
                }
                finally { camera.targetTexture = old; RenderTexture.active = active; Destroy(render); Destroy(pixels); }
            }
            if (benchmark.IsFinished || Time.realtimeSinceStartupAsDouble > deadline)
            {
                DurableWrite(Path.Combine(output, "capture-summary.json"), Encoding.UTF8.GetBytes("{\"diagnostic\":true,\"applied_frames\":" + applied + ",\"captured\":" + (captured ? "true" : "false") + ",\"device_acceptance\":false}\n"));
                int code = benchmark.IsFinished && applied > 0 && captured ? 0 : 1;
#if UNITY_EDITOR
                UnityEditor.EditorApplication.Exit(code);
#else
                Application.Quit(code);
#endif
            }
        }
    }
}
