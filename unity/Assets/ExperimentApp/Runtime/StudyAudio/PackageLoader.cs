using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.StudyAudio
{
    public sealed class NovelSlotException : AudioFault
    { public NovelSlotException() : base("NOVEL_SLOT_NOT_AUTHORIZED") { } }

    internal sealed class AudioFileRecord
    { internal string Hash; internal long Length; }
    internal sealed class MessageRecord
    { internal string Action, Referent, Hash, Path; internal bool Trained; internal int Samples; }

    public sealed class LoadedAudioPackage
    {
        readonly string directory, manifestFileHash, profile;
        readonly Dictionary<string,AudioFileRecord> files;
        readonly Dictionary<string,string> atoms;
        readonly Dictionary<string,MessageRecord> messages;
        public string Study { get; }
        public string PackageId { get; }
        public string PackageSha256 { get; }
        public bool Demo { get; }
        public int CombinationsChecked { get; }
        public IReadOnlyList<string> AtomIds => Array.AsReadOnly(PackageRules.Atoms.ToArray());
        internal LoadedAudioPackage(string root,string manifestHash,JObject manifest,
            Dictionary<string,AudioFileRecord> allFiles,Dictionary<string,string> atomFiles,
            Dictionary<string,MessageRecord> indexedMessages,int checkedCount)
        {
            directory=root; manifestFileHash=manifestHash; files=allFiles; atoms=atomFiles; messages=indexedMessages;
            Study=(string)manifest["study"]; PackageId=(string)manifest["package_id"];
            PackageSha256=(string)manifest["package_sha256"]; Demo=(bool)manifest["demo"];
            profile=(string)manifest["profile"]; CombinationsChecked=checkedCount;
        }
        string Key(string id,string selectedProfile,int actionRank=0,int referentRank=0)
        {
            if(Study=="A")
            { PackageRules.Require((selectedProfile==null || selectedProfile==profile) && actionRank==0 && referentRank==0); return id; }
            PackageRules.Require(PackageRules.Profiles.Contains(selectedProfile) && actionRank>=1 && actionRank<=4);
            return selectedProfile+"/"+id+"/"+actionRank+(referentRank==0?"":"/"+referentRank);
        }
        PcmWave Read(string path)
        {
            try
            {
                // Catch post-load file, manifest, extra-file and link changes
                // before a buffer is handed to the explicit preload boundary.
                PackageRules.CheckTree(directory,files.Keys);
                PackageRules.Require(PcmWave.Hash(PackageRules.Read(directory,"manifest.json",2*1024*1024))==manifestFileHash);
                var spec=files[path]; byte[] bytes=PackageRules.Read(directory,path,spec.Length);
                PackageRules.Require(bytes.LongLength==spec.Length && PcmWave.Hash(bytes)==spec.Hash);
                return PcmWave.ParseCanonical(bytes);
            }
            catch(AudioIntegrityException) { throw; }
            catch(Exception) { throw new AudioIntegrityException(); }
        }
        public PcmWave ReadAtom(string atomId,string selectedProfile=null,int rank=0)
        {
            PackageRules.Require(atoms.TryGetValue(Key(atomId,selectedProfile,rank),out var path));
            return Read(path);
        }
        MessageRecord Get(string messageId,string selectedProfile,int actionRank,int referentRank)
        {
            if(Study=="B") PackageRules.Require(referentRank>=1 && referentRank<=4);
            PackageRules.Require(messages.TryGetValue(Key(messageId,selectedProfile,actionRank,referentRank),out var item));
            return item;
        }
        (PcmWave action,PcmWave referent) Parts(MessageRecord record) => (Read(record.Action),Read(record.Referent));
        public string CompositeHash(string messageId,string selectedProfile=null,int actionRank=0,int referentRank=0)
        {
            var item=Get(messageId,selectedProfile,actionRank,referentRank); var parts=Parts(item);
            string hash=MessageComposer.CompositeHash(parts.action,parts.referent); PackageRules.Require(hash==item.Hash); return hash;
        }
        public PcmWave ReadTrainedMessage(string messageId,string selectedProfile=null,int actionRank=0,int referentRank=0)
        {
            var item=Get(messageId,selectedProfile,actionRank,referentRank);
            if(!item.Trained) throw new NovelSlotException();
            if(item.Path!=null) { var wave=Read(item.Path); PackageRules.Require(wave.PcmSha256==item.Hash); return wave; }
            var parts=Parts(item); return MessageComposer.Compose(parts.action,parts.referent,item.Hash);
        }
        // Useful for the all-18 stored-vs-composed reproduction check. Heldout
        // buffers remain unavailable through this ordinary composition route.
        public PcmWave ComposeTrainedMessage(string messageId,string selectedProfile=null,int actionRank=0,int referentRank=0)
        {
            var item=Get(messageId,selectedProfile,actionRank,referentRank);
            if(!item.Trained) throw new NovelSlotException();
            var parts=Parts(item); return MessageComposer.Compose(parts.action,parts.referent,item.Hash);
        }
        public PcmWave ComposeApprovedNovel(string messageId,INovelSlotAuthorization authorization,
            string selectedProfile=null,int actionRank=0,int referentRank=0)
        {
            var item=Get(messageId,selectedProfile,actionRank,referentRank);
            if(item.Trained || authorization==null) throw new NovelSlotException();
            var parts=Parts(item);
            PackageRules.Require(MessageComposer.CompositeHash(parts.action,parts.referent)==item.Hash);
            // Only atom buffers exist before the trusted single-use decision.
            if(!authorization.TryConsume(PackageSha256,messageId)) throw new NovelSlotException();
            return MessageComposer.Compose(parts.action,parts.referent,item.Hash);
        }
    }

    public static class PackageLoader
    {
        public static LoadedAudioPackage Load(string directory,string expectedPackageSha256,bool allowDemo=false)
        {
            try { return LoadChecked(Path.GetFullPath(directory),expectedPackageSha256,allowDemo); }
            catch(AudioIntegrityException) { throw; }
            catch(Exception) { throw new AudioIntegrityException(); }
        }
        // Canonical manifest hash is exposed for independent producer vectors;
        // the input is not retained and no hidden document is returned.
        public static string CanonicalPackageHash(string manifestJson)
        {
            try
            {
                var value=PackageRules.Json(Encoding.UTF8.GetBytes(manifestJson)); value.Remove("package_sha256");
                return PcmWave.Hash(Encoding.ASCII.GetBytes(PackageRules.Canonical(value)));
            }
            catch(AudioIntegrityException) { throw; }
            catch(Exception) { throw new AudioIntegrityException(); }
        }
        static LoadedAudioPackage LoadChecked(string root,string expected,bool allowDemo)
        {
            PackageRules.Require(PackageRules.IsHash(expected));
            byte[] manifestBytes=PackageRules.Read(root,"manifest.json",2*1024*1024);
            var manifest=PackageRules.Json(manifestBytes); string study=PackageRules.String(manifest["study"]);
            PackageRules.Require(study=="A" || study=="B");
            PackageRules.Keys(manifest,new[]{"format","format_version","study","package_id","demo","builder","renderer_version","composition_contract","files","package_sha256"}
                .Concat(study=="A"?new[]{"profile","book"}:new[]{"bank"}).ToArray());
            PackageRules.Require((string)manifest["format"]=="av-sound/package" && PackageRules.Integer(manifest["format_version"])==1);
            string id=PackageRules.String(manifest["package_id"]); bool demo=PackageRules.Boolean(manifest["demo"]);
            PackageRules.Require(PackageRules.Match(id,@"[A-Za-z0-9][A-Za-z0-9-]{1,62}[A-Za-z0-9]") && id.StartsWith("DEMO-",StringComparison.Ordinal)==demo && (!demo || allowDemo));
            PackageRules.Keys(manifest["builder"],"name","version");
            PackageRules.Require((string)manifest["builder"]["name"]=="av-sound" && PackageRules.Match((string)manifest["builder"]["version"],@"[0-9]+\.[0-9]+\.[0-9]+") &&
                (string)manifest["renderer_version"]=="0.1.0" && (string)manifest["composition_contract"]=="1.0.0");
            if(study=="A")
            {
                PackageRules.Require(PackageRules.Profiles.Contains((string)manifest["profile"]));
                PackageRules.Keys(manifest["book"],"frozen_head","snapshot_sha256");
                foreach(var p in ((JObject)manifest["book"]).Properties()) PackageRules.Require(PackageRules.IsHash((string)p.Value));
            }
            else
            {
                PackageRules.Keys(manifest["bank"],"format","format_version","bank_sha256");
                PackageRules.Require((string)manifest["bank"]["format"]=="av-sound/provisional-bank" && PackageRules.Integer(manifest["bank"]["format_version"])==1 && PackageRules.IsHash((string)manifest["bank"]["bank_sha256"]));
            }
            PackageRules.Require((string)manifest["package_sha256"]==expected && CanonicalPackageHash(Encoding.UTF8.GetString(manifestBytes))==expected);
            var files=new Dictionary<string,AudioFileRecord>(StringComparer.Ordinal);
            PackageRules.Require(manifest["files"] is JObject listed && listed.Count>=2 && listed.Count<=1024);
            long total=0;
            foreach(var item in ((JObject)manifest["files"]).Properties())
            {
                PackageRules.Require(PackageRules.ValidPath(item.Name,study)); PackageRules.Keys(item.Value,"sha256","bytes");
                long length=PackageRules.Integer(item.Value["bytes"]); string hash=PackageRules.String(item.Value["sha256"]);
                PackageRules.Require(length>0 && length<=2*1024*1024 && PackageRules.IsHash(hash)); total+=length;
                files.Add(item.Name,new AudioFileRecord { Length=length,Hash=hash });
            }
            PackageRules.Require(total<=64*1024*1024 && files.ContainsKey("answers.json") && files.ContainsKey("audio.json"));
            PackageRules.CheckTree(root,files.Keys);
            var documents=new Dictionary<string,JObject>(StringComparer.Ordinal); var waves=new Dictionary<string,PcmWave>(StringComparer.Ordinal);
            foreach(var item in files)
            {
                byte[] bytes=PackageRules.Read(root,item.Key,item.Value.Length);
                PackageRules.Require(bytes.LongLength==item.Value.Length && PcmWave.Hash(bytes)==item.Value.Hash);
                if(item.Key.EndsWith(".wav",StringComparison.Ordinal)) waves.Add(item.Key,PcmWave.ParseCanonical(bytes));
                else documents.Add(item.Key,PackageRules.Json(bytes));
            }
            var answers=documents["answers.json"]; PackageRules.Answers(answers,study,id,demo);
            PackageRules.Slots(documents,files,answers,study,id,demo);
            var audio=documents["audio.json"];
            PackageRules.Keys(audio,new[]{"format","format_version","study","package_id","messages"}.Concat(study=="A"?new[]{"profile","atoms"}:new[]{"profiles","waves","options"}).ToArray());
            PackageRules.Identity(audio,"av-sound/package-audio",study,id);
            var atomFiles=new Dictionary<string,string>(StringComparer.Ordinal); var used=new HashSet<string>(StringComparer.Ordinal);
            if(study=="A")
            {
                PackageRules.Require((string)audio["profile"]==(string)manifest["profile"]);
                var rows=PackageRules.Array(audio["atoms"],16);
                for(int i=0;i<16;i++)
                {
                    var row=rows[i]; PackageRules.Keys(row,"atom_id","path","n_samples","pcm_sha256","file_sha256");
                    string atom=PackageRules.Atoms[i],path="atoms/"+atom+".wav";
                    PackageRules.Require((string)row["atom_id"]==atom); CheckWave(row,path,"pcm_sha256",waves,used,true); atomFiles.Add(atom,path);
                }
            }
            else
            {
                PackageRules.Strings(audio["profiles"],PackageRules.Profiles);
                var introduced=PackageRules.Array(audio["waves"],3);
                for(int w=1;w<=3;w++)
                {
                    var row=introduced[w-1]; PackageRules.Keys(row,"wave","visit","atoms");
                    PackageRules.Require(PackageRules.Integer(row["wave"])==w && (string)row["visit"]=="V"+w);
                    PackageRules.Strings(row["atoms"],PackageRules.Atoms.Where(a=>PackageRules.AtomWave(a)==w));
                }
                var rows=PackageRules.Array(audio["options"],192); int i=0;
                foreach(string p in PackageRules.Profiles) foreach(string atom in PackageRules.Atoms) for(int rank=1;rank<=4;rank++)
                {
                    var row=rows[i++]; PackageRules.Keys(row,"profile","atom_id","rank","menu","path","n_samples","pcm_sha256","file_sha256");
                    PackageRules.Require((string)row["profile"]==p && (string)row["atom_id"]==atom && PackageRules.Integer(row["rank"])==rank && (string)row["menu"]==(rank==4?"reserve":"shown"));
                    string path="options/"+p+"/"+atom+"-"+rank+".wav";
                    CheckWave(row,path,"pcm_sha256",waves,used,true); atomFiles.Add(p+"/"+atom+"/"+rank,path);
                }
            }
            var messages=new Dictionary<string,MessageRecord>(StringComparer.Ordinal); var messageRows=PackageRules.Array(audio["messages"],32); int m=0,checkedCount=0;
            foreach(string message in PackageRules.Messages)
            {
                var row=messageRows[m++]; string action=message.Substring(0,4),referent=message[0]+"-"+message.Substring(5);
                bool trained=PackageRules.Trained(message);
                PackageRules.Keys(row,(study=="A"?new[]{"message_id","action_atom","referent_atom","status","n_samples","duration_ms","composite_sha256","path","file_sha256"}:new[]{"message_id","action_atom","referent_atom","status","combinations"}));
                PackageRules.Require((string)row["message_id"]==message && (string)row["action_atom"]==action && (string)row["referent_atom"]==referent && (string)row["status"]==(trained?"trained":"heldout"));
                if(study=="A")
                {
                    string path=trained?"messages/"+message+".wav":null;
                    if(trained) CheckWave(row,path,"composite_sha256",waves,used,false);
                    else PackageRules.Require(row["path"].Type==JTokenType.Null && row["file_sha256"].Type==JTokenType.Null);
                    Add(message,row,atomFiles[action],atomFiles[referent],trained,path);
                }
                else
                {
                    var combinations=PackageRules.Array(row["combinations"],48); int c=0;
                    foreach(string p in PackageRules.Profiles) for(int a=1;a<=4;a++) for(int r=1;r<=4;r++)
                    {
                        var combination=combinations[c++]; PackageRules.Keys(combination,"profile","action_rank","referent_rank","n_samples","duration_ms","composite_sha256");
                        PackageRules.Require((string)combination["profile"]==p && PackageRules.Integer(combination["action_rank"])==a && PackageRules.Integer(combination["referent_rank"])==r);
                        Add(p+"/"+message+"/"+a+"/"+r,combination,atomFiles[p+"/"+action+"/"+a],atomFiles[p+"/"+referent+"/"+r],trained,null);
                    }
                }
            }
            PackageRules.Require(used.SetEquals(waves.Keys));
            return new LoadedAudioPackage(root,PcmWave.Hash(manifestBytes),manifest,files,atomFiles,messages,checkedCount);
            void Add(string key,JToken row,string action,string referent,bool trained,string path)
            {
                int count=checked((int)PackageRules.Integer(row["n_samples"])); string hash=PackageRules.String(row["composite_sha256"]);
                PackageRules.Require(PackageRules.IsHash(hash) && count==waves[action].SampleCount+MessageComposer.GapSamples+waves[referent].SampleCount &&
                    PackageRules.Integer(row["duration_ms"])*48==count && MessageComposer.CompositeHash(waves[action],waves[referent])==hash);
                messages.Add(key,new MessageRecord { Action=action,Referent=referent,Hash=hash,Samples=count,Trained=trained,Path=path }); checkedCount++;
            }
        }
        static void CheckWave(JToken row,string path,string hashKey,Dictionary<string,PcmWave> waves,HashSet<string> used,bool atom)
        {
            PackageRules.Require((string)row["path"]==path && waves.TryGetValue(path,out var wave));
            wave=waves[path]; PackageRules.Require((string)row["file_sha256"]==wave.FileSha256 && (string)row[hashKey]==wave.PcmSha256 && PackageRules.Integer(row["n_samples"])==wave.SampleCount);
            if(atom) MessageComposer.CheckAtom(wave); used.Add(path);
        }
    }

    internal static class PackageRules
    {
        internal static readonly string[] Profiles={"P1","P2","P3"};
        internal static readonly string[] Atoms=(from f in new[]{"K","Q"} from role in new[]{"a","r"} from n in Enumerable.Range(1,4) select f+"-"+role+n).ToArray();
        internal static readonly string[] Messages=(from f in new[]{"K","Q"} from a in Enumerable.Range(1,4) from r in Enumerable.Range(1,4) select f+"-a"+a+"-r"+r).ToArray();
        static readonly string[,] matrix={{"1","H-V1","2","H-W1"},{"H-W4","1","H-V2","3"},{"2","H-W1","2","3"},{"H-V3","3","3","H-W4"}};
        internal static void Require(bool value) { if(!value) throw new AudioIntegrityException(); }
        internal static bool Match(string value,string pattern) => value!=null && Regex.IsMatch(value,"\\A(?:"+pattern+")\\z",RegexOptions.CultureInvariant);
        internal static bool IsHash(string value) => Match(value,"[0-9a-f]{64}");
        internal static string String(JToken value) { Require(value?.Type==JTokenType.String); return (string)value; }
        internal static long Integer(JToken value) { Require(value?.Type==JTokenType.Integer); return (long)value; }
        internal static bool Boolean(JToken value) { Require(value?.Type==JTokenType.Boolean); return (bool)value; }
        internal static JArray Array(JToken value,int count) { Require(value is JArray a && a.Count==count); return (JArray)value; }
        internal static void Keys(JToken value,params string[] keys)
        { Require(value is JObject obj && new HashSet<string>(obj.Properties().Select(x=>x.Name),StringComparer.Ordinal).SetEquals(keys)); }
        internal static void Strings(JToken value,IEnumerable<string> expected)
        { Require(value is JArray array && array.Select(String).SequenceEqual(expected)); }
        internal static string Cell(string message) { Require(Messages.Contains(message)); return matrix[message[3]-'1',message[6]-'1']; }
        internal static bool Trained(string message) => Cell(message).Length==1;
        internal static int AtomWave(string atom) => atom[3]<'3'?1:atom[3]-'1';
        internal static JObject Json(byte[] bytes)
        {
            string raw=new UTF8Encoding(false,true).GetString(bytes);
            LexicalJson(raw);
            // Newtonsoft also accepts JavaScript extensions. Refuse those at
            // this format boundary before duplicate-key and shape validation.
            bool quoted=false,escaped=false; char previous='\0';
            foreach(char c in raw)
            {
                if(quoted) { if(escaped) escaped=false; else if(c=='\\') escaped=true; else if(c=='"') quoted=false; continue; }
                if(c=='"') { quoted=true; previous='"'; continue; }
                Require(c!='\'' && c!='/' && !((c=='}' || c==']') && previous==','));
                if(!char.IsWhiteSpace(c)) previous=c;
            }
            using(var scan=new JsonTextReader(new StringReader(raw)) { DateParseHandling=DateParseHandling.None,MaxDepth=64 })
                while(scan.Read())
                {
                    Require(scan.TokenType!=JsonToken.Comment && scan.TokenType!=JsonToken.Undefined && scan.TokenType!=JsonToken.StartConstructor);
                    if(scan.TokenType==JsonToken.PropertyName || scan.TokenType==JsonToken.String) Require(scan.QuoteChar=='"');
                    if(scan.TokenType==JsonToken.Float) Require(!double.IsNaN(Convert.ToDouble(scan.Value)) && !double.IsInfinity(Convert.ToDouble(scan.Value)));
                }
            using var text=new StringReader(raw);
            using var reader=new JsonTextReader(text) { DateParseHandling=DateParseHandling.None,MaxDepth=64 };
            var result=JObject.Load(reader,new JsonLoadSettings { DuplicatePropertyNameHandling=DuplicatePropertyNameHandling.Error });
            Require(!reader.Read()); return result;
        }
        static readonly Regex JsonNumber=new Regex(@"\G-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?",RegexOptions.CultureInvariant);
        static void LexicalJson(string raw)
        {
            for(int i=0;i<raw.Length;i++)
            {
                char c=raw[i]; if(" \t\r\n{}[]:,".IndexOf(c)>=0) continue;
                if(c=='"')
                {
                    bool ended=false;
                    while(++i<raw.Length)
                    {
                        c=raw[i]; Require(c>=32);
                        if(c=='"') { ended=true; break; }
                        if(c!='\\') continue;
                        Require(++i<raw.Length); c=raw[i]; Require("\"\\/bfnrtu".IndexOf(c)>=0);
                        if(c=='u') for(int n=0;n<4;n++) { Require(++i<raw.Length); Require(Uri.IsHexDigit(raw[i])); }
                    }
                    Require(ended); continue;
                }
                int end=i;
                if(c=='-' || c>='0' && c<='9') { var number=JsonNumber.Match(raw,i); Require(number.Success); end=i+number.Length; }
                else
                {
                    string literal=c=='t'?"true":c=='f'?"false":c=='n'?"null":null;
                    Require(literal!=null && i+literal.Length<=raw.Length && string.CompareOrdinal(raw,i,literal,0,literal.Length)==0); end=i+literal.Length;
                }
                Require(end==raw.Length || " \t\r\n,]}".IndexOf(raw[end])>=0); i=end-1;
            }
        }
        internal static string Canonical(JToken value)
        {
            if(value is JObject obj) return "{"+string.Join(",",obj.Properties().OrderBy(p=>p.Name,StringComparer.Ordinal).Select(p=>Canonical(new JValue(p.Name))+":"+Canonical(p.Value)))+"}";
            if(value.Type==JTokenType.String)
            { string s=(string)value; Require(s.All(c=>c>=32 && c<=126 && c!='"' && c!='\\')); return "\""+s+"\""; }
            if(value.Type==JTokenType.Integer) return ((long)value).ToString(CultureInfo.InvariantCulture);
            if(value.Type==JTokenType.Boolean) return (bool)value?"true":"false";
            throw new AudioIntegrityException();
        }
        internal static bool ValidPath(string path,string study)
        {
            string shared=@"answers\.json|audio\.json|permutation\.json|allocation\.json";
            return Match(path,shared+(study=="A"?@"|atoms/[KQ]-[ar][1-4]\.wav|messages/[KQ]-a(1-r1|1-r3|2-r2|2-r4|3-r1|3-r3|3-r4|4-r2|4-r3)\.wav|schedules/A-[PC][0-9]{2}-L[0-9]{2}/D[07]\.json":@"|options/P[123]/[KQ]-[ar][1-4]-[1-4]\.wav|schedules/B-[PCS][0-9]{2}-M[12]/(V[123]|W[14])\.json"));
        }
        static void NoLinks(string path)
        {
            var current=new DirectoryInfo(path);
            while(current!=null) { Require((current.Attributes&FileAttributes.ReparsePoint)==0); current=current.Parent; }
        }
        internal static byte[] Read(string root,string relative,long maximum)
        {
            NoLinks(root); string path=Path.Combine(root,relative.Replace('/',Path.DirectorySeparatorChar));
            NoLinks(Path.GetDirectoryName(path)); var info=new FileInfo(path);
            Require(info.Exists && (info.Attributes&FileAttributes.ReparsePoint)==0 && info.Length<=maximum);
            byte[] bytes=File.ReadAllBytes(path); Require(bytes.LongLength<=maximum); return bytes;
        }
        internal static void CheckTree(string root,IEnumerable<string> listed)
        {
            NoLinks(root); var found=new HashSet<string>(StringComparer.Ordinal);
            int entries=0;
            void Walk(DirectoryInfo directory,string prefix,int depth=0)
            {
                Require(depth<=3);
                foreach(var item in directory.EnumerateFileSystemInfos())
                {
                    Require(++entries<=2048);
                    Require((item.Attributes&FileAttributes.ReparsePoint)==0);
                    string relative=prefix+item.Name;
                    if(item is DirectoryInfo child) Walk(child,relative+"/",depth+1); else Require(found.Add(relative));
                }
            }
            Walk(new DirectoryInfo(root),""); Require(found.SetEquals(listed.Concat(new[]{"manifest.json"})));
        }
        internal static void Identity(JToken value,string format,string study,string id)
        { Require((string)value["format"]==format && Integer(value["format_version"])==1 && (string)value["study"]==study && (string)value["package_id"]==id); }
        internal static void Answers(JObject answers,string study,string id,bool demo)
        {
            Keys(answers,"format","format_version","study","package_id","demo","hidden_answer","atoms","messages","trained_message_ids","heldout_message_ids","heldout_sets");
            Identity(answers,"av-sound/package-answers",study,id); Require(Boolean(answers["demo"])==demo && Boolean(answers["hidden_answer"]));
            var rows=Array(answers["atoms"],16); var labels=new Dictionary<string,string>();
            for(int i=0;i<16;i++)
            {
                string atom=Atoms[i]; var row=rows[i]; Keys(row,"atom_id","family","role","index","semantic_label","wave");
                Require((string)row["atom_id"]==atom && (string)row["family"]==atom.Substring(0,1) && (string)row["role"]==(atom[2]=='a'?"action":"referent") && Integer(row["index"])==atom[3]-'0' && Integer(row["wave"])==AtomWave(atom));
                labels.Add(atom,String(row["semantic_label"]));
            }
            string[][] groups={new[]{"ADD_ONE","REMOVE_ONE","FLIP_CARD","ALIGN_ARROW"},new[]{"A","B","C","D"},new[]{"SCAN","TAG","CLOSE","QUARANTINE"},new[]{"E","F","G","H"}};
            for(int g=0;g<4;g++) Require(new HashSet<string>(Atoms.Skip(g*4).Take(4).Select(a=>labels[a])).SetEquals(groups[g]));
            var messages=Array(answers["messages"],32);
            for(int i=0;i<32;i++)
            {
                string idm=Messages[i],a=idm.Substring(0,4),r=idm[0]+"-"+idm.Substring(5); var row=messages[i];
                Keys(row,"message_id","family","action_atom","referent_atom","action_index","referent_index","semantic_action","semantic_referent","status","training_wave","heldout_set");
                Require((string)row["message_id"]==idm && (string)row["family"]==idm.Substring(0,1) && (string)row["action_atom"]==a && (string)row["referent_atom"]==r && Integer(row["action_index"])==idm[3]-'0' && Integer(row["referent_index"])==idm[6]-'0' && (string)row["semantic_action"]==labels[a] && (string)row["semantic_referent"]==labels[r]);
                Require((string)row["status"]==(Trained(idm)?"trained":"heldout"));
                if(Trained(idm)) Require(Integer(row["training_wave"])==int.Parse(Cell(idm),CultureInfo.InvariantCulture) && row["heldout_set"].Type==JTokenType.Null);
                else Require(row["training_wave"].Type==JTokenType.Null && (string)row["heldout_set"]==Cell(idm));
            }
            Strings(answers["trained_message_ids"],Messages.Where(Trained)); Strings(answers["heldout_message_ids"],Messages.Where(m=>!Trained(m)));
            Keys(answers["heldout_sets"],"H-V1","H-V2","H-V3","H-W1","H-W4");
            foreach(var p in ((JObject)answers["heldout_sets"]).Properties()) Strings(p.Value,Messages.Where(m=>Cell(m)==p.Name));
        }
        internal static void Slots(Dictionary<string,JObject> documents,Dictionary<string,AudioFileRecord> files,JObject answers,string study,string id,bool demo)
        {
            documents.TryGetValue("permutation.json",out var permutation); documents.TryGetValue("allocation.json",out var allocation);
            var atoms=((JArray)answers["atoms"]).ToDictionary(a=>(string)a["atom_id"]);
            var messages=((JArray)answers["messages"]).ToDictionary(a=>(string)a["message_id"]);
            if(permutation!=null)
            {
                Require((string)permutation["format"]=="av-schedules/permutation" && Integer(permutation["format_version"])==2 && (string)permutation["study"]==study && (!Boolean(permutation["demo"]) || demo));
                Require(study=="A"?(string)permutation["unit_kind"]=="batch":new[]{"dyad","spare"}.Contains((string)permutation["unit_kind"]));
                var order=Array(permutation["atom_order"],16).Select(String).ToArray(); Require(order.Distinct().Count()==16 && new HashSet<string>(order).SetEquals(Atoms));
                foreach(var row in Array(permutation["atoms"],16))
                {
                    string atom=String(row["atom_id"]); Require(atoms.ContainsKey(atom));
                    foreach(string field in new[]{"family","role","index","semantic_label"}) Require(JToken.DeepEquals(row[field],atoms[atom][field]));
                    Require(Integer(row["matrix_wave"])==AtomWave(atom));
                }
                Require(((JArray)permutation["atoms"]).Select(a=>(string)a["atom_id"]).Distinct().Count()==16);
                var seen=new HashSet<string>();
                foreach(var row in Array(permutation["messages"],32))
                {
                    string message=String(row["message_id"]); Require(seen.Add(message) && messages.ContainsKey(message));
                    foreach(var field in ((JObject)messages[message]).Properties().Where(p=>p.Name!="action_index" && p.Name!="referent_index")) Require(JToken.DeepEquals(row[field.Name],field.Value));
                }
                foreach(string family in new[]{"K","Q"}) foreach(string role in new[]{"action","referent"})
                    Strings(permutation["labels"][family][role],Atoms.Where(a=>a[0]==family[0] && a[2]==(role=="action"?'a':'r')).Select(a=>(string)atoms[a]["semantic_label"]));
                if(study=="B")
                {
                    Keys(permutation["wave_atom_order"],"1","2","3"); var all=new List<string>();
                    for(int w=1;w<=3;w++) { var list=((JArray)permutation["wave_atom_order"][w.ToString()]).Select(String).ToArray(); Require(list.Distinct().Count()==list.Length && new HashSet<string>(list).SetEquals(Atoms.Where(a=>AtomWave(a)==w))); all.AddRange(list); }
                    Require(order.SequenceEqual(all));
                }
                else Require(permutation["wave_atom_order"]==null);
            }
            string[] visits=study=="A"?new[]{"D0","D7"}:new[]{"V1","V2","V3","W1","W4"};
            string NovelSet(string visit,bool swap)
            {
                string set=visit=="D0"?"H-W1":visit=="D7"?"H-W4":"H-"+visit;
                return swap && set=="H-W1"?"H-W4":swap && set=="H-W4"?"H-W1":set;
            }
            if(allocation!=null)
            {
                Keys(allocation,new[]{"format","format_version","study","package_id","swap_w1_w4","novel_by_visit"}.Concat(study=="B" && allocation["structured_family"]!=null?new[]{"structured_family"}:System.Array.Empty<string>()).ToArray());
                Identity(allocation,"av-sound/package-allocation",study,id); bool swap=Boolean(allocation["swap_w1_w4"]); Keys(allocation["novel_by_visit"],visits);
                if(allocation["structured_family"]!=null) Require(new[]{"K","Q"}.Contains(String(allocation["structured_family"])));
                foreach(string visit in visits) Strings(allocation["novel_by_visit"][visit],Messages.Where(m=>Cell(m)==NovelSet(visit,swap)));
            }
            foreach(var entry in documents.Where(x=>x.Key.StartsWith("schedules/",StringComparison.Ordinal)))
            {
                var schedule=entry.Value; Require((string)schedule["format"]=="av-schedules/visit-schedule" && Integer(schedule["format_version"])==1 && Boolean(schedule["hidden_answer"]) && (!Boolean(schedule["demo"]) || demo) && (string)schedule["study"]==study);
                string visit=String(schedule["visit"]),person=String(schedule["person_id"]);
                Require(visits.Contains(visit) && entry.Key=="schedules/"+person+"/"+visit+".json" && permutation!=null && (string)schedule["unit_id"]==(string)permutation["unit_id"] && person.StartsWith((string)permutation["unit_id"]+"-",StringComparison.Ordinal) && (string)schedule["permutation_json_sha256"]==files["permutation.json"].Hash);
                bool swap=Boolean(schedule["swap_w1_w4"]); if(allocation!=null) Require(swap==Boolean(allocation["swap_w1_w4"]));
                foreach(string message in ((JArray)schedule["dictionary_messages"]).Select(String)) Require(Trained(message));
                foreach(var block in (JArray)schedule["blocks"]) foreach(var item in (JArray)block["items"])
                {
                    string kind=String(item["trial_type"]); string message=item["message_id"].Type==JTokenType.Null?null:String(item["message_id"]);
                    if(message!=null)
                    {
                        Require(messages.ContainsKey(message));
                        Require(Trained(message)?kind!="novel":kind=="novel" && Cell(message)==NovelSet(visit,swap));
                        if(kind=="message_lesson")
                        {
                            string structured=(string)allocation?["structured_family"];
                            if(study=="A" || structured!=null) Require((string)item["presentation"]==(study=="A" || message[0]==structured[0]?"structured":"dictionary"));
                        }
                    }
                    if(item["intended"] is JObject intended)
                    {
                        bool msg=(string)intended["kind"]=="message"; string key=String(intended[msg?"message_id":"atom_id"]);
                        var answer=msg?messages[key]:atoms[key];
                        foreach(string field in msg?new[]{"family","action_index","referent_index","semantic_action","semantic_referent"}:new[]{"family","role","index","semantic_label"}) Require(JToken.DeepEquals(intended[field],answer[field]));
                    }
                }
            }
        }
    }
}
