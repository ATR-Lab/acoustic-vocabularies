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
    // Optional dedicated private stage journal. Joined sessions use #72's adapter.
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
                    var record=AssessmentRecordCodec.FromJson((JObject)row["record"]);Require(record.ScheduleSha256==schedule&&Row(record).ToString(Formatting.None)==line);
                    records.Add(record);previous=PcmWave.Hash(Encoding.UTF8.GetBytes(line+"\n"));
                }
                if(bytes.Length>0||File.Exists(tipPath))Require(File.Exists(tipPath)&&File.ReadAllText(tipPath)==Tip(records.Count,previous));
            }
            catch { Dispose();throw new AssessmentFault("ASSESSMENT_JOURNAL_DAMAGED"); }
        }
        static void Require(bool value){if(!value)throw new AssessmentFault("ASSESSMENT_JOURNAL_DAMAGED");}
        static void Keys(JObject value,params string[] keys)=>Require(value!=null&&value.Properties().Select(x=>x.Name).OrderBy(x=>x).SequenceEqual(keys.OrderBy(x=>x)));
        JObject Row(AssessmentRecord r)=>new JObject{["version"]=1,["sequence"]=records.Count+1,["previous"]=previous,["record"]=AssessmentRecordCodec.ToJson(r)};
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
