using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.IO;
using System.Linq;
using System.Net;
using System.Text;
using System.Text.RegularExpressions;
using AcousticVocab.Foundation;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.SessionIntegration
{
    // A byte/pin boundary only. Domain loaders still validate package contents,
    // reviews, calibration, source health and store receipts independently.
    public sealed class PinnedJoinFile
    {
        public string Path { get; }
        public string Sha256 { get; }
        public long Length { get; }
        readonly int maximumBytes;
        internal PinnedJoinFile(string path,string sha256,int maximum)
        {
            Path=path;Sha256=sha256;maximumBytes=maximum;
            Length=JoinedEngineeringConfig.ReadPinned(path,sha256,maximum,null).Length;
        }
        // Never returns retained mutable bytes, and never trusts the first read
        // after a file or a parent directory has changed.
        public byte[] ReadVerified()=>JoinedEngineeringConfig.ReadPinned(Path,Sha256,maximumBytes,Length);
    }

    public sealed class JoinedEngineeringConfig
    {
        public const int MaximumConfigBytes=65536;
        static readonly string[] requiredFiles={"schedule","permutation","package_manifest","run_sheet_manifest","schedule_manifest","run_sheet_csv","schedule_schema","permutation_schema","run_sheet_schema","station","frame","state_source","neutral","response_panel"};
        static readonly string[] optionalFiles={"teaching_manifest","teaching_review","teaching_allocation","menu_script","menu_review","menu_allocation","reserved_registry","assessment_script","assessment_review","rating_review","speech_manifest","speech_review","audio_calibration","menu_snapshot","menu_bridge_config","comfort_gain","menu_replay_ledger"};
        static readonly string[] v2Files={"yoked_active_schedule","yoked_active_run_sheet_manifest","yoked_active_schedule_manifest","yoked_active_run_sheet_csv"};
        static readonly string[] directoryNames={"package","teaching","grammar","speech","menu_examples","menu_scripts","menu_mailbox","operator_mailbox","evidence"};
        static readonly string[] optionalDirectories={"teaching","grammar","speech","menu_examples","menu_scripts"};
        static readonly string[] pinNames={"package_sha256","bank_sha256","menu_manifest_sha256","menu_head_sha256","menu_snapshot_sha256"};
        readonly IReadOnlyDictionary<string,PinnedJoinFile> files;
        readonly IReadOnlyDictionary<string,string> directories,pins;
        public string ConfigSha256 { get; }
        public int ConfigVersion { get; }
        public int? YokedAnchorLeadMs { get; }
        public string ProtocolVersion { get; }
        public string StationId { get; }
        public string UnitId { get; }
        public string CodedId { get; }
        public string SessionId { get; }
        public string VisitId { get; }
        public string BuildId { get; }
        public string ControlEndpoint { get; }
        public string ControlSessionId { get; }
        public string Scope=>"DEMO_ENGINEERING";
        public bool ParticipantAdmission=>false;

        JoinedEngineeringConfig(string hash,string protocol,JObject identity,string endpoint,string controlSession,int version,int? anchorLead,
            Dictionary<string,PinnedJoinFile> files,Dictionary<string,string> directories,Dictionary<string,string> pins)
        {
            ConfigSha256=hash;ConfigVersion=version;YokedAnchorLeadMs=anchorLead;ProtocolVersion=protocol;StationId=(string)identity["station_id"];
            UnitId=(string)identity["unit_id"];CodedId=(string)identity["coded_id"];SessionId=(string)identity["session_id"];
            VisitId=(string)identity["visit_id"];BuildId=(string)identity["build_id"];
            ControlEndpoint=endpoint;ControlSessionId=controlSession;
            this.files=new ReadOnlyDictionary<string,PinnedJoinFile>(files);
            this.directories=new ReadOnlyDictionary<string,string>(directories);
            this.pins=new ReadOnlyDictionary<string,string>(pins);
        }
        public PinnedJoinFile RequireFile(string name)
        {
            Need(name!=null&&files.TryGetValue(name,out var unused)&&files[name]!=null,"SESSION_JOIN_FILE_REQUIRED");
            return files[name];
        }
        public bool TryFile(string name,out PinnedJoinFile value)
        {
            Need(name!=null&&files.ContainsKey(name),"SESSION_JOIN_FILE_NAME");
            value=files[name];return value!=null;
        }
        public string Directory(string name)
        {
            Need(name!=null&&directories.ContainsKey(name),"SESSION_JOIN_DIRECTORY_NAME");
            string path=directories[name];
            if(path!=null)
            {
                NoLinks(path);Need(!File.Exists(path),"SESSION_JOIN_DIRECTORY_KIND");
                if(name=="package"||optionalDirectories.Contains(name))Need(System.IO.Directory.Exists(path),"SESSION_JOIN_DIRECTORY_REQUIRED");
            }
            return path;
        }
        public string Pin(string name)
        {
            Need(name!=null&&pins.ContainsKey(name),"SESSION_JOIN_PIN_NAME");return pins[name];
        }

        public static JoinedEngineeringConfig Load(string configPath,string independentlyPinnedRawSha256,string protocolVersion)
        {
            try
            {
                Need(IsHash(independentlyPinnedRawSha256)&&Id(protocolVersion),"SESSION_JOIN_CONFIG_PIN");
                Need(!string.IsNullOrEmpty(configPath)&&!Unc(configPath),"SESSION_JOIN_CONFIG_PATH");
                string path=System.IO.Path.GetFullPath(configPath),root=System.IO.Path.GetDirectoryName(path);
                byte[] raw=ReadPinned(path,independentlyPinnedRawSha256,MaximumConfigBytes,null);
                var document=StationConfig.ParseStrict(new UTF8Encoding(false,true).GetString(raw));
                Need(document["version"]?.Type==JTokenType.Integer&&((long)document["version"]>=1&&(long)document["version"]<=3),"SESSION_JOIN_CONFIG_VERSION");
                int version=(int)document["version"];
                Keys(document,version==1?new[]{"version","scope","protocol_version","identity","files","directories","pins","control"}:new[]{"version","scope","protocol_version","identity","files","directories","pins","control","yoked_start"});
                int? anchorLead=null;
                if(version>=2&&document["yoked_start"].Type!=JTokenType.Null)
                {
                    var policy=Object(document["yoked_start"]);Keys(policy,"policy","lead_ms");
                    Need(Text(policy["policy"])=="operator_start_plus_lead"&&policy["lead_ms"].Type==JTokenType.Integer&&(long)policy["lead_ms"]>=2000&&(long)policy["lead_ms"]<=60000,"SESSION_JOIN_ANCHOR_POLICY");
                    anchorLead=(int)policy["lead_ms"];
                }
                Need(Text(document["scope"])=="DEMO_ENGINEERING","SESSION_JOIN_SCOPE");
                Need(Text(document["protocol_version"])==protocolVersion,"SESSION_JOIN_PROTOCOL");
                var identity=Object(document["identity"]);
                Keys(identity,"station_id","unit_id","coded_id","session_id","visit_id","build_id");
                // Actual DEMO producers use ordinary opaque unit/person IDs.
                // The domain loader requires package.Demo and schedule.Demo;
                // an invented ID prefix cannot establish that authority.
                foreach(string key in new[]{"station_id","unit_id","coded_id","visit_id","build_id"})Need(Id(Text(identity[key])),"SESSION_JOIN_IDENTITY");
                Need(Guid(Text(identity["session_id"])),"SESSION_JOIN_IDENTITY");
                var acceptedOptional=optionalFiles.Concat(version>=2?v2Files:Array.Empty<string>()).Concat(version>=3?new[]{"grammar_review"}:Array.Empty<string>()).ToArray();
                var fileRows=Object(document["files"]);Keys(fileRows,requiredFiles.Concat(acceptedOptional).ToArray());
                var files=new Dictionary<string,PinnedJoinFile>(StringComparer.Ordinal);
                foreach(string name in requiredFiles.Concat(acceptedOptional))
                {
                    JToken token=fileRows[name];
                    if(token.Type==JTokenType.Null){Need(acceptedOptional.Contains(name),"SESSION_JOIN_FILE_REQUIRED");files.Add(name,null);continue;}
                    var row=Object(token);Keys(row,"path","sha256");string sha=Text(row["sha256"]);Need(IsHash(sha),"SESSION_JOIN_FILE_PIN");
                    string full=Resolve(root,Text(row["path"]));
                    int maximum=name=="menu_replay_ledger"?32*1024*1024:name=="schedule"||name=="neutral"||name=="run_sheet_csv"||name=="yoked_active_schedule"||name=="yoked_active_run_sheet_csv"?16*1024*1024:1024*1024;
                    files.Add(name,new PinnedJoinFile(full,sha,maximum));
                }
                if(version==1)foreach(string name in v2Files)files.Add(name,null);
                if(version<3)files.Add("grammar_review",null);
                var dirRows=Object(document["directories"]);Keys(dirRows,directoryNames);
                var directories=new Dictionary<string,string>(StringComparer.Ordinal);
                foreach(string name in directoryNames)
                {
                    JToken token=dirRows[name];
                    if(token.Type==JTokenType.Null){Need(optionalDirectories.Contains(name),"SESSION_JOIN_DIRECTORY_REQUIRED");directories.Add(name,null);continue;}
                    string full=Resolve(root,Text(token));
                    Need(!File.Exists(full),"SESSION_JOIN_DIRECTORY_KIND");
                    if(name=="package"||optionalDirectories.Contains(name))Need(System.IO.Directory.Exists(full),"SESSION_JOIN_DIRECTORY_REQUIRED");
                    directories.Add(name,full);
                }
                var pinRows=Object(document["pins"]);Keys(pinRows,pinNames);
                var pins=new Dictionary<string,string>(StringComparer.Ordinal);
                foreach(string name in pinNames)
                {
                    if(pinRows[name].Type==JTokenType.Null){Need(name!="package_sha256","SESSION_JOIN_PIN_REQUIRED");pins.Add(name,null);}
                    else{string value=Text(pinRows[name]);Need(IsHash(value),"SESSION_JOIN_FILE_PIN");pins.Add(name,value);}
                }
                var control=Object(document["control"]);Keys(control,"endpoint","session_id");
                string endpoint=Text(control["endpoint"]),session=Text(control["session_id"]);
                Need(Uri.TryCreate(endpoint,UriKind.Absolute,out var uri)&&uri.Scheme=="ws"&&
                    IPAddress.TryParse(uri.Host,out var ip)&&IPAddress.IsLoopback(ip)&&uri.AbsolutePath=="/commands"&&
                    uri.UserInfo.Length==0&&uri.Query.Length==0&&uri.Fragment.Length==0,"SESSION_JOIN_CONTROL_ENDPOINT");
                Need(Guid(session),"SESSION_JOIN_CONTROL_SESSION");
                return new JoinedEngineeringConfig(independentlyPinnedRawSha256,protocolVersion,identity,endpoint,session,version,anchorLead,files,directories,pins);
            }
            catch(SessionFault){throw;}catch{throw new SessionFault("SESSION_JOIN_CONFIG_INVALID");}
        }

        static JObject Object(JToken token){Need(token is JObject,"SESSION_JOIN_CONFIG_SHAPE");return (JObject)token;}
        static void Keys(JObject value,params string[] keys)=>Need(value.Properties().Select(x=>x.Name).OrderBy(x=>x,StringComparer.Ordinal).SequenceEqual(keys.OrderBy(x=>x,StringComparer.Ordinal)),"SESSION_JOIN_CONFIG_SHAPE");
        static string Text(JToken token)
        {
            Need(token?.Type==JTokenType.String,"SESSION_JOIN_CONFIG_TYPE");string value=(string)token;
            Need(value.Length>0&&value.Length<=1024&&!value.Any(char.IsControl),"SESSION_JOIN_CONFIG_TYPE");return value;
        }
        static bool Id(string value)=>value!=null&&Regex.IsMatch(value,@"\A[A-Za-z0-9][A-Za-z0-9._-]{0,95}\z");
        static bool IsHash(string value)=>value!=null&&Regex.IsMatch(value,@"\A[0-9a-f]{64}\z");
        static bool Guid(string value)=>value!=null&&Regex.IsMatch(value,@"\A[0-9a-f]{32}\z");
        static bool Unc(string value)=>value.StartsWith("\\\\",StringComparison.Ordinal)||value.StartsWith("//",StringComparison.Ordinal);
        static void Need(bool condition,string code){if(!condition)throw new SessionFault(code);}
        static string Resolve(string root,string relative)
        {
            Need(!System.IO.Path.IsPathRooted(relative)&&Regex.IsMatch(relative,@"\A[A-Za-z0-9._/-]{1,1024}\z"),"SESSION_JOIN_RELATIVE_PATH");
            foreach(string segment in relative.Split('/'))
                Need(segment.Length>0&&segment.Length<=128&&segment!="."&&segment!=".."&&!segment.EndsWith(".",StringComparison.Ordinal)&&
                    !Regex.IsMatch(segment,@"\A(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|\z)",RegexOptions.IgnoreCase),"SESSION_JOIN_RELATIVE_PATH");
            string full=System.IO.Path.GetFullPath(System.IO.Path.Combine(root,relative.Replace('/',System.IO.Path.DirectorySeparatorChar)));
            var comparison=System.IO.Path.DirectorySeparatorChar=='\\'?StringComparison.OrdinalIgnoreCase:StringComparison.Ordinal;
            Need(full.StartsWith(root.TrimEnd(System.IO.Path.DirectorySeparatorChar)+System.IO.Path.DirectorySeparatorChar,comparison),"SESSION_JOIN_PATH_ESCAPE");
            NoLinks(full);return full;
        }
        internal static void NoLinks(string path)
        {
            Need(!Unc(path),"SESSION_JOIN_LOCAL_PATH");
            for(string at=System.IO.Path.GetFullPath(path);at!=null;at=System.IO.Path.GetDirectoryName(at))
            {
                try{Need((File.GetAttributes(at)&FileAttributes.ReparsePoint)==0,"SESSION_JOIN_PATH_LINK");}
                catch(FileNotFoundException){}catch(DirectoryNotFoundException){}
            }
        }
        internal static byte[] ReadPinned(string path,string sha256,int maximum,long? exactLength)
        {
            try
            {
                NoLinks(path);
                using var stream=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.Read);
                Need(stream.Length>0&&stream.Length<=maximum&&(!exactLength.HasValue||stream.Length==exactLength.Value),"SESSION_JOIN_FILE_SIZE");
                var bytes=new byte[(int)stream.Length];int offset=0;
                while(offset<bytes.Length){int count=stream.Read(bytes,offset,bytes.Length-offset);Need(count>0,"SESSION_JOIN_FILE_CHANGED");offset+=count;}
                Need(stream.ReadByte()==-1,"SESSION_JOIN_FILE_CHANGED");
                Need(PcmWave.Hash(bytes)==sha256,"SESSION_JOIN_FILE_HASH");NoLinks(path);return bytes;
            }
            catch(SessionFault){throw;}catch{throw new SessionFault("SESSION_JOIN_FILE_UNAVAILABLE");}
        }
    }
}
