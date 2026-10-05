using System;
using System.Collections.Generic;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.Teaching
{
    internal sealed class GrammarFlow
    {
        internal readonly string ReadyId=Guid.NewGuid().ToString("N"),ClicksId=Guid.NewGuid().ToString("N");
        internal int Requests {get;private set;}
        internal bool Complete {get;private set;}
        bool readyComplete,failed;
        double last=-1;
        internal event Action<string,double> Play;
        internal event Action<string> Display;
        readonly Action<string,string,double> persist;
        internal GrammarFlow(Action<string,string,double> persist){this.persist=persist??throw new ArgumentNullException(nameof(persist));}
        void Now(double now){LessonTimeline.Require(!double.IsNaN(now)&&!double.IsInfinity(now)&&now>=0&&now>=last,"GRAMMAR_CLOCK");last=now;}
        internal void Tick(double now)
        {
            Now(now);if(failed||Complete)return;
            if(Requests==0||Requests==1&&readyComplete)
            {string id=Requests==0?ReadyId:ClicksId;persist("grammar_request",id,now);Requests++;Display?.Invoke(Requests==1?"ready":"roles");Play?.Invoke(id,now+750);}
        }
        internal void Completed(string id,double now)
        {
            Now(now);LessonTimeline.Require(!failed&&!Complete&&(Requests==1&&id==ReadyId&&!readyComplete||Requests==2&&id==ClicksId&&readyComplete),"GRAMMAR_COMPLETION");
            persist("grammar_complete",id,now);if(id==ReadyId)readyComplete=true;else{Complete=true;Display?.Invoke("hidden");}
        }
        internal void Abort(double now){Now(now);if(failed)return;failed=true;Complete=false;Display?.Invoke("hidden");persist("grammar_interrupted",null,now);}
    }
    // Explicit familiarization action, never an automatic pre-visit exposure.
    // The two-play spacing (750 ms scheduling lead after software completion)
    // is an engineering sequence, pending review with the missing script.
    public sealed class GrammarFamiliarization : IDisposable
    {
        readonly AudioPlayer player;readonly GrammarFlow flow;readonly Action<AudioPlaybackEvent> audioSink;readonly Action<string> fault;
        bool disposed,aborting;
        public bool Complete=>!disposed&&flow.Complete&&!player.Playing;
        public GrammarFamiliarization(GrammarAssets assets,AudioPlayer player,AudioRouteCalibration route,float gain,
            Func<bool> neutralFocusGate,Action<string> showRoleLabels,Action<JObject> durableGrammarSink,Action<AudioPlaybackEvent> durableAudioSink,Action<string> faultSink)
        {
            LessonTimeline.Require(assets!=null&&player!=null&&route!=null&&neutralFocusGate!=null&&showRoleLabels!=null&&durableGrammarSink!=null&&durableAudioSink!=null&&faultSink!=null,"GRAMMAR_CONFIGURATION");
            this.player=player;audioSink=durableAudioSink;fault=faultSink;
            flow=new GrammarFlow((kind,id,now)=>durableGrammarSink(new JObject{["kind"]=kind,["audio_request_id"]=id,["host_mono_ms"]=now,["registry_sha256"]=assets.RegistrySha256,["calibration_only"]=!route.IsQualified}));
            flow.Display+=showRoleLabels;flow.Play+=(id,onset)=>{if(route.IsQualified)player.Schedule(id,onset/1000);else player.ScheduleCalibration(id,onset/1000);};
            player.Event+=OnAudio;
            try{player.Configure(route,neutralFocusGate);player.SetComfortableGain(gain);player.Preload(assets.Preload(flow.ReadyId,flow.ClicksId),1024*1024);}catch{player.Event-=OnAudio;throw;}
        }
        public void Tick(){if(disposed)return;try{flow.Tick(AudioPlayer.Now*1000);}catch(SessionFault e){Abort(e.Code);}catch{Abort("GRAMMAR_FAILED");}}
        void OnAudio(AudioPlaybackEvent value)
        {
            audioSink(value);LessonTimeline.Require(value.AudioId==flow.ReadyId||value.AudioId==flow.ClicksId,"GRAMMAR_AUDIO_CONTEXT");
            if(value.Code=="AUDIO_PLAYBACK_COMPLETED")
            {LessonTimeline.Require(value.CallbackCount>0,"GRAMMAR_DELIVERY_MISSING");flow.Completed(value.AudioId,value.ObservedMonoSeconds*1000);}
            else if(value.Code!="AUDIO_REQUESTED"&&value.Code!="AUDIO_ONSET_ESTIMATED"&&value.Code!="CALIBRATION_DELIVERY_OBSERVED")Abort(value.Code);
        }
        void Abort(string code)
        {if(disposed||aborting)return;aborting=true;try{TeachingCleanup.ThrowFirst(TeachingCleanup.Attempt(()=>flow.Abort(AudioPlayer.Now*1000),()=>player.Abort(code),()=>fault(code)));}finally{aborting=false;}}
        public void Dispose()
        {
            if(disposed)return;bool stop=!Complete&&!aborting;disposed=true;Exception first=null;
            try{if(stop)first=TeachingCleanup.Attempt(()=>flow.Abort(AudioPlayer.Now*1000),()=>player.Abort("GRAMMAR_SHUTDOWN"),()=>fault("GRAMMAR_SHUTDOWN"));}
            finally{player.Event-=OnAudio;}
            TeachingCleanup.ThrowFirst(first);
        }
    }
}
