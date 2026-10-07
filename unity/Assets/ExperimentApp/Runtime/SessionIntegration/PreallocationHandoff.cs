using System;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using AcousticVocab.Orientation;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
namespace AcousticVocab.SessionIntegration
{
    public sealed class AllocationJoinBinding
    {
        public string Study{get;}public string Set{get;}public string UnitId{get;}public string SlotId{get;}public string PackageKey{get;}public string Role{get;}
        public string RevealReceiptSha256{get;}public string OrientationReceiptSha256{get;}
        readonly byte[] mapping;
        internal AllocationJoinBinding(string study,string set,string unit,string slot,string packageKey,string role,string reveal,string orientation,byte[] mapping)
        {Study=study;Set=set;UnitId=unit;SlotId=slot;PackageKey=packageKey;Role=role;RevealReceiptSha256=reveal;OrientationReceiptSha256=orientation;this.mapping=(byte[])mapping.Clone();}
        public void Validate(JoinedEngineeringConfig config)
        {
            PreallocationHandoff.Need(config.UnitId==UnitId&&config.CodedId==SlotId,"JOIN_ALLOCATION_IDENTITY");
            var schedule=JoinedVisitArtifacts.Json(config.RequireFile("schedule").ReadVerified());var manifest=JoinedVisitArtifacts.Json(config.RequireFile("run_sheet_manifest").ReadVerified());var hashes=JoinedVisitArtifacts.Json(mapping);
            PreallocationHandoff.Keys(hashes,"format","format_version","study","set","demo","placeholder","packages");
            PreallocationHandoff.Need((string)schedule["study"]==Study&&(string)schedule["set"]==Set&&(string)hashes["study"]==Study&&(string)hashes["set"]==Set&&
                (string)hashes["format"]=="av-schedules/package-hashes"&&hashes["format_version"].Type==JTokenType.Integer&&(int)hashes["format_version"]==1&&hashes["placeholder"].Type==JTokenType.Boolean&&!(bool)hashes["placeholder"]&&hashes["demo"].Type==JTokenType.Boolean&&JToken.DeepEquals(hashes["demo"],schedule["demo"])&&
                (string)manifest["package_hashes"]?["sha256"]==PcmWave.Hash(mapping)&&hashes["packages"] is JObject&&((JObject)hashes["packages"]).Properties().All(p=>PreallocationHandoff.Hash((string)p.Value))&&
                (string)hashes["packages"][PackageKey]==config.Pin("package_sha256"),"JOIN_ALLOCATION_PACKAGE_BINDING");
        }
    }
    // No study files are accepted or loaded until current durable orientation
    // and operator-owned allocation receipts form a complete pinned chain.
    public sealed class PreallocationHandoff
    {
        readonly OrientationReceipt orientation;readonly string listHash;bool consumed;
        public PreallocationHandoff(OrientationReceipt orientation,string independentlyPinnedListSha256)
        {Need(orientation!=null&&Hash(independentlyPinnedListSha256),"JOIN_ORIENTATION_RECEIPT");this.orientation=orientation;listHash=independentlyPinnedListSha256;}
        internal static void Need(bool yes,string code){if(!yes)throw new SessionFault(code);}
        internal static bool Hash(string value)=>value!=null&&Regex.IsMatch(value,@"\A[0-9a-f]{64}\z");
        internal static bool Code(string value)=>value!=null&&Regex.IsMatch(value,@"\A[A-Za-z0-9._-]{1,32}\z");
        internal static void Keys(JObject value,params string[] keys)=>Need(value!=null&&value.Properties().Select(p=>p.Name).OrderBy(p=>p,StringComparer.Ordinal).SequenceEqual(keys.OrderBy(p=>p,StringComparer.Ordinal)),"JOIN_ALLOCATION_FIELDS");
        static JObject Receipt(byte[] raw,string pin,string type,params string[] fields)
        {
            Need(raw!=null&&raw.Length>0&&raw.Length<=65536&&Hash(pin)&&PcmWave.Hash(raw)==pin,"JOIN_ALLOCATION_PIN");var doc=JoinedVisitArtifacts.Json(raw);Keys(doc,fields);
            Need(doc["schema_version"].Type==JTokenType.Integer&&(int)doc["schema_version"]==1&&(string)doc["receipt_type"]==type&&ReceiptCanonical.Valid(doc),"JOIN_ALLOCATION_RECEIPT");return doc;
        }
        public AllocationJoinBinding Consume(byte[] eligibility,string eligibilityRawSha256,byte[] reveal,string revealRawSha256,byte[] packageHashes,string packageHashesRawSha256)
        {
            Need(!consumed&&orientation.Eligible,"JOIN_PREALLOCATION_NOT_ELIGIBLE");consumed=true;
            var e=Receipt(eligibility,eligibilityRawSha256,"allocation-eligibility","schema_version","receipt_type","study","set","list_sha256","eligibility_id","screening_ids","orientation_receipt_sha256","journal_head_sha256","journal_line","receipt_sha256");
            var r=Receipt(reveal,revealRawSha256,"allocation-reveal","schema_version","receipt_type","study","set","list_sha256","eligibility_id","eligibility_receipt_sha256","screening_ids","entry","entry_sha256","journal_head_sha256","journal_line","receipt_sha256");
            string study=(string)e["study"],set=(string)e["set"];int people=study=="A"?1:study=="B"?2:0;
            Need(people>0&&set is "pilot" or "confirmatory"&&(string)e["list_sha256"]==listHash&&(string)r["list_sha256"]==listHash&&(string)r["study"]==study&&(string)r["set"]==set&&
                Regex.IsMatch((string)e["eligibility_id"]??"",@"\AE[0-9]{4,}\z")&&JToken.DeepEquals(e["eligibility_id"],r["eligibility_id"])&&(string)r["eligibility_receipt_sha256"]==(string)e["receipt_sha256"]&&
                Hash((string)e["journal_head_sha256"])&&Hash((string)r["journal_head_sha256"])&&e["journal_line"].Type==JTokenType.Integer&&r["journal_line"].Type==JTokenType.Integer&&(long)e["journal_line"]>0&&(long)r["journal_line"]>(long)e["journal_line"],"JOIN_ALLOCATION_CHAIN");
            Need(e["screening_ids"] is JArray ids&&ids.Count==people&&ids.All(x=>x.Type==JTokenType.String&&Code((string)x))&&ids.Distinct(JToken.EqualityComparer).Count()==people&&JToken.DeepEquals(e["screening_ids"],r["screening_ids"])&&e["orientation_receipt_sha256"] is JArray proofs&&proofs.Count==people&&proofs.All(x=>x.Type==JTokenType.String&&Hash((string)x)),"JOIN_ALLOCATION_SCREENING");
            int index=((JArray)e["screening_ids"]).ToList().FindIndex(x=>(string)x==orientation.ScreeningId);
            Need(index>=0&&(string)e["orientation_receipt_sha256"][index]==orientation.Sha256,"JOIN_ALLOCATION_ORIENTATION");
            var entry=r["entry"] as JObject;Need(entry!=null&&Hash((string)r["entry_sha256"])&&ReceiptCanonical.Hash(entry)==(string)r["entry_sha256"],"JOIN_ALLOCATION_ENTRY");
            string unit,slot,key,role=null;
            if(study=="A")
            {
                Keys(entry,"slot_id","unit_id","slot","order","wave","wave_position","profile","book_id","participant_id");unit=(string)entry["unit_id"];slot=(string)entry["slot_id"];key=(string)entry["book_id"];
                Need(Regex.IsMatch(unit??"",@"\AA-[PC][0-9]{2}\z")&&Regex.IsMatch(slot??"",@"\AA-[PC][0-9]{2}-L[0-9]{2}\z")&&slot==unit+"-"+(string)entry["slot"]&&Regex.IsMatch(key??"",@"\ABK-[PC]-[BCFGHJKMNPQRTVWXY4-9]{6}\z")&&(string)entry["participant_id"]==orientation.ScreeningId&&new[]{"P1","P2","P3"}.Contains((string)entry["profile"])&&Integer(entry["order"],1,int.MaxValue)&&Integer(entry["wave"],1,int.MaxValue)&&Integer(entry["wave_position"],1,3),"JOIN_ALLOCATION_ENTRY");
            }
            else
            {
                Keys(entry,"unit_id","kind","order","block_id","block_position","sq_arm","structured_family","swap_w1_w4","members","bank_id","profile_menu_order","replaces");unit=(string)entry["unit_id"];key=unit;
                Need(Regex.IsMatch(unit??"",@"\AB-[PCS][0-9]{2}\z")&&new[]{"dyad","spare"}.Contains((string)entry["kind"])&&Integer(entry["order"],1,int.MaxValue)&&Integer(entry["block_position"],1,4)&&Regex.IsMatch((string)entry["block_id"]??"",@"\AB-[PC]-blk[0-9]{2}\z")&&new[]{"SQ-1","SQ-2"}.Contains((string)entry["sq_arm"])&&new[]{"K","Q"}.Contains((string)entry["structured_family"])&&entry["swap_w1_w4"].Type==JTokenType.Boolean&&Regex.IsMatch((string)entry["bank_id"]??"",@"\Abank-[PC][0-9]{3}\z")&&entry["profile_menu_order"] is JArray profiles&&profiles.Select(x=>(string)x).OrderBy(x=>x).SequenceEqual(new[]{"P1","P2","P3"})&&(entry["replaces"].Type==JTokenType.Null||Regex.IsMatch((string)entry["replaces"]??"",@"\AB-[PC][0-9]{2}\z"))&&entry["members"] is JArray members&&members.Count==2,"JOIN_ALLOCATION_ENTRY");
                for(int i=0;i<2;i++){var m=entry["members"][i] as JObject;Keys(m,"slot_id","member","role","participant_id");Need(Integer(m["member"],i+1,i+1)&&(string)m["slot_id"]==unit+"-M"+(i+1)&&(string)m["participant_id"]==(string)e["screening_ids"][i]&&new[]{"active","yoked"}.Contains((string)m["role"]),"JOIN_ALLOCATION_ENTRY");}
                Need((string)entry["members"][0]["role"]!=(string)entry["members"][1]["role"],"JOIN_ALLOCATION_ENTRY");slot=(string)entry["members"][index]["slot_id"];role=(string)entry["members"][index]["role"];
            }
            Need(packageHashes!=null&&packageHashes.Length>0&&packageHashes.Length<=1024*1024&&Hash(packageHashesRawSha256)&&PcmWave.Hash(packageHashes)==packageHashesRawSha256,"JOIN_ALLOCATION_PACKAGE_PIN");
            return new AllocationJoinBinding(study,set,unit,slot,key,role,(string)r["receipt_sha256"],orientation.Sha256,packageHashes);
        }
        static bool Integer(JToken x,long min,long max)=>x?.Type==JTokenType.Integer&&(long)x>=min&&(long)x<=max;
    }
}
