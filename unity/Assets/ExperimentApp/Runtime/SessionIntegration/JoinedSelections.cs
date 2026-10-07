using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.CompilerServices;
using System.Text;
using System.Text.RegularExpressions;
using AcousticVocab.Assessment;
using AcousticVocab.Foundation;
using AcousticVocab.SelectionMenus;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using AcousticVocab.Teaching;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

[assembly: InternalsVisibleTo("AcousticVocab.SessionIntegration.Tests")]
[assembly: InternalsVisibleTo("AcousticVocab.SessionIntegration.PlayModeTests")]

namespace AcousticVocab.SessionIntegration
{
    // Visit-owned view of immutable package identities and the actual B store.
    // Module leases may discard this view, but never dispose or reset its store.
    // ScheduleLoader must first bind verifiedPermutation to this exact package.
    // No semantic answer, PCM buffer, selection default or novel permit is exposed.
    public sealed class JoinedSelections : ITeachingSelections, IAssessmentSelections
    {
        readonly LoadedAudioPackage package;
        readonly ITeachingSelections store;
        readonly HashSet<string> atoms=new HashSet<string>(StringComparer.Ordinal);
        readonly Dictionary<string,(string action,string referent)> messages=new Dictionary<string,(string,string)>(StringComparer.Ordinal);
        public string PackageSha256=>package.PackageSha256;
        public bool OldHashesVerified=>package.Study=="A"||store.PackageSha256==PackageSha256&&store.OldHashesVerified;

        public JoinedSelections(LoadedAudioPackage package,byte[] verifiedPermutation,FileMenuStore store=null)
            :this(package,verifiedPermutation,(ITeachingSelections)store){}

        // Pure adapter seam only; production construction accepts FileMenuStore.
        internal JoinedSelections(LoadedAudioPackage package,byte[] verifiedPermutation,ITeachingSelections store)
        {
            Need(package!=null&&verifiedPermutation!=null&&verifiedPermutation.Length>0&&verifiedPermutation.Length<=1024*1024,"JOIN_SELECTION_CONFIGURATION");
            this.package=package;this.store=store;
            Need(package.Study=="A"?store==null:package.Study=="B"&&store!=null&&store.PackageSha256==package.PackageSha256,"JOIN_SELECTION_PACKAGE");
            var value=Json(verifiedPermutation);
            Need(Text(value["format"])=="av-schedules/permutation"&&value["format_version"]?.Type==JTokenType.Integer&&JToken.DeepEquals(value["format_version"],new JValue(2))&&Text(value["study"])==package.Study&&value["demo"]?.Type==JTokenType.Boolean&&(bool)value["demo"]==package.Demo,"JOIN_SELECTION_PERMUTATION");
            Need(value["atoms"] is JArray&&value["messages"] is JArray&&value["atoms"].Count()==16&&value["messages"].Count()==32,"JOIN_SELECTION_PERMUTATION");
            foreach(var row in value["atoms"])
            {
                Need(row is JObject,"JOIN_SELECTION_PERMUTATION");string id=Text(row["atom_id"]);
                Need(Regex.IsMatch(id,@"\A[KQ]-[ar][1-4]\z")&&atoms.Add(id)&&Text(row["family"])==id.Substring(0,1)&&Text(row["role"])==(id[2]=='a'?"action":"referent")&&row["index"]?.Type==JTokenType.Integer&&JToken.DeepEquals(row["index"],new JValue(id[3]-'0')),"JOIN_SELECTION_PERMUTATION");
            }
            Need(atoms.SetEquals(package.AtomIds),"JOIN_SELECTION_PERMUTATION");
            foreach(var row in value["messages"])
            {
                Need(row is JObject,"JOIN_SELECTION_PERMUTATION");string id=Text(row["message_id"]);
                Need(Regex.IsMatch(id,@"\A[KQ]-a[1-4]-r[1-4]\z"),"JOIN_SELECTION_PERMUTATION");
                string action=id.Substring(0,4),referent=id.Substring(0,2)+id.Substring(5,2);
                Need(Text(row["action_atom"])==action&&Text(row["referent_atom"])==referent&&Text(row["family"])==id.Substring(0,1)&&atoms.Contains(action)&&atoms.Contains(referent)&&messages.TryAdd(id,(action,referent)),"JOIN_SELECTION_PERMUTATION");
            }
        }

        public TeachingSelection Get(string atomId)
        {
            Need(atomId!=null&&atoms.Contains(atomId)&&OldHashesVerified,"JOIN_SELECTION_UNAVAILABLE");
            // Fixed Study A package chooses its already sealed profile; its
            // loader requires the sentinel null/zero, not a fabricated rank.
            if(package.Study=="A")return default;
            var selection=store.Get(atomId);
            Need(new[]{"P1","P2","P3"}.Contains(selection.Profile)&&selection.Rank>=1&&selection.Rank<=3&&OldHashesVerified,"JOIN_SELECTION_UNAVAILABLE");
            return selection;
        }
        public AudioSelection For(string contentId)
        {
            Need(contentId!=null&&OldHashesVerified,"JOIN_SELECTION_UNAVAILABLE");
            if(atoms.Contains(contentId))
            {
                var atom=Get(contentId);return contentId[2]=='a'?new AudioSelection(atom.Profile,atom.Rank,0):new AudioSelection(atom.Profile,0,atom.Rank);
            }
            Need(messages.TryGetValue(contentId,out var pair),"JOIN_SELECTION_UNAVAILABLE");
            var action=Get(pair.action);var referent=Get(pair.referent);
            Need(action.Profile==referent.Profile&&OldHashesVerified,"JOIN_SELECTION_PROFILE_MISMATCH");
            return new AudioSelection(action.Profile,action.Rank,referent.Rank);
        }

        public static MenuStoreBinding CreateStoreBinding(byte[] bridgeBytes,string rawBridgePin,byte[] verifiedPackageManifest,
            LoadedAudioPackage package,string expectedUnitId,string expectedBankSha256)
        {
            Need(bridgeBytes!=null&&bridgeBytes.Length>0&&bridgeBytes.Length<=65536&&PcmWave.Hash(bridgeBytes)==rawBridgePin&&package!=null&&package.Demo&&package.Study=="B","JOIN_STORE_CONFIG_PIN");
            var config=Json(bridgeBytes);
            Keys(config,"schema_version","unit_id","book_id","bank_sha256","package_sha256","demo_only","bank_path","bank_file_sha256","package_path","store_root");
            Need(config["schema_version"]?.Type==JTokenType.Integer&&JToken.DeepEquals(config["schema_version"],new JValue(1))&&config["demo_only"]?.Type==JTokenType.Boolean&&(bool)config["demo_only"],"JOIN_STORE_CONFIG_SCOPE");
            foreach(string key in new[]{"bank_path","package_path","store_root"})
            {string path=Text(config[key]);Need(path.Length<=32767&&!path.Any(char.IsControl)&&!path.Replace('\\','/').StartsWith("//",StringComparison.Ordinal),"JOIN_STORE_CONFIG_PATH");}
            string bridgeUnit=BridgeUnitId(expectedUnitId);
            Need(IsHash(Text(config["bank_file_sha256"]))&&Text(config["unit_id"])==bridgeUnit&&Text(config["package_sha256"])==package.PackageSha256&&Text(config["bank_sha256"])==expectedBankSha256,"JOIN_STORE_CONFIG_BINDING");
            Need(verifiedPackageManifest!=null&&verifiedPackageManifest.Length>0&&verifiedPackageManifest.Length<=2*1024*1024,"JOIN_STORE_PACKAGE_PIN");
            string manifestText=new UTF8Encoding(false,true).GetString(verifiedPackageManifest);var manifest=Json(verifiedPackageManifest);
            Need(PackageLoader.CanonicalPackageHash(manifestText)==package.PackageSha256&&Text(manifest["package_sha256"])==package.PackageSha256&&manifest["bank"] is JObject&&Text(manifest["bank"]["bank_sha256"])==expectedBankSha256,"JOIN_STORE_PACKAGE_PIN");
            return new MenuStoreBinding(bridgeUnit,Text(config["book_id"]),expectedBankSha256,package.PackageSha256,PcmWave.Hash(Encoding.ASCII.GetBytes(Canonical(config))));
        }
        // The synthetic #11 bridge reserves a DEMO- namespace. Keep the actual
        // producer unit unchanged everywhere else and require this exact mapping
        // in the independently pinned bridge config; never accept an alias.
        public static string BridgeUnitId(string producerUnitId)
        {
            Need(producerUnitId!=null&&Regex.IsMatch(producerUnitId,@"\A[A-Za-z0-9-]{1,58}\z"),"JOIN_STORE_UNIT_NAMESPACE");
            return "DEMO-"+producerUnitId;
        }
        // Shared across the active/yoked pair. Per-person coded/session/build
        // identifiers and local paths are deliberately absent. Inputs must come
        // from the independently pinned package/permutation/bank/unit boundary.
        public static string SharedUnitBindingSha256(string packageHash,string permutationRawHash,string bankHash,string unitId)
        {
            Need(IsHash(packageHash)&&IsHash(permutationRawHash)&&IsHash(bankHash)&&unitId!=null&&Regex.IsMatch(unitId,@"\A[A-Za-z0-9][A-Za-z0-9._-]{0,95}\z"),"JOIN_UNIT_BINDING");
            var identity=new JObject{["format"]="av-joined-unit-binding/1",["package_sha256"]=packageHash,["permutation_sha256"]=permutationRawHash,["bank_sha256"]=bankHash,["unit_id"]=unitId};
            return PcmWave.Hash(Encoding.ASCII.GetBytes(Canonical(identity)));
        }
        static string Canonical(JObject value)=>JsonConvert.SerializeObject(new JObject(value.Properties().OrderBy(x=>x.Name,StringComparer.Ordinal).Select(x=>new JProperty(x.Name,x.Value.DeepClone()))),Formatting.None,new JsonSerializerSettings{StringEscapeHandling=StringEscapeHandling.EscapeNonAscii});
        static bool IsHash(string value)=>value!=null&&Regex.IsMatch(value,@"\A[0-9a-f]{64}\z");
        static JObject Json(byte[] bytes){try{return StationConfig.ParseStrict(new UTF8Encoding(false,true).GetString(bytes));}catch{throw new SessionFault("JOIN_SELECTION_JSON");}}
        static string Text(JToken token){Need(token?.Type==JTokenType.String&&!string.IsNullOrEmpty((string)token),"JOIN_SELECTION_SHAPE");return(string)token;}
        static void Keys(JObject value,params string[] keys)=>Need(value.Properties().Select(x=>x.Name).OrderBy(x=>x,StringComparer.Ordinal).SequenceEqual(keys.OrderBy(x=>x,StringComparer.Ordinal)),"JOIN_STORE_CONFIG_SHAPE");
        static void Need(bool condition,string code){if(!condition)throw new SessionFault(code);}
    }
}
