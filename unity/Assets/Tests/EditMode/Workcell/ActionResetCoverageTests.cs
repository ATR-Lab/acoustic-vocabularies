using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using AcousticVocab.Foundation.Editor;
using AcousticVocab.Workcell.Editor;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace AcousticVocab.Workcell.Tests
{
    // #61 AC4: for each of the eight actions, every object that its Isaac precondition reads
    // (isaac/workcell/layout.py preconditions; docs/isaac/workcell/preconditions.json) is present in
    // the Unity workcell after reset, visible and enabled, in the initial state that keeps it possible.
    public class ActionResetCoverageTests
    {
        sealed class Row
        {
            public readonly string Action, Family; public readonly string[] Objects, WasherLocations;
            public Row(string action,string family,string[] objects,params string[] washerLocations){Action=action;Family=family;Objects=objects;WasherLocations=washerLocations;}
        }
        // {T} is the target id and {L} its letter. Washer locations are counted, not named.
        static readonly Row[] Table =
        {
            new Row("ADD_ONE","tray",new[]{"{T}","supply_cup"},"supply_cup","{T}"),
            new Row("REMOVE_ONE","tray",new[]{"{T}","return_cup"},"{T}","return_cup"),
            new Row("FLIP_CARD","tray",new[]{"{T}","{T}/card"}),
            new Row("ALIGN_ARROW","tray",new[]{"{T}","{T}/arrow"}),
            new Row("SCAN","container",new[]{"{T}","{T}/code"}),
            new Row("TAG","container",new[]{"{T}","{T}/tag"}),
            new Row("CLOSE","container",new[]{"{T}","{T}/lid"}),
            new Row("QUARANTINE","container",new[]{"{T}","quarantine_{L}"}),
        };
        static readonly Dictionary<string,string> KindByObject=new()
        {
            ["{T}/card"]="card",["{T}/arrow"]="arrow",["{T}/code"]="code",["{T}/tag"]="tag",["{T}/lid"]="lid",["quarantine_{L}"]="quarantine",["supply_cup"]="cup",["return_cup"]="cup"
        };

        WorkcellRegistry registry;
        JObject layout;
        JArray isaacPairs;
        [OneTimeSetUp] public void BuildImportedScene()
        {
            WorkcellBuild.Configure(); layout=WorkcellBuild.ReadLayout();
            registry=UnityEngine.Object.FindAnyObjectByType<WorkcellRegistry>(FindObjectsInactive.Include);
            var preconditions=JObject.Parse(File.ReadAllText(Path.Combine(FoundationBuild.RepositoryRoot,"docs/isaac/workcell/preconditions.json")));
            isaacPairs=(JArray)preconditions["pairs"];
            Assert.That((int)preconditions["pair_count"],Is.EqualTo(32));Assert.That((int)preconditions["possible_count"],Is.EqualTo(32));
        }
        [SetUp] public void Reset() => registry.ResetToImportedNeutral();

        static string Expand(string template,string target)=>template.Replace("{T}",target).Replace("{L}",target.Substring(target.Length-1));
        IEnumerable<string> Targets(string action)=>isaacPairs.Where(x=>(string)x["action"]==action).Select(x=>(string)x["target"]);
        ObjectBinding Object(string id){Assert.That(registry.TryGetObject(id,out var item),Is.True,id+" missing from the Unity workcell");return item;}
        ObjectBinding[] WashersAt(string location)=>registry.objects.Where(x=>x.kind=="washer"&&x.root.gameObject.activeSelf&&x.semanticEnabled&&x.currentState.location==location).ToArray();
        int Capacity(string id)=>(int)layout["objects"].Single(x=>(string)x["id"]==id)["capacity"];

        [Test]
        public void TableCoversExactlyTheIsaacActionTargetPairs()
        {
            CollectionAssert.AreEqual(new[]{"ADD_ONE","REMOVE_ONE","FLIP_CARD","ALIGN_ARROW","SCAN","TAG","CLOSE","QUARANTINE"},Table.Select(x=>x.Action));
            var expected=isaacPairs.Select(x=>(string)x["action"]+" "+(string)x["target"]).ToArray();
            var table=Table.SelectMany(row=>"ABCDEFGH".Where(l=>row.Family=="tray"?l<'E':l>='E').Select(l=>row.Action+" "+row.Family+"_"+l)).ToArray();
            CollectionAssert.AreEquivalent(expected,table);
            Assert.That(isaacPairs.All(x=>(bool)x["possible"]),Is.True,"Isaac reset makes all 32 pairs possible");
        }

        [TestCase("ADD_ONE")][TestCase("REMOVE_ONE")][TestCase("FLIP_CARD")][TestCase("ALIGN_ARROW")]
        [TestCase("SCAN")][TestCase("TAG")][TestCase("CLOSE")][TestCase("QUARANTINE")]
        public void EveryObjectTheActionReadsIsPresentAtReset(string action)
        {
            var row=Table.Single(x=>x.Action==action);
            var targets=Targets(action).ToArray();Assert.That(targets.Length,Is.EqualTo(4));
            foreach(string target in targets)
            {
                Assert.That(target,Does.StartWith(row.Family+"_"));
                foreach(string template in row.Objects)
                {
                    string id=Expand(template,target);var item=Object(id);
                    Assert.That(item.kind,Is.EqualTo(template=="{T}"?row.Family:KindByObject[template]),id);
                    Assert.That(item.root.gameObject.activeSelf,Is.True,id+" hidden at reset");
                    Assert.That(item.semanticEnabled,Is.True,id+" disabled at reset");
                    Assert.That(item.root.GetComponentsInChildren<MeshRenderer>(true).Length,Is.GreaterThan(0),id+" has no geometry");
                    InitialState(item,target);
                }
                foreach(string template in row.WasherLocations)
                {
                    string location=Expand(template,target);var washers=WashersAt(location);
                    if(location=="supply_cup"){Assert.That(washers.Length,Is.EqualTo((int)layout["neutral_supply_washers"]).And.GreaterThan(0),location);}
                    else if(location=="return_cup"){Assert.That(washers.Length,Is.Zero.And.LessThan(Capacity("return_cup")),location);}
                    else{Assert.That(washers.Length,Is.EqualTo((int)layout["neutral_washers_per_tray"]).And.GreaterThan(0).And.LessThan(Capacity(location)),location);}
                    foreach(var washer in washers)Assert.That(InsideFootprint(washer,Object(location)),Is.True,washer.id+" is not over "+location);
                }
                Assert.That(Possible(action,target),Is.EqualTo((bool)isaacPairs.Single(x=>(string)x["action"]==action&&(string)x["target"]==target)["possible"]),action+" "+target);
            }
        }

        [Test]
        public void ResetRestoresEveryActionPreconditionAfterTrialMutations()
        {
            foreach(char l in "ABCD")
            {
                var card=Object("tray_"+l+"/card");var arrow=Object("tray_"+l+"/arrow");
                // Either face keeps FLIP_CARD possible, so only a hidden, disabled card blocks it.
                Assert.That(registry.ApplyObject(card.id,card.neutralPosition,card.neutralRotation,false,false,new PublicVisualState(cardFace:1)),Is.True);
                Assert.That(registry.ApplyObject(arrow.id,arrow.neutralPosition,arrow.neutralRotation,true,true,new PublicVisualState(arrowAngleRad:0)),Is.True);
                foreach(var washer in WashersAt("tray_"+l))Assert.That(registry.ApplyObject(washer.id,washer.neutralPosition,washer.neutralRotation,true,true,new PublicVisualState(location:"return_cup")),Is.True);
            }
            foreach(var washer in WashersAt("supply_cup"))Assert.That(registry.ApplyObject(washer.id,washer.neutralPosition,washer.neutralRotation,false,false,new PublicVisualState(location:"supply_cup")),Is.True);
            foreach(char l in "EFGH")
            {
                var tag=Object("container_"+l+"/tag");var lid=Object("container_"+l+"/lid");var container=Object("container_"+l);var code=Object("container_"+l+"/code");
                Assert.That(registry.ApplyObject(code.id,code.neutralPosition,code.neutralRotation,false,false,code.NeutralState),Is.True);
                Assert.That(registry.ApplyObject(tag.id,tag.neutralPosition,tag.neutralRotation,true,true,new PublicVisualState(tagAttached:true,location:"container_"+l+"/home")),Is.True);
                Assert.That(registry.ApplyObject(lid.id,lid.neutralPosition,lid.neutralRotation,true,true,new PublicVisualState(lidOpenFraction:0)),Is.True);
                Assert.That(registry.ApplyObject(container.id,container.neutralPosition,container.neutralRotation,true,true,new PublicVisualState(location:"quarantine_"+l)),Is.True);
            }
            // The mutated scene really blocks every pair, so the reset assertion below is not vacuous.
            foreach(var pair in isaacPairs)Assert.That(Possible((string)pair["action"],(string)pair["target"]),Is.False,(string)pair["action"]+" "+(string)pair["target"]);
            registry.ResetToImportedNeutral();
            foreach(var row in Table)EveryObjectTheActionReadsIsPresentAtReset(row.Action);
        }

        void InitialState(ObjectBinding item,string target)
        {
            string letter=target.Substring(target.Length-1);
            switch(item.kind)
            {
                case "tray": Assert.That(item.label,Is.EqualTo(letter)); break;
                case "container": Assert.That(item.currentState.location,Is.EqualTo(item.id+"/home")); break;
                case "cup": Assert.That(item.label,Is.EqualTo(item.id=="supply_cup"?"SUPPLY":"RETURN")); break;
                case "card":
                    Assert.That(item.hasCardFace,Is.True);Assert.That(item.currentState.cardFace,Is.EqualTo(0));
                    Assert.That(Quaternion.Angle(item.visual.localRotation,Quaternion.identity),Is.LessThan(.01f));
                    Assert.That(item.visual.Find("BackFace"),Is.Not.Null,"two-sided card");
                    break;
                case "arrow":
                    Assert.That(item.hasArrowAngle,Is.True);var slot=item.root.Find("MarkedSlot");
                    Assert.That(slot,Is.Not.Null,"marked upright slot");Assert.That(slot.IsChildOf(item.visual),Is.False,"slot must not rotate with the arrow");
                    float angle=item.currentState.arrowAngleRad.Value;
                    Assert.That(Mathf.Abs((float)Math.IEEERemainder(angle,2*Math.PI)),Is.GreaterThan(.01f),"arrow starts misaligned");
                    Assert.That(Quaternion.Angle(item.visual.localRotation,slot.localRotation),Is.EqualTo(angle*Mathf.Rad2Deg).Within(.01f));
                    break;
                case "code":
                    Assert.That(item.label,Is.EqualTo(letter+"001"));
                    Assert.That(item.label,Is.EqualTo((string)layout["objects"].Single(x=>(string)x["id"]==item.id)["label"]));
                    break;
                case "tag":
                    Assert.That(item.hasTagAttached,Is.True);Assert.That(item.currentState.tagAttached,Is.False);
                    Assert.That(item.currentState.location,Is.EqualTo(target+"/tag_free"));
                    break;
                case "lid":
                    Assert.That(item.hasLidFraction,Is.True);Assert.That(item.currentState.lidOpenFraction,Is.EqualTo(1));
                    Assert.That(Quaternion.Angle(item.visual.localRotation,Quaternion.identity),Is.EqualTo(Mathf.Abs(item.lidOpenAngleRad)*Mathf.Rad2Deg).Within(.01f),"lid rendered open");
                    break;
                case "quarantine":
                    Assert.That(item.label,Is.EqualTo(letter));
                    Assert.That(registry.objects.Where(x=>x.currentState.location==item.id),Is.Empty,"quarantine area starts empty");
                    break;
                default: Assert.Fail("Unexpected kind "+item.kind); break;
            }
        }

        // Horizontal footprint of the location object, in its own Isaac frame (x, y horizontal; z up).
        bool InsideFootprint(ObjectBinding item,ObjectBinding location)
        {
            var size=layout["objects"].Single(x=>(string)x["id"]==location.id)["dimensions_m"];
            var local=location.root.InverseTransformPoint(item.root.position);
            float x=local.z,y=-local.x;
            return Mathf.Abs(x)<=(float)size[0]/2&&Mathf.Abs(y)<=(float)size[1]/2;
        }

        // Unity-side mirror of isaac/workcell/layout.py preconditions() on the registry's current public state.
        bool Possible(string action,string target)
        {
            bool Available(string id)=>Object(id).root.gameObject.activeSelf&&Object(id).semanticEnabled;
            string letter=target.Substring(target.Length-1);
            bool possible=action switch
            {
                "ADD_ONE"=>WashersAt("supply_cup").Length>0&&WashersAt(target).Length<Capacity(target),
                "REMOVE_ONE"=>WashersAt(target).Length>0&&WashersAt("return_cup").Length<Capacity("return_cup"),
                "FLIP_CARD"=>Available(target+"/card")&&Object(target+"/card").currentState.cardFace is 0 or 1,
                "ALIGN_ARROW"=>Available(target+"/arrow")&&Math.Abs(Math.IEEERemainder(Object(target+"/arrow").currentState.arrowAngleRad.Value,2*Math.PI))>.01,
                "SCAN"=>Available(target+"/code")&&Object(target+"/code").label.Length>0,
                "TAG"=>Available(target+"/tag")&&Object(target+"/tag").currentState.tagAttached==false,
                "CLOSE"=>Available(target+"/lid")&&Object(target+"/lid").currentState.lidOpenFraction>0,
                "QUARANTINE"=>Available("quarantine_"+letter)&&!registry.objects.Any(x=>x.currentState.location=="quarantine_"+letter),
                _=>throw new ArgumentException(action)
            };
            return possible&&Available(target);
        }
    }
}
