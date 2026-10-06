using System;
using System.Collections.Generic;
using System.Reflection;
using AcousticVocab.StudyAudio;
using AcousticVocab.Teaching;
using NUnit.Framework;
using UnityEngine;

namespace AcousticVocab.Tests.Teaching
{
    public sealed class GrammarAudioFailureTests
    {
        static T New<T>(params object[] args)=>(T)Activator.CreateInstance(typeof(T),BindingFlags.Instance|BindingFlags.NonPublic,null,args,null);

        // Reproduces the native ordering: request persisted, then Schedule's
        // second validation refuses before any AudioSource play is submitted.
        // Stored silence and an unmeasured route never confer audio authority.
        [TestCase(false,"AUDIO_EXPOSURE_BLOCKED")]
        [TestCase(true,"AUDIO_PATH_CHANGED")]
        public void RefusalAfterDurableRequestRetainsExactAudioFaultAndNeverReplays(bool changePath,string expected)
        {
            var node=new GameObject("Grammar failure source");
            GrammarFamiliarization grammar=null;
            try
            {
                var player=node.AddComponent<AudioPlayer>();
                Assert.That(AudioSettings.outputSampleRate,Is.EqualTo(48000));
                var wave=New<PcmWave>(new byte[30720],new string('a',64),null,null);
                var assets=New<GrammarAssets>(wave,wave,new string('b',64));
                var flow=new List<string>();var audio=new List<AudioPlaybackEvent>();var faults=new List<string>();bool ready=true;
                grammar=new GrammarFamiliarization(assets,player,AudioRouteCalibration.Unmeasured("ENGINEERING_UNMEASURED"),.05f,
                    ()=>ready,_=>{},row=>flow.Add((string)row["kind"]),value=>
                    {
                        audio.Add(value);
                        if(value.Code=="AUDIO_REQUESTED")
                        {if(changePath)node.GetComponent<AudioSource>().mute=true;else ready=false;}
                    },faults.Add);
                grammar.Tick();
                Assert.That(flow,Is.EqualTo(new[]{"grammar_request","grammar_interrupted"}));
                Assert.That(audio.Count,Is.EqualTo(1));Assert.That(audio[0].Code,Is.EqualTo("AUDIO_REQUESTED"));
                Assert.That(audio[0].CallbackCount,Is.Zero);Assert.That(faults,Is.EqualTo(new[]{expected}));
                Assert.That(grammar.Complete,Is.False);Assert.That(player.Playing,Is.False);Assert.That(player.Ready,Is.False);
                Assert.That(node.GetComponent<AudioSource>().isPlaying,Is.False);
                grammar.Tick();Assert.That(audio.Count,Is.EqualTo(1));Assert.That(faults.Count,Is.EqualTo(1));
            }
            finally{grammar?.Dispose();UnityEngine.Object.DestroyImmediate(node);}
        }
    }
}
