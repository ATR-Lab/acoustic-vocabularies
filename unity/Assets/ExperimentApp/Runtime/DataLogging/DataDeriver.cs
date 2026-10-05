using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.DataLogging
{
    public sealed class DerivedTables
    {
        public IReadOnlyList<IReadOnlyDictionary<string,string>> Trials {get;}
        public IReadOnlyList<IReadOnlyDictionary<string,string>> Exposures {get;}
        internal DerivedTables(IEnumerable<Dictionary<string,string>> trials,IEnumerable<Dictionary<string,string>> exposures)
        {Trials=trials.Select(x=>(IReadOnlyDictionary<string,string>)new System.Collections.ObjectModel.ReadOnlyDictionary<string,string>(x)).ToList().AsReadOnly();Exposures=exposures.Select(x=>(IReadOnlyDictionary<string,string>)new System.Collections.ObjectModel.ReadOnlyDictionary<string,string>(x)).ToList().AsReadOnly();}
    }
    public static class DataDeriver
    {
        // Provisional public columns. Exact external reviewed templates must match
        // before qualification; unknown/missing columns are never silently filled.
        public static readonly IReadOnlyList<string> TrialHeaders=Array.AsReadOnly(new[]{"schema_version","session_id","coded_id","visit_id","station_id","opportunity_id","attempt_id","retry_of","method_masked","waveform_sha256","audio_request_mono_ms","audio_onset_estimate_mono_ms","onset_uncertainty_ms","playback_status","frame_freeze_ms","reset_ok","focus_ok","response_code","technical_fault_code","exposure_consumed","deviation_id","interrupted","audio_request_ids","selected_target","selected_action","response_target","response_action"});
        public static readonly IReadOnlyList<string> ExposureHeaders=Array.AsReadOnly(new[]{"schema_version","session_id","coded_id","visit_id","station_id","opportunity_id","attempt_id","audio_request_id","retry_of","yoked_source_event_id","candidate_id","accepted_or_rejected","audio_id","waveform_sha256","audio_request_mono_ms","scheduled_onset_mono_ms","audio_onset_estimate_mono_ms","onset_uncertainty_ms","playback_status","audible_status","callback_observed","pause_ms","matching_deviation_id","exposure_consumed","technical_fault_code"});
        sealed class Attempt
        {
            internal Dictionary<string,string> Row;internal bool Done,SessionConsumed;internal readonly List<Exposure> Audio=new List<Exposure>();
        }
        sealed class Exposure
        {
            internal Dictionary<string,string> Row;internal string Pcm;internal bool Consumed=true,Callback,Conflict,EverAudible;internal string Evidence;
        }
        static string Value(JToken x)=>x==null||x.Type==JTokenType.Null?"":x.Type==JTokenType.Boolean?((bool)x?"true":"false"):x.Type==JTokenType.Float||x.Type==JTokenType.Integer?((double)x).ToString("R",CultureInfo.InvariantCulture):(string)x;
        static Dictionary<string,string> Base(IReadOnlyList<string> headers,DataIdentity id,EventContext c)
        {
            var row=headers.ToDictionary(x=>x,x=>"",StringComparer.Ordinal);row["schema_version"]="data-csv-provisional-1";row["session_id"]=id.SessionId;row["coded_id"]=id.CodedId;row["visit_id"]=id.VisitId;row["station_id"]=id.StationId;row["opportunity_id"]=c.OpportunityId;row["attempt_id"]=c.AttemptId;return row;
        }
        static void Fault(Dictionary<string,string> row,string code)
        {if(string.IsNullOrEmpty(code))return;var codes=(row["technical_fault_code"]??"").Split(';').Where(x=>x.Length>0).ToList();if(!codes.Contains(code))codes.Add(code);row["technical_fault_code"]=string.Join(";",codes);}
        static void Response(Attempt a,string code)
        {if(string.IsNullOrEmpty(code))return;if(a.Row["response_code"].Length==0)a.Row["response_code"]=code;else if(a.Row["response_code"]!=code)Fault(a.Row,"DATA_RESPONSE_CONFLICT");}
        static void SameContext(Exposure e,EventContext c) => DataJson.Require(e.Row["attempt_id"]==c.AttemptId&&e.Row["opportunity_id"]==c.OpportunityId,"DATA_AUDIO_CONTEXT_CHANGED");
        static void Evidence(Exposure e,string status,string hash)
        {
            e.EverAudible|=status=="confirmed_audible";
            if(e.Conflict||status=="confirmed_no_onset"&&(e.EverAudible||e.Callback||e.Evidence!=null&&e.Row["audible_status"]=="uncertain")||e.Row["audible_status"]=="confirmed_no_onset"&&status!="confirmed_no_onset")
            {e.Conflict=true;e.Consumed=true;e.Row["audible_status"]="uncertain";e.Row["playback_status"]="uncertain";Fault(e.Row,"DATA_ONSET_EVIDENCE_CONFLICT");return;}
            e.Evidence=hash;e.Row["audible_status"]=status;e.Row["playback_status"]=status;e.Consumed=status!="confirmed_no_onset";
        }
        public static DerivedTables Derive(JournalSnapshot snapshot,DataIdentity identity)
        {
            var attempts=new Dictionary<string,Attempt>(StringComparer.Ordinal);var audio=new Dictionary<string,Exposure>(StringComparer.Ordinal);var methods=new Dictionary<string,string>(StringComparer.Ordinal);var choices=new Dictionary<string,DataRecord>(StringComparer.Ordinal);
            Attempt Need(EventContext c)
            {
                DataJson.Require(c.AttemptId!=null&&c.OpportunityId!=null,"DATA_ATTEMPT_CONTEXT");
                if(!attempts.TryGetValue(c.AttemptId,out var a)){a=new Attempt{Row=Base(TrialHeaders,identity,c)};a.Row["focus_ok"]="";a.Row["reset_ok"]="false";a.Row["frame_freeze_ms"]="";attempts.Add(c.AttemptId,a);}
                DataJson.Require(a.Row["opportunity_id"]==c.OpportunityId,"DATA_ATTEMPT_CONTEXT");return a;
            }
            foreach(var record in snapshot.Records)
            {
                var p=record.Payload;var c=record.Context;
                switch(record.Kind)
                {
                    case "opportunity":
                        string m=(string)p["method_masked"];DataJson.Require(!methods.TryGetValue(c.OpportunityId,out var old)||old==m,"DATA_METHOD_CHANGED");methods[c.OpportunityId]=m;break;
                    case "session":
                        if(c.AttemptId==null)break;
                        var a=Need(c);string retry=Value(p["retry_of"]);DataJson.Require(a.Row["retry_of"].Length==0||a.Row["retry_of"]==retry,"DATA_RETRY_CHANGED");a.Row["retry_of"]=retry;
                        a.SessionConsumed|=(bool)p["exposure_consumed"];a.Row["reset_ok"]=Value(p["reset_ok"]);if(!(bool)p["focus_ok"])a.Row["focus_ok"]="false";else if(a.Row["focus_ok"].Length==0)a.Row["focus_ok"]="true";
                        Fault(a.Row,Value(p["technical_fault_code"]));Response(a,Value(p["response_code"]));
                        if((string)p["state"]=="Done"&&(string)p["event"]=="state_after")a.Done=true;
                        if((string)p["event"]=="onset_evidence"&&p["evidence_sha256"].Type!=JTokenType.Null&&((JArray)p["audio_request_ids"]).Count==1)
                        {
                            string id=(string)p["audio_request_ids"][0],status=(string)p["audible_status"];
                            if(audio.TryGetValue(id,out var e)&&new[]{"ConfirmedAudible","ConfirmedNoOnset","Uncertain"}.Contains(status)) { SameContext(e,c);Evidence(e,status=="ConfirmedAudible"?"confirmed_audible":status=="ConfirmedNoOnset"?"confirmed_no_onset":"uncertain",(string)p["evidence_sha256"]);if(status=="ConfirmedNoOnset"&&!e.Consumed)a.SessionConsumed=false; }
                        }
                        break;
                    case "audio_request":
                        DataJson.Require(!audio.ContainsKey(c.AudioRequestId),"DATA_DUPLICATE_AUDIO_REQUEST");var attempt=Need(c);
                        var exposure=new Exposure{Row=Base(ExposureHeaders,identity,c),Pcm=(string)p["pcm_sha256"]};exposure.Row["audio_request_id"]=c.AudioRequestId;exposure.Row["audio_id"]=(string)p["audio_id"];exposure.Row["waveform_sha256"]=Value(p["waveform_sha256"]);
                        exposure.Row["audio_request_mono_ms"]=Value(p["request_mono_ms"]);exposure.Row["scheduled_onset_mono_ms"]=Value(p["scheduled_mono_ms"]);exposure.Row["playback_status"]="uncertain";exposure.Row["audible_status"]="uncertain";exposure.Row["accepted_or_rejected"]="pending";exposure.Row["pause_ms"]="0";
                        audio.Add(c.AudioRequestId,exposure);attempt.Audio.Add(exposure);break;
                    case "audio_observation":
                        DataJson.Require(audio.TryGetValue(c.AudioRequestId,out var observed),"DATA_OBSERVATION_WITHOUT_REQUEST");
                        DataJson.Require(observed.Row["attempt_id"]==c.AttemptId&&observed.Pcm==(string)p["pcm_sha256"]&&observed.Row["audio_id"]==(string)p["audio_id"]&&observed.Row["waveform_sha256"]==Value(p["waveform_sha256"]),"DATA_AUDIO_CHANGED");
                        SameContext(observed,c);bool callback=(long)p["callback_count"]>0;observed.Callback|=callback;
                        if(callback&&observed.Row["audible_status"]=="confirmed_no_onset"){observed.Conflict=true;Fault(observed.Row,"DATA_ONSET_EVIDENCE_CONFLICT");observed.Row["audible_status"]="uncertain";observed.Row["playback_status"]="uncertain";observed.Consumed=true;}
                        if(callback&&p["onset_estimate_mono_ms"].Type!=JTokenType.Null)
                        {observed.Row["audio_onset_estimate_mono_ms"]=Value(p["onset_estimate_mono_ms"]);observed.Row["onset_uncertainty_ms"]=Value(p["onset_uncertainty_ms"]);if(observed.Evidence==null)observed.Row["audible_status"]="estimated";}
                        string code=(string)p["code"];
                        if(code=="AUDIO_PLAYBACK_COMPLETED"&&callback){if(observed.Evidence==null)observed.Row["playback_status"]="callback_complete";}
                        else if(!new[]{"AUDIO_ONSET_ESTIMATED","CALIBRATION_DELIVERY_OBSERVED"}.Contains(code))
                        {Fault(observed.Row,code);observed.Row["playback_status"]="uncertain";observed.Consumed=true;}
                        break;
                    case "audio_evidence":
                        DataJson.Require(audio.TryGetValue(c.AudioRequestId,out var evidenced)&&evidenced.Row["attempt_id"]==c.AttemptId,"DATA_EVIDENCE_WITHOUT_REQUEST");SameContext(evidenced,c);Evidence(evidenced,(string)p["audible_status"],(string)p["evidence_sha256"]);break;
                    case "choice":
                        DataJson.Require(!choices.TryGetValue(c.AudioRequestId,out var priorChoice)||JToken.DeepEquals(priorChoice.Payload,p)&&priorChoice.Context.AttemptId==c.AttemptId&&priorChoice.Context.OpportunityId==c.OpportunityId,"DATA_CHOICE_CHANGED");choices[c.AudioRequestId]=record;break;
                    case "deviation_reference":
                        var deviated=Need(c);var existing=(deviated.Row["deviation_id"]??"").Split(';').Where(x=>x.Length>0).ToList();string deviation=(string)p["deviation_id"];if(!existing.Contains(deviation))existing.Add(deviation);deviated.Row["deviation_id"]=string.Join(";",existing);break;
                    case "panel_process":case "panel_response":
                        var panelAttempt=Need(c);panelAttempt.Row["selected_target"]=Value(p["selected_target"]);panelAttempt.Row["selected_action"]=Value(p["selected_action"]);
                        string response=record.Kind=="panel_response"?(string)p["response_code"]:new[]{"commit","dont_know","timeout"}.Contains((string)p["kind"])?(string)p["kind"]:null;
                        bool first=panelAttempt.Row["response_code"].Length==0;Response(panelAttempt,response);
                        if(first&&response=="commit"){panelAttempt.Row["response_target"]=Value(record.Kind=="panel_response"?p["response_target"]:p["selected_target"]);panelAttempt.Row["response_action"]=Value(record.Kind=="panel_response"?p["response_action"]:p["selected_action"]);}break;
                    case "device":
                        if(c.AttemptId==null)break;var deviceAttempt=Need(c);string device=(string)p["kind"];
                        if(device=="focus"&&p["value"].Type==JTokenType.Boolean){if(!(bool)p["value"])deviceAttempt.Row["focus_ok"]="false";else if(deviceAttempt.Row["focus_ok"].Length==0)deviceAttempt.Row["focus_ok"]="true";}
                        if(device=="reset"&&p["value"].Type==JTokenType.Boolean)deviceAttempt.Row["reset_ok"]=Value(p["value"]);
                        if(device=="frame_freeze"&&p["duration_ms"].Type!=JTokenType.Null)deviceAttempt.Row["frame_freeze_ms"]=Math.Max(deviceAttempt.Row["frame_freeze_ms"].Length==0?0:double.Parse(deviceAttempt.Row["frame_freeze_ms"],CultureInfo.InvariantCulture),(double)p["duration_ms"]).ToString("R",CultureInfo.InvariantCulture);
                        if(device=="pause"&&c.AudioRequestId!=null&&p["duration_ms"].Type!=JTokenType.Null&&audio.TryGetValue(c.AudioRequestId,out var paused)){SameContext(paused,c);paused.Row["pause_ms"]=(double.Parse(paused.Row["pause_ms"],CultureInfo.InvariantCulture)+(double)p["duration_ms"]).ToString("R",CultureInfo.InvariantCulture);}
                        Fault(deviceAttempt.Row,Value(p["code"]));break;
                }
            }
            foreach(var entry in choices)
            {
                DataJson.Require(audio.TryGetValue(entry.Key,out var e),"DATA_CHOICE_WITHOUT_REQUEST");SameContext(e,entry.Value.Context);var p=entry.Value.Payload;
                foreach(string key in new[]{"candidate_id","accepted_or_rejected","yoked_source_event_id","matching_deviation_id"})e.Row[key]=Value(p[key]);
                e.Row["pause_ms"]=(double.Parse(e.Row["pause_ms"],CultureInfo.InvariantCulture)+(double)p["pause_ms"]).ToString("R",CultureInfo.InvariantCulture);
            }
            foreach(var a in attempts.Values)
            {
                if(a.Row["retry_of"].Length>0)DataJson.Require(attempts.TryGetValue(a.Row["retry_of"],out var original)&&original.Row["opportunity_id"]==a.Row["opportunity_id"]&&original.Row["retry_of"].Length==0,"DATA_RETRY_LINK");
                a.Row["method_masked"]=methods.TryGetValue(a.Row["opportunity_id"],out var method)?method:"";a.Row["interrupted"]=a.Done?"false":"true";
                if(!a.Done)Fault(a.Row,"DATA_INTERRUPTED_ATTEMPT");
                a.Row["exposure_consumed"]=(a.SessionConsumed||a.Audio.Any(x=>x.Consumed))?"true":"false";
                a.Row["audio_request_ids"]=new JArray(a.Audio.Select(x=>x.Row["audio_request_id"])).ToString(Formatting.None);
                a.Row["playback_status"]=a.Audio.Count==0?(a.SessionConsumed?"uncertain":"not_requested"):a.Audio.All(x=>x.Row["playback_status"]=="confirmed_no_onset")?"confirmed_no_onset":a.Audio.All(x=>x.Row["playback_status"]=="callback_complete"||x.Row["playback_status"]=="confirmed_audible")?"observed_complete":"uncertain";
                if(a.Audio.Count>0)foreach(string key in new[]{"waveform_sha256","audio_request_mono_ms","audio_onset_estimate_mono_ms","onset_uncertainty_ms"})a.Row[key]=a.Audio[0].Row[key];
                foreach(var e in a.Audio){e.Row["retry_of"]=a.Row["retry_of"];e.Row["exposure_consumed"]=e.Consumed?"true":"false";e.Row["callback_observed"]=e.Callback?"true":"false";}
            }
            return new DerivedTables(attempts.Values.Select(x=>x.Row),audio.Values.Select(x=>x.Row));
        }
    }
}
