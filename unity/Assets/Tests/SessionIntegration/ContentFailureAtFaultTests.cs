using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace AcousticVocab.SessionIntegration.Tests
{
    // Yoked attempt simulation-test-B-V1-025-C01Y-isaac2-031: the profile menu's
    // prepare failed (SESSION_CONTENT_PREPARE_FAILED) right after its commit and
    // the visit paused. #232's content_failure_detail is written only at a
    // terminal fault, so nothing explained it until the run was closed 277 s
    // later. Every content fault is now explained when it happens.
    public sealed class ContentFailureAtFaultTests
    {
        static T New<T>(params object[] a)=>(T)Activator.CreateInstance(typeof(T),BindingFlags.Instance|BindingFlags.NonPublic,null,a,null);
        sealed class Clock:ISessionClock{internal double Time;public double NowMs=>Time;}
        sealed class Journal:ISessionJournal{internal readonly List<SessionRecord> Rows=new List<SessionRecord>();public IReadOnlyList<SessionRecord> Records=>Rows;public void Append(SessionRecord r)=>Rows.Add(r);}
        sealed class Menus:ISlotContentFactory,ISlotContent
        {
            internal readonly Queue<Exception> Prepares=new Queue<Exception>();
            public ISlotContent Create(SlotItem item)=>this;
            public void Prepare(SlotContext c){if(Prepares.Count>0){var error=Prepares.Dequeue();if(error!=null)throw error;}}
            public SlotReadiness Readiness=>new SlotReadiness(true,true,true,true,true,true,true,true);public bool ResetComplete=>true;
            public void RequestCue(SlotContext c,INovelSlotAuthorization p){}public void OpenResponse(SlotContext c){}public void CloseResponse(SlotContext c){}public void RequestReset(SlotContext c){}public void Interrupt(string code){}
        }
        static (FixedSlotEngine engine,Journal journal,Menus menus,Clock clock) Engine()
        {
            var item=New<SlotItem>("B-C01-M2-V1-PM-01","profile_menu",null,null,"selection","selection",false,60,8,1);
            var schedule=New<VisitSchedule>(new string('a',64),new string('b',64),"synthetic","V1",true,new[]{New<ScheduleBlock>("profile_menu",new[]{item})},"B","S",null);
            var clock=new Clock{Time=17631.7};var journal=new Journal();var menus=new Menus();return (new FixedSlotEngine(schedule,clock,journal,menus),journal,menus,clock);
        }
        [Test]public void EachContentFaultIsReportedWhenItHappensWithItsOwnBoundedDetail()
        {
            var (engine,journal,menus,clock)=Engine();var seen=new List<(string code,string attempt,string detail,int rows)>();
            engine.ContentFailed+=(code,attempt,detail)=>seen.Add((code,attempt,detail,journal.Rows.Count(x=>x.Event=="item_fault")));
            menus.Prepares.Enqueue(new InvalidOperationException("CONTROL_QUEUED"));menus.Prepares.Enqueue(new IOException("C:\\private\\path failed"));
            engine.ConfirmResume();engine.Tick();
            Assert.That(engine.Status,Is.EqualTo(SessionState.Paused),"The visit pauses on the prepare fault");
            Assert.That(seen.Single(),Is.EqualTo(("SESSION_CONTENT_PREPARE_FAILED","B-C01-M2-V1-PM-01","InvalidOperationException:CONTROL_QUEUED",0)),"Reported before its item_fault record");
            clock.Time+=900;engine.ConfirmResume();engine.Tick();
            Assert.That(seen.Count,Is.EqualTo(2));Assert.That(seen[1].detail,Is.EqualTo("IOException"),"Free text never leaves the engine");
            Assert.That(engine.ContentFailureDetail,Is.EqualTo("InvalidOperationException:CONTROL_QUEUED"),"The terminal record keeps the first cause");
            Assert.That(journal.Rows.Count(x=>x.Event=="item_fault"&&x.TechnicalFaultCode=="SESSION_CONTENT_PREPARE_FAILED"),Is.EqualTo(2));
        }
        [Test]public void AThrowingObserverNeverMasksTheFault()
        {
            var (engine,journal,menus,_)=Engine();engine.ContentFailed+=(a,b,c)=>throw new InvalidOperationException("observer");
            menus.Prepares.Enqueue(new InvalidOperationException("CONTROL_QUEUED"));engine.ConfirmResume();engine.Tick();
            Assert.That(engine.Status,Is.EqualTo(SessionState.Paused));Assert.That(journal.Rows.Any(x=>x.Event=="item_fault"&&x.TechnicalFaultCode=="SESSION_CONTENT_PREPARE_FAILED"),Is.True);
        }
        [Test]public void JoinedOwnerJournalsTheFailureImmediately()
        {
            const BindingFlags Fields=BindingFlags.Instance|BindingFlags.NonPublic;
            string root=Path.Combine(Path.GetTempPath(),".local","content-failure-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(root);string path=Path.Combine(root,"audit.local.jsonl");
            var go=new GameObject("Content failure journal");go.SetActive(false);var host=go.AddComponent<JoinedEngineeringBootstrap>();JoinedAudit audit=null;
            try
            {
                var hostClock=(ISessionClock)typeof(JoinedEngineeringBootstrap).GetField("clock",Fields).GetValue(host);
                audit=new JoinedAudit(path,()=>hostClock.NowMs);typeof(JoinedEngineeringBootstrap).GetField("audit",Fields).SetValue(host,audit);
                var (engine,_,menus,_)=Engine();engine.ContentFailed+=(code,attempt,detail)=>typeof(JoinedEngineeringBootstrap).GetMethod("RecordContentFailure",Fields).Invoke(host,new object[]{code,attempt,detail});
                menus.Prepares.Enqueue(new InvalidOperationException("CONTROL_QUEUED"));engine.ConfirmResume();engine.Tick();audit.Dispose();
                var payload=(JObject)JObject.Parse(File.ReadAllLines(path).Single())["payload"];
                Assert.That((string)payload["kind"],Is.EqualTo("content_failure"));Assert.That((string)payload["code"],Is.EqualTo("SESSION_CONTENT_PREPARE_FAILED"));
                Assert.That((string)payload["attempt_id"],Is.EqualTo("B-C01-M2-V1-PM-01"));Assert.That((string)payload["exception"],Is.EqualTo("InvalidOperationException:CONTROL_QUEUED"));
                Assert.That(payload["observed_mono_ms"].Type,Is.EqualTo(JTokenType.Float));
            }
            finally{audit?.Dispose();typeof(JoinedEngineeringBootstrap).GetField("closed",Fields).SetValue(host,true);UnityEngine.Object.DestroyImmediate(go);if(Directory.Exists(root))Directory.Delete(root,true);}
        }
    }
}
