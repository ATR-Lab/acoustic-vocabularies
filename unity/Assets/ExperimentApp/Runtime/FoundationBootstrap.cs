using System;
using System.Collections.Generic;
using System.IO;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.XR;

namespace AcousticVocab.Foundation
{
    [DisallowMultipleComponent]
    public sealed class FoundationBootstrap : MonoBehaviour
    {
        public Transform seatedOrigin;
        public Camera observerCamera;
        public GameObject presentationRoot;
        public bool Ready { get; private set; }
        JObject configuration;
        public JObject Configuration => configuration == null ? null : (JObject)configuration.DeepClone();
        public event Action<string> Faulted;
        readonly ObserverReference reference = new ObserverReference();
        readonly List<XRInputSubsystem> subscribed = new List<XRInputSubsystem>();
        FoundationLog log;
        bool initialRestore = true;
        bool trackingWasValid;
        string lastFault;

        void Awake()
        {
            Neutral();
            JObject identity = new JObject { ["build_id"] = "invalid", ["commit_sha"] = "invalid", ["protocol_version"] = "invalid" };
            string reason = null;
            try
            {
                var metadata = Resources.Load<TextAsset>("BuildIdentity");
                if (metadata == null) throw new ConfigurationFault("build_identity_missing");
                identity = StationConfig.ParseStrict(metadata.text);
                var schema = Resources.Load<TextAsset>("StationConfigSchema");
                if (schema == null) throw new ConfigurationFault("station_schema_missing");
                string path = Path.Combine(Application.persistentDataPath, "station.local.json");
                configuration = StationConfig.Validate(File.ReadAllText(path), schema.text, (string)identity["protocol_version"]);
                string expectedTopology = Application.platform == RuntimePlatform.Android ? "standalone_quest" : "pcvr_link";
                if ((string)configuration["topology"] != expectedTopology) throw new ConfigurationFault("build_topology_mismatch");
            }
            catch (ConfigurationFault ex) { reason = ex.Message; }
            catch (Exception) { reason = "station_config_unreadable"; }
            try { log = new FoundationLog(Path.Combine(Application.persistentDataPath, "operator-logs"), identity,
                reason == null ? (string)configuration["station_id"] : "unprovisioned",
                reason == null ? (string)configuration["robot_state_source"] : "unresolved"); }
            catch (Exception) { configuration = null; Faulted?.Invoke("operator_log_unavailable"); return; }
            if (reason != null) { configuration = null; Fault(reason); }
            else Record("configuration_validated", new JObject { ["qualification"] = "development_only", ["topology"] = configuration["topology"], ["reference_calibration_id"] = configuration["observer_reference"]["calibration_id"] });
        }

        void Update()
        {
            var systems = new List<XRInputSubsystem>();
            SubsystemManager.GetSubsystems(systems);
            foreach (var system in systems)
                if (!subscribed.Contains(system)) { subscribed.Add(system); system.trackingOriginUpdated += OnTrackingOriginUpdated; }
            bool tracked = IsTracked();
            if (trackingWasValid && !tracked) { reference.MarkRecenter(); Fault("tracking_lost"); }
            trackingWasValid = tracked;
            if (initialRestore && configuration != null && tracked && subscribed.Exists(x => x.running))
            {
                // Device mode is explicit, and only accepted after the runtime reports it.
                foreach (var system in subscribed)
                    if (system.running && system.GetTrackingOriginMode() != TrackingOriginModeFlags.Device) system.TrySetTrackingOriginMode(TrackingOriginModeFlags.Device);
                if (subscribed.Exists(x => x.running && x.GetTrackingOriginMode() == TrackingOriginModeFlags.Device))
                    initialRestore = !RestoreAtSafeBoundary("startup");
            }
        }

        static bool IsTracked()
        {
            var device = InputDevices.GetDeviceAtXRNode(XRNode.Head);
            return device.TryGetFeatureValue(CommonUsages.isTracked, out bool tracked) && tracked &&
                device.TryGetFeatureValue(CommonUsages.trackingState, out InputTrackingState state) &&
                (state & (InputTrackingState.Position | InputTrackingState.Rotation)) == (InputTrackingState.Position | InputTrackingState.Rotation);
        }
        void OnTrackingOriginUpdated(XRInputSubsystem _) { if (reference.MarkRecenter()) Fault("tracking_origin_changed"); }
        void Neutral() { Ready = false; if (presentationRoot != null) presentationRoot.SetActive(false); if (observerCamera != null) { observerCamera.clearFlags = CameraClearFlags.SolidColor; observerCamera.backgroundColor = Color.black; } }
        bool Record(string kind, JObject fields)
        {
            try { if (log == null) return false; log.Write(kind, fields); return true; }
            catch (Exception) { Neutral(); configuration = null; Faulted?.Invoke("operator_log_unavailable"); return false; }
        }
        void Fault(string reason)
        {
            Neutral();
            if (lastFault == reason) return;
            lastFault = reason;
            Record("fault", new JObject { ["reason"] = reason, ["restore_pending"] = reference.RestorePending });
            Faulted?.Invoke(reason);
        }

        // Future trial controller calls only at a safe boundary with the observer in the calibration pose.
        // The argument is a bounded engineering reason, never trial content or participant information.
        public bool RestoreAtSafeBoundary(string boundary)
        {
            if (configuration == null || log == null || !IsTracked() || !reference.RestorePending) return false;
            if (boundary != "startup" && boundary != "between_trials" && boundary != "operator_recovery") throw new ArgumentException("Unknown safe boundary");
            var headInOrigin = new Pose(seatedOrigin.InverseTransformPoint(observerCamera.transform.position), Quaternion.Inverse(seatedOrigin.rotation) * observerCamera.transform.rotation);
            Pose pose;
            try { pose = ObserverReference.ResolveOrigin(StationConfig.ReferencePose(configuration), headInOrigin); }
            catch (ConfigurationFault ex) { Fault(ex.Message); return false; }
            seatedOrigin.SetPositionAndRotation(pose.position, pose.rotation);
            if (!Record("observer_reference_restored", new JObject { ["boundary"] = boundary, ["calibration_id"] = configuration["observer_reference"]["calibration_id"] })) return false;
            reference.Restored(); lastFault = null; Ready = true; presentationRoot.SetActive(true);
            return true;
        }
        void OnApplicationPause(bool paused) { if (paused && !initialRestore) { reference.MarkRecenter(); Fault("application_paused"); } }
        void OnDestroy() { foreach (var system in subscribed) system.trackingOriginUpdated -= OnTrackingOriginUpdated; log?.Dispose(); }
    }
}
