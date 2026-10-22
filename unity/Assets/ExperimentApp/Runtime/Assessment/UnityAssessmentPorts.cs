using System;
using System.Collections.Generic;
using AcousticVocab.Foundation;
using AcousticVocab.ResponsePanel;
using AcousticVocab.SessionEngine;
using AcousticVocab.StateIntegration;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.Assessment
{
    public sealed class UnityProtectedState : IProtectedState,IProtectedStateDiagnostics
    {
        readonly PrivateModeResetClient control;
        readonly StateSourceHost source;
        readonly FoundationBootstrap foundation;
        string reset,confirmed;
        // First failing term of the latest evaluation (null when it passed),
        // recorded in the unchanged short-circuit order without extra reads.
        string neutralFailure,modeFailure;double neutralAt=-1,modeAt=-1;
        public UnityProtectedState(PrivateModeResetClient control,StateSourceHost source,FoundationBootstrap foundation)
        {
            this.control=control??throw new ArgumentNullException(nameof(control));
            if(control.RequiredMode!="test")throw new AssessmentFault("ASSESSMENT_TEST_MODE_REQUIRED");
            this.source=source??throw new ArgumentNullException(nameof(source));this.foundation=foundation??throw new ArgumentNullException(nameof(foundation));
        }
        public void BeginTrial(){confirmed=null;reset=control.RequestReset();}
        public void RequestReset(){confirmed=null;reset=control.RequestReset();}
        public void Pump()
        {
            control.Pump();
            if(reset!=null&&confirmed!=reset&&control.ResetAcknowledged(reset)&&source.ConfirmReset())confirmed=reset;
        }
        public bool ModeReady
        {get{modeFailure=!control.ModeAcknowledged?"control_mode_not_acknowledged":!control.NeutralHoldHealthy?"control_neutral_hold_not_healthy":null;modeAt=AudioPlayer.Now*1000;return modeFailure==null;}}
        public bool NeutralReady
        {get{neutralFailure=reset==null?"reset_not_requested":confirmed!=reset?"reset_not_confirmed":!control.ResetAcknowledged(reset)?"control_reset_not_acknowledged":!source.CheckExposureReady()?"source_exposure_not_ready":null;neutralAt=AudioPlayer.Now*1000;return neutralFailure==null;}}
        public bool ResetComplete=>NeutralReady;
        // Non-pumping snapshot: never advances control or grants exposure.
        public JObject Readiness()=>new JObject{["neutral_failure"]=neutralFailure,["neutral_evaluated_mono_ms"]=neutralAt<0?JValue.CreateNull():new JValue(neutralAt),
            ["mode_failure"]=modeFailure,["mode_evaluated_mono_ms"]=modeAt<0?JValue.CreateNull():new JValue(modeAt),
            ["source"]=source.ExposureDiagnostic(),["control"]=control.ReadinessDiagnostic(reset)};
        public bool FocusOk=>foundation.Ready;
    }

    public sealed class UnityAssessmentPanel : IAssessmentPanel,IAssessmentPanelDiagnostics,IDisposable
    {
        readonly ResponsePanelController panel;
        public UnityAssessmentPanel(ResponsePanelController panel)
        {
            this.panel=panel??throw new ArgumentNullException(nameof(panel));
            panel.Responded+=OnResponse;
        }
        void OnResponse(PanelResponse value)=>Responded?.Invoke(value.Code==ResponseCode.Commit?"commit":value.Code==ResponseCode.DontKnow?"dont_know":"timeout");
        public bool Ready=>panel.ReadyForTrial;
        public event Action<string> Responded;
        public void Tick()=>panel.State?.Tick();
        public void Open(PanelRequest request)=>panel.Open(request);
        public void Hide()=>panel.CloseAtBoundary();
        public JObject Readiness()=>panel.ReadinessDiagnostic();
        public void Dispose()=>panel.Responded-=OnResponse;
    }

    public readonly struct AudioSelection
    {
        public readonly string Profile;
        public readonly int ActionRank,ReferentRank;
        public AudioSelection(string profile,int actionRank,int referentRank){Profile=profile;ActionRank=actionRank;ReferentRank=referentRank;}
    }
    // #70 supplies its immutable verified old/current selection snapshot.
    // No browser input or choice change during a test may implement this authority.
    public interface IAssessmentSelections
    {
        string PackageSha256 { get; }
        bool OldHashesVerified { get; }
        AudioSelection For(string contentId);
    }

    public sealed class UnityAssessmentAudio : IAssessmentAudio,IDisposable
    {
        readonly LoadedAudioPackage package;
        readonly AudioPlayer player;
        readonly IAssessmentSelections selections;
        readonly SpeechBank speech;
        readonly Action<AudioPlaybackEvent> persist;readonly Action<SlotContext,int,PcmWave> beforeSchedule;
        SlotContext context;
        AudioSelection choice;
        PcmWave prepared;
        bool requested,disposed;
        public double? QualifiedOnsetMonoMs { get; private set; }
        public string FaultCode { get; private set; }
        public bool Ready=>!disposed&&FaultCode==null&&player.TrialReady;
        public UnityAssessmentAudio(LoadedAudioPackage package,AudioPlayer player,SpeechBank speech,
            IAssessmentSelections selections,Action<AudioPlaybackEvent> durableAudioSink,Action<SlotContext,int,PcmWave> beforeSchedule=null)
        {
            this.package=package??throw new ArgumentNullException(nameof(package));this.player=player??throw new ArgumentNullException(nameof(player));
            this.speech=speech;this.selections=selections;this.beforeSchedule=beforeSchedule;persist=durableAudioSink??throw new ArgumentNullException(nameof(durableAudioSink));
            player.Event+=OnAudio;
        }
        AudioSelection Select(string id)
        {
            if(package.Study=="A")return default;
            if(selections==null||selections.PackageSha256!=package.PackageSha256||!selections.OldHashesVerified)throw new AssessmentFault("ASSESSMENT_SELECTION_UNAVAILABLE");
            return selections.For(id);
        }
        public void Prepare(SlotContext value)
        {
            if(disposed||player.Playing)throw new AssessmentFault("ASSESSMENT_AUDIO_BUSY");
            context=value;requested=false;FaultCode=null;QualifiedOnsetMonoMs=null;prepared=null;
            string type=value.Item.TrialType,id=value.Item.ContentId;
            if(type=="speech")
            {
                // Only an existing verified non-speech atom is prepared before
                // the stage's speech permit; no speech PCM is read early.
                choice=Select("K-a1");prepared=package.ReadAtom("K-a1",choice.Profile,choice.ActionRank);
            }
            else
            {
                choice=Select(id);
                if(type=="novel")prepared=package.ReadAtom(id.Substring(0,4),choice.Profile,choice.ActionRank);
                else if(type=="atomic")prepared=package.ReadAtom(id,choice.Profile,value.Item.Role=="action"?choice.ActionRank:choice.ReferentRank);
                else prepared=package.ReadTrainedMessage(id,choice.Profile,choice.ActionRank,choice.ReferentRank);
            }
            player.Preload(new Dictionary<string,PcmWave>{{value.AudioRequestIds[0],prepared}},64*1024*1024);
        }
        public void Request(SlotContext value,INovelSlotAuthorization novel,ISpeechSlotAuthorization speechPermission)
        {
            if(requested||value.Item!=context.Item||!Ready)throw new AssessmentFault("ASSESSMENT_AUDIO_REFUSED");
            requested=true;
            if(value.Item.TrialType=="novel")
                prepared=package.ComposeApprovedNovel(value.Item.ContentId,novel,choice.Profile,choice.ActionRank,choice.ReferentRank);
            else if(value.Item.TrialType=="speech")
            {
                if(speech==null)throw new AssessmentFault("ASSESSMENT_SPEECH_MISSING");
                prepared=speech.ReadForValidity(value.Item.ContentId,speechPermission);
            }
            if(value.Item.TrialType is "novel" or "speech")player.Preload(new Dictionary<string,PcmWave>{{value.AudioRequestIds[0],prepared}},64*1024*1024);
            beforeSchedule?.Invoke(value,0,prepared);
            player.Schedule(value.AudioRequestIds[0],value.OnsetMonoMs/1000);
        }
        void OnAudio(AudioPlaybackEvent value)
        {
            if(context.Item==null||value.AudioId!=context.AudioRequestIds[0])return;
            persist(value); // durable before granting the response anchor
            if((value.Code is "AUDIO_ONSET_ESTIMATED" or "SIMULATION_DELIVERY_OBSERVED")&&value.Timing.PresentationAnchorMonoSeconds.HasValue)
                QualifiedOnsetMonoMs=value.Timing.PresentationAnchorMonoSeconds.Value*1000;
            else if(value.Code is not ("AUDIO_REQUESTED" or "AUDIO_PLAYBACK_COMPLETED"))FaultCode=value.Code;
        }
        public void Stop(string fault)=>player.Abort(fault);
        public void Dispose()
        {
            if(disposed)return;disposed=true;
            try{player.Abort("ASSESSMENT_AUDIO_DISPOSED");}finally{player.Event-=OnAudio;}
        }
    }
}
