using System;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.FrameBudget.Tests
{
    // The native probe itself needs the engineering scene and a simulator run;
    // these tests cover its argument gate and the exact monitor/evidence path
    // its synthetic timeline drives. No device or headset result is implied.
    public sealed class FrameProbePlanTests
    {
        static string Fresh(string name)=>Path.Combine(Environment.GetEnvironmentVariable("FRAME_TEST_ROOT")??Path.Combine(Environment.CurrentDirectory,".local","frame-tests"),name+"-"+Guid.NewGuid().ToString("N"));
        static string[] Args(params string[] extra)=>new[]{"Experiment.exe","-batchmode"}.Concat(extra).ToArray();

        [TestCase("0",false,"none")][TestCase("200",false,"stall")][TestCase("300",false,"stall")]
        public void StallOnlyRunsKeepTheirExistingArguments(string stall,bool loss,string injection)
        {
            var plan=FrameProbePlan.Parse(Args("-frameProbeOutput","out","-frameProbeStall",stall));
            Assert.That(plan.Output,Is.EqualTo("out"));Assert.That(plan.StallMs,Is.EqualTo(int.Parse(stall)));Assert.That(plan.PanelLoss,Is.EqualTo(loss));Assert.That(plan.Injection,Is.EqualTo(injection));
        }
        [Test] public void PanelLossIsABareSwitchOnlyWithZeroStall()
        {
            foreach(var args in new[]{Args("-frameProbeOutput","out","-frameProbeStall","0","-frameProbePanelLoss"),Args("-frameProbePanelLoss","-frameProbeOutput","out","-frameProbeStall","0"),Args("-frameProbeOutput","out","-frameProbePanelLoss","-frameProbeStall","0")})
            {var plan=FrameProbePlan.Parse(args);Assert.That(plan.PanelLoss,Is.True);Assert.That(plan.StallMs,Is.Zero);Assert.That(plan.Injection,Is.EqualTo("panel_loss"));}
        }
        [Test] public void AmbiguousOrCombinedInjectionsAreRefused()
        {
            foreach(var args in new[]{
                Args("-frameProbeOutput","out","-frameProbeStall","200","-frameProbePanelLoss"),
                Args("-frameProbeOutput","out","-frameProbeStall","300","-frameProbePanelLoss"),
                Args("-frameProbeOutput","out","-frameProbeStall","0","-frameProbePanelLoss","0"),
                Args("-frameProbeOutput","out","-frameProbeStall","0","-frameProbePanelLoss","1"),
                Args("-frameProbeOutput","out","-frameProbeStall","0","-frameProbePanelLoss","-frameProbePanelLoss"),
                Args("-frameProbeOutput","out","-frameProbeStall","0","-frameProbeStall","300"),
                Args("-frameProbeOutput","out","-frameProbeOutput","other","-frameProbeStall","0"),
                Args("-frameProbePanelLoss"),
                Args("-frameProbeOutput","-frameProbePanelLoss","-frameProbeStall","0"),
                Args("-frameProbeOutput"," ","-frameProbeStall","0"),
                Args("-frameProbeOutput","out","-frameProbeStall","250"),
                Args("-frameProbeOutput","out","-frameProbeStall","+200"),
                Args("-frameProbeOutput","out","-frameProbeStall")})
                Assert.That(Assert.Throws<FrameFault>(()=>FrameProbePlan.Parse(args)).Code,Is.EqualTo("FRAME_PROBE_ARGUMENTS"));
            Assert.That(Assert.Throws<FrameFault>(()=>FrameProbePlan.Parse(null)).Code,Is.EqualTo("FRAME_PROBE_ARGUMENTS"));
        }

        // Drives the probe's own windows and injection time at 72 Hz with no gap
        // longer than one frame: the only fault cause is the reported input state.
        static (JObject result,string[] events,JObject metadata,JObject manifest,int rows) Run(FrameProbePlan plan,bool loseInputAtInjection)
        {
            string path=Fresh("probe-"+plan.Injection);double start=5000,now=start,step=1000d/72;double? injectedAt=null;long frame=0;FrameMonitor monitor;
            using(var evidence=new FrameCsvEvidence(path,plan.Describe(new JObject{["version"]=1,["source"]="editmode_virtual_clock",["qualification"]=false})))
            {
                monitor=new FrameMonitor(72,evidence);monitor.Render(now,new RenderSample(frame++),true);monitor.Register(plan.Attempt(start),plan.Response(start),now);
                bool input=true;
                while(now-start<=FrameProbePlan.FinishAfterMs)
                {
                    now+=step;
                    if(injectedAt==null&&now-start>FrameProbePlan.InjectAfterMs){injectedAt=now;if(loseInputAtInjection)input=false;}
                    monitor.Render(now,new RenderSample(frame++),input);
                }
            }
            var result=plan.Result("FRAME_PROBE_COMPLETE",0,(int)frame-1,true,monitor,injectedAt);
            return (result,File.ReadAllLines(Path.Combine(path,"events.jsonl")),JObject.Parse(File.ReadAllText(Path.Combine(path,"metadata.json"))),JObject.Parse(File.ReadAllText(Path.Combine(path,"manifest.json"))),File.ReadLines(Path.Combine(path,"frames.csv")).Count()-1);
        }
        [Test] public void InjectedPanelLossWritesOneInterfaceFaultWithoutFreeze()
        {
            var plan=FrameProbePlan.Parse(Args("-frameProbeOutput","out","-frameProbeStall","0","-frameProbePanelLoss"));
            var run=Run(plan,true);var events=run.events.Select(JObject.Parse).ToArray();
            var fault=events.Single(e=>(string)e["kind"]=="fault");
            Assert.That((string)fault["technical_fault_code"],Is.EqualTo("FRAME_INTERFACE_UNAVAILABLE"));
            Assert.That((double)fault["observed_mono_ms"],Is.GreaterThan(5000+FrameProbePlan.InjectAfterMs).And.LessThan(5000+FrameProbePlan.ResponseEndMs));
            Assert.That((double)fault["observed_mono_ms"],Is.EqualTo((double)run.result["injected_mono_ms"]));
            Assert.That((double)fault["render_gap_ms"],Is.LessThan(250));Assert.That((bool)fault["watchdog"],Is.False);
            var summary=events.Single(e=>(string)e["kind"]=="summary");Assert.That((bool)summary["capture_complete"],Is.True);Assert.That((double)summary["frame_freeze_ms"],Is.LessThan(250));
            Assert.That((bool)run.result["faulted"],Is.True);Assert.That((string)run.result["injection"],Is.EqualTo("panel_loss"));Assert.That((bool)run.result["physical_qualification"],Is.False);
            Assert.That((bool)run.metadata["requested_panel_loss"],Is.True);Assert.That((int)run.metadata["requested_stall_ms"],Is.Zero);Assert.That((string)run.metadata["injection"],Is.EqualTo("panel_loss"));
            Assert.That(((JArray)run.manifest["files"]).Select(f=>(string)f["path"]),Is.EqualTo(new[]{"metadata.json","frames.csv","events.jsonl"}));Assert.That(run.rows,Is.GreaterThan(600));
        }
        [Test] public void SameTimelineWithoutLossOrStallHasNoFault()
        {
            var plan=FrameProbePlan.Parse(Args("-frameProbeOutput","out","-frameProbeStall","0"));
            var run=Run(plan,false);
            Assert.That(run.events.Select(JObject.Parse).Any(e=>(string)e["kind"]=="fault"),Is.False);
            Assert.That((bool)run.result["faulted"],Is.False);Assert.That((string)run.result["injection"],Is.EqualTo("none"));Assert.That((bool)run.metadata["requested_panel_loss"],Is.False);
        }
    }
}
