using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using AcousticVocab.OperatorConsole;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;

// Bounded synthetic host of actual Unity-compiled engine/mailbox DLLs.
// No Unity scene, participant material, audio device or measured health.
static class Program
{
    static T New<T>(params object[] values)=>(T)Activator.CreateInstance(typeof(T),BindingFlags.Instance|BindingFlags.NonPublic,null,values,null);
    sealed class Clock : ISessionClock { readonly Stopwatch time=Stopwatch.StartNew();public double NowMs=>time.Elapsed.TotalMilliseconds*5; }
    sealed class Memory : ISessionJournal { readonly List<SessionRecord> records=new List<SessionRecord>();public IReadOnlyList<SessionRecord> Records=>records;public void Append(SessionRecord record)=>records.Add(record); }
    sealed class Factory : ISlotContentFactory { public ISlotContent Create(SlotItem item)=>new Content(); }
    sealed class Content : ISlotContent
    {
        public SlotReadiness Readiness=>new SlotReadiness(true,true,true,true,true,true,true,true);
        public bool ResetComplete{get;private set;}
        public void Prepare(SlotContext c){} public void RequestCue(SlotContext c,INovelSlotAuthorization p){}public void OpenResponse(SlotContext c){}public void CloseResponse(SlotContext c){}public void RequestReset(SlotContext c){ResetComplete=true;}public void Interrupt(string code){}
    }
    static int Main(string[] args)
    {
        string directory=args[0];Directory.CreateDirectory(directory);string nonce=Guid.NewGuid().ToString("N");
        var items=Enumerable.Range(0,2).Select(i=>New<SlotItem>("synthetic-"+i,"trained","synthetic-content",null,null,"protected",false,14,1,1)).ToArray();
        var visit=New<VisitSchedule>(new string('b',64),new string('c',64),"SYNTHETIC","DEMO",true,new[]{New<ScheduleBlock>("synthetic",items)});
        var clock=new Clock();var engine=new FixedSlotEngine(visit,clock,new Memory(),new Factory());
        using var journal=new FileOperatorCommandJournal(directory,nonce);
        using var mailbox=new OperatorMailbox(directory,nonce,new string('a',64),engine,journal,()=>new OperatorAdmission(true,true,true),()=>new OperatorHealth(true,true,true,true,5,14,14),()=>clock.NowMs);
        var limit=Stopwatch.StartNew();
        while(limit.Elapsed.TotalSeconds<30){mailbox.Tick();if(engine.Status==SessionState.Stopped)return 0;Thread.Sleep(10);}
        return 2;
    }
}
