using System;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Tests
{
    public sealed class SpeechBankTests
    {
        string directory,manifestHash,reviewHash,listHash;
        JObject manifest;
        static JToken Sorted(JToken value) => value is JObject o?new JObject(o.Properties().OrderBy(p=>p.Name,StringComparer.Ordinal).Select(p=>new JProperty(p.Name,Sorted(p.Value)))):value is JArray a?new JArray(a.Select(Sorted)):value.DeepClone();
        static byte[] Bytes(JToken value)=>Encoding.UTF8.GetBytes(Sorted(value).ToString(Newtonsoft.Json.Formatting.None)+"\n");
        void Write(string file,byte[] data)=>File.WriteAllBytes(Path.Combine(directory,file),data);
        byte[] Wav()
        {
            using var stream=new MemoryStream();using var writer=new BinaryWriter(stream);
            int samples=4800;writer.Write(Encoding.ASCII.GetBytes("RIFF"));writer.Write(36+2*samples);writer.Write(Encoding.ASCII.GetBytes("WAVEfmt "));
            writer.Write(16);writer.Write((short)1);writer.Write((short)1);writer.Write(48000);writer.Write(96000);writer.Write((short)2);writer.Write((short)16);writer.Write(Encoding.ASCII.GetBytes("data"));writer.Write(samples*2);
            for(int i=0;i<samples;i++)writer.Write((short)(i==100?23170:0));writer.Flush();return stream.ToArray();
        }
        void Make(bool reviewed,bool correct=true,bool demo=false)
        {
            directory=Path.Combine(Path.GetTempPath(),"speech-unit-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(directory);
            var items=new JArray();var chosen=new JArray();byte[] wav=Wav();string hash=PcmWave.Hash(wav),pcm=PcmWave.Hash(wav.Skip(44).ToArray());
            string[] actions={"ADD_ONE","REMOVE_ONE","FLIP_CARD","ALIGN_ARROW","SCAN","TAG","CLOSE","QUARANTINE"};
            var selection=new JObject{["format"]="av-schedules/speech-list",["format_version"]=1,["demo"]=demo,["study"]="A",["set"]="confirmatory",
                ["commands"]=new JArray(actions.Select((a,i)=>new JObject{["position"]=i+1,["family"]=i<4?"K":"Q",["speech_id"]=(i<4?"K":"Q")+"-"+a+"-"+(char)('A'+i),["semantic_action"]=a,["semantic_referent"]=((char)('A'+i)).ToString()}))};
            byte[] selectionBytes=Bytes(selection);listHash=PcmWave.Hash(selectionBytes);Write("selection.local.json",selectionBytes);
            for(int ai=0;ai<8;ai++)for(int ti=ai/4*4;ti<ai/4*4+4;ti++)for(int take=1;take<=2;take++)
            {
                string target=((char)('A'+ti)).ToString(),id="speech-"+actions[ai].ToLowerInvariant()+"-"+target.ToLowerInvariant()+"-t"+take;
                bool selected=ai==ti&&take==1;Write(id+".wav",wav);if(selected)chosen.Add(id);
                items.Add(new JObject{["speech_id"]=id,["action"]=actions[ai],["target"]=target,["text"]=actions[ai].Replace('_',' ')+", "+target+".",["take"]=take,["chosen"]=selected,
                    ["duration_ms"]=100d,["samples"]=4800,["sha256"]=hash,["pcm_sha256"]=pcm,["raw_sha256"]=hash,
                    ["trim"]=new JObject{["first_kept_sample"]=0,["end_exclusive_sample"]=4800,["raw_samples"]=4800,["raw_peak_pcm"]=23170}});
            }
            manifest=new JObject{["version"]=1,["status"]="engineering_unreviewed",["listening_review_sha256"]=JValue.CreateNull(),["demo"]=demo,["study"]="A",["set"]="confirmatory",
                ["voice"]=JObject.Parse("{\"name\":\"Microsoft Zira Desktop\",\"id\":\"TTS_MS_EN-US_ZIRA_11.0\",\"culture\":\"en-US\",\"version\":\"11.0\",\"rate\":0,\"volume\":100}"),
                ["rule"]=JObject.Parse("{\"version\":1,\"sample_rate_hz\":48000,\"channels\":1,\"bits\":16,\"trim_threshold_pcm\":33,\"edge_padding_samples\":480,\"peak_pcm\":23170,\"rounding\":\"nearest_ties_away_from_zero\"}"),
                ["source"]=new JObject{["requests_sha256"]=new string('a',64),["synthesis_evidence_sha256"]=new string('b',64),["speech_list_sha256"]=listHash},["items"]=items,["balanced_list"]=chosen};
            if(reviewed)
            {
                var review=new JObject{["version"]=1,["manifest_sha256"]=PcmWave.Hash(Bytes(manifest)),["reviewer_code"]="SYNTHETIC_TEST",["reviewed_utc"]="2026-01-01T00:00:00Z",
                    ["items"]=new JArray(items.Select(i=>new JObject{["speech_id"]=(string)i["speech_id"],["sha256"]=(string)i["sha256"],["wording_correct"]=correct,["acceptable_clarity"]=true}))};
                byte[] reviewBytes=Bytes(review);reviewHash=PcmWave.Hash(reviewBytes);Write("listening-review.local.json",reviewBytes);
                manifest["status"]="reviewed_frozen";manifest["listening_review_sha256"]=reviewHash;
            }
            else{Write("manifest.local.csv",Array.Empty<byte>());Write("listening-review.template.local.json",Array.Empty<byte>());}
            SaveManifest();
        }
        void SaveManifest(){byte[] bytes=Bytes(manifest);manifestHash=PcmWave.Hash(bytes);Write("manifest.local.json",bytes);Write("manifest.sha256",Encoding.UTF8.GetBytes(manifestHash+"\n"));}
        [TearDown] public void Cleanup()
        {
            if(directory==null)return;
            foreach(string path in Directory.GetFiles(directory))File.Delete(path);
            Directory.Delete(directory);directory=null;
        }
        sealed class Once:ISpeechSlotAuthorization
        {
            public int Calls;
            public bool Consume(string id,string hash,string selectionHash)=>++Calls==1;
        }
        [Test] public void UnreviewedBankIsInspectableButCannotProvidePlayback()
        {
            Make(false);var bank=SpeechBank.InspectEngineering(directory,manifestHash);Assert.That(bank.VerifiedFiles,Is.EqualTo(64));Assert.That(bank.BalancedIds.Count,Is.EqualTo(8));
            var permit=new Once();Assert.Throws<AudioFault>(()=>bank.ReadForValidity(bank.BalancedIds[0],permit));Assert.That(permit.Calls,Is.Zero);
            Assert.Throws<AudioIntegrityException>(()=>SpeechBank.LoadReviewed(directory,manifestHash,new string('0',64),listHash));
        }
        [Test] public void ReviewedBankRequiresOneUseExactSlotAuthorization()
        {
            Make(true);var bank=SpeechBank.LoadReviewed(directory,manifestHash,reviewHash,listHash);var permit=new Once();
            Assert.Throws<AudioFault>(()=>bank.ReadForValidity(bank.BalancedIds[0],null));
            Assert.That(bank.ReadForValidity(bank.BalancedIds[0],permit).SampleCount,Is.EqualTo(4800));
            Assert.Throws<AudioFault>(()=>bank.ReadForValidity(bank.BalancedIds[0],permit));
            Assert.Throws<AudioFault>(()=>bank.ReadForValidity("speech-add_one-b-t1",new Once()));
        }
        [Test] public void UnpassedListeningReviewCannotFreezeReadiness()
        {Make(true,false);Assert.Throws<AudioIntegrityException>(()=>SpeechBank.LoadReviewed(directory,manifestHash,reviewHash,listHash));}
        [Test] public void DemoBankAndMismatchedScheduleCannotProvideStudyPlayback()
        {
            Make(true,true,true);Assert.Throws<AudioIntegrityException>(()=>SpeechBank.LoadReviewed(directory,manifestHash,reviewHash,new string('0',64)));
            var bank=SpeechBank.LoadReviewed(directory,manifestHash,reviewHash,listHash);Assert.That(bank.Demo,Is.True);
            Assert.Throws<AudioFault>(()=>bank.ReadForValidity(bank.BalancedIds[0],new Once()));
        }
        [Test] public void ChangedWaveRejectedBeforeConsumingSlot()
        {
            Make(true);var bank=SpeechBank.LoadReviewed(directory,manifestHash,reviewHash,listHash);var permit=new Once();byte[] wav=Wav();wav[100]^=1;Write("speech-add_one-a-t1.wav",wav);
            Assert.Throws<AudioIntegrityException>(()=>bank.ReadForValidity(bank.BalancedIds[0],permit));Assert.That(permit.Calls,Is.Zero);
        }
        [Test] public void ChangedReviewOrExtraFileRejectedAfterLoad()
        {
            Make(true);var bank=SpeechBank.LoadReviewed(directory,manifestHash,reviewHash,listHash);Write("unexpected.local.json",new byte[]{1});
            Assert.Throws<AudioIntegrityException>(()=>bank.ReadForValidity(bank.BalancedIds[0],new Once()));File.Delete(Path.Combine(directory,"unexpected.local.json"));
            Write("listening-review.local.json",new byte[]{1});Assert.Throws<AudioIntegrityException>(()=>bank.ReadForValidity(bank.BalancedIds[0],new Once()));
        }
        [TestCase("text","Extra hint")]
        [TestCase("chosen",false)]
        [TestCase("duration_ms",101d)]
        [TestCase("take",2)]
        public void MismatchedWordingBalanceOrDurationRejected(string field,object value)
        {
            Make(false);manifest["items"][0][field]=JToken.FromObject(value);SaveManifest();Assert.Throws<AudioIntegrityException>(()=>SpeechBank.InspectEngineering(directory,manifestHash));
        }
        [Test] public void ActualPrivateSyntheticSpeechBankVerifiesButStaysUnreviewed()
        {
            string path=Environment.GetEnvironmentVariable("SPEECH_BANK_EVIDENCE");if(string.IsNullOrEmpty(path))Assert.Ignore("Actual private speech bank was not supplied");
            byte[] bytes=File.ReadAllBytes(Path.Combine(path,"manifest.local.json"));var bank=SpeechBank.InspectEngineering(path,PcmWave.Hash(bytes));
            Assert.That(bank.VerifiedFiles,Is.EqualTo(64));Assert.That(bank.Reviewed,Is.False);
            Assert.Throws<AudioFault>(()=>bank.ReadForValidity(bank.BalancedIds[0],new Once()));
        }
    }
}
