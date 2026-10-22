using System;
using AcousticVocab.DataLogging;
using AcousticVocab.SelectionMenus;
using AcousticVocab.SessionEngine;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.SessionIntegration
{
    // Tee the real durable menu sink after its ledger append. The role is the
    // verified catalog's role, never inferred from missing replay event IDs.
    public static class NativeMenuDataAdapter
    {
        public static void Record(DataJournal journal,VisitSchedule schedule,MenuEvent value,string role)
        {
            if(journal==null||schedule==null||value==null)throw new ArgumentNullException();
            var p=new JObject{["schema_version"]=NativeObservationCodec.MenuVersion,["schedule_sha256"]=schedule.Sha256,["package_sha256"]=schedule.PackageSha256,
                ["role"]=role,["event_id"]=value.EventId,["kind"]=value.Kind,["menu_key"]=value.MenuKey,["meaning_display_id"]=value.MeaningDisplayId,
                ["slot_start_mono_ms"]=value.SlotStartMonoMs,["observed_mono_ms"]=value.MonoMs,["expected_mono_ms"]=value.ExpectedMonoMs,["onset_uncertainty_ms"]=value.OnsetUncertaintyMs,
                ["presentation_index"]=value.PresentationIndex,["candidate_id"]=value.CandidateId,["pcm_sha256"]=value.PcmSha256,["file_sha256"]=value.FileSha256,
                ["yoked_source_event_id"]=value.YokedSourceEventId,["selected_index"]=value.SelectedIndex,["defaulted"]=value.Defaulted,["phase"]=value.Phase?.ToString(),
                ["receipt_sha256"]=value.ReceiptSha256,["matching_deviation_id"]=value.MatchingDeviationId};
            journal.Append(new EventDraft("menu_observation",new EventContext(value.OpportunityId,value.AttemptId,value.AudioRequestId),p));
        }
    }
}
