using System;
using System.Collections.Generic;
using AcousticVocab.DataLogging;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
namespace AcousticVocab.SessionIntegration
{
    // Reports a confirmed no-onset to the engine, at most once per audio request
    // and only for a single-play item, so the engine can queue its one linked
    // retry at the end of the block. The evidence is software delivery only: the
    // player's missing-playback outcome, already durable, with no output callback,
    // no rendered sample and no onset estimate for the request. It cannot prove
    // that the speaker or earphones were silent. Every other outcome is uncertain
    // and is not reported, so the exposure the engine consumed when it requested
    // the cue stays consumed. Runs on the host main thread inside the audio sink.
    public sealed class NoOnsetReporter
    {
        public const string EvidenceKind="trusted_delivery_evidence";
        const int MaximumRequests=10000,RecentRows=8;
        sealed class Request{internal string TrialId;internal bool SinglePlay,Requested,Rendered,Closed;}
        readonly FixedSlotEngine engine;readonly DataJournal journal;
        readonly Dictionary<string,Request> requests=new Dictionary<string,Request>(StringComparer.Ordinal);
        public NoOnsetReporter(FixedSlotEngine engine,DataJournal journal)
        {this.engine=engine??throw new ArgumentNullException(nameof(engine));this.journal=journal??throw new ArgumentNullException(nameof(journal));}
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
            var row=DurableMissingPlayback(value.AudioId);
            if(row==null||engine.Status!=SessionState.Running||engine.CurrentTrialId!=request.TrialId||
                !(engine.CurrentState is ItemState state&&state>=ItemState.CueRequested&&state<ItemState.Done))return false;
            try{engine.ObserveAudible(AudibleStatus.ConfirmedNoOnset,row.Sha256);}
            catch(SessionFault){return false;}
            journal.Append(DataObservations.TrustedAudioEvidence(row.Context,"confirmed_no_onset",row.Sha256,EvidenceKind,value.ObservedMonoSeconds*1000));
            return true;
        }
        // The newest durable observation of this request must itself record the
        // missing playback with no callback and no delivered sample. Its row hash
        // is the evidence hash.
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
