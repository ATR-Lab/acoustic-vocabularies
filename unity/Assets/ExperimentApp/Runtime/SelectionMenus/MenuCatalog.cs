using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.Foundation;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using AcousticVocab.Teaching;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.SelectionMenus
{
    public sealed class MenuMaterial
    {
        public string Key{get;}public string MeaningDisplayId{get;}public TeachingDisplay Meaning{get;}
        public string Instructions{get;}public string ChoiceInstructions{get;}public IReadOnlyList<string> Labels{get;}
        public IReadOnlyList<MenuOption> Options{get;}
        internal MenuMaterial(string key,string meaningId,TeachingDisplay meaning,string instructions,string choice,string[] labels,MenuOption[] options)
        {Key=key;MeaningDisplayId=meaningId;Meaning=meaning;Instructions=instructions;ChoiceInstructions=choice;Labels=Array.AsReadOnly((string[])labels.Clone());Options=Array.AsReadOnly((MenuOption[])options.Clone());}
    }
    public sealed class MenuCatalog
    {
        readonly LoadedAudioPackage package;readonly Dictionary<string,TeachingDisplay> meanings;
        readonly Dictionary<string,PcmWave> examples;readonly JObject script;readonly string[] profileOrder;
        readonly HashSet<string> menuAtoms;
        public string PackageSha256=>package.PackageSha256;public string BankSha256{get;}public string ScheduleSha256{get;}
        public string ReviewSha256{get;}public string AllocationSha256{get;}public string ScriptSha256{get;}public string Visit{get;}
        public string Role{get;}public bool Demo=>package.Demo;public bool ParticipantBankQualified=>false;
        public IReadOnlyList<string> MenuKeys{get;}
        MenuCatalog(LoadedAudioPackage package,string bank,VisitSchedule schedule,string review,string allocation,string scriptHash,string role,string[] order,JObject script,Dictionary<string,PcmWave> examples,Dictionary<string,TeachingDisplay> meanings,string[] keys)
        {this.package=package;BankSha256=bank;ScheduleSha256=schedule.Sha256;ReviewSha256=review;AllocationSha256=allocation;ScriptSha256=scriptHash;Visit=schedule.Visit;Role=role;profileOrder=order;this.script=script;this.examples=examples;this.meanings=meanings;MenuKeys=Array.AsReadOnly(keys);menuAtoms=new HashSet<string>(keys.Where(x=>x!="profile"),StringComparer.Ordinal);}
        static void Check(bool okay)=>MenuRules.Require(okay,"MENU_CATALOG_INVALID");
        static string Text(JToken value,int maximum=512)
        {Check(value?.Type==JTokenType.String);string text=(string)value;Check(text.Length>0&&text.Length<=maximum&&!text.Any(char.IsControl));return text;}
        static JObject Json(byte[] bytes)=>StationConfig.ParseStrict(new UTF8Encoding(false,true).GetString(bytes));
        static byte[] Read(string path,int maximum)
        {MenuJson.NoLinks(path);var info=new FileInfo(path);Check(info.Exists&&info.Length>0&&info.Length<=maximum);var bytes=File.ReadAllBytes(path);Check(bytes.Length<=maximum);return bytes;}
        public static MenuCatalog Load(string scriptDirectory,string scriptSha256,string reviewSha256,LoadedAudioPackage package,VisitSchedule schedule,
            TeachingCatalog teaching,byte[] packageManifest,byte[] permutationBytes,byte[] allocationBytes,string allocationSha256,string bankSha256,
            string exampleDirectory,byte[] reservedRegistry,string registrySha256,bool engineeringPreview=false)
        {
            try
            {
                Check(package!=null&&package.Study=="B"&&schedule!=null&&teaching!=null&&new[]{"V1","V2","V3"}.Contains(schedule.Visit)&&schedule.PackageSha256==package.PackageSha256&&teaching.PackageSha256==package.PackageSha256&&teaching.ScheduleSha256==schedule.Sha256&&new[]{scriptSha256,reviewSha256,allocationSha256,bankSha256,registrySha256}.All(MenuRules.Hash));
                // #26 has no qualified handoff in this implementation. The #13
                // provisional bank is deliberately limited to DEMO engineering.
                MenuRules.Require(engineeringPreview&&package.Demo&&schedule.Demo,"MENU_BANK_HANDOFF_PENDING");
                Check(packageManifest!=null&&PackageLoader.CanonicalPackageHash(Encoding.UTF8.GetString(packageManifest))==package.PackageSha256);var manifest=Json(packageManifest);
                Check((string)manifest["bank"]?["format"]=="av-sound/provisional-bank"&&(string)manifest["bank"]?["bank_sha256"]==bankSha256&&permutationBytes!=null&&PcmWave.Hash(permutationBytes)==(string)manifest["files"]?["permutation.json"]?["sha256"]);
                var permutation=Json(permutationBytes);Check(allocationBytes!=null&&PcmWave.Hash(allocationBytes)==allocationSha256);var allocation=Json(allocationBytes);Check(allocation["demo"]?.Type==JTokenType.Boolean&&(bool)allocation["demo"]&&allocation["dyads"] is JArray);
                var dyads=allocation["dyads"].Where(x=>x["members"] is JArray&&x["members"].Any(m=>(string)m["slot_id"]==schedule.PersonSlot)).ToArray();Check(dyads.Length==1);
                var members=dyads[0]["members"].Where(x=>(string)x["slot_id"]==schedule.PersonSlot).ToArray();Check(members.Length==1);string role=Text(members[0]["role"]);Check(role=="active"||role=="yoked");
                Check(dyads[0]["profile_menu_order"] is JArray);string[] order=dyads[0]["profile_menu_order"].Select(x=>Text(x)).ToArray();Check(order.Length==3&&order.OrderBy(x=>x).SequenceEqual(new[]{"P1","P2","P3"}));
                var items=schedule.Blocks.SelectMany(x=>x.Items).Where(x=>x.Phase=="selection").ToArray();string[] keys=items.Select(x=>x.TrialType=="profile_menu"?"profile":x.ContentId).ToArray();
                Check(items.Length==(schedule.Visit=="V1"?9:4)&&items.All(x=>!x.Heldout&&x.Plays==8&&(x.TrialType=="profile_menu"?x.SlotSeconds==60:x.TrialType=="atom_menu"&&x.SlotSeconds==45))&&keys.Distinct().Count()==keys.Length);
                var wave=permutation["wave_atom_order"]?[schedule.Visit.Substring(1)];Check(wave is JArray&&keys.Where(x=>x!="profile").SequenceEqual(wave.Select(x=>(string)x))&&(schedule.Visit!="V1"||keys[0]=="profile"));
                byte[] scriptBytes=Read(Path.Combine(scriptDirectory,"menu-script.local.json"),65536),reviewBytes=Read(Path.Combine(scriptDirectory,"review.local.json"),65536);Check(PcmWave.Hash(scriptBytes)==scriptSha256&&PcmWave.Hash(reviewBytes)==reviewSha256);var script=Json(scriptBytes);var review=Json(reviewBytes);
                MenuJson.Keys(script,"format","package_sha256","profile_display_id","profile_names","profile_instructions","atom_instructions","active_choice_instructions","yoked_choice_instructions","candidate_labels");
                Check((string)script["format"]=="av-menu-script/1"&&(string)script["package_sha256"]==package.PackageSha256&&MenuRules.Id(Text(script["profile_display_id"])));MenuJson.Keys((JObject)script["profile_names"],"P1","P2","P3");foreach(var x in ((JObject)script["profile_names"]).Properties())Text(x.Value,80);
                foreach(string key in new[]{"profile_instructions","atom_instructions","active_choice_instructions","yoked_choice_instructions"})Text(script[key]);Check(script["candidate_labels"] is JArray&&script["candidate_labels"].Count()==3);foreach(var x in script["candidate_labels"])Text(x,80);
                MenuJson.Keys(review,"version","approved","script_sha256","teaching_review_sha256","methodology_sha256");Check(MenuJson.Integer(review["version"],1,1)==1&&review["approved"]?.Type==JTokenType.Boolean&&(bool)review["approved"]&&(string)review["script_sha256"]==scriptSha256&&(string)review["teaching_review_sha256"]==teaching.ReviewSha256&&(string)review["methodology_sha256"]==teaching.MethodologySha256);
                Check(Directory.GetFileSystemEntries(scriptDirectory).Select(Path.GetFileName).OrderBy(x=>x,StringComparer.Ordinal).SequenceEqual(new[]{"menu-script.local.json","review.local.json"}));
                Check(reservedRegistry!=null&&PcmWave.Hash(reservedRegistry)==registrySha256);var registry=Json(reservedRegistry);Check(registry["registry_version"]?.Type==JTokenType.Integer&&(int)registry["registry_version"]==1&&registry["entries"] is JArray);
                var examples=new Dictionary<string,PcmWave>(StringComparer.Ordinal);foreach(string profile in order)
                {
                    string id="calibration-"+profile;var rows=registry["entries"].Where(x=>(string)x["id"]==id).ToArray();Check(rows.Length==1);var row=rows[0];Check((string)row["kind"]=="calibration"&&(string)row["profile"]==profile&&row["recipe"].Type==JTokenType.Null&&MenuJson.Integer(row["n_samples"],96000,96000)==96000);
                    var pcm=PcmWave.ParseCanonical(Read(Path.Combine(exampleDirectory,id+".wav"),192044));Check(pcm.SampleCount==96000&&pcm.FileSha256==(string)row["file_sha256"]&&pcm.PcmSha256==(string)row["pcm_sha256"]);examples.Add(profile,pcm);
                }
                var meanings=new Dictionary<string,TeachingDisplay>(StringComparer.Ordinal);foreach(string atom in keys.Where(x=>x!="profile"))meanings.Add(atom,teaching.ReadSelectionAtom(atom,new MeaningPermit(package.PackageSha256,schedule.Sha256,atom)));
                return new MenuCatalog(package,bankSha256,schedule,reviewSha256,allocationSha256,scriptSha256,role,order,script,examples,meanings,keys);
            }
            catch(SessionFault){throw;}catch{throw new SessionFault("MENU_CATALOG_INVALID");}
        }
        public string ProfileAt(int storedIndex){Check(storedIndex>=1&&storedIndex<=3);return profileOrder[storedIndex-1];}
        public MenuMaterial Prepare(SlotItem item,string committedProfile)
        {
            Check(item!=null&&item.Phase=="selection"&&!item.Heldout&&item.Plays==8);bool profile=item.TrialType=="profile_menu";string key=profile?"profile":item.ContentId;
            Check(MenuKeys.Contains(key)&&(!profile||Visit=="V1")&&(profile||menuAtoms.Contains(key)&&new[]{"P1","P2","P3"}.Contains(committedProfile)));
            var options=profile?profileOrder.Select(x=>new MenuOption("calibration-"+x,examples[x])).ToArray():Enumerable.Range(1,3).Select(x=>new MenuOption(committedProfile+"-"+key+"-"+x,package.ReadAtom(key,committedProfile,x))).ToArray();
            string[] labels=profile?profileOrder.Select(x=>(string)script["profile_names"][x]).ToArray():script["candidate_labels"].Select(x=>(string)x).ToArray();var meaning=profile?null:meanings[key];
            return new MenuMaterial(key,profile?(string)script["profile_display_id"]:meaning.MeaningDisplayId,meaning,(string)script[profile?"profile_instructions":"atom_instructions"],(string)script[Role=="active"?"active_choice_instructions":"yoked_choice_instructions"],labels,options);
        }
        public MenuLedgerBinding Binding(string unitBinding)=>new MenuLedgerBinding(PackageSha256,BankSha256,AllocationSha256,ScheduleSha256,unitBinding,ReviewSha256,Visit,Role,MenuKeys);
        sealed class MeaningPermit:ISelectionMeaningAuthorization
        {readonly string package,schedule,atom;bool used;internal MeaningPermit(string package,string schedule,string atom){this.package=package;this.schedule=schedule;this.atom=atom;}public bool TryConsume(string p,string s,string a){if(used||p!=package||s!=schedule||a!=atom)return false;used=true;return true;}}
    }
}
