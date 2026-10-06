using System;
using System.Linq;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.StateIntegration
{
    // Pure receipt freshness checker shared by the real transport and tests.
    // It is a validation boundary, not an authentication capability.
    internal sealed class ControlHealthGate
    {
        internal const int CommandDeadlineMs=3000;
        readonly string session,mode;readonly Func<double> now;
        double received=-1,host=-1,transit,neutralAge,publisherAge;
        bool good;
        internal ControlHealthGate(string session,string mode,Func<double> now){this.session=session;this.mode=mode;this.now=now;}
        internal bool Fresh => good&&now()>=received&&now()-received+transit+Math.Max(neutralAge,publisherAge)<=250;
        internal JObject Diagnostic()
        {
            double current=now(),elapsed=received<0?double.NaN:current-received,bound=elapsed+transit+Math.Max(neutralAge,publisherAge);
            JToken Finite(double value)=>double.IsFinite(value)?new JValue(value):JValue.CreateNull();
            return new JObject{["health_progressing_and_valid"]=good,["observed_local_mono_ms"]=Finite(current),["receipt_elapsed_ms"]=Finite(elapsed),
                ["round_trip_ms"]=Finite(transit),["neutral_age_ms"]=Finite(neutralAge),["publisher_age_ms"]=Finite(publisherAge),["effective_age_ms"]=Finite(bound),["maximum_age_ms"]=250};
        }
        internal void Invalidate(){good=false;}
        static void Require(bool value,string code="CONTROL_SCHEMA"){if(!value)throw new ControlFault(code);}
        static double Number(JToken value)
        {Require(value?.Type is JTokenType.Integer or JTokenType.Float);double n=(double)value;Require(!double.IsNaN(n)&&!double.IsInfinity(n)&&n>=0);return n;}
        static bool Bool(JToken value){Require(value?.Type==JTokenType.Boolean);return(bool)value;}
        readonly struct Sample
        {
            internal readonly double Host,NeutralAge,PublisherAge;internal readonly bool Healthy;
            internal Sample(double host,double neutral,double publisher,bool healthy){Host=host;NeutralAge=neutral;PublisherAge=publisher;Healthy=healthy;}
        }
        // Completion and live probes share the entire strict payload parser.
        // A command's durable work may take longer than a freshness window;
        // its receipt is history only and never supplies exposure timestamps.
        Sample Parse(JObject value,double sent,double arrived,int deadline)
        {
            string[] keys={"control_session_id","mode","paused","stopped","fault","demo_active","publisher_ready","neutral_verification_age_ms","publisher_age_ms","health_sample_host_mono_ms","exposure_ready","public_stream_recovered"};
            Require(value!=null&&value.Properties().Select(x=>x.Name).OrderBy(x=>x,StringComparer.Ordinal).SequenceEqual(keys.OrderBy(x=>x,StringComparer.Ordinal)));
            Require(value["control_session_id"].Type==JTokenType.String&&(string)value["control_session_id"]==session,"CONTROL_SESSION_CHANGED");
            Require(value["mode"].Type==JTokenType.String&&new[]{"teaching","test","idle"}.Contains((string)value["mode"]));
            Require(value["fault"].Type==JTokenType.Null||value["fault"].Type==JTokenType.String);
            double sample=Number(value["health_sample_host_mono_ms"]);
            Require(double.IsFinite(sent)&&double.IsFinite(arrived)&&sent>=0&&arrived>=sent&&arrived-sent<=deadline&&sample>=host,"CONTROL_STALE");
            bool paused=Bool(value["paused"]),stopped=Bool(value["stopped"]),demo=Bool(value["demo_active"]),publisher=Bool(value["publisher_ready"]),exposure=Bool(value["exposure_ready"]);Bool(value["public_stream_recovered"]);
            bool healthy=(string)value["mode"]==mode&&!paused&&!stopped&&value["fault"].Type==JTokenType.Null&&!demo&&publisher&&(mode!="test"||exposure);
            double neutral=value["neutral_verification_age_ms"].Type==JTokenType.Null?double.PositiveInfinity:Number(value["neutral_verification_age_ms"]);
            double published=value["publisher_age_ms"].Type==JTokenType.Null?double.PositiveInfinity:Number(value["publisher_age_ms"]);
            return new Sample(sample,neutral,published,healthy);
        }
        internal double ObserveCommandCompletion(JObject value,double sent,double arrived)
        {
            good=false;
            var sample=Parse(value,sent,arrived,CommandDeadlineMs);
            host=sample.Host;
            return sample.Host;
        }
        internal void Observe(JObject value,double sent,double arrived)
        {
            try
            {
                var sample=Parse(value,sent,arrived,250);
                if(sample.Host>host){good=sample.Healthy&&host>=0;host=sample.Host;received=arrived;transit=arrived-sent;neutralAge=sample.NeutralAge;publisherAge=sample.PublisherAge;}
                else if(!sample.Healthy)good=false;
            }
            catch{good=false;throw;}
        }
    }
}
