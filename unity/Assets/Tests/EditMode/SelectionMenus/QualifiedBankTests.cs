using System;
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
    // Self-contained: Python canonical-JSON vector (json.dumps(sort_keys=True,
    // separators=(",",":"), ensure_ascii=True)) and the float boundary.
    public sealed class QualifiedBankCanonicalTests
    {
        [Test]public void CanonicalHashMatchesPythonVector()
        {
            var value=JObject.Parse("{\"b\":[1,0.6,1.0,null,true,-6],\"a\":{\"z\":\"options/P1/K-a1-1.wav\",\"k\":0.8},\"n\":null,\"e\":[]}");
            Assert.That(QualifiedBank.CanonicalSha256(value),Is.EqualTo("415739c33724506e68210f729e4c97eee45835248d872eae79a4c0065617b9f5"));
        }
        [Test]public void CanonicalHashRefusesFloatsAndTextItCannotReproduce()
        {
            foreach(string raw in new[]{"{\"v\":0.7}","{\"v\":1e-7}","{\"v\":\"quote\\\"d\"}","{\"v\":\"caf\\u00e9\"}"})
                Assert.That(Assert.Throws<SessionFault>(()=>QualifiedBank.CanonicalSha256(JObject.Parse(raw))).Code,Is.EqualTo(QualifiedBank.Fault));
        }
    }

    // Seeded synthetic `banks build` fixture from tools/qualified_bank_fixture.py
    // (DEMO bank from the real builder with the scripted rehearsal proposer, its
    // qualified #13 package and the fixed #14 calibration examples). Ignored when
    // absent: a skipped fixture test is not a pass.
    public sealed class QualifiedBankTests
    {
        const string Hash="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        string repo,temp,packageRoot,examples,bankHash,allocationHash,registryHash;JObject fixture;
        byte[] bankBytes,manifestBytes,permutationBytes,allocationBytes,registryBytes;LoadedAudioPackage package;
        static void Write(string path,JObject value)=>File.WriteAllText(path,value.ToString()+"\n",new UTF8Encoding(false));
        static void Copy(string from,string to){Directory.CreateDirectory(to);foreach(string file in Directory.GetFiles(from))File.Copy(file,Path.Combine(to,Path.GetFileName(file)));foreach(string dir in Directory.GetDirectories(from))Copy(dir,Path.Combine(to,Path.GetFileName(dir)));}
        [SetUp]public void Setup()
        {
            var dir=new DirectoryInfo(Directory.GetCurrentDirectory());while(dir!=null&&!Directory.Exists(Path.Combine(dir.FullName,"sound","reserved")))dir=dir.Parent;Assert.That(dir,Is.Not.Null);repo=dir.FullName;
            string root=Environment.GetEnvironmentVariable("AV_QUALIFIED_BANK_ROOT")??Path.Combine(repo,".local","qualified-bank-demo");
            if(!File.Exists(Path.Combine(root,"fixture.json")))Assert.Ignore("Provision tools/qualified_bank_fixture.py output (synthetic DEMO bank); no participant fallback");
            fixture=JObject.Parse(File.ReadAllText(Path.Combine(root,"fixture.json")));Assert.That((bool)fixture["synthetic"]&&(bool)fixture["demo"]&&!(bool)fixture["participant_ready"],Is.True);
            temp=Path.Combine(Path.GetTempPath(),"av-qualified-bank-"+Guid.NewGuid().ToString("N"));packageRoot=Path.Combine(temp,"package");Copy(Path.Combine(root,(string)fixture["package_dir"]),packageRoot);
            examples=Path.Combine(root,(string)fixture["examples_dir"]);bankBytes=File.ReadAllBytes(Path.Combine(root,(string)fixture["bank_dir"],"manifest.json"));
            Assert.That(PcmWave.Hash(bankBytes),Is.EqualTo((string)fixture["bank_manifest_file_sha256"]));bankHash=(string)fixture["bank_sha256"];
            manifestBytes=File.ReadAllBytes(Path.Combine(packageRoot,"manifest.json"));permutationBytes=File.ReadAllBytes(Path.Combine(packageRoot,"permutation.json"));
            package=PackageLoader.Load(packageRoot,(string)fixture["package_sha256"],true);
            allocationBytes=File.ReadAllBytes(Path.Combine(repo,"schedules","examples","demo-allocation","B","confirmatory-dyads.json"));allocationHash=PcmWave.Hash(allocationBytes);
            registryBytes=File.ReadAllBytes(Path.Combine(repo,"sound","reserved","registry.json"));registryHash=PcmWave.Hash(registryBytes);
        }
        [TearDown]public void Cleanup(){if(temp!=null&&Directory.Exists(temp))Directory.Delete(temp,true);}
        QualifiedBank Verify(byte[] bank=null,string pin=null,LoadedAudioPackage loaded=null,byte[] manifest=null,byte[] permutation=null)=>QualifiedBank.Verify(bank??bankBytes,pin??bankHash,loaded??package,manifest??manifestBytes,permutation??permutationBytes);
        static void Mismatch(TestDelegate action)=>Assert.That(Assert.Throws<SessionFault>(action).Code,Is.EqualTo(QualifiedBank.Fault));
        static byte[] Bytes(JObject value)=>new UTF8Encoding(false).GetBytes(value.ToString()+"\n");
        JObject Bank()=>JObject.Parse(Encoding.UTF8.GetString(bankBytes));
        // Re-seal the package manifest so it records `bank` (a tampered bank with a
        // consistent package/pin): only the per-option comparison can catch it.
        LoadedAudioPackage Reseal(Action<JObject> edit)
        {
            var manifest=JObject.Parse(Encoding.UTF8.GetString(manifestBytes));edit(manifest);manifest.Remove("package_sha256");
            manifest["package_sha256"]=PackageLoader.CanonicalPackageHash(manifest.ToString());Write(Path.Combine(packageRoot,"manifest.json"),manifest);
            manifestBytes=File.ReadAllBytes(Path.Combine(packageRoot,"manifest.json"));return PackageLoader.Load(packageRoot,(string)manifest["package_sha256"],true);
        }

        [Test]public void SeededBanksBuildFixtureVerifiesEveryOptionAgainstThePackage()
        {
            Assert.That(package.BankFormat,Is.EqualTo(QualifiedBank.Format));Assert.That(package.BankSha256,Is.EqualTo(bankHash));
            var bank=Verify();Assert.That(bank.OptionsVerified,Is.EqualTo(192));Assert.That(bank.BankSha256,Is.EqualTo(bankHash));
            Assert.That(bank.BankId,Is.EqualTo((string)fixture["bank_id"]));Assert.That(bank.Demo,Is.True);Assert.That(bank.Set,Is.EqualTo("demo"));Assert.That(bank.DyadSlot,Is.EqualTo("B-C01"));
            Assert.That(QualifiedBank.CanonicalSha256(Bank()),Is.EqualTo(bankHash));
        }
        [Test]public void ChangedBankManifestOrWrongPinFailsBeforeAnyMenu()
        {
            var bank=Bank();var option=bank["cells"][5]["options"][1];string pcm=(string)option["pcm_sha256"];option["pcm_sha256"]=(pcm[0]=='0'?"1":"0")+pcm.Substring(1);
            Mismatch(()=>Verify(Bytes(bank)));
            Mismatch(()=>Verify(pin:Hash));
            Mismatch(()=>Verify(Encoding.UTF8.GetBytes(Encoding.UTF8.GetString(bankBytes).Replace("\"status\": \"complete\"","\"status\": \"unavailable\""))));
            // A re-pinned tampered bank still differs from the bank the package records.
            Mismatch(()=>Verify(Bytes(bank),QualifiedBank.CanonicalSha256(bank)));
        }
        [Test]public void ResealedPackageWithSwappedBankOptionsFailsPerOptionCheck()
        {
            var bank=Bank();var options=(JArray)bank["cells"][17]["options"];
            foreach(string field in new[]{"pcm_sha256","file_sha256"}){var first=options[0][field];options[0][field]=options[1][field];options[1][field]=first;}
            string tampered=QualifiedBank.CanonicalSha256(bank);var resealed=Reseal(m=>m["bank"]["bank_sha256"]=tampered);
            Mismatch(()=>Verify(Bytes(bank),tampered,resealed));
        }
        [Test]public void ChangedPackageWaveAfterLoadOrChangedPermutationFails()
        {
            Mismatch(()=>Verify(permutation:Encoding.UTF8.GetBytes(Encoding.UTF8.GetString(permutationBytes)+" ")));
            string atom=(string)Bank()["atom_order"][3];string path=Path.Combine(packageRoot,"options","P2",atom+"-2.wav");byte[] bytes=File.ReadAllBytes(path);bytes[bytes.Length-1]^=1;File.WriteAllBytes(path,bytes);
            Mismatch(()=>Verify());
        }
        [Test]public void ProvisionalRelabelOfTheSamePackageIsNeverQualified()
        {
            var relabeled=Reseal(m=>m["bank"]["format"]=QualifiedBank.ProvisionalFormat);
            Assert.That(relabeled.BankFormat,Is.EqualTo(QualifiedBank.ProvisionalFormat));Mismatch(()=>Verify(loaded:relabeled));
        }
        [Test]public void OptionRecheckAcceptsOnlyTheFrozenShownCandidate()
        {
            var bank=Verify();string atom=(string)Bank()["atom_order"][0];
            Assert.DoesNotThrow(()=>bank.CheckOption("P1",atom,1,package.ReadAtom(atom,"P1",1)));
            Mismatch(()=>bank.CheckOption("P1",atom,1,package.ReadAtom(atom,"P1",2)));
            Mismatch(()=>bank.CheckOption("P1",atom,4,package.ReadAtom(atom,"P1",4)));
            Mismatch(()=>bank.CheckOption("P2",atom,1,package.ReadAtom(atom,"P1",1)));
        }

        // MenuCatalog boundary for both dyad members (B-C01-M2 active, M1 yoked).
        (string scripts,string script,string review,VisitSchedule schedule,TeachingCatalog teaching) Assets(string slot)
        {
            var permutation=JObject.Parse(Encoding.UTF8.GetString(permutationBytes));
            var items=new[]{new SlotItem("DEMO-profile","profile_menu",null,null,"selection","selection",false,60,8,1)}.Concat(permutation["wave_atom_order"]["1"].Select((x,i)=>new SlotItem("DEMO-atom-"+i,"atom_menu",(string)x,null,"selection","selection",false,45,8,1))).ToArray();
            var schedule=new VisitSchedule(Hash,package.PackageSha256,slot,"V1",true,new[]{new ScheduleBlock("menus",items)});
            string root=Path.Combine(temp,slot),scripts=Path.Combine(root,"menu"),teachingRoot=Path.Combine(root,"teaching");Directory.CreateDirectory(scripts);Directory.CreateDirectory(Path.Combine(teachingRoot,"images"));
            byte[] png=Convert.FromBase64String("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a7V8AAAAASUVORK5CYII=");File.WriteAllBytes(Path.Combine(teachingRoot,"images","fixture.png"),png);
            var ids=permutation["atoms"].Select(x=>(string)x["atom_id"]).Concat(permutation["messages"].Where(x=>(string)x["status"]=="trained").Select(x=>(string)x["message_id"]));
            var catalog=new JObject{["format"]="av-teaching/1",["package_sha256"]=package.PackageSha256,["content"]=new JArray(ids.Select(id=>new JObject{["content_id"]=id,["meaning_display_id"]="DEMO-display-"+id,["definition"]="Synthetic definition",["action_words"]="Synthetic action",["target_words"]="Synthetic target",["image_id"]="fixture"})),["images"]=new JObject{["fixture"]=new JObject{["file"]="images/fixture.png",["sha256"]=PcmWave.Hash(png)}},["feedback"]=new JObject()};
            foreach(string kind in new[]{"atomic","correct","incorrect","timeout"})catalog["feedback"][kind]=new JObject{["id"]="DEMO-"+kind,["text"]="Synthetic feedback"};
            Write(Path.Combine(teachingRoot,"catalog.local.json"),catalog);string teachingHash=PcmWave.Hash(File.ReadAllBytes(Path.Combine(teachingRoot,"catalog.local.json")));
            Write(Path.Combine(teachingRoot,"review.local.json"),new JObject{["version"]=1,["approved"]=true,["catalog_sha256"]=teachingHash,["methodology_sha256"]=Hash});string teachingReview=PcmWave.Hash(File.ReadAllBytes(Path.Combine(teachingRoot,"review.local.json")));
            var teaching=TeachingCatalog.Load(teachingRoot,teachingHash,teachingReview,package,schedule,manifestBytes,permutationBytes,allocationBytes,allocationHash);
            Write(Path.Combine(scripts,"menu-script.local.json"),new JObject{["format"]="av-menu-script/1",["package_sha256"]=package.PackageSha256,["profile_display_id"]="DEMO-profile-display",["profile_names"]=new JObject{["P1"]="DEMO profile 1",["P2"]="DEMO profile 2",["P3"]="DEMO profile 3"},["profile_instructions"]="Synthetic profile instructions",["atom_instructions"]="Synthetic atom instructions",["active_choice_instructions"]="Synthetic choose wording",["yoked_choice_instructions"]="Synthetic assigned wording",["candidate_labels"]=new JArray("DEMO option 1","DEMO option 2","DEMO option 3")});
            string scriptHash=PcmWave.Hash(File.ReadAllBytes(Path.Combine(scripts,"menu-script.local.json")));
            Write(Path.Combine(scripts,"review.local.json"),new JObject{["version"]=1,["approved"]=true,["script_sha256"]=scriptHash,["teaching_review_sha256"]=teachingReview,["methodology_sha256"]=Hash});
            return (scripts,scriptHash,PcmWave.Hash(File.ReadAllBytes(Path.Combine(scripts,"review.local.json"))),schedule,teaching);
        }
        MenuCatalog Catalog(string slot,byte[] bank,bool engineering=true)
        {var a=Assets(slot);return MenuCatalog.Load(a.scripts,a.script,a.review,package,a.schedule,a.teaching,manifestBytes,permutationBytes,allocationBytes,allocationHash,bankHash,examples,registryBytes,registryHash,engineering,null,bank);}
        [Test]public void QualifiedCatalogVerifiesBankAndRechecksEachAtomMenuForBothRoles()
        {
            foreach(var (slot,role) in new[]{("B-C01-M2","active"),("B-C01-M1","yoked")})
            {
                var catalog=Catalog(slot,bankBytes);Assert.That(catalog.Role,Is.EqualTo(role));
                Assert.That(catalog.BankQualified,Is.True);Assert.That(catalog.BankFormat,Is.EqualTo(QualifiedBank.Format));Assert.That(catalog.ParticipantBankQualified,Is.False,"DEMO bank");
            }
            var menu=Catalog("B-C01-M2",bankBytes);var atoms=JObject.Parse(Encoding.UTF8.GetString(permutationBytes))["wave_atom_order"]["1"].Select(x=>(string)x).ToArray();
            for(int i=0;i<atoms.Length;i++)
            {
                var material=menu.Prepare(new SlotItem("DEMO-atom-"+i,"atom_menu",atoms[i],null,"selection","selection",false,45,8,1),"P2");
                Assert.That(material.Options.Select(x=>x.Wave.PcmSha256),Is.EqualTo(Enumerable.Range(1,3).Select(r=>package.ReadAtom(atoms[i],"P2",r).PcmSha256)));
            }
        }
        [Test]public void MissingOrTamperedBankManifestBlocksActiveAndYokedCatalogs()
        {
            var bank=Bank();bank["labels"][(string)bank["atom_order"][0]]="TAMPERED";
            foreach(string slot in new[]{"B-C01-M2","B-C01-M1"})
            {
                Assert.That(Assert.Throws<SessionFault>(()=>Catalog(slot,null)).Code,Is.EqualTo(QualifiedBank.Fault));
                Assert.That(Assert.Throws<SessionFault>(()=>Catalog(slot,Bytes(bank))).Code,Is.EqualTo(QualifiedBank.Fault));
            }
        }
        [Test]public void DemoQualifiedBankRemainsEngineeringOnly()
            =>Assert.That(Assert.Throws<SessionFault>(()=>Catalog("B-C01-M2",bankBytes,false)).Code,Is.EqualTo("MENU_DEMO_BANK_ENGINEERING_ONLY"));
    }
}
