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
        // Decoded once per appended record, as SessionDataJournal: forms code
        // reads this per frame and the append-only journal never rewrites rows.
        readonly List<AssessmentRecord> decoded=new List<AssessmentRecord>();int scanned;
        public IReadOnlyList<AssessmentRecord> Records
        {
            get
            {
                var source=journal.Records;
                for(;scanned<source.Count;scanned++)if(source[scanned].Kind=="assessment_stage")decoded.Add(AssessmentRecordCodec.FromJson(source[scanned].Payload));
                return Array.AsReadOnly(decoded.ToArray());
            }
        }
        public void Append(AssessmentRecord record)
        {
            DataJson.Require(record!=null&&record.ScheduleSha256==schedule,"DATA_ASSESSMENT_SCHEDULE");
            journal.Append(new EventDraft("assessment_stage",new EventContext(),AssessmentRecordCodec.ToJson(record)));
        }
    }
}
