using System;
using System.Linq;
using AcousticVocab.StateIntegration;
using AcousticVocab.StateSources;
using AcousticVocab.Workcell;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace AcousticVocab.Tests
{
    public sealed class WorkcellStateRendererTests
    {
        GameObject root;
        WorkcellRegistry registry;
        WorkcellStateRenderer renderer;
        [SetUp] public void Setup()
        {
            root=new GameObject("SyntheticRendererTest");registry=root.AddComponent<WorkcellRegistry>();
            Transform Child(string name) { var value=new GameObject(name).transform;value.SetParent(root.transform);return value; }
            registry.joints=new[]{new RobotJointBinding { name="j0",link=Child("j0"),unityAxis=Vector3.up,lowerRad=-1,upperRad=1 },
                new RobotJointBinding { name="j1",link=Child("j1"),unityAxis=Vector3.up,lowerRad=-1,upperRad=1 }};
            registry.objects=new[]{new ObjectBinding { id="a",root=Child("a"),visual=Child("av"),hasCardFace=true,neutralVisible=true,neutralEnabled=true,neutralRotation=Quaternion.identity },
                new ObjectBinding { id="b",root=Child("b"),visual=Child("bv"),neutralVisible=true,neutralEnabled=true,neutralRotation=Quaternion.identity }};
            registry.ResetToImportedNeutral();renderer=new WorkcellStateRenderer(registry);
        }
        [TearDown] public void Cleanup() { UnityEngine.Object.DestroyImmediate(root); }
        SceneFrame Frame(double lastJoint=.2,string lastId="b",float lastX=2) => new SceneFrame(new string('a',32),0,0,0,0,"live",new[]{.1,lastJoint},new[]{
            new SceneObject("a",new Vector3(1,2,3),Quaternion.identity,true,true,new JObject { ["card_face"]=1 }),
            new SceneObject(lastId,new Vector3(lastX,0,0),Quaternion.identity,false,false,new JObject()) });
        [Test] public void ConvertsOnceAndAppliesDiscreteState()
        {
            renderer.Apply(Frame());
            Assert.That(registry.objects[0].root.localPosition,Is.EqualTo(new Vector3(-2,3,1)));
            Assert.That(registry.objects[0].currentState.cardFace,Is.EqualTo(1));
            Assert.That(registry.objects[1].root.gameObject.activeSelf,Is.False);
            Assert.That(registry.objects[1].semanticEnabled,Is.False);
            Assert.That(Quaternion.Angle(registry.joints[1].link.localRotation,Quaternion.AngleAxis(.2f*Mathf.Rad2Deg,Vector3.up)),Is.LessThan(.001f));
        }
        [TestCase("joint")][TestCase("object")][TestCase("overflow")]
        public void LaterInvalidValueCannotPartiallyMutateScene(string fault)
        {
            var frame=fault=="joint"?Frame(2):fault=="object"?Frame(.2,"missing"):Frame(.2,"b",float.PositiveInfinity);
            Assert.Throws<StateFault>(()=>renderer.Apply(frame));
            Assert.That(registry.objects[0].root.localPosition,Is.EqualTo(Vector3.zero));
            Assert.That(registry.objects[0].currentState.cardFace,Is.Zero);
            Assert.That(registry.joints[0].link.localRotation,Is.EqualTo(Quaternion.identity));
        }
        [Test] public void ImportedNeutralMismatchIsRefused()
        { Assert.Throws<StateFault>(()=>renderer.VerifyImportedNeutral(Frame())); }
    }
}
