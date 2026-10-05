using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.DataLogging
{
    public sealed class JournalSnapshot
    {
        readonly List<DataRecord> records;readonly List<string> files;readonly JArray pending;
        public IReadOnlyList<DataRecord> Records=>records.AsReadOnly();
        public IReadOnlyList<string> SegmentFiles=>files.AsReadOnly();
        public bool HasUnacknowledgedTail=>pending.Count>0;
        internal JArray PendingTails=>(JArray)pending.DeepClone();
        internal Dictionary<string,double> Clocks {get;}
        internal string Previous=>records.Count==0?new string('0',64):records.Last().Sha256;
        internal JournalSnapshot(List<DataRecord> r,List<string> f,JArray p,Dictionary<string,double> clocks){records=r;files=f;pending=p;Clocks=clocks;}
    }
    // Sole append path. Complete malformed records never become recoverable tails.
    // A fresh segment is used after every reopen; existing bytes are never changed.
    public sealed class DataJournal : IDisposable
    {
        public const long MaximumSegmentBytes=32*1024*1024;
        readonly DataIdentity identity;readonly string clockEpoch;readonly Func<double> clock;
        readonly FileStream output;readonly List<DataRecord> records;
        readonly Dictionary<string,double> clocks;
        readonly int ownerThread=System.Threading.Thread.CurrentThread.ManagedThreadId;
        string previous;long sequence;bool failed,closed;
        internal Action BeforeDurableFlush; // Fault-injection seam, inaccessible outside this assembly/tests.
        public bool Failed=>failed;public bool Closed=>closed;
        public IReadOnlyList<DataRecord> Records=>records.AsReadOnly();
        public string SegmentPath {get;}
        public DataIdentity Identity=>identity;
        public DataJournalHealth Health=>new DataJournalHealth(this);
        public DataJournal(string privateDirectory,DataIdentity identity,string clockEpoch,Func<double> monotonicMilliseconds)
        {
            this.identity=identity??throw new ArgumentNullException(nameof(identity));this.clockEpoch=clockEpoch;
            clock=monotonicMilliseconds??throw new ArgumentNullException(nameof(monotonicMilliseconds));DataJson.Require(DataJson.Guid(clockEpoch),"DATA_CLOCK_EPOCH");
            DataJson.NoLinks(privateDirectory);Directory.CreateDirectory(privateDirectory);DataJson.NoLinks(privateDirectory);
            var snapshot=Verify(privateDirectory,identity);records=snapshot.Records.ToList();clocks=snapshot.Clocks;previous=snapshot.Previous;sequence=records.Count;
            DataJson.Require(snapshot.SegmentFiles.Count<1000,"DATA_SEGMENT_LIMIT");
            SegmentPath=Path.Combine(privateDirectory,"events-"+snapshot.SegmentFiles.Count.ToString("D4")+".local.jsonl");
            output=new FileStream(SegmentPath,FileMode.CreateNew,FileAccess.Write,FileShare.Read,4096,FileOptions.WriteThrough);
            try { if(snapshot.HasUnacknowledgedTail)Append(new EventDraft("recovery",null,new JObject{["preserved_tails"]=snapshot.PendingTails})); }
            catch {output.Dispose();throw;}
        }
        public DataRecord Append(EventDraft draft)
        {
            if(failed||closed)throw new DataFault("DATA_JOURNAL_UNAVAILABLE");
            try
            {
                DataJson.Require(System.Threading.Thread.CurrentThread.ManagedThreadId==ownerThread,"DATA_WRONG_THREAD");
                DataJson.Require(draft!=null,"DATA_DRAFT");double now=clock();CheckClock(clocks,clockEpoch,now);
                var p=draft.Payload;DataEventSchema.Validate(draft.Kind,draft.Context,p);
                if(draft.Kind=="session")CheckClock(clocks,"session:"+(string)p["clock_epoch"],(double)p["host_mono_ms"]);
                if(draft.Kind=="assessment_stage")CheckClock(clocks,"assessment:"+(string)p["clock_epoch"],(double)p["host_mono_ms"]);
                var row=new JObject{["schema_version"]=DataEventSchema.Version,["sequence"]=sequence,["event_id"]=Guid.NewGuid().ToString("N"),["clock_epoch"]=clockEpoch,["host_mono_ms"]=now,["identity"]=identity.ToJson(),
                    ["event_type"]=draft.Kind,["opportunity_id"]=draft.Context.OpportunityId,["attempt_id"]=draft.Context.AttemptId,["audio_request_id"]=draft.Context.AudioRequestId,["previous_sha256"]=previous,["payload"]=p};
                string hash=DataJson.HashBytes(DataJson.Bytes(row));row["sha256"]=hash;byte[] bytes=DataJson.Bytes(row);
                DataJson.Require(bytes.Length<=DataJson.MaxLine&&output.Length+bytes.Length<=MaximumSegmentBytes,"DATA_SEGMENT_LIMIT");
                output.Write(bytes,0,bytes.Length);BeforeDurableFlush?.Invoke();output.Flush(true);
                var record=new DataRecord(row);records.Add(record);previous=hash;sequence++;return record;
            }
            catch {failed=true;throw new DataFault("DATA_APPEND_FAILED");}
        }
        static void CheckClock(Dictionary<string,double> clocks,string epoch,double time)
        {
            DataJson.Require(!double.IsNaN(time)&&!double.IsInfinity(time)&&time>=0&&(!clocks.TryGetValue(epoch,out double previous)||time>=previous),"DATA_CLOCK_REGRESSION");clocks[epoch]=time;
        }
        public static JournalSnapshot Verify(string privateDirectory,DataIdentity expectedIdentity)
        {
            DataJson.NoLinks(privateDirectory);
            var files=Directory.Exists(privateDirectory)?Directory.GetFiles(privateDirectory,"events-*.local.jsonl").OrderBy(x=>x,StringComparer.Ordinal).ToList():new List<string>();
            DataJson.Require(files.Count<=1000,"DATA_SEGMENT_LIMIT");var records=new List<DataRecord>();var clocks=new Dictionary<string,double>(StringComparer.Ordinal);
            var pending=new JArray();string previous=new string('0',64);long sequence=0,total=0;var ids=new HashSet<string>(StringComparer.Ordinal);
            for(int i=0;i<files.Count;i++)
            {
                DataJson.Require(Path.GetFileName(files[i])=="events-"+i.ToString("D4")+".local.jsonl","DATA_SEGMENT_ORDER");DataJson.NoLinks(files[i]);
                long size=new FileInfo(files[i]).Length;total+=size;DataJson.Require(size<=MaximumSegmentBytes&&total<=512*1024*1024,"DATA_SEGMENT_LIMIT");
                byte[] bytes=File.ReadAllBytes(files[i]);int from=0;
                for(int at=0;at<bytes.Length;at++)if(bytes[at]==10)
                {
                    int length=at-from+1;DataJson.Require(length>1&&length<=DataJson.MaxLine,"DATA_LINE_LIMIT");var line=new byte[length];Buffer.BlockCopy(bytes,from,line,0,length);from=at+1;
                    var row=DataJson.ParseCanonical(line);DataJson.Keys(row,"schema_version","sequence","event_id","clock_epoch","host_mono_ms","identity","event_type","opportunity_id","attempt_id","audio_request_id","previous_sha256","payload","sha256");
                    DataJson.Require(DataJson.Text(row["schema_version"])==DataEventSchema.Version&&DataJson.Integer(row["sequence"])==sequence&&DataJson.Text(row["previous_sha256"])==previous,"DATA_CHAIN");
                    string id=DataJson.Text(row["event_id"]),epoch=DataJson.Text(row["clock_epoch"]),hash=DataJson.Text(row["sha256"]);
                    DataJson.Require(DataJson.Guid(id)&&ids.Add(id)&&DataJson.Guid(epoch)&&DataJson.Hash(hash),"DATA_ENVELOPE");
                    var source=DataIdentity.Parse(row["identity"] as JObject);DataJson.Require(JToken.DeepEquals(source.ToJson(),expectedIdentity.ToJson()),"DATA_IDENTITY");
                    var hashInput=(JObject)row.DeepClone();hashInput.Remove("sha256");DataJson.Require(DataJson.HashBytes(DataJson.Bytes(hashInput))==hash,"DATA_HASH");
                    var context=new EventContext(DataJson.OptionalText(row["opportunity_id"]),DataJson.OptionalText(row["attempt_id"]),DataJson.OptionalText(row["audio_request_id"]));
                    string kind=DataJson.Text(row["event_type"]);var payload=row["payload"] as JObject;DataEventSchema.Validate(kind,context,payload);
                    if(pending.Count>0){DataJson.Require(kind=="recovery"&&JToken.DeepEquals(payload["preserved_tails"],pending),"DATA_TAIL_CHAIN");pending.Clear();}
                    else DataJson.Require(kind!="recovery","DATA_TAIL_CHAIN");
                    CheckClock(clocks,epoch,DataJson.Number(row["host_mono_ms"]));
                    if(kind=="session")CheckClock(clocks,"session:"+(string)payload["clock_epoch"],(double)payload["host_mono_ms"]);
                    if(kind=="assessment_stage")CheckClock(clocks,"assessment:"+(string)payload["clock_epoch"],(double)payload["host_mono_ms"]);
                    records.Add(new DataRecord(row));previous=hash;sequence++;
                }
                if(from<bytes.Length)
                {
                    var tail=new byte[bytes.Length-from];Buffer.BlockCopy(bytes,from,tail,0,tail.Length);DataJson.Require(pending.Count<32,"DATA_TAIL_LIMIT");
                    pending.Add(new JObject{["segment"]=Path.GetFileName(files[i]),["tail_offset"]=from,["tail_sha256"]=DataJson.HashBytes(tail),["segment_sha256"]=DataJson.HashBytes(bytes)});
                }
            }
            return new JournalSnapshot(records,files,pending,clocks);
        }
        public void Dispose()
        {
            if(closed)return;closed=true;
            try {output.Flush(true);}catch{failed=true;throw new DataFault("DATA_CLOSE_FAILED");}finally{output.Dispose();}
        }
    }
}
