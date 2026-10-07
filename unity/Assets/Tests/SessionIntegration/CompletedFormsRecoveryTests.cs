using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using AcousticVocab.Assessment;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
namespace AcousticVocab.SessionIntegration.Tests
{
    public sealed class CompletedFormsRecoveryTests
    {
        static T New<T>(params object[] args)=>(T)Activator.CreateInstance(typeof(T),BindingFlags.Instance|BindingFlags.NonPublic,null,args,null);
        sealed class Clock:ISessionClock{public double NowMs{get;set;}}
        sealed class Journal:ISessionJournal{public readonly List<SessionRecord> Rows=new List<SessionRecord>();public IReadOnlyList<SessionRecord> Records=>Rows;public void Append(SessionRecord record)=>Rows.Add(record);}
        sealed class Ratings:IAssessmentJournal{public readonly List<AssessmentRecord> Rows=new List<AssessmentRecord>();public IReadOnlyList<AssessmentRecord> Records=>Rows;public void Append(AssessmentRecord row)=>Rows.Add(row);}
        sealed class Factory:ISlotContentFactory,ISlotContent
        {
            public int Requests;public ISlotContent Create(SlotItem item)=>this;
            public SlotReadiness Readiness=>new SlotReadiness(true,true,true,true,true,true,true,true);public bool ResetComplete=>true;
            public void Prepare(SlotContext c){}public void RequestCue(SlotContext c,INovelSlotAuthorization p){Requests++;}
            public void OpenResponse(SlotContext c){}public void CloseResponse(SlotContext c){}public void RequestReset(SlotContext c){}public void Interrupt(string code){}
        }
        sealed class View:IDisposable{public int Closed;public void Dispose(){Closed++;}}
        sealed class Fixture
        {
            internal readonly Clock Clock=new Clock();internal readonly Journal Journal=new Journal();internal readonly Ratings Ratings=new Ratings();internal readonly Factory Factory=new Factory();
            internal readonly VisitSchedule Schedule;internal readonly FixedSlotEngine Engine;
            internal Fixture()
            {
                var item=New<SlotItem>("DEMO-recovery","atomic","K-a1","action","atomic","protected",false,9,1,1);
                Schedule=New<VisitSchedule>(new string('a',64),new string('b',64),"DEMO-person","D0",true,new[]{New<ScheduleBlock>("atomic",new[]{item})},"A","S",null);
                Engine=new FixedSlotEngine(Schedule,Clock,Journal,Factory);Engine.ConfirmResume();
                for(int n=0;n<=9750;n+=250){Clock.NowMs=n;Engine.Tick();}
                Assert.That(Engine.Status,Is.EqualTo(SessionState.Complete));
            }
            internal CompletedFormsRecovery Recover(Func<AssessmentStages,IDisposable> view,bool reviewed=true)=>new CompletedFormsRecovery(Schedule,Engine,Clock,Journal,Ratings,reviewed,view);
        }
        [Test]public void CompletedVisitPresentsOnlyRemainingRatingsWithoutNewTrialOrCue()
        {
            var f=new Fixture();var initial=new AssessmentStages(f.Schedule,f.Journal,f.Ratings,f.Clock,()=>true,true);initial.BeginForms();initial.Rate(initial.CurrentRating.Id,1);
            int records=f.Journal.Rows.Count,requests=f.Factory.Requests,shown=0;var view=new View();
            using(var recovery=f.Recover(stages=>{shown++;Assert.That(stages.CurrentRating.Id,Is.EqualTo("pleasantness"));return view;}))
            {recovery.Pump();recovery.Pump();Assert.That(shown,Is.EqualTo(1));Assert.That(recovery.Complete,Is.False);recovery.Stages.Rate("pleasantness",2);recovery.Pump();Assert.That(recovery.Complete,Is.True);}
            Assert.That(view.Closed,Is.EqualTo(1));Assert.That(f.Factory.Requests,Is.EqualTo(requests));Assert.That(f.Journal.Rows.Count,Is.EqualTo(records));Assert.That(f.Ratings.Rows.Count(r=>r.EventKind=="rating"),Is.EqualTo(2));
        }
        [Test]public void NewFormsBeginOnceAndCompletedFormsNeverCreateAView()
        {
            var f=new Fixture();int shown=0;
            using(var recovery=f.Recover(stages=>{shown++;stages.BeginForms();return new View();}))
            {recovery.Pump();recovery.Pump();while(recovery.Stages.CurrentRating!=null)recovery.Stages.Rate(recovery.Stages.CurrentRating.Id,1);}
            using var completed=f.Recover(_=>throw new Exception("Completed history must not reopen forms"));completed.Pump();Assert.That(completed.Complete,Is.True);Assert.That(shown,Is.EqualTo(1));
        }
        [Test]public void MalformedHistoryUnreviewedWordingAndFailedViewCannotReopen()
        {
            var f=new Fixture();Assert.Throws<SessionFault>(()=>f.Recover(_=>new View(),false));
            f.Ratings.Rows.Add(new AssessmentRecord("forms_completed",f.Schedule.Sha256,0,"forms"));Assert.Throws<AssessmentFault>(()=>f.Recover(_=>new View()));f.Ratings.Rows.Clear();
            int calls=0;using var failed=f.Recover(_=>{calls++;throw new IOException("Synthetic view failure");});Assert.Throws<IOException>(()=>failed.Pump());Assert.Throws<SessionFault>(()=>failed.Pump());Assert.That(calls,Is.EqualTo(1));
        }
        [Test]public void AssessmentInputConsumesExactlyStationSchemaNames()
        {
            var root=new DirectoryInfo(Directory.GetCurrentDirectory());while(root!=null&&!File.Exists(Path.Combine(root.FullName,"apparatus/schemas/station.schema.json")))root=root.Parent;
            Assert.That(root,Is.Not.Null);var schema=JObject.Parse(File.ReadAllText(Path.Combine(root.FullName,"apparatus/schemas/station.schema.json")));
            var names=schema["properties"]["input_method"]["enum"].Values<string>().ToArray();Assert.That(names,Is.EquivalentTo(new[]{"controllers","hands"}));
            foreach(string name in names)Assert.That(AssessmentScreen.UsesControllers(name),Is.EqualTo(name=="controllers"));
            Assert.Throws<AssessmentFault>(()=>AssessmentScreen.UsesControllers("controller_ray"));Assert.Throws<AssessmentFault>(()=>AssessmentScreen.UsesControllers("hand_poke"));
        }
    }
}
