using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using Unity.Collections;
using UnityEngine;
using UnityEngine.XR;
using UnityEngine.XR.OpenXR.Features.Meta;

namespace AcousticVocab.Spikes.OpenXR
{
    // Engineering telemetry only. These timestamps are host monotonic clock values,
    // never simulation time and never a claim of actual photon presentation time.
    public sealed class FrameIntervalLogger : MonoBehaviour
    {
        [Serializable] public sealed class Configuration
        {
            public float requestedRefreshHz;
            public float durationSeconds = 900;
        }

        static long launchTicks;
        static readonly CultureInfo Invariant = CultureInfo.InvariantCulture;
        readonly List<XRDisplaySubsystem> displays = new();
        readonly FrameTiming[] timing = new FrameTiming[1];
        StreamWriter frames;
        StreamWriter events;
        Configuration configuration;
        long previousTicks;
        double readySeconds = -1;
        double nextFlush;
        double nextRefreshProbe;
        bool? priorRunning;
        bool? priorPresence;
        bool refreshRequested;
        bool closed;
        bool trackingOriginSet;
        float currentHz;

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.BeforeSplashScreen)]
        static void CaptureLaunch() => launchTicks = Stopwatch.GetTimestamp();

        public static double HostSeconds => (Stopwatch.GetTimestamp() - launchTicks) / (double)Stopwatch.Frequency;

        void Awake()
        {
            configuration = new Configuration();
            string configPath = Path.Combine(Application.persistentDataPath, "spike-config.local.json");
            if (File.Exists(configPath))
                configuration = JsonUtility.FromJson<Configuration>(File.ReadAllText(configPath));
            string[] arguments = Environment.GetCommandLineArgs();
            for (int i = 0; i < arguments.Length - 1; ++i)
                if (arguments[i] == "--refresh-hz")
                    configuration.requestedRefreshHz = float.Parse(arguments[i + 1], Invariant);
            if (configuration.durationSeconds <= 0) configuration.durationSeconds = 900;
            string variant = Application.platform == RuntimePlatform.Android ? "standalone" : "link";
            string folder = Path.Combine(Application.persistentDataPath, "openxr-spike", Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(folder);
            frames = new StreamWriter(Path.Combine(folder, "frame_timing.csv"));
            events = new StreamWriter(Path.Combine(folder, "events.csv"));
            frames.WriteLine("host_ticks,host_seconds,unity_realtime_seconds,frame,interval_ms,refresh_hz,over_budget,app_gap_gt_250ms,frame_timing_available,cpu_frame_ms,gpu_frame_ms,xr_running,measurement_active");
            events.WriteLine("host_ticks,host_seconds,event,value");
            Event("variant", variant);
            Event("unity_version", Application.unityVersion);
            Event("stopwatch_frequency", Stopwatch.Frequency.ToString(Invariant));
            Event("duration_seconds", configuration.durationSeconds.ToString(Invariant));
            Event("requested_refresh_hz", configuration.requestedRefreshHz.ToString(Invariant));
            Event("start_anchor", "BeforeSplashScreen_callback_not_process_launch");
            InputDevices.deviceConnected += DeviceConnected;
            InputDevices.deviceDisconnected += DeviceDisconnected;
            SessionStateProbe.StateChanged += SessionChanged;
        }

        void Update()
        {
            if (closed) return;
            long ticks = Stopwatch.GetTimestamp();
            double now = (ticks - launchTicks) / (double)Stopwatch.Frequency;
            SubsystemManager.GetSubsystems(displays);
            XRDisplaySubsystem display = displays.Find(candidate => candidate.running);
            bool running = display != null;
            if (priorRunning != running) { Event("xr_display_running", running.ToString()); priorRunning = running; }
            var head = InputDevices.GetDeviceAtXRNode(XRNode.Head);
            if (head.TryGetFeatureValue(CommonUsages.userPresence, out bool presence) && priorPresence != presence)
            { Event("user_presence", presence.ToString()); priorPresence = presence; }

            if (!trackingOriginSet && running)
            {
                var inputs = new List<XRInputSubsystem>();
                SubsystemManager.GetSubsystems(inputs);
                foreach (var input in inputs)
                    if (input.running)
                    {
                        trackingOriginSet = input.TrySetTrackingOriginMode(TrackingOriginModeFlags.Device);
                        Event("device_tracking_origin_requested", trackingOriginSet.ToString());
                    }
            }
            if (running && now >= nextRefreshProbe)
            {
                if (display.TryGetDisplayRefreshRate(out float hz)) currentHz = hz;
                Event("current_refresh_hz", currentHz > 0 ? currentHz.ToString(Invariant) : "unavailable");
                if (display.TryGetSupportedDisplayRefreshRates(Allocator.Temp, out var rates))
                {
                    using (rates)
                    {
                        var values = new string[rates.Length];
                        for (int i = 0; i < rates.Length; ++i) values[i] = rates[i].ToString(Invariant);
                        Event("offered_refresh_hz", string.Join(";", values));
                        if (!refreshRequested && configuration.requestedRefreshHz > 0)
                        {
                            bool accepted = display.TryRequestDisplayRefreshRate(configuration.requestedRefreshHz);
                            Event("refresh_request_accepted", accepted.ToString());
                            refreshRequested = true;
                        }
                    }
                }
                else Event("offered_refresh_hz", "unavailable_runtime_extension");
                nextRefreshProbe = now + 10;
            }
            if (readySeconds < 0 && running && head.TryGetFeatureValue(CommonUsages.isTracked, out bool tracked) && tracked)
            { readySeconds = now; Event("ready", now.ToString("F6", Invariant)); }
            uint count = FrameTimingManager.GetLatestTimings(1, timing);
            FrameTimingManager.CaptureFrameTimings();
            if (previousTicks != 0)
            {
                double interval = (ticks - previousTicks) * 1000.0 / Stopwatch.Frequency;
                bool active = readySeconds >= 0 && now >= readySeconds + 10;
                frames.WriteLine(string.Join(",", ticks, now.ToString("F9", Invariant),
                    Time.realtimeSinceStartupAsDouble.ToString("F9", Invariant), Time.frameCount,
                    interval.ToString("F6", Invariant), currentHz.ToString("F3", Invariant),
                    currentHz > 0 ? (interval > 1000.0 / currentHz ? "1" : "0") : "",
                    interval > 250 ? "1" : "0", count > 0 ? "1" : "0",
                    count > 0 ? timing[0].cpuFrameTime.ToString("F6", Invariant) : "",
                    count > 0 ? timing[0].gpuFrameTime.ToString("F6", Invariant) : "",
                    running ? "1" : "0", active ? "1" : "0"));
            }
            previousTicks = ticks;
            if (now >= nextFlush) { frames.Flush(); events.Flush(); nextFlush = now + 5; }
            if (readySeconds >= 0 && now >= readySeconds + 10 + configuration.durationSeconds)
            { Event("measurement_complete", "true"); Close(); }
        }

        public void Event(string kind, string value)
        {
            if (events == null || closed) return;
            events.WriteLine($"{Stopwatch.GetTimestamp()},{HostSeconds.ToString("F9", Invariant)},{Csv(kind)},{Csv(value)}");
        }
        static string Csv(string value) => "\"" + value.Replace("\"", "\"\"") + "\"";
        void DeviceConnected(InputDevice device) => Event("input_device_connected", device.characteristics.ToString());
        void DeviceDisconnected(InputDevice device) => Event("input_device_disconnected", device.characteristics.ToString());
        void SessionChanged(int oldState, int newState) => Event("openxr_session_state", $"{oldState}->{newState}");
        void OnApplicationFocus(bool value) => Event("application_focus", value.ToString());
        void OnApplicationPause(bool value)
        {
            if (closed) return;
            Event("application_pause", value.ToString()); frames?.Flush(); events?.Flush();
        }
        void OnApplicationQuit() { Event("application_quit", "true"); Close(); }
        void OnDestroy() => Close();
        void Close()
        {
            if (closed) return;
            closed = true;
            InputDevices.deviceConnected -= DeviceConnected;
            InputDevices.deviceDisconnected -= DeviceDisconnected;
            SessionStateProbe.StateChanged -= SessionChanged;
            frames?.Dispose(); events?.Dispose();
        }
    }
}
