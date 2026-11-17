using System.Collections;
using System.Collections.Generic;
using System.Linq;
using AcousticVocab.ResponsePanel;
using AcousticVocab.Workcell;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine.TestTools;
using UnityEngine;
using System.Reflection;

namespace AcousticVocab.Orientation.PlayModeTests
{
    public sealed class OrientationFlowPlayTests
    {
        static OrientationPlan SyntheticPlan()
        {
            var targets=PublicCommands.Targets.ToArray();var actions=PublicCommands.Actions.ToArray();
            var value=JObject.FromObject(new {version=1,protocol_version="synthetic",content_status="protocol_reviewed",review_evidence_sha256=new string('a',64),source_note="Synthetic test only; no human eligibility evidence",practice_window_ms=1000,
                actions=actions.Select(x=>new {id=x,title=x,meaning="Synthetic action"}),targets=targets.Select(x=>new {id=x,title=x,meaning="Synthetic target"}),
                practice_pairs=targets.Select((x,i)=>new {id="p"+i,target=x,action=actions[i],request="Synthetic written request"}),second_order=Enumerable.Range(0,8).Reverse().Select(i=>"p"+i)});
            return OrientationPlan.Parse(value.ToString(),"synthetic");
        }
        // Eight synthetic boxes stand in for the workcell targets; the marker is the one OrientationHost uses.
        static OrientationTargetMarker SyntheticMarker(Transform scene)
        {
            var workcell=new GameObject("Synthetic workcell").AddComponent<WorkcellRegistry>();workcell.transform.SetParent(scene,false);
            workcell.objects=PublicCommands.Targets.Select((target,i)=>
            {
                var box=GameObject.CreatePrimitive(PrimitiveType.Cube).transform;box.SetParent(workcell.transform,false);box.localPosition=new Vector3(i*.2f,0,0);box.localScale=Vector3.one*.1f;
                return new ObjectBinding{id=OrientationTargetMarker.ObjectId(target),kind=PublicCommands.Family(target)==0?"tray":"container",label=target,root=box,visual=box};
            }).ToArray();
            var screen=new GameObject("Silent orientation screen").transform;screen.SetParent(scene,false);
            return new OrientationTargetMarker(workcell,screen,Shader.Find("Unlit/Color"));
        }
        IEnumerator Run(int firstWrong,int secondWrong,EligibilityCode expected)
        {
            double now=0;var rows=new List<JObject>();var flow=new OrientationFlow(SyntheticPlan(),()=>now,rows.Add);
            var scene=new GameObject("Synthetic orientation scene");var marker=SyntheticMarker(scene.transform);var marked=new List<string>();
            // As OrientationHost.Refresh does after every step: the object is marked only while its target card is shown.
            void Present()
            {
                marker.Present(flow);bool target=flow.Stage==OrientationStage.Target;
                Assert.That(marker.Pointer.gameObject.activeSelf,Is.EqualTo(target),flow.Stage.ToString());Assert.That(marker.MarkedTarget,Is.EqualTo(target?flow.CurrentCard.Id:null));
            }
            flow.Start();Present();
            for(int attempt=0;attempt<2;attempt++)
            {
                for(int i=0;i<8;i++) { now+=10000;flow.CompleteDemo(flow.CurrentCard.Id,10000,10000,1000d/30);flow.Next();Present();yield return null; }
                for(int i=0;i<8;i++) { marked.Add(marker.MarkedTarget);flow.Next();Present(); }
                Assert.That(marked.Skip(attempt*8),Is.EqualTo(PublicCommands.Targets),"Each target card marks its own object");
                for(int i=0;i<8;i++)
                {
                    Assert.That(flow.EligibleOutcomeRecorded,Is.False);var request=flow.OpenPractice();Present();var panel=new ResponseState(()=>now,_=>{});panel.Responded+=flow.Respond;panel.Open(request);
                    if(i==(attempt==0?firstWrong:secondWrong))panel.DontKnow();else {panel.SelectTarget(flow.CurrentItem.Target);panel.SelectAction(flow.CurrentItem.Action);panel.Commit();}
                    yield return null;Assert.That(flow.Stage,Is.EqualTo(OrientationStage.Feedback));Present();flow.Next();Present();
                }
                if(flow.Stage==OrientationStage.RecordedOutcome)break;flow.Next();Present();
            }
            Assert.That(marker.Pointer.gameObject.activeSelf,Is.False);marker.Dispose();Object.Destroy(scene);
            Assert.That(flow.Outcome.Code,Is.EqualTo(expected));Assert.That((string)rows.Last()["event"],Is.EqualTo("eligibility_outcome"));Assert.That(flow.EligibleOutcomeRecorded,Is.EqualTo(expected!=EligibilityCode.Fail));Assert.That(flow.Reexplanations,Is.LessThanOrEqualTo(1));
        }
        [UnityTest] public IEnumerator EightFirstUsesRealPanelStateAcrossFrames() => Run(-1,-1,EligibilityCode.PassFirst);
        [UnityTest] public IEnumerator SevenThenEightUsesOneReexplanation() => Run(7,-1,EligibilityCode.PassSecond);
        [UnityTest] public IEnumerator SevenThenSevenStopsBeforeAllocation() => Run(7,7,EligibilityCode.Fail);
        [UnityTest] public IEnumerator DisablingHostImmediatelyRevokesItsGateAndLatchesFault()
        {
            var gameObject=new GameObject("Synthetic orientation lifecycle test");gameObject.SetActive(false);
            var host=gameObject.AddComponent<OrientationHost>();var rows=new List<JObject>();var flow=new OrientationFlow(SyntheticPlan(),()=>0,rows.Add);
            typeof(OrientationHost).GetProperty("Flow").SetValue(host,flow);
            LogAssert.Expect(LogType.Error,"ORIENTATION_COMPONENT_DISABLED");
            typeof(OrientationHost).GetMethod("OnDisable",BindingFlags.NonPublic|BindingFlags.Instance).Invoke(host,null);
            Assert.That(flow.Stage,Is.EqualTo(OrientationStage.Fault));Assert.That(host.EligibleOutcomeRecorded,Is.False);
            Assert.That((string)rows.Last()["code"],Is.EqualTo("ORIENTATION_COMPONENT_DISABLED"));
            Object.Destroy(gameObject);yield return null;
        }
    }
}
