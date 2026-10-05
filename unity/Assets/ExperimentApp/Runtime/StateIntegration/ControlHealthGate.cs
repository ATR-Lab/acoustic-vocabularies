using System;
using System.Linq;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.StateIntegration
{
    // Pure receipt freshness checker shared by the real transport and tests.
    // It is a validation boundary, not an authentication capability.
    internal sealed class ControlHealthGate
    {
        readonly string session,mode;readonly Func<double> now;
        double received=-1,host=-1,transit,neutralAge,publisherAge;
        bool good;
        internal ControlHealthGate(string session,string mode,Func<double> now){this.session=session;this.mode=mode;this.now=now;}
        internal bool Fresh => good&&now()>=received&&now()-received+transit+Math.Max(neutralAge,publisherAge)<=250;
        internal void Invalidate(){good=false;}
        static void Require(bool value,string code="CONTROL_SCHEMA"){if(!value)throw new ControlFault(code);}
        static double Number(JToken value)
        {Require(value?.Type is JTokenType.Integer or JTokenType.Float);double n=(double)value;Require(!double.IsNaN(n)&&!double.IsInfinity(n)&&n>=0);return n;}
        static bool Bool(JToken value){Require(value?.Type==JTokenType.Boolean);return(bool)value;}
        internal void Observe(JObject value,double sent,double arrived)
        {
            try
            {
                string[] keys={"control_session_id","mode","paused","stopped","fault","demo_active","publisher_ready","neutral_verification_age_ms","publisher_age_ms","health_sample_host_mono_ms","exposure_ready","public_stream_recovered"};
                Require(value!=null&&value.Properties().Select(x=>x.Name).OrderBy(x=>x,StringComparer.Ordinal).SequenceEqual(keys.OrderBy(x=>x,StringComparer.Ordinal)));
                Require(value["control_session_id"].Type==JTokenType.String&&(string)value["control_session_id"]==session,"CONTROL_SESSION_CHANGED");
                Require(value["mode"].Type==JTokenType.String&&new[]{"teaching","test","idle"}.Contains((string)value["mode"]));
                Require(value["fault"].Type==JTokenType.Null||value["fault"].Type==JTokenType.String);
                double sample=Number(value["health_sample_host_mono_ms"]);
                Require(!double.IsNaN(sent)&&!double.IsInfinity(sent)&&!double.IsNaN(arrived)&&!double.IsInfinity(arrived)&&sent>=0&&arrived>=sent&&arrived-sent<=250&&sample>=host,"CONTROL_STALE");
                bool paused=Bool(value["paused"]),stopped=Bool(value["stopped"]),demo=Bool(value["demo_active"]),publisher=Bool(value["publisher_ready"]),exposure=Bool(value["exposure_ready"]);Bool(value["public_stream_recovered"]);
                bool healthy=(string)value["mode"]==mode&&!paused&&!stopped&&value["fault"].Type==JTokenType.Null&&!demo&&publisher&&(mode!="test"||exposure);
                double neutral=value["neutral_verification_age_ms"].Type==JTokenType.Null?double.PositiveInfinity:Number(value["neutral_verification_age_ms"]);
                double published=value["publisher_age_ms"].Type==JTokenType.Null?double.PositiveInfinity:Number(value["publisher_age_ms"]);
                if(sample>host){good=healthy&&host>=0;host=sample;received=arrived;transit=arrived-sent;neutralAge=neutral;publisherAge=published;}
                else if(!healthy)good=false;
            }
            catch{good=false;throw;}
        }
    }
}
