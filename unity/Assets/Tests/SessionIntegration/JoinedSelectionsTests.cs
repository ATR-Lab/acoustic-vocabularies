using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.SessionEngine;
using AcousticVocab.SelectionMenus;
using AcousticVocab.StudyAudio;
using AcousticVocab.Teaching;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.SessionIntegration.Tests
{
    public sealed class JoinedSelectionsTests
    {
        LoadedAudioPackage a,b;byte[] permutationA,permutationB,manifestB;string fixtures;
        sealed class Selections:ITeachingSelections
        {
            public string PackageSha256{get;set;}public bool OldHashesVerified{get;set;}=true;
            public readonly Dictionary<string,TeachingSelection> Values=new Dictionary<string,TeachingSelection>(StringComparer.Ordinal);
            public TeachingSelection Get(string id)=>Values.TryGetValue(id,out var value)?value:throw new SessionFault("JOIN_SELECTION_MISSING");
        }
        [OneTimeSetUp]public void Setup()
        {
            string root=Environment.GetEnvironmentVariable("AV_PACKAGE_DEMO_ROOT");if(root==null||!Directory.Exists(root))Assert.Ignore("Provision actual synthetic producer packages");
            LoadedAudioPackage Load(string name){string path=Path.Combine(root,name);var manifest=JObject.Parse(File.ReadAllText(Path.Combine(path,"manifest.json")));return PackageLoader.Load(path,(string)manifest["package_sha256"],true);}
            a=Load("package-demo");b=Load("dyad-demo");permutationA=File.ReadAllBytes(Path.Combine(root,"package-demo","permutation.json"));permutationB=File.ReadAllBytes(Path.Combine(root,"dyad-demo","permutation.json"));manifestB=File.ReadAllBytes(Path.Combine(root,"dyad-demo","manifest.json"));fixtures=Environment.GetEnvironmentVariable("AV_MENU_STORE_INTEROP");
        }
        Selections Store(){var store=new Selections{PackageSha256=b.PackageSha256};foreach(string id in b.AtomIds)store.Values.Add(id,new TeachingSelection("P2",id=="K-a1"?2:id=="K-r1"?3:1));return store;}
        [Test]public void FixedStudyAPackageUsesNullZeroSentinelsForAllActualIds()
        {
            var choices=new JoinedSelections(a,permutationA);Assert.That(choices.OldHashesVerified,Is.True);
            foreach(string id in a.AtomIds){var atom=choices.Get(id);Assert.That(atom.Profile,Is.Null);Assert.That(atom.Rank,Is.Zero);}
            foreach(var row in JObject.Parse(Encoding.UTF8.GetString(permutationA))["messages"]){var choice=choices.For((string)row["message_id"]);Assert.That(choice.Profile,Is.Null);Assert.That(choice.ActionRank,Is.Zero);Assert.That(choice.ReferentRank,Is.Zero);}
            Assert.Throws<SessionFault>(()=>choices.For("K-a1_K-r1"));Assert.Throws<SessionFault>(()=>choices.Get("K-a1-r1"));
        }
        [Test]public void ActualHyphenatedMessageIdsResolveExactActionAndReferentRanks()
        {
            var store=Store();var choices=new JoinedSelections(b,permutationB,store);var result=choices.For("K-a1-r1");Assert.That(result.Profile,Is.EqualTo("P2"));Assert.That(result.ActionRank,Is.EqualTo(2));Assert.That(result.ReferentRank,Is.EqualTo(3));
            Assert.That(choices.For("K-a1").ReferentRank,Is.Zero);Assert.That(choices.For("K-r1").ActionRank,Is.Zero);
            foreach(var row in JObject.Parse(Encoding.UTF8.GetString(permutationB))["messages"])Assert.DoesNotThrow(()=>choices.For((string)row["message_id"]));
            // Adapter retains the same verified store across fresh module views.
            var later=new JoinedSelections(b,permutationB,store);Assert.That(later.Get("K-a1").Rank,Is.EqualTo(2));store.OldHashesVerified=false;Assert.That(choices.OldHashesVerified,Is.False);Assert.Throws<SessionFault>(()=>later.For("K-a1-r1"));
        }
        [Test]public void MissingRankReserveRankProfileMismatchAndWrongPackageAreRefused()
        {
            var store=Store();var choices=new JoinedSelections(b,permutationB,store);store.Values.Remove("K-r1");Assert.Throws<SessionFault>(()=>choices.For("K-a1-r1"));store.Values["K-r1"]=new TeachingSelection("P2",4);Assert.Throws<SessionFault>(()=>choices.For("K-a1-r1"));store.Values["K-r1"]=new TeachingSelection("P3",1);Assert.Throws<SessionFault>(()=>choices.For("K-a1-r1"));store.PackageSha256=new string('a',64);Assert.That(choices.OldHashesVerified,Is.False);Assert.Throws<SessionFault>(()=>new JoinedSelections(b,permutationB,store));Assert.Throws<SessionFault>(()=>new JoinedSelections(b,permutationB));
        }
        [TestCase("message_id")][TestCase("action_atom")][TestCase("referent_atom")]
        public void MismatchedCanonicalMappingsCannotSelectOtherAtoms(string field)
        {
            var json=JObject.Parse(Encoding.UTF8.GetString(permutationB));var message=json["messages"][0];message[field]=field=="message_id"?"K-a1_K-r1":field=="action_atom"?"Q-a1":"K-r2";
            Assert.Throws<SessionFault>(()=>new JoinedSelections(b,Encoding.UTF8.GetBytes(json.ToString()),Store()));
        }
        [Test]public void WrongStudyOrDuplicateMatrixCellIsRefused()
        {Assert.Throws<SessionFault>(()=>new JoinedSelections(a,permutationB));var json=JObject.Parse(Encoding.UTF8.GetString(permutationB));json["messages"][1]=json["messages"][0].DeepClone();Assert.Throws<SessionFault>(()=>new JoinedSelections(b,Encoding.UTF8.GetBytes(json.ToString()),Store()));}
        [Test]public void ActualPythonConfigHasDistinctRawAndCanonicalPins()
        {
            if(fixtures==null||!Directory.Exists(fixtures))Assert.Ignore("Provision actual producer/store interop fixture");
            byte[] bytes=File.ReadAllBytes(Path.Combine(fixtures,"config.json"));var config=JObject.Parse(Encoding.UTF8.GetString(bytes));var summary=JObject.Parse(File.ReadAllText(Path.Combine(fixtures,"summary.json")));
            // This retained fixture proves codec interoperability only. Its
            // arbitrary original bridge unit is not a joined producer binding.
            string codecUnit=((string)config["unit_id"]).Substring("DEMO-".Length);
            var binding=JoinedSelections.CreateStoreBinding(bytes,PcmWave.Hash(bytes),manifestB,b,codecUnit,(string)config["bank_sha256"]);
            Assert.That(binding.ConfigSha256,Is.EqualTo((string)summary["config_sha256"]));Assert.That(binding.BookId,Is.EqualTo((string)config["book_id"]));Assert.That(binding.PackageSha256,Is.EqualTo(b.PackageSha256));
            Assert.Throws<SessionFault>(()=>JoinedSelections.CreateStoreBinding(bytes,new string('a',64),manifestB,b,(string)config["unit_id"],(string)config["bank_sha256"]));
            Assert.Throws<SessionFault>(()=>JoinedSelections.CreateStoreBinding(bytes,PcmWave.Hash(bytes),manifestB,b,"DEMO-other",(string)config["bank_sha256"]));
            config["bank_sha256"]=new string('a',64);bytes=Encoding.UTF8.GetBytes(config.ToString());Assert.Throws<SessionFault>(()=>JoinedSelections.CreateStoreBinding(bytes,PcmWave.Hash(bytes),manifestB,b,codecUnit,new string('a',64)));
        }
        [Test]public void ProducerUnitRequiresExactSyntheticBridgeNamespace()
        {
            if(fixtures==null||!Directory.Exists(fixtures))Assert.Ignore("Provision actual producer/store interop fixture");
            string producerUnit=(string)JObject.Parse(Encoding.UTF8.GetString(permutationB))["unit_id"];
            Assert.That(producerUnit,Is.EqualTo("B-C01"));Assert.That(JoinedSelections.BridgeUnitId(producerUnit),Is.EqualTo("DEMO-B-C01"));
            byte[] bytes=File.ReadAllBytes(Path.Combine(fixtures,"config.json"));var config=JObject.Parse(Encoding.UTF8.GetString(bytes));
            Assert.Throws<SessionFault>(()=>JoinedSelections.CreateStoreBinding(bytes,PcmWave.Hash(bytes),manifestB,b,producerUnit,(string)config["bank_sha256"]));
            config["unit_id"]=JoinedSelections.BridgeUnitId(producerUnit);bytes=Encoding.UTF8.GetBytes(config.ToString());
            var binding=JoinedSelections.CreateStoreBinding(bytes,PcmWave.Hash(bytes),manifestB,b,producerUnit,(string)config["bank_sha256"]);
            Assert.That(binding.UnitId,Is.EqualTo("DEMO-B-C01"));
            Assert.Throws<SessionFault>(()=>JoinedSelections.CreateStoreBinding(bytes,PcmWave.Hash(bytes),manifestB,b,"B-C02",(string)config["bank_sha256"]));
            Assert.Throws<SessionFault>(()=>JoinedSelections.BridgeUnitId("B-C01\n"));
            Assert.Throws<SessionFault>(()=>JoinedSelections.BridgeUnitId("B_C01"));
        }
        [Test]public void SharedUnitBindingMatchesCanonicalPythonAndRejectsChangedIdentity()
        {
            string package=new string('a',64),permutation=new string('b',64),bank=new string('c',64);
            string hash=JoinedSelections.SharedUnitBindingSha256(package,permutation,bank,"DEMO-UNIT");Assert.That(hash,Is.EqualTo("c6c7692eedbcddc7fa39cf7432b2ef87e13897364465aec24eb793c8043e03a1"));
            Assert.That(JoinedSelections.SharedUnitBindingSha256(package,permutation,bank,"DEMO-other"),Is.Not.EqualTo(hash));Assert.That(JoinedSelections.SharedUnitBindingSha256(package,new string('d',64),bank,"DEMO-UNIT"),Is.Not.EqualTo(hash));
            Assert.Throws<SessionFault>(()=>JoinedSelections.SharedUnitBindingSha256(package,permutation,bank,"DEMO-UNIT\n"));Assert.Throws<SessionFault>(()=>JoinedSelections.SharedUnitBindingSha256(null,permutation,bank,"DEMO-UNIT"));
        }
        // The snapshots and WAV hashes were produced by the real #11 bridge
        // using the actual existing #13 DEMO package. This test simulates only
        // the correlated verification response; it makes no live-service claim.
        [TestCase(1,8)][TestCase(2,12)][TestCase(3,16)]
        public void ActualCompletedWaveSnapshotFeedsReadonlyChoicesAcrossModuleViews(int wave,int count)
        {
            string fixture=Environment.GetEnvironmentVariable("AV_JOINED_READONLY_STORE");
            if(fixture==null||!Directory.Exists(fixture))Assert.Ignore("Provision actual completed DEMO store snapshots");
            var summary=JObject.Parse(File.ReadAllText(Path.Combine(fixture,"summary.json")));
            foreach(var file in ((JObject)summary["files"]).Properties())Assert.That(PcmWave.Hash(File.ReadAllBytes(Path.Combine(fixture,file.Name))),Is.EqualTo((string)file.Value));
            var configBytes=File.ReadAllBytes(Path.Combine(fixture,"config.json"));var config=JObject.Parse(Encoding.UTF8.GetString(configBytes));
            string unit=(string)JObject.Parse(Encoding.UTF8.GetString(permutationB))["unit_id"];
            var binding=JoinedSelections.CreateStoreBinding(configBytes,PcmWave.Hash(configBytes),manifestB,b,unit,(string)config["bank_sha256"]);
            Assert.That(binding.ConfigSha256,Is.EqualTo((string)summary["config_sha256"]));
            byte[] snapshotBytes=File.ReadAllBytes(Path.Combine(fixture,"snapshot-"+count+".json"));var snapshot=JObject.Parse(Encoding.UTF8.GetString(snapshotBytes));
            string[] allowed=b.AtomIds.Where(x=>wave==1?x.EndsWith("1")||x.EndsWith("2"):x.EndsWith((wave+1).ToString())).ToArray();
            string mailbox=Path.Combine(Path.GetTempPath(),"av-joined-readonly-"+Guid.NewGuid().ToString("N"));
            try
            {
                using var store=new FileMenuStore(mailbox,binding,b,snapshotBytes,(string)snapshot["manifest_sha256"],(string)snapshot["book_head"],(string)snapshot["snapshot_sha256"],allowed,_=>{},()=>1000,true);
                var firstLease=new JoinedSelections(b,permutationB,store);
                Assert.That(store.Ready,Is.False);Assert.That(firstLease.OldHashesVerified,Is.False);Assert.Throws<SessionFault>(()=>firstLease.Get("K-a1"));
                var request=JObject.Parse(File.ReadAllText(Directory.GetFiles(Path.Combine(mailbox,"requests"),"*.json").Single()));
                Assert.That((string)request["operation"],Is.EqualTo("verify"));
                var response=new JObject{["schema_version"]=1,["request_id"]=request["request_id"],["request_sha256"]=PcmWave.Hash(CanonicalBytes(request)),["config_sha256"]=binding.ConfigSha256,["receipt"]=JValue.CreateNull(),["snapshot"]=snapshot.DeepClone(),["error"]=JValue.CreateNull()};
                response["response_sha256"]=PcmWave.Hash(CanonicalBytes(response));
                File.WriteAllBytes(Path.Combine(mailbox,"responses",(string)request["request_id"]+".json"),CanonicalBytes(response));store.Pump();
                Assert.That(store.Ready,Is.True);Assert.That(snapshot["entries"].Count(),Is.EqualTo(count));
                var laterLease=new JoinedSelections(b,permutationB,store);
                foreach(var entry in snapshot["entries"])
                {
                    string id=(string)entry["atom_id"];var first=firstLease.Get(id);var later=laterLease.Get(id);
                    Assert.That(first.Profile,Is.EqualTo((string)entry["profile"]));Assert.That(first.Rank,Is.EqualTo((int)entry["rank"]));
                    Assert.That(later.Profile,Is.EqualTo(first.Profile));Assert.That(later.Rank,Is.EqualTo(first.Rank));
                }
                foreach(string missing in b.AtomIds.Except(snapshot["entries"].Select(x=>(string)x["atom_id"])))Assert.Throws<SessionFault>(()=>laterLease.Get(missing));
                var message=laterLease.For("K-a1-r1");Assert.That(message.Profile,Is.EqualTo("P1"));Assert.That(message.ActionRank,Is.EqualTo(1));Assert.That(message.ReferentRank,Is.EqualTo(1));
                if(wave==3)foreach(var row in JObject.Parse(Encoding.UTF8.GetString(permutationB))["messages"])Assert.DoesNotThrow(()=>laterLease.For((string)row["message_id"]));
                Assert.Throws<SessionFault>(()=>store.RequestSelection(allowed[0],"P1",1));
                // A new end-wave read revokes readiness until its exact response.
                store.RequestVerification(true);Assert.That(laterLease.OldHashesVerified,Is.False);Assert.Throws<SessionFault>(()=>laterLease.For("K-a1-r1"));
            }
            finally{if(Directory.Exists(mailbox))Directory.Delete(mailbox,true);}
        }
        static byte[] CanonicalBytes(JToken value)
        {
            JToken Ordered(JToken token)=>token is JObject obj?new JObject(obj.Properties().OrderBy(x=>x.Name,StringComparer.Ordinal).Select(x=>new JProperty(x.Name,Ordered(x.Value)))):token is JArray array?new JArray(array.Select(Ordered)):token.DeepClone();
            return new UTF8Encoding(false,true).GetBytes(Ordered(value).ToString(Formatting.None));
        }
    }
}
