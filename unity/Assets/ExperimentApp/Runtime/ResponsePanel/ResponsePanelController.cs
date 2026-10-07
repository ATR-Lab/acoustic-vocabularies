using System;
using System.Collections.Generic;
using System.IO;
using AcousticVocab.Foundation;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.XR;
using UnityEngine.XR.Hands;

namespace AcousticVocab.ResponsePanel
{
    [DisallowMultipleComponent]
    public sealed class ResponsePanelController : MonoBehaviour
    {
        public FoundationBootstrap foundation;
        public Transform trackingSpace;
        public Shader panelShader;
        public Font panelFont;
        public ResponseState State { get; private set; }
        public bool InputAvailable { get; private set; }
        public string LoadedConfigurationSha256{get;private set;}
        public bool InputConfigured => settings!=null;
        public bool UsesLeftHand => settings!=null&&settings.LeftHand;
        public bool FaultLatched { get; private set; }
        public bool? ConfiguredLeftHand => settings?.LeftHand;
        public bool ReadyForTrial => isActiveAndEnabled && focused && !paused && State != null && foundation.Ready && InputAvailable && !FaultLatched;
        public event Action<PanelResponse> Responded;
        // Synchronous durable subscriber boundary; failures propagate into ResponseState's abort latch.
        public event Action<PanelProcessEvent> ProcessRecorded;
        public event Action<string> Faulted;
        sealed class Key { public string Kind, Value; public Transform Root; public BoxCollider Collider; public MeshRenderer Surface; public TextMesh Text; }
        readonly List<Key> keys = new List<Key>();
        readonly List<XRHandSubsystem> handSubsystems = new List<XRHandSubsystem>();
        readonly List<Material> materials = new List<Material>();
        PanelSettings settings;
        PanelJournal journal;
        Transform panel;
        TextMesh roleLabel;
        GameObject roleBacking;
        LineRenderer ray;
        GameObject tip;
        bool triggerDown, triggerArmed, pokeArmed, haveTip, engineeringStarted, paused, focused = true;
        Vector3 previousTip;
        string lastFault;

        void PersistProcess(PanelProcessEvent value)
        {
            journal.Process(value);
            ProcessRecorded?.Invoke(value);
        }
        void Start()
        {
            try
            {
                if (foundation == null || trackingSpace == null || foundation.Configuration == null) throw new ConfigurationFault("panel_foundation_unavailable");
                var config = foundation.Configuration;
                var schema = Resources.Load<TextAsset>("ResponsePanelSchema");
                if (schema == null) throw new ConfigurationFault("panel_schema_missing");
                byte[] settingsBytes=File.ReadAllBytes(Path.Combine(Application.persistentDataPath,"response-panel.local.json"));
                using(var sha=System.Security.Cryptography.SHA256.Create())LoadedConfigurationSha256=BitConverter.ToString(sha.ComputeHash(settingsBytes)).Replace("-","").ToLowerInvariant();
                settings = PanelSettings.Parse(new System.Text.UTF8Encoding(false,true).GetString(settingsBytes), schema.text, (string)config["protocol_version"]);
                settings.VerifyStationInput((string)config["input_method"]);
                var identity = StationConfig.ParseStrict(Resources.Load<TextAsset>("BuildIdentity").text);
                journal = new PanelJournal(Path.Combine(Application.persistentDataPath, "operator-logs"), identity, (string)config["station_id"], settings.RecordedConfiguration);
                State = new ResponseState(() => PanelJournal.NowMs, PersistProcess);
                State.Responded += value => { journal.Response(value); Responded?.Invoke(value); };
                CreatePresentation(StationConfig.ReferencePose(config));
                foundation.Faulted += FoundationFault;
            }
            catch (Exception)
            {
                if (journal == null)
                    try { journal = new PanelJournal(Path.Combine(Application.persistentDataPath, "operator-logs"),
                        StationConfig.ParseStrict(Resources.Load<TextAsset>("BuildIdentity").text),
                        (string)foundation.Configuration["station_id"], new JObject { ["configuration_status"] = "rejected" }); } catch { }
                Fail("panel_configuration_or_log_unavailable");
            }
        }

        public void Open(PanelRequest request)
        {
            if (!ReadyForTrial) throw new InvalidOperationException("Panel is not ready at a safe boundary");
            State.Open(request); panel.gameObject.SetActive(true); Refresh();
        }
        public bool ConfirmInputRecoveryAtSafeBoundary()
        {
            if (!isActiveAndEnabled || !focused || paused || State == null || !foundation.Ready || !InputAvailable) return false;
            FaultLatched = false; lastFault = null; return true;
        }
        public void CloseAtBoundary() { State?.Abort(); if (panel != null) panel.gameObject.SetActive(false); }
        public bool SimulationResponse(SimulationTestAuthority authority,string response,string target,string action)
        {
            if(authority==null||!SimulationTestAuthority.CompiledCapability)throw new InvalidOperationException("SIMULATION_INPUT_AUTHORITY");
            if(!ReadyForTrial||State?.Request==null||!State.WindowOpen)return false;
            if(response=="dont_know"){Press(keys.Find(x=>x.Value=="dont_know"));return State.Locked;}
            if(response!="commit")throw new InvalidOperationException("SIMULATION_INPUT_VALUE");
            if(State.Request.Role!=PanelRole.Action)Press(keys.Find(x=>x.Kind=="target"&&x.Value==target));
            if(State.Request.Role!=PanelRole.Target)Press(keys.Find(x=>x.Kind=="action"&&x.Value==action));
            Press(keys.Find(x=>x.Value=="commit"));return State.Locked;
        }
        double simulatedInputLossUntil = -1;
        // SIMULATION_TEST fault injection (#81): the configured controller/hand
        // is treated as untracked for a bounded interval. The ordinary Update
        // availability edge, panel fault latch and frame interface check respond.
        public void SimulationSuppressInput(SimulationTestAuthority authority,int milliseconds)
        {
            if(authority==null||!SimulationTestAuthority.CompiledCapability)throw new InvalidOperationException("SIMULATION_INPUT_AUTHORITY");
            if(milliseconds<100||milliseconds>10000)throw new InvalidOperationException("SIMULATION_INPUT_VALUE");
            simulatedInputLossUntil = Time.realtimeSinceStartupAsDouble + milliseconds/1000d;
        }
        bool SimulatedInputLost => simulatedInputLossUntil>=0 && Time.realtimeSinceStartupAsDouble<simulatedInputLossUntil;
        void FoundationFault(string _) => Fail("foundation_fault");
        void Fail(string reason)
        {
            FaultLatched = true;
            if (panel != null) panel.gameObject.SetActive(false);
            if (ray != null) ray.enabled = false; if (tip != null) tip.SetActive(false);
            if (reason == lastFault) return; lastFault = reason;
            try { State?.Abort(); journal?.Fault(reason); } catch { reason = "panel_log_unavailable"; }
            Faulted?.Invoke(reason);
        }
        void Update()
        {
            if (State == null || settings == null || panel == null) return;
            try
            {
                bool available = focused && !paused && foundation.Ready && !SimulatedInputLost && (settings.InputMethod == "controller_ray" ? PollController() : PollHand());
                if (!available)
                {
                    if (InputAvailable && State.Request != null && !State.Locked) Fail("input_lost");
                    triggerArmed = false; triggerDown = false; pokeArmed = false; haveTip = false;
                    ray.enabled = false; tip.SetActive(false);
                }
                InputAvailable = available;
                if (!engineeringStarted && settings.EngineeringMode != "disabled" && ReadyForTrial)
                {
                    // Explicit private engineering mode only. No audio onset or study trial is fabricated.
                    engineeringStarted = true; Open(settings.EngineeringRequest(PanelJournal.NowMs));
                }
                if (State.Request != null) { State.Tick(); Refresh(); }
            }
            catch (Exception) { Fail("panel_runtime_or_log_fault"); }
        }
        void Press(Key key)
        {
            if (key==null || !isActiveAndEnabled || !focused || paused || !foundation.Ready || FaultLatched || State?.Request == null) return;
            if (key.Kind == "target") State.SelectTarget(key.Value);
            else if (key.Kind == "action") State.SelectAction(key.Value);
            else if (key.Value == "commit") State.Commit();
            else State.DontKnow();
            Refresh();
        }
        void Hit(Ray pointer, float distance)
        {
            Key closest = null; float nearest = distance;
            foreach (var key in keys)
                if (key.Root.gameObject.activeInHierarchy && key.Collider.Raycast(pointer, out RaycastHit hit, distance) && hit.distance < nearest) { nearest = hit.distance; closest = key; }
            if (closest != null) Press(closest);
        }
        static bool Finite(Vector3 p) => float.IsFinite(p.x) && float.IsFinite(p.y) && float.IsFinite(p.z);
        bool PollController()
        {
            tip.SetActive(false);
            var device = InputDevices.GetDeviceAtXRNode(settings.LeftHand ? XRNode.LeftHand : XRNode.RightHand);
            if (!device.isValid || !device.TryGetFeatureValue(CommonUsages.isTracked, out bool tracked) || !tracked ||
                !device.TryGetFeatureValue(CommonUsages.trackingState, out InputTrackingState tracking) ||
                (tracking & (InputTrackingState.Position | InputTrackingState.Rotation)) != (InputTrackingState.Position | InputTrackingState.Rotation) ||
                !device.TryGetFeatureValue(CommonUsages.devicePosition, out Vector3 position) || !Finite(position) ||
                !device.TryGetFeatureValue(CommonUsages.deviceRotation, out Quaternion rotation) ||
                !device.TryGetFeatureValue(CommonUsages.triggerButton, out bool down)) return false;
            Vector3 start = trackingSpace.TransformPoint(position), direction = trackingSpace.TransformDirection(rotation * Vector3.forward);
            float rotationNorm = rotation.x * rotation.x + rotation.y * rotation.y + rotation.z * rotation.z + rotation.w * rotation.w;
            if (!float.IsFinite(rotationNorm) || Mathf.Abs(rotationNorm - 1) > .001f || !Finite(direction) || direction.sqrMagnitude < .9f || direction.sqrMagnitude > 1.1f) return false;
            ray.enabled = !FaultLatched && panel.gameObject.activeInHierarchy;
            ray.SetPosition(0, start); ray.SetPosition(1, start + direction * 2);
            if (!down) triggerArmed = true;
            if (down && !triggerDown && triggerArmed) { Hit(new Ray(start, direction), 2); triggerArmed = false; }
            triggerDown = down; return true;
        }
        bool PollHand()
        {
            ray.enabled = false; handSubsystems.Clear(); SubsystemManager.GetSubsystems(handSubsystems);
            var subsystem = handSubsystems.Find(x => x.running); if (subsystem == null) return false;
            var hand = settings.LeftHand ? subsystem.leftHand : subsystem.rightHand;
            if (!hand.isTracked || !hand.GetJoint(XRHandJointID.IndexTip).TryGetPose(out Pose pose) || !Finite(pose.position)) return false;
            Vector3 point = trackingSpace.TransformPoint(pose.position);
            tip.SetActive(!FaultLatched && panel.gameObject.activeInHierarchy); tip.transform.position = point;
            Vector3 local = panel.InverseTransformPoint(point);
            if (local.z < -.04f) pokeArmed = true;
            // Front face of the .015 m button box. Do not consume the poke before contact.
            if (haveTip && pokeArmed && local.z >= -.0075f)
            {
                Vector3 delta = point - previousTip;
                if (delta.sqrMagnitude > 0) Hit(new Ray(previousTip, delta.normalized), delta.magnitude);
                pokeArmed = false;
            }
            previousTip = point; haveTip = true; return true;
        }
        void CreatePresentation(Pose reference)
        {
            panel = new GameObject("Response panel").transform; panel.SetParent(foundation.presentationRoot.transform, false);
            panel.SetPositionAndRotation(reference.position + reference.rotation * new Vector3(0, settings.VerticalOffset, settings.Distance), reference.rotation);
            for (int i = 0; i < 8; i++) AddKey("target", PublicCommands.Targets[i], Grid(i, 0));
            for (int i = 0; i < 4; i++) AddKey("action", PublicCommands.Actions[i], Grid(i, -2 * (settings.ButtonHeight + settings.Gap)));
            AddKey("control", "commit", new Vector3(-settings.ButtonWidth - settings.Gap, -3 * (settings.ButtonHeight + settings.Gap), 0), 1.5f);
            AddKey("control", "dont_know", new Vector3(settings.ButtonWidth + settings.Gap, -3 * (settings.ButtonHeight + settings.Gap), 0), 1.5f);
            roleLabel = Text(panel, "", new Vector3(0, settings.ButtonHeight + settings.Gap, -.015f));
            roleBacking = GameObject.CreatePrimitive(PrimitiveType.Cube); roleBacking.name = "Role label backing";
            roleBacking.transform.SetParent(panel, false); roleBacking.transform.localPosition = new Vector3(0, settings.ButtonHeight + settings.Gap, 0);
            roleBacking.transform.localScale = new Vector3(settings.ButtonWidth * 1.5f, settings.ButtonHeight * .75f, .015f);
            DisposeObject(roleBacking.GetComponent<Collider>()); roleBacking.GetComponent<Renderer>().sharedMaterial = Material("Unlit/Color", new Color(.075f, .085f, .095f));
            ray = new GameObject("Input ray").AddComponent<LineRenderer>(); ray.transform.SetParent(panel, false); ray.positionCount = 2;
            ray.startWidth = ray.endWidth = .0015f; ray.sharedMaterial = Material("Unlit/Color", Color.cyan); ray.enabled = false;
            tip = GameObject.CreatePrimitive(PrimitiveType.Sphere); tip.name = "Index tip"; tip.transform.SetParent(panel, false); tip.transform.localScale = Vector3.one * .008f;
            DisposeObject(tip.GetComponent<Collider>()); tip.GetComponent<Renderer>().sharedMaterial = Material("Unlit/Color", Color.cyan); tip.SetActive(false);
            panel.gameObject.SetActive(false);
        }
        Vector3 Grid(int index, float shift) => new Vector3((index % 4 - 1.5f) * (settings.ButtonWidth + settings.Gap), shift - index / 4 * (settings.ButtonHeight + settings.Gap), 0);
        Material Material(string shader, Color color) { if (panelShader == null) throw new ConfigurationFault("panel_shader_missing"); var result = new Material(panelShader) { color = color }; materials.Add(result); return result; }
        TextMesh Text(Transform parent, string value, Vector3 position)
        {
            var label = new GameObject("Label").AddComponent<TextMesh>(); label.transform.SetParent(parent, false); label.transform.localPosition = position;
            label.font = panelFont != null ? panelFont : throw new ConfigurationFault("panel_font_missing"); label.GetComponent<MeshRenderer>().sharedMaterial = label.font.material;
            label.anchor = TextAnchor.MiddleCenter; label.alignment = TextAlignment.Center; label.fontSize = 100; label.characterSize = .01f; label.color = Color.white; label.text = value; return label;
        }
        void AddKey(string kind, string value, Vector3 position, float widthScale = 1)
        {
            var root = new GameObject("Response key").transform; root.SetParent(panel, false); root.localPosition = position;
            var box = GameObject.CreatePrimitive(PrimitiveType.Cube); box.transform.SetParent(root, false); box.transform.localScale = new Vector3(settings.ButtonWidth * widthScale, settings.ButtonHeight, .015f);
            var surface = box.GetComponent<MeshRenderer>(); surface.sharedMaterial = Material("Unlit/Color", new Color(.12f, .16f, .2f));
            keys.Add(new Key { Kind = kind, Value = value, Root = root, Collider = box.GetComponent<BoxCollider>(), Surface = surface, Text = Text(root, "", new Vector3(0, 0, -.012f)) });
        }
        void Label(TextMesh label, string value, float width)
        {
            if (label.text == value) return;
            label.text = value; label.transform.localScale = Vector3.one;
            if (value.Length == 0) return;
            Vector3 bounds = label.GetComponent<MeshRenderer>().localBounds.size;
            if (bounds.y <= 0 || bounds.x <= 0) throw new InvalidOperationException("Glyph bounds unavailable");
            float distance = Vector3.Distance(StationConfig.ReferencePose(foundation.Configuration).position, label.transform.position);
            float inkHeight = PanelTypography.LocalInkHeight(label);
            float scale = 2 * distance * Mathf.Tan(settings.TextAngleDegrees * Mathf.Deg2Rad / 2) / inkHeight;
            if (bounds.x * scale > width * .9f || inkHeight * scale > settings.ButtonHeight * .8f) throw new InvalidOperationException("Configured glyph size does not fit button");
            label.transform.localScale = Vector3.one * scale;
        }
        void Refresh()
        {
            if (State?.Request == null) return;
            var role = State.Request.Role; int family = PublicCommands.Family(State.SelectedTarget);
            for (int i = 0; i < keys.Count; i++)
            {
                var key = keys[i]; bool show = true, selected = false, enabled = State.WindowOpen;
                string text;
                if (i < 8)
                {
                    key.Kind = role == PanelRole.Action ? "action" : "target"; key.Value = role == PanelRole.Action ? PublicCommands.Actions[i] : PublicCommands.Targets[i];
                    text = key.Value.Replace('_', ' '); selected = key.Value == State.SelectedTarget || key.Value == State.SelectedAction;
                }
                else if (i < 12)
                {
                    show = role == PanelRole.Command && family >= 0; key.Value = PublicCommands.Actions[Math.Max(0, family) * 4 + i - 8];
                    text = key.Value.Replace('_', ' '); selected = key.Value == State.SelectedAction;
                }
                else { text = key.Value == "commit" ? "Commit" : "Don't know"; if (key.Value == "commit") enabled = State.CanCommit; }
                key.Root.gameObject.SetActive(show); if (!show) continue;
                Label(key.Text, text, settings.ButtonWidth * (i >= 12 ? 1.5f : 1));
                key.Surface.sharedMaterial.color = selected ? new Color(.04f, .36f, .5f) : enabled ? new Color(.15f, .2f, .25f) : new Color(.075f, .085f, .095f);
            }
            Label(roleLabel, role == PanelRole.Command ? "" : role == PanelRole.Action ? "Action" : "Target", settings.ButtonWidth * 4);
            roleBacking.SetActive(role != PanelRole.Command);
        }
        void OnApplicationFocus(bool value) { focused = value; if (!value && State?.Request != null && !State.Locked) Fail("application_focus_lost"); }
        void OnApplicationPause(bool value) { paused = value; if (value && State?.Request != null && !State.Locked) Fail("application_paused"); }
        void OnDisable() { InputAvailable = false; if (State != null) Fail("panel_component_disabled"); }
        void OnDestroy()
        {
            if (foundation != null) foundation.Faulted -= FoundationFault;
            journal?.Dispose(); foreach (var material in materials) if (material != null) DisposeObject(material);
        }
        static void DisposeObject(UnityEngine.Object value) { if (Application.isPlaying) Destroy(value); else DestroyImmediate(value); }
    }
}
