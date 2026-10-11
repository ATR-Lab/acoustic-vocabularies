using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;

namespace AcousticVocab.FrameBudget
{
    public sealed class FrameFault : Exception
    {
        public string Code=>Message;
        public FrameFault(string code):base(code!=null&&Regex.IsMatch(code,@"\AFRAME_[A-Z0-9_]+\z")?code:"FRAME_INVALID"){}
    }
    internal static class Check
    {
        internal static void That(bool value,string code="FRAME_INVALID"){if(!value)throw new FrameFault(code);}
        internal static bool Finite(double v)=>!double.IsNaN(v)&&!double.IsInfinity(v)&&v>=0;
        internal static bool Id(string v)=>v!=null&&Regex.IsMatch(v,@"\A[A-Za-z0-9][A-Za-z0-9._-]{0,79}\z");
    }
    public sealed class FrameAttempt
    {
        public string OpportunityId{get;} public string AttemptId{get;}
        public double StartMs{get;} public double EndMs{get;} public int ExpectedCues{get;}
        public FrameAttempt(string opportunity,string attempt,double start,double end,int expectedCues)
        {Check.That(Check.Id(opportunity)&&Check.Id(attempt)&&Check.Finite(start)&&Check.Finite(end)&&end>start&&expectedCues>=0&&expectedCues<=8);OpportunityId=opportunity;AttemptId=attempt;StartMs=start;EndMs=end;ExpectedCues=expectedCues;}
    }
    public sealed class FrameWindow
    {
        public string Id{get;} public string Kind{get;} public double StartMs{get;} public double EndMs{get;}
        public FrameWindow(string id,string kind,double start,double end)
        {Check.That(Check.Id(id)&&(kind=="cue"||kind=="response")&&Check.Finite(start)&&Check.Finite(end)&&end>start);Id=id;Kind=kind;StartMs=start;EndMs=end;}
        public double Overlap(double from,double to)=>Math.Max(0,Math.Min(to,EndMs)-Math.Max(from,StartMs));
    }
    public sealed class RenderSample
    {
        public long FrameIndex{get;} public double? CpuMs{get;} public double? GpuMs{get;}
        public double? RuntimeRefreshHz{get;} public double UnityFixedStepMs{get;} public int PhysicsSteps{get;}
        public int? PresentedCount{get;} public int? DroppedCount{get;}
        public RenderSample(long frame,double? cpu=null,double? gpu=null,double? refresh=null,double fixedStepMs=20,int physicsSteps=0,int? presented=null,int? dropped=null)
        {Check.That(frame>=0&&Check.Finite(fixedStepMs)&&fixedStepMs>0&&physicsSteps>=0);foreach(var n in new[]{cpu,gpu,refresh})Check.That(!n.HasValue||Check.Finite(n.Value));FrameIndex=frame;CpuMs=cpu;GpuMs=gpu;RuntimeRefreshHz=refresh;UnityFixedStepMs=fixedStepMs;PhysicsSteps=physicsSteps;PresentedCount=presented;DroppedCount=dropped;}
    }
    public sealed class FrameInterval
    {
        public FrameAttempt Attempt{get;} public FrameWindow Window{get;} public RenderSample Sample{get;}
        public double FromMs{get;} public double ToMs{get;} public double IntervalMs=>ToMs-FromMs;public double OverlapMs=>Window.Overlap(FromMs,ToMs);
        internal FrameInterval(FrameAttempt attempt,FrameWindow window,RenderSample sample,double from,double to){Attempt=attempt;Window=window;Sample=sample;FromMs=from;ToMs=to;}
    }
    public sealed class FrameFaultRecord
    {
        public FrameAttempt Attempt{get;}public string Code{get;}public double ObservedMs{get;}public double GapMs{get;}public bool Watchdog{get;}
        internal FrameFaultRecord(FrameAttempt attempt,string code,double observed,double gap,bool watchdog){Attempt=attempt;Code=code;ObservedMs=observed;GapMs=gap;Watchdog=watchdog;}
    }
    public sealed class FrameSummary
    {
        public FrameAttempt Attempt{get;}public double ObservedMs{get;}public double? MaximumMs{get;}public int FrameCount{get;}public int WithinBudgetCount{get;}public bool Complete{get;}public bool CancelledBeforeWindow{get;}
        internal FrameSummary(FrameAttempt attempt,double observed,double? maximum,int count,int budget,bool complete,bool cancelledBeforeWindow=false){CancelledBeforeWindow=cancelledBeforeWindow;Attempt=attempt;ObservedMs=observed;MaximumMs=maximum;FrameCount=count;WithinBudgetCount=budget;Complete=complete;}
    }
    public interface IFrameEvidence
    {
        void Interval(FrameInterval interval);
        void Fault(FrameFaultRecord fault);
        void Summary(FrameSummary summary);
    }
    // All calls are serialized internally. Watchdog may latch while the render
    // thread stalls; callbacks exposed to Unity are drained on its owner thread.
    public sealed class FrameMonitor
    {
        sealed class Active
        {internal FrameAttempt Attempt;internal readonly List<FrameWindow> Windows=new List<FrameWindow>();internal int Frames,Budget;internal double Maximum;internal bool BaselineMissing,Cancelled;internal readonly HashSet<string> Faults=new HashSet<string>();}
        readonly object sync=new object();readonly IFrameEvidence evidence;readonly double nominalMs;
        readonly Dictionary<string,Active> active=new Dictionary<string,Active>();readonly HashSet<string> ids=new HashSet<string>();
        readonly Queue<FrameFaultRecord> faults=new Queue<FrameFaultRecord>();readonly Queue<FrameSummary> summaries=new Queue<FrameSummary>();
        double lastClock=-1;double? previous;bool failed;double maximum,lastInterval;bool input=true;long lastFrame=-1;
        public FrameMonitor(double selectedHz,IFrameEvidence evidence)
        {Check.That(Check.Finite(selectedHz)&&selectedHz>0);nominalMs=1000/selectedHz;this.evidence=evidence??throw new ArgumentNullException(nameof(evidence));}
        public bool Healthy{get{lock(sync)return !failed;}}
        public bool HasRenderSample{get{lock(sync)return previous.HasValue;}}
        public double MaximumMs{get{lock(sync)return maximum;}}public double LatestMs{get{lock(sync)return lastInterval;}}public bool InputAvailable{get{lock(sync)return input;}}
        void Clock(double now){if(!Check.Finite(now)||now<lastClock){failed=true;throw new FrameFault("FRAME_CLOCK_INVALID");}lastClock=now;}
        void Record(Action action){try{action();}catch{failed=true;throw new FrameFault("FRAME_LOG_FAILED");}}
        public void Register(FrameAttempt attempt,FrameWindow response,double now)
        {
            lock(sync){Clock(now);Check.That(attempt!=null&&response!=null&&response.Kind=="response"&&now<=response.StartMs&&response.StartMs>=attempt.StartMs&&response.EndMs<=attempt.EndMs&&!failed&&ids.Count<10000&&ids.Add(attempt.AttemptId),"FRAME_ATTEMPT_INVALID");var a=new Active{Attempt=attempt,BaselineMissing=!previous.HasValue};a.Windows.Add(response);active.Add(attempt.AttemptId,a);}
        }
        // A cue anchored at the slot onset arrives as seconds (ms/1000*1000),
        // which can land one ULP before the attempt start (attr-011: onset
        // 1031101.5334 ms, cue 1031101.5333999998 ms). Only a representation
        // difference up to 1 us is snapped onto the attempt bounds.
        public const double RepresentationToleranceMs=.001;
        public void Cue(string attemptId,FrameWindow cue,double now)
        {
            lock(sync)
            {
                Clock(now);Check.That(active.TryGetValue(attemptId,out var a)&&cue!=null&&cue.Kind=="cue","FRAME_CUE_INVALID");
                double start=cue.StartMs<a.Attempt.StartMs&&a.Attempt.StartMs-cue.StartMs<=RepresentationToleranceMs?a.Attempt.StartMs:cue.StartMs;
                double end=cue.EndMs>a.Attempt.EndMs&&cue.EndMs-a.Attempt.EndMs<=RepresentationToleranceMs?a.Attempt.EndMs:cue.EndMs;
                Check.That(now<=start&&start>=a.Attempt.StartMs&&end<=a.Attempt.EndMs&&end>start&&!a.Cancelled&&!a.Windows.Any(w=>w.Id==cue.Id)&&a.Windows.Count(w=>w.Kind=="cue")<a.Attempt.ExpectedCues,"FRAME_CUE_INVALID");
                a.Windows.Add(start==cue.StartMs&&end==cue.EndMs?cue:new FrameWindow(cue.Id,cue.Kind,start,end));
            }
        }
        void Fault(Active a,string code,double now,double gap,bool watchdog)
        {
            failed=true;if(!a.Faults.Add(code))return;var value=new FrameFaultRecord(a.Attempt,code,now,gap,watchdog);faults.Enqueue(value);Record(()=>evidence.Fault(value));
        }
        // A positive overlap charges the full observed interval conservatively;
        // the exact clipped overlap is retained separately. Mere boundary touch is zero.
        public void Render(double now,RenderSample sample,bool responseInputAvailable)
        {
            lock(sync)
            {
                Clock(now);if(sample==null||sample.FrameIndex<=lastFrame){failed=true;throw new FrameFault("FRAME_SEQUENCE_INVALID");}lastFrame=sample.FrameIndex;input=responseInputAvailable;
                if(previous.HasValue)
                {
                    lastInterval=now-previous.Value;
                    foreach(var a in active.Values)
                    {
                        var windows=a.Windows.Where(w=>w.Overlap(previous.Value,now)>0).ToArray();
                        if(windows.Length==0)continue;
                        a.Frames++;a.Maximum=Math.Max(a.Maximum,lastInterval);maximum=Math.Max(maximum,lastInterval);if(lastInterval<=nominalMs*1.5)a.Budget++;
                        foreach(var w in windows)Record(()=>evidence.Interval(new FrameInterval(a.Attempt,w,sample,previous.Value,now)));
                        if(lastInterval>250)Fault(a,"FRAME_FREEZE",now,lastInterval,false);
                        if(!input&&windows.Any(w=>w.Kind=="response"&&now>=w.StartMs&&now<w.EndMs))Fault(a,"FRAME_INTERFACE_UNAVAILABLE",now,lastInterval,false);
                    }
                }
                previous=now;
                foreach(var key in active.Where(p=>now>=p.Value.Attempt.EndMs).Select(p=>p.Key).ToArray())
                {
                    var a=active[key];bool complete=!a.Cancelled&&!a.BaselineMissing&&a.Frames>0&&a.Windows.Count(w=>w.Kind=="cue")==a.Attempt.ExpectedCues;
                    if(!complete)Fault(a,"FRAME_CAPTURE_INCOMPLETE",now,a.Maximum,false);
                    var summary=new FrameSummary(a.Attempt,now,a.Frames>0?(double?)a.Maximum:null,a.Frames,a.Budget,complete);Record(()=>evidence.Summary(summary));summaries.Enqueue(summary);active.Remove(key);
                }
            }
        }
        public void Watchdog(double now)
        {
            lock(sync){Clock(now);if(!previous.HasValue)return;double gap=now-previous.Value;if(gap<=250)return;foreach(var a in active.Values)if(a.Windows.Any(w=>w.Overlap(previous.Value,now)>0)){maximum=Math.Max(maximum,gap);Fault(a,"FRAME_FREEZE",now,gap,true);}}
        }
        public void Cancel(string attemptId,double now,bool cueRequested=true)
        {
            lock(sync)
            {
                Clock(now);if(!active.TryGetValue(attemptId,out var a))return;
                if(!cueRequested&&a.Frames==0&&!a.Windows.Any(w=>w.Kind=="cue")&&now<a.Windows.Min(w=>w.StartMs))
                {
                    // No cue was admitted and no protected window began. Keep
                    // an explicit raw cancellation but permit a fresh planning
                    // attempt for this still-unplayed scheduled opportunity.
                    var cancelled=new FrameSummary(a.Attempt,now,null,0,0,false,true);Record(()=>evidence.Summary(cancelled));summaries.Enqueue(cancelled);active.Remove(attemptId);ids.Remove(attemptId);return;
                }
                a.Cancelled=true;Fault(a,"FRAME_ATTEMPT_INTERRUPTED",now,previous.HasValue?now-previous.Value:0,false);
            }
        }
        public void CloseIncomplete(double now,string code="FRAME_CAPTURE_INCOMPLETE")
        {
            lock(sync){Clock(now);failed=true;foreach(var a in active.Values){Fault(a,code,now,a.Maximum,false);var summary=new FrameSummary(a.Attempt,now,a.Frames>0?(double?)a.Maximum:null,a.Frames,a.Budget,false);Record(()=>evidence.Summary(summary));summaries.Enqueue(summary);}active.Clear();}
        }
        public IReadOnlyList<FrameFaultRecord> DrainFaults(){lock(sync){var result=faults.ToArray();faults.Clear();return result;}}
        public IReadOnlyList<FrameSummary> DrainSummaries(){lock(sync){var result=summaries.ToArray();summaries.Clear();return result;}}
    }
}
