using System;
using System.Linq;
using AcousticVocab.Workcell.Editor;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace AcousticVocab.Workcell.Tests
{
    // Requirement trace for the participant robot. Uses the same imported
    // workcell as LayoutParityTests (reviewed converted G1 description).
    public class RequirementTraceWorkcellTests
    {
        WorkcellRegistry registry;
        JObject layout;
        [OneTimeSetUp] public void BuildImportedScene() { WorkcellBuild.Configure();layout=WorkcellBuild.ReadLayout(); }
        [SetUp] public void Find()
        { registry=UnityEngine.Object.FindAnyObjectByType<WorkcellRegistry>(FindObjectsInactive.Include);Assert.That(registry,Is.Not.Null);registry.ResetToImportedNeutral(); }
        [TearDown] public void Reset() { if(registry!=null) registry.ResetToImportedNeutral(); }
        Transform Robot()
        {
            var holders=registry.GetComponentsInChildren<Transform>(true).Where(t=>t.name=="ImportedG1").ToArray();
            Assert.That(holders.Length,Is.EqualTo(1));return holders[0];
        }

        [Test, Category("REQ_APP_01"), NUnit.Framework.Property("Requirement","REQ-APP-01")]
        public void OneFixedBaseG1WithDexThreeHandsIsTheOnlyRobot()
        {
            Assert.That(UnityEngine.Object.FindObjectsByType<WorkcellRegistry>(FindObjectsInactive.Include).Length,Is.EqualTo(1));
            var robot=layout["robot"];
            Assert.That((string)robot["configuration"],Is.EqualTo("G129_CFG_WITH_DEX3_BASE_FIX"));
            Assert.That((bool)robot["fixed_base"],Is.True);
            Assert.That((int)robot["body_joint_count"],Is.EqualTo(29));
            Assert.That((int)robot["left_hand_joint_count"],Is.EqualTo(7));Assert.That((int)robot["right_hand_joint_count"],Is.EqualTo(7));
            var names=registry.CanonicalJointNames;
            Assert.That(names.Count,Is.EqualTo(43));
            Assert.That(names.Count(n=>n.StartsWith("left_hand_",StringComparison.Ordinal)),Is.EqualTo(7));
            Assert.That(names.Count(n=>n.StartsWith("right_hand_",StringComparison.Ordinal)),Is.EqualTo(7));
            foreach(string finger in new[]{"thumb_0","thumb_1","thumb_2","index_0","index_1","middle_0","middle_1"})
                foreach(string side in new[]{"left","right"}) Assert.That(names,Does.Contain(side+"_hand_"+finger+"_joint"));
            var holder=Robot();
            Assert.That(Vector3.Distance(holder.localPosition,WorkcellBuild.Position(robot["position_m"])),Is.LessThan(1e-6f));
            Assert.That(holder.localRotation,Is.EqualTo(Quaternion.identity));
            foreach(var joint in registry.joints) Assert.That(joint.link.IsChildOf(holder),Is.True,joint.name);
        }

        [Test, Category("REQ_APP_01"), NUnit.Framework.Property("Requirement","REQ-APP-01")]
        public void DrivingEveryJointToBothLimitsNeverMovesTheRobotBase()
        {
            var holder=Robot();var roots=holder.Cast<Transform>().ToArray();Assert.That(roots.Length,Is.GreaterThan(0));
            Vector3 holderPosition=holder.position;Quaternion holderRotation=holder.rotation;
            var rootPoses=roots.Select(r=>(r.position,r.rotation)).ToArray();
            var linkOffsets=registry.joints.Select(j=>j.link.localPosition).ToArray();
            foreach(bool upper in new[]{false,true})
            {
                foreach(var joint in registry.joints) Assert.That(registry.ApplyJoint(joint.name,upper?joint.upperRad:joint.lowerRad),Is.True,joint.name);
                Assert.That(Vector3.Distance(holder.position,holderPosition),Is.LessThan(1e-6f));
                Assert.That(Quaternion.Angle(holder.rotation,holderRotation),Is.LessThan(1e-3f));
                for(int i=0;i<roots.Length;i++)
                {
                    Assert.That(Vector3.Distance(roots[i].position,rootPoses[i].position),Is.LessThan(1e-6f),roots[i].name);
                    Assert.That(Quaternion.Angle(roots[i].rotation,rootPoses[i].rotation),Is.LessThan(1e-3f),roots[i].name);
                }
                for(int i=0;i<registry.joints.Length;i++)
                    Assert.That(registry.joints[i].link.localPosition,Is.EqualTo(linkOffsets[i]),"joints only rotate: "+registry.joints[i].name);
            }
        }

        [Test, Category("REQ_APP_01"), NUnit.Framework.Property("Requirement","REQ-APP-01")]
        public void RobotFramesCannotBeMovedThroughTheObjectApi()
        {
            var holder=Robot();Vector3 before=holder.localPosition;
            var robotNames=registry.links.Select(l=>l.name).Concat(registry.joints.Select(j=>j.name)).Append("ImportedG1").ToArray();
            Assert.That(registry.objects.Select(o=>o.id).Intersect(robotNames),Is.Empty);
            foreach(string name in robotNames)
                Assert.That(registry.ApplyObject(name,holder.localPosition+Vector3.forward,Quaternion.identity,true,true,default),Is.False,name);
            Assert.That(holder.localPosition,Is.EqualTo(before));
        }
    }
}
