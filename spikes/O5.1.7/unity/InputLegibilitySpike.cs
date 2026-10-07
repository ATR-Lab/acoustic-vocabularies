using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using UnityEngine;
using UnityEngine.XR;
using UnityEngine.XR.Hands;

namespace AcousticVocab.Spikes.Input
{
    public sealed class InputLegibilitySpike : MonoBehaviour
    {
        [Serializable] public sealed class Settings
        {
            public string tester_code = "T01";
            public bool use_left_hand;
            public float text_angle_deg = .6f;
            public float panel_distance_m = .55f;
            public float panel_height_m = 1.1f;
            public float observer_height_m = 1.2f;
        }
        [Serializable] public sealed class Trial
        { public string trial_id, tester_code, method, target, action; }
        [Serializable] public sealed class Schedule { public Trial[] trials; }
        sealed class Key
        { public string id; public Transform root; public BoxCollider box; public Renderer renderer; public TextMesh label; }

        public Settings settings = new Settings();
        public Transform trackingSpace;
        readonly PanelState state = new PanelState();
        readonly List<Key> keys = new List<Key>();
        readonly List<Trial> trials = new List<Trial>();
        readonly List<XRHandSubsystem> handSubsystems = new List<XRHandSubsystem>();
        Transform panel;
        TextMesh prompt;
        LineRenderer ray;
        GameObject tipMarker;
        StreamWriter log;
        int cursor, attempt;
        bool active, triggerWasDown, pokeArmed, havePreviousTip, focused = true;
        bool? wasAvailable;
        Vector3 previousTip;
        string method;
        string lossReason = "unavailable";
        static double Now => (double)Stopwatch.GetTimestamp() / Stopwatch.Frequency;
        Trial Current => cursor < trials.Count ? trials[cursor] : null;
        string Family => PanelState.Family(state.Target);

        void Start()
        {
            PanelState.VerifyLegality();
            string root = Application.persistentDataPath;
            string configuration = Path.Combine(root, "input.local.json");
            if (File.Exists(configuration)) settings = JsonUtility.FromJson<Settings>(File.ReadAllText(configuration));
            if (!System.Text.RegularExpressions.Regex.IsMatch(settings.tester_code, "^T[0-9]{2,3}$") ||
                settings.text_angle_deg <= 0 || settings.text_angle_deg > 3 || settings.panel_distance_m <= .2f || trackingSpace == null)
                throw new InvalidOperationException("Complete input local settings and assign CameraOffset tracking space");
            string schedulePath = Path.Combine(root, "commands.local.json");
            if (!File.Exists(schedulePath)) throw new InvalidOperationException("Generate and copy commands.local.json before running");
            var schedule = JsonUtility.FromJson<Schedule>(File.ReadAllText(schedulePath));
            foreach (var trial in schedule.trials)
            {
                if (!PanelState.Legal(trial.target, trial.action) || (trial.method != "controller-ray" && trial.method != "hand-poke"))
                    throw new InvalidOperationException("Invalid command schedule");
                if (trial.tester_code == settings.tester_code) trials.Add(trial);
            }
            if (trials.Count != 64) throw new InvalidOperationException("Need 32 legal commands for each of two methods per tester");
            var unique = new HashSet<string>();
            foreach (var trial in trials)
                if (!unique.Add(trial.method + ":" + trial.target + ":" + trial.action)) throw new InvalidOperationException("Duplicate command");
            string directory = Path.Combine(root, "input-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            File.WriteAllText(Path.Combine(directory, "settings.json"), JsonUtility.ToJson(settings, true));
            log = new StreamWriter(Path.Combine(directory, "events.csv"));
            log.WriteLine("tester_code,method,trial_id,attempt,event,host_s,target,action,prompt_target,prompt_action,detail");
            method = Current.method;
            CreatePanel();
            prompt.text = "Ready: " + method + " — select Next";
            Refresh();
            UnityEngine.Debug.Log("Input spike ready. Output directory: " + directory);
        }

        void CreatePanel()
        {
            panel = new GameObject("Engineering response panel").transform;
            panel.SetParent(transform, false);
            panel.localPosition = new Vector3(0, settings.panel_height_m, settings.panel_distance_m);
            for (int i = 0; i < 8; i++) AddKey(((char)('A' + i)).ToString(), ((char)('A' + i)).ToString(), new Vector3((i - 3.5f) * .08f, 0, 0), new Vector2(.07f, .07f));
            for (int i = 1; i <= 4; i++) AddKey("action" + i, "Action " + i, new Vector3((i - 2.5f) * .16f, -.10f, 0), new Vector2(.145f, .07f));
            AddKey("commit", "Commit", new Vector3(-.10f, -.21f, 0), new Vector2(.17f, .07f));
            AddKey("next", "Next", new Vector3(.10f, -.21f, 0), new Vector2(.17f, .07f));
            prompt = MakeText(panel, "Ready", new Vector3(0, .11f, -.015f));
            prompt.characterSize = .002f; prompt.fontSize = 50;
            ray = new GameObject("Controller ray").AddComponent<LineRenderer>();
            ray.positionCount = 2; ray.startWidth = .0015f; ray.endWidth = .0015f;
            ray.material = new Material(Shader.Find("Sprites/Default")); ray.startColor = Color.cyan; ray.endColor = Color.cyan;
            tipMarker = GameObject.CreatePrimitive(PrimitiveType.Sphere);
            tipMarker.name = "Tracked index tip"; tipMarker.transform.localScale = Vector3.one * .009f;
            Destroy(tipMarker.GetComponent<Collider>());
        }

        TextMesh MakeText(Transform parent, string value, Vector3 position)
        {
            var text = new GameObject("Label").AddComponent<TextMesh>();
            text.transform.SetParent(parent, false); text.transform.localPosition = position;
            text.text = value; text.anchor = TextAnchor.MiddleCenter; text.alignment = TextAlignment.Center;
            text.font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            text.GetComponent<Renderer>().sharedMaterial = text.font.material;
            text.color = Color.white; text.fontSize = 100; text.characterSize = .01f;
            return text;
        }

        void AddKey(string id, string label, Vector3 position, Vector2 size)
        {
            var root = new GameObject(id).transform;
            root.SetParent(panel, false); root.localPosition = position;
            var surface = GameObject.CreatePrimitive(PrimitiveType.Cube);
            surface.transform.SetParent(root, false); surface.transform.localScale = new Vector3(size.x, size.y, .02f);
            var renderer = surface.GetComponent<Renderer>();
            renderer.material = new Material(Shader.Find("Unlit/Color"));
            var text = MakeText(root, label, new Vector3(0, 0, -.016f));
            keys.Add(new Key { id = id, root = root, box = surface.GetComponent<BoxCollider>(), renderer = renderer, label = text });
        }

        void SetLabel(Key key, string value)
        {
            key.label.text = value;
            key.label.transform.localScale = Vector3.one;
            // Measure the rendered glyph bounds rather than claiming an em-size equals visual angle.
            float measuredHeight = key.label.GetComponent<Renderer>().bounds.size.y;
            Vector3 observer = transform.TransformPoint(new Vector3(0, settings.observer_height_m, 0));
            float distance = Vector3.Distance(observer, key.label.transform.position);
            float desiredHeight = 2 * distance * Mathf.Tan(settings.text_angle_deg * Mathf.Deg2Rad / 2);
            if (measuredHeight > 0) key.label.transform.localScale = Vector3.one * desiredHeight / measuredHeight;
        }

        bool Enabled(Key key)
        {
            if (!focused || wasAvailable != true) return false;
            if (key.id == "next") return !active && Current != null;
            if (!active) return false;
            if (key.id == "commit") return state.CanCommit;
            return !key.id.StartsWith("action") || Family != "";
        }

        void Refresh()
        {
            foreach (var key in keys)
            {
                string text = key.id.StartsWith("action") ? (Family == "" ? "Action " : Family + " ") + key.id.Substring(6) : key.id == "commit" ? "Commit" : key.id == "next" ? "Next" : key.id;
                SetLabel(key, text);
                bool selected = key.id == state.Target || (key.id.StartsWith("action") && state.Action == Family + "-" + key.id.Substring(6));
                key.renderer.material.color = !Enabled(key) ? new Color(.12f, .12f, .12f) : selected ? new Color(.05f, .4f, .55f) : new Color(.2f, .25f, .3f);
            }
        }

        void Press(Key key)
        {
            if (!Enabled(key)) { Log("disabled_press", key.id); return; }
            if (key.id == "next")
            {
                state.Reset(); attempt++; active = true;
                prompt.text = Current.target + " then " + Current.action + " then Commit";
                Log("prompt", "text_prompt_command_time");
            }
            else if (key.id == "commit")
            {
                Log("commit", ""); active = false; cursor++; attempt = 0; state.Reset();
                if (Current != null)
                {
                    string nextMethod = Current.method;
                    if (method != nextMethod) { method = nextMethod; wasAvailable = null; triggerWasDown = false; havePreviousTip = false; }
                    prompt.text = "Ready: " + method + " — select Next";
                }
                else prompt.text = "Complete — operator review required";
            }
            else if (key.id.StartsWith("action"))
            { state.SelectAction(int.Parse(key.id.Substring(6))); Log("action", ""); }
            else
            {
                string oldAction = state.Action; state.SelectTarget(key.id); Log("target", "");
                if (oldAction != "" && state.Action == "") Log("action_cleared", "family_change");
            }
            Refresh();
        }

        void Update()
        {
            if (panel == null || method == null) return;
            bool available;
            if (method == "controller-ray") available = PollController();
            else available = PollHand();
            if (wasAvailable != available)
            {
                wasAvailable = available;
                Log(available ? "input_recovered" : "input_lost", available ? method : lossReason);
                if (!available) PauseAttempt("input unavailable");
                else if (!active) prompt.text = "Ready: " + method + " — select Next";
                Refresh();
            }
        }

        bool PollController()
        {
            tipMarker.SetActive(false);
            var device = InputDevices.GetDeviceAtXRNode(settings.use_left_hand ? XRNode.LeftHand : XRNode.RightHand);
            bool tracked = device.isValid && device.TryGetFeatureValue(CommonUsages.isTracked, out bool isTracked) && isTracked;
            bool valid = tracked && device.TryGetFeatureValue(CommonUsages.devicePosition, out _) && device.TryGetFeatureValue(CommonUsages.deviceRotation, out _);
            ray.enabled = valid;
            if (!valid) { lossReason = device.isValid ? "controller_pose_lost" : "controller_disconnected"; triggerWasDown = false; return false; }
            device.TryGetFeatureValue(CommonUsages.devicePosition, out Vector3 position);
            device.TryGetFeatureValue(CommonUsages.deviceRotation, out Quaternion rotation);
            Vector3 start = trackingSpace.TransformPoint(position);
            Vector3 direction = trackingSpace.TransformDirection(rotation * Vector3.forward);
            ray.SetPosition(0, start); ray.SetPosition(1, start + direction * 2);
            device.TryGetFeatureValue(CommonUsages.triggerButton, out bool down);
            if (down && !triggerWasDown && Physics.Raycast(start, direction, out RaycastHit hit, 2))
                foreach (var key in keys) if (hit.collider == key.box) { Press(key); break; }
            triggerWasDown = down;
            return true;
        }

        bool PollHand()
        {
            ray.enabled = false;
            SubsystemManager.GetSubsystems(handSubsystems);
            XRHandSubsystem subsystem = handSubsystems.Find(s => s.running);
            if (subsystem == null) { lossReason = "hand_subsystem_unavailable"; tipMarker.SetActive(false); havePreviousTip = false; return false; }
            XRHand hand = settings.use_left_hand ? subsystem.leftHand : subsystem.rightHand;
            if (!hand.isTracked || !hand.GetJoint(XRHandJointID.IndexTip).TryGetPose(out Pose pose))
            { lossReason = "hand_or_index_pose_lost"; tipMarker.SetActive(false); havePreviousTip = false; return false; }
            Vector3 point = trackingSpace.TransformPoint(pose.position);
            tipMarker.SetActive(true); tipMarker.transform.position = point;
            Vector3 local = panel.InverseTransformPoint(point);
            if (local.z < -.04f) pokeArmed = true;
            if (havePreviousTip && pokeArmed && local.z >= -.01f)
            {
                Vector3 delta = point - previousTip;
                foreach (var key in keys)
                {
                    if (delta.sqrMagnitude > 0 && key.box.Raycast(new Ray(previousTip, delta.normalized), out _, delta.magnitude))
                    { Press(key); pokeArmed = false; break; }
                }
            }
            previousTip = point; havePreviousTip = true;
            return true;
        }

        void PauseAttempt(string reason)
        {
            if (active) Log("paused", reason);
            active = false; state.Reset(); triggerWasDown = false; pokeArmed = false;
            prompt.text = "Paused — restore input, then Next to retry";
            Refresh();
        }
        void OnApplicationFocus(bool hasFocus)
        {
            focused = hasFocus;
            if (log == null) return;
            Log(hasFocus ? "focus_recovered" : "focus_lost", "");
            if (!hasFocus) PauseAttempt("app_focus");
            Refresh();
        }
        void Log(string eventName, string detail)
        {
            if (log == null) return;
            var trial = Current;
            log.WriteLine(string.Join(",", settings.tester_code, method, trial?.trial_id ?? "done", attempt.ToString(),
                eventName, Now.ToString("R", CultureInfo.InvariantCulture), state.Target, state.Action,
                trial?.target ?? "", trial?.action ?? "", detail.Replace(",", " ")));
            log.Flush();
        }
        void OnDisable() { log?.Dispose(); log = null; }
    }
}
