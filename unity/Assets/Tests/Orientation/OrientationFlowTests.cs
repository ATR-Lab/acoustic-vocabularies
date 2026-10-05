using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using AcousticVocab.Foundation.Editor;
using AcousticVocab.ResponsePanel;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Orientation.Tests
{
    public sealed class OrientationFlowTests
    {
        double now;OrientationFlow flow;List<JObject> rows;
        static JObject Example=>JObject.Parse(File.ReadAllText(Path.Combine(FoundationBuild.RepositoryRoot,"apparatus/orientation/orientation-plan.example.json")));
        static OrientationPlan Plan(bool draft=false)
        { var v=Example;if(!draft){v["content_status"]="protocol_reviewed";v["review_evidence_sha256"]=new string('a',64);}return OrientationPlan.Parse(v.ToString(),"engineering-pending-review"); }
        void Setup(bool draft=false,Action<JObject> sink=null)
        { now=0;rows=new List<JObject>();flow=new OrientationFlow(Plan(draft),()=>now,sink??rows.Add); }
        void Introduction(bool start)
        {
            if(start)flow.Start();
            for(int i=0;i<8;i++) { Assert.That(flow.Stage,Is.EqualTo(OrientationStage.Action));now+=10000;flow.CompleteDemo(flow.CurrentCard.Id,10000,10000,1000d/30);flow.Next(); }
            for(int i=0;i<8;i++) { Assert.That(flow.Stage,Is.EqualTo(OrientationStage.Target));flow.Next(); }
            Assert.That(flow.Stage,Is.EqualTo(OrientationStage.Practice));
        }
        void Check(int wrong=-1)
        {
            for(int i=0;i<8;i++)
            {
                var item=flow.CurrentItem;var request=flow.OpenPractice();var panel=new ResponseState(()=>now,_=>{});panel.Responded+=flow.Respond;panel.Open(request);
                string target=i==wrong?PublicCommands.Targets[(PublicCommands.Index(PublicCommands.Targets,item.Target)/4)*4+(PublicCommands.Index(PublicCommands.Targets,item.Target)+1)%4]:item.Target;
                panel.SelectTarget(target);panel.SelectAction(item.Action);now+=1;Assert.That(panel.Commit(),Is.True);Assert.That(flow.Stage,Is.EqualTo(OrientationStage.Feedback));flow.Next();
            }
        }
        [Test] public void EightFirstPassesAfterDurableOutcomeBeforeAnyEligibilityGrant()
        {
            Setup();bool notified=false;flow.OutcomeRecorded+=r=> { Assert.That((string)rows.Last()["event"],Is.EqualTo("eligibility_outcome"));Assert.That(flow.EligibleOutcomeRecorded,Is.True);notified=true; };
            Assert.That(flow.EligibleOutcomeRecorded,Is.False);Introduction(true);Check();
            Assert.That(flow.Outcome.Code,Is.EqualTo(EligibilityCode.PassFirst));Assert.That(flow.Reexplanations,Is.Zero);Assert.That(notified,Is.True);Assert.That(flow.Outcome.FirstCorrect.Count,Is.EqualTo(8));
        }
        [TestCase(0)] [TestCase(1)] [TestCase(2)] [TestCase(3)] [TestCase(4)] [TestCase(5)] [TestCase(6)] [TestCase(7)]
        public void SevenThenEightPassesSecondWithOneIdenticalReexplanation(int wrong)
        {
            Setup();Introduction(true);Check(wrong);Assert.That(flow.Stage,Is.EqualTo(OrientationStage.Reexplanation));Assert.That(flow.Outcome,Is.Null);Assert.That(flow.EligibleOutcomeRecorded,Is.False);
            flow.Next();Introduction(false);Check();Assert.That(flow.Outcome.Code,Is.EqualTo(EligibilityCode.PassSecond));Assert.That(flow.Reexplanations,Is.EqualTo(1));Assert.That(flow.EligibleOutcomeRecorded,Is.True);
            Assert.That(rows.Count(x=>(string)x["event"]=="standard_reexplanation"),Is.EqualTo(1));
            var actions=rows.Where(x=>(string)x["event"]=="action_screen").Select(x=>(string)x["action"]).ToArray();Assert.That(actions.Take(8),Is.EqualTo(actions.Skip(8)));
            Assert.Throws<OrientationFault>(()=>flow.Next());
        }
        [Test] public void SevenThenSevenFailsWithoutThirdCheckOrAllocation()
        {
            Setup();Introduction(true);Check(7);flow.Next();Introduction(false);Check(0);Assert.That(flow.Outcome.Code,Is.EqualTo(EligibilityCode.Fail));Assert.That(flow.EligibleOutcomeRecorded,Is.False);Assert.That(flow.Reexplanations,Is.EqualTo(1));Assert.Throws<OrientationFault>(()=>flow.Next());
        }
        [Test] public void DraftSuccessIsRecordedButNeverAuthorizesAllocation()
        { Setup(true);Introduction(true);Check();Assert.That(flow.Outcome.Passed,Is.True);Assert.That(flow.Outcome.EngineeringDraft,Is.True);Assert.That(flow.EligibleOutcomeRecorded,Is.False); }
        [Test] public void FailedOutcomeWriteCannotPublishOrGrantEligibility()
        {
            bool notified=false;Setup(false,row=> { if((string)row["event"]=="eligibility_outcome")throw new IOException("synthetic"); });flow.OutcomeRecorded+=_=>notified=true;Introduction(true);
            Assert.Throws<OrientationFault>(()=>Check());Assert.That(flow.Stage,Is.EqualTo(OrientationStage.Fault));Assert.That(flow.Outcome,Is.Null);Assert.That(flow.EligibleOutcomeRecorded,Is.False);Assert.That(notified,Is.False);
        }
        [Test] public void MissingOrShortDemoCannotAdvanceToPractice()
        {
            Setup();flow.Start();Assert.Throws<OrientationFault>(()=>flow.Next());now=9999;flow.CompleteDemo(flow.CurrentCard.Id,9999,10000,1000d/30);Assert.That(flow.Stage,Is.EqualTo(OrientationStage.Fault));Assert.That(flow.Outcome,Is.Null);
        }
        [Test] public void LateDemoCompletionIsNotSilentlyTimeCompressed()
        { Setup();flow.Start();now=10100;flow.CompleteDemo(flow.CurrentCard.Id,10100,10000,1000d/30);Assert.That(flow.Stage,Is.EqualTo(OrientationStage.Fault)); }
        [Test] public void TimeoutAndDontKnowAreIncorrectPracticeResponses()
        {
            Setup();Introduction(true);var request=flow.OpenPractice();var panel=new ResponseState(()=>now,_=>{});panel.Responded+=flow.Respond;panel.Open(request);panel.DontKnow();Assert.That(flow.LastCorrect,Is.False);flow.Next();
            request=flow.OpenPractice();panel=new ResponseState(()=>now,_=>{});panel.Responded+=flow.Respond;panel.Open(request);now=request.DeadlineMonoMs;panel.Tick();Assert.That(flow.LastCorrect,Is.False);
        }
        [Test] public void ListsCoverEveryActionAndTargetAndHaveStoredDifferentOrder()
        {
            var plan=Plan();Assert.That(plan.FirstCheck.Select(x=>x.Action).Distinct().Count(),Is.EqualTo(8));Assert.That(plan.FirstCheck.Select(x=>x.Target).Distinct().Count(),Is.EqualTo(8));Assert.That(plan.FirstCheck.All(x=>PublicCommands.Legal(x.Target,x.Action)),Is.True);
            Assert.That(plan.SecondCheck.Select(x=>x.Id).OrderBy(x=>x),Is.EqualTo(plan.FirstCheck.Select(x=>x.Id).OrderBy(x=>x)));Assert.That(plan.SecondCheck.Select(x=>x.Id),Is.Not.EqualTo(plan.FirstCheck.Select(x=>x.Id)));
        }
        [Test] public void UnknownFieldsIllegalPairRepeatedOrderAndMissingReviewAreRejected()
        {
            var v=Example;v["unknown"]=true;Assert.Throws<OrientationFault>(()=>OrientationPlan.Parse(v.ToString(),"engineering-pending-review"));
            v=Example;v["practice_pairs"][0]["target"]="E";Assert.Throws<OrientationFault>(()=>OrientationPlan.Parse(v.ToString(),"engineering-pending-review"));
            v=Example;v["second_order"][0]=v["second_order"][1];Assert.Throws<OrientationFault>(()=>OrientationPlan.Parse(v.ToString(),"engineering-pending-review"));
            v=Example;v["content_status"]="protocol_reviewed";Assert.Throws<OrientationFault>(()=>OrientationPlan.Parse(v.ToString(),"engineering-pending-review"));
        }
        [Test] public void SilentAssemblyHasNoStudyAudioOrPackageDependency()
        {
            var names=typeof(OrientationFlow).Assembly.GetReferencedAssemblies().Select(x=>x.Name).ToArray();
            Assert.That(names.Any(x=>x.Contains("StudyAudio")||x.Contains("Package")),Is.False);
            string runtime=Path.Combine(FoundationBuild.RepositoryRoot,"unity/Assets/ExperimentApp/Runtime/Orientation");
            foreach(string file in Directory.GetFiles(runtime,"*.cs"))
            { string source=File.ReadAllText(file);Assert.That(source.Contains("AudioSource")||source.Contains("AudioClip")||source.Contains("PlayScheduled")||source.Contains("PackageLoader"),Is.False,file); }
        }
    }
}
