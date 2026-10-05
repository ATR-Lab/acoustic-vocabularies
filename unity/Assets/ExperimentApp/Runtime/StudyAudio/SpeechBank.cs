using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.StudyAudio
{
    // #69 must issue this only after the delayed protected battery and consume
    // it once for the exact validity slot. The bank never manufactures a grant.
    public interface ISpeechSlotAuthorization { bool Consume(string speechId,string manifestSha256,string speechListSha256); }

    public sealed class SpeechBank
    {
        static readonly string[] Actions={"ADD_ONE","REMOVE_ONE","FLIP_CARD","ALIGN_ARROW","SCAN","TAG","CLOSE","QUARANTINE"};
        static readonly string[] ItemFields={"speech_id","action","target","text","take","duration_ms","samples","sha256","pcm_sha256","chosen","raw_sha256","trim"};
        readonly string directory;
        readonly string expectedReviewSha256;
        readonly Dictionary<string,JObject> entries=new Dictionary<string,JObject>(StringComparer.Ordinal);
        readonly List<string> chosen=new List<string>();
        readonly Dictionary<string,string> scheduleIds=new Dictionary<string,string>(StringComparer.Ordinal);
        public string ManifestSha256 { get; }
        public string SpeechListSha256 { get; }
        public string Study { get; }
        public string Set { get; }
        public bool Demo { get; }
        public bool Reviewed { get; }
        public IReadOnlyList<string> BalancedIds => Array.AsReadOnly(chosen.Select(id=>scheduleIds.First(p=>p.Value==id).Key).ToArray());
        public int VerifiedFiles => entries.Count;
        public static SpeechBank InspectEngineering(string directory,string manifestSha256) => new SpeechBank(directory,manifestSha256,null,null,true);
        public static SpeechBank LoadReviewed(string directory,string manifestSha256,string reviewSha256,string speechListSha256) => new SpeechBank(directory,manifestSha256,reviewSha256,speechListSha256,false);

        SpeechBank(string root,string manifestHash,string reviewHash,string speechListHash,bool engineering)
        {
            try
            {
                PackageRules.Require(PackageRules.IsHash(manifestHash));
                directory=Path.GetFullPath(root);ManifestSha256=manifestHash;expectedReviewSha256=reviewHash;
                byte[] raw=PackageRules.Read(directory,"manifest.local.json",512*1024);
                PackageRules.Require(PcmWave.Hash(raw)==manifestHash);var value=PackageRules.Json(raw);
                PackageRules.Keys(value,"version","status","voice","rule","source","items","balanced_list","listening_review_sha256","demo","study","set");
                Demo=PackageRules.Boolean(value["demo"]);Study=PackageRules.String(value["study"]);Set=PackageRules.String(value["set"]);
                PackageRules.Require((Study=="A"||Study=="B")&&(Set=="pilot"||Set=="confirmatory"));
                PackageRules.Require(PackageRules.Integer(value["version"])==1);
                string status=PackageRules.String(value["status"]);
                Reviewed=status=="reviewed_frozen";
                PackageRules.Require(engineering?status=="engineering_unreviewed"&&!Reviewed:Reviewed);
                PackageRules.Require(JToken.DeepEquals(value["voice"],JObject.Parse("{\"name\":\"Microsoft Zira Desktop\",\"id\":\"TTS_MS_EN-US_ZIRA_11.0\",\"culture\":\"en-US\",\"version\":\"11.0\",\"rate\":0,\"volume\":100}")));
                PackageRules.Require(JToken.DeepEquals(value["rule"],JObject.Parse("{\"version\":1,\"sample_rate_hz\":48000,\"channels\":1,\"bits\":16,\"trim_threshold_pcm\":33,\"edge_padding_samples\":480,\"peak_pcm\":23170,\"rounding\":\"nearest_ties_away_from_zero\"}")));
                PackageRules.Keys(value["source"],"requests_sha256","synthesis_evidence_sha256","speech_list_sha256");
                foreach(var p in ((JObject)value["source"]).Properties())PackageRules.Require(PackageRules.IsHash(PackageRules.String(p.Value)));
                SpeechListSha256=PackageRules.String(value["source"]["speech_list_sha256"]);
                if(!engineering)PackageRules.Require(SpeechListSha256==speechListHash);
                foreach(var token in PackageRules.Array(value["items"],64))
                {
                    PackageRules.Keys(token,ItemFields);var item=(JObject)token;
                    string action=PackageRules.String(item["action"]),target=PackageRules.String(item["target"]);
                    int ai=Array.IndexOf(Actions,action);long take=PackageRules.Integer(item["take"]);
                    PackageRules.Require(ai>=0&&target.Length==1&&target[0]>='A'&&target[0]<='H'&&(target[0]-'A')/4==ai/4&&(take==1||take==2));
                    string id="speech-"+action.ToLowerInvariant()+"-"+target.ToLowerInvariant()+"-t"+take;
                    PackageRules.Require(PackageRules.String(item["speech_id"])==id&&!entries.ContainsKey(id));
                    PackageRules.Require(PackageRules.String(item["text"])==action.Replace('_',' ')+", "+target+".");
                    foreach(string field in new[]{"sha256","pcm_sha256","raw_sha256"})PackageRules.Require(PackageRules.IsHash(PackageRules.String(item[field])));
                    bool selected=PackageRules.Boolean(item["chosen"]);PackageRules.Require(!selected||take==1);
                    PackageRules.Keys(item["trim"],"first_kept_sample","end_exclusive_sample","raw_samples","raw_peak_pcm");
                    long first=PackageRules.Integer(item["trim"]["first_kept_sample"]),end=PackageRules.Integer(item["trim"]["end_exclusive_sample"]),count=PackageRules.Integer(item["trim"]["raw_samples"]),peak=PackageRules.Integer(item["trim"]["raw_peak_pcm"]);
                    PackageRules.Require(first>=0&&end>first&&count>=end&&count<=480000&&peak>33&&peak<=32768&&end-first==PackageRules.Integer(item["samples"]));
                    entries.Add(id,(JObject)item.DeepClone());if(selected)chosen.Add(id);
                    ReadChecked(id);
                }
                PackageRules.Require(chosen.Count==8&&PackageRules.Array(value["balanced_list"],8).Select(PackageRules.String).SequenceEqual(chosen));
                byte[] selectionBytes=PackageRules.Read(directory,"selection.local.json",128*1024);
                PackageRules.Require(PcmWave.Hash(selectionBytes)==SpeechListSha256);var selection=PackageRules.Json(selectionBytes);
                PackageRules.Require((string)selection["format"]=="av-schedules/speech-list"&&PackageRules.Integer(selection["format_version"])==1&&PackageRules.Boolean(selection["demo"])==Demo&&(string)selection["study"]==Study&&(string)selection["set"]==Set);
                var actionsSeen=new HashSet<string>();var targetsSeen=new HashSet<string>();int position=0;
                foreach(var command in PackageRules.Array(selection["commands"],8))
                {
                    PackageRules.Keys(command,"position","speech_id","family","semantic_action","semantic_referent");
                    string action=PackageRules.String(command["semantic_action"]),target=PackageRules.String(command["semantic_referent"]);int ai=Array.IndexOf(Actions,action);
                    PackageRules.Require(ai>=0&&target.Length==1&&target[0]>='A'&&target[0]<='H'&&(target[0]-'A')/4==ai/4&&actionsSeen.Add(action)&&targetsSeen.Add(target));
                    string family=ai<4?"K":"Q",id="speech-"+action.ToLowerInvariant()+"-"+target.ToLowerInvariant()+"-t1",scheduleId=family+"-"+action+"-"+target;
                    PackageRules.Require(PackageRules.Integer(command["position"])==++position&&(string)command["family"]==family&&(string)command["speech_id"]==scheduleId&&chosen.Contains(id));
                    scheduleIds.Add(scheduleId,id);
                }
                if(engineering)PackageRules.Require(value["listening_review_sha256"].Type==JTokenType.Null);
                else
                {
                    PackageRules.Require(PackageRules.IsHash(reviewHash)&&PackageRules.String(value["listening_review_sha256"])==reviewHash);
                    byte[] reviewBytes=PackageRules.Read(directory,"listening-review.local.json",128*1024);
                    PackageRules.Require(PcmWave.Hash(reviewBytes)==reviewHash);var review=PackageRules.Json(reviewBytes);
                    PackageRules.Keys(review,"version","manifest_sha256","reviewer_code","reviewed_utc","items");
                    string original=Encoding.UTF8.GetString(raw).Replace("\"status\":\"reviewed_frozen\"","\"status\":\"engineering_unreviewed\"").Replace("\"listening_review_sha256\":\""+reviewHash+"\"","\"listening_review_sha256\":null");
                    PackageRules.Require(PackageRules.Integer(review["version"])==1&&PcmWave.Hash(Encoding.UTF8.GetBytes(original))==PackageRules.String(review["manifest_sha256"]));
                    PackageRules.Require(System.Text.RegularExpressions.Regex.IsMatch(PackageRules.String(review["reviewer_code"]),@"\A[A-Za-z0-9][A-Za-z0-9_-]{0,31}\z"));
                    PackageRules.Require(DateTimeOffset.TryParseExact(PackageRules.String(review["reviewed_utc"]),"yyyy-MM-dd'T'HH:mm:ss'Z'",CultureInfo.InvariantCulture,DateTimeStyles.AssumeUniversal,out var when)&&when<=DateTimeOffset.UtcNow);
                    var reviewed=new HashSet<string>();
                    foreach(var item in PackageRules.Array(review["items"],64))
                    {
                        PackageRules.Keys(item,"speech_id","sha256","wording_correct","acceptable_clarity");string id=PackageRules.String(item["speech_id"]);
                        PackageRules.Require(entries.ContainsKey(id)&&reviewed.Add(id)&&PackageRules.String(item["sha256"])==(string)entries[id]["sha256"]&&PackageRules.Boolean(item["wording_correct"])&&PackageRules.Boolean(item["acceptable_clarity"]));
                    }
                }
                CheckDirectory();
            }
            catch(AudioIntegrityException){throw;}
            catch(Exception){throw new AudioIntegrityException();}
        }
        void CheckDirectory()
        {
            PackageRules.Require(PcmWave.Hash(PackageRules.Read(directory,"manifest.local.json",512*1024))==ManifestSha256);
            if(Reviewed)PackageRules.Require(PcmWave.Hash(PackageRules.Read(directory,"listening-review.local.json",128*1024))==expectedReviewSha256);
            PackageRules.Require(PcmWave.Hash(PackageRules.Read(directory,"selection.local.json",128*1024))==SpeechListSha256);
            var files=new HashSet<string>(entries.Keys.Select(x=>x+".wav"),StringComparer.Ordinal){"manifest.local.json","manifest.sha256","selection.local.json"};
            if(Reviewed)files.Add("listening-review.local.json");else{files.Add("manifest.local.csv");files.Add("listening-review.template.local.json");}
            var actual=new HashSet<string>();
            foreach(var path in Directory.EnumerateFileSystemEntries(directory))
            {
                var info=new FileInfo(path);PackageRules.Require((info.Attributes&(FileAttributes.ReparsePoint|FileAttributes.Directory))==0);
                PackageRules.Require(actual.Add(info.Name));
            }
            PackageRules.Require(actual.SetEquals(files));
        }
        PcmWave ReadChecked(string id)
        {
            var item=entries[id];byte[] data=PackageRules.Read(directory,id+".wav",960044);
            PackageRules.Require(PcmWave.Hash(data)==(string)item["sha256"]);var wave=PcmWave.ParseCanonical(data);
            PackageRules.Require(wave.PcmSha256==(string)item["pcm_sha256"]&&wave.SampleCount==PackageRules.Integer(item["samples"]));
            PackageRules.Require(item["duration_ms"].Type==JTokenType.Float||item["duration_ms"].Type==JTokenType.Integer);
            double duration=(double)item["duration_ms"];PackageRules.Require(duration>0&&duration<10000&&Math.Abs(duration-wave.SampleCount*1000d/48000)<1e-9);
            byte[] pcm=wave.CopyPcm16();int peak=0;for(int i=0;i<pcm.Length;i+=2)peak=Math.Max(peak,Math.Abs((int)(short)(pcm[i]|pcm[i+1]<<8)));
            PackageRules.Require(peak==23170);return wave;
        }
        public PcmWave ReadForValidity(string speechId,ISpeechSlotAuthorization authorization)
        {
            if(!Reviewed||Demo||authorization==null||!scheduleIds.TryGetValue(speechId,out var fileId))throw new AudioFault("SPEECH_SLOT_NOT_AUTHORIZED");
            try
            {
                CheckDirectory();var wave=ReadChecked(fileId);
                if(!authorization.Consume(speechId,ManifestSha256,SpeechListSha256))throw new AudioFault("SPEECH_SLOT_NOT_AUTHORIZED");
                return wave;
            }
            catch(AudioFault){throw;}
            catch(Exception){throw new AudioIntegrityException();}
        }
    }
}
