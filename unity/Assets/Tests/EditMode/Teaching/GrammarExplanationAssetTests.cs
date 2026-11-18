using System;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace AcousticVocab.Teaching.Tests
{
    // Every WAV here is synthesised at run time; no recording is committed.
    public sealed class GrammarExplanationAssetTests
    {
        string directory;byte[] registry;

        static byte[] Wave(int samples,int channels=1,int seed=7)
        {
            using var stream=new MemoryStream();using var w=new BinaryWriter(stream);
            w.Write(Encoding.ASCII.GetBytes("RIFF"));w.Write(36+samples*2*channels);w.Write(Encoding.ASCII.GetBytes("WAVEfmt "));w.Write(16);w.Write((short)1);w.Write((short)channels);
            w.Write(48000);w.Write(96000*channels);w.Write((short)(2*channels));w.Write((short)16);w.Write(Encoding.ASCII.GetBytes("data"));w.Write(samples*2*channels);
            for(int i=0;i<samples*channels;i++)w.Write((short)((i*seed)%2001-1000));
            return stream.ToArray();
        }
        string Put(string id,byte[] bytes){File.WriteAllBytes(Path.Combine(directory,id+".wav"),bytes);return PcmWave.Hash(bytes);}

        [SetUp]public void Create()
        {
            directory=Path.Combine(Path.GetTempPath(),"av-grammar-explanation-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(directory);
            var entries=new JArray();
            foreach(var (id,kind,samples) in new[]{("ready-cue","ready_cue",15360),("click-grammar-demo","click",13824)})
            {
                var parsed=PcmWave.ParseCanonical(Wave(samples));Put(id,Wave(samples));
                entries.Add(new JObject{["id"]=id,["kind"]=kind,["profile"]=null,["recipe"]=null,["n_samples"]=samples,["file_sha256"]=parsed.FileSha256,["pcm_sha256"]=parsed.PcmSha256});
            }
            registry=Encoding.UTF8.GetBytes(new JObject{["registry_version"]=1,["entries"]=entries}.ToString());
        }
        [TearDown]public void Remove(){if(Directory.Exists(directory))Directory.Delete(directory,true);}
        GrammarAssets Reserved()=>GrammarAssets.Load(directory,registry,PcmWave.Hash(registry));
        string Refusal(string pin)=>Assert.Throws<SessionFault>(()=>Reserved().WithExplanation(pin)).Code;

        [Test, Category("REQ_GRAM_02"), NUnit.Framework.Property("Requirement", "REQ-GRAM-02")]
        public void OneFixedRecordingIsLoadedOnlyByItsPinnedFileHash()
        {
            Assert.That(GrammarAssets.ExplanationId,Is.EqualTo("grammar-explanation"));
            // Neither loader step takes a study, role or lesson-format input.
            Assert.That(typeof(GrammarAssets).GetMethod("Load").GetParameters().Select(p=>p.Name),Is.EqualTo(new[]{"directory","registry","expectedRegistrySha256"}));
            Assert.That(typeof(GrammarAssets).GetMethod("WithExplanation").GetParameters().Select(p=>p.Name),Is.EqualTo(new[]{"expectedFileSha256"}));
            string pin=Put(GrammarAssets.ExplanationId,Wave(240000));
            var reserved=Reserved();Assert.That(reserved.Explanation,Is.Null);
            Assert.That(Assert.Throws<SessionFault>(()=>reserved.Preload("e","r","c")).Code,Is.EqualTo("GRAMMAR_EXPLANATION_MISSING"));
            var first=reserved.WithExplanation(pin);var second=Reserved().WithExplanation(pin);
            Assert.That(first.Explanation.FileSha256,Is.EqualTo(pin));Assert.That(second.Explanation.PcmSha256,Is.EqualTo(first.Explanation.PcmSha256));
            Assert.That(first.Explanation.SampleCount,Is.EqualTo(240000));Assert.That(first.RegistrySha256,Is.EqualTo(reserved.RegistrySha256));
            Assert.That(first.Preload("e","r","c").Select(p=>(p.Key,p.Value.PcmSha256)),Is.EqualTo(new[]{("e",first.Explanation.PcmSha256),("r",first.Ready.PcmSha256),("c",first.Clicks.PcmSha256)}));
            Assert.That(Assert.Throws<SessionFault>(()=>first.WithExplanation(pin)).Code,Is.EqualTo("GRAMMAR_EXPLANATION_STATE"));
            Assert.That(Refusal(new string('0',64)),Is.EqualTo("GRAMMAR_EXPLANATION_HASH"));
            Assert.That(Refusal(pin.ToUpperInvariant()),Is.EqualTo("GRAMMAR_EXPLANATION_HASH"));
            Assert.That(Refusal(null),Is.EqualTo("GRAMMAR_EXPLANATION_HASH"));
            // A changed recording under the same name is refused against the old pin.
            Put(GrammarAssets.ExplanationId,Wave(240000,seed:11));Assert.That(Refusal(pin),Is.EqualTo("GRAMMAR_EXPLANATION_HASH"));
            File.Delete(Path.Combine(directory,GrammarAssets.ExplanationId+".wav"));Assert.That(Refusal(pin),Is.EqualTo("GRAMMAR_EXPLANATION_MISSING"));
            Assert.That(Refusal(Put(GrammarAssets.ExplanationId,Wave(120000,channels:2))),Is.EqualTo("GRAMMAR_EXPLANATION_FORMAT"));
        }

        [Test, Category("REQ_GRAM_04"), NUnit.Framework.Property("Requirement", "REQ-GRAM-04")]
        public void ExplanationCannotBeAMotifMessageOrReservedSignal()
        {
            // Motifs are 21,600 to 43,200 samples; a message or the calibration example is at most 96,000.
            foreach(int samples in new[]{13824,15360,21600,43200,52800,96000})
            {var bytes=Wave(samples);Assert.That(Refusal(Put(GrammarAssets.ExplanationId,bytes)),Is.EqualTo("GRAMMAR_EXPLANATION_LENGTH"),samples.ToString());}
            // The reserved READY cue itself, copied under the explanation name, is refused.
            byte[] ready=File.ReadAllBytes(Path.Combine(directory,"ready-cue.wav"));Assert.That(Refusal(Put(GrammarAssets.ExplanationId,ready)),Is.EqualTo("GRAMMAR_EXPLANATION_LENGTH"));
            Assert.That(Refusal(Put(GrammarAssets.ExplanationId,Wave(GrammarAssets.ExplanationMaximumSamples+1))),Is.EqualTo("GRAMMAR_EXPLANATION_LENGTH"));
            var shortest=Reserved().WithExplanation(Put(GrammarAssets.ExplanationId,Wave(GrammarAssets.ExplanationMinimumSamples)));
            Assert.That(shortest.Explanation.SampleCount,Is.EqualTo(96001));
            Assert.That(new[]{shortest.Ready.PcmSha256,shortest.Clicks.PcmSha256},Has.No.Member(shortest.Explanation.PcmSha256));
        }

        [Test, Category("REQ_GRAM_03"), NUnit.Framework.Property("Requirement", "REQ-GRAM-03")]
        public void FamiliarizationRefusesToStartWithoutTheExplanation()
        {
            var node=new GameObject("Grammar explanation refusal");
            try
            {
                var player=node.AddComponent<AudioPlayer>();int rows=0;
                var code=Assert.Throws<SessionFault>(()=>new GrammarFamiliarization(Reserved(),player,AudioRouteCalibration.Unmeasured("ENGINEERING_UNMEASURED"),.05f,
                    ()=>true,_=>{},_=>rows++,_=>rows++,_=>rows++)).Code;
                Assert.That(code,Is.EqualTo("GRAMMAR_EXPLANATION_MISSING"));Assert.That(rows,Is.Zero);Assert.That(player.Ready,Is.False);
            }
            finally{UnityEngine.Object.DestroyImmediate(node);}
        }
    }
}
