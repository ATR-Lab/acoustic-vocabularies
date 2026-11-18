using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Tests.StudyAudio
{
    // Requirement trace tests for stored study audio. Pattern PCM and the public
    // DEMO package JSON only; no participant package or WAV fixture is opened.
    public sealed class RequirementTraceStudyAudioTests
    {
        static readonly int[] AtomSamples={21600,28800,36000,43200};
        string directory;

        static byte[] Wav(byte[] data,int rate=48000,short channels=1,short bits=16,short format=1,byte[] extraChunk=null)
        {
            using var stream=new MemoryStream();using var writer=new BinaryWriter(stream);
            int extra=extraChunk==null?0:extraChunk.Length;
            writer.Write(Encoding.ASCII.GetBytes("RIFF"));writer.Write(36+extra+data.Length);writer.Write(Encoding.ASCII.GetBytes("WAVEfmt "));
            writer.Write(16);writer.Write(format);writer.Write(channels);writer.Write(rate);writer.Write(rate*channels*bits/8);
            writer.Write((short)(channels*bits/8));writer.Write(bits);
            if(extraChunk!=null) writer.Write(extraChunk);
            writer.Write(Encoding.ASCII.GetBytes("data"));writer.Write(data.Length);writer.Write(data);return stream.ToArray();
        }
        static string Sha(byte[] bytes)
        { using var sha=SHA256.Create();return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-","").ToLowerInvariant(); }
        static string WriteJson(string path,JObject value) { File.WriteAllText(path,value.ToString()+"\n",new UTF8Encoding(false));return path; }

        [TearDown] public void Cleanup()
        { if(directory!=null && Directory.Exists(directory)) Directory.Delete(directory,true);directory=null; }

        // Same construction as PackageLoaderTests: the public DEMO scaffold with
        // pattern PCM written at the listed paths, then resealed.
        void BuildDemoPackage()
        {
            directory=Path.Combine(Path.GetTempPath(),"av-trace-audio-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(directory);
            string example=Path.Combine(PackageLoaderTests.Repository,"sound","examples","package-demo");
            foreach(string file in Directory.GetFiles(example,"*.json")) File.Copy(file,Path.Combine(directory,Path.GetFileName(file)));
            Directory.CreateDirectory(Path.Combine(directory,"atoms"));Directory.CreateDirectory(Path.Combine(directory,"messages"));
            var audio=JObject.Parse(File.ReadAllText(Path.Combine(directory,"audio.json")));var pcms=new Dictionary<string,byte[]>();int n=0;
            foreach(var atom in (JArray)audio["atoms"])
            {
                byte[] pcm=PackageLoaderTests.Pattern((int)atom["n_samples"],7+2*n,101*n++);pcms.Add((string)atom["atom_id"],pcm);
                byte[] wav=Wav(pcm);File.WriteAllBytes(Path.Combine(directory,(string)atom["path"]),wav);
                atom["pcm_sha256"]=PcmWave.Hash(pcm);atom["file_sha256"]=PcmWave.Hash(wav);
            }
            foreach(var item in (JArray)audio["messages"])
            {
                byte[] action=pcms[(string)item["action_atom"]],referent=pcms[(string)item["referent_atom"]];
                item["composite_sha256"]=MessageComposer.CompositeHash(PcmWave.ParseCanonical(Wav(action)),PcmWave.ParseCanonical(Wav(referent)));
                if((string)item["status"]=="trained")
                {
                    var pcm=new byte[action.Length+19200+referent.Length];Buffer.BlockCopy(action,0,pcm,0,action.Length);Buffer.BlockCopy(referent,0,pcm,action.Length+19200,referent.Length);
                    byte[] wav=Wav(pcm);File.WriteAllBytes(Path.Combine(directory,(string)item["path"]),wav);item["file_sha256"]=PcmWave.Hash(wav);
                }
            }
            WriteJson(Path.Combine(directory,"audio.json"),audio);Seal();
        }
        void Seal()
        {
            string path=Path.Combine(directory,"manifest.json");var manifest=JObject.Parse(File.ReadAllText(path));
            foreach(var file in ((JObject)manifest["files"]).Properties())
            { byte[] bytes=File.ReadAllBytes(Path.Combine(directory,file.Name));file.Value["sha256"]=PcmWave.Hash(bytes);file.Value["bytes"]=bytes.Length; }
            manifest["package_sha256"]=PackageLoader.CanonicalPackageHash(manifest.ToString());WriteJson(path,manifest);
        }
        string PackageHash => (string)JObject.Parse(File.ReadAllText(Path.Combine(directory,"manifest.json")))["package_sha256"];
        LoadedAudioPackage Load() => PackageLoader.Load(directory,PackageHash,true);
        void Edit(string file,Action<JObject> change)
        { string path=Path.Combine(directory,file);var value=JObject.Parse(File.ReadAllText(path));change(value);WriteJson(path,value); }

        [Test, Category("REQ_AUD_01"), NUnit.Framework.Property("Requirement","REQ-AUD-01")]
        public void CanonicalParserAcceptsOnlyMonoFortyEightKilohertzSixteenBitPcm()
        {
            byte[] pcm=PackageLoaderTests.Pattern(21600,3,5);
            var accepted=PcmWave.ParseCanonical(Wav(pcm));
            Assert.That(PcmWave.SampleRate,Is.EqualTo(48000));
            Assert.That(accepted.SampleCount,Is.EqualTo(21600));Assert.That(accepted.CopySamples().Length,Is.EqualTo(21600),"one sample per frame: mono");
            var refused=new (string name,byte[] bytes)[]{
                ("44.1 kHz",Wav(pcm,rate:44100)),("96 kHz",Wav(pcm,rate:96000)),("stereo",Wav(pcm,channels:2)),
                ("8-bit",Wav(pcm,bits:8)),("24-bit",Wav(PackageLoaderTests.Pattern(32400,3,5),bits:24)),
                ("float",Wav(pcm,bits:32,format:3)),("extensible",Wav(pcm,format:unchecked((short)0xFFFE))),
                ("metadata chunk",Wav(pcm,extraChunk:Encoding.ASCII.GetBytes("LIST").Concat(BitConverter.GetBytes(4)).Concat(Encoding.ASCII.GetBytes("INFO")).ToArray()))};
            foreach(var item in refused)
                Assert.That(Assert.Throws<AudioIntegrityException>(()=>PcmWave.ParseCanonical(item.bytes),item.name).Code,Is.EqualTo("HASH_MISMATCH"),item.name);
        }

        [Test, Category("REQ_AUD_01"), NUnit.Framework.Property("Requirement","REQ-AUD-01"), Category("REQ_APP_05"), NUnit.Framework.Property("Requirement","REQ-APP-05")]
        public void RuntimeHasOneWavDecoderAndCreatesClipsOnlyFromVerifiedStoredPcm()
        {
            string runtime=Path.Combine(PackageLoaderTests.Repository,"unity","Assets","ExperimentApp","Runtime");
            var sources=Directory.GetFiles(runtime,"*.cs",SearchOption.AllDirectories).ToDictionary(x=>x.Substring(runtime.Length+1).Replace('\\','/'),File.ReadAllText);
            Assert.That(sources.Count,Is.GreaterThan(50));
            // Simulation-only engineering hosts (file names starting "Simulation") may write
            // silent placeholder WAVs; they never decode or play study audio.
            string[] Using(string text) => sources.Where(x=>x.Value.Contains(text)&&!Path.GetFileName(x.Key).StartsWith("Simulation",StringComparison.Ordinal)).Select(x=>x.Key).OrderBy(x=>x,StringComparer.Ordinal).ToArray();
            // One canonical decoder; every package, calibration, speech, grammar
            // and menu WAV goes through PcmWave.ParseCanonical.
            Assert.That(Using("\"RIFF\""),Is.EqualTo(new[]{"StudyAudio/PcmWave.cs"}));
            // One clip factory, non-streaming, filled from verified PcmWave copies.
            Assert.That(Using("AudioClip.Create("),Is.EqualTo(new[]{"StudyAudio/AudioPlayer.cs"}));
            Assert.That(Using(".SetData("),Is.EqualTo(new[]{"StudyAudio/AudioPlayer.cs"}));
            string player=sources["StudyAudio/AudioPlayer.cs"];
            Assert.That(Regex.Matches(player,@"AudioClip\.Create\(").Count,Is.EqualTo(1));
            Assert.That(player,Does.Contain("AudioClip.Create(\"verified-audio\",entry.Value.SampleCount,1,48000,false)"));
            Assert.That(player,Does.Contain("clip.SetData(entry.Value.CopySamples(),0)"));
            // No streamed/procedural callbacks, synthesizers or sample writes in the output filter.
            foreach(string forbidden in new[]{"PCMReaderCallback","PCMSetPositionCallback","OnAudioRead","SpeechSynthesizer","System.Speech","TextToSpeech","OscillatorNode"})
                Assert.That(Using(forbidden),Is.Empty,forbidden);
            var filter=Regex.Match(player,@"void OnAudioFilterRead\(float\[\] data,int channels\)\s*\{(?<body>[^}]*)\}");
            Assert.That(filter.Success,Is.True);
            Assert.That(Regex.IsMatch(filter.Groups["body"].Value,@"data\s*\[[^\]]*\]\s*[\+\-\*/]?="),Is.False,"the output observer never writes samples");
        }

        [Test, Category("REQ_AUD_06"), NUnit.Framework.Property("Requirement","REQ-AUD-06")]
        public void CompleteMessageIsActionThenExactlyTwoHundredMillisecondsOfZerosThenTarget()
        {
            Assert.That(MessageComposer.GapSamples,Is.EqualTo(9600));
            Assert.That(MessageComposer.GapSamples*1000.0/PcmWave.SampleRate,Is.EqualTo(200.0));
            var action=PackageLoaderTests.PatternWave(21600,11,3);var target=PackageLoaderTests.PatternWave(28800,5,17);
            byte[] expected=action.CopyPcm16().Concat(new byte[9600*2]).Concat(target.CopyPcm16()).ToArray();
            Assert.That(MessageComposer.CompositeHash(action,target),Is.EqualTo(Sha(expected)));
            byte[] reversed=target.CopyPcm16().Concat(new byte[9600*2]).Concat(action.CopyPcm16()).ToArray();
            byte[] shortGap=action.CopyPcm16().Concat(new byte[9599*2]).Concat(target.CopyPcm16()).ToArray();
            byte[] longGap=action.CopyPcm16().Concat(new byte[9601*2]).Concat(target.CopyPcm16()).ToArray();
            foreach(var other in new[]{reversed,shortGap,longGap})
                Assert.That(MessageComposer.CompositeHash(action,target),Is.Not.EqualTo(Sha(other)));
        }

        [Test, Category("REQ_AUD_07"), NUnit.Framework.Property("Requirement","REQ-AUD-07")]
        public void EveryAllowedMotifPairingLastsFromElevenHundredToTwoThousandMilliseconds()
        {
            var durations=new List<double>();
            foreach(int a in AtomSamples) foreach(int r in AtomSamples)
            {
                var action=PackageLoaderTests.PatternWave(a,3,1);var target=PackageLoaderTests.PatternWave(r,5,2);
                Assert.That(MessageComposer.CompositeHash(action,target),Does.Match("^[0-9a-f]{64}$"));
                double ms=(a+MessageComposer.GapSamples+r)*1000.0/PcmWave.SampleRate;
                Assert.That(ms,Is.InRange(1100.0,2000.0));durations.Add(ms);
            }
            Assert.That(durations.Min(),Is.EqualTo(1100.0));Assert.That(durations.Max(),Is.EqualTo(2000.0));
            var ok=PackageLoaderTests.PatternWave(21600,3,1);
            foreach(int samples in new[]{21598,21602,24000,43202,48000,9600})
            {
                var odd=PackageLoaderTests.PatternWave(samples,3,1);
                Assert.Throws<AudioIntegrityException>(()=>MessageComposer.CompositeHash(odd,ok),samples+" as action");
                Assert.Throws<AudioIntegrityException>(()=>MessageComposer.CompositeHash(ok,odd),samples+" as target");
            }
        }

        [Test, Category("REQ_VOC_03"), NUnit.Framework.Property("Requirement","REQ-VOC-03")]
        public void ChangedInMemoryPcmIsRefusedBeforeAnyCopyIsHandedToPlayback()
        {
            var wave=PackageLoaderTests.PatternWave(21600,7,9);string hash=wave.PcmSha256;
            Assert.That(wave.CopySamples().Length,Is.EqualTo(21600));
            var field=typeof(PcmWave).GetField("pcm",BindingFlags.Instance|BindingFlags.NonPublic);
            Assert.That(field,Is.Not.Null);((byte[])field.GetValue(wave))[100]^=1;
            Assert.That(Assert.Throws<AudioIntegrityException>(()=>wave.Verify()).Code,Is.EqualTo("HASH_MISMATCH"));
            Assert.Throws<AudioIntegrityException>(()=>wave.CopySamples());
            Assert.Throws<AudioIntegrityException>(()=>wave.CopyPcm16());
            Assert.Throws<AudioIntegrityException>(()=>MessageComposer.CompositeHash(wave,PackageLoaderTests.PatternWave(21600,1,1)));
            Assert.That(wave.PcmSha256,Is.EqualTo(hash),"the stored hash is not recomputed from changed bytes");
        }

        sealed class CountingPermit : INovelSlotAuthorization
        { internal int Calls; public bool TryConsume(string package,string message) { Calls++;return true; } }

        [Test, Category("REQ_VOC_03"), NUnit.Framework.Property("Requirement","REQ-VOC-03")]
        public void FilesChangedAfterLoadAreRefusedOnEveryPlaybackRead()
        {
            BuildDemoPackage();var package=Load();
            Assert.That(package.ReadTrainedMessage("K-a1-r1").PcmSha256,Is.EqualTo(package.CompositeHash("K-a1-r1")),"positive control");
            string message=Path.Combine(directory,"messages","K-a1-r1.wav");byte[] original=File.ReadAllBytes(message);
            byte[] changed=(byte[])original.Clone();changed[200]^=1;File.WriteAllBytes(message,changed);
            Assert.That(Assert.Throws<AudioIntegrityException>(()=>package.ReadTrainedMessage("K-a1-r1")).Code,Is.EqualTo("HASH_MISMATCH"));
            File.WriteAllBytes(message,original);Assert.That(package.ReadTrainedMessage("K-a1-r1").PcmSha256,Is.EqualTo(package.CompositeHash("K-a1-r1")));
            // A changed atom blocks novel composition before the single-use slot is consumed.
            string atom=Path.Combine(directory,"atoms","K-r2.wav");byte[] bytes=File.ReadAllBytes(atom);bytes[300]^=1;File.WriteAllBytes(atom,bytes);
            var permit=new CountingPermit();
            Assert.Throws<AudioIntegrityException>(()=>package.ComposeApprovedNovel("K-a1-r2",permit));
            Assert.That(permit.Calls,Is.Zero);
        }

        [Test, Category("REQ_SA_07"), NUnit.Framework.Property("Requirement","REQ-SA-07")]
        public void MethodOrDesignerFieldsCannotEnterThroughAnyClosedPackageDocument()
        {
            BuildDemoPackage();Assert.That(Load().Demo,Is.True,"positive control");
            var edits=new (string file,Action<JObject> change)[]{
                ("manifest.json",x=>x["method"]="A1"),("manifest.json",x=>x["builder"]["designer"]="D1"),
                ("audio.json",x=>x["method"]="A2"),("audio.json",x=>x["atoms"][0]["designer"]="D2"),("audio.json",x=>x["messages"][0]["method"]="A3"),
                ("answers.json",x=>x["method"]="A1"),("answers.json",x=>x["atoms"][0]["rating"]=5),
                ("allocation.json",x=>x["method"]="A2"),("allocation.json",x=>x["designer"]="D3")};
            foreach(var edit in edits)
            {
                string path=Path.Combine(directory,edit.file);string before=File.ReadAllText(path);
                Edit(edit.file,edit.change);if(edit.file!="manifest.json") Seal();
                else { var m=JObject.Parse(File.ReadAllText(path));m["package_sha256"]=PackageLoader.CanonicalPackageHash(m.ToString());WriteJson(path,m); }
                Assert.Throws<AudioIntegrityException>(()=>Load(),edit.file);
                File.WriteAllText(path,before,new UTF8Encoding(false));Seal();Assert.That(Load().Demo,Is.True);
            }
            var exposed=typeof(LoadedAudioPackage).GetProperties().Select(p=>p.Name).ToArray();
            foreach(string word in new[]{"Method","Designer","Rating","Source","Candidate","Answers","Allocation"})
                Assert.That(exposed.Any(name=>name.IndexOf(word,StringComparison.OrdinalIgnoreCase)>=0),Is.False,word);
        }

        [Test, Category("REQ_AUD_05"), NUnit.Framework.Property("Requirement","REQ-AUD-05")]
        public void StoredComfortGainIsRestoredUnchangedAtEveryLaterVisitAndHistoryHoldsOnlyComfortRows()
        {
            directory=Path.Combine(Path.GetTempPath(),"av-trace-gain-"+Guid.NewGuid().ToString("N"));
            var store=new ComfortableGainStore(directory);store.ChangeForComfort("DEMO-person",new string('a',32),.25f,3,true);
            string file=Directory.GetFiles(directory).Single();byte[] stored=File.ReadAllBytes(file);
            for(int visit=0;visit<4;visit++)
            {
                Assert.That(new ComfortableGainStore(directory).Restore("DEMO-person"),Is.EqualTo(.25f),"later visit "+visit);
                Assert.That(ComfortableGainStore.RestoreVerified(stored,PcmWave.Hash(stored),"DEMO-person"),Is.EqualTo(.25f));
            }
            Assert.That(File.ReadAllBytes(file),Is.EqualTo(stored),"restoring never rewrites the stored gain");
            Assert.That(ComfortableGainStore.InitialGain,Is.EqualTo(.1f));
            // Only six-field comfort rows are accepted; a performance-derived row blocks restoration.
            var row=JObject.Parse(Encoding.UTF8.GetString(stored).TrimEnd('\n'));
            Assert.That(row.Properties().Select(p=>p.Name).OrderBy(x=>x,StringComparer.Ordinal),Is.EqualTo(new[]{"coded_id","event","mono_s","new_gain","old_gain","visit_id"}));
            Assert.That((string)row["event"],Is.EqualTo("comfort_gain_changed"));
            foreach(var tuned in new[]{
                new JObject{["event"]="accuracy_gain_changed",["coded_id"]="DEMO-person",["visit_id"]=new string('b',32),["old_gain"]=.25,["new_gain"]=.4,["mono_s"]=9.0},
                new JObject{["event"]="comfort_gain_changed",["coded_id"]="DEMO-person",["visit_id"]=new string('b',32),["old_gain"]=.25,["new_gain"]=.4,["mono_s"]=9.0,["accuracy"]=.9}})
            {
                byte[] bytes=stored.Concat(Encoding.UTF8.GetBytes(tuned.ToString(Formatting.None)+"\n")).ToArray();
                Assert.That(Assert.Throws<AudioFault>(()=>ComfortableGainStore.RestoreVerified(bytes,PcmWave.Hash(bytes),"DEMO-person")).Code,Is.EqualTo("GAIN_HISTORY_INVALID"));
            }
            var writers=typeof(ComfortableGainStore).GetMethods(BindingFlags.Public|BindingFlags.Instance|BindingFlags.Static|BindingFlags.DeclaredOnly).Select(m=>m.Name).OrderBy(x=>x,StringComparer.Ordinal).ToArray();
            // Writers may only record a comfort change or carry an already recorded gain forward
            // (screening-to-slot carry, #270); nothing derives a gain from performance.
            Assert.That(writers,Does.Contain("ChangeForComfort"));
            Assert.That(writers,Is.SubsetOf(new[]{"CarryForward","ChangeForComfort","Restore","RestoreOrCarry","RestoreVerified"}),"only comfort changes and carries write a gain");
        }
    }
}
