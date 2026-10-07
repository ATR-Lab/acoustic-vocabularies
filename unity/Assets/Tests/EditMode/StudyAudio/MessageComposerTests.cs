using System;
using System.IO;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Tests.StudyAudio
{
    public sealed class MessageComposerTests
    {
        [Test] public void AllFourIndependentPatternVectorsMatchWithoutRendering()
        {
            var vectors=JObject.Parse(File.ReadAllText(Path.Combine(PackageLoaderTests.Repository,"sound","testvectors","composition","vectors.json")));
            foreach(var p in (JArray)vectors["patterns"])
            {
                PcmWave Part(string key) { var v=p[key]; return PackageLoaderTests.PatternWave((int)v["n_samples"],(int)v["mul"],(int)v["add"]); }
                var a=Part("action"); var r=Part("referent");
                Assert.That(a.PcmSha256,Is.EqualTo((string)p["action_pcm_sha256"])); Assert.That(r.PcmSha256,Is.EqualTo((string)p["referent_pcm_sha256"]));
                Assert.That(MessageComposer.CompositeHash(a,r),Is.EqualTo((string)p["composite_sha256"]));
                Assert.That(a.SampleCount+9600+r.SampleCount,Is.EqualTo((int)p["n_samples"]));
            }
        }
        [Test] public void IntegerToFloatConversionPreservesCanonicalScale()
        {
            var wave=PackageLoaderTests.PatternWave(21600,0,0);
            Assert.That(wave.CopySamples()[0],Is.EqualTo(-32767/32768f));
            Assert.That(wave.SampleCount,Is.EqualTo(21600)); Assert.That(PcmWave.SampleRate,Is.EqualTo(48000));
        }
        [Test] public void FaultMessagesCannotCarryPathsOrUnboundedText()
        {
            Assert.That(new AudioFault("C:/private/path").Code,Is.EqualTo("AUDIO_FAULT"));
            Assert.That(new AudioFault("AUDIO_UNDERRUN").Message,Is.EqualTo("AUDIO_UNDERRUN"));
        }
    }
}
