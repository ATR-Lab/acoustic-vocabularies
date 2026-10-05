using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.DataLogging;
using AcousticVocab.OperatorConsole;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.FrameBudget.Tests
{
    public sealed class FrameMonitorTests
    {
        sealed class Memory : IFrameEvidence
        {
            internal readonly List<FrameInterval> Intervals=new List<FrameInterval>();internal readonly List<FrameFaultRecord> Faults=new List<FrameFaultRecord>();internal readonly List<FrameSummary> Summaries=new List<FrameSummary>();internal bool Fail;
            public void Interval(FrameInterval x){if(Fail)throw new IOException("synthetic");Intervals.Add(x);}public void Fault(FrameFaultRecord x)=>Faults.Add(x);public void Summary(FrameSummary x)=>Summaries.Add(x);
        }
        static string Fresh(string name)=>Path.Combine(Environment.GetEnvironmentVariable("FRAME_TEST_ROOT")??Path.Combine(Environment.CurrentDirectory,".local","frame-tests"),name+"-"+Guid.NewGuid().ToString("N"));
        static FrameMonitor Monitor(Memory e,double start=100,double responseEnd=1000,double end=1200,int cues=0)
        {var m=new FrameMonitor(72,e);m.Render(0,new RenderSample(0),true);m.Register(new FrameAttempt("original","attempt",start,end,cues),new FrameWindow("response","response",start,responseEnd),0);return m;}
        [TestCase(200,false)][TestCase(250,false)][TestCase(250.000001,true)][TestCase(300,true)]
        public void StrictThresholdLogsShortStallWithoutFreeze(double gap,bool expected)
        {
            var e=new Memory();var m=Monitor(e);m.Render(gap,new RenderSample(1),true);
            Assert.That(e.Intervals.Single().IntervalMs,Is.EqualTo(gap));Assert.That(e.Faults.Any(f=>f.Code=="FRAME_FREEZE"),Is.EqualTo(expected));Assert.That(m.Healthy,Is.EqualTo(!expected));
        }
        [Test] public void CrossingResponseIntoAcknowledgmentKeepsOldAttemptAndExactOverlap()
        {
            var e=new Memory();var m=new FrameMonitor(72,e);m.Render(900,new RenderSample(0),true);
            m.Register(new FrameAttempt("old","old-attempt",950,1500,0),new FrameWindow("response","response",950,1000),900);
            m.Register(new FrameAttempt("next","next-attempt",1500,2500,0),new FrameWindow("response","response",1500,2200),900);
            m.Render(1200,new RenderSample(1),true);var row=e.Intervals.Single();Assert.That(row.Attempt.AttemptId,Is.EqualTo("old-attempt"));Assert.That(row.IntervalMs,Is.EqualTo(300));Assert.That(row.OverlapMs,Is.EqualTo(50));Assert.That(e.Faults.Single().Code,Is.EqualTo("FRAME_FREEZE"));
        }
        [Test] public void ExactBoundaryTouchDoesNotCountOrFault()
        {var e=new Memory();var m=Monitor(e,start:300);m.Render(300,new RenderSample(1),false);Assert.That(e.Intervals,Is.Empty);Assert.That(e.Faults,Is.Empty);}
        [Test] public void InputLossWithinResponseFaultsWithNormalFrames()
        {var e=new Memory();var m=Monitor(e,start:1);m.Render(14,new RenderSample(1),false);Assert.That(e.Faults.Single().Code,Is.EqualTo("FRAME_INTERFACE_UNAVAILABLE"));Assert.That(e.Faults.Single().GapMs,Is.EqualTo(14));}
        [Test] public void WatchdogLatchesBeforeAnotherRenderAndDoesNotDoubleCountSamples()
        {
            var e=new Memory();var m=Monitor(e);m.Watchdog(251);Assert.That(m.Healthy,Is.False);Assert.That(e.Faults.Single().Watchdog,Is.True);Assert.That(e.Intervals,Is.Empty);
            m.Render(300,new RenderSample(1),true);Assert.That(e.Faults.Count,Is.EqualTo(1));Assert.That(e.Intervals.Single().IntervalMs,Is.EqualTo(300));Assert.That(m.MaximumMs,Is.EqualTo(300));
        }
        [Test] public void FinalLessonCueIsCapturedAfterResponseWindowCloses()
        {
            var e=new Memory();var m=Monitor(e,responseEnd:400,end:1000,cues:1);m.Cue("attempt",new FrameWindow("cue-3","cue",800,900),0);
            for(int i=1;i<=20;i++)m.Render(i*50,new RenderSample(i),true);
            Assert.That(e.Intervals.Any(x=>x.Window.Kind=="cue"&&x.FromMs>=800),Is.True);Assert.That(e.Summaries.Single().Complete,Is.True);
        }
        [Test] public void MissingCueBindingAndMissingBaselineNeverBecomeCompleteEvidence()
        {
            var e=new Memory();var m=new FrameMonitor(72,e);m.Register(new FrameAttempt("a","a",0,100,1),new FrameWindow("r","response",0,50),0);m.Render(100,new RenderSample(0),true);
            Assert.That(e.Summaries.Single().Complete,Is.False);Assert.That(e.Summaries.Single().MaximumMs,Is.Null);Assert.That(e.Faults.Single().Code,Is.EqualTo("FRAME_CAPTURE_INCOMPLETE"));
        }
        [Test] public void LateCueAndDuplicateAttemptAreRejected()
        {var e=new Memory();var m=Monitor(e,cues:1);Assert.Throws<FrameFault>(()=>m.Cue("attempt",new FrameWindow("cue","cue",100,120),101));Assert.Throws<FrameFault>(()=>m.Register(new FrameAttempt("original","attempt",200,300,0),new FrameWindow("r","response",200,250),101));}
        [Test] public void ClockRegressionAndEvidenceFailureLatch()
        {
            var e=new Memory();var m=Monitor(e);Assert.Throws<FrameFault>(()=>m.Watchdog(-1));Assert.That(m.Healthy,Is.False);
            e=new Memory{Fail=true};m=Monitor(e);Assert.That(Assert.Throws<FrameFault>(()=>m.Render(200,new RenderSample(1),true)).Code,Is.EqualTo("FRAME_LOG_FAILED"));Assert.That(m.Healthy,Is.False);
        }
        [Test] public void ThirtySixVirtualTrialsKeepDistinctAttemptsAndBudgetCounts()
        {
            var e=new Memory();var m=new FrameMonitor(72,e);double now=0;long frame=0;m.Render(now,new RenderSample(frame++),true);
            for(int trial=0;trial<36;trial++)
            {
                string id="synthetic-"+trial;double start=now+100,end=start+14000;m.Register(new FrameAttempt(id,id,start,end,0),new FrameWindow("response","response",start,start+12000),now);
                while(now<end){now=Math.Min(now+1000d/72,end);m.Render(now,new RenderSample(frame++),true);}
            }
            Assert.That(e.Summaries.Count,Is.EqualTo(36));Assert.That(e.Summaries.All(x=>x.Complete&&x.FrameCount>800&&x.WithinBudgetCount==x.FrameCount&&x.MaximumMs<14),Is.True);Assert.That(e.Faults,Is.Empty);
            Assert.That(e.Summaries.Select(x=>x.Attempt.AttemptId).Distinct().Count(),Is.EqualTo(36));
        }
        [Test] public void RawEvidenceIsCreateNewAndHashManifestContainsRetainedFiles()
        {
            string path=Fresh("csv");using(var writer=new FrameCsvEvidence(path,new JObject{["qualification"]="synthetic"}))
            {var m=new FrameMonitor(72,writer);m.Render(0,new RenderSample(0),true);m.Register(new FrameAttempt("a","a",1,100,0),new FrameWindow("r","response",1,80),0);for(int i=1;i<=10;i++)m.Render(i*10,new RenderSample(i),true);}
            Assert.That(File.ReadLines(Path.Combine(path,"frames.csv")).Count(),Is.GreaterThan(1));var manifest=JObject.Parse(File.ReadAllText(Path.Combine(path,"manifest.json")));Assert.That(((JArray)manifest["files"]).Count,Is.EqualTo(3));Assert.Throws<FrameFault>(()=>new FrameCsvEvidence(path,new JObject()));
        }
        sealed class FailingCloseStream : FileStream
        {
            internal bool RejectFlush,Closed;
            internal FailingCloseStream(string p):base(p,FileMode.CreateNew,FileAccess.Write,FileShare.Read){}
            public override void Flush(){if(RejectFlush)throw new IOException("synthetic_flush");base.Flush();}
            public override void Flush(bool disk){if(RejectFlush)throw new IOException("synthetic_flush");base.Flush(disk);}
            protected override void Dispose(bool disposing){Closed=true;base.Dispose(disposing);}
        }
        [Test] public void FlushFailureClosesBothStreamsAndNeverPublishesManifest()
        {
            string path=Fresh("close-failure");var streams=new List<FailingCloseStream>();
            var writer=new FrameCsvEvidence(path,new JObject(),p=>{var stream=new FailingCloseStream(p);streams.Add(stream);return stream;});
            streams[0].RejectFlush=true;Assert.Throws<FrameFault>(()=>writer.Dispose());
            Assert.That(streams.All(x=>x.Closed),Is.True);Assert.That(File.Exists(Path.Combine(path,"manifest.json")),Is.False);
            Assert.That(File.Exists(Path.Combine(path,"frames.csv")),Is.True);writer.Dispose();
        }
        static DataIdentity Identity=>new DataIdentity(new string('1',32),"synthetic","visit","station-01","engineering",new string('a',64));
        [Test] public void TrialExportUsesMaximumNotSumAndMissingMeasurementStaysBlank()
        {
            string path=Fresh("data");using(var journal=new DataJournal(path,Identity,new string('2',32),()=>1))
            {
                var c=new EventContext("one","one");journal.Append(DataObservations.Device(c,"frame_freeze",1,durationMs:200));journal.Append(DataObservations.Device(c,"frame_freeze",2,durationMs:300));journal.Append(DataObservations.Device(c,"frame_freeze",3,durationMs:200));
                journal.Append(DataObservations.Device(new EventContext("two","two"),"input",4,value:false,code:"FRAME_INTERFACE_UNAVAILABLE"));
            }
            var rows=DataDeriver.Derive(DataJournal.Verify(path,Identity),Identity).Trials;Assert.That(rows.Single(x=>x["attempt_id"]=="one")["frame_freeze_ms"],Is.EqualTo("300"));Assert.That(rows.Single(x=>x["attempt_id"]=="two")["frame_freeze_ms"],Is.Empty);
        }
        [Test] public void HealthAdapterCannotManufactureReadinessOrHideLatchedFreeze()
        {
            var e=new Memory();var m=Monitor(e);var trusted=new OperatorHealth(true,true,true,true,2,14,14);Assert.That(FrameDataAdapter.Health(trusted,m,false).Ready,Is.False);m.Render(300,new RenderSample(1),true);var health=FrameDataAdapter.Health(trusted,m,true);Assert.That(health.Ready,Is.False);Assert.That(health.MaxGapMs,Is.EqualTo(300));
        }
        [Test] public void QualifiedPlannedCueUsesOnsetAnchorAndNotOffsetScheduledStart()
        {
            var clock=new DspClockMapping();clock.Observe(10,20,10.001,.01);
            var timing=clock.Schedule(10.002,11,new AudioRouteCalibration("synthetic",50,2,new string('a',64)));
            var window=FrameAudioAdapter.PlannedWindow("audio",timing,new FrameCueBinding("attempt",new string('a',64),24000,48000));
            Assert.That(timing.ScheduledMonoSeconds,Is.EqualTo(10.95));Assert.That(window.StartMs,Is.EqualTo(11000));Assert.That(window.EndMs,Is.EqualTo(11500));
            var calibration=clock.Schedule(10.002,11,AudioRouteCalibration.Unmeasured("synthetic"),calibrationOnly:true);
            Assert.Throws<FrameFault>(()=>FrameAudioAdapter.PlannedWindow("audio",calibration,new FrameCueBinding("attempt",new string('a',64),24000,48000)));
        }
        [Test] public void RepeatedRenderSequenceLatchesFailure()
        {var e=new Memory();var m=Monitor(e);Assert.Throws<FrameFault>(()=>m.Render(1,new RenderSample(0),true));Assert.That(m.Healthy,Is.False);}
        [Test] public void OneThrowingFaultSubscriberDoesNotDiscardOtherTypedFaults()
        {
            var raw=new Memory();var monitor=Monitor(raw,start:1);monitor.Render(300,new RenderSample(1),false);
            Assert.That(raw.Faults.Count,Is.EqualTo(2));int calls=0;
            using var journal=new DataJournal(Fresh("dispatch"),Identity,new string('2',32),()=>1);
            var adapter=new FrameDataAdapter(journal,_=>{calls++;if(calls==1)throw new Exception("synthetic");});
            Assert.Throws<FrameFault>(()=>adapter.Drain(monitor));Assert.That(calls,Is.EqualTo(2));Assert.That(journal.Records.Count,Is.EqualTo(2));Assert.That(raw.Faults.Count,Is.EqualTo(2));
        }
        [Test] public void CancellingUnadmittedFutureAttemptKeepsHealthAndEarlierTail()
        {
            var raw=new Memory();var monitor=new FrameMonitor(72,raw);monitor.Render(0,new RenderSample(0),true);
            monitor.Register(new FrameAttempt("old","old",10,1000,0),new FrameWindow("r","response",10,500),0);
            monitor.Register(new FrameAttempt("next","next",1100,2000,0),new FrameWindow("r","response",1100,1900),0);
            monitor.Cancel("next",0,false);Assert.That(monitor.Healthy,Is.True);Assert.That(raw.Summaries.Single().CancelledBeforeWindow,Is.True);
            monitor.Register(new FrameAttempt("next","next",1100,2000,0),new FrameWindow("r","response",1100,1900),0);
            monitor.Render(300,new RenderSample(1),true);Assert.That(raw.Faults.Single().Attempt.AttemptId,Is.EqualTo("old"));
        }
        sealed class Runtime : IDisplayRate
        {internal double Hz=72;internal double[] Rates=new double[]{72,90};internal int Requests;internal bool Accepted=true;public bool Running=>true;public bool TryOffered(out double[] rates){rates=Rates;return rates!=null;}public bool TryCurrent(out double hz){hz=Hz;return true;}public bool Request(double hz){Requests++;return Accepted;}}
        static FrameSetup Setup(string control="request")=>FrameSetup.Load(Encoding.UTF8.GetBytes("{\"version\":1,\"protocol_version\":\"engineering\",\"station_id\":\"station-01\",\"refresh_control\":\""+control+"\",\"watchdog_poll_ms\":10,\"engineering_stall_hook\":false}"),new JObject{["protocol_version"]="engineering",["station_id"]="station-01",["refresh_hz"]=72});
        [Test] public void OfferedRequestNeedsActualConfirmationAndCannotChangeDuringVisit()
        {var r=new Runtime();var p=new RefreshPin(Setup(),r);p.Begin();Assert.That(p.Ready,Is.False);for(int i=0;i<3;i++)p.Observe();Assert.That(p.Ready,Is.True);r.Hz=90;Assert.That(Assert.Throws<FrameFault>(()=>p.Observe()).Code,Is.EqualTo("FRAME_REFRESH_CHANGED"));Assert.That(r.Requests,Is.EqualTo(1));Assert.That(p.Ready,Is.False);}
        [Test] public void MissingOfferedRateOrRejectedRequestNeverPass()
        {var r=new Runtime{Rates=new double[]{90}};Assert.Throws<FrameFault>(()=>new RefreshPin(Setup(),r).Begin());r=new Runtime{Accepted=false};Assert.Throws<FrameFault>(()=>new RefreshPin(Setup(),r).Begin());}
        [Test] public void PinExistingDoesNotChangeRuntimeAndStillRequiresMatchingRate()
        {var r=new Runtime{Rates=null};var p=new RefreshPin(Setup("require_current"),r);p.Begin();for(int i=0;i<3;i++)p.Observe();Assert.That(p.Ready,Is.True);Assert.That(r.Requests,Is.Zero);Assert.That(p.Offered,Is.Empty);}
    }
}
