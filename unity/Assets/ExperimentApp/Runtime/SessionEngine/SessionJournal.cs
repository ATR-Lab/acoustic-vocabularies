using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.IO;
using System.Linq;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using AcousticVocab.StudyAudio;

namespace AcousticVocab.SessionEngine
{
    // Minimal recovery journal for #67, not the #72 template/export/upload
    // implementation. Never updates, truncates or deletes an existing segment.
    public sealed class SessionJournal : ISessionJournal,IDisposable
    {
        readonly List<SessionRecord> records=new List<SessionRecord>();
        readonly FileStream output;
        string previous=new string('0',64);
        long sequence;
        bool failed;
        public IReadOnlyList<SessionRecord> Records => records.AsReadOnly();
        public string IgnoredFinalTailSha256 { get; private set; }
        public SessionJournal(string privateDirectory)
        {
            Directory.CreateDirectory(privateDirectory);
            SessionJson.Require((File.GetAttributes(privateDirectory)&FileAttributes.ReparsePoint)==0,"SESSION_JOURNAL_LINK");
            var files=Directory.GetFiles(privateDirectory,"segment-*.local.jsonl").OrderBy(x=>x,StringComparer.Ordinal).ToArray();
            SessionJson.Require(files.Length<1000,"SESSION_JOURNAL_LIMIT");
            for(int i=0;i<files.Length;i++)
            {
                SessionJson.Require(Path.GetFileName(files[i])=="segment-"+i.ToString("D4")+".local.jsonl" &&
                    (File.GetAttributes(files[i])&FileAttributes.ReparsePoint)==0,"SESSION_JOURNAL_SEGMENT");
                var info=new FileInfo(files[i]);SessionJson.Require(info.Length<=16*1024*1024,"SESSION_JOURNAL_LIMIT");
                byte[] bytes=File.ReadAllBytes(files[i]);int from=0;
                for(int at=0;at<bytes.Length;at++)if(bytes[at]==10)
                {
                    int length=at-from;SessionJson.Require(length>0&&length<=16384,"SESSION_JOURNAL_LINE");
                    var line=new byte[length];Buffer.BlockCopy(bytes,from,line,0,length);Read(line);from=at+1;
                }
                if(from<bytes.Length)
                {
                    // Preserve a final torn write byte-for-byte. It can only be
                    // the newest segment; earlier complete records remain the
                    // durable state. The new segment records the tail hash.
                    SessionJson.Require(i==files.Length-1,"SESSION_JOURNAL_TORN_HISTORY");
                    var tail=new byte[bytes.Length-from];Buffer.BlockCopy(bytes,from,tail,0,tail.Length);IgnoredFinalTailSha256=PcmWave.Hash(tail);
                }
            }
            output=new FileStream(Path.Combine(privateDirectory,"segment-"+files.Length.ToString("D4")+".local.jsonl"),FileMode.CreateNew,FileAccess.Write,FileShare.Read,4096,FileOptions.WriteThrough);
        }
        void Read(byte[] line)
        {
            var value=SessionJson.Parse(line,16384);SessionJson.Keys(value,"version","sequence","previous_sha256","record","sha256");
            SessionJson.Require((int?)value["version"]==1&&(long?)value["sequence"]==sequence&&(string)value["previous_sha256"]==previous,"SESSION_JOURNAL_CHAIN");
            string hash=(string)value["sha256"];value.Remove("sha256");
            SessionJson.Require(SessionJson.Hash(hash)&&PcmWave.Hash(SessionJson.Bytes(value))==hash,"SESSION_JOURNAL_HASH");
            var record=ParseRecord((JObject)value["record"]);records.Add(record);previous=hash;sequence++;
        }
        public void Append(SessionRecord record)
        {
            if(failed||record==null)throw new SessionFault("SESSION_JOURNAL_UNAVAILABLE");
            try
            {
                SessionJson.Require(output.Length<16*1024*1024,"SESSION_JOURNAL_LIMIT");
                var row=new JObject { ["version"]=1,["sequence"]=sequence,["previous_sha256"]=previous,["record"]=Serialize(record) };
                string hash=PcmWave.Hash(SessionJson.Bytes(row));row["sha256"]=hash;
                byte[] bytes=SessionJson.Bytes(row);output.Write(bytes,0,bytes.Length);output.Flush(true);
                records.Add(record);sequence++;previous=hash;
            }
            catch {failed=true;throw new SessionFault("SESSION_JOURNAL_WRITE_FAILED");}
        }
        internal static JObject Serialize(SessionRecord r)=>new JObject { ["event"]=r.Event,["clock_epoch"]=r.ClockEpoch,["schedule_sha256"]=r.ScheduleSha256,
            ["trial_id"]=r.TrialId,["retry_of"]=r.RetryOf,["block_index"]=r.BlockIndex,["item_index"]=r.ItemIndex,["host_mono_ms"]=r.MonoMs,
            ["scheduled_onset_mono_ms"]=r.ScheduledOnsetMonoMs.HasValue?new JValue(r.ScheduledOnsetMonoMs.Value):JValue.CreateNull(),
            ["state"]=r.State?.ToString(),["audible_status"]=r.AudibleStatus.ToString(),["exposure_consumed"]=r.ExposureConsumed,["reset_ok"]=r.ResetOk,
            ["focus_ok"]=r.FocusOk,["technical_fault_code"]=r.TechnicalFaultCode,["response_code"]=r.ResponseCode,["evidence_sha256"]=r.EvidenceSha256 };
        internal static SessionRecord ParseRecord(JObject v)
        {
            SessionJson.Keys(v,"event","clock_epoch","schedule_sha256","trial_id","retry_of","block_index","item_index","host_mono_ms","scheduled_onset_mono_ms",
                "state","audible_status","exposure_consumed","reset_ok","focus_ok","technical_fault_code","response_code","evidence_sha256");
            SessionJson.Require(SessionJson.Hash((string)v["schedule_sha256"])&&System.Text.RegularExpressions.Regex.IsMatch((string)v["clock_epoch"]??"",@"\A[0-9a-f]{32}\z"));
            foreach(string key in new[]{"trial_id","retry_of"})SessionJson.Require(v[key].Type==JTokenType.Null||v[key].Type==JTokenType.String&&System.Text.RegularExpressions.Regex.IsMatch((string)v[key],@"\A[A-Za-z0-9][A-Za-z0-9._-]{0,79}\z"));
            foreach(string key in new[]{"host_mono_ms","scheduled_onset_mono_ms"})SessionJson.Require(key=="scheduled_onset_mono_ms"&&v[key].Type==JTokenType.Null ||
                (v[key].Type==JTokenType.Float||v[key].Type==JTokenType.Integer)&&(double)v[key]>=0&&!double.IsInfinity((double)v[key]));
            foreach(string key in new[]{"exposure_consumed","reset_ok","focus_ok"})SessionJson.Require(v[key].Type==JTokenType.Boolean);
            foreach(string key in new[]{"block_index","item_index"})SessionJson.Require(v[key].Type==JTokenType.Integer&&(int)v[key]>=0);
            SessionJson.Require(v["event"].Type==JTokenType.String&&new[]{"operator_resume","visit_complete","state_before","state_after","onset_evidence","response","pause_requested","stop_requested","item_fault","retry_queued","boundary_late","session_stopped","session_paused","novel_buffer_authorized"}.Contains((string)v["event"]));
            SessionJson.Require(v["technical_fault_code"].Type==JTokenType.Null||new SessionFault((string)v["technical_fault_code"]).Code==(string)v["technical_fault_code"]);
            SessionJson.Require(v["response_code"].Type==JTokenType.Null||new[]{"commit","dont_know","timeout"}.Contains((string)v["response_code"]));
            SessionJson.Require(v["evidence_sha256"].Type==JTokenType.Null||SessionJson.Hash((string)v["evidence_sha256"]));
            ItemState? state=v["state"].Type==JTokenType.Null?(ItemState?)null:Enum.TryParse<ItemState>((string)v["state"],out var parsed)&&Enum.IsDefined(typeof(ItemState),parsed)?parsed:throw new SessionFault("SESSION_JOURNAL_STATE");
            SessionJson.Require(Enum.TryParse<AudibleStatus>((string)v["audible_status"],out var audible)&&Enum.IsDefined(typeof(AudibleStatus),audible));
            return new SessionRecord((string)v["event"],(string)v["clock_epoch"],(string)v["schedule_sha256"],(string)v["trial_id"],(string)v["retry_of"],(int)v["block_index"],(int)v["item_index"],
                (double)v["host_mono_ms"],v["scheduled_onset_mono_ms"].Type==JTokenType.Null?null:(double?)v["scheduled_onset_mono_ms"],state,audible,(bool)v["exposure_consumed"],(bool)v["reset_ok"],(bool)v["focus_ok"],(string)v["technical_fault_code"],(string)v["response_code"],(string)v["evidence_sha256"]);
        }
        public void Dispose(){try{output.Flush(true);}finally{output.Dispose();}}
    }
}
