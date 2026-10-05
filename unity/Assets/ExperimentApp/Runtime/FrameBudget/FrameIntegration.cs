using System;
using System.Linq;
using AcousticVocab.DataLogging;
using AcousticVocab.OperatorConsole;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;

namespace AcousticVocab.FrameBudget
{
    // Main-thread adapter. The raw monitor may latch on its watchdog thread,
    // but the append-only study journal and engine are touched only by Drain.
    public sealed class FrameDataAdapter
    {
        readonly DataJournal journal;readonly Action<string> fault;
        public FrameDataAdapter(DataJournal journal,Action<string> engineFault){this.journal=journal??throw new ArgumentNullException(nameof(journal));fault=engineFault??throw new ArgumentNullException(nameof(engineFault));}
        public void Drain(FrameMonitor monitor)
        {
            Exception first=null;
            // Raw evidence has already durably flushed every queued fault.
            // Attempt all typed records and notifications even after one sink
            // fails; the owner then latches and marks the typed prefix incomplete.
            foreach(var f in monitor.DrainFaults())
            {
                try{journal.Append(DataObservations.Device(new EventContext(f.Attempt.OpportunityId,f.Attempt.AttemptId),f.Code=="FRAME_INTERFACE_UNAVAILABLE"?"input":"frame_freeze",f.ObservedMs,value:f.Code=="FRAME_INTERFACE_UNAVAILABLE"?(bool?)false:null,durationMs:f.Code=="FRAME_FREEZE"?(double?)f.GapMs:null,code:f.Code));}catch(Exception e){first??=e;}
                try{fault(f.Code);}catch(Exception e){first??=e;}
            }
            foreach(var s in monitor.DrainSummaries())
                try{journal.Append(DataObservations.Device(new EventContext(s.Attempt.OpportunityId,s.Attempt.AttemptId),"frame_freeze",s.ObservedMs,durationMs:s.MaximumMs,code:s.Complete||s.CancelledBeforeWindow?null:"FRAME_CAPTURE_INCOMPLETE"));}catch(Exception e){first??=e;}
            if(first!=null)throw new FrameFault("FRAME_DISPATCH_FAILED");
        }
        public static OperatorHealth Health(OperatorHealth trusted,FrameMonitor monitor,bool rateReady)
        {
            if(trusted==null||monitor==null)throw new ArgumentNullException();
            bool ready=rateReady&&monitor.Healthy&&monitor.HasRenderSample;
            return new OperatorHealth(trusted.Headset&&ready,trusted.Audio,trusted.Reset,trusted.Input&&monitor.InputAvailable&&ready,trusted.BridgeAgeMs,monitor.LatestMs,Math.Max(trusted.MaxGapMs,monitor.MaximumMs));
        }
    }
    public sealed class FrameCueBinding
    {
        public string AttemptId{get;}public string PcmSha256{get;}public int SampleCount{get;}public int SampleRate{get;}
        public FrameCueBinding(string attempt,string pcmHash,int samples,int sampleRate)
        {Check.That(Check.Id(attempt)&&pcmHash!=null&&System.Text.RegularExpressions.Regex.IsMatch(pcmHash,@"\A[0-9a-f]{64}\z")&&samples>0&&sampleRate>0);AttemptId=attempt;PcmSha256=pcmHash;SampleCount=samples;SampleRate=sampleRate;}
    }
    // Compose this call into the existing durable audio sink. The caller resolves
    // PCM length from the verified loaded wave; no sound is opened by this adapter.
    public sealed class FrameAudioAdapter
    {
        readonly FrameCaptureHost host;readonly Func<AudioPlaybackEvent,FrameCueBinding> resolve;
        public FrameAudioAdapter(FrameCaptureHost host,Func<AudioPlaybackEvent,FrameCueBinding> resolve){this.host=host??throw new ArgumentNullException(nameof(host));this.resolve=resolve??throw new ArgumentNullException(nameof(resolve));}
        public static FrameWindow PlannedWindow(string audioId,AudioScheduleTiming timing,FrameCueBinding binding)
        {
            Check.That(timing!=null&&!timing.CalibrationOnly&&timing.OnsetEstimateMonoSeconds.HasValue&&binding!=null,"FRAME_AUDIO_BINDING");
            // The protected window follows the qualified planned onset anchor.
            // ScheduledMonoSeconds precedes it by the measured route offset.
            double start=timing.OnsetEstimateMonoSeconds.Value*1000;
            return new FrameWindow(audioId,"cue",start,start+binding.SampleCount*1000d/binding.SampleRate);
        }
        public void Record(AudioPlaybackEvent value)
        {
            if(value.Code!="AUDIO_REQUESTED")return;var b=resolve(value);Check.That(b!=null&&b.PcmSha256==value.PcmSha256,"FRAME_AUDIO_BINDING");
            host.RegisterCue(b.AttemptId,PlannedWindow(value.AudioId,value.Timing,b));
        }
    }
    // Wrap the trusted factory/multiplexer, not the engine's current trial ID.
    // Existing slot timing supplies response windows; each actual audio request
    // separately supplies its verified duration, including a lesson's final cue.
    public interface IFrameCapture
    {bool Ready{get;}void Drain();void Register(SlotContext context);void Cancel(string attempt,bool cueRequested=true);}
    public sealed class FrameContentFactory : ISlotContentFactory,ISessionContentPump,ISlotStartPlan
    {
        readonly ISlotContentFactory inner;readonly IFrameCapture host;
        public FrameContentFactory(ISlotContentFactory inner,IFrameCapture host){this.inner=inner??throw new ArgumentNullException(nameof(inner));this.host=host??throw new ArgumentNullException(nameof(host));}
        public ISlotContent Create(SlotItem item)=>new Content(inner.Create(item),host);
        public double MinimumGapBeforeMs(SlotItem item,double baseOnsetMonoMs)=>inner is ISlotStartPlan plan?plan.MinimumGapBeforeMs(item,baseOnsetMonoMs):0;
        public void Pump(){host.Drain();if(inner is ISessionContentPump pump)pump.Pump();}
        sealed class Content : ISlotContent
        {
            readonly ISlotContent inner;readonly IFrameCapture host;SlotContext context;bool requested;
            internal Content(ISlotContent inner,IFrameCapture host){this.inner=inner??throw new ArgumentNullException(nameof(inner));this.host=host;}
            public void Prepare(SlotContext c){context=c;host.Register(c);inner.Prepare(c);}
            public SlotReadiness Readiness{get{var r=inner.Readiness;return new SlotReadiness(r.HashVerified,r.AudioPreloaded,r.ResetAcknowledged,r.RendererReady&&host.Ready,r.PanelIdle,r.FocusOk,r.InputOk&&host.Ready,r.ModeAcknowledged);}}
            public bool ResetComplete=>inner.ResetComplete;
            public void RequestCue(SlotContext c,INovelSlotAuthorization permit){Check.That(host.Ready,"FRAME_CAPTURE_UNAVAILABLE");requested=true;inner.RequestCue(c,permit);}
            public void OpenResponse(SlotContext c)=>inner.OpenResponse(c);
            public void CloseResponse(SlotContext c)=>inner.CloseResponse(c);
            public void RequestReset(SlotContext c)=>inner.RequestReset(c);
            public void Interrupt(string code){try{if(context.Item!=null)host.Cancel(context.Item.TrialId,requested);}finally{inner.Interrupt(code);}}
        }
    }
}
