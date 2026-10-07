using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using AcousticVocab.Foundation;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using AcousticVocab.Teaching;
using Newtonsoft.Json.Linq;
namespace AcousticVocab.SelectionMenus
{
    public sealed class MenuStoreBinding
    {
        public string UnitId{get;}public string BookId{get;}public string BankSha256{get;}public string PackageSha256{get;}public string ConfigSha256{get;}
        public MenuStoreBinding(string unitId,string bookId,string bankSha256,string packageSha256,string configSha256)
        {MenuRules.Require(new[]{unitId,bookId}.All(x=>x!=null&&Regex.IsMatch(x,@"\ADEMO-[A-Za-z0-9-]{1,58}\z"))&&new[]{bankSha256,packageSha256,configSha256}.All(MenuRules.Hash),"MENU_STORE_BINDING");UnitId=unitId;BookId=bookId;BankSha256=bankSha256;PackageSha256=packageSha256;ConfigSha256=configSha256;}
        internal void Check(JObject value)
        {MenuRules.Require(MenuJson.Integer(value["schema_version"],1,1)==1&&MenuStoreCodec.String(value["unit_id"])==UnitId&&MenuStoreCodec.String(value["book_id"])==BookId&&MenuStoreCodec.String(value["bank_sha256"])==BankSha256&&MenuStoreCodec.String(value["package_sha256"])==PackageSha256&&MenuStoreCodec.String(value["config_sha256"])==ConfigSha256,"MENU_STORE_BINDING");}
    }
    internal sealed class MenuStoreSnapshot
    {
        internal JObject Document;internal string Head,SnapshotHash,ManifestHash,Profile,ProfileReceipt,JournalHead;
        internal readonly Dictionary<string,JObject> Entries=new Dictionary<string,JObject>(StringComparer.Ordinal);
    }
    internal static class MenuStoreCodec
    {
        internal static string String(JToken value){MenuRules.Require(value?.Type==JTokenType.String,"MENU_STORE_SCHEMA");return(string)value;}
        internal static string Hash(JToken value,bool nullable=false)
        {if(nullable&&value?.Type==JTokenType.Null)return null;string hash=String(value);MenuRules.Require(MenuRules.Hash(hash),"MENU_STORE_SCHEMA");return hash;}
        internal static bool Boolean(JToken value){MenuRules.Require(value?.Type==JTokenType.Boolean,"MENU_STORE_SCHEMA");return(bool)value;}
        internal static JObject Parse(byte[] bytes)
        {MenuRules.Require(bytes!=null&&bytes.Length>0&&bytes.Length<=65536,"MENU_STORE_SIZE");return StationConfig.ParseStrict(new UTF8Encoding(false,true).GetString(bytes));}
        internal static string VerifySelfHash(JObject row,string field)
        {string hash=Hash(row[field]);var body=(JObject)row.DeepClone();body.Remove(field);MenuRules.Require(hash==PcmWave.Hash(MenuJson.Bytes(body)),"MENU_STORE_HASH");return hash;}
        internal static void Scope(JObject row)=>MenuRules.Require(String(row["source_kind"])=="synthetic"&&!Boolean(row["participant_ready"]),"MENU_STORE_SCOPE");
        internal static MenuStoreSnapshot Snapshot(JObject row,string expectedManifest,string expectedHead,string expectedSnapshot,MenuStoreBinding binding,LoadedAudioPackage package)
        {
            MenuJson.Keys(row,"schema_version","unit_id","book_id","bank_sha256","package_sha256","config_sha256","profile","profile_selection_receipt_sha256","book_head","snapshot_sha256","journal_head","source_kind","participant_ready","entries","manifest_sha256");binding.Check(row);Scope(row);
            var result=new MenuStoreSnapshot{Document=(JObject)row.DeepClone(),Head=Hash(row["book_head"],true),SnapshotHash=Hash(row["snapshot_sha256"]),ManifestHash=VerifySelfHash(row,"manifest_sha256"),JournalHead=Hash(row["journal_head"],true),ProfileReceipt=Hash(row["profile_selection_receipt_sha256"],true)};
            MenuRules.Require(result.ManifestHash==expectedManifest&&result.Head==expectedHead&&result.SnapshotHash==expectedSnapshot&&row["entries"] is JArray&&row["entries"].Count()<=16&&package!=null&&package.Demo&&package.Study=="B"&&package.PackageSha256==binding.PackageSha256,"MENU_STORE_PIN");
            result.Profile=row["profile"].Type==JTokenType.Null?null:String(row["profile"]);MenuRules.Require(result.Profile==null?result.Head==null&&result.JournalHead==null&&result.ProfileReceipt==null&&row["entries"].Count()==0&&result.SnapshotHash==PcmWave.Hash(Encoding.ASCII.GetBytes("{}")):new[]{"P1","P2","P3"}.Contains(result.Profile)&&result.Head!=null&&result.JournalHead!=null&&result.ProfileReceipt!=null,"MENU_STORE_PROFILE");
            foreach(var item in row["entries"])
            {
                MenuRules.Require(item is JObject,"MENU_STORE_SCHEMA");var entry=(JObject)item;MenuJson.Keys(entry,"atom_id","profile","rank","pcm_sha256","file_sha256","selection_receipt_sha256");string atom=String(entry["atom_id"]);int rank=MenuJson.Integer(entry["rank"],1,3);
                MenuRules.Require(package.AtomIds.Contains(atom)&&!result.Entries.ContainsKey(atom)&&String(entry["profile"])==result.Profile,"MENU_STORE_ENTRY");var wave=package.ReadAtom(atom,result.Profile,rank);MenuRules.Require(Hash(entry["pcm_sha256"])==wave.PcmSha256&&Hash(entry["file_sha256"])==wave.FileSha256&&MenuRules.Hash(Hash(entry["selection_receipt_sha256"])),"MENU_STORE_ENTRY");result.Entries.Add(atom,(JObject)entry.DeepClone());
            }
            return result;
        }
        internal static MenuStoreSnapshot Receipt(JObject receipt,JObject snapshot,JObject request,MenuStoreSnapshot before,MenuStoreBinding binding,LoadedAudioPackage package)
        {
            MenuJson.Keys(receipt,"schema_version","request_id","operation","unit_id","book_id","bank_sha256","package_sha256","profile","menu_key","rank","request_sha256","config_sha256","status","accepted","reason","before_head","after_head","before_snapshot_sha256","after_snapshot_sha256","pcm_sha256","file_sha256","source_kind","participant_ready","receipt_sha256");binding.Check(receipt);Scope(receipt);VerifySelfHash(receipt,"receipt_sha256");
            foreach(string key in new[]{"schema_version","request_id","operation","unit_id","book_id","bank_sha256","package_sha256","profile","menu_key","rank"})MenuRules.Require(JToken.DeepEquals(request[key],receipt[key]),"MENU_STORE_REPLY_CONTEXT");
            MenuRules.Require(Hash(receipt["request_sha256"])==PcmWave.Hash(MenuJson.Bytes(request))&&Hash(receipt["before_head"],true)==before.Head&&Hash(receipt["before_snapshot_sha256"])==before.SnapshotHash,"MENU_STORE_REPLY_CONTEXT");
            bool profile=String(request["operation"])=="profile",accepted=Boolean(receipt["accepted"]);string status=String(receipt["status"]);MenuRules.Require(accepted?status==(profile?"profile_selected":"committed")&&receipt["reason"].Type==JTokenType.Null:!profile&&status=="rejected"&&String(receipt["reason"])=="E_REJECTED","MENU_STORE_REPLY_STATUS");
            var after=Snapshot(snapshot,Hash(snapshot["manifest_sha256"]),Hash(receipt["after_head"],true),Hash(receipt["after_snapshot_sha256"]),binding,package);
            MenuRules.Require(after.Profile==String(request["profile"])&&(before.Profile==null||before.Profile==after.Profile)&&before.Entries.All(x=>after.Entries.TryGetValue(x.Key,out var value)&&JToken.DeepEquals(x.Value,value)),"MENU_STORE_OLD_ENTRY_CHANGED");
            if(profile)
                MenuRules.Require(before.Profile==null&&after.Entries.Count==0&&after.ProfileReceipt==(string)receipt["receipt_sha256"]&&receipt["pcm_sha256"].Type==JTokenType.Null&&receipt["file_sha256"].Type==JTokenType.Null,"MENU_STORE_PROFILE_RECEIPT");
            else if(accepted)
            {
                string atom=String(request["menu_key"]);MenuRules.Require(!before.Entries.ContainsKey(atom)&&after.Entries.Count==before.Entries.Count+1&&after.Entries.TryGetValue(atom,out var unused)&&after.ProfileReceipt==before.ProfileReceipt,"MENU_STORE_ENTRY");var added=after.Entries[atom];
                MenuRules.Require(JToken.DeepEquals(added["rank"],request["rank"])&&(string)added["pcm_sha256"]==Hash(receipt["pcm_sha256"])&&(string)added["file_sha256"]==Hash(receipt["file_sha256"])&&(string)added["selection_receipt_sha256"]==(string)receipt["receipt_sha256"],"MENU_STORE_ENTRY");
            }
            else MenuRules.Require(after.Entries.Count==before.Entries.Count&&after.Head==before.Head&&after.SnapshotHash==before.SnapshotHash&&receipt["pcm_sha256"].Type==JTokenType.Null&&receipt["file_sha256"].Type==JTokenType.Null,"MENU_STORE_REJECTION_CHANGED_STATE");
            return after;
        }
    }
    // Private file mailbox to the operator-side real #11 process. The client
    // never loads recipes or executes Python, and has one bounded outstanding
    // request. This is an application boundary, not an authentication scheme.
    public sealed class FileMenuStore:IMenuStore,IDisposable
    {
        readonly string root;readonly MenuStoreBinding binding;readonly LoadedAudioPackage package;readonly Func<double> clock;readonly Action<JObject> persist;
        readonly bool readOnly;readonly HashSet<string> allowed,completeWave;readonly Dictionary<string,string> receipts=new Dictionary<string,string>(StringComparer.Ordinal);
        MenuStoreSnapshot snapshot;JObject pending;double submitted,last=-1;bool failed,disposed;
        public string PackageSha256=>binding.PackageSha256;public string BankSha256=>binding.BankSha256;public string Profile=>snapshot.Profile;
        public bool Ready=>!failed&&!disposed&&pending==null;public bool OldHashesVerified=>Ready;
        public FileMenuStore(string mailbox,MenuStoreBinding binding,LoadedAudioPackage package,byte[] initialSnapshot,string manifestPin,string headPin,string snapshotPin,IEnumerable<string> allowedWaveAtoms,Action<JObject> durableCheckpoint,Func<double> monotonicClock,bool readOnly=false)
        {
            MenuRules.Require(binding!=null&&package!=null&&package.Demo&&package.PackageSha256==binding.PackageSha256&&allowedWaveAtoms!=null&&durableCheckpoint!=null&&monotonicClock!=null,"MENU_STORE_CONFIGURATION");this.readOnly=readOnly;this.binding=binding;this.package=package;persist=durableCheckpoint;clock=monotonicClock;allowed=new HashSet<string>(allowedWaveAtoms,StringComparer.Ordinal);MenuRules.Require(allowed.Count is 4 or 8&&allowed.All(package.AtomIds.Contains),"MENU_STORE_WAVE");
            snapshot=MenuStoreCodec.Snapshot(MenuStoreCodec.Parse(initialSnapshot),manifestPin,headPin,snapshotPin,binding,package);int wave=allowed.SetEquals(package.AtomIds.Where(x=>x.EndsWith("1",StringComparison.Ordinal)||x.EndsWith("2",StringComparison.Ordinal)))?1:allowed.SetEquals(package.AtomIds.Where(x=>x.EndsWith("3",StringComparison.Ordinal)))?2:allowed.SetEquals(package.AtomIds.Where(x=>x.EndsWith("4",StringComparison.Ordinal)))?3:0;
            MenuRules.Require(wave>0,"MENU_STORE_WAVE");var prior=new HashSet<string>(package.AtomIds.Where(x=>wave>1&&(x.EndsWith("1",StringComparison.Ordinal)||x.EndsWith("2",StringComparison.Ordinal))||wave>2&&x.EndsWith("3",StringComparison.Ordinal)),StringComparer.Ordinal);completeWave=new HashSet<string>(prior.Concat(allowed),StringComparer.Ordinal);
            MenuRules.Require(new HashSet<string>(snapshot.Entries.Keys,StringComparer.Ordinal).SetEquals(readOnly?completeWave:prior),"MENU_STORE_WAVE_STATE");
            MenuRules.Require(!string.IsNullOrEmpty(mailbox)&&!mailbox.Replace('\\','/').StartsWith("//",StringComparison.Ordinal),"MENU_STORE_LOCAL_PATH_REQUIRED");root=Path.GetFullPath(mailbox);MenuRules.Require(!root.Replace('\\','/').StartsWith("//",StringComparison.Ordinal),"MENU_STORE_LOCAL_PATH_REQUIRED");MenuJson.NoLinks(root);Directory.CreateDirectory(root);foreach(string child in new[]{"requests","responses"}){Directory.CreateDirectory(Path.Combine(root,child));MenuJson.NoLinks(Path.Combine(root,child));}Now();RequestVerification();
        }
        double Now(){double now=clock();MenuRules.Require(MenuRules.Finite(now)&&now>=0&&now>=last,"MENU_STORE_CLOCK");last=now;return now;}
        public TeachingSelection Get(string atomId)
        {MenuRules.Require(Ready&&snapshot.Entries.TryGetValue(atomId,out var unused),"MENU_STORE_SELECTION_UNVERIFIED");var row=snapshot.Entries[atomId];return new TeachingSelection(snapshot.Profile,(int)row["rank"]);}
        public string RequestSelection(string menuKey,string profile,int? rank)
        {
            MenuRules.Require(Ready&&!readOnly&&new[]{"P1","P2","P3"}.Contains(profile),"MENU_STORE_NOT_READY");bool isProfile=menuKey=="profile";
            MenuRules.Require(isProfile?snapshot.Profile==null&&!rank.HasValue:snapshot.Profile==profile&&allowed.Contains(menuKey)&&!snapshot.Entries.ContainsKey(menuKey)&&rank>=1&&rank<=3,"MENU_STORE_SELECTION_REFUSED");
            string id=Guid.NewGuid().ToString("N");var request=new JObject{["schema_version"]=1,["request_id"]=id,["operation"]=isProfile?"profile":"atom",["unit_id"]=binding.UnitId,["book_id"]=binding.BookId,["bank_sha256"]=binding.BankSha256,["package_sha256"]=binding.PackageSha256,["expected_head"]=snapshot.Head==null?JValue.CreateNull():new JValue(snapshot.Head),["expected_snapshot_sha256"]=snapshot.SnapshotHash,["profile"]=profile,["menu_key"]=menuKey,["rank"]=rank.HasValue?new JValue(rank.Value):JValue.CreateNull()};
            return Publish(request);
        }
        public void RequestVerification(bool waveComplete=false)
        {
            MenuRules.Require(!disposed&&!failed&&pending==null,"MENU_STORE_NOT_READY");
            if(waveComplete)MenuRules.Require(new HashSet<string>(snapshot.Entries.Keys,StringComparer.Ordinal).SetEquals(completeWave),"MENU_STORE_WAVE_INCOMPLETE");
            var request=new JObject{["schema_version"]=1,["request_id"]=Guid.NewGuid().ToString("N"),["operation"]="verify",["unit_id"]=binding.UnitId,["book_id"]=binding.BookId,["bank_sha256"]=binding.BankSha256,["package_sha256"]=binding.PackageSha256,["expected_head"]=snapshot.Head==null?JValue.CreateNull():new JValue(snapshot.Head),["expected_snapshot_sha256"]=snapshot.SnapshotHash,["profile"]=snapshot.Profile==null?JValue.CreateNull():new JValue(snapshot.Profile),["menu_key"]="verify",["rank"]=JValue.CreateNull()};Publish(request);
        }
        string Publish(JObject request)
        {
            submitted=Now();pending=request;string id=(string)request["request_id"];
            try{persist(new JObject{["event"]="menu_store_intent",["mono_ms"]=submitted,["request"]=request.DeepClone()});string path=Path.Combine(root,"requests",id+".json");MenuJson.NoLinks(path);byte[] bytes=MenuJson.Bytes(request);string temporary=Path.Combine(root,"requests","."+id+".tmp");MenuJson.NoLinks(temporary);using(var stream=new FileStream(temporary,FileMode.CreateNew,FileAccess.Write,FileShare.None,4096,FileOptions.WriteThrough)){stream.Write(bytes,0,bytes.Length);stream.WriteByte(10);stream.Flush(true);}File.Move(temporary,path);return id;}
            catch{failed=true;throw new SessionFault("MENU_STORE_REQUEST_FAILED");}
        }

        public void Pump()
        {
            if(disposed||failed||pending==null)return;
            try
            {
                double now=Now();MenuRules.Require(now-submitted<=2000,"MENU_STORE_TIMEOUT");string id=(string)pending["request_id"],path=Path.Combine(root,"responses",id+".json");if(!File.Exists(path))return;MenuJson.NoLinks(path);var info=new FileInfo(path);MenuRules.Require(info.Length>0&&info.Length<=65536,"MENU_STORE_SIZE");var response=MenuStoreCodec.Parse(File.ReadAllBytes(path));
                MenuJson.Keys(response,"schema_version","request_id","request_sha256","config_sha256","receipt","snapshot","error","response_sha256");MenuRules.Require(MenuJson.Integer(response["schema_version"],1,1)==1&&MenuStoreCodec.String(response["request_id"])==id&&MenuStoreCodec.Hash(response["request_sha256"])==PcmWave.Hash(MenuJson.Bytes(pending))&&MenuStoreCodec.Hash(response["config_sha256"])==binding.ConfigSha256,"MENU_STORE_REPLY_CONTEXT");MenuStoreCodec.VerifySelfHash(response,"response_sha256");MenuRules.Require(response["error"].Type==JTokenType.Null&&response["snapshot"] is JObject,"MENU_STORE_BRIDGE_REFUSED");
                if((string)pending["operation"]=="verify")
                {
                    MenuRules.Require(response["receipt"].Type==JTokenType.Null,"MENU_STORE_REPLY_CONTEXT");var value=(JObject)response["snapshot"];var verified=MenuStoreCodec.Snapshot(value,snapshot.ManifestHash,snapshot.Head,snapshot.SnapshotHash,binding,package);MenuRules.Require(JToken.DeepEquals(verified.Document,snapshot.Document),"MENU_STORE_OLD_ENTRY_CHANGED");
                    persist(new JObject{["event"]="menu_store_verified",["mono_ms"]=now,["response"]=response.DeepClone()});pending=null;return;
                }
                MenuRules.Require(response["receipt"] is JObject,"MENU_STORE_REPLY_CONTEXT");
                var receipt=(JObject)response["receipt"];var next=MenuStoreCodec.Receipt(receipt,(JObject)response["snapshot"],pending,snapshot,binding,package);
                persist(new JObject{["event"]="menu_store_verified",["mono_ms"]=now,["response"]=response.DeepClone()});
                snapshot=next;pending=null;MenuRules.Require((bool)receipt["accepted"],"MENU_STORE_CANDIDATE_REJECTED");receipts.Add(id,(string)receipt["receipt_sha256"]);
            }
            catch(SessionFault){failed=true;throw;}catch{failed=true;throw new SessionFault("MENU_STORE_REPLY_INVALID");}
        }
        public bool TryGetReceipt(string exactRequestId,out string receiptSha256)
        {receiptSha256=null;return Ready&&receipts.TryGetValue(exactRequestId,out receiptSha256);}
        public bool VerifyRecordedSelection(string menuKey,int selectedIndex,string receiptSha256,MenuCatalog catalog)
        {if(!Ready||catalog==null||catalog.PackageSha256!=PackageSha256||catalog.BankSha256!=BankSha256)return false;if(menuKey=="profile")return snapshot.Profile==catalog.ProfileAt(selectedIndex)&&snapshot.ProfileReceipt==receiptSha256;return snapshot.Entries.TryGetValue(menuKey,out var value)&&(int)value["rank"]==selectedIndex&&(string)value["selection_receipt_sha256"]==receiptSha256;}
        public void Dispose(){disposed=true;pending=null;}
    }
}
