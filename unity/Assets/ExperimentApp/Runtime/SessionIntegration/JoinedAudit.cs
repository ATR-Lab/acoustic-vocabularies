using System;
using System.IO;
using System.Text;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using AcousticVocab.Teaching;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
namespace AcousticVocab.SessionIntegration
{
    // Supplemental private module/control evidence. Session/audio/panel/frame
    // events use DataJournal; these records are not trial/exposure CSV rows.
    internal sealed class JoinedAudit:IDisposable
    {
        readonly FileStream output;readonly Func<double> clock;readonly string epoch=Guid.NewGuid().ToString("N");string previous=new string('0',64);long sequence;double last=-1;bool failed,closed;
        internal JoinedAudit(string freshPath,Func<double> clock){this.clock=clock;output=new FileStream(freshPath,FileMode.CreateNew,FileAccess.Write,FileShare.Read,4096,FileOptions.WriteThrough);}
        internal void Write(string kind,JObject payload)
        {
            if(closed||failed)throw new SessionFault("JOIN_AUDIT_UNAVAILABLE");
            try
            {
                double now=clock();if(!double.IsFinite(now)||now<last||sequence>=100000||output.Length>32*1024*1024||payload==null||
                    kind is not ("configuration" or "control" or "store" or "lesson" or "module" or "fault" or "view" or "simulation_input" or "simulation_fault"))throw new SessionFault("JOIN_AUDIT_INVALID");
                var record=new JObject{["version"]=1,["clock_epoch"]=epoch,["sequence"]=sequence,["host_mono_ms"]=now,["previous_sha256"]=previous,["kind"]=kind,["payload"]=payload.DeepClone()};
                byte[] body=Encoding.UTF8.GetBytes(record.ToString(Formatting.None));if(body.Length>65536)throw new SessionFault("JOIN_AUDIT_LIMIT");string hash=PcmWave.Hash(body);record["sha256"]=hash;
                byte[] bytes=Encoding.UTF8.GetBytes(record.ToString(Formatting.None)+"\n");output.Write(bytes,0,bytes.Length);output.Flush(true);previous=hash;sequence++;last=now;
            }
            catch{failed=true;throw;}
        }
        internal void Lesson(LessonEvent e)=>Write("lesson",new JObject{["kind"]=e.Kind,["attempt_id"]=e.AttemptId,["opportunity_id"]=e.OpportunityId,["audio_request_id"]=e.AudioRequestId,
            ["presentation_index"]=e.PresentationIndex,["observed_mono_ms"]=e.MonoMs,["expected_mono_ms"]=e.ExpectedMonoMs,["meaning_display_id"]=e.MeaningDisplayId,
            ["feedback_content_id"]=e.FeedbackContentId,["highlight"]=e.Highlight,["pcm_sha256"]=e.PcmSha256,["action_pcm_sha256"]=e.ActionPcmSha256,["referent_pcm_sha256"]=e.ReferentPcmSha256});
        public void Dispose(){if(closed)return;closed=true;try{output.Flush(true);}finally{output.Dispose();}}
    }
}
