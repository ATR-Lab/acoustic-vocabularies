using System;
using System.Collections.Generic;
using System.Linq;
using AcousticVocab.StudyAudio;
using NUnit.Framework;

namespace AcousticVocab.SessionEngine.Tests
{
    // The engine half of each #81 software injection: whatever hook caused the
    // fault, the item keeps its consumed exposure, no new cue is requested, the
    // session pauses at the fixed slot end and only an explicit resume moves on.
    public sealed class InjectedFaultHandlingTests
    {
        const string Hash="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        const string Package="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
        sealed class Clock:ISessionClock{public double Time;public double NowMs=>Time;}
        sealed class Memory:ISessionJournal{public readonly List<SessionRecord> Rows=new List<SessionRecord>();public IReadOnlyList<SessionRecord> Records=>Rows;public void Append(SessionRecord value)=>Rows.Add(value);}
        sealed class Content:ISlotContent
        {
            public bool InputOk=true,CompleteReset=true;public Exception CueFault;public int Cues,Interrupts;public string Id;
            public SlotReadiness Readiness=>new SlotReadiness(true,true,true,true,true,true,InputOk,true);
            public bool ResetComplete{get;private set;}
            public void Prepare(SlotContext context){Id=context.Item.TrialId;}
            public void RequestCue(SlotContext context,INovelSlotAuthorization permit){Cues++;if(CueFault!=null)throw CueFault;}
            public void OpenResponse(SlotContext context){}public void CloseResponse(SlotContext context){}
            public void RequestReset(SlotContext context){ResetComplete=CompleteReset;}
            public void Interrupt(string code){Interrupts++;}
        }
        sealed class Factory:ISlotContentFactory{public readonly List<Content> Items=new List<Content>();public Action<Content> Configure;public ISlotContent Create(SlotItem item){var c=new Content();Configure?.Invoke(c);Items.Add(c);return c;}}
        Clock clock;Memory journal;Factory factory;FixedSlotEngine engine;
        [SetUp]public void Setup()
        {
            clock=new Clock();journal=new Memory();factory=new Factory();
            var items=Enumerable.Range(0,3).Select(i=>new SlotItem("DEMO-"+i,"trained","K-a1-r1",null,null,"protected",false,14,1,1)).ToArray();
            engine=new FixedSlotEngine(new VisitSchedule(Hash,Package,"DEMO","D0",true,new[]{new ScheduleBlock("trained",items)}),clock,journal,factory);
        }
        void Run(double until){while(clock.Time<until&&engine.Status==SessionState.Running){clock.Time+=50;engine.Tick();}}
        void AssertPausedWithoutReplay(string code)
        {
            Assert.That(journal.Rows.Where(r=>r.Event=="item_fault").Select(r=>r.TechnicalFaultCode),Does.Contain(code));
            Assert.That(engine.Status,Is.EqualTo(SessionState.Paused));Assert.That(engine.NeedsOperatorConfirmation,Is.True);
            var faulted=journal.Rows.Where(r=>r.TrialId=="DEMO-0"&&r.Event!="state_before").ToList();
            Assert.That(faulted.SkipWhile(r=>!r.ExposureConsumed).All(r=>r.ExposureConsumed&&r.AudibleStatus==AudibleStatus.Uncertain),Is.True,"The injected item stays consumed");
            Assert.That(factory.Items.Count,Is.EqualTo(1),"No later cue is prepared before an explicit resume");Assert.That(factory.Items[0].Cues,Is.EqualTo(1));
            var paused=journal.Rows.FindLastIndex(r=>r.Event=="session_paused");Assert.That(paused,Is.GreaterThan(journal.Rows.FindIndex(r=>r.Event=="item_fault")));
            Assert.That(journal.Rows[paused].MonoMs,Is.GreaterThanOrEqualTo(750+14000),"The fixed slot tail is retained");
            engine.ConfirmResume();engine.Tick();Assert.That(engine.CurrentTrialId,Is.EqualTo("DEMO-1"),"Resume starts at the next unplayed item");
            Assert.That(factory.Items.Count(x=>x.Id=="DEMO-0"),Is.EqualTo(1),"The consumed item is never replayed as unheard");
        }
        [Test]public void CorruptedHashAtCueKeepsConsumedExposureAndPauses()
        {
            factory.Configure=c=>{if(factory.Items.Count==0)c.CueFault=new AudioIntegrityException();};
            engine.ConfirmResume();engine.Tick();Run(16000);AssertPausedWithoutReplay("HASH_MISMATCH");
        }
        [Test]public void WithheldResetReplyMissesTheDeadlineAndPausesAfterTheItem()
        {
            factory.Configure=c=>{if(factory.Items.Count==0)c.CompleteReset=false;};
            engine.ConfirmResume();engine.Tick();Run(16000);
            Assert.That(journal.Rows.Any(r=>r.TechnicalFaultCode=="SESSION_RESET_DEADLINE_MISSED"),Is.True);
            Assert.That(engine.Status,Is.EqualTo(SessionState.Paused));Assert.That(factory.Items.Count,Is.EqualTo(1));
            engine.ConfirmResume();engine.Tick();Assert.That(engine.CurrentTrialId,Is.EqualTo("DEMO-1"));
        }
        [Test]public void InputLossAfterCueFaultsWithoutAnotherCue()
        {
            engine.ConfirmResume();engine.Tick();clock.Time=2000;factory.Items[0].InputOk=false;engine.Tick();Run(16000);
            AssertPausedWithoutReplay("SESSION_FOCUS_OR_INPUT_LOST");
        }
        [TestCase("AUDIO_UNDERRUN")][TestCase("FRAME_FREEZE")][TestCase("JOIN_FOCUS_LOST")]
        public void HostReportedFaultDuringResponseKeepsTheConsumedItem(string code)
        {
            engine.ConfirmResume();engine.Tick();clock.Time=2000;engine.Tick();engine.Fault(code);Run(16000);
            AssertPausedWithoutReplay(code);
        }
    }
}
