using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using AcousticVocab.FrameBudget;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using NUnit.Framework;

namespace AcousticVocab.SessionIntegration.Tests
{
    // Native attempt simulation-test-B-V1-020-stall-011, message lesson 3: the
    // cue anchored at the slot onset (1,031,101.5334 ms) reached the frame
    // monitor as seconds*1000 = 1,031,101.5333999998 ms, one ULP before the
    // attempt start, while lesson 2's tail attempt was still open. The monitor
    // refused it inside the AUDIO_REQUESTED sink; the engine reported only
    // SESSION_CONTENT_FAILED and the frame attempt was interrupted.
    public sealed class FrameCueRepresentationTests
    {
        static T New<T>(params object[] a)=>(T)Activator.CreateInstance(typeof(T),BindingFlags.Instance|BindingFlags.NonPublic,null,a,null);
        sealed class Clock:ISessionClock{internal double Time;public double NowMs=>Time;}
        sealed class Journal:ISessionJournal{internal readonly List<SessionRecord> Rows=new List<SessionRecord>();public IReadOnlyList<SessionRecord> Records=>Rows;public void Append(SessionRecord r)=>Rows.Add(r);}
        sealed class Capture:IFrameCapture,IFrameEvidence
        {
            readonly Clock clock;internal readonly FrameMonitor Monitor;internal readonly List<FrameSummary> Summaries=new List<FrameSummary>();internal readonly List<string> Faults=new List<string>();long frame;
            internal Capture(Clock clock){this.clock=clock;Monitor=new FrameMonitor(72,this);Monitor.Render(clock.Time,new RenderSample(frame++),true);}
            internal void Render(){Monitor.Render(clock.Time,new RenderSample(frame++),true);}
            public bool Ready=>Monitor.Healthy;public void Drain(){}
            // Same attempt/response construction as FrameCaptureHost.Register.
            public void Register(SlotContext c)=>Monitor.Register(new FrameAttempt(c.OpportunityId,c.Item.TrialId,c.OnsetMonoMs,c.EndMonoMs,c.Item.Plays),new FrameWindow("response","response",c.OnsetMonoMs+c.Item.ChoiceOpensSeconds*1000,c.OnsetMonoMs+c.Item.ChoiceClosesSeconds*1000),clock.Time);
            public void Cancel(string id,bool requested=true)=>Monitor.Cancel(id,clock.Time,requested);
            public void Interval(FrameInterval x){}public void Fault(FrameFaultRecord x)=>Faults.Add(x.Attempt.AttemptId+":"+x.Code);public void Summary(FrameSummary x)=>Summaries.Add(x);
        }
        sealed class Lessons:ISlotContentFactory
        {
            readonly Clock clock;readonly Capture capture;internal readonly List<FrameWindow> Cues=new List<FrameWindow>();internal Func<SlotContext,Exception> Throw;
            internal Lessons(Clock clock,Capture capture){this.clock=clock;this.capture=capture;}
            public ISlotContent Create(SlotItem item)=>new Content(this);
            sealed class Content:ISlotContent
            {
                readonly Lessons owner;SlotContext context;bool reset;
                internal Content(Lessons owner){this.owner=owner;}
                public void Prepare(SlotContext c)=>context=c;
                public SlotReadiness Readiness=>new SlotReadiness(true,true,true,true,true,true,true,true);
                public bool ResetComplete=>reset;
                // The real path: the audio player schedules in seconds and the
                // durable AUDIO_REQUESTED sink registers the planned cue window.
                public void RequestCue(SlotContext c,INovelSlotAuthorization permit)
                {
                    var error=owner.Throw?.Invoke(c);if(error!=null)throw error;
                    double nowSeconds=owner.clock.Time/1000;var mapping=new DspClockMapping();mapping.Observe(nowSeconds,nowSeconds,nowSeconds+.0005,.01);
                    var timing=mapping.Schedule(nowSeconds+.001,c.OnsetMonoMs/1000,new AudioRouteCalibration("synthetic",50,2,new string('a',64)));
                    var cue=FrameAudioAdapter.PlannedWindow(c.AudioRequestIds[0],timing,new FrameCueBinding(c.Item.TrialId,new string('a',64),48000,48000));
                    owner.Cues.Add(cue);owner.capture.Monitor.Cue(c.Item.TrialId,cue,owner.clock.Time);
                }
                public void OpenResponse(SlotContext c){}public void CloseResponse(SlotContext c){}public void RequestReset(SlotContext c)=>reset=true;public void Interrupt(string code){}
            }
        }
        sealed class Fixture
        {
            internal readonly Clock Clock=new Clock();internal readonly Journal Journal=new Journal();internal readonly Capture Capture;internal readonly Lessons Lessons;internal readonly FixedSlotEngine Engine;
            internal Fixture()
            {
                // Resume time chosen so lesson 3's onset is exactly 1,031,101.5334 ms.
                Clock.Time=1006351.5334;Capture=new Capture(Clock);Lessons=new Lessons(Clock,Capture);
                SlotItem Item(string id)=>New<SlotItem>(id,"message_lesson","K-a1-r1",null,"lesson","lesson",false,24,1,1);
                var schedule=New<VisitSchedule>(new string('a',64),new string('b',64),"synthetic","V1",true,new[]{New<ScheduleBlock>("message_lessons",new[]{Item("B-C01-M1-V1-ML-02"),Item("B-C01-M1-V1-ML-03")})},"B","S",null);
                Engine=new FixedSlotEngine(schedule,Clock,Journal,new FrameContentFactory(Lessons,Capture));Engine.ConfirmResume();
            }
            internal void Run(double until){while(Clock.Time<until&&Engine.Status==SessionState.Running){Clock.Time=Math.Min(until,Clock.Time+50);Capture.Render();Engine.Tick();}}
        }
        [Test]public void OnsetCueOneUlpBeforeAttemptStartIsAdmittedWhilePreviousTailIsOpen()
        {
            var f=new Fixture();f.Run(1031101.5334-700);
            Assert.That(f.Engine.CurrentTrialId,Is.EqualTo("B-C01-M1-V1-ML-03"));Assert.That(f.Engine.CurrentState,Is.EqualTo(ItemState.CueRequested));
            var cue=f.Lessons.Cues.Last();Assert.That(cue.StartMs,Is.LessThan(1031101.5334),"Reproduces the native ms->s->ms representation");
            Assert.That(f.Capture.Summaries.Any(x=>x.Attempt.AttemptId=="B-C01-M1-V1-ML-02"),Is.False,"Lesson 2's attempt is still open (overlap)");
            Assert.That(f.Journal.Rows.Where(x=>x.Event=="item_fault"),Is.Empty);Assert.That(f.Capture.Faults,Is.Empty);
            f.Run(1031101.5334+24000+100);
            Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Complete));Assert.That(f.Journal.Rows.Where(x=>x.Event=="item_fault"),Is.Empty);
            Assert.That(f.Capture.Summaries.Select(x=>(x.Attempt.AttemptId,x.Complete)),Is.EquivalentTo(new[]{("B-C01-M1-V1-ML-02",true),("B-C01-M1-V1-ML-03",true)}));
        }
        [Test]public void CueOutsideTheRepresentationToleranceIsStillRefused()
        {
            var events=new List<FrameSummary>();var monitor=new FrameMonitor(72,new Sink());monitor.Render(0,new RenderSample(0),true);
            monitor.Register(new FrameAttempt("one","one",1000,25000,1),new FrameWindow("response","response",9000,18000),0);
            Assert.Throws<FrameFault>(()=>monitor.Cue("one",new FrameWindow("a","cue",1000-FrameMonitor.RepresentationToleranceMs*2,2000),1));
            Assert.Throws<FrameFault>(()=>monitor.Cue("one",new FrameWindow("b","cue",24000,25000+FrameMonitor.RepresentationToleranceMs*2),1));
            Assert.DoesNotThrow(()=>monitor.Cue("one",new FrameWindow("c","cue",1000-FrameMonitor.RepresentationToleranceMs/2,2000),1));
        }
        sealed class Sink:IFrameEvidence{public void Interval(FrameInterval x){}public void Fault(FrameFaultRecord x){}public void Summary(FrameSummary x){}}
        [TestCase("frame",typeof(FrameFault),"FRAME_CUE_INVALID","FrameFault:FRAME_CUE_INVALID")]
        [TestCase("private_text",typeof(InvalidOperationException),"C:\\private\\path failed","InvalidOperationException")]
        public void UntypedContentExceptionIsRecordedWithOnlyItsBoundedCode(string name,Type type,string message,string expected)
        {
            var f=new Fixture();f.Lessons.Throw=c=>c.Item.TrialId.EndsWith("ML-03")?(Exception)Activator.CreateInstance(type,message):null;
            f.Run(1031101.5334-700);
            Assert.That(f.Journal.Rows.First(x=>x.Event=="item_fault").TechnicalFaultCode,Is.EqualTo("SESSION_CONTENT_FAILED"));
            Assert.That(f.Engine.ContentFailureDetail,Is.EqualTo(expected));
        }
    }
}
