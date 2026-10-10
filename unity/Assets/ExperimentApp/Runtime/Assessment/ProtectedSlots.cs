using System;
using System.Collections.Generic;
using System.Linq;
using AcousticVocab.ResponsePanel;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.Assessment
{
    public interface IProtectedState
    {
        void BeginTrial();
        void RequestReset();
        void Pump();
        bool ModeReady { get; }
        bool NeutralReady { get; }
        bool ResetComplete { get; }
        bool FocusOk { get; }
    }
    // Optional non-pumping readiness evidence, captured only on failure.
    public interface IProtectedStateDiagnostics { JObject Readiness(); }
    public interface IAssessmentAudio
    {
        void Prepare(SlotContext context);
        bool Ready { get; }
        double? QualifiedOnsetMonoMs { get; }
        string FaultCode { get; }
        void Request(SlotContext context,INovelSlotAuthorization novel,ISpeechSlotAuthorization speech);
        void Stop(string fault);
    }
    public interface IAssessmentPanel
    {
        bool Ready { get; }
        event Action<string> Responded;
        void Tick();
        void Open(PanelRequest request);
        void Hide();
    }
    // No answer, correctness, score, content ID or robot command enters this view.
    public interface IAssessmentView
    {
        void Neutral();
        void Acknowledgment();
    }
    public interface IAssessmentViewEvidence
    {void ObserveView(string attemptId,string phase,double observedMonoMs);}

    public sealed class ProtectedContentFactory : ISlotContentFactory,ISessionContentPump,IDisposable
    {
        readonly ISessionClock clock;
        readonly IProtectedState scene;
        readonly IAssessmentAudio audio;
        readonly IAssessmentPanel panel;
        readonly IAssessmentView view;
        readonly AssessmentStages stages;
        readonly SpeechBank speech;
        readonly Action<string> response,fault;
        readonly Func<string,bool> cueAuthority;
        readonly List<ProtectedSlot> timeline=new List<ProtectedSlot>();
        ProtectedSlot responseOwner;
        bool failed,disposed;
        // Which readiness term ended a started trial, and when. Evidence only;
        // a diagnostic failure never masks or replaces the original fault.
        public JObject ReadinessLoss{get;private set;}
        void CaptureReadinessLoss(SlotContext context,double now)
        {
            if(ReadinessLoss!=null)return;
            try{var value=(scene as IProtectedStateDiagnostics)?.Readiness()??new JObject();value["attempt_id"]=context.Item.TrialId;value["onset_mono_ms"]=context.OnsetMonoMs;value["observed_mono_ms"]=now;ReadinessLoss=value;}
            catch{ReadinessLoss=new JObject{["attempt_id"]=context.Item.TrialId,["observed_mono_ms"]=now,["diagnostic_failed"]=true};}
        }
        public ProtectedContentFactory(ISessionClock clock,IProtectedState scene,IAssessmentAudio audio,IAssessmentPanel panel,
            IAssessmentView view,AssessmentStages stages,SpeechBank speech,Action<string> response,Action<string> fault,Func<string,bool> cueAuthority)
        {
            this.clock=clock??throw new ArgumentNullException(nameof(clock));this.scene=scene??throw new ArgumentNullException(nameof(scene));
            this.audio=audio??throw new ArgumentNullException(nameof(audio));this.panel=panel??throw new ArgumentNullException(nameof(panel));
            this.view=view??throw new ArgumentNullException(nameof(view));this.stages=stages??throw new ArgumentNullException(nameof(stages));
            this.speech=speech;this.response=response??throw new ArgumentNullException(nameof(response));this.fault=fault??throw new ArgumentNullException(nameof(fault));
            this.cueAuthority=cueAuthority??throw new ArgumentNullException(nameof(cueAuthority));
            panel.Responded+=Responded;
        }
        public ISlotContent Create(SlotItem item)
        {
            if(failed||disposed||item==null||!item.Protected||!new[]{"trained","novel","pre_old","atomic","speech","no_cue"}.Contains(item.TrialType)||
                item.Plays!=(item.TrialType=="no_cue"?0:1)||item.SlotSeconds!=(item.TrialType=="atomic"?9:14))
                throw new AssessmentFault("ASSESSMENT_SLOT_UNSUPPORTED");
            if(item.Phase=="validity")stages.ValidateValidityBank(speech);
            var slot=new ProtectedSlot(this);timeline.Add(slot);return slot;
        }
        void Responded(string code)
        {
            if(responseOwner==null)return;
            try
            {
                response(code); // #67 persists before participant confirmation.
                responseOwner.HasResponse=true;panel.Hide();responseOwner=null;
            }
            catch { Fail("ASSESSMENT_RESPONSE_LOG_FAILED"); }
        }
        public bool ExposureGate=>!disposed&&!failed&&scene.NeutralReady&&scene.ModeReady&&scene.FocusOk&&panel.Ready;
        void Fail(string code)
        {
            if(failed)return;failed=true;
            foreach(var slot in timeline)slot.Interrupted=true;
            StopPorts(code);responseOwner=null;fault(code);
        }
        void StopPorts(string code)
        {
            // A failed cleanup must not prevent the other independent shutdowns.
            try{audio.Stop(code);}catch{}
            try{panel.Hide();}catch{}
            try{view.Neutral();}catch{}
        }
        // FixedSlotEngine invokes this before its response and tail boundaries.
        public void Pump()=>Tick();
        public void Tick()
        {
            if(failed||disposed)return;
            try
            {
                panel.Tick();scene.Pump();double now=clock.NowMs;
                foreach(var slot in timeline.ToArray())slot.Tick(now);
                timeline.RemoveAll(x=>x.Prepared&&now>=x.Context.EndMonoMs);
            }
            catch(AssessmentFault error){Fail(error.Code);}
            catch(Exception){Fail("ASSESSMENT_RUNTIME_FAILED");}
        }
        public void Dispose()
        {
            if(disposed)return;disposed=true;
            bool active=timeline.Exists(x=>x.Prepared&&!x.Interrupted);
            foreach(var slot in timeline)slot.Interrupted=true;
            StopPorts("ASSESSMENT_DISPOSED");panel.Responded-=Responded;responseOwner=null;timeline.Clear();
            if(active)fault("ASSESSMENT_DISPOSED");
        }
        sealed class ProtectedSlot : ISlotContent
        {
            readonly ProtectedContentFactory owner;
            public SlotContext Context;
            public bool Prepared,HasResponse,Interrupted;
            bool requested,openRequested,panelOpened,shown,acknowledged,resetRequested,ended;
            double? anchor;
            public ProtectedSlot(ProtectedContentFactory owner){this.owner=owner;}
            public void Prepare(SlotContext context)
            {
                Context=context;Prepared=true;owner.scene.BeginTrial();
                if(context.Item.Plays>0)owner.audio.Prepare(context);
            }
            public SlotReadiness Readiness => !Prepared?default:new SlotReadiness(!Interrupted,Context.Item.Plays==0||owner.audio.Ready||requested,
                owner.scene.NeutralReady,owner.scene.NeutralReady,owner.responseOwner==null||owner.responseOwner==this,
                owner.scene.FocusOk,owner.panel.Ready,owner.scene.ModeReady);
            public bool ResetComplete=>resetRequested&&owner.scene.ResetComplete;
            public void RequestCue(SlotContext context,INovelSlotAuthorization novel)
            {
                if(requested||Interrupted||!Readiness.Ready)throw new AssessmentFault("ASSESSMENT_CUE_REFUSED");
                requested=true;
                if(Context.Item.Plays==0){anchor=Context.OnsetMonoMs;return;}
                var permission=Context.Item.TrialType=="speech"?owner.stages.SpeechPermit(Context,owner.speech,()=>owner.cueAuthority(Context.Item.TrialId)):null;
                owner.audio.Request(Context,novel,permission);
            }
            public void OpenResponse(SlotContext context){openRequested=true;Tick(owner.clock.NowMs);}
            public void CloseResponse(SlotContext context)
            {
                Tick(owner.clock.NowMs);
                if(!Interrupted&&!HasResponse)
                {
                    if(!panelOpened){owner.Fail("ASSESSMENT_ONSET_UNCONFIRMED");return;}
                    owner.Fail("ASSESSMENT_RESPONSE_LOG_MISSING");return;
                }
                owner.panel.Hide();if(owner.responseOwner==this)owner.responseOwner=null;
            }
            public void RequestReset(SlotContext context){resetRequested=true;owner.scene.RequestReset();}
            public void Interrupt(string code)
            {
                Interrupted=true;owner.StopPorts(code);
                if(owner.responseOwner==this)owner.responseOwner=null;
            }
            public void Tick(double now)
            {
                if(!Prepared||Interrupted)return;
                if(double.IsNaN(now)||double.IsInfinity(now))throw new AssessmentFault("ASSESSMENT_CLOCK_INVALID");
                if(now>=Context.EndMonoMs)
                {
                    if(!ended){if(owner.responseOwner==this){owner.panel.Hide();owner.responseOwner=null;}if(shown){owner.view.Neutral();(owner.view as IAssessmentViewEvidence)?.ObserveView(Context.Item.TrialId,"tail_end",now);}ended=true;}
                    return;
                }
                if(!requested||now<Context.OnsetMonoMs)return;
                if(!resetRequested&&(!owner.scene.NeutralReady||!owner.scene.ModeReady)){owner.CaptureReadinessLoss(Context,now);throw new AssessmentFault("ASSESSMENT_NEUTRAL_LOST");}
                if(Context.Item.Plays>0&&owner.audio.FaultCode!=null)throw new AssessmentFault(owner.audio.FaultCode);
                if(Context.Item.Plays>0&&!anchor.HasValue)
                {
                    if(owner.audio.FaultCode!=null)throw new AssessmentFault(owner.audio.FaultCode);
                    anchor=owner.audio.QualifiedOnsetMonoMs;
                    // #64's calibrated mapping schedules this exact requested
                    // estimate. Never silently substitute a different clock.
                    if(anchor.HasValue&&Math.Abs(anchor.Value-Context.OnsetMonoMs)>.001)throw new AssessmentFault("ASSESSMENT_ONSET_MISMATCH");
                }
                if(!shown){owner.view.Neutral();(owner.view as IAssessmentViewEvidence)?.ObserveView(Context.Item.TrialId,"protected_neutral",now);shown=true;}
                if(anchor.HasValue&&openRequested&&!panelOpened&&!HasResponse)
                {
                    var role=Context.Item.TrialType=="atomic"?(Context.Item.Role=="action"?PanelRole.Action:PanelRole.Target):PanelRole.Command;
                    owner.responseOwner=this;panelOpened=true;
                    owner.panel.Open(new PanelRequest(Context.Item.TrialId,Context.Item.TrialType=="atomic"?PanelMode.AtomicProbe:PanelMode.FullMessage,role,anchor.Value));
                }
                double deadline=Context.OnsetMonoMs+(Context.Item.TrialType=="atomic"?7000:12000);
                if(now>=deadline&&!acknowledged)
                {
                    if(!anchor.HasValue)throw new AssessmentFault("ASSESSMENT_ONSET_UNCONFIRMED");
                    owner.panel.Hide();owner.view.Acknowledgment();(owner.view as IAssessmentViewEvidence)?.ObserveView(Context.Item.TrialId,"acknowledgment",now);acknowledged=true;
                }
            }
        }
    }
}
