using System;
using System.Linq;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.StateIntegration
{
    // Transport-only health transaction. It never enters the command dispatcher
    // or grants readiness: the unchanged ControlHealthGate checks its payload.
    internal static class PrivateHealthProbe
    {
        static void Require(bool condition,string code="CONTROL_HEALTH_PROBE_SCHEMA")
        {if(!condition)throw new ControlFault(code);}
        static bool Id(string value)=>value!=null&&Regex.IsMatch(value,@"\A[0-9a-f]{32}\z");
        internal static JObject Request(string session,string requestId)
        {
            Require(Id(session)&&Id(requestId));
            return new JObject{["version"]=1,["kind"]="private_health_probe",["control_session_id"]=session,["request_id"]=requestId};
        }
        internal static JObject Payload(JObject value,string session,string expectedRequestId)
        {
            string[] keys={"version","kind","control_session_id","request_id","accepted","reason","health"};
            Require(Id(session)&&Id(expectedRequestId)&&value!=null&&value.Properties().Select(x=>x.Name).OrderBy(x=>x,StringComparer.Ordinal).SequenceEqual(keys.OrderBy(x=>x,StringComparer.Ordinal)));
            Require(value["version"].Type==JTokenType.Integer&&(long)value["version"]==1&&value["kind"].Type==JTokenType.String&&(string)value["kind"]=="private_health_reply");
            Require(value["control_session_id"].Type==JTokenType.String&&(string)value["control_session_id"]==session,"CONTROL_SESSION_CHANGED");
            Require(value["request_id"].Type==JTokenType.String&&(string)value["request_id"]==expectedRequestId,"CONTROL_HEALTH_PROBE_CORRELATION");
            Require(value["accepted"].Type==JTokenType.Boolean&&value["reason"].Type==JTokenType.String);
            string reason=(string)value["reason"];
            Require(new[]{"HEALTH","MALFORMED_PROBE","CONTROL_SESSION_MISMATCH","HEALTH_SESSION_CHANGED"}.Contains(reason));
            if(!(bool)value["accepted"])
            {
                Require(reason!="HEALTH"&&value["health"].Type==JTokenType.Null);
                throw new ControlFault("CONTROL_HEALTH_PROBE_REJECTED");
            }
            Require(reason=="HEALTH"&&value["health"] is JObject);
            return (JObject)value["health"];
        }
    }
}
