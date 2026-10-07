using System;
using System.Collections.Generic;
using System.Linq;
using AcousticVocab.SessionEngine;

namespace AcousticVocab.Teaching
{
    public enum LessonHighlight { None, Action, Target }
    public enum LessonDisplayPhase { Hidden, Definition, Retrieval, Feedback, Ended }

    // Private durable handoff to the data logger. No semantic answer is included.
    public sealed class LessonEvent
    {
        public string Kind { get; }
        public string AttemptId { get; }
        public string OpportunityId { get; }
        public string AudioRequestId { get; }
        public int? PresentationIndex { get; }
        public double MonoMs { get; }
        public double? ExpectedMonoMs { get; }
        public string MeaningDisplayId { get; }
        public string FeedbackContentId { get; }
        public string Highlight { get; }
        public string PcmSha256 { get; }
        public string ActionPcmSha256 { get; }
        public string ReferentPcmSha256 { get; }
        internal LessonEvent(string kind,SlotContext c,double now,double? expected=null,int? index=null,string meaning=null,string feedback=null,string highlight=null,string pcm=null,string action=null,string referent=null)
        {Kind=kind;AttemptId=c.Item.TrialId;OpportunityId=c.OpportunityId;MonoMs=now;ExpectedMonoMs=expected;PresentationIndex=index;AudioRequestId=index.HasValue?c.AudioRequestIds[index.Value-1]:null;MeaningDisplayId=meaning;FeedbackContentId=feedback;Highlight=highlight;PcmSha256=pcm;ActionPcmSha256=action;ReferentPcmSha256=referent;}
    }

    // The clock is the same absolute Stopwatch clock used by the session,
    // response panel and audio subsystem. This class never schedules a replay.
    public sealed class LessonTimeline
    {
        readonly SlotContext context;
        readonly bool atomic,aligned;
        readonly int actionSamples,referentSamples;
        readonly Action<LessonEvent> persist;
        readonly Func<double> observedClock;
        readonly string meaning,pcm,actionHash,referentHash;
        readonly double[] offsets;
        readonly bool[] requested=new bool[3],completed=new bool[3];
        readonly double?[] onsets=new double?[3];
        double last=-1,lastRecord=-1;
        bool started,interrupted,responded,resetRequested;
        string feedback;
        public LessonDisplayPhase Phase { get; private set; }=LessonDisplayPhase.Hidden;
        public LessonHighlight Highlight { get; private set; }
        public bool Ended => Phase==LessonDisplayPhase.Ended;
        public bool AllPlaysComplete => completed.All(x=>x);
        public bool ResetMayBegin => resetRequested && (interrupted || AllPlaysComplete);
        public bool Responded => responded;
        public event Action<LessonDisplayPhase,string> DisplayChanged;
        public event Action<LessonHighlight> HighlightChanged;
        public event Action<int,string,double> PlayRequested;
        public LessonTimeline(SlotContext context,bool aligned,int actionSamples,int referentSamples,string meaningDisplayId,string pcmHash,string actionHash,string referentHash,Action<LessonEvent> persist,Func<double> observedClock=null)
        {
            var item=context.Item;
            Require(item!=null && !item.Heldout && item.Phase=="teaching" && item.Plays==3 && context.AudioRequestIds.Count==3,"LESSON_CONTRACT");
            atomic=item.TrialType=="atomic_lesson";
            Require((atomic && item.SlotSeconds==20)||(item.TrialType=="message_lesson" && item.SlotSeconds==24),"LESSON_CONTRACT");
            Require(actionSamples>0 && actionSamples<=48000*4 && (atomic?referentSamples==0:referentSamples>0&&referentSamples<=48000*4),"LESSON_SAMPLES");
            Require((actionSamples+referentSamples+(atomic?0:9600))/48d<=5250,"LESSON_AUDIO_OVERLAPS_BOUNDARY");
            this.context=context;this.aligned=aligned&&!atomic;this.actionSamples=actionSamples;this.referentSamples=referentSamples;
            meaning=meaningDisplayId;pcm=pcmHash;this.actionHash=actionHash;this.referentHash=referentHash;this.persist=persist??throw new ArgumentNullException(nameof(persist));
            this.observedClock=observedClock;
            offsets=atomic?new[]{0d,6000,14000}:new[]{0d,8000,18000};
        }
        internal static void Require(bool condition,string code) { if(!condition)throw new SessionFault(code); }
        void Emit(string kind,double now,double? expected=null,int? index=null,string feedbackId=null,string highlight=null)
        {
            double observed=observedClock?.Invoke()??now;
            Require(!double.IsNaN(observed)&&!double.IsInfinity(observed)&&observed>=now&&observed>=lastRecord,"LESSON_OBSERVATION_CLOCK");
            lastRecord=observed;persist(new LessonEvent(kind,context,observed,expected,index,meaning,feedbackId,highlight,pcm,actionHash,referentHash));
        }
        public void Start(double now)
        {Require(!started&&!interrupted,"LESSON_ALREADY_STARTED");started=true;Tick(now);}
        void Clock(double now)
        {Require(!double.IsNaN(now)&&!double.IsInfinity(now)&&now>=0&&now>=last,"LESSON_CLOCK");last=now;}
        public void Tick(double now)
        {
            Clock(now);if(!started||interrupted||Ended)return;
            for(int i=0;i<3;i++)
            {
                double expected=context.OnsetMonoMs+offsets[i];
                if(!requested[i]&&now>=expected-750)
                {
                    Require(now<=expected-150 && (i==0||completed[i-1]),"LESSON_PLAY_DEADLINE");
                    Emit("play_request",now,expected,i+1);requested[i]=true;
                    PlayRequested?.Invoke(i+1,context.AudioRequestIds[i],expected);
                }
            }
            double elapsed=now-context.OnsetMonoMs;
            var phase=elapsed<0?LessonDisplayPhase.Hidden:elapsed<offsets[1]?LessonDisplayPhase.Definition:
                elapsed<offsets[2]?LessonDisplayPhase.Retrieval:elapsed<context.Item.SlotSeconds*1000?LessonDisplayPhase.Feedback:LessonDisplayPhase.Ended;
            if(phase==LessonDisplayPhase.Feedback)Require(feedback!=null,"LESSON_FEEDBACK_MISSING");
            if(phase!=Phase){Require((int)phase==(int)Phase+1,"LESSON_DISPLAY_BOUNDARY_MISSED");SetPhase(phase,now);}
            UpdateHighlight(now);
            // No nominal fallback: the first callback must provide qualified
            // onset authority. The hardware uncertainty target stays separate.
            for(int i=0;i<3;i++)if(requested[i]&&!onsets[i].HasValue&&now>context.OnsetMonoMs+offsets[i]+250)
                throw new SessionFault("LESSON_ONSET_EVIDENCE_MISSING");
            if(Ended)Require(AllPlaysComplete,"LESSON_PLAY_INCOMPLETE");
        }
        void SetPhase(LessonDisplayPhase phase,double now)
        {
            var previous=Phase;
            // Intent is durable before changing the view. Success records mean
            // the software visibility call returned; HMD scanout is unmeasured.
            Emit("display_request",now,feedbackId:phase==LessonDisplayPhase.Feedback?feedback:null);
            Phase=phase;
            DisplayChanged?.Invoke(phase,phase==LessonDisplayPhase.Feedback?feedback:null);
            if(previous==LessonDisplayPhase.Definition||previous==LessonDisplayPhase.Feedback)Emit("display_end",now,previous==LessonDisplayPhase.Definition?context.OnsetMonoMs+offsets[1]:context.EndMonoMs,feedbackId:previous==LessonDisplayPhase.Feedback?feedback:null);
            if(phase==LessonDisplayPhase.Definition||phase==LessonDisplayPhase.Feedback)Emit("display_start",now,phase==LessonDisplayPhase.Definition?context.OnsetMonoMs:context.OnsetMonoMs+offsets[2],feedbackId:phase==LessonDisplayPhase.Feedback?feedback:null);
            if(phase==LessonDisplayPhase.Retrieval)Emit("retrieval_opportunity",now,context.OnsetMonoMs+offsets[1]);
            if(phase==LessonDisplayPhase.Ended)Emit(interrupted?"lesson_interrupted":"lesson_end",now,context.EndMonoMs);
        }
        void UpdateHighlight(double now)
        {
            LessonHighlight next=LessonHighlight.None;double? edge=null;
            if(aligned&&(Phase==LessonDisplayPhase.Definition||Phase==LessonDisplayPhase.Feedback))
            {
                int i=Phase==LessonDisplayPhase.Definition?0:2;
                if(onsets[i].HasValue)
                {
                    double start=onsets[i].Value,aEnd=start+actionSamples/48d,rStart=aEnd+200,rEnd=rStart+referentSamples/48d;
                    if(now>=start&&now<aEnd){next=LessonHighlight.Action;edge=start;}
                    else if(now>=rStart&&now<rEnd){next=LessonHighlight.Target;edge=rStart;}
                    else if(now>=rEnd)edge=rEnd;else if(now>=aEnd)edge=aEnd;
                }
            }
            if(next!=Highlight)
            {Emit("highlight_request",now,edge,highlight:next.ToString().ToLowerInvariant());Highlight=next;HighlightChanged?.Invoke(next);Emit("highlight",now,edge,highlight:next.ToString().ToLowerInvariant());}
        }
        public void Onset(string audioId,double onsetMonoMs,double uncertaintyMs,double now)
        {
            Clock(now);int i=Array.IndexOf(context.AudioRequestIds.ToArray(),audioId);
            Require(!interrupted&&i>=0&&requested[i]&&!onsets[i].HasValue&&!double.IsNaN(onsetMonoMs)&&!double.IsInfinity(onsetMonoMs)&&onsetMonoMs>=0&&
                !double.IsNaN(uncertaintyMs)&&!double.IsInfinity(uncertaintyMs)&&uncertaintyMs>=0&&uncertaintyMs<=20,"LESSON_ONSET_AUTHORITY");
            Require(Math.Abs(onsetMonoMs-(context.OnsetMonoMs+offsets[i]))<=20,"LESSON_ONSET_DRIFT");
            onsets[i]=onsetMonoMs;Emit("onset_authority",now,onsetMonoMs,i+1);UpdateHighlight(now);
        }
        public void Completed(string audioId,double now)
        {Clock(now);int i=Array.IndexOf(context.AudioRequestIds.ToArray(),audioId);Require(i>=0&&requested[i]&&onsets[i].HasValue&&!completed[i],"LESSON_COMPLETION_INVALID");completed[i]=true;Emit("play_complete",now,index:i+1);}
        public void Response(string feedbackContentId,double now,bool timeout=false)
        {Clock(now);double deadline=context.OnsetMonoMs+(atomic?13000:17000);Require(!interrupted&&!responded&&now>=context.OnsetMonoMs+offsets[1]&&(timeout?now>=deadline&&now<context.OnsetMonoMs+offsets[2]:now<deadline)&&!string.IsNullOrEmpty(feedbackContentId),"LESSON_RESPONSE_REFUSED");responded=true;feedback=feedbackContentId;Emit("retrieval_result",now,feedbackId:feedback);}
        public void RequestReset() {resetRequested=true;}
        public void Interrupt(double now)
        {
            Clock(now);if(interrupted)return;interrupted=true;SetPhase(LessonDisplayPhase.Ended,now);
            if(Highlight!=LessonHighlight.None){Highlight=LessonHighlight.None;Emit("highlight",now,highlight:"none");HighlightChanged?.Invoke(Highlight);}
        }
    }
}
