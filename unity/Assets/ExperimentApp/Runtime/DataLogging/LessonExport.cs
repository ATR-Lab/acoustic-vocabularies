using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.DataLogging
{
    public static class LessonExport
    {
        public const string Table="lesson-exposures.csv",Contract="lesson-header-contract.json",Version="lesson-exposures-provisional-1";
        public static readonly IReadOnlyList<string> Headers=Array.AsReadOnly(new[]{"schema_version","session_id","coded_id","visit_id","station_id","schedule_sha256","package_sha256","clock_epoch","session_clock_epoch","opportunity_id","attempt_id","context_audio_request_ids","lesson_type","row_kind","presentation_index","audio_request_id","meaning_display_id","feedback_content_id","pcm_sha256","action_pcm_sha256","referent_pcm_sha256","start_event_sha256","end_event_sha256","observed_start_mono_ms","observed_end_mono_ms","expected_start_mono_ms","display_start_mono_ms","display_end_mono_ms","retrieval_opportunity","interval_status","lesson_status","audio_ledger_status","audible_status","exposure_consumed","audio_onset_estimate_mono_ms","onset_uncertainty_ms","evidence_level"});
        internal static JObject HeaderContract()=>new JObject{["schema_version"]=Version,["qualified"]=false,["headers"]=new JArray(Headers),["exposure_ledger_relation"]="play_rows_join_by_audio_request_id_no_additional_exposures",["evidence_level"]="software_event_calls_not_physical_presentation",["acoustic_authority"]=false};
        static string Value(JToken p)=>p==null||p.Type==JTokenType.Null?"":p.Type==JTokenType.Integer||p.Type==JTokenType.Float?((double)p).ToString("R",CultureInfo.InvariantCulture):(string)p;
        public static IReadOnlyList<IReadOnlyDictionary<string,string>> Derive(JournalSnapshot snapshot,DataIdentity identity,DerivedTables audioTables=null)
        {
            audioTables??=DataDeriver.Derive(snapshot,identity);var audio=audioTables.Exposures.ToDictionary(x=>x["audio_request_id"],StringComparer.Ordinal);
            var requests=snapshot.Records.Where(x=>x.Kind=="audio_request").ToDictionary(x=>x.Context.AudioRequestId,StringComparer.Ordinal);
            var traces=new Dictionary<string,LessonTrace>();var prior=new List<DataRecord>();
            foreach(var r in snapshot.Records)
            {
                if(r.Kind=="lesson")
                {
                    var p=r.Payload;LessonRecordCodec.MatchLoaded(p,prior);string key=LessonRecordCodec.Key(p);
                    if(!traces.TryGetValue(key,out var trace))traces.Add(key,trace=new LessonTrace());
                    trace.Accept(p,r.ToJson());
                }
                prior.Add(r);
            }
            var rows=new List<IReadOnlyDictionary<string,string>>();
            foreach(var trace in traces.Values)
            {
                var events=trace.Events;string status=events.Any(x=>(string)x.Payload["kind"]=="lesson_interrupted")?"interrupted":events.Any(x=>(string)x.Payload["kind"]=="lesson_end")?"software_ended":"incomplete";
                foreach(var start in events.Where(x=>new[]{"play_request","display_start","retrieval_opportunity"}.Contains((string)x.Payload["kind"])))
                {
                    var p=start.Payload;string kind=(string)p["kind"],rowKind=kind=="play_request"?"play":kind=="display_start"?"display":"retrieval";
                    string endKind=rowKind=="play"?"play_complete":rowKind=="display"?"display_end":"retrieval_result";
                    var endings=events.Where(x=>(long)x.Envelope["sequence"]>(long)start.Envelope["sequence"]&&(string)x.Payload["kind"]==endKind&&
                        (rowKind!="play"||JToken.DeepEquals(x.Payload["presentation_index"],p["presentation_index"]))&&
                        (rowKind!="display"||JToken.DeepEquals(x.Payload["feedback_content_id"],p["feedback_content_id"]))).ToList();
                    DataJson.Require(endings.Count<=1,"DATA_LESSON_DUPLICATE_END");var end=endings.Count==1?endings[0].Payload:null;var endEnvelope=endings.Count==1?endings[0].Envelope:null;
                    DataJson.Require(endEnvelope==null||(string)endEnvelope["clock_epoch"]==(string)start.Envelope["clock_epoch"],"DATA_LESSON_CLOCK_EPOCH");
                    var row=Headers.ToDictionary(k=>k,k=>"",StringComparer.Ordinal);
                    row["schema_version"]=Version;row["session_id"]=identity.SessionId;row["coded_id"]=identity.CodedId;row["visit_id"]=identity.VisitId;row["station_id"]=identity.StationId;
                    foreach(string key in new[]{"schedule_sha256","package_sha256","session_clock_epoch","opportunity_id","attempt_id","lesson_type","presentation_index","audio_request_id","meaning_display_id","feedback_content_id","pcm_sha256","action_pcm_sha256","referent_pcm_sha256"})row[key]=Value(p[key]);
                    row["clock_epoch"]=(string)start.Envelope["clock_epoch"];row["context_audio_request_ids"]=string.Join(";",p["audio_request_ids"].Values<string>());row["row_kind"]=rowKind;
                    row["start_event_sha256"]=(string)start.Envelope["sha256"];row["end_event_sha256"]=endEnvelope==null?"":(string)endEnvelope["sha256"];
                    row["observed_start_mono_ms"]=Value(p["observed_mono_ms"]);row["observed_end_mono_ms"]=Value(end?["observed_mono_ms"]);row["expected_start_mono_ms"]=Value(p["expected_mono_ms"]);
                    row["interval_status"]=end==null?"incomplete":"software_observed";row["lesson_status"]=status;row["evidence_level"]="software_event_calls_not_physical_presentation";
                    row["retrieval_opportunity"]=rowKind=="retrieval"?"true":"false";
                    if(rowKind=="retrieval")row["feedback_content_id"]=Value(end?["feedback_content_id"]);
                    if(rowKind=="display"){row["display_start_mono_ms"]=row["observed_start_mono_ms"];row["display_end_mono_ms"]=row["observed_end_mono_ms"];}
                    if(rowKind=="play")
                    {
                        string id=row["audio_request_id"];row["audio_ledger_status"]="request_missing";
                        if(audio.TryGetValue(id,out var linked))
                        {
                            var q=requests[id];DataJson.Require(q.Context.AttemptId==row["attempt_id"]&&q.Context.OpportunityId==row["opportunity_id"]&&q.Sequence>(long)start.Envelope["sequence"],"DATA_LESSON_AUDIO_BINDING");
                            foreach(string key in new[]{"pcm_sha256","action_pcm_sha256","referent_pcm_sha256"})DataJson.Require(JToken.DeepEquals(q.Payload[key],p[key]),"DATA_LESSON_AUDIO_HASH");
                            row["audio_ledger_status"]="linked";foreach(string key in new[]{"audible_status","exposure_consumed","audio_onset_estimate_mono_ms","onset_uncertainty_ms"})row[key]=linked[key];
                        }
                    }
                    rows.Add(new System.Collections.ObjectModel.ReadOnlyDictionary<string,string>(row));
                }
            }
            return rows.AsReadOnly();
        }
    }
}
