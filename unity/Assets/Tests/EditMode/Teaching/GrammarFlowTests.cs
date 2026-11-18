using System.Collections.Generic;
using NUnit.Framework;
using AcousticVocab.SessionEngine;

namespace AcousticVocab.Teaching.Tests
{
    public sealed class GrammarFlowTests
    {
        [Test]public void ExactlyExplanationThenReadyThenClicksWithoutReplay()
        {
            var events=new List<string>();var plays=new List<string>();var flow=new GrammarFlow((kind,id,now)=>events.Add(kind));flow.Play+=(id,time)=>plays.Add(id);
            flow.Tick(0);flow.Tick(500);Assert.That(plays,Is.EqualTo(new[]{flow.ExplanationId}));
            flow.Completed(flow.ExplanationId,20750);flow.Tick(20760);Assert.That(plays,Is.EqualTo(new[]{flow.ExplanationId,flow.ReadyId}));
            flow.Completed(flow.ReadyId,21830);flow.Tick(21840);Assert.That(plays,Is.EqualTo(new[]{flow.ExplanationId,flow.ReadyId,flow.ClicksId}));
            flow.Completed(flow.ClicksId,22878);flow.Tick(30000);Assert.That(flow.Complete,Is.True);Assert.That(plays.Count,Is.EqualTo(3));
        }
        [Test]public void MissingReadyCompletionNeverSchedulesClicks()
        {var flow=new GrammarFlow((kind,id,now)=>{});flow.Tick(0);flow.Completed(flow.ExplanationId,100);flow.Tick(200);flow.Tick(10000);Assert.That(flow.Requests,Is.EqualTo(2));Assert.That(flow.Complete,Is.False);}
        [Test]public void OutOfOrderCompletionRefused()
        {var flow=new GrammarFlow((kind,id,now)=>{});flow.Tick(0);Assert.Throws<SessionFault>(()=>flow.Completed(flow.ClicksId,100));}
        [Test]public void DurableFailurePreventsPlay()
        {int count=0;var flow=new GrammarFlow((kind,id,now)=>throw new System.IO.IOException());flow.Play+=(id,time)=>count++;Assert.Throws<System.IO.IOException>(()=>flow.Tick(0));Assert.That(count,Is.Zero);}
        [Test]public void AbortHidesAndCannotReplay()
        {string display=null;var flow=new GrammarFlow((kind,id,now)=>{});flow.Display+=value=>display=value;flow.Tick(0);flow.Abort(5);flow.Tick(10000);Assert.That(display,Is.EqualTo("hidden"));Assert.That(flow.Requests,Is.EqualTo(1));Assert.That(flow.Complete,Is.False);}

        [Test, Category("REQ_GRAM_01"), NUnit.Framework.Property("Requirement", "REQ-GRAM-01")]
        public void SpokenExplanationPlaysOnceBeforeTheReadyCueWithNoLabels()
        {
            var plays=new List<string>();var displays=new List<string>();var flow=new GrammarFlow((kind,id,now)=>{});
            flow.Play+=(id,time)=>plays.Add(id);flow.Display+=displays.Add;
            flow.Tick(0);Assert.That(plays,Is.EqualTo(new[]{flow.ExplanationId}));Assert.That(displays,Is.EqualTo(new[]{"explanation"}));
            // No replay or READY while the explanation is still playing.
            for(double now=1;now<60000;now+=997)flow.Tick(now);Assert.That(plays,Is.EqualTo(new[]{flow.ExplanationId}));
            Assert.That(Assert.Throws<SessionFault>(()=>flow.Completed(flow.ReadyId,60000)).Code,Is.EqualTo("GRAMMAR_COMPLETION"));
            flow.Completed(flow.ExplanationId,60000);flow.Tick(60001);flow.Completed(flow.ReadyId,61000);flow.Tick(61001);flow.Completed(flow.ClicksId,62000);
            Assert.That(plays,Is.EqualTo(new[]{flow.ExplanationId,flow.ReadyId,flow.ClicksId}));Assert.That(displays,Is.EqualTo(new[]{"explanation","ready","roles","hidden"}));
            Assert.That(flow.Complete,Is.True);
        }
        [Test, Category("REQ_GRAM_03"), NUnit.Framework.Property("Requirement", "REQ-GRAM-03")]
        public void ExplanationRequestAndCompletionArePersistedBeforeReadyAndAnAbortedExplanationNeverCompletes()
        {
            var rows=new List<(string kind,string id,double at)>();var flow=new GrammarFlow((kind,id,now)=>rows.Add((kind,id,now)));double? scheduled=null;flow.Play+=(id,time)=>scheduled??=time;
            flow.Tick(10);Assert.That(rows,Is.EqualTo(new[]{("grammar_request",flow.ExplanationId,10.0)}));Assert.That(scheduled,Is.EqualTo(760));
            flow.Completed(flow.ExplanationId,25000);flow.Tick(25001);
            Assert.That(rows.GetRange(1,2),Is.EqualTo(new[]{("grammar_complete",flow.ExplanationId,25000.0),("grammar_request",flow.ReadyId,25001.0)}));
            var aborted=new GrammarFlow((kind,id,now)=>{});aborted.Tick(0);aborted.Abort(5);
            Assert.Throws<SessionFault>(()=>aborted.Completed(aborted.ExplanationId,6));aborted.Tick(10000);Assert.That(aborted.Requests,Is.EqualTo(1));Assert.That(aborted.Complete,Is.False);
        }
    }
}
