using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.Foundation;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.Assessment
{
    // Dedicated private stage records until the #72 typed adapter is wired.
    // The raw segment is never repaired/rewritten on a damaged restart.
    public sealed class AssessmentJournal : IAssessmentJournal,IDisposable
    {
        readonly string path,tipPath,lockPath,schedule;
        readonly List<AssessmentRecord> records=new List<AssessmentRecord>();
        FileStream ownership;
        string previous=new string('0',64);
        bool failed,closed;
        public IReadOnlyList<AssessmentRecord> Records=>records.AsReadOnly();
        public AssessmentJournal(string path,string scheduleSha256)
        {
            if(path==null||scheduleSha256==null||!System.Text.RegularExpressions.Regex.IsMatch(scheduleSha256,@"\A[0-9a-f]{64}\z"))throw new AssessmentFault("ASSESSMENT_JOURNAL_CONFIG");
            this.path=Path.GetFullPath(path);tipPath=this.path+".tip";lockPath=this.path+".lock";schedule=scheduleSha256;
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(this.path));ownership=new FileStream(lockPath,FileMode.OpenOrCreate,FileAccess.Write,FileShare.None);
                Require(!File.Exists(this.path)||new FileInfo(this.path).Length<=16*1024*1024);
                byte[] bytes=File.Exists(this.path)?File.ReadAllBytes(this.path):Array.Empty<byte>();
                Require(bytes.Length<=16*1024*1024&&(bytes.Length==0||bytes[bytes.Length-1]==10));
                string text=new UTF8Encoding(false,true).GetString(bytes);
                Require(!text.StartsWith("\n",StringComparison.Ordinal)&&!text.Contains("\n\n"));
                foreach(string line in text.Split('\n'))if(line.Length>0)
                {
                    var row=StationConfig.ParseStrict(line);Keys(row,"version","sequence","previous","record");
                    Require(row["version"].Type==JTokenType.Integer&&(int)row["version"]==1&&row["sequence"].Type==JTokenType.Integer&&(int)row["sequence"]==records.Count+1&&(string)row["previous"]==previous);
                    var record=Parse((JObject)row["record"]);Require(record.ScheduleSha256==schedule&&Row(record).ToString(Formatting.None)==line);
                    records.Add(record);previous=PcmWave.Hash(Encoding.UTF8.GetBytes(line+"\n"));
                }
                if(bytes.Length>0||File.Exists(tipPath))Require(File.Exists(tipPath)&&File.ReadAllText(tipPath)==Tip(records.Count,previous));
            }
            catch { Dispose();throw new AssessmentFault("ASSESSMENT_JOURNAL_DAMAGED"); }
        }
        static void Require(bool value){if(!value)throw new AssessmentFault("ASSESSMENT_JOURNAL_DAMAGED");}
        static void Keys(JObject value,params string[] keys)=>Require(value!=null&&value.Properties().Select(x=>x.Name).OrderBy(x=>x).SequenceEqual(keys.OrderBy(x=>x)));
        static string Text(JToken value,bool nullable=false)
        {Require(value!=null&&(value.Type==JTokenType.String||nullable&&value.Type==JTokenType.Null));return(string)value;}
        static AssessmentRecord Parse(JObject r)
        {
            Keys(r,"event_kind","schedule_sha256","host_mono_ms","clock_epoch","stage","item_id","value","outcome_code");
            Require(r["host_mono_ms"].Type is JTokenType.Integer or JTokenType.Float);Require(r["value"].Type is JTokenType.Integer or JTokenType.Null);
            return new AssessmentRecord(Text(r["event_kind"]),Text(r["schedule_sha256"]),(double)r["host_mono_ms"],Text(r["stage"]),Text(r["item_id"],true),(int?)r["value"],Text(r["outcome_code"],true),Text(r["clock_epoch"]));
        }
        JObject Row(AssessmentRecord r)=>new JObject{["version"]=1,["sequence"]=records.Count+1,["previous"]=previous,["record"]=new JObject{
            ["event_kind"]=r.EventKind,["schedule_sha256"]=r.ScheduleSha256,["host_mono_ms"]=r.HostMonoMs,["clock_epoch"]=r.ClockEpoch,["stage"]=r.Stage,["item_id"]=r.ItemId,["value"]=r.Value,["outcome_code"]=r.OutcomeCode}};
        static string Tip(int sequence,string hash)=>new JObject{["sequence"]=sequence,["sha256"]=hash}.ToString(Formatting.None);
        public void Append(AssessmentRecord record)
        {
            if(failed||closed||record==null||record.ScheduleSha256!=schedule)throw new AssessmentFault("ASSESSMENT_JOURNAL_UNAVAILABLE");
            byte[] bytes=Encoding.UTF8.GetBytes(Row(record).ToString(Formatting.None)+"\n");string hash=PcmWave.Hash(bytes);
            try
            {
                using(var stream=new FileStream(path,FileMode.Append,FileAccess.Write,FileShare.Read)){stream.Write(bytes,0,bytes.Length);stream.Flush(true);}
                string temp=tipPath+".tmp";
                using(var stream=new FileStream(temp,FileMode.Create,FileAccess.Write,FileShare.None))
                {byte[] tip=Encoding.UTF8.GetBytes(Tip(records.Count+1,hash));stream.Write(tip,0,tip.Length);stream.Flush(true);}
                if(File.Exists(tipPath))File.Replace(temp,tipPath,null);else File.Move(temp,tipPath);
                records.Add(record);previous=hash;
            }
            catch {failed=true;throw new AssessmentFault("ASSESSMENT_JOURNAL_FAILED");}
        }
        public void Dispose()
        {
            if(closed)return;closed=true;
            if(ownership!=null){ownership.Dispose();ownership=null;}
        }
    }
}
