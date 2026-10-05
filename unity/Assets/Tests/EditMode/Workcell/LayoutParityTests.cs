using System;
using System.IO;
using System.Linq;
using AcousticVocab.Foundation;
using AcousticVocab.Foundation.Editor;
using AcousticVocab.Workcell.Editor;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace AcousticVocab.Workcell.Tests
{
    public class LayoutParityTests
    {
        WorkcellRegistry registry;
        JObject layout;
        [OneTimeSetUp] public void BuildImportedScene()
        {
            WorkcellBuild.Configure(); layout=WorkcellBuild.ReadLayout();
            registry=UnityEngine.Object.FindAnyObjectByType<WorkcellRegistry>(FindObjectsInactive.Include);
        }
        [SetUp] public void Reset() => registry.ResetToImportedNeutral();
        [Test] public void LayoutBytesAreExactIsaacExport()
        {
            Assert.That(FoundationBuild.Hash(registry.ImportedLayout.bytes),Is.EqualTo(WorkcellBuild.ExpectedLayoutHash));
            Assert.That(registry.ImportedLayout.bytes,Is.EqualTo(File.ReadAllBytes(Path.Combine(FoundationBuild.RepositoryRoot,"apparatus/workcell_layout.json"))));
        }
        [Test] public void EveryObjectHasMatchingIdLabelAndPose()
        {
            Assert.That(registry.objects.Length,Is.EqualTo(60));
            foreach(var d in layout["objects"])
            {
                Assert.That(registry.TryGetObject((string)d["id"],out var obj),Is.True);
                Assert.That(obj.label,Is.EqualTo((string)d["label"]));
                // Compare back in the source coordinate system, independently of the loader call.
                Vector3 p=obj.root.localPosition;
                Assert.That(Vector3.Distance(new Vector3(p.z,-p.x,p.y),new Vector3((float)d["position_m"][0],(float)d["position_m"][1],(float)d["position_m"][2])),Is.LessThan(.01f));
                Quaternion q=obj.root.localRotation;
                Assert.That(Quaternion.Angle(new Quaternion(-q.z,q.x,-q.y,q.w),new Quaternion((float)d["rotation_xyzw"][0],(float)d["rotation_xyzw"][1],(float)d["rotation_xyzw"][2],(float)d["rotation_xyzw"][3])),Is.LessThan(2));
                Assert.That(obj.root.GetComponentsInChildren<MeshRenderer>(true).Length,Is.GreaterThan(0));
                Assert.That(obj.root.localScale,Is.EqualTo(Vector3.one));
            }
        }
        [Test] public void PrimaryLabelsHaveTheSameGlyphSizeAndContrast()
        {
            var labels=registry.objects.Where(x=>x.kind=="tray"||x.kind=="code"||x.kind=="quarantine").ToArray();
            Assert.That(labels.Length,Is.EqualTo(12));
            CollectionAssert.AreEquivalent(new[]{"A","B","C","D","E","F","G","H"},labels.Where(x=>x.kind!="code").Select(x=>x.label));
            var ink=labels.SelectMany(x=>x.root.GetComponentsInChildren<MeshRenderer>(true)).Where(x=>x.name.StartsWith("c",StringComparison.Ordinal)).ToArray();
            Assert.That(ink.Select(x=>x.sharedMaterial).Distinct().Count(),Is.EqualTo(1));
            foreach(var item in labels) Assert.That(item.primaryPixelMetres,Is.EqualTo(.0038f));
        }
        [Test] public void AllPublicResetPropFamiliesArePresent()
        {
            foreach(string name in new[]{"tray","container","code","lid","tag","quarantine","arrow","card"}) Assert.That(registry.objects.Count(x=>x.kind==name),Is.EqualTo(4));
            Assert.That(registry.objects.Count(x=>x.kind=="washer"),Is.EqualTo(24));
            Assert.That(registry.objects.Count(x=>x.kind=="cup"),Is.EqualTo(2));
            foreach(var item in registry.objects.Where(x=>x.kind=="lid")) Assert.That(item.currentState.lidOpenFraction,Is.EqualTo(1));
            foreach(var item in registry.objects.Where(x=>x.kind=="arrow")) Assert.That(item.root.Find("MarkedSlot"),Is.Not.Null);
            foreach(var item in registry.objects.Where(x=>x.kind=="card")) Assert.That(item.visual.Find("BackFace"),Is.Not.Null);
        }
        [Test] public void CanonicalJointOrderAndCountsMatchVerifiedMap()
        {
            var rows=File.ReadAllLines(Path.Combine(FoundationBuild.RepositoryRoot,"docs/spikes/urdf/joint_map.csv")).Skip(1).Where(x=>x.Length!=0).Select(x=>x.Split(',')[1]).ToArray();
            CollectionAssert.AreEqual(rows,registry.CanonicalJointNames);
            Assert.That(rows.Length,Is.EqualTo(43));
            Assert.That(rows.Count(x=>x.StartsWith("left_hand_",StringComparison.Ordinal)),Is.EqualTo(7));
            Assert.That(rows.Count(x=>x.StartsWith("right_hand_",StringComparison.Ordinal)),Is.EqualTo(7));
            Assert.That(registry.links.Length,Is.EqualTo(55));
        }
        [Test] public void InvalidJointCannotMutateRobot()
        {
            var joint=registry.joints[0];var before=joint.link.localRotation;
            Assert.That(registry.ApplyJoint(joint.name,float.NaN),Is.False);
            Assert.That(registry.ApplyJoint(joint.name,joint.upperRad+1),Is.False);
            Assert.That(registry.ApplyJoint("unknown",0),Is.False);
            Assert.That(joint.link.localRotation,Is.EqualTo(before));
        }
        [Test] public void RobotUsesAuthoredVisualMaterialBindings()
        {
            var authored=JObject.Parse(File.ReadAllText(Path.Combine(FoundationBuild.RepositoryRoot,"docs/workcell/robot-material-bindings.json")));
            var visible=authored["meshes"].Where(m=>(string)m["purpose"]=="default"&&((string)m["path"]).Contains("/visuals/")).ToArray();
            Assert.That(visible.Length,Is.EqualTo(51));
            foreach(var link in registry.links)
            {
                var visual=link.link.Find("Visual"); if(visual==null)continue;
                var binding=visible.Single(x=>((string)x["rigid_body"]).Split('/').Last()==link.name);
                var inputs=authored["materials"][(string)binding["material"]]["shaders"][0]["inputs"];
                var rgb=inputs["diffuse_color_constant"]??inputs["diffuse_reflection_color"];
                Color actual=visual.GetComponent<MeshRenderer>().sharedMaterial.color;
                // Unity serializes material channels as float; allow one micro-unit
                // rather than requiring double-JSON and serialized float bit equality.
                Assert.That(Mathf.Abs(actual.r-(float)rgb[0]),Is.LessThan(1e-6f),link.name);
                Assert.That(Mathf.Abs(actual.g-(float)rgb[1]),Is.LessThan(1e-6f),link.name);
                Assert.That(Mathf.Abs(actual.b-(float)rgb[2]),Is.LessThan(1e-6f),link.name);
            }
        }
        [Test] public void InvalidObjectCannotPartiallyMutateScene()
        {
            var item=registry.objects.First(x=>x.kind=="card"); var before=item.root.localPosition;
            Assert.That(registry.ApplyObject(item.id,Vector3.one,Quaternion.identity,false,false,new PublicVisualState(cardFace:2)),Is.False);
            Assert.That(registry.ApplyObject(item.id,Vector3.one,Quaternion.identity,false,false,new PublicVisualState(cardFace:1,location:"tray_A")),Is.False);
            Assert.That(registry.ApplyObject(item.id,Vector3.one,new Quaternion(0,0,0,0),false,false,item.NeutralState),Is.False);
            Assert.That(item.root.localPosition,Is.EqualTo(before)); Assert.That(item.root.gameObject.activeSelf,Is.True);
        }
        [Test] public void VisualStateRotatesOnlyTheAuthoredMovingPart()
        {
            var arrow=registry.objects.First(x=>x.kind=="arrow");var fixedSlot=arrow.root.Find("MarkedSlot"); var before=fixedSlot.localRotation;
            Assert.That(registry.ApplyObject(arrow.id,arrow.neutralPosition,arrow.neutralRotation,true,false,new PublicVisualState(arrowAngleRad:0)),Is.True);
            Assert.That(arrow.visual.localRotation,Is.EqualTo(Quaternion.identity)); Assert.That(fixedSlot.localRotation,Is.EqualTo(before));
            Assert.That(arrow.semanticEnabled,Is.False); Assert.That(arrow.root.gameObject.activeSelf,Is.True);
            var lid=registry.objects.First(x=>x.kind=="lid");
            Assert.That(registry.ApplyObject(lid.id,lid.neutralPosition,lid.neutralRotation,true,true,new PublicVisualState(lidOpenFraction:0)),Is.True);
            Assert.That(lid.visual.localRotation,Is.EqualTo(Quaternion.identity));
            registry.ResetToImportedNeutral(); Assert.That(lid.currentState.lidOpenFraction,Is.EqualTo(1)); Assert.That(arrow.semanticEnabled,Is.True);
        }
        [Test] public void CoordinateConversionPreservesPhysicalRotation()
        {
            var axis=new Vector3(1,2,3).normalized;var q=Quaternion.AngleAxis(37,axis);var v=new Vector3(.2f,-.8f,.9f);
            Assert.That(Vector3.Distance(SceneCoordinates.Position(q*v),SceneCoordinates.Rotation(q)*SceneCoordinates.Position(v)),Is.LessThan(.000001f));
            Assert.That(SceneCoordinates.Position(Vector3.right),Is.EqualTo(Vector3.forward));
        }
        [Test] public void SceneAssemblyCannotReferenceSessionAnswersOrSourceTransport()
        {
            var references=typeof(WorkcellRegistry).Assembly.GetReferencedAssemblies().Select(x=>x.Name).ToArray();
            foreach(string name in references) Assert.That(name=="mscorlib"||name=="System"||name=="System.Core"||name=="netstandard"||name.StartsWith("UnityEngine.",StringComparison.Ordinal),Is.True,name);
            var definition=JObject.Parse(File.ReadAllText("Assets/ExperimentApp/Runtime/Workcell/AcousticVocab.Workcell.asmdef"));
            Assert.That(definition["references"].Count(),Is.Zero); Assert.That((bool)definition["overrideReferences"],Is.True);
        }
        [Test] public void ParticipantSceneHasNoAmbientAudioPhysicsOrExtraCamera()
        {
            Assert.That(registry.GetComponentsInChildren<AudioSource>(true),Is.Empty);
            Assert.That(registry.GetComponentsInChildren<Collider>(true),Is.Empty);
            Assert.That(registry.GetComponentsInChildren<Rigidbody>(true),Is.Empty);
            Assert.That(registry.GetComponentsInChildren<Camera>(true),Is.Empty);
            FoundationBuild.ParticipantScenePath=WorkcellBuild.ScenePath; FoundationBuild.VerifyParticipantScene();
            // Guard reopens the scene; refresh references for subsequent tests.
            registry=UnityEngine.Object.FindAnyObjectByType<WorkcellRegistry>(FindObjectsInactive.Include);
        }
    }
}
