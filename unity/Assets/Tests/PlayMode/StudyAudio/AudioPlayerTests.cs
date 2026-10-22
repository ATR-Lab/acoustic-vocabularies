using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace AcousticVocab.Tests.StudyAudio
{
    public sealed class AudioPlayerTests
    {
        GameObject sourceObject,listenerObject;
        AudioPlayer player;
        readonly List<AudioPlaybackEvent> events=new List<AudioPlaybackEvent>();
        static PcmWave SilentWave()
        {
            const int samples=9600;using var stream=new MemoryStream();using var writer=new BinaryWriter(stream);
            writer.Write(Encoding.ASCII.GetBytes("RIFF"));writer.Write(36+samples*2);writer.Write(Encoding.ASCII.GetBytes("WAVEfmt "));
            writer.Write(16);writer.Write((short)1);writer.Write((short)1);writer.Write(48000);writer.Write(96000);
            writer.Write((short)2);writer.Write((short)16);writer.Write(Encoding.ASCII.GetBytes("data"));writer.Write(samples*2);
            writer.Write(new byte[samples*2]);return PcmWave.ParseCanonical(stream.ToArray());
        }
        [UnitySetUp] public IEnumerator Setup()
        {
            events.Clear();listenerObject=new GameObject("Audio test listener");listenerObject.AddComponent<AudioListener>();
            sourceObject=new GameObject("Audio test source");player=sourceObject.AddComponent<AudioPlayer>();
            yield return null;
            Assert.That(AudioSettings.outputSampleRate,Is.EqualTo(48000),"The software callback test requires an actual 48 kHz output device.");
            player.Event+=events.Add;player.Configure(AudioRouteCalibration.Unmeasured("ENGINEERING_UNMEASURED"),()=>true);
            player.Preload(new Dictionary<string,PcmWave>{{"silence",SilentWave()}},1024*1024);
        }
        [UnityTearDown] public IEnumerator Cleanup()
        { if(sourceObject!=null) UnityEngine.Object.Destroy(sourceObject);if(listenerObject!=null) UnityEngine.Object.Destroy(listenerObject);yield return null; }
        [UnityTest] public IEnumerator OneHundredSilentPlaysRequireActualCallbackCoverage()
        {
            // Real Unity DSP callbacks, silent stored PCM. This does not measure
            // acoustic delivery, headset performance, route offset or loudness.
            for(int i=0;i<100;i++)
            {
                int first=events.Count;player.ScheduleCalibration("silence",AudioPlayer.Now+.25);
                double deadline=AudioPlayer.Now+2;
                while(player.Playing && AudioPlayer.Now<deadline) yield return null;
                Assert.That(player.Playing,Is.False,"software delivery timeout at play "+i);
                Assert.That(events.Count-first,Is.EqualTo(3),"event count at play "+i);
                Assert.That(events[first].Code,Is.EqualTo("AUDIO_REQUESTED"));
                Assert.That(events[first+1].Code,Is.EqualTo("CALIBRATION_DELIVERY_OBSERVED"));
                Assert.That(events[first+2].Code,Is.EqualTo("AUDIO_PLAYBACK_COMPLETED"));
                Assert.That(events[first+2].DeliveredSamples,Is.EqualTo(9600));Assert.That(events[first+2].CallbackCount,Is.GreaterThan(0));
                Assert.That(events[first+2].Timing.OnsetEstimateMonoSeconds,Is.Null);
                Assert.That(player.TrialReady,Is.False);
            }
        }
        [UnityTest] public IEnumerator SourceIsNonspatialAndEffectsAreRejected()
        {
            var source=sourceObject.GetComponent<AudioSource>();Assert.That(source.spatialBlend,Is.Zero);
            Assert.That(source.panStereo,Is.Zero);Assert.That(source.dopplerLevel,Is.Zero);Assert.That(source.spatialize,Is.False);
            sourceObject.AddComponent<AudioEchoFilter>();
            Assert.Throws<AudioFault>(()=>player.ScheduleCalibration("silence",AudioPlayer.Now+.3));
            Assert.That(player.Ready,Is.False);yield return null;
        }
        [UnityTest] public IEnumerator ReplacedClipRevokesScheduledPlayback()
        {
            player.ScheduleCalibration("silence",AudioPlayer.Now+.3);sourceObject.GetComponent<AudioSource>().clip=null;
            yield return null;Assert.That(player.Playing,Is.False);Assert.That(player.Ready,Is.False);
            Assert.That(events[events.Count-1].Code,Is.EqualTo("AUDIO_PATH_CHANGED"));
        }
        static string Repository
        {
            get
            {
                var directory=new DirectoryInfo(Directory.GetCurrentDirectory());
                while(directory!=null && !File.Exists(Path.Combine(directory.FullName,"apparatus/examples/audio-onset-calibration.example.json"))) directory=directory.Parent;
                Assert.That(directory,Is.Not.Null); return directory.FullName;
            }
        }
        // A test-only "qualified" variant of the committed synthetic record, measured
        // (by declaration) with the given DSP buffer. No route has been measured.
        static AudioRouteCalibration RecordQualified(int frames,int count)
        {
            const string device="Synthetic fixture: no headset or earphones measured";
            string text=File.ReadAllText(Path.Combine(Repository,"apparatus/examples/audio-onset-calibration.example.json"))
                .Replace("\"status\": \"provisional\"","\"status\": \"qualified\"")
                .Replace("\"evidence_kind\": \"synthetic_fixture\"","\"evidence_kind\": \"physical_measurement\"")
                .Replace("\"full_scene_loaded\": false","\"full_scene_loaded\": true")
                .Replace("\"review\": null","\"review\": {\"reviewed_by\": \"Fixture reviewer\", \"reviewed_at\": \"2027-04-07T10:00:00Z\"}")
                .Replace("\"buffer_frames\": 1024","\"buffer_frames\": "+frames).Replace("\"buffer_count\": 4","\"buffer_count\": "+count);
            byte[] bytes=new UTF8Encoding(false).GetBytes(text);
            var station=new JObject { ["station_id"]="EXAMPLE_STATION",["audio"]=new JObject { ["route"]="EXAMPLE_ROUTE",["buffer_samples"]=frames,["route_offset_ms"]=37.348956,
                ["connection_mode"]="wired_3_5mm_earphones",["output_device"]=device,["onset_calibration_record_sha256"]=PcmWave.Hash(bytes) } };
            var route=AudioRouteCalibration.FromStationConfig(station,bytes,new AudioRuntimeRoute("EXAMPLE_STATION","EXAMPLE_ROUTE","wired_3_5mm_earphones",device,48000,frames,count));
            Assert.That(route.IsQualified,Is.True); return route;
        }
        [UnityTest] public IEnumerator QualifiedRecordRouteIsBoundToTheMeasuredDeviceFormat()
        {
            AudioSettings.GetDSPBufferSize(out int frames,out int count);
            Assume.That(frames>=64 && frames<=8192 && count>=1 && count<=16,"device DSP buffer outside the record schema range");
            player.Configure(RecordQualified(frames,count),()=>true);Assert.That(player.TrialReady,Is.True);
            LogAssert.Expect(LogType.Error,new Regex("^AUDIO_CALIBRATION_FAULT AUDIO_CALIBRATION_RUNTIME_MISMATCH record_sha256=[0-9a-f]{64}$"));
            var other=RecordQualified(frames==1024?512:1024,count);
            Assert.That(Assert.Throws<AudioFault>(()=>player.Configure(other,()=>true)).Code,Is.EqualTo("AUDIO_CALIBRATION_RUNTIME_MISMATCH"));
            yield return null;
        }
        [UnityTest] public IEnumerator DisablingObserverStopsSeparateAudioSourceAndLatchesFault()
        {
            player.ScheduleCalibration("silence",AudioPlayer.Now+.3);player.enabled=false;
            Assert.That(player.Playing,Is.False);Assert.That(player.Ready,Is.False);
            Assert.That(sourceObject.GetComponent<AudioSource>().isPlaying,Is.False);
            Assert.That(events[events.Count-1].Code,Is.EqualTo("AUDIO_COMPONENT_DISABLED"));
            player.enabled=true;yield return null;Assert.That(player.Ready,Is.False);
        }
    }
}
