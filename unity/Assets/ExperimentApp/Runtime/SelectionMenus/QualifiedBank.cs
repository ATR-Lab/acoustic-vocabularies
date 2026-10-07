using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.SelectionMenus
{
    // The #26 bank manifest (av-banks/bank-manifest v1) verified against an
    // independent bank-hash pin and bound to the loaded #13 dyad package. Every
    // failure is MENU_BANK_HASH_MISMATCH; no partial result is returned. The
    // provisional av-sound/provisional-bank input never takes this path.
    public sealed class QualifiedBank
    {
        public const string Format="av-banks/bank-manifest";
        public const string ProvisionalFormat="av-sound/provisional-bank";
        public const string Fault="MENU_BANK_HASH_MISMATCH";
        const int MaximumBytes=4*1024*1024;
        static readonly string[] Profiles={"P1","P2","P3"};
        static readonly string[] TopKeys={"atom_order","attempt_used","attempts","bank_id","bank_version","cells","demo","dyad_slot","format","format_version","generation_config_sha256","labels","permutation_sha256","seed_namespace","set","status"};
        static readonly string[] OptionKeys={"file_sha256","menu","option_id","pcm_sha256","rank","recipe","recipe_sha256","slot_id","wav"};
        readonly Dictionary<string,(string Pcm,string File)> options;
        public string BankId{get;}public string BankSha256{get;}public string Set{get;}public bool Demo{get;}public string DyadSlot{get;}
        public int OptionsVerified=>options.Count;
        QualifiedBank(string id,string hash,string set,bool demo,string slot,Dictionary<string,(string,string)> verified)
        {BankId=id;BankSha256=hash;Set=set;Demo=demo;DyadSlot=slot;options=verified;}
        static void Need(bool okay){if(!okay)throw new SessionFault(Fault);}
        static string Text(JToken value){Need(value?.Type==JTokenType.String);return (string)value;}
        static long Integer(JToken value,long min,long max){Need(value?.Type==JTokenType.Integer);long n=(long)value;Need(n>=min&&n<=max);return n;}
        static bool IsHash(string value)=>value!=null&&Regex.IsMatch(value,@"\A[0-9a-f]{64}\z");
        static void Keys(JToken value,IEnumerable<string> keys)
        {Need(value is JObject o&&o.Properties().Select(x=>x.Name).OrderBy(x=>x,StringComparer.Ordinal).SequenceEqual(keys.OrderBy(x=>x,StringComparer.Ordinal),StringComparer.Ordinal));}
        static string Sha(byte[] bytes)=>PcmWave.Hash(bytes);
        static string Key(string profile,string atom,long rank)=>profile+"/"+atom+"/"+rank.ToString(CultureInfo.InvariantCulture);

        // SHA-256 of Python's compact canonical JSON (sorted keys, "," and ":",
        // ASCII): av_generation.bank_manifest.bank_sha256 and recipe_sha256. Floats
        // occur only as recipe amplitudes; any other float is refused rather than
        // guessed, so a representation difference can never pass as a match.
        public static string CanonicalSha256(JToken value)
        {var text=new StringBuilder();Canonical(value,text);return Sha(Encoding.ASCII.GetBytes(text.ToString()));}
        static void Canonical(JToken value,StringBuilder text)
        {
            switch(value?.Type)
            {
                case JTokenType.Object:
                    text.Append('{');bool first=true;
                    foreach(var p in ((JObject)value).Properties().OrderBy(x=>x.Name,StringComparer.Ordinal)){if(!first)text.Append(',');first=false;String(p.Name,text);text.Append(':');Canonical(p.Value,text);}
                    text.Append('}');return;
                case JTokenType.Array:
                    text.Append('[');for(int i=0;i<((JArray)value).Count;i++){if(i>0)text.Append(',');Canonical(value[i],text);}text.Append(']');return;
                case JTokenType.String:String((string)value,text);return;
                case JTokenType.Integer:text.Append(((long)value).ToString(CultureInfo.InvariantCulture));return;
                case JTokenType.Boolean:text.Append((bool)value?"true":"false");return;
                case JTokenType.Null:text.Append("null");return;
                case JTokenType.Float:
                    double d=(double)value;text.Append(d==0.6?"0.6":d==0.8?"0.8":d==1.0?"1.0":throw new SessionFault(Fault));return;
                default:throw new SessionFault(Fault);
            }
        }
        static void String(string value,StringBuilder text)
        {Need(value.All(c=>c>=32&&c<=126&&c!='"'&&c!='\\'));text.Append('"').Append(value).Append('"');}

        static void Recipe(JToken recipe)
        {
            Keys(recipe,new[]{"amplitudes","gaps_ms","pitches","rhythm_weights","total_ms"});
            Need(new long[]{450,600,750,900}.Contains(Integer(recipe["total_ms"],450,900)));
            void Items(JToken array,int count,Func<JToken,bool> valid){Need(array is JArray a&&a.Count==count&&a.All(valid));}
            Items(recipe["pitches"],3,x=>x.Type==JTokenType.Integer&&(long)x>=-6&&(long)x<=6);
            Items(recipe["rhythm_weights"],3,x=>x.Type==JTokenType.Integer&&(long)x>=1&&(long)x<=4);
            Items(recipe["gaps_ms"],2,x=>x.Type==JTokenType.Integer&&new long[]{20,40,60}.Contains((long)x));
            Items(recipe["amplitudes"],3,x=>(x.Type==JTokenType.Float||x.Type==JTokenType.Integer)&&new[]{0.6,0.8,1.0}.Contains((double)x));
        }

        public static QualifiedBank Verify(byte[] bankManifest,string pinnedBankSha256,LoadedAudioPackage package,byte[] packageManifest,byte[] permutationBytes)
        {
            try{return VerifyChecked(bankManifest,pinnedBankSha256,package,packageManifest,permutationBytes);}
            catch(SessionFault){throw;}catch{throw new SessionFault(Fault);}
        }
        static QualifiedBank VerifyChecked(byte[] bankManifest,string pin,LoadedAudioPackage package,byte[] packageManifest,byte[] permutationBytes)
        {
            Need(IsHash(pin)&&package!=null&&package.Study=="B"&&package.BankFormat==Format&&package.BankSha256==pin&&bankManifest!=null&&bankManifest.Length<=MaximumBytes&&packageManifest!=null&&permutationBytes!=null);
            // Package side: the exact pinned package records this exact bank.
            Need(PackageLoader.CanonicalPackageHash(new UTF8Encoding(false,true).GetString(packageManifest))==package.PackageSha256);
            var pkg=PackageLoader.ParseStrictDocument(packageManifest,2*1024*1024);
            Keys(pkg["bank"],new[]{"format","format_version","bank_sha256"});
            Need(Text(pkg["bank"]["format"])==Format&&Integer(pkg["bank"]["format_version"],1,1)==1&&Text(pkg["bank"]["bank_sha256"])==pin);
            Need(Sha(permutationBytes)==Text(pkg["files"]?["permutation.json"]?["sha256"]));
            // Bank side: the whole canonical manifest is the pinned bank hash.
            var bank=PackageLoader.ParseStrictDocument(bankManifest,MaximumBytes);Need(CanonicalSha256(bank)==pin);
            Keys(bank,TopKeys);
            Need(Text(bank["format"])==Format&&Integer(bank["format_version"],1,1)==1&&Text(bank["status"])=="complete");
            string id=Text(bank["bank_id"]),set=Text(bank["set"]);Need(bank["demo"]?.Type==JTokenType.Boolean);bool demo=(bool)bank["demo"];
            Need(set=="demo"?demo&&id.StartsWith("DEMO-",StringComparison.Ordinal):!demo&&Regex.IsMatch(id,set=="pilot"?@"\Abank-P[0-9]{3}\z":set=="confirmatory"?@"\Abank-C[0-9]{3}\z":@"\A\z"));
            Need(id==package.PackageId&&demo==package.Demo&&Text(pkg["package_id"])==id);
            Need(Regex.IsMatch(Text(bank["bank_version"]),@"\A[0-9]+\.[0-9]+\.[0-9]+\z")&&IsHash(Text(bank["generation_config_sha256"]))&&Text(bank["seed_namespace"]).Length>0);
            long used=Integer(bank["attempt_used"],1,4);Need(bank["attempts"] is JArray attempts&&attempts.Count==used);
            for(int i=0;i<used;i++)
            {
                var attempt=bank["attempts"][i];Keys(attempt,new[]{"attempt","failed_cell","slots_sha256","slots_used","status","wall_ms"});
                bool last=i==used-1;Need(Integer(attempt["attempt"],1,4)==i+1&&Text(attempt["status"])==(last?"complete":"failed")&&IsHash(Text(attempt["slots_sha256"])));
                Integer(attempt["slots_used"],last?192:1,576);Integer(attempt["wall_ms"],0,long.MaxValue);Need(last==(attempt["failed_cell"].Type==JTokenType.Null));
            }
            // The bank was built from this dyad's package-safe permutation.
            Need(Text(bank["permutation_sha256"])==Sha(permutationBytes));
            var permutation=PackageLoader.ParseStrictDocument(permutationBytes,2*1024*1024);
            string slot=Text(bank["dyad_slot"]);Need(slot==Text(permutation["unit_id"]));
            string[] order=((JArray)bank["atom_order"]).Select(Text).ToArray();
            Need(order.Length==16&&order.Distinct(StringComparer.Ordinal).Count()==16&&order.SequenceEqual(((JArray)permutation["atom_order"]).Select(Text),StringComparer.Ordinal));
            var labels=permutation["atoms"].ToDictionary(x=>Text(x["atom_id"]),x=>Text(x["semantic_label"]),StringComparer.Ordinal);
            Keys(bank["labels"],labels.Keys);foreach(var p in ((JObject)bank["labels"]).Properties())Need(Text(p.Value)==labels[p.Name]);
            // Every displayed and reserve option equals the package's verified audio.
            Need(bank["cells"] is JArray cells&&cells.Count==48);var verified=new Dictionary<string,(string,string)>(StringComparer.Ordinal);int c=0;
            foreach(string profile in Profiles)
            {
                var seen=new HashSet<string>(StringComparer.Ordinal);
                foreach(string atom in order)
                {
                    var cell=bank["cells"][c++];Keys(cell,new[]{"atom_id","options","profile","slots_used"});
                    Need(Text(cell["profile"])==profile&&Text(cell["atom_id"])==atom&&cell["options"] is JArray list&&list.Count==4);Integer(cell["slots_used"],4,12);
                    for(int rank=1;rank<=4;rank++)
                    {
                        var option=cell["options"][rank-1];Keys(option,OptionKeys);
                        Need(Integer(option["rank"],1,4)==rank&&Text(option["menu"])==(rank==4?"reserve":"shown")&&Text(option["option_id"])==id+"."+profile+"."+atom+"."+rank&&Text(option["wav"])=="options/"+profile+"/"+atom+"-"+rank+".wav"&&Text(option["slot_id"]).Length>0);
                        Recipe(option["recipe"]);Need(Text(option["recipe_sha256"])==CanonicalSha256(option["recipe"]));
                        string pcm=Text(option["pcm_sha256"]),file=Text(option["file_sha256"]);Need(IsHash(pcm)&&IsHash(file)&&seen.Add(pcm));
                        var wave=package.ReadAtom(atom,profile,rank);Need(wave.PcmSha256==pcm&&wave.FileSha256==file);
                        verified.Add(Key(profile,atom,rank),(pcm,file));
                    }
                }
            }
            return new QualifiedBank(id,pin,set,demo,slot,verified);
        }
        // Rechecked before each atom menu: the candidate about to be shown must
        // still be the frozen bank's option, not only the package's.
        public void CheckOption(string profile,string atom,int rank,PcmWave wave)
        {Need(wave!=null&&rank>=1&&rank<=3&&options.TryGetValue(Key(profile,atom,rank),out var row)&&row.Pcm==wave.PcmSha256&&row.File==wave.FileSha256);}
    }
}
