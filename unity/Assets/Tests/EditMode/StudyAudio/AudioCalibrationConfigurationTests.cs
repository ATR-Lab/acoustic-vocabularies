using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Tests.StudyAudio
{
    public sealed class AudioCalibrationConfigurationTests
    {
        static JObject Example() => JObject.Parse(File.ReadAllText(Path.Combine(PackageLoaderTests.Repository,"apparatus/examples/audio-calibration.example.json")));
        static AudioCalibrationConfiguration Load(JObject value) => AudioCalibrationConfiguration.Load(Encoding.UTF8.GetBytes(value.ToString()));
        [Test] public void StoredOrderAndPrivateConfigHashArePreserved()
        {
            var value=Example();value["profile_order"]=new JArray("P3","P1","P2");
            byte[] bytes=Encoding.UTF8.GetBytes(value.ToString());var loaded=AudioCalibrationConfiguration.Load(bytes);
            Assert.That(loaded.ProfileOrder,Is.EqualTo(new[]{"P3","P1","P2"}));
            Assert.That(loaded.ConfigSha256,Is.EqualTo(PcmWave.Hash(bytes)));Assert.That(loaded.DesktopPreview,Is.False);
        }
        [Test] public void ArbitraryStudySoundCannotReplacePinnedExample()
        {
            var value=Example();value["examples"]["P1"]["file_sha256"]=new string('a',64);
            Assert.Throws<AudioIntegrityException>(()=>Load(value));
            value=Example();value["examples"]["P2"]["file"]="../messages/heldout.wav";
            Assert.Throws<AudioIntegrityException>(()=>Load(value));
        }
        [Test] public void ProfileOrderMustBeACompleteStoredPermutation()
        {
            var value=Example();value["profile_order"]=new JArray("P1","P1","P3");Assert.Throws<AudioIntegrityException>(()=>Load(value));
            value["profile_order"]=new JArray("P1","P2");Assert.Throws<AudioIntegrityException>(()=>Load(value));
        }
        [Test] public void ConfigHasNoStudyOrAcousticEvidenceOverride()
        {
            var value=Example();value["route_offset_ms"]=0;Assert.Throws<AudioIntegrityException>(()=>Load(value));
            value=Example();value["heldout"]=true;Assert.Throws<AudioIntegrityException>(()=>Load(value));
            value=Example();value["preload_budget_bytes"]=1;Assert.Throws<AudioIntegrityException>(()=>Load(value));
        }
        [Test] public void AllThreePinsMatchTheExistingProducerRegistry()
        {
            var registry=JObject.Parse(File.ReadAllText(Path.Combine(PackageLoaderTests.Repository,"sound/reserved/registry.json")));
            var rows=(JArray)registry["entries"];
            for(int i=0;i<3;i++)
            {
                var row=rows.Single(x=>(string)x["id"]=="calibration-P"+(i+1));
                Assert.That((string)row["file_sha256"],Is.EqualTo(AudioCalibrationConfiguration.FileHashes[i]));
                Assert.That((string)row["pcm_sha256"],Is.EqualTo(AudioCalibrationConfiguration.PcmHashes[i]));
                Assert.That((int)row["n_samples"],Is.EqualTo(96000));
            }
        }
    }
}
