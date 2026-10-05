using System.Collections;
using System.Collections.Generic;
using System.Linq;
using AcousticVocab.ResponsePanel;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine.TestTools;

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
        IEnumerator Run(int firstWrong,int secondWrong,EligibilityCode expected)
        {
            double now=0;var rows=new List<JObject>();var flow=new OrientationFlow(SyntheticPlan(),()=>now,rows.Add);flow.Start();
            for(int attempt=0;attempt<2;attempt++)
            {
                for(int i=0;i<8;i++) { now+=10000;flow.CompleteDemo(flow.CurrentCard.Id,10000,10000,1000d/30);flow.Next();yield return null; }
                for(int i=0;i<8;i++)flow.Next();
                for(int i=0;i<8;i++)
                {
                    Assert.That(flow.EligibleOutcomeRecorded,Is.False);var request=flow.OpenPractice();var panel=new ResponseState(()=>now,_=>{});panel.Responded+=flow.Respond;panel.Open(request);
                    if(i==(attempt==0?firstWrong:secondWrong))panel.DontKnow();else {panel.SelectTarget(flow.CurrentItem.Target);panel.SelectAction(flow.CurrentItem.Action);panel.Commit();}
                    yield return null;Assert.That(flow.Stage,Is.EqualTo(OrientationStage.Feedback));flow.Next();
                }
                if(flow.Stage==OrientationStage.RecordedOutcome)break;flow.Next();
            }
            Assert.That(flow.Outcome.Code,Is.EqualTo(expected));Assert.That((string)rows.Last()["event"],Is.EqualTo("eligibility_outcome"));Assert.That(flow.EligibleOutcomeRecorded,Is.EqualTo(expected!=EligibilityCode.Fail));Assert.That(flow.Reexplanations,Is.LessThanOrEqualTo(1));
        }
        [UnityTest] public IEnumerator EightFirstUsesRealPanelStateAcrossFrames() => Run(-1,-1,EligibilityCode.PassFirst);
        [UnityTest] public IEnumerator SevenThenEightUsesOneReexplanation() => Run(7,-1,EligibilityCode.PassSecond);
        [UnityTest] public IEnumerator SevenThenSevenStopsBeforeAllocation() => Run(7,7,EligibilityCode.Fail);
    }
}
