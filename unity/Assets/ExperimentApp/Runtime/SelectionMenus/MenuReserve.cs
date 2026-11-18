using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.SelectionMenus
{
    // REQ-BANK-05 reserve rule. A cell's rank-4 reserve may stand in for one shown
    // option of an atom menu only when that option's cached file is unusable
    // (missing or changed) and no ledger holds any event of that menu. Only the
    // active session decides, only from a verified qualified bank, and its ledger
    // records the amendment before any display or audio of that menu. The assigned
    // session never decides: it reproduces the active ledger's record exactly.
    public interface IMenuPresentationHistory
    {
        // True when any ledger segment of this visit holds an event of the menu.
        bool Presented(string menuKey);
    }

    // The journaled bank amendment: which option the reserve replaced, why, and
    // the reserve's bank identity and hashes.
    public sealed class MenuAmendment
    {
        public const string Kind="menu_amended";
        static readonly string[] RowKeys={"kind","event_id","attempt_id","opportunity_id","menu_key","meaning_display_id","slot_start_mono_ms","mono_ms","yoked_source_event_id",
            "bank_sha256","profile","replaced_rank","replaced_candidate_id","reason","reserve_option_id","reserve_candidate_id","reserve_pcm_sha256","reserve_file_sha256","reserve_recipe_sha256"};
        public string BankSha256{get;}public string Profile{get;}public string MenuKey{get;}public int ReplacedRank{get;}public string Reason{get;}
        public string ReplacedCandidateId=>Candidate(Profile,MenuKey,ReplacedRank);public string ReserveCandidateId=>Candidate(Profile,MenuKey,4);
        public string ReserveOptionId{get;}public string ReservePcmSha256{get;}public string ReserveFileSha256{get;}public string ReserveRecipeSha256{get;}
        internal MenuAmendment(string bank,string profile,string key,int rank,string reason,string reserveOption,string pcm,string file,string recipe)
        {
            MenuRules.Require(MenuRules.Hash(bank)&&new[]{"P1","P2","P3"}.Contains(profile)&&key!=null&&Regex.IsMatch(key,@"\A[KQ]-[ar][1-4]\z")&&rank>=1&&rank<=3&&
                (reason==LoadedAudioPackage.OptionFileMissing||reason==LoadedAudioPackage.OptionHashMismatch)&&reserveOption!=null&&Regex.IsMatch(reserveOption,@"\A[A-Za-z0-9][A-Za-z0-9._-]{0,127}\z")&&new[]{pcm,file,recipe}.All(MenuRules.Hash),"MENU_LEDGER_AMENDMENT");
            BankSha256=bank;Profile=profile;MenuKey=key;ReplacedRank=rank;Reason=reason;ReserveOptionId=reserveOption;ReservePcmSha256=pcm;ReserveFileSha256=file;ReserveRecipeSha256=recipe;
        }
        internal static string Candidate(string profile,string key,int rank)=>profile+"-"+key+"-"+rank;
        public bool SameAs(MenuAmendment other)=>other!=null&&other.BankSha256==BankSha256&&other.Profile==Profile&&other.MenuKey==MenuKey&&other.ReplacedRank==ReplacedRank&&other.Reason==Reason&&
            other.ReserveOptionId==ReserveOptionId&&other.ReservePcmSha256==ReservePcmSha256&&other.ReserveFileSha256==ReserveFileSha256&&other.ReserveRecipeSha256==ReserveRecipeSha256;
        internal static JObject Row(MenuEvent value)
        {
            var a=value.Amendment;MenuRules.Require(a!=null&&value.MenuKey==a.MenuKey,"MENU_LEDGER_AMENDMENT");
            return new JObject{["kind"]=Kind,["event_id"]=value.EventId,["attempt_id"]=value.AttemptId,["opportunity_id"]=value.OpportunityId,["menu_key"]=value.MenuKey,["meaning_display_id"]=value.MeaningDisplayId,
                ["slot_start_mono_ms"]=value.SlotStartMonoMs,["mono_ms"]=value.MonoMs,["yoked_source_event_id"]=value.YokedSourceEventId==null?JValue.CreateNull():new JValue(value.YokedSourceEventId),
                ["bank_sha256"]=a.BankSha256,["profile"]=a.Profile,["replaced_rank"]=a.ReplacedRank,["replaced_candidate_id"]=a.ReplacedCandidateId,["reason"]=a.Reason,
                ["reserve_option_id"]=a.ReserveOptionId,["reserve_candidate_id"]=a.ReserveCandidateId,["reserve_pcm_sha256"]=a.ReservePcmSha256,["reserve_file_sha256"]=a.ReserveFileSha256,["reserve_recipe_sha256"]=a.ReserveRecipeSha256};
        }
        internal static MenuAmendment Check(JObject row)
        {
            MenuJson.Keys(row,RowKeys);
            foreach(string field in RowKeys.Where(x=>x is not ("slot_start_mono_ms" or "mono_ms" or "yoked_source_event_id" or "replaced_rank")))MenuRules.Require(row[field].Type==JTokenType.String,"MENU_LEDGER_SCHEMA");
            MenuRules.Require((string)row["kind"]==Kind&&MenuRules.Guid((string)row["event_id"])&&MenuRules.Id((string)row["attempt_id"])&&MenuRules.Id((string)row["opportunity_id"])&&MenuRules.Id((string)row["meaning_display_id"]),"MENU_LEDGER_SCHEMA");
            MenuJson.Number(row["slot_start_mono_ms"]);MenuJson.Number(row["mono_ms"]);
            MenuRules.Require(row["yoked_source_event_id"].Type==JTokenType.Null||row["yoked_source_event_id"].Type==JTokenType.String&&MenuRules.Guid((string)row["yoked_source_event_id"]),"MENU_LEDGER_SCHEMA");
            var amendment=new MenuAmendment((string)row["bank_sha256"],(string)row["profile"],(string)row["menu_key"],MenuJson.Integer(row["replaced_rank"],1,3),(string)row["reason"],(string)row["reserve_option_id"],(string)row["reserve_pcm_sha256"],(string)row["reserve_file_sha256"],(string)row["reserve_recipe_sha256"]);
            MenuRules.Require((string)row["replaced_candidate_id"]==amendment.ReplacedCandidateId&&(string)row["reserve_candidate_id"]==amendment.ReserveCandidateId,"MENU_LEDGER_AMENDMENT");
            return amendment;
        }
    }

    internal static class MenuReserveRule
    {
        internal const string Assigned="MENU_RESERVE_ASSIGNED_REFUSED",AfterPresentation="MENU_RESERVE_AFTER_PRESENTATION",HistoryUnavailable="MENU_RESERVE_HISTORY_UNAVAILABLE",
            HistoryInvalid="MENU_RESERVE_HISTORY_INVALID",Multiple="MENU_RESERVE_MULTIPLE_UNUSABLE",ReserveUnusable="MENU_RESERVE_UNUSABLE",Unqualified="MENU_RESERVE_BANK_UNQUALIFIED",RecordMismatch="MENU_RESERVE_RECORD_MISMATCH";
        // The verified qualified bank's checks for one profile/atom cell.
        internal sealed class Bank
        {
            internal readonly string Sha256;internal readonly Func<int,PcmWave,bool> ShownMatches;internal readonly Func<PcmWave,(string OptionId,string RecipeSha256)?> Reserve;
            internal Bank(string sha256,Func<int,PcmWave,bool> shownMatches,Func<PcmWave,(string,string)?> reserve){Sha256=sha256;ShownMatches=shownMatches;Reserve=reserve;}
        }
        internal sealed class Result{internal PcmWave[] Waves;internal int[] Ranks;internal MenuAmendment Amendment;}
        static readonly int[] AtomSamples={21600,28800,36000,43200};
        // `cell`/`unusable`: the package's ranks 1-4 for this cell (null wave = unusable,
        // with its reason). `replaying` reproduces `recorded` (an assigned replay or a
        // ledger verification) and never decides; otherwise the active session decides
        // against `history`. The replaced option of a recorded amendment is never used.
        internal static Result Resolve(string role,string profile,string key,PcmWave[] cell,string[] unusable,Bank bank,IMenuPresentationHistory history,bool replaying,MenuAmendment recorded)
        {
            MenuRules.Require(cell!=null&&cell.Length==4&&unusable!=null&&unusable.Length==4&&(recorded==null||replaying),"MENU_RESERVE_RULE");
            if(recorded!=null)
            {MenuRules.Require(bank!=null,Unqualified);MenuRules.Require(recorded.BankSha256==bank.Sha256&&recorded.Profile==profile&&recorded.MenuKey==key,RecordMismatch);}
            var waves=new PcmWave[3];var reasons=new string[3];var ranks=new[]{1,2,3};
            for(int rank=1;rank<=3;rank++)
            {
                if(recorded?.ReplacedRank==rank)continue;
                waves[rank-1]=cell[rank-1];reasons[rank-1]=unusable[rank-1];
                if(waves[rank-1]!=null&&bank!=null&&!bank.ShownMatches(rank,waves[rank-1])){waves[rank-1]=null;reasons[rank-1]=LoadedAudioPackage.OptionHashMismatch;}
            }
            var bad=Enumerable.Range(1,3).Where(r=>recorded?.ReplacedRank!=r&&waves[r-1]==null).ToArray();int replaced=recorded?.ReplacedRank??0;
            if(replaying)MenuRules.Require(bad.Length==0,role=="active"?AfterPresentation:Assigned);
            else if(bad.Length>0)
            {
                MenuRules.Require(role=="active",Assigned);MenuRules.Require(bank!=null,Unqualified);MenuRules.Require(history!=null,HistoryUnavailable);
                MenuRules.Require(!history.Presented(key),AfterPresentation);MenuRules.Require(bad.Length==1,Multiple);replaced=bad[0];
            }
            if(replaced==0)return new Result{Waves=waves,Ranks=ranks};
            var reserve=cell[3];var shown=waves.Where((x,i)=>i!=replaced-1).ToArray();
            var record=reserve!=null&&AtomSamples.Contains(reserve.SampleCount)&&shown.All(x=>x.PcmSha256!=reserve.PcmSha256&&x.FileSha256!=reserve.FileSha256)?bank.Reserve(reserve):null;
            MenuRules.Require(record.HasValue,ReserveUnusable);
            var amendment=recorded??new MenuAmendment(bank.Sha256,profile,key,replaced,reasons[replaced-1],record.Value.OptionId,reserve.PcmSha256,reserve.FileSha256,record.Value.RecipeSha256);
            MenuRules.Require(amendment.ReserveOptionId==record.Value.OptionId&&amendment.ReservePcmSha256==reserve.PcmSha256&&amendment.ReserveFileSha256==reserve.FileSha256&&amendment.ReserveRecipeSha256==record.Value.RecipeSha256,RecordMismatch);
            waves[replaced-1]=reserve;ranks[replaced-1]=4;return new Result{Waves=waves,Ranks=ranks,Amendment=amendment};
        }
    }

    // History across the visit: the live segment plus every retained earlier
    // segment (interrupted, unsealed or sealed; active or assigned). A retained
    // segment must be a complete, verifiable hash chain whose header binds the same
    // package, bank, allocation, unit, review and visit; otherwise every query
    // fails closed (MENU_RESERVE_HISTORY_INVALID) and no substitution is possible.
    public sealed class MenuPresentationHistory:IMenuPresentationHistory
    {
        static readonly string[] Bound={"package_sha256","bank_sha256","allocation_sha256","unit_binding_sha256","review_sha256","visit"};
        readonly IMenuPresentationHistory live;readonly HashSet<string> presented=new HashSet<string>(StringComparer.Ordinal);readonly bool invalid;
        public MenuPresentationHistory(MenuLedgerBinding binding,IEnumerable<byte[]> retainedSegments,IMenuPresentationHistory live)
        {
            MenuRules.Require(binding!=null&&retainedSegments!=null&&live!=null,MenuReserveRule.HistoryUnavailable);this.live=live;
            foreach(var segment in retainedSegments.ToArray())if(!Scan(segment,binding.Json()))invalid=true;
        }
        bool Scan(byte[] bytes,JObject binding)
        {
            try
            {
                if(bytes==null||bytes.Length==0||bytes[bytes.Length-1]!=10)return false;
                var text=new UTF8Encoding(false,true);var lines=text.GetString(bytes).Split('\n');string previous=new string('0',64);var keys=new List<string>();
                for(int i=0;i<lines.Length-1;i++)
                {
                    byte[] line=text.GetBytes(lines[i]);var envelope=MenuJson.Parse(line);MenuJson.Keys(envelope,"version","sequence","previous_sha256","record","sha256");
                    if(!line.SequenceEqual(MenuJson.Bytes(envelope))||MenuJson.Integer(envelope["version"],1,1)!=1||MenuJson.Integer(envelope["sequence"],0,9999)!=i||envelope["previous_sha256"].Type!=JTokenType.String||(string)envelope["previous_sha256"]!=previous||envelope["sha256"].Type!=JTokenType.String)return false;
                    string hash=(string)envelope["sha256"];envelope.Remove("sha256");if(hash!=PcmWave.Hash(MenuJson.Bytes(envelope)))return false;previous=hash;
                    if(envelope["record"] is not JObject record)return false;
                    if(i==0){var header=record["binding"] as JObject;if(record["kind"]?.Type!=JTokenType.String||(string)record["kind"]!="header"||record["format"]?.Type!=JTokenType.String||(string)record["format"]!="av-menu-ledger/1"||header==null||!Bound.All(k=>JToken.DeepEquals(header[k],binding[k])))return false;}
                    else if(record["menu_key"]?.Type==JTokenType.String)keys.Add((string)record["menu_key"]);
                }
                if(lines.Length<2)return false;presented.UnionWith(keys);return true;
            }
            catch{return false;}
        }
        public bool Presented(string menuKey){MenuRules.Require(!invalid,MenuReserveRule.HistoryInvalid);return presented.Contains(menuKey)||live.Presented(menuKey);}
    }
}
