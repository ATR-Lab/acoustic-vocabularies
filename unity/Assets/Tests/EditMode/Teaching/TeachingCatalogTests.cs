using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Teaching.Tests
{
    public sealed class TeachingCatalogTests
    {
        string directory,packageRoot,catalogRoot,repository,allocationHash;
        byte[] allocationBytes;
        LoadedAudioPackage package;
        VisitSchedule schedule;
        JObject catalog;
        string catalogHash,reviewHash;
        const string Hash="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        static byte[] Wave(byte[] pcm)
        {using var stream=new MemoryStream();using var w=new BinaryWriter(stream);w.Write(Encoding.ASCII.GetBytes("RIFF"));w.Write(36+pcm.Length);w.Write(Encoding.ASCII.GetBytes("WAVEfmt "));w.Write(16);w.Write((short)1);w.Write((short)1);w.Write(48000);w.Write(96000);w.Write((short)2);w.Write((short)16);w.Write(Encoding.ASCII.GetBytes("data"));w.Write(pcm.Length);w.Write(pcm);return stream.ToArray();}
        static void Json(string path,JObject value)=>File.WriteAllText(path,value.ToString()+"\n",new UTF8Encoding(false));
        [SetUp]public void Setup()
        {
            directory=Path.Combine(Path.GetTempPath(),"av-teaching-test-"+Guid.NewGuid().ToString("N"));packageRoot=Path.Combine(directory,"package");catalogRoot=Path.Combine(directory,"catalog");Directory.CreateDirectory(packageRoot);Directory.CreateDirectory(Path.Combine(catalogRoot,"images"));
            var repo=new DirectoryInfo(Directory.GetCurrentDirectory());while(repo!=null&&!Directory.Exists(Path.Combine(repo.FullName,"sound","examples","package-demo")))repo=repo.Parent;Assert.That(repo,Is.Not.Null);repository=repo.FullName;allocationBytes=null;allocationHash=null;
            foreach(string file in Directory.GetFiles(Path.Combine(repo.FullName,"sound","examples","package-demo"),"*.json"))File.Copy(file,Path.Combine(packageRoot,Path.GetFileName(file)));
            Directory.CreateDirectory(Path.Combine(packageRoot,"atoms"));Directory.CreateDirectory(Path.Combine(packageRoot,"messages"));
            var audio=JObject.Parse(File.ReadAllText(Path.Combine(packageRoot,"audio.json")));var atoms=new Dictionary<string,PcmWave>();int n=0;
            foreach(var atom in audio["atoms"])
            {
                var pcm=new byte[(int)atom["n_samples"]*2];for(int i=0;i<pcm.Length;i++)pcm[i]=(byte)((i*3+n)%251);n++;
                byte[] wav=Wave(pcm);var parsed=PcmWave.ParseCanonical(wav);atoms.Add((string)atom["atom_id"],parsed);File.WriteAllBytes(Path.Combine(packageRoot,(string)atom["path"]),wav);atom["pcm_sha256"]=parsed.PcmSha256;atom["file_sha256"]=parsed.FileSha256;
            }
            foreach(var row in audio["messages"])
            {
                var a=atoms[(string)row["action_atom"]];var r=atoms[(string)row["referent_atom"]];row["composite_sha256"]=MessageComposer.CompositeHash(a,r);
                if((string)row["status"]=="trained")
                {var pcm=new byte[(a.SampleCount+9600+r.SampleCount)*2];Buffer.BlockCopy(a.CopyPcm16(),0,pcm,0,a.SampleCount*2);Buffer.BlockCopy(r.CopyPcm16(),0,pcm,a.SampleCount*2+19200,r.SampleCount*2);byte[] wav=Wave(pcm);File.WriteAllBytes(Path.Combine(packageRoot,(string)row["path"]),wav);row["file_sha256"]=PcmWave.Hash(wav);}
            }
            Json(Path.Combine(packageRoot,"audio.json"),audio);var manifest=JObject.Parse(File.ReadAllText(Path.Combine(packageRoot,"manifest.json")));
            foreach(var entry in ((JObject)manifest["files"]).Properties()){var bytes=File.ReadAllBytes(Path.Combine(packageRoot,entry.Name));entry.Value["bytes"]=bytes.Length;entry.Value["sha256"]=PcmWave.Hash(bytes);}
            manifest["package_sha256"]=PackageLoader.CanonicalPackageHash(manifest.ToString());Json(Path.Combine(packageRoot,"manifest.json"),manifest);package=PackageLoader.Load(packageRoot,(string)manifest["package_sha256"],true);
            schedule=new VisitSchedule(Hash,package.PackageSha256,"DEMO-slot","D0",true,Array.Empty<ScheduleBlock>());
            byte[] png=Convert.FromBase64String("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a7V8AAAAASUVORK5CYII=");File.WriteAllBytes(Path.Combine(catalogRoot,"images","fixture.png"),png);
            var permutation=JObject.Parse(File.ReadAllText(Path.Combine(packageRoot,"permutation.json")));
            var ids=permutation["atoms"].Select(x=>(string)x["atom_id"]).Concat(permutation["messages"].Where(x=>(string)x["status"]=="trained").Select(x=>(string)x["message_id"]));
            catalog=new JObject{["format"]="av-teaching/1",["package_sha256"]=package.PackageSha256,["content"]=new JArray(ids.Select(id=>new JObject{["content_id"]=id,["meaning_display_id"]="DEMO-display-"+id,["definition"]="Synthetic definition",["action_words"]="Synthetic action",["target_words"]="Synthetic target",["image_id"]="fixture"})),["images"]=new JObject{["fixture"]=new JObject{["file"]="images/fixture.png",["sha256"]=PcmWave.Hash(png)}},["feedback"]=new JObject()};
            foreach(string kind in new[]{"atomic","correct","incorrect","timeout"})catalog["feedback"][kind]=new JObject{["id"]="DEMO-feedback-"+kind,["text"]="Synthetic feedback"};SealCatalog();
        }
        void SealCatalog()
        {Json(Path.Combine(catalogRoot,"catalog.local.json"),catalog);catalogHash=PcmWave.Hash(File.ReadAllBytes(Path.Combine(catalogRoot,"catalog.local.json")));Json(Path.Combine(catalogRoot,"review.local.json"),new JObject{["version"]=1,["catalog_sha256"]=catalogHash,["approved"]=true,["methodology_sha256"]=Hash});reviewHash=PcmWave.Hash(File.ReadAllBytes(Path.Combine(catalogRoot,"review.local.json")));}
        TeachingCatalog Load()=>TeachingCatalog.Load(catalogRoot,catalogHash,reviewHash,package,schedule,File.ReadAllBytes(Path.Combine(packageRoot,"manifest.json")),File.ReadAllBytes(Path.Combine(packageRoot,"permutation.json")),allocationBytes,allocationHash);
        [TearDown]public void Cleanup(){if(directory!=null&&Directory.Exists(directory))Directory.Delete(directory,true);}
        [Test]public void AllTrainedContentBindsActualProducerPermutationAndComposition()
        {
            var loaded=Load();var rows=JObject.Parse(File.ReadAllText(Path.Combine(packageRoot,"permutation.json")))["messages"];
            int count=0;foreach(var row in rows.Where(x=>(string)x["status"]=="trained"))
            {var item=new SlotItem("DEMO-"+count++,"message_lesson",(string)row["message_id"],null,"structured","teaching",false,24,3,1);var material=loaded.Prepare(item,null);Assert.That(material.Wave.PcmSha256,Is.EqualTo(package.CompositeHash(item.ContentId)));Assert.That(material.ExpectedAction,Is.EqualTo((string)row["semantic_action"]));Assert.That(material.ExpectedTarget,Is.EqualTo((string)row["semantic_referent"]));}
            Assert.That(count,Is.EqualTo(18));
        }
        [Test]public void ChangedReviewCannotSelfAuthorize()
        {File.AppendAllText(Path.Combine(catalogRoot,"review.local.json")," ");Assert.Throws<SessionFault>(()=>Load());}
        [Test]public void ChangedImageRefused()
        {File.AppendAllText(Path.Combine(catalogRoot,"images","fixture.png")," ");Assert.Throws<SessionFault>(()=>Load());}
        [Test]public void ExtraFileRefused()
        {File.WriteAllText(Path.Combine(catalogRoot,"extra.txt"),"unexpected");Assert.Throws<SessionFault>(()=>Load());}
        [Test]public void UnknownCatalogKeyRefused()
        {catalog["heldout_allowed"]=true;SealCatalog();Assert.Throws<SessionFault>(()=>Load());}
        [Test]public void HeldoutInsteadOfTrainedContentRefused()
        {catalog["content"][16]["content_id"]="K-a1-r2";SealCatalog();Assert.Throws<SessionFault>(()=>Load());}
        [Test]public void StudyARejectsDictionarySchedule()
        {var item=new SlotItem("DEMO-dictionary","message_lesson","K-a1-r1",null,"dictionary","teaching",false,24,3,1);Assert.Throws<SessionFault>(()=>Load().Prepare(item,null));}
        [Test]public void CatalogCannotChangeScoringAnswer()
        {catalog["content"][0]["expected_action"]="SCAN";SealCatalog();Assert.Throws<SessionFault>(()=>Load());}
        [Test]public void MissingFeedbackOrInvalidReviewBooleanRefused()
        {var path=Path.Combine(catalogRoot,"review.local.json");var review=JObject.Parse(File.ReadAllText(path));review["approved"]="true";Json(path,review);reviewHash=PcmWave.Hash(File.ReadAllBytes(path));Assert.Throws<SessionFault>(()=>Load());}
        [Test]public void OversizedPngHeaderRefusedBeforeTextureAllocation()
        {byte[] png=File.ReadAllBytes(Path.Combine(catalogRoot,"images","fixture.png"));png[16]=1;File.WriteAllBytes(Path.Combine(catalogRoot,"images","fixture.png"),png);catalog["images"]["fixture"]["sha256"]=PcmWave.Hash(png);SealCatalog();Assert.Throws<SessionFault>(()=>Load());}
        sealed class Selection:ITeachingSelections
        {public string PackageSha256{get;set;}public bool OldHashesVerified=>true;public readonly Dictionary<string,int> Counts=new Dictionary<string,int>();public TeachingSelection Get(string atom){Counts[atom]=Counts.TryGetValue(atom,out var n)?n+1:1;return new TeachingSelection("P1",1);}}
        sealed class DictionaryPermit:IPostStudyDictionaryAuthorization
        {internal bool Used;internal string Package,Schedule,Atom;public bool TryConsume(string package,string schedule,string atom){if(Used)return false;Used=true;Package=package;Schedule=schedule;Atom=atom;return true;}}
        [Test]public void ActualProducerBSelectionsAndOneUsePostStudyDictionaryWhenProvisioned()
        {
            string root=Environment.GetEnvironmentVariable("AV_PACKAGE_DEMO_ROOT")??Path.Combine(repository,".local","producer-package-demo");
            if(!Directory.Exists(root))Assert.Ignore("Provision the verified DEMO-only #13 producer artifact; no participant fallback");
            packageRoot=Path.Combine(root,"dyad-demo");var manifest=JObject.Parse(File.ReadAllText(Path.Combine(packageRoot,"manifest.json")));package=PackageLoader.Load(packageRoot,(string)manifest["package_sha256"],true);
            Assert.That(package.Study,Is.EqualTo("B"));Assert.That(package.CombinationsChecked,Is.EqualTo(1536));
            schedule=new VisitSchedule(Hash,package.PackageSha256,"B-C01-M1","W4",true,Array.Empty<ScheduleBlock>());allocationBytes=File.ReadAllBytes(Path.Combine(repository,"schedules","examples","demo-allocation","B","confirmatory-dyads.json"));allocationHash=PcmWave.Hash(allocationBytes);
            catalog["package_sha256"]=package.PackageSha256;SealCatalog();var loaded=Load();var selected=new Selection{PackageSha256=package.PackageSha256};
            var item=new SlotItem("DEMO-B","message_lesson","K-a1-r1",null,"dictionary","teaching",false,24,3,1);var material=loaded.Prepare(item,selected);
            Assert.That(selected.Counts.Values,Is.All.EqualTo(1));Assert.That(material.Wave.PcmSha256,Is.EqualTo(package.CompositeHash(item.ContentId,"P1",1,1)));
            var permit=new DictionaryPermit();var display=loaded.ReadPostStudyDictionaryAtom("K-a1",permit);Assert.That(display.MeaningDisplayId,Is.EqualTo("DEMO-display-K-a1"));Assert.That(permit.Package,Is.EqualTo(package.PackageSha256));Assert.That(permit.Schedule,Is.EqualTo(Hash));Assert.That(permit.Atom,Is.EqualTo("K-a1"));
            Assert.Throws<SessionFault>(()=>loaded.ReadPostStudyDictionaryAtom("K-a1",permit));Assert.Throws<SessionFault>(()=>loaded.ReadPostStudyDictionaryAtom("K-a1-r2",new DictionaryPermit()));Assert.Throws<SessionFault>(()=>loaded.ReadPostStudyDictionaryAtom("K-a1",null));
        }
    }
}
