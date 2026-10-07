using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using AcousticVocab.Teaching;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
namespace AcousticVocab.SelectionMenus.Tests
{
    public sealed class MenuCatalogTests
    {
        const string Hash="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        string temp,repo,packageRoot,scriptRoot,teachingRoot,scriptHash,reviewHash,allocationHash,registryHash,bankHash,examples;
        byte[] manifestBytes,permutationBytes,allocationBytes,registryBytes;LoadedAudioPackage package;VisitSchedule schedule;TeachingCatalog teaching;
        static void Write(string path,JObject value)=>File.WriteAllText(path,value.ToString()+"\n",new UTF8Encoding(false));
        [SetUp]public void Setup()
        {
            var dir=new DirectoryInfo(Directory.GetCurrentDirectory());while(dir!=null&&!Directory.Exists(Path.Combine(dir.FullName,"sound","reserved")))dir=dir.Parent;Assert.That(dir,Is.Not.Null);repo=dir.FullName;
            string root=Environment.GetEnvironmentVariable("AV_PACKAGE_DEMO_ROOT")??Path.Combine(repo,".local","producer-package-demo");examples=Environment.GetEnvironmentVariable("AV_GRAMMAR_ASSETS")??Path.Combine(repo,".local","grammar-assets");
            if(!Directory.Exists(root)||!File.Exists(Path.Combine(examples,"calibration-P1.wav")))Assert.Ignore("Provision actual #13 DEMO package and #14 calibration files; no participant fallback");
            packageRoot=Path.Combine(root,"dyad-demo");manifestBytes=File.ReadAllBytes(Path.Combine(packageRoot,"manifest.json"));var manifest=JObject.Parse(Encoding.UTF8.GetString(manifestBytes));package=PackageLoader.Load(packageRoot,(string)manifest["package_sha256"],true);bankHash=(string)manifest["bank"]["bank_sha256"];
            permutationBytes=File.ReadAllBytes(Path.Combine(packageRoot,"permutation.json"));var permutation=JObject.Parse(Encoding.UTF8.GetString(permutationBytes));allocationBytes=File.ReadAllBytes(Path.Combine(repo,"schedules","examples","demo-allocation","B","confirmatory-dyads.json"));allocationHash=PcmWave.Hash(allocationBytes);
            var items=new[]{new SlotItem("DEMO-profile","profile_menu",null,null,"selection","selection",false,60,8,1)}.Concat(permutation["wave_atom_order"]["1"].Select((x,i)=>new SlotItem("DEMO-atom-"+i,"atom_menu",(string)x,null,"selection","selection",false,45,8,1))).ToArray();schedule=new VisitSchedule(Hash,package.PackageSha256,"B-C01-M1","V1",true,new[]{new ScheduleBlock("menus",items)});
            temp=Path.Combine(Path.GetTempPath(),"av-menu-catalog-"+Guid.NewGuid().ToString("N"));scriptRoot=Path.Combine(temp,"menu");teachingRoot=Path.Combine(temp,"teaching");Directory.CreateDirectory(scriptRoot);Directory.CreateDirectory(Path.Combine(teachingRoot,"images"));
            byte[] png=Convert.FromBase64String("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a7V8AAAAASUVORK5CYII=");File.WriteAllBytes(Path.Combine(teachingRoot,"images","fixture.png"),png);
            var ids=permutation["atoms"].Select(x=>(string)x["atom_id"]).Concat(permutation["messages"].Where(x=>(string)x["status"]=="trained").Select(x=>(string)x["message_id"]));var catalog=new JObject{["format"]="av-teaching/1",["package_sha256"]=package.PackageSha256,["content"]=new JArray(ids.Select(id=>new JObject{["content_id"]=id,["meaning_display_id"]="DEMO-display-"+id,["definition"]="Synthetic definition",["action_words"]="Synthetic action",["target_words"]="Synthetic target",["image_id"]="fixture"})),["images"]=new JObject{["fixture"]=new JObject{["file"]="images/fixture.png",["sha256"]=PcmWave.Hash(png)}},["feedback"]=new JObject()};foreach(string kind in new[]{"atomic","correct","incorrect","timeout"})catalog["feedback"][kind]=new JObject{["id"]="DEMO-"+kind,["text"]="Synthetic feedback"};
            Write(Path.Combine(teachingRoot,"catalog.local.json"),catalog);string teachingHash=PcmWave.Hash(File.ReadAllBytes(Path.Combine(teachingRoot,"catalog.local.json")));Write(Path.Combine(teachingRoot,"review.local.json"),new JObject{["version"]=1,["approved"]=true,["catalog_sha256"]=teachingHash,["methodology_sha256"]=Hash});string teachingReview=PcmWave.Hash(File.ReadAllBytes(Path.Combine(teachingRoot,"review.local.json")));teaching=TeachingCatalog.Load(teachingRoot,teachingHash,teachingReview,package,schedule,manifestBytes,permutationBytes,allocationBytes,allocationHash);
            Write(Path.Combine(scriptRoot,"menu-script.local.json"),new JObject{["format"]="av-menu-script/1",["package_sha256"]=package.PackageSha256,["profile_display_id"]="DEMO-profile-display",["profile_names"]=new JObject{["P1"]="DEMO profile 1",["P2"]="DEMO profile 2",["P3"]="DEMO profile 3"},["profile_instructions"]="Synthetic profile instructions",["atom_instructions"]="Synthetic atom instructions",["active_choice_instructions"]="Synthetic choose wording",["yoked_choice_instructions"]="Synthetic assigned wording",["candidate_labels"]=new JArray("DEMO option 1","DEMO option 2","DEMO option 3")});scriptHash=PcmWave.Hash(File.ReadAllBytes(Path.Combine(scriptRoot,"menu-script.local.json")));Write(Path.Combine(scriptRoot,"review.local.json"),new JObject{["version"]=1,["approved"]=true,["script_sha256"]=scriptHash,["teaching_review_sha256"]=teachingReview,["methodology_sha256"]=Hash});reviewHash=PcmWave.Hash(File.ReadAllBytes(Path.Combine(scriptRoot,"review.local.json")));
            registryBytes=File.ReadAllBytes(Path.Combine(repo,"sound","reserved","registry.json"));registryHash=PcmWave.Hash(registryBytes);
        }
        MenuCatalog Load(bool engineering=true)=>MenuCatalog.Load(scriptRoot,scriptHash,reviewHash,package,schedule,teaching,manifestBytes,permutationBytes,allocationBytes,allocationHash,bankHash,examples,registryBytes,registryHash,engineering);
        [TearDown]public void Cleanup(){if(temp!=null&&Directory.Exists(temp))Directory.Delete(temp,true);}
        [Test]public void ActualProducerOptionsAndCalibrationFollowStoredOrderWithoutReserve()
        {var catalog=Load();Assert.That(catalog.ParticipantBankQualified,Is.False);var allocation=JObject.Parse(Encoding.UTF8.GetString(allocationBytes));var dyad=allocation["dyads"].Single(x=>x["members"].Any(m=>(string)m["slot_id"]==schedule.PersonSlot));Assert.That(Enumerable.Range(1,3).Select(catalog.ProfileAt),Is.EqualTo(dyad["profile_menu_order"].Select(x=>(string)x)));foreach(var item in schedule.Blocks[0].Items){var material=catalog.Prepare(item,"P1");Assert.That(material.Options.Count,Is.EqualTo(3));if(item.TrialType=="atom_menu"){Assert.That(material.MeaningDisplayId,Is.EqualTo("DEMO-display-"+item.ContentId));Assert.That(material.Options.Select(x=>x.Wave.PcmSha256),Is.EqualTo(Enumerable.Range(1,3).Select(x=>package.ReadAtom(item.ContentId,"P1",x).PcmSha256)));}else Assert.That(material.Options.Select(x=>x.Wave.SampleCount),Is.All.EqualTo(96000));}}
        [Test]public void ProvisionalBankNeverGrantsParticipantAdmission()=>Assert.That(Assert.Throws<SessionFault>(()=>Load(false)).Code,Is.EqualTo("MENU_BANK_HANDOFF_PENDING"));
        [Test]public void WrongBankOrAllocationOrReviewPinRefusesLoad()
        {string old=bankHash;bankHash=Hash;Assert.Throws<SessionFault>(()=>Load());bankHash=old;allocationHash=Hash;Assert.Throws<SessionFault>(()=>Load());allocationHash=PcmWave.Hash(allocationBytes);reviewHash=Hash;Assert.Throws<SessionFault>(()=>Load());}
        [Test]public void WrongMenuOrderRefusesBeforeAnyPresentation()
        {var items=schedule.Blocks[0].Items.Reverse().ToArray();schedule=new VisitSchedule(Hash,package.PackageSha256,schedule.PersonSlot,"V1",true,new[]{new ScheduleBlock("menus",items)});Assert.Throws<SessionFault>(()=>Load());}
        [Test]public void MissingProfileOrMeaningOutsideCurrentWaveRefused()
        {var catalog=Load();Assert.Throws<SessionFault>(()=>catalog.Prepare(schedule.Blocks[0].Items[1],null));var unknown=package.AtomIds.Except(catalog.MenuKeys).First();var item=new SlotItem("DEMO-extra","atom_menu",unknown,null,"selection","selection",false,45,8,1);Assert.Throws<SessionFault>(()=>catalog.Prepare(item,"P1"));}
    }
}
