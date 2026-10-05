using System;
using AcousticVocab.Foundation;
using AcousticVocab.ResponsePanel;
using AcousticVocab.SessionEngine;
using AcousticVocab.StateIntegration;
using AcousticVocab.StudyAudio;
using UnityEngine;

namespace AcousticVocab.Assessment
{
    // Installs only after the trusted admission owner supplies independently
    // verified inputs. This component never reads package paths from a browser,
    // chooses a schedule, advances a trial, or declares a calibration qualified.
    [DisallowMultipleComponent]
    public sealed class AssessmentSessionHost:MonoBehaviour
    {
        public FoundationBootstrap foundation;
        public StateSourceHost source;
        public ResponsePanelController panel;
        public AudioPlayer player;
        public AssessmentScreen screen;
        public AssessmentStages Stages{get;private set;}
        public bool Installed=>factory!=null&&!failed&&isActiveAndEnabled;
        public event Action<string> Faulted;
        ProtectedContentFactory factory;
        UnityAssessmentAudio audio;
        UnityAssessmentPanel responsePanel;
        FixedSlotEngine engine;
        VisitSchedule schedule;
        AssessmentScripts scripts;
        bool failed,handlingFault,disposed;
        public ProtectedContentFactory Install(VisitSchedule visit,LoadedAudioPackage package,ISessionClock clock,ISessionJournal sessionJournal,
            IAssessmentJournal stageJournal,PrivateModeResetClient testControl,AudioRouteCalibration qualifiedRoute,float storedGain,
            SpeechBank speech,IAssessmentSelections selections,AssessmentScripts reviewedScripts,bool ratingWordingReviewed,
            Action<AudioPlaybackEvent> durableAudioSink,bool engineeringPreview=false)
        {
            if(factory!=null||disposed||failed||!isActiveAndEnabled||foundation==null||!foundation.Ready||source==null||panel==null||!panel.ReadyForTrial||player==null||screen==null||
                visit==null||package==null||visit.PackageSha256!=package.PackageSha256||visit.Demo!=package.Demo||visit.Demo&&!engineeringPreview||
                testControl==null||testControl.RequiredMode!="test"||!testControl.ModeAcknowledged||qualifiedRoute==null||!qualifiedRoute.IsQualified||qualifiedRoute.UncertaintyMs>20||
                reviewedScripts==null||Faulted==null)throw new AssessmentFault("ASSESSMENT_HOST_NOT_READY");
            schedule=visit;scripts=reviewedScripts;
            try
            {
                Stages=new AssessmentStages(visit,sessionJournal,stageJournal,clock,()=>engine!=null&&engine.Status is (SessionState.AwaitingOperator or SessionState.Paused or SessionState.Complete),ratingWordingReviewed);
                screen.Configure(Stages);screen.Faulted+=Fail;panel.Faulted+=Fail;foundation.Faulted+=Fail;
                audio=new UnityAssessmentAudio(package,player,speech,selections,durableAudioSink);responsePanel=new UnityAssessmentPanel(panel);
                factory=new ProtectedContentFactory(clock,new UnityProtectedState(testControl,source,foundation),audio,responsePanel,screen,Stages,speech,
                    code=>{if(engine==null)throw new AssessmentFault("ASSESSMENT_ENGINE_UNBOUND");engine.RecordResponse(code);},Fail,
                    id=>engine!=null&&engine.Status==SessionState.Running&&engine.CurrentState==ItemState.CueRequested&&engine.CurrentTrialId==id);
                player.Configure(qualifiedRoute,()=>Installed&&factory.ExposureGate);player.SetComfortableGain(storedGain);
                return factory;
            }
            catch{Fail("ASSESSMENT_INSTALL_FAILED");throw;}
        }
        public void BindEngine(FixedSlotEngine value)
        {
            if(!Installed||engine!=null||value==null||value.ScheduleSha256!=schedule.Sha256||value.PackageSha256!=schedule.PackageSha256)
                throw new AssessmentFault("ASSESSMENT_ENGINE_BINDING");engine=value;
        }
        public void ShowInstruction(string block)
        {if(!Installed||engine==null)throw new AssessmentFault("ASSESSMENT_HOST_NOT_READY");screen.ShowInstruction(scripts,block);}
        public void BeginForms()
        {if(!Installed||engine==null)throw new AssessmentFault("ASSESSMENT_HOST_NOT_READY");screen.BeginForms();}
        void Fail(string code)
        {
            if(handlingFault||failed||disposed)return;handlingFault=true;failed=true;
            try
            {
                try{audio?.Stop(code);}catch{}
                try{panel?.CloseAtBoundary();}catch{}
                try{screen?.Neutral();}catch{}
                try{engine?.Fault(code);}catch{}
                try{factory?.Dispose();}catch{}
                Faulted?.Invoke(new AssessmentFault(code).Code);
            }
            finally{handlingFault=false;}
        }
        void Shutdown()
        {
            if(disposed)return;disposed=true;
            try{factory?.Dispose();}catch{}
            try{audio?.Dispose();}catch{}
            responsePanel?.Dispose();
            if(screen!=null)screen.Faulted-=Fail;if(panel!=null)panel.Faulted-=Fail;if(foundation!=null)foundation.Faulted-=Fail;
        }
        void OnApplicationFocus(bool value){if(!value&&factory!=null)Fail("ASSESSMENT_FOCUS_LOST");}
        void OnApplicationPause(bool value){if(value&&factory!=null)Fail("ASSESSMENT_APPLICATION_PAUSED");}
        void OnDisable(){if(factory!=null)Fail("ASSESSMENT_HOST_DISABLED");}
        void OnDestroy()=>Shutdown();
    }
}
