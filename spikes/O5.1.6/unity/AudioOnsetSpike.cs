using System;
using System.Collections;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using UnityEngine;

namespace AcousticVocab.Spikes.Audio
{
    // Engineering-only stimulus. Never load study audio into this scene.
    [RequireComponent(typeof(AudioSource))]
    public sealed class AudioOnsetSpike : MonoBehaviour
    {
        [Serializable] public sealed class Settings
        {
            public string route = "UNSET"; // headset-speakers, headset-wired, pc-headphones
            public string mode = "plain"; // plain or scheduled
            public int count = 200;
            public int volume_step = -1;
            public double scheduled_lead_s = .2;
            public double warmup_s = 10;
        }
        [Serializable] sealed class DeviceRecord
        {
            public string route, mode, unity_version, platform;
            public int count, volume_step, app_sample_rate_hz, buffer_frames, buffer_count;
            public double scheduled_lead_s;
            public string observed_underruns = "unknown; inspect platform capture";
        }

        public AudioClip click;
        public Camera observer;
        public Settings settings = new Settings();
        StreamWriter writer;
        AudioSource source;
        int previousMask;
        CameraClearFlags previousClear;
        Color previousColor;
        bool flashing;
        static double HostSeconds => (double)Stopwatch.GetTimestamp() / Stopwatch.Frequency;
        static string Number(double value) => value.ToString("R", CultureInfo.InvariantCulture);

        IEnumerator Start()
        {
            string configuration = Path.Combine(Application.persistentDataPath, "audio.local.json");
            if (File.Exists(configuration)) settings = JsonUtility.FromJson<Settings>(File.ReadAllText(configuration));
            if ((settings.route != "headset-speakers" && settings.route != "headset-wired" && settings.route != "pc-headphones") ||
                (settings.mode != "plain" && settings.mode != "scheduled") || settings.volume_step < 0 ||
                settings.count < 200 || settings.scheduled_lead_s < .1 || settings.scheduled_lead_s > .5 ||
                click == null || click.frequency != 48000 || click.channels != 1 || observer == null)
            {
                UnityEngine.Debug.LogError("Audio spike: complete local route/volume settings and assign a preloaded 48 kHz mono test click + observer.");
                yield break;
            }
            click.LoadAudioData();
            while (click.loadState == AudioDataLoadState.Loading) yield return null;
            if (click.loadState != AudioDataLoadState.Loaded) throw new InvalidOperationException("Test click failed to preload");
            source = GetComponent<AudioSource>();
            source.clip = click;
            source.playOnAwake = false;
            source.spatialBlend = 0;
            source.loop = false;
            source.volume = 1; // generated click has -18 dBFS peak; record hardware volume separately
            source.bypassEffects = true;
            source.bypassListenerEffects = true;
            AudioSettings.GetDSPBufferSize(out int frames, out int buffers);
            string directory = Path.Combine(Application.persistentDataPath, "audio-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            var record = new DeviceRecord { route = settings.route, mode = settings.mode, count = settings.count,
                volume_step = settings.volume_step, scheduled_lead_s = settings.scheduled_lead_s,
                app_sample_rate_hz = AudioSettings.outputSampleRate, buffer_frames = frames, buffer_count = buffers,
                unity_version = Application.unityVersion, platform = Application.platform.ToString() };
            File.WriteAllText(Path.Combine(directory, "device-settings.json"), JsonUtility.ToJson(record, true));
            writer = new StreamWriter(Path.Combine(directory, "events.csv"));
            writer.WriteLine("trial_id,request_host_s,dsp_request_s,scheduled_dsp_s,flash_command_host_s");
            writer.Flush();
            UnityEngine.Debug.Log("Audio spike ready; capture now. Output directory: " + directory);
            var random = new System.Random();
            double deadline = HostSeconds + settings.warmup_s;
            while (HostSeconds < deadline) yield return null;
            for (int i = 0; i < settings.count; i++)
            {
                double dsp = AudioSettings.dspTime;
                double scheduled = dsp + settings.scheduled_lead_s;
                double request = HostSeconds;
                if (settings.mode == "scheduled") source.PlayScheduled(scheduled);
                else source.Play();
                double flash = HostSeconds;
                Flash(); // command time only; photon arrival is a separately measured event
                writer.WriteLine(i + "," + Number(request) + "," + Number(dsp) + "," +
                    (settings.mode == "scheduled" ? Number(scheduled) : "") + "," + Number(flash));
                double darkAt = HostSeconds + .08;
                while (HostSeconds < darkAt) yield return null;
                RestoreCamera();
                writer.Flush();
                // At least 1.3 s between requests; matching uses disjoint <=0.8 s windows.
                deadline = request + 1.3 + random.NextDouble() * .7;
                while (HostSeconds < deadline) yield return null;
            }
            writer.Dispose(); writer = null;
            UnityEngine.Debug.Log("Audio spike finished; inspect capture and exported settings before analysis.");
        }

        void Flash()
        {
            previousMask = observer.cullingMask; previousClear = observer.clearFlags; previousColor = observer.backgroundColor;
            observer.cullingMask = 0; observer.clearFlags = CameraClearFlags.SolidColor; observer.backgroundColor = Color.white;
            flashing = true;
        }
        void RestoreCamera()
        {
            if (!flashing || observer == null) return;
            observer.cullingMask = previousMask; observer.clearFlags = previousClear; observer.backgroundColor = previousColor;
            flashing = false;
        }
        void OnDisable() { RestoreCamera(); if (source != null) source.Stop(); writer?.Dispose(); writer = null; }
    }
}
