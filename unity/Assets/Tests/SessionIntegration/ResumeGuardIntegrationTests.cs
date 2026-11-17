using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using AcousticVocab.DataLogging;
using AcousticVocab.OperatorConsole;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.SessionIntegration.Tests
{
    // Interrupted-battery resumption (#278) through the joined host's mailbox
    // admission and the shared hash-chained visit journal, across restarts.
    public sealed class ResumeGuardIntegrationTests
    {
        const string Manifest="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        const string Schedule="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
        const string Session="0123456789abcdef0123456789abcdef";
        static readonly DataIdentity Identity=new DataIdentity(Session,"DEMO-CODED","D0","simulator-01","DEMO-protocol-1",new string('d',64));
        static T New<T>(params object[] args)=>(T)Activator.CreateInstance(typeof(T),BindingFlags.Instance|BindingFlags.NonPublic,null,args,null);
        static DateTimeOffset Day(int day,int hour)=>new DateTimeOffset(2027,1,day,hour,0,0,TimeSpan.FromHours(1));
        sealed class Clock:ISessionClock{public double Time;public double NowMs=>Time;}
        sealed class Audit:IOperatorCommandJournal{public readonly List<OperatorAuditRecord> Rows=new List<OperatorAuditRecord>();public void Append(OperatorAuditRecord record)=>Rows.Add(record);}
        sealed class Content:ISlotContent
        {
            public int Cues;
            public SlotReadiness Readiness=>new SlotReadiness(true,true,true,true,true,true,true,true);
            public bool ResetComplete{get;private set;}
            public void Prepare(SlotContext context){}
            public void RequestCue(SlotContext context,INovelSlotAuthorization permit){Cues++;}
            public void OpenResponse(SlotContext context){}
            public void CloseResponse(SlotContext context){}
            public void RequestReset(SlotContext context){ResetComplete=true;}
            public void Interrupt(string code){}
        }
        sealed class Factory:ISlotContentFactory{public readonly List<Content> Items=new List<Content>();public ISlotContent Create(SlotItem item){var value=new Content();Items.Add(value);return value;}}

        string root;
        [SetUp]public void Setup(){root=Path.Combine(Environment.CurrentDirectory,".local","resume-guard-tests",Guid.NewGuid().ToString("N"));Directory.CreateDirectory(root);}
        [TearDown]public void Cleanup(){foreach(var host in hosts)host.Dispose();hosts.Clear();if(Directory.Exists(root))Directory.Delete(root,true);}
        readonly List<Host> hosts=new List<Host>();

        // One host process: reopened visit journal, recovered engine, a fresh
        // mailbox nonce and the same admission the joined bootstrap installs.
        sealed class Host:IDisposable
        {
            public readonly Clock Clock=new Clock();public readonly Factory Factory=new Factory();public readonly Audit Commands=new Audit();
            public readonly DataJournal Data;public readonly SessionDataJournal Sessions;public readonly FixedSlotEngine Engine;public readonly OperatorMailbox Mailbox;
            public DateTimeOffset Now;
            public Host(string root,DateTimeOffset now)
            {
                Now=now;
                Data=new DataJournal(Path.Combine(root,"data"),Identity,Guid.NewGuid().ToString("N"),()=>Clock.Time);Sessions=new SessionDataJournal(Data);
                var items=Enumerable.Range(1,3).Select(i=>New<SlotItem>("DEMO-TR-0"+i,"trained","K-a1-r1",null,null,"protected",false,14,1,1)).ToArray();
                var schedule=New<VisitSchedule>(Schedule,new string('c',64),"DEMO-CODED","D0",true,new[]{New<ScheduleBlock>("trained",items)},"A","pilot",null);
                Engine=new FixedSlotEngine(schedule,Clock,Sessions,Factory);
                var guard=new ResumeGuard(new ResumePolicy("A","D0",Session,null),new ResumeDataRecord(Data),()=>Now);
                string mailbox=Path.Combine(root,"mailbox-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(mailbox);
                Mailbox=new OperatorMailbox(mailbox,Guid.NewGuid().ToString("N"),Manifest,Engine,Commands,()=>new OperatorAdmission(true,true,true),
                    ()=>new OperatorHealth(true,true,true,true,5,14,14),()=>Clock.Time,stageBeforeResume:(engine,request)=>{JoinedEngineeringBootstrap.AdmitResume(guard,engine,Sessions.Records);return true;});
                Assert.That(Send(1,"load").Accepted,Is.True);
            }
            public OperatorReceipt Send(long sequence,string command)=>Mailbox.Handle(OperatorRequest.Parse(System.Text.Encoding.UTF8.GetBytes(new JObject{
                ["version"]=1,["session_nonce"]=Mailbox.SessionNonce,["request_id"]=Guid.NewGuid().ToString("N"),["sequence"]=sequence,["command"]=command,
                ["run_sheet_manifest_sha256"]=Manifest,["schedule_sha256"]=Schedule}.ToString(Formatting.None))));
            // Losing the host is not a stop: no adapter disposal is recorded.
            public void Crash(){Data.Dispose();}
            public void Dispose(){try{Mailbox.Dispose();}catch{}try{Data.Dispose();}catch{}}
        }
        Host Start(DateTimeOffset now){var host=new Host(root,now);hosts.Add(host);return host;}
        static IEnumerable<DataRecord> Rows(Host host,string kind)=>host.Data.Records.Where(r=>r.Kind==kind);

        [Test,Category("REQ_INT_02"),Category("REQ_INT_04")]
        public void ImmediateTestRefusedOnALaterDayIsClosedAndKeepsCommittedTrials()
        {
            var first=Start(Day(4,16));
            Assert.That(first.Send(2,"start").Accepted,Is.True);
            var start=first.Data.Records.First(r=>r.Kind=="visit_start"||r.Kind=="session");
            Assert.That(start.Kind,Is.EqualTo("visit_start"),"the start day is durable before the first session row");
            Assert.That((string)start.Payload["local_date"],Is.EqualTo("2027-01-04"));Assert.That((string)start.Payload["session_id"],Is.EqualTo(Session));
            for(first.Clock.Time=50;first.Clock.Time<=800;first.Clock.Time+=50)first.Engine.Tick();
            first.Engine.RecordResponse("commit");Assert.That(first.Factory.Items.Single().Cues,Is.EqualTo(1));
            first.Crash();

            var later=Start(Day(5,9));
            Assert.That(later.Engine.Status,Is.EqualTo(SessionState.AwaitingOperator));
            var refused=later.Send(2,"start");
            Assert.That(refused.Accepted,Is.False);Assert.That(refused.Code,Is.EqualTo("resume_outside_session"));
            Assert.That(later.Mailbox.Failed,Is.False,"a refused resumption is not an adapter fault");
            Assert.That(later.Engine.Status,Is.EqualTo(SessionState.Stopped));Assert.That(later.Factory.Items,Is.Empty);
            Assert.That(Rows(later,"visit_exit").Select(r=>(string)r.Payload["code"]),Is.EqualTo(new[]{ResumePolicy.OutsideSession}));
            Assert.That(later.Sessions.Records.Count(r=>r.Event=="response"&&r.ResponseCode=="commit"&&r.TrialId=="DEMO-TR-01"),Is.EqualTo(1));
            Assert.That(later.Commands.Rows.Last().Receipt.Code,Is.EqualTo("resume_outside_session"));
            later.Crash();

            // A further restart, even on the original day, stays closed without a second record.
            var again=Start(Day(4,18));
            Assert.That(again.Send(2,"start").Code,Is.EqualTo("resume_outside_session"));
            Assert.That(Rows(again,"visit_exit").Count(),Is.EqualTo(1));Assert.That(Rows(again,"visit_start").Count(),Is.EqualTo(1));
            Assert.That(again.Factory.Items,Is.Empty);
            Assert.That(again.Sessions.Records.Where(r=>r.Event=="state_before"&&r.State==ItemState.CueRequested).Select(r=>r.TrialId),Is.EqualTo(new[]{"DEMO-TR-01"}));
        }

        [Test,Category("REQ_INT_01"),Category("REQ_INT_02")]
        public void SameDayRestartResumesWithTheNextUnplayedTrial()
        {
            var first=Start(Day(4,16));
            Assert.That(first.Send(2,"start").Accepted,Is.True);
            for(first.Clock.Time=50;first.Clock.Time<=800;first.Clock.Time+=50)first.Engine.Tick();
            first.Engine.RecordResponse("commit");first.Crash();
            var same=Start(Day(4,17));
            Assert.That(same.Send(2,"start").Accepted,Is.True);
            Assert.That(same.Engine.Status,Is.EqualTo(SessionState.Running));Assert.That(same.Engine.CurrentTrialId,Is.EqualTo("DEMO-TR-02"));
            Assert.That(Rows(same,"visit_start").Count(),Is.EqualTo(1));Assert.That(Rows(same,"visit_exit"),Is.Empty);
        }

        [Test]
        public void VisitStartRowsAreClosedAndBoundToTheJournalSession()
        {
            Assert.That(()=>new EventDraft("visit_start",null,new JObject{["session_id"]=Session,["local_date"]="2027-02-30"}),Throws.TypeOf<DataFault>());
            Assert.That(()=>new EventDraft("visit_start",null,new JObject{["session_id"]="DEMO",["local_date"]="2027-01-04"}),Throws.TypeOf<DataFault>());
            Assert.That(()=>new EventDraft("visit_start",new EventContext("DEMO-TR-01"),new JObject{["session_id"]=Session,["local_date"]="2027-01-04"}),Throws.TypeOf<DataFault>());
            Assert.That(()=>new EventDraft("visit_start",null,new JObject{["session_id"]=Session,["local_date"]="2027-01-04",["utc"]="x"}),Throws.TypeOf<DataFault>());
            using var data=new DataJournal(Path.Combine(root,"bound"),Identity,Guid.NewGuid().ToString("N"),()=>0);
            var record=new ResumeDataRecord(data);
            Assert.That(()=>record.RecordAnchor(new ResumeAnchor("fedcba9876543210fedcba9876543210","2027-01-04")),Throws.TypeOf<DataFault>());
            record.RecordAnchor(new ResumeAnchor(Session,"2027-01-04"));
            Assert.That(()=>record.RecordAnchor(new ResumeAnchor(Session,"2027-01-05")),Throws.TypeOf<DataFault>());
            Assert.That(record.Anchor.LocalDate,Is.EqualTo("2027-01-04"));Assert.That(record.ClosedCode,Is.Null);
        }
    }
}
