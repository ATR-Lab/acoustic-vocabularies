using System;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.Foundation.Editor;
using AcousticVocab.StateIntegration;
using AcousticVocab.StateSources;
using AcousticVocab.Workcell;
using AcousticVocab.Workcell.Editor;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace AcousticVocab.Tests
{
    public sealed class ActualNeutralRendererTests
    {
        [Test]
        public void RecordedProtectedLiveFramesMatchSnapshotAndImportedRenderer()
        {
            string root=Environment.GetEnvironmentVariable("PROTECTED_STATE_SOURCE_EVIDENCE");
            if(string.IsNullOrWhiteSpace(root)) Assert.Ignore("Set private PROTECTED_STATE_SOURCE_EVIDENCE to the completed actual protected stream.");
            var summary=JObject.Parse(File.ReadAllText(Path.Combine(root,"protected-stream/summary.json")));
            Assert.That((bool)summary["completed"],Is.True);Assert.That(summary["fault"].Type,Is.EqualTo(JTokenType.Null));
            Assert.That((string)summary["source_kind"],Is.EqualTo("live"));Assert.That((bool)summary["protected_neutral_diagnostic"],Is.True);
            WorkcellBuild.Configure();
            var workcell=UnityEngine.Object.FindAnyObjectByType<WorkcellRegistry>(FindObjectsInactive.Include);
            byte[] neutral=File.ReadAllBytes(Path.Combine(root,"reset-check/neutral_v1.json"));
            Assert.That(SceneRegistry.Hash(neutral),Is.EqualTo((string)summary["reset_snapshot_sha256"]));
            var layout=JObject.Parse(workcell.ImportedLayout.text);
            var keys=((JArray)layout["objects"]).ToDictionary(x=>(string)x["id"],x=>((JObject)x["state"]).Properties().Select(p=>p.Name).ToArray());
            var registry=new SceneRegistry((string)summary["station_id"],(string)summary["scene_sha256"],SceneRegistry.Hash(neutral),workcell.CanonicalJointNames,keys,workcell.anchorIds);
            var snapshot=new SnapshotSource(neutral,registry,0);var renderer=new WorkcellStateRenderer(workcell);
            renderer.VerifyImportedNeutral(snapshot.Neutral);
            foreach(string name in new[]{"sample-first.json","sample-last.json"})
            {
                byte[] bytes=File.ReadAllBytes(Path.Combine(root,"protected-stream",name));
                Assert.That(SceneRegistry.Hash(bytes),Is.EqualTo((string)summary["hashes"][name]));
                var frame=StateParser.Parse(Encoding.UTF8.GetString(bytes),registry);
                Assert.That(frame.Provenance,Is.EqualTo("live"));Assert.That(frame.Joints.Count,Is.EqualTo(43));Assert.That(frame.Objects.Count,Is.EqualTo(60));
                Assert.That(NeutralComparison.Matches(frame,snapshot.Neutral),Is.True,name);
                renderer.Apply(frame);
                foreach(var item in workcell.objects)
                {
                    Assert.That(Vector3.Distance(item.root.localPosition,item.neutralPosition),Is.LessThan(.001),item.id);
                    Assert.That(Quaternion.Angle(item.root.localRotation,item.neutralRotation),Is.LessThan(.5),item.id);
                }
            }
            // Parsing retained actual frames proves pose parity, not current
            // network freshness, qualified clocks, reset authorization or rate.
        }
        [Test]
        public void CapturedIsaacNeutralMatchesImportedWorkcellAndBothSources()
        {
            WorkcellBuild.Configure();
            var workcell=UnityEngine.Object.FindAnyObjectByType<WorkcellRegistry>(FindObjectsInactive.Include);
            byte[] bytes=File.ReadAllBytes(Path.Combine(FoundationBuild.RepositoryRoot,"isaac/snapshots/neutral_v1.json"));
            Assert.That(SceneRegistry.Hash(bytes),Is.EqualTo("e2628102a9a85dfc7566052297e0b038cdf4aa4025319d0e868425cecbb1a80e"));
            var snapshot=JObject.Parse(Encoding.UTF8.GetString(bytes));
            var layout=JObject.Parse(workcell.ImportedLayout.text);
            var keys=((JArray)layout["objects"]).ToDictionary(x=>(string)x["id"],x=>((JObject)x["state"]).Properties().Select(p=>p.Name).ToArray());
            var registry=new SceneRegistry("station-01",(string)snapshot["scene_sha256"],SceneRegistry.Hash(bytes),
                workcell.CanonicalJointNames,keys,workcell.anchorIds);
            var offline=new SnapshotSource(bytes,registry,0); var renderer=new WorkcellStateRenderer(workcell);
            for(int i=0;i<43;i++) Assert.That(Math.Abs(offline.Neutral.Joints[i]-workcell.joints[i].neutralRad),Is.LessThan(Math.PI/360),"joint "+workcell.joints[i].name);
            foreach(var value in offline.Neutral.Objects)
            {
                Assert.That(workcell.TryGetObject(value.Id,out var item),Is.True);
                Assert.That(Vector3.Distance(SceneCoordinates.Position(value.Position),item.neutralPosition),Is.LessThan(.001),"position "+value.Id);
                Assert.That(Quaternion.Angle(SceneCoordinates.Rotation(value.Rotation),item.neutralRotation),Is.LessThan(.5),"rotation "+value.Id);
                Assert.That(value.Visible,Is.EqualTo(item.neutralVisible),"visible "+value.Id);
                Assert.That(value.Enabled,Is.EqualTo(item.neutralEnabled),"enabled "+value.Id);
                foreach(var field in value.VisualState.Properties())
                {
                    var state=item.NeutralState;
                    if(field.Name=="card_face") Assert.That((int)field.Value,Is.EqualTo(state.cardFace),value.Id);
                    if(field.Name=="arrow_angle_rad") Assert.That((double)field.Value,Is.EqualTo((double)state.arrowAngleRad.Value).Within(1e-6),value.Id);
                    if(field.Name=="lid_open_fraction") Assert.That((double)field.Value,Is.EqualTo((double)state.lidOpenFraction.Value),value.Id);
                    if(field.Name=="tag_attached") Assert.That((bool)field.Value,Is.EqualTo(state.tagAttached),value.Id);
                    if(field.Name=="location") Assert.That((string)field.Value,Is.EqualTo(state.location),value.Id);
                }
            }
            renderer.VerifyImportedNeutral(offline.Neutral);renderer.Apply(offline.Render(0));
            Assert.That(offline.ConfirmReset(offline.Neutral,0),Is.True);
            // This is an explicitly synthetic wire projection of an actual
            // snapshot, not evidence of a measured live neutral stream.
            var objects=new JArray(((JObject)snapshot["state"]["objects"]).Properties().OrderBy(x=>x.Name,StringComparer.Ordinal).Select(pair=>
            {
                var item=new JObject { ["id"]=pair.Name };
                foreach(string key in new[]{"position_m","rotation_xyzw","visible","enabled","state"}) item[key]=pair.Value[key].DeepClone();
                return item;
            }));
            var raw=new JObject { ["version"]=2,["kind"]="state",["source_kind"]="synthetic",["station_id"]="station-01",
                ["scene_sha256"]=registry.SceneHash,["reset_snapshot_sha256"]=registry.SnapshotHash,["session_id"]=new string('a',32),
                ["seq"]=0,["host_monotonic_ns"]="0",["sim_time"]=0,["sim_step"]=0,["joint_names"]=new JArray(registry.JointNames),
                ["joint_positions"]=snapshot["state"]["robot"]["joint_positions_rad"].DeepClone(),["objects"]=objects };
            var clock=new SourceClock(0,5,new string('d',64));clock.Echo(0,0,0,0);
            var live=new LiveIsaacSource(0,0,clock);live.Receive(StateParser.Parse(raw,registry,true),0,0);
            Assert.That(live.ConfirmReset(offline.Neutral,0),Is.True);
            renderer.Apply(live.Render(0));
            foreach(var obj in workcell.objects)
            {
                Assert.That(Vector3.Distance(obj.root.localPosition,obj.neutralPosition),Is.LessThan(.001));
                Assert.That(Quaternion.Angle(obj.root.localRotation,obj.neutralRotation),Is.LessThan(.5));
            }
            foreach(var joint in workcell.joints)
                Assert.That(Quaternion.Angle(joint.link.localRotation,Quaternion.AngleAxis(joint.neutralRad*Mathf.Rad2Deg,joint.unityAxis)),Is.LessThan(.5));
        }
    }
}
