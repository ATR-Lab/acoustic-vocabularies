using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.Foundation;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.SelectionMenus
{
    public sealed class MenuLedgerBinding
    {
        public string PackageSha256 {get;}public string BankSha256 {get;}public string AllocationSha256 {get;}
        public string ScheduleSha256 {get;}public string UnitBindingSha256 {get;}public string ReviewSha256 {get;}
        public string Visit {get;}public string Role {get;}public IReadOnlyList<string> MenuKeys {get;}
        public MenuLedgerBinding(string package,string bank,string allocation,string schedule,string unitBinding,string review,string visit,string role,IEnumerable<string> menuKeys)
        {
            var keys=menuKeys?.ToArray();MenuRules.Require(new[]{package,bank,allocation,schedule,unitBinding,review}.All(MenuRules.Hash)&&new[]{"V1","V2","V3"}.Contains(visit)&&new[]{"active","yoked"}.Contains(role)&&keys!=null&&keys.Length==(visit=="V1"?9:4)&&keys.Distinct().Count()==keys.Length,"MENU_LEDGER_BINDING");
            MenuRules.Require(keys.Select((x,i)=>visit=="V1"&&i==0?x=="profile":System.Text.RegularExpressions.Regex.IsMatch(x??"",@"\A[KQ]-[ar][1-4]\z")).All(x=>x),"MENU_LEDGER_BINDING");
            PackageSha256=package;BankSha256=bank;AllocationSha256=allocation;ScheduleSha256=schedule;UnitBindingSha256=unitBinding;ReviewSha256=review;Visit=visit;Role=role;MenuKeys=Array.AsReadOnly(keys);
        }
        internal JObject Json()=>new JObject{["package_sha256"]=PackageSha256,["bank_sha256"]=BankSha256,["allocation_sha256"]=AllocationSha256,["schedule_sha256"]=ScheduleSha256,["unit_binding_sha256"]=UnitBindingSha256,["review_sha256"]=ReviewSha256,["visit"]=Visit,["role"]=Role,["menu_keys"]=new JArray(MenuKeys)};
    }
    internal static class MenuJson
    {
        internal static void Keys(JObject value,params string[] keys)=>MenuRules.Require(value!=null&&value.Properties().Select(x=>x.Name).OrderBy(x=>x,StringComparer.Ordinal).SequenceEqual(keys.OrderBy(x=>x,StringComparer.Ordinal)),"MENU_LEDGER_SCHEMA");
        internal static JToken Ordered(JToken token)=>token is JObject o?new JObject(o.Properties().OrderBy(x=>x.Name,StringComparer.Ordinal).Select(x=>new JProperty(x.Name,Ordered(x.Value)))):token is JArray a?new JArray(a.Select(Ordered)):token.DeepClone();
        internal static byte[] Bytes(JToken token)=>new UTF8Encoding(false,true).GetBytes(Ordered(token).ToString(Formatting.None));
        internal static JObject Parse(byte[] bytes)
        {MenuRules.Require(bytes!=null&&bytes.Length>0&&bytes.Length<=16384,"MENU_LEDGER_LINE");return StationConfig.ParseStrict(new UTF8Encoding(false,true).GetString(bytes));}
        internal static double Number(JToken value)
        {MenuRules.Require(value?.Type is JTokenType.Float or JTokenType.Integer,"MENU_LEDGER_SCHEMA");double result=(double)value;MenuRules.Require(MenuRules.Finite(result)&&result>=0,"MENU_LEDGER_SCHEMA");return result;}
        internal static int Integer(JToken value,int min,int max)
        {MenuRules.Require(value?.Type==JTokenType.Integer,"MENU_LEDGER_SCHEMA");long n=(long)value;MenuRules.Require(n>=min&&n<=max,"MENU_LEDGER_SCHEMA");return(int)n;}
        internal static JObject Event(MenuEvent value)
        {var row=new JObject{["kind"]=value.Kind,["event_id"]=value.EventId,["attempt_id"]=value.AttemptId,["opportunity_id"]=value.OpportunityId,["menu_key"]=value.MenuKey,["meaning_display_id"]=value.MeaningDisplayId,["slot_start_mono_ms"]=value.SlotStartMonoMs,["mono_ms"]=value.MonoMs,["expected_mono_ms"]=value.ExpectedMonoMs,["onset_uncertainty_ms"]=value.OnsetUncertaintyMs,["audio_request_id"]=value.AudioRequestId,["presentation_index"]=value.PresentationIndex,["candidate_id"]=value.CandidateId,["pcm_sha256"]=value.PcmSha256,["file_sha256"]=value.FileSha256,["yoked_source_event_id"]=value.YokedSourceEventId,["selected_index"]=value.SelectedIndex,["defaulted"]=value.Defaulted,["phase"]=value.Phase?.ToString(),["matching_deviation_id"]=value.MatchingDeviationId,["receipt_sha256"]=value.ReceiptSha256};
            foreach(var property in row.Properties().ToArray())if(property.Value is JValue v&&v.Value==null)property.Value=JValue.CreateNull();return row;
        }
        internal static void CheckEvent(JObject row)
        {
            Keys(row,"kind","event_id","attempt_id","opportunity_id","menu_key","meaning_display_id","slot_start_mono_ms","mono_ms","expected_mono_ms","onset_uncertainty_ms","audio_request_id","presentation_index","candidate_id","pcm_sha256","file_sha256","yoked_source_event_id","selected_index","defaulted","phase","matching_deviation_id","receipt_sha256");
            foreach(string field in new[]{"kind","event_id","attempt_id","opportunity_id","menu_key","meaning_display_id"})MenuRules.Require(row[field].Type==JTokenType.String,"MENU_LEDGER_SCHEMA");
            string kind=(string)row["kind"];MenuRules.Require(new[]{"menu_start","choice_revised","choice_final","selection_verified","display_request","display_changed","play_request","onset_authority","play_complete","menu_interrupted"}.Contains(kind)&&MenuRules.Guid((string)row["event_id"])&&MenuRules.Id((string)row["attempt_id"])&&MenuRules.Id((string)row["opportunity_id"])&&MenuRules.Id((string)row["menu_key"])&&MenuRules.Id((string)row["meaning_display_id"]),"MENU_LEDGER_SCHEMA");
            Number(row["slot_start_mono_ms"]);Number(row["mono_ms"]);if(row["expected_mono_ms"].Type!=JTokenType.Null)Number(row["expected_mono_ms"]);
            MenuRules.Require(kind=="onset_authority"?row["onset_uncertainty_ms"].Type!=JTokenType.Null:row["onset_uncertainty_ms"].Type==JTokenType.Null,"MENU_LEDGER_SCHEMA");
            if(kind=="onset_authority")MenuRules.Require(Number(row["onset_uncertainty_ms"])<=20,"MENU_LEDGER_SCHEMA");
            bool play=kind is "play_request" or "onset_authority" or "play_complete";
            MenuRules.Require(play==(row["presentation_index"].Type!=JTokenType.Null),"MENU_LEDGER_SCHEMA");
            foreach(string field in new[]{"audio_request_id","candidate_id","pcm_sha256","file_sha256"})MenuRules.Require(play?row[field].Type==JTokenType.String:row[field].Type==JTokenType.Null,"MENU_LEDGER_SCHEMA");
            if(play){Integer(row["presentation_index"],1,8);MenuRules.Require(MenuRules.Guid((string)row["audio_request_id"])&&MenuRules.Id((string)row["candidate_id"])&&MenuRules.Hash((string)row["pcm_sha256"])&&MenuRules.Hash((string)row["file_sha256"]),"MENU_LEDGER_SCHEMA");}
            MenuRules.Require(row["yoked_source_event_id"].Type==JTokenType.Null||row["yoked_source_event_id"].Type==JTokenType.String&&MenuRules.Guid((string)row["yoked_source_event_id"]),"MENU_LEDGER_SCHEMA");
            if(row["selected_index"].Type!=JTokenType.Null)Integer(row["selected_index"],1,3);
            MenuRules.Require(row["defaulted"].Type is JTokenType.Null or JTokenType.Boolean,"MENU_LEDGER_SCHEMA");
            MenuRules.Require(row["phase"].Type==JTokenType.Null||row["phase"].Type==JTokenType.String&&Enum.GetNames(typeof(MenuPhase)).Contains((string)row["phase"]),"MENU_LEDGER_SCHEMA");
            MenuRules.Require(row["receipt_sha256"].Type==JTokenType.Null||row["receipt_sha256"].Type==JTokenType.String&&MenuRules.Hash((string)row["receipt_sha256"]),"MENU_LEDGER_SCHEMA");
            bool hasExpected=kind is "menu_start" or "choice_final" or "play_request" or "onset_authority";
            MenuRules.Require(hasExpected==(row["expected_mono_ms"].Type!=JTokenType.Null),"MENU_LEDGER_SCHEMA");
            bool choice=kind is "choice_final" or "selection_verified",display=kind is "display_request" or "display_changed";
            MenuRules.Require((choice||kind=="choice_revised"||display&&(string)row["phase"]=="Selected")== (row["selected_index"].Type!=JTokenType.Null)&&choice==(row["defaulted"].Type!=JTokenType.Null)&&display==(row["phase"].Type!=JTokenType.Null)&&(kind=="selection_verified")== (row["receipt_sha256"].Type!=JTokenType.Null),"MENU_LEDGER_SCHEMA");
            MenuRules.Require(kind=="menu_interrupted"?row["matching_deviation_id"].Type==JTokenType.String&&(string)row["matching_deviation_id"]==(string)row["event_id"]:row["matching_deviation_id"].Type==JTokenType.Null,"MENU_LEDGER_SCHEMA");
            MenuRules.Require(play||choice||display||kind=="menu_start"||row["yoked_source_event_id"].Type==JTokenType.Null,"MENU_LEDGER_SCHEMA");
        }
        internal static void NoLinks(string path)
        {for(FileSystemInfo entry=new FileInfo(Path.GetFullPath(path));entry!=null;entry=entry is FileInfo f?f.Directory:((DirectoryInfo)entry).Parent)if(entry.Exists)MenuRules.Require((entry.Attributes&FileAttributes.ReparsePoint)==0,"MENU_LEDGER_LINK");}
    }
    // Fresh append-only segment. A partial/interrupted segment is preserved and
    // cannot be sealed/replayed. Reconstruction is an explicit separate action.
    public sealed class MenuLedger : IDisposable
    {
        readonly FileStream output;readonly MenuLedgerBinding binding;readonly List<JObject> records=new List<JObject>();
        string previous=new string('0',64);int sequence;double last=-1;bool failed,closed,sealedLedger;
        public string ChainHead=>previous;
        public MenuLedger(string newPath,MenuLedgerBinding binding,DateTimeOffset createdUtc)
        {
            MenuRules.Require(binding!=null&&createdUtc.Offset==TimeSpan.Zero,"MENU_LEDGER_BINDING");this.binding=binding;MenuJson.NoLinks(newPath);
            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(newPath)));MenuJson.NoLinks(newPath);
            output=new FileStream(newPath,FileMode.CreateNew,FileAccess.Write,FileShare.Read,4096,FileOptions.WriteThrough);
            try{Write(new JObject{["kind"]="header",["format"]="av-menu-ledger/1",["binding"]=binding.Json(),["created_utc"]=createdUtc.ToString("yyyy-MM-dd'T'HH:mm:ss.fff'Z'",CultureInfo.InvariantCulture),["clock_epoch"]=Guid.NewGuid().ToString("N")});}
            catch{output.Dispose();throw;}
        }
        void Write(JObject record)
        {
            MenuRules.Require(!failed&&!closed&&!sealedLedger&&sequence<10000&&output.Length<32*1024*1024,"MENU_LEDGER_UNAVAILABLE");
            try
            {
                var envelope=new JObject{["version"]=1,["sequence"]=sequence,["previous_sha256"]=previous,["record"]=record};string hash=PcmWave.Hash(MenuJson.Bytes(envelope));envelope["sha256"]=hash;
                byte[] line=MenuJson.Bytes(envelope);MenuRules.Require(line.Length<=16384,"MENU_LEDGER_LINE");output.Write(line,0,line.Length);output.WriteByte(10);MainThreadStages.Flush(output,"menu_ledger");previous=hash;sequence++;
            }
            catch{failed=true;throw new SessionFault("MENU_LEDGER_WRITE_FAILED");}
        }
        public void Append(MenuEvent value)
        {
            MenuRules.Require(value!=null,"MENU_LEDGER_EVENT");var row=MenuJson.Event(value);MenuJson.CheckEvent(row);
            MenuRules.Require(value.MonoMs>=last&&binding.MenuKeys.Contains(value.MenuKey),"MENU_LEDGER_SEQUENCE");
            Write(row);records.Add(row);last=value.MonoMs;if(value.Kind=="menu_interrupted")failed=true;
        }
        public void Seal(MenuLedgerVerification verification)
        {
            MenuReplaySequence.ValidateRecords(records,binding,verification);
            Write(new JObject{["kind"]="sealed",["menu_count"]=binding.MenuKeys.Count});sealedLedger=true;
        }
        public MenuReplayComparison SealYoked(MenuReplaySequence source,MenuLedgerVerification verification)
        {MenuRules.Require(source!=null,"MENU_YOKED_BINDING");var comparison=source.CompareYokedRecords(records,binding,verification);Write(new JObject{["kind"]="sealed_yoked",["menu_count"]=binding.MenuKeys.Count,["active_ledger_sha256"]=comparison.ActiveLedgerSha256,["yoked_event_sha256"]=comparison.YokedEventSha256});sealedLedger=true;return comparison;}
        public void Dispose(){if(closed)return;closed=true;try{output.Flush(true);}finally{output.Dispose();}}
    }
}
