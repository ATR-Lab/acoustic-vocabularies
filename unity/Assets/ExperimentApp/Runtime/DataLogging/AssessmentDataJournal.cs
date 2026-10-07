using System;
using System.Collections.Generic;
using System.Linq;
using AcousticVocab.Assessment;

namespace AcousticVocab.DataLogging
{
    public sealed class AssessmentDataJournal:IAssessmentJournal
    {
        readonly DataJournal journal;readonly string schedule;
        public AssessmentDataJournal(DataJournal journal,string scheduleSha256)
        {
            this.journal=journal??throw new ArgumentNullException(nameof(journal));
            DataJson.Require(DataJson.Hash(scheduleSha256),"DATA_ASSESSMENT_SCHEDULE");schedule=scheduleSha256;
            DataJson.Require(Records.All(r=>r.ScheduleSha256==schedule),"DATA_ASSESSMENT_SCHEDULE");
        }
        public IReadOnlyList<AssessmentRecord> Records=>journal.Records.Where(r=>r.Kind=="assessment_stage").Select(r=>AssessmentRecordCodec.FromJson(r.Payload)).ToList().AsReadOnly();
        public void Append(AssessmentRecord record)
        {
            DataJson.Require(record!=null&&record.ScheduleSha256==schedule,"DATA_ASSESSMENT_SCHEDULE");
            journal.Append(new EventDraft("assessment_stage",new EventContext(),AssessmentRecordCodec.ToJson(record)));
        }
    }
}
