using System;
using AcousticVocab.DataLogging;
using AcousticVocab.SessionEngine;
using AcousticVocab.Soak;
using Newtonsoft.Json.Linq;
namespace AcousticVocab.SessionIntegration
{
    // Durable session records for the synthetic soak input feed, appended to
    // the real hash-chained DataJournal and bound to the pinned driver schedule.
    // These are soak-feed facts, not FixedSlotEngine participant trials: no cue
    // is played, so every record states NoCue and no exposure is consumed.
    public sealed class SoakSessionRecorder:ISoakSessionRecorder
    {
        readonly DataJournal journal;readonly SoakSchedule schedule;readonly Func<double> clockMs;readonly Func<bool> focused;
        readonly string epoch=Guid.NewGuid().ToString("N");
        public SoakSessionRecorder(DataJournal journal,SoakSchedule schedule,Func<double> monotonicMilliseconds,Func<bool> focused)
        {
            this.journal=journal??throw new ArgumentNullException(nameof(journal));this.schedule=schedule??throw new ArgumentNullException(nameof(schedule));
            clockMs=monotonicMilliseconds??throw new ArgumentNullException(nameof(monotonicMilliseconds));this.focused=focused??throw new ArgumentNullException(nameof(focused));
        }
        static JToken Text(string value)=>value==null?JValue.CreateNull():new JValue(value);
        SoakDataReference Write(string kind,SoakTrial trial,int blockIndex,string state,bool resetOk,string response,string fault)
        {
            var value=new JObject{["event"]=kind,["clock_epoch"]=epoch,["schedule_sha256"]=schedule.Sha256,["trial_id"]=Text(trial?.TrialId),["retry_of"]=JValue.CreateNull(),
                ["block_index"]=trial?.BlockIndex??blockIndex,["item_index"]=trial?.ItemIndex??0,["host_mono_ms"]=clockMs(),["scheduled_onset_mono_ms"]=JValue.CreateNull(),["state"]=Text(state),
                ["audible_status"]=AudibleStatus.NoCue.ToString(),["exposure_consumed"]=false,["reset_ok"]=resetOk,["focus_ok"]=focused(),["technical_fault_code"]=Text(fault),
                ["response_code"]=Text(response),["evidence_sha256"]=JValue.CreateNull(),["opportunity_id"]=Text(trial?.TrialId),["audio_request_ids"]=new JArray()};
            var record=SessionRecordCodec.FromJson(value);
            var row=journal.Append(new EventDraft("session",new EventContext(record.OpportunityId,record.TrialId),SessionRecordCodec.ToJson(record)));
            return new SoakDataReference((string)row.ToJson()["event_id"],row.Sha256);
        }
        public SoakDataReference TrialBegan(SoakTrial trial)=>Write("state_before",trial??throw new ArgumentNullException(nameof(trial)),0,ItemState.Loaded.ToString(),true,null,null);
        public SoakDataReference Responded(SoakTrial trial,string responseCode)=>Write("response",trial??throw new ArgumentNullException(nameof(trial)),0,ItemState.ResponseOpen.ToString(),true,responseCode,null);
        public SoakDataReference Paused(SoakTrial interrupted,string blockId,string faultType)=>Write("session_paused",interrupted,schedule.BlockOrdinal(blockId),null,false,null,"SOAK_"+faultType.ToUpperInvariant());
        public SoakDataReference Resumed(string blockId)=>Write("operator_resume",null,schedule.BlockOrdinal(blockId),null,true,null,null);
    }
}
