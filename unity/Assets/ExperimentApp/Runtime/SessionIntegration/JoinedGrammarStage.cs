using System;
using System.Collections.Generic;
using System.Linq;
using AcousticVocab.DataLogging;
using AcousticVocab.OperatorConsole;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using AcousticVocab.Teaching;
using Newtonsoft.Json.Linq;
namespace AcousticVocab.SessionIntegration
{
    public sealed class JoinedGrammarReview
    {
        public string Sha256{get;}public string RegistrySha256{get;}
        JoinedGrammarReview(string hash,string registry){Sha256=hash;RegistrySha256=registry;}
        public static JoinedGrammarReview Load(byte[] bytes,string rawSha,string registrySha,string methodologySha)
        {
            if(bytes==null||PcmWave.Hash(bytes)!=rawSha)throw new SessionFault("JOIN_GRAMMAR_REVIEW_HASH");
            var p=JoinedVisitArtifacts.Json(bytes);
            var expected=new JObject{["version"]=1,["approved"]=true,["methodology_sha256"]=methodologySha,["registry_sha256"]=registrySha,
                ["boundary"]="before_first_teaching_block",["repeat_policy"]="once_per_visit_no_partial_replay",["scheduling_lead_ms"]=750,
                ["completion_authority"]="software_delivery_only",["labels"]=new JObject{["ready"]="READY",["action"]="Action",["target"]="Target"}};
            if(!JToken.DeepEquals(p,expected))throw new SessionFault("JOIN_GRAMMAR_REVIEW");return new JoinedGrammarReview(rawSha,registrySha);
        }
    }
    // The same fsynced raw journal survives reconstruction. Incomplete reserved
    // plays are never silently replayed; only a fully ordered finished sequence
    // can skip this stage after restart. This does not grant acoustic authority.
    public sealed class JoinedGrammarStage
    {
        readonly DataJournal journal;readonly GrammarAssets assets;readonly JoinedGrammarReview review;readonly string schedule,epoch=Guid.NewGuid().ToString("N");readonly Func<double> now;
        readonly List<JObject> records=new List<JObject>();State state;bool failed;readonly bool recoveredPartial;
        sealed class State{internal bool Started,Finished,Interrupted,Requested,Delivered;internal int Completed;internal string Id;}
        public bool Complete=>!failed&&state.Finished;
        public bool Started=>state.Started;
        public bool Running=>Started&&!Complete&&!failed&&!recoveredPartial;
        public JoinedGrammarStage(DataJournal journal,GrammarAssets assets,JoinedGrammarReview review,string scheduleSha,Func<double> clock)
        {
            this.journal=journal??throw new ArgumentNullException(nameof(journal));this.assets=assets??throw new ArgumentNullException(nameof(assets));this.review=review??throw new ArgumentNullException(nameof(review));schedule=scheduleSha;now=clock??throw new ArgumentNullException(nameof(clock));
            records.AddRange(journal.Records.Where(r=>r.Kind=="grammar_stage").Select(r=>r.Payload));state=Reduce(records);recoveredPartial=records.Count>0&&!state.Finished;
            if(recoveredPartial)throw new SessionFault("JOIN_GRAMMAR_RECOVERY_REQUIRED");
        }
        static void Need(bool ok){if(!ok)throw new SessionFault("JOIN_GRAMMAR_HISTORY");}
        State Reduce(IEnumerable<JObject> rows)
        {
            var s=new State();var ids=new HashSet<string>();string executionEpoch=null;
            foreach(var p in rows)
            {
                GrammarStageCodec.Validate(p);Need((string)p["schedule_sha256"]==schedule&&(string)p["registry_sha256"]==assets.RegistrySha256&&(string)p["review_sha256"]==review.Sha256&&!s.Finished);
                string kind=(string)p["kind"],id=(string)p["audio_request_id"],phase=(string)p["phase"];
                if(kind=="started"){Need(!s.Started);s.Started=true;executionEpoch=(string)p["clock_epoch"];continue;}
                Need(s.Started&&(string)p["clock_epoch"]==executionEpoch);
                if(kind=="interrupted"){s.Interrupted=true;continue;}
                Need(!s.Interrupted);
                if(kind=="finished"){Need(s.Completed==2&&s.Id==null);s.Finished=true;continue;}
                Need(s.Completed<2&&phase==(s.Completed==0?"ready":"clicks"));
                if(kind=="request"){Need(s.Id==null&&ids.Add(id));s.Id=id;s.Requested=false;s.Delivered=false;continue;}
                Need(id==s.Id&&id!=null);
                if(kind=="completed"){Need(s.Requested&&s.Delivered);s.Completed++;s.Id=null;continue;}
                var a=(JObject)p["audio"];var wave=s.Completed==0?assets.Ready:assets.Clicks;
                Need((string)a["waveform_sha256"]==wave.FileSha256&&(string)a["pcm_sha256"]==wave.PcmSha256&&(string)a["action_pcm_sha256"]==null&&(string)a["referent_pcm_sha256"]==null);
                string code=(string)a["code"];
                if(code=="AUDIO_REQUESTED"){Need(!s.Requested);s.Requested=true;}
                else{Need(s.Requested&&!s.Delivered);if(code=="AUDIO_PLAYBACK_COMPLETED"){Need((long)a["callback_count"]>0&&(long)a["delivered_samples"]==wave.SampleCount&&a["first_callback_dsp_s"].Type!=JTokenType.Null);s.Delivered=true;}
                    else if(code!="AUDIO_ONSET_ESTIMATED")s.Interrupted=true;}
            }
            return s;
        }
        void Append(string kind,string phase=null,string id=null,JObject audio=null,JObject command=null,double? observed=null)
        {
            if(failed||recoveredPartial)throw new SessionFault("JOIN_GRAMMAR_UNAVAILABLE");
            try
            {
                var row=new JObject{["version"]=1,["schedule_sha256"]=schedule,["registry_sha256"]=assets.RegistrySha256,["review_sha256"]=review.Sha256,["clock_epoch"]=epoch,["host_mono_ms"]=observed??now(),["kind"]=kind,["phase"]=phase,["audio_request_id"]=id,["operator_command"]=command,["audio"]=audio};
                var next=Reduce(records.Concat(new[]{row}));journal.Append(new EventDraft("grammar_stage",null,row));records.Add(row);state=next;
            }
            catch{failed=true;throw;}
        }
        public void Begin(OperatorRequest command)
        {if(command==null||command.Command is not ("start" or "resume"))throw new SessionFault("JOIN_GRAMMAR_OPERATOR_REQUIRED");Append("started",command:new JObject{["request_id"]=command.RequestId,["sequence"]=command.Sequence,["session_nonce"]=command.SessionNonce});}
        public void Observe(JObject value)
        {
            if(value==null||!value.Properties().Select(p=>p.Name).OrderBy(n=>n).SequenceEqual(new[]{"kind","audio_request_id","host_mono_ms","registry_sha256","calibration_only"}.OrderBy(n=>n))||(string)value["registry_sha256"]!=assets.RegistrySha256||value["calibration_only"]?.Type!=JTokenType.Boolean||(bool)value["calibration_only"])throw new SessionFault("JOIN_GRAMMAR_OBSERVATION");
            string kind=(string)value["kind"];
            if(kind=="grammar_interrupted"){Append("interrupted",observed:(double)value["host_mono_ms"]);return;}
            if(kind is not ("grammar_request" or "grammar_complete"))throw new SessionFault("JOIN_GRAMMAR_OBSERVATION");
            Append(kind=="grammar_request"?"request":"completed",state.Completed==0?"ready":"clicks",(string)value["audio_request_id"],observed:(double)value["host_mono_ms"]);
        }
        public void Audio(AudioPlaybackEvent value)
        {var wave=state.Completed==0?assets.Ready:assets.Clicks;Append("audio",state.Completed==0?"ready":"clicks",value.AudioId,GrammarStageCodec.Audio(value,wave.FileSha256),observed:value.ObservedMonoSeconds*1000);}
        public void Finish(){if(!Complete)Append("finished");}
        public void RequireBeforeTeachingHistory(IEnumerable<SessionRecord> history,IEnumerable<string> teachingOpportunities)
        {
            var ids=new HashSet<string>(teachingOpportunities??throw new ArgumentNullException(nameof(teachingOpportunities)));
            if(!Complete&&history.Any(r=>r.ExposureConsumed&&r.OpportunityId!=null&&ids.Contains(r.OpportunityId)))throw new SessionFault("JOIN_GRAMMAR_HISTORY_MISSING");
        }
    }
}
