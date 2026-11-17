using System;
using System.IO;
using System.Linq;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.DataLogging.Tests
{
    // The visit-start opportunity list, with the real session engine running
    // only the first block of a synthetic visit before the operator stops it.
    public sealed class ScheduledOpportunityTests
    {
        const string Masked="DEMO-BOOK-P1";
        sealed class Clock : ISessionClock { internal double Time; public double NowMs=>Time; }
        sealed class Factory : ISlotContentFactory
        {
            readonly DataJournal journal;internal Factory(DataJournal journal){this.journal=journal;}
            public ISlotContent Create(SlotItem item)=>new Content(journal);
            sealed class Content : ISlotContent
            {
                readonly DataJournal journal;internal Content(DataJournal journal){this.journal=journal;}
                public SlotReadiness Readiness=>new SlotReadiness(true,true,true,true,true,true,true,true);
                public bool ResetComplete{get;private set;}
                public void Prepare(SlotContext c){}
                public void RequestCue(SlotContext c,INovelSlotAuthorization permit)=>journal.Append(SyntheticData.Audio(new EventContext(c.OpportunityId,c.Item.TrialId,c.AudioRequestIds[0])));
                public void OpenResponse(SlotContext c){} public void CloseResponse(SlotContext c){}
                public void RequestReset(SlotContext c){ResetComplete=true;} public void Interrupt(string code){}
            }
        }
        static SlotItem Item(string id,string type,string phase,int slot,int pass)=>new SlotItem(id,type,"synthetic-content",null,null,phase,false,slot,1,pass);
        // Six slots in three blocks; the trained block has two passes.
        static VisitSchedule Schedule(string prefix="SYNTHETIC")=>new VisitSchedule(new string('b',64),new string('c',64),"SYNTHETIC","DEMO",true,new[]{
            new ScheduleBlock("pre_old",new[]{Item(prefix+"-PO-01","pre_old","pre_test",14,1)}),
            new ScheduleBlock("trained",new[]{Item(prefix+"-TR-01","trained","protected",14,1),Item(prefix+"-TR-02","trained","protected",14,1),Item(prefix+"-TR-03","trained","protected",14,2),Item(prefix+"-TR-04","trained","protected",14,2)}),
            new ScheduleBlock("atomic",new[]{Item(prefix+"-AT-01","atomic","protected",9,1)})},"A","A",null);
        static string[] Ids(VisitSchedule s)=>s.Blocks.SelectMany(b=>b.Items).Select(i=>i.TrialId).ToArray();
        static int Opportunities(DataJournal journal)=>journal.Records.Count(r=>r.Kind=="opportunity");

        [Test, Category("REQ_LOG_01"), NUnit.Framework.Property("Requirement","REQ-LOG-01")]
        public void AVisitStoppedAfterItsFirstTrialExportsEveryScheduledSlotAndARelaunchAddsNone()
        {
            string raw=SyntheticData.Folder("opportunities-stopped");var clock=new Clock();var schedule=Schedule();
            using(var writer=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>clock.Time))
            {
                Assert.That(ScheduledOpportunities.Journal(writer,schedule,Masked),Is.EqualTo(6));
                var engine=new FixedSlotEngine(schedule,clock,new SessionDataJournal(writer),new Factory(writer));
                engine.ConfirmResume();engine.Tick();bool responded=false;
                while(engine.Status==SessionState.Running&&clock.Time<60000)
                {
                    clock.Time+=50;engine.Tick();
                    if(!responded&&engine.CurrentState==ItemState.ResponseOpen){responded=true;engine.RecordResponse("commit");}
                }
                Assert.That(engine.Status,Is.EqualTo(SessionState.Paused),"the first block ends at a boundary");
                engine.RequestStop();Assert.That(engine.Status,Is.EqualTo(SessionState.Stopped));
            }
            var snapshot=DataJournal.Verify(raw,SyntheticData.Identity);
            Assert.That(snapshot.Records.Take(6).Select(r=>r.Kind),Has.All.EqualTo("opportunity"),"listed before any session or audio row");
            Assert.That(snapshot.Records.Count(r=>r.Kind=="audio_request"),Is.EqualTo(1));

            var rows=OpportunityExport.Derive(snapshot,SyntheticData.Identity);
            Assert.That(rows.Select(r=>r["opportunity_id"]),Is.EqualTo(Ids(schedule)));
            var played=rows[0];
            Assert.That(played["opportunity_status"],Is.EqualTo("attempted"));Assert.That(played["attempt_ids"],Is.EqualTo("[\"SYNTHETIC-PO-01\"]"));
            Assert.That(new[]{played["block"],played["pass"],played["item_kind"],played["role"],played["method_masked"],played["schedule_sha256"]},
                Is.EqualTo(new[]{"pre_old","1","pre_old","primary",Masked,schedule.Sha256}));
            Assert.That(rows.Skip(1).Select(r=>r["opportunity_status"]),Has.All.EqualTo("unplayed"));Assert.That(rows.Skip(1).Select(r=>r["attempt_ids"]),Has.All.EqualTo("[]"));
            Assert.That(rows.Select(r=>r["block"]+"/"+r["pass"]+"/"+r["item_kind"]),Is.EqualTo(new[]{"pre_old/1/pre_old","trained/1/trained","trained/1/trained","trained/2/trained","trained/2/trained","atomic/1/atomic"}));
            var trial=DataDeriver.Derive(snapshot,SyntheticData.Identity).Trials.Single();
            Assert.That(trial["opportunity_id"],Is.EqualTo("SYNTHETIC-PO-01"));Assert.That(trial["method_masked"],Is.EqualTo(Masked),"the trial log is unchanged: one row per actual attempt");

            using(var relaunched=new DataJournal(raw,SyntheticData.Identity,new string('4',32),()=>clock.Time))
            {
                Assert.That(ScheduledOpportunities.Journal(relaunched,schedule,Masked),Is.EqualTo(0));
                Assert.That(Opportunities(relaunched),Is.EqualTo(6));
            }

            string output=SyntheticData.Folder("opportunities-export");
            var bundle=ExportBundle.Create(raw,output,SyntheticData.Identity,ExportHeaders.Provisional());Assert.That(bundle.HeadersQualified,Is.False);
            var lines=File.ReadAllLines(Path.Combine(output,OpportunityExport.Table));
            Assert.That(lines[0],Is.EqualTo(string.Join(",",OpportunityExport.Headers)));Assert.That(lines.Length,Is.EqualTo(7),"one row per scheduled slot after the relaunch");
            Assert.That(lines.Count(l=>l.EndsWith(",unplayed,[]",StringComparison.Ordinal)),Is.EqualTo(5));
            var loaded=ExportBundle.Load(output,bundle.ManifestSha256).Files.Select(f=>f.RelativePath).ToArray();
            Assert.That(loaded,Does.Contain(OpportunityExport.Table));Assert.That(loaded,Does.Contain(OpportunityExport.Contract));
        }

        [Test, Category("REQ_LOG_01"), NUnit.Framework.Property("Requirement","REQ-LOG-01")]
        public void RelaunchCompletesAPartialListAndRefusesChangedUnscheduledOrLateRows()
        {
            string raw=SyntheticData.Folder("opportunities-relaunch");double now=0;var schedule=Schedule();
            using(var first=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>now++))
            {
                // A launch that stopped after two of the six rows, before any audio.
                first.Append(DataObservations.Opportunity("SYNTHETIC-PO-01",ScheduledOpportunities.Role,Masked,schedule.Sha256,"pre_old",1,"pre_old"));
                first.Append(DataObservations.Opportunity("SYNTHETIC-TR-01",ScheduledOpportunities.Role,Masked,schedule.Sha256,"trained",1,"trained"));
            }
            using(var second=new DataJournal(raw,SyntheticData.Identity,new string('4',32),()=>now++))
            {
                Assert.That(ScheduledOpportunities.Journal(second,schedule,Masked),Is.EqualTo(4));
                Assert.That(second.Records.Where(r=>r.Kind=="opportunity").Select(r=>r.Context.OpportunityId),Is.EqualTo(Ids(schedule)));
                Assert.That(ScheduledOpportunities.Journal(second,schedule,Masked),Is.EqualTo(0));
                Assert.That(Assert.Throws<DataFault>(()=>ScheduledOpportunities.Journal(second,schedule,"DEMO-BOOK-P2")).Code,Is.EqualTo("DATA_OPPORTUNITY_CHANGED"));
                Assert.That(Assert.Throws<DataFault>(()=>ScheduledOpportunities.Journal(second,Schedule("OTHER"),Masked)).Code,Is.EqualTo("DATA_OPPORTUNITY_UNSCHEDULED"));
                Assert.That(Opportunities(second),Is.EqualTo(6));
            }
            string late=SyntheticData.Folder("opportunities-late");
            using(var w=new DataJournal(late,SyntheticData.Identity,new string('3',32),()=>now++))
            {
                SyntheticData.Trial(w,"SYNTHETIC-PO-01");
                Assert.That(Assert.Throws<DataFault>(()=>ScheduledOpportunities.Journal(w,schedule,Masked)).Code,Is.EqualTo("DATA_OPPORTUNITY_AFTER_AUDIO"));
                Assert.That(Opportunities(w),Is.EqualTo(0));
            }
        }

        [Test, Category("REQ_LOG_01"), NUnit.Framework.Property("Requirement","REQ-LOG-01")]
        public void ASlotRowCarriesBlockPassAndKindTogetherAndOlderRowsStayValid()
        {
            string hash=new string('b',64);
            Assert.DoesNotThrow(()=>DataObservations.Opportunity("SYNTHETIC-TR-01","primary",Masked,hash));
            Assert.That((int)DataObservations.Opportunity("SYNTHETIC-TR-01","primary",Masked,hash,"trained",2,"trained").Payload["pass"],Is.EqualTo(2));
            JObject Payload()=>new JObject{["role"]="primary",["method_masked"]=Masked,["schedule_sha256"]=hash,["block"]="trained",["pass"]=1,["item_kind"]="trained"};
            foreach(string missing in new[]{"block","pass","item_kind"})
            {var partial=Payload();partial.Remove(missing);Assert.Throws<DataFault>(()=>new EventDraft("opportunity",new EventContext("SYNTHETIC-TR-01"),partial),missing);}
            var zero=Payload();zero["pass"]=0;
            Assert.That(Assert.Throws<DataFault>(()=>new EventDraft("opportunity",new EventContext("SYNTHETIC-TR-01"),zero)).Code,Is.EqualTo("DATA_OPPORTUNITY_SLOT"));
            Assert.Throws<DataFault>(()=>new EventDraft("opportunity",new EventContext("SYNTHETIC-TR-01","SYNTHETIC-TR-01"),Payload()),"an opportunity row has no attempt");
        }
    }
}
