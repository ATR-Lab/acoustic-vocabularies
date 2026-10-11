using System;
using System.Linq;
using System.Reflection;
using AcousticVocab.Assessment;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using NUnit.Framework;

namespace AcousticVocab.DataLogging.Tests
{
    // Native attempts simulation-test-A-D0-023-isaac2-019/-020: the first frame
    // after visit_complete spent 270.7/286.2 ms in the assessment boundary with
    // 2/3 full collections. Every SessionDataJournal.Records read re-decoded the
    // whole visit's session rows (1,640 in A D0), and the forms transition and
    // boundary checks read it several times per frame. These drive the real
    // engine, durable journal and assessment stages through that transition.
    public sealed class FormsTransitionDecodeTests
    {
        static T Construct<T>(params object[] values)=>(T)Activator.CreateInstance(typeof(T),BindingFlags.Instance|BindingFlags.NonPublic,null,values,null);
        sealed class Clock:ISessionClock{internal double Time;public double NowMs=>Time;}
        sealed class Content:ISlotContentFactory,ISlotContent
        {
            public ISlotContent Create(SlotItem item)=>this;
            public SlotReadiness Readiness=>new SlotReadiness(true,true,true,true,true,true,true,true);public bool ResetComplete=>true;
            public void Prepare(SlotContext c){}public void RequestCue(SlotContext c,INovelSlotAuthorization permit){}
            public void OpenResponse(SlotContext c){}public void CloseResponse(SlotContext c){}public void RequestReset(SlotContext c){}public void Interrupt(string code){}
        }
        [Test]public void CompletedVisitFormsReuseDecodedHistoryAndSeeEveryNewRecord()
        {
            string raw=SyntheticData.Folder("forms-transition");var clock=new Clock();
            using var writer=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>clock.Time);
            var items=Enumerable.Range(0,40).Select(i=>Construct<SlotItem>("SYNTHETIC-"+i,"atomic","synthetic-content",null,null,"protected",false,9,1,1)).ToArray();
            var schedule=Construct<VisitSchedule>(new string('b',64),new string('c',64),"SYNTHETIC","D0",true,new[]{Construct<ScheduleBlock>("atomic",items)},"A","A",null);
            foreach(var item in items)writer.Append(DataObservations.Opportunity(item.TrialId,"primary","synthetic-masked",schedule.Sha256));
            var session=new SessionDataJournal(writer);var ratings=new AssessmentDataJournal(writer,schedule.Sha256);
            var engine=new FixedSlotEngine(schedule,clock,session,new Content());engine.ConfirmResume();engine.Tick();
            while(engine.Status==SessionState.Running&&clock.Time<400000){clock.Time+=250;engine.Tick();}
            Assert.That(engine.Status,Is.EqualTo(SessionState.Complete));

            var history=session.Records;int sessionRows=writer.Records.Count(r=>r.Kind=="session");
            Assert.That(history.Count,Is.EqualTo(sessionRows));Assert.That(history.Last().Event,Is.EqualTo("visit_complete"));
            var stages=new AssessmentStages(schedule,session,ratings,clock,()=>true,true);
            // Per-frame boundary checks and the forms transition itself.
            for(int frame=0;frame<120;frame++)Assert.That(stages.ProtectedComplete,Is.True);
            clock.Time+=60;stages.BeginForms();
            while(stages.CurrentRating!=null){clock.Time+=1500;stages.Rate(stages.CurrentRating.Id,stages.CurrentRating.Minimum);}
            Assert.That(stages.FormsComplete,Is.True);

            var after=session.Records;
            Assert.That(after.Count,Is.EqualTo(history.Count),"Forms add no session rows");
            for(int i=0;i<history.Count;i++)Assert.That(after[i],Is.SameAs(history[i]),"Each durable session row is decoded once");
            Assert.That(after,Is.Not.SameAs(history),"Every read is an independent snapshot");
            var stageRows=ratings.Records;Assert.That(stageRows.First().EventKind,Is.EqualTo("forms_started"));Assert.That(stageRows.Last().EventKind,Is.EqualTo("forms_completed"));
            Assert.That(ratings.Records.Zip(stageRows,(a,b)=>ReferenceEquals(a,b)).All(x=>x),Is.True);

            // Rows appended later are still seen, decoded exactly as a fresh reader decodes them.
            engine.Fault("SYNTHETIC_LATE_FAULT");
            var grown=session.Records;var fresh=new SessionDataJournal(writer).Records;
            Assert.That(grown.Count,Is.EqualTo(fresh.Count));Assert.That(grown.Count,Is.GreaterThan(history.Count));
            for(int i=0;i<fresh.Count;i++)Assert.That(SessionRecordCodec.ToJson(grown[i]).ToString(),Is.EqualTo(SessionRecordCodec.ToJson(fresh[i]).ToString()));
            Assert.That(grown.Skip(history.Count).Any(r=>r.Event=="item_fault"&&r.TechnicalFaultCode=="SYNTHETIC_LATE_FAULT"),Is.True);
        }
    }
}
