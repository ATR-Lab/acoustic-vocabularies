using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;
namespace AcousticVocab.Foundation
{
    // Constructible only in the explicitly compiled simulation player (or unit
    // tests in Editor). This capability never represents human review, comfort,
    // acoustic calibration or participant admission.
    public sealed class SimulationTestAuthority
    {
        public string RawSha256{get;}public string FixtureSetSha256{get;}public string PackageSha256{get;}public string ScheduleSha256{get;}
        public string OutputDirectory{get;}public float AudioGain{get;}public bool ParticipantAdmission=>false;
        SimulationTestAuthority(string hash,JObject p)
        {RawSha256=hash;FixtureSetSha256=(string)p["fixture_set_sha256"];PackageSha256=(string)p["package_sha256"];ScheduleSha256=(string)p["schedule_sha256"];OutputDirectory=Path.GetFullPath((string)p["output_directory"]);AudioGain=(float)p["audio_gain"];}
        public static bool CompiledCapability
        {
            get
            {
#if UNITY_EDITOR || AV_SIMULATION_TEST
                return true;
#else
                return false;
#endif
            }
        }
        static void Need(bool okay){if(!okay)throw new InvalidDataException("SIMULATION_AUTHORITY_INVALID");}
        static bool Hash(string value)=>value!=null&&Regex.IsMatch(value,@"\A[0-9a-f]{64}\z");
        static void Keys(JObject p,params string[] keys)=>Need(p!=null&&p.Properties().Select(x=>x.Name).OrderBy(x=>x).SequenceEqual(keys.OrderBy(x=>x)));
        public static SimulationTestAuthority Load(string path,string rawPin,string build,string protocol)
        {
            Need(CompiledCapability&&Hash(rawPin)&&Path.IsPathRooted(path));NoLinks(path);
            var info=new FileInfo(path);Need(info.Exists&&info.Length>0&&info.Length<=65536);
            byte[] bytes=File.ReadAllBytes(path);using var sha=SHA256.Create();Need(BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-","").ToLowerInvariant()==rawPin);
            var p=StationConfig.ParseStrict(new UTF8Encoding(false,true).GetString(bytes));
            Keys(p,"version","scope","fixture_set_sha256","package_sha256","schedule_sha256","build_id","protocol_version","output_directory","audio_gain","participant_admission","acoustic_qualification");
            Need(p["version"].Type==JTokenType.Integer&&(int)p["version"]==1&&(string)p["scope"]=="SIMULATION_TEST"&&(string)p["build_id"]==build&&(string)p["protocol_version"]==protocol);
            foreach(string name in new[]{"fixture_set_sha256","package_sha256","schedule_sha256"})Need(p[name].Type==JTokenType.String&&Hash((string)p[name]));
            Need(p["participant_admission"].Type==JTokenType.Boolean&&!(bool)p["participant_admission"]&&p["acoustic_qualification"].Type==JTokenType.Boolean&&!(bool)p["acoustic_qualification"]);
            Need(p["audio_gain"].Type is JTokenType.Float or JTokenType.Integer&&(double)p["audio_gain"]>0&&(double)p["audio_gain"]<=.1);
            string output=(string)p["output_directory"];Need(output!=null&&Path.IsPathRooted(output)&&!output.StartsWith("\\\\")&&!output.StartsWith("//"));NoLinks(output);
            // A dedicated unmistakably named root is mandatory, never a normal
            // pilot/confirmatory journal directory selected by a browser.
            Need(Path.GetFullPath(output).Split(Path.DirectorySeparatorChar,Path.AltDirectorySeparatorChar).Any(x=>x.StartsWith("simulation-test-",StringComparison.Ordinal)));
            Need(Path.GetFullPath(output).Split(Path.DirectorySeparatorChar,Path.AltDirectorySeparatorChar).Any(x=>new[]{".local","private","local-data"}.Contains(x,StringComparer.OrdinalIgnoreCase)));
            return new SimulationTestAuthority(rawPin,p);
        }
        public void Bind(string package,string schedule,bool packageDemo,bool scheduleDemo,string output)
        {Need(CompiledCapability&&packageDemo&&scheduleDemo&&package==PackageSha256&&schedule==ScheduleSha256&&Path.GetFullPath(output)==OutputDirectory);NoLinks(output);}
        public void Attest(JObject record,string role,JObject bindings)
        {
            var expected=new JObject{["version"]=1,["scope"]="SIMULATION_TEST",["role"]=role,["fixture_set_sha256"]=FixtureSetSha256,["bindings"]=bindings};
            Need(CompiledCapability&&JToken.DeepEquals(record,expected));
        }
        static void NoLinks(string path)
        {for(string at=Path.GetFullPath(path);at!=null;at=Path.GetDirectoryName(at)){try{Need((File.GetAttributes(at)&FileAttributes.ReparsePoint)==0);}catch(FileNotFoundException){}catch(DirectoryNotFoundException){}}}
    }
}
