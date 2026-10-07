using System;
using System.Globalization;
using System.Linq;
using System.Text;
using AcousticVocab.Assessment;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.DataLogging
{
    internal static class AssessmentExport
    {
        internal const string Table="assessment-stages.csv",Contract="assessment-header-contract.json";
        internal static readonly string[] Headers={"schema_version","session_id","coded_id","visit_id","station_id","sequence","schedule_sha256","clock_epoch","host_mono_ms","event_kind","stage","item_id","value","outcome_code"};
        internal static JObject HeaderContract()=>new JObject{["schema_version"]="assessment-stage-csv-provisional-1",["qualified"]=false,["headers"]=new JArray(Headers)};
        internal static byte[] Csv(JournalSnapshot snapshot,DataIdentity id)
        {
            string Cell(string s){s??="";return s.IndexOfAny(new[]{',','"','\r','\n'})<0?s:"\""+s.Replace("\"","\"\"")+"\"";}
            var text=new StringBuilder(string.Join(",",Headers)+"\n");
            foreach(var row in snapshot.Records.Where(r=>r.Kind=="assessment_stage"))
            {
                var r=AssessmentRecordCodec.FromJson(row.Payload);
                text.Append(string.Join(",",new[]{"assessment-stage-csv-provisional-1",id.SessionId,id.CodedId,id.VisitId,id.StationId,
                    row.Sequence.ToString(CultureInfo.InvariantCulture),r.ScheduleSha256,r.ClockEpoch,r.HostMonoMs.ToString("R",CultureInfo.InvariantCulture),
                    r.EventKind,r.Stage,r.ItemId,r.Value?.ToString(CultureInfo.InvariantCulture),r.OutcomeCode}.Select(Cell))).Append('\n');
            }
            return new UTF8Encoding(false,true).GetBytes(text.ToString());
        }
    }
}
