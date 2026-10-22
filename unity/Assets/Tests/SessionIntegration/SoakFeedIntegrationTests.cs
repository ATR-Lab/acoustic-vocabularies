using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using AcousticVocab.DataLogging;
using AcousticVocab.SessionEngine;
using AcousticVocab.Soak;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
namespace AcousticVocab.SessionIntegration.Tests
{
    public sealed class SoakFeedIntegrationTests
    {
        static string Fixture=>Path.GetFullPath(Path.Combine(UnityEngine.Application.dataPath,"..","..","tests","fixtures","soak_unity_feed"));
        static readonly string Rooted=Path.Combine(Path.GetTempPath(),"feed.jsonl");
        [TestCase(null,null,false,false,false,ExpectedResult=null)]
        [TestCase("in",null,true,true,true,ExpectedResult="SOAK_FEED_ARGUMENTS")]
        [TestCase("relative",null,true,true,true,ExpectedResult="SOAK_FEED_ARGUMENTS")]
        [TestCase("in","in",false,true,true,ExpectedResult="SOAK_FEED_REQUIRES_SIMULATION")]
        [TestCase("in","in",true,false,true,ExpectedResult="SOAK_FEED_REQUIRES_SIMULATION")]
        [TestCase("in","in",true,true,false,ExpectedResult="SOAK_FEED_REQUIRES_SIMULATION")]
        [TestCase("in","in",true,true,true,ExpectedResult=null)]
        public string FeedModeExistsOnlyBehindTheSimulationCapability(string inputs,string schedule,bool compiled,bool scene,bool authority)
        {
            string map(string x)=>x=="in"?Rooted:x=="relative"?"relative/feed.jsonl":x;
            string result=JoinedSoakCapture.FeedRefusal(map(inputs),map(schedule),compiled,scene,authority);
            if(inputs=="relative")Assert.That(JoinedSoakCapture.FeedRefusal(Rooted,"relative/schedule.json",true,true,true),Is.EqualTo("SOAK_FEED_ARGUMENTS"));
            return result;
        }
        [Test]public void UncPathsAreNotAFeedOrSchedule()
        {Assert.That(JoinedSoakCapture.FeedRefusal("\\\\host\\share\\feed.jsonl",Rooted,true,true,true),Is.EqualTo("SOAK_FEED_ARGUMENTS"));}
        [Test]public void RealDataJournalBindsEveryObservationToAVerifiedSessionRecord()
        {
            string root=Path.Combine(Path.GetTempPath(),"av-soak-data-"+Guid.NewGuid().ToString("N"));
            try
            {
                var raw=File.ReadAllBytes(Path.Combine(Fixture,"schedule.json"));var schedule=SoakSchedule.Load(raw,SoakPlan.Hash(raw));
                var identity=new DataIdentity(Guid.NewGuid().ToString("N"),"SOAK-SYNTHETIC","soak-feed","station-01","soak-feed-v1",new string('b',64));
                double now=1000;var observed=new List<(string kind,JObject payload)>();
                using(var journal=new DataJournal(root,identity,Guid.NewGuid().ToString("N"),()=>now+=1.5))
                {
                    var consumer=new SoakFeedConsumer(schedule,new SoakSessionRecorder(journal,schedule,()=>now,()=>true),(k,p)=>observed.Add((k,p)),_=>{});
                    foreach(var row in new SoakInputFeed(schedule,"station-01").Accept(File.ReadAllBytes(Path.Combine(Fixture,"unity-inputs.jsonl"))))
                    {
                        consumer.Accept(row);
                        if(row.Kind=="command_ack"&&row.Payload["fault_id"].Type!=JTokenType.Null)
                            consumer.OperatorResume(new JObject{["version"]=1,["fault_id"]=row.Payload["fault_id"],["recovery_reset_request_id"]=row.Payload["request_id"],["operator_initiated"]=true});
                    }
                }
                var records=DataJournal.Verify(root,identity).Records.ToList();
                Assert.That(records.All(r=>r.Kind=="session"),Is.True);
                var byId=records.ToDictionary(r=>(string)r.ToJson()["event_id"]);
                var references=observed.Where(x=>x.kind=="durable_record"||x.kind=="operator_resume").Select(x=>x.payload).ToList();
                Assert.That(references.Count,Is.EqualTo(records.Count));
                foreach(var p in references){var r=byId[(string)p["data_event_id"]];Assert.That(r.Sha256,Is.EqualTo((string)p["data_sha256"]));}
                var sessions=records.Select(r=>SessionRecordCodec.FromJson(r.Payload)).ToList();
                Assert.That(sessions.All(s=>s.ScheduleSha256==schedule.Sha256&&s.AudibleStatus==AudibleStatus.NoCue&&!s.ExposureConsumed&&s.EvidenceSha256==null),Is.True);
                Assert.That(sessions.Count(s=>s.Event=="state_before"),Is.EqualTo(11));Assert.That(sessions.Count(s=>s.Event=="response"),Is.EqualTo(4));
                var pause=sessions.Single(s=>s.Event=="session_paused");Assert.That(pause.TechnicalFaultCode,Is.EqualTo("SOAK_WIFI_DROP"));Assert.That(pause.TrialId,Is.EqualTo("block-0003-trial-000"));
                Assert.That(sessions.Single(s=>s.Event=="operator_resume").TrialId,Is.Null);
                var codes=new SoakInputFeed(schedule,"station-01").Accept(File.ReadAllBytes(Path.Combine(Fixture,"unity-inputs.jsonl"))).Where(r=>r.Kind=="dummy_response").Select(r=>(string)r.Payload["response_code"]);
                Assert.That(sessions.Where(s=>s.Event=="response").Select(s=>s.ResponseCode),Is.EqualTo(codes));
                int paused=records.FindIndex(x=>(string)x.Payload["event"]=="session_paused");
                Assert.That((string)observed.Single(x=>x.kind=="fault_injection").payload["last_committed_data_sha256"],Is.EqualTo(records.Take(paused).Last(r=>(string)r.Payload["event"]=="response").Sha256));
            }
            finally{if(Directory.Exists(root))Directory.Delete(root,true);}
        }
    }
}
