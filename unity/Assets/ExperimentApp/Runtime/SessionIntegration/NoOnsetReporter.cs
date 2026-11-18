using System;
using System.Collections.Generic;
using AcousticVocab.DataLogging;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
namespace AcousticVocab.SessionIntegration
{
    // How the reporter judged a request's missing playback.
    public enum NoOnsetVerdict
    {
        NotAssessed,
        // No loopback source is configured; software evidence alone.
        NoLoopbackSource,
        // A source is configured but gave no capture that binds this request and
        // spans its window, or failed.
        LoopbackUnusable,
        // The capture shows a level at or above the silence thresholds.
        LoopbackSignal,
        // The capture is silent over the whole window.
        LoopbackSilent
    }
    // Reports a confirmed no-onset to the engine, at most once per audio request
    // and only for a single-play item, so the engine can queue its one linked
    // retry at the end of the block. Two independent records are required: the
    // player's durable missing-playback outcome with no output callback, no
    // rendered sample and no onset estimate for the request, and a loopback
    // capture of the audio route for the same request that is silent from the
    // request hand-off to the missing-playback observation. Software evidence
    // alone never confirms no onset: without a usable silent capture the request
    // is not reported, the item keeps its AUDIO_MISSING_PLAYBACK technical fault,
    // and the exposure the engine consumed when it requested the cue stays
    // consumed and uncertain. Runs on the host main thread inside the audio sink.
    public sealed class NoOnsetReporter
    {
        public const string EvidenceKind="acoustic_measurement",EvidenceFormat="no-onset-evidence-v1";
        const int MaximumRequests=10000,RecentRows=8;
        sealed class Request{internal string TrialId;internal bool SinglePlay,Requested,Rendered,Closed;internal NoOnsetVerdict Verdict;}
        readonly FixedSlotEngine engine;readonly DataJournal journal;readonly ILoopbackObservationSource loopback;
        readonly Dictionary<string,Request> requests=new Dictionary<string,Request>(StringComparer.Ordinal);
        // With no loopback source (the default) no request is ever reported.
        public NoOnsetReporter(FixedSlotEngine engine,DataJournal journal,ILoopbackObservationSource loopback=null)
        {this.engine=engine??throw new ArgumentNullException(nameof(engine));this.journal=journal??throw new ArgumentNullException(nameof(journal));this.loopback=loopback;}
        public NoOnsetVerdict VerdictFor(string audioRequestId)=>audioRequestId!=null&&requests.TryGetValue(audioRequestId,out var r)?r.Verdict:NoOnsetVerdict.NotAssessed;
        // Called with the slot's own context before the request is scheduled.
        public void Bind(SlotContext context,int index)
        {
            if(context.Item==null||context.AudioRequestIds==null||index<0||index>=context.AudioRequestIds.Count)throw new SessionFault("SESSION_AUDIO_BINDING");
            string id=context.AudioRequestIds[index];if(requests.Count>=MaximumRequests||requests.ContainsKey(id))throw new SessionFault("SESSION_AUDIO_BINDING");
            requests.Add(id,new Request{TrialId=context.Item.TrialId,SinglePlay=context.Item.Plays==1&&context.AudioRequestIds.Count==1});
        }
        // Called after the event is durable. True only when the engine accepted a
        // confirmed no-onset for this request.
        public bool Observe(AudioPlaybackEvent value)
        {
            if(value==null||value.AudioId==null||!requests.TryGetValue(value.AudioId,out var request)||request.Closed)return false;
            if(value.DeliveredSamples!=0||value.CallbackCount!=0||value.FirstOutputCallbackDspSeconds.HasValue)request.Rendered=true;
            if(value.Code=="AUDIO_REQUESTED"){request.Requested=true;return false;}
            if(value.Code is "AUDIO_ONSET_ESTIMATED" or "SIMULATION_DELIVERY_OBSERVED" or "CALIBRATION_DELIVERY_OBSERVED"){request.Rendered=true;return false;}
            // Completion or any fault ends this request's evidence.
            request.Closed=true;
            if(value.Code!="AUDIO_MISSING_PLAYBACK"||!request.SinglePlay||!request.Requested||request.Rendered||value.Timing==null||value.Timing.CalibrationOnly)return false;
            var row=DurableMissingPlayback(value.AudioId);if(row==null)return false;
            var capture=Capture(value,out request.Verdict);
            // A signal on the route keeps the exposure consumed; the measurement
            // is recorded so the exposure ledger shows why.
            if(request.Verdict==NoOnsetVerdict.LoopbackSignal)
            {journal.Append(DataObservations.TrustedAudioEvidence(row.Context,"uncertain",capture.EvidenceSha256,EvidenceKind,value.ObservedMonoSeconds*1000));return false;}
            if(request.Verdict!=NoOnsetVerdict.LoopbackSilent||engine.Status!=SessionState.Running||engine.CurrentTrialId!=request.TrialId||
                !(engine.CurrentState is ItemState state&&state>=ItemState.CueRequested&&state<ItemState.Done))return false;
            string evidence=Evidence(row.Sha256,capture.EvidenceSha256);
            try{engine.ObserveAudible(AudibleStatus.ConfirmedNoOnset,evidence);}
            catch(SessionFault){return false;}
            journal.Append(DataObservations.TrustedAudioEvidence(row.Context,"confirmed_no_onset",evidence,EvidenceKind,value.ObservedMonoSeconds*1000));
            return true;
        }
        // The one evidence hash binds both records: SHA-256 of the format name,
        // the durable software row hash and the loopback evidence hash, one per
        // line, each line ending in a newline.
        public static string Evidence(string softwareRowSha256,string loopbackEvidenceSha256)=>
            LoopbackObservation.Sha256(EvidenceFormat+"\n"+softwareRowSha256+"\n"+loopbackEvidenceSha256+"\n");
        LoopbackObservation Capture(AudioPlaybackEvent value,out NoOnsetVerdict verdict)
        {
            verdict=NoOnsetVerdict.NoLoopbackSource;if(loopback==null)return null;
            verdict=NoOnsetVerdict.LoopbackUnusable;LoopbackObservation capture;
            try{capture=loopback.Find(value.AudioId);}catch(Exception){return null;}
            if(capture==null||!capture.Covers(value.AudioId,value.Timing.RequestMonoSeconds,value.ObservedMonoSeconds))return null;
            verdict=capture.Silent?NoOnsetVerdict.LoopbackSilent:NoOnsetVerdict.LoopbackSignal;return capture;
        }
        // The newest durable observation of this request must itself record the
        // missing playback with no callback and no delivered sample. Its row hash
        // is the software half of the evidence.
        DataRecord DurableMissingPlayback(string id)
        {
            var rows=journal.Records;
            for(int i=rows.Count-1;i>=0&&i>=rows.Count-RecentRows;i--)
            {
                var row=rows[i];if(row.Kind!="audio_observation"||row.Context.AudioRequestId!=id)continue;
                var p=row.Payload;
                return (string)p["code"]=="AUDIO_MISSING_PLAYBACK"&&(string)p["audio_id"]==id&&(long)p["delivered_samples"]==0&&(long)p["callback_count"]==0&&
                    p["first_callback_dsp_s"]?.Type==JTokenType.Null?row:null;
            }
            return null;
        }
    }
}
