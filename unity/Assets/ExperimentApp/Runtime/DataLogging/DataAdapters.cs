using System;
using System.Collections.Generic;
using System.Linq;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using AcousticVocab.ResponsePanel;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.DataLogging
{
    public sealed class SessionDataJournal : ISessionJournal
    {
        readonly DataJournal journal;
        public SessionDataJournal(DataJournal journal){this.journal=journal??throw new ArgumentNullException(nameof(journal));}
        public IReadOnlyList<SessionRecord> Records=>journal.Records.Where(r=>r.Kind=="session").Select(r=>SessionRecordCodec.FromJson(r.Payload)).ToList().AsReadOnly();
        public void Append(SessionRecord record)=>journal.Append(new EventDraft("session",new EventContext(record.OpportunityId,record.TrialId),SessionRecordCodec.ToJson(record)));
    }
    public sealed class AudioRequestContext
    {
        public EventContext Context {get;} public string AudioId {get;} public string WaveformSha256 {get;}
        public AudioRequestContext(EventContext context,string audioId,string waveformHash)
        { DataJson.Require(context?.AudioRequestId!=null&&DataJson.Id(audioId)&&(waveformHash==null||DataJson.Hash(waveformHash)));Context=context;AudioId=audioId;WaveformSha256=waveformHash; }
    }
    // Bind a distinct scheduled play ID before calling AudioPlayer.Schedule.
    // Observations remain on the host's main thread; no disk I/O enters the DSP callback.
    public sealed class AudioDataAdapter : IDisposable
    {
        readonly DataJournal journal;readonly AudioPlayer player;readonly Func<AudioPlaybackEvent,AudioRequestContext> resolve;
        readonly HashSet<string> requested=new HashSet<string>(StringComparer.Ordinal);
        public AudioDataAdapter(DataJournal journal,AudioPlayer player,Func<AudioPlaybackEvent,AudioRequestContext> resolve,bool subscribeToPlayer=true)
        { this.journal=journal??throw new ArgumentNullException(nameof(journal));this.player=player??throw new ArgumentNullException(nameof(player));this.resolve=resolve??throw new ArgumentNullException(nameof(resolve));foreach(var row in journal.Records.Where(r=>r.Kind=="audio_request"))requested.Add(row.Context.AudioRequestId);if(subscribeToPlayer)player.Event+=Record; }
        public void Record(AudioPlaybackEvent value)
        {
            var binding=resolve(value);DataJson.Require(binding!=null&&binding.AudioId==value.AudioId,"DATA_AUDIO_BINDING");
            string id=binding.Context.AudioRequestId;bool request=value.Code=="AUDIO_REQUESTED";
            DataJson.Require(request?requested.Add(id):requested.Contains(id),"DATA_AUDIO_REQUEST_ORDER");
            var t=value.Timing??throw new DataFault("DATA_AUDIO_TIMING");
            journal.Append(new EventDraft(request?"audio_request":"audio_observation",binding.Context,GrammarStageCodec.Audio(value,binding.WaveformSha256)));
        }
        public void Dispose(){if(player!=null)player.Event-=Record;}
    }
    public sealed class PanelDataAdapter : IDisposable
    {
        readonly DataJournal journal;readonly ResponsePanelController panel;readonly Func<string,EventContext> resolve;
        public PanelDataAdapter(DataJournal journal,ResponsePanelController panel,Func<string,EventContext> resolve)
        {this.journal=journal??throw new ArgumentNullException(nameof(journal));this.panel=panel??throw new ArgumentNullException(nameof(panel));this.resolve=resolve??throw new ArgumentNullException(nameof(resolve));panel.ProcessRecorded+=Process;panel.Responded+=Response;}
        EventContext Context(PanelRequest request){var c=resolve(request.TrialId);DataJson.Require(c?.AttemptId==request.TrialId&&c.AudioRequestId==null,"DATA_PANEL_BINDING");return c;}
        public void Process(PanelProcessEvent p)=>journal.Append(new EventDraft("panel_process",Context(p.Request),Panel(p.Kind,p.MonoMs,p.Request,p.Input,p.Target,p.Action,null,null,null)));
        public void Response(PanelResponse p)=>journal.Append(new EventDraft("panel_response",Context(p.Request),Panel("response",p.ResponseMonoMs,p.Request,null,p.SelectedTarget,p.SelectedAction,p.Code==ResponseCode.Commit?"commit":p.Code==ResponseCode.DontKnow?"dont_know":"timeout",p.Target,p.Action)));
        static JObject Panel(string kind,double observed,PanelRequest request,string input,string target,string action,string response,string responseTarget,string responseAction)=>new JObject{
            ["kind"]=kind,["observed_mono_ms"]=observed,["mode"]=request.Mode.ToString(),["role"]=request.Role.ToString(),["input"]=input,["selected_target"]=target,["selected_action"]=action,["response_code"]=response,["response_target"]=responseTarget,["response_action"]=responseAction};
        public void Dispose(){if(panel!=null){panel.ProcessRecorded-=Process;panel.Responded-=Response;}}
    }
    public static class DataObservations
    {
        public static EventDraft Opportunity(string id,string role,string methodMasked,string scheduleHash)=>new EventDraft("opportunity",new EventContext(id),new JObject{["role"]=role,["method_masked"]=methodMasked,["schedule_sha256"]=scheduleHash});
        public static EventDraft Device(EventContext c,string kind,double observedMs,bool? value=null,double? durationMs=null,string code=null)=>new EventDraft("device",c,new JObject{["kind"]=kind,["observed_mono_ms"]=observedMs,["value"]=value,["duration_ms"]=durationMs,["code"]=code});
        public static EventDraft DeviationReference(EventContext c,string deviationId,string signedLogHash,double observedMs)=>new EventDraft("deviation_reference",c,new JObject{["deviation_id"]=deviationId,["signed_log_sha256"]=signedLogHash,["observed_mono_ms"]=observedMs});
        public static EventDraft Gain(GainChange g)=>new EventDraft("gain",null,new JObject{["old_gain"]=g.Previous,["new_gain"]=g.Current,["observed_mono_ms"]=g.MonoSeconds*1000,["reason"]="comfort"});
        public static EventDraft Choice(EventContext c,string candidate,string status,string yokedSource,double pauseMs,string deviation)=>new EventDraft("choice",c,new JObject{["candidate_id"]=candidate,["accepted_or_rejected"]=status,["yoked_source_event_id"]=yokedSource,["pause_ms"]=pauseMs,["matching_deviation_id"]=deviation});
        public static EventDraft TrustedAudioEvidence(EventContext c,string status,string evidenceHash,string evidenceKind,double observedMs)=>new EventDraft("audio_evidence",c,new JObject{["audible_status"]=status,["evidence_sha256"]=evidenceHash,["evidence_kind"]=evidenceKind,["observed_mono_ms"]=observedMs});
    }
}
