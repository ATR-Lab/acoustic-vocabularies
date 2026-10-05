using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Text;
using AcousticVocab.StudyAudio;
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
    }
}
