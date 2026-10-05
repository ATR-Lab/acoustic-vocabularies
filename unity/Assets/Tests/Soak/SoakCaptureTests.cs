using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.Soak;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Soak.Tests
{
    public sealed class SoakCaptureTests
    {
        static byte[] Bytes(JObject p)=>Encoding.UTF8.GetBytes(p.ToString());
        static JObject Plan()=>new JObject{["version"]=1,["scope"]="synthetic_nonstudy",["participants"]=false,["station_id"]="station-01",["build_id"]="DEMO-build",["scene_sha256"]=new string('a',64),["snapshot_sha256"]=new string('b',64),["schedule_sha256"]=new string('c',64),["source_kind"]="live",["seconds"]=60,["client_kind"]="headset_equivalent",["substitute_justification"]="Explicit engineering client; not physical headset evidence"};
        static SoakContext Protected()=>new SoakContext("protected","test-01","Running","DEMO-trial");
        [Test]public void PlanDetachedAndExplicitlyNonstudy()
        {var p=Plan();var b=Bytes(p);var loaded=SoakPlan.Load(b,SoakPlan.Hash(b));p["seconds"]=28800;var copy=loaded.Json;copy["participants"]=true;Assert.That(loaded.Seconds,Is.EqualTo(60));Assert.That((bool)loaded.Json["participants"],Is.False);}
        [TestCase("scope","participant")][TestCase("source_kind","snapshot")][TestCase("client_kind","fake")]
        public void UnsupportedPlanCannotBecomeEvidence(string key,string value){var p=Plan();p[key]=value;var b=Bytes(p);Assert.Throws<SoakFault>(()=>SoakPlan.Load(b,SoakPlan.Hash(b)));}
        [Test]public void RawIndependentPinRequired(){var b=Bytes(Plan());Assert.Throws<SoakFault>(()=>SoakPlan.Load(b,new string('0',64)));}
        [Test]public void ParticipantOrExtraAuthorityRefused(){var p=Plan();p["participants"]=true;var b=Bytes(p);Assert.Throws<SoakFault>(()=>SoakPlan.Load(b,SoakPlan.Hash(b)));p=Plan();p["approved"]=true;b=Bytes(p);Assert.Throws<SoakFault>(()=>SoakPlan.Load(b,SoakPlan.Hash(b)));}
        [TestCase(0)][TestCase(-1)][TestCase(36001)]public void BoundedDuration(double seconds){var p=Plan();p["seconds"]=seconds;var b=Bytes(p);Assert.Throws<SoakFault>(()=>SoakPlan.Load(b,SoakPlan.Hash(b)));}
        [Test]public void WatchdogDetectsGapWithoutMainThreadOrRenderCallback()
        {
            var rows=new List<(string,double,JObject)>();var m=new ContinuousSoakMonitor(0,Protected(),(k,t,p)=>rows.Add((k,t,p)));
            m.Source(new string('a',32),0,0,0);m.Render(0,0);m.Tick(.251);
            Assert.That(m.Healthy,Is.False);Assert.That(rows.Count(x=>x.Item1.EndsWith("_started")),Is.EqualTo(2));
            m.Source(new string('a',32),1,.4,.4);m.Render(1,.4);m.Tick(.5);
            Assert.That(rows.Single(x=>x.Item1=="stale_gap").Item3["duration_ms"].Value<double>(),Is.EqualTo(400));Assert.That(m.Healthy,Is.False);
        }
        [Test]public void RepeatedInterpolatedFrameDoesNotInventMirrorProgress()
        {
            var rows=new List<JObject>();var m=new ContinuousSoakMonitor(0,Protected(),(k,t,p)=>{if(k=="heartbeat")rows.Add(p);});
            string s=new string('a',32);m.Source(s,1,0,0);m.Source(s,1,.05,.05);m.Render(1,.05);m.Tick(.1);
            Assert.That((long)rows.Single()["mirrored_frames"],Is.EqualTo(1));m.Source(s,2,.2,.2);m.Render(2,.2);m.Tick(.35);Assert.That((long)rows.Last()["mirrored_frames"],Is.EqualTo(2));
        }
        [Test]public void ClockRegressionAndFutureReceiptRefused()
        {var m=new ContinuousSoakMonitor(1,Protected(),(k,t,p)=>{});Assert.Throws<SoakFault>(()=>m.Tick(.9));Assert.Throws<SoakFault>(()=>m.Source(new string('a',32),0,3,2));}
        [Test]public void SourceBeforeMonitorStartIsObservedWithItsRealAge()
        {var rows=new List<JObject>();var m=new ContinuousSoakMonitor(1,Protected(),(k,t,p)=>{if(k=="heartbeat")rows.Add(p);});m.Source(new string('a',32),2,.9,1);m.Render(1,1);m.Tick(1.01);Assert.That((double)rows.Single()["state_age_ms"],Is.EqualTo(110).Within(.0001));}
        [Test]public void RecoveryWithoutWatchdogTickStillRetainsGap()
        {var kinds=new List<string>();var m=new ContinuousSoakMonitor(0,Protected(),(k,t,p)=>kinds.Add(k));m.Source(new string('a',32),0,0,0);m.Source(new string('a',32),1,.5,.5);Assert.That(kinds,Does.Contain("stale_gap_started"));Assert.That(kinds,Does.Contain("stale_gap"));}
        [Test]public void SinkFailureLatchesAndEscapes()
        {var m=new ContinuousSoakMonitor(0,Protected(),(k,t,p)=>throw new IOException());Assert.Throws<IOException>(()=>m.Tick(0));Assert.That(m.Fault,Is.EqualTo("SOAK_LOG_FAILED"));}
        [Test]public void NativeJournalHashBindsExactBytesAndUsesFreshOutput()
        {
            string root=Path.Combine(Path.GetTempPath(),"av-soak-test-"+Guid.NewGuid().ToString("N"));
            try
            {
                using(var j=new SoakNativeJournal(root,new string('a',32),10000000)){j.Write("heartbeat",1.25,new JObject{["measured"]=true});j.Write("session_end",2.5,new JObject{["completed"]=false});}
                var lines=File.ReadAllLines(Path.Combine(root,"soak-native.jsonl"));Assert.That(lines.Length,Is.EqualTo(2));string previous=new string('0',64);
                for(int i=0;i<lines.Length;i++){var r=JObject.Parse(lines[i]);string h=(string)r["sha256"];r.Remove("sha256");Assert.That(SoakPlan.Hash(Encoding.UTF8.GetBytes(r.ToString(Newtonsoft.Json.Formatting.None)+"\n")),Is.EqualTo(h));Assert.That((string)r["previous_sha256"],Is.EqualTo(previous));previous=h;}
                Assert.Throws<SoakFault>(()=>new SoakNativeJournal(root,new string('a',32),10000000));
            }
            finally{if(Directory.Exists(root))Directory.Delete(root,true);}
        }
    }
}
