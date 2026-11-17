using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using AcousticVocab.StudyAudio;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace AcousticVocab.Tests.StudyAudio
{
    // Requirement trace tests on the real AudioPlayer with silent stored PCM.
    // Software output path only; nothing here measures acoustic delivery.
    public sealed class RequirementTraceAudioPlayerTests
    {
        readonly List<GameObject> created=new List<GameObject>();
        readonly List<AudioPlaybackEvent> events=new List<AudioPlaybackEvent>();
        static AudioRouteCalibration Qualified() => new AudioRouteCalibration("route-01",0,1,new string('a',64));

        static PcmWave Silent(int samples)
        {
            using var stream=new MemoryStream();using var writer=new BinaryWriter(stream);
            writer.Write(Encoding.ASCII.GetBytes("RIFF"));writer.Write(36+samples*2);writer.Write(Encoding.ASCII.GetBytes("WAVEfmt "));
            writer.Write(16);writer.Write((short)1);writer.Write((short)1);writer.Write(48000);writer.Write(96000);
            writer.Write((short)2);writer.Write((short)16);writer.Write(Encoding.ASCII.GetBytes("data"));writer.Write(samples*2);
            writer.Write(new byte[samples*2]);return PcmWave.ParseCanonical(stream.ToArray());
        }
        AudioPlayer NewPlayer(string name)
        {
            var go=new GameObject(name);created.Add(go);var player=go.AddComponent<AudioPlayer>();player.Event+=events.Add;return player;
        }
        void Configure(AudioPlayer player,AudioRouteCalibration route,IReadOnlyDictionary<string,PcmWave> waves=null)
        {
            player.Configure(route,()=>true);
            player.Preload(waves??new Dictionary<string,PcmWave>{{"silence",Silent(9600)}},4*1024*1024);
        }
        IEnumerator WaitIdle(AudioPlayer player,double seconds=3)
        { double deadline=AudioPlayer.Now+seconds;while(player.Playing && AudioPlayer.Now<deadline) yield return null; }

        [UnitySetUp] public IEnumerator Setup()
        {
            events.Clear();var listener=new GameObject("Trace audio listener");listener.AddComponent<AudioListener>();created.Add(listener);
            yield return null;
            Assert.That(AudioSettings.outputSampleRate,Is.EqualTo(48000),"The software callback test requires an actual 48 kHz output device.");
        }
        [UnityTearDown] public IEnumerator Cleanup()
        { foreach(var go in created) if(go!=null) UnityEngine.Object.Destroy(go);created.Clear();yield return null; }

        [UnityTest, Category("REQ_AUD_02"), NUnit.Framework.Property("Requirement","REQ-AUD-02")]
        public IEnumerator PanSpatialReverbOrEffectChangesRefuseSchedulingOnTheStudyPath()
        {
            var mutations=new (string name,Action<AudioSource> change)[]{
                ("spatial blend",s=>s.spatialBlend=1),("stereo pan",s=>s.panStereo=-.5f),("doppler",s=>s.dopplerLevel=1),
                ("reverb zones",s=>s.bypassReverbZones=false),("listener effects",s=>s.bypassListenerEffects=false),("effects bypass",s=>s.bypassEffects=true)};
            foreach(var mutation in mutations)
            {
                var player=NewPlayer("Trace source "+mutation.name);yield return null;Configure(player,Qualified());
                var source=player.GetComponent<AudioSource>();
                Assert.That(source.spatialBlend,Is.Zero);Assert.That(source.panStereo,Is.Zero);Assert.That(source.bypassReverbZones,Is.True);
                Assert.That(player.TrialReady,Is.True,"positive control: "+mutation.name);
                int before=events.Count;mutation.change(source);
                var fault=Assert.Throws<AudioFault>(()=>player.Schedule("silence",AudioPlayer.Now+.4),mutation.name);
                Assert.That(fault.Code,Is.EqualTo("AUDIO_PATH_CHANGED"),mutation.name);
                Assert.That(events.Count,Is.EqualTo(before),"no request is recorded for "+mutation.name);
                Assert.That(source.isPlaying,Is.False);Assert.That(player.Ready,Is.False);
            }
        }

        [UnityTest, Category("REQ_AUD_02"), NUnit.Framework.Property("Requirement","REQ-AUD-02")]
        public IEnumerator DifferentAudioIdsPlayThroughOneUnchangedNonspatialMonoPath()
        {
            var player=NewPlayer("Trace shared source");yield return null;
            Configure(player,AudioRouteCalibration.Unmeasured("ENGINEERING_UNMEASURED"),new Dictionary<string,PcmWave>{{"first",Silent(9600)},{"second",Silent(14400)}});
            var source=player.GetComponent<AudioSource>();
            Assert.That(player.GetComponents<AudioSource>().Length,Is.EqualTo(1));
            var seen=new List<string>();
            void Snapshot(AudioPlaybackEvent e)
            {
                if(e.Code!="CALIBRATION_DELIVERY_OBSERVED") return;
                seen.Add(e.AudioId+"|"+source.spatialBlend+"|"+source.panStereo+"|"+source.dopplerLevel+"|"+(source.outputAudioMixerGroup==null)+"|"+
                    source.bypassReverbZones+"|"+source.bypassListenerEffects+"|"+source.clip.channels+"|"+source.clip.frequency+"|"+player.GetComponents<AudioSource>().Length+"|"+ReferenceEquals(player.GetComponent<AudioSource>(),source));
            }
            player.Event+=Snapshot;
            foreach(string id in new[]{"first","second"})
            {
                player.ScheduleCalibration(id,AudioPlayer.Now+.25);yield return WaitIdle(player);
                Assert.That(player.Playing,Is.False,id);Assert.That(events.Last().Code,Is.EqualTo("AUDIO_PLAYBACK_COMPLETED"),id);
            }
            Assert.That(seen.Count,Is.EqualTo(2));
            const string expected="|0|0|0|True|True|True|1|48000|1|True";
            Assert.That(seen[0],Is.EqualTo("first"+expected));Assert.That(seen[1],Is.EqualTo("second"+expected));
        }

        [UnityTest, Category("REQ_AUD_03"), NUnit.Framework.Property("Requirement","REQ-AUD-03")]
        public IEnumerator UnmeasuredRouteRefusesStudyAudioBeforeAnyRequestRecord()
        {
            var player=NewPlayer("Trace unmeasured route");yield return null;
            Configure(player,AudioRouteCalibration.Unmeasured("ENGINEERING_UNMEASURED"));
            Assert.That(player.Ready,Is.True);Assert.That(player.TrialReady,Is.False);
            Assert.That(Assert.Throws<AudioFault>(()=>player.Schedule("silence",AudioPlayer.Now+.4)).Code,Is.EqualTo("AUDIO_ROUTE_UNCALIBRATED"));
            Assert.That(events,Is.Empty);Assert.That(player.GetComponent<AudioSource>().isPlaying,Is.False);
            var calibrated=NewPlayer("Trace calibrated route");yield return null;Configure(calibrated,Qualified());
            Assert.That(calibrated.TrialReady,Is.True);calibrated.Schedule("silence",AudioPlayer.Now+.3);
            Assert.That(events.First().Code,Is.EqualTo("AUDIO_REQUESTED"));Assert.That(events.First().Timing.OnsetEstimateMonoSeconds,Is.Not.Null);
            yield return WaitIdle(calibrated);Assert.That(events.Last().Code,Is.EqualTo("AUDIO_PLAYBACK_COMPLETED"));
        }

        [UnityTest, Category("REQ_AUD_03"), NUnit.Framework.Property("Requirement","REQ-AUD-03")]
        public IEnumerator OutputDeviceChangeStopsPlaybackAndRevokesTheCalibratedRoute()
        {
            var player=NewPlayer("Trace device change");yield return null;Configure(player,Qualified());
            Assert.That(player.TrialReady,Is.True);player.Schedule("silence",AudioPlayer.Now+.4);Assert.That(player.Playing,Is.True);
            var changed=typeof(AudioPlayer).GetMethod("OnAudioConfigurationChanged",BindingFlags.Instance|BindingFlags.NonPublic);
            Assert.That(changed,Is.Not.Null);changed.Invoke(player,new object[]{true});
            Assert.That(events.Last().Code,Is.EqualTo("AUDIO_DEVICE_CHANGED"));
            Assert.That(player.Playing,Is.False);Assert.That(player.Ready,Is.False);Assert.That(player.TrialReady,Is.False);
            Assert.That(player.GetComponent<AudioSource>().isPlaying,Is.False);
            Assert.That(Assert.Throws<AudioFault>(()=>player.Schedule("silence",AudioPlayer.Now+.4)).Code,Is.EqualTo("AUDIO_NOT_READY"));
            yield return null;
        }

        [UnityTest, Category("REQ_AUD_05"), NUnit.Framework.Property("Requirement","REQ-AUD-05")]
        public IEnumerator AppliedComfortGainCannotBeRetunedDuringPlaybackOrOnTheSource()
        {
            var player=NewPlayer("Trace gain");yield return null;Configure(player,Qualified());
            player.SetComfortableGain(.3f);var source=player.GetComponent<AudioSource>();
            Assert.That(player.CurrentGain,Is.EqualTo(.3f));Assert.That(source.volume,Is.EqualTo(.3f));
            player.Schedule("silence",AudioPlayer.Now+.4);
            Assert.That(Assert.Throws<AudioFault>(()=>player.SetComfortableGain(.6f)).Code,Is.EqualTo("AUDIO_GAIN_INVALID"));
            Assert.That(player.CurrentGain,Is.EqualTo(.3f));Assert.That(source.volume,Is.EqualTo(.3f));
            source.volume=.6f;yield return null;yield return null;
            Assert.That(player.Playing,Is.False);Assert.That(events.Last().Code,Is.EqualTo("AUDIO_PATH_CHANGED"));Assert.That(player.Ready,Is.False);
        }

        [UnityTest, Category("REQ_VOC_03"), NUnit.Framework.Property("Requirement","REQ-VOC-03")]
        public IEnumerator ChangedPcmIsRefusedAtPreloadSoNothingCanBeScheduled()
        {
            var player=NewPlayer("Trace corrupted preload");yield return null;
            var wave=Silent(9600);((byte[])typeof(PcmWave).GetField("pcm",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(wave))[10]^=1;
            player.Configure(Qualified(),()=>true);
            var fault=Assert.Throws<AudioIntegrityException>(()=>player.Preload(new Dictionary<string,PcmWave>{{"changed",wave}},1024*1024));
            Assert.That(fault.Code,Is.EqualTo("HASH_MISMATCH"));
            Assert.That(player.Ready,Is.False);Assert.That(player.EstimatedPreloadBytes,Is.Zero);
            Assert.That(Assert.Throws<AudioFault>(()=>player.Schedule("changed",AudioPlayer.Now+.4)).Code,Is.EqualTo("AUDIO_NOT_READY"));
            Assert.That(events,Is.Empty);Assert.That(player.GetComponent<AudioSource>().isPlaying,Is.False);
        }
    }
}
