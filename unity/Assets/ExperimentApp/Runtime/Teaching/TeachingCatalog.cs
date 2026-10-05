using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using AcousticVocab.Foundation;
using AcousticVocab.ResponsePanel;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.Teaching
{
    public readonly struct TeachingSelection
    {
        public readonly string Profile; public readonly int Rank;
        public TeachingSelection(string profile,int rank)
        {LessonTimeline.Require(new[]{"P1","P2","P3"}.Contains(profile)&&rank>=1&&rank<=4,"LESSON_SELECTION_INVALID");Profile=profile;Rank=rank;}
    }
    // Supplied only by the future verified Study B selection/admission path.
    // This module neither allocates profiles nor selects candidate ranks.
    public interface ITeachingSelections { string PackageSha256 {get;} bool OldHashesVerified {get;} TeachingSelection Get(string atomId); }
    public interface ISelectionMeaningAuthorization
    { bool TryConsume(string packageSha256,string scheduleSha256,string atomId); }
    public interface IPostStudyDictionaryAuthorization
    { bool TryConsume(string packageSha256,string scheduleSha256,string atomId); }
    public sealed class TeachingDisplay
    {
        public string MeaningDisplayId { get; }
        public string Definition { get; }
        public string ActionWords { get; }
        public string TargetWords { get; }
        public string ImageId { get; }
        public string ImageSha256 { get; }
        readonly byte[] image;
        internal TeachingDisplay(JObject row,byte[] png,string hash)
        {MeaningDisplayId=(string)row["meaning_display_id"];Definition=(string)row["definition"];ActionWords=(string)row["action_words"];TargetWords=(string)row["target_words"];ImageId=(string)row["image_id"];ImageSha256=hash;image=(byte[])png.Clone();}
        public byte[] CopyImage()=>(byte[])image.Clone();
    }
    internal sealed class LessonMaterial
    {
        internal TeachingDisplay Display;
        internal PcmWave Wave,Action,Referent;
        internal string ExpectedAction,ExpectedTarget;
        internal bool Atomic,Aligned;
    }
    public sealed class TeachingCatalog
    {
        readonly LoadedAudioPackage package;
        readonly JObject permutation,content;
        readonly Dictionary<string,TeachingDisplay> displays=new Dictionary<string,TeachingDisplay>(StringComparer.Ordinal);
        readonly string alignedFamily,visit;
        readonly HashSet<string> selectionAtoms=new HashSet<string>(StringComparer.Ordinal);
        public string Sha256 { get; }
        public string ReviewSha256 { get; }
        public string MethodologySha256 { get; }
        public bool Demo => package.Demo;
        public string PackageSha256 => package.PackageSha256;
        public string ScheduleSha256 { get; }
        TeachingCatalog(LoadedAudioPackage package,JObject permutation,JObject content,string hash,string reviewHash,string methodology,string schedule,string alignedFamily,string visit)
        {this.package=package;this.permutation=permutation;this.content=content;Sha256=hash;ReviewSha256=reviewHash;MethodologySha256=methodology;ScheduleSha256=schedule;this.alignedFamily=alignedFamily;this.visit=visit;}
        static void Require(bool condition)=>LessonTimeline.Require(condition,"LESSON_CATALOG_INVALID");
        static bool Hash(string value)=>value!=null&&Regex.IsMatch(value,@"\A[0-9a-f]{64}\z");
        static JObject Json(byte[] bytes)=>StationConfig.ParseStrict(new UTF8Encoding(false,true).GetString(bytes));
        static void Keys(JToken token,params string[] keys)=>Require(token is JObject o&&o.Properties().Select(x=>x.Name).OrderBy(x=>x,StringComparer.Ordinal).SequenceEqual(keys.OrderBy(x=>x,StringComparer.Ordinal)));
        static string Text(JToken token,int max=256)
        {Require(token?.Type==JTokenType.String);string s=(string)token;Require(s.Length>0&&s.Length<=max&&!s.Any(char.IsControl));return s;}
        static byte[] Read(string path,int limit)
        {
            var file=new FileInfo(Path.GetFullPath(path));Require(file.Exists&&file.Length>0&&file.Length<=limit);
            for(FileSystemInfo item=file;item!=null;item=item is FileInfo f?f.Directory:((DirectoryInfo)item).Parent)Require((item.Attributes&FileAttributes.ReparsePoint)==0);
            return File.ReadAllBytes(file.FullName);
        }
        // Pins are provided by the trusted admission/configuration boundary;
        // neither a local "reviewed" flag nor a self-asserted hash grants access.
        public static TeachingCatalog Load(string directory,string catalogSha,string reviewSha,LoadedAudioPackage package,VisitSchedule schedule,
            byte[] packageManifest,byte[] permutationBytes,byte[] allocationBytes=null,string allocationSha=null)
        {
            try
            {
                Require(Hash(catalogSha)&&Hash(reviewSha)&&package!=null&&schedule!=null&&package.PackageSha256==schedule.PackageSha256);
                byte[] catalogBytes=Read(Path.Combine(directory,"catalog.local.json"),65536),reviewBytes=Read(Path.Combine(directory,"review.local.json"),65536);
                Require(PcmWave.Hash(catalogBytes)==catalogSha&&PcmWave.Hash(reviewBytes)==reviewSha);
                var document=Json(catalogBytes);var review=Json(reviewBytes);var manifest=Json(packageManifest);
                Require(PackageLoader.CanonicalPackageHash(Encoding.UTF8.GetString(packageManifest))==package.PackageSha256);
                Require(PcmWave.Hash(permutationBytes)==(string)manifest["files"]?["permutation.json"]?["sha256"]);
                var permutation=Json(permutationBytes);
                Keys(document,"format","package_sha256","content","images","feedback");Require((string)document["format"]=="av-teaching/1"&&(string)document["package_sha256"]==package.PackageSha256);
                Keys(review,"version","catalog_sha256","approved","methodology_sha256");
                Require(review["version"].Type==JTokenType.Integer&&(int)review["version"]==1&&review["approved"].Type==JTokenType.Boolean&&(bool)review["approved"]&&(string)review["catalog_sha256"]==catalogSha&&Hash((string)review["methodology_sha256"]));
                string family=ResolveAlignedFamily(package.Study,schedule.PersonSlot,schedule.Demo,allocationBytes,allocationSha);
                var result=new TeachingCatalog(package,permutation,document,catalogSha,reviewSha,(string)review["methodology_sha256"],schedule.Sha256,family,schedule.Visit);
                Require(document["content"] is JArray&&document["images"] is JObject);
                var expected=new HashSet<string>(permutation["atoms"].Select(x=>(string)x["atom_id"]).Concat(permutation["messages"].Where(x=>(string)x["status"]=="trained").Select(x=>(string)x["message_id"])),StringComparer.Ordinal);
                Require(expected.Count==34);var usedImages=new HashSet<string>(StringComparer.Ordinal);var expectedFiles=new HashSet<string>(new[]{"catalog.local.json","review.local.json"},StringComparer.Ordinal);
                foreach(var token in document["content"])
                {
                    Keys(token,"content_id","meaning_display_id","definition","action_words","target_words","image_id");var row=(JObject)token;
                    string id=Text(row["content_id"]);Require(expected.Remove(id));Text(row["meaning_display_id"]);Text(row["definition"],512);
                    Require(row["action_words"].Type==JTokenType.String&&row["target_words"].Type==JTokenType.String);string words=(string)row["action_words"]+(string)row["target_words"];Require(words.Length>0&&words.Length<=256&&!words.Any(char.IsControl));
                    string imageId=Text(row["image_id"]);var spec=document["images"][imageId];Keys(spec,"file","sha256");string path=Text(spec["file"]);Require(Regex.IsMatch(path,@"\Aimages/[A-Za-z0-9_-]+\.png\z")&&Hash((string)spec["sha256"]));
                    byte[] png=Read(Path.Combine(directory,path.Replace('/',Path.DirectorySeparatorChar)),4*1024*1024);Require(PcmWave.Hash(png)==(string)spec["sha256"]&&png.Length>=33&&png.Take(8).SequenceEqual(new byte[]{137,80,78,71,13,10,26,10}));
                    uint Be(int at)=>(uint)png[at]<<24|(uint)png[at+1]<<16|(uint)png[at+2]<<8|png[at+3];
                    Require(Be(8)==13&&Encoding.ASCII.GetString(png,12,4)=="IHDR"&&Be(16)>0&&Be(16)<=2048&&Be(20)>0&&Be(20)<=2048);
                    result.displays.Add(id,new TeachingDisplay(row,png,(string)spec["sha256"]));usedImages.Add(imageId);expectedFiles.Add(path);
                }
                Require(expected.Count==0&&usedImages.SetEquals(((JObject)document["images"]).Properties().Select(x=>x.Name)));
                Keys(document["feedback"],"atomic","correct","incorrect","timeout");
                foreach(var row in ((JObject)document["feedback"]).Properties()) {Keys(row.Value,"id","text");Text(row.Value["id"]);Text(row.Value["text"]);}
                Require(((JObject)document["feedback"]).Properties().Select(x=>(string)x.Value["id"]).Distinct().Count()==4);
                var root=Path.GetFullPath(directory);var directories=new Queue<string>();directories.Enqueue(root);var actualFiles=new HashSet<string>(StringComparer.Ordinal);int entries=0;
                while(directories.Count>0)foreach(var path in Directory.GetFileSystemEntries(directories.Dequeue()))
                {var attributes=File.GetAttributes(path);Require(++entries<=128&&(attributes&FileAttributes.ReparsePoint)==0);if((attributes&FileAttributes.Directory)!=0)directories.Enqueue(path);else actualFiles.Add(Path.GetRelativePath(root,path).Replace('\\','/'));}
                Require(expectedFiles.SetEquals(actualFiles));
                foreach(var item in schedule.Blocks.SelectMany(x=>x.Items).Where(x=>x.Phase=="teaching"))result.Validate(item);
                foreach(var item in schedule.Blocks.SelectMany(x=>x.Items).Where(x=>x.TrialType=="atom_menu"&&x.Phase=="selection"))result.selectionAtoms.Add(item.ContentId);
                return result;
            }
            catch(SessionFault){throw;}catch{throw new SessionFault("LESSON_CATALOG_INVALID");}
        }
        internal static string ResolveAlignedFamily(string study,string slot,bool demo,byte[] allocationBytes,string allocationSha)
        {
            if(study=="A"){Require(allocationBytes==null&&allocationSha==null);return null;}
            Require(study=="B"&&allocationBytes!=null&&Hash(allocationSha)&&PcmWave.Hash(allocationBytes)==allocationSha);
            var allocation=Json(allocationBytes);
            Require(allocation["demo"]?.Type==JTokenType.Boolean&&(bool)allocation["demo"]==demo&&allocation["dyads"] is JArray);
            var match=allocation["dyads"].Where(x=>x["members"] is JArray&&x["members"].Any(m=>(string)m["slot_id"]==slot)).ToArray();Require(match.Length==1);
            string arm=(string)match[0]["sq_arm"],family=(string)match[0]["structured_family"];
            Require(arm=="SQ-1"&&family=="K"||arm=="SQ-2"&&family=="Q");return family;
        }
        internal void Validate(SlotItem item)
        {
            Require(!item.Heldout&&item.Phase=="teaching"&&displays.ContainsKey(item.ContentId)&&item.Plays==3);
            if(item.TrialType=="atomic_lesson")Require(item.SlotSeconds==20&&(item.Role=="action"||item.Role=="referent"));
            else Require(item.TrialType=="message_lesson"&&item.SlotSeconds==24&&item.Presentation==(package.Study=="A"||item.ContentId.StartsWith(alignedFamily+"-",StringComparison.Ordinal)?"structured":"dictionary"));
        }
        internal LessonMaterial Prepare(SlotItem item,ITeachingSelections selections)
        {
            Validate(item);bool atomic=item.TrialType=="atomic_lesson";var material=new LessonMaterial{Display=displays[item.ContentId],Atomic=atomic,Aligned=item.Presentation=="structured"};
            Require(package.Study=="A"||selections!=null&&selections.OldHashesVerified&&selections.PackageSha256==package.PackageSha256);
            TeachingSelection Select(string atom)=>package.Study=="A"?default:(selections??throw new SessionFault("LESSON_SELECTION_MISSING")).Get(atom);
            PcmWave Atom(string atom,TeachingSelection selected)=>package.ReadAtom(atom,selected.Profile,selected.Rank);
            if(atomic)
            {
                var semantic=permutation["atoms"].Single(x=>(string)x["atom_id"]==item.ContentId);Require((string)semantic["role"]==item.Role);
                string expected=(string)semantic["semantic_label"];if(item.Role=="action")material.ExpectedAction=expected;else material.ExpectedTarget=expected;
                material.Wave=material.Action=Atom(item.ContentId,Select(item.ContentId));
            }
            else
            {
                var semantic=permutation["messages"].Single(x=>(string)x["message_id"]==item.ContentId);Require((string)semantic["status"]=="trained");
                string a=(string)semantic["action_atom"],r=(string)semantic["referent_atom"];var sa=Select(a);var sr=Select(r);Require(sa.Profile==sr.Profile);
                material.Wave=package.ComposeTrainedMessage(item.ContentId,sa.Profile,sa.Rank,sr.Rank);material.Action=Atom(a,sa);material.Referent=Atom(r,sr);
                material.ExpectedAction=(string)semantic["semantic_action"];material.ExpectedTarget=(string)semantic["semantic_referent"];Require(PublicCommands.Legal(material.ExpectedTarget,material.ExpectedAction));
            }
            return material;
        }
        internal string Feedback(LessonMaterial material,PanelResponse response)
        {
            string key=material.Atomic?"atomic":response.Code==ResponseCode.Timeout?"timeout":response.Code==ResponseCode.Commit&&response.Action==material.ExpectedAction&&response.Target==material.ExpectedTarget?"correct":"incorrect";
            return (string)content["feedback"][key]["id"];
        }
        public string FeedbackText(string id)
        {var rows=((JObject)content["feedback"]).Properties().Where(x=>(string)x.Value["id"]==id).ToArray();Require(rows.Length==1);return(string)rows[0].Value["text"];}
        public TeachingDisplay ReadSelectionAtom(string atomId,ISelectionMeaningAuthorization authorization)
        {
            LessonTimeline.Require(package.Study=="B"&&new[]{"V1","V2","V3"}.Contains(visit)&&atomId!=null&&selectionAtoms.Contains(atomId)&&authorization!=null,"MENU_MEANING_REFUSED");
            LessonTimeline.Require(authorization.TryConsume(PackageSha256,ScheduleSha256,atomId),"MENU_MEANING_REFUSED");return displays[atomId];
        }
        // No PCM or phrase lookup exists here. The stage engine provides one
        // consultation token only after its B W4 final forms/validity boundary.
        public TeachingDisplay ReadPostStudyDictionaryAtom(string atomId,IPostStudyDictionaryAuthorization authorization)
        {
            LessonTimeline.Require(package.Study=="B"&&visit=="W4"&&atomId!=null&&permutation["atoms"].Any(x=>(string)x["atom_id"]==atomId)&&authorization!=null,"LESSON_DICTIONARY_REFUSED");
            LessonTimeline.Require(authorization.TryConsume(PackageSha256,ScheduleSha256,atomId),"LESSON_DICTIONARY_REFUSED");
            return displays[atomId];
        }
    }
}
