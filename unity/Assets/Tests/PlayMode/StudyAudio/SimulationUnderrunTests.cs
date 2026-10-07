using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.Foundation;
using AcousticVocab.StudyAudio;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace AcousticVocab.Tests.StudyAudio
{
    // #81 audio_underrun: the SIMULATION_TEST hook really pauses the output
    // source mid-cue. Only the ordinary delivery/deadline checks name the fault.
    public sealed class SimulationUnderrunTests
    {
        GameObject sourceObject,listenerObject;AudioPlayer player;string root;SimulationTestAuthority authority;
        readonly List<AudioPlaybackEvent> events=new List<AudioPlaybackEvent>();
        static PcmWave SilentWave(int samples)
        {
            using var stream=new MemoryStream();using var writer=new BinaryWriter(stream);
            writer.Write(Encoding.ASCII.GetBytes("RIFF"));writer.Write(36+samples*2);writer.Write(Encoding.ASCII.GetBytes("WAVEfmt "));
            writer.Write(16);writer.Write((short)1);writer.Write((short)1);writer.Write(48000);writer.Write(96000);
            writer.Write((short)2);writer.Write((short)16);writer.Write(Encoding.ASCII.GetBytes("data"));writer.Write(samples*2);
            writer.Write(new byte[samples*2]);return PcmWave.ParseCanonical(stream.ToArray());
        }
        [UnitySetUp]public IEnumerator Setup()
        {
            events.Clear();root=Path.Combine(Path.GetTempPath(),".local","simulation-test-audio-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(root);
            string capability="{\"version\":1,\"scope\":\"SIMULATION_TEST\",\"fixture_set_sha256\":\""+new string('a',64)+"\",\"package_sha256\":\""+new string('b',64)+"\",\"schedule_sha256\":\""+new string('c',64)+
                "\",\"build_id\":\"mock-build\",\"protocol_version\":\"simulation-test-v1\",\"output_directory\":"+Newtonsoft.Json.JsonConvert.ToString(Path.Combine(root,"evidence"))+",\"audio_gain\":0.05,\"participant_admission\":false,\"acoustic_qualification\":false}";
            byte[] raw=new UTF8Encoding(false).GetBytes(capability);string path=Path.Combine(root,"capability.json");File.WriteAllBytes(path,raw);
            authority=SimulationTestAuthority.Load(path,PcmWave.Hash(raw),"mock-build","simulation-test-v1");
            listenerObject=new GameObject("Underrun listener");listenerObject.AddComponent<AudioListener>();
            sourceObject=new GameObject("Underrun source");player=sourceObject.AddComponent<AudioPlayer>();
            yield return null;
            Assert.That(AudioSettings.outputSampleRate,Is.EqualTo(48000),"The software callback test requires an actual 48 kHz output device.");
            player.Event+=events.Add;player.Configure(AudioRouteCalibration.Unmeasured("ENGINEERING_UNMEASURED"),()=>true);
            player.Preload(new Dictionary<string,PcmWave>{{"silence",SilentWave(48000)}},4*1024*1024);
        }
        [UnityTearDown]public IEnumerator Cleanup()
        {
            if(sourceObject!=null)UnityEngine.Object.Destroy(sourceObject);if(listenerObject!=null)UnityEngine.Object.Destroy(listenerObject);yield return null;
            if(Directory.Exists(root))Directory.Delete(root,true);
        }
        [UnityTest]public IEnumerator StalledAudioThreadDuringDeliveryIsDetectedAsUnderrunNotCompletion()
        {
            Assert.Throws<AudioFault>(()=>player.SimulationStallAudioThread(null,500));
            Assert.That(player.SimulationStallAudioThread(authority,500),Is.EqualTo("AUDIO_NOT_DELIVERING"));
            player.ScheduleCalibration("silence",AudioPlayer.Now+.25);double deadline=AudioPlayer.Now+2;
            while(!events.Any(e=>e.Code=="CALIBRATION_DELIVERY_OBSERVED")&&AudioPlayer.Now<deadline)yield return null;
            Assert.That(events.Any(e=>e.Code=="CALIBRATION_DELIVERY_OBSERVED"),Is.True,"delivery never started");
            Assert.That(player.SimulationStallAudioThread(authority,500),Is.EqualTo("AUDIO_STALL_PENDING"),"armed for the next audio callback");
            deadline=AudioPlayer.Now+2;while(player.SimulationStallAudioThread(authority,500)!=null&&AudioPlayer.Now<deadline)yield return null;
            Assert.That(player.SimulationStallAudioThread(authority,500),Is.Null,"the audio callback thread actually stalled once");
            deadline=AudioPlayer.Now+4;while(player.Playing&&AudioPlayer.Now<deadline)yield return null;
            Assert.That(player.Playing,Is.False);var last=events.Last();
            Assert.That(last.Code,Is.EqualTo("AUDIO_UNDERRUN"));Assert.That(last.DeliveredSamples,Is.LessThan(48000));Assert.That(last.CallbackCount,Is.GreaterThan(0));
            Assert.That(events.Any(e=>e.Code=="AUDIO_PLAYBACK_COMPLETED"),Is.False);Assert.That(player.Ready,Is.False,"The ordinary failure latch holds");
        }
    }
}
