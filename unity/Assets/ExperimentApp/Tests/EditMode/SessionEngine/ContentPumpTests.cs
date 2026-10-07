using System;
using System.Collections.Generic;
using System.Linq;
using AcousticVocab.StudyAudio;
using NUnit.Framework;

namespace AcousticVocab.SessionEngine.Tests
{
    public sealed class ContentPumpTests
    {
        sealed class Clock:ISessionClock{public double Time;public double NowMs=>Time;}
        sealed class Log:ISessionJournal{public readonly List<SessionRecord> Rows=new List<SessionRecord>();public IReadOnlyList<SessionRecord> Records=>Rows;public void Append(SessionRecord row)=>Rows.Add(row);}
        sealed class Content:ISlotContent
        {
            public Action Interrupted;
            public SlotReadiness Readiness=>new SlotReadiness(true,true,true,true,true,true,true,true);
            public bool ResetComplete{get;private set;}
            public void Prepare(SlotContext context){}
            public void RequestCue(SlotContext context,INovelSlotAuthorization permit){}
            public void OpenResponse(SlotContext context){}
            public void CloseResponse(SlotContext context){}
            public void RequestReset(SlotContext context){ResetComplete=true;}
            public void Interrupt(string code){Interrupted?.Invoke();}
        }
        sealed class Factory:ISlotContentFactory,ISessionContentPump
        {public readonly Content Content=new Content();public Action Action;public int Calls;public ISlotContent Create(SlotItem item)=>Content;public void Pump(){Calls++;Action?.Invoke();}}
        static FixedSlotEngine Make(Clock clock,Log log,Factory factory)
        {var item=new SlotItem("DEMO-item","atomic_lesson","K-a1","action",null,"teaching",false,20,3,1);return new FixedSlotEngine(new VisitSchedule(new string('a',64),new string('b',64),"DEMO","D0",true,new[]{new ScheduleBlock("atomic_lessons",new[]{item})}),clock,log,factory);}
        [Test]public void TimeoutPumpRunsBeforeEngineClosesDeadline()
        {var clock=new Clock();var log=new Log();var factory=new Factory();var engine=Make(clock,log,factory);engine.ConfirmResume();engine.Tick();clock.Time=6750;engine.Tick();factory.Action=()=>{if(clock.Time==13750&&engine.CurrentState==ItemState.ResponseOpen)engine.RecordResponse("timeout");};clock.Time=13750;engine.Tick();Assert.That(log.Rows.Single(x=>x.Event=="response").ResponseCode,Is.EqualTo("timeout"));}
        [Test]public void RetiredPresentationPumpContinuesThroughFixedTail()
        {var clock=new Clock();var log=new Log();var factory=new Factory();var engine=Make(clock,log,factory);engine.ConfirmResume();engine.Tick();clock.Time=6750;engine.Tick();clock.Time=13750;engine.Tick();Assert.That(engine.CurrentTrialId,Is.Null);Assert.That(engine.Status,Is.EqualTo(SessionState.Running));int before=factory.Calls;clock.Time=20750;engine.Tick();Assert.That(factory.Calls,Is.EqualTo(before+1));Assert.That(engine.Status,Is.EqualTo(SessionState.Complete));}
        [Test]public void RecursiveTickAndRecursiveFaultCannotReenter()
        {var clock=new Clock();var log=new Log();var factory=new Factory();var engine=Make(clock,log,factory);engine.ConfirmResume();factory.Action=()=>engine.Tick();engine.Tick();Assert.That(factory.Calls,Is.EqualTo(1));factory.Content.Interrupted=()=>engine.Fault("RECURSIVE_FAULT");factory.Action=()=>throw new InvalidOperationException();engine.Tick();Assert.That(log.Rows.Count(x=>x.Event=="item_fault"),Is.EqualTo(1));Assert.That(log.Rows.Single(x=>x.Event=="item_fault").TechnicalFaultCode,Is.EqualTo("SESSION_CONTENT_PUMP_FAILED"));}
    }
}
