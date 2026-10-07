using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using AcousticVocab.StudyAudio;
using NUnit.Framework;
using UnityEngine.TestTools;

namespace AcousticVocab.SessionEngine.PlayModeTests
{
    // Unity frame/lifecycle fault injection with private durable files and mock
    // content. No audio, physical headset, renderer or reset device is asserted.
    public sealed class SessionLifecycleTests
    {
        sealed class Clock:ISessionClock { public double NowMs { get; set; } }
        sealed class Content:ISlotContent
        {
            public bool Focus=true,CompleteReset=true;
            public int Cues,Interrupts;
            public SlotReadiness Readiness=>new SlotReadiness(true,true,true,true,true,Focus,true,true);
            public bool ResetComplete { get; private set; }
            public void Prepare(SlotContext context){}
            public void RequestCue(SlotContext context,INovelSlotAuthorization permit){Cues++;}
            public void OpenResponse(SlotContext context){}
            public void CloseResponse(SlotContext context){}
            public void RequestReset(SlotContext context){ResetComplete=CompleteReset;}
            public void Interrupt(string code){Interrupts++;}
        }
        sealed class Factory:ISlotContentFactory
        {
            public readonly List<Content> Items=new List<Content>();
            public ISlotContent Create(SlotItem item){var content=new Content();Items.Add(content);return content;}
        }
        static VisitSchedule Schedule()=>new VisitSchedule(new string('a',64),new string('b',64),"DEMO","D0",true,
            new[]{new ScheduleBlock("trained",Enumerable.Range(0,2).Select(i=>new SlotItem("DEMO-"+i,"trained","K-a1-r1",null,null,"protected",false,14,1,1)).ToArray())});
        static string DirectoryPath()=>Path.Combine(Path.GetTempPath(),"session-play-"+Guid.NewGuid().ToString("N"));

        [UnityTest] public IEnumerator FocusLossBetweenFramesKeepsUncertainExposureAndStopsNextCue()
        {
            var clock=new Clock();var factory=new Factory();using var journal=new SessionJournal(DirectoryPath());
            var engine=new FixedSlotEngine(Schedule(),clock,journal,factory);engine.ConfirmResume();engine.Tick();
            Assert.That(factory.Items[0].Cues,Is.EqualTo(1));yield return null;
            factory.Items[0].Focus=false;clock.NowMs=100;engine.Tick();
            Assert.That(factory.Items[0].Interrupts,Is.GreaterThan(0));
            clock.NowMs=14750;engine.Tick();yield return null;engine.Tick();
            Assert.That(engine.Status,Is.EqualTo(SessionState.Paused));Assert.That(factory.Items.Count,Is.EqualTo(1));
            Assert.That(journal.Records.Last(x=>x.TrialId!=null).ExposureConsumed,Is.True);
            Assert.That(journal.Records.Any(x=>x.Event=="retry_queued"),Is.False);
        }

        [UnityTest] public IEnumerator DurableRequestSurvivesEngineRecreationWithoutReplayingConsumedItem()
        {
            string path=DirectoryPath();var firstClock=new Clock();var firstFactory=new Factory();
            using(var firstJournal=new SessionJournal(path))
            {
                var first=new FixedSlotEngine(Schedule(),firstClock,firstJournal,firstFactory);first.ConfirmResume();first.Tick();
                Assert.That(firstFactory.Items[0].Cues,Is.EqualTo(1));yield return null;
            }
            using var resumedJournal=new SessionJournal(path);var factory=new Factory();
            var resumed=new FixedSlotEngine(Schedule(),new Clock(),resumedJournal,factory);
            Assert.That(resumed.NeedsOperatorConfirmation,Is.True);Assert.That(factory.Items.Count,Is.Zero);yield return null;
            resumed.ConfirmResume();resumed.Tick();Assert.That(resumed.CurrentTrialId,Is.EqualTo("DEMO-1"));
            Assert.That(resumedJournal.Records.Where(x=>x.TrialId=="DEMO-0"&&x.Event=="state_before"&&x.State==ItemState.CueRequested).Count(),Is.EqualTo(1));
        }

        [UnityTest] public IEnumerator MissingNewResetAcknowledgmentBlocksTheFollowingSlot()
        {
            var clock=new Clock();var factory=new Factory();using var journal=new SessionJournal(DirectoryPath());
            var engine=new FixedSlotEngine(Schedule(),clock,journal,factory);engine.ConfirmResume();factory.Items[0].CompleteReset=false;engine.Tick();
            clock.NowMs=750;engine.Tick();engine.RecordResponse("commit");yield return null;
            clock.NowMs=12750;engine.Tick();yield return null;
            clock.NowMs=14750;engine.Tick();yield return null;engine.Tick();Assert.That(engine.Status,Is.EqualTo(SessionState.Paused));
            Assert.That(factory.Items.Count,Is.EqualTo(1));Assert.That(factory.Items[0].Cues,Is.EqualTo(1));
        }
    }
}
