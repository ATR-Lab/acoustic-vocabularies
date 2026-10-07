using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.Soak;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Soak.Tests
{
    // Uses the committed synthetic driver output (tests/fixtures/soak_unity_feed),
    // produced by isaac.soak.driver against the in-process fake command server.
    public sealed class SoakInputFeedTests
    {
        internal static string Fixture=>Path.GetFullPath(Path.Combine(UnityEngine.Application.dataPath,"..","..","tests","fixtures","soak_unity_feed"));
        internal static byte[] ScheduleBytes=>File.ReadAllBytes(Path.Combine(Fixture,"schedule.json"));
        internal static byte[] FeedBytes=>File.ReadAllBytes(Path.Combine(Fixture,"unity-inputs.jsonl"));
        static SoakSchedule Schedule()=>SoakSchedule.Load(ScheduleBytes,SoakPlan.Hash(ScheduleBytes));
        static byte[] Encode(JObject row)=>new UTF8Encoding(false).GetBytes(row.ToString(Formatting.None)+"\n");
        internal static List<JObject> Rows(byte[] feed)
        {
            var result=new List<JObject>();
            foreach(var line in Encoding.UTF8.GetString(feed).Split('\n').Where(x=>x.Length>0))
            {using var reader=new JsonTextReader(new StringReader(line)){DateParseHandling=DateParseHandling.None};result.Add(JObject.Load(reader));}
            return result;
        }
        // Recomputes the driver's chain over edited rows, so a test isolates one
        // semantic refusal instead of a hash mismatch.
        internal static byte[] Rechain(IEnumerable<JObject> rows)
        {
            string previous=new string('0',64);long seq=0;var output=new MemoryStream();
            foreach(var original in rows)
            {
                var r=(JObject)original.DeepClone();r.Remove("sha256");r["seq"]=seq++;r["previous_sha256"]=previous;
                previous=SoakPlan.Hash(Encode(r));r["sha256"]=previous;var b=Encode(r);output.Write(b,0,b.Length);
            }
            return output.ToArray();
        }
        static string Code(TestDelegate action)=>Assert.Throws<SoakFault>(action).Message;
        internal sealed class Recorder:ISoakSessionRecorder
        {
            public readonly List<string> Calls=new List<string>();
            SoakDataReference Next(string call){Calls.Add(call);return new SoakDataReference(Guid.NewGuid().ToString("N"),SoakPlan.Hash(Encoding.UTF8.GetBytes(call+Calls.Count)));}
            public SoakDataReference TrialBegan(SoakTrial trial)=>Next("state_before:"+trial.TrialId+":"+trial.BlockIndex+":"+trial.ItemIndex);
            public SoakDataReference Responded(SoakTrial trial,string code)=>Next("response:"+trial.TrialId+":"+code);
            public SoakDataReference Paused(SoakTrial trial,string block,string fault)=>Next("session_paused:"+(trial?.TrialId??"none")+":"+fault);
            public SoakDataReference Resumed(string block)=>Next("operator_resume:"+block);
        }
        sealed class Run
        {
            public readonly List<(string kind,JObject payload)> Native=new List<(string,JObject)>();
            public readonly Recorder Recorder=new Recorder();public SoakFeedConsumer Consumer;public IReadOnlyList<SoakFeedRow> Rows;
            public Run(byte[] feed,Action<Run,SoakFeedRow> after=null)
            {
                var schedule=Schedule();
                Consumer=new SoakFeedConsumer(schedule,Recorder,(k,p)=>Native.Add((k,p)),c=>Native.Add(("context",new JObject{["block"]=c.Block,["block_id"]=c.BlockId,["engine_state"]=c.EngineState,["trial_id"]=c.TrialId})));
                Rows=new SoakInputFeed(schedule,"station-01").Accept(feed);
                foreach(var row in Rows){Consumer.Accept(row);after?.Invoke(this,row);}
            }
            public int Count(string kind)=>Native.Count(x=>x.kind==kind);
        }
        static JObject Resume(string fault,string reset,bool operatorInitiated=true)=>new JObject{["version"]=1,["fault_id"]=fault,["recovery_reset_request_id"]=reset,["operator_initiated"]=operatorInitiated};
        static void ResumeAfterRecovery(Run run,SoakFeedRow row){if(row.Kind=="command_ack"&&row.Payload["fault_id"].Type!=JTokenType.Null)run.Consumer.OperatorResume(Resume((string)row.Payload["fault_id"],(string)row.Payload["request_id"]));}

        [Test]public void PinnedDriverScheduleLoadsAndOtherBytesAreRefused()
        {
            var raw=ScheduleBytes;var schedule=SoakSchedule.Load(raw,SoakPlan.Hash(raw));
            Assert.That(schedule.StationId,Is.EqualTo("station-01"));Assert.That(schedule.BlockOrdinal("block-0003"),Is.EqualTo(3));Assert.That(schedule.TrialOrdinal("block-0001-trial-002"),Is.EqualTo(2));
            Assert.That(Code(()=>SoakSchedule.Load(raw,new string('0',64))),Is.EqualTo("SOAK_SCHEDULE_PIN"));
            var pretty=Encoding.UTF8.GetBytes(JObject.Parse(Encoding.UTF8.GetString(raw)).ToString(Formatting.Indented)+"\n");
            Assert.That(Code(()=>SoakSchedule.Load(pretty,SoakPlan.Hash(pretty))),Is.EqualTo("SOAK_SCHEDULE_JSON"));
            var document=JObject.Parse(Encoding.UTF8.GetString(raw));document["participants"]=true;var participant=Encoding.UTF8.GetBytes(document.ToString(Formatting.None)+"\n");
            Assert.That(Code(()=>SoakSchedule.Load(participant,SoakPlan.Hash(participant))),Is.EqualTo("SOAK_SCHEDULE_SCOPE"));
        }
        [Test]public void EveryDriverRowVerifiesAcrossArbitraryChunksAndPartialTailWaits()
        {
            var feed=FeedBytes;var verifier=new SoakInputFeed(Schedule(),"station-01");var rows=new List<SoakFeedRow>();
            for(int at=0;at<feed.Length;at+=7)rows.AddRange(verifier.Accept(feed.Skip(at).Take(7).ToArray()));
            Assert.That(rows.Count,Is.EqualTo(Rows(feed).Count));Assert.That(rows.Select(r=>r.Seq),Is.EqualTo(Enumerable.Range(0,rows.Count).Select(i=>(long)i)));
            Assert.That(verifier.HasPartialLine,Is.False);
            var again=new SoakInputFeed(Schedule(),"station-01");var firstLine=feed.TakeWhile(b=>b!=10).ToArray();
            Assert.That(again.Accept(firstLine),Is.Empty);Assert.That(again.HasPartialLine,Is.True);Assert.That(again.Accept(new byte[]{10}).Single().Kind,Is.EqualTo("block_begin"));
        }
        [Test]public void FeedFileIsReadIncrementallyAndMissingFileMeansNotStarted()
        {
            string root=Path.Combine(Path.GetTempPath(),"av-soak-feed-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(root);
            try
            {
                string path=Path.Combine(root,"unity-inputs.jsonl");var feed=new SoakInputFeed(Schedule(),"station-01",path);Assert.That(feed.Poll(),Is.Empty);
                var bytes=FeedBytes;int half=bytes.Length/2;File.WriteAllBytes(path,bytes.Take(half).ToArray());int first=feed.Poll().Count;
                using(var s=new FileStream(path,FileMode.Append,FileAccess.Write,FileShare.Read))s.Write(bytes,half,bytes.Length-half);
                Assert.That(first+feed.Poll().Count,Is.EqualTo(Rows(bytes).Count));
                File.WriteAllBytes(path,bytes.Take(10).ToArray());Assert.That(Code(()=>feed.Poll()),Is.EqualTo("SOAK_FEED_TRUNCATED"));Assert.That(feed.Failure,Is.EqualTo("SOAK_FEED_TRUNCATED"));
            }
            finally{Directory.Delete(root,true);}
        }
        [Test]public void TamperedOrRebindingRowsAreRefusedAndLatched()
        {
            var feed=FeedBytes;var rows=Rows(feed);
            var flipped=(byte[])feed.Clone();int at=Encoding.UTF8.GetString(feed).IndexOf("MODE_CHANGED",StringComparison.Ordinal);flipped[at]=(byte)'N';
            var verifier=new SoakInputFeed(Schedule(),"station-01");Assert.That(Code(()=>verifier.Accept(flipped)),Is.EqualTo("SOAK_FEED_HASH"));
            Assert.That(Code(()=>verifier.Accept(new byte[0])),Is.EqualTo("SOAK_FEED_HASH"));
            var dropped=Encoding.UTF8.GetBytes(string.Join("\n",Encoding.UTF8.GetString(feed).Split('\n').Where((_,i)=>i!=1)));
            Assert.That(Code(()=>new SoakInputFeed(Schedule(),"station-01").Accept(dropped)),Is.EqualTo("SOAK_FEED_CHAIN"));
            Assert.That(Code(()=>new SoakInputFeed(Schedule(),"station-02")),Is.EqualTo("SOAK_FEED_BINDING"));
            var moved=rows.Select(r=>(JObject)r.DeepClone()).ToList();moved[0]["payload"]["block_id"]="block-0001";
            Assert.That(Code(()=>new SoakInputFeed(Schedule(),"station-01").Accept(Rechain(moved))),Is.EqualTo("SOAK_FEED_SCHEDULE"));
            var other=rows.Select(r=>(JObject)r.DeepClone()).ToList();other[0]["station_id"]="station-02";
            Assert.That(Code(()=>new SoakInputFeed(Schedule(),"station-01").Accept(Rechain(other))),Is.EqualTo("SOAK_FEED_BINDING"));
            var reply=rows.Select(r=>(JObject)r.DeepClone()).ToList();reply[1]["payload"]["reply_utf8"]=((string)reply[1]["payload"]["reply_utf8"]).Replace("MODE_CHANGED","MODE_CHANGES");
            Assert.That(Code(()=>new SoakInputFeed(Schedule(),"station-01").Accept(Rechain(reply))),Is.EqualTo("SOAK_FEED_REPLY_HASH"));
            var extra=rows.Select(r=>(JObject)r.DeepClone()).ToList();extra[0]["payload"]["approved"]=true;
            Assert.That(Code(()=>new SoakInputFeed(Schedule(),"station-01").Accept(Rechain(extra))),Is.EqualTo("SOAK_FEED_PAYLOAD"));
            var backwards=rows.Select(r=>(JObject)r.DeepClone()).ToList();backwards.Insert(3,(JObject)backwards[0].DeepClone());
            Assert.That(Code(()=>new SoakInputFeed(Schedule(),"station-01").Accept(Rechain(backwards))),Is.EqualTo("SOAK_FEED_STEP"));
            // A correctly hashed row that is not the driver's compact spelling.
            var first=Encoding.UTF8.GetString(Rechain(rows.Take(1))).TrimEnd('\n').Replace("\"version\":1,","\"version\": 1,");
            string spaced=first.Substring(0,first.Length-78+1)+"}";string hash=SoakPlan.Hash(Encoding.UTF8.GetBytes(spaced+"\n"));var noncanonical=Encoding.UTF8.GetBytes(spaced.Substring(0,spaced.Length-1)+",\"sha256\":\""+hash+"\"}\n");
            Assert.That(Code(()=>new SoakInputFeed(Schedule(),"station-01").Accept(noncanonical)),Is.EqualTo("SOAK_FEED_JSON"));
        }
        [Test]public void ConsumerLogsExactlyTheNormalizerPayloadsInOrder()
        {
            var run=new Run(FeedBytes,ResumeAfterRecovery);
            string[] receipt={"input_seq","request_id","reply_sha256"};
            Assert.That(run.Count("reset_receipt"),Is.EqualTo(14));Assert.That(run.Count("lock_probe_receipt"),Is.EqualTo(4));
            Assert.That(run.Count("fault_injection"),Is.EqualTo(1));Assert.That(run.Count("operator_resume"),Is.EqualTo(1));Assert.That(run.Count("durable_record"),Is.EqualTo(16));
            Assert.That(run.Native.Where(x=>x.kind.EndsWith("_receipt")).All(x=>x.payload.Properties().Select(p=>p.Name).SequenceEqual(receipt)),Is.True);
            Assert.That(run.Native.Where(x=>x.kind=="durable_record").All(x=>x.payload.Properties().Select(p=>p.Name).SequenceEqual(new[]{"data_event_id","data_sha256","reset_request_id"})),Is.True);
            JObject context=null;
            foreach(var (kind,payload) in run.Native)
            {
                if(kind=="context")context=payload;
                if(kind=="lock_probe_receipt")Assert.That((string)context["block"],Is.EqualTo("protected"));
                if(kind=="reset_receipt"){var row=run.Rows[(int)payload["input_seq"]];Assert.That(row.Kind,Is.EqualTo("command_ack"));Assert.That((string)payload["reply_sha256"],Is.EqualTo((string)row.Payload["reply_sha256"]));}
            }
            // Fault: retained baseline is the last committed response, then a
            // paused context, then the durable pause record.
            int fault=run.Native.FindIndex(x=>x.kind=="fault_injection");
            Assert.That((string)run.Native[fault+1].payload["block"],Is.EqualTo("paused"));Assert.That(run.Native[fault+2].kind,Is.EqualTo("durable_record"));
            var lastResponse=run.Native.Take(fault).Last(x=>x.kind=="durable_record"&&x.payload["reset_request_id"].Type==JTokenType.Null);
            Assert.That((string)run.Native[fault].payload["last_committed_data_sha256"],Is.EqualTo((string)lastResponse.payload["data_sha256"]));
            var resume=run.Native.Single(x=>x.kind=="operator_resume").payload;
            var recovery=run.Rows.Single(r=>r.Kind=="command_ack"&&r.Payload["fault_id"].Type!=JTokenType.Null);
            Assert.That((string)resume["reset_request_id"],Is.EqualTo((string)recovery.Payload["request_id"]));Assert.That((string)resume["fault_id"],Is.EqualTo((string)recovery.Payload["fault_id"]));
            Assert.That((string)resume["last_committed_data_sha256"],Is.EqualTo((string)run.Native[fault].payload["last_committed_data_sha256"]));
            Assert.That(run.Recorder.Calls.Count(c=>c.StartsWith("state_before:")),Is.EqualTo(11));Assert.That(run.Recorder.Calls,Does.Contain("session_paused:block-0003-trial-000:wifi_drop"));
            Assert.That(run.Consumer.ActiveFaultId,Is.Null);Assert.That(run.Consumer.SkippedTrials,Is.Zero);
        }
        [Test]public void PausedHostRecordsResetsButNeverBeginsTrialsOrReceiptsProbes()
        {
            var run=new Run(FeedBytes);
            Assert.That(run.Consumer.ActiveFaultId,Is.Not.Null);Assert.That(run.Consumer.Context.Block,Is.EqualTo("paused"));
            Assert.That(run.Count("reset_receipt"),Is.EqualTo(14));Assert.That(run.Count("lock_probe_receipt"),Is.EqualTo(3));Assert.That(run.Consumer.UnreceiptedLockProbes,Is.EqualTo(1));
            Assert.That(run.Consumer.SkippedTrials,Is.EqualTo(1));Assert.That(run.Consumer.SkippedResponses,Is.EqualTo(1));Assert.That(run.Count("operator_resume"),Is.Zero);
            Assert.That(run.Recorder.Calls.Any(c=>c.StartsWith("state_before:block-0003-trial-002")),Is.False);
            // Resume binds the latest successful reset placed during the fault.
            var reset=run.Native.Last(x=>x.kind=="reset_receipt").payload;var recovery=run.Rows.Single(r=>r.Kind=="command_ack"&&r.Payload["fault_id"].Type!=JTokenType.Null);
            run.Consumer.OperatorResume(Resume(run.Consumer.ActiveFaultId,(string)recovery.Payload["request_id"]));
            Assert.That((string)run.Native.Single(x=>x.kind=="operator_resume").payload["reset_request_id"],Is.EqualTo((string)reset["request_id"]));
        }
        [Test]public void OperatorResumeIsExplicitBoundAndNeverPremature()
        {
            var rows=Rows(FeedBytes);int marker=rows.FindIndex(r=>(string)r["kind"]=="fault_marker");
            var early=new Run(Rechain(rows.Take(marker+1)));string fault=early.Consumer.ActiveFaultId;string recoveryId=(string)rows[marker+1]["payload"]["request_id"];
            Assert.That(early.Consumer.ResumeBlocker,Is.EqualTo("SOAK_RESUME_WAITING_RECOVERY_RESET"));
            Assert.That(Code(()=>early.Consumer.OperatorResume(Resume(fault,recoveryId))),Is.EqualTo("SOAK_RESUME_WAITING_RECOVERY_RESET"));
            var ready=new Run(Rechain(rows.Take(marker+2)));
            Assert.That(Code(()=>ready.Consumer.OperatorResume(Resume(fault,new string('e',32)))),Is.EqualTo("SOAK_RESUME_BINDING"));
            Assert.That(Code(()=>ready.Consumer.OperatorResume(Resume(fault,recoveryId,false))),Is.EqualTo("SOAK_RESUME_FILE_INVALID"));
            var extra=Resume(fault,recoveryId);extra["approved"]=true;Assert.That(Code(()=>ready.Consumer.OperatorResume(extra)),Is.EqualTo("SOAK_RESUME_FILE_INVALID"));
            Assert.That(ready.Count("operator_resume"),Is.Zero);Assert.That(ready.Consumer.Failure,Is.Null);
            string root=Path.Combine(Path.GetTempPath(),"av-soak-resume-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(root);
            try
            {
                Assert.That(ready.Consumer.PollOperator(root),Is.EqualTo("SOAK_RESUME_READY"));
                string file=Path.Combine(root,"operator-resume-"+fault+".json");File.WriteAllText(file,"{\"version\":1,");Assert.That(ready.Consumer.PollOperator(root),Is.EqualTo("SOAK_RESUME_FILE_INVALID"));
                File.WriteAllText(file,Resume(fault,recoveryId).ToString());Assert.That(ready.Consumer.PollOperator(root),Is.EqualTo("SOAK_RESUMED"));
                Assert.That(ready.Count("operator_resume"),Is.EqualTo(1));Assert.That(ready.Consumer.PollOperator(root),Is.Null);
            }
            finally{Directory.Delete(root,true);}
        }
        [Test]public void FaultWithoutCommittedRecordAndDuplicateReceiptLatch()
        {
            var rows=Rows(FeedBytes);
            var uncommitted=rows.Where(r=>(string)r["kind"]!="dummy_response").ToList();
            var run=Assert.Throws<SoakFault>(()=>new Run(Rechain(uncommitted)));Assert.That(run.Message,Is.EqualTo("SOAK_FEED_FAULT_WITHOUT_COMMITTED_RECORD"));
            var duplicate=rows.Select(r=>(JObject)r.DeepClone()).ToList();duplicate.Insert(3,(JObject)duplicate[2].DeepClone());
            Assert.That(Assert.Throws<SoakFault>(()=>new Run(Rechain(duplicate))).Message,Is.EqualTo("SOAK_FEED_DUPLICATE_RECEIPT"));
        }
        [Test]public void FailedResetCannotStartTheFollowingTrial()
        {
            var rows=Rows(FeedBytes);var failed=rows.Select(r=>(JObject)r.DeepClone()).ToList();
            var ack=failed[2]["payload"];var reply=JObject.Parse((string)ack["reply_utf8"]);reply["accepted"]=false;reply["reason"]="RESET_FAILED";reply["reset_ok"]=false;
            ack["reply_utf8"]=reply.ToString(Formatting.None);ack["reply_sha256"]=SoakPlan.Hash(Encoding.UTF8.GetBytes((string)ack["reply_utf8"]));
            var run=new Run(Rechain(failed.Take(5)));
            Assert.That(run.Count("reset_receipt"),Is.EqualTo(1));Assert.That(run.Count("durable_record"),Is.Zero);Assert.That(run.Consumer.SkippedTrials,Is.EqualTo(1));
            Assert.That(run.Consumer.Context.TrialId,Is.Null);
        }
    }
}
