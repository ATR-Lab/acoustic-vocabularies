using System;
using System.Collections.Generic;
using System.Linq;
using AcousticVocab.ResponsePanel;
using AcousticVocab.SessionEngine;
using AcousticVocab.StateIntegration;
using AcousticVocab.StudyAudio;

namespace AcousticVocab.Teaching
{
    // Control receipt and renderer verification are independent authorities.
    // The implementation must bind the exact private control session/request,
    // reject stale health, and never infer an acknowledgement from a frame.
    public interface ITeachingBackend
    {
        void Pump();
        void RequestTeachingMode();
        bool TeachingModeAcknowledged { get; }
        bool NeutralHoldHealthy { get; }
        string RequestReset();
        bool ResetAcknowledged(string exactRequestId);
        void Interrupt();
    }
    public interface ITeachingView
    {
        void Prepare(TeachingDisplay display);
        void Show(string owner,TeachingDisplay display,string feedbackText);
        void Hide(string owner);
        void Highlight(string owner,LessonHighlight highlight);
    }
    public sealed class TeachingContentFactory : ISlotContentFactory,ISessionContentPump,IDisposable
    {
        readonly TeachingCatalog catalog;readonly ITeachingSelections selections;readonly ITeachingBackend backend;
        readonly AudioPlayer audio;readonly ResponsePanelController panel;readonly StateSourceHost source;readonly ITeachingView view;
        readonly Func<bool> focused;readonly Action<LessonEvent> persist;readonly Action<AudioPlaybackEvent> audioPersist;readonly Action<string> responseSink,faultSink;
        readonly List<Content> live=new List<Content>();
        bool disposed;
        public TeachingContentFactory(TeachingCatalog catalog,ITeachingSelections selections,ITeachingBackend backend,AudioPlayer audio,
            ResponsePanelController panel,StateSourceHost source,ITeachingView view,Func<bool> focused,Action<LessonEvent> persist,
            Action<AudioPlaybackEvent> audioPersist,Action<string> responseSink,Action<string> faultSink)
        {
            this.catalog=catalog??throw new ArgumentNullException(nameof(catalog));this.selections=selections;this.backend=backend??throw new ArgumentNullException(nameof(backend));
            this.audio=audio??throw new ArgumentNullException(nameof(audio));this.panel=panel??throw new ArgumentNullException(nameof(panel));this.source=source??throw new ArgumentNullException(nameof(source));
            this.view=view??throw new ArgumentNullException(nameof(view));this.focused=focused??throw new ArgumentNullException(nameof(focused));this.persist=persist??throw new ArgumentNullException(nameof(persist));
            this.audioPersist=audioPersist??throw new ArgumentNullException(nameof(audioPersist));this.responseSink=responseSink??throw new ArgumentNullException(nameof(responseSink));this.faultSink=faultSink??throw new ArgumentNullException(nameof(faultSink));
            audio.Event+=AudioEvent;panel.Responded+=PanelResponse;panel.Faulted+=PanelFault;
            if(!backend.TeachingModeAcknowledged)backend.RequestTeachingMode();
        }
        public bool ExposureGate => !disposed&&focused()&&panel.ReadyForTrial&&source.CheckExposureReady()&&backend.TeachingModeAcknowledged&&backend.NeutralHoldHealthy;
        public ISlotContent Create(SlotItem item)
        {LessonTimeline.Require(!disposed,"LESSON_DISPOSED");catalog.Validate(item);var content=new Content(this,catalog.Prepare(item,selections));live.Add(content);return content;}
        public void Tick()
        {
            if(disposed)return;
            try
            {
                backend.Pump();
                // Timeouts must reach the engine before it closes this frame's
                // response boundary. All clocks use AudioPlayer.Now/Stopwatch.
                panel.State?.Tick();
                double now=AudioPlayer.Now*1000;
                foreach(var item in live.ToArray())item.Tick(now);
                live.RemoveAll(x=>x.Retired);
            }
            catch(SessionFault error){faultSink(error.Code);}catch(AudioFault error){faultSink(error.Code);}catch(ControlFault error){faultSink(error.Code);}catch{faultSink("LESSON_INTEGRATION_FAILED");}
        }
        public void Pump()=>Tick();
        void AudioEvent(AudioPlaybackEvent value)
        {
            // Persist every request/observation before allowing the player to
            // continue. An exception prevents scheduling or aborts the ticket.
            audioPersist(value);
            var content=live.SingleOrDefault(x=>x.Context.AudioRequestIds?.Contains(value.AudioId)==true);
            if(content==null)throw new SessionFault("LESSON_AUDIO_CONTEXT");
            if(value.Code=="AUDIO_REQUESTED")return;
            if(value.Code=="AUDIO_ONSET_ESTIMATED")
            {
                LessonTimeline.Require(!value.Timing.CalibrationOnly&&value.CallbackCount>0&&value.Timing.OnsetEstimateMonoSeconds.HasValue&&value.Timing.OnsetUncertaintyMs.HasValue&&value.Timing.RouteOffsetMs.HasValue,"LESSON_ONSET_AUTHORITY");
                content.Timeline.Onset(value.AudioId,value.Timing.OnsetEstimateMonoSeconds.Value*1000,value.Timing.OnsetUncertaintyMs.Value,value.ObservedMonoSeconds*1000);
            }
            else if(value.Code=="AUDIO_PLAYBACK_COMPLETED")content.Timeline.Completed(value.AudioId,value.ObservedMonoSeconds*1000);
            else {content.Interrupt(value.Code);faultSink(value.Code);}
        }
        void PanelResponse(PanelResponse value)
        {
            var content=live.SingleOrDefault(x=>x.Context.Item?.TrialId==value.Request.TrialId);
            if(content==null)throw new SessionFault("LESSON_RESPONSE_CONTEXT");
            content.Timeline.Response(catalog.Feedback(content.Material,value),value.ResponseMonoMs,value.Code==ResponseCode.Timeout);
            responseSink(value.Code==ResponseCode.Commit?"commit":value.Code==ResponseCode.DontKnow?"dont_know":"timeout");
        }
        void PanelFault(string _) => faultSink("LESSON_INPUT_LOST");
        public void Dispose()
        {
            if(disposed)return;
            // Abort while the durable sink and ticket owner still exist.
            foreach(var item in live.ToArray())item.Interrupt("LESSON_SHUTDOWN");
            disposed=true;audio.Event-=AudioEvent;panel.Responded-=PanelResponse;panel.Faulted-=PanelFault;live.Clear();
        }
        sealed class Content : ISlotContent
        {
            readonly TeachingContentFactory owner;
            internal readonly LessonMaterial Material;
            internal SlotContext Context;
            internal LessonTimeline Timeline;
            string initialReset,finalReset;bool prepared,resetWanted,interrupted,interrupting,initialRenderer,finalRenderer;
            internal Content(TeachingContentFactory owner,LessonMaterial material){this.owner=owner;Material=material;}
            public void Prepare(SlotContext context)
            {
                LessonTimeline.Require(!prepared,"LESSON_ALREADY_PREPARED");Context=context;
                // Prepare has no display side effect: the preceding item's
                // presentation owns its view through its 20/24-second end.
                owner.view.Prepare(Material.Display);
                owner.audio.Preload(context.AudioRequestIds.ToDictionary(x=>x,x=>Material.Wave),16*1024*1024);
                Timeline=new LessonTimeline(context,Material.Aligned,Material.Action.SampleCount,Material.Referent?.SampleCount??0,
                    Material.Display.MeaningDisplayId,Material.Wave.PcmSha256,Material.Atomic?null:Material.Action.PcmSha256,Material.Referent?.PcmSha256,owner.persist,()=>AudioPlayer.Now*1000);
                Timeline.PlayRequested+=(index,id,time)=>owner.audio.Schedule(id,time/1000);
                Timeline.DisplayChanged+=(phase,feedback)=>
                {if(phase==LessonDisplayPhase.Definition||phase==LessonDisplayPhase.Feedback)owner.view.Show(Context.Item.TrialId,Material.Display,feedback==null?null:owner.catalog.FeedbackText(feedback));else owner.view.Hide(Context.Item.TrialId);};
                Timeline.HighlightChanged+=highlight=>owner.view.Highlight(Context.Item.TrialId,highlight);
                initialReset=owner.backend.RequestReset();prepared=true;
            }
            internal bool Retired => Timeline!=null&&Timeline.Ended&&finalRenderer;
            public SlotReadiness Readiness => new SlotReadiness(prepared,!interrupted&&prepared&&(owner.audio.TrialReady||owner.audio.Playing),initialReset!=null&&owner.backend.ResetAcknowledged(initialReset),
                initialRenderer&&owner.source.CheckExposureReady(),owner.panel.State!=null&&(owner.panel.State.Request==null||owner.panel.State.Locked),owner.focused(),owner.panel.ReadyForTrial,owner.backend.TeachingModeAcknowledged&&owner.backend.NeutralHoldHealthy);
            public bool ResetComplete => finalReset!=null&&owner.backend.ResetAcknowledged(finalReset)&&finalRenderer&&owner.source.CheckExposureReady();
            public void RequestCue(SlotContext context,INovelSlotAuthorization authorization)
            {LessonTimeline.Require(authorization==null&&!interrupted&&Readiness.Ready,"LESSON_CUE_REFUSED");Timeline.Start(AudioPlayer.Now*1000);}
            public void OpenResponse(SlotContext context)
            {
                if(interrupted)return;
                owner.panel.Open(new PanelRequest(context.Item.TrialId,Material.Atomic?PanelMode.LessonAtomic:PanelMode.LessonMessage,
                    Material.Atomic?(context.Item.Role=="action"?PanelRole.Action:PanelRole.Target):PanelRole.Command,context.OnsetMonoMs));
            }
            public void CloseResponse(SlotContext context){owner.panel.CloseAtBoundary();}
            public void RequestReset(SlotContext context){resetWanted=true;Timeline?.RequestReset();}
            internal void Tick(double now)
            {
                if(!initialRenderer&&initialReset!=null&&owner.backend.ResetAcknowledged(initialReset))initialRenderer=owner.source.ConfirmReset();
                Timeline?.Tick(now);
                if(resetWanted&&finalReset==null&&Timeline!=null&&Timeline.ResetMayBegin&&!owner.audio.Playing)finalReset=owner.backend.RequestReset();
                if(!finalRenderer&&finalReset!=null&&owner.backend.ResetAcknowledged(finalReset))finalRenderer=owner.source.ConfirmReset();
            }
            public void Interrupt(string boundedCode)
            {
                if(interrupting||interrupted)return;interrupting=true;interrupted=true;
                try{Timeline?.Interrupt(AudioPlayer.Now*1000);owner.view.Hide(Context.Item.TrialId);owner.panel.CloseAtBoundary();owner.audio.Abort(boundedCode);owner.backend.Interrupt();}
                finally{interrupting=false;}
            }
        }
    }
}
