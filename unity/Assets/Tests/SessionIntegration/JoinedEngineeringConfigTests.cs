using System;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.SessionIntegration.Tests
{
    public sealed class JoinedEngineeringConfigTests
    {
        const string Protocol="DEMO-protocol-1";
        string root,path;
        JObject value;
        static readonly string[] Required={"schedule","permutation","package_manifest","run_sheet_manifest","schedule_manifest","run_sheet_csv","schedule_schema","permutation_schema","run_sheet_schema","station","frame","state_source","neutral","response_panel"};
        static readonly string[] Optional={"teaching_manifest","teaching_review","teaching_allocation","menu_script","menu_review","menu_allocation","reserved_registry","assessment_script","assessment_review","rating_review","speech_manifest","speech_review","audio_calibration","menu_snapshot","menu_bridge_config","comfort_gain","menu_replay_ledger"};
        [SetUp] public void Setup()
        {
            root=Path.Combine(Path.GetTempPath(),"av-join-config-test-"+Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);Directory.CreateDirectory(Path.Combine(root,"package"));path=Path.Combine(root,"join.local.json");
            var files=new JObject();
            foreach(string name in Required)
            {
                byte[] bytes=Encoding.UTF8.GetBytes("synthetic opaque "+name+" bytes\n");string file=name+".json";
                File.WriteAllBytes(Path.Combine(root,file),bytes);files[name]=new JObject{["path"]=file,["sha256"]=PcmWave.Hash(bytes)};
            }
            foreach(string name in Optional)files[name]=JValue.CreateNull();
            value=new JObject{["version"]=1,["scope"]="DEMO_ENGINEERING",["protocol_version"]=Protocol,
                ["identity"]=new JObject{["station_id"]="simulator-01",["unit_id"]="DEMO-UNIT",["coded_id"]="DEMO-CODED",["session_id"]=new string('a',32),["visit_id"]="V1",["build_id"]="DEMO-build"},
                ["files"]=files,["directories"]=new JObject{["package"]="package",["teaching"]=JValue.CreateNull(),["grammar"]=JValue.CreateNull(),["speech"]=JValue.CreateNull(),["menu_examples"]=JValue.CreateNull(),["menu_scripts"]=JValue.CreateNull(),["menu_mailbox"]="out/menu",["operator_mailbox"]="out/operator",["evidence"]="out/evidence"},
                ["pins"]=new JObject{["package_sha256"]=new string('b',64),["bank_sha256"]=JValue.CreateNull(),["menu_manifest_sha256"]=JValue.CreateNull(),["menu_head_sha256"]=JValue.CreateNull(),["menu_snapshot_sha256"]=JValue.CreateNull()},
                ["control"]=new JObject{["endpoint"]="ws://127.0.0.1:19001/commands",["session_id"]=new string('c',32)}};
        }
        [TearDown] public void Cleanup()
        {
            if(root==null)return;
            string full=Path.GetFullPath(root),prefix=Path.GetFullPath(Path.GetTempPath()).TrimEnd(Path.DirectorySeparatorChar)+Path.DirectorySeparatorChar+"av-join-config-test-";
            Assert.That(full.StartsWith(prefix,StringComparison.Ordinal),Is.True);
            if(Directory.Exists(full))Directory.Delete(full,true);
        }
        string Save(string text=null)
        {
            byte[] bytes=new UTF8Encoding(false).GetBytes(text??value.ToString(Formatting.None));File.WriteAllBytes(path,bytes);return PcmWave.Hash(bytes);
        }
        JoinedEngineeringConfig Load()=>JoinedEngineeringConfig.Load(path,Save(),Protocol);
        void Version2(int? lead=5000)
        {
            value["version"]=2;value["yoked_start"]=lead.HasValue?new JObject{["policy"]="operator_start_plus_lead",["lead_ms"]=lead.Value}:JValue.CreateNull();
            foreach(string name in new[]{"yoked_active_schedule","yoked_active_run_sheet_manifest","yoked_active_schedule_manifest","yoked_active_run_sheet_csv"})value["files"][name]=JValue.CreateNull();
        }
        [Test]public void Version2AnchorPolicyIsExplicitWithoutInventingAnAnchor()
        {Version2();var config=Load();Assert.That(config.ConfigVersion,Is.EqualTo(2));Assert.That(config.YokedAnchorLeadMs,Is.EqualTo(5000));Assert.That(config.TryFile("yoked_active_schedule",out _),Is.False);Assert.That(config.ParticipantAdmission,Is.False);Version2(null);Assert.That(Load().YokedAnchorLeadMs,Is.Null);}
        [TestCase(1999)][TestCase(60001)]public void AnchorLeadOutsideEngineeringBoundsIsRefused(int lead)
        {Version2(lead);Assert.Throws<SessionFault>(()=>Load());}
        [Test]public void PriorClockAnchorOrImplicitPolicyCannotEnterVersion2()
        {Version2();value["yoked_start"]["anchor_mono_ms"]=10000;Assert.Throws<SessionFault>(()=>Load());Version2();value["yoked_start"]["policy"]="automatic_now";Assert.Throws<SessionFault>(()=>Load());Version2();value["yoked_start"]["lead_ms"]=5000.5;Assert.Throws<SessionFault>(()=>Load());}
        [Test]public void Version1DoesNotGainAnchorAuthority()
        {var config=Load();Assert.That(config.ConfigVersion,Is.EqualTo(1));Assert.That(config.YokedAnchorLeadMs,Is.Null);Assert.That(config.TryFile("yoked_active_schedule",out _),Is.False);value["yoked_start"]=JValue.CreateNull();Assert.Throws<SessionFault>(()=>Load());}
        void Refused(Action<JObject> change,string code=null)
        {
            change(value);var fault=Assert.Throws<SessionFault>(()=>Load());if(code!=null)Assert.That(fault.Code,Is.EqualTo(code));
        }
        [Test] public void ValidPinsRemainEngineeringOnlyAndDoNotCreateOutputs()
        {
            var loaded=Load();Assert.That(loaded.ParticipantAdmission,Is.False);Assert.That(loaded.Scope,Is.EqualTo("DEMO_ENGINEERING"));
            Assert.That(loaded.UnitId,Is.EqualTo("DEMO-UNIT"));Assert.That(loaded.ProtocolVersion,Is.EqualTo(Protocol));
            Assert.That(loaded.Pin("package_sha256"),Is.EqualTo(new string('b',64)));
            Assert.That(loaded.Pin("bank_sha256"),Is.Null);Assert.That(loaded.TryFile("teaching_review",out var missing),Is.False);Assert.That(missing,Is.Null);
            Assert.That(loaded.Directory("teaching"),Is.Null);Assert.That(Directory.Exists(Path.Combine(root,"out")),Is.False);
            foreach(string name in Required)Assert.That(loaded.RequireFile(name).ReadVerified(),Is.Not.Empty);
        }
        [Test] public void OptionalFilesAreStillHashedAndReturnedBytesAreDetached()
        {
            value["files"]["teaching_review"]=value["files"]["station"].DeepClone();var loaded=Load();var file=loaded.RequireFile("teaching_review");
            byte[] first=file.ReadVerified(),original=(byte[])first.Clone();first[0]^=1;
            Assert.That(file.ReadVerified(),Is.EqualTo(original));Assert.That(file.Length,Is.EqualTo(original.Length));
            value["identity"]["coded_id"]="DEMO-MUTATED";Save();Assert.That(loaded.CodedId,Is.EqualTo("DEMO-CODED"));
        }
        [Test] public void RawConfigHashIsIndependentAndProtocolMustMatchCaller()
        {
            string pin=Save();Assert.Throws<SessionFault>(()=>JoinedEngineeringConfig.Load(path,new string('0',64),Protocol));
            Assert.Throws<SessionFault>(()=>JoinedEngineeringConfig.Load(path,pin,"DEMO-other"));
            File.AppendAllText(path," ");Assert.Throws<SessionFault>(()=>JoinedEngineeringConfig.Load(path,pin,Protocol));
        }
        [TestCase("PARTICIPANT")][TestCase("demo_engineering")]
        public void ScopeCannotGrantAdmission(string scope)=>Refused(x=>x["scope"]=scope,"SESSION_JOIN_SCOPE");
        [TestCase("unit_id")][TestCase("coded_id")]
        public void UnsafeIdentityRefused(string field)=>Refused(x=>x["identity"][field]="../ordinary-code","SESSION_JOIN_IDENTITY");
        [Test] public void ActualDemoProducerOpaqueIdsDoNotInventAdmission()
        {
            value["identity"]["unit_id"]="A-C01";value["identity"]["coded_id"]="A-C01-L01";
            var loaded=Load();Assert.That(loaded.UnitId,Is.EqualTo("A-C01"));Assert.That(loaded.CodedId,Is.EqualTo("A-C01-L01"));
            Assert.That(loaded.ParticipantAdmission,Is.False);
        }
        [Test] public void BooleanVersionAndSelfAssertedApprovalAreRefused()
        {
            Refused(x=>x["version"]=true);value["version"]=1;
            Refused(x=>x["approved"]=true,"SESSION_JOIN_CONFIG_SHAPE");
        }
        [Test] public void UnknownNestedFieldsAndMissingOptionalKeysRefused()
        {
            Refused(x=>x["files"]["station"]["qualified"]=true,"SESSION_JOIN_CONFIG_SHAPE");
            ((JObject)value["files"]["station"]).Remove("qualified");Refused(x=>((JObject)x["files"]).Remove("speech_review"),"SESSION_JOIN_CONFIG_SHAPE");
        }
        [Test] public void DuplicateKeysCommentsAndTrailingDocumentsRefused()
        {
            string json=value.ToString(Formatting.None);
            foreach(string altered in new[]{json.Replace("\"version\":1","\"version\":1,\"version\":1"),"/* comment */"+json,json+"{}"})
                Assert.Throws<SessionFault>(()=>JoinedEngineeringConfig.Load(path,Save(altered),Protocol));
        }
        [TestCase("../station.json")][TestCase("package/../station.json")][TestCase("/station.json")]
        [TestCase("C:/station.json")][TestCase("//server/share/file")][TestCase("package\\station.json")]
        [TestCase("station.json:stream")][TestCase("package//station.json")][TestCase("NUL.json")][TestCase("package./station.json")]
        public void UnsafeReferencedPathsRefused(string relative)=>Refused(x=>x["files"]["station"]["path"]=relative,"SESSION_JOIN_RELATIVE_PATH");
        [Test] public void MissingTamperedAndOversizedFilesRefused()
        {
            string name=Path.Combine(root,"station.json");byte[] original=File.ReadAllBytes(name);File.Delete(name);Assert.Throws<SessionFault>(()=>Load());
            File.WriteAllBytes(name,original.Select(x=>(byte)(x^1)).ToArray());Assert.Throws<SessionFault>(()=>Load());
            File.WriteAllBytes(name,new byte[1024*1024+1]);Assert.Throws<SessionFault>(()=>Load());
        }
        [Test] public void ReadVerifiedRejectsChangedBytesOrLengthAfterInitialLoad()
        {
            var pinned=Load().RequireFile("station");byte[] original=File.ReadAllBytes(pinned.Path),changed=(byte[])original.Clone();changed[0]^=1;
            File.WriteAllBytes(pinned.Path,changed);Assert.Throws<SessionFault>(()=>pinned.ReadVerified());
            File.WriteAllBytes(pinned.Path,original.Concat(new byte[]{0}).ToArray());Assert.Throws<SessionFault>(()=>pinned.ReadVerified());
        }
        [Test] public void RequiredNullFilesPinsAndDirectoriesRefused()
        {
            Refused(x=>x["files"]["schedule"]=JValue.CreateNull(),"SESSION_JOIN_FILE_REQUIRED");
            value["files"]["schedule"]=value["files"]["station"].DeepClone();Refused(x=>x["pins"]["package_sha256"]=JValue.CreateNull(),"SESSION_JOIN_PIN_REQUIRED");
            value["pins"]["package_sha256"]=new string('b',64);Refused(x=>x["directories"]["package"]=JValue.CreateNull(),"SESSION_JOIN_DIRECTORY_REQUIRED");
        }
        [Test] public void PresentOptionalDirectoryMustExistAndCannotBeAFile()
        {
            Refused(x=>x["directories"]["teaching"]="absent","SESSION_JOIN_DIRECTORY_REQUIRED");
            Refused(x=>x["directories"]["teaching"]="station.json","SESSION_JOIN_DIRECTORY_KIND");
        }
        [TestCase("ws://192.168.1.9:19001/commands")][TestCase("ws://localhost:19001/commands")]
        [TestCase("wss://127.0.0.1:19001/commands")][TestCase("ws://127.0.0.1:19001/state")]
        [TestCase("ws://user@127.0.0.1:19001/commands")][TestCase("ws://127.0.0.1:19001/commands?a=1")]
        [TestCase("ws://127.0.0.1:19001/commands#fragment")]
        public void UnboundOrNonlocalControlRefused(string endpoint)=>Refused(x=>x["control"]["endpoint"]=endpoint,"SESSION_JOIN_CONTROL_ENDPOINT");
        [Test] public void BadSessionAndHashTypesRefused()
        {
            Refused(x=>x["control"]["session_id"]="not-a-session","SESSION_JOIN_CONTROL_SESSION");
            value["control"]["session_id"]=new string('c',32);Refused(x=>x["pins"]["package_sha256"]=new string('A',64),"SESSION_JOIN_FILE_PIN");
        }
        [Test] public void HelpersRefuseUnknownNamesRatherThanReturningImplicitDefaults()
        {
            var loaded=Load();Assert.Throws<SessionFault>(()=>loaded.RequireFile("missing"));Assert.Throws<SessionFault>(()=>loaded.TryFile("missing",out _));
            Assert.Throws<SessionFault>(()=>loaded.Directory("missing"));Assert.Throws<SessionFault>(()=>loaded.Pin("missing"));
            Assert.Throws<SessionFault>(()=>loaded.RequireFile("teaching_review"));
        }
        [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]
        [return: MarshalAs(UnmanagedType.I1)]
        static extern bool CreateSymbolicLink(string link,string target,int flags);
        [DllImport("libc",EntryPoint="symlink",SetLastError=true)]
        static extern int PosixSymbolicLink(string target,string link);
        [Test] public void RealDirectoryLinkIsRefusedBeforeReadingPinnedContent()
        {
            string target=Path.Combine(root,"target"),link=Path.Combine(root,"linked");Directory.CreateDirectory(target);
            File.Copy(Path.Combine(root,"station.json"),Path.Combine(target,"station.json"));
            bool made=Path.DirectorySeparatorChar=='\\'?CreateSymbolicLink(link,target,3):PosixSymbolicLink(target,link)==0;
            if(!made)Assert.Ignore("Platform did not permit an owned temporary symlink; no link-protection pass claimed.");
            try{Refused(x=>x["files"]["station"]["path"]="linked/station.json","SESSION_JOIN_PATH_LINK");}
            finally{Directory.Delete(link);}
        }
    }
}
