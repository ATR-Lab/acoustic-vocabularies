using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using AcousticVocab.SessionEngine;
using AcousticVocab.SessionIntegration;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.SelectionMenus.Tests
{
    public sealed class MenuLedgerTests
    {
        static readonly string Hash=new string('a',64);
        static readonly string[] Keys={"K-a1","K-a2","K-r1","K-r2"};
        readonly DateTimeOffset created=new DateTimeOffset(2026,1,1,0,0,0,TimeSpan.Zero);
        string directory;
        [SetUp]public void Before(){directory=Path.Combine(Path.GetTempPath(),"av-menu-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(directory);}
        [TearDown]public void After(){Directory.Delete(directory,true);}
        static MenuLedgerBinding Binding(string role="active")=>new MenuLedgerBinding(Hash,Hash,Hash,Hash,Hash,Hash,"V2",role,Keys);
        static MenuOption[] Options(string unused)=>Enumerable.Range(1,3).Select(i=>new MenuOption("DEMO-candidate-"+i,SelectionMenuTests.Wave(unused=="profile"?96000:21600,i*100))).ToArray();
        static MenuLedgerVerification Verification()=>new MenuLedgerVerification(Options,(_,index,hash)=>index==1&&hash==Hash,_=>"DEMO-meaning");
        static List<MenuEvent> Events(MenuReplaySequence replay=null,string[] keys=null,double observedDelay=0)
        {
            var result=new List<MenuEvent>();keys??=Keys;double start=750;
            for(int menu=0;menu<keys.Length;menu++)
            {
                bool profile=keys[menu]=="profile";var item=new SlotItem("DEMO-menu-"+menu,profile?"profile_menu":"atom_menu",profile?null:keys[menu],null,"selection","selection",false,profile?60:45,8,1);var ctx=new SlotContext(item,start,null);double clock=start-750;
                var timeline=new MenuTimeline(ctx,Options(keys[menu]),"DEMO-meaning",result.Add,replay?.For(item),()=>clock+observedDelay);var plays=new List<(string id,MenuOption option,double at)>();var heard=new HashSet<string>();var done=new HashSet<string>();
                timeline.PlayRequested+=(_,id,option,at)=>plays.Add((id,option,at));if(replay==null)timeline.SelectionRequested+=(index,unused)=>timeline.ConfirmSelection(index,Hash,start+(profile?45000:32000));timeline.Start(start-750);
                for(double now=start;now<=start+item.SlotSeconds*1000+20;now+=10)
                {clock=now;timeline.Tick(now);foreach(var play in plays.ToArray()){if(now>=play.at&&heard.Add(play.id))timeline.Onset(play.id,play.at,10,now);if(now>=play.at+play.option.Wave.SampleCount/48d&&done.Add(play.id))timeline.Completed(play.id,now);}}
                Assert.That(timeline.Complete,Is.True);start+=item.SlotSeconds*1000+5000;
            }
            return result;
        }
        string Write(IReadOnlyList<MenuEvent> events,bool seal=true,MenuLedgerBinding binding=null)
        {string path=Path.Combine(directory,Guid.NewGuid().ToString("N")+".jsonl");using(var writer=new MenuLedger(path,binding??Binding(),created)){foreach(var row in events)writer.Append(row);if(seal)writer.Seal(Verification());}return path;}
        MenuReplaySequence Load(string path,DateTimeOffset? now=null,MenuLedgerVerification verification=null)=>MenuReplaySequence.Load(path,PcmWave.Hash(File.ReadAllBytes(path)),Binding(),verification??Verification(),now??created.AddHours(1),1000,()=>0);
        [Test]public void ExplicitJoinedAnchorIsDurableBeforeRealReplayLoadAndNeverReanchors()
        {
            string path=Write(Events()),pin=PcmWave.Hash(File.ReadAllBytes(path));double now=1000;var records=new List<YokedAnchorRecord>();
            var authority=new YokedReplayAuthority(5000,new string('b',32),pin,Hash,()=>now,records.Add,anchor=>
            {Assert.That(records.Count,Is.EqualTo(1));Assert.That(records[0].AnchorMonoMs,Is.EqualTo(anchor));return MenuReplaySequence.Load(path,pin,Binding(),Verification(),created.AddHours(1),anchor,()=>now);});
            string id=Guid.NewGuid().ToString("N");var replay=authority.BindForExplicitStart(id,2,"resume");
            Assert.That(records[0].OperatorRequestId,Is.EqualTo(id));Assert.That(records[0].ClockEpoch,Is.EqualTo(new string('b',32)));Assert.That(records[0].AnchorMonoMs,Is.EqualTo(6000));
            var first=new SlotItem("DEMO-first","atom_menu",Keys[0],null,"selection","selection",false,45,8,1);
            Assert.That(replay.MinimumGapBeforeMs(first,1750),Is.EqualTo(4250));Assert.That(authority.Replay,Is.SameAs(replay));
            Assert.Throws<SessionFault>(()=>authority.BindForExplicitStart(Guid.NewGuid().ToString("N"),3,"resume"));
            now=6000;Assert.Throws<SessionFault>(()=>replay.MinimumGapBeforeMs(first,now));Assert.That(records.Count,Is.EqualTo(1));
            var report=replay.CompareYoked(Events(replay),Binding("yoked"),Verification());Assert.That(report.AudioPlays,Is.EqualTo(32));
        }
        [Test]public void FailedDurableAnchorNeverLoadsAndCannotRetryIntoANewClock()
        {
            int loads=0;var authority=new YokedReplayAuthority(2000,Guid.NewGuid().ToString("N"),Hash,Hash,()=>100,_=>throw new IOException("Synthetic journal failure"),_=>{loads++;return null;});
            Assert.Throws<IOException>(()=>authority.BindForExplicitStart(Guid.NewGuid().ToString("N"),1,"start"));Assert.That(loads,Is.Zero);
            Assert.Throws<SessionFault>(()=>authority.BindForExplicitStart(Guid.NewGuid().ToString("N"),2,"resume"));
        }
        [Test]public void WrongIndependentActiveScheduleCannotBeAuthorizedByLedgerHeader()
        {
            string path=Write(Events()),pin=PcmWave.Hash(File.ReadAllBytes(path));var wrong=new MenuLedgerBinding(Hash,Hash,Hash,new string('f',64),Hash,Hash,"V2","active",Keys);int writes=0;
            var authority=new YokedReplayAuthority(5000,Guid.NewGuid().ToString("N"),pin,wrong.ScheduleSha256,()=>100,_=>writes++,anchor=>MenuReplaySequence.Load(path,pin,wrong,Verification(),created.AddHours(1),anchor,()=>100));
            Assert.Throws<SessionFault>(()=>authority.BindForExplicitStart(Guid.NewGuid().ToString("N"),1,"start"));Assert.That(writes,Is.EqualTo(1));Assert.That(authority.Replay,Is.Null);
            Assert.Throws<SessionFault>(()=>authority.BindForExplicitStart(Guid.NewGuid().ToString("N"),2,"start"));
        }
        [Test]public void SealedIndependentAssetBoundLedgerPreservesAllPlaysSourceIdsAndPauses()
        {
            var events=Events();string path=Write(events);var loaded=Load(path);Assert.That(loaded.SourceSpanMs,Is.EqualTo(195000));
            var item=new SlotItem("DEMO-yoke","atom_menu",Keys[1],null,"selection","selection",false,45,8,1);Assert.That(loaded.MinimumGapBeforeMs(item,46000),Is.EqualTo(5000));
            var replay=loaded.For(item);Assert.That(replay.SourceEvents,Is.EqualTo(events.Where(x=>x.MenuKey==Keys[1]&&x.Kind=="play_request").Select(x=>x.EventId)));Assert.That(replay.OffsetsMs,Is.EqualTo(new[]{5000d,8000,11000,14000,17000,20000,35000,38000}));
            Assert.Throws<SessionFault>(()=>loaded.MinimumGapBeforeMs(item,51001));
        }
        [Test]public void ActiveAndYokedMatchAllFilesCountsChoicesAndSourceEvents()
        {var replay=Load(Write(Events()));var rows=Events(replay);var comparison=replay.CompareYoked(rows,Binding("yoked"),Verification());Assert.That(comparison.Menus,Is.EqualTo(4));Assert.That(comparison.AtomPlays,Is.EqualTo(32));Assert.That(rows.All(x=>x.YokedSourceEventId!=null),Is.True);Assert.That(rows.Any(x=>x.Kind=="choice_revised"),Is.False);
         using var ledger=new MenuLedger(Path.Combine(directory,"yoked.jsonl"),Binding("yoked"),created.AddHours(1));foreach(var row in rows)ledger.Append(row);Assert.That(ledger.SealYoked(replay,Verification()).AudioPlays,Is.EqualTo(32));}
        [Test]public void WrongYokedMeaningBindingOrSourceEventCannotMatch()
        {var replay=Load(Write(Events()));var rows=Events(replay).Select(MenuJson.Event).ToList();rows[0]["yoked_source_event_id"]=Guid.NewGuid().ToString("N");Assert.Throws<SessionFault>(()=>replay.CompareYokedRecords(rows,Binding("yoked"),Verification()));}
        [Test]public void RecordedDisplayOffsetsRemainAuthoritativeInYokedRun()
        {var active=Events(observedDelay:10);var replay=Load(Write(active));var yoked=Events(replay);var compared=replay.CompareYoked(yoked,Binding("yoked"),Verification());Assert.That(compared.AudioPlays,Is.EqualTo(32));Assert.That(yoked.First(x=>x.Kind=="display_changed").MonoMs-yoked.First().SlotStartMonoMs,Is.EqualTo(10));}
        [TestCase(false,2,true)][TestCase(false,19,false)][TestCase(true,2,true)][TestCase(true,19,false)]
        public void ActiveAndYokedOnsetsAreBoundedByTheirOwnUncertainty(bool yoked,double residual,bool accepted)
        {
            var replay=Load(Write(Events()));var rows=Events(yoked?replay:null).Select(MenuJson.Event).ToList();var onset=rows.First(x=>(string)x["kind"]=="onset_authority");onset["expected_mono_ms"]=(double)onset["expected_mono_ms"]+residual;onset["onset_uncertainty_ms"]=2;
            TestDelegate validate=()=>{if(yoked)replay.CompareYokedRecords(rows,Binding("yoked"),Verification());else MenuReplaySequence.ValidateRecords(rows,Binding(),Verification());};if(accepted)Assert.DoesNotThrow(validate);else Assert.Throws<SessionFault>(validate);
        }
        [Test]public void AllThreeVisitPairsPreserve128AtomAnd8ProfilePlaysPerPerson()
        {
            int atom=0,profile=0;foreach(string visit in new[]{"V1","V2","V3"})
            {
                string[] keys=visit=="V1"?new[]{"profile","K-a1","K-a2","K-r1","K-r2","Q-a1","Q-a2","Q-r1","Q-r2"}:visit=="V2"?new[]{"K-a3","K-r3","Q-a3","Q-r3"}:new[]{"K-a4","K-r4","Q-a4","Q-r4"};
                var binding=new MenuLedgerBinding(Hash,Hash,Hash,Hash,Hash,Hash,visit,"active",keys);string path=Write(Events(keys:keys),binding:binding);var replay=MenuReplaySequence.Load(path,PcmWave.Hash(File.ReadAllBytes(path)),binding,Verification(),created.AddHours(1),1000,()=>0);
                var yoked=new MenuLedgerBinding(Hash,Hash,Hash,Hash,Hash,Hash,visit,"yoked",keys);var report=replay.CompareYoked(Events(replay,keys),yoked,Verification());atom+=report.AtomPlays;profile+=report.ProfilePlays;
            }
            Assert.That(atom,Is.EqualTo(128));Assert.That(profile,Is.EqualTo(8));
        }
        [Test]public void ExposureHandoffRetainsRejectedCandidatesAndPauseFacts()
        {var rows=MenuExposureProjection.Derive(Events());Assert.That(rows.Count,Is.EqualTo(32));Assert.That(rows.Count(x=>x.AcceptedOrRejected=="rejected"),Is.EqualTo(16));Assert.That(rows.All(x=>x.ActiveChoiceOrDefault=="default"&&x.PlaybackStatus=="software_completed"),Is.True);Assert.That(rows.Skip(8).All(x=>x.PauseMs==5000),Is.True);}
        [Test]public void StartedMenuBeforeFirstDisplayStillNeedsNewSegmentAndCannotReplay()
        {
            var item=new SlotItem("DEMO-pause","atom_menu",Keys[0],null,"selection","selection",false,45,8,1);var context=new SlotContext(item,1000,null);var rows=new List<MenuEvent>();var timeline=new MenuTimeline(context,Options(Keys[0]),"DEMO-meaning",rows.Add);
            timeline.Start(0);timeline.Interrupt(0);Assert.That(rows.Last().MatchingDeviationId,Is.EqualTo(rows.Last().EventId));Assert.That(MenuExposureProjection.Derive(rows),Is.Empty);
            string path=Path.Combine(directory,"partial.jsonl");using(var ledger=new MenuLedger(path,Binding(),created)){foreach(var row in rows)ledger.Append(row);Assert.Throws<SessionFault>(()=>ledger.Seal(Verification()));Assert.Throws<SessionFault>(()=>ledger.Append(rows.Last()));}
            Assert.That(File.Exists(path),Is.True);Assert.Throws<SessionFault>(()=>Load(path));Assert.DoesNotThrow(()=>Load(Write(Events())));
        }
        [Test]public void NeverStartedCancellationLeavesVisitLedgerAvailableButOldTimelineCannotRestart()
        {
            string path=Path.Combine(directory,"not-started.jsonl");
            using(var ledger=new MenuLedger(path,Binding(),created))
            {
                var item=new SlotItem("DEMO-cancel","atom_menu",Keys[0],null,"selection","selection",false,45,8,1);
                var timeline=new MenuTimeline(new SlotContext(item,750,null),Options(Keys[0]),"DEMO-meaning",ledger.Append);
                var views=new List<MenuPhase>();timeline.DisplayChanged+=(phase,_)=>views.Add(phase);
                timeline.Interrupt(100);timeline.Interrupt(101);timeline.Tick(1000);
                Assert.That(timeline.Interrupted,Is.True);Assert.That(timeline.Complete,Is.False);
                Assert.That(views,Is.EqualTo(new[]{MenuPhase.Hidden}));
                Assert.That(File.ReadAllLines(path).Length,Is.EqualTo(1),"No menu_start or interruption may be invented for a never-started view");
                Assert.Throws<SessionFault>(()=>timeline.Start(1001));
                foreach(var row in Events())ledger.Append(row);ledger.Seal(Verification());
            }
            Assert.DoesNotThrow(()=>Load(path),"Only the complete replacement wave, with its original timing checks, can be sealed");
        }
        [TestCase(false)][TestCase(true)]public void ActualEnginePreCueRefusalReplacesLeaseAndKeepsUntouchedVisitLedger(bool profile)
        {
            var binding=profile?new MenuLedgerBinding(Hash,Hash,Hash,Hash,Hash,Hash,"V1","active",new[]{"profile","K-a1","K-a2","K-r1","K-r2","Q-a1","Q-a2","Q-r1","Q-r2"}):Binding();
            string path=Path.Combine(directory,"engine-cancel.jsonl");using var ledger=new MenuLedger(path,binding,created);
            var clock=new Clock();var journal=new Journal();var leases=new List<Lease>();
            string block=profile?"profile_menu":"atom_menus";
            var item=new SlotItem("DEMO-engine-cancel",profile?"profile_menu":"atom_menu",profile?null:Keys[0],null,"selection","selection",false,profile?60:45,8,1);
            var schedule=new VisitSchedule(Hash,Hash,"DEMO",profile?"V1":"V2",true,new[]{new ScheduleBlock(block,new[]{item})});
            var routes=new Dictionary<string,Func<ModuleConstructionScope,ISlotContentFactory>>{[block]=_=>{var lease=new Lease(clock,ledger,leases.Count>0);leases.Add(lease);return lease;}};
            using var mux=new ExclusiveContentMultiplexer(schedule,clock,routes);var engine=new FixedSlotEngine(schedule,clock,journal,mux);
            mux.PrepareBlockAtBoundary(engine);engine.ConfirmResume();engine.Tick();clock.Time=601;engine.Tick();
            Assert.That(engine.Status,Is.EqualTo(SessionState.Paused));Assert.That(leases[0].Current.Timeline.Interrupted,Is.True);
            Assert.That(journal.Records.Any(x=>x.Event=="item_fault"&&x.TechnicalFaultCode=="SESSION_READY_DEADLINE_MISSED"),Is.True);
            Assert.That(journal.Records.All(x=>!x.ExposureConsumed),Is.True);
            Assert.That(File.ReadAllLines(path).Length,Is.EqualTo(1));
            clock.Time=1000;mux.PrepareBlockAtBoundary(engine);engine.ConfirmResume();engine.Tick();
            Assert.That(leases.Count,Is.EqualTo(2));Assert.That(leases[0].Disposed,Is.True);
            Assert.That(engine.CurrentState,Is.EqualTo(ItemState.CueRequested));Assert.That(engine.CompletedOpportunities,Is.Zero);
            Assert.That(leases[1].Current.Context.OpportunityId,Is.EqualTo(leases[0].Current.Context.OpportunityId));
            Assert.That(leases[1].Current.Context.AudioRequestIds.Intersect(leases[0].Current.Context.AudioRequestIds),Is.Empty);
            var rows=File.ReadAllLines(path).Select(x=>JObject.Parse(x)["record"]).ToArray();
            Assert.That(rows.Select(x=>(string)x["kind"]),Is.EqualTo(new[]{"header","menu_start"}));
            Assert.That((double)rows[1]["slot_start_mono_ms"],Is.EqualTo(1750),"Explicit resume gets the normal engine lead, not a compressed old slot");
            Assert.That(journal.Records.Any(x=>x.State==ItemState.CueRequested&&x.ExposureConsumed),Is.True,"A started replacement stays consumed/uncertain");
        }
        sealed class Clock:ISessionClock{public double Time;public double NowMs=>Time;}
        sealed class Journal:ISessionJournal
        {
            readonly List<SessionRecord> rows=new List<SessionRecord>();public IReadOnlyList<SessionRecord> Records=>rows;
            public void Append(SessionRecord value)=>rows.Add(value);
        }
        sealed class Lease:ISlotContentFactory,IDisposable
        {
            readonly Clock clock;readonly MenuLedger ledger;readonly bool ready;public bool Disposed;public Content Current;
            public Lease(Clock clock,MenuLedger ledger,bool ready){this.clock=clock;this.ledger=ledger;this.ready=ready;}
            public ISlotContent Create(SlotItem item)=>Current=new Content(clock,ledger,ready);
            public void Dispose(){if(Disposed)return;Disposed=true;Current?.Interrupt("SYNTHETIC_LEASE_RETIRED");}
            public sealed class Content:ISlotContent
            {
                readonly Clock clock;readonly MenuLedger ledger;readonly bool ready;public MenuTimeline Timeline;public SlotContext Context;
                public Content(Clock clock,MenuLedger ledger,bool ready){this.clock=clock;this.ledger=ledger;this.ready=ready;}
                public void Prepare(SlotContext value){Context=value;Timeline=new MenuTimeline(value,Options(value.Item.TrialType=="profile_menu"?"profile":Keys[0]),"DEMO-meaning",ledger.Append);}
                public SlotReadiness Readiness=>new SlotReadiness(true,true,ready,true,true,true,true,true);
                public bool ResetComplete=>true;
                public void RequestCue(SlotContext value,INovelSlotAuthorization permit)=>Timeline.Start(clock.NowMs);
                public void OpenResponse(SlotContext value){}public void CloseResponse(SlotContext value){}public void RequestReset(SlotContext value){}
                public void Interrupt(string code)=>Timeline?.Interrupt(clock.NowMs);
            }
        }
        [Test]public void PartialOrExpiredOrFutureOrWrongReceiptLedgerCannotReplay()
        {
            var events=Events();Assert.Throws<SessionFault>(()=>Load(Write(events,false)));var path=Write(events);
            Assert.Throws<SessionFault>(()=>Load(path,created.AddHours(24).AddMilliseconds(1)));Assert.Throws<SessionFault>(()=>Load(path,created.AddMilliseconds(-1)));
            Assert.Throws<SessionFault>(()=>Load(path,verification:new MenuLedgerVerification(Options,(_,__,___)=>false,_=>"DEMO-meaning")));
        }
        [Test]public void WrongIndependentCandidateBytesCannotBeAuthorizedByValidChain()
        {var path=Write(Events());Assert.Throws<SessionFault>(()=>Load(path,verification:new MenuLedgerVerification(_=>Enumerable.Range(1,3).Select(i=>new MenuOption("DEMO-candidate-"+i,SelectionMenuTests.Wave(21600,i*200))).ToArray(),(_,__,___)=>true,_=>"DEMO-meaning")));}
        [TestCase("missing_completion")][TestCase("reused_audio")][TestCase("wrong_choice")][TestCase("early_highlight")][TestCase("onset_late")][TestCase("changed_pcm")][TestCase("gap_compression")]
        public void SelfConsistentStorageCannotHideInvalidPresentationEvidence(string mutation)
        {
            var records=Events().Select(MenuJson.Event).ToList();var play=records.First(x=>(string)x["kind"]=="play_request");
            if(mutation=="missing_completion")records.Remove(records.First(x=>(string)x["kind"]=="play_complete"));
            if(mutation=="reused_audio"){string id=(string)play["audio_request_id"];foreach(var row in records.Where(x=>(string)x["menu_key"]==Keys[1]&&x["presentation_index"].Type==JTokenType.Integer&&(int)x["presentation_index"]==1))row["audio_request_id"]=id;}
            if(mutation=="wrong_choice")records.First(x=>(string)x["kind"]=="choice_final")["selected_index"]=2;
            if(mutation=="early_highlight")records.First(x=>(string)x["kind"]=="display_changed"&&(string)x["phase"]=="Choice")["selected_index"]=1;
            if(mutation=="onset_late")records.First(x=>(string)x["kind"]=="onset_authority")["expected_mono_ms"]=(double)play["expected_mono_ms"]+21;
            if(mutation=="changed_pcm")play["pcm_sha256"]=new string('b',64);
            if(mutation=="gap_compression")foreach(var row in records.Where(x=>(string)x["menu_key"]==Keys[1]))row["slot_start_mono_ms"]=1000;
            Assert.Throws<SessionFault>(()=>MenuReplaySequence.ValidateRecords(records,Binding(),Verification()));
        }
        [Test]public void EnvelopeByteTamperAndNoncanonicalRepresentationAreRejected()
        {
            var path=Write(Events());string expected=PcmWave.Hash(File.ReadAllBytes(path));File.AppendAllText(path," ");
            Assert.Throws<SessionFault>(()=>MenuReplaySequence.Load(path,expected,Binding(),Verification(),created,1000,()=>0));
            Assert.Throws<SessionFault>(()=>Load(path));
        }
        [Test]public void SchemaRejectsCoercionAndExtraPrivateField()
        {
            var record=MenuJson.Event(Events().First());record["attempt_id"]=123;Assert.Throws<SessionFault>(()=>MenuJson.CheckEvent(record));
            record=MenuJson.Event(Events().First());record["answer"]="not allowed";Assert.Throws<SessionFault>(()=>MenuJson.CheckEvent(record));
        }
    }
}
