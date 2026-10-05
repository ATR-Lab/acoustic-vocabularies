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
