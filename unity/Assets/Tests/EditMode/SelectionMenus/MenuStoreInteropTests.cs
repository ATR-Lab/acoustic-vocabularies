using System;
using System.IO;
using System.Linq;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
namespace AcousticVocab.SelectionMenus.Tests
{
    public sealed class MenuStoreInteropTests
    {
        string root;JObject summary,config,initial,profileResponse,atomResponse,profileRequest,atomRequest;LoadedAudioPackage package;MenuStoreBinding binding;
        [SetUp]public void Setup()
        {
            root=Environment.GetEnvironmentVariable("AV_MENU_STORE_INTEROP");if(root==null||!Directory.Exists(root))Assert.Ignore("Provision the real #11/#13 DEMO-only interop fixtures");
            summary=JObject.Parse(File.ReadAllText(Path.Combine(root,"summary.json")));foreach(var file in ((JObject)summary["files"]).Properties())Assert.That(PcmWave.Hash(File.ReadAllBytes(Path.Combine(root,file.Name))),Is.EqualTo((string)file.Value));
            config=Read("config.json");package=PackageLoader.Load((string)summary["package_path"],(string)summary["package_sha256"],true);binding=new MenuStoreBinding((string)config["unit_id"],(string)config["book_id"],(string)config["bank_sha256"],package.PackageSha256,(string)summary["config_sha256"]);
            initial=Read("initial-snapshot.json");profileResponse=Read("profile-response.json");atomResponse=Read("atom-response.json");profileRequest=Read("profile-request.json");atomRequest=Read("atom-request.json");
        }
        JObject Read(string name)=>MenuStoreCodec.Parse(File.ReadAllBytes(Path.Combine(root,name)));
        MenuStoreSnapshot Initial()=>MenuStoreCodec.Snapshot(initial,(string)summary["initial_manifest_sha256"],null,(string)initial["snapshot_sha256"],binding,package);
        MenuStoreSnapshot Profile()=>MenuStoreCodec.Receipt((JObject)profileResponse["receipt"],(JObject)profileResponse["snapshot"],profileRequest,Initial(),binding,package);
        [Test]public void ActualPythonReceiptsAndSnapshotsAgreeWithUnityAndTeachingSelection()
        {
            var first=Profile();Assert.That(first.Profile,Is.EqualTo("P1"));Assert.That(first.Entries,Is.Empty);Assert.That(first.ProfileReceipt,Is.EqualTo((string)profileResponse["receipt"]["receipt_sha256"]));
            var last=MenuStoreCodec.Receipt((JObject)atomResponse["receipt"],(JObject)atomResponse["snapshot"],atomRequest,first,binding,package);Assert.That(last.Entries.Keys,Is.EqualTo(new[]{"K-a1"}));Assert.That((int)last.Entries["K-a1"]["rank"],Is.EqualTo(1));Assert.That(last.ManifestHash,Is.EqualTo((string)summary["final_manifest_sha256"]));
            Assert.That(MenuStoreCodec.VerifySelfHash(Read("verify-response.json"),"response_sha256"),Is.EqualTo((string)Read("verify-response.json")["response_sha256"]));
        }
        [Test]public void ReplyFromAnotherRequestOrChangedPriorSnapshotIsRefused()
        {var request=(JObject)atomRequest.DeepClone();request["request_id"]=Guid.NewGuid().ToString("N");Assert.Throws<SessionFault>(()=>MenuStoreCodec.Receipt((JObject)atomResponse["receipt"],(JObject)atomResponse["snapshot"],request,Profile(),binding,package));var before=Profile();before.SnapshotHash=new string('b',64);Assert.Throws<SessionFault>(()=>MenuStoreCodec.Receipt((JObject)atomResponse["receipt"],(JObject)atomResponse["snapshot"],atomRequest,before,binding,package));}
        [Test]public void ChangedHashOrNonintegerRankOrExtraPrivateFieldCannotSelfAuthorize()
        {
            foreach(string field in new[]{"pcm_sha256","rank","answer"})
            {var snapshot=(JObject)atomResponse["snapshot"].DeepClone();var entry=(JObject)snapshot["entries"][0];if(field=="rank")entry[field]=1.0;else entry[field]=new string('b',64);snapshot.Remove("manifest_sha256");snapshot["manifest_sha256"]=PcmWave.Hash(MenuJson.Bytes(snapshot));Assert.Throws<SessionFault>(()=>MenuStoreCodec.Receipt((JObject)atomResponse["receipt"],snapshot,atomRequest,Profile(),binding,package));}
        }
        [Test]public void FilePortRequiresFreshVerifyAndTimesOutWithoutInventingReady()
        {
            string mailbox=Path.Combine(Path.GetTempPath(),"av-store-"+Guid.NewGuid().ToString("N"));double now=0;int intents=0;
            try{using var port=new FileMenuStore(mailbox,binding,package,File.ReadAllBytes(Path.Combine(root,"initial-snapshot.json")),(string)summary["initial_manifest_sha256"],null,(string)initial["snapshot_sha256"],package.AtomIds.Where(x=>x.EndsWith("1")||x.EndsWith("2")),_=>intents++,()=>now);Assert.That(port.Ready,Is.False);Assert.That(intents,Is.EqualTo(1));Assert.That(Directory.GetFiles(Path.Combine(mailbox,"requests"),"*.json").Length,Is.EqualTo(1));Assert.That(Directory.GetFiles(Path.Combine(mailbox,"requests"),"*.tmp"),Is.Empty);now=2001;Assert.Throws<SessionFault>(port.Pump);Assert.That(port.OldHashesVerified,Is.False);}
            finally{if(Directory.Exists(mailbox))Directory.Delete(mailbox,true);}
        }
        [Test]public void NetworkMailboxAndPartialWaveReconstructionAreRefusedBeforePublishing()
        {
            var atoms=package.AtomIds.Where(x=>x.EndsWith("1")||x.EndsWith("2"));
            foreach(string network in new[]{@"\\unavailable\private","//unavailable/private"})Assert.Throws<SessionFault>(()=>new FileMenuStore(network,binding,package,File.ReadAllBytes(Path.Combine(root,"initial-snapshot.json")),(string)summary["initial_manifest_sha256"],null,(string)initial["snapshot_sha256"],atoms,_=>Assert.Fail("Must not publish"),()=>0));
            var partial=(JObject)atomResponse["snapshot"];Assert.Throws<SessionFault>(()=>new FileMenuStore(Path.Combine(Path.GetTempPath(),Guid.NewGuid().ToString("N")),binding,package,System.Text.Encoding.UTF8.GetBytes(partial.ToString()),(string)partial["manifest_sha256"],(string)partial["book_head"],(string)partial["snapshot_sha256"],atoms,_=>Assert.Fail("Must not publish"),()=>0));
        }
        [Test]public void FinalWaveVerificationCannotAcceptOnlyAnInitialEmptyBook()
        {
            string mailbox=Path.Combine(Path.GetTempPath(),"av-wave-"+Guid.NewGuid().ToString("N"));try
            {
                using var port=new FileMenuStore(mailbox,binding,package,File.ReadAllBytes(Path.Combine(root,"initial-snapshot.json")),(string)summary["initial_manifest_sha256"],null,(string)initial["snapshot_sha256"],package.AtomIds.Where(x=>x.EndsWith("1")||x.EndsWith("2")),_=>{},()=>0);
                var request=JObject.Parse(File.ReadAllText(Directory.GetFiles(Path.Combine(mailbox,"requests"),"*.json").Single()));var reply=new JObject{["schema_version"]=1,["request_id"]=request["request_id"],["request_sha256"]=PcmWave.Hash(MenuJson.Bytes(request)),["config_sha256"]=binding.ConfigSha256,["receipt"]=JValue.CreateNull(),["snapshot"]=initial.DeepClone(),["error"]=JValue.CreateNull()};reply["response_sha256"]=PcmWave.Hash(MenuJson.Bytes(reply));File.WriteAllBytes(Path.Combine(mailbox,"responses",(string)request["request_id"]+".json"),MenuJson.Bytes(reply));port.Pump();Assert.That(port.Ready,Is.True);Assert.Throws<SessionFault>(()=>port.RequestVerification(true));Assert.That(Directory.GetFiles(Path.Combine(mailbox,"requests"),"*.json").Length,Is.EqualTo(1));
            }finally{if(Directory.Exists(mailbox))Directory.Delete(mailbox,true);}
        }
    }
}
