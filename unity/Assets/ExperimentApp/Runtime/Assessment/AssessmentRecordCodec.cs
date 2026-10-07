using System;
using System.Linq;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.Assessment
{
    // Closed payload shared by the dedicated journal and the #72 durable adapter.
    public static class AssessmentRecordCodec
    {
        public static JObject ToJson(AssessmentRecord r)
        {
            if(r==null)throw new AssessmentFault("ASSESSMENT_RECORD_INVALID");
            return new JObject{["event_kind"]=r.EventKind,["schedule_sha256"]=r.ScheduleSha256,["host_mono_ms"]=r.HostMonoMs,
                ["clock_epoch"]=r.ClockEpoch,["stage"]=r.Stage,["item_id"]=r.ItemId,["value"]=r.Value,["outcome_code"]=r.OutcomeCode};
        }
        public static AssessmentRecord FromJson(JObject r)
        {
            try
            {
                string[] keys={"event_kind","schedule_sha256","host_mono_ms","clock_epoch","stage","item_id","value","outcome_code"};
                if(r==null||!r.Properties().Select(p=>p.Name).OrderBy(x=>x,StringComparer.Ordinal).SequenceEqual(keys.OrderBy(x=>x,StringComparer.Ordinal))||
                    r["host_mono_ms"].Type is not (JTokenType.Integer or JTokenType.Float)||r["value"].Type is not (JTokenType.Integer or JTokenType.Null))
                    throw new AssessmentFault("ASSESSMENT_RECORD_INVALID");
                string Text(string key,bool nullable=false)
                {var v=r[key];if(v.Type==JTokenType.String)return(string)v;if(nullable&&v.Type==JTokenType.Null)return null;throw new AssessmentFault("ASSESSMENT_RECORD_INVALID");}
                return new AssessmentRecord(Text("event_kind"),Text("schedule_sha256"),(double)r["host_mono_ms"],Text("stage"),Text("item_id",true),(int?)r["value"],Text("outcome_code",true),Text("clock_epoch"));
            }
            catch(AssessmentFault){throw;}catch{throw new AssessmentFault("ASSESSMENT_RECORD_INVALID");}
        }
    }
}
