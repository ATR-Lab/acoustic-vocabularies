using System;
using System.Linq;
using AcousticVocab.SessionEngine;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.DataLogging
{
    // Private executed-item metadata, recorded at module binding. This contains
    // opaque content handles and runtime gates, never the hidden intended tuple.
    public static class NativeObservationCodec
    {
        public const string SlotVersion="native-slot-context-1",MenuVersion="native-menu-event-1";
        public static void BindSlot(DataJournal journal,VisitSchedule schedule,SlotContext context)
        {
            var item=context.Item;
            DataJson.Require(journal!=null&&schedule!=null&&item!=null,"DATA_NATIVE_SLOT");
            var original=schedule.Blocks.SelectMany(b=>b.Items).SingleOrDefault(x=>x.TrialId==context.OpportunityId);
            DataJson.Require(original!=null&&original.TrialType==item.TrialType&&original.ContentId==item.ContentId&&original.Role==item.Role&&
                original.Presentation==item.Presentation&&original.Phase==item.Phase&&original.Heldout==item.Heldout&&original.SlotSeconds==item.SlotSeconds&&original.Plays==item.Plays&&original.Pass==item.Pass,"DATA_NATIVE_SCHEDULE");
            var loaded=journal.Records.LastOrDefault(r=>r.Kind=="session"&&r.Context.AttemptId==item.TrialId&&
                (string)r.Payload["event"]=="state_after"&&(string)r.Payload["state"]=="Loaded");
            DataJson.Require(loaded!=null,"DATA_NATIVE_LOADED_REQUIRED");
            var p=new JObject{["schema_version"]=SlotVersion,["schedule_sha256"]=schedule.Sha256,["package_sha256"]=schedule.PackageSha256,
                ["session_clock_epoch"]=loaded.Payload["clock_epoch"],["observation_clock_epoch"]=journal.ClockEpoch,["clock_basis"]="shared_host_monotonic",
                ["loaded_event_sha256"]=loaded.Sha256,["person_slot"]=schedule.PersonSlot,["trial_type"]=item.TrialType,
                ["content_id"]=item.ContentId,["role"]=item.Role,["presentation"]=item.Presentation,["phase"]=item.Phase,["heldout"]=item.Heldout,
                ["slot_seconds"]=item.SlotSeconds,["plays"]=item.Plays,["pass"]=item.Pass,["scheduled_onset_mono_ms"]=context.OnsetMonoMs,
                ["audio_request_ids"]=new JArray(context.AudioRequestIds),["dictionary_policy"]="prohibited",
                ["feedback_policy"]=item.Phase=="teaching"?"scheduled_lesson":"prohibited"};
            var prior=journal.Records.Where(r=>r.Kind=="slot_context"&&(string)r.Payload["loaded_event_sha256"]==loaded.Sha256).ToArray();
            DataJson.Require(prior.Length<=1&&(prior.Length==0||JToken.DeepEquals(prior[0].Payload,p)),"DATA_NATIVE_SLOT_CHANGED");
            if(prior.Length==0)journal.Append(new EventDraft("slot_context",new EventContext(context.OpportunityId,item.TrialId),p));
        }
        internal static void ValidateSlot(JObject p,EventContext c)
        {
            DataJson.Keys(p,"schema_version","schedule_sha256","package_sha256","session_clock_epoch","observation_clock_epoch","clock_basis","loaded_event_sha256","person_slot","trial_type","content_id","role","presentation","phase","heldout","slot_seconds","plays","pass","scheduled_onset_mono_ms","audio_request_ids","dictionary_policy","feedback_policy");
            DataJson.Require(c.AttemptId!=null&&c.AudioRequestId==null&&(string)p["schema_version"]==SlotVersion,"DATA_NATIVE_SLOT");
            foreach(string k in new[]{"schedule_sha256","package_sha256","loaded_event_sha256"})DataJson.Require(DataJson.Hash(DataJson.Text(p[k])));
            DataJson.Require(DataJson.Guid(DataJson.Text(p["session_clock_epoch"]))&&DataJson.Guid(DataJson.Text(p["observation_clock_epoch"]))&&
                (string)p["clock_basis"]=="shared_host_monotonic"&&DataJson.Id(DataJson.Text(p["person_slot"]))&&DataJson.Id(DataJson.Text(p["trial_type"])));
            foreach(string k in new[]{"content_id","role","presentation"}){string v=DataJson.OptionalText(p[k]);DataJson.Require(v==null||DataJson.Id(v));}
            DataJson.Require(new[]{"selection","teaching","pre_test","protected","validity"}.Contains(DataJson.Text(p["phase"])));
            DataJson.Boolean(p["heldout"]);DataJson.Require(DataJson.Integer(p["slot_seconds"])>0&&DataJson.Integer(p["plays"])<=8&&DataJson.Integer(p["pass"])>0);
            DataJson.Number(p["scheduled_onset_mono_ms"]);
            DataJson.Require(p["audio_request_ids"] is JArray ids&&ids.Count==(long)p["plays"]&&ids.All(x=>DataJson.Guid(DataJson.Text(x)))&&ids.Distinct(JToken.EqualityComparer).Count()==ids.Count);
            DataJson.Require((string)p["dictionary_policy"]=="prohibited"&&
                (string)p["feedback_policy"]==((string)p["phase"]=="teaching"?"scheduled_lesson":"prohibited"),"DATA_NATIVE_POLICY");
        }
        internal static void ValidateMenu(JObject p,EventContext c)
        {
            DataJson.Keys(p,"schema_version","schedule_sha256","package_sha256","role","event_id","kind","menu_key","meaning_display_id","slot_start_mono_ms","observed_mono_ms","expected_mono_ms","onset_uncertainty_ms","presentation_index","candidate_id","pcm_sha256","file_sha256","yoked_source_event_id","selected_index","defaulted","phase","receipt_sha256","matching_deviation_id");
            DataJson.Require(c.AttemptId!=null&&(string)p["schema_version"]==MenuVersion&&new[]{"active","yoked"}.Contains(DataJson.Text(p["role"])));
            foreach(string k in new[]{"schedule_sha256","package_sha256"})DataJson.Require(DataJson.Hash(DataJson.Text(p[k])));
            DataJson.Require(DataJson.Guid(DataJson.Text(p["event_id"]))&&DataJson.Id(DataJson.Text(p["menu_key"]))&&DataJson.Id(DataJson.Text(p["meaning_display_id"])));
            string kind=DataJson.Text(p["kind"]);
            DataJson.Require(new[]{"menu_start","choice_revised","choice_final","selection_verified","display_request","display_changed","play_request","onset_authority","play_complete","menu_interrupted"}.Contains(kind));
            DataJson.Number(p["slot_start_mono_ms"]);DataJson.Number(p["observed_mono_ms"]);DataJson.OptionalNumber(p["expected_mono_ms"]);DataJson.OptionalNumber(p["onset_uncertainty_ms"]);
            bool play=new[]{"play_request","onset_authority","play_complete"}.Contains(kind);
            DataJson.Require(play?c.AudioRequestId!=null&&DataJson.Integer(p["presentation_index"])>=1&&DataJson.Integer(p["presentation_index"])<=8:c.AudioRequestId==null&&p["presentation_index"].Type==JTokenType.Null);
            foreach(string k in new[]{"candidate_id","phase","matching_deviation_id"}){string v=DataJson.OptionalText(p[k]);DataJson.Require(v==null||DataJson.Id(v));}
            foreach(string k in new[]{"pcm_sha256","file_sha256","receipt_sha256"}){string v=DataJson.OptionalText(p[k]);DataJson.Require(v==null||DataJson.Hash(v));}
            string source=DataJson.OptionalText(p["yoked_source_event_id"]);DataJson.Require(source==null||DataJson.Guid(source));
            DataJson.Require(p["selected_index"].Type==JTokenType.Null||DataJson.Integer(p["selected_index"])>=1&&DataJson.Integer(p["selected_index"])<=3);
            DataJson.Require(p["defaulted"].Type==JTokenType.Null||p["defaulted"].Type==JTokenType.Boolean);
            bool final=kind=="choice_final"||kind=="selection_verified";
            DataJson.Require(final==(p["defaulted"].Type==JTokenType.Boolean)&&(!final||p["selected_index"].Type==JTokenType.Integer));
            DataJson.Require(!play||p["candidate_id"].Type==JTokenType.String&&p["pcm_sha256"].Type==JTokenType.String&&p["file_sha256"].Type==JTokenType.String);
        }
        internal static void Match(string kind,JObject p,EventContext c,System.Collections.Generic.IReadOnlyList<DataRecord> prior)
        {
            if(kind=="slot_context")
            {
                var loaded=prior.SingleOrDefault(r=>r.Sha256==(string)p["loaded_event_sha256"]);
                DataJson.Require(loaded!=null&&loaded.Kind=="session"&&loaded.Context.AttemptId==c.AttemptId&&loaded.Context.OpportunityId==c.OpportunityId&&
                    (string)loaded.Payload["event"]=="state_after"&&(string)loaded.Payload["state"]=="Loaded"&&
                    (string)loaded.Payload["schedule_sha256"]==(string)p["schedule_sha256"]&&(string)loaded.Payload["clock_epoch"]==(string)p["session_clock_epoch"]&&
                    JToken.DeepEquals(loaded.Payload["scheduled_onset_mono_ms"],p["scheduled_onset_mono_ms"])&&JToken.DeepEquals(loaded.Payload["audio_request_ids"],p["audio_request_ids"]),"DATA_NATIVE_LOADED_BINDING");
                DataJson.Require(!prior.Any(r=>r.Kind=="slot_context"&&(string)r.Payload["loaded_event_sha256"]==(string)p["loaded_event_sha256"]),"DATA_NATIVE_SLOT_CHANGED");
            }
            else if(kind=="menu_observation")
            {
                var slot=prior.LastOrDefault(r=>r.Kind=="slot_context"&&r.Context.AttemptId==c.AttemptId);DataJson.Require(slot!=null,"DATA_NATIVE_MENU_SLOT");var s=slot.Payload;
                DataJson.Require(slot.Context.OpportunityId==c.OpportunityId&&(string)s["phase"]=="selection"&&
                    (string)p["schedule_sha256"]==(string)s["schedule_sha256"]&&(string)p["package_sha256"]==(string)s["package_sha256"]&&
                    (double)p["slot_start_mono_ms"]==(double)s["scheduled_onset_mono_ms"]&&(string)p["menu_key"]==((string)s["trial_type"]=="profile_menu"?"profile":(string)s["content_id"]),"DATA_NATIVE_MENU_BINDING");
                if(c.AudioRequestId!=null)DataJson.Require((string)s["audio_request_ids"][(int)p["presentation_index"]-1]==c.AudioRequestId,"DATA_NATIVE_MENU_AUDIO");
            }
        }
    }
    // This is a source-exact projection, not another exposure ledger or an
    // acoustic measurement. Consumers must verify each reference against raw/.
    public static class NativeObservationExport
    {
        public const string FileName="native-observations.json",ManifestVersion="data-export-native-1";
        static readonly string[] Kinds={"slot_context","panel_response","panel_process","audio_request","audio_observation","audio_evidence","lesson","assessment_stage","menu_observation","device","choice","session"};
        public static JObject Create(JournalSnapshot snapshot,DataIdentity identity)
        {
            var events=new JArray();
            foreach(var r in snapshot.Records.Where(r=>Kinds.Contains(r.Kind)))
            {
                var source=r.ToJson();events.Add(new JObject{["sequence"]=r.Sequence,["source_event_sha256"]=r.Sha256,["clock_epoch"]=source["clock_epoch"],
                    ["host_mono_ms"]=source["host_mono_ms"],["opportunity_id"]=source["opportunity_id"],["attempt_id"]=source["attempt_id"],
                    ["audio_request_id"]=source["audio_request_id"],["kind"]=r.Kind,["payload"]=r.Payload});
            }
            return new JObject{["format"]="av-native-observations",["format_version"]=1,["evidence_level"]="software_event_calls",["acoustic_authority"]=false,["identity"]=identity.ToJson(),["events"]=events};
        }
        internal static void Verify(byte[] bytes,JournalSnapshot snapshot,DataIdentity identity)
        {DataJson.Require(JToken.DeepEquals(DataJson.ParseCanonical(bytes,(int)DataJournal.MaximumSegmentBytes),Create(snapshot,identity)),"DATA_NATIVE_PROJECTION_CHANGED");}
    }
}
