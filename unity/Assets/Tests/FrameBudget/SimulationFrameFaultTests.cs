using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using AcousticVocab.DataLogging;
using NUnit.Framework;

namespace AcousticVocab.FrameBudget.Tests
{
    // #81 presentation_stall and input_loss: the injected main-thread stall or
    // untracked input is detected by the ordinary monitor and becomes exactly
    // the typed device cause row tools/mock_visit/faults.py accepts.
    public sealed class SimulationFrameFaultTests
    {
        sealed class Memory : IFrameEvidence
        {
            internal readonly List<FrameFaultRecord> Faults=new List<FrameFaultRecord>();
            public void Interval(FrameInterval x){}public void Fault(FrameFaultRecord x)=>Faults.Add(x);public void Summary(FrameSummary x){}
        }
        static DataIdentity Identity=>new DataIdentity(new string('1',32),"DEMO-fault","D0","station-01","simulation-test-v1",new string('a',64));
        string root;
        [SetUp]public void Setup(){root=Path.Combine(Path.GetTempPath(),".local","simulation-frame-fault-"+Guid.NewGuid().ToString("N"));}
        [TearDown]public void Cleanup(){if(Directory.Exists(root))Directory.Delete(root,true);}
        static FrameMonitor Planned(Memory e)
        {
            var m=new FrameMonitor(72,e);m.Render(0,new RenderSample(0),true);
            m.Register(new FrameAttempt("DEMO-novel-01","DEMO-novel-01",100,14100,1),new FrameWindow("response","response",1100,13100),0);return m;
        }
        [Test]public void InjectedFourHundredMillisecondStallBecomesDurableFrameFreezeCauseAndEngineFault()
        {
            var e=new Memory();var m=Planned(e);var codes=new List<string>();
            m.Render(1095,new RenderSample(1),true);m.Render(1110,new RenderSample(2),true);m.Render(1510,new RenderSample(3),true); // NativeFaultTargets.StallMs
            using var journal=new DataJournal(root,Identity,Guid.NewGuid().ToString("N"),()=>3000);
            new FrameDataAdapter(journal,codes.Add).Drain(m);
            var row=journal.Records.Single(r=>r.Kind=="device");var p=row.Payload;
            Assert.That((string)p["kind"],Is.EqualTo("frame_freeze"));Assert.That((string)p["code"],Is.EqualTo("FRAME_FREEZE"));Assert.That((double)p["duration_ms"],Is.EqualTo(400));
            Assert.That(row.Context.OpportunityId,Is.EqualTo("DEMO-novel-01"));Assert.That(codes,Is.EqualTo(new[]{"FRAME_FREEZE"}));Assert.That(m.Healthy,Is.False);
        }
        [Test]public void SuppressedInputInsideTheResponseWindowBecomesDurableInputFalseCause()
        {
            var e=new Memory();var m=Planned(e);var codes=new List<string>();
            m.Render(1095,new RenderSample(1),true);m.Render(1110,new RenderSample(2),false);
            using var journal=new DataJournal(root,Identity,Guid.NewGuid().ToString("N"),()=>3000);
            new FrameDataAdapter(journal,codes.Add).Drain(m);
            var p=journal.Records.Single(r=>r.Kind=="device").Payload;
            Assert.That((string)p["kind"],Is.EqualTo("input"));Assert.That((bool)p["value"],Is.False);Assert.That((string)p["code"],Is.EqualTo("FRAME_INTERFACE_UNAVAILABLE"));
            Assert.That(codes,Is.EqualTo(new[]{"FRAME_INTERFACE_UNAVAILABLE"}));
        }
    }
}
