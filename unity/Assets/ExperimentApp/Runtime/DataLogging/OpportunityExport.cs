using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Globalization;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.DataLogging
{
    // One row per journaled scheduled opportunity. An opportunity with no
    // attempt row in the trial log was never reached and is exported as
    // "unplayed"; the trial log keeps one row per actual attempt only.
    public static class OpportunityExport
    {
        public const string Table="opportunities.csv",Contract="opportunity-header-contract.json",Version="opportunities-provisional-1";
        public static readonly IReadOnlyList<string> Headers=Array.AsReadOnly(new[]{"schema_version","session_id","coded_id","visit_id","station_id","sequence","opportunity_id","block","pass","item_kind","role","method_masked","schedule_sha256","opportunity_status","attempt_ids"});
        internal static JObject HeaderContract()=>new JObject{["schema_version"]=Version,["qualified"]=false,["headers"]=new JArray(Headers),["trial_log_relation"]="attempt_ids_join_trial_log_attempt_id",["unplayed_meaning"]="no_attempt_row_for_the_opportunity"};
        static string Value(JToken x)=>x==null||x.Type==JTokenType.Null?"":x.Type==JTokenType.Integer?((long)x).ToString(CultureInfo.InvariantCulture):(string)x;
        public static IReadOnlyList<IReadOnlyDictionary<string,string>> Derive(JournalSnapshot snapshot,DataIdentity identity,DerivedTables tables=null)
        {
            tables??=DataDeriver.Derive(snapshot,identity);
            var attempts=tables.Trials.GroupBy(x=>x["opportunity_id"],StringComparer.Ordinal).ToDictionary(g=>g.Key,g=>g.Select(x=>x["attempt_id"]).ToArray(),StringComparer.Ordinal);
            var listed=new Dictionary<string,JObject>(StringComparer.Ordinal);var rows=new List<IReadOnlyDictionary<string,string>>();
            foreach(var record in snapshot.Records.Where(r=>r.Kind=="opportunity"))
            {
                var p=record.Payload;string id=record.Context.OpportunityId;
                if(listed.TryGetValue(id,out var prior)){DataJson.Require(JToken.DeepEquals(prior,p),"DATA_OPPORTUNITY_CHANGED");continue;}
                listed.Add(id,p);var row=Headers.ToDictionary(k=>k,k=>"",StringComparer.Ordinal);
                row["schema_version"]=Version;row["session_id"]=identity.SessionId;row["coded_id"]=identity.CodedId;row["visit_id"]=identity.VisitId;row["station_id"]=identity.StationId;
                row["sequence"]=record.Sequence.ToString(CultureInfo.InvariantCulture);row["opportunity_id"]=id;
                foreach(string key in new[]{"block","pass","item_kind","role","method_masked","schedule_sha256"})row[key]=Value(p[key]);
                var ids=attempts.TryGetValue(id,out var found)?found:Array.Empty<string>();
                row["opportunity_status"]=ids.Length==0?"unplayed":"attempted";row["attempt_ids"]=new JArray(ids).ToString(Formatting.None);
                rows.Add(new ReadOnlyDictionary<string,string>(row));
            }
            return rows.AsReadOnly();
        }
    }
}
