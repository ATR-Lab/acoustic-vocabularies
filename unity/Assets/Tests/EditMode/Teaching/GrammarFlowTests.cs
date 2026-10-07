using System.Collections.Generic;
using NUnit.Framework;
using AcousticVocab.SessionEngine;

namespace AcousticVocab.Teaching.Tests
{
    public sealed class GrammarFlowTests
    {
        [Test]public void ExactlyReadyThenClicksWithoutReplay()
        {var events=new List<string>();var plays=new List<string>();var flow=new GrammarFlow((kind,id,now)=>events.Add(kind));flow.Play+=(id,time)=>plays.Add(id);flow.Tick(0);flow.Tick(500);Assert.That(plays.Count,Is.EqualTo(1));flow.Completed(flow.ReadyId,1070);flow.Tick(1080);Assert.That(plays,Is.EqualTo(new[]{flow.ReadyId,flow.ClicksId}));flow.Completed(flow.ClicksId,2118);flow.Tick(10000);Assert.That(flow.Complete,Is.True);Assert.That(plays.Count,Is.EqualTo(2));}
        [Test]public void MissingReadyCompletionNeverSchedulesClicks()
        {var flow=new GrammarFlow((kind,id,now)=>{});flow.Tick(0);flow.Tick(10000);Assert.That(flow.Requests,Is.EqualTo(1));Assert.That(flow.Complete,Is.False);}
        [Test]public void OutOfOrderCompletionRefused()
        {var flow=new GrammarFlow((kind,id,now)=>{});flow.Tick(0);Assert.Throws<SessionFault>(()=>flow.Completed(flow.ClicksId,100));}
        [Test]public void DurableFailurePreventsPlay()
        {int count=0;var flow=new GrammarFlow((kind,id,now)=>throw new System.IO.IOException());flow.Play+=(id,time)=>count++;Assert.Throws<System.IO.IOException>(()=>flow.Tick(0));Assert.That(count,Is.Zero);}
        [Test]public void AbortHidesAndCannotReplay()
        {string display=null;var flow=new GrammarFlow((kind,id,now)=>{});flow.Display+=value=>display=value;flow.Tick(0);flow.Abort(5);flow.Tick(10000);Assert.That(display,Is.EqualTo("hidden"));Assert.That(flow.Requests,Is.EqualTo(1));Assert.That(flow.Complete,Is.False);}
    }
}
