using System;
using System.Collections.Generic;
using System.Linq;
using AcousticVocab.SessionEngine;
using NUnit.Framework;

namespace AcousticVocab.Teaching.Tests
{
    public sealed class LessonTimelineTests
    {
        const string Hash="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        static SlotContext Context(bool atomic=false,bool heldout=false,string id="DEMO-lesson")=>new SlotContext(new SlotItem(id,atomic?"atomic_lesson":"message_lesson",atomic?"K-a1":"K-a1-r1",atomic?"action":null,"structured","teaching",heldout,atomic?20:24,3,1),750,null);
        sealed class Run
        {
            internal readonly List<LessonEvent> Events=new List<LessonEvent>();
            internal readonly List<(int index,double at)> Plays=new List<(int,double)>();
            internal readonly LessonTimeline Timeline;
            internal readonly SlotContext Context;
            internal Run(bool atomic=false,bool aligned=true)
            {
                Context=LessonTimelineTests.Context(atomic);
                Timeline=new LessonTimeline(Context,aligned,24000,atomic?0:33600,"DEMO-meaning",Hash,atomic?null:Hash,atomic?null:Hash,Events.Add);
                Timeline.PlayRequested+=(index,id,at)=>Plays.Add((index,at));Timeline.Start(0);
            }
            internal void Complete(bool timeout=false)
            {
                double[] offsets=Context.Item.TrialType=="atomic_lesson"?new[]{0d,6000,14000}:new[]{0d,8000,18000};
                double duration=Context.Item.TrialType=="atomic_lesson"?500:1400;
                for(double now=5;now<=Context.EndMonoMs;now+=5)
                {
                    Timeline.Tick(now);
                    for(int i=0;i<3;i++)
                    {
                        if(now==750+offsets[i])Timeline.Onset(Context.AudioRequestIds[i],now,10,now);
                        if(now==750+offsets[i]+duration)Timeline.Completed(Context.AudioRequestIds[i],now);
                    }
                    if(now==750+(timeout?(Context.Item.ResponseClosesSeconds*1000+5):offsets[1]+100))Timeline.Response(timeout?"DEMO-timeout":"DEMO-correct",now,timeout);
                    if(now==750+Context.Item.ResponseClosesSeconds*1000)Timeline.RequestReset();
                }
            }
        }
        [TestCase(true)][TestCase(false)]public void ExactlyThreeFixedPlaysAndFullSlotDespiteEarlyResponse(bool atomic)
        {
            var run=new Run(atomic);run.Complete();
            Assert.That(run.Plays.Select(x=>x.at),Is.EqualTo(atomic?new[]{750d,6750,14750}:new[]{750d,8750,18750}));
            Assert.That(run.Events.Where(x=>x.Kind=="play_request").Select(x=>x.PresentationIndex),Is.EqualTo(new int?[]{1,2,3}));
            Assert.That(run.Events.Where(x=>x.Kind=="play_request").Select(x=>x.AudioRequestId).Distinct().Count(),Is.EqualTo(3));
            Assert.That(run.Events.Last(x=>x.Kind=="lesson_end").MonoMs,Is.EqualTo(run.Context.EndMonoMs));
            Assert.That(run.Timeline.ResetMayBegin,Is.True);
            Assert.That(run.Events.Count(x=>x.Kind=="retrieval_opportunity"),Is.EqualTo(1));
        }
        [Test]public void VariantsHaveIdenticalNonHighlightEvents()
        {
            var aligned=new Run();var dictionary=new Run(aligned:false);aligned.Complete();dictionary.Complete();
            string Key(LessonEvent e)=>string.Join("|",e.Kind,e.MonoMs,e.ExpectedMonoMs,e.PresentationIndex,e.MeaningDisplayId,e.FeedbackContentId,e.PcmSha256,e.ActionPcmSha256,e.ReferentPcmSha256);
            Assert.That(aligned.Events.Where(x=>!x.Kind.StartsWith("highlight",StringComparison.Ordinal)).Select(Key),Is.EqualTo(dictionary.Events.Select(Key)));
            Assert.That(aligned.Events.Count(x=>x.Kind=="highlight"),Is.EqualTo(8));Assert.That(dictionary.Events.Any(x=>x.Kind=="highlight"),Is.False);
        }
        [Test]public void HighlightsUseMeasuredAnchorAndAreOffForExactGapAndRetrieval()
        {
            var run=new Run();run.Timeline.Tick(750);run.Timeline.Onset(run.Context.AudioRequestIds[0],760,10,750);
            Assert.That(run.Timeline.Highlight,Is.EqualTo(LessonHighlight.None));run.Timeline.Tick(760);Assert.That(run.Timeline.Highlight,Is.EqualTo(LessonHighlight.Action));
            run.Timeline.Tick(1260);Assert.That(run.Timeline.Highlight,Is.EqualTo(LessonHighlight.None));run.Timeline.Tick(1459);Assert.That(run.Timeline.Highlight,Is.EqualTo(LessonHighlight.None));
            run.Timeline.Tick(1460);Assert.That(run.Timeline.Highlight,Is.EqualTo(LessonHighlight.Target));run.Timeline.Completed(run.Context.AudioRequestIds[0],2160);run.Timeline.Tick(2160);Assert.That(run.Timeline.Highlight,Is.EqualTo(LessonHighlight.None));
        }
        [Test]public void NoOnsetEvidenceDoesNotFallBackToRequestedOnset()
        {var run=new Run();run.Timeline.Tick(750);Assert.Throws<SessionFault>(()=>run.Timeline.Tick(1005));Assert.That(run.Timeline.Highlight,Is.EqualTo(LessonHighlight.None));}
        [TestCase(21d)][TestCase(double.NaN)][TestCase(double.PositiveInfinity)][TestCase(-1d)]public void UnqualifiedUncertaintyRefused(double uncertainty)
        {var run=new Run();Assert.Throws<SessionFault>(()=>run.Timeline.Onset(run.Context.AudioRequestIds[0],750,uncertainty,0));}
        [Test]public void LateScheduleDoesNotBackfillMissedPlay()
        {var context=Context();var rows=new List<LessonEvent>();var value=new LessonTimeline(context,true,24000,24000,"meaning",Hash,Hash,Hash,rows.Add);Assert.Throws<SessionFault>(()=>value.Start(601));Assert.That(rows,Is.Empty);}
        [Test]public void HeldoutLessonIsRefusedBeforeCue()
        {Assert.Throws<SessionFault>(()=>new LessonTimeline(Context(heldout:true),true,24000,24000,"meaning",Hash,Hash,Hash,_=>{}));}
        [TestCase(true)][TestCase(false)]public void InstructionalTimeoutPermittedOnFirstLateDeadlineFrame(bool atomic)
        {var run=new Run(atomic);run.Complete(true);Assert.That(run.Events.Single(x=>x.Kind=="retrieval_result").FeedbackContentId,Is.EqualTo("DEMO-timeout"));}
        [Test]public void ResetCannotBeginBeforeThirdCompletion()
        {var run=new Run();run.Timeline.RequestReset();Assert.That(run.Timeline.ResetMayBegin,Is.False);run.Timeline.Interrupt(1);Assert.That(run.Timeline.ResetMayBegin,Is.True);}
        [Test]public void InterruptConcealsAndPreventsLaterPlays()
        {var run=new Run();run.Timeline.Tick(750);run.Timeline.Onset(run.Context.AudioRequestIds[0],750,10,750);run.Timeline.Interrupt(800);run.Timeline.Tick(25000);Assert.That(run.Plays.Count,Is.EqualTo(1));Assert.That(run.Timeline.Highlight,Is.EqualTo(LessonHighlight.None));Assert.That(run.Timeline.Ended,Is.True);}
        [Test]public void SecondResponseIsRefused()
        {var run=new Run();run.Complete();Assert.Throws<SessionFault>(()=>run.Timeline.Response("other",24750));}
        [Test]public void CompletedAudioDoesNotAllowSkippingFeedbackInterval()
        {var run=new Run();run.Timeline.Tick(750);run.Timeline.Onset(run.Context.AudioRequestIds[0],750,10,750);run.Timeline.Completed(run.Context.AudioRequestIds[0],2150);Assert.Throws<SessionFault>(()=>run.Timeline.Tick(25000));}
        [TestCase(16,36,48,108)][TestCase(8,8,24,24)][TestCase(4,12,12,36)][TestCase(4,16,12,48)]
        public void MockCurriculumCountsUseAllThreePlays(int atoms,int messages,int atomPlays,int messagePlays)
        {int a=0,m=0;for(int i=0;i<atoms+messages;i++){var run=new Run(i<atoms);run.Complete();if(i<atoms)a+=run.Plays.Count;else m+=run.Plays.Count;}Assert.That(a,Is.EqualTo(atomPlays));Assert.That(m,Is.EqualTo(messagePlays));}
    }
}
