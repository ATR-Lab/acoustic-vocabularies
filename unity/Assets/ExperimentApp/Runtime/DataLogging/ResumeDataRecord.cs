using System;
using System.Linq;
using AcousticVocab.SessionEngine;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.DataLogging
{
    // IResumeRecord on the shared visit journal. `visit_start` is appended
    // before the battery's first accepted start; `visit_exit` closes it after a
    // refused resumption. Both rows share the session records' hash chain.
    public sealed class ResumeDataRecord : IResumeRecord
    {
        readonly DataJournal journal;
        public ResumeDataRecord(DataJournal journal){this.journal=journal??throw new ArgumentNullException(nameof(journal));}
        public ResumeAnchor Anchor
        {
            get
            {
                var row=journal.Records.FirstOrDefault(r=>r.Kind=="visit_start");if(row==null)return null;var p=row.Payload;
                try{return new ResumeAnchor((string)p["session_id"],(string)p["local_date"]);}
                catch(SessionFault){throw new DataFault("DATA_RESUME_ANCHOR");}
            }
        }
        public string ClosedCode=>journal.Records.Where(r=>r.Kind=="visit_exit").Select(r=>(string)r.Payload["code"]).FirstOrDefault();
        public void RecordAnchor(ResumeAnchor anchor)
        {
            DataJson.Require(anchor!=null&&Anchor==null&&anchor.SessionId==journal.Identity.SessionId,"DATA_RESUME_ANCHOR");
            journal.Append(new EventDraft("visit_start",null,new JObject{["session_id"]=anchor.SessionId,["local_date"]=anchor.LocalDate}));
        }
        public void RecordClosed(string code)=>journal.Append(new EventDraft("visit_exit",null,new JObject{["code"]=code}));
    }
}
