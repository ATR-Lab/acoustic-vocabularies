using System;
using System.Collections.Generic;
using AcousticVocab.Assessment;
using AcousticVocab.DataLogging;
using AcousticVocab.FrameBudget;
using AcousticVocab.OperatorConsole;
using AcousticVocab.ResponsePanel;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
namespace AcousticVocab.SessionIntegration
{
    // Concrete trusted joining boundary. No schedule loading, allocation, audio
    // qualification, network transport or automatic ticking is performed here.
    public sealed class SessionIntegrationOwner:IDisposable
    {
        readonly DataJournal data;readonly AudioPlayer player;readonly FrameCaptureHost frames;readonly ExclusiveContentMultiplexer mux;
        readonly AudioDataAdapter audioData;readonly FrameAudioAdapter audioFrames;readonly PanelDataAdapter panelData;
        readonly Dictionary<string,AudioRequestContext> audio=new Dictionary<string,AudioRequestContext>();readonly Dictionary<string,FrameCueBinding> cues=new Dictionary<string,FrameCueBinding>();readonly Dictionary<string,EventContext> attempts=new Dictionary<string,EventContext>();
        bool closed,disposing;
        public ExclusiveContentMultiplexer Modules=>mux;public Resources Shared{get;}
        public FixedSlotEngine Engine{get;}public IAssessmentJournal StageJournal{get;}public ISessionJournal SessionJournal{get;}
        public sealed class Resources
        {
            readonly SessionIntegrationOwner owner;internal Resources(SessionIntegrationOwner owner){this.owner=owner;}
            public AudioPlayer Player=>owner.player;public IAssessmentJournal StageJournal=>owner.StageJournal;
            public Action<AudioPlaybackEvent> DurableAudioSink=>owner.PersistAudio;
            // Called from module Prepare/Request immediately before Schedule,
            // after novel/speech composition has produced its verified wave.
            public void BindAudio(SlotContext c,int index,PcmWave verifiedWave)
            {
                if(owner.closed||c.Item==null||index<0||index>=c.AudioRequestIds.Count||verifiedWave==null)throw new SessionFault("SESSION_AUDIO_BINDING");verifiedWave.Verify();
                string id=c.AudioRequestIds[index];if(owner.audio.Count>=10000||owner.audio.ContainsKey(id))throw new SessionFault("SESSION_AUDIO_BINDING");
                owner.audio.Add(id,new AudioRequestContext(new EventContext(c.OpportunityId,c.Item.TrialId,id),id,verifiedWave.FileSha256));
                owner.cues.Add(id,new FrameCueBinding(c.Item.TrialId,verifiedWave.PcmSha256,verifiedWave.SampleCount,PcmWave.SampleRate));
            }
        }
        public SessionIntegrationOwner(VisitSchedule schedule,ISessionClock clock,DataJournal journal,AudioPlayer sharedPlayer,ResponsePanelController panel,FrameCaptureHost capture,
            IReadOnlyDictionary<string,Func<Resources,ModuleConstructionScope,ISlotContentFactory>> factories,Action<SlotGateRefusal> gateRefused=null)
        {
            data=journal??throw new ArgumentNullException(nameof(journal));player=sharedPlayer??throw new ArgumentNullException(nameof(sharedPlayer));frames=capture??throw new ArgumentNullException(nameof(capture));
            if(!capture.Ready||player.Playing||panel==null||factories==null)throw new SessionFault("SESSION_JOIN_NOT_READY");
            StageJournal=new AssessmentDataJournal(data,schedule.Sha256);SessionJournal=new SessionDataJournal(data);var resources=new Resources(this);Shared=resources;
            var routes=new Dictionary<string,Func<ModuleConstructionScope,ISlotContentFactory>>();foreach(var entry in factories){var creator=entry.Value;routes.Add(entry.Key,scope=>{if(player.Playing)throw new SessionFault("SESSION_AUDIO_LEASE_BUSY");return creator(resources,scope);});}
            mux=new ExclusiveContentMultiplexer(schedule,clock,routes,c=>attempts[c.Item.TrialId]=new EventContext(c.OpportunityId,c.Item.TrialId));
            Engine=new FixedSlotEngine(schedule,clock,SessionJournal,new FrameContentFactory(mux,capture),gateRefused);
            audioData=new AudioDataAdapter(data,player,e=>audio.TryGetValue(e.AudioId,out var value)?value:null,subscribeToPlayer:false);
            audioFrames=new FrameAudioAdapter(capture,e=>cues.TryGetValue(e.AudioId,out var value)?value:null);
            panelData=new PanelDataAdapter(data,panel,id=>attempts.TryGetValue(id,out var value)?value:null);
        }
        void PersistAudio(AudioPlaybackEvent value){if(closed)throw new SessionFault("SESSION_JOIN_CLOSED");audioData.Record(value);audioFrames.Record(value);}
        public void PumpRetainedAtBoundary(){if(closed||Engine.Status==SessionState.Running)throw new SessionFault("SESSION_MODULE_BOUNDARY");mux.Pump();}
        public void PrepareResume(FixedSlotEngine engine){if(closed||!ReferenceEquals(engine,Engine))throw new SessionFault("SESSION_JOIN_BINDING");mux.PrepareBlockAtBoundary(engine);}
        // Install as OperatorMailbox's prepareResume hook. Mailbox remains the
        // only engine.Tick owner; no MonoBehaviour Update is introduced.
        public OperatorHealth Health(OperatorHealth trusted)=>FrameDataAdapter.Health(trusted,frames.Monitor,frames.Ready);
        public void Dispose()
        {
            if(closed||disposing)return;disposing=true;Exception first=null;
            // Keep durable observers attached while interrupting and stopping.
            try{Engine.Fault("SESSION_JOIN_DISPOSED");}catch(Exception e){first=e;}
            try{player.Abort("SESSION_JOIN_DISPOSED");}catch(Exception e){first??=e;}
            try{mux.Dispose();}catch(Exception e){first??=e;}
            closed=true;try{panelData.Dispose();}catch(Exception e){first??=e;}try{audioData.Dispose();}catch(Exception e){first??=e;}
            if(first!=null)throw new SessionFault("SESSION_JOIN_DISPOSE_FAILED");
        }
    }
}
