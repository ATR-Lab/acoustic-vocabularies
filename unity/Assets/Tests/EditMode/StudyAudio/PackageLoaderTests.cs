using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Text;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Tests.StudyAudio
{
    // Pattern PCM plus the public DEMO document scaffolds. These tests never
    // synthesize or open participant packages, and publish no WAV fixtures.
    public sealed class PackageLoaderTests
    {
        string directory;
        internal static string Repository
        {
            get
            {
                var p=new DirectoryInfo(Directory.GetCurrentDirectory());
                while(p!=null && !Directory.Exists(Path.Combine(p.FullName,"sound","examples","package-demo"))) p=p.Parent;
                Assert.That(p,Is.Not.Null,"run from the repository or Unity project"); return p.FullName;
            }
        }
        static byte[] Wave(byte[] pcm)
        {
            using var stream=new MemoryStream(); using var writer=new BinaryWriter(stream);
            writer.Write(Encoding.ASCII.GetBytes("RIFF")); writer.Write(36+pcm.Length); writer.Write(Encoding.ASCII.GetBytes("WAVEfmt "));
            writer.Write(16); writer.Write((short)1); writer.Write((short)1); writer.Write(48000); writer.Write(96000);
            writer.Write((short)2); writer.Write((short)16); writer.Write(Encoding.ASCII.GetBytes("data")); writer.Write(pcm.Length); writer.Write(pcm); return stream.ToArray();
        }
        internal static byte[] Pattern(int samples,int mul,int add)
        {
            var bytes=new byte[samples*2];
            for(int i=0;i<samples;i++) { short v=(short)(((long)mul*i+add)%65535-32767); bytes[2*i]=(byte)v; bytes[2*i+1]=(byte)(v>>8); }
            return bytes;
        }
        internal static PcmWave PatternWave(int samples,int mul,int add) => PcmWave.ParseCanonical(Wave(Pattern(samples,mul,add)));
        static string WriteJson(string path,JObject value) { File.WriteAllText(path,value.ToString()+"\n",new UTF8Encoding(false)); return path; }
        [SetUp] public void Setup()
        {
            directory=Path.Combine(Path.GetTempPath(),"av-audio-test-"+Guid.NewGuid().ToString("N")); Directory.CreateDirectory(directory);
            string example=Path.Combine(Repository,"sound","examples","package-demo");
            foreach(string file in Directory.GetFiles(example,"*.json")) File.Copy(file,Path.Combine(directory,Path.GetFileName(file)));
            Directory.CreateDirectory(Path.Combine(directory,"atoms")); Directory.CreateDirectory(Path.Combine(directory,"messages"));
            var audio=JObject.Parse(File.ReadAllText(Path.Combine(directory,"audio.json"))); var pcms=new Dictionary<string,byte[]>(); int n=0;
            foreach(var atom in (JArray)audio["atoms"])
            {
                byte[] pcm=Pattern((int)atom["n_samples"],7+2*n,101*n++); pcms.Add((string)atom["atom_id"],pcm);
                byte[] wav=Wave(pcm); File.WriteAllBytes(Path.Combine(directory,(string)atom["path"]),wav);
                atom["pcm_sha256"]=PcmWave.Hash(pcm); atom["file_sha256"]=PcmWave.Hash(wav);
            }
            foreach(var item in (JArray)audio["messages"])
            {
                byte[] action=pcms[(string)item["action_atom"]],referent=pcms[(string)item["referent_atom"]];
                item["composite_sha256"]=MessageComposer.CompositeHash(PcmWave.ParseCanonical(Wave(action)),PcmWave.ParseCanonical(Wave(referent)));
                if((string)item["status"]=="trained")
                {
                    var pcm=new byte[action.Length+19200+referent.Length]; Buffer.BlockCopy(action,0,pcm,0,action.Length); Buffer.BlockCopy(referent,0,pcm,action.Length+19200,referent.Length);
                    byte[] wav=Wave(pcm); File.WriteAllBytes(Path.Combine(directory,(string)item["path"]),wav); item["file_sha256"]=PcmWave.Hash(wav);
                }
            }
            WriteJson(Path.Combine(directory,"audio.json"),audio); Seal();
        }
        void Seal()
        {
            string path=Path.Combine(directory,"manifest.json"); var manifest=JObject.Parse(File.ReadAllText(path));
            foreach(var file in ((JObject)manifest["files"]).Properties())
            { byte[] bytes=File.ReadAllBytes(Path.Combine(directory,file.Name)); file.Value["sha256"]=PcmWave.Hash(bytes); file.Value["bytes"]=bytes.Length; }
            manifest["package_sha256"]=PackageLoader.CanonicalPackageHash(manifest.ToString()); WriteJson(path,manifest);
        }
        string Hash => (string)JObject.Parse(File.ReadAllText(Path.Combine(directory,"manifest.json")))["package_sha256"];
        LoadedAudioPackage Load() => PackageLoader.Load(directory,Hash,true);
        [TearDown] public void Cleanup()
        { if(directory!=null && Directory.Exists(directory)) Directory.Delete(directory,true); }

        [Test] public void AllEighteenTrainedMessagesReproduceAndHoldoutsRemainHashOnly()
        {
            var package=Load(); Assert.That(package.CombinationsChecked,Is.EqualTo(32)); int trained=0,heldout=0;
            var audio=JObject.Parse(File.ReadAllText(Path.Combine(directory,"audio.json")));
            foreach(var message in (JArray)audio["messages"])
            {
                string id=(string)message["message_id"];
                Assert.That(package.CompositeHash(id),Is.EqualTo((string)message["composite_sha256"]));
                if((string)message["status"]=="trained")
                { Assert.That(package.ComposeTrainedMessage(id).CopyPcm16(),Is.EqualTo(package.ReadTrainedMessage(id).CopyPcm16())); trained++; }
                else { Assert.Throws<NovelSlotException>(()=>package.ReadTrainedMessage(id)); Assert.Throws<NovelSlotException>(()=>package.ComposeTrainedMessage(id)); heldout++; }
            }
            Assert.That(trained,Is.EqualTo(18)); Assert.That(heldout,Is.EqualTo(14));
        }
        [Test] public void ChangedWaveByteBlocksLoadAndPostLoadRead()
        {
            var package=Load(); string path=Path.Combine(directory,"atoms","K-a1.wav"); byte[] bytes=File.ReadAllBytes(path); bytes[101]^=1; File.WriteAllBytes(path,bytes);
            Assert.That(Assert.Throws<AudioIntegrityException>(()=>Load()).Code,Is.EqualTo("HASH_MISMATCH"));
            Assert.Throws<AudioIntegrityException>(()=>package.ReadAtom("K-a1"));
        }
        [Test] public void ResealedStereoOrMetadataWavStillRejected()
        {
            string path=Path.Combine(directory,"atoms","K-a1.wav"); byte[] bytes=File.ReadAllBytes(path); bytes[22]=2; File.WriteAllBytes(path,bytes); Seal();
            Assert.Throws<AudioIntegrityException>(()=>Load());
        }
        [Test] public void MissingExtraAndWrongExpectedHashAreRejected()
        {
            Assert.Throws<AudioIntegrityException>(()=>PackageLoader.Load(directory,new string('0',64),true));
            string extra=Path.Combine(directory,"extra.json"); File.WriteAllText(extra,"{}"); Assert.Throws<AudioIntegrityException>(()=>Load()); File.Delete(extra);
            File.Delete(Path.Combine(directory,"messages","K-a1-r1.wav")); Assert.Throws<AudioIntegrityException>(()=>Load());
        }
        [Test] public void ResealedIndexCannotTurnHeldoutIntoTrainedOrChangeMeaning()
        {
            string path=Path.Combine(directory,"audio.json"); var audio=JObject.Parse(File.ReadAllText(path)); audio["messages"][1]["status"]="trained"; WriteJson(path,audio); Seal();
            Assert.Throws<AudioIntegrityException>(()=>Load());
        }
        [Test] public void HiddenAnswerMismatchIsRefusedWithoutExposingContent()
        {
            string path=Path.Combine(directory,"answers.json"); var answers=JObject.Parse(File.ReadAllText(path)); answers["messages"][0]["semantic_action"]="SCAN"; WriteJson(path,answers); Seal();
            Assert.That(Assert.Throws<AudioIntegrityException>(()=>Load()).Message,Is.EqualTo("HASH_MISMATCH"));
            Assert.That(typeof(LoadedAudioPackage).GetProperties().Any(x=>new[]{"Answers","Permutation","Schedules","Allocation"}.Contains(x.Name)),Is.False);
        }
        [Test] public void DemoNeedsExplicitEngineeringOptIn()
        { Assert.Throws<AudioIntegrityException>(()=>PackageLoader.Load(directory,Hash)); Assert.That(Load().Demo,Is.True); }
        [Test] public void CopiesCannotMutateVerifiedAudio()
        {
            var wave=Load().ReadAtom("K-a1"); string hash=wave.PcmSha256; var bytes=wave.CopyPcm16(); bytes[0]^=255;
            var samples=wave.CopySamples(); samples[0]=float.NaN; wave.Verify(); Assert.That(wave.PcmSha256,Is.EqualTo(hash)); Assert.That(float.IsNaN(wave.CopySamples()[0]),Is.False);
        }
        sealed class Permit : INovelSlotAuthorization
        {
            readonly string expected; internal int Calls;
            internal Permit(string hash) { expected=hash; }
            public bool TryConsume(string package,string message) => ++Calls==1 && package==expected && message=="K-a1-r2";
        }
        [Test] public void NovelBufferRequiresOneConsumedSlotAndNeverCreatesAFile()
        {
            var package=Load(); int before=Directory.GetFiles(directory,"*",SearchOption.AllDirectories).Length;
            Assert.Throws<NovelSlotException>(()=>package.ComposeApprovedNovel("K-a1-r2",null)); var permit=new Permit(Hash);
            var wave=package.ComposeApprovedNovel("K-a1-r2",permit); Assert.That(wave.PcmSha256,Is.EqualTo(package.CompositeHash("K-a1-r2")));
            Assert.That(wave.FileSha256,Is.Null); Assert.Throws<NovelSlotException>(()=>package.ComposeApprovedNovel("K-a1-r2",permit));
            Assert.That(permit.Calls,Is.EqualTo(2)); Assert.That(Directory.GetFiles(directory,"*",SearchOption.AllDirectories).Length,Is.EqualTo(before));
        }
        [Test] public void ProducerManifestCanonicalHashMatchesCommittedVector()
        {
            string raw=File.ReadAllText(Path.Combine(Repository,"sound","examples","package-demo","manifest.json"));
            Assert.That(PackageLoader.CanonicalPackageHash(raw),Is.EqualTo((string)JObject.Parse(raw)["package_sha256"]));
            Assert.Throws<AudioIntegrityException>(()=>PackageLoader.CanonicalPackageHash(raw.Replace("\"demo\": true","\"demo\": true, \"demo\": true")));
        }
        [Test] public void JsonExtensionsAndDuplicateKeysFailBeforeHashing()
        {
            foreach(string raw in new[]{"{\"v\":0x1}","{\"v\":01}","{\"v\":1,}","{'v':1}","{\"v\":NaN}","{\"v\":1,\"v\":1}","{\"v\":\"line\nfeed\"}"})
                Assert.Throws<AudioIntegrityException>(()=>PackageLoader.CanonicalPackageHash(raw));
        }
        [Test] public void CanonicalHeaderAndGapUseExactSampleCounts()
        {
            var package=Load(); var action=package.ReadAtom("K-a1"); var referent=package.ReadAtom("K-r1");
            byte[] message=package.ComposeTrainedMessage("K-a1-r1").CopyPcm16(); int at=action.SampleCount*2;
            Assert.That(message.Skip(at).Take(19200).All(v=>v==0),Is.True);
            Assert.That(message.Skip(at+19200).ToArray(),Is.EqualTo(referent.CopyPcm16()));
            byte[] wave=Wave(Pattern(21600,1,0)); wave[24]=0; Assert.Throws<AudioIntegrityException>(()=>PcmWave.ParseCanonical(wave));
            wave=Wave(Pattern(21600,1,0)).Concat(new byte[2]).ToArray(); Assert.Throws<AudioIntegrityException>(()=>PcmWave.ParseCanonical(wave));
        }
        [Test] public void ActualProducerArtifactChecksAAndBWhenProvisioned()
        {
            string root=Environment.GetEnvironmentVariable("AV_PACKAGE_DEMO_ROOT")??Path.Combine(Repository,".local","producer-package-demo");
            if(!Directory.Exists(root)) Assert.Ignore("Provision the verified DEMO-only #13 package-demo artifact; no participant fallback");
            foreach(string folder in new[]{"package-demo","dyad-demo"})
            {
                string path=Path.Combine(root,folder); var manifest=JObject.Parse(File.ReadAllText(Path.Combine(path,"manifest.json")));
                var package=PackageLoader.Load(path,(string)manifest["package_sha256"],true);
                Assert.That(package.CombinationsChecked,Is.EqualTo(package.Study=="A"?32:1536));
                if(package.Study=="A") foreach(var row in (JArray)JObject.Parse(File.ReadAllText(Path.Combine(path,"audio.json")))["messages"])
                    if((string)row["status"]=="trained") Assert.That(package.ComposeTrainedMessage((string)row["message_id"]).CopyPcm16(),Is.EqualTo(package.ReadTrainedMessage((string)row["message_id"]).CopyPcm16()));
            }
        }
    }
}
