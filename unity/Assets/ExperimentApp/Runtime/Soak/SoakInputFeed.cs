using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using AcousticVocab.Foundation;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.Soak
{
    // Exact-byte readers for the Python soak driver's pinned schedule and its
    // hash-chained Unity input feed (isaac/soak/driver.py). Rows are verified,
    // never reformatted. Python and Newtonsoft agree on compact ASCII JSON with
    // integers; any other spelling is refused rather than reinterpreted.
    static class SoakJson
    {
        internal static readonly UTF8Encoding Utf8=new UTF8Encoding(false,true);
        internal static bool Guid(string x)=>x!=null&&Regex.IsMatch(x,@"\A[0-9a-f]{32}\z");
        internal static bool Integer(JToken x,out long value){value=0;if(x==null||x.Type!=JTokenType.Integer)return false;try{value=(long)x;return true;}catch{return false;}}
        internal static string Text(JToken x)=>x!=null&&x.Type==JTokenType.String?(string)x:null;
        internal static bool Keys(JObject value,params string[] names)=>value!=null&&value.Properties().Select(p=>p.Name).OrderBy(n=>n,StringComparer.Ordinal).SequenceEqual(names.OrderBy(n=>n,StringComparer.Ordinal));
        // Parses one complete document and requires its compact Newtonsoft
        // serialization plus "\n" to equal the original bytes.
        internal static JObject Canonical(byte[] bytes,int maxDepth,string code)
        {
            string text;JToken value;
            try
            {
                text=Utf8.GetString(bytes);
                using var reader=new JsonTextReader(new StringReader(text)){DateParseHandling=DateParseHandling.None,FloatParseHandling=FloatParseHandling.Double,MaxDepth=maxDepth};
                value=JToken.Load(reader,new JsonLoadSettings{DuplicatePropertyNameHandling=DuplicatePropertyNameHandling.Error,CommentHandling=CommentHandling.Ignore,LineInfoHandling=LineInfoHandling.Ignore});
                if(reader.Read())throw new SoakFault(code);
            }
            catch(SoakFault){throw;}catch{throw new SoakFault(code);}
            SoakPlan.Need(value is JObject&&value.ToString(Formatting.None)+"\n"==text,code);
            return (JObject)value;
        }
        internal static JObject Loose(string text,string code)
        {
            try
            {
                using var reader=new JsonTextReader(new StringReader(text)){DateParseHandling=DateParseHandling.None,FloatParseHandling=FloatParseHandling.Double,MaxDepth=16};
                var value=JToken.Load(reader,new JsonLoadSettings{DuplicatePropertyNameHandling=DuplicatePropertyNameHandling.Error,CommentHandling=CommentHandling.Ignore});
                if(reader.Read()||!(value is JObject o))throw new SoakFault(code);
                return o;
            }
            catch(SoakFault){throw;}catch{throw new SoakFault(code);}
        }
        internal static bool Sorted(JToken value)
        {
            if(value is JObject o){var names=o.Properties().Select(p=>p.Name).ToList();for(int i=1;i<names.Count;i++)if(string.CompareOrdinal(names[i-1],names[i])>=0)return false;return o.Properties().All(p=>Sorted(p.Value));}
            return !(value is JArray a)||a.All(Sorted);
        }
        internal static void NoLinks(string path,string code)
        {
            for(FileSystemInfo entry=File.Exists(path)?new FileInfo(path):(FileSystemInfo)new DirectoryInfo(path);entry!=null;entry=entry is FileInfo f?f.Directory:((DirectoryInfo)entry).Parent)
                if(entry.Exists)SoakPlan.Need((entry.Attributes&FileAttributes.ReparsePoint)==0,code);
        }
    }
    // The canonical isaac.soak.driver schedule bytes pinned by the station plan.
    // Unity indexes, but never regenerates, the schedule: the driver and the
    // normalizer independently rerun the deterministic generator and gate.
    public sealed class SoakSchedule
    {
        public const int MaximumBytes=16*1024*1024;
        public string Sha256{get;}public string StationId{get;}public int StepCount=>steps.Count;
        readonly List<JObject> steps;readonly Dictionary<string,int> blocks,items;
        static readonly Dictionary<string,string[]> Fields=new Dictionary<string,string[]>
        {
            ["block_begin"]=new string[0],["set_mode"]=new[]{"mode"},["reset"]=new[]{"trial_id"},["trial"]=new[]{"trial_id"},
            ["demo"]=new[]{"trial_id","action","target"},["dummy_response"]=new[]{"trial_id","response_code"},
            ["lock_probe"]=new[]{"trial_id","action","target"},["fault_slot"]=new[]{"trial_id","fault_type"},
        };
        SoakSchedule(string hash,string station,List<JObject> steps,Dictionary<string,int> blocks,Dictionary<string,int> items){Sha256=hash;StationId=station;this.steps=steps;this.blocks=blocks;this.items=items;}
        public static SoakSchedule Load(byte[] raw,string pin)
        {
            SoakPlan.Need(raw!=null&&raw.Length>0&&raw.Length<=MaximumBytes&&SoakPlan.HashOk(pin)&&SoakPlan.Hash(raw)==pin,"SOAK_SCHEDULE_PIN");
            var d=SoakJson.Canonical(raw,8,"SOAK_SCHEDULE_JSON");SoakPlan.Need(SoakJson.Sorted(d),"SOAK_SCHEDULE_JSON");
            SoakPlan.Need(SoakJson.Keys(d,"version","kind","generator","scope","participants","station_id","seed","seconds","block_seconds","trial_interval_s","fault_types","min_command_interval_s","max_commands_per_minute","steps"),"SOAK_SCHEDULE_FIELDS");
            SoakPlan.Need(SoakJson.Integer(d["version"],out long version)&&version==1&&SoakJson.Text(d["kind"])=="soak_synthetic_schedule"&&SoakJson.Text(d["generator"])=="isaac.soak.driver/1"&&
                SoakJson.Text(d["scope"])=="synthetic_nonstudy"&&d["participants"].Type==JTokenType.Boolean&&!(bool)d["participants"]&&SoakPlan.Id(SoakJson.Text(d["station_id"])),"SOAK_SCHEDULE_SCOPE");
            SoakPlan.Need(d["steps"] is JArray array&&array.Count>0,"SOAK_SCHEDULE_STEPS");
            var list=new List<JObject>();var blocks=new Dictionary<string,int>(StringComparer.Ordinal);var items=new Dictionary<string,int>(StringComparer.Ordinal);
            string block=null,blockId=null;int trialInBlock=0;
            foreach(var token in (JArray)d["steps"])
            {
                var s=token as JObject;string op=SoakJson.Text(s?["op"]);
                SoakPlan.Need(op!=null&&Fields.ContainsKey(op)&&SoakJson.Keys(s,new[]{"index","t_s","op","block","block_id"}.Concat(Fields[op]).ToArray()),"SOAK_SCHEDULE_STEP");
                SoakPlan.Need(SoakJson.Integer(s["index"],out long index)&&index==list.Count&&SoakJson.Integer(s["t_s"],out long t)&&t>=0,"SOAK_SCHEDULE_ORDER");
                string stepBlock=SoakJson.Text(s["block"]),stepId=SoakJson.Text(s["block_id"]);
                SoakPlan.Need((stepBlock=="teaching"||stepBlock=="protected")&&SoakPlan.Id(stepId),"SOAK_SCHEDULE_BLOCK");
                if(op=="block_begin"){SoakPlan.Need(!blocks.ContainsKey(stepId),"SOAK_SCHEDULE_BLOCK");blocks[stepId]=blocks.Count;block=stepBlock;blockId=stepId;trialInBlock=0;}
                SoakPlan.Need(stepBlock==block&&stepId==blockId,"SOAK_SCHEDULE_BLOCK");
                if(op!="block_begin"&&op!="set_mode")SoakPlan.Need(SoakPlan.Id(SoakJson.Text(s["trial_id"])),"SOAK_SCHEDULE_TRIAL");
                if(op=="trial"){string trial=(string)s["trial_id"];SoakPlan.Need(!items.ContainsKey(trial),"SOAK_SCHEDULE_TRIAL");items[trial]=trialInBlock++;}
                if(op=="set_mode")SoakPlan.Need(SoakJson.Text(s["mode"])==(block=="teaching"?"teaching":"test"),"SOAK_SCHEDULE_MODE");
                list.Add(s);
            }
            return new SoakSchedule(pin,(string)d["station_id"],list,blocks,items);
        }
        internal JObject Step(long index){SoakPlan.Need(index>=0&&index<steps.Count,"SOAK_FEED_STEP");return steps[(int)index];}
        public int BlockOrdinal(string blockId)=>blockId!=null&&blocks.TryGetValue(blockId,out int value)?value:0;
        public int TrialOrdinal(string trialId)=>trialId!=null&&items.TryGetValue(trialId,out int value)?value:0;
    }
    public sealed class SoakFeedRow
    {
        readonly JObject payload;
        public long Seq{get;}public string Kind{get;}public string Sha256{get;}public long StepIndex{get;}
        public JObject Payload=>(JObject)payload.DeepClone();
        internal JToken this[string key]=>payload[key];
        internal SoakFeedRow(long seq,string kind,JObject payload,string sha,long step){Seq=seq;Kind=kind;this.payload=(JObject)payload.DeepClone();Sha256=sha;StepIndex=step;}
    }
    // Incremental verifier for unity-inputs.jsonl. A partial final line waits
    // for its newline; truncation, a changed prefix, a broken chain, a binding
    // mismatch or a row that disagrees with the pinned schedule latches a fault.
    public sealed class SoakInputFeed
    {
        public const int MaximumLine=262144;public const long MaximumBytes=256L*1024*1024;
        static readonly string[] RowKeys={"version","schedule_sha256","station_id","kind","payload","seq","previous_sha256","sha256"};
        static readonly Dictionary<string,string[]> PayloadKeys=new Dictionary<string,string[]>
        {
            ["block_begin"]=new[]{"step_index","block","block_id"},
            ["trial"]=new[]{"step_index","block_id","trial_id","reset_request_id"},
            ["dummy_response"]=new[]{"step_index","block_id","trial_id","response_code"},
            ["fault_marker"]=new[]{"step_index","fault_id","fault_type","block_id","trial_id"},
            ["command_ack"]=new[]{"step_index","op","request_id","reply_sha256","reply_utf8","fault_id"},
        };
        readonly SoakSchedule schedule;readonly string station,path;
        readonly List<byte> pending=new List<byte>();
        string previous=new string('0',64);long seq,offset,lastStep=-1,bytes;
        public string Failure{get;private set;}
        public long Rows=>seq;
        public SoakInputFeed(SoakSchedule schedule,string stationId,string absolutePath=null)
        {
            SoakPlan.Need(schedule!=null&&stationId==schedule.StationId&&(absolutePath==null||Path.IsPathRooted(absolutePath)&&!absolutePath.StartsWith("\\\\")&&!absolutePath.StartsWith("//")),"SOAK_FEED_BINDING");
            this.schedule=schedule;station=stationId;path=absolutePath==null?null:Path.GetFullPath(absolutePath);
        }
        // Reads newly appended bytes. A missing file means the driver has not
        // started yet; it never synthesizes or skips a row.
        public IReadOnlyList<SoakFeedRow> Poll(int maximumBytes=4*1024*1024)
        {
            SoakPlan.Need(path!=null,"SOAK_FEED_BINDING");if(Failure!=null)throw new SoakFault(Failure);
            try
            {
                SoakJson.NoLinks(path,"SOAK_FEED_LINK");if(!File.Exists(path))return Array.Empty<SoakFeedRow>();
                using var stream=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.ReadWrite|FileShare.Delete);
                SoakPlan.Need(stream.Length>=offset,"SOAK_FEED_TRUNCATED");
                int count=(int)Math.Min(maximumBytes,stream.Length-offset);if(count<=0)return Array.Empty<SoakFeedRow>();
                var chunk=new byte[count];stream.Seek(offset,SeekOrigin.Begin);int read=0;
                while(read<count){int n=stream.Read(chunk,read,count-read);if(n<=0)break;read+=n;}
                offset+=read;return Accept(read==count?chunk:chunk.Take(read).ToArray());
            }
            catch(SoakFault e){Failure=Failure??e.Message;throw new SoakFault(Failure);}
            catch{Failure=Failure??"SOAK_FEED_READ_FAILED";throw new SoakFault(Failure);}
        }
        public IReadOnlyList<SoakFeedRow> Accept(byte[] chunk)
        {
            if(Failure!=null)throw new SoakFault(Failure);
            try
            {
                SoakPlan.Need(chunk!=null,"SOAK_FEED_BYTES");bytes+=chunk.Length;SoakPlan.Need(bytes<=MaximumBytes,"SOAK_FEED_LIMIT");
                var rows=new List<SoakFeedRow>();
                foreach(byte b in chunk)
                {
                    pending.Add(b);SoakPlan.Need(pending.Count<=MaximumLine,"SOAK_FEED_LINE_LIMIT");
                    if(b==10){rows.Add(Verify(pending.ToArray()));pending.Clear();}
                }
                return rows;
            }
            catch(SoakFault e){Failure=Failure??e.Message;throw new SoakFault(Failure);}
            catch{Failure=Failure??"SOAK_FEED_INVALID";throw new SoakFault(Failure);}
        }
        public bool HasPartialLine=>pending.Count>0;
        SoakFeedRow Verify(byte[] line)
        {
            var r=SoakJson.Canonical(line,8,"SOAK_FEED_JSON");
            SoakPlan.Need(SoakJson.Keys(r,RowKeys)&&r.Properties().Last().Name=="sha256","SOAK_FEED_FIELDS");
            string hash=SoakJson.Text(r["sha256"]);SoakPlan.Need(SoakPlan.HashOk(hash),"SOAK_FEED_HASH");
            // The hash covers the exact compact row before its final member.
            const int suffix=78;var body=new byte[line.Length-suffix+2];Buffer.BlockCopy(line,0,body,0,line.Length-suffix);body[body.Length-2]=(byte)'}';body[body.Length-1]=10;
            SoakPlan.Need(SoakPlan.Hash(body)==hash,"SOAK_FEED_HASH");
            bool versionOk=SoakJson.Integer(r["version"],out long version),seqOk=SoakJson.Integer(r["seq"],out long rowSeq);
            SoakPlan.Need(versionOk&&version==1&&seqOk&&rowSeq==seq&&SoakJson.Text(r["previous_sha256"])==previous,"SOAK_FEED_CHAIN");
            SoakPlan.Need(SoakJson.Text(r["schedule_sha256"])==schedule.Sha256&&SoakJson.Text(r["station_id"])==station,"SOAK_FEED_BINDING");
            string kind=SoakJson.Text(r["kind"]);var p=r["payload"] as JObject;
            SoakPlan.Need(kind!=null&&PayloadKeys.ContainsKey(kind)&&SoakJson.Keys(p,PayloadKeys[kind]),"SOAK_FEED_PAYLOAD");
            bool stepOk=SoakJson.Integer(p["step_index"],out long step);SoakPlan.Need(stepOk&&step>=lastStep,"SOAK_FEED_STEP");
            var s=schedule.Step(step);string op=(string)s["op"];
            bool same(string key)=>SoakJson.Text(p[key])!=null&&SoakJson.Text(p[key])==SoakJson.Text(s[key]);
            switch(kind)
            {
                case "block_begin":SoakPlan.Need(op=="block_begin"&&same("block")&&same("block_id"),"SOAK_FEED_SCHEDULE");break;
                case "trial":SoakPlan.Need(op=="trial"&&same("block_id")&&same("trial_id")&&SoakJson.Guid(SoakJson.Text(p["reset_request_id"])),"SOAK_FEED_SCHEDULE");break;
                case "dummy_response":SoakPlan.Need(op=="dummy_response"&&same("block_id")&&same("trial_id")&&same("response_code"),"SOAK_FEED_SCHEDULE");break;
                case "fault_marker":SoakPlan.Need(op=="fault_slot"&&same("block_id")&&same("trial_id")&&same("fault_type")&&SoakJson.Guid(SoakJson.Text(p["fault_id"])),"SOAK_FEED_SCHEDULE");break;
                default:
                    string ackOp=SoakJson.Text(p["op"]),request=SoakJson.Text(p["request_id"]),reply=SoakJson.Text(p["reply_utf8"]),fault=SoakJson.Text(p["fault_id"]);
                    SoakPlan.Need(SoakJson.Guid(request)&&SoakPlan.HashOk(SoakJson.Text(p["reply_sha256"]))&&reply!=null&&(p["fault_id"].Type==JTokenType.Null||SoakJson.Guid(fault)),"SOAK_FEED_ACK");
                    // A recovery reset is bound to its fault slot's step.
                    SoakPlan.Need(fault==null?ackOp==op&&new[]{"set_mode","reset","demo","lock_probe"}.Contains(op):ackOp=="reset"&&op=="fault_slot","SOAK_FEED_SCHEDULE");
                    SoakPlan.Need(SoakPlan.Hash(Encoding.UTF8.GetBytes(reply))==(string)p["reply_sha256"],"SOAK_FEED_REPLY_HASH");
                    var value=SoakJson.Loose(reply,"SOAK_FEED_REPLY");
                    SoakPlan.Need(SoakJson.Text(value["kind"])=="private_reply"&&SoakJson.Text(value["request_id"])==request&&value["reset_ok"]!=null&&(value["reset_ok"].Type==JTokenType.Null||value["reset_ok"].Type==JTokenType.Boolean),"SOAK_FEED_REPLY");
                    break;
            }
            lastStep=step;previous=hash;seq++;
            return new SoakFeedRow(rowSeq,kind,p,hash,step);
        }
        internal static bool? ReplyResetOk(SoakFeedRow row){var v=SoakJson.Loose((string)row["reply_utf8"],"SOAK_FEED_REPLY")["reset_ok"];return v.Type==JTokenType.Null?(bool?)null:(bool)v;}
    }
    public sealed class SoakDataReference
    {
        public string EventId{get;}public string Sha256{get;}
        public SoakDataReference(string eventId,string sha256){SoakPlan.Need(SoakJson.Guid(eventId)&&SoakPlan.HashOk(sha256),"SOAK_DATA_REFERENCE");EventId=eventId;Sha256=sha256;}
    }
    public sealed class SoakTrial
    {
        public string BlockId{get;}public int BlockIndex{get;}public string TrialId{get;}public int ItemIndex{get;}public string ResetRequestId{get;}
        internal bool Responded;
        internal SoakTrial(string blockId,int blockIndex,string trialId,int itemIndex,string reset){BlockId=blockId;BlockIndex=blockIndex;TrialId=trialId;ItemIndex=itemIndex;ResetRequestId=reset;}
    }
    // Durable data side. Each call appends one hash-chained data-journal record
    // and returns its exact event ID and hash for the native observation.
    public interface ISoakSessionRecorder
    {
        SoakDataReference TrialBegan(SoakTrial trial);
        SoakDataReference Responded(SoakTrial trial,string responseCode);
        SoakDataReference Paused(SoakTrial interrupted,string blockId,string faultType);
        SoakDataReference Resumed(string blockId);
    }
    // Consumes verified feed rows in order and logs exactly the native payloads
    // isaac/soak/normalize.py joins. Lock-probe receipts are logged only in a
    // protected, unpaused context. A fault pauses before the next trial; only an
    // explicit operator file resumes, after a successful fault-bound reset.
    public sealed class SoakFeedConsumer
    {
        public static SoakContext Waiting=>new SoakContext("paused","soak-feed-waiting","SoakFeedWaiting",null);
        readonly SoakSchedule schedule;readonly ISoakSessionRecorder recorder;readonly Action<string,JObject> observe;readonly Action<SoakContext> contextChanged;
        readonly Dictionary<string,bool> resets=new Dictionary<string,bool>(StringComparer.Ordinal);readonly HashSet<string> used=new HashSet<string>(StringComparer.Ordinal),faults=new HashSet<string>(StringComparer.Ordinal);
        string block,blockId,committed,faultType,recoveryReset,latestReset;bool latestOk;SoakTrial trial;SoakContext context=Waiting;string contextKey;long expected;
        public string ActiveFaultId{get;private set;}
        public string LastCommittedSha256=>committed;
        public string Failure{get;private set;}
        public SoakContext Context=>context;
        public int SkippedTrials{get;private set;}public int SkippedResponses{get;private set;}public int UnreceiptedLockProbes{get;private set;}
        public SoakFeedConsumer(SoakSchedule schedule,ISoakSessionRecorder recorder,Action<string,JObject> observe,Action<SoakContext> contextChanged)
        {
            SoakPlan.Need(schedule!=null&&recorder!=null&&observe!=null&&contextChanged!=null,"SOAK_FEED_CONSUMER");
            this.schedule=schedule;this.recorder=recorder;this.observe=observe;this.contextChanged=contextChanged;contextKey=Key(context);
        }
        static string Key(SoakContext c)=>c.Json().ToString(Formatting.None);
        void SetContext(SoakContext value){string key=Key(value);context=value;if(key==contextKey)return;contextKey=key;contextChanged(value);}
        SoakContext Running()=>new SoakContext(block,blockId,"SoakFeedRunning",trial?.TrialId);
        public void Accept(SoakFeedRow row)
        {
            if(Failure!=null)throw new SoakFault(Failure);
            try{SoakPlan.Need(row!=null&&row.Seq==expected,"SOAK_FEED_ORDER");expected++;Apply(row);}
            catch(SoakFault e){Failure=e.Message;throw;}
            catch{Failure="SOAK_FEED_CONSUMER_FAILED";throw new SoakFault(Failure);}
        }
        void Apply(SoakFeedRow row)
        {
            switch(row.Kind)
            {
                case "block_begin":
                    block=(string)row["block"];blockId=(string)row["block_id"];trial=null;if(ActiveFaultId==null)SetContext(Running());break;
                case "command_ack":Acknowledge(row);break;
                case "trial":Begin(row);break;
                case "dummy_response":Respond(row);break;
                default:Fault(row);break;
            }
        }
        JObject Receipt(SoakFeedRow row)=>new JObject{["input_seq"]=row.Seq,["request_id"]=(string)row["request_id"],["reply_sha256"]=(string)row["reply_sha256"]};
        void Acknowledge(SoakFeedRow row)
        {
            string op=(string)row["op"],id=(string)row["request_id"],fault=row["fault_id"].Type==JTokenType.Null?null:(string)row["fault_id"];
            var step=schedule.Step(row.StepIndex);
            bool reset=op=="reset"||op=="set_mode"&&(string)step["mode"]=="test";
            if(reset)
            {
                SoakPlan.Need(!resets.ContainsKey(id),"SOAK_FEED_DUPLICATE_RECEIPT");
                bool ok=SoakInputFeed.ReplyResetOk(row)==true;
                observe("reset_receipt",Receipt(row));resets[id]=ok;
                if(ActiveFaultId!=null){latestReset=id;latestOk=ok;if(fault==ActiveFaultId&&ok)recoveryReset=id;}
            }
            else if(op=="lock_probe")
            {
                // The normalizer places a probe only in a protected native
                // context; a probe acknowledged while paused stays unplaced.
                if(ActiveFaultId==null&&block=="protected")observe("lock_probe_receipt",Receipt(row));else UnreceiptedLockProbes++;
            }
        }
        void Begin(SoakFeedRow row)
        {
            string reset=(string)row["reset_request_id"],id=(string)row["trial_id"];trial=null;
            if(ActiveFaultId!=null||!resets.TryGetValue(reset,out bool ok)||!ok||used.Contains(reset)){SkippedTrials++;if(ActiveFaultId==null)SetContext(Running());return;}
            var next=new SoakTrial((string)row["block_id"],schedule.BlockOrdinal((string)row["block_id"]),id,schedule.TrialOrdinal(id),reset);
            var data=recorder.TrialBegan(next);SoakPlan.Need(data!=null,"SOAK_DATA_REFERENCE");used.Add(reset);trial=next;
            observe("durable_record",new JObject{["data_event_id"]=data.EventId,["data_sha256"]=data.Sha256,["reset_request_id"]=reset});
            SetContext(Running());
        }
        void Respond(SoakFeedRow row)
        {
            if(ActiveFaultId!=null||trial==null||trial.TrialId!=(string)row["trial_id"]||trial.Responded){SkippedResponses++;return;}
            var data=recorder.Responded(trial,(string)row["response_code"]);SoakPlan.Need(data!=null,"SOAK_DATA_REFERENCE");trial.Responded=true;committed=data.Sha256;
            observe("durable_record",new JObject{["data_event_id"]=data.EventId,["data_sha256"]=data.Sha256,["reset_request_id"]=null});
        }
        void Fault(SoakFeedRow row)
        {
            string id=(string)row["fault_id"];
            SoakPlan.Need(ActiveFaultId==null&&!faults.Contains(id),"SOAK_FEED_FAULT_OVERLAP");
            // A fault needs a retained committed record as its baseline.
            SoakPlan.Need(committed!=null,"SOAK_FEED_FAULT_WITHOUT_COMMITTED_RECORD");
            observe("fault_injection",new JObject{["input_seq"]=row.Seq,["fault_id"]=id,["last_committed_data_sha256"]=committed});
            faults.Add(id);ActiveFaultId=id;faultType=(string)row["fault_type"];recoveryReset=latestReset=null;latestOk=false;
            var interrupted=trial;trial=null;
            SetContext(new SoakContext("paused",blockId,"SoakFaultPaused",null));
            var data=recorder.Paused(interrupted,blockId,faultType);SoakPlan.Need(data!=null,"SOAK_DATA_REFERENCE");
            observe("durable_record",new JObject{["data_event_id"]=data.EventId,["data_sha256"]=data.Sha256,["reset_request_id"]=null});
        }
        // The analyzer binds a resume to the latest reset placed during the
        // fault; Unity also requires the fault's own successful recovery reset.
        public string ResumeBlocker=>ActiveFaultId==null?"SOAK_RESUME_NO_ACTIVE_FAULT":recoveryReset==null?"SOAK_RESUME_WAITING_RECOVERY_RESET":!latestOk?"SOAK_RESUME_LATEST_RESET_FAILED":null;
        public void OperatorResume(JObject request)
        {
            if(Failure!=null)throw new SoakFault(Failure);
            SoakPlan.Need(request!=null&&SoakJson.Keys(request,"version","fault_id","recovery_reset_request_id","operator_initiated")&&SoakJson.Integer(request["version"],out long v)&&v==1&&
                request["operator_initiated"].Type==JTokenType.Boolean&&(bool)request["operator_initiated"],"SOAK_RESUME_FILE_INVALID");
            SoakPlan.Need(ResumeBlocker==null,ResumeBlocker??"SOAK_RESUME_REFUSED");
            SoakPlan.Need(SoakJson.Text(request["fault_id"])==ActiveFaultId&&SoakJson.Text(request["recovery_reset_request_id"])==recoveryReset,"SOAK_RESUME_BINDING");
            try
            {
                var data=recorder.Resumed(blockId);SoakPlan.Need(data!=null,"SOAK_DATA_REFERENCE");
                observe("operator_resume",new JObject{["fault_id"]=ActiveFaultId,["data_event_id"]=data.EventId,["data_sha256"]=data.Sha256,["reset_request_id"]=latestReset,["last_committed_data_sha256"]=committed});
                ActiveFaultId=null;faultType=recoveryReset=latestReset=null;SetContext(Running());
            }
            catch(SoakFault e){Failure=e.Message;throw;}
            catch{Failure="SOAK_FEED_CONSUMER_FAILED";throw new SoakFault(Failure);}
        }
        // Reads <capture>/operator-resume-<fault_id>.json only while paused. A
        // malformed or premature file is refused and reported, never repaired.
        public string PollOperator(string captureDirectory)
        {
            if(Failure!=null)throw new SoakFault(Failure);
            if(ActiveFaultId==null||captureDirectory==null)return null;
            string file=Path.Combine(captureDirectory,"operator-resume-"+ActiveFaultId+".json");
            if(!File.Exists(file))return ResumeBlocker??"SOAK_RESUME_READY";
            JObject request;
            try
            {
                SoakJson.NoLinks(file,"SOAK_RESUME_FILE_INVALID");var info=new FileInfo(file);SoakPlan.Need(info.Length>0&&info.Length<=4096,"SOAK_RESUME_FILE_INVALID");
                request=StationConfig.ParseStrict(SoakJson.Utf8.GetString(File.ReadAllBytes(file)));
            }
            catch{return "SOAK_RESUME_FILE_INVALID";}
            if(ResumeBlocker!=null)return ResumeBlocker;
            try{OperatorResume(request);return "SOAK_RESUMED";}
            catch(SoakFault e)when(Failure==null){return e.Message;}
        }
    }
}
