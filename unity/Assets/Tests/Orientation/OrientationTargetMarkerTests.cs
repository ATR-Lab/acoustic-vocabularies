using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Text.RegularExpressions;
using AcousticVocab.Foundation.Editor;
using AcousticVocab.ResponsePanel;
using AcousticVocab.Workcell;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace AcousticVocab.Orientation.Tests
{
    // Synthetic boxes at the reviewed workcell layout poses; no study content.
    public sealed class OrientationTargetMarkerTests
    {
        GameObject presentation;WorkcellRegistry workcell;Transform screen;OrientationTargetMarker marker;
        static Vector3 Vector(JToken t) => new Vector3((float)t[0],(float)t[1],(float)t[2]);
        [SetUp] public void BuildSyntheticWorkcell()
        {
            var layout=JObject.Parse(File.ReadAllText(Path.Combine(FoundationBuild.RepositoryRoot,"apparatus/workcell_layout.json")));
            presentation=new GameObject("Synthetic presentation");
            workcell=new GameObject("Workcell").AddComponent<WorkcellRegistry>();workcell.transform.SetParent(presentation.transform,false);
            var objects=new List<ObjectBinding>();
            foreach(JObject d in layout["objects"])
            {
                string id=(string)d["id"];if(!Regex.IsMatch(id,@"\A(tray|container|quarantine)_[A-H]"))continue;
                var root=new GameObject(id.Replace('/','_')).transform;root.SetParent(workcell.transform,false);root.localPosition=SceneCoordinates.Position(Vector(d["position_m"]));
                var size=Vector(d["dimensions_m"]);var box=GameObject.CreatePrimitive(PrimitiveType.Cube).transform;box.SetParent(root,false);box.localScale=new Vector3(size.y,size.z,size.x);
                objects.Add(new ObjectBinding{id=id,kind=(string)d["kind"],label=(string)d["label"],root=root,visual=box});
            }
            workcell.objects=objects.ToArray();
            screen=new GameObject("Silent orientation screen").transform;screen.SetParent(presentation.transform,false);screen.localPosition=new Vector3(0,1.2f,.95f);
            marker=new OrientationTargetMarker(workcell,screen,Shader.Find("Unlit/Color"));
        }
        [TearDown] public void DestroySyntheticWorkcell() { marker.Dispose();Object.DestroyImmediate(presentation); }
        Bounds Box(ObjectBinding item)
        {
            var b=item.visual.GetComponent<MeshFilter>().sharedMesh.bounds;var m=workcell.transform.worldToLocalMatrix*item.visual.localToWorldMatrix;
            var result=new Bounds(m.MultiplyPoint3x4(b.min),Vector3.zero);result.Encapsulate(m.MultiplyPoint3x4(b.max));return result;
        }
        static bool Over(Bounds b,Vector3 p) => b.min.x<p.x&&p.x<b.max.x&&b.min.z<p.z&&p.z<b.max.z;

        [Test, Category("REQ_ORI_01"), NUnit.Framework.Property("Requirement", "REQ-ORI-01"), Category("REQ_ORI_02"), NUnit.Framework.Property("Requirement", "REQ-ORI-02")]
        public void EachTargetCardMarksItsOwnObjectFromAbove()
        {
            Assert.That(PublicCommands.Targets.Select(OrientationTargetMarker.ObjectId),Is.EqualTo(new[]{"tray_A","tray_B","tray_C","tray_D","container_E","container_F","container_G","container_H"}));
            Assert.Throws<OrientationFault>(()=>OrientationTargetMarker.ObjectId("I"));Assert.Throws<OrientationFault>(()=>OrientationTargetMarker.ObjectId(null));
            Assert.Throws<OrientationFault>(()=>marker.Show("I"));Assert.That(marker.Pointer.gameObject.activeSelf,Is.False);
            foreach(string target in PublicCommands.Targets)
            {
                marker.Show(target);string id=OrientationTargetMarker.ObjectId(target);
                Assert.That(marker.MarkedTarget,Is.EqualTo(target));Assert.That(marker.Pointer.gameObject.activeInHierarchy,Is.True);
                var point=workcell.transform.InverseTransformPoint(marker.Pointer.position);
                foreach(var item in workcell.objects)
                {
                    var box=Box(item);bool own=item.id==id,part=item.id.StartsWith(id+"/");
                    if(own)Assert.That(Over(box,point),Is.True,target+" points at its own object");
                    else if(!part&&item.kind is "tray" or "container" or "quarantine")Assert.That(Over(box,point),Is.False,target+" does not point at "+item.id);
                    if(own||part)Assert.That(point.y,Is.GreaterThan(box.max.y),target+" stays above "+item.id);
                }
            }
            // A target whose object is missing or of another kind refuses before any marking.
            var empty=new GameObject("Empty workcell").AddComponent<WorkcellRegistry>();empty.transform.SetParent(presentation.transform,false);
            Assert.Throws<OrientationFault>(()=>new OrientationTargetMarker(empty,screen,Shader.Find("Unlit/Color")));
            Assert.That(workcell.TryGetObject("container_E",out var container),Is.True);container.kind="quarantine";
            Assert.Throws<OrientationFault>(()=>new OrientationTargetMarker(workcell,screen,Shader.Find("Unlit/Color")));
            Assert.That(screen.childCount,Is.EqualTo(1),"A refused marker leaves nothing on the screen");
        }

        [Test, Category("REQ_NEUT_01"), NUnit.Framework.Property("Requirement", "REQ-NEUT-01")]
        public void MarkingNeverMovesTheNeutralSceneOrJoinsTheWorkcell()
        {
            var objects=workcell.objects;int children=workcell.transform.childCount;
            object State(ObjectBinding x) => (x.root.localPosition,x.root.localRotation,x.root.localScale,x.root.gameObject.activeSelf,x.semanticEnabled,x.visual.localPosition,x.visual.localRotation,x.visual.localScale);
            var before=objects.Select(State).ToArray();
            foreach(string target in PublicCommands.Targets.Concat(PublicCommands.Targets.Reverse()))
            {
                marker.Show(target);
                Assert.That(marker.Pointer.IsChildOf(workcell.transform),Is.False);Assert.That(marker.Pointer.IsChildOf(screen),Is.True);
            }
            marker.Show(null);
            Assert.That(marker.Pointer.gameObject.activeSelf,Is.False);Assert.That(marker.MarkedTarget,Is.Null);
            Assert.That(workcell.objects,Is.SameAs(objects));Assert.That(workcell.transform.childCount,Is.EqualTo(children));
            Assert.That(objects.Select(State),Is.EqualTo(before));
        }

        [Test, Category("REQ_NEUT_01"), NUnit.Framework.Property("Requirement", "REQ-NEUT-01")]
        public void ProtectedBlocksNeverShowTheMarking()
        {
            // Only orientation code (and its tests) can construct or show the marker.
            Assert.That(typeof(OrientationTargetMarker).IsVisible,Is.False);
            Assert.That(typeof(OrientationTargetMarker).Assembly.GetCustomAttributes<InternalsVisibleToAttribute>().Select(x=>x.AssemblyName),
                Is.SubsetOf(new[]{"AcousticVocab.Orientation.Tests","AcousticVocab.Orientation.PlayModeTests"}));
            // A fault hides it.
            double now=0;var plan=OrientationPlan.Parse(PlanJson(),"engineering-pending-review");var flow=new OrientationFlow(plan,()=>now,_=>{});flow.Start();
            for(int i=0;i<8;i++) { now+=10000;flow.CompleteDemo(flow.CurrentCard.Id,10000,10000,1000d/30);flow.Next(); }
            marker.Present(flow);Assert.That(marker.MarkedTarget,Is.EqualTo(plan.Targets[0].Id));
            flow.Fault("SYNTHETIC");marker.Present(flow);Assert.That(marker.Pointer.gameObject.activeSelf,Is.False);
            // The handoff to protected blocks hides the screen that carries it.
            marker.Show("E");Assert.That(marker.Pointer.gameObject.activeInHierarchy,Is.True);
            var hostObject=new GameObject("Synthetic orientation host");hostObject.SetActive(false);hostObject.transform.SetParent(presentation.transform,false);
            var host=hostObject.AddComponent<OrientationHost>();const BindingFlags flags=BindingFlags.NonPublic|BindingFlags.Instance;
            typeof(OrientationHost).GetField("display",flags).SetValue(host,screen);typeof(OrientationHost).GetField("marker",flags).SetValue(host,marker);
            host.ReleaseForHandoff();
            Assert.That(marker.Pointer.gameObject.activeInHierarchy,Is.False,"No marking once protected blocks own the scene");
        }
        static string PlanJson()
        {
            var v=JObject.Parse(File.ReadAllText(Path.Combine(FoundationBuild.RepositoryRoot,"apparatus/orientation/orientation-plan.example.json")));
            v["content_status"]="protocol_reviewed";v["review_evidence_sha256"]=new string('a',64);return v.ToString();
        }
    }
}
