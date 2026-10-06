using System;
using System.Collections.Generic;
using System.Linq;
using AcousticVocab.SessionEngine;
using AcousticVocab.Teaching;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.DataLogging
{
    // Supplemental software observations, never additional audible exposures.
    public static class LessonRecordCodec
    {
        public const string Version="lesson-events-provisional-1";
        internal static readonly string[] EventFields={"kind","attempt_id","opportunity_id","audio_request_id","presentation_index","observed_mono_ms","expected_mono_ms","meaning_display_id","feedback_content_id","highlight","pcm_sha256","action_pcm_sha256","referent_pcm_sha256"};
        internal static readonly string[] BindingFields={"schema_version","schedule_sha256","package_sha256","session_clock_epoch","lesson_type","slot_start_mono_ms","audio_request_ids"};
        internal static JObject Event(LessonEvent e)
        {
            var p=new JObject{
            ["kind"]=e.Kind,["attempt_id"]=e.AttemptId,["opportunity_id"]=e.OpportunityId,["audio_request_id"]=e.AudioRequestId,["presentation_index"]=e.PresentationIndex,
            ["observed_mono_ms"]=e.MonoMs,["expected_mono_ms"]=e.ExpectedMonoMs,["meaning_display_id"]=e.MeaningDisplayId,["feedback_content_id"]=e.FeedbackContentId,
            ["highlight"]=e.Highlight,["pcm_sha256"]=e.PcmSha256,["action_pcm_sha256"]=e.ActionPcmSha256,["referent_pcm_sha256"]=e.ReferentPcmSha256};
            foreach(var v in p.Properties().ToArray())if(v.Value.Type==JTokenType.String&&(string)v.Value==null)v.Value=JValue.CreateNull();
            return p;
        }
        public static void Validate(JObject p,EventContext c)
        {
            DataJson.Keys(p,BindingFields.Concat(EventFields).ToArray());
            DataJson.Require(DataJson.Text(p["schema_version"])==Version&&DataJson.Hash(DataJson.Text(p["schedule_sha256"]))&&DataJson.Hash(DataJson.Text(p["package_sha256"]))&&DataJson.Guid(DataJson.Text(p["session_clock_epoch"])),"DATA_LESSON_BINDING");
            string type=DataJson.Text(p["lesson_type"]),kind=DataJson.Text(p["kind"]);
            DataJson.Require(type=="atomic_lesson"||type=="message_lesson","DATA_LESSON_TYPE");
            DataJson.Require(new[]{"play_request","onset_authority","play_complete","display_request","display_start","display_end","retrieval_opportunity","retrieval_result","highlight_request","highlight","lesson_end","lesson_interrupted"}.Contains(kind),"DATA_LESSON_KIND");
            DataJson.Require(DataJson.Id(DataJson.Text(p["attempt_id"]))&&DataJson.Id(DataJson.Text(p["opportunity_id"]))&&c.AttemptId==(string)p["attempt_id"]&&c.OpportunityId==(string)p["opportunity_id"]&&c.AudioRequestId==DataJson.OptionalText(p["audio_request_id"]),"DATA_LESSON_CONTEXT");
            DataJson.Require(p["audio_request_ids"] is JArray ids&&ids.Count==3&&ids.All(x=>DataJson.Guid(DataJson.Text(x)))&&ids.Distinct(JToken.EqualityComparer).Count()==3,"DATA_LESSON_AUDIO_IDS");
            bool play=kind=="play_request"||kind=="onset_authority"||kind=="play_complete";
            if(play){long index=DataJson.Integer(p["presentation_index"]);DataJson.Require(index>=1&&index<=3&&c.AudioRequestId==(string)p["audio_request_ids"][(int)index-1],"DATA_LESSON_PRESENTATION");}
            else DataJson.Require(p["presentation_index"].Type==JTokenType.Null&&c.AudioRequestId==null,"DATA_LESSON_PRESENTATION");
            DataJson.Number(p["slot_start_mono_ms"]);DataJson.Number(p["observed_mono_ms"]);DataJson.OptionalNumber(p["expected_mono_ms"]);
            if(new[]{"play_request","onset_authority","display_start","display_end","retrieval_opportunity","lesson_end","lesson_interrupted"}.Contains(kind))DataJson.Require(p["expected_mono_ms"].Type!=JTokenType.Null,"DATA_LESSON_EXPECTED_TIME");
            DataJson.Require(DataJson.Id(DataJson.Text(p["meaning_display_id"]))&&DataJson.Hash(DataJson.Text(p["pcm_sha256"])),"DATA_LESSON_CONTENT");
            string feedback=DataJson.OptionalText(p["feedback_content_id"]),highlight=DataJson.OptionalText(p["highlight"]);
            DataJson.Require(feedback==null||DataJson.Id(feedback),"DATA_LESSON_CONTENT");
            DataJson.Require((kind=="highlight"||kind=="highlight_request")?new[]{"none","action","target"}.Contains(highlight):highlight==null,"DATA_LESSON_HIGHLIGHT");
            if(kind=="retrieval_result")DataJson.Require(feedback!=null,"DATA_LESSON_FEEDBACK");
            foreach(string key in new[]{"action_pcm_sha256","referent_pcm_sha256"}){string hash=DataJson.OptionalText(p[key]);DataJson.Require(hash==null||DataJson.Hash(hash),"DATA_LESSON_CONTENT");}
            DataJson.Require(type=="atomic_lesson"?p["action_pcm_sha256"].Type==JTokenType.Null&&p["referent_pcm_sha256"].Type==JTokenType.Null:p["action_pcm_sha256"].Type==JTokenType.String&&p["referent_pcm_sha256"].Type==JTokenType.String,"DATA_LESSON_ATOMS");
        }
        internal static string Key(JObject p)=>p["session_clock_epoch"]+":"+p["audio_request_ids"][0];
        internal static void MatchLoaded(JObject p,IEnumerable<DataRecord> prior)
        {
            var r=prior.LastOrDefault(x=>x.Kind=="session"&&x.Context.AttemptId==(string)p["attempt_id"]&&
                (string)x.Payload["event"]=="state_after"&&(string)x.Payload["state"]=="Loaded");
            DataJson.Require(r!=null,"DATA_LESSON_LOADED_REQUIRED");var s=r.Payload;
            DataJson.Require((string)s["schedule_sha256"]==(string)p["schedule_sha256"]&&(string)s["clock_epoch"]==(string)p["session_clock_epoch"]&&
                (string)s["opportunity_id"]==(string)p["opportunity_id"]&&JToken.DeepEquals(s["audio_request_ids"],p["audio_request_ids"])&&
                (double)s["scheduled_onset_mono_ms"]==(double)p["slot_start_mono_ms"]&&(double)p["observed_mono_ms"]>=(double)s["host_mono_ms"],"DATA_LESSON_LOADED_BINDING");
        }
    }
    public sealed class LessonDataJournal
    {
        readonly DataJournal journal;readonly VisitSchedule schedule;
        readonly Dictionary<string,JObject> bindings=new Dictionary<string,JObject>();
        readonly Dictionary<string,LessonTrace> traces=new Dictionary<string,LessonTrace>();bool failed;
        public LessonDataJournal(DataJournal journal,VisitSchedule schedule)
        {
            this.journal=journal??throw new ArgumentNullException(nameof(journal));this.schedule=schedule??throw new ArgumentNullException(nameof(schedule));
            // Recovered events are checked before any new context can be bound.
            var prior=new List<DataRecord>();foreach(var row in journal.Records){if(row.Kind=="lesson"){var p=row.Payload;DataJson.Require((string)p["schedule_sha256"]==schedule.Sha256&&(string)p["package_sha256"]==schedule.PackageSha256,"DATA_LESSON_BINDING");LessonRecordCodec.MatchLoaded(p,prior);Accept(p,row.ToJson());}prior.Add(row);}
        }
        void Accept(JObject p,JObject envelope)
        {string key=LessonRecordCodec.Key(p);if(!traces.TryGetValue(key,out var trace))traces.Add(key,trace=new LessonTrace());trace.Accept(p,envelope);}
        public void Bind(SlotContext context)
        {
            try
            {
                DataJson.Require(!failed&&!journal.Failed&&!journal.Closed,"DATA_LESSON_UNAVAILABLE");var c=context;var item=c.Item;
                DataJson.Require(item!=null&&!item.Heldout&&item.Phase=="teaching"&&item.Plays==3&&(item.TrialType=="atomic_lesson"||item.TrialType=="message_lesson"),"DATA_LESSON_TYPE");
                var original=schedule.Blocks.SelectMany(x=>x.Items).SingleOrDefault(x=>x.TrialId==c.OpportunityId);
                DataJson.Require(original!=null&&original.TrialType==item.TrialType&&original.ContentId==item.ContentId&&original.Phase==item.Phase&&original.SlotSeconds==item.SlotSeconds&&!original.Heldout&&original.Plays==3,"DATA_LESSON_SCHEDULE");
                var loaded=journal.Records.LastOrDefault(x=>x.Kind=="session"&&x.Context.AttemptId==item.TrialId&&(string)x.Payload["event"]=="state_after"&&(string)x.Payload["state"]=="Loaded");
                DataJson.Require(loaded!=null,"DATA_LESSON_LOADED_REQUIRED");var p=new JObject{["schema_version"]=LessonRecordCodec.Version,["schedule_sha256"]=schedule.Sha256,["package_sha256"]=schedule.PackageSha256,["session_clock_epoch"]=loaded.Payload["clock_epoch"].DeepClone(),["lesson_type"]=item.TrialType,["slot_start_mono_ms"]=c.OnsetMonoMs,["audio_request_ids"]=new JArray(c.AudioRequestIds)};
                DataJson.Require(JToken.DeepEquals(loaded.Payload["audio_request_ids"],p["audio_request_ids"])&&(double)loaded.Payload["scheduled_onset_mono_ms"]==c.OnsetMonoMs,"DATA_LESSON_LOADED_BINDING");
                bindings[item.TrialId]=p;
            }
            catch{failed=true;throw;}
        }
        public void Append(LessonEvent value)
        {
            try
            {
                DataJson.Require(!failed&&value!=null&&bindings.ContainsKey(value.AttemptId),"DATA_LESSON_UNAVAILABLE");var p=(JObject)bindings[value.AttemptId].DeepClone();
                foreach(var pair in LessonRecordCodec.Event(value))p.Add(pair.Key,pair.Value);
                var context=new EventContext(value.OpportunityId,value.AttemptId,value.AudioRequestId);LessonRecordCodec.Validate(p,context);LessonRecordCodec.MatchLoaded(p,journal.Records);
                // Validate before append; any sink failure latches this adapter.
                Accept(p,null);journal.Append(new EventDraft("lesson",context,p));
            }
            catch{failed=true;throw;}
        }
    }
    internal sealed class LessonTrace
    {
        internal readonly List<(JObject Payload,JObject Envelope)> Events=new List<(JObject,JObject)>();
        readonly HashSet<string> once=new HashSet<string>();JObject first;double last=-1;string epoch;int plays;string openDisplay,feedback;bool retrieval,ended;
        internal void Accept(JObject p,JObject envelope)
        {
            LessonRecordCodec.Validate(p,new EventContext((string)p["opportunity_id"],(string)p["attempt_id"],(string)p["audio_request_id"]));
            if(envelope!=null){string next=(string)envelope["clock_epoch"];DataJson.Require(epoch==null||epoch==next,"DATA_LESSON_CLOCK_EPOCH");epoch=next;}
            double now=(double)p["observed_mono_ms"];DataJson.Require(now>=last,"DATA_LESSON_CLOCK");last=now;
            if(first==null)first=(JObject)p.DeepClone();else foreach(string k in LessonRecordCodec.BindingFields.Concat(new[]{"attempt_id","opportunity_id","meaning_display_id","pcm_sha256","action_pcm_sha256","referent_pcm_sha256"}))DataJson.Require(JToken.DeepEquals(first[k],p[k]),"DATA_LESSON_CHANGED");
            string kind=(string)p["kind"],index=p["presentation_index"].ToString(),f=(string)p["feedback_content_id"];
            DataJson.Require(!ended||kind=="highlight"&&(string)p["highlight"]=="none","DATA_LESSON_AFTER_END");
            if(kind=="play_request"){DataJson.Require((int)p["presentation_index"]==++plays,"DATA_LESSON_PLAY_ORDER");}
            if(new[]{"play_request","onset_authority","play_complete"}.Contains(kind))
            {DataJson.Require(once.Add(kind+index)&& (kind=="play_request"||once.Contains("play_request"+index))&&(kind!="play_complete"||once.Contains("onset_authority"+index)),"DATA_LESSON_DUPLICATE_OR_ORDER");}
            if(kind=="display_start"){string key=f==null?"definition":"feedback";DataJson.Require(openDisplay==null&&once.Add("display:"+key)&&(f==null||retrieval&&f==feedback),"DATA_LESSON_DISPLAY_ORDER");openDisplay=key;}
            if(kind=="display_end"){DataJson.Require(openDisplay==(f==null?"definition":"feedback"),"DATA_LESSON_DISPLAY_ORDER");openDisplay=null;}
            if(kind=="retrieval_opportunity"){DataJson.Require(!retrieval&&openDisplay==null&&once.Contains("display:definition"),"DATA_LESSON_RETRIEVAL_ORDER");retrieval=true;}
            if(kind=="retrieval_result"){DataJson.Require(retrieval&&feedback==null,"DATA_LESSON_RETRIEVAL_ORDER");feedback=f;}
            if(kind=="lesson_end"||kind=="lesson_interrupted"){DataJson.Require(!ended&&openDisplay==null,"DATA_LESSON_END_ORDER");ended=true;}
            Events.Add(((JObject)p.DeepClone(),envelope==null?null:(JObject)envelope.DeepClone()));
        }
    }
}
