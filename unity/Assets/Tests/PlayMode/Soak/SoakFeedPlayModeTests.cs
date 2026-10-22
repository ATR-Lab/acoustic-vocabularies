using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.DataLogging;
using AcousticVocab.SessionIntegration;
using AcousticVocab.Soak;
using AcousticVocab.StateSources;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace AcousticVocab.Soak.PlayModeTests
{
    // Real SoakCaptureHost frames, timer and native journal consuming the
    // committed synthetic driver feed. The live frames are local test frames;
    // nothing here is soak evidence or a station run.
    public sealed class SoakFeedPlayModeTests
    {
        static string Fixture=>Path.GetFullPath(Path.Combine(Application.dataPath,"..","..","tests","fixtures","soak_unity_feed"));
        static byte[] ScheduleBytes=>File.ReadAllBytes(Path.Combine(Fixture,"schedule.json"));
        static byte[][] FeedLines=>SplitLines(File.ReadAllBytes(Path.Combine(Fixture,"unity-inputs.jsonl")));
        static byte[][] SplitLines(byte[] all){var lines=new List<byte[]>();int from=0;for(int i=0;i<all.Length;i++)if(all[i]==10){lines.Add(all.Skip(from).Take(i-from+1).ToArray());from=i+1;}return lines.ToArray();}
        static JObject Parse(byte[] line){using var reader=new JsonTextReader(new StringReader(Encoding.UTF8.GetString(line))){DateParseHandling=DateParseHandling.None};return JObject.Load(reader);}
        string root;GameObject go;Harness harness;
        [SetUp]public void SetUp(){root=Path.Combine(Path.GetTempPath(),"av-soak-play-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(root);}
        [TearDown]public void TearDown()
        {
            try{harness?.Close();}finally{if(go!=null)UnityEngine.Object.DestroyImmediate(go);if(Directory.Exists(root))Directory.Delete(root,true);}
        }
        sealed class FakeRecorder:ISoakSessionRecorder
        {
            int n;SoakDataReference Next()=>new SoakDataReference(Guid.NewGuid().ToString("N"),SoakPlan.Hash(BitConverter.GetBytes(++n)));
            public SoakDataReference TrialBegan(SoakTrial t)=>Next();public SoakDataReference Responded(SoakTrial t,string c)=>Next();
            public SoakDataReference Paused(SoakTrial t,string b,string f)=>Next();public SoakDataReference Resumed(string b)=>Next();
        }
        sealed class Harness
        {
            public SoakCaptureHost Capture;public SoakFeedConsumer Consumer;public SoakInputFeed Feed;public DataJournal Data;public byte[] Plan;public string FeedPath,Status;
            readonly string session=Guid.NewGuid().ToString("N");long sequence;
            public void Frame(){double now=SoakCaptureHost.Now;Capture.SourceApplied(new SceneFrame(session,sequence++,0,0,0,"live",new double[0],new SceneObject[0]),now);}
            public void Pump(){foreach(var row in Feed.Poll())Consumer.Accept(row);Status=Consumer.PollOperator(Capture.OutputDirectory);}
            public string[] Native=>File.ReadAllLines(Path.Combine(Capture.OutputDirectory,"soak-native.jsonl"));
            public void Close(){Capture?.Finish();Data?.Dispose();Data=null;}
        }
        Harness Install(bool realData)
        {
            var schedule=SoakSchedule.Load(ScheduleBytes,SoakPlan.Hash(ScheduleBytes));
            var plan=Encoding.UTF8.GetBytes(new JObject{["version"]=1,["scope"]="synthetic_nonstudy",["participants"]=false,["station_id"]="station-01",["build_id"]="soak-feed-fixture",
                ["scene_sha256"]=new string('a',64),["snapshot_sha256"]=new string('b',64),["schedule_sha256"]=schedule.Sha256,["source_kind"]="live",["seconds"]=120,
                ["client_kind"]="headset_equivalent",["substitute_justification"]="Synthetic PlayMode fixture with local test frames; not a headset, station or soak run"}.ToString(Formatting.None));
            var h=new Harness{Plan=plan,FeedPath=Path.Combine(root,"driver","unity-inputs.jsonl")};harness=h;Directory.CreateDirectory(Path.GetDirectoryName(h.FeedPath));
            go=new GameObject("soak feed host");h.Capture=go.AddComponent<SoakCaptureHost>();
            h.Capture.Install(plan,SoakPlan.Hash(plan),Path.Combine(root,"capture"),"station-01","soak-feed-fixture",new string('a',64),new string('b',64),schedule.Sha256,()=>h.Consumer?.Context??SoakFeedConsumer.Waiting,_=>{});
            ISoakSessionRecorder recorder=new FakeRecorder();
            if(realData)
            {
                h.Data=new DataJournal(Path.Combine(h.Capture.OutputDirectory,"soak-data"),new DataIdentity(Guid.NewGuid().ToString("N"),"SOAK-SYNTHETIC","soak-feed","station-01","soak-feed-fixture",new string('c',64)),Guid.NewGuid().ToString("N"),()=>SoakCaptureHost.Now*1000);
                recorder=new SoakSessionRecorder(h.Data,schedule,()=>SoakCaptureHost.Now*1000,()=>true);
            }
            h.Consumer=new SoakFeedConsumer(schedule,recorder,h.Capture.Observe,h.Capture.ObserveContext);h.Feed=new SoakInputFeed(schedule,"station-01",h.FeedPath);
            return h;
        }
        static void Append(string path,params byte[][] chunks){using var s=new FileStream(path,FileMode.Append,FileAccess.Write,FileShare.Read);foreach(var c in chunks)s.Write(c,0,c.Length);}
        static List<JObject> Rows(string[] lines)=>lines.Select(l=>Parse(Encoding.UTF8.GetBytes(l+"\n"))).ToList();
        IEnumerator Frames(int count){for(int i=0;i<count;i++){harness.Frame();yield return null;}}
        static void WriteResume(Harness h,string fault,string reset)=>File.WriteAllText(Path.Combine(h.Capture.OutputDirectory,"operator-resume-"+fault+".json"),
            new JObject{["version"]=1,["fault_id"]=fault,["recovery_reset_request_id"]=reset,["operator_initiated"]=true}.ToString(Formatting.None));

        [UnityTest]public IEnumerator WholeFeedInOneFrameStillOrdersContextBeforeEveryReceipt()
        {
            var h=Install(false);yield return Frames(3);
            var lines=FeedLines;int marker=lines.ToList().FindIndex(l=>(string)Parse(l)["kind"]=="fault_marker");
            Append(h.FeedPath,lines.Take(marker).ToArray());h.Pump();yield return Frames(3);h.Close();
            var native=Rows(h.Native);JObject context=null;int probes=0;
            Assert.That((string)native[0]["kind"],Is.EqualTo("session_start"));Assert.That((string)native.Last()["kind"],Is.EqualTo("session_end"));
            foreach(var row in native)
            {
                if((string)row["kind"]=="context")context=(JObject)row["payload"];
                if((string)row["kind"]=="lock_probe_receipt"){probes++;Assert.That((string)context["block"],Is.EqualTo("protected"));}
            }
            Assert.That(probes,Is.EqualTo(3));Assert.That(native.Count(r=>(string)r["kind"]=="reset_receipt"),Is.EqualTo(12));
            Assert.That(native.Any(r=>(string)r["kind"]=="heartbeat"),Is.True);Assert.That((string)context["block"],Is.EqualTo("protected"));Assert.That((string)context["engine_state"],Is.EqualTo("SoakFeedRunning"));
        }
        [UnityTest]public IEnumerator PartialLineWaitsAndOperatorFileResumesOnlyAfterRecoveryReset()
        {
            var h=Install(false);var lines=FeedLines;int marker=lines.ToList().FindIndex(l=>(string)Parse(l)["kind"]=="fault_marker");
            var recovery=Parse(lines[marker+1]);string fault=(string)recovery["payload"]["fault_id"],reset=(string)recovery["payload"]["request_id"];
            Append(h.FeedPath,lines.Take(marker+1).ToArray());Append(h.FeedPath,lines[marker+1].Take(100).ToArray());
            h.Pump();yield return Frames(2);
            Assert.That(h.Consumer.ActiveFaultId,Is.EqualTo(fault));Assert.That(h.Consumer.Context.Block,Is.EqualTo("paused"));Assert.That(h.Feed.HasPartialLine,Is.True);
            WriteResume(h,fault,reset);h.Pump();yield return Frames(2);
            Assert.That(h.Status,Is.EqualTo("SOAK_RESUME_WAITING_RECOVERY_RESET"));Assert.That(h.Consumer.ActiveFaultId,Is.EqualTo(fault));
            Append(h.FeedPath,lines[marker+1].Skip(100).ToArray());h.Pump();yield return Frames(2);
            Assert.That(h.Status,Is.EqualTo("SOAK_RESUMED"));Assert.That(h.Consumer.ActiveFaultId,Is.Null);
            h.Close();var native=Rows(h.Native);
            int receipt=native.FindIndex(r=>(string)r["kind"]=="reset_receipt"&&(long)r["payload"]["input_seq"]==marker+1),resume=native.FindIndex(r=>(string)r["kind"]=="operator_resume");
            Assert.That(receipt,Is.GreaterThan(0));Assert.That(resume,Is.GreaterThan(receipt));Assert.That((string)native[resume]["payload"]["reset_request_id"],Is.EqualTo(reset));
            Assert.That(native.Skip(resume).First(r=>(string)r["kind"]=="context")["payload"]["block"].ToString(),Is.EqualTo("protected"));
        }
        // Also the exporter for tests/fixtures/soak_unity_feed: set
        // AV_SOAK_FEED_FIXTURE_EXPORT to a fresh directory to retain the bytes.
        [UnityTest]public IEnumerator DriverFixtureReplaysThroughRealHostAndDataJournal()
        {
            var h=Install(true);yield return Frames(3);
            var lines=FeedLines;string resumed=null;
            foreach(var line in lines)
            {
                Append(h.FeedPath,line);h.Pump();var row=Parse(line);
                if((string)row["kind"]=="command_ack"&&row["payload"]["fault_id"].Type!=JTokenType.Null){WriteResume(h,(string)row["payload"]["fault_id"],(string)row["payload"]["request_id"]);h.Pump();resumed=h.Status;}
                yield return Frames(2);
            }
            yield return Frames(3);h.Close();
            Assert.That(resumed,Is.EqualTo("SOAK_RESUMED"));Assert.That(h.Feed.Rows,Is.EqualTo(lines.Length));
            var native=Rows(h.Native);var kinds=native.Select(r=>(string)r["kind"]).ToList();
            Assert.That(kinds.Count(k=>k=="reset_receipt"),Is.EqualTo(14));Assert.That(kinds.Count(k=>k=="lock_probe_receipt"),Is.EqualTo(4));Assert.That(kinds.Count(k=>k=="durable_record"),Is.EqualTo(16));
            Assert.That(kinds.Count(k=>k=="fault_injection"),Is.EqualTo(1));Assert.That(kinds.Count(k=>k=="operator_resume"),Is.EqualTo(1));Assert.That(kinds.Contains("cue_observation"),Is.False);
            string dataDirectory=Path.Combine(h.Capture.OutputDirectory,"soak-data");var segments=Directory.GetFiles(dataDirectory,"events-*.local.jsonl");Assert.That(segments.Length,Is.EqualTo(1));
            Assert.That(File.ReadAllLines(segments[0]).Length,Is.EqualTo(17));
            TestContext.WriteLine("render_frame_index_last="+native.Last(r=>(string)r["kind"]=="heartbeat")["payload"]["render_frame_index"]);
            string export=Environment.GetEnvironmentVariable("AV_SOAK_FEED_FIXTURE_EXPORT");
            if(!string.IsNullOrEmpty(export))
            {
                Assert.That(Directory.Exists(export),Is.False,"Export requires a fresh directory");Directory.CreateDirectory(export);
                File.WriteAllBytes(Path.Combine(export,"station-plan.json"),h.Plan);
                File.Copy(Path.Combine(h.Capture.OutputDirectory,"soak-native.jsonl"),Path.Combine(export,"soak-native.jsonl"));
                File.Copy(segments[0],Path.Combine(export,"data-events.jsonl"));
            }
        }
    }
}
