using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using AcousticVocab.SessionEngine;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.DataLogging
{
    public sealed class DataFault : Exception
    {
        public string Code=>Message;
        public DataFault(string code):base(code!=null&&Regex.IsMatch(code,@"\ADATA_[A-Z0-9_]{1,70}\z")?code:"DATA_INVALID"){}
    }
    public sealed class DataIdentity
    {
        public string SessionId {get;} public string CodedId {get;} public string VisitId {get;}
        public string StationId {get;} public string ProtocolVersion {get;} public string BuildSha256 {get;}
        public DataIdentity(string session,string coded,string visit,string station,string protocol,string buildHash)
        { DataJson.Require(DataJson.Guid(session)&&DataJson.Hash(buildHash));foreach(string s in new[]{coded,visit,station,protocol})DataJson.Require(DataJson.Id(s));SessionId=session;CodedId=coded;VisitId=visit;StationId=station;ProtocolVersion=protocol;BuildSha256=buildHash; }
        public JObject ToJson()=>new JObject{["session_id"]=SessionId,["coded_id"]=CodedId,["visit_id"]=VisitId,["station_id"]=StationId,["protocol_version"]=ProtocolVersion,["build_sha256"]=BuildSha256};
        internal static DataIdentity Parse(JObject v){DataJson.Keys(v,"session_id","coded_id","visit_id","station_id","protocol_version","build_sha256");return new DataIdentity(DataJson.Text(v["session_id"]),DataJson.Text(v["coded_id"]),DataJson.Text(v["visit_id"]),DataJson.Text(v["station_id"]),DataJson.Text(v["protocol_version"]),DataJson.Text(v["build_sha256"]));}
    }
    public sealed class EventContext
    {
        public string OpportunityId {get;} public string AttemptId {get;} public string AudioRequestId {get;}
        public EventContext(string opportunity=null,string attempt=null,string audioRequest=null)
        { DataJson.Require((opportunity==null||DataJson.Id(opportunity))&&(attempt==null||DataJson.Id(attempt))&&(audioRequest==null||DataJson.Guid(audioRequest)));DataJson.Require(attempt==null||opportunity!=null);DataJson.Require(audioRequest==null||attempt!=null);OpportunityId=opportunity;AttemptId=attempt;AudioRequestId=audioRequest; }
    }
    public sealed class EventDraft
    {
        readonly JObject payload;
        public string Kind {get;} public EventContext Context {get;} public JObject Payload=>(JObject)payload.DeepClone();
        public EventDraft(string kind,EventContext context,JObject value)
        { Kind=kind;Context=context??new EventContext();payload=value==null?throw new DataFault("DATA_PAYLOAD"): (JObject)value.DeepClone();DataEventSchema.Validate(kind,Context,payload); }
    }
    public sealed class DataRecord
    {
        readonly JObject value;
        internal DataRecord(JObject value){this.value=(JObject)value.DeepClone();}
        public JObject ToJson()=>(JObject)value.DeepClone();
        public long Sequence=>(long)value["sequence"]; public string Kind=>(string)value["event_type"];
        public string Sha256=>(string)value["sha256"]; public JObject Payload=>(JObject)value["payload"].DeepClone();
        public EventContext Context=>new EventContext((string)value["opportunity_id"],(string)value["attempt_id"],(string)value["audio_request_id"]);
    }
    internal static class DataJson
    {
        internal const int MaxLine=65536;
        internal static void Require(bool value,string code="DATA_INVALID"){if(!value)throw new DataFault(code);}
        internal static bool Id(string v)=>v!=null&&Regex.IsMatch(v,@"\A[A-Za-z0-9][A-Za-z0-9._-]{0,79}\z");
        internal static bool Guid(string v)=>v!=null&&Regex.IsMatch(v,@"\A[0-9a-f]{32}\z");
        internal static bool Hash(string v)=>v!=null&&Regex.IsMatch(v,@"\A[0-9a-f]{64}\z");
        internal static bool Code(string v)=>v!=null&&Regex.IsMatch(v,@"\A[A-Z][A-Z0-9_]{0,79}\z");
        internal static void Keys(JToken value,params string[] names)=>Require(value is JObject o&&o.Properties().Select(p=>p.Name).OrderBy(x=>x,StringComparer.Ordinal).SequenceEqual(names.OrderBy(x=>x,StringComparer.Ordinal)),"DATA_FIELDS");
        internal static string Text(JToken v){Require(v?.Type==JTokenType.String);return (string)v;}
        internal static string OptionalText(JToken v){Require(v!=null);return v.Type==JTokenType.Null?null:Text(v);}
        internal static double Number(JToken v){Require(v!=null&&(v.Type==JTokenType.Float||v.Type==JTokenType.Integer));double n=(double)v;Require(!double.IsNaN(n)&&!double.IsInfinity(n)&&n>=0,"DATA_NUMBER");return n;}
        internal static double? OptionalNumber(JToken v){Require(v!=null);return v.Type==JTokenType.Null?(double?)null:Number(v);}
        internal static bool Boolean(JToken v){Require(v?.Type==JTokenType.Boolean);return (bool)v;}
        internal static long Integer(JToken v){Require(v?.Type==JTokenType.Integer);long n=(long)v;Require(n>=0);return n;}
        internal static byte[] Bytes(JObject v)=>new UTF8Encoding(false,true).GetBytes(v.ToString(Formatting.None)+"\n");
        internal static string HashBytes(byte[] b){using var h=SHA256.Create();return BitConverter.ToString(h.ComputeHash(b)).Replace("-","").ToLowerInvariant();}
        internal static string HashStream(Stream s){using var h=SHA256.Create();return BitConverter.ToString(h.ComputeHash(s)).Replace("-","").ToLowerInvariant();}
        internal static JObject ParseCanonical(byte[] bytes)
        {
            try
            {
                Require(bytes.Length>0&&bytes.Length<=MaxLine,"DATA_LINE_LIMIT");
                using var reader=new JsonTextReader(new StringReader(new UTF8Encoding(false,true).GetString(bytes))){DateParseHandling=DateParseHandling.None,FloatParseHandling=FloatParseHandling.Double,MaxDepth=32};
                var value=JObject.Load(reader,new JsonLoadSettings{DuplicatePropertyNameHandling=DuplicatePropertyNameHandling.Error,CommentHandling=CommentHandling.Load});
                Require(!reader.Read()&&Bytes(value).SequenceEqual(bytes),"DATA_NONCANONICAL_JSON");return value;
            }
            catch(DataFault){throw;}catch{throw new DataFault("DATA_JSON");}
        }
        internal static void NoLinks(string path)
        {
            FileSystemInfo item=File.Exists(path)?new FileInfo(path):new DirectoryInfo(path);
            for(;item!=null;item=item is FileInfo f?f.Directory:((DirectoryInfo)item).Parent)
                if(item.Exists)Require((item.Attributes&FileAttributes.ReparsePoint)==0,"DATA_REPARSE_POINT");
        }
    }
    public static class DataEventSchema
    {
        public const string Version="data-events-provisional-1";
        public static void Validate(string kind,EventContext c,JObject p)
        {
            switch(kind)
            {
                case "session":
                    var r=SessionRecordCodec.FromJson(p);DataJson.Require(c.OpportunityId==r.OpportunityId&&c.AttemptId==r.TrialId&&c.AudioRequestId==null,"DATA_SESSION_CONTEXT");break;
                case "opportunity":
                    DataJson.Keys(p,"role","method_masked","schedule_sha256");DataJson.Require(c.OpportunityId!=null&&c.AttemptId==null&&c.AudioRequestId==null&&DataJson.Id(DataJson.Text(p["role"]))&&DataJson.Id(DataJson.Text(p["method_masked"]))&&DataJson.Hash(DataJson.Text(p["schedule_sha256"])));break;
                case "audio_request": case "audio_observation":
                    Audio(p);DataJson.Require(c.AudioRequestId!=null,"DATA_AUDIO_CONTEXT");DataJson.Require(kind!="audio_request"||(string)p["code"]=="AUDIO_REQUESTED");break;
                case "audio_evidence":
                    DataJson.Keys(p,"audible_status","evidence_sha256","evidence_kind","observed_mono_ms");
                    DataJson.Require(c.AudioRequestId!=null&&new[]{"confirmed_audible","confirmed_no_onset"}.Contains(DataJson.Text(p["audible_status"]))&&DataJson.Hash(DataJson.Text(p["evidence_sha256"]))&&new[]{"acoustic_measurement","trusted_delivery_evidence"}.Contains(DataJson.Text(p["evidence_kind"])));DataJson.Number(p["observed_mono_ms"]);break;
                case "panel_process": case "panel_response":
                    DataJson.Keys(p,"kind","observed_mono_ms","mode","role","input","selected_target","selected_action","response_code","response_target","response_action");
                    DataJson.Require(c.AttemptId!=null&&c.AudioRequestId==null&&DataJson.Id(DataJson.Text(p["kind"])));DataJson.Number(p["observed_mono_ms"]);
                    DataJson.Require(new[]{"FullMessage","AtomicProbe","LessonAtomic","LessonMessage","Practice"}.Contains(DataJson.Text(p["mode"]))&&new[]{"Command","Action","Target"}.Contains(DataJson.Text(p["role"])));
                    foreach(string key in new[]{"input","selected_target","selected_action","response_target","response_action"}){string s=DataJson.OptionalText(p[key]);DataJson.Require(s==null||DataJson.Id(s));}
                    string response=DataJson.OptionalText(p["response_code"]);DataJson.Require(kind=="panel_process"?response==null:new[]{"commit","dont_know","timeout"}.Contains(response));break;
                case "device":
                    DataJson.Keys(p,"kind","observed_mono_ms","value","duration_ms","code");DataJson.Require(new[]{"headset","input","focus","reset","pause","frame_freeze","controller_exception"}.Contains(DataJson.Text(p["kind"])));DataJson.Number(p["observed_mono_ms"]);
                    DataJson.Require(p["value"].Type==JTokenType.Null||p["value"].Type==JTokenType.Boolean);DataJson.OptionalNumber(p["duration_ms"]);string code=DataJson.OptionalText(p["code"]);DataJson.Require(code==null||DataJson.Code(code));break;
                case "gain":
                    DataJson.Keys(p,"old_gain","new_gain","observed_mono_ms","reason");DataJson.Require(DataJson.Number(p["old_gain"])>0&&DataJson.Number(p["old_gain"])<=1&&DataJson.Number(p["new_gain"])>0&&DataJson.Number(p["new_gain"])<=1&&(string)p["reason"]=="comfort");DataJson.Number(p["observed_mono_ms"]);break;
                case "choice":
                    DataJson.Keys(p,"candidate_id","accepted_or_rejected","yoked_source_event_id","pause_ms","matching_deviation_id");DataJson.Require(c.AudioRequestId!=null&&DataJson.Id(DataJson.Text(p["candidate_id"]))&&new[]{"accepted","rejected","pending"}.Contains(DataJson.Text(p["accepted_or_rejected"])));
                    foreach(string key in new[]{"yoked_source_event_id","matching_deviation_id"}){string s=DataJson.OptionalText(p[key]);DataJson.Require(s==null||DataJson.Id(s));}DataJson.Number(p["pause_ms"]);break;
                case "visit_exit":DataJson.Keys(p,"code");DataJson.Require(c.OpportunityId==null&&DataJson.Code(DataJson.Text(p["code"])));break;
                case "recovery":
                    DataJson.Keys(p,"preserved_tails");DataJson.Require(c.OpportunityId==null&&p["preserved_tails"] is JArray tails&&tails.Count>0&&tails.Count<=32);foreach(JObject t in (JArray)p["preserved_tails"]){DataJson.Keys(t,"segment","tail_offset","tail_sha256","segment_sha256");DataJson.Require(Regex.IsMatch(DataJson.Text(t["segment"]),@"\Aevents-[0-9]{4}\.local\.jsonl\z")&&DataJson.Hash(DataJson.Text(t["tail_sha256"]))&&DataJson.Hash(DataJson.Text(t["segment_sha256"])));DataJson.Integer(t["tail_offset"]);}break;
                default:throw new DataFault("DATA_EVENT_KIND");
            }
        }
        static void Audio(JObject p)
        {
            DataJson.Keys(p,"code","audio_id","waveform_sha256","pcm_sha256","action_pcm_sha256","referent_pcm_sha256","observed_mono_ms","request_mono_ms","scheduled_mono_ms","scheduled_dsp_s","onset_estimate_mono_ms","onset_uncertainty_ms","first_callback_dsp_s","delivered_samples","callback_count");
            DataJson.Require(DataJson.Code(DataJson.Text(p["code"]))&&DataJson.Id(DataJson.Text(p["audio_id"]))&&DataJson.Hash(DataJson.Text(p["pcm_sha256"])));
            foreach(string k in new[]{"waveform_sha256","action_pcm_sha256","referent_pcm_sha256"}){string h=DataJson.OptionalText(p[k]);DataJson.Require(h==null||DataJson.Hash(h));}
            foreach(string k in new[]{"observed_mono_ms","request_mono_ms","scheduled_mono_ms","scheduled_dsp_s"})DataJson.Number(p[k]);
            foreach(string k in new[]{"onset_estimate_mono_ms","onset_uncertainty_ms","first_callback_dsp_s"})DataJson.OptionalNumber(p[k]);
            DataJson.Require((p["onset_estimate_mono_ms"].Type==JTokenType.Null)==(p["onset_uncertainty_ms"].Type==JTokenType.Null));
            DataJson.Integer(p["delivered_samples"]);DataJson.Integer(p["callback_count"]);
        }
    }
}
